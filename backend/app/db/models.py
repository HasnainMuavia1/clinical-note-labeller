from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from .base import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_BATCH = "awaiting_batch"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FileStatus(StrEnum):
    PENDING = "pending"
    PARSED = "parsed"
    UNPARSED = "unparsed"
    SKIPPED = "skipped"
    FILED = "filed"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    api_key_id: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.PENDING)
    stage: Mapped[str] = mapped_column(String(32), default="intake")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    original_filenames: Mapped[list] = mapped_column(JSONType, default=list)
    batch_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    files: Mapped[list[JobFile]] = relationship(back_populates="job", cascade="all, delete-orphan")
    approvals: Mapped[list[Approval]] = relationship(back_populates="job", cascade="all, delete-orphan")
    audit_entries: Mapped[list[AuditEntry]] = relationship(back_populates="job",
                                                             cascade="all, delete-orphan")


class JobFile(Base):
    __tablename__ = "job_files"
    __table_args__ = (UniqueConstraint("job_id", "file_id", name="uq_job_file"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    file_id: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(512), default="")
    source_path: Mapped[str] = mapped_column(String(1024), default="")
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default=FileStatus.PENDING)
    parser: Mapped[str | None] = mapped_column(String(32), nullable=True)
    parse_trail: Mapped[list] = mapped_column(JSONType, default=list)
    has_codes: Mapped[bool] = mapped_column(Boolean, default=False)
    code_hits: Mapped[list] = mapped_column(JSONType, default=list)
    code_rejected: Mapped[list] = mapped_column(JSONType, default=list)
    npis: Mapped[list] = mapped_column(JSONType, default=list)
    specialty: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    output_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    job: Mapped[Job] = relationship(back_populates="files")


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONType, default=dict)
    status: Mapped[str] = mapped_column(String(32), default=ApprovalStatus.PENDING)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[Job] = relationship(back_populates="approvals")


class AuditEntry(Base):
    __tablename__ = "audit_entries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(64))
    detail: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    job: Mapped[Job] = relationship(back_populates="audit_entries")


class NpiCache(Base):
    __tablename__ = "npi_cache"

    npi: Mapped[str] = mapped_column(String(10), primary_key=True)
    specialty: Mapped[str | None] = mapped_column(String(128), nullable=True)
    taxonomy_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_individual: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
