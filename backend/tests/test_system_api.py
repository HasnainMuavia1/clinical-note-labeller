def test_health_is_public_and_ok(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_version_reports_v1(client):
    r = client.get("/api/v1/version")
    assert r.json()["api_version"] == "v1"


def test_protected_route_without_key_returns_problem_json(client):
    r = client.get("/api/v1/specialties")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["title"] == "Unauthorized"
    assert body["status"] == 401


def test_request_id_header_is_echoed(client):
    r = client.get("/api/v1/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"


def test_unknown_route_returns_problem_json(client):
    r = client.get("/api/v1/nope")
    assert r.headers["content-type"].startswith("application/problem+json")
