import asyncio
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.exceptions import (
    EntityNotFoundError,
)
from app.db.transaction import transaction
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.organization import (
    OrganizationCreate,
    ProvisionOrganizationInput,
)
from app.schemas.user import UserCreate, UserRead
from app.schemas.workspace import WorkspaceCreate
from app.services.organization import OrganizationService
from app.services.user import UserService
from app.services.workspace import WorkspaceService


@pytest.mark.asyncio
async def test_atomic_organization_provisioning_success(db_session: AsyncSession, unique_id: str):
    """Integration test: verifies atomic multi-step organization provisioning."""
    service = OrganizationService(db_session)
    payload = ProvisionOrganizationInput(
        organization_name=f"Enterprise {unique_id}",
        organization_slug=f"ent-{unique_id}",
        user_email=f"owner_{unique_id}@enterprise.com",
        user_display_name="Enterprise Owner",
        default_workspace_name="Executive Workspace",
        default_workspace_slug="executive",
    )

    org_read, user_read, ws_read = await service.provision_organization_atomic(payload)

    assert org_read.slug == f"ent-{unique_id}"
    assert user_read.normalized_email == f"owner_{unique_id}@enterprise.com"
    assert ws_read.slug == "executive"
    assert ws_read.organization_id == org_read.id

    # Verify entities exist in database
    db_org = await db_session.get(Organization, org_read.id)
    assert db_org is not None

    db_user = await db_session.get(User, user_read.id)
    assert db_user is not None

    db_ws = await db_session.get(Workspace, ws_read.id)
    assert db_ws is not None
    assert db_ws.organization_id == db_org.id


@pytest.mark.asyncio
async def test_atomic_organization_provisioning_rollback_on_failure(
    db_session: AsyncSession, unique_id: str
):
    """Integration test: verifies complete transaction rollback if default workspace creation fails.

    Specifically asserts that when an error occurs after creating organization, user,
    and membership, but during workspace creation, ZERO records are committed or leaked
    in ANY of the four database tables:
    1. organizations
    2. users
    3. organization_memberships
    4. workspaces
    """
    service = OrganizationService(db_session)
    org_slug = f"fail-org-{unique_id}"
    user_email = f"fail_user_{unique_id}@test.com"
    ws_slug = f"failing-ws-{unique_id}"

    payload = ProvisionOrganizationInput(
        organization_name="Failing Organization",
        organization_slug=org_slug,
        user_email=user_email,
        user_display_name="Fail User",
        default_workspace_name="Failing Workspace",
        default_workspace_slug=ws_slug,
    )

    # Simulate an unexpected operational failure in workspace creation (step 4)
    with patch.object(
        service.workspace_repo,
        "create",
        side_effect=RuntimeError("Simulated database failure during workspace insertion"),
    ):
        with pytest.raises(RuntimeError, match="Simulated database failure"):
            await service.provision_organization_atomic(payload)

    # 1. Direct PostgreSQL query: organizations table
    stmt_org = select(Organization).where(Organization.slug == org_slug)
    result_org = await db_session.execute(stmt_org)
    assert result_org.scalar_one_or_none() is None

    # 2. Direct PostgreSQL query: users table
    stmt_user = select(User).where(User.normalized_email == user_email.lower())
    result_user = await db_session.execute(stmt_user)
    assert result_user.scalar_one_or_none() is None

    # 3. Direct PostgreSQL query: organization_memberships table
    # Query all memberships joined to either this org slug or user email
    stmt_membership = (
        select(OrganizationMembership)
        .join(Organization, OrganizationMembership.organization_id == Organization.id)
        .where(Organization.slug == org_slug)
    )
    result_membership = await db_session.execute(stmt_membership)
    assert len(result_membership.scalars().all()) == 0

    # 4. Direct PostgreSQL query: workspaces table
    stmt_ws = select(Workspace).where(Workspace.slug == ws_slug)
    result_ws = await db_session.execute(stmt_ws)
    assert len(result_ws.scalars().all()) == 0


