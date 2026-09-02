from __future__ import annotations

from contextvars import ContextVar
from typing import Callable

file_progress_listener: ContextVar[Callable[[str, int, int, dict], None] | None] = ContextVar(
    "file_progress_listener", default=None,
)


def report_file_progress(stage: str, done: int, total: int, record: dict) -> None:
    hook = file_progress_listener.get()
    if hook is not None:
        hook(stage, done, total, record)
