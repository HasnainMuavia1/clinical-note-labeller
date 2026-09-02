from __future__ import annotations

import asyncio
import io
import json
import uuid
import zipfile

from fastapi import APIRouter, Depends, Header, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse

from ...db.models import ApprovalStatus, JobStatus
from ...db.repository import get_repository
from ...errors import ProblemException
from ...security import require_api_key
from ...storage import job_root, save_uploads
from ...tasks import dispatch_job
from .schemas import AuditEntryOut, FileDetail, JobDetail, JobSummary, Page

router = APIRouter(tags=["jobs"], dependencies=[Depends(require_api_key)])


def _job_or_404(job_id: str):
    job = get_repository().get_job(job_id)
    if job is None:
        raise ProblemException(404, "Not Found", f"Job {job_id!r} does not exist.")
    return job


def _summary(job) -> JobSummary:
    return JobSummary(id=job.id, status=job.status, stage=job.stage, progress=job.progress,
                      created_at=job.created_at, file_count=len(job.files))


def _file_detail(row) -> FileDetail:
    return FileDetail(
        file_id=row.file_id, filename=row.filename, source_path=row.source_path,
        status=row.status, parser=row.parser, parse_trail=row.parse_trail or [],
        has_codes=row.has_codes, code_hits=row.code_hits or [],
        code_rejected=row.code_rejected or [], npis=row.npis or [], specialty=row.specialty,
        confidence=row.confidence, method=row.method, output_path=row.output_path)


@router.post("/jobs", status_code=202, response_model=JobDetail)
def create_job(files: list[UploadFile], api_key: str = Depends(require_api_key),
               idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")
               ) -> JobDetail:
    repo = get_repository()
    if idempotency_key:
        existing = repo.find_by_idempotency_key(idempotency_key)
        if existing is not None:
            return JobDetail(**_summary(existing).model_dump(),
                             original_filenames=existing.original_filenames)

    if not files:
        raise ProblemException(422, "Unprocessable Entity", "At least one file is required.")

    job_id = str(uuid.uuid4())
    names = save_uploads(job_id, files)
    job = repo.create_job(job_id, api_key, names, idempotency_key)
    repo.audit(job_id, "job_created", {"files": names})
    dispatch_job(job_id)
    return JobDetail(**_summary(job).model_dump(), original_filenames=names)


@router.get("/jobs", response_model=Page)
def list_jobs(status: str | None = None, limit: int = Query(default=25, le=100),
              cursor: str | None = None) -> Page:
    jobs, next_cursor = get_repository().list_jobs(status=status, limit=limit, cursor=cursor)
    return Page(items=[_summary(j).model_dump() for j in jobs], next_cursor=next_cursor)


@router.get("/jobs/{job_id}", response_model=JobDetail)
def get_job(job_id: str) -> JobDetail:
    job = _job_or_404(job_id)
    pending = len([a for a in job.approvals if a.status == ApprovalStatus.PENDING])
    return JobDetail(**_summary(job).model_dump(),
                     original_filenames=job.original_filenames, batch_id=job.batch_id,
                     error=job.error, pending_approvals=pending)


@router.get("/jobs/{job_id}/files", response_model=Page)
def list_job_files(job_id: str) -> Page:
    _job_or_404(job_id)
    rows = get_repository().list_files(job_id)
    return Page(items=[_file_detail(r).model_dump() for r in rows], next_cursor=None)


@router.get("/jobs/{job_id}/files/{file_id}", response_model=FileDetail)
def get_job_file(job_id: str, file_id: str) -> FileDetail:
    _job_or_404(job_id)
    row = next((r for r in get_repository().list_files(job_id) if r.file_id == file_id), None)
    if row is None:
        raise ProblemException(404, "Not Found",
                               f"File {file_id!r} is not part of job {job_id!r}.")
    return _file_detail(row)


@router.post("/jobs/{job_id}/cancel", response_model=JobDetail)
def cancel_job(job_id: str) -> JobDetail:
    _job_or_404(job_id)
    repo = get_repository()
    repo.update_job(job_id, status=JobStatus.CANCELLED)
    repo.audit(job_id, "job_cancelled", {})
    return get_job(job_id)


@router.get("/jobs/{job_id}/audit", response_model=Page)
def job_audit(job_id: str) -> Page:
    job = _job_or_404(job_id)
    items = [
        AuditEntryOut(id=row.id, action=row.action, detail=row.detail or {},
                      created_at=row.created_at).model_dump()
        for row in get_repository().list_audit(job.id)
    ]
    return Page(items=items, next_cursor=None)


@router.get("/jobs/{job_id}/tree")
def job_tree(job_id: str) -> dict:
    _job_or_404(job_id)
    output = job_root(job_id) / "output"
    paths = (sorted(str(p.relative_to(output)) for p in output.rglob("*") if p.is_file())
             if output.exists() else [])
    return {"root": "output", "paths": paths}


@router.get("/jobs/{job_id}/download")
def download_results(job_id: str) -> StreamingResponse:
    _job_or_404(job_id)
    output = job_root(job_id) / "output"
    if not output.exists():
        raise ProblemException(409, "Conflict", "This job has no output yet.")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in output.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(output))
    buffer.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="{job_id}-output.zip"'}
    return StreamingResponse(buffer, media_type="application/zip", headers=headers)


@router.get("/jobs/{job_id}/manifest.csv")
def download_manifest(job_id: str) -> FileResponse:
    _job_or_404(job_id)
    path = job_root(job_id) / "output" / "labels.csv"
    if not path.exists():
        raise ProblemException(409, "Conflict", "This job has no manifest yet.")
    return FileResponse(path, media_type="text/csv", filename=f"{job_id}-labels.csv")


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> EventSourceResponse:
    _job_or_404(job_id)

    async def stream():
        last = None
        terminal = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
        while True:
            job = get_repository().get_job(job_id)
            if job is None:
                break
            snapshot = {
                "status": job.status, "stage": job.stage, "progress": job.progress,
                "pending_approvals": len(
                    [a for a in job.approvals if a.status == ApprovalStatus.PENDING]),
            }
            if snapshot != last:
                last = snapshot
                yield {"event": "progress", "data": json.dumps(snapshot)}
            if job.status in terminal:
                break
            await asyncio.sleep(2)

    return EventSourceResponse(stream())
