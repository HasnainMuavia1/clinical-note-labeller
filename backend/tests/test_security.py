from fastapi.testclient import TestClient

from app.main import create_app
from app.security import reset_rate_limit


def test_live_job_gets_are_not_rate_limited():
    reset_rate_limit()
    client = TestClient(create_app(), raise_server_exceptions=False)
    statuses = [client.get("/api/v1/jobs").status_code for _ in range(150)]
    assert statuses.count(429) == 0


def test_repeated_writes_still_rate_limit():
    reset_rate_limit()
    client = TestClient(create_app(), raise_server_exceptions=False)
    headers = {"X-API-Key": "flood-key"}
    codes = []
    for _ in range(130):
        codes.append(client.post("/api/v1/jobs", files=[], headers=headers).status_code)
    assert 429 in codes
