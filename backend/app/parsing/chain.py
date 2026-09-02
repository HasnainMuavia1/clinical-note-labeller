from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings
from .llamaparse import llamaparse_text
from .sandbox_client import call_sandbox

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParseAttempt:
    parser: str
    ok: bool
    reason: str | None


@dataclass(frozen=True)
class ParseResult:
    text: str
    parser: str
    pages: int
    ok: bool
    trail: list[ParseAttempt] = field(default_factory=list)


async def parse_via_sandbox(path: Path, *, ocr: bool = False) -> ParseResult:
    try:
        payload = await call_sandbox(path, ocr=ocr)
    except Exception as exc:  # noqa: BLE001
        parser = "ocr" if ocr else "sandbox"
        return ParseResult("", parser, 0, False,
                           [ParseAttempt(parser, False, f"{type(exc).__name__}: {exc}")])

    parser = "ocr" if ocr else (payload.get("parser") or "sandbox")
    if parser == "none" and not ocr:
        parser = "sandbox"
    attempt = ParseAttempt(parser, bool(payload.get("ok")), payload.get("reason"))
    return ParseResult(payload.get("text", ""), parser, int(payload.get("pages", 0)),
                       attempt.ok, [attempt])


async def parse_via_llamaparse(path: Path) -> ParseResult:
    if not get_settings().llama_cloud_api_key:
        return ParseResult("", "llamaparse", 0, False,
                           [ParseAttempt("llamaparse", False,
                                         "LLAMA_CLOUD_API_KEY is not configured")])
    try:
        text = await llamaparse_text(path)
    except Exception as exc:  # noqa: BLE001
        return ParseResult("", "llamaparse", 0, False,
                           [ParseAttempt("llamaparse", False, f"{type(exc).__name__}: {exc}")])
    ok = bool(text.strip())
    return ParseResult(text, "llamaparse", 1, ok,
                       [ParseAttempt("llamaparse", ok, None if ok else "no extractable text")])


async def parse_document(path: Path) -> ParseResult:
    """pypdf/docx/text -> LlamaParse -> OCR -> failure. Records every hop."""
    import sys

    module = sys.modules[__name__]
    trail: list[ParseAttempt] = []

    primary = await module.parse_via_sandbox(path)
    trail.extend(primary.trail)
    if primary.ok:
        return ParseResult(primary.text, primary.parser, primary.pages, True, trail)

    secondary = await module.parse_via_llamaparse(path)
    trail.extend(secondary.trail)
    if secondary.ok:
        return ParseResult(secondary.text, secondary.parser, secondary.pages, True, trail)

    tertiary = await module.parse_via_sandbox(path, ocr=True)
    trail.extend(tertiary.trail)
    if tertiary.ok:
        return ParseResult(tertiary.text, "ocr", tertiary.pages, True, trail)

    log.warning("all parsers failed for %s", path.name)
    return ParseResult("", "none", 0, False, trail)
