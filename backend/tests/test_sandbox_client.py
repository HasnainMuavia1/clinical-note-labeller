from contextlib import asynccontextmanager

import httpx
import pytest
import respx

from app.parsing.sandbox_client import call_sandbox


@asynccontextmanager
async def _open_slot():
    yield

SANDBOX = "http://parser-sandbox:8081/parse"


@pytest.fixture()
def note(tmp_path):
    path = tmp_path / "note.pdf"
    path.write_bytes(b"%PDF-1.4 stub")
    return path


@respx.mock
async def test_ocr_read_timeout_is_not_retried(note, monkeypatch):
    calls = {"n": 0}

    def once(_request):
        calls["n"] += 1
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr("app.parsing.sandbox_client.acquire_ocr_slot", _open_slot)
    respx.post(SANDBOX).mock(side_effect=once)
    with pytest.raises(httpx.ReadTimeout):
        await call_sandbox(note, ocr=True)
    assert calls["n"] == 1


def test_ocr_timeout_is_thirty_minutes():
    from app.parsing.sandbox_client import OCR_TIMEOUT, PARSE_TIMEOUT
    assert OCR_TIMEOUT.read >= 1800
    assert PARSE_TIMEOUT.read <= 120


@respx.mock
async def test_retries_when_the_sandbox_disconnects(note):
    calls = {"n": 0}

    def flaky(_request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, json={"text": "rescued", "pages": 1,
                                         "parser": "ocr", "ok": True, "reason": None})

    respx.post(SANDBOX).mock(side_effect=flaky)
    payload = await call_sandbox(note, ocr=True)
    assert payload["ok"] is True
    assert payload["text"] == "rescued"
    assert calls["n"] == 3
