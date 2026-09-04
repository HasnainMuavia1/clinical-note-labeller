import importlib.util
import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SANDBOX_APP = Path(__file__).resolve().parents[2] / "sandbox" / "app.py"


@pytest.fixture()
def sandbox_client():
    spec = importlib.util.spec_from_file_location("sandbox_app", SANDBOX_APP)
    module = importlib.util.module_from_spec(spec)
    sys.modules["sandbox_app"] = module
    spec.loader.exec_module(module)
    return TestClient(module.create_app(), raise_server_exceptions=False)


def test_health(sandbox_client):
    assert sandbox_client.get("/health").json()["status"] == "ok"


def test_parses_plain_text(sandbox_client):
    files = {"file": ("note.txt", io.BytesIO(b"Patient presents with cough."), "text/plain")}
    body = sandbox_client.post("/parse", files=files).json()
    assert body["ok"] is True
    assert body["parser"] == "text"
    assert "cough" in body["text"]


def test_parses_latin1_text_without_crashing(sandbox_client):
    files = {"file": ("note.txt", io.BytesIO("Cafe visit".encode("latin-1")), "text/plain")}
    body = sandbox_client.post("/parse", files=files).json()
    assert body["ok"] is True
    assert "visit" in body["text"]


def test_reports_failure_for_unreadable_pdf(sandbox_client):
    files = {"file": ("broken.pdf", io.BytesIO(b"%PDF-1.4 not really a pdf"), "application/pdf")}
    body = sandbox_client.post("/parse", files=files).json()
    assert body["ok"] is False
    assert body["reason"]


def test_ocr_pages_run_in_parallel_when_workers_gt_one(sandbox_client, monkeypatch, tmp_path):
    import sandbox_app
    import time
    from concurrent.futures import ThreadPoolExecutor

    inflight = 0
    peak = 0

    def fake_run(cmd, check=True, timeout=600, capture_output=True):
        nonlocal inflight, peak
        if cmd[0] == "pdftoppm":
            out_prefix = cmd[-1]
            for i in range(3):
                Path(f"{out_prefix}-{i}.png").write_bytes(b"png")
            return type("P", (), {"stdout": b"", "returncode": 0})()
        inflight += 1
        peak = max(peak, inflight)
        time.sleep(0.05)
        inflight -= 1
        return type("P", (), {"stdout": b"page text", "returncode": 0})()

    monkeypatch.setattr(sandbox_app.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(sandbox_app.subprocess, "run", fake_run)
    monkeypatch.setattr(sandbox_app, "_ocr_workers", lambda: 3)
    monkeypatch.setattr(sandbox_app, "ThreadPoolExecutor", ThreadPoolExecutor)

    text, pages = sandbox_app._parse_ocr(b"%PDF-1.4 fake", ".pdf")
    assert pages == 3
    assert "page text" in text
    assert peak >= 2


def test_empty_file_is_reported_as_failure(sandbox_client):
    files = {"file": ("empty.txt", io.BytesIO(b"   \n  "), "text/plain")}
    body = sandbox_client.post("/parse", files=files).json()
    assert body["ok"] is False
