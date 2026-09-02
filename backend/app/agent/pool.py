from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .progress import report_file_progress

Worker = Callable[[dict], Awaitable[dict]]


async def map_files(
    records: list[dict],
    worker: Worker,
    *,
    stage: str,
    concurrency: int,
) -> list[dict]:
    """Run one async worker per file, capped by a semaphore, preserving input order."""
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
            updated = await worker(record)
        async with lock:
            done += 1
            results[index] = updated
            report_file_progress(stage, done, len(records), updated)

    await asyncio.gather(*(run(index, record) for index, record in enumerate(records)))
    return [row for row in results if row is not None]
