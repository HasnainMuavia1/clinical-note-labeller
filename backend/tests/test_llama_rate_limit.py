import asyncio
import time

from app.parsing.rate_limit import SlidingWindowLimiter, limits_for_tier


def test_standard_tier_matches_official_parse_upload_limit():
    max_requests, window = limits_for_tier("standard")
    assert max_requests == 50
    assert window == 10.0


def test_free_tier_matches_official_per_minute_limit():
    max_requests, window = limits_for_tier("free")
    assert max_requests == 20
    assert window == 60.0


async def test_limiter_allows_a_full_window_then_waits():
    limiter = SlidingWindowLimiter(max_requests=2, window_seconds=0.25)
    t0 = time.monotonic()
    await limiter.acquire()
    await limiter.acquire()
    await limiter.acquire()
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.2


async def test_limiter_does_not_block_under_the_cap():
    limiter = SlidingWindowLimiter(max_requests=5, window_seconds=1.0)
    t0 = time.monotonic()
    await asyncio.gather(*(limiter.acquire() for _ in range(4)))
    assert time.monotonic() - t0 < 0.15
