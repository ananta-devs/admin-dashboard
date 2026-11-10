"""enforce_tenant_integrity_composite_fks

Revision ID: 7a8e219cb8d4
Revises: 03fa52a1f499
Create Date: 2026-11-01 13:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7a8e219cb8d4"
down_revision: str | Sequence[str] | None = "03fa52a1f499"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Unique constraints for composite foreign key targets
    op.create_unique_constraint(
        "uq_organization_memberships_id_org",
        "organization_memberships",
        ["id", "organization_id"],
    )
    op.create_unique_constraint(
        "uq_organization_roles_id_org",
        "organization_roles",
        ["id", "organization_id"],
    )

    # 2. Add organization_id to membership_roles
    op.add_column("membership_roles", sa.Column("organization_id", sa.UUID(), nullable=True))

    # Populate existing organization_id values from organization_memberships
    op.execute(
        "UPDATE membership_roles mr SET organization_id = m.organization_id "
        "FROM organization_memberships m WHERE mr.membership_id = m.id"
    )

    # Enforce NOT NULL and create index
    op.alter_column("membership_roles", "organization_id", nullable=False)
    op.create_index(
        op.f("ix_membership_roles_organization_id"),
        "membership_roles",
        ["organization_id"],
        unique=False,
    )

    # 3. Replace simple FKs on membership_roles with composite FKs enforcing tenant match
    op.drop_constraint(
        "fk_membership_roles_membership_id_organization_memberships",
        "membership_roles",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_membership_roles_role_id_organization_roles",
        "membership_roles",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_membership_roles_membership_org",
        "membership_roles",
        "organization_memberships",
        ["membership_id", "organization_id"],
        ["id", "organization_id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_membership_roles_role_org",
        "membership_roles",
        "organization_roles",
        ["role_id", "organization_id"],
        ["id", "organization_id"],
        ondelete="CASCADE",
    )

    # 4. Enforce tenant match on organization_invitations role reference
    op.drop_constraint(
        "fk_organization_invitations_role_id_organization_roles",
        "organization_invitations",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_organization_invitations_role_org",
        "organization_invitations",
        "organization_roles",
        ["role_id", "organization_id"],
        ["id", "organization_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # 4. Revert organization_invitations FK
    op.drop_constraint(
        "fk_organization_invitations_role_org",
        "organization_invitations",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_organization_invitations_role_id_organization_roles",
        "organization_invitations",
        "organization_roles",
        ["role_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 3. Revert membership_roles FKs
    op.drop_constraint(
        "fk_membership_roles_role_org",
        "membership_roles",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_membership_roles_membership_org",
        "membership_roles",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_membership_roles_role_id_organization_roles",
        "membership_roles",
        "organization_roles",
        ["role_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_membership_roles_membership_id_organization_memberships",
        "membership_roles",
        "organization_memberships",
        ["membership_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # 2. Drop organization_id from membership_roles
    op.drop_index(op.f("ix_membership_roles_organization_id"), table_name="membership_roles")
    op.drop_column("membership_roles", "organization_id")

    # 1. Drop unique constraints
    op.drop_constraint("uq_organization_roles_id_org", "organization_roles", type_="unique")
    op.drop_constraint(
        "uq_organization_memberships_id_org", "organization_memberships", type_="unique"
    )
