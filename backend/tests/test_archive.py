import zipfile

from app.workspace.archive import extract_archive


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


def test_skips_a_zip_slip_entry_and_keeps_the_rest(tmp_path):
    archive = tmp_path / "mixed.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("good.txt", "ok")
        zf.writestr("../escaped.txt", "bad")
    extracted = extract_archive(archive, tmp_path / "out")
    assert {e.path.name for e in extracted} == {"good.txt"}
    assert extracted.skipped
    assert any("outside" in row.reason or "escaped" in row.source_path for row in extracted.skipped)
    assert not (tmp_path / "escaped.txt").exists()


def test_skips_entries_over_the_byte_budget_and_keeps_earlier_ones(tmp_path):
    archive = make_zip(tmp_path / "bomb.zip", {"small.txt": "ok", "big.txt": "A" * 100_000})
    extracted = extract_archive(archive, tmp_path / "out", max_total_bytes=1000)
    names = {e.path.name for e in extracted}
    assert "small.txt" in names
    assert "big.txt" not in names
    assert any("budget" in row.reason for row in extracted.skipped)


def test_skips_entries_past_the_count_limit_and_keeps_earlier_ones(tmp_path):
    archive = make_zip(tmp_path / "many.zip", {f"n{i}.txt": "x" for i in range(20)})
    extracted = extract_archive(archive, tmp_path / "out", max_entries=5)
    assert len(extracted.entries) == 5
    assert len(extracted.skipped) == 15
    assert all("entries" in row.reason for row in extracted.skipped)


def test_skips_excessive_nesting_and_keeps_shallower_files(tmp_path):
    current = make_zip(tmp_path / "l0.zip", {"leaf.txt": "x"})
    for level in range(1, 4):
        nxt = tmp_path / f"l{level}.zip"
        with zipfile.ZipFile(nxt, "w") as zf:
            zf.write(current, f"l{level - 1}.zip")
        current = nxt
    extracted = extract_archive(current, tmp_path / "out", max_depth=2)
    assert extracted.skipped
    assert any("depth" in row.reason for row in extracted.skipped)


def test_corrupt_archive_is_skipped_instead_of_raising(tmp_path):
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"this is not a zip")
    extracted = extract_archive(archive, tmp_path / "out")
    assert extracted.entries == []
    assert extracted.skipped
    assert "broken.zip" in extracted.skipped[0].filename
