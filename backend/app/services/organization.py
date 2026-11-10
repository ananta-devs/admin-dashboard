import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import EntityAlreadyExistsError, EntityNotFoundError
from app.core.security import (
    generate_opaque_token,
    hash_password,
    hash_token,
    validate_password_policy,
)
from app.db.transaction import transaction
from app.models.action_token import AuthenticationActionToken
from app.models.credentials import UserCredentials
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.user import User
from app.models.workspace import Workspace
from app.repositories.action_token import ActionTokenRepository
from app.repositories.credentials import CredentialsRepository
from app.repositories.membership import MembershipRepository
from app.repositories.organization import OrganizationRepository
from app.repositories.role import RoleRepository
from app.repositories.user import UserRepository
from app.repositories.workspace import WorkspaceRepository
from app.schemas.organization import (
    OrganizationCreate,
    OrganizationRead,
    ProvisionOrganizationInput,
)
from app.schemas.user import UserRead, normalize_email_address
from app.schemas.workspace import WorkspaceRead
from app.services.role import RoleService


class OrganizationService:
    """Service orchestrating organization lifecycle and atomic tenant provisioning."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.org_repo = OrganizationRepository(session)
        self.user_repo = UserRepository(session)
        self.membership_repo = MembershipRepository(session)
        self.workspace_repo = WorkspaceRepository(session)
        self.credentials_repo = CredentialsRepository(session)
        self.action_token_repo = ActionTokenRepository(session)
        self.role_repo = RoleRepository(session)
        self.role_service = RoleService(session)

    async def create_organization(self, data: OrganizationCreate) -> OrganizationRead:
        """Create a standalone organization with unique slug."""
        existing = await self.org_repo.get_by_slug(data.slug)
        if existing:
            raise EntityAlreadyExistsError("Organization", "slug", data.slug)

        org = Organization(name=data.name.strip(), slug=data.slug.strip().lower())
        async with transaction(self.session):
            await self.org_repo.create(org)

        return OrganizationRead.model_validate(org)

    async def provision_organization_atomic(
        self, data: ProvisionOrganizationInput
    ) -> tuple[OrganizationRead, UserRead, WorkspaceRead]:
        """Atomically create organization, associate initial user, and create default workspace.

        All mutations execute in a single atomic database transaction. If any step fails,
        the entire transaction rolls back cleanly.
        """
        # Validate slug uniqueness before embarking on writes
        existing_org = await self.org_repo.get_by_slug(data.organization_slug)
        if existing_org:
            raise EntityAlreadyExistsError("Organization", "slug", data.organization_slug)

        async with transaction(self.session):
            # 1. Create Organization
            org = Organization(
                name=data.organization_name.strip(),
                slug=data.organization_slug.strip().lower(),
            )
            await self.org_repo.create(org)

            # 2. Find or create Owner User
            normalized_email = normalize_email_address(data.user_email)
            user = await self.user_repo.get_by_normalized_email(normalized_email)
            if not user:
                user = User(
                    email=data.user_email.strip(),
                    normalized_email=normalized_email,
                    display_name=data.user_display_name.strip(),
                )
                await self.user_repo.create(user)

            # 3. Create Organization Membership
            membership = OrganizationMembership(
                organization_id=org.id,
                user_id=user.id,
            )
            await self.membership_repo.create(membership)

            # 4. Bootstrap Default Roles & Assign Owner Role
            roles = await self.role_service.seed_default_roles_for_organization(org.id)
            if "owner" in roles:
                await self.role_repo.set_membership_roles(
                    membership.id, [roles["owner"].id], organization_id=org.id
                )

            # 5. Create Default Workspace
            workspace = Workspace(
                organization_id=org.id,
                name=data.default_workspace_name.strip(),
                slug=data.default_workspace_slug.strip().lower(),
            )
            await self.workspace_repo.create(workspace)

        return (
            OrganizationRead.model_validate(org),
            UserRead.model_validate(user),
            WorkspaceRead.model_validate(workspace),
        )

    async def register_user_and_organization_atomic(
        self,
        email: str,
        password: str,
        display_name: str,
        organization_name: str,
        organization_slug: str,
        default_workspace_name: str = "Default Workspace",
        default_workspace_slug: str = "default",
    ) -> tuple[UserRead, OrganizationRead, WorkspaceRead, str]:
        """Atomically register user, credentials, organization, membership, workspace, and verification token."""
        validate_password_policy(password)
        normalized_email = normalize_email_address(email)

        # Pre-check collisions before starting transaction
        existing_user = await self.user_repo.get_by_normalized_email(normalized_email)
        if existing_user:
            raise EntityAlreadyExistsError("User", "email", email)

        existing_org = await self.org_repo.get_by_slug(organization_slug.strip().lower())
        if existing_org:
            raise EntityAlreadyExistsError("Organization", "slug", organization_slug)

        pwd_hash = hash_password(password)
        raw_token = generate_opaque_token()
        token_hash = hash_token(raw_token)
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=settings.EMAIL_VERIFICATION_TOKEN_LIFETIME_SECONDS)

        async with transaction(self.session):
            # 1. Create Organization
            org = Organization(
                name=organization_name.strip(),
                slug=organization_slug.strip().lower(),
            )
            await self.org_repo.create(org)

            # 2. Create User
            user = User(
                email=email.strip(),
                normalized_email=normalized_email,
                display_name=display_name.strip(),
                email_verified_at=None,
            )
            await self.user_repo.create(user)

            # 3. Create UserCredentials (Argon2id)
            credentials = UserCredentials(
                user_id=user.id,
                password_hash=pwd_hash,
            )
            await self.credentials_repo.create(credentials)

            # 4. Create Organization Membership
            membership = OrganizationMembership(
                organization_id=org.id,
                user_id=user.id,
            )
            await self.membership_repo.create(membership)

            # 5. Bootstrap Default Roles & Assign Owner Role
            roles = await self.role_service.seed_default_roles_for_organization(org.id)
            if "owner" in roles:
                await self.role_repo.set_membership_roles(
                    membership.id, [roles["owner"].id], organization_id=org.id
                )

            # 6. Create Default Workspace
            workspace = Workspace(
                organization_id=org.id,
                name=default_workspace_name.strip(),
                slug=default_workspace_slug.strip().lower(),
            )
            await self.workspace_repo.create(workspace)

            # 6. Create Action Token (Email Verification)
            action_token = AuthenticationActionToken(
                user_id=user.id,
                purpose="email_verification",
                token_hash=token_hash,
                created_at=now,
                expires_at=expires_at,
            )
            await self.action_token_repo.create(action_token)

        return (
            UserRead.model_validate(user),
            OrganizationRead.model_validate(org),
            WorkspaceRead.model_validate(workspace),
            raw_token,
        )

    async def get_organization(self, org_id: uuid.UUID) -> OrganizationRead:
        """Retrieve organization by UUID."""
        org = await self.org_repo.get_by_id(org_id)
        if not org:
            raise EntityNotFoundError("Organization", str(org_id))
        return OrganizationRead.model_validate(org)

    async def get_organization_by_slug(self, slug: str) -> OrganizationRead:
        """Retrieve organization by slug."""
        org = await self.org_repo.get_by_slug(slug)
        if not org:
            raise EntityNotFoundError("Organization", slug)
        return OrganizationRead.model_validate(org)

    async def list_organizations(self, limit: int = 50, offset: int = 0) -> list[OrganizationRead]:
        """List organizations with pagination."""
        orgs = await self.org_repo.list_all(limit=limit, offset=offset)
        return [OrganizationRead.model_validate(o) for o in orgs]
