"""create_analytics_quota_reservations

Revision ID: d71a4f8e2c9b
Revises: c93e1a5f7d2b
Create Date: 2026-11-01 20:45:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d71a4f8e2c9b"
down_revision: Union[str, None] = "c93e1a5f7d2b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create analytics_quota_reservations table for single-authority distributed quota safety
    op.create_table(
        "analytics_quota_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("worker_id", sa.String(length=100), nullable=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("analytics_jobs.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_analytics_quota_res_org_status_expires",
        "analytics_quota_reservations",
        ["organization_id", "status", "expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_analytics_quota_res_org_status_expires", table_name="analytics_quota_reservations"
    )
    op.drop_table("analytics_quota_reservations")
