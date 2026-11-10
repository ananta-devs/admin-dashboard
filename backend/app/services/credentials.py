import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AuthenticationError, EntityNotFoundError
from app.core.security import (
    check_needs_rehash,
    hash_password,
    validate_password_policy,
    verify_dummy_password,
    verify_password,
)
from app.db.transaction import transaction
from app.models.credentials import UserCredentials
from app.models.user import User
from app.repositories.credentials import CredentialsRepository
from app.repositories.user import UserRepository
from app.schemas.user import normalize_email_address


class CredentialsService:
    """Service handling password validation, hashing, and credential lifecycle."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = CredentialsRepository(session)
        self.user_repo = UserRepository(session)

    async def authenticate(self, email: str, password: str) -> User | None:
        """Authenticate user by email and password in constant time, defending against timing enumeration."""
        normalized = normalize_email_address(email)
        user = await self.user_repo.get_by_normalized_email(normalized)
        if not user:
            verify_dummy_password(password)
            return None

        is_valid = await self.verify_user_credentials(user.id, password)
        if not is_valid:
            return None

        return user

    async def update_password(self, user_id: uuid.UUID, new_password: str) -> bool:
        """Update user password adhering to enterprise password policy."""
        return await self.set_password_direct(user_id, new_password)

    async def create_credentials(self, user_id: uuid.UUID, password: str) -> UserCredentials:
        """Validate policy, hash password with Argon2id, and persist credential record."""
        validate_password_policy(password)
        pwd_hash = hash_password(password)

        credentials = UserCredentials(
            user_id=user_id,
            password_hash=pwd_hash,
        )
        async with transaction(self.session):
            await self.repo.create(credentials)

        return credentials

    async def verify_user_credentials(self, user_id: uuid.UUID | None, password: str) -> bool:
        """Authenticate user password in constant time, defending against timing enumeration."""
        if user_id is None:
            # Account does not exist: execute dummy verification to balance response timing
            verify_dummy_password(password)
            return False

        credentials = await self.repo.get_by_user_id(user_id)
        if not credentials:
            verify_dummy_password(password)
            return False

        is_valid = verify_password(password, credentials.password_hash)
        if is_valid and check_needs_rehash(credentials.password_hash):
            # Opportunistic safe rehash when cost parameters are upgraded
            new_hash = hash_password(password)
            async with transaction(self.session):
                await self.repo.upgrade_password_hash_if_matches(
                    user_id=user_id,
                    expected_old_hash=credentials.password_hash,
                    new_hash=new_hash,
                )

        return is_valid

    async def change_password(
        self, user_id: uuid.UUID, current_password: str, new_password: str
    ) -> bool:
        """Verify current password and update to new password adhering to security policy."""
        valid = await self.verify_user_credentials(user_id, current_password)
        if not valid:
            raise AuthenticationError("Current password verification failed.")

        validate_password_policy(new_password)
        new_hash = hash_password(new_password)

        async with transaction(self.session):
            updated = await self.repo.update_password(user_id, new_hash)

        if not updated:
            raise EntityNotFoundError("UserCredentials", str(user_id))

        return True

    async def set_password_direct(self, user_id: uuid.UUID, new_password: str) -> bool:
        """Directly set a new password (e.g. after password reset token verification)."""
        validate_password_policy(new_password)
        new_hash = hash_password(new_password)

        async with transaction(self.session):
            updated = await self.repo.update_password(user_id, new_hash)

        if not updated:
            raise EntityNotFoundError("UserCredentials", str(user_id))

        return True
