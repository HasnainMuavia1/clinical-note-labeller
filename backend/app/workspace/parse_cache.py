from __future__ import annotations

import json
from pathlib import Path


def _path(root: Path, sha256: str) -> Path:
    return Path(root) / "cache" / f"{sha256}.json"


def save(root: Path, sha256: str | None, payload: dict) -> None:
    if not sha256:
        return
    path = _path(root, sha256)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "text": payload.get("text") or "",
        "parser": payload.get("parser"),
        "ok": bool(payload.get("ok")),
        "parse_trail": payload.get("parse_trail") or [],
    }), encoding="utf-8")


def load(root: Path, sha256: str | None) -> dict | None:
    if not sha256:
        return None
    path = _path(root, sha256)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not data.get("ok") or not (data.get("text") or "").strip():
        return None
    return data
