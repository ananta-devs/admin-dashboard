"""audit_retention_policy_set_null

Revision ID: e48a1c9b2d3e
Revises: 7a8e219cb8d4
Create Date: 2026-11-01 13:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e48a1c9b2d3e"
down_revision: str | Sequence[str] | None = "7a8e219cb8d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Drop existing CASCADE foreign key on administrative_audit_events.organization_id
    op.drop_constraint(
        "fk_administrative_audit_events_organization_id_organizations",
        "administrative_audit_events",
        type_="foreignkey",
    )

    # 2. Make organization_id nullable so audit events survive organization deletion
    op.alter_column(
        "administrative_audit_events",
        "organization_id",
        nullable=True,
    )

    # 3. Add organization_slug column for immutable tenant attribution
    op.add_column(
        "administrative_audit_events",
        sa.Column("organization_slug", sa.String(length=100), nullable=True),
    )

    # Backfill organization_slug from organizations for existing rows
    op.execute(
        "UPDATE administrative_audit_events a SET organization_slug = o.slug "
        "FROM organizations o WHERE a.organization_id = o.id"
    )

    # Index organization_slug
    op.create_index(
        op.f("ix_administrative_audit_events_organization_slug"),
        "administrative_audit_events",
        ["organization_slug"],
        unique=False,
    )

    # 4. Recreate foreign key with ondelete="SET NULL"
    op.create_foreign_key(
        op.f("fk_administrative_audit_events_organization_id_organizations"),
        "administrative_audit_events",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # 1. Drop SET NULL foreign key
    op.drop_constraint(
        op.f("fk_administrative_audit_events_organization_id_organizations"),
        "administrative_audit_events",
        type_="foreignkey",
    )

    # 2. Delete audit events that have NULL organization_id before re-enforcing NOT NULL
    op.execute("DELETE FROM administrative_audit_events WHERE organization_id IS NULL")

    # 3. Drop index and column organization_slug
    op.drop_index(
        op.f("ix_administrative_audit_events_organization_slug"),
        table_name="administrative_audit_events",
    )
    op.drop_column("administrative_audit_events", "organization_slug")

    # 4. Make organization_id NOT NULL
    op.alter_column(
        "administrative_audit_events",
        "organization_id",
        nullable=False,
    )

    # 5. Recreate CASCADE foreign key
    op.create_foreign_key(
        op.f("fk_administrative_audit_events_organization_id_organizations"),
        "administrative_audit_events",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )

