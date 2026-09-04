import httpx
import pytest
import respx

from app.parsing import chain as chain_module
from app.parsing.chain import ParseAttempt, ParseResult, parse_document, parse_via_llamaparse

SANDBOX = "http://parser-sandbox:8081/parse"


@pytest.fixture()
def note(tmp_path):
    p = tmp_path / "note.pdf"
    p.write_bytes(b"%PDF-1.4 stub")
    return p


@pytest.fixture(autouse=True)
def cpu_parse_order(monkeypatch):
    monkeypatch.setattr(chain_module, "ocr_first", lambda: False)


@respx.mock
async def test_sandbox_success_short_circuits_the_chain(note, monkeypatch):
    route = respx.post(SANDBOX).mock(return_value=httpx.Response(
        200, json={"text": "Dx: E11.9", "pages": 1, "parser": "pypdf", "ok": True, "reason": None}))
    called = {"llama": False}

    async def never(_):
        called["llama"] = True

    monkeypatch.setattr(chain_module, "parse_via_llamaparse", never)

    result = await parse_document(note)
    assert result.ok and result.parser == "pypdf"
    assert called["llama"] is False
    assert route.called
    assert [a.parser for a in result.trail] == ["pypdf"]


@respx.mock
async def test_falls_through_to_llamaparse_when_sandbox_fails(note, monkeypatch):
    respx.post(SANDBOX).mock(return_value=httpx.Response(
        200, json={"text": "", "pages": 0, "parser": "none", "ok": False,
                   "reason": "no extractable text"}))

    async def fake_llama(path):
        return ParseResult("parsed by llama", "llamaparse", 1, True,
                           [ParseAttempt("llamaparse", True, None)])

    monkeypatch.setattr(chain_module, "parse_via_llamaparse", fake_llama)

    result = await parse_document(note)
    assert result.ok and result.parser == "llamaparse"
    assert [a.parser for a in result.trail][:2] == ["sandbox", "llamaparse"]


@respx.mock
async def test_falls_through_to_ocr_when_llamaparse_fails(note, monkeypatch):
    def sandbox_response(request):
        if "ocr=true" in str(request.url):
            return httpx.Response(200, json={"text": "ocr text", "pages": 1, "parser": "ocr",
                                             "ok": True, "reason": None})
        return httpx.Response(200, json={"text": "", "pages": 0, "parser": "none",
                                         "ok": False, "reason": "empty"})

    respx.post(SANDBOX).mock(side_effect=sandbox_response)

    async def failing_llama(path):
        return ParseResult("", "llamaparse", 0, False, [ParseAttempt("llamaparse", False, "no key")])

    monkeypatch.setattr(chain_module, "parse_via_llamaparse", failing_llama)

    result = await parse_document(note)
    assert result.parser == "ocr" and result.ok
    assert [a.parser for a in result.trail] == ["sandbox", "llamaparse", "ocr"]


@respx.mock
async def test_all_hops_failing_returns_not_ok_with_full_trail(note, monkeypatch):
    respx.post(SANDBOX).mock(return_value=httpx.Response(
        200, json={"text": "", "pages": 0, "parser": "none", "ok": False, "reason": "empty"}))

    async def failing_llama(path):
        return ParseResult("", "llamaparse", 0, False, [ParseAttempt("llamaparse", False, "no key")])

    monkeypatch.setattr(chain_module, "parse_via_llamaparse", failing_llama)

    result = await parse_document(note)
    assert result.ok is False
    assert len(result.trail) == 3
    assert all(not a.ok for a in result.trail)


@respx.mock
async def test_gpu_tries_tesseract_before_pypdf_and_llamaparse(note, monkeypatch):
    monkeypatch.setattr(chain_module, "ocr_first", lambda: True)
    order = []

    def sandbox_response(request):
        ocr = "ocr=true" in str(request.url)
        order.append("ocr" if ocr else "pypdf")
        if ocr:
            return httpx.Response(200, json={"text": "scanned note", "pages": 1,
                                             "parser": "ocr", "ok": True, "reason": None})
        pytest.fail("pypdf must not run after Tesseract already succeeded")

    respx.post(SANDBOX).mock(side_effect=sandbox_response)

    async def never(_):
        pytest.fail("LlamaParse must not run after Tesseract already succeeded")

    monkeypatch.setattr(chain_module, "parse_via_llamaparse", never)

    result = await parse_document(note)
    assert result.ok and result.parser == "ocr"
    assert order == ["ocr"]
    assert [a.parser for a in result.trail] == ["ocr"]