@pytest.mark.asyncio
async def test_workspace_service_cross_tenant_isolation(db_session: AsyncSession, unique_id: str):
    """Integration test: proves workspaces cannot leak or be accessed across tenant boundaries.

    Verifies the secure 404 policy: requesting a workspace owned by another tenant
    returns EntityNotFoundError (404) identically to a non-existent ID, preventing
    sensitive resource enumeration probing.
    """
    org_service = OrganizationService(db_session)
    ws_service = WorkspaceService(db_session)

    # Provision two separate organizations
    org1, _, _ = await org_service.provision_organization_atomic(
        ProvisionOrganizationInput(
            organization_name="Tenant Alpha",
            organization_slug=f"alpha-{unique_id}",
            user_email=f"alpha_{unique_id}@test.com",
            user_display_name="Alpha Admin",
        )
    )
    org2, _, _ = await org_service.provision_organization_atomic(
        ProvisionOrganizationInput(
            organization_name="Tenant Beta",
            organization_slug=f"beta-{unique_id}",
            user_email=f"beta_{unique_id}@test.com",
            user_display_name="Beta Admin",
        )
    )

    # Create workspace under Tenant Alpha
    alpha_ws = await ws_service.create_workspace(
        org1.id, WorkspaceCreate(name="Alpha Financials", slug="financials")
    )

    # 1. Tenant Alpha can access its own workspace
    retrieved = await ws_service.get_workspace(alpha_ws.id, organization_id=org1.id)
    assert retrieved.id == alpha_ws.id

    # 2. Tenant Beta attempting to access Tenant Alpha's workspace raises EntityNotFoundError (safe 404)
    with pytest.raises(EntityNotFoundError) as exc_info:
        await ws_service.get_workspace(alpha_ws.id, organization_id=org2.id)

    assert exc_info.value.code == "ENTITY_NOT_FOUND"
    assert str(alpha_ws.id) in exc_info.value.message

    # 3. Non-existent workspace raises EntityNotFoundError with identical structure
    with pytest.raises(EntityNotFoundError):
        await ws_service.get_workspace(uuid.uuid4(), organization_id=org1.id)

    # 4. Workspace listings do not leak cross-tenant records
    alpha_workspaces = await ws_service.list_workspaces(org1.id)
    assert any(w.id == alpha_ws.id for w in alpha_workspaces)

    beta_workspaces = await ws_service.list_workspaces(org2.id)
    assert not any(w.id == alpha_ws.id for w in beta_workspaces)


@pytest.mark.asyncio
async def test_user_service_get_or_create(db_session: AsyncSession, unique_id: str):
    """Integration test: verifies get_or_create_user handles both new and existing users."""
    user_service = UserService(db_session)
    email = f"shared_{unique_id}@test.com"

    # First call: creates user
    u1, created1 = await user_service.get_or_create_user(
        UserCreate(email=email.upper(), display_name="Shared User")
    )
    assert created1 is True
    assert u1.normalized_email == email.lower()

    # Second call: returns existing user
    u2, created2 = await user_service.get_or_create_user(
        UserCreate(email=f"  {email}  ", display_name="Different Display Name")
    )
    assert created2 is False
    assert u2.id == u1.id


@pytest.mark.asyncio
async def test_user_service_get_or_create_deterministic_collision_recovery(
    db_session: AsyncSession, unique_id: str
):
    """Deterministic recovery test: pre-inserts a colliding record and mocks the lookup

    to return None, forcing a real PostgreSQL IntegrityError on ix_users_normalized_email.
    Verifies that get_or_create_user recovers cleanly without corrupting the session.
    """
    user_service = UserService(db_session)
    email = f"deterministic_{unique_id}@enterprise.com"
    normalized = email.lower()

    # Pre-insert user into database directly to simulate a concurrent transaction committing first
    concurrent_user = User(
        email=email,
        normalized_email=normalized,
        display_name="Pre-existing User",
    )
    async with transaction(db_session):
        await user_service.repo.create(concurrent_user)

    # Now simulate the race condition: patch the initial lookup to return None
    # as if the user was not found at the instant get_or_create_user was called.
    original_lookup = user_service.repo.get_by_normalized_email
    lookup_count = 0

    async def mocked_lookup(norm_email: str):
        nonlocal lookup_count
        lookup_count += 1
        if lookup_count == 1:
            # First lookup: race condition simulation (saw None)
            return None
        # Subsequent lookup: calls real DB lookup and retrieves the inserted record
        return await original_lookup(norm_email)

    with patch.object(user_service.repo, "get_by_normalized_email", side_effect=mocked_lookup):
        user_read, created = await user_service.get_or_create_user(
            UserCreate(email=email, display_name="Race Condition Caller")
        )

    assert created is False
    assert user_read.id == concurrent_user.id
    assert user_read.normalized_email == normalized


