import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.organization import Organization


class OrganizationRepository:
    """Persistence operations for Organization entities."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, organization: Organization) -> Organization:
        """Add organization to the session and flush to generate ID/timestamps."""
        self.session.add(organization)
        await self.session.flush()
        return organization

    async def get_by_id(self, org_id: uuid.UUID) -> Organization | None:
        """Retrieve an organization by its primary key UUID."""
        stmt = select(Organization).where(Organization.id == org_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Organization | None:
        """Retrieve an organization by its unique lowercase slug."""
        stmt = select(Organization).where(Organization.slug == slug)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_all(self, limit: int = 50, offset: int = 0) -> list[Organization]:
        """List organizations with explicit limit and offset pagination."""
        stmt = (
            select(Organization)
            .order_by(Organization.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def delete(self, organization: Organization) -> None:
        """Mark an organization for deletion (cascades to memberships and workspaces)."""
        await self.session.delete(organization)
        await self.session.flush()
