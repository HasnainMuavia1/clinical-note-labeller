from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class JobSummary(BaseModel):
    id: str
    status: str
    stage: str
    progress: float
    created_at: datetime
    file_count: int = 0
    files_done: int = 0
    files_total: int = 0


class FileDetail(BaseModel):
    file_id: str
    filename: str
    source_path: str
    status: str
    parser: str | None
    parse_trail: list[dict] = Field(default_factory=list)
    has_codes: bool
    code_hits: list[dict] = Field(default_factory=list)
    code_rejected: list[dict] = Field(default_factory=list)
    npis: list[str] = Field(default_factory=list)
    specialty: str | None
    confidence: float
    method: str | None
    output_path: str | None


class JobDetail(JobSummary):
    original_filenames: list[str] = Field(default_factory=list)
    batch_id: str | None = None
    error: str | None = None
    pending_approvals: int = 0


class ApprovalOut(BaseModel):
    id: str
    kind: str
    status: str
    payload: dict[str, Any]
    created_at: datetime


class ApprovalDecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    note: str | None = None
    # low_confidence: `specialty` applies to every file in the approval;
    # `specialties` (file_id -> specialty) overrides it per file.
    specialty: str | None = None
    specialties: dict[str, str] | None = None


class AuditEntryOut(BaseModel):
    id: str
    action: str
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class Page(BaseModel):
    items: list[Any]
    next_cursor: str | None = None
