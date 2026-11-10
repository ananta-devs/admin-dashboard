import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.membership import OrganizationMembership
    from app.models.organization import Organization
    from app.models.permission import Permission
    from app.models.user import User


class OrganizationRole(Base):
    """Organization-scoped role defining a bundle of permissions."""

    __tablename__ = "organization_roles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "slug",
            name="uq_organization_roles_org_slug",
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_organization_roles_id_org",
        ),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship()
    permissions: Mapped[list["Permission"]] = relationship(
        secondary="role_permissions",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<OrganizationRole org={self.organization_id} slug={self.slug}>"


class RolePermission(Base):
    """Association table linking organization roles to permissions."""

    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organization_roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("permissions.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<RolePermission role={self.role_id} permission={self.permission_id}>"


class MembershipRole(Base):
    """Association table linking organization memberships to assigned roles."""

    __tablename__ = "membership_roles"

    membership_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        index=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    assigned_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["membership_id", "organization_id"],
            ["organization_memberships.id", "organization_memberships.organization_id"],
            name="fk_membership_roles_membership_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["role_id", "organization_id"],
            ["organization_roles.id", "organization_roles.organization_id"],
            name="fk_membership_roles_role_org",
            ondelete="CASCADE",
        ),
    )

    # Relationships
    membership: Mapped["OrganizationMembership"] = relationship(
        back_populates="membership_roles",
        overlaps="role",
    )
    role: Mapped["OrganizationRole"] = relationship(
        lazy="selectin",
        overlaps="membership,membership_roles",
    )
    assigned_by: Mapped["User | None"] = relationship()

    def __repr__(self) -> str:
        return f"<MembershipRole membership={self.membership_id} role={self.role_id}>"
