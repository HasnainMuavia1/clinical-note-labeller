from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import httpx

from ..config import get_settings
from .ocr_gate import acquire_ocr_slot

log = logging.getLogger(__name__)

PARSE_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
OCR_TIMEOUT = httpx.Timeout(1800.0, connect=10.0)
SANDBOX_RETRIES = 3
_TRANSIENT = (
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.ConnectError,
    httpx.WriteError,
    httpx.TimeoutException,
)


def resolve_ocr_workers() -> int:
    try:
        from ..runtime.capacity import resolve_capacity
        return max(1, resolve_capacity().ocr_workers)
    except Exception:
        return 1


def _ocr_page_workers() -> int:
    try:
        from ..runtime.capacity import resolve_capacity
        return max(1, resolve_capacity().ocr_page_workers)
    except Exception:
        return 2


async def call_sandbox(path: Path, *, ocr: bool = False) -> dict:
    url = f"{get_settings().sandbox_url.rstrip('/')}/parse"
    params = {"ocr": str(ocr).lower()}
    if ocr:
        params["workers"] = str(_ocr_page_workers())

    async def post() -> dict:
        last_exc: Exception | None = None
        timeout = OCR_TIMEOUT if ocr else PARSE_TIMEOUT
        for attempt in range(1, SANDBOX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    with path.open("rb") as fh:
                        response = await client.post(
                            url, files={"file": (path.name, fh)}, params=params
                        )
                response.raise_for_status()
                return response.json()
            except _TRANSIENT as exc:
                last_exc = exc
                log.warning("sandbox %s on %s (attempt %s/%s)",
                            type(exc).__name__, path.name, attempt, SANDBOX_RETRIES)
                if ocr and isinstance(exc, httpx.TimeoutException):
                    raise
                if attempt < SANDBOX_RETRIES:
                    await asyncio.sleep(0.4 * attempt)
        assert last_exc is not None
        raise last_exc

    if ocr:
        async with acquire_ocr_slot():
            return await post()
    return await post()
