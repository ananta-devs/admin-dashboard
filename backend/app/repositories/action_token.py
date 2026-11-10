import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.action_token import AuthenticationActionToken


class ActionTokenRepository:
    """Repository handling single-use action tokens (email verification, password reset)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_token_hash(self, token_hash: str) -> AuthenticationActionToken | None:
        stmt = (
            select(AuthenticationActionToken)
            .options(selectinload(AuthenticationActionToken.user))
            .where(AuthenticationActionToken.token_hash == token_hash)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, token: AuthenticationActionToken) -> AuthenticationActionToken:
        self.session.add(token)
        await self.session.flush()
        return token

    async def consume_atomically(
        self, token_hash: str, purpose: str
    ) -> AuthenticationActionToken | None:
        """Atomically mark token as consumed if it is unconsumed and unexpired."""
        now = datetime.now(UTC)
        stmt = (
            update(AuthenticationActionToken)
            .where(
                and_(
                    AuthenticationActionToken.token_hash == token_hash,
                    AuthenticationActionToken.purpose == purpose,
                    AuthenticationActionToken.consumed_at.is_(None),
                    AuthenticationActionToken.expires_at > now,
                )
            )
            .values(consumed_at=now)
            .returning(AuthenticationActionToken)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one_or_none()

    async def invalidate_pending_tokens_for_user(self, user_id: uuid.UUID, purpose: str) -> int:
        """Invalidate previously issued unconsumed tokens of a specific purpose."""
        now = datetime.now(UTC)
        stmt = (
            update(AuthenticationActionToken)
            .where(
                and_(
                    AuthenticationActionToken.user_id == user_id,
                    AuthenticationActionToken.purpose == purpose,
                    AuthenticationActionToken.consumed_at.is_(None),
                )
            )
            .values(consumed_at=now)
            .returning(AuthenticationActionToken.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return len(list(res.scalars().all()))
