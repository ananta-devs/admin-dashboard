import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import generate_opaque_token, hash_token
from app.models.action_token import AuthenticationActionToken
from app.repositories.action_token import ActionTokenRepository

PURPOSE_EMAIL_VERIFICATION = "email_verification"
PURPOSE_PASSWORD_RESET = "password_reset"


class ActionTokenService:
    """Service governing single-use cryptographic action tokens (Email Verification, Password Reset)."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = ActionTokenRepository(session)

    async def create_email_verification_token(self, user_id: uuid.UUID) -> str:
        """Invalidates older pending verification tokens and issues a new one."""
        await self.repo.invalidate_pending_tokens_for_user(user_id, PURPOSE_EMAIL_VERIFICATION)

        raw_token = generate_opaque_token()
        token_hash = hash_token(raw_token)
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=settings.EMAIL_VERIFICATION_TOKEN_LIFETIME_SECONDS)

        action_token = AuthenticationActionToken(
            user_id=user_id,
            purpose=PURPOSE_EMAIL_VERIFICATION,
            token_hash=token_hash,
            created_at=now,
            expires_at=expires_at,
        )
        await self.repo.create(action_token)
        return raw_token

    async def create_password_reset_token(self, user_id: uuid.UUID) -> str:
        """Invalidates older pending reset tokens and issues a new one."""
        await self.repo.invalidate_pending_tokens_for_user(user_id, PURPOSE_PASSWORD_RESET)

        raw_token = generate_opaque_token()
        token_hash = hash_token(raw_token)
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=settings.PASSWORD_RESET_TOKEN_LIFETIME_SECONDS)

        action_token = AuthenticationActionToken(
            user_id=user_id,
            purpose=PURPOSE_PASSWORD_RESET,
            token_hash=token_hash,
            created_at=now,
            expires_at=expires_at,
        )
        await self.repo.create(action_token)
        return raw_token

    async def consume_email_verification_token(
        self, raw_token: str
    ) -> AuthenticationActionToken | None:
        """Atomically validates and marks an email verification token as consumed."""
        if not raw_token:
            return None
        token_hash = hash_token(raw_token)
        return await self.repo.consume_atomically(token_hash, PURPOSE_EMAIL_VERIFICATION)

    async def consume_password_reset_token(
        self, raw_token: str
    ) -> AuthenticationActionToken | None:
        """Atomically validates and marks a password reset token as consumed."""
        if not raw_token:
            return None
        token_hash = hash_token(raw_token)
        return await self.repo.consume_atomically(token_hash, PURPOSE_PASSWORD_RESET)
