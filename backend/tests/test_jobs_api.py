import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.repository import Repository

AUTH = {"X-API-Key": "test-key"}


@pytest.fixture()
def api(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    repo = Repository(sessionmaker(bind=engine, expire_on_commit=False))

    import app.api.v1.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "get_repository", lambda: repo)
    dispatched = []
    monkeypatch.setattr(jobs_module, "dispatch_job", lambda job_id: dispatched.append(job_id))

    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("API_KEYS", "test-key")

    from app.main import create_app
    from app.security import reset_rate_limit

    reset_rate_limit()
    yield TestClient(create_app(), raise_server_exceptions=False), repo, dispatched
    reset_rate_limit()
    get_settings.cache_clear()


def test_upload_creates_a_job_and_dispatches_it(api):
    client, repo, dispatched = api
    files = [("files", ("note.txt", io.BytesIO(b"Dx: E11.9"), "text/plain"))]
    r = client.post("/api/v1/jobs", files=files, headers=AUTH)
    assert r.status_code == 202
    job_id = r.json()["id"]
    assert repo.get_job(job_id) is not None
    assert dispatched == [job_id]


def test_job_id_is_the_uploaded_filename_stem(api):
    client, repo, dispatched = api
    files = [("files", ("ECW_zip.zip", io.BytesIO(b"PK"), "application/zip"))]
    r = client.post("/api/v1/jobs", files=files, headers=AUTH)
    assert r.status_code == 202
    assert r.json()["id"] == "ECW_zip"
    assert dispatched == ["ECW_zip"]
    assert repo.get_job("ECW_zip") is not None


def test_reuploading_the_same_name_gets_a_numeric_suffix(api, tmp_path):
    client, _, dispatched = api
    files = [("files", ("ECW_zip.zip", io.BytesIO(b"PK"), "application/zip"))]
    first = client.post("/api/v1/jobs", files=files, headers=AUTH).json()["id"]
    files = [("files", ("ECW_zip.zip", io.BytesIO(b"PK"), "application/zip"))]
    second = client.post("/api/v1/jobs", files=files, headers=AUTH).json()["id"]
    assert first == "ECW_zip"
    assert second == "ECW_zip__2"
    assert (tmp_path / "ECW_zip" / "input" / "ECW_zip.zip").exists()
    assert (tmp_path / "ECW_zip__2" / "input" / "ECW_zip.zip").exists()
    assert dispatched == ["ECW_zip", "ECW_zip__2"]


def test_uploaded_bytes_land_in_the_job_input_folder(api, tmp_path):
    client, _, _ = api
    files = [("files", ("note.txt", io.BytesIO(b"hello"), "text/plain"))]
    job_id = client.post("/api/v1/jobs", files=files, headers=AUTH).json()["id"]
    assert (tmp_path / job_id / "input" / "note.txt").read_bytes() == b"hello"


def test_upload_succeeds_without_a_key(api):
    client, repo, dispatched = api
    files = [("files", ("note.txt", io.BytesIO(b"x"), "text/plain"))]
    r = client.post("/api/v1/jobs", files=files)
    assert r.status_code == 202
    assert repo.get_job(r.json()["id"]) is not None
    assert dispatched == [r.json()["id"]]


def test_idempotency_key_returns_the_same_job(api):
    client, _, dispatched = api
    headers = {**AUTH, "Idempotency-Key": "abc"}
    files = [("files", ("note.txt", io.BytesIO(b"x"), "text/plain"))]
    first = client.post("/api/v1/jobs", files=files, headers=headers).json()["id"]
    files = [("files", ("note.txt", io.BytesIO(b"x"), "text/plain"))]
    second = client.post("/api/v1/jobs", files=files, headers=headers).json()["id"]
    assert first == second
    assert dispatched == [first]


def test_list_jobs_returns_a_paginated_envelope(api):
    client, repo, _ = api
    repo.create_job("j1", "test-key", ["a.pdf"], None)
    body = client.get("/api/v1/jobs?limit=1", headers=AUTH).json()
    assert "items" in body and "next_cursor" in body


