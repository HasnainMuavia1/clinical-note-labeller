from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from app.agent.graph import run_job
from app.agent.nodes import parse_node
from app.parsing.chain import ParseAttempt, ParseResult


async def test_parse_skips_already_ok_files(tmp_path, monkeypatch):
    calls = []

    async def fake_parse(path):
        calls.append(path.name)
        return ParseResult("new", "pypdf", 1, True, [ParseAttempt("pypdf", True, None)])

    monkeypatch.setattr("app.agent.hydrate.get_repository",
                        lambda: type("R", (), {"list_files": staticmethod(lambda _id: [])})())
    monkeypatch.setattr("app.agent.nodes.parse_document", fake_parse)
    (tmp_path / "b.pdf").write_bytes(b"%PDF")
    out = await parse_node({
        "job_id": "j", "root": str(tmp_path),
        "files": [
            {"file_id": "done", "path": str(tmp_path / "a.pdf"), "filename": "a.pdf",
             "ok": True, "text": "cached", "parser": "pypdf", "parse_trail": []},
            {"file_id": "todo", "path": str(tmp_path / "b.pdf"), "filename": "b.pdf",
             "ok": False, "text": ""},
        ],
    })
    assert calls == ["b.pdf"]
    by_id = {f["file_id"]: f for f in out["files"]}
    assert by_id["done"]["text"] == "cached"


async def test_parse_replays_only_the_winning_parser_on_resume(tmp_path, monkeypatch):
    note = tmp_path / "a.pdf"
    note.write_bytes(b"%PDF")
    hops = []

    async def sandbox(path, ocr=False):
        hops.append("ocr" if ocr else "pypdf")
        return ParseResult("from pypdf", "pypdf", 1, True, [ParseAttempt("pypdf", True, None)])

    async def llama(path):
        hops.append("llamaparse")
        return ParseResult("llama", "llamaparse", 1, True, [ParseAttempt("llamaparse", True, None)])

    async def full(path):
        hops.append("full")
        return ParseResult("full", "pypdf", 1, True, [ParseAttempt("pypdf", True, None)])

    monkeypatch.setattr("app.agent.hydrate.get_repository",
                        lambda: type("R", (), {"list_files": staticmethod(lambda _id: [])})())
    monkeypatch.setattr("app.agent.nodes.parse_via_sandbox", sandbox)
    monkeypatch.setattr("app.agent.nodes.parse_via_llamaparse", llama)
    monkeypatch.setattr("app.agent.nodes.parse_document", full)
    out = await parse_node({
        "job_id": "j", "root": str(tmp_path),
        "files": [{"file_id": "f1", "path": str(note), "filename": "a.pdf",
                   "ok": False, "_resume_parser": "pypdf", "sha256": "x"}],
    })
    assert hops == ["pypdf"]
    assert out["files"][0]["text"] == "from pypdf"


async def test_parse_retries_a_transient_sandbox_failure(tmp_path, monkeypatch):
    note = tmp_path / "scan.pdf"
    note.write_bytes(b"%PDF")
    hits = {"n": 0}

    async def flaky(path):
        hits["n"] += 1
        if hits["n"] < 3:
            return ParseResult("", "ocr", 0, False, [
                ParseAttempt("ocr", False, "RemoteProtocolError: Server disconnected"),
            ])
        return ParseResult("rescued", "ocr", 1, True, [ParseAttempt("ocr", True, None)])

    monkeypatch.setattr("app.agent.hydrate.get_repository",
                        lambda: type("R", (), {"list_files": staticmethod(lambda _id: [])})())
    monkeypatch.setattr("app.agent.nodes.parse_document", flaky)
    out = await parse_node({
        "job_id": "j", "root": str(tmp_path),
        "files": [{"file_id": "f1", "path": str(note), "filename": "scan.pdf", "ok": False}],
    })
    assert out["files"][0]["ok"] is True
    assert out["files"][0]["text"] == "rescued"
    assert hits["n"] >= 3


async def test_rerunning_a_job_does_not_reparse_cached_files(tmp_path, monkeypatch):
    from app.agent import nodes as node_module
    from app.workspace.parse_cache import save

    root = tmp_path / "job-resume"
    (root / "input").mkdir(parents=True)
    note = root / "input" / "note.txt"
    note.write_text("Dx: E11.9\nProcedure Code: 99213")
    digest = __import__("hashlib").sha256(note.read_bytes()).hexdigest()
    save(root, digest, {
        "text": note.read_text(), "parser": "text", "ok": True,
        "parse_trail": [{"parser": "text", "ok": True, "reason": None}],
    })

    parses = []

    async def fake_parse(path):
        parses.append(path.name)
        return ParseResult(path.read_text(), "text", 1, True, [ParseAttempt("text", True, None)])

    async def fake_classify(state):
        files = [{**f, "specialty": "Cardiology", "confidence": 0.9, "method": "llm_sync"}
                 for f in state["files"]]
        return {"files": files, "stage": "classify"}

    monkeypatch.setattr(node_module, "parse_document", fake_parse)
    monkeypatch.setattr(node_module, "classify_node", fake_classify)
    monkeypatch.setattr("app.agent.hydrate.get_repository",
                        lambda: type("R", (), {"list_files": staticmethod(lambda _id: [])})())

    saver = MemorySaver()
    await run_job("job-resume", root, saver)
    first = list(parses)
    await run_job("job-resume", root, saver)
    # Second start may continue from checkpoint (no extra parse) or hydrate from cache.
    assert parses == first or "note.txt" not in parses[len(first):]
