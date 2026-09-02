import asyncio
import zipfile

import pytest

from app.agent.nodes import (
    detect_codes_node,
    execute_ops_node,
    intake_node,
    manifest_node,
    parse_node,
    plan_placement_node,
    unpack_node,
)
from app.parsing.chain import ParseAttempt, ParseResult


@pytest.fixture()
def workspace(tmp_path):
    root = tmp_path / "job-1"
    (root / "input").mkdir(parents=True)
    (root / "input" / "note.txt").write_text("Dx: E11.9\nProcedure Code: 99213")
    (root / "input" / "story.txt").write_text(
        "The patient is 45 and lives in Beverly Hills, CA 90210.")
    return root


async def test_intake_enumerates_input_files(workspace):
    out = await intake_node({"job_id": "job-1", "root": str(workspace)})
    assert len(out["files"]) == 2
    assert all(f["sha256"] and f["file_id"] for f in out["files"])


async def test_unpack_expands_a_zip_and_records_source_path(tmp_path):
    root = tmp_path / "job-2"
    (root / "input").mkdir(parents=True)
    with zipfile.ZipFile(root / "input" / "bundle.zip", "w") as zf:
        zf.writestr("cardio/a.txt", "Dx: I10")
    state = await intake_node({"job_id": "job-2", "root": str(root)})
    out = await unpack_node({**state, "job_id": "job-2", "root": str(root)})
    names = {f["filename"] for f in out["files"]}
    assert "a.txt" in names
    assert "bundle.zip" not in names
    entry = next(f for f in out["files"] if f["filename"] == "a.txt")
    assert entry["source_path"] == "bundle.zip!/cardio/a.txt"


async def test_parse_node_keeps_several_files_in_flight(workspace, monkeypatch):
    inflight = 0
    peak = 0

    async def fake_parse(path):
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0.08)
        inflight -= 1
        return ParseResult("text", "text", 1, True, [ParseAttempt("text", True, None)])

    monkeypatch.setattr("app.agent.nodes.parse_document", fake_parse)
    monkeypatch.setattr("app.agent.nodes.get_settings", lambda: type("S", (), {"file_concurrency": 4})())

    files = [
        {"file_id": "a", "path": str(workspace / "input" / "note.txt"), "ok": False},
        {"file_id": "b", "path": str(workspace / "input" / "story.txt"), "ok": False},
        {"file_id": "c", "path": str(workspace / "input" / "note.txt"), "ok": False},
    ]
    out = await parse_node({"files": files, "root": str(workspace)})
    assert [row["file_id"] for row in out["files"]] == ["a", "b", "c"]
    assert all(row["ok"] for row in out["files"])
    assert peak >= 3


async def test_detect_codes_splits_coded_and_uncoded(workspace):
    files = [
        {"file_id": "f1", "text": "Dx: E11.9\nProcedure Code: 99213", "ok": True},
        {"file_id": "f2", "text": "The patient is 45 and lives in Beverly Hills, CA 90210.",
         "ok": True},
    ]
    out = await detect_codes_node({"files": files, "root": str(workspace)})
    by_id = {f["file_id"]: f for f in out["files"]}
    assert by_id["f1"]["has_codes"] is True
    assert by_id["f2"]["has_codes"] is False


async def test_plan_placement_builds_branch_and_specialty_paths(workspace):
    files = [
        {"file_id": "f1", "filename": "a.txt", "path": str(workspace / "input" / "note.txt"),
         "has_codes": True, "specialty": "Cardiology", "confidence": 0.9, "ok": True,
         "method": "llm_sync"},
        {"file_id": "f2", "filename": "b.txt", "path": str(workspace / "input" / "story.txt"),
         "has_codes": False, "specialty": "Obstetrics & Gynecology", "confidence": 0.9,
         "ok": True, "method": "llm_sync"},
    ]
    out = await plan_placement_node({"files": files, "root": str(workspace), "job_id": "job-1"})
    targets = {op["target"] for op in out["pending_ops"]}
    assert "output/with-codes/Cardiology/a.txt" in targets
    assert "output/without-codes/Obstetrics-and-Gynecology/b.txt" in targets


async def test_unparsed_files_are_planned_into_quarantine(workspace):
    files = [{"file_id": "f9", "filename": "bad.pdf",
              "path": str(workspace / "input" / "note.txt"), "ok": False, "has_codes": False,
              "specialty": None, "confidence": 0.0, "method": None}]
    out = await plan_placement_node({"files": files, "root": str(workspace), "job_id": "job-1"})
    assert out["pending_ops"][0]["target"] == "output/unparsed/bad.pdf"


async def test_execute_ops_writes_the_files(workspace):
    ops = [{"op": "copy", "source": str(workspace / "input" / "note.txt"),
            "target": "output/with-codes/Cardiology/note.txt", "reason": "coded", "file_id": "f1"}]
    out = await execute_ops_node({"pending_ops": ops, "root": str(workspace), "job_id": "job-1",
                                  "files": [{"file_id": "f1"}]})
    assert (workspace / "output" / "with-codes" / "Cardiology" / "note.txt").exists()
    assert out["files"][0]["output_path"] == "output/with-codes/Cardiology/note.txt"


async def test_manifest_writes_a_sibling_output_zip(workspace):
    dest = workspace / "output" / "with-codes" / "Cardiology"
    dest.mkdir(parents=True)
    (dest / "note.txt").write_text("ok")
    await manifest_node({
        "job_id": workspace.name,
        "root": str(workspace),
        "files": [{
            "file_id": "f1", "filename": "note.txt", "ok": True, "has_codes": True,
            "specialty": "Cardiology", "confidence": 0.9, "method": "npi",
        }],
    })
    zip_path = workspace.parent / f"{workspace.name}-output.zip"
    assert zip_path.is_file()
    with zipfile.ZipFile(zip_path) as zf:
        assert "with-codes/Cardiology/note.txt" in zf.namelist()
