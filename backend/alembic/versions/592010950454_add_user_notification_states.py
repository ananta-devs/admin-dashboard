"""add_user_notification_states

Revision ID: 592010950454
Revises: 481909849343
Create Date: 2026-11-10 00:10:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "592010950454"
down_revision: Union[str, None] = "481909849343"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_notification_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id", ondelete="CASCADE", name="fk_user_notification_states_user_id"
            ),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "organizations.id", ondelete="CASCADE", name="fk_user_notification_states_org_id"
            ),
            nullable=False,
        ),
        sa.Column("notification_id", sa.String(length=255), nullable=False),
        sa.Column("is_read", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_dismissed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id",
            "organization_id",
            "notification_id",
            name="uq_user_notif_state_user_org_notif",
        ),
    )

    op.create_index(
        "ix_user_notification_states_user_org",
        "user_notification_states",
        ["user_id", "organization_id"],
    )
    op.create_index(
        "ix_user_notification_states_notification_id",
        "user_notification_states",
        ["notification_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_user_notification_states_notification_id", table_name="user_notification_states"
    )
    op.drop_index("ix_user_notification_states_user_org", table_name="user_notification_states")
    op.drop_table("user_notification_states")
