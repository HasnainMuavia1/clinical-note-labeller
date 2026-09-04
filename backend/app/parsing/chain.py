from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings
from .llamaparse import llamaparse_text
from .sandbox_client import call_sandbox

OCR_FIRST_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}

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


def ocr_first() -> bool:
    """Tesseract-first only when a CUDA GPU can actually reach the sandbox."""
    try:
        from ..runtime.hardware import detect_gpus
        return any(gpu.backend == "cuda" for gpu in detect_gpus())
    except Exception:
        return False


def should_ocr_first(path: Path) -> bool:
    return ocr_first() and path.suffix.lower() in OCR_FIRST_SUFFIXES


def _take(trail: list[ParseAttempt], result: ParseResult) -> ParseResult | None:
    trail.extend(result.trail)
    if result.ok:
        parser = "ocr" if result.parser == "ocr" else result.parser
        return ParseResult(result.text, parser, result.pages, True, trail)
    return None


async def parse_document(path: Path) -> ParseResult:
    """CPU: pypdf → LlamaParse → Tesseract. GPU: Tesseract → pypdf → LlamaParse."""
    import sys

    module = sys.modules[__name__]
    trail: list[ParseAttempt] = []

    if module.should_ocr_first(path):
        hops = (
            lambda: module.parse_via_sandbox(path, ocr=True),
            lambda: module.parse_via_sandbox(path),
            lambda: module.parse_via_llamaparse(path),
        )
    else:
        hops = (
            lambda: module.parse_via_sandbox(path),
            lambda: module.parse_via_llamaparse(path),
            lambda: module.parse_via_sandbox(path, ocr=True),
        )

    for hop in hops:
        won = _take(trail, await hop())
        if won is not None:
            return won

    log.warning("all parsers failed for %s", path.name)
    return ParseResult("", "none", 0, False, trail)
