import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import ApprovalStatus, JobStatus
from app.db.repository import Repository


@pytest.fixture()
def repo():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return Repository(sessionmaker(bind=engine, expire_on_commit=False))


def test_create_and_get_job(repo):
    job = repo.create_job("job-1", "key-a", ["a.pdf"], None)
    assert job.status == JobStatus.PENDING
    assert repo.get_job("job-1").id == "job-1"


def test_idempotency_key_lookup(repo):
    repo.create_job("job-1", "key-a", ["a.pdf"], "idem-1")
    assert repo.find_by_idempotency_key("idem-1").id == "job-1"
    assert repo.find_by_idempotency_key("nope") is None


def test_update_job_status_and_stage(repo):
    repo.create_job("job-1", "key-a", [], None)
    updated = repo.update_job("job-1", status=JobStatus.RUNNING, stage="parse", progress=0.25)
    assert updated.status == JobStatus.RUNNING
    assert updated.stage == "parse"


def test_upsert_file_is_idempotent(repo):
    repo.create_job("job-1", "key-a", [], None)
    repo.upsert_file("job-1", "f1", filename="a.pdf", specialty="Cardiology")
    repo.upsert_file("job-1", "f1", has_codes=True)
    files = repo.list_files("job-1")
    assert len(files) == 1
    assert files[0].specialty == "Cardiology"
    assert files[0].has_codes is True


def test_approval_lifecycle(repo):
    repo.create_job("job-1", "key-a", [], None)
    approval = repo.create_approval("job-1", "delete", {"path": "output/x.pdf"})
    assert approval.status == ApprovalStatus.PENDING
    pending = repo.list_approvals("job-1", status=ApprovalStatus.PENDING)
    assert len(pending) == 1
    decided = repo.decide_approval(approval.id, "approve", "looks fine")
    assert decided.status == ApprovalStatus.APPROVED
    assert repo.list_approvals("job-1", status=ApprovalStatus.PENDING) == []


def test_audit_entries_are_appended(repo):
    repo.create_job("job-1", "key-a", [], None)
    repo.audit("job-1", "path_escape_denied", {"path": "../etc/passwd"})
    repo.audit("job-1", "approval_granted", {"approval_id": "x"})
    job = repo.get_job("job-1")
    assert len(job.audit_entries) == 2


def test_npi_cache_roundtrip(repo):
    assert repo.get_npi("1234567893") is None
    repo.put_npi("1234567893", "Cardiology", "207RC0000X", True)
    assert repo.get_npi("1234567893").specialty == "Cardiology"


def test_list_jobs_paginates(repo):
    for i in range(5):
        repo.create_job(f"job-{i}", "key-a", [], None)
    page, cursor = repo.list_jobs(limit=2)
    assert len(page) == 2 and cursor is not None
    page2, _ = repo.list_jobs(limit=2, cursor=cursor)
    assert {j.id for j in page} & {j.id for j in page2} == set()