def test_job_payload_includes_files_done_and_total(api):
    client, repo, _ = api
    repo.create_job("j1", "test-key", ["a.pdf", "b.pdf"], None)
    repo.upsert_file("j1", "f1", filename="a.pdf", source_path="a.pdf", status="parsed")
    repo.upsert_file("j1", "f2", filename="b.pdf", source_path="b.pdf", status="pending")
    body = client.get("/api/v1/jobs/j1", headers=AUTH).json()
    assert body["files_done"] == 1
    assert body["files_total"] == 2


def test_list_files_hides_stale_duplicates_and_the_zip_shell(api):
    client, repo, _ = api
    repo.create_job("j1", "test-key", ["notes.zip"], None)
    repo.upsert_file("j1", "zip", filename="notes.zip", source_path="notes.zip", status="pending")
    repo.upsert_file("j1", "old", filename="a.pdf", source_path="notes.zip!/a.pdf", status="pending")
    repo.upsert_file(
        "j1", "new", filename="a.pdf", source_path="notes.zip!/a.pdf", status="filed",
        specialty="Cardiology", method="llm_batch", confidence=0.9, has_codes=True,
    )
    body = client.get("/api/v1/jobs/j1/files", headers=AUTH).json()
    assert [row["filename"] for row in body["items"]] == ["a.pdf"]
    assert body["items"][0]["specialty"] == "Cardiology"
    assert body["items"][0]["status"] == "filed"


def test_file_counts_dedupe_the_same_note_after_a_reparse(api):
    client, repo, _ = api
    repo.create_job("j1", "test-key", ["notes.zip"], None)
    repo.upsert_file("j1", "old", filename="a.pdf", source_path="notes.zip!/a.pdf", status="pending")
    repo.upsert_file("j1", "new", filename="a.pdf", source_path="notes.zip!/a.pdf", status="parsed")
    repo.upsert_file("j1", "b", filename="b.pdf", source_path="notes.zip!/b.pdf", status="pending")
    body = client.get("/api/v1/jobs/j1", headers=AUTH).json()
    assert body["files_done"] == 1
    assert body["files_total"] == 2


def test_completed_job_payload_shows_manifest_at_100(api):
    client, repo, _ = api
    repo.create_job("j1", "test-key", ["a.pdf"], None)
    repo.update_job("j1", status="completed", stage="parse", progress=0.19)
    body = client.get("/api/v1/jobs/j1", headers=AUTH).json()
    assert body["status"] == "completed"
    assert body["stage"] == "manifest"
    assert body["progress"] == 1.0


def test_get_unknown_job_returns_problem_json(api):
    client, _, _ = api
    r = client.get("/api/v1/jobs/nope", headers=AUTH)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/problem+json")


def test_file_detail_exposes_code_evidence(api):
    client, repo, _ = api
    repo.create_job("j1", "test-key", [], None)
    repo.upsert_file("j1", "f1", filename="a.pdf", has_codes=True, specialty="Cardiology",
                     code_hits=[{"code": "99213", "rule": "dictionary+cue",
                                 "context": "Procedure Code: 99213"}])
    body = client.get("/api/v1/jobs/j1/files/f1", headers=AUTH).json()
    assert body["code_hits"][0]["code"] == "99213"
    assert body["specialty"] == "Cardiology"


def test_audit_endpoint_lists_job_events(api):
    client, repo, _ = api
    repo.create_job("j1", "test-key", ["a.pdf"], None)
    repo.audit("j1", "agent_step", {"stage": "intake", "node": "intake_node"})
    body = client.get("/api/v1/jobs/j1/audit", headers=AUTH).json()
    actions = [item["action"] for item in body["items"]]
    assert "job_created" in actions or "agent_step" in actions
    assert any(item["action"] == "agent_step" for item in body["items"])
    assert body["items"][0]["detail"]


def test_tree_endpoint_lists_the_output_structure(api, tmp_path):
    client, repo, _ = api
    repo.create_job("j1", "test-key", [], None)
    target = tmp_path / "j1" / "output" / "with-codes" / "Cardiology"
    target.mkdir(parents=True)
    (target / "note.pdf").write_text("x")
    body = client.get("/api/v1/jobs/j1/tree", headers=AUTH).json()
    assert "with-codes/Cardiology/note.pdf" in body["paths"]
