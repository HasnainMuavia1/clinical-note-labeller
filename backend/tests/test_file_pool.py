import asyncio
import time

from app.agent.pool import map_files
from app.agent.progress import file_progress_listener


async def test_map_files_runs_workers_in_parallel():
    started = []

    async def worker(record):
        started.append(time.monotonic())
        await asyncio.sleep(0.2)
        return {**record, "ok": True}

    t0 = time.monotonic()
    out = await map_files(
        [{"file_id": "a"}, {"file_id": "b"}, {"file_id": "c"}],
        worker, stage="parse", concurrency=3,
    )
    elapsed = time.monotonic() - t0

    assert [row["file_id"] for row in out] == ["a", "b", "c"]
    assert elapsed < 0.45
    assert max(started) - min(started) < 0.1


async def test_map_files_caps_in_flight_work():
    inflight = 0
    peak = 0

    async def worker(record):
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0.05)
        inflight -= 1
        return record

    await map_files([{"file_id": str(i)} for i in range(8)], worker, stage="parse", concurrency=3)
    assert peak == 3


async def test_map_files_reports_progress_as_each_file_finishes():
    ticks = []

    def listen(stage, done, total, record):
        ticks.append((stage, done, total, record["file_id"]))

    token = file_progress_listener.set(listen)
    try:
        async def worker(record):
            await asyncio.sleep(0.01 if record["file_id"] == "slow" else 0)
            return record

        await map_files(
            [{"file_id": "slow"}, {"file_id": "fast"}],
            worker, stage="parse", concurrency=2,
        )
    finally:
        file_progress_listener.reset(token)

    assert [tick[1] for tick in ticks] == [1, 2]
    assert ticks[0][3] == "fast"
    assert ticks[-1] == ("parse", 2, 2, "slow")
