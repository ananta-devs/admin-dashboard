"""harden_analytics_worker_and_snapshots

Revision ID: c93e1a5f7d2b
Revises: b82d9f3e5c7a
Create Date: 2026-11-01 16:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c93e1a5f7d2b"
down_revision: Union[str, None] = "b82d9f3e5c7a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add claim fencing and snapshot metadata columns to analytics_jobs
    op.add_column(
        "analytics_jobs", sa.Column("claim_token", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column(
        "analytics_jobs",
        sa.Column("claim_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "analytics_jobs",
        sa.Column("snapshot_created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "analytics_jobs",
        sa.Column("snapshot_sheet_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "analytics_jobs", sa.Column("snapshot_sheet_version", sa.Integer(), nullable=True)
    )
    op.add_column("analytics_jobs", sa.Column("snapshot_row_count", sa.Integer(), nullable=True))
    op.add_column("analytics_jobs", sa.Column("snapshot_col_count", sa.Integer(), nullable=True))
    op.add_column(
        "analytics_jobs", sa.Column("snapshot_checksum_sha256", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "analytics_jobs", sa.Column("snapshot_size_bytes", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "analytics_jobs", sa.Column("snapshot_storage_id", sa.String(length=500), nullable=True)
    )

    op.create_index(
        "ix_analytics_jobs_claim_token",
        "analytics_jobs",
        ["claim_token"],
        unique=False,
    )

    # 2. Add published sheet and publish idempotency columns to analytics_results
    op.add_column(
        "analytics_results",
        sa.Column("published_sheet_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "analytics_results", sa.Column("published_sheet_name", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "analytics_results",
        sa.Column("publish_idempotency_key", sa.String(length=100), nullable=True),
    )

    op.create_index(
        "ix_analytics_results_published_sheet_id",
        "analytics_results",
        ["published_sheet_id"],
        unique=False,
    )
    op.create_index(
        "ix_analytics_results_org_publish_idempotency",
        "analytics_results",
        ["organization_id", "publish_idempotency_key"],
        unique=True,
        postgresql_where=sa.text("publish_idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_analytics_results_org_publish_idempotency", table_name="analytics_results")
    op.drop_index("ix_analytics_results_published_sheet_id", table_name="analytics_results")
    op.drop_column("analytics_results", "publish_idempotency_key")
    op.drop_column("analytics_results", "published_sheet_name")
    op.drop_column("analytics_results", "published_sheet_id")

    op.drop_index("ix_analytics_jobs_claim_token", table_name="analytics_jobs")
    op.drop_column("analytics_jobs", "snapshot_storage_id")
    op.drop_column("analytics_jobs", "snapshot_size_bytes")
    op.drop_column("analytics_jobs", "snapshot_checksum_sha256")
    op.drop_column("analytics_jobs", "snapshot_col_count")
    op.drop_column("analytics_jobs", "snapshot_row_count")
    op.drop_column("analytics_jobs", "snapshot_sheet_version")
    op.drop_column("analytics_jobs", "snapshot_sheet_id")
    op.drop_column("analytics_jobs", "snapshot_created_at")
    op.drop_column("analytics_jobs", "claim_generation")
    op.drop_column("analytics_jobs", "claim_token")
