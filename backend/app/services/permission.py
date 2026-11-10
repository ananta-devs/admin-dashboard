import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.membership import OrganizationMembership
from app.models.permission import Permission
from app.models.role import MembershipRole, OrganizationRole, RolePermission
from app.repositories.permission import PermissionRepository


class PermissionService:
    """Service governing permission lookups and authoritative PostgreSQL-backed permission evaluation."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = PermissionRepository(session)

    async def list_available_permissions(self) -> Sequence[Permission]:
        """Fetch the fixed system permission catalogue."""
        return await self.repo.list_all()

    async def get_effective_permissions(
        self, membership_id: uuid.UUID, organization_id: uuid.UUID
    ) -> set[str]:
        """Resolve the complete set of effective permission codes assigned to an organization membership.

        Strictly scoped to both the membership AND the mandatory active organization context.
        Fails closed on missing or mismatched organization context.
        Performs a single optimized SQL join:
        organization_memberships -> membership_roles -> organization_roles -> role_permissions -> permissions
        """
        if not membership_id or not organization_id:
            return set()

        try:
            stmt = (
                select(Permission.code)
                .join(RolePermission, RolePermission.permission_id == Permission.id)
                .join(OrganizationRole, OrganizationRole.id == RolePermission.role_id)
                .join(
                    MembershipRole,
                    (MembershipRole.role_id == OrganizationRole.id)
                    & (MembershipRole.organization_id == OrganizationRole.organization_id),
                )
                .join(
                    OrganizationMembership,
                    (OrganizationMembership.id == MembershipRole.membership_id)
                    & (OrganizationMembership.organization_id == MembershipRole.organization_id),
                )
                .where(
                    OrganizationMembership.id == membership_id,
                    OrganizationMembership.organization_id == organization_id,
                    OrganizationRole.organization_id == organization_id,
                    MembershipRole.organization_id == organization_id,
                )
                .distinct()
            )
            res = await self.session.execute(stmt)
            return set(res.scalars().all())
        except Exception:
            # Authorization fails closed
            return set()

    async def check_membership_has_permission(
        self,
        membership_id: uuid.UUID,
        permission_code: str,
        organization_id: uuid.UUID,
    ) -> bool:
        """Check whether a membership possesses a specific permission code in the active organization context."""
        if not organization_id:
            return False
        effective_codes = await self.get_effective_permissions(
            membership_id, organization_id=organization_id
        )
        return permission_code in effective_codes
