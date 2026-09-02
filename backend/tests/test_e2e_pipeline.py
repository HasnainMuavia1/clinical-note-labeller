"""End-to-end: mixed upload -> parse -> detect -> classify -> filed output tree.

The OpenAI and NPI calls are stubbed; everything else is the real pipeline.
"""
import csv
import json
import zipfile
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.agent import nodes as node_module
from app.agent.graph import run_job


@pytest.fixture()
def workspace(tmp_path):
    root = tmp_path / "job-e2e"
    inbox = root / "input"
    inbox.mkdir(parents=True)
    (inbox / "cardiac.txt").write_text(
        "ASSESSMENT\nDiagnosis Code: I10 essential hypertension\nProcedure Code: 99213-25\n")
    (inbox / "narrative.txt").write_text(
        "The patient is a 45 year old who lives in Beverly Hills, CA 90210 "
        "and reports a headache.\n")
    with zipfile.ZipFile(inbox / "bundle.zip", "w") as zf:
        zf.writestr("derm/rash.txt", "Diagnosis Code: L20.9 atopic dermatitis. CPT: 11100 biopsy.")
    return root


@pytest.fixture()
def stub_external(monkeypatch):
    async def fake_classify_node(state):
        mapping = {"cardiac.txt": "Cardiology", "narrative.txt": "Family Medicine",
                   "rash.txt": "Dermatology"}
        files = [{**f, "specialty": mapping.get(f["filename"], "Unclassified"),
                  "confidence": 0.95, "method": "llm_sync"} for f in state["files"]]
        return {"files": files, "stage": "classify"}

    async def fake_resolve_npi_node(state):
        return {"files": state["files"], "stage": "resolve_npi"}

    async def fake_parse_node(state):
        files = [{**f, "text": Path(f["path"]).read_text(), "ok": True, "parser": "text",
                  "parse_trail": [{"parser": "text", "ok": True, "reason": None}]}
                 for f in state["files"]]
        return {"files": files, "stage": "parse"}

    monkeypatch.setattr(node_module, "classify_node", fake_classify_node)
    monkeypatch.setattr(node_module, "resolve_npi_node", fake_resolve_npi_node)
    monkeypatch.setattr(node_module, "parse_node", fake_parse_node)


async def test_full_pipeline_produces_the_expected_output_tree(workspace, stub_external):
    await run_job("job-e2e", workspace, MemorySaver())
    output = workspace / "output"

    assert (output / "with-codes" / "Cardiology" / "cardiac.txt").exists()
    assert (output / "with-codes" / "Dermatology" / "rash.txt").exists()
    assert (output / "without-codes" / "Family-Medicine" / "narrative.txt").exists()


async def test_zip_contents_are_flattened_but_source_is_recorded(workspace, stub_external):
    await run_job("job-e2e", workspace, MemorySaver())
    records = [json.loads(line)
               for line in (workspace / "output" / "manifest.jsonl").read_text().splitlines()]
    rash = next(r for r in records if r["filename"] == "rash.txt")
    assert rash["source_path"] == "bundle.zip!/derm/rash.txt"
    assert rash["output_path"] == "output/with-codes/Dermatology/rash.txt"


async def test_the_zip_itself_is_not_filed(workspace, stub_external):
    await run_job("job-e2e", workspace, MemorySaver())
    assert not list((workspace / "output").rglob("bundle.zip"))


async def test_labels_csv_covers_every_file(workspace, stub_external):
    await run_job("job-e2e", workspace, MemorySaver())
    rows = list(csv.DictReader((workspace / "output" / "labels.csv").open()))
    assert {r["filename"] for r in rows} == {"cardiac.txt", "narrative.txt", "rash.txt"}


async def test_input_folder_is_untouched(workspace, stub_external):
    before = {p.name for p in (workspace / "input").iterdir()}
    await run_job("job-e2e", workspace, MemorySaver())
    assert {p.name for p in (workspace / "input").iterdir()} == before


async def test_audit_log_records_every_executed_operation(workspace, stub_external):
    await run_job("job-e2e", workspace, MemorySaver())
    entries = [json.loads(line)
               for line in (workspace / "logs" / "audit.jsonl").read_text().splitlines()]
    assert len([e for e in entries if e["action"] == "op_executed"]) == 3
