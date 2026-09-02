from __future__ import annotations

from pathlib import Path


class PathEscapeError(Exception):
    """Raised when a path would resolve outside the trusted root."""


def _resolved_root(root: Path) -> Path:
    return Path(root).resolve()


def is_within(root: Path, candidate: Path) -> bool:
    try:
        Path(candidate).resolve().relative_to(_resolved_root(root))
    except (ValueError, OSError):
        return False
    return True


def resolve_within(root: Path, candidate: str | Path) -> Path:
    root_resolved = _resolved_root(root)
    raw = Path(candidate)
    target = raw if raw.is_absolute() else root_resolved / raw

    resolved = target.resolve()
    if not is_within(root_resolved, resolved):
        raise PathEscapeError(f"{candidate!r} resolves outside the trusted root {root_resolved}")

    probe = target
    while probe != root_resolved and probe.parent != probe:
        if probe.is_symlink() and not is_within(root_resolved, probe.resolve()):
            raise PathEscapeError(f"{candidate!r} traverses a symlink leaving {root_resolved}")
        probe = probe.parent
    return resolved
