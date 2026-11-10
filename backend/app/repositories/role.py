import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.role import MembershipRole, OrganizationRole, RolePermission


class RoleRepository:
    """Repository handling CRUD and assignments for organization roles and permissions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(
        self, role_id: uuid.UUID, organization_id: uuid.UUID
    ) -> OrganizationRole | None:
        """Fetch role by ID scoped to specific organization with loaded permissions."""
        stmt = (
            select(OrganizationRole)
            .where(
                OrganizationRole.id == role_id,
                OrganizationRole.organization_id == organization_id,
            )
            .options(selectinload(OrganizationRole.permissions))
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_slug(self, slug: str, organization_id: uuid.UUID) -> OrganizationRole | None:
        """Fetch role by slug scoped to specific organization."""
        stmt = (
            select(OrganizationRole)
            .where(
                OrganizationRole.slug == slug,
                OrganizationRole.organization_id == organization_id,
            )
            .options(selectinload(OrganizationRole.permissions))
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_by_organization(self, organization_id: uuid.UUID) -> Sequence[OrganizationRole]:
        """List all roles for an organization ordered by is_system desc, name asc."""
        stmt = (
            select(OrganizationRole)
            .where(OrganizationRole.organization_id == organization_id)
            .options(selectinload(OrganizationRole.permissions))
            .order_by(OrganizationRole.is_system.desc(), OrganizationRole.name.asc())
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def create(self, role: OrganizationRole) -> OrganizationRole:
        """Persist a new organization role."""
        self.session.add(role)
        await self.session.flush()
        return role

    async def delete(self, role_id: uuid.UUID, organization_id: uuid.UUID) -> bool:
        """Delete an organization role."""
        stmt = (
            delete(OrganizationRole)
            .where(
                OrganizationRole.id == role_id,
                OrganizationRole.organization_id == organization_id,
            )
            .returning(OrganizationRole.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.scalar_one_or_none() is not None

    async def set_role_permissions(
        self,
        role_id: uuid.UUID,
        permission_ids: list[uuid.UUID],
    ) -> None:
        """Atomically synchronize permission associations for a custom role.

        System roles (owner, admin, member, viewer) are immutable and cannot have
        their permission associations modified through ordinary operations.
        """
        role = await self.session.get(OrganizationRole, role_id)
        if role and role.is_system:
            from app.core.exceptions import ValidationError

            raise ValidationError(
                f"System role '{role.slug}' is immutable and its permission associations cannot be modified."
            )

        await self._sync_role_permissions(role_id, permission_ids)

    async def bootstrap_system_role_permissions(
        self,
        role_id: uuid.UUID,
        permission_ids: list[uuid.UUID],
    ) -> None:
        """Dedicated internal method to populate system role permissions during initial bootstrap.

        Only permitted for roles with is_system=True.
        """
        role = await self.session.get(OrganizationRole, role_id)
        if not role or not role.is_system:
            from app.core.exceptions import ValidationError

            raise ValidationError(
                "bootstrap_system_role_permissions can only be used on system roles."
            )

        await self._sync_role_permissions(role_id, permission_ids)

    async def _sync_role_permissions(
        self,
        role_id: uuid.UUID,
        permission_ids: list[uuid.UUID],
    ) -> None:
        """Internal helper to atomically replace role permission mappings."""
        # 1. Remove existing permissions
        await self.session.execute(delete(RolePermission).where(RolePermission.role_id == role_id))

        # 2. Insert new permissions
        for p_id in permission_ids:
            self.session.add(RolePermission(role_id=role_id, permission_id=p_id))

        await self.session.flush()

    async def get_membership_roles(self, membership_id: uuid.UUID) -> Sequence[OrganizationRole]:
        """Fetch all assigned roles for a membership."""
        stmt = (
            select(OrganizationRole)
            .join(MembershipRole, MembershipRole.role_id == OrganizationRole.id)
            .where(MembershipRole.membership_id == membership_id)
            .options(selectinload(OrganizationRole.permissions))
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def set_membership_roles(
        self,
        membership_id: uuid.UUID,
        role_ids: list[uuid.UUID],
        organization_id: uuid.UUID | None = None,
        assigned_by_user_id: uuid.UUID | None = None,
    ) -> None:
        """Atomically synchronize assigned roles for an organization membership."""
        if organization_id is None:
            from app.models.membership import OrganizationMembership

            m = await self.session.get(OrganizationMembership, membership_id)
            if not m:
                raise ValueError(f"Membership {membership_id} not found")
            organization_id = m.organization_id

        # 1. Clear existing roles
        await self.session.execute(
            delete(MembershipRole).where(MembershipRole.membership_id == membership_id)
        )

        # 2. Assign new roles
        for r_id in role_ids:
            self.session.add(
                MembershipRole(
                    membership_id=membership_id,
                    role_id=r_id,
                    organization_id=organization_id,
                    assigned_by_user_id=assigned_by_user_id,
                )
            )

        await self.session.flush()
