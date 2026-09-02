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

    yield TestClient(create_app(), raise_server_exceptions=False), repo, dispatched
    get_settings.cache_clear()


def test_upload_creates_a_job_and_dispatches_it(api):
    client, repo, dispatched = api
    files = [("files", ("note.txt", io.BytesIO(b"Dx: E11.9"), "text/plain"))]
    r = client.post("/api/v1/jobs", files=files, headers=AUTH)
    assert r.status_code == 202
    job_id = r.json()["id"]
    assert repo.get_job(job_id) is not None
    assert dispatched == [job_id]


def test_uploaded_bytes_land_in_the_job_input_folder(api, tmp_path):
    client, _, _ = api
    files = [("files", ("note.txt", io.BytesIO(b"hello"), "text/plain"))]
    job_id = client.post("/api/v1/jobs", files=files, headers=AUTH).json()["id"]
    assert (tmp_path / job_id / "input" / "note.txt").read_bytes() == b"hello"


def test_upload_requires_authentication(api):
    client, _, _ = api
    files = [("files", ("note.txt", io.BytesIO(b"x"), "text/plain"))]
    assert client.post("/api/v1/jobs", files=files).status_code == 401


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


def test_tree_endpoint_lists_the_output_structure(api, tmp_path):
    client, repo, _ = api
    repo.create_job("j1", "test-key", [], None)
    target = tmp_path / "j1" / "output" / "with-codes" / "Cardiology"
    target.mkdir(parents=True)
    (target / "note.pdf").write_text("x")
    body = client.get("/api/v1/jobs/j1/tree", headers=AUTH).json()
    assert "with-codes/Cardiology/note.pdf" in body["paths"]
