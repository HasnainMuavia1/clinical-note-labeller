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

STAGE_ORDER = list(STAGE_PROGRESS)


def _band(stage: str) -> tuple[float, float]:
    if stage not in STAGE_PROGRESS:
        return 0.0, STAGE_PROGRESS.get(stage, 0.0)
    index = STAGE_ORDER.index(stage)
    start = STAGE_PROGRESS[STAGE_ORDER[index - 1]] if index else 0.0
    return start, STAGE_PROGRESS[stage]


def _file_tick(job_id: str, stage: str, done: int, total: int, record: dict) -> None:
    """Persist one file so the UI can show N of M while a long node is still running."""
    try:
        repo = get_repository()
        job = repo.get_job(job_id)
        if job and job.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}:
            return
        start, end = _band(stage)
        frac = done / total if total else 1.0
        repo.update_job(job_id, stage=stage, progress=start + (end - start) * frac)
        _persist(job_id, {"stage": stage, "files": [record]})
        if done == 1 or done == total or done % 10 == 0:
            repo.audit(job_id, "file_progress", {
                "stage": stage, "done": done, "total": total,
                "filename": record.get("filename"),
            })
    except Exception:
        log.exception("file tick failed for %s; continuing", record.get("filename"))


celery_app = Celery("labeller", broker=_settings.redis_url, backend=_settings.redis_url)
celery_app.conf.update(task_acks_late=True, worker_prefetch_multiplier=1, task_track_started=True)
try:
    from .runtime.capacity import apply_celery_concurrency
    apply_celery_concurrency(celery_app)
except Exception:
    log.exception("capacity planner failed; using Celery default concurrency")


async def _with_checkpointer(coro_factory):
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    dsn = get_settings().database_url.replace("postgresql+psycopg", "postgresql")
    async with AsyncPostgresSaver.from_conn_string(dsn) as saver:
        await saver.setup()
        return await coro_factory(saver)


def _file_status(record: dict, stage: str) -> str:
    if record.get("skipped"):
        return "skipped"
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
        try:
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
        except Exception as exc:
            filename = record.get("filename") or record.get("file_id")
            log.warning("skipping persist for %s: %s", filename, exc)
            try:
                repo.audit(job_id, "file_skipped", {
                    "filename": filename,
                    "file_id": record.get("file_id"),
                    "reason": f"{filename}: {type(exc).__name__}: {exc}",
                    "stage": stage,
                })
            except Exception:
                log.exception("could not audit skipped file %s", filename)


def _checkpoint(job_id: str, node: str, result: dict, prior: dict) -> None:
    """Write the node's stage, files, and an audit line so the UI can reason live."""
    merged = {**prior, **result, "job_id": job_id}
    stage = result.get("stage") or merged.get("stage") or "intake"
    repo = get_repository()
    try:
        repo.update_job(job_id, stage=stage, progress=STAGE_PROGRESS.get(stage, 0.0))
    except Exception:
        log.exception("checkpoint status update failed for %s; continuing", job_id)
    if merged.get("files"):
        try:
            _persist(job_id, merged)
        except Exception:
            log.exception("checkpoint persist failed for %s; continuing", job_id)
    try:
        repo.audit(job_id, "agent_step", {
            "node": node,
            "stage": stage,
            "file_count": len(merged.get("files") or []),
            "ops": len(merged.get("pending_ops") or []),
            "filenames": [f.get("filename") for f in (merged.get("files") or [])][:12],
        })
    except Exception:
        log.exception("checkpoint audit failed for %s; continuing", job_id)


def _handle_interrupt(job_id: str, state: dict) -> bool:
    """Persist an interrupt as an Approval row. Returns True if the job is now parked."""
    interrupts = state.get("__interrupt__") or []
    if not interrupts:
        return False

    try:
        first = interrupts[0]
        payload = first.value if hasattr(first, "value") else first
        if not isinstance(payload, dict):
            payload = {}
        repo = get_repository()

        if payload.get("kind") == "batch_pending":
            batch_id = payload.get("batch_id")
            repo.update_job(job_id, status=JobStatus.AWAITING_BATCH, batch_id=batch_id)
            if batch_id:
                poll_batch_task.apply_async((job_id, batch_id), countdown=60)
            else:
                resume_job_task.delay(job_id, [])
            return True

        kind = payload.get("kind")
        if not kind:
            log.warning("interrupt for %s had no kind; continuing the job", job_id)
            return False

        repo.create_approval(job_id, kind, payload)
        repo.update_job(job_id, status=JobStatus.AWAITING_APPROVAL)
        repo.audit(job_id, "approval_requested", payload)
        return True
    except Exception:
        log.exception("interrupt handling failed for %s; continuing", job_id)
        return False


