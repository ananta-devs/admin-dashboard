"""create_phase8_integrations_dashboards_scripts

Revision ID: e82f1b4a9c3d
Revises: d71a4f8e2c9b
Create Date: 2026-11-01 21:20:00.000000

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
revision: str = "e82f1b4a9c3d"
down_revision: Union[str, None] = "d71a4f8e2c9b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. external_integrations
    op.create_table(
        "external_integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "connected_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("account_email", sa.String(length=255), nullable=False),
        sa.Column("account_name", sa.String(length=255), nullable=True),
        sa.Column("account_avatar_url", sa.String(length=512), nullable=True),
        sa.Column("encrypted_access_token", sa.String(length=1024), nullable=False),
        sa.Column("encrypted_refresh_token", sa.String(length=1024), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "authorized_scopes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "organization_id",
            "provider",
            "account_email",
            name="uq_external_integrations_org_prov_email",
        ),
    )
    op.create_index("ix_external_integrations_org_id", "external_integrations", ["organization_id"])
    op.create_index(
        "ix_external_integrations_org_prov",
        "external_integrations",
        ["organization_id", "provider"],
    )
    op.create_index("ix_external_integrations_status", "external_integrations", ["status"])

    # 2. oauth_states
    op.create_table(
        "oauth_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("state_token", sa.String(length=128), nullable=False, unique=True),
        sa.Column("redirect_uri", sa.String(length=512), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_oauth_states_state_token", "oauth_states", ["state_token"])
    op.create_index("ix_oauth_states_org_id", "oauth_states", ["organization_id"])

    # 3. spreadsheet_sync_mappings
    op.create_table(
        "spreadsheet_sync_mappings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "integration_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("external_integrations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workbook_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workbooks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sheet_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sheets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("external_spreadsheet_id", sa.String(length=255), nullable=False),
        sa.Column("external_sheet_id", sa.String(length=255), nullable=False),
        sa.Column("external_sheet_name", sa.String(length=255), nullable=True),
        sa.Column(
            "direction", sa.String(length=32), nullable=False, server_default="bidirectional"
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="idle"),
        sa.Column("conflict_policy", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checkpoint_vibe_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_checkpoint_external_version", sa.String(length=128), nullable=True),
        sa.Column("last_checkpoint_hash", sa.String(length=64), nullable=True),
        sa.Column("schedule_interval_minutes", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint(
            "sheet_id",
            "external_spreadsheet_id",
            "external_sheet_id",
            name="uq_sync_mapping_vibe_ext",
        ),
    )
    op.create_index(
        "ix_sync_mappings_org_status", "spreadsheet_sync_mappings", ["organization_id", "status"]
    )
    op.create_index("ix_sync_mappings_sheet_id", "spreadsheet_sync_mappings", ["sheet_id"])
    op.create_index(
        "ix_sync_mappings_integration_id", "spreadsheet_sync_mappings", ["integration_id"]
    )

    # 4. spreadsheet_sync_runs
    op.create_table(
        "spreadsheet_sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "mapping_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("spreadsheet_sync_mappings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "triggered_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("trigger_type", sa.String(length=32), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("direction", sa.String(length=32), nullable=False),
        sa.Column("rows_transferred", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cells_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("conflict_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_sync_runs_mapping_id", "spreadsheet_sync_runs", ["mapping_id"])
    op.create_index(
        "ix_sync_runs_org_status", "spreadsheet_sync_runs", ["organization_id", "status"]
    )

    # 5. spreadsheet_sync_conflicts
    op.create_table(
        "spreadsheet_sync_conflicts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("spreadsheet_sync_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "mapping_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("spreadsheet_sync_mappings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("column_letter", sa.String(length=10), nullable=False),
        sa.Column("vibe_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("external_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="detected"),
        sa.Column("resolution_choice", sa.String(length=32), nullable=True),
        sa.Column(
            "resolved_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_sync_conflicts_run_id", "spreadsheet_sync_conflicts", ["run_id"])
    op.create_index(
        "ix_sync_conflicts_mapping_status", "spreadsheet_sync_conflicts", ["mapping_id", "status"]
    )

    # 6. dashboards
    op.create_table(
        "dashboards",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "layout_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("is_shared", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_dashboards_org_id", "dashboards", ["organization_id"])
    op.create_index("ix_dashboards_workspace_id", "dashboards", ["workspace_id"])

    # 7. dashboard_widgets
    op.create_table(
        "dashboard_widgets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "dashboard_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dashboards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("widget_type", sa.String(length=50), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "query_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("position_x", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("position_y", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("width", sa.Integer(), nullable=False, server_default="6"),
        sa.Column("height", sa.Integer(), nullable=False, server_default="4"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_dashboard_widgets_dashboard_id", "dashboard_widgets", ["dashboard_id"])
    op.create_index("ix_dashboard_widgets_org_id", "dashboard_widgets", ["organization_id"])

    # 8. python_scripts
    op.create_table(
        "python_scripts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
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
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column(
            "parameters_schema",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_python_scripts_org_id", "python_scripts", ["organization_id"])

    # 9. python_script_runs
    op.create_table(
        "python_script_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column(
            "script_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("python_scripts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "requested_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "source_sheet_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sheets.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("input_cell_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("execution_time_ms", sa.Integer(), nullable=True),
        sa.Column("memory_peak_mb", sa.Float(), nullable=True),
        sa.Column("stdout_logs", sa.Text(), nullable=True),
        sa.Column("stderr_logs", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_python_script_runs_script_id", "python_script_runs", ["script_id"])
    op.create_index(
        "ix_python_script_runs_org_status", "python_script_runs", ["organization_id", "status"]
    )

    # 10. Seed permissions table with Phase 8 permissions
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

    phase8_perms = [
        # Integrations
        {
            "id": uuid.uuid4(),
            "code": "integrations.read",
            "name": "View Integrations",
            "description": "View connected external integrations and status",
            "category": "integrations",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "integrations.manage",
            "name": "Manage Integrations",
            "description": "Connect, configure, and disconnect external integrations",
            "category": "integrations",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "integrations.sync",
            "name": "Synchronize External Spreadsheets",
            "description": "Trigger and manage spreadsheet synchronization",
            "category": "integrations",
            "created_at": now,
        },
        # Dashboards
        {
            "id": uuid.uuid4(),
            "code": "dashboards.read",
            "name": "View Dashboards",
            "description": "View analytics dashboards and chart widgets",
            "category": "dashboards",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "dashboards.create",
            "name": "Create Dashboards",
            "description": "Create new analytics dashboards",
            "category": "dashboards",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "dashboards.update",
            "name": "Update Dashboards",
            "description": "Edit dashboards, configure widgets and layout",
            "category": "dashboards",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "dashboards.delete",
            "name": "Delete Dashboards",
            "description": "Delete analytics dashboards",
            "category": "dashboards",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "dashboards.share",
            "name": "Share Dashboards",
            "description": "Share dashboards with workspace members",
            "category": "dashboards",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "dashboards.export",
            "name": "Export Dashboard Data",
            "description": "Export dashboard charts and data",
            "category": "dashboards",
            "created_at": now,
        },
        # Governed Scripts
        {
            "id": uuid.uuid4(),
            "code": "analytics.scripts.read",
            "name": "View Python Scripts",
            "description": "View governed analytical Python scripts and runs",
            "category": "analytics",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "analytics.scripts.create",
            "name": "Create Python Scripts",
            "description": "Author new governed Python analytics scripts",
            "category": "analytics",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "analytics.scripts.update",
            "name": "Update Python Scripts",
            "description": "Edit and version governed Python analytics scripts",
            "category": "analytics",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "analytics.scripts.execute",
            "name": "Execute Python Scripts",
            "description": "Execute governed Python scripts in isolated sandbox",
            "category": "analytics",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "analytics.scripts.delete",
            "name": "Delete Python Scripts",
            "description": "Delete governed Python analytics scripts",
            "category": "analytics",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "analytics.scripts.manage",
            "name": "Manage All Python Scripts",
            "description": "Manage, override, and reassign analytical scripts across all workspace members",
            "category": "analytics",
            "created_at": now,
        },
        {
            "id": uuid.uuid4(),
            "code": "analytics.scripts.manage_dependencies",
            "name": "Manage Script Dependencies",
            "description": "Manage allowlisted dependencies for Python scripts",
            "category": "analytics",
            "created_at": now,
        },
    ]
    op.bulk_insert(permissions_table, phase8_perms)

    # 11. Backfill system roles with Phase 8 permissions
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT code, id FROM permissions")).fetchall()
    perm_map = {code: p_id for code, p_id in rows}

    role_perms_table = sa.table(
        "role_permissions",
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    owner_admin_new_perms = [
        "integrations.read",
        "integrations.manage",
        "integrations.sync",
        "dashboards.read",
        "dashboards.create",
        "dashboards.update",
        "dashboards.delete",
        "dashboards.share",
        "dashboards.export",
        "analytics.scripts.read",
        "analytics.scripts.create",
        "analytics.scripts.update",
        "analytics.scripts.execute",
        "analytics.scripts.delete",
        "analytics.scripts.manage",
        "analytics.scripts.manage_dependencies",
    ]
    member_new_perms = [
        "integrations.read",
        "integrations.sync",
        "dashboards.read",
        "dashboards.create",
        "dashboards.update",
        "dashboards.share",
        "dashboards.export",
        "analytics.scripts.read",
        "analytics.scripts.create",
        "analytics.scripts.update",
        "analytics.scripts.execute",
    ]
    viewer_new_perms = [
        "dashboards.read",
        "dashboards.export",
        "analytics.scripts.read",
    ]

    # Query existing system roles
    role_rows = connection.execute(
        sa.text("SELECT id, slug FROM organization_roles WHERE is_system = true")
    ).fetchall()
    role_perms_to_insert = []
    for r_id, r_slug in role_rows:
        target_codes = []
        if r_slug in ("owner", "admin"):
            target_codes = owner_admin_new_perms
        elif r_slug == "member":
            target_codes = member_new_perms
        elif r_slug == "viewer":
            target_codes = viewer_new_perms

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
    # 1. Clean up Phase 8 role permissions
    connection = op.get_bind()
    connection.execute(
        sa.text("""
        DELETE FROM role_permissions
        WHERE permission_id IN (
            SELECT id FROM permissions
            WHERE code IN (
                'integrations.read', 'integrations.manage', 'integrations.sync',
                'dashboards.read', 'dashboards.create', 'dashboards.update', 'dashboards.delete', 'dashboards.share', 'dashboards.export',
                'analytics.scripts.read', 'analytics.scripts.create', 'analytics.scripts.update', 'analytics.scripts.execute', 'analytics.scripts.delete', 'analytics.scripts.manage_dependencies'
            )
        )
    """)
    )

    # 2. Delete Phase 8 permissions
    connection.execute(
        sa.text("""
        DELETE FROM permissions
        WHERE code IN (
            'integrations.read', 'integrations.manage', 'integrations.sync',
            'dashboards.read', 'dashboards.create', 'dashboards.update', 'dashboards.delete', 'dashboards.share', 'dashboards.export',
            'analytics.scripts.read', 'analytics.scripts.create', 'analytics.scripts.update', 'analytics.scripts.execute', 'analytics.scripts.delete', 'analytics.scripts.manage_dependencies'
        )
    """)
    )

    # 3. Drop tables in reverse order
    op.drop_table("python_script_runs")
    op.drop_table("python_scripts")
    op.drop_table("dashboard_widgets")
    op.drop_table("dashboards")
    op.drop_table("spreadsheet_sync_conflicts")
    op.drop_table("spreadsheet_sync_runs")
    op.drop_table("spreadsheet_sync_mappings")
    op.drop_table("oauth_states")
    op.drop_table("external_integrations")
