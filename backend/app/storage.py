from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from pathlib import Path

from fastapi import UploadFile

from .config import get_settings
from .workspace.paths import resolve_within

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def job_root(job_id: str) -> Path:
    return get_settings().workspace_root / job_id


def folder_slug(filename: str) -> str:
    """Turn 'ECW_zip.zip' into a Finder-safe folder name."""
    stem = Path(filename or "upload").name
    stem = Path(stem).stem or "upload"
    slug = _UNSAFE.sub("-", stem.replace(" ", "-")).strip(".-") or "upload"
    return slug[:60]


def allocate_job_id(filenames: list[str], is_taken: Callable[[str], bool] | None = None) -> str:
    """Use the upload name as the job folder; suffix __2, __3, … on collision."""
    base = folder_slug(filenames[0] if filenames else "upload")
    taken = is_taken or (lambda name: job_root(name).exists())
    candidate = base
    n = 2
    while taken(candidate):
        candidate = f"{base}__{n}"
        n += 1
    return candidate


def save_uploads(job_id: str, uploads: list[UploadFile]) -> list[str]:
    """Stream uploads into <workspace>/<job_id>/input/, rejecting unsafe names."""
    input_dir = job_root(job_id) / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    names: list[str] = []
    for upload in uploads:
        safe_name = Path(upload.filename or "unnamed").name
        target = resolve_within(input_dir, safe_name)
        with target.open("wb") as fh:
            shutil.copyfileobj(upload.file, fh, length=1 << 20)
        names.append(safe_name)
    return names
