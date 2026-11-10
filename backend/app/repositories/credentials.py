import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.credentials import UserCredentials


class CredentialsRepository:
    """Repository managing password hash credentials (no independent commits)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_user_id(self, user_id: uuid.UUID) -> UserCredentials | None:
        stmt = select(UserCredentials).where(UserCredentials.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(self, credentials: UserCredentials) -> UserCredentials:
        self.session.add(credentials)
        await self.session.flush()
        return credentials

    async def update_password(self, user_id: uuid.UUID, new_hash: str) -> bool:
        stmt = (
            update(UserCredentials)
            .where(UserCredentials.user_id == user_id)
            .values(
                password_hash=new_hash,
                password_changed_at=datetime.now(UTC),
            )
            .returning(UserCredentials.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.scalar_one_or_none() is not None

    async def upgrade_password_hash_if_matches(
        self, user_id: uuid.UUID, expected_old_hash: str, new_hash: str
    ) -> bool:
        """Atomically upgrade password hash only if it has not been changed concurrently."""
        stmt = (
            update(UserCredentials)
            .where(
                and_(
                    UserCredentials.user_id == user_id,
                    UserCredentials.password_hash == expected_old_hash,
                )
            )
            .values(
                password_hash=new_hash,
                password_changed_at=datetime.now(UTC),
            )
            .returning(UserCredentials.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.scalar_one_or_none() is not None
