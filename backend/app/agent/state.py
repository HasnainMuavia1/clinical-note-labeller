from __future__ import annotations

from typing import TypedDict


class JobState(TypedDict, total=False):
    job_id: str
    root: str
    stage: str
    files: list[dict]
    pending_ops: list[dict]
    batch_id: str | None
    manifest: list[dict]
    error: str | None
