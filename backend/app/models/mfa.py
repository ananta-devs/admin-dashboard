import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.user import User


class MfaCredential(Base):
    """TOTP multi-factor authentication credentials with AES-256-GCM encrypted secret."""

    __tablename__ = "mfa_credentials"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    encrypted_secret: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    encryption_key_version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    enabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_accepted_step: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

    # Relationships
    user: Mapped["User"] = relationship(back_populates="mfa_credential")

    @property
    def is_enabled(self) -> bool:
        return self.enabled_at is not None

    def __repr__(self) -> str:
        return f"<MfaCredential user_id={self.user_id} enabled={self.is_enabled}>"


class MfaRecoveryCode(Base):
    """Single-use backup recovery codes for bypassing TOTP in emergency."""

    __tablename__ = "mfa_recovery_codes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="mfa_recovery_codes")

    @property
    def is_consumed(self) -> bool:
        return self.consumed_at is not None

    def __repr__(self) -> str:
        return f"<MfaRecoveryCode id={self.id} consumed={self.is_consumed}>"


class MfaLoginChallenge(Base):
    """Short-lived challenge session issued when primary credentials are valid but MFA is required."""

    __tablename__ = "mfa_login_challenges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    challenge_token_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    attempts_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    # Relationships
    user: Mapped["User"] = relationship(back_populates="mfa_login_challenges")

    @property
    def is_valid(self) -> bool:
        if self.consumed_at is not None or self.attempts_count >= 5:
            return False
        now = datetime.now(self.expires_at.tzinfo)
        return now < self.expires_at

    def __repr__(self) -> str:
        return f"<MfaLoginChallenge id={self.id} user_id={self.user_id}>"
