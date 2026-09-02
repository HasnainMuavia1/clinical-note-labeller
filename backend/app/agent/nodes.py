from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from pathlib import Path

from langgraph.types import interrupt

from ..codes.detector import detect_codes
from ..codes.dictionaries import get_dictionaries
from ..config import get_settings
from ..parsing.chain import parse_document
from ..specialty.classifier import ClassificationRequest, classify
from ..specialty.npi import resolve_specialty_from_npis
from ..specialty.taxonomy import UNCLASSIFIED, folder_name, normalize_specialty
from ..workspace.archive import ArchiveError, extract_archive
from ..workspace.filetools import FileOp, GuardedFileTools
from ..workspace.manifest import write_labels_csv, write_manifest, write_output_zip
from .approvals import approval_payload
from .pool import map_files
from .state import JobState

log = logging.getLogger(__name__)


class ClassificationLike:
    """Rehydrates a Classification that crossed the Celery/interrupt boundary as a dict."""

    def __init__(self, file_id: str, specialty: str, confidence: float,
                 rationale: str = "", method: str = "llm_batch") -> None:
        self.file_id = file_id
        self.specialty = specialty
        self.confidence = confidence
        self.rationale = rationale
        self.method = method


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _tools(state: JobState) -> GuardedFileTools:
    audit_log = Path(state["root"]) / "logs" / "audit.jsonl"

    def audit(action: str, detail: dict) -> None:
        audit_log.parent.mkdir(parents=True, exist_ok=True)
        with audit_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"job_id": state.get("job_id"), "action": action,
                                 "detail": detail}) + "\n")

    return GuardedFileTools(Path(state["root"]), audit)


def _file_record(path: Path, source_path: str) -> dict:
    return {
        "file_id": str(uuid.uuid4()),
        "path": str(path),
        "filename": path.name,
        "source_path": source_path,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "text": "",
        "parser": None,
        "parse_trail": [],
        "ok": False,
        "has_codes": False,
        "code_hits": [],
        "code_rejected": [],
        "npis": [],
        "specialty": None,
        "confidence": 0.0,
        "method": None,
        "output_path": None,
    }


async def intake_node(state: JobState) -> dict:
    input_dir = Path(state["root"]) / "input"
    files = [_file_record(p, p.name) for p in sorted(input_dir.rglob("*")) if p.is_file()]
    log.info("intake: %d files for job %s", len(files), state.get("job_id"))
    return {"files": files, "stage": "intake"}


async def unpack_node(state: JobState) -> dict:
    root = Path(state["root"])
    extracted_root = root / "extracted"
    result: list[dict] = []
    for record in state.get("files", []):
        path = Path(record["path"])
        if path.suffix.lower() != ".zip":
            result.append(record)
            continue
        dest = extracted_root / path.stem
        try:
            entries = extract_archive(path, dest)
        except ArchiveError as exc:
            log.warning("archive rejected: %s (%s)", path.name, exc)
            result.append({**record, "ok": False,
                           "parse_trail": [{"parser": "zip", "ok": False, "reason": str(exc)}]})
            continue
        for entry in entries:
            result.append(_file_record(entry.path, f"{path.name}!/{entry.source_path}"))
    return {"files": result, "stage": "unpack"}


async def parse_node(state: JobState) -> dict:
    async def work(record: dict) -> dict:
        if record.get("ok"):
            return record
        parsed = await parse_document(Path(record["path"]))
        return {**record, "text": parsed.text, "parser": parsed.parser, "ok": parsed.ok,
                "parse_trail": [{"parser": a.parser, "ok": a.ok, "reason": a.reason}
                                for a in parsed.trail]}

    files = await map_files(
        state.get("files", []), work, stage="parse",
        concurrency=get_settings().file_concurrency,
    )
    return {"files": files, "stage": "parse"}


async def detect_codes_node(state: JobState) -> dict:
    dicts = get_dictionaries()
    threshold = get_settings().code_evidence_threshold

    async def work(record: dict) -> dict:
        if not record.get("ok"):
            return record
        result = await asyncio.to_thread(detect_codes, record.get("text", ""), dicts, threshold)
        return {**record,
                "has_codes": result.has_codes,
                "code_hits": [h.__dict__ for h in result.hits],
                "code_rejected": [h.__dict__ for h in result.rejected],
                "npis": result.npis}

    files = await map_files(
        state.get("files", []), work, stage="detect_codes",
        concurrency=get_settings().file_concurrency,
    )
    return {"files": files, "stage": "detect_codes"}


async def resolve_npi_node(state: JobState) -> dict:
    async def work(record: dict) -> dict:
        if not record.get("ok") or not record.get("npis"):
            return record
        resolved = await resolve_specialty_from_npis(record["npis"])
        if resolved and resolved.specialty:
            return {**record, "specialty": resolved.specialty,
                    "confidence": 1.0, "method": "npi"}
        return record

    files = await map_files(
        state.get("files", []), work, stage="resolve_npi",
        concurrency=get_settings().file_concurrency,
    )
    return {"files": files, "stage": "resolve_npi"}


