import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.role import OrganizationRole
    from app.models.user import User


class OrganizationInvitation(Base):
    """Pending, accepted, or revoked invitation to join an organization with a specific role."""

    __tablename__ = "organization_invitations"

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
    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    normalized_email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    invited_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        index=True,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["role_id", "organization_id"],
            ["organization_roles.id", "organization_roles.organization_id"],
            name="fk_organization_invitations_role_org",
            ondelete="CASCADE",
        ),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship()
    role: Mapped["OrganizationRole"] = relationship(overlaps="organization")
    invited_by: Mapped["User"] = relationship()

    @property
    def is_valid(self) -> bool:
        """Check whether the invitation is pending, unexpired, and unrevoked."""
        if self.accepted_at is not None or self.revoked_at is not None:
            return False
        now = datetime.now(self.expires_at.tzinfo)
        return now < self.expires_at

    def __repr__(self) -> str:
        return (
            f"<OrganizationInvitation id={self.id} org={self.organization_id} email={self.email}>"
        )
