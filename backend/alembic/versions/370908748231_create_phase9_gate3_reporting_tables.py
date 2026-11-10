"""create_phase9_gate3_reporting_tables

Revision ID: 370908748231
Revises: e82f1b4a9c3d
Create Date: 2026-11-03 19:40:00.000000

"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "370908748231"
down_revision: Union[str, None] = "e82f1b4a9c3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 0. Add composite unique constraints on dashboards and dashboard_widgets to enable composite FKs
    op.create_unique_constraint(
        "uq_dashboards_id_org",
        "dashboards",
        ["id", "organization_id"],
    )
    op.create_unique_constraint(
        "uq_dashboard_widgets_id_org",
        "dashboard_widgets",
        ["id", "organization_id"],
    )

    # 1. report_definitions
    op.create_table(
        "report_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dashboard_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("format", sa.String(length=20), nullable=False, server_default="pdf"),
        sa.Column(
            "page_orientation", sa.String(length=20), nullable=False, server_default="portrait"
        ),
        sa.Column("page_size", sa.String(length=20), nullable=False, server_default="a4"),
        sa.Column("header_text", sa.String(length=255), nullable=True),
        sa.Column("footer_text", sa.String(length=255), nullable=True),
        sa.Column("include_table_of_contents", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("include_cover_page", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("theme", sa.String(length=50), nullable=False, server_default="default"),
        sa.Column("is_archived", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("id", "organization_id", name="uq_report_defs_id_org"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            ondelete="CASCADE",
            name="fk_report_defs_workspace_org",
        ),
        sa.ForeignKeyConstraint(
            ["dashboard_id", "organization_id"],
            ["dashboards.id", "dashboards.organization_id"],
            ondelete="SET NULL (dashboard_id)",
            name="fk_report_defs_dashboard_org",
        ),
    )
    op.create_index("ix_report_defs_org_id", "report_definitions", ["organization_id"])
    op.create_index("ix_report_defs_workspace_id", "report_definitions", ["workspace_id"])
    op.create_index("ix_report_defs_dashboard_id", "report_definitions", ["dashboard_id"])
    op.create_index("ix_report_defs_created_by", "report_definitions", ["created_by_user_id"])
    op.create_index(
        "ix_report_defs_org_workspace", "report_definitions", ["organization_id", "workspace_id"]
    )
    op.create_index(
        "ix_report_defs_org_archived", "report_definitions", ["organization_id", "is_archived"]
    )

    # 2. report_sections
    op.create_table(
        "report_sections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("report_definition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("widget_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("section_type", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("custom_content", sa.Text(), nullable=True),
        sa.Column(
            "override_query_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("page_break_after", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_report_sections_id_org"),
        sa.UniqueConstraint(
            "report_definition_id", "sort_order", name="uq_report_sections_def_order"
        ),
        sa.ForeignKeyConstraint(
            ["report_definition_id", "organization_id"],
            ["report_definitions.id", "report_definitions.organization_id"],
            ondelete="CASCADE",
            name="fk_report_sections_def_org",
        ),
        sa.ForeignKeyConstraint(
            ["widget_id", "organization_id"],
            ["dashboard_widgets.id", "dashboard_widgets.organization_id"],
            ondelete="SET NULL (widget_id)",
            name="fk_report_sections_widget_org",
        ),
    )
    op.create_index("ix_report_sections_def_id", "report_sections", ["report_definition_id"])
    op.create_index("ix_report_sections_org_id", "report_sections", ["organization_id"])
    op.create_index("ix_report_sections_widget_id", "report_sections", ["widget_id"])
    op.create_index(
        "ix_report_sections_def_order", "report_sections", ["report_definition_id", "sort_order"]
    )

    # 3. report_runs
    op.create_table(
        "report_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("report_definition_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("report_title", sa.String(length=255), nullable=False),
        sa.Column("trigger_type", sa.String(length=30), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("idempotency_key", sa.String(length=100), nullable=True),
        sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claim_generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("snapshot_schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "report_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "data_revision_ref",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("artifact_path", sa.String(length=500), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("execution_duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "queued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_report_runs_id_org"),
        sa.UniqueConstraint(
            "organization_id", "idempotency_key", name="uq_report_runs_org_idempotency"
        ),
        sa.ForeignKeyConstraint(
            ["report_definition_id", "organization_id"],
            ["report_definitions.id", "report_definitions.organization_id"],
            ondelete="SET NULL (report_definition_id)",
            name="fk_report_runs_def_org",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            ondelete="SET NULL (workspace_id)",
            name="fk_report_runs_workspace_org",
        ),
        sa.CheckConstraint("trigger_type IN ('manual', 'api')", name="ck_report_runs_trigger_type"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_report_runs_status",
        ),
    )
    op.create_index("ix_report_runs_org_id", "report_runs", ["organization_id"])
    op.create_index("ix_report_runs_def_id", "report_runs", ["report_definition_id"])
    op.create_index("ix_report_runs_workspace_id", "report_runs", ["workspace_id"])
    op.create_index("ix_report_runs_status", "report_runs", ["status"])
    op.create_index("ix_report_runs_idempotency", "report_runs", ["idempotency_key"])
    op.create_index("ix_report_runs_claim_token", "report_runs", ["claim_token"])
    op.create_index("ix_report_runs_lease_expires", "report_runs", ["lease_expires_at"])
    op.create_index(
        "ix_report_runs_org_status_created",
        "report_runs",
        ["organization_id", "status", "created_at"],
    )

    # 4. report_download_tokens
    op.create_table(
        "report_download_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_revoked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("download_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_downloads", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("token_hash", name="uq_report_download_tokens_hash"),
        sa.ForeignKeyConstraint(
            ["run_id", "organization_id"],
            ["report_runs.id", "report_runs.organization_id"],
            ondelete="CASCADE",
            name="fk_report_download_tokens_run_org",
        ),
    )
    op.create_index("ix_report_download_tokens_run_id", "report_download_tokens", ["run_id"])
    op.create_index(
        "ix_report_download_tokens_org_id", "report_download_tokens", ["organization_id"]
    )
    op.create_index("ix_report_download_tokens_expires", "report_download_tokens", ["expires_at"])
    op.create_index(
        "ix_report_download_tokens_lookup",
        "report_download_tokens",
        ["token_hash", "is_revoked", "expires_at"],
    )

    # 5. report_outbox_events
    op.create_table(
        "report_outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            sa.String(length=50),
            nullable=False,
            server_default="report.run.dispatched",
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "organization_id"],
            ["report_runs.id", "report_runs.organization_id"],
            ondelete="CASCADE",
            name="fk_report_outbox_run_org",
        ),
    )
    op.create_index("ix_report_outbox_run_id", "report_outbox_events", ["run_id"])
    op.create_index("ix_report_outbox_org_id", "report_outbox_events", ["organization_id"])
    op.create_index(
        "ix_report_outbox_pending", "report_outbox_events", ["published_at", "locked_at"]
    )

    # 6. Seed 6 report permissions into permissions table
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

    reporting_perms = [
        {
            "id": uuid.uuid4(),
            "code": "reports.read",
            "name": "View Reports",
            "description": "View report definitions, sections, and historical run metadata",
            "category": "reports",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "reports.create",
            "name": "Create Reports",
            "description": "Create new report definitions and configure sections",
            "category": "reports",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "reports.update",
            "name": "Update Reports",
            "description": "Edit report definitions, section layout, and query overrides",
            "category": "reports",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "reports.delete",
            "name": "Delete Reports",
            "description": "Archive or delete report definitions",
            "category": "reports",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "reports.run",
            "name": "Execute Report Generation",
            "description": "Trigger on-demand asynchronous report generation runs",
            "category": "reports",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "reports.export",
            "name": "Download Report Artifacts",
            "description": "Download completed PDF and data report artifacts",
            "category": "reports",
            "created_at": now,
        },
    ]
    op.bulk_insert(permissions_table, reporting_perms)

    # 7. Backfill system roles with Gate 3 permissions
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT code, id FROM permissions WHERE code LIKE 'reports.%'")
    ).fetchall()
    perm_map = {code: p_id for code, p_id in rows}

    role_perms_table = sa.table(
        "role_permissions",
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    owner_admin_perms = [
        "reports.read",
        "reports.create",
        "reports.update",
        "reports.delete",
        "reports.run",
        "reports.export",
    ]
    member_perms = [
        "reports.read",
        "reports.create",
        "reports.update",
        "reports.run",
        "reports.export",
    ]
    viewer_perms = ["reports.read", "reports.export"]

    role_rows = connection.execute(
        sa.text("SELECT id, slug FROM organization_roles WHERE is_system = true")
    ).fetchall()
    role_perms_to_insert = []
    for r_id, r_slug in role_rows:
        target_codes = []
        if r_slug in ("owner", "admin"):
            target_codes = owner_admin_perms
        elif r_slug == "member":
            target_codes = member_perms
        elif r_slug == "viewer":
            target_codes = viewer_perms

        for code in target_codes:
            if code in perm_map:
                role_perms_to_insert.append(
                    {
                        "role_id": r_id,
                        "permission_id": perm_map[code],
                        "created_at": now,
                    }
                )

    if role_perms_to_insert:
        op.bulk_insert(role_perms_table, role_perms_to_insert)


def downgrade() -> None:
    # 1. Clean up role permissions for reporting
    connection = op.get_bind()
    connection.execute(
        sa.text("""
        DELETE FROM role_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions
            WHERE code IN (
                'reports.read', 'reports.create', 'reports.update', 'reports.delete', 'reports.run', 'reports.export'
            )
        )
    """)
    )

    # 2. Delete reporting permissions
    connection.execute(
        sa.text("""
        DELETE FROM permissions
        WHERE code IN (
            'reports.read', 'reports.create', 'reports.update', 'reports.delete', 'reports.run', 'reports.export'
        )
    """)
    )

    # 3. Drop reporting tables in reverse order
    op.drop_table("report_outbox_events")
    op.drop_table("report_download_tokens")
    op.drop_table("report_runs")
    op.drop_table("report_sections")
    op.drop_table("report_definitions")

    # 4. Drop unique constraints added to dashboard tables
    op.drop_constraint("uq_dashboard_widgets_id_org", "dashboard_widgets", type_="unique")
    op.drop_constraint("uq_dashboards_id_org", "dashboards", type_="unique")
