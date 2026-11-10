"""SQLAlchemy models for external spreadsheet synchronization mappings, runs, and conflicts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, utc_now


class SpreadsheetSyncMapping(Base):
    """Persistent bidirectional or unidirectional mapping between a VIBE sheet and an external sheet."""

    __tablename__ = "spreadsheet_sync_mappings"

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
    integration_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("external_integrations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workbooks.id", ondelete="CASCADE"),
        nullable=False,
    )
    sheet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sheets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_spreadsheet_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    external_sheet_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    external_sheet_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    direction: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="bidirectional",  # 'vibe_to_google', 'google_to_vibe', 'bidirectional'
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="idle",  # 'idle', 'syncing', 'conflict', 'error', 'paused'
        index=True,
    )
    conflict_policy: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="manual",  # 'manual', 'vibe_wins', 'external_wins'
    )
    last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_checkpoint_vibe_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    last_checkpoint_external_version: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    last_checkpoint_hash: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    schedule_interval_minutes: Mapped[int | None] = mapped_column(
        Integer,
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
        UniqueConstraint("sheet_id", "external_spreadsheet_id", "external_sheet_id", name="uq_sync_mapping_vibe_ext"),
        Index("ix_sync_mappings_org_status", "organization_id", "status"),
    )


class SpreadsheetSyncRun(Base):
    """Execution history record for an external synchronization run."""

    __tablename__ = "spreadsheet_sync_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    mapping_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spreadsheet_sync_mappings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    triggered_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    trigger_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="manual",  # 'manual', 'scheduled', 'retry'
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending",  # 'pending', 'running', 'completed', 'failed', 'conflict'
        index=True,
    )
    direction: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    rows_transferred: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    cells_updated: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    conflict_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    error_message: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
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


class SpreadsheetSyncConflict(Base):
    """Detailed record of a cell-level conflicting change requiring manual or policy resolution."""

    __tablename__ = "spreadsheet_sync_conflicts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spreadsheet_sync_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    mapping_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("spreadsheet_sync_mappings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    row_index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    column_letter: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )
    vibe_value: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    external_value: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="detected",  # 'detected', 'resolved'
        index=True,
    )
    resolution_choice: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,  # 'keep_vibe', 'keep_external', 'manual'
    )
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