@pytest.mark.asyncio
async def test_get_or_create_user_persistence_after_autobegin(
    session_factory: async_sessionmaker[AsyncSession], unique_id: str
):
    """Critical persistence test: verifies that an operation following an autobegun SELECT

    properly commits its changes to PostgreSQL when the session closes.

    Execution sequence:
    1. Create a fresh AsyncSession.
    2. Execute a SELECT to deliberately trigger SQLAlchemy autobegin.
    3. Verify session.in_transaction() is True.
    4. Call UserService.get_or_create_user() with a new normalized email.
    5. Verify the result reports created=True.
    6. Close the session according to the actual application session lifecycle.
    7. Open an entirely new AsyncSession.
    8. Query the user by normalized email.
    9. Verify the user exists in PostgreSQL.
    """
    target_email = f"autobegin_persist_{unique_id}@enterprise.com"
    normalized_target = target_email.lower()

    # 1. Create a fresh AsyncSession
    async with session_factory() as session:
        # 2. Execute a SELECT to deliberately trigger SQLAlchemy autobegin
        stmt = select(User).limit(1)
        await session.execute(stmt)

        # 3. Verify session.in_transaction() is True
        assert session.in_transaction() is True

        # 4. Call UserService.get_or_create_user() with a new normalized email
        user_service = UserService(session)
        user_read, created = await user_service.get_or_create_user(
            UserCreate(email=target_email, display_name="Autobegin Persisted User")
        )

        # 5. Verify the result reports created=True
        assert created is True
        assert user_read.normalized_email == normalized_target
        created_user_id = user_read.id

        # 6. Close the session according to actual application lifecycle (exit async with block)

    # 7. Open an entirely new AsyncSession
    async with session_factory() as verify_session:
        # 8. Query the user by normalized email
        query_stmt = select(User).where(User.normalized_email == normalized_target)
        result = await verify_session.execute(query_stmt)
        persisted_user = result.scalar_one_or_none()

        # 9. Verify the user exists in PostgreSQL
        assert persisted_user is not None
        assert persisted_user.id == created_user_id
        assert persisted_user.normalized_email == normalized_target


@pytest.mark.asyncio
async def test_create_organization_persistence_after_autobegin(
    session_factory: async_sessionmaker[AsyncSession], unique_id: str
):
    """Persistence test: verifies OrganizationService.create_organization commits

    cleanly even after prior SELECT queries have triggered SQLAlchemy autobegin.
    """
    org_slug = f"autobegin-org-{unique_id}"

    async with session_factory() as session:
        # Deliberately trigger autobegin
        await session.execute(select(Organization).limit(1))
        assert session.in_transaction() is True

        org_service = OrganizationService(session)
        org_read = await org_service.create_organization(
            OrganizationCreate(name=f"Autobegin Org {unique_id}", slug=org_slug)
        )
        created_org_id = org_read.id

    # Verify in new independent session
    async with session_factory() as verify_session:
        stmt = select(Organization).where(Organization.slug == org_slug)
        persisted_org = (await verify_session.execute(stmt)).scalar_one_or_none()
        assert persisted_org is not None
        assert persisted_org.id == created_org_id


@pytest.mark.asyncio
async def test_create_workspace_persistence_after_autobegin(
    session_factory: async_sessionmaker[AsyncSession], unique_id: str
):
    """Persistence test: verifies WorkspaceService.create_workspace commits

    cleanly even after prior SELECT queries have triggered SQLAlchemy autobegin.
    """
    org_slug = f"ws-org-{unique_id}"
    ws_slug = f"ws-{unique_id}"

    # Setup parent organization in its own session
    async with session_factory() as setup_session:
        org_service = OrganizationService(setup_session)
        org_read = await org_service.create_organization(
            OrganizationCreate(name="Parent Org", slug=org_slug)
        )
        parent_org_id = org_read.id

    # Test workspace creation after autobegin
    async with session_factory() as session:
        await session.execute(select(Workspace).limit(1))
        assert session.in_transaction() is True

        ws_service = WorkspaceService(session)
        ws_read = await ws_service.create_workspace(
            parent_org_id, WorkspaceCreate(name="Autobegin WS", slug=ws_slug)
        )
        created_ws_id = ws_read.id

    # Verify in new independent session
    async with session_factory() as verify_session:
        stmt = select(Workspace).where(Workspace.id == created_ws_id)
        persisted_ws = (await verify_session.execute(stmt)).scalar_one_or_none()
        assert persisted_ws is not None
        assert persisted_ws.slug == ws_slug
        assert persisted_ws.organization_id == parent_org_id


