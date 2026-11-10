from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    pass


class AnalyticsJobStatus:
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"

    ALL_STATUSES = {
        QUEUED,
        RUNNING,
        RETRYING,
        SUCCEEDED,
        FAILED,
        CANCEL_REQUESTED,
        CANCELLED,
    }

    TERMINAL_STATUSES = {
        SUCCEEDED,
        FAILED,
        CANCELLED,
    }

    VALID_TRANSITIONS: dict[str, set[str]] = {
        QUEUED: {RUNNING, CANCEL_REQUESTED, CANCELLED},
        RUNNING: {SUCCEEDED, FAILED, RETRYING, CANCEL_REQUESTED},
        RETRYING: {QUEUED, FAILED, CANCEL_REQUESTED, CANCELLED},
        CANCEL_REQUESTED: {CANCELLED, FAILED, SUCCEEDED},
        SUCCEEDED: set(),
        FAILED: set(),
        CANCELLED: set(),
    }


class AnalyticsJob(Base):
    """Persistent lifecycle entity for an analytical processing job."""

    __tablename__ = "analytics_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    source_workbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    source_sheet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    operation_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=AnalyticsJobStatus.QUEUED, index=True
    )
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_stage: Mapped[str | None] = mapped_column(String(100), nullable=True)
    input_snapshot_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    input_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_sheet_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    result_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    safe_error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    claim_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    snapshot_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    snapshot_sheet_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    snapshot_sheet_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_col_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    snapshot_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    snapshot_storage_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name="fk_analytics_jobs_workspace_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_workbook_id", "organization_id"],
            ["workbooks.id", "workbooks.organization_id"],
            name="fk_analytics_jobs_workbook_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_sheet_id", "organization_id"],
            ["sheets.id", "sheets.organization_id"],
            name="fk_analytics_jobs_sheet_org",
            ondelete="CASCADE",
        ),
        Index(
            "ix_analytics_jobs_org_idempotency",
            "organization_id",
            "idempotency_key",
            unique=True,
            postgresql_where=(idempotency_key.isnot(None)),
        ),
        Index(
            "ix_analytics_jobs_org_status_created",
            "organization_id",
            "status",
            "created_at",
        ),
    )


class AnalyticsOutboxEvent(Base):
    """Transactional outbox event committed atomically with AnalyticsJob."""

    __tablename__ = "analytics_outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytics_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="analytics.job.scheduled"
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        Index(
            "ix_analytics_outbox_pending",
            "published_at",
            "locked_at",
        ),
    )


class AnalyticsResult(Base):
    """Persistent business result of an executed analytical job."""

    __tablename__ = "analytics_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytics_jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    result_type: Mapped[str] = mapped_column(String(50), nullable=False)
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    artifact_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    artifact_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    artifact_checksum_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    column_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    published_sheet_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    published_sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    publish_idempotency_key: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        Index(
            "ix_analytics_results_org_publish_idempotency",
            "organization_id",
            "publish_idempotency_key",
            unique=True,
            postgresql_where=(publish_idempotency_key.isnot(None)),
        ),
    )


class AnalyticsQuotaReservation(Base):
    """PostgreSQL-backed atomic storage quota reservation for analytics workloads."""

    __tablename__ = "analytics_quota_reservations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("analytics_jobs.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "ix_analytics_quota_res_org_status_expires",
            "organization_id",
            "status",
            "expires_at",
        ),
    )
