import zipfile

import pytest

from app.workspace.archive import ArchiveError, extract_archive


def make_zip(path, entries):
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def test_extracts_flat_entries(tmp_path):
    archive = make_zip(tmp_path / "a.zip", {"note1.txt": "hello", "note2.txt": "world"})
    dest = tmp_path / "out"
    entries = extract_archive(archive, dest)
    assert {e.path.name for e in entries} == {"note1.txt", "note2.txt"}
    assert (dest / "note1.txt").read_text() == "hello"


def test_preserves_source_path_for_nested_folders(tmp_path):
    archive = make_zip(tmp_path / "a.zip", {"cardio/note1.txt": "x", "derm/sub/note2.txt": "y"})
    entries = extract_archive(archive, tmp_path / "out")
    sources = {e.source_path for e in entries}
    assert "cardio/note1.txt" in sources
    assert "derm/sub/note2.txt" in sources


def test_extracts_nested_archives_recursively(tmp_path):
    inner = make_zip(tmp_path / "inner.zip", {"deep.txt": "deep"})
    outer = tmp_path / "outer.zip"
    with zipfile.ZipFile(outer, "w") as zf:
        zf.write(inner, "nested/inner.zip")
    entries = extract_archive(outer, tmp_path / "out")
    assert any(e.path.name == "deep.txt" for e in entries)
    assert any("inner.zip" in e.source_path for e in entries)


def test_rejects_zip_slip(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escaped.txt", "bad")
    with pytest.raises(ArchiveError, match="outside"):
        extract_archive(archive, tmp_path / "out")


def test_rejects_zip_bomb_over_byte_budget(tmp_path):
    archive = make_zip(tmp_path / "bomb.zip", {"big.txt": "A" * 100_000})
    with pytest.raises(ArchiveError, match="budget"):
        extract_archive(archive, tmp_path / "out", max_total_bytes=1000)


def test_rejects_too_many_entries(tmp_path):
    archive = make_zip(tmp_path / "many.zip", {f"n{i}.txt": "x" for i in range(20)})
    with pytest.raises(ArchiveError, match="entries"):
        extract_archive(archive, tmp_path / "out", max_entries=5)


def test_rejects_excessive_nesting_depth(tmp_path):
    current = make_zip(tmp_path / "l0.zip", {"leaf.txt": "x"})
    for level in range(1, 4):
        nxt = tmp_path / f"l{level}.zip"
        with zipfile.ZipFile(nxt, "w") as zf:
            zf.write(current, f"l{level - 1}.zip")
        current = nxt
    with pytest.raises(ArchiveError, match="depth"):
        extract_archive(current, tmp_path / "out", max_depth=2)
