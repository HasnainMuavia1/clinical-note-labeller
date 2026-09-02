"""LlamaParse adapter.

Talks to the LlamaCloud REST API directly. The official SDK pulls the whole
llama-index stack (numpy, pillow, nltk, networkx) for what is an upload, a poll
and a fetch, which is ~155 MB the client would have to download.

Requires network egress, so this runs in the worker, never in the parser sandbox.
"""
from __future__ import annotations

import asyncio
import logging
import random
from pathlib import Path

import httpx

from ..config import get_settings
from .rate_limit import SlidingWindowLimiter, limits_for_tier

BASE_URL = "https://api.cloud.llamaindex.ai/api/v1/parsing"
UPLOAD_TIMEOUT = httpx.Timeout(300.0, connect=15.0)
POLL_TIMEOUT = httpx.Timeout(60.0, connect=15.0)
POLL_INTERVAL_SECONDS = 1.0
MAX_POLL_SECONDS = 900
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 6

_limiter: SlidingWindowLimiter | None = None


def reset_upload_limiter() -> None:
    global _limiter
    _limiter = None


def get_upload_limiter() -> SlidingWindowLimiter:
    global _limiter
    max_requests, window = limits_for_tier(get_settings().llama_parse_tier)
    if _limiter is None or _limiter.max_requests != max_requests or _limiter.window_seconds != window:
        _limiter = SlidingWindowLimiter(max_requests, window)
    return _limiter


async def _request_with_backoff(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    limit: bool = False,
    **kwargs,
) -> httpx.Response:
    delay = 1.0
    response: httpx.Response | None = None
    for attempt in range(MAX_RETRIES):
        if limit:
            await get_upload_limiter().acquire()
        response = await client.request(method, url, **kwargs)
        if response.status_code not in RETRY_STATUSES:
            return response
        if attempt == MAX_RETRIES - 1:
            break
        await asyncio.sleep(delay * (0.5 + random.random()))
        delay = min(delay * 2, 32)
    assert response is not None
    return response


class LlamaParseError(RuntimeError):
    """The LlamaCloud API rejected the document or never finished parsing it."""


async def llamaparse_text(path: Path) -> str:
    """Upload a document, wait for the job, and return its extracted text."""
    settings = get_settings()
    if not settings.llama_cloud_api_key:
        raise LlamaParseError("LLAMA_CLOUD_API_KEY is not configured")

    headers = {"Authorization": f"Bearer {settings.llama_cloud_api_key}",
               "accept": "application/json"}

    async with httpx.AsyncClient(timeout=UPLOAD_TIMEOUT, headers=headers) as client:
        payload = path.read_bytes()
        response = await _request_with_backoff(
            client, "POST", f"{BASE_URL}/upload",
            limit=True, files={"file": (path.name, payload)},
        )
        if response.status_code >= 400:
            raise LlamaParseError(f"upload failed: HTTP {response.status_code} {response.text[:200]}")

        job_id = response.json().get("id")
        if not job_id:
            raise LlamaParseError("upload response contained no job id")

        client.timeout = POLL_TIMEOUT
        waited = 0.0
        while waited < MAX_POLL_SECONDS:
            status_response = await _request_with_backoff(
                client, "GET", f"{BASE_URL}/job/{job_id}")
            if status_response.status_code >= 400:
                raise LlamaParseError(
                    f"status failed: HTTP {status_response.status_code}")
            status = (status_response.json().get("status") or "").upper()
            if status in {"SUCCESS", "COMPLETED", "PARTIAL_SUCCESS"}:
                break
            if status in {"ERROR", "FAILED", "CANCELLED"}:
                raise LlamaParseError(f"job {job_id} finished as {status}")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            waited += POLL_INTERVAL_SECONDS
        else:
            raise LlamaParseError(f"job {job_id} did not finish within {MAX_POLL_SECONDS}s")

        result = await _request_with_backoff(
            client, "GET", f"{BASE_URL}/job/{job_id}/result/text")
        if result.status_code >= 400:
            raise LlamaParseError(f"result fetch failed: HTTP {result.status_code}")
        return result.json().get("text", "")


log = logging.getLogger(__name__)
