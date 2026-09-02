from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from app.agent import nodes as node_module
from app.agent.graph import build_graph


@pytest.fixture()
def workspace(tmp_path):
    root = tmp_path / "job-1"
    (root / "input").mkdir(parents=True)
    (root / "input" / "coded.txt").write_text("Diagnosis Code: E11.9\nProcedure Code: 99213")
    (root / "input" / "plain.txt").write_text(
        "Patient reports a mild headache and is otherwise well.")
    return root


@pytest.fixture()
def stub_nodes(monkeypatch):
    async def fake_classify_node(state):
        files = [{**f, "specialty": "Cardiology", "confidence": 0.95, "method": "llm_sync"}
                 for f in state["files"]]
        return {"files": files, "stage": "classify"}

    async def fake_parse_node(state):
        files = [{**f, "text": Path(f["path"]).read_text(), "ok": True, "parser": "text",
                  "parse_trail": [{"parser": "text", "ok": True, "reason": None}]}
                 for f in state["files"]]
        return {"files": files, "stage": "parse"}

    monkeypatch.setattr(node_module, "classify_node", fake_classify_node)
    monkeypatch.setattr(node_module, "parse_node", fake_parse_node)


async def test_graph_runs_end_to_end_and_files_both_branches(workspace, stub_nodes):
    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "job-1"}}
    result = await graph.ainvoke({"job_id": "job-1", "root": str(workspace)}, config)

    assert (workspace / "output" / "with-codes" / "Cardiology" / "coded.txt").exists()
    assert (workspace / "output" / "without-codes" / "Cardiology" / "plain.txt").exists()
    assert (workspace / "output" / "manifest.jsonl").exists()
    assert (workspace / "output" / "labels.csv").exists()
    assert len(result["manifest"]) == 2


async def test_graph_interrupts_when_an_op_needs_approval_and_resumes(workspace, stub_nodes):
    target = workspace / "output" / "with-codes" / "Cardiology" / "coded.txt"
    target.parent.mkdir(parents=True)
    target.write_text("existing")

    graph = build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "job-2"}}
    result = await graph.ainvoke({"job_id": "job-2", "root": str(workspace)}, config)

    assert "__interrupt__" in result
    payload = result["__interrupt__"][0].value
    assert payload["kind"] == "overwrite"

    resumed = await graph.ainvoke(
        Command(resume={"decisions": {payload["ops"][0]["target"]: "approve"}}), config)
    assert target.read_text().startswith("Diagnosis Code")
    assert len(resumed["manifest"]) == 2
