from __future__ import annotations

import asyncio
import logging

from celery import Celery

from .config import get_settings
from .db.models import JobStatus
from .db.repository import get_repository
from .storage import job_root

log = logging.getLogger(__name__)
_settings = get_settings()

STAGE_PROGRESS = {
    "intake": 0.08,
    "unpack": 0.16,
    "parse": 0.32,
    "detect_codes": 0.48,
    "resolve_npi": 0.58,
    "classify": 0.72,
    "plan_placement": 0.82,
    "approval_gate": 0.88,
    "execute_ops": 0.94,
    "manifest": 1.0,
}

celery_app = Celery("labeller", broker=_settings.redis_url, backend=_settings.redis_url)
celery_app.conf.update(task_acks_late=True, worker_prefetch_multiplier=1, task_track_started=True)


async def _with_checkpointer(coro_factory):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    dsn = get_settings().database_url.replace("postgresql+psycopg", "postgresql")
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        await saver.setup()
        return await coro_factory(saver)


def _file_status(record: dict, stage: str) -> str:
    if record.get("output_path"):
        return "filed"
    if record.get("ok"):
        return "parsed"
    if stage in {"intake", "unpack"}:
        return "pending"
    return "unparsed"


def _persist(job_id: str, state: dict) -> None:
    repo = get_repository()
    stage = state.get("stage") or ""
    for record in state.get("files", []):
        repo.upsert_file(
            job_id, record["file_id"],
            filename=record.get("filename", ""),
            source_path=record.get("source_path", ""),
            sha256=record.get("sha256"),
            size_bytes=record.get("size_bytes", 0),
            status=_file_status(record, stage),
            parser=record.get("parser"),
            parse_trail=record.get("parse_trail", []),
            has_codes=bool(record.get("has_codes")),
            code_hits=record.get("code_hits", []),
            code_rejected=record.get("code_rejected", []),
            npis=record.get("npis", []),
            specialty=record.get("specialty"),
            confidence=record.get("confidence", 0.0),
            method=record.get("method"),
            output_path=record.get("output_path"),
        )


def _checkpoint(job_id: str, node: str, result: dict, prior: dict) -> None:
    """Write the node's stage, files, and an audit line so the UI can reason live."""
    merged = {**prior, **result, "job_id": job_id}
    stage = result.get("stage") or merged.get("stage") or "intake"
    repo = get_repository()
    repo.update_job(job_id, stage=stage, progress=STAGE_PROGRESS.get(stage, 0.0))
    if merged.get("files"):
        _persist(job_id, merged)
    repo.audit(job_id, "agent_step", {
        "node": node,
        "stage": stage,
        "file_count": len(merged.get("files") or []),
        "ops": len(merged.get("pending_ops") or []),
        "filenames": [f.get("filename") for f in (merged.get("files") or [])][:12],
    })


def _handle_interrupt(job_id: str, state: dict) -> bool:
    """Persist an interrupt as an Approval row. Returns True if the job is now parked."""
    interrupts = state.get("__interrupt__") or []
    if not interrupts:
        return False

    repo = get_repository()
    first = interrupts[0]
    payload = first.value if hasattr(first, "value") else first

    if payload.get("kind") == "batch_pending":
        repo.update_job(job_id, status=JobStatus.AWAITING_BATCH, batch_id=payload.get("batch_id"))
        poll_batch_task.apply_async((job_id, payload["batch_id"]), countdown=60)
        return True

    repo.create_approval(job_id, payload["kind"], payload)
    repo.update_job(job_id, status=JobStatus.AWAITING_APPROVAL)
    repo.audit(job_id, "approval_requested", payload)
    return True


@celery_app.task(name="jobs.run")
def run_job_task(job_id: str) -> None:
    from .agent.graph import run_job

    repo = get_repository()
    repo.update_job(job_id, status=JobStatus.RUNNING, stage="intake")
    try:
        from .agent.graph import step_listener

        token = step_listener.set(
            lambda node, result, prior: _checkpoint(job_id, node, result, prior))
        try:
            state = asyncio.run(_with_checkpointer(
                lambda saver: run_job(job_id, job_root(job_id), saver)))
        finally:
            step_listener.reset(token)
        _persist(job_id, state)
        if not _handle_interrupt(job_id, state):
            repo.update_job(job_id, status=JobStatus.COMPLETED, stage="manifest", progress=1.0)
    except Exception as exc:
        log.exception("job %s failed", job_id)
        repo.update_job(job_id, status=JobStatus.FAILED, error=str(exc))


@celery_app.task(name="jobs.resume")
def resume_job_task(job_id: str, resume_value) -> None:
    from .agent.graph import resume_job

    repo = get_repository()
    repo.update_job(job_id, status=JobStatus.RUNNING)
    try:
        from .agent.graph import step_listener

        token = step_listener.set(
            lambda node, result, prior: _checkpoint(job_id, node, result, prior))
        try:
            state = asyncio.run(_with_checkpointer(
                lambda saver: resume_job(job_id, resume_value, saver)))
        finally:
            step_listener.reset(token)
        _persist(job_id, state)
        if not _handle_interrupt(job_id, state):
            repo.update_job(job_id, status=JobStatus.COMPLETED, stage="manifest", progress=1.0)
    except Exception as exc:
        log.exception("resuming job %s failed", job_id)
        repo.update_job(job_id, status=JobStatus.FAILED, error=str(exc))


@celery_app.task(name="jobs.poll_batch")
def poll_batch_task(job_id: str, batch_id: str) -> None:
    from .specialty.classifier import fetch_batch_results, poll_batch

    status = poll_batch(batch_id)
    if status in {"validating", "in_progress", "finalizing"}:
        poll_batch_task.apply_async((job_id, batch_id), countdown=60)
        return
    if status != "completed":
        get_repository().update_job(job_id, status=JobStatus.FAILED,
                                    error=f"OpenAI batch {batch_id} {status}")
        return
    results = [r.__dict__ for r in fetch_batch_results(batch_id)]
    resume_job_task.delay(job_id, results)


def dispatch_job(job_id: str) -> None:
    run_job_task.delay(job_id)
