from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.spreadsheet import (
    Sheet,
    SheetCellDependency,
    SheetCellHistory,
    SheetRow,
    Workbook,
)


class WorkbookRepository:
    """Data access repository for workbooks strictly scoped to an organization."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, workbook: Workbook) -> Workbook:
        self.session.add(workbook)
        await self.session.flush()
        return workbook

    async def get_by_id(
        self, workbook_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Workbook | None:
        stmt = (
            select(Workbook)
            .where(
                Workbook.id == workbook_id,
                Workbook.organization_id == organization_id,
            )
            .options(selectinload(Workbook.sheets))
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_by_workspace(
        self, workspace_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Sequence[Workbook]:
        stmt = (
            select(Workbook)
            .where(
                Workbook.workspace_id == workspace_id,
                Workbook.organization_id == organization_id,
            )
            .options(selectinload(Workbook.sheets))
            .order_by(Workbook.created_at.desc())
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def list_by_organization(self, organization_id: uuid.UUID) -> Sequence[Workbook]:
        stmt = (
            select(Workbook)
            .where(Workbook.organization_id == organization_id)
            .options(selectinload(Workbook.sheets))
            .order_by(Workbook.created_at.desc())
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def delete(self, workbook_id: uuid.UUID, organization_id: uuid.UUID) -> bool:
        stmt = (
            delete(Workbook)
            .where(
                Workbook.id == workbook_id,
                Workbook.organization_id == organization_id,
            )
            .returning(Workbook.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.scalar_one_or_none() is not None


class SheetRepository:
    """Data access repository for sheets within workbooks."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, sheet: Sheet) -> Sheet:
        self.session.add(sheet)
        await self.session.flush()
        return sheet

    async def get_by_id(self, sheet_id: uuid.UUID, organization_id: uuid.UUID) -> Sheet | None:
        stmt = (
            select(Sheet)
            .where(
                Sheet.id == sheet_id,
                Sheet.organization_id == organization_id,
            )
            .options(selectinload(Sheet.columns))
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_id_for_update(
        self, sheet_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Sheet | None:
        stmt = (
            select(Sheet)
            .where(
                Sheet.id == sheet_id,
                Sheet.organization_id == organization_id,
            )
            .with_for_update()
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_by_workbook(
        self, workbook_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Sequence[Sheet]:
        stmt = (
            select(Sheet)
            .where(
                Sheet.workbook_id == workbook_id,
                Sheet.organization_id == organization_id,
            )
            .order_by(Sheet.position.asc(), Sheet.created_at.asc())
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def delete(self, sheet_id: uuid.UUID, organization_id: uuid.UUID) -> bool:
        stmt = (
            delete(Sheet)
            .where(
                Sheet.id == sheet_id,
                Sheet.organization_id == organization_id,
            )
            .returning(Sheet.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.scalar_one_or_none() is not None


class SheetRowRepository:
    """Data access repository for row-oriented JSONB grid storage."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_row(self, sheet_id: uuid.UUID, row_index: int) -> SheetRow | None:
        stmt = select(SheetRow).where(
            SheetRow.sheet_id == sheet_id,
            SheetRow.row_index == row_index,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_rows(self, sheet_id: uuid.UUID, row_indices: list[int]) -> Sequence[SheetRow]:
        if not row_indices:
            return []
        stmt = (
            select(SheetRow)
            .where(
                SheetRow.sheet_id == sheet_id,
                SheetRow.row_index.in_(row_indices),
            )
            .order_by(SheetRow.row_index.asc())
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def get_rows_in_range(
        self, sheet_id: uuid.UUID, start_row: int, end_row: int
    ) -> Sequence[SheetRow]:
        stmt = (
            select(SheetRow)
            .where(
                SheetRow.sheet_id == sheet_id,
                SheetRow.row_index >= start_row,
                SheetRow.row_index <= end_row,
            )
            .order_by(SheetRow.row_index.asc())
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def list_rows_paginated(
        self, sheet_id: uuid.UUID, limit: int, offset: int
    ) -> Sequence[SheetRow]:
        stmt = (
            select(SheetRow)
            .where(SheetRow.sheet_id == sheet_id)
            .order_by(SheetRow.row_index.asc())
            .offset(offset)
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def count_populated_rows(self, sheet_id: uuid.UUID) -> int:
        stmt = select(func.count(SheetRow.id)).where(SheetRow.sheet_id == sheet_id)
        res = await self.session.execute(stmt)
        return res.scalar_one() or 0

    async def count_non_empty_cells(self, sheet_id: uuid.UUID) -> int:
        """Count all non-empty cells across all rows in a sheet."""
        stmt = text("""
            SELECT count(*)
            FROM sheet_rows sr, jsonb_each(sr.cells) c
            WHERE sr.sheet_id = :sheet_id
              AND ((c.value->>'v') IS NOT NULL OR (c.value->>'c') IS NOT NULL)
        """)
        res = await self.session.execute(stmt, {"sheet_id": sheet_id})
        return res.scalar_one() or 0

    async def upsert_row(
        self,
        sheet_id: uuid.UUID,
        organization_id: uuid.UUID,
        row_index: int,
        cells: dict[str, Any],
        expected_version: int | None = None,
    ) -> SheetRow:
        """Upsert a row with optimistic concurrency control checking expected_version."""
        existing = await self.get_row(sheet_id, row_index)
        if existing:
            if expected_version is not None and existing.version != expected_version:
                from app.core.exceptions import ConflictError

                raise ConflictError(
                    f"Row {row_index + 1} was modified by another concurrent edit. Expected version {expected_version}, but found {existing.version}."
                )
            existing.cells = cells
            existing.version += 1
            await self.session.flush()
            return existing

        new_row = SheetRow(
            sheet_id=sheet_id,
            organization_id=organization_id,
            row_index=row_index,
            cells=cells,
            version=1,
        )
        self.session.add(new_row)
        await self.session.flush()
        return new_row


class DependencyRepository:
    """Manages the normalized formula dependency graph in PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_all_for_sheet(self, sheet_id: uuid.UUID) -> Sequence[SheetCellDependency]:
        stmt = select(SheetCellDependency).where(SheetCellDependency.sheet_id == sheet_id)
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def get_dependents_for_precedents(
        self, sheet_id: uuid.UUID, precedents: list[str]
    ) -> Sequence[SheetCellDependency]:
        if not precedents:
            return []
        stmt = select(SheetCellDependency).where(
            SheetCellDependency.sheet_id == sheet_id,
            SheetCellDependency.precedent_cell_ref.in_([p.upper() for p in precedents]),
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def sync_cell_dependencies(
        self,
        sheet_id: uuid.UUID,
        organization_id: uuid.UUID,
        dependent_cell_ref: str,
        precedent_refs: set[str],
    ) -> None:
        dep_ref = dependent_cell_ref.upper()
        # 1. Clear existing dependencies for this dependent cell
        await self.session.execute(
            delete(SheetCellDependency).where(
                SheetCellDependency.sheet_id == sheet_id,
                SheetCellDependency.dependent_cell_ref == dep_ref,
            )
        )

        # 2. Add new dependencies
        for prec in precedent_refs:
            self.session.add(
                SheetCellDependency(
                    sheet_id=sheet_id,
                    organization_id=organization_id,
                    precedent_cell_ref=prec.upper(),
                    dependent_cell_ref=dep_ref,
                )
            )
        await self.session.flush()


class HistoryRepository:
    """Manages bounded cell revision logs for undo/redo."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add_entry(self, entry: SheetCellHistory) -> SheetCellHistory:
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def list_history(
        self, sheet_id: uuid.UUID, limit: int = 50, offset: int = 0
    ) -> Sequence[SheetCellHistory]:
        stmt = (
            select(SheetCellHistory)
            .where(SheetCellHistory.sheet_id == sheet_id)
            .order_by(SheetCellHistory.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def get_latest_undoable_batch(self, sheet_id: uuid.UUID) -> Sequence[SheetCellHistory]:
        stmt = (
            select(SheetCellHistory.batch_id)
            .where(
                SheetCellHistory.sheet_id == sheet_id,
                SheetCellHistory.is_undone.is_(False),
            )
            .order_by(SheetCellHistory.created_at.desc())
            .limit(1)
        )
        res = await self.session.execute(stmt)
        batch_id = res.scalar_one_or_none()
        if not batch_id:
            return []
        stmt_entries = (
            select(SheetCellHistory)
            .where(
                SheetCellHistory.sheet_id == sheet_id,
                SheetCellHistory.batch_id == batch_id,
                SheetCellHistory.is_undone.is_(False),
            )
            .order_by(SheetCellHistory.created_at.asc())
        )
        res_entries = await self.session.execute(stmt_entries)
        return res_entries.scalars().all()

    async def get_latest_redoable_batch(self, sheet_id: uuid.UUID) -> Sequence[SheetCellHistory]:
        stmt = (
            select(SheetCellHistory.batch_id)
            .where(
                SheetCellHistory.sheet_id == sheet_id,
                SheetCellHistory.is_undone.is_(True),
            )
            .order_by(SheetCellHistory.created_at.desc())
            .limit(1)
        )
        res = await self.session.execute(stmt)
        batch_id = res.scalar_one_or_none()
        if not batch_id:
            return []
        stmt_entries = (
            select(SheetCellHistory)
            .where(
                SheetCellHistory.sheet_id == sheet_id,
                SheetCellHistory.batch_id == batch_id,
                SheetCellHistory.is_undone.is_(True),
            )
            .order_by(SheetCellHistory.created_at.asc())
        )
        res_entries = await self.session.execute(stmt_entries)
        return res_entries.scalars().all()

    async def has_newer_active_edit(
        self, sheet_id: uuid.UUID, cell_ref: str, after_timestamp: datetime
    ) -> bool:
        stmt = select(func.count(SheetCellHistory.id)).where(
            SheetCellHistory.sheet_id == sheet_id,
            SheetCellHistory.cell_ref == cell_ref,
            SheetCellHistory.created_at > after_timestamp,
            SheetCellHistory.is_undone.is_(False),
        )
        res = await self.session.execute(stmt)
        return (res.scalar_one() or 0) > 0

    async def purge_redo_history(self, sheet_id: uuid.UUID) -> None:
        await self.session.execute(
            delete(SheetCellHistory).where(
                SheetCellHistory.sheet_id == sheet_id,
                SheetCellHistory.is_undone.is_(True),
            )
        )

    async def prune_history(self, sheet_id: uuid.UUID, max_batches: int = 100) -> None:
        stmt = (
            select(SheetCellHistory.batch_id)
            .where(SheetCellHistory.sheet_id == sheet_id)
            .group_by(SheetCellHistory.batch_id)
            .order_by(func.max(SheetCellHistory.created_at).desc())
            .offset(max_batches)
        )
        res = await self.session.execute(stmt)
        old_batch_ids = res.scalars().all()
        if old_batch_ids:
            await self.session.execute(
                delete(SheetCellHistory).where(
                    SheetCellHistory.sheet_id == sheet_id,
                    SheetCellHistory.batch_id.in_(old_batch_ids),
                )
            )

    async def get_latest_undoable(self, sheet_id: uuid.UUID) -> SheetCellHistory | None:
        stmt = (
            select(SheetCellHistory)
            .where(
                SheetCellHistory.sheet_id == sheet_id,
                SheetCellHistory.is_undone.is_(False),
            )
            .order_by(SheetCellHistory.created_at.desc())
            .limit(1)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_latest_redoable(self, sheet_id: uuid.UUID) -> SheetCellHistory | None:
        stmt = (
            select(SheetCellHistory)
            .where(
                SheetCellHistory.sheet_id == sheet_id,
                SheetCellHistory.is_undone.is_(True),
            )
            .order_by(SheetCellHistory.created_at.asc())
            .limit(1)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()
