import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.role import MembershipRole, OrganizationRole
    from app.models.user import User


class OrganizationMembership(Base):
    """Foundational organization-to-user membership link."""

    __tablename__ = "organization_memberships"

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
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_organization_memberships_org_user",
        ),
        UniqueConstraint(
            "id",
            "organization_id",
            name="uq_organization_memberships_id_org",
        ),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship(back_populates="memberships")
    user: Mapped["User"] = relationship(back_populates="memberships")
    membership_roles: Mapped[list["MembershipRole"]] = relationship(
        back_populates="membership",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    roles: Mapped[list["OrganizationRole"]] = relationship(
        secondary="membership_roles",
        viewonly=True,
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<OrganizationMembership org={self.organization_id} user={self.user_id}>"
