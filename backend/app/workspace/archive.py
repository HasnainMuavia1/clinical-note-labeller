from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .paths import PathEscapeError, resolve_within

log = logging.getLogger(__name__)

ARCHIVE_SUFFIXES = {".zip"}


class ArchiveError(Exception):
    """Raised when an archive is unsafe or exceeds its resource budget."""


@dataclass(frozen=True)
class ExtractedEntry:
    path: Path
    source_path: str
    size: int


@dataclass(frozen=True)
class SkippedArchiveEntry:
    source_path: str
    filename: str
    reason: str


@dataclass
class ArchiveExtract:
    entries: list[ExtractedEntry] = field(default_factory=list)
    skipped: list[SkippedArchiveEntry] = field(default_factory=list)

    def __iter__(self):
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


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


def _note_skip(skipped: list[SkippedArchiveEntry], source_path: str, reason: str) -> None:
    name = Path(source_path.split("!/")[-1]).name or source_path
    log.warning("skipping archive entry %s: %s", source_path, reason)
    skipped.append(SkippedArchiveEntry(source_path=source_path, filename=name, reason=reason))


def _extract(archive: Path, dest: Path, prefix: str, depth: int, max_depth: int,
             budget: _Budget, out: list[ExtractedEntry],
             skipped: list[SkippedArchiveEntry]) -> None:
    label = prefix.rstrip("!/") or archive.name
    if depth > max_depth:
        _note_skip(skipped, label, f"archive nesting exceeds max depth {max_depth}")
        return

    dest.mkdir(parents=True, exist_ok=True)
    try:
        zf = zipfile.ZipFile(archive)
    except zipfile.BadZipFile as exc:
        _note_skip(skipped, label, f"corrupt archive {archive.name}: {exc}")
        return
    except Exception as exc:
        _note_skip(skipped, label, f"{type(exc).__name__}: {exc}")
        return

    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            source_path = f"{prefix}{info.filename}"
            try:
                target = resolve_within(dest, info.filename)
            except PathEscapeError as exc:
                _note_skip(skipped, source_path, str(exc))
                continue

            try:
                budget.take(info.file_size)
            except ArchiveError as exc:
                _note_skip(skipped, source_path, str(exc))
                continue

            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    written = 0
                    while chunk := src.read(1 << 20):
                        written += len(chunk)
                        if written > info.file_size + (1 << 20):
                            raise ArchiveError("declared size mismatch; refusing to continue")
                        dst.write(chunk)
            except Exception as exc:
                target.unlink(missing_ok=True)
                _note_skip(skipped, source_path, f"{type(exc).__name__}: {exc}")
                continue

            if target.suffix.lower() in ARCHIVE_SUFFIXES:
                nested_dest = target.parent / f"{target.stem}__unpacked"
                _extract(target, nested_dest, f"{source_path}!/", depth + 1, max_depth,
                         budget, out, skipped)
                target.unlink(missing_ok=True)
            else:
                out.append(ExtractedEntry(target, source_path, info.file_size))


def extract_archive(archive: Path, dest: Path, *, max_total_bytes: int = 5 * 1024**3,
                    max_entries: int = 50_000, max_depth: int = 5) -> ArchiveExtract:
    out: list[ExtractedEntry] = []
    skipped: list[SkippedArchiveEntry] = []
    try:
        _extract(Path(archive), Path(dest), "", 1, max_depth,
                 _Budget(max_total_bytes, max_entries), out, skipped)
    except Exception as exc:
        _note_skip(skipped, Path(archive).name, f"{type(exc).__name__}: {exc}")
    return ArchiveExtract(entries=out, skipped=skipped)
