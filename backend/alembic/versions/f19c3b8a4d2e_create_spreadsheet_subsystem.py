"""create_spreadsheet_subsystem

Revision ID: f19c3b8a4d2e
Revises: e48a1c9b2d3e
Create Date: 2026-11-01 14:45:00.000000

"""

import uuid
from collections.abc import Sequence
from datetime import UTC
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f19c3b8a4d2e"
down_revision: Union[str, None] = "e48a1c9b2d3e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add composite unique constraint to workspaces to allow composite FK referencing (workspace_id, organization_id)
    op.create_unique_constraint(
        "uq_workspaces_id_org",
        "workspaces",
        ["id", "organization_id"],
    )

    # 2. Create workbooks table
    op.create_table(
        "workbooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("id", "organization_id", name="uq_workbooks_id_org"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            ondelete="CASCADE",
            name="fk_workbooks_workspace_org",
        ),
    )
    op.create_index("ix_workbooks_organization_id", "workbooks", ["organization_id"])
    op.create_index("ix_workbooks_workspace_id", "workbooks", ["workspace_id"])
    op.create_index("ix_workbooks_created_by_user_id", "workbooks", ["created_by_user_id"])

    # 3. Create sheets table
    op.create_table(
        "sheets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("workbook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("column_count", sa.Integer(), nullable=False, server_default="26"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("id", "organization_id", name="uq_sheets_id_org"),
        sa.UniqueConstraint("workbook_id", "name", name="uq_sheets_workbook_name"),
        sa.ForeignKeyConstraint(
            ["workbook_id", "organization_id"],
            ["workbooks.id", "workbooks.organization_id"],
            ondelete="CASCADE",
            name="fk_sheets_workbook_org",
        ),
    )
    op.create_index("ix_sheets_workbook_id", "sheets", ["workbook_id"])
    op.create_index("ix_sheets_organization_id", "sheets", ["organization_id"])

    # 4. Create sheet_columns table
    op.create_table(
        "sheet_columns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("sheet_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sheets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("column_index", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("data_type", sa.String(length=20), nullable=False, server_default="text"),
        sa.Column("width", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("format", sa.String(length=50), nullable=True),
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("sheet_id", "column_index", name="uq_sheet_columns_sheet_col_idx"),
    )
    op.create_index("ix_sheet_columns_sheet_id", "sheet_columns", ["sheet_id"])

    # 5. Create sheet_rows table
    op.create_table(
        "sheet_rows",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("sheet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("cells", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("sheet_id", "row_index", name="uq_sheet_rows_sheet_row_idx"),
        sa.ForeignKeyConstraint(
            ["sheet_id", "organization_id"],
            ["sheets.id", "sheets.organization_id"],
            ondelete="CASCADE",
            name="fk_sheet_rows_sheet_org",
        ),
    )
    op.create_index("ix_sheet_rows_sheet_id_row_idx", "sheet_rows", ["sheet_id", "row_index"])
    op.create_index("ix_sheet_rows_organization_id", "sheet_rows", ["organization_id"])

    # 6. Create sheet_cell_dependencies table
    op.create_table(
        "sheet_cell_dependencies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("sheet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("precedent_cell_ref", sa.String(length=20), nullable=False),
        sa.Column("dependent_cell_ref", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("sheet_id", "precedent_cell_ref", "dependent_cell_ref", name="uq_sheet_deps_prec_dep"),
        sa.ForeignKeyConstraint(
            ["sheet_id", "organization_id"],
            ["sheets.id", "sheets.organization_id"],
            ondelete="CASCADE",
            name="fk_sheet_deps_sheet_org",
        ),
    )
    op.create_index("ix_sheet_deps_precedent", "sheet_cell_dependencies", ["sheet_id", "precedent_cell_ref"])
    op.create_index("ix_sheet_deps_dependent", "sheet_cell_dependencies", ["sheet_id", "dependent_cell_ref"])

    # 7. Create sheet_cell_history table
    op.create_table(
        "sheet_cell_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        sa.Column("sheet_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("cell_ref", sa.String(length=20), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("column_index", sa.Integer(), nullable=False),
        sa.Column("previous_raw_value", sa.Text(), nullable=True),
        sa.Column("previous_computed_value", sa.Text(), nullable=True),
        sa.Column("previous_formula", sa.Text(), nullable=True),
        sa.Column("new_raw_value", sa.Text(), nullable=True),
        sa.Column("new_computed_value", sa.Text(), nullable=True),
        sa.Column("new_formula", sa.Text(), nullable=True),
        sa.Column("row_version_before", sa.Integer(), nullable=False),
        sa.Column("row_version_after", sa.Integer(), nullable=False),
        sa.Column("edited_by_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_undone", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["sheet_id", "organization_id"],
            ["sheets.id", "sheets.organization_id"],
            ondelete="CASCADE",
            name="fk_sheet_history_sheet_org",
        ),
    )
    op.create_index("ix_sheet_history_sheet_cell", "sheet_cell_history", ["sheet_id", "cell_ref", "created_at"])
    op.create_index("ix_sheet_history_sheet_created", "sheet_cell_history", ["sheet_id", "created_at"])

    # 8. Seed permissions table with spreadsheet permission codes
    from datetime import datetime, timezone
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

    spreadsheet_perms = [
        {"id": uuid.uuid4(), "code": "spreadsheets.read", "name": "View Spreadsheets", "description": "View workbooks, sheets, and grid data", "category": "spreadsheets", "created_at": now},
        {"id": uuid.uuid4(), "code": "spreadsheets.create", "name": "Create Spreadsheets", "description": "Create new workbooks and sheets", "category": "spreadsheets", "created_at": now},
        {"id": uuid.uuid4(), "code": "spreadsheets.update", "name": "Edit Spreadsheets", "description": "Edit cells, formulas, columns, and sheets", "category": "spreadsheets", "created_at": now},
        {"id": uuid.uuid4(), "code": "spreadsheets.delete", "name": "Delete Spreadsheets", "description": "Delete workbooks and sheets", "category": "spreadsheets", "created_at": now},
    ]
    op.bulk_insert(permissions_table, spreadsheet_perms)

    # 9. Backfill system roles with the spreadsheet permissions
    # Fetch all newly created permission IDs
    connection = op.get_bind()
    perm_map = {}
    rows = connection.execute(sa.text("SELECT code, id FROM permissions WHERE code LIKE 'spreadsheets.%'")).fetchall()
    for code, p_id in rows:
        perm_map[code] = p_id

    # For each organization:
    # owner: all 4
    # admin: all 4
    # member: read, create, update
    # viewer: read
    role_perms_table = sa.table(
        "role_permissions",
        sa.column("role_id", postgresql.UUID(as_uuid=True)),
        sa.column("permission_id", postgresql.UUID(as_uuid=True)),
        sa.column("organization_id", postgresql.UUID(as_uuid=True)),
    )

    org_roles = connection.execute(sa.text("SELECT id, organization_id, slug FROM organization_roles WHERE is_system = true")).fetchall()
    role_perm_entries = []
    for r_id, org_id, slug in org_roles:
        if slug in ("owner", "admin"):
            for code in ("spreadsheets.read", "spreadsheets.create", "spreadsheets.update", "spreadsheets.delete"):
                if code in perm_map:
                    role_perm_entries.append({"role_id": r_id, "permission_id": perm_map[code], "organization_id": org_id})
        elif slug == "member":
            for code in ("spreadsheets.read", "spreadsheets.create", "spreadsheets.update"):
                if code in perm_map:
                    role_perm_entries.append({"role_id": r_id, "permission_id": perm_map[code], "organization_id": org_id})
        elif slug == "viewer":
            if "spreadsheets.read" in perm_map:
                role_perm_entries.append({"role_id": r_id, "permission_id": perm_map["spreadsheets.read"], "organization_id": org_id})

    if role_perm_entries:
        op.bulk_insert(role_perms_table, role_perm_entries)


def downgrade() -> None:
    # 1. Remove spreadsheet permissions from role_permissions
    connection = op.get_bind()
    connection.execute(sa.text(
        "DELETE FROM role_permissions WHERE permission_id IN (SELECT id FROM permissions WHERE code LIKE 'spreadsheets.%')"
    ))

    # 2. Delete spreadsheet permissions from permissions table
    connection.execute(sa.text(
        "DELETE FROM permissions WHERE code LIKE 'spreadsheets.%'"
    ))

    # 3. Drop tables in reverse order
    op.drop_table("sheet_cell_history")
    op.drop_table("sheet_cell_dependencies")
    op.drop_table("sheet_rows")
    op.drop_table("sheet_columns")
    op.drop_table("sheets")
    op.drop_table("workbooks")

    # 4. Drop unique constraint from workspaces
    op.drop_constraint("uq_workspaces_id_org", "workspaces", type_="unique")
