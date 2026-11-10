"""Data access repository for governed user-defined Python scripts and run audit logs."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.script import PythonScript, PythonScriptRun


class PythonScriptRepository:
    """Data access repository for user Python analytical scripts."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, script: PythonScript) -> PythonScript:
        self.session.add(script)
        await self.session.flush()
        return script

    async def get_by_id(
        self, script_id: uuid.UUID, organization_id: uuid.UUID
    ) -> PythonScript | None:
        stmt = select(PythonScript).where(
            PythonScript.id == script_id,
            PythonScript.organization_id == organization_id,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_by_organization(
        self, organization_id: uuid.UUID
    ) -> Sequence[PythonScript]:
        stmt = (
            select(PythonScript)
            .where(PythonScript.organization_id == organization_id)
            .order_by(PythonScript.created_at.desc())
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def update(self, script: PythonScript) -> PythonScript:
        await self.session.flush()
        return script

    async def delete(self, script_id: uuid.UUID, organization_id: uuid.UUID) -> bool:
        stmt = (
            delete(PythonScript)
            .where(
                PythonScript.id == script_id,
                PythonScript.organization_id == organization_id,
            )
            .returning(PythonScript.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.scalar_one_or_none() is not None


class PythonScriptRunRepository:
    """Data access repository for script execution audit logs and output records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, run: PythonScriptRun) -> PythonScriptRun:
        self.session.add(run)
        await self.session.flush()
        return run

    async def get_by_id(
        self, run_id: uuid.UUID, organization_id: uuid.UUID
    ) -> PythonScriptRun | None:
        stmt = select(PythonScriptRun).where(
            PythonScriptRun.id == run_id,
            PythonScriptRun.organization_id == organization_id,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_by_script(
        self, script_id: uuid.UUID, organization_id: uuid.UUID, limit: int = 50
    ) -> Sequence[PythonScriptRun]:
        stmt = (
            select(PythonScriptRun)
            .where(
                PythonScriptRun.script_id == script_id,
                PythonScriptRun.organization_id == organization_id,
            )
            .order_by(PythonScriptRun.created_at.desc())
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def update(self, run: PythonScriptRun) -> PythonScriptRun:
        await self.session.flush()
        return run

