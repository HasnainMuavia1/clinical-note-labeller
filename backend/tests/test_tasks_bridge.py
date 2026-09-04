"""The Celery layer translates graph output into DB rows and parked jobs."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import tasks as tasks_module
from app.db.base import Base
from app.db.models import ApprovalStatus, JobStatus
from app.db.repository import Repository


class FakeInterrupt:
    def __init__(self, value):
        self.value = value


@pytest.fixture()
def repo(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    repository = Repository(sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(tasks_module, "get_repository", lambda: repository)
    repository.create_job("j1", "key", [], None)
    return repository


def test_persist_writes_a_row_per_file(repo):
    state = {"files": [
        {"file_id": "f1", "filename": "a.txt", "source_path": "a.txt", "sha256": "x",
         "size_bytes": 10, "ok": True, "parser": "text", "parse_trail": [],
         "has_codes": True, "code_hits": [{"code": "99213"}], "code_rejected": [],
         "npis": [], "specialty": "Cardiology", "confidence": 0.9, "method": "llm_sync",
         "output_path": "output/with-codes/Cardiology/a.txt"},
    ]}
    tasks_module._persist("j1", state)
    rows = repo.list_files("j1")
    assert len(rows) == 1
    assert rows[0].status == "filed"
    assert rows[0].specialty == "Cardiology"
    assert rows[0].code_hits[0]["code"] == "99213"


def test_persist_strips_nul_bytes_from_json_fields(repo):
    tasks_module._persist("j1", {"files": [{
        "file_id": "f-nul", "filename": "note.pdf", "ok": True,
        "code_hits": [{"code": "I10", "context": "Essential\u0000 hypertension"}],
        "code_rejected": [{
            "code": "19176", "kind": "cpt",
            "context": 'C:\\\\Program Files\\\\Google\\\\Chrome\\\\Application\\\\chrome.exe" \x00 leftover',
        }],
        "parse_trail": [{"parser": "text", "note": "ok\u0000"}],
    }]})
    row = repo.list_files("j1")[0]
    assert "\x00" not in row.code_hits[0]["context"]
    assert "\x00" not in row.code_rejected[0]["context"]
    assert "chrome.exe" in row.code_rejected[0]["context"]
    assert "\x00" not in row.parse_trail[0]["note"]


def test_persist_skips_a_row_that_cannot_be_saved_and_keeps_going(repo, monkeypatch):
    real_upsert = repo.upsert_file

    def flaky(job_id, file_id, **fields):
        if file_id == "boom":
            raise RuntimeError("disk full")
        return real_upsert(job_id, file_id, **fields)

    monkeypatch.setattr(repo, "upsert_file", flaky)
    tasks_module._persist("j1", {"files": [
        {"file_id": "boom", "filename": "bad.pdf", "ok": True},
        {"file_id": "ok", "filename": "good.pdf", "ok": True, "output_path": "output/a.pdf"},
    ]})
    rows = repo.list_files("j1")
    assert [r.filename for r in rows] == ["good.pdf"]
    audits = [e for e in repo.get_job("j1").audit_entries if e.action == "file_skipped"]
    assert audits[-1].detail["filename"] == "bad.pdf"
    assert "disk full" in audits[-1].detail["reason"]


def test_persist_marks_unparsed_files(repo):
    tasks_module._persist("j1", {"files": [
        {"file_id": "f2", "filename": "bad.pdf", "ok": False, "output_path": None},
    ]})
    assert repo.list_files("j1")[0].status == "unparsed"


def test_no_interrupt_leaves_the_job_running(repo):
    assert tasks_module._handle_interrupt("j1", {"files": []}) is False


def test_an_approval_interrupt_parks_the_job(repo):
    payload = {"kind": "overwrite", "ops": [{"target": "output/a.txt"}]}
    parked = tasks_module._handle_interrupt("j1", {"__interrupt__": [FakeInterrupt(payload)]})

    assert parked is True
    assert repo.get_job("j1").status == JobStatus.AWAITING_APPROVAL
    approvals = repo.list_approvals("j1", status=ApprovalStatus.PENDING)
    assert len(approvals) == 1
    assert approvals[0].kind == "overwrite"
    assert any(e.action == "approval_requested" for e in repo.get_job("j1").audit_entries)


def test_checkpoint_updates_stage_progress_files_and_audit(repo):
    tasks_module._checkpoint(
        "j1",
        "parse_node",
        {"stage": "parse", "files": [
            {"file_id": "f1", "filename": "note.txt", "ok": True, "parser": "text",
             "parse_trail": [{"parser": "text", "ok": True}]},
        ]},
        {"job_id": "j1"},
    )
    job = repo.get_job("j1")
    assert job.stage == "parse"
    assert job.progress == pytest.approx(0.32)
    assert repo.list_files("j1")[0].status == "parsed"
    steps = [e for e in job.audit_entries if e.action == "agent_step"]
    assert steps[-1].detail["stage"] == "parse"
    assert steps[-1].detail["node"] == "parse_node"


def test_file_tick_moves_progress_inside_a_stage(repo):
    tasks_module._file_tick(
        "j1", "parse", 50, 100,
        {"file_id": "f50", "filename": "note-50.pdf", "ok": True, "parser": "llamaparse",
         "parse_trail": [{"parser": "llamaparse", "ok": True}]},
    )
    job = repo.get_job("j1")
    assert job.stage == "parse"
    assert job.progress == pytest.approx(0.24)  # halfway between unpack 0.16 and parse 0.32
    assert repo.list_files("j1")[0].status == "parsed"
    ticks = [e for e in job.audit_entries if e.action == "file_progress"]
    assert ticks[-1].detail["done"] == 50
    assert ticks[-1].detail["total"] == 100


def test_a_batch_interrupt_parks_the_job_and_schedules_a_poll(repo, monkeypatch):
    scheduled = []
    monkeypatch.setattr(tasks_module.poll_batch_task, "apply_async",
                        lambda args, countdown=None: scheduled.append((args, countdown)))

    payload = {"kind": "batch_pending", "batch_id": "batch-9"}
    parked = tasks_module._handle_interrupt("j1", {"__interrupt__": [FakeInterrupt(payload)]})

    assert parked is True
    job = repo.get_job("j1")
    assert job.status == JobStatus.AWAITING_BATCH
    assert job.batch_id == "batch-9"
    assert scheduled == [(("j1", "batch-9"), 60)]
    # A batch wait is not an approval; it must not appear in the approvals inbox.
    assert repo.list_approvals("j1") == []


def test_checkpoint_survives_an_audit_failure(repo, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(repo, "audit", boom)
    tasks_module._checkpoint(
        "j1",
        "parse_node",
        {"stage": "parse", "files": [
            {"file_id": "f1", "filename": "note.txt", "ok": True, "parser": "text"},
        ]},
        {"job_id": "j1"},
    )
    job = repo.get_job("j1")
    assert job.stage == "parse"
    assert repo.list_files("j1")[0].filename == "note.txt"


def test_file_tick_survives_an_audit_failure(repo, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(repo, "audit", boom)
    tasks_module._file_tick(
        "j1", "parse", 1, 1,
        {"file_id": "f1", "filename": "note.txt", "ok": True},
    )
    assert repo.list_files("j1")[0].filename == "note.txt"


def test_handle_interrupt_with_malformed_payload_does_not_raise(repo):
    parked = tasks_module._handle_interrupt("j1", {"__interrupt__": [FakeInterrupt({})]})
    assert parked is False or repo.get_job("j1").status != JobStatus.FAILED


def test_failed_openai_batch_resumes_instead_of_failing_the_job(repo, monkeypatch):
    resumed = []
    monkeypatch.setattr("app.specialty.classifier.poll_batch", lambda _bid: "failed")
    monkeypatch.setattr(tasks_module.resume_job_task, "delay",
                        lambda job_id, value: resumed.append((job_id, value)))
    tasks_module.poll_batch_task.run("j1", "batch-1")
    assert resumed == [("j1", [])]
    assert repo.get_job("j1").status != JobStatus.FAILED


def test_poll_batch_exception_eventually_resumes(repo, monkeypatch):
    resumed = []

    def explode(_bid):
        raise RuntimeError("openai timeout")

    monkeypatch.setattr("app.specialty.classifier.poll_batch", explode)
    monkeypatch.setattr(tasks_module.resume_job_task, "delay",
                        lambda job_id, value: resumed.append((job_id, value)))
    tasks_module.poll_batch_task.run("j1", "batch-1", 5)
    assert resumed == [("j1", [])]
    assert repo.get_job("j1").status != JobStatus.FAILED
