import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.session import AuthenticationSession


class SessionRepository:
    """Repository handling server-side authentication sessions in PostgreSQL."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_token_hash(self, token_hash: str) -> AuthenticationSession | None:
        stmt = (
            select(AuthenticationSession)
            .options(
                selectinload(AuthenticationSession.user),
                selectinload(AuthenticationSession.active_organization),
            )
            .where(AuthenticationSession.token_hash == token_hash)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_id(self, session_id: uuid.UUID) -> AuthenticationSession | None:
        stmt = (
            select(AuthenticationSession)
            .options(selectinload(AuthenticationSession.user))
            .where(AuthenticationSession.id == session_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, auth_session: AuthenticationSession) -> AuthenticationSession:
        self.session.add(auth_session)
        await self.session.flush()
        return auth_session

    async def list_active_by_user_id(self, user_id: uuid.UUID) -> list[AuthenticationSession]:
        now = datetime.now(UTC)
        stmt = (
            select(AuthenticationSession)
            .where(
                and_(
                    AuthenticationSession.user_id == user_id,
                    AuthenticationSession.revoked_at.is_(None),
                    AuthenticationSession.idle_expires_at > now,
                    AuthenticationSession.absolute_expires_at > now,
                )
            )
            .order_by(AuthenticationSession.last_seen_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def revoke(self, session_id: uuid.UUID) -> bool:
        stmt = (
            update(AuthenticationSession)
            .where(
                and_(
                    AuthenticationSession.id == session_id,
                    AuthenticationSession.revoked_at.is_(None),
                )
            )
            .values(revoked_at=datetime.now(UTC))
            .returning(AuthenticationSession.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.scalar_one_or_none() is not None

    async def revoke_all_for_user(
        self, user_id: uuid.UUID, except_session_id: uuid.UUID | None = None
    ) -> int:
        conditions = [
            AuthenticationSession.user_id == user_id,
            AuthenticationSession.revoked_at.is_(None),
        ]
        if except_session_id is not None:
            conditions.append(AuthenticationSession.id != except_session_id)

        stmt = (
            update(AuthenticationSession)
            .where(and_(*conditions))
            .values(revoked_at=datetime.now(UTC))
            .returning(AuthenticationSession.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return len(list(res.scalars().all()))

    async def update_last_seen(
        self, session_id: uuid.UUID, last_seen_at: datetime, idle_expires_at: datetime
    ) -> None:
        stmt = (
            update(AuthenticationSession)
            .where(AuthenticationSession.id == session_id)
            .values(last_seen_at=last_seen_at, idle_expires_at=idle_expires_at)
        )
        await self.session.execute(stmt)
        await self.session.flush()

    async def set_active_organization(
        self, session_id: uuid.UUID, organization_id: uuid.UUID | None
    ) -> None:
        stmt = (
            update(AuthenticationSession)
            .where(AuthenticationSession.id == session_id)
            .values(active_organization_id=organization_id)
        )
        await self.session.execute(stmt)
        await self.session.flush()
