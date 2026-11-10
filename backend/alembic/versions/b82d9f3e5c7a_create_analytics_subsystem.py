"""create_analytics_subsystem

Revision ID: b82d9f3e5c7a
Revises: a71c8e2d4b5f
Create Date: 2026-11-01 15:45:00.000000

"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timezone
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b82d9f3e5c7a"
down_revision: Union[str, None] = "a71c8e2d4b5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create analytics_jobs table
    op.create_table(
        "analytics_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_workbook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_sheet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("operation_id", sa.String(length=100), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_stage", sa.String(length=100), nullable=True),
        sa.Column("input_snapshot_id", sa.String(length=255), nullable=True),
        sa.Column("input_snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("source_sheet_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("result_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("safe_error_message", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("worker_id", sa.String(length=100), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("cancellation_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("id", "organization_id", name="uq_analytics_jobs_id_org"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            ondelete="CASCADE",
            name="fk_analytics_jobs_workspace_org",
        ),
        sa.ForeignKeyConstraint(
            ["source_workbook_id", "organization_id"],
            ["workbooks.id", "workbooks.organization_id"],
            ondelete="CASCADE",
            name="fk_analytics_jobs_workbook_org",
        ),
        sa.ForeignKeyConstraint(
            ["source_sheet_id", "organization_id"],
            ["sheets.id", "sheets.organization_id"],
            ondelete="CASCADE",
            name="fk_analytics_jobs_sheet_org",
        ),
    )
    op.create_index("ix_analytics_jobs_organization_id", "analytics_jobs", ["organization_id"])
    op.create_index("ix_analytics_jobs_workspace_id", "analytics_jobs", ["workspace_id"])
    op.create_index("ix_analytics_jobs_source_workbook_id", "analytics_jobs", ["source_workbook_id"])
    op.create_index("ix_analytics_jobs_source_sheet_id", "analytics_jobs", ["source_sheet_id"])
    op.create_index("ix_analytics_jobs_requested_by_user_id", "analytics_jobs", ["requested_by_user_id"])
    op.create_index("ix_analytics_jobs_operation_id", "analytics_jobs", ["operation_id"])
    op.create_index("ix_analytics_jobs_status", "analytics_jobs", ["status"])
    op.create_index("ix_analytics_jobs_lease_expires_at", "analytics_jobs", ["lease_expires_at"])
    op.create_index("ix_analytics_jobs_result_id", "analytics_jobs", ["result_id"])
    op.create_index(
        "ix_analytics_jobs_org_idempotency",
        "analytics_jobs",
        ["organization_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_analytics_jobs_org_status_created",
        "analytics_jobs",
        ["organization_id", "status", "created_at"],
    )

    # 2. Create analytics_outbox_events table
    op.create_table(
        "analytics_outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("analytics_jobs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False, server_default="analytics.job.scheduled"),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_analytics_outbox_job_id", "analytics_outbox_events", ["job_id"])
    op.create_index("ix_analytics_outbox_organization_id", "analytics_outbox_events", ["organization_id"])
    op.create_index("ix_analytics_outbox_published_at", "analytics_outbox_events", ["published_at"])
    op.create_index("ix_analytics_outbox_pending", "analytics_outbox_events", ["published_at", "locked_at"])

    # 3. Create analytics_results table
    op.create_table(
        "analytics_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("analytics_jobs.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("result_type", sa.String(length=50), nullable=False),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("artifact_path", sa.String(length=500), nullable=True),
        sa.Column("artifact_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("artifact_checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("column_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_analytics_results_organization_id", "analytics_results", ["organization_id"])
    op.create_index("ix_analytics_results_job_id", "analytics_results", ["job_id"])

    # 4. Seed permissions table with analytics permission codes
    now = datetime.now(UTC)
    permissions_table = sa.table(
        "permissions",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.String),
        sa.column("category", sa.String),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    analytics_perms = [
        {"id": uuid.uuid4(), "code": "analytics.read", "name": "View Analytics", "description": "View analytics operations, jobs, and results", "category": "analytics", "created_at": now},
        {"id": uuid.uuid4(), "code": "analytics.create", "name": "Create Analysis", "description": "Submit and execute analytical jobs", "category": "analytics", "created_at": now},
        {"id": uuid.uuid4(), "code": "analytics.cancel", "name": "Cancel Analysis", "description": "Cancel queued or running analytical jobs", "category": "analytics", "created_at": now},
        {"id": uuid.uuid4(), "code": "analytics.export", "name": "Export Analysis", "description": "Export analytical results and publish to spreadsheets", "category": "analytics", "created_at": now},
    ]
    op.bulk_insert(permissions_table, analytics_perms)

    # 5. Backfill system roles with analytics permissions
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT code, id FROM permissions WHERE code LIKE 'analytics.%'")).fetchall()
    perm_map = {code: p_id for code, p_id in rows}

    role_perms_table = sa.table(
        "role_permissions",
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    org_roles = connection.execute(sa.text("SELECT id, slug FROM organization_roles WHERE is_system = true")).fetchall()
    role_perm_entries = []
    for r_id, slug in org_roles:
        if slug in ("owner", "admin"):
            for code in ("analytics.read", "analytics.create", "analytics.cancel", "analytics.export"):
                if code in perm_map:
                    role_perm_entries.append({"role_id": r_id, "permission_id": perm_map[code], "created_at": now})
        elif slug == "member":
            for code in ("analytics.read", "analytics.create", "analytics.cancel", "analytics.export"):
                if code in perm_map:
                    role_perm_entries.append({"role_id": r_id, "permission_id": perm_map[code], "created_at": now})
        elif slug == "viewer":
            if "analytics.read" in perm_map:
                role_perm_entries.append({"role_id": r_id, "permission_id": perm_map["analytics.read"], "created_at": now})

    if role_perm_entries:
        op.bulk_insert(role_perms_table, role_perm_entries)


def downgrade() -> None:
    # 1. Remove analytics permissions from role_permissions
    connection = op.get_bind()
    connection.execute(sa.text(
        "DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code LIKE 'analytics.%')"
    ))

    # 2. Delete analytics permissions from permissions table
    connection.execute(sa.text(
        "DELETE FROM permissions WHERE code LIKE 'analytics.%'"
    ))

    # 3. Drop tables in reverse order
    op.drop_table("analytics_results")
    op.drop_table("analytics_outbox_events")
    op.drop_table("analytics_jobs")
