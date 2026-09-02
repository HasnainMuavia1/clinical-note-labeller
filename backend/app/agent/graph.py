from __future__ import annotations

from itertools import pairwise
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from . import nodes
from .state import JobState

NODE_ORDER = ["intake_node", "unpack_node", "parse_node", "detect_codes_node",
              "resolve_npi_node", "classify_node", "plan_placement_node",
              "approval_gate_node", "execute_ops_node", "manifest_node"]


def _delegate(name: str):
    """Look the node up at call time so tests can monkeypatch module attributes."""

    async def wrapper(state: JobState) -> dict:
        return await getattr(nodes, name)(state)

    wrapper.__name__ = name
    return wrapper


def build_graph(checkpointer):
    graph = StateGraph(JobState)
    for name in NODE_ORDER:
        graph.add_node(name, _delegate(name))
    graph.add_edge(START, NODE_ORDER[0])
    for current, following in pairwise(NODE_ORDER):
        graph.add_edge(current, following)
    graph.add_edge(NODE_ORDER[-1], END)
    return graph.compile(checkpointer=checkpointer)


async def run_job(job_id: str, root: Path, checkpointer) -> JobState:
    graph = build_graph(checkpointer)
    config = {"configurable": {"thread_id": job_id}}
    return await graph.ainvoke({"job_id": job_id, "root": str(root)}, config)


async def resume_job(job_id: str, resume_value, checkpointer) -> JobState:
    from langgraph.types import Command

    graph = build_graph(checkpointer)
    config = {"configurable": {"thread_id": job_id}}
    return await graph.ainvoke(Command(resume=resume_value), config)
