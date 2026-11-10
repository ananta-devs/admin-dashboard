import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import EntityAlreadyExistsError, EntityNotFoundError
from app.db.transaction import transaction
from app.models.user import User
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate, UserRead, normalize_email_address


class UserService:
    """Service handling User profile and identity records."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = UserRepository(session)

    async def create_user(self, data: UserCreate) -> UserRead:
        """Create a new user ensuring normalized email uniqueness."""
        normalized = normalize_email_address(data.email)
        existing = await self.repo.get_by_normalized_email(normalized)
        if existing:
            raise EntityAlreadyExistsError("User", "email", normalized)

        user = User(
            email=data.email.strip(),
            normalized_email=normalized,
            display_name=data.display_name.strip(),
        )
        try:
            async with transaction(self.session):
                await self.repo.create(user)
        except IntegrityError:
            raise EntityAlreadyExistsError("User", "email", normalized) from None

        return UserRead.model_validate(user)

    async def get_or_create_user(self, data: UserCreate) -> tuple[UserRead, bool]:
        """Retrieve existing user by normalized email or create a new user profile.

        Safely handles concurrent race conditions where two requests attempt to
        create the same normalized email address simultaneously.
        """
        normalized = normalize_email_address(data.email)
        existing = await self.repo.get_by_normalized_email(normalized)
        if existing:
            return UserRead.model_validate(existing), False

        user = User(
            email=data.email.strip(),
            normalized_email=normalized,
            display_name=data.display_name.strip(),
        )
        try:
            async with transaction(self.session):
                await self.repo.create(user)
            return UserRead.model_validate(user), True
        except IntegrityError:
            # Concurrent insert collision handled via savepoint rollback
            existing = await self.repo.get_by_normalized_email(normalized)
            if existing:
                return UserRead.model_validate(existing), False
            raise

    async def get_user_by_id(self, user_id: uuid.UUID) -> UserRead:
        """Retrieve user by UUID."""
        user = await self.repo.get_by_id(user_id)
        if not user:
            raise EntityNotFoundError("User", str(user_id))
        return UserRead.model_validate(user)

    async def get_user_by_email(self, email: str) -> UserRead:
        """Retrieve user by email address."""
        normalized = normalize_email_address(email)
        user = await self.repo.get_by_normalized_email(normalized)
        if not user:
            raise EntityNotFoundError("User", normalized)
        return UserRead.model_validate(user)