@pytest.mark.asyncio
async def test_user_service_get_or_create_real_concurrency(
    session_factory: async_sessionmaker[AsyncSession], unique_id: str
):
    """Genuine concurrent PostgreSQL test:

    - Opens two independent AsyncSession instances.
    - Starts two independent get_or_create_user operations for the same normalized email.
    - Synchronizes their initial database lookups with asyncio events so BOTH observe
      that the user does not yet exist.
    - Allows both operations to attempt insertion concurrently against PostgreSQL.
    - Lets PostgreSQL enforce the unique constraint (ix_users_normalized_email).
    - Verifies the losing operation catches IntegrityError, recovers via savepoint rollback,
      and retrieves the committed user.

    Expected Results:
    - Exactly one operation returns created=True.
    - Exactly one operation returns created=False.
    - Exactly one user row exists for that normalized email.
    - Neither session is left in a failed transaction state.
    - Both operations return the exact same persisted user ID.
    """
    target_email = f"real_concurrent_{unique_id}@enterprise.com"
    normalized_target = target_email.lower()

    lookup1_done = asyncio.Event()
    lookup2_done = asyncio.Event()

    result1: tuple[UserRead, bool] | None = None
    result2: tuple[UserRead, bool] | None = None
    err1: Exception | None = None
    err2: Exception | None = None

    async def worker1():
        nonlocal result1, err1
        async with session_factory() as session1:
            service1 = UserService(session1)
            orig_lookup1 = service1.repo.get_by_normalized_email
            first_call = True

            async def synced_lookup(em: str):
                nonlocal first_call
                res = await orig_lookup1(em)
                if first_call:
                    first_call = False
                    # Signal that worker 1 has observed None, wait for worker 2
                    lookup1_done.set()
                    await lookup2_done.wait()
                return res

            service1.repo.get_by_normalized_email = synced_lookup
            try:
                result1 = await service1.get_or_create_user(
                    UserCreate(email=target_email, display_name="Concurrent Worker 1")
                )
            except Exception as exc:
                err1 = exc

    async def worker2():
        nonlocal result2, err2
        async with session_factory() as session2:
            service2 = UserService(session2)
            orig_lookup2 = service2.repo.get_by_normalized_email
            first_call = True

            async def synced_lookup(em: str):
                nonlocal first_call
                res = await orig_lookup2(em)
                if first_call:
                    first_call = False
                    # Signal that worker 2 has observed None, wait for worker 1
                    lookup2_done.set()
                    await lookup1_done.wait()
                return res

            service2.repo.get_by_normalized_email = synced_lookup
            try:
                result2 = await service2.get_or_create_user(
                    UserCreate(email=target_email, display_name="Concurrent Worker 2")
                )
            except Exception as exc:
                err2 = exc

    # Execute both concurrent workers simultaneously
    await asyncio.gather(worker1(), worker2())

    assert err1 is None, f"Worker 1 raised unexpected exception: {err1}"
    assert err2 is None, f"Worker 2 raised unexpected exception: {err2}"
    assert result1 is not None
    assert result2 is not None

    user1, created1 = result1
    user2, created2 = result2

    # Exactly one operation returns created=True, and exactly one returns created=False
    assert (created1 is True and created2 is False) or (created1 is False and created2 is True)

    # Both operations return the same persisted user ID
    assert user1.id == user2.id
    assert user1.normalized_email == normalized_target
    assert user2.normalized_email == normalized_target

    # Verify exactly one user row exists in PostgreSQL
    async with session_factory() as verify_session:
        stmt = select(User).where(User.normalized_email == normalized_target)
        users = (await verify_session.execute(stmt)).scalars().all()
        assert len(users) == 1
        assert users[0].id == user1.id