@respx.mock
async def test_gpu_falls_through_tesseract_to_pypdf_then_llamaparse(note, monkeypatch):
    monkeypatch.setattr(chain_module, "ocr_first", lambda: True)
    order = []

    def sandbox_response(request):
        ocr = "ocr=true" in str(request.url)
        order.append("ocr" if ocr else "pypdf")
        if ocr:
            return httpx.Response(200, json={"text": "", "pages": 0, "parser": "ocr",
                                             "ok": False, "reason": "blank scan"})
        return httpx.Response(200, json={"text": "", "pages": 0, "parser": "pypdf",
                                         "ok": False, "reason": "no extractable text"})

    respx.post(SANDBOX).mock(side_effect=sandbox_response)

    async def fake_llama(path):
        order.append("llamaparse")
        return ParseResult("llama text", "llamaparse", 1, True,
                           [ParseAttempt("llamaparse", True, None)])

    monkeypatch.setattr(chain_module, "parse_via_llamaparse", fake_llama)

    result = await parse_document(note)
    assert result.ok and result.parser == "llamaparse"
    assert order == ["ocr", "pypdf", "llamaparse"]
    assert [a.parser for a in result.trail] == ["ocr", "pypdf", "llamaparse"]


@respx.mock
async def test_gpu_uses_pypdf_when_tesseract_finds_nothing(note, monkeypatch):
    monkeypatch.setattr(chain_module, "ocr_first", lambda: True)

    def sandbox_response(request):
        if "ocr=true" in str(request.url):
            return httpx.Response(200, json={"text": "", "pages": 1, "parser": "ocr",
                                             "ok": False, "reason": "blank"})
        return httpx.Response(200, json={"text": "Dx: I10", "pages": 1, "parser": "pypdf",
                                         "ok": True, "reason": None})

    respx.post(SANDBOX).mock(side_effect=sandbox_response)

    async def never(_):
        pytest.fail("LlamaParse must not run after pypdf succeeded")

    monkeypatch.setattr(chain_module, "parse_via_llamaparse", never)

    result = await parse_document(note)
    assert result.ok and result.parser == "pypdf"
    assert [a.parser for a in result.trail] == ["ocr", "pypdf"]


@respx.mock
async def test_gpu_does_not_ocr_plain_text_first(tmp_path, monkeypatch):
    monkeypatch.setattr(chain_module, "ocr_first", lambda: True)
    note = tmp_path / "note.txt"
    note.write_text("Dx: E11.9")
    order = []

    def sandbox_response(request):
        order.append("ocr" if "ocr=true" in str(request.url) else "text")
        return httpx.Response(200, json={"text": "Dx: E11.9", "pages": 1,
                                         "parser": "text", "ok": True, "reason": None})

    respx.post(SANDBOX).mock(side_effect=sandbox_response)
    monkeypatch.setattr(chain_module, "parse_via_llamaparse",
                        lambda _path: pytest.fail("LlamaParse must not run"))

    result = await parse_document(note)
    assert result.ok and result.parser == "text"
    assert order == ["text"]


@respx.mock
async def test_ocr_request_sends_planned_worker_count(note, monkeypatch):
    monkeypatch.setattr(chain_module, "ocr_first", lambda: True)
    monkeypatch.setattr("app.parsing.sandbox_client.resolve_ocr_workers", lambda: 6)
    seen = {}

    def sandbox_response(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"text": "scanned", "pages": 1,
                                         "parser": "ocr", "ok": True, "reason": None})

    respx.post(SANDBOX).mock(side_effect=sandbox_response)
    monkeypatch.setattr(chain_module, "parse_via_llamaparse",
                        lambda _path: pytest.fail("unused"))

    result = await parse_document(note)
    assert result.ok
    assert "workers=6" in seen["url"]
    assert "ocr=true" in seen["url"]


async def test_llamaparse_without_key_fails_fast(note, monkeypatch):
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("LLAMA_CLOUD_API_KEY", "")
    result = await parse_via_llamaparse(note)
    assert result.ok is False
    assert "key" in (result.trail[0].reason or "").lower()
    get_settings.cache_clear()
