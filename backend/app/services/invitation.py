import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import EntityNotFoundError, ForbiddenError, ValidationError
from app.core.security import generate_opaque_token, hash_token
from app.models.invitation import OrganizationInvitation
from app.models.membership import OrganizationMembership
from app.models.user import User
from app.repositories.invitation import InvitationRepository
from app.repositories.membership import MembershipRepository
from app.repositories.role import RoleRepository
from app.repositories.user import UserRepository
from app.schemas.invitation import InvitationCreateInput
from app.schemas.user import normalize_email_address
from app.services.email import EmailService
from app.services.permission import PermissionService

INVITATION_EXPIRY_DAYS = 7


class InvitationService:
    """Service handling invitation lifecycle, cryptographic verification, and atomic acceptance."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.invitation_repo = InvitationRepository(session)
        self.role_repo = RoleRepository(session)
        self.membership_repo = MembershipRepository(session)
        self.user_repo = UserRepository(session)
        self.perm_service = PermissionService(session)
        self.email_service = EmailService()

    async def list_invitations(
        self, organization_id: uuid.UUID
    ) -> Sequence[OrganizationInvitation]:
        """List active pending invitations for an organization."""
        return await self.invitation_repo.list_pending_by_organization(organization_id)

    async def create_invitation(
        self,
        actor_membership_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        organization_id: uuid.UUID,
        data: InvitationCreateInput,
    ) -> tuple[OrganizationInvitation, str]:
        """Create a new organization invitation with delegation checks."""
        normalized_email = normalize_email_address(data.email)

        # 1. Check if user is already a member of this organization
        existing_user = await self.user_repo.get_by_normalized_email(normalized_email)
        if existing_user:
            existing_membership = await self.membership_repo.get(organization_id, existing_user.id)
            if existing_membership:
                raise ValidationError(
                    "User with this email is already a member of this organization"
                )

        # 2. Check role existence and validate actor authority
        role = await self.role_repo.get_by_id(data.role_id, organization_id)
        if not role:
            raise ValidationError("Specified role does not exist in this organization")

        # The Owner role cannot be assigned through invitations
        if role.slug == "owner":
            raise ValidationError("Invitations cannot be issued for the Owner role.")

        actor_perms = await self.perm_service.get_effective_permissions(
            actor_membership_id, organization_id=organization_id
        )
        role_perms = {p.code for p in role.permissions}
        unauthorized = role_perms - actor_perms
        if unauthorized:
            raise ForbiddenError(
                f"Cannot invite with a role that grants permissions you lack: {', '.join(sorted(unauthorized))}"
            )

        # 3. Revoke any pending invitations for this email in this organization
        stmt = select(OrganizationInvitation).where(
            OrganizationInvitation.organization_id == organization_id,
            OrganizationInvitation.normalized_email == normalized_email,
            OrganizationInvitation.accepted_at.is_(None),
            OrganizationInvitation.revoked_at.is_(None),
        )
        existing_invites = (await self.session.execute(stmt)).scalars().all()
        now = datetime.now(UTC)
        for inv in existing_invites:
            inv.revoked_at = now

        # 4. Generate opaque token
        raw_token = generate_opaque_token()
        token_hash = hash_token(raw_token)
        expires_at = now + timedelta(days=INVITATION_EXPIRY_DAYS)

        invitation = OrganizationInvitation(
            organization_id=organization_id,
            email=data.email.strip(),
            normalized_email=normalized_email,
            role_id=role.id,
            token_hash=token_hash,
            invited_by_user_id=actor_user_id,
            expires_at=expires_at,
        )
        await self.invitation_repo.create(invitation)

        # 5. Fetch organization name for email
        from app.repositories.organization import OrganizationRepository

        org_repo = OrganizationRepository(self.session)
        org = await org_repo.get_by_id(organization_id)
        org_name = org.name if org else "an organization"

        # Send invitation email (mock / log)
        invite_link = f"{settings.FRONTEND_URL}/invitations/accept?token={raw_token}"
        await self.email_service.send_email(
            to_email=data.email,
            subject=f"You've been invited to join {org_name}",
            html_content=f"<p>You have been invited to join {org_name} as a {role.name}.</p><p><a href='{invite_link}'>Accept Invitation</a></p>",
        )

        refreshed = await self.invitation_repo.get_by_id(invitation.id, organization_id)
        assert refreshed is not None
        return refreshed, raw_token

    async def revoke_invitation(self, organization_id: uuid.UUID, invitation_id: uuid.UUID) -> None:
        """Revoke a pending organization invitation."""
        invitation = await self.invitation_repo.get_by_id(invitation_id, organization_id)
        if not invitation:
            raise EntityNotFoundError("Invitation", str(invitation_id))
        if invitation.accepted_at is not None:
            raise ValidationError("Cannot revoke an invitation that has already been accepted")

        await self.invitation_repo.revoke(invitation_id, organization_id)

    async def accept_invitation(self, token: str, current_user: User) -> OrganizationMembership:
        """Atomically accept an invitation for the authenticated user."""
        token_hash = hash_token(token)
        invitation = await self.invitation_repo.get_by_token_hash(token_hash)
        if not invitation or not invitation.is_valid:
            raise ValidationError("Invitation is invalid, expired, or has already been consumed")

        # Ensure accepting user's email matches the invitation email
        if current_user.normalized_email != invitation.normalized_email:
            raise ForbiddenError(
                f"This invitation was sent to {invitation.email}, but you are logged in as {current_user.email}"
            )

        # Ensure accepting user has verified their email if verification is required
        if settings.EMAIL_VERIFICATION_REQUIRED and current_user.email_verified_at is None:
            from app.core.exceptions import EmailNotVerifiedError

            raise EmailNotVerifiedError(
                "You must verify your email address before accepting invitations"
            )

        # Check if already a member
        existing_membership = await self.membership_repo.get(
            invitation.organization_id, current_user.id
        )
        if existing_membership:
            # Mark consumed
            await self.invitation_repo.consume_invitation_atomically(invitation.id)
            return existing_membership

        # Consume atomically
        consumed = await self.invitation_repo.consume_invitation_atomically(invitation.id)
        if not consumed:
            raise ValidationError("Invitation has already been accepted or revoked")

        # Create membership
        membership = OrganizationMembership(
            organization_id=invitation.organization_id,
            user_id=current_user.id,
        )
        await self.membership_repo.create(membership)

        # Assign role with database-enforced organization_id match
        await self.role_repo.set_membership_roles(
            membership.id,
            [invitation.role_id],
            organization_id=invitation.organization_id,
            assigned_by_user_id=invitation.invited_by_user_id,
        )

        refreshed = await self.membership_repo.get_by_id(
            membership.id, organization_id=invitation.organization_id
        )
        assert refreshed is not None
        return refreshed
