"""Repository for ExternalIntegration and OAuthState entities."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration import ExternalIntegration, OAuthState


class IntegrationRepository:
    """Data access layer for external OAuth integrations and state tokens."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, integration: ExternalIntegration) -> ExternalIntegration:
        self.session.add(integration)
        await self.session.flush()
        return integration

    async def get_by_id(
        self, integration_id: uuid.UUID, organization_id: uuid.UUID
    ) -> ExternalIntegration | None:
        stmt = select(ExternalIntegration).where(
            ExternalIntegration.id == integration_id,
            ExternalIntegration.organization_id == organization_id,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_provider(
        self, organization_id: uuid.UUID, provider: str
    ) -> ExternalIntegration | None:
        stmt = (
            select(ExternalIntegration)
            .where(
                ExternalIntegration.organization_id == organization_id,
                ExternalIntegration.provider == provider,
                ExternalIntegration.status != "revoked",
            )
            .order_by(ExternalIntegration.created_at.desc())
        )
        res = await self.session.execute(stmt)
        return res.scalars().first()

    async def list_by_organization(
        self, organization_id: uuid.UUID
    ) -> Sequence[ExternalIntegration]:
        stmt = (
            select(ExternalIntegration)
            .where(ExternalIntegration.organization_id == organization_id)
            .order_by(ExternalIntegration.created_at.desc())
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def delete(self, integration_id: uuid.UUID, organization_id: uuid.UUID) -> bool:
        stmt = (
            delete(ExternalIntegration)
            .where(
                ExternalIntegration.id == integration_id,
                ExternalIntegration.organization_id == organization_id,
            )
            .returning(ExternalIntegration.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.scalar_one_or_none() is not None

    async def create_oauth_state(self, oauth_state: OAuthState) -> OAuthState:
        self.session.add(oauth_state)
        await self.session.flush()
        return oauth_state

    async def consume_oauth_state(
        self, state_token: str, organization_id: uuid.UUID
    ) -> OAuthState | None:
        """Find valid unexpired OAuth state for organization and delete it atomically (single-use)."""
        now = datetime.now(UTC)
        stmt = select(OAuthState).where(
            OAuthState.state_token == state_token,
            OAuthState.organization_id == organization_id,
            OAuthState.expires_at > now,
        )
        res = await self.session.execute(stmt)
        oauth_state = res.scalar_one_or_none()
        if oauth_state:
            await self.session.delete(oauth_state)
            await self.session.flush()
        return oauth_state
