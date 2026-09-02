from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from .paths import PathEscapeError, resolve_within

ARCHIVE_SUFFIXES = {".zip"}


class ArchiveError(Exception):
    """Raised when an archive is unsafe or exceeds its resource budget."""


@dataclass(frozen=True)
class ExtractedEntry:
    path: Path
    source_path: str
    size: int


class _Budget:
    def __init__(self, max_total_bytes: int, max_entries: int) -> None:
        self.remaining_bytes = max_total_bytes
        self.remaining_entries = max_entries

    def take(self, size: int) -> None:
        self.remaining_entries -= 1
        if self.remaining_entries < 0:
            raise ArchiveError("archive exceeds the maximum number of entries")
        self.remaining_bytes -= size
        if self.remaining_bytes < 0:
            raise ArchiveError("archive exceeds the uncompressed byte budget")


def _extract(archive: Path, dest: Path, prefix: str, depth: int, max_depth: int,
             budget: _Budget, out: list[ExtractedEntry]) -> None:
    if depth > max_depth:
        raise ArchiveError(f"archive nesting exceeds max depth {max_depth}")

    dest.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        raise ArchiveError(f"corrupt archive {archive.name}") from exc

    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            try:
                target = resolve_within(dest, info.filename)
            except PathEscapeError as exc:
                raise ArchiveError(
                    f"entry {info.filename!r} resolves outside the extraction root"
                ) from exc

            budget.take(info.file_size)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                written = 0
                while chunk := src.read(1 << 20):
                    written += len(chunk)
                    if written > info.file_size + (1 << 20):
                        raise ArchiveError("declared size mismatch; refusing to continue")
                    dst.write(chunk)

            source_path = f"{prefix}{info.filename}"
            if target.suffix.lower() in ARCHIVE_SUFFIXES:
                nested_dest = target.parent / f"{target.stem}__unpacked"
                _extract(target, nested_dest, f"{source_path}!/", depth + 1, max_depth, budget, out)
                target.unlink(missing_ok=True)
            else:
                out.append(ExtractedEntry(target, source_path, info.file_size))


def extract_archive(archive: Path, dest: Path, *, max_total_bytes: int = 5 * 1024**3,
                    max_entries: int = 50_000, max_depth: int = 5) -> list[ExtractedEntry]:
    out: list[ExtractedEntry] = []
    _extract(Path(archive), Path(dest), "", 1, max_depth, _Budget(max_total_bytes, max_entries), out)
    return out
