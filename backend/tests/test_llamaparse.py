import httpx
import pytest
import respx

from app.config import get_settings
from app.parsing.llamaparse import BASE_URL, LlamaParseError, llamaparse_text


@pytest.fixture()
def note(tmp_path):
    p = tmp_path / "scan.pdf"
    p.write_bytes(b"%PDF-1.4 stub")
    return p


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("LLAMA_CLOUD_API_KEY", "test-llama-key")
    yield
    get_settings.cache_clear()


@respx.mock
async def test_uploads_polls_and_returns_text(note):
    upload = respx.post(f"{BASE_URL}/upload").mock(
        return_value=httpx.Response(200, json={"id": "job-1", "status": "PENDING"}))
    statuses = iter([{"status": "PENDING"}, {"status": "SUCCESS"}])
    respx.get(f"{BASE_URL}/job/job-1").mock(
        side_effect=lambda r: httpx.Response(200, json=next(statuses)))
    respx.get(f"{BASE_URL}/job/job-1/result/text").mock(
        return_value=httpx.Response(200, json={"text": "Dx: E11.9"}))

    assert await llamaparse_text(note) == "Dx: E11.9"
    assert upload.called
    assert upload.calls[0].request.headers["authorization"] == "Bearer test-llama-key"


@respx.mock
async def test_upload_failure_raises(note):
    respx.post(f"{BASE_URL}/upload").mock(return_value=httpx.Response(401, text="bad key"))
    with pytest.raises(LlamaParseError, match="upload failed"):
        await llamaparse_text(note)


@respx.mock
async def test_job_error_raises(note):
    respx.post(f"{BASE_URL}/upload").mock(return_value=httpx.Response(200, json={"id": "job-2"}))
    respx.get(f"{BASE_URL}/job/job-2").mock(return_value=httpx.Response(200, json={"status": "ERROR"}))
    with pytest.raises(LlamaParseError, match="finished as ERROR"):
        await llamaparse_text(note)


async def test_missing_key_raises(note, monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("LLAMA_CLOUD_API_KEY", "")
    with pytest.raises(LlamaParseError, match="LLAMA_CLOUD_API_KEY"):
        await llamaparse_text(note)
    get_settings.cache_clear()
