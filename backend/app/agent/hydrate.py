from __future__ import annotations

import logging
from pathlib import Path

from ..workspace.parse_cache import load as load_cache

log = logging.getLogger(__name__)

_RETRYABLE = (
    "remoteprotocolerror",
    "server disconnected",
    "connecterror",
    "readerror",
    "timeoutexception",
    "can't start new thread",
    "libgomp",
    "resource temporarily unavailable",
    "thread creation failed",
)


def is_retryable(record: dict) -> bool:
    if record.get("skipped") and "not a clinical note" in (record.get("skip_reason") or "").lower():
        return False
    blob = " ".join(
        str(item.get("reason") or "")
        for item in (record.get("parse_trail") or [])
    ).lower()
    blob = f"{blob} {(record.get('skip_reason') or '').lower()}"
    if "no extractable text" in blob and not any(token in blob for token in _RETRYABLE):
        return False
    return any(token in blob for token in _RETRYABLE)


def get_repository():
    from ..db.repository import get_repository as _get
    return _get()


def _row_key(row) -> str:
    return getattr(row, "source_path", None) or getattr(row, "filename", "") or ""


def hydrate_files(job_id: str | None, files: list[dict], root: Path | str) -> list[dict]:
    """Reuse finished work from the DB / parse cache; leave failed files for retry."""
    if not job_id or not files:
        return files
    try:
        rows = get_repository().list_files(job_id)
    except Exception as exc:
        log.warning("hydrate skipped listing files for %s: %s", job_id, exc)
        rows = []

    by_source: dict[str, object] = {}
    by_sha: dict[str, object] = {}
    rank = {"filed": 4, "parsed": 3, "unparsed": 2, "skipped": 2, "pending": 1}
    for row in rows:
        key = _row_key(row)
        if key:
            prior = by_source.get(key)
            if prior is None or rank.get(getattr(row, "status", ""), 0) >= rank.get(
                    getattr(prior, "status", ""), 0):
                by_source[key] = row
        sha = getattr(row, "sha256", None)
        if sha:
            prior = by_sha.get(sha)
            if prior is None or rank.get(getattr(row, "status", ""), 0) >= rank.get(
                    getattr(prior, "status", ""), 0):
                by_sha[sha] = row

    out = []
    for record in files:
        key = record.get("source_path") or record.get("filename") or ""
        row = by_source.get(key) or by_sha.get(record.get("sha256") or "")
        cached = load_cache(root, record.get("sha256") or getattr(row, "sha256", None))
        if cached:
            merged = {
                **record,
                "ok": True,
                "text": cached["text"],
                "parser": cached.get("parser") or record.get("parser"),
                "parse_trail": cached.get("parse_trail") or record.get("parse_trail") or [],
            }
            if row is not None:
                merged["file_id"] = getattr(row, "file_id", None) or record.get("file_id")
                for field in ("specialty", "confidence", "method", "has_codes",
                              "code_hits", "code_rejected", "npis", "output_path"):
                    value = getattr(row, field, None)
                    if value not in (None, [], ""):
                        merged[field] = value
                merged["_codes_done"] = True
            out.append(merged)
            continue
        if row is not None and getattr(row, "status", "") == "parsed" and getattr(row, "parser", None):
            trail = list(getattr(row, "parse_trail", None) or [])
            if row.parser == "ocr":
                out.append({**record, "ok": True, "parser": "ocr", "_already_parsed": True,
                            "parse_trail": trail})
                continue
            out.append({**record, "ok": False, "_resume_parser": row.parser,
                        "parse_trail": trail})
            continue
        if row is not None and getattr(row, "status", "") in {"unparsed", "skipped"}:
            trail = list(getattr(row, "parse_trail", None) or record.get("parse_trail") or [])
            merged = {**record, "parse_trail": trail, "ok": False}
            if is_retryable(merged):
                merged["retry"] = True
            out.append(merged)
            continue
        out.append(record)
    return out
