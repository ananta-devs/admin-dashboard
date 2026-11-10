import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.invitation import OrganizationInvitation
from app.models.role import OrganizationRole


class InvitationRepository:
    """Repository handling persistence and atomic state transitions for organization invitations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, invitation: OrganizationInvitation) -> OrganizationInvitation:
        """Persist a new invitation record."""
        self.session.add(invitation)
        await self.session.flush()
        return invitation

    async def get_by_id(
        self, invitation_id: uuid.UUID, organization_id: uuid.UUID
    ) -> OrganizationInvitation | None:
        """Fetch invitation by ID scoped to specific organization."""
        stmt = (
            select(OrganizationInvitation)
            .where(
                OrganizationInvitation.id == invitation_id,
                OrganizationInvitation.organization_id == organization_id,
            )
            .options(
                selectinload(OrganizationInvitation.role).selectinload(
                    OrganizationRole.permissions
                ),
                selectinload(OrganizationInvitation.invited_by),
            )
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_token_hash(self, token_hash: str) -> OrganizationInvitation | None:
        """Fetch invitation by token hash across organizations for acceptance verification."""
        stmt = (
            select(OrganizationInvitation)
            .where(OrganizationInvitation.token_hash == token_hash)
            .options(
                selectinload(OrganizationInvitation.role).selectinload(
                    OrganizationRole.permissions
                ),
                selectinload(OrganizationInvitation.organization),
                selectinload(OrganizationInvitation.invited_by),
            )
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_pending_by_organization(
        self, organization_id: uuid.UUID
    ) -> Sequence[OrganizationInvitation]:
        """List all pending, unexpired, and unrevoked invitations for an organization."""
        now = datetime.now(UTC)
        stmt = (
            select(OrganizationInvitation)
            .where(
                OrganizationInvitation.organization_id == organization_id,
                OrganizationInvitation.accepted_at.is_(None),
                OrganizationInvitation.revoked_at.is_(None),
                OrganizationInvitation.expires_at > now,
            )
            .options(
                selectinload(OrganizationInvitation.role).selectinload(
                    OrganizationRole.permissions
                ),
                selectinload(OrganizationInvitation.invited_by),
            )
            .order_by(OrganizationInvitation.created_at.desc())
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def consume_invitation_atomically(self, invitation_id: uuid.UUID) -> bool:
        """Atomically mark an invitation as accepted if it is currently pending and unexpired.

        Uses PostgreSQL conditional UPDATE ... WHERE accepted_at IS NULL AND revoked_at IS NULL RETURNING id.
        """
        now = datetime.now(UTC)
        stmt = (
            update(OrganizationInvitation)
            .where(
                OrganizationInvitation.id == invitation_id,
                OrganizationInvitation.accepted_at.is_(None),
                OrganizationInvitation.revoked_at.is_(None),
                OrganizationInvitation.expires_at > now,
            )
            .values(accepted_at=now)
            .returning(OrganizationInvitation.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.scalar_one_or_none() is not None

    async def revoke(self, invitation_id: uuid.UUID, organization_id: uuid.UUID) -> bool:
        """Atomically mark an invitation as revoked."""
        now = datetime.now(UTC)
        stmt = (
            update(OrganizationInvitation)
            .where(
                OrganizationInvitation.id == invitation_id,
                OrganizationInvitation.organization_id == organization_id,
                OrganizationInvitation.accepted_at.is_(None),
                OrganizationInvitation.revoked_at.is_(None),
            )
            .values(revoked_at=now)
            .returning(OrganizationInvitation.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.scalar_one_or_none() is not None
