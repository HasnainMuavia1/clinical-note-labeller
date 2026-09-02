"""initial schema

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("api_key_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), unique=True, nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("original_filenames", JSON_TYPE, nullable=True),
        sa.Column("batch_id", sa.String(128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])

    op.create_table(
        "job_files",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("jobs.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("file_id", sa.String(64), nullable=False, index=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("source_path", sa.String(1024), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("parser", sa.String(32), nullable=True),
        sa.Column("parse_trail", JSON_TYPE, nullable=True),
        sa.Column("has_codes", sa.Boolean(), nullable=False),
        sa.Column("code_hits", JSON_TYPE, nullable=True),
        sa.Column("code_rejected", JSON_TYPE, nullable=True),
        sa.Column("npis", JSON_TYPE, nullable=True),
        sa.Column("specialty", sa.String(128), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("method", sa.String(32), nullable=True),
        sa.Column("output_path", sa.String(1024), nullable=True),
        sa.UniqueConstraint("job_id", "file_id", name="uq_job_file"),
    )

    op.create_table(
        "approvals",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("jobs.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("payload", JSON_TYPE, nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "audit_entries",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("job_id", sa.String(64), sa.ForeignKey("jobs.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("detail", JSON_TYPE, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "npi_cache",
        sa.Column("npi", sa.String(10), primary_key=True),
        sa.Column("specialty", sa.String(128), nullable=True),
        sa.Column("taxonomy_code", sa.String(16), nullable=True),
        sa.Column("is_individual", sa.Boolean(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("npi_cache")
    op.drop_table("audit_entries")
    op.drop_table("approvals")
    op.drop_table("job_files")
    op.drop_index("ix_jobs_created_at", table_name="jobs")
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_table("jobs")
