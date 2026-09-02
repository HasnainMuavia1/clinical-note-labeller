import csv
import json

from app.workspace.manifest import write_labels_csv, write_manifest

RECORDS = [{
    "file_id": "f1", "filename": "note.pdf", "source_path": "bundle.zip!/cardio/note.pdf",
    "codes_branch": "with-codes", "specialty": "Cardiology", "confidence": 0.91,
    "method": "npi", "parser": "pypdf", "output_path": "output/with-codes/Cardiology/note.pdf",
    "code_hits": [{"code": "99213", "rule": "dictionary+cue"}],
}]


def test_manifest_is_jsonl(tmp_path):
    path = tmp_path / "manifest.jsonl"
    write_manifest(path, RECORDS)
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["file_id"] == "f1"


def test_labels_csv_has_the_expected_header_and_row(tmp_path):
    path = tmp_path / "labels.csv"
    write_labels_csv(path, RECORDS)
    rows = list(csv.DictReader(path.open()))
    assert rows[0]["specialty"] == "Cardiology"
    assert rows[0]["codes_branch"] == "with-codes"
    assert rows[0]["code_count"] == "1"
