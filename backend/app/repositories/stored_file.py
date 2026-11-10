"""Persistence operations for StoredFile entity strictly bound to organization tenant."""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stored_file import StoredFile


class StoredFileRepository:
    """Repository handling SQL persistence for stored file metadata."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, file_entity: StoredFile) -> StoredFile:
        """Add stored file metadata and flush."""
        self.session.add(file_entity)
        await self.session.flush()
        return file_entity

    async def get_by_id(
        self, file_id: uuid.UUID, organization_id: uuid.UUID
    ) -> StoredFile | None:
        """Fetch file metadata by primary key and organization scope."""
        stmt = select(StoredFile).where(
            StoredFile.id == file_id,
            StoredFile.organization_id == organization_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_filename(
        self, filename: str, folder: str, organization_id: uuid.UUID
    ) -> StoredFile | None:
        """Fetch file metadata by filename and folder within organization."""
        stmt = select(StoredFile).where(
            StoredFile.filename == filename,
            StoredFile.folder == folder,
            StoredFile.organization_id == organization_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_organization(
        self,
        organization_id: uuid.UUID,
        folder: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[StoredFile]:
        """List files belonging strictly to organization with optional folder and search filtering."""
        stmt = select(StoredFile).where(StoredFile.organization_id == organization_id)
        if folder:
            stmt = stmt.where(StoredFile.folder == folder)
        if search and search.strip():
            term = f"%{search.strip().lower()}%"
            stmt = stmt.where(func.lower(StoredFile.filename).like(term))
        stmt = stmt.order_by(StoredFile.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_organization_usage(self, organization_id: uuid.UUID) -> tuple[int, int]:
        """Return total bytes used and total files stored by the organization."""
        stmt = select(
            func.coalesce(func.sum(StoredFile.size_bytes), 0),
            func.count(StoredFile.id),
        ).where(StoredFile.organization_id == organization_id)
        result = await self.session.execute(stmt)
        row = result.one()
        return int(row[0]), int(row[1])

    async def delete(self, file_entity: StoredFile) -> None:
        """Delete file entity from database."""
        await self.session.delete(file_entity)
        await self.session.flush()

