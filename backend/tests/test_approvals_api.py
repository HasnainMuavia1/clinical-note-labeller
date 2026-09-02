import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import ApprovalStatus
from app.db.repository import Repository

AUTH = {"X-API-Key": "test-key"}


@pytest.fixture()
def api(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    repo = Repository(sessionmaker(bind=engine, expire_on_commit=False))

    import app.api.v1.approvals as approvals_module
    import app.api.v1.jobs as jobs_module

    monkeypatch.setattr(approvals_module, "get_repository", lambda: repo)
    monkeypatch.setattr(jobs_module, "get_repository", lambda: repo)
    resumed = []
    monkeypatch.setattr(approvals_module, "resume_job",
                        lambda job_id, value: resumed.append((job_id, value)))

    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("API_KEYS", "test-key")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))

    from app.main import create_app
    from app.security import reset_rate_limit

    reset_rate_limit()
    yield TestClient(create_app(), raise_server_exceptions=False), repo, resumed
    reset_rate_limit()
    get_settings.cache_clear()


def test_lists_pending_approvals(api):
    client, repo, _ = api
    repo.create_job("j1", "test-key", [], None)
    repo.create_approval("j1", "overwrite",
                         {"kind": "overwrite", "ops": [{"target": "output/a.pdf"}]})
    body = client.get("/api/v1/jobs/j1/approvals", headers=AUTH).json()
    assert len(body["items"]) == 1
    assert body["items"][0]["kind"] == "overwrite"


def test_approving_resumes_the_graph_with_the_decision(api):
    client, repo, resumed = api
    repo.create_job("j1", "test-key", [], None)
    approval = repo.create_approval("j1", "overwrite",
                                    {"kind": "overwrite", "ops": [{"target": "output/a.pdf"}]})
    r = client.post(f"/api/v1/jobs/j1/approvals/{approval.id}",
                    json={"decision": "approve"}, headers=AUTH)
    assert r.status_code == 200
    assert repo.list_approvals("j1")[0].status == ApprovalStatus.APPROVED
    job_id, value = resumed[0]
    assert job_id == "j1"
    assert value["decisions"]["output/a.pdf"] == "approve"


def test_rejecting_records_the_decision(api):
    client, repo, resumed = api
    repo.create_job("j1", "test-key", [], None)
    approval = repo.create_approval("j1", "overwrite",
                                    {"kind": "overwrite", "ops": [{"target": "output/a.pdf"}]})
    client.post(f"/api/v1/jobs/j1/approvals/{approval.id}",
                json={"decision": "reject", "note": "keep the original"}, headers=AUTH)
    assert repo.list_approvals("j1")[0].status == ApprovalStatus.REJECTED
    assert resumed[0][1]["decisions"]["output/a.pdf"] == "reject"


def test_low_confidence_approval_carries_a_specialty_override(api):
    client, repo, resumed = api
    repo.create_job("j1", "test-key", [], None)
    approval = repo.create_approval("j1", "low_confidence",
                                    {"kind": "low_confidence", "files": [{"file_id": "f1"}]})
    client.post(f"/api/v1/jobs/j1/approvals/{approval.id}",
                json={"decision": "approve", "specialty": "Neurology"}, headers=AUTH)
    assert resumed[0][1]["specialties"]["f1"] == "Neurology"


def test_deciding_an_already_decided_approval_is_a_conflict(api):
    client, repo, _ = api
    repo.create_job("j1", "test-key", [], None)
    approval = repo.create_approval("j1", "overwrite", {"kind": "overwrite", "ops": []})
    client.post(f"/api/v1/jobs/j1/approvals/{approval.id}",
                json={"decision": "approve"}, headers=AUTH)
    r = client.post(f"/api/v1/jobs/j1/approvals/{approval.id}",
                    json={"decision": "reject"}, headers=AUTH)
    assert r.status_code == 409


def test_code_lookup_returns_the_dictionary_source(api):
    client, _, _ = api
    body = client.get("/api/v1/codes/lookup?code=99213", headers=AUTH).json()
    assert body["found"] is True
    assert body["source"] == "cpt"


def test_code_lookup_reports_unknown_codes(api):
    client, _, _ = api
    body = client.get("/api/v1/codes/lookup?code=ZZZZZ", headers=AUTH).json()
    assert body["found"] is False


def test_low_confidence_approval_accepts_a_per_file_specialty_map(api):
    client, repo, resumed = api
    repo.create_job("j1", "test-key", [], None)
    approval = repo.create_approval(
        "j1", "low_confidence",
        {"kind": "low_confidence", "files": [{"file_id": "f1"}, {"file_id": "f2"},
                                             {"file_id": "f3"}]})
    client.post(f"/api/v1/jobs/j1/approvals/{approval.id}", headers=AUTH, json={
        "decision": "approve",
        "specialty": "Internal Medicine",
        "specialties": {"f1": "Cardiology", "f2": "Dermatology"},
    })
    chosen = resumed[0][1]["specialties"]
    assert chosen["f1"] == "Cardiology"
    assert chosen["f2"] == "Dermatology"
    # f3 has no per-file entry, so it takes the blanket specialty.
    assert chosen["f3"] == "Internal Medicine"


def test_rejecting_a_low_confidence_approval_files_everything_unclassified(api):
    client, repo, resumed = api
    repo.create_job("j1", "test-key", [], None)
    approval = repo.create_approval(
        "j1", "low_confidence",
        {"kind": "low_confidence", "files": [{"file_id": "f1"}, {"file_id": "f2"}]})
    client.post(f"/api/v1/jobs/j1/approvals/{approval.id}", headers=AUTH,
                json={"decision": "reject", "specialty": "Cardiology"})
    assert resumed[0][1]["specialties"] == {"f1": "Unclassified", "f2": "Unclassified"}
