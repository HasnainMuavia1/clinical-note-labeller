import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("API_KEYS", "test-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("REFERENCE_ROOT", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


@pytest.fixture()
def client():
    from app.main import create_app
    from app.security import reset_rate_limit

    reset_rate_limit()
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.fixture()
def auth():
    return {"X-API-Key": "test-key"}
