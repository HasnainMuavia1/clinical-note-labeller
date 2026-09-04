from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from .progress import report_file_progress

log = logging.getLogger(__name__)
Worker = Callable[[dict], Awaitable[dict]]


def skipped(record: dict, stage: str, exc: BaseException) -> dict:
    filename = record.get("filename") or record.get("path") or record.get("file_id") or "unknown"
    reason = f"{filename}: {type(exc).__name__}: {exc}"
    trail = list(record.get("parse_trail") or [])
    trail.append({
        "parser": stage, "ok": False, "reason": reason,
        "filename": filename, "skipped": True,
    })
    return {
        **record,
        "ok": False,
        "skipped": True,
        "skip_reason": reason,
        "parse_trail": trail,
    }


async def map_files(
    records: list[dict],
    worker: Worker,
    *,
    stage: str,
    concurrency: int,
) -> list[dict]:
    """Run one async worker per file, capped by a semaphore, preserving input order.

    A worker exception skips that file and does not cancel the rest.
    """
    if not records:
        return []

    limit = max(1, concurrency)
    semaphore = asyncio.Semaphore(limit)
    done = 0
    lock = asyncio.Lock()
    results: list[dict | None] = [None] * len(records)

    async def run(index: int, record: dict) -> None:
        nonlocal done
        async with semaphore:
            try:
                updated = await worker(record)
            except Exception as exc:
                log.warning("skipping %s at %s: %s",
                            record.get("filename") or record.get("file_id"), stage, exc)
                updated = skipped(record, stage, exc)
        async with lock:
            done += 1
            results[index] = updated
            try:
                report_file_progress(stage, done, len(records), updated)
            except Exception:
                log.exception("progress hook failed after %s; continuing",
                              record.get("filename") or record.get("file_id"))

    gathered = await asyncio.gather(
        *(run(index, record) for index, record in enumerate(records)),
        return_exceptions=True,
    )
    for index, item in enumerate(gathered):
        if isinstance(item, BaseException):
            log.warning("file worker crashed at %s: %s", stage, item)
            record = records[index]
            results[index] = skipped(record, stage, item if isinstance(item, Exception)
                                     else RuntimeError(str(item)))
    return [row for row in results if row is not None]
