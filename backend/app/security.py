import time
from collections import defaultdict, deque

from fastapi import Header, Query, Request

from .errors import ProblemException

_WINDOW_SECONDS = 60
_MAX_WRITES = 120
_hits: dict[str, deque[float]] = defaultdict(deque)
_READS = {"GET", "HEAD", "OPTIONS"}


def reset_rate_limit() -> None:
    _hits.clear()


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None),
    api_key: str | None = Query(default=None),
) -> str:
    """Open access: a key is optional. Anonymous callers share the public bucket.

    Live job polling is GET-heavy (SSE refresh, files, tree). Those reads are
    not counted. Writes stay capped so a client cannot flood job creation.
    """
    presented = x_api_key or api_key or "public"
    if request.method in _READS:
        return presented

    now = time.monotonic()
    bucket = _hits[presented]
    while bucket and now - bucket[0] > _WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= _MAX_WRITES:
        raise ProblemException(429, "Too Many Requests", "Rate limit exceeded.")
    bucket.append(now)
    return presented
