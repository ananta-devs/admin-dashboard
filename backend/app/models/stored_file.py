"""StoredFile domain model representing tenant-isolated uploaded datasets and user documents."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User
    from app.models.workspace import Workspace


class StoredFile(Base):
    """File metadata entity strictly scoped within an organization."""

    __tablename__ = "stored_files"

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
    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    folder: Mapped[str] = mapped_column(String(255), nullable=False, default="/")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_stored_files_size_bytes_nonnegative"),
        UniqueConstraint(
            "organization_id",
            "folder",
            "filename",
            name="uq_stored_files_org_folder_filename",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            name="fk_stored_files_workspace_composite",
            ondelete="SET NULL",
        ),
        Index("ix_stored_files_created_at", "created_at"),
        Index("ix_stored_files_org_folder", "organization_id", "folder"),
    )

    # Relationships
    organization: Mapped["Organization"] = relationship()
    workspace: Mapped["Workspace | None"] = relationship(overlaps="organization")
    uploaded_by_user: Mapped["User | None"] = relationship()

    def __repr__(self) -> str:
        return f"<StoredFile id={self.id} org={self.organization_id} name={self.filename!r} size={self.size_bytes}>"
