"""Process-wide OCR slot limiter so parallel parse workers share one Tesseract cap."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

log = logging.getLogger(__name__)
OCR_KEY = "labeller:ocr-inflight"
OCR_TTL_SECONDS = 600

_memory_slots: asyncio.Semaphore | None = None


def reset_ocr_gate() -> None:
    global _memory_slots
    _memory_slots = None


def _redis():
    import redis
    from ..config import get_settings
    return redis.Redis.from_url(get_settings().redis_url)


def _limit() -> int:
    try:
        from ..runtime.capacity import resolve_capacity
        return max(1, resolve_capacity().ocr_inflight)
    except Exception:
        return 5


def _memory_gate() -> asyncio.Semaphore:
    global _memory_slots
    if _memory_slots is None:
        _memory_slots = asyncio.Semaphore(_limit())
    return _memory_slots


@asynccontextmanager
async def acquire_ocr_slot():
    try:
        client = _redis()
        client.ping()
    except Exception:
        async with _memory_gate():
            yield
        return

    limit = _limit()
    while True:
        held = int(client.incr(OCR_KEY))
        if held <= limit:
            client.expire(OCR_KEY, OCR_TTL_SECONDS)
            break
        client.decr(OCR_KEY)
        await asyncio.sleep(0.15)
    try:
        yield
    finally:
        try:
            client.decr(OCR_KEY)
        except Exception:
            log.exception("ocr gate release failed")
