from pathlib import Path

import pytest

from app.workspace.paths import PathEscapeError, is_within, resolve_within


def test_resolves_a_child_path(tmp_path):
    assert resolve_within(tmp_path, "a/b.txt") == (tmp_path / "a" / "b.txt").resolve()


def test_rejects_parent_traversal(tmp_path):
    with pytest.raises(PathEscapeError):
        resolve_within(tmp_path, "../outside.txt")


def test_rejects_absolute_path_outside_root(tmp_path):
    with pytest.raises(PathEscapeError):
        resolve_within(tmp_path, "/etc/passwd")


def test_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside_dir"
    outside.mkdir(exist_ok=True)
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathEscapeError):
        resolve_within(root, "link/secret.txt")


def test_is_within_reports_boolean(tmp_path):
    assert is_within(tmp_path, tmp_path / "x")
    assert not is_within(tmp_path, Path("/etc"))