async def classify_node(state: JobState) -> dict:
    pending = [f for f in state.get("files", []) if f.get("ok") and not f.get("specialty")]
    if not pending:
        return {"files": state.get("files", []), "stage": "classify"}

    requests = [ClassificationRequest(f["file_id"], f.get("text", "")) for f in pending]
    results, batch_id = await classify(requests, Path(state["root"]) / "batch")
    if results is None:
        # The Celery task polls the OpenAI batch and resumes the graph with the labels.
        raw = interrupt(approval_payload("batch_pending", {"batch_id": batch_id})) or []
        results = [ClassificationLike(**r) if isinstance(r, dict) else r for r in raw]

    by_id = {r.file_id: r for r in results}
    files = []
    for record in state.get("files", []):
        label = by_id.get(record["file_id"])
        if label is None:
            files.append(record)
            continue
        files.append({**record, "specialty": normalize_specialty(label.specialty),
                      "confidence": label.confidence, "method": label.method})
    return {"files": files, "stage": "classify", "batch_id": batch_id}


async def plan_placement_node(state: JobState) -> dict:
    """Agent node: turn labelled files into a proposed operation list.

    Placement follows from the label; the judgement calls are collision handling
    and low-confidence escalation, both of which raise approvals.
    """
    threshold = get_settings().specialty_confidence_threshold
    tools = _tools(state)
    files = state.get("files", [])

    def needs_review(record: dict) -> bool:
        return (record.get("ok")
                and record.get("method") not in {"npi", "human"}
                and record.get("confidence", 0.0) < threshold)

    low_confidence = [{"file_id": f["file_id"], "filename": f["filename"],
                       "proposed_specialty": f.get("specialty") or UNCLASSIFIED,
                       "confidence": f.get("confidence", 0.0)}
                      for f in files if needs_review(f)]

    overrides: dict[str, str] = {}
    if low_confidence:
        answer = interrupt(approval_payload("low_confidence", {"files": low_confidence}))
        overrides = (answer or {}).get("specialties") or {}

    resolved: list[dict] = []
    for record in files:
        if record["file_id"] in overrides:
            record = {**record, "specialty": normalize_specialty(overrides[record["file_id"]]),
                      "confidence": 1.0, "method": "human"}
        elif needs_review(record):
            record = {**record, "specialty": UNCLASSIFIED}
        resolved.append(record)

    ops: list[dict] = []
    for record in resolved:
        if not record.get("ok"):
            target = tools.unique_target(f"output/unparsed/{record['filename']}")
            ops.append({"op": "copy", "source": record["path"], "target": target,
                        "reason": "no parser could extract text", "file_id": record["file_id"]})
            continue

        specialty = record.get("specialty") or UNCLASSIFIED
        branch = "with-codes" if record.get("has_codes") else "without-codes"
        target = f"output/{branch}/{folder_name(specialty)}/{record['filename']}"
        planned = tools.plan_copy(Path(record["path"]), target, f"{branch} / {specialty}")
        ops.append({"op": planned.op, "source": planned.source, "target": planned.target,
                    "reason": planned.reason, "file_id": record["file_id"]})

    return {"files": resolved, "pending_ops": ops, "stage": "plan_placement"}


async def approval_gate_node(state: JobState) -> dict:
    guarded = [op for op in state.get("pending_ops", []) if op["op"] in {"delete", "overwrite"}]
    if not guarded:
        return {"stage": "approval_gate"}

    answer = interrupt(approval_payload("overwrite", {"ops": guarded}))
    decisions = (answer or {}).get("decisions") or {}
    approved_targets = {t for t, d in decisions.items() if d == "approve"}

    tools = _tools(state)
    ops = []
    for op in state.get("pending_ops", []):
        if op["op"] in {"delete", "overwrite"} and op["target"] not in approved_targets:
            ops.append({**op, "op": "copy", "target": tools.unique_target(op["target"]),
                        "reason": f"{op['reason']} (approval declined; auto-suffixed)"})
        else:
            ops.append({**op, "approved": op["target"] in approved_targets})
    return {"pending_ops": ops, "stage": "approval_gate"}


async def execute_ops_node(state: JobState) -> dict:
    tools = _tools(state)
    outputs: dict[str, str] = {}
    for op in state.get("pending_ops", []):
        file_op = FileOp(op["op"], op.get("source"), op["target"], op.get("reason", ""))
        tools.execute(file_op, approved=bool(op.get("approved")))
        outputs[op["file_id"]] = op["target"]

    files = [{**f, "output_path": outputs.get(f["file_id"], f.get("output_path"))}
             for f in state.get("files", [])]
    return {"files": files, "stage": "execute_ops"}


async def manifest_node(state: JobState) -> dict:
    root = Path(state["root"])
    records = []
    for record in state.get("files", []):
        records.append({
            "file_id": record["file_id"],
            "filename": record["filename"],
            "source_path": record.get("source_path", ""),
            "sha256": record.get("sha256"),
            "size_bytes": record.get("size_bytes", 0),
            "codes_branch": ("unparsed" if not record.get("ok")
                             else "with-codes" if record.get("has_codes") else "without-codes"),
            "specialty": record.get("specialty") or UNCLASSIFIED,
            "confidence": record.get("confidence", 0.0),
            "method": record.get("method"),
            "parser": record.get("parser"),
            "parse_trail": record.get("parse_trail", []),
            "code_hits": record.get("code_hits", []),
            "code_rejected": record.get("code_rejected", []),
            "npis": record.get("npis", []),
            "output_path": record.get("output_path") or "",
        })
    write_manifest(root / "output" / "manifest.jsonl", records)
    write_labels_csv(root / "output" / "labels.csv", records)
    write_output_zip(root / "output", root.parent / f"{root.name}-output.zip")
    return {"manifest": records, "stage": "manifest"}
