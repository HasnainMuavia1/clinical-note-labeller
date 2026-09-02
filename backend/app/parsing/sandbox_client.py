from __future__ import annotations

from pathlib import Path

import httpx

from ..config import get_settings

TIMEOUT = httpx.Timeout(600.0, connect=10.0)


async def call_sandbox(path: Path, *, ocr: bool = False) -> dict:
    url = f"{get_settings().sandbox_url.rstrip('/')}/parse"
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        with path.open("rb") as fh:
            response = await client.post(
                url, files={"file": (path.name, fh)}, params={"ocr": str(ocr).lower()}
            )
    response.raise_for_status()
    return response.json()
