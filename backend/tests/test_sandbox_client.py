import httpx
import pytest
import respx

from app.parsing.sandbox_client import call_sandbox

SANDBOX = "http://parser-sandbox:8081/parse"


@pytest.fixture()
def note(tmp_path):
    path = tmp_path / "note.pdf"
    path.write_bytes(b"%PDF-1.4 stub")
    return path


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
