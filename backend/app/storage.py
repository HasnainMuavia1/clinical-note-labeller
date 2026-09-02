from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import UploadFile

from .config import get_settings
from .workspace.paths import resolve_within


def job_root(job_id: str) -> Path:
    return get_settings().workspace_root / job_id


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
