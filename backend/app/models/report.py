"""SQLAlchemy models for reporting subsystem, immutable snapshots, and artifact management."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.dashboard import Dashboard, DashboardWidget
    from app.models.organization import Organization
    from app.models.user import User
    from app.models.workspace import Workspace


class ReportRunStatus:
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    ALL_STATUSES = {
        QUEUED,
        RUNNING,
        SUCCEEDED,
        FAILED,
        CANCELLED,
    }


class ReportDefinition(Base):
    """Template definition for structured multi-section business intelligence reports."""

    __tablename__ = "report_definitions"

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
    dashboard_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    format: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pdf",
    )
    page_orientation: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="portrait",
    )
    page_size: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="a4",
    )
    header_text: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    footer_text: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    include_table_of_contents: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    include_cover_page: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    theme: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="default",
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_report_defs_id_org"),
        ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            ondelete="CASCADE",
            name="fk_report_defs_workspace_org",
        ),
        ForeignKeyConstraint(
            ["dashboard_id", "organization_id"],
            ["dashboards.id", "dashboards.organization_id"],
            ondelete="SET NULL (dashboard_id)",
            name="fk_report_defs_dashboard_org",
        ),
        Index("ix_report_defs_org_workspace", "organization_id", "workspace_id"),
        Index("ix_report_defs_org_archived", "organization_id", "is_archived"),
    )

    organization: Mapped[Organization] = relationship()
    workspace: Mapped[Workspace] = relationship(overlaps="organization")
    dashboard: Mapped[Dashboard | None] = relationship(overlaps="organization,workspace")
    created_by: Mapped[User | None] = relationship()
    sections: Mapped[list[ReportSection]] = relationship(
        "ReportSection",
        back_populates="report_definition",
        cascade="all, delete-orphan",
        order_by="ReportSection.sort_order",
    )
    runs: Mapped[list[ReportRun]] = relationship(
        "ReportRun",
        back_populates="report_definition",
        passive_deletes=True,
    )


class ReportSection(Base):
    """Ordered content block within a report definition (chart, table, summary, markdown)."""

    __tablename__ = "report_sections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    report_definition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    widget_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    section_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    title: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    custom_content: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    override_query_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    sort_order: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    page_break_after: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_report_sections_id_org"),
        UniqueConstraint("report_definition_id", "sort_order", name="uq_report_sections_def_order"),
        ForeignKeyConstraint(
            ["report_definition_id", "organization_id"],
            ["report_definitions.id", "report_definitions.organization_id"],
            ondelete="CASCADE",
            name="fk_report_sections_def_org",
        ),
        ForeignKeyConstraint(
            ["widget_id", "organization_id"],
            ["dashboard_widgets.id", "dashboard_widgets.organization_id"],
            ondelete="SET NULL (widget_id)",
            name="fk_report_sections_widget_org",
        ),
        Index("ix_report_sections_def_order", "report_definition_id", "sort_order"),
    )

    report_definition: Mapped[ReportDefinition] = relationship(back_populates="sections")
    widget: Mapped[DashboardWidget | None] = relationship(overlaps="report_definition,sections")


class ReportRun(Base):
    """Immutable execution history and materialized snapshot for an on-demand report."""

    __tablename__ = "report_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    report_definition_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    report_title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    trigger_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="manual",
    )
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=ReportRunStatus.QUEUED,
        index=True,
    )
    idempotency_key: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
        index=True,
    )
    claim_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    claim_generation: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3,
    )
    snapshot_schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    report_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    data_revision_ref: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    artifact_path: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    file_size_bytes: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    checksum_sha256: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    pdf_artifact_path: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    pdf_checksum_sha256: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    pdf_file_size_bytes: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    pdf_page_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    pdf_rendered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    execution_duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    error_code: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_report_runs_id_org"),
        UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_report_runs_org_idempotency"
        ),
        ForeignKeyConstraint(
            ["report_definition_id", "organization_id"],
            ["report_definitions.id", "report_definitions.organization_id"],
            ondelete="SET NULL (report_definition_id)",
            name="fk_report_runs_def_org",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            ondelete="SET NULL (workspace_id)",
            name="fk_report_runs_workspace_org",
        ),
        CheckConstraint("trigger_type IN ('manual', 'api')", name="ck_report_runs_trigger_type"),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_report_runs_status",
        ),
        CheckConstraint(
            "(pdf_file_size_bytes IS NULL OR pdf_file_size_bytes >= 0) AND (pdf_page_count IS NULL OR pdf_page_count > 0)",
            name="ck_report_runs_pdf_metrics",
        ),
        Index("ix_report_runs_org_status_created", "organization_id", "status", "created_at"),
    )

    organization: Mapped[Organization] = relationship(overlaps="runs")
    workspace: Mapped[Workspace | None] = relationship(overlaps="organization,runs")
    report_definition: Mapped[ReportDefinition | None] = relationship(
        back_populates="runs", overlaps="organization,workspace,runs"
    )
    created_by: Mapped[User | None] = relationship()
    download_tokens: Mapped[list[ReportDownloadToken]] = relationship(
        "ReportDownloadToken",
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    outbox_events: Mapped[list[ReportOutboxEvent]] = relationship(
        "ReportOutboxEvent",
        back_populates="run",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class ReportDownloadToken(Base):
    """Short-lived, single-use cryptographically secure authorized download token."""

    __tablename__ = "report_download_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    is_revoked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )
    download_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    max_downloads: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    last_downloaded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "organization_id"],
            ["report_runs.id", "report_runs.organization_id"],
            ondelete="CASCADE",
            name="fk_report_download_tokens_run_org",
        ),
        Index("ix_report_download_tokens_lookup", "token_hash", "is_revoked", "expires_at"),
    )

    run: Mapped[ReportRun] = relationship(back_populates="download_tokens")
    organization: Mapped[Organization] = relationship(overlaps="download_tokens,run")
    created_by: Mapped[User | None] = relationship()


class ReportOutboxEvent(Base):
    """Transactional outbox event committed atomically with report run creation."""

    __tablename__ = "report_outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
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
        String(50),
        nullable=False,
        default="report.run.dispatched",
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    locked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    locked_by: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "organization_id"],
            ["report_runs.id", "report_runs.organization_id"],
            ondelete="CASCADE",
            name="fk_report_outbox_run_org",
        ),
        Index("ix_report_outbox_pending", "published_at", "locked_at"),
    )

    run: Mapped[ReportRun] = relationship(back_populates="outbox_events")
    organization: Mapped[Organization] = relationship(overlaps="outbox_events,run")
