import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit import AdministrativeAuditEvent


class AuditRepository:
    """Repository handling append-only writes and tenant-scoped queries for administrative audit events."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, event: AdministrativeAuditEvent) -> AdministrativeAuditEvent:
        """Append an audit event record."""
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_by_organization(
        self, organization_id: uuid.UUID, limit: int = 50, offset: int = 0
    ) -> Sequence[AdministrativeAuditEvent]:
        """Fetch audit events strictly scoped to the given organization ordered chronologically descending."""
        stmt = (
            select(AdministrativeAuditEvent)
            .where(AdministrativeAuditEvent.organization_id == organization_id)
            .options(selectinload(AdministrativeAuditEvent.actor))
            .order_by(AdministrativeAuditEvent.occurred_at.desc())
            .limit(limit)
            .offset(offset)
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def count_by_organization(self, organization_id: uuid.UUID) -> int:
        """Count total audit events for an organization."""
        stmt = select(func.count(AdministrativeAuditEvent.id)).where(
            AdministrativeAuditEvent.organization_id == organization_id
        )
        res = await self.session.execute(stmt)
        return res.scalar_one() or 0
