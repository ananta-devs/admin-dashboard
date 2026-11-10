import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    get_current_membership,
    get_current_session,
    get_current_user,
    require_any_permission,
    require_permission,
    verify_csrf_token,
)
from app.core.exceptions import EntityNotFoundError, ValidationError
from app.db.session import get_db_session
from app.db.transaction import transaction
from app.models.membership import OrganizationMembership
from app.models.session import AuthenticationSession
from app.models.user import User
from app.repositories.membership import MembershipRepository
from app.repositories.organization import OrganizationRepository
from app.schemas.audit import AuditEventRead
from app.schemas.invitation import (
    InvitationAcceptInput,
    InvitationCreateInput,
    InvitationRead,
)
from app.schemas.member import MemberRead
from app.schemas.organization import OrganizationRead, OrganizationUpdate
from app.schemas.ownership import OwnershipTransferInput
from app.schemas.permission import PermissionRead
from app.schemas.role import (
    MemberRolesUpdateInput,
    RoleCreateInput,
    RoleRead,
    RoleUpdateInput,
)
from app.services.audit import AuditService
from app.services.invitation import InvitationService
from app.services.ownership import OwnershipService
from app.services.permission import PermissionService
from app.services.role import RoleService

router = APIRouter(prefix="/organization", tags=["Organization Administration"])


# ---------------------------------------------------------------------------
# Active Membership & Permissions Info
# ---------------------------------------------------------------------------


@router.get("/me")
async def get_my_membership_and_permissions(
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
):
    """Retrieve the current user's membership details and effective permissions in the active organization."""
    perm_service = PermissionService(db)
    perms = await perm_service.get_effective_permissions(
        membership.id, organization_id=membership.organization_id
    )
    membership_repo = MembershipRepository(db)
    loaded = await membership_repo.get_by_id(membership.id, membership.organization_id)
    return {
        "success": True,
        "data": {
            "membership": MemberRead.model_validate(loaded),
            "effective_permissions": sorted(perms),
        },
    }


# ---------------------------------------------------------------------------
# Permissions Catalogue
# ---------------------------------------------------------------------------


@router.get("/permissions", response_model=list[PermissionRead])
async def list_permissions(
    _perm: None = Depends(require_permission("roles.read")),
    db: AsyncSession = Depends(get_db_session),
):
    """List the fixed system permission catalogue."""
    perm_service = PermissionService(db)
    permissions = await perm_service.list_available_permissions()
    return [PermissionRead.model_validate(p) for p in permissions]


# ---------------------------------------------------------------------------
# Role Management
# ---------------------------------------------------------------------------


@router.get("/roles", response_model=list[RoleRead])
async def list_roles(
    membership: OrganizationMembership = Depends(get_current_membership),
    _perm: None = Depends(require_permission("roles.read")),
    db: AsyncSession = Depends(get_db_session),
):
    """List all system and custom roles defined for the active organization."""
    role_service = RoleService(db)
    roles = await role_service.list_roles(membership.organization_id)
    return [RoleRead.model_validate(r) for r in roles]


@router.get("/roles/{role_id}", response_model=RoleRead)
async def get_role(
    role_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    _perm: None = Depends(require_permission("roles.read")),
    db: AsyncSession = Depends(get_db_session),
):
    """Get details of a specific role."""
    role_service = RoleService(db)
    role = await role_service.get_role(role_id, membership.organization_id)
    return RoleRead.model_validate(role)


