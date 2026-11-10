"""add batch_id and cell_versions to history

Revision ID: a71c8e2d4b5f
Revises: f19c3b8a4d2e
Create Date: 2026-11-01 15:10:00.000000

"""

import uuid
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a71c8e2d4b5f"
down_revision: Union[str, None] = "f19c3b8a4d2e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add batch_id with server_default gen_random_uuid()
    op.add_column(
        "sheet_cell_history",
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
    )
    op.create_index(
        op.f("ix_sheet_cell_history_batch_id"),
        "sheet_cell_history",
        ["batch_id"],
        unique=False,
    )

    # 2. Add cell_version_before and cell_version_after
    op.add_column(
        "sheet_cell_history",
        sa.Column(
            "cell_version_before",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )
    op.add_column(
        "sheet_cell_history",
        sa.Column(
            "cell_version_after",
            sa.Integer(),
            server_default=sa.text("2"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("sheet_cell_history", "cell_version_after")
    op.drop_column("sheet_cell_history", "cell_version_before")
    op.drop_index(
        op.f("ix_sheet_cell_history_batch_id"),
        table_name="sheet_cell_history",
    )
    op.drop_column("sheet_cell_history", "batch_id")

