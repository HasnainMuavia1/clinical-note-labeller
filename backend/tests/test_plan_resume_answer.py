"""plan_placement must tolerate a non-dict interrupt resume value.

A concurrent OpenAI batch poll can resume the graph with a list of
classifications while plan_placement is parked on low_confidence. That used
to raise AttributeError and mass-skip every file.
"""
from __future__ import annotations

import pytest

from app.agent.nodes import plan_placement_node, approval_gate_node


@pytest.fixture()
def workspace(tmp_path):
    root = tmp_path / "job-1"
    (root / "input").mkdir(parents=True)
    (root / "input" / "note.txt").write_text("Dx: E11.9")
    return root


async def test_plan_placement_accepts_list_resume_from_interrupt(workspace, monkeypatch):
    monkeypatch.setattr(
        "app.agent.nodes.interrupt",
        lambda payload: [{"file_id": "x", "specialty": "Cardiology"}],
    )
    files = [{
        "file_id": "f1",
        "filename": "note.txt",
        "path": str(workspace / "input" / "note.txt"),
        "has_codes": True,
        "specialty": "Cardiology",
        "confidence": 0.2,
        "ok": True,
        "method": "llm_sync",
    }]
    out = await plan_placement_node({"files": files, "root": str(workspace), "job_id": "job-1"})
    assert out["pending_ops"]
    assert out["files"][0].get("skipped") is not True
    assert "AttributeError" not in (out["files"][0].get("skip_reason") or "")


async def test_plan_placement_uses_specialties_dict_when_present(workspace, monkeypatch):
    monkeypatch.setattr(
        "app.agent.nodes.interrupt",
        lambda payload: {"specialties": {"f1": "Neurology"}},
    )
    files = [{
        "file_id": "f1",
        "filename": "note.txt",
        "path": str(workspace / "input" / "note.txt"),
        "has_codes": False,
        "specialty": "Unclassified",
        "confidence": 0.1,
        "ok": True,
        "method": "llm_sync",
    }]
    out = await plan_placement_node({"files": files, "root": str(workspace), "job_id": "job-1"})
    assert out["files"][0]["specialty"] == "Neurology"
    assert out["files"][0]["method"] == "human"
    assert any("Neurology" in op["target"] for op in out["pending_ops"])


async def test_approval_gate_accepts_list_resume_from_interrupt(workspace, monkeypatch):
    monkeypatch.setattr(
        "app.agent.nodes.interrupt",
        lambda payload: ["not", "a", "dict"],
    )
    ops = [{
        "op": "overwrite",
        "source": str(workspace / "input" / "note.txt"),
        "target": "output/with-codes/Cardiology/note.txt",
        "reason": "exists",
        "file_id": "f1",
    }]
    out = await approval_gate_node({"pending_ops": ops, "root": str(workspace), "job_id": "job-1"})
    # Empty/list resume ⇒ nothing approved ⇒ overwrite becomes a safe copy.
    assert out["pending_ops"][0]["op"] == "copy"
    assert "AttributeError" not in str(out)