@router.post(
    "/roles",
    response_model=RoleRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(verify_csrf_token), Depends(require_permission("roles.create"))],
)
async def create_custom_role(
    data: RoleCreateInput,
    membership: OrganizationMembership = Depends(get_current_membership),
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a new custom role within the active organization."""
    role_service = RoleService(db)
    audit_service = AuditService(db)

    async with transaction(db):
        role = await role_service.create_custom_role(
            actor_membership_id=membership.id,
            organization_id=membership.organization_id,
            data=data,
        )
        await audit_service.record_event(
            organization_id=membership.organization_id,
            actor_user_id=session.user_id,
            action="role.created",
            target_type="role",
            target_id=str(role.id),
            metadata={"slug": role.slug, "name": role.name, "permissions": data.permission_codes},
        )

    return RoleRead.model_validate(role)


@router.patch(
    "/roles/{role_id}",
    response_model=RoleRead,
    dependencies=[Depends(verify_csrf_token), Depends(require_permission("roles.update"))],
)
async def update_custom_role(
    role_id: uuid.UUID,
    data: RoleUpdateInput,
    membership: OrganizationMembership = Depends(get_current_membership),
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Update a custom role's metadata and permission associations."""
    role_service = RoleService(db)
    audit_service = AuditService(db)

    async with transaction(db):
        role = await role_service.update_custom_role(
            actor_membership_id=membership.id,
            organization_id=membership.organization_id,
            role_id=role_id,
            data=data,
        )
        await audit_service.record_event(
            organization_id=membership.organization_id,
            actor_user_id=session.user_id,
            action="role.updated",
            target_type="role",
            target_id=str(role.id),
            metadata=data.model_dump(exclude_unset=True),
        )

    return RoleRead.model_validate(role)


@router.delete(
    "/roles/{role_id}",
    dependencies=[Depends(verify_csrf_token), Depends(require_permission("roles.delete"))],
)
async def delete_custom_role(
    role_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Delete an unassigned custom role. System roles cannot be deleted."""
    role_service = RoleService(db)
    audit_service = AuditService(db)

    async with transaction(db):
        await role_service.delete_custom_role(
            organization_id=membership.organization_id, role_id=role_id
        )
        await audit_service.record_event(
            organization_id=membership.organization_id,
            actor_user_id=session.user_id,
            action="role.deleted",
            target_type="role",
            target_id=str(role_id),
        )

    return {"success": True, "message": "Role deleted successfully"}


# ---------------------------------------------------------------------------
# Member Administration
# ---------------------------------------------------------------------------


@router.get("/members", response_model=list[MemberRead])
async def list_members(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    membership: OrganizationMembership = Depends(get_current_membership),
    _perm: None = Depends(require_permission("members.read")),
    db: AsyncSession = Depends(get_db_session),
):
    """List all members of the active organization with user profiles and assigned roles."""
    repo = MembershipRepository(db)
    members = await repo.list_by_organization(
        organization_id=membership.organization_id, limit=limit, offset=offset
    )
    return [MemberRead.model_validate(m) for m in members]


@router.get("/members/{member_id}", response_model=MemberRead)
async def get_member(
    member_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    _perm: None = Depends(require_permission("members.read")),
    db: AsyncSession = Depends(get_db_session),
):
    """Get single member details."""
    repo = MembershipRepository(db)
    member = await repo.get_by_id(member_id, organization_id=membership.organization_id)
    if not member:
        raise EntityNotFoundError("OrganizationMembership", str(member_id))
    return MemberRead.model_validate(member)


@router.put(
    "/members/{member_id}/roles",
    response_model=MemberRead,
    dependencies=[
        Depends(verify_csrf_token),
        Depends(require_any_permission("members.roles.assign", "members.update")),
    ],
)
async def update_member_roles(
    member_id: uuid.UUID,
    data: MemberRolesUpdateInput,
    membership: OrganizationMembership = Depends(get_current_membership),
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Assign roles to a member with privilege escalation checks and last-owner protection."""
    role_service = RoleService(db)
    audit_service = AuditService(db)

    async with transaction(db):
        updated_member = await role_service.assign_roles_to_member(
            actor_membership_id=membership.id,
            actor_user_id=session.user_id,
            organization_id=membership.organization_id,
            target_membership_id=member_id,
            role_ids=data.role_ids,
        )
        await audit_service.record_event(
            organization_id=membership.organization_id,
            actor_user_id=session.user_id,
            action="member.roles_updated",
            target_type="member",
            target_id=str(member_id),
            metadata={"assigned_role_ids": [str(rid) for rid in data.role_ids]},
        )

    return MemberRead.model_validate(updated_member)


@router.delete(
    "/members/{member_id}",
    dependencies=[
        Depends(verify_csrf_token),
        Depends(require_any_permission("members.remove", "members.delete")),
    ],
)
async def remove_member(
    member_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Remove a member from the active organization with last-owner protection."""
    repo = MembershipRepository(db)
    audit_service = AuditService(db)

    async with transaction(db):
        # Serialize member removal and owner validation using organization-level row lock
        from sqlalchemy import select

        from app.models.organization import Organization

        await db.execute(
            select(Organization.id)
            .where(Organization.id == membership.organization_id)
            .with_for_update()
        )

        target = await repo.get_by_id(member_id, organization_id=membership.organization_id)
        if not target:
            raise EntityNotFoundError("OrganizationMembership", str(member_id))

        # Check if target is an Owner
        target_role_slugs = {r.slug for r in target.roles}
        if "owner" in target_role_slugs:
            owners = await repo.get_owner_memberships(membership.organization_id, for_update=True)
            if len(owners) <= 1:
                raise ValidationError("Cannot remove the last Owner of an organization")

        await repo.delete(target)
        await audit_service.record_event(
            organization_id=membership.organization_id,
            actor_user_id=session.user_id,
            action="member.removed",
            target_type="member",
            target_id=str(member_id),
            metadata={"removed_user_id": str(target.user_id)},
        )

    return {"success": True, "message": "Member removed successfully"}


# ---------------------------------------------------------------------------
# Invitations Lifecycle
# ---------------------------------------------------------------------------


@router.get("/invitations", response_model=list[InvitationRead])
async def list_invitations(
    membership: OrganizationMembership = Depends(get_current_membership),
    _perm: None = Depends(require_permission("members.read")),
    db: AsyncSession = Depends(get_db_session),
):
    """List all pending, unexpired invitations for the active organization."""
    service = InvitationService(db)
    invites = await service.list_invitations(membership.organization_id)
    return [InvitationRead.model_validate(inv) for inv in invites]


@router.post(
    "/invitations",
    dependencies=[Depends(verify_csrf_token), Depends(require_permission("members.invite"))],
)
async def create_invitation(
    data: InvitationCreateInput,
    membership: OrganizationMembership = Depends(get_current_membership),
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Invite a new user to join the active organization."""
    service = InvitationService(db)
    audit_service = AuditService(db)

    async with transaction(db):
        invitation, raw_token = await service.create_invitation(
            actor_membership_id=membership.id,
            actor_user_id=session.user_id,
            organization_id=membership.organization_id,
            data=data,
        )
        await audit_service.record_event(
            organization_id=membership.organization_id,
            actor_user_id=session.user_id,
            action="invitation.created",
            target_type="invitation",
            target_id=str(invitation.id),
            metadata={"email": invitation.email, "role_id": str(invitation.role_id)},
        )

    return {
        "success": True,
        "data": {
            "invitation": InvitationRead.model_validate(invitation),
            "token": raw_token,  # Provided for programmatic / test convenience
        },
    }


@router.delete(
    "/invitations/{invitation_id}",
    dependencies=[Depends(verify_csrf_token), Depends(require_permission("members.invite"))],
)
async def revoke_invitation(
    invitation_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
):
    """Revoke a pending organization invitation."""
    service = InvitationService(db)
    audit_service = AuditService(db)

    async with transaction(db):
        await service.revoke_invitation(membership.organization_id, invitation_id)
        await audit_service.record_event(
            organization_id=membership.organization_id,
            actor_user_id=session.user_id,
            action="invitation.revoked",
            target_type="invitation",
            target_id=str(invitation_id),
        )

    return {"success": True, "message": "Invitation revoked successfully"}


@router.post(
    "/invitations/accept",
    dependencies=[Depends(verify_csrf_token)],
)
async def accept_invitation(
    data: InvitationAcceptInput,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Accept an organization invitation using an action token."""
    service = InvitationService(db)
    audit_service = AuditService(db)

    async with transaction(db):
        membership = await service.accept_invitation(data.token, user)
        await audit_service.record_event(
            organization_id=membership.organization_id,
            actor_user_id=user.id,
            action="invitation.accepted",
            target_type="membership",
            target_id=str(membership.id),
            metadata={"user_id": str(user.id)},
        )

    return {
        "success": True,
        "message": "Invitation accepted successfully",
        "data": MemberRead.model_validate(membership),
    }


# ---------------------------------------------------------------------------
# Ownership Transfer
# ---------------------------------------------------------------------------


@router.post(
    "/ownership/transfer",
    dependencies=[Depends(verify_csrf_token), Depends(require_permission("ownership.transfer"))],
)
async def transfer_ownership(
    data: OwnershipTransferInput,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
):
    """Atomically transfer organization ownership to another active member."""
    service = OwnershipService(db)
    await service.transfer_ownership(
        actor_membership=membership,
        organization_id=membership.organization_id,
        target_membership_id=data.target_membership_id,
        confirmation=data.confirmation,
    )
    return {"success": True, "message": "Ownership transferred successfully"}


# ---------------------------------------------------------------------------
# Organization Settings & Profile
# ---------------------------------------------------------------------------


@router.get("/settings", response_model=OrganizationRead)
async def get_organization_settings(
    membership: OrganizationMembership = Depends(get_current_membership),
    _perm: None = Depends(require_permission("organization.read")),
    db: AsyncSession = Depends(get_db_session),
):
    """Retrieve organization profile settings for the active organization."""
    org_repo = OrganizationRepository(db)
    org = await org_repo.get_by_id(membership.organization_id)
    if not org:
        raise EntityNotFoundError("Organization", str(membership.organization_id))
    return OrganizationRead.model_validate(org)


@router.patch(
    "/settings",
    response_model=OrganizationRead,
    dependencies=[
        Depends(verify_csrf_token),
        Depends(require_permission("organization.settings.update")),
    ],
)
@router.put(
    "/settings",
    response_model=OrganizationRead,
    dependencies=[
        Depends(verify_csrf_token),
        Depends(require_permission("organization.settings.update")),
    ],
)
async def update_organization_settings(
    data: OrganizationUpdate,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
):
    """Update organization settings, protected by organization.settings.update and CSRF."""
    org_repo = OrganizationRepository(db)
    audit_service = AuditService(db)

    async with transaction(db):
        org = await org_repo.get_by_id(membership.organization_id)
        if not org:
            raise EntityNotFoundError("Organization", str(membership.organization_id))

        old_name = org.name
        if data.name is not None and data.name.strip():
            org.name = data.name.strip()
            await db.flush()

        await audit_service.record_event(
            organization_id=membership.organization_id,
            organization_slug=org.slug,
            actor_user_id=membership.user_id,
            action="organization.settings_updated",
            target_type="organization",
            target_id=str(org.id),
            metadata={"old_name": old_name, "new_name": org.name},
        )

    refreshed = await org_repo.get_by_id(membership.organization_id)
    assert refreshed is not None
    return OrganizationRead.model_validate(refreshed)


# ---------------------------------------------------------------------------
# Administrative Audit Logs
# ---------------------------------------------------------------------------


@router.get("/audit-logs", response_model=list[AuditEventRead])
async def list_audit_logs(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    membership: OrganizationMembership = Depends(get_current_membership),
    _perm: None = Depends(require_permission("audit.read")),
    db: AsyncSession = Depends(get_db_session),
):
    """Query append-only administrative audit events for the active organization."""
    service = AuditService(db)
    events = await service.list_events(membership.organization_id, limit=limit, offset=offset)
    return [AuditEventRead.model_validate(e) for e in events]


@router.get("/audit-events", response_model=list[AuditEventRead])
async def list_audit_events_alias(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    membership: OrganizationMembership = Depends(get_current_membership),
    _perm: None = Depends(require_permission("audit.read")),
    db: AsyncSession = Depends(get_db_session),
):
    """Reconciled alias endpoint for /audit-logs ensuring total route agreement."""
    return await list_audit_logs(
        limit=limit, offset=offset, membership=membership, _perm=_perm, db=db
    )
