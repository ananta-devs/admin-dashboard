import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.membership import OrganizationMembership


class MembershipRepository:
    """Persistence operations for OrganizationMembership links."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, membership: OrganizationMembership) -> OrganizationMembership:
        """Add membership link to session and flush."""
        self.session.add(membership)
        await self.session.flush()
        return membership

    async def get(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> OrganizationMembership | None:
        """Find a specific user's membership within an organization."""
        stmt = select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(
        self, membership_id: uuid.UUID, organization_id: uuid.UUID | None = None
    ) -> OrganizationMembership | None:
        """Find a membership by its ID, optionally scoped to an organization."""
        from app.models.role import OrganizationRole

        stmt = (
            select(OrganizationMembership)
            .options(
                selectinload(OrganizationMembership.user),
                selectinload(OrganizationMembership.roles).selectinload(
                    OrganizationRole.permissions
                ),
            )
            .where(OrganizationMembership.id == membership_id)
        )
        if organization_id is not None:
            stmt = stmt.where(OrganizationMembership.organization_id == organization_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_by_organization(self, organization_id: uuid.UUID) -> int:
        """Count total members of an organization."""
        from sqlalchemy import func

        stmt = select(func.count(OrganizationMembership.id)).where(
            OrganizationMembership.organization_id == organization_id
        )
        res = await self.session.execute(stmt)
        return res.scalar_one() or 0

    async def get_owner_memberships(
        self, organization_id: uuid.UUID, for_update: bool = False
    ) -> list[OrganizationMembership]:
        """Fetch all memberships that possess the Owner role, optionally with row lock."""
        from app.models.role import MembershipRole, OrganizationRole

        stmt = (
            select(OrganizationMembership)
            .join(MembershipRole, MembershipRole.membership_id == OrganizationMembership.id)
            .join(OrganizationRole, OrganizationRole.id == MembershipRole.role_id)
            .where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationRole.slug == "owner",
                OrganizationRole.is_system.is_(True),
            )
        )
        if for_update:
            stmt = stmt.with_for_update()
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def list_by_organization(
        self, organization_id: uuid.UUID, limit: int = 50, offset: int = 0
    ) -> list[OrganizationMembership]:
        """List all members of an organization with joined user profiles and roles."""
        from app.models.role import OrganizationRole

        stmt = (
            select(OrganizationMembership)
            .options(
                selectinload(OrganizationMembership.user),
                selectinload(OrganizationMembership.roles).selectinload(
                    OrganizationRole.permissions
                ),
            )
            .where(OrganizationMembership.organization_id == organization_id)
            .order_by(OrganizationMembership.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_user(
        self, user_id: uuid.UUID, limit: int = 50, offset: int = 0
    ) -> list[OrganizationMembership]:
        """List all organization memberships for a specific user."""
        stmt = (
            select(OrganizationMembership)
            .options(selectinload(OrganizationMembership.organization))
            .where(OrganizationMembership.user_id == user_id)
            .order_by(OrganizationMembership.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def delete(self, membership: OrganizationMembership) -> None:
        """Remove a membership link."""
        await self.session.delete(membership)
        await self.session.flush()
