from __future__ import annotations

from fastapi import APIRouter, Depends

from ...db.models import ApprovalStatus, JobStatus
from ...db.repository import get_repository
from ...errors import ProblemException
from ...security import require_api_key
from ...tasks import resume_job_task
from .schemas import ApprovalDecisionIn, ApprovalOut, Page

router = APIRouter(tags=["approvals"], dependencies=[Depends(require_api_key)])


def resume_job(job_id: str, resume_value: dict) -> None:
    """Indirection so tests can substitute the Celery dispatch."""
    resume_job_task.delay(job_id, resume_value)


@router.get("/jobs/{job_id}/approvals", response_model=Page)
def list_approvals(job_id: str, status: str | None = None) -> Page:
    repo = get_repository()
    if repo.get_job(job_id) is None:
        raise ProblemException(404, "Not Found", f"Job {job_id!r} does not exist.")
    rows = repo.list_approvals(job_id, status=status)
    items = [ApprovalOut(id=r.id, kind=r.kind, status=r.status, payload=r.payload,
                         created_at=r.created_at).model_dump() for r in rows]
    return Page(items=items, next_cursor=None)


def _resume_value(kind: str, payload: dict, body: ApprovalDecisionIn) -> dict:
    if kind == "low_confidence":
        file_ids = [f["file_id"] for f in payload.get("files", [])]
        if body.decision != "approve":
            return {"specialties": {fid: "Unclassified" for fid in file_ids}}
        per_file = body.specialties or {}
        fallback = body.specialty or "Unclassified"
        return {"specialties": {fid: per_file.get(fid, fallback) for fid in file_ids}}
    return {"decisions": {op["target"]: body.decision for op in payload.get("ops", [])}}


@router.post("/jobs/{job_id}/approvals/{approval_id}", response_model=ApprovalOut)
def decide_approval(job_id: str, approval_id: str, body: ApprovalDecisionIn) -> ApprovalOut:
    repo = get_repository()
    if repo.get_job(job_id) is None:
        raise ProblemException(404, "Not Found", f"Job {job_id!r} does not exist.")

    approval = next((a for a in repo.list_approvals(job_id) if a.id == approval_id), None)
    if approval is None:
        raise ProblemException(404, "Not Found", f"Approval {approval_id!r} does not exist.")
    if approval.status != ApprovalStatus.PENDING:
        raise ProblemException(409, "Conflict",
                               f"Approval {approval_id!r} was already {approval.status}.")

    decided = repo.decide_approval(approval_id, body.decision, body.note)
    repo.audit(job_id, f"approval_{body.decision}d",
               {"approval_id": approval_id, "kind": approval.kind, "note": body.note})
    repo.update_job(job_id, status=JobStatus.RUNNING)
    resume_job(job_id, _resume_value(approval.kind, approval.payload, body))

    return ApprovalOut(id=decided.id, kind=decided.kind, status=decided.status,
                       payload=decided.payload, created_at=decided.created_at)
