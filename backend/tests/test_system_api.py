def test_health_is_public_and_ok(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_version_reports_v1(client):
    r = client.get("/api/v1/version")
    assert r.json()["api_version"] == "v1"


def test_capacity_reports_planned_workers(client):
    r = client.get("/api/v1/capacity")
    assert r.status_code == 200
    body = r.json()
    assert body["file_concurrency"] >= 1
    assert body["celery_concurrency"] >= 1
    assert "cpu_count" in body
    assert "gpu_count" in body
    assert "gpu_batch_size" in body


def test_routes_are_open_without_a_key(client):
    r = client.get("/api/v1/specialties")
    assert r.status_code == 200
    assert "items" in r.json()


def test_request_id_header_is_echoed(client):
    r = client.get("/api/v1/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["X-Request-ID"] == "abc-123"


def test_unknown_route_returns_problem_json(client):
    r = client.get("/api/v1/nope")
    assert r.headers["content-type"].startswith("application/problem+json")
