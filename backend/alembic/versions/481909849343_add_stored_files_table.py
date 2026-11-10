"""add_stored_files_table

Revision ID: 481909849343
Revises: 370908748232
Create Date: 2026-11-04 21:15:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "481909849343"
down_revision: Union[str, None] = "370908748232"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stored_files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "organizations.id", ondelete="CASCADE", name="fk_stored_files_organization_id"
            ),
            nullable=False,
        ),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "uploaded_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", ondelete="SET NULL", name="fk_stored_files_uploaded_by_user_id"
            ),
            nullable=True,
        ),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_path", sa.String(length=500), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("folder", sa.String(length=255), server_default="/", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("clock_timestamp()"),
            nullable=False,
        ),
        sa.CheckConstraint("size_bytes >= 0", name="ck_stored_files_size_bytes_nonnegative"),
        sa.UniqueConstraint("storage_path", name="uq_stored_files_storage_path"),
        sa.UniqueConstraint(
            "organization_id", "folder", "filename", name="uq_stored_files_org_folder_filename"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name="fk_stored_files_workspace_composite",
            ondelete="SET NULL",
        ),
    )
    op.create_index("ix_stored_files_organization_id", "stored_files", ["organization_id"])
    op.create_index("ix_stored_files_workspace_id", "stored_files", ["workspace_id"])
    op.create_index("ix_stored_files_created_at", "stored_files", ["created_at"])
    op.create_index("ix_stored_files_org_folder", "stored_files", ["organization_id", "folder"])


def downgrade() -> None:
    op.drop_index("ix_stored_files_org_folder", table_name="stored_files")
    op.drop_index("ix_stored_files_created_at", table_name="stored_files")
    op.drop_index("ix_stored_files_workspace_id", table_name="stored_files")
    op.drop_index("ix_stored_files_organization_id", table_name="stored_files")
    op.drop_table("stored_files")
