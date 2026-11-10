import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.mfa import MfaCredential, MfaLoginChallenge, MfaRecoveryCode


class MfaRepository:
    """Repository handling TOTP credentials, login challenges, and recovery codes."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- TOTP Credentials ---

    async def get_credential_by_user_id(self, user_id: uuid.UUID) -> MfaCredential | None:
        stmt = (
            select(MfaCredential)
            .options(selectinload(MfaCredential.user))
            .where(MfaCredential.user_id == user_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def save_credential(self, credential: MfaCredential) -> MfaCredential:
        self.session.add(credential)
        await self.session.flush()
        return credential

    async def delete_credential(self, user_id: uuid.UUID) -> bool:
        stmt = (
            delete(MfaCredential)
            .where(MfaCredential.user_id == user_id)
            .returning(MfaCredential.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.scalar_one_or_none() is not None

    async def update_last_accepted_step(self, user_id: uuid.UUID, step: int) -> bool:
        """Atomically advance last_accepted_step using conditional compare-and-swap.

        Ensures step is strictly greater than previously recorded accepted step.
        Returns True if step was successfully advanced; False if step was already consumed/replayed.
        """
        stmt = (
            update(MfaCredential)
            .where(
                and_(
                    MfaCredential.user_id == user_id,
                    or_(
                        MfaCredential.last_accepted_step.is_(None),
                        MfaCredential.last_accepted_step < step,
                    ),
                )
            )
            .values(last_accepted_step=step)
            .returning(MfaCredential.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.scalar_one_or_none() is not None

    # --- MFA Login Challenges ---

    async def get_challenge_by_token_hash(
        self, challenge_token_hash: str
    ) -> MfaLoginChallenge | None:
        stmt = (
            select(MfaLoginChallenge)
            .options(selectinload(MfaLoginChallenge.user))
            .where(MfaLoginChallenge.challenge_token_hash == challenge_token_hash)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_challenge(self, challenge: MfaLoginChallenge) -> MfaLoginChallenge:
        self.session.add(challenge)
        await self.session.flush()
        return challenge

    async def consume_challenge(self, challenge_id: uuid.UUID) -> bool:
        stmt = (
            update(MfaLoginChallenge)
            .where(
                and_(
                    MfaLoginChallenge.id == challenge_id,
                    MfaLoginChallenge.consumed_at.is_(None),
                )
            )
            .values(consumed_at=datetime.now(UTC))
            .returning(MfaLoginChallenge.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.scalar_one_or_none() is not None

    async def increment_challenge_attempts(self, challenge_id: uuid.UUID) -> int:
        stmt = (
            update(MfaLoginChallenge)
            .where(MfaLoginChallenge.id == challenge_id)
            .values(attempts_count=MfaLoginChallenge.attempts_count + 1)
            .returning(MfaLoginChallenge.attempts_count)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one()

    # --- Recovery Codes ---

    async def replace_recovery_codes(
        self, user_id: uuid.UUID, hashed_codes: list[str]
    ) -> list[MfaRecoveryCode]:
        # Invalidate / delete previous recovery codes
        await self.session.execute(
            delete(MfaRecoveryCode).where(MfaRecoveryCode.user_id == user_id)
        )

        new_records: list[MfaRecoveryCode] = []
        for h_code in hashed_codes:
            rec = MfaRecoveryCode(user_id=user_id, code_hash=h_code)
            self.session.add(rec)
            new_records.append(rec)

        await self.session.flush()
        return new_records

    async def consume_recovery_code_atomically(self, user_id: uuid.UUID, code_hash: str) -> bool:
        now = datetime.now(UTC)
        stmt = (
            update(MfaRecoveryCode)
            .where(
                and_(
                    MfaRecoveryCode.user_id == user_id,
                    MfaRecoveryCode.code_hash == code_hash,
                    MfaRecoveryCode.consumed_at.is_(None),
                )
            )
            .values(consumed_at=now)
            .returning(MfaRecoveryCode.id)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return result.scalar_one_or_none() is not None
