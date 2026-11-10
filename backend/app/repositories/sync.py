"""Data access repository for spreadsheet synchronization mappings, runs, and conflicts."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sync import (
    SpreadsheetSyncConflict,
    SpreadsheetSyncMapping,
    SpreadsheetSyncRun,
)


class SpreadsheetSyncRepository:
    """Repository handling persistence and queries for spreadsheet synchronization."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # =========================================================================
    # Mappings
    # =========================================================================

    async def create_mapping(self, mapping: SpreadsheetSyncMapping) -> SpreadsheetSyncMapping:
        self.session.add(mapping)
        await self.session.flush()
        return mapping

    async def get_mapping_by_id(
        self, mapping_id: uuid.UUID, organization_id: uuid.UUID
    ) -> SpreadsheetSyncMapping | None:
        stmt = select(SpreadsheetSyncMapping).where(
            SpreadsheetSyncMapping.id == mapping_id,
            SpreadsheetSyncMapping.organization_id == organization_id,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_mapping_by_id_system(
        self, mapping_id: uuid.UUID
    ) -> SpreadsheetSyncMapping | None:
        stmt = select(SpreadsheetSyncMapping).where(SpreadsheetSyncMapping.id == mapping_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_mapping_by_sheet(
        self, sheet_id: uuid.UUID, organization_id: uuid.UUID
    ) -> SpreadsheetSyncMapping | None:
        stmt = select(SpreadsheetSyncMapping).where(
            SpreadsheetSyncMapping.sheet_id == sheet_id,
            SpreadsheetSyncMapping.organization_id == organization_id,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_mappings_by_org(
        self, organization_id: uuid.UUID
    ) -> Sequence[SpreadsheetSyncMapping]:
        stmt = (
            select(SpreadsheetSyncMapping)
            .where(SpreadsheetSyncMapping.organization_id == organization_id)
            .order_by(SpreadsheetSyncMapping.created_at.desc())
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def update_mapping(self, mapping: SpreadsheetSyncMapping) -> SpreadsheetSyncMapping:
        await self.session.flush()
        return mapping

    async def delete_mapping(
        self, mapping_id: uuid.UUID, organization_id: uuid.UUID
    ) -> bool:
        stmt = (
            delete(SpreadsheetSyncMapping)
            .where(
                SpreadsheetSyncMapping.id == mapping_id,
                SpreadsheetSyncMapping.organization_id == organization_id,
            )
            .returning(SpreadsheetSyncMapping.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.scalar_one_or_none() is not None

    # =========================================================================
    # Runs
    # =========================================================================

    async def create_run(self, run: SpreadsheetSyncRun) -> SpreadsheetSyncRun:
        self.session.add(run)
        await self.session.flush()
        return run

    async def get_run_by_id(
        self, run_id: uuid.UUID, organization_id: uuid.UUID
    ) -> SpreadsheetSyncRun | None:
        stmt = select(SpreadsheetSyncRun).where(
            SpreadsheetSyncRun.id == run_id,
            SpreadsheetSyncRun.organization_id == organization_id,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_runs_by_mapping(
        self, mapping_id: uuid.UUID, organization_id: uuid.UUID, limit: int = 50
    ) -> Sequence[SpreadsheetSyncRun]:
        stmt = (
            select(SpreadsheetSyncRun)
            .where(
                SpreadsheetSyncRun.mapping_id == mapping_id,
                SpreadsheetSyncRun.organization_id == organization_id,
            )
            .order_by(SpreadsheetSyncRun.created_at.desc())
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def update_run(self, run: SpreadsheetSyncRun) -> SpreadsheetSyncRun:
        await self.session.flush()
        return run

    # =========================================================================
    # Conflicts
    # =========================================================================

    async def create_conflict(
        self, conflict: SpreadsheetSyncConflict
    ) -> SpreadsheetSyncConflict:
        self.session.add(conflict)
        await self.session.flush()
        return conflict

    async def get_conflict_by_id(
        self, conflict_id: uuid.UUID, organization_id: uuid.UUID
    ) -> SpreadsheetSyncConflict | None:
        stmt = select(SpreadsheetSyncConflict).where(
            SpreadsheetSyncConflict.id == conflict_id,
            SpreadsheetSyncConflict.organization_id == organization_id,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_conflicts_by_mapping(
        self,
        mapping_id: uuid.UUID,
        organization_id: uuid.UUID,
        status: str | None = None,
    ) -> Sequence[SpreadsheetSyncConflict]:
        stmt = select(SpreadsheetSyncConflict).where(
            SpreadsheetSyncConflict.mapping_id == mapping_id,
            SpreadsheetSyncConflict.organization_id == organization_id,
        )
        if status:
            stmt = stmt.where(SpreadsheetSyncConflict.status == status)
        stmt = stmt.order_by(
            SpreadsheetSyncConflict.row_index.asc(),
            SpreadsheetSyncConflict.column_letter.asc(),
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def list_conflicts_by_run(
        self, run_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Sequence[SpreadsheetSyncConflict]:
        stmt = (
            select(SpreadsheetSyncConflict)
            .where(
                SpreadsheetSyncConflict.run_id == run_id,
                SpreadsheetSyncConflict.organization_id == organization_id,
            )
            .order_by(
                SpreadsheetSyncConflict.row_index.asc(),
                SpreadsheetSyncConflict.column_letter.asc(),
            )
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def count_unresolved_conflicts(self, mapping_id: uuid.UUID) -> int:
        stmt = select(func.count(SpreadsheetSyncConflict.id)).where(
            SpreadsheetSyncConflict.mapping_id == mapping_id,
            SpreadsheetSyncConflict.status == "detected",
        )
        res = await self.session.execute(stmt)
        return res.scalar_one() or 0

