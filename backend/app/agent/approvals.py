from __future__ import annotations


def approval_payload(kind: str, detail: dict) -> dict:
    """Shape of every interrupt raised by the graph."""
    return {"kind": kind, **detail}


def resume_mapping(answer, key: str) -> dict:
    """Pull a nested dict out of an interrupt resume value.

    Concurrent batch polls can resume the graph with a bare list of labels.
    Callers must not crash on that shape — treat it as an empty override map.
    """
    if not isinstance(answer, dict):
        return {}
    value = answer.get(key) or {}
    return value if isinstance(value, dict) else {}


def decision_for(resume_value: dict | None, key: str, default: str = "reject") -> str:
    return resume_mapping(resume_value, "decisions").get(key, default)
