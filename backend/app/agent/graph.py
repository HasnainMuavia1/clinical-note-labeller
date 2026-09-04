from __future__ import annotations

import logging
from contextvars import ContextVar
from itertools import pairwise
from pathlib import Path
from typing import Callable

from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph

from . import nodes
from .pool import skipped
from .state import JobState

log = logging.getLogger(__name__)

# Celery sets this so the UI can stream plan/reasoning after every node.
step_listener: ContextVar[Callable[[str, dict, JobState], None] | None] = ContextVar(
    "step_listener", default=None,
)

NODE_ORDER = ["intake_node", "unpack_node", "parse_node", "detect_codes_node",
              "resolve_npi_node", "classify_node", "plan_placement_node",
              "approval_gate_node", "execute_ops_node", "manifest_node"]


def _delegate(name: str):
    """Look the node up at call time so tests can monkeypatch module attributes."""

    async def wrapper(state: JobState) -> dict:
        try:
            result = await getattr(nodes, name)(state)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            log.exception("node %s failed; skipping files and continuing the job", name)
            result = {
                "files": [skipped(record, name, exc) for record in (state.get("files") or [])],
                "stage": name.removesuffix("_node"),
                "pending_ops": list(state.get("pending_ops") or []),
            }
        listener = step_listener.get()
        if listener is not None:
            try:
                listener(name, result, state)
            except Exception:
                log.exception("step listener failed after %s; continuing", name)
        return result

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
