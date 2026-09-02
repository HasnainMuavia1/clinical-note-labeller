import time
from collections import defaultdict, deque

from fastapi import Header

from .config import get_settings
from .errors import ProblemException

_WINDOW_SECONDS = 60
_MAX_REQUESTS = 120
_hits: dict[str, deque[float]] = defaultdict(deque)


def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    settings = get_settings()
    if not x_api_key or x_api_key not in settings.api_keys:
        raise ProblemException(401, "Unauthorized", "A valid X-API-Key header is required.")
    now = time.monotonic()
    bucket = _hits[x_api_key]
    while bucket and now - bucket[0] > _WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= _MAX_REQUESTS:
        raise ProblemException(429, "Too Many Requests", "Rate limit exceeded for this API key.")
    bucket.append(now)
    return x_api_key
