from __future__ import annotations

import csv
import json
from pathlib import Path

CSV_COLUMNS = ["file_id", "filename", "source_path", "codes_branch", "specialty",
               "confidence", "method", "parser", "output_path", "code_count"]


def write_manifest(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, default=str) + "\n")


def write_labels_csv(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for record in records:
            row = {key: record.get(key, "") for key in CSV_COLUMNS}
            row["code_count"] = len(record.get("code_hits") or [])
            writer.writerow(row)
