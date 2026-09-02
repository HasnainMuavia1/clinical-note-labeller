from __future__ import annotations


def approval_payload(kind: str, detail: dict) -> dict:
    """Shape of every interrupt raised by the graph."""
    return {"kind": kind, **detail}


def decision_for(resume_value: dict | None, key: str, default: str = "reject") -> str:
    if not resume_value:
        return default
    return (resume_value.get("decisions") or {}).get(key, default)
