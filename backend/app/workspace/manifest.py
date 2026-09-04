from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

CSV_COLUMNS = ["file_id", "filename", "source_path", "codes_branch", "specialty",
               "confidence", "method", "parser", "output_path", "code_count",
               "skip_reason"]


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


def write_output_zip(output_dir: Path, zip_path: Path) -> Path | None:
    """Pack labelled output into <upload-name>-output.zip next to the job folder."""
    if not output_dir.is_dir():
        return None
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = zip_path.with_suffix(zip_path.suffix + ".part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(output_dir.rglob("*")):
            if not path.is_file() or path.suffix == ".part":
                continue
            try:
                zf.write(path, path.relative_to(output_dir).as_posix())
            except Exception:
                continue
    tmp.replace(zip_path)
    return zip_path
