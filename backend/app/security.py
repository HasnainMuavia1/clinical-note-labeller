import time
from collections import defaultdict, deque

from fastapi import Header, Query

from .errors import ProblemException

_WINDOW_SECONDS = 60
_MAX_REQUESTS = 120
_hits: dict[str, deque[float]] = defaultdict(deque)


def require_api_key(
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None),
) -> str:
    """Open access: a key is optional. Anonymous callers share the public bucket."""
    presented = x_api_key or api_key or "public"
    now = time.monotonic()
    bucket = _hits[presented]
    while bucket and now - bucket[0] > _WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= _MAX_REQUESTS:
        raise ProblemException(429, "Too Many Requests", "Rate limit exceeded.")
    bucket.append(now)
    return presented
