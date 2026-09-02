"""LlamaCloud parse-upload limits from the official rate-limit docs.

https://developers.llamaindex.ai/llamaparse/general/rate_limits/

Parse Upload is 50 requests / 10 seconds on POST /api/v1/parsing/upload.
Free-tier orgs are 20 requests / minute. 429s have no Retry-After.
"""
from __future__ import annotations

import asyncio
import time

# Official Parse Upload window (paid / standard). Free is 20/min.
TIER_LIMITS: dict[str, tuple[int, float]] = {
    "free": (20, 60.0),
    "standard": (50, 10.0),
}


def limits_for_tier(tier: str) -> tuple[int, float]:
    return TIER_LIMITS.get((tier or "standard").lower(), TIER_LIMITS["standard"])


class SlidingWindowLimiter:
    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._times: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                cutoff = now - self.window_seconds
                self._times = [stamp for stamp in self._times if stamp > cutoff]
                if len(self._times) < self.max_requests:
                    self._times.append(now)
                    return
                wait = self.window_seconds - (now - self._times[0]) + 0.01
            await asyncio.sleep(max(wait, 0.01))
