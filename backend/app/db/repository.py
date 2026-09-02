from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

from sqlalchemy import select, tuple_
from sqlalchemy.orm import selectinload, sessionmaker

from .models import Approval, ApprovalStatus, AuditEntry, Job, JobFile, JobStatus, NpiCache


def _encode_cursor(created_at: datetime, job_id: str) -> str:
    payload = json.dumps([created_at.isoformat(), job_id])
    return base64.urlsafe_b64encode(payload.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    created_at, job_id = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    return datetime.fromisoformat(created_at), job_id


class Repository:
    def __init__(self, session_factory: sessionmaker) -> None:
        self._sf = session_factory

    # jobs -----------------------------------------------------------------
    def create_job(self, job_id: str, api_key_id: str, original_filenames: list[str],
                   idempotency_key: str | None) -> Job:
        with self._sf() as s:
            job = Job(id=job_id, api_key_id=api_key_id, original_filenames=original_filenames,
                      idempotency_key=idempotency_key, status=JobStatus.PENDING)
            s.add(job)
            s.commit()
        # Re-read eagerly: callers touch job.files on a detached instance.
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> Job | None:
        with self._sf() as s:
            return s.execute(
                select(Job).where(Job.id == job_id).options(
                    selectinload(Job.files),
                    selectinload(Job.approvals),
                    selectinload(Job.audit_entries),
                )
            ).scalar_one_or_none()

    def find_by_idempotency_key(self, key: str) -> Job | None:
        with self._sf() as s:
            return s.execute(
                select(Job).where(Job.idempotency_key == key).options(
                    selectinload(Job.files),
                    selectinload(Job.approvals),
                    selectinload(Job.audit_entries),
                )
            ).scalar_one_or_none()

    def list_jobs(self, status: str | None = None, limit: int = 50, cursor: str | None = None
                  ) -> tuple[list[Job], str | None]:
        with self._sf() as s:
            stmt = (select(Job)
                    .options(selectinload(Job.files))
                    .order_by(Job.created_at.desc(), Job.id.desc()))
            if status:
                stmt = stmt.where(Job.status == status)
            if cursor:
                created_at, job_id = _decode_cursor(cursor)
                stmt = stmt.where(tuple_(Job.created_at, Job.id) < tuple_(created_at, job_id))
            rows = list(s.execute(stmt.limit(limit + 1)).scalars())
            has_more = len(rows) > limit
            page = rows[:limit]
            next_cursor = _encode_cursor(page[-1].created_at, page[-1].id) if has_more and page else None
            return page, next_cursor

    def update_job(self, job_id: str, **fields) -> Job:
        with self._sf() as s:
            job = s.get(Job, job_id)
            for key, value in fields.items():
                setattr(job, key, value)
            s.commit()
        return self.get_job(job_id)

    # files ----------------------------------------------------------------
    def upsert_file(self, job_id: str, file_id: str, **fields) -> JobFile:
        with self._sf() as s:
            row = s.execute(
                select(JobFile).where(JobFile.job_id == job_id, JobFile.file_id == file_id)
            ).scalar_one_or_none()
            if row is None:
                row = JobFile(job_id=job_id, file_id=file_id)
                s.add(row)
            for key, value in fields.items():
                setattr(row, key, value)
            s.commit()
            return row

    def list_files(self, job_id: str) -> list[JobFile]:
        with self._sf() as s:
            return list(s.execute(
                select(JobFile).where(JobFile.job_id == job_id).order_by(JobFile.filename)
            ).scalars())

    # approvals ------------------------------------------------------------
    def create_approval(self, job_id: str, kind: str, payload: dict) -> Approval:
        with self._sf() as s:
            approval = Approval(job_id=job_id, kind=kind, payload=payload)
            s.add(approval)
            s.commit()
            return approval

    def list_approvals(self, job_id: str, status: str | None = None) -> list[Approval]:
        with self._sf() as s:
            stmt = select(Approval).where(Approval.job_id == job_id).order_by(Approval.created_at)
            if status:
                stmt = stmt.where(Approval.status == status)
            return list(s.execute(stmt).scalars())

    def decide_approval(self, approval_id: str, decision: str, note: str | None) -> Approval:
        with self._sf() as s:
            approval = s.get(Approval, approval_id)
            approval.status = (ApprovalStatus.APPROVED if decision == "approve"
                               else ApprovalStatus.REJECTED)
            approval.decision_note = note
            approval.decided_at = datetime.now(UTC)
            s.commit()
            return approval

    # audit ----------------------------------------------------------------
    def audit(self, job_id: str, action: str, detail: dict) -> AuditEntry:
        with self._sf() as s:
            entry = AuditEntry(job_id=job_id, action=action, detail=detail)
            s.add(entry)
            s.commit()
            return entry

    # npi cache ------------------------------------------------------------
    def get_npi(self, npi: str) -> NpiCache | None:
        with self._sf() as s:
            return s.get(NpiCache, npi)

    def put_npi(self, npi: str, specialty: str | None, taxonomy_code: str | None,
                is_individual: bool) -> NpiCache:
        with self._sf() as s:
            row = s.get(NpiCache, npi) or NpiCache(npi=npi)
            row.specialty, row.taxonomy_code, row.is_individual = specialty, taxonomy_code, is_individual
            s.add(row)
            s.commit()
            return row


def get_repository() -> Repository:
    from .session import get_sessionmaker

    return Repository(get_sessionmaker())
