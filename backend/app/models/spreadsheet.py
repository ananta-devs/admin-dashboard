from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, utc_now

if TYPE_CHECKING:
    from app.models.organization import Organization
    from app.models.user import User
    from app.models.workspace import Workspace


class Workbook(Base):
    """Spreadsheet workbook containing one or more sheets, scoped to organization and workspace."""

    __tablename__ = "workbooks"

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
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_workbooks_id_org"),
        ForeignKeyConstraint(
            ["workspace_id", "organization_id"],
            ["workspaces.id", "workspaces.organization_id"],
            ondelete="CASCADE",
            name="fk_workbooks_workspace_org",
        ),
    )

    # Relationships
    organization: Mapped[Organization] = relationship()
    workspace: Mapped[Workspace] = relationship(overlaps="organization")
    created_by: Mapped[User | None] = relationship()
    sheets: Mapped[list[Sheet]] = relationship(
        back_populates="workbook",
        cascade="all, delete-orphan",
        order_by="Sheet.position",
    )


class Sheet(Base):
    """Individual spreadsheet grid within a workbook."""

    __tablename__ = "sheets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    workbook_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=1000, nullable=False)
    column_count: Mapped[int] = mapped_column(Integer, default=26, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("id", "organization_id", name="uq_sheets_id_org"),
        UniqueConstraint("workbook_id", "name", name="uq_sheets_workbook_name"),
        ForeignKeyConstraint(
            ["workbook_id", "organization_id"],
            ["workbooks.id", "workbooks.organization_id"],
            ondelete="CASCADE",
            name="fk_sheets_workbook_org",
        ),
    )

    # Relationships
    workbook: Mapped[Workbook] = relationship(back_populates="sheets")
    columns: Mapped[list[SheetColumn]] = relationship(
        back_populates="sheet",
        cascade="all, delete-orphan",
        order_by="SheetColumn.column_index",
    )
    rows: Mapped[list[SheetRow]] = relationship(
        back_populates="sheet",
        cascade="all, delete-orphan",
        order_by="SheetRow.row_index",
    )


class SheetColumn(Base):
    """Metadata definition for a spreadsheet column."""

    __tablename__ = "sheet_columns"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    sheet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sheets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    column_index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    data_type: Mapped[str] = mapped_column(String(20), default="text", nullable=False)
    width: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    format: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("sheet_id", "column_index", name="uq_sheet_columns_sheet_col_idx"),
    )

    sheet: Mapped[Sheet] = relationship(back_populates="columns")


class SheetRow(Base):
    """Sparse row record containing a JSONB dictionary of populated cells."""

    __tablename__ = "sheet_rows"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    sheet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    cells: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("sheet_id", "row_index", name="uq_sheet_rows_sheet_row_idx"),
        ForeignKeyConstraint(
            ["sheet_id", "organization_id"],
            ["sheets.id", "sheets.organization_id"],
            ondelete="CASCADE",
            name="fk_sheet_rows_sheet_org",
        ),
    )

    sheet: Mapped[Sheet] = relationship(back_populates="rows")


class SheetCellDependency(Base):
    """Normalized formula dependency mapping precedent cells to dependent cells."""

    __tablename__ = "sheet_cell_dependencies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    sheet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    precedent_cell_ref: Mapped[str] = mapped_column(String(20), nullable=False)
    dependent_cell_ref: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "sheet_id", "precedent_cell_ref", "dependent_cell_ref", name="uq_sheet_deps_prec_dep"
        ),
        ForeignKeyConstraint(
            ["sheet_id", "organization_id"],
            ["sheets.id", "sheets.organization_id"],
            ondelete="CASCADE",
            name="fk_sheet_deps_sheet_org",
        ),
    )


class SheetCellHistory(Base):
    """Bounded revision history for undo/redo and audit tracking."""

    __tablename__ = "sheet_cell_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    sheet_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    cell_ref: Mapped[str] = mapped_column(String(20), nullable=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        default=uuid.uuid4,
        nullable=False,
        index=True,
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    column_index: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_computed_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    previous_formula: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_raw_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_computed_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_formula: Mapped[str | None] = mapped_column(Text, nullable=True)
    cell_version_before: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    cell_version_after: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    row_version_before: Mapped[int] = mapped_column(Integer, nullable=False)
    row_version_after: Mapped[int] = mapped_column(Integer, nullable=False)
    edited_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    is_undone: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["sheet_id", "organization_id"],
            ["sheets.id", "sheets.organization_id"],
            ondelete="CASCADE",
            name="fk_sheet_history_sheet_org",
        ),
    )
