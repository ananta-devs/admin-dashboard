import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import (
    build_totp_uri,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_opaque_token,
    generate_recovery_codes,
    generate_totp_secret,
    hash_recovery_code,
    hash_token,
    verify_totp_code,
)
from app.models.mfa import MfaCredential, MfaLoginChallenge
from app.repositories.mfa import MfaRepository


class MfaService:
    """Service governing TOTP multi-factor authentication, recovery codes, and challenges."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = MfaRepository(session)

    async def get_credential(self, user_id: uuid.UUID) -> MfaCredential | None:
        return await self.repo.get_credential_by_user_id(user_id)

    async def is_mfa_enabled(self, user_id: uuid.UUID) -> bool:
        cred = await self.get_credential(user_id)
        return cred is not None and cred.is_enabled

    async def begin_totp_enrollment(self, user_id: uuid.UUID, user_email: str) -> tuple[str, str]:
        """Generate a new unconfirmed TOTP credential for the user.

        Returns:
            Tuple of (raw_base32_secret, otpauth_uri).
        """
        raw_secret = generate_totp_secret()
        encrypted_secret = encrypt_totp_secret(raw_secret)
        otpauth_uri = build_totp_uri(raw_secret, user_email)

        existing = await self.repo.get_credential_by_user_id(user_id)
        if existing:
            existing.encrypted_secret = encrypted_secret
            existing.encryption_key_version = settings.MFA_AES_KEY_VERSION
            existing.enabled_at = None
            existing.last_accepted_step = None
        else:
            cred = MfaCredential(
                user_id=user_id,
                encrypted_secret=encrypted_secret,
                encryption_key_version=settings.MFA_AES_KEY_VERSION,
                enabled_at=None,
            )
            await self.repo.save_credential(cred)

        return raw_secret, otpauth_uri

    async def confirm_totp_enrollment(self, user_id: uuid.UUID, code: str) -> list[str] | None:
        """Verify the first TOTP code and activate MFA, generating 10 recovery codes.

        Returns:
            List of 10 raw recovery codes, or None if code is invalid.
        """
        cred = await self.repo.get_credential_by_user_id(user_id)
        if not cred or cred.is_enabled:
            return None

        secret = decrypt_totp_secret(cred.encrypted_secret)
        is_valid, step = verify_totp_code(secret, code)
        if not is_valid:
            return None

        now = datetime.now(UTC)
        cred.enabled_at = now
        cred.last_accepted_step = step

        # Generate and store recovery codes
        raw_codes, hashed_codes = generate_recovery_codes()
        await self.repo.replace_recovery_codes(user_id, hashed_codes)

        return raw_codes

    async def disable_totp(self, user_id: uuid.UUID) -> bool:
        """Disable and remove TOTP credential and recovery codes for the user."""
        return await self.repo.delete_credential(user_id)

    async def regenerate_recovery_codes(self, user_id: uuid.UUID) -> list[str] | None:
        """Generate a fresh set of 10 recovery codes, replacing all prior ones."""
        cred = await self.repo.get_credential_by_user_id(user_id)
        if not cred or not cred.is_enabled:
            return None

        raw_codes, hashed_codes = generate_recovery_codes()
        await self.repo.replace_recovery_codes(user_id, hashed_codes)
        return raw_codes

    async def create_login_challenge(self, user_id: uuid.UUID) -> tuple[str, datetime]:
        """Create a short-lived login challenge requiring 2FA.

        Returns:
            Tuple of (raw_challenge_token, expires_at).
        """
        raw_token = generate_opaque_token()
        token_hash = hash_token(raw_token)
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=settings.MFA_CHALLENGE_LIFETIME_SECONDS)

        challenge = MfaLoginChallenge(
            user_id=user_id,
            challenge_token_hash=token_hash,
            created_at=now,
            expires_at=expires_at,
        )
        await self.repo.create_challenge(challenge)
        return raw_token, expires_at

    async def verify_challenge_totp(self, challenge_raw_token: str, code: str) -> uuid.UUID | None:
        """Verify TOTP code against challenge. Returns user_id if successful, else None."""
        if not challenge_raw_token or not code:
            return None

        token_hash = hash_token(challenge_raw_token)
        challenge = await self.repo.get_challenge_by_token_hash(token_hash)
        if not challenge or not challenge.is_valid:
            return None

        attempts = await self.repo.increment_challenge_attempts(challenge.id)
        if attempts > 5:
            await self.repo.consume_challenge(challenge.id)
            return None

        cred = await self.repo.get_credential_by_user_id(challenge.user_id)
        if not cred or not cred.is_enabled:
            return None

        secret = decrypt_totp_secret(cred.encrypted_secret)
        is_valid, step = verify_totp_code(secret, code, last_accepted_step=cred.last_accepted_step)
        if not is_valid or step is None:
            return None

        advanced = await self.repo.update_last_accepted_step(challenge.user_id, step)
        if not advanced:
            # Replay detected and rejected by atomic compare-and-swap
            return None

        await self.repo.consume_challenge(challenge.id)
        return challenge.user_id

    async def verify_user_totp(self, user_id: uuid.UUID, code: str) -> bool:
        """Verify user's TOTP code and atomically advance the accepted step counter."""
        cred = await self.repo.get_credential_by_user_id(user_id)
        if not cred or not cred.is_enabled:
            return False

        secret = decrypt_totp_secret(cred.encrypted_secret)
        is_valid, step = verify_totp_code(secret, code, last_accepted_step=cred.last_accepted_step)
        if not is_valid or step is None:
            return False

        return await self.repo.update_last_accepted_step(user_id, step)

    async def verify_and_consume_recovery_code(self, user_id: uuid.UUID, raw_code: str) -> bool:
        """Atomically verify and consume a single-use backup recovery code."""
        h_code = hash_recovery_code(raw_code)
        return await self.repo.consume_recovery_code_atomically(user_id, h_code)

    async def verify_challenge_recovery_code(
        self, challenge_raw_token: str, raw_recovery_code: str
    ) -> uuid.UUID | None:
        """Verify and atomically consume a backup recovery code against challenge."""
        if not challenge_raw_token or not raw_recovery_code:
            return None

        token_hash = hash_token(challenge_raw_token)
        challenge = await self.repo.get_challenge_by_token_hash(token_hash)
        if not challenge or not challenge.is_valid:
            return None

        attempts = await self.repo.increment_challenge_attempts(challenge.id)
        if attempts > 5:
            await self.repo.consume_challenge(challenge.id)
            return None

        h_code = hash_recovery_code(raw_recovery_code)
        consumed = await self.repo.consume_recovery_code_atomically(challenge.user_id, h_code)
        if not consumed:
            return None

        await self.repo.consume_challenge(challenge.id)
        return challenge.user_id
