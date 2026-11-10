import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import EntityNotFoundError, ForbiddenError, ValidationError
from app.db.transaction import transaction
from app.models.membership import OrganizationMembership
from app.repositories.membership import MembershipRepository
from app.repositories.role import RoleRepository
from app.services.audit import AuditService
from app.services.permission import PermissionService


class OwnershipService:
    """Service governing atomic ownership transfer with row locking and audit logging."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.membership_repo = MembershipRepository(session)
        self.role_repo = RoleRepository(session)
        self.perm_service = PermissionService(session)
        self.audit_service = AuditService(session)

    async def transfer_ownership(
        self,
        actor_membership: OrganizationMembership,
        organization_id: uuid.UUID,
        target_membership_id: uuid.UUID,
        confirmation: str,
        request_id: str | None = None,
    ) -> None:
        """Atomically transfer organization ownership to another active member."""
        # 1. Validate confirmation phrase
        if confirmation.strip() != "TRANSFER":
            raise ValidationError("Confirmation phrase must be 'TRANSFER'")

        # 2. Prevent self-transfer
        if actor_membership.id == target_membership_id:
            raise ValidationError("Cannot transfer ownership to yourself")

        # 3. Verify actor has ownership.transfer permission
        actor_perms = await self.perm_service.get_effective_permissions(
            actor_membership.id, organization_id=organization_id
        )
        if "ownership.transfer" not in actor_perms:
            raise ForbiddenError("Only the organization owner can transfer ownership")

        # 4. Verify target membership exists and belongs to the same organization
        target_membership = await self.membership_repo.get_by_id(
            target_membership_id, organization_id=organization_id
        )
        if not target_membership:
            raise EntityNotFoundError("Target OrganizationMembership", str(target_membership_id))

        # 5. Atomic ownership transfer with PostgreSQL organization-level row locking
        from sqlalchemy import select

        from app.models.organization import Organization

        async with transaction(self.session):
            # 1. Acquire organization-level lock to strictly serialize ownership transfers
            await self.session.execute(
                select(Organization.id).where(Organization.id == organization_id).with_for_update()
            )

            # Revalidate actor authorization under the transactional lock
            actor_perms_locked = await self.perm_service.get_effective_permissions(
                actor_membership.id, organization_id=organization_id
            )
            if "ownership.transfer" not in actor_perms_locked:
                raise ForbiddenError("Only the organization owner can transfer ownership")

            # 2. Re-query owner memberships under organization lock
            owners = await self.membership_repo.get_owner_memberships(organization_id)
            actor_is_owner = any(o.id == actor_membership.id for o in owners)
            if not actor_is_owner:
                raise ForbiddenError("Actor is not an active Owner of this organization")

            # Revalidate target membership under the lock
            target_membership_locked = await self.membership_repo.get_by_id(
                target_membership_id, organization_id=organization_id
            )
            if not target_membership_locked:
                raise EntityNotFoundError(
                    "Target OrganizationMembership", str(target_membership_id)
                )

            # Fetch owner and admin roles
            owner_role = await self.role_repo.get_by_slug("owner", organization_id)
            admin_role = await self.role_repo.get_by_slug("admin", organization_id)

            if not owner_role or not admin_role:
                raise ValidationError("System owner or admin role not found for organization")

            # Demote current owner to admin
            await self.role_repo.set_membership_roles(
                actor_membership.id,
                [admin_role.id],
                organization_id=organization_id,
                assigned_by_user_id=actor_membership.user_id,
            )

            # Promote target to owner
            await self.role_repo.set_membership_roles(
                target_membership_id,
                [owner_role.id],
                organization_id=organization_id,
                assigned_by_user_id=actor_membership.user_id,
            )

            # Record audit event
            await self.audit_service.record_event(
                organization_id=organization_id,
                actor_user_id=actor_membership.user_id,
                action="organization.ownership_transferred",
                target_type="organization",
                target_id=str(organization_id),
                request_id=request_id,
                metadata={
                    "previous_owner_user_id": str(actor_membership.user_id),
                    "new_owner_user_id": str(target_membership.user_id),
                    "new_owner_membership_id": str(target_membership_id),
                },
            )

    async def demote_co_owner(
        self,
        actor_membership: OrganizationMembership,
        organization_id: uuid.UUID,
        target_membership_id: uuid.UUID,
        request_id: str | None = None,
    ) -> None:
        """Atomically demote a co-owner to admin under organization-level lock with last-owner protection."""
        from sqlalchemy import select

        from app.models.organization import Organization

        async with transaction(self.session):
            # 1. Acquire organization-level lock to strictly serialize owner mutations
            await self.session.execute(
                select(Organization.id).where(Organization.id == organization_id).with_for_update()
            )

            # 2. Revalidate actor permissions under the lock
            actor_perms = await self.perm_service.get_effective_permissions(
                actor_membership.id, organization_id=organization_id
            )
            if "ownership.transfer" not in actor_perms:
                raise ForbiddenError("Only an Owner may demote another Owner")

            # 3. Re-evaluate active owner count under the lock
            owners = await self.membership_repo.get_owner_memberships(organization_id)
            if len(owners) <= 1:
                raise ValidationError("Cannot demote the last Owner of an organization")

            target = await self.membership_repo.get_by_id(
                target_membership_id, organization_id=organization_id
            )
            if not target:
                raise EntityNotFoundError(
                    "Target OrganizationMembership", str(target_membership_id)
                )

            target_roles = {r.slug for r in target.roles}
            if "owner" not in target_roles:
                raise ValidationError("Target member is not currently an Owner")

            admin_role = await self.role_repo.get_by_slug("admin", organization_id)
            if not admin_role:
                raise ValidationError("Admin role not found for organization")

            # Demote target to admin
            await self.role_repo.set_membership_roles(
                target_membership_id,
                [admin_role.id],
                organization_id=organization_id,
                assigned_by_user_id=actor_membership.user_id,
            )

            # Record audit event
            await self.audit_service.record_event(
                organization_id=organization_id,
                actor_user_id=actor_membership.user_id,
                action="organization.owner_demoted",
                target_type="member",
                target_id=str(target_membership_id),
                request_id=request_id,
                metadata={"demoted_member_id": str(target_membership_id)},
            )