def _listen(job_id: str):
    from .agent.graph import step_listener
    from .agent.progress import file_progress_listener

    step_token = step_listener.set(
        lambda node, result, prior: _checkpoint(job_id, node, result, prior))
    file_token = file_progress_listener.set(
        lambda stage, done, total, record: _file_tick(job_id, stage, done, total, record))
    return step_token, file_token


def _unlisten(tokens) -> None:
    from .agent.graph import step_listener
    from .agent.progress import file_progress_listener

    step_token, file_token = tokens
    file_progress_listener.reset(file_token)
    step_listener.reset(step_token)


@celery_app.task(name="jobs.run")
def run_job_task(job_id: str) -> None:
    from .agent.graph import run_job

    repo = get_repository()
    repo.update_job(job_id, status=JobStatus.RUNNING, stage="intake")
    try:
        tokens = _listen(job_id)
        try:
            state = asyncio.run(_with_checkpointer(
                lambda saver: run_job(job_id, job_root(job_id), saver)))
        finally:
            _unlisten(tokens)
        try:
            _persist(job_id, state)
        except Exception:
            log.exception("final persist failed for %s; marking complete anyway", job_id)
        if not _handle_interrupt(job_id, state):
            repo.update_job(job_id, status=JobStatus.COMPLETED, stage="manifest", progress=1.0)
    except Exception as exc:
        log.exception("job %s hit a fatal error; completing remaining work", job_id)
        try:
            repo.update_job(job_id, status=JobStatus.COMPLETED, stage="manifest", progress=1.0,
                            error=str(exc))
        except Exception:
            repo.update_job(job_id, status=JobStatus.FAILED, error=str(exc))


@celery_app.task(name="jobs.resume")
def resume_job_task(job_id: str, resume_value) -> None:
    from .agent.graph import resume_job

    repo = get_repository()
    repo.update_job(job_id, status=JobStatus.RUNNING)
    try:
        tokens = _listen(job_id)
        try:
            state = asyncio.run(_with_checkpointer(
                lambda saver: resume_job(job_id, resume_value, saver)))
        finally:
            _unlisten(tokens)
        try:
            _persist(job_id, state)
        except Exception:
            log.exception("final persist failed for %s; marking complete anyway", job_id)
        if not _handle_interrupt(job_id, state):
            repo.update_job(job_id, status=JobStatus.COMPLETED, stage="manifest", progress=1.0)
    except Exception as exc:
        log.exception("resuming job %s hit a fatal error; completing remaining work", job_id)
        try:
            repo.update_job(job_id, status=JobStatus.COMPLETED, stage="manifest", progress=1.0,
                            error=str(exc))
        except Exception:
            repo.update_job(job_id, status=JobStatus.FAILED, error=str(exc))


@celery_app.task(name="jobs.poll_batch")
def poll_batch_task(job_id: str, batch_id: str, failures: int = 0) -> None:
    from .specialty.classifier import fetch_batch_results, poll_batch

    try:
        status = poll_batch(batch_id)
    except Exception as exc:
        log.warning("poll_batch %s failed (%s): %s", batch_id, failures, exc)
        if failures >= 5:
            resume_job_task.delay(job_id, [])
            return
        poll_batch_task.apply_async((job_id, batch_id, failures + 1), countdown=60)
        return
    if status in {"validating", "in_progress", "finalizing"}:
        poll_batch_task.apply_async((job_id, batch_id), countdown=60)
        return
    if status != "completed":
        log.warning("OpenAI batch %s ended as %s; resuming without labels", batch_id, status)
        resume_job_task.delay(job_id, [])
        return
    try:
        results = [r.__dict__ for r in fetch_batch_results(batch_id)]
    except Exception as exc:
        log.warning("fetch_batch_results %s failed: %s; resuming without labels", batch_id, exc)
        results = []
    resume_job_task.delay(job_id, results)


def dispatch_job(job_id: str) -> None:
    run_job_task.delay(job_id)
