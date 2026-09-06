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
    body = sandbox_client.get("/health").json()
    assert body["status"] == "ok"
    assert body["ocr_engine"] == "tesseract"


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


def test_ocr_rasterizes_pages_at_150_dpi(sandbox_client, monkeypatch):
    import sandbox_app

    seen = {}

    def fake_run(cmd, check=True, timeout=600, capture_output=True):
        if cmd[0] == "pdftoppm":
            seen["cmd"] = cmd
            out_prefix = cmd[-1]
            Path(f"{out_prefix}-1.png").write_bytes(b"png")
            return type("P", (), {"stdout": b"", "returncode": 0})()
        return type("P", (), {"stdout": b"typed note", "returncode": 0})()

    monkeypatch.setattr(sandbox_app.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(sandbox_app.subprocess, "run", fake_run)

    sandbox_app._parse_ocr(b"%PDF-1.4 fake", ".pdf", workers=1)
    assert seen["cmd"][:4] == ["pdftoppm", "-r", "150", "-png"]


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
    monkeypatch.setattr(sandbox_app, "_ocr_workers", lambda: 1)
    monkeypatch.setattr(sandbox_app, "ThreadPoolExecutor", ThreadPoolExecutor)

    text, pages = sandbox_app._parse_ocr(b"%PDF-1.4 fake", ".pdf", workers=3)
    assert pages == 3
    assert "page text" in text
    assert peak >= 2


def _workspace_pdf() -> Path | None:
    root = Path(__file__).resolve().parents[2] / "workspace"
    if not root.is_dir():
        return None
    extracted = list(root.rglob("extracted/**/*.pdf"))
    if extracted:
        return extracted[0]
    hits = [p for p in root.rglob("*.pdf") if p.is_file()]
    return hits[0] if hits else None


def test_ocr_reads_text_from_a_workspace_pdf(sandbox_client):
    import shutil

    import sandbox_app

    if not shutil.which("tesseract") or not shutil.which("pdftoppm"):
        pytest.skip("tesseract/pdftoppm are not installed on this host")
    pdf = _workspace_pdf()
    if pdf is None:
        pytest.skip("no workspace PDF available")
    text, pages = sandbox_app._parse_ocr(pdf.read_bytes(), ".pdf")
    assert pages >= 1
    assert text.strip(), f"OCR returned empty text for {pdf.name}"


def test_macos_metadata_is_not_treated_as_a_note(sandbox_client):
    files = {"file": (".DS_Store", io.BytesIO(b"\x00\x00Bud1"), "application/octet-stream")}
    body = sandbox_client.post("/parse", files=files).json()
    assert body["ok"] is False
    assert "unsupported" in (body["reason"] or "").lower()


def test_ocr_caps_page_workers_so_tesseract_cannot_exhaust_pids(sandbox_client, monkeypatch, tmp_path):
    import sandbox_app
    seen = {}

    def fake_run(cmd, check=True, timeout=600, capture_output=True):
        if cmd[0] == "pdftoppm":
            out_prefix = cmd[-1]
            for i in range(6):
                Path(f"{out_prefix}-{i}.png").write_bytes(b"png")
            return type("P", (), {"stdout": b"", "returncode": 0})()
        return type("P", (), {"stdout": b"page", "returncode": 0})()

    class RecordingPool:
        def __init__(self, max_workers):
            seen["max_workers"] = max_workers

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def map(self, fn, images):
            return ["page" for _ in images]

    monkeypatch.setattr(sandbox_app.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(sandbox_app.subprocess, "run", fake_run)
    monkeypatch.setattr(sandbox_app, "ThreadPoolExecutor", RecordingPool)

    text, pages = sandbox_app._parse_ocr(b"%PDF-1.4 fake", ".pdf", workers=8)
    assert pages == 6
    assert "page" in text
    assert seen["max_workers"] <= 4


def test_empty_file_is_reported_as_failure(sandbox_client):
    files = {"file": ("empty.txt", io.BytesIO(b"   \n  "), "text/plain")}
    body = sandbox_client.post("/parse", files=files).json()
    assert body["ok"] is False
