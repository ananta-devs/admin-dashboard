import asyncio
import uuid
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.exceptions import ForbiddenError, ValidationError
from app.db.base import utc_now
from app.db.session import get_db_session
from app.db.transaction import transaction
from app.main import app
from app.models.audit import AdministrativeAuditEvent
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.role import OrganizationRole
from app.models.user import User
from app.repositories.membership import MembershipRepository
from app.repositories.organization import OrganizationRepository
from app.repositories.role import RoleRepository
from app.repositories.user import UserRepository
from app.schemas.organization import ProvisionOrganizationInput
from app.schemas.user import normalize_email_address
from app.services.audit import AuditService
from app.services.organization import OrganizationService
from app.services.ownership import OwnershipService
from app.services.permission import PermissionService
from app.services.role import RoleService


@pytest.fixture
async def api_client():
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def get_test_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = get_test_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.fixture
async def db_session():
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await test_engine.dispose()


async def register_and_login_helper(
    client: AsyncClient,
    email: str,
    password: str = "SecurePass1234!",
    org_name: str | None = None,
    org_slug: str | None = None,
) -> tuple[dict[str, Any], str]:
    uid = uuid.uuid4().hex[:8]
    if not org_name:
        org_name = f"Org {uid}"
    if not org_slug:
        org_slug = f"org-{uid}"

    reg_payload = {
        "email": email,
        "password": password,
        "display_name": f"User {uid}",
        "organization_name": org_name,
        "organization_slug": org_slug,
    }
    reg_res = await client.post("/api/v1/auth/register", json=reg_payload)
    assert reg_res.status_code == 201

    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": "http://localhost:3000"},
    )
    assert login_res.status_code == 200
    data: dict[str, Any] = login_res.json()
    csrf_token: str = str(data.get("csrf_token") or login_res.cookies.get("csrf_token") or "")
    return data, csrf_token


# ===========================================================================
# 1. DATABASE-ENFORCED TENANT CONSISTENCY
# ===========================================================================


@pytest.mark.asyncio
async def test_composite_foreign_key_cross_tenant_membership_role_rejected(
    db_session: AsyncSession,
):
    """Direct PostgreSQL check: inserting MembershipRole across organizations is rejected by composite FK."""
    org_repo = OrganizationRepository(db_session)
    user_repo = UserRepository(db_session)
    membership_repo = MembershipRepository(db_session)
    role_repo = RoleRepository(db_session)

    uid = uuid.uuid4().hex[:8]

    # Create Org A
    org_a = Organization(name=f"Org A {uid}", slug=f"org-a-{uid}")
    await org_repo.create(org_a)
    user_a = User(
        email=f"usera_{uid}@example.com",
        normalized_email=f"usera_{uid}@example.com",
        display_name="User A",
        email_verified_at=utc_now(),
    )
    await user_repo.create(user_a)
    mem_a = OrganizationMembership(organization_id=org_a.id, user_id=user_a.id)
    await membership_repo.create(mem_a)
    role_a = OrganizationRole(
        organization_id=org_a.id, name=f"Role A {uid}", slug=f"role-a-{uid}", is_system=False
    )
    await role_repo.create(role_a)

    # Create Org B
    org_b = Organization(name=f"Org B {uid}", slug=f"org-b-{uid}")
    await org_repo.create(org_b)
    user_b = User(
        email=f"userb_{uid}@example.com",
        normalized_email=f"userb_{uid}@example.com",
        display_name="User B",
        email_verified_at=utc_now(),
    )
    await user_repo.create(user_b)
    mem_b = OrganizationMembership(organization_id=org_b.id, user_id=user_b.id)
    await membership_repo.create(mem_b)
    role_b = OrganizationRole(
        organization_id=org_b.id, name=f"Role B {uid}", slug=f"role-b-{uid}", is_system=False
    )
    await role_repo.create(role_b)

    await db_session.commit()

    # Capture primitive UUIDs so session expiration does not trigger lazy loading
    mem_a_id = mem_a.id
    role_b_id = role_b.id
    org_a_id = org_a.id
    org_b_id = org_b.id

    # Attempt 1: Direct SQL inserting MembershipRole with membership_a (Org A) but organization_id of Org B
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO membership_roles (membership_id, role_id, organization_id, assigned_at) "
                "VALUES (:m_id, :r_id, :org_id, NOW())"
            ),
            {"m_id": mem_a_id, "r_id": role_b_id, "org_id": org_b_id},
        )
        await db_session.commit()

    await db_session.rollback()

    # Attempt 2: Direct SQL inserting MembershipRole with membership_a (Org A) and role_b (Org B) claiming Org A
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO membership_roles (membership_id, role_id, organization_id, assigned_at) "
                "VALUES (:m_id, :r_id, :org_id, NOW())"
            ),
            {"m_id": mem_a_id, "r_id": role_b_id, "org_id": org_a_id},
        )
        await db_session.commit()

    await db_session.rollback()


@pytest.mark.asyncio
async def test_composite_foreign_key_cross_tenant_invitation_role_rejected(
    db_session: AsyncSession,
):
    """Direct PostgreSQL check: inserting OrganizationInvitation with role from another org is rejected."""
    org_repo = OrganizationRepository(db_session)
    user_repo = UserRepository(db_session)
    role_repo = RoleRepository(db_session)

    uid = uuid.uuid4().hex[:8]

    # Create Org A and User A
    org_a = Organization(name=f"Org A Inv {uid}", slug=f"org-a-inv-{uid}")
    await org_repo.create(org_a)
    user_a = User(
        email=f"inv_a_{uid}@example.com",
        normalized_email=f"inv_a_{uid}@example.com",
        display_name="User Inv A",
        email_verified_at=utc_now(),
    )
    await user_repo.create(user_a)

    # Create Org B and Role B
    org_b = Organization(name=f"Org B Inv {uid}", slug=f"org-b-inv-{uid}")
    await org_repo.create(org_b)
    role_b = OrganizationRole(
        organization_id=org_b.id,
        name=f"Role B Inv {uid}",
        slug=f"role-b-inv-{uid}",
        is_system=False,
    )
    await role_repo.create(role_b)

    await db_session.commit()

    org_a_id = org_a.id
    role_b_id = role_b.id
    user_a_id = user_a.id

    # Attempt inserting invitation for Org A referencing Role B
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO organization_invitations "
                "(id, organization_id, email, normalized_email, role_id, token_hash, invited_by_user_id, created_at, expires_at) "
                "VALUES (:id, :org_id, :email, :norm_email, :role_id, :token_hash, :inv_by, NOW(), NOW() + INTERVAL '7 days')"
            ),
            {
                "id": uuid.uuid4(),
                "org_id": org_a_id,
                "email": "invitee@example.com",
                "norm_email": "invitee@example.com",
                "role_id": role_b_id,  # From Org B!
                "token_hash": uuid.uuid4().hex,
                "inv_by": user_a_id,
            },
        )
        await db_session.commit()

    await db_session.rollback()


# ===========================================================================
# 2. PERMISSION QUERY SCOPING ACROSS ORGANIZATIONS
# ===========================================================================


@pytest.mark.asyncio
async def test_permission_scoping_strict_isolation(db_session: AsyncSession):
    """Verify effective permissions strictly respect organization_id and cannot leak."""
    org_repo = OrganizationRepository(db_session)
    user_repo = UserRepository(db_session)
    membership_repo = MembershipRepository(db_session)
    role_repo = RoleRepository(db_session)
    perm_service = PermissionService(db_session)

    uid = uuid.uuid4().hex[:8]

    # Create Org A with Role "custom-auditor" having "audit.read"
    org_a = Organization(name=f"Scope A {uid}", slug=f"scope-a-{uid}")
    await org_repo.create(org_a)
    user_a = User(
        email=f"scope_a_{uid}@example.com",
        normalized_email=f"scope_a_{uid}@example.com",
        display_name="User Scope A",
        email_verified_at=utc_now(),
    )
    await user_repo.create(user_a)
    mem_a = OrganizationMembership(organization_id=org_a.id, user_id=user_a.id)
    await membership_repo.create(mem_a)

    role_a = OrganizationRole(
        organization_id=org_a.id, name="Auditor", slug="auditor", is_system=False
    )
    await role_repo.create(role_a)
    perm_audit = await perm_service.repo.get_by_code("audit.read")
    assert perm_audit is not None
    await role_repo.set_role_permissions(role_a.id, [perm_audit.id])
    await role_repo.set_membership_roles(mem_a.id, [role_a.id], organization_id=org_a.id)

    # Create Org B with same role name "Auditor" having "workspaces.delete"
    org_b = Organization(name=f"Scope B {uid}", slug=f"scope-b-{uid}")
    await org_repo.create(org_b)
    user_b = User(
        email=f"scope_b_{uid}@example.com",
        normalized_email=f"scope_b_{uid}@example.com",
        display_name="User Scope B",
        email_verified_at=utc_now(),
    )
    await user_repo.create(user_b)
    mem_b = OrganizationMembership(organization_id=org_b.id, user_id=user_b.id)
    await membership_repo.create(mem_b)

    role_b = OrganizationRole(
        organization_id=org_b.id, name="Auditor", slug="auditor", is_system=False
    )
    await role_repo.create(role_b)
    perm_ws = await perm_service.repo.get_by_code("workspaces.delete")
    assert perm_ws is not None
    await role_repo.set_role_permissions(role_b.id, [perm_ws.id])
    await role_repo.set_membership_roles(mem_b.id, [role_b.id], organization_id=org_b.id)

    await db_session.commit()

    # Query Org A member within Org A
    perms_a = await perm_service.get_effective_permissions(mem_a.id, organization_id=org_a.id)
    assert perms_a == {"audit.read"}
    assert "workspaces.delete" not in perms_a

    # Query Org B member within Org B
    perms_b = await perm_service.get_effective_permissions(mem_b.id, organization_id=org_b.id)
    assert perms_b == {"workspaces.delete"}
    assert "audit.read" not in perms_b

    # Cross-tenant query attempt: Query Org A member passing Org B ID -> must return empty set
    cross_perms = await perm_service.get_effective_permissions(mem_a.id, organization_id=org_b.id)
    assert cross_perms == set()


# ===========================================================================
# 3. LAST-OWNER CONCURRENCY & SERIALIZATION (ROW LOCK VERIFICATION)
# ===========================================================================


@pytest.mark.asyncio
async def test_concurrent_last_owner_demotion_serialized():
    """Verify that concurrent demotion requests of the last two owners serialize and one fails."""
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        org_repo = OrganizationRepository(session)
        user_repo = UserRepository(session)
        membership_repo = MembershipRepository(session)
        role_repo = RoleRepository(session)
        role_service = RoleService(session)

        uid = uuid.uuid4().hex[:8]
        org = Organization(name=f"Conc Demote Org {uid}", slug=f"conc-demote-{uid}")
        await org_repo.create(org)

        # Bootstrap roles
        await role_service.seed_default_roles_for_organization(org.id)
        owner_role = await role_repo.get_by_slug("owner", org.id)
        member_role = await role_repo.get_by_slug("member", org.id)
        assert owner_role is not None and member_role is not None

        # Create two owners
        u1 = User(
            email=f"owner1_{uid}@example.com",
            normalized_email=f"owner1_{uid}@example.com",
            display_name="Owner 1",
            email_verified_at=utc_now(),
        )
        u2 = User(
            email=f"owner2_{uid}@example.com",
            normalized_email=f"owner2_{uid}@example.com",
            display_name="Owner 2",
            email_verified_at=utc_now(),
        )
        await user_repo.create(u1)
        await user_repo.create(u2)

        m1 = OrganizationMembership(organization_id=org.id, user_id=u1.id)
        m2 = OrganizationMembership(organization_id=org.id, user_id=u2.id)
        await membership_repo.create(m1)
        await membership_repo.create(m2)

        await role_repo.set_membership_roles(m1.id, [owner_role.id], organization_id=org.id)
        await role_repo.set_membership_roles(m2.id, [owner_role.id], organization_id=org.id)
        await session.commit()

        org_id = org.id
        m1_id = m1.id
        m2_id = m2.id

    # Run two concurrent tasks demoting m1 and m2 via the dedicated OwnershipService
    barrier = asyncio.Barrier(2)

    async def demote_owner(actor_m_id: uuid.UUID, target_m_id: uuid.UUID):
        async with session_factory() as local_session:
            await barrier.wait()
            srv = OwnershipService(local_session)
            actor = await local_session.get(OrganizationMembership, actor_m_id)
            assert actor is not None
            return await srv.demote_co_owner(
                actor_membership=actor,
                organization_id=org_id,
                target_membership_id=target_m_id,
            )

    results = await asyncio.gather(
        demote_owner(m1_id, m2_id),
        demote_owner(m2_id, m1_id),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1, f"Expected 1 success, got {len(successes)}"
    assert len(failures) == 1, f"Expected 1 failure, got {len(failures)}"
    assert isinstance(failures[0], (ValidationError, ForbiddenError))
    assert "Owner" in str(failures[0]) or "last Owner" in str(failures[0])

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_last_owner_removal_serialized():
    """Verify that concurrent removal of the last two owners serializes and one is rejected."""
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        org_repo = OrganizationRepository(session)
        user_repo = UserRepository(session)
        membership_repo = MembershipRepository(session)
        role_repo = RoleRepository(session)
        role_service = RoleService(session)

        uid = uuid.uuid4().hex[:8]
        org = Organization(name=f"Conc Remove Org {uid}", slug=f"conc-remove-{uid}")
        await org_repo.create(org)
        await role_service.seed_default_roles_for_organization(org.id)
        owner_role = await role_repo.get_by_slug("owner", org.id)
        assert owner_role is not None

        u1 = User(
            email=f"rem_owner1_{uid}@example.com",
            normalized_email=f"rem_owner1_{uid}@example.com",
            display_name="Owner 1",
            email_verified_at=utc_now(),
        )
        u2 = User(
            email=f"rem_owner2_{uid}@example.com",
            normalized_email=f"rem_owner2_{uid}@example.com",
            display_name="Owner 2",
            email_verified_at=utc_now(),
        )
        await user_repo.create(u1)
        await user_repo.create(u2)

        m1 = OrganizationMembership(organization_id=org.id, user_id=u1.id)
        m2 = OrganizationMembership(organization_id=org.id, user_id=u2.id)
        await membership_repo.create(m1)
        await membership_repo.create(m2)

        await role_repo.set_membership_roles(m1.id, [owner_role.id], organization_id=org.id)
        await role_repo.set_membership_roles(m2.id, [owner_role.id], organization_id=org.id)
        await session.commit()

        org_id = org.id
        m1_id = m1.id
        m2_id = m2.id

    barrier = asyncio.Barrier(2)

    async def remove_member_task(target_m_id: uuid.UUID):
        await barrier.wait()
        async with session_factory() as local_session:
            async with transaction(local_session):
                # Lock org row
                await local_session.execute(
                    select(Organization.id).where(Organization.id == org_id).with_for_update()
                )
                repo = MembershipRepository(local_session)
                target = await repo.get_by_id(target_m_id, organization_id=org_id)
                assert target is not None

                owners = await repo.get_owner_memberships(org_id, for_update=True)
                if len(owners) <= 1:
                    raise ValidationError("Cannot remove the last Owner of an organization")
                await repo.delete(target)

    results = await asyncio.gather(
        remove_member_task(m1_id),
        remove_member_task(m2_id),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ValidationError)
    assert "last Owner" in str(failures[0])

    await test_engine.dispose()


# ===========================================================================
# 4. INITIAL OWNER PROVISIONING & ATOMIC ROLLBACK
# ===========================================================================


@pytest.mark.asyncio
async def test_initial_owner_provisioning_success(db_session: AsyncSession):
    """Verify org creator gets Owner role atomically upon initial provisioning."""
    org_service = OrganizationService(db_session)
    uid = uuid.uuid4().hex[:8]

    # Success case: Provisioning org atomically assigns Owner role
    data = ProvisionOrganizationInput(
        organization_name=f"Provisioned Org {uid}",
        organization_slug=f"prov-org-{uid}",
        user_email=f"creator_{uid}@example.com",
        user_display_name="Creator User",
        default_workspace_name="Default Workspace",
        default_workspace_slug="default",
    )
    org_read, user_read, ws_read = await org_service.provision_organization_atomic(data)
    assert org_read is not None
    assert user_read is not None
    assert ws_read is not None

    # Check member has Owner role
    mem_repo = MembershipRepository(db_session)
    mem_link = await mem_repo.get(organization_id=org_read.id, user_id=user_read.id)
    assert mem_link is not None
    member = await mem_repo.get_by_id(mem_link.id, organization_id=org_read.id)
    assert member is not None
    member_roles = {r.slug for r in member.roles}
    assert "owner" in member_roles


@pytest.mark.asyncio
async def test_registration_atomic_provisioning_failure_at_owner_assignment_rollback():
    """Verify full user registration and initial org provisioning atomic rollback across all 9 affected tables.

    Specifically asserts that when an error occurs during initial Owner role assignment,
    the transaction rolls back and ZERO rows leak into ANY of the 9 database tables:
    1. organizations
    2. users
    3. user_credentials
    4. organization_memberships
    5. organization_roles
    6. role_permissions
    7. membership_roles
    8. workspaces
    9. authentication_action_tokens
    """
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    uid = uuid.uuid4().hex[:8]
    org_slug = f"reg-fail-org-{uid}"
    user_email = f"reg_fail_{uid}@example.com"
    pwd = "SecurePassword123!"

    async with session_factory() as session:
        org_service = OrganizationService(session)

        # Force failure during initial Owner role assignment in register_user_and_organization_atomic
        with patch.object(
            RoleRepository,
            "set_membership_roles",
            side_effect=RuntimeError("Simulated Owner assignment failure"),
        ):
            with pytest.raises(RuntimeError, match="Simulated Owner assignment failure"):
                await org_service.register_user_and_organization_atomic(
                    email=user_email,
                    password=pwd,
                    display_name="Fail User",
                    organization_name=f"Failing Org {uid}",
                    organization_slug=org_slug,
                    default_workspace_name="Default Workspace",
                    default_workspace_slug="default",
                )

    # Open a fresh independent session to verify absolute rollback in PostgreSQL
    async with session_factory() as verify_session:
        failing_norm_email = normalize_email_address(user_email)

        # 1. organizations
        res = await verify_session.execute(
            text("SELECT count(*) FROM organizations WHERE slug = :slug"),
            {"slug": org_slug},
        )
        assert res.scalar_one() == 0, "organizations table must have 0 rows"

        # 2. users
        res = await verify_session.execute(
            text("SELECT count(*) FROM users WHERE normalized_email = :email"),
            {"email": failing_norm_email},
        )
        assert res.scalar_one() == 0, "users table must have 0 rows"

        # 3. user_credentials
        res = await verify_session.execute(
            text(
                "SELECT count(*) FROM user_credentials WHERE user_id IN "
                "(SELECT id FROM users WHERE normalized_email = :email)"
            ),
            {"email": failing_norm_email},
        )
        assert res.scalar_one() == 0, "user_credentials table must have 0 rows"

        # 4. organization_memberships
        res = await verify_session.execute(
            text(
                "SELECT count(*) FROM organization_memberships WHERE organization_id IN "
                "(SELECT id FROM organizations WHERE slug = :slug)"
            ),
            {"slug": org_slug},
        )
        assert res.scalar_one() == 0, "organization_memberships table must have 0 rows"

        # 5. organization_roles
        res = await verify_session.execute(
            text(
                "SELECT count(*) FROM organization_roles WHERE organization_id IN "
                "(SELECT id FROM organizations WHERE slug = :slug)"
            ),
            {"slug": org_slug},
        )
        assert res.scalar_one() == 0, "organization_roles table must have 0 rows"

        # 6. role_permissions
        res = await verify_session.execute(
            text(
                "SELECT count(*) FROM role_permissions WHERE role_id IN "
                "(SELECT id FROM organization_roles WHERE organization_id IN "
                "(SELECT id FROM organizations WHERE slug = :slug))"
            ),
            {"slug": org_slug},
        )
        assert res.scalar_one() == 0, "role_permissions table must have 0 rows"

        # 7. membership_roles
        res = await verify_session.execute(
            text(
                "SELECT count(*) FROM membership_roles WHERE organization_id IN "
                "(SELECT id FROM organizations WHERE slug = :slug)"
            ),
            {"slug": org_slug},
        )
        assert res.scalar_one() == 0, "membership_roles table must have 0 rows"

        # 8. workspaces
        res = await verify_session.execute(
            text(
                "SELECT count(*) FROM workspaces WHERE organization_id IN "
                "(SELECT id FROM organizations WHERE slug = :slug)"
            ),
            {"slug": org_slug},
        )
        assert res.scalar_one() == 0, "workspaces table must have 0 rows"

        # 9. authentication_action_tokens
        res = await verify_session.execute(
            text(
                "SELECT count(*) FROM authentication_action_tokens WHERE user_id IN "
                "(SELECT id FROM users WHERE normalized_email = :email)"
            ),
            {"email": failing_norm_email},
        )
        assert res.scalar_one() == 0, "authentication_action_tokens table must have 0 rows"

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_provision_organization_atomic_workspace_failure_rollback():
    """Verify complete rollback when failure occurs during later workspace creation."""
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    uid = uuid.uuid4().hex[:8]
    org_slug = f"ws-fail-org-{uid}"
    user_email = f"ws_fail_{uid}@example.com"

    payload = ProvisionOrganizationInput(
        organization_name=f"Failing Org {uid}",
        organization_slug=org_slug,
        user_email=user_email,
        user_display_name="Fail User",
        default_workspace_name="Failing Workspace",
        default_workspace_slug="failing-ws",
    )

    async with session_factory() as session:
        org_service = OrganizationService(session)
        with patch.object(
            org_service.workspace_repo,
            "create",
            side_effect=RuntimeError("Simulated database failure during workspace insertion"),
        ):
            with pytest.raises(RuntimeError, match="Simulated database failure"):
                await org_service.provision_organization_atomic(payload)

    # Verify absence of rows using fresh independent session
    async with session_factory() as verify_session:
        res = await verify_session.execute(
            text("SELECT count(*) FROM organizations WHERE slug = :slug"),
            {"slug": org_slug},
        )
        assert res.scalar_one() == 0

        res = await verify_session.execute(
            text("SELECT count(*) FROM users WHERE normalized_email = :email"),
            {"email": normalize_email_address(user_email)},
        )
        assert res.scalar_one() == 0

        res = await verify_session.execute(
            text(
                "SELECT count(*) FROM organization_memberships WHERE organization_id IN "
                "(SELECT id FROM organizations WHERE slug = :slug)"
            ),
            {"slug": org_slug},
        )
        assert res.scalar_one() == 0

        res = await verify_session.execute(
            text(
                "SELECT count(*) FROM workspaces WHERE organization_id IN "
                "(SELECT id FROM organizations WHERE slug = :slug)"
            ),
            {"slug": org_slug},
        )
        assert res.scalar_one() == 0

    await test_engine.dispose()


# ===========================================================================
# 5. INVITATION SECURITY & ATOMIC ACCEPTANCE
# ===========================================================================


@pytest.mark.asyncio
async def test_invitation_unverified_email_rejected(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Verify that unverified users cannot accept an invitation."""
    uid = uuid.uuid4().hex[:8]
    login_data, csrf = await register_and_login_helper(api_client, f"inviter_{uid}@example.com")

    # Get member role
    roles_res = await api_client.get("/api/v1/organization/roles")
    roles = roles_res.json()
    member_role = next(r for r in roles if r["slug"] == "member")

    invite_email = f"invitee_{uid}@example.com"
    inv_res = await api_client.post(
        "/api/v1/organization/invitations",
        json={"email": invite_email, "role_id": member_role["id"]},
        headers={"X-CSRF-Token": csrf, "Origin": "http://localhost:3000"},
    )
    assert inv_res.status_code == 200
    raw_token = inv_res.json()["data"]["token"]

    # Register invitee through API
    inv_login, inv_csrf = await register_and_login_helper(api_client, invite_email)

    # Directly set invitee email_verified_at = None in database
    user_repo = UserRepository(db_session)
    invitee_user = await user_repo.get_by_id(uuid.UUID(inv_login["user"]["id"]))
    assert invitee_user is not None
    invitee_user.email_verified_at = None
    await db_session.commit()

    # Attempt to accept invitation without email verification -> MUST FAIL 403
    with patch.object(settings, "EMAIL_VERIFICATION_REQUIRED", True):
        accept_res = await api_client.post(
            "/api/v1/organization/invitations/accept",
            json={"token": raw_token},
            headers={"X-CSRF-Token": inv_csrf, "Origin": "http://localhost:3000"},
        )
        assert accept_res.status_code == 403
        err_text = str(accept_res.json())
        assert "verified" in err_text.lower()


@pytest.mark.asyncio
async def test_invitation_mismatched_email_rejected(api_client: AsyncClient):
    """Verify that a user with an email different from the invitation cannot accept it."""
    uid = uuid.uuid4().hex[:8]
    login_data, csrf = await register_and_login_helper(
        api_client, f"owner_mismatch_{uid}@example.com"
    )

    roles_res = await api_client.get("/api/v1/organization/roles")
    member_role = next(r for r in roles_res.json() if r["slug"] == "member")

    inv_res = await api_client.post(
        "/api/v1/organization/invitations",
        json={"email": f"intended_{uid}@example.com", "role_id": member_role["id"]},
        headers={"X-CSRF-Token": csrf, "Origin": "http://localhost:3000"},
    )
    assert inv_res.status_code == 200
    raw_token = inv_res.json()["data"]["token"]

    # Register and login different user
    diff_login, diff_csrf = await register_and_login_helper(
        api_client, f"different_{uid}@example.com"
    )

    accept_res = await api_client.post(
        "/api/v1/organization/invitations/accept",
        json={"token": raw_token},
        headers={"X-CSRF-Token": diff_csrf, "Origin": "http://localhost:3000"},
    )
    assert accept_res.status_code == 403
    err_text = str(accept_res.json())
    assert "different email" in err_text.lower() or "logged in as" in err_text.lower()


@pytest.mark.asyncio
async def test_invitation_case_insensitive_normalized_acceptance(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Verify invitation accepted with uppercase/whitespace email variations succeeds."""
    uid = uuid.uuid4().hex[:8]
    login_data, csrf = await register_and_login_helper(api_client, f"owner_case_{uid}@example.com")

    roles_res = await api_client.get("/api/v1/organization/roles")
    member_role = next(r for r in roles_res.json() if r["slug"] == "member")

    # Invite with lowercase
    target_email = f"case_test_{uid}@example.com"
    inv_res = await api_client.post(
        "/api/v1/organization/invitations",
        json={"email": target_email, "role_id": member_role["id"]},
        headers={"X-CSRF-Token": csrf, "Origin": "http://localhost:3000"},
    )
    assert inv_res.status_code == 200
    raw_token = inv_res.json()["data"]["token"]

    # Register user with mixed case and trailing whitespace
    mixed_email = f"  Case_Test_{uid}@EXAMPLE.com  "
    user_login, user_csrf = await register_and_login_helper(api_client, mixed_email)

    # Ensure invitee is verified
    user_repo = UserRepository(db_session)
    invitee_user = await user_repo.get_by_id(uuid.UUID(user_login["user"]["id"]))
    assert invitee_user is not None
    invitee_user.email_verified_at = utc_now()
    await db_session.commit()

    accept_res = await api_client.post(
        "/api/v1/organization/invitations/accept",
        json={"token": raw_token},
        headers={"X-CSRF-Token": user_csrf, "Origin": "http://localhost:3000"},
    )
    assert accept_res.status_code == 200
    assert accept_res.json()["success"] is True


# ===========================================================================
# 6. AUDIT EVENT INTEGRITY & SENSITIVE DATA MASKING
# ===========================================================================


@pytest.mark.asyncio
async def test_audit_sensitive_metadata_masking(db_session: AsyncSession):
    """Verify audit logger masks sensitive keys like password, token, secret, csrf, session."""
    audit_service = AuditService(db_session)
    org_repo = OrganizationRepository(db_session)
    user_repo = UserRepository(db_session)

    uid = uuid.uuid4().hex[:8]
    org = Organization(name=f"Audit Mask Org {uid}", slug=f"audit-mask-{uid}")
    await org_repo.create(org)
    user = User(
        email=f"audit_user_{uid}@example.com",
        normalized_email=f"audit_user_{uid}@example.com",
        display_name="Audit User",
        email_verified_at=utc_now(),
    )
    await user_repo.create(user)
    await db_session.commit()

    raw_metadata = {
        "user_email": "safe@example.com",
        "nested": {
            "password": "ClearTextPassword123!",
            "auth_token": "secret_token_abc",
            "csrf_token": "csrf_val_xyz",
            "session_id": "sess_123",
            "recovery_code": "rec_999",
            "safe_property": "visible_value",
        },
        "cookie_payload": "secret_cookie",
    }

    event = await audit_service.record_event(
        organization_id=org.id,
        actor_user_id=user.id,
        action="security.audit_test",
        target_type="test",
        target_id=str(user.id),
        metadata=raw_metadata,
    )
    await db_session.commit()

    res = await db_session.execute(
        select(AdministrativeAuditEvent).where(AdministrativeAuditEvent.id == event.id)
    )
    saved = res.scalar_one_or_none()
    assert saved is not None
    meta = saved.sanitized_metadata
    assert isinstance(meta, dict)
    nested = meta.get("nested")
    assert isinstance(nested, dict)

    assert meta["user_email"] == "safe@example.com"
    assert nested["safe_property"] == "visible_value"
    assert nested["password"] == "[REDACTED]"
    assert nested["auth_token"] == "[REDACTED]"
    assert nested["csrf_token"] == "[REDACTED]"
    assert nested["session_id"] == "[REDACTED]"
    assert nested["recovery_code"] == "[REDACTED]"
    assert meta["cookie_payload"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_audit_event_retained_on_actor_deletion(db_session: AsyncSession):
    """Verify that deleting a user preserves audit records with actor_user_id = NULL."""
    audit_service = AuditService(db_session)
    org_repo = OrganizationRepository(db_session)
    user_repo = UserRepository(db_session)

    uid = uuid.uuid4().hex[:8]
    org = Organization(name=f"Audit Delete Org {uid}", slug=f"audit-del-{uid}")
    await org_repo.create(org)
    user = User(
        email=f"del_user_{uid}@example.com",
        normalized_email=f"del_user_{uid}@example.com",
        display_name="To Delete",
        email_verified_at=utc_now(),
    )
    await user_repo.create(user)
    await db_session.commit()

    event = await audit_service.record_event(
        organization_id=org.id,
        actor_user_id=user.id,
        action="user.temporary_action",
        target_type="test",
        target_id=str(user.id),
    )
    await db_session.commit()

    event_id = event.id

    # Delete the actor user directly through session
    await db_session.delete(user)
    await db_session.commit()

    # Expire identity map cache so fresh DB row state is queried
    db_session.expire_all()

    # Verify audit event is retained
    res = await db_session.execute(
        select(AdministrativeAuditEvent).where(AdministrativeAuditEvent.id == event_id)
    )
    refreshed_event = res.scalar_one_or_none()
    assert refreshed_event is not None
    assert refreshed_event.actor_user_id is None, (
        "Audit event actor must cascade to NULL on user deletion"
    )


# ===========================================================================
# 7. PRIVILEGE ESCALATION DIRECT API TESTS
# ===========================================================================


@pytest.mark.asyncio
async def test_admin_cannot_assign_owner_role(api_client: AsyncClient, db_session: AsyncSession):
    """Verify an Admin cannot assign the Owner role to themselves or another member."""
    uid = uuid.uuid4().hex[:8]
    # Org Owner registers
    owner_data, owner_csrf = await register_and_login_helper(
        api_client, f"owner_esc_{uid}@example.com"
    )
    org_id = owner_data["active_organization"]["id"]

    roles_res = await api_client.get("/api/v1/organization/roles")
    roles = roles_res.json()
    admin_role = next(r for r in roles if r["slug"] == "admin")
    owner_role = next(r for r in roles if r["slug"] == "owner")

    # Invite user 2 as Admin
    admin_email = f"admin_target_{uid}@example.com"
    inv_res = await api_client.post(
        "/api/v1/organization/invitations",
        json={"email": admin_email, "role_id": admin_role["id"]},
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    raw_token_2 = inv_res.json()["data"]["token"]

    # Register and accept as User 2
    u2_data, u2_csrf = await register_and_login_helper(api_client, admin_email)

    # Ensure u2 email is verified
    user_repo = UserRepository(db_session)
    u2_user = await user_repo.get_by_id(uuid.UUID(u2_data["user"]["id"]))
    assert u2_user is not None
    u2_user.email_verified_at = utc_now()
    await db_session.commit()

    await api_client.post(
        "/api/v1/organization/invitations/accept",
        json={"token": raw_token_2},
        headers={"X-CSRF-Token": u2_csrf, "Origin": "http://localhost:3000"},
    )

    # Switch active org of User 2 to Org 1
    switch_res = await api_client.post(
        "/api/v1/auth/select-tenant",
        json={"organization_id": org_id},
        headers={"X-CSRF-Token": u2_csrf, "Origin": "http://localhost:3000"},
    )
    assert switch_res.status_code == 200

    # User 2 (Admin) tries to assign Owner role to User 2 -> MUST BE REJECTED 400
    members_res = await api_client.get("/api/v1/organization/members")
    u2_membership = next(m for m in members_res.json() if m["user"]["email"] == admin_email)

    assign_res = await api_client.put(
        f"/api/v1/organization/members/{u2_membership['id']}/roles",
        json={"role_ids": [admin_role["id"], owner_role["id"]]},
        headers={"X-CSRF-Token": u2_csrf, "Origin": "http://localhost:3000"},
    )
    assert assign_res.status_code in (400, 403)
    err_text = str(assign_res.json())
    assert (
        "owner" in err_text.lower()
        or "forbidden" in err_text.lower()
        or "permission" in err_text.lower()
    )


@pytest.mark.asyncio
async def test_admin_cannot_create_role_with_unpossessed_permissions(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Verify Admin cannot create a custom role with permissions they do not possess (e.g. ownership.transfer)."""
    uid = uuid.uuid4().hex[:8]
    owner_data, owner_csrf = await register_and_login_helper(
        api_client, f"owner_perm_{uid}@example.com"
    )
    org_id = owner_data["active_organization"]["id"]

    roles_res = await api_client.get("/api/v1/organization/roles")
    admin_role = next(r for r in roles_res.json() if r["slug"] == "admin")

    # Invite user 2 as Admin
    admin_email = f"admin_perm_{uid}@example.com"
    inv_res = await api_client.post(
        "/api/v1/organization/invitations",
        json={"email": admin_email, "role_id": admin_role["id"]},
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    raw_token_2 = inv_res.json()["data"]["token"]

    u2_data, u2_csrf = await register_and_login_helper(api_client, admin_email)

    # Ensure u2 email is verified
    user_repo = UserRepository(db_session)
    u2_user = await user_repo.get_by_id(uuid.UUID(u2_data["user"]["id"]))
    assert u2_user is not None
    u2_user.email_verified_at = utc_now()
    await db_session.commit()

    await api_client.post(
        "/api/v1/organization/invitations/accept",
        json={"token": raw_token_2},
        headers={"X-CSRF-Token": u2_csrf, "Origin": "http://localhost:3000"},
    )
    switch_res = await api_client.post(
        "/api/v1/auth/select-tenant",
        json={"organization_id": org_id},
        headers={"X-CSRF-Token": u2_csrf, "Origin": "http://localhost:3000"},
    )
    assert switch_res.status_code == 200

    # Admin tries to create custom role with 'ownership.transfer'
    create_role_res = await api_client.post(
        "/api/v1/organization/roles",
        json={
            "name": "Super Admin",
            "slug": f"super-admin-{uid}",
            "permission_codes": ["roles.read", "ownership.transfer"],
        },
        headers={"X-CSRF-Token": u2_csrf, "Origin": "http://localhost:3000"},
    )
    assert create_role_res.status_code in (400, 403)
    err_text = str(create_role_res.json())
    assert (
        "possess" in err_text.lower()
        or "forbidden" in err_text.lower()
        or "permission" in err_text.lower()
    )


# ===========================================================================
# 8. SYSTEM ROLE IMMUTABILITY & DEDICATED OWNERSHIP WORKFLOW TESTS
# ===========================================================================


@pytest.mark.asyncio
async def test_system_role_permission_immutability(db_session: AsyncSession):
    """Verify that Owner, Admin, Member, and Viewer system roles cannot have permissions changed."""
    org_service = OrganizationService(db_session)
    role_repo = RoleRepository(db_session)
    role_service = RoleService(db_session)

    uid = uuid.uuid4().hex[:8]
    data = ProvisionOrganizationInput(
        organization_name=f"Sys Role Org {uid}",
        organization_slug=f"sys-role-org-{uid}",
        user_email=f"sys_owner_{uid}@example.com",
        user_display_name="Sys Owner",
        default_workspace_name="Default Workspace",
        default_workspace_slug="default",
    )
    org_read, user_read, _ = await org_service.provision_organization_atomic(data)
    mem_repo = MembershipRepository(db_session)
    mem = await mem_repo.get(org_read.id, user_read.id)
    assert mem is not None

    system_slugs = ["owner", "admin", "member", "viewer"]
    dummy_perm = (await role_service.perm_repo.list_all())[0]

    for slug in system_slugs:
        role = await role_repo.get_by_slug(slug, org_read.id)
        assert role is not None
        assert role.is_system is True
        original_perm_ids = [p.id for p in role.permissions]

        # 1. Attempt to modify permissions directly via repository set_role_permissions
        with pytest.raises(
            ValidationError, match="immutable and its permission associations cannot be modified"
        ):
            await role_repo.set_role_permissions(role.id, [dummy_perm.id])

        # 2. Attempt to modify role via service update_custom_role
        from app.schemas.role import RoleUpdateInput

        with pytest.raises(ValidationError, match="System roles cannot be modified or weakened"):
            await role_service.update_custom_role(
                actor_membership_id=mem.id,
                organization_id=org_read.id,
                role_id=role.id,
                data=RoleUpdateInput(name="Hacked System Role", permission_codes=[dummy_perm.code]),
            )

        # 3. Attempt to delete system role
        with pytest.raises(ValidationError, match="System roles cannot be deleted"):
            await role_service.delete_custom_role(org_read.id, role.id)

        # Verify permissions were untouched
        refreshed = await role_repo.get_by_id(role.id, org_read.id)
        assert refreshed is not None
        assert [p.id for p in refreshed.permissions] == original_perm_ids

    # 4. Attempting to call dedicated bootstrap_system_role_permissions on custom non-system role must fail
    custom_role = OrganizationRole(
        organization_id=org_read.id,
        name=f"Custom Analyst {uid}",
        slug=f"custom-analyst-{uid}",
        description="Non-system role",
        is_system=False,
    )
    await role_repo.create(custom_role)
    with pytest.raises(
        ValidationError,
        match="bootstrap_system_role_permissions can only be used on system roles",
    ):
        await role_repo.bootstrap_system_role_permissions(custom_role.id, [dummy_perm.id])

    # 5. Calling ordinary set_role_permissions on custom role succeeds without bypass flags
    await role_repo.set_role_permissions(custom_role.id, [dummy_perm.id])
    custom_refreshed = await role_repo.get_by_id(custom_role.id, org_read.id)
    assert custom_refreshed is not None
    assert [p.id for p in custom_refreshed.permissions] == [dummy_perm.id]


@pytest.mark.asyncio
async def test_generic_role_assignment_cannot_grant_or_demote_owner(db_session: AsyncSession):
    """Verify that RoleService.assign_roles_to_member rejects granting Owner or modifying an Owner."""
    org_service = OrganizationService(db_session)
    role_repo = RoleRepository(db_session)
    role_service = RoleService(db_session)
    user_repo = UserRepository(db_session)
    mem_repo = MembershipRepository(db_session)

    uid = uuid.uuid4().hex[:8]
    data = ProvisionOrganizationInput(
        organization_name=f"Assign Guard Org {uid}",
        organization_slug=f"assign-guard-org-{uid}",
        user_email=f"guard_owner_{uid}@example.com",
        user_display_name="Guard Owner",
        default_workspace_name="Default Workspace",
        default_workspace_slug="default",
    )
    org_read, owner_user, _ = await org_service.provision_organization_atomic(data)
    owner_mem = await mem_repo.get(org_read.id, owner_user.id)
    assert owner_mem is not None

    # Create a regular member
    reg_user = User(
        email=f"reg_{uid}@example.com",
        normalized_email=f"reg_{uid}@example.com",
        display_name="Regular User",
        email_verified_at=utc_now(),
    )
    await user_repo.create(reg_user)
    reg_mem = OrganizationMembership(organization_id=org_read.id, user_id=reg_user.id)
    await mem_repo.create(reg_mem)
    member_role = await role_repo.get_by_slug("member", org_read.id)
    owner_role = await role_repo.get_by_slug("owner", org_read.id)
    assert member_role is not None and owner_role is not None
    await role_repo.set_membership_roles(reg_mem.id, [member_role.id], organization_id=org_read.id)

    # 1. Attempt to grant Owner role to regular member via generic assign_roles_to_member
    with pytest.raises(
        ValidationError, match="The Owner role cannot be granted via generic role assignment"
    ):
        await role_service.assign_roles_to_member(
            actor_membership_id=owner_mem.id,
            actor_user_id=owner_user.id,
            organization_id=org_read.id,
            target_membership_id=reg_mem.id,
            role_ids=[owner_role.id],
        )

    # 2. Attempt to modify or demote the Owner via generic assign_roles_to_member
    with pytest.raises(
        ValidationError,
        match="Owner memberships cannot be modified or demoted via generic role assignment",
    ):
        await role_service.assign_roles_to_member(
            actor_membership_id=owner_mem.id,
            actor_user_id=owner_user.id,
            organization_id=org_read.id,
            target_membership_id=owner_mem.id,
            role_ids=[member_role.id],
        )

    # 3. Attempt to issue an invitation for the Owner role
    from app.schemas.invitation import InvitationCreateInput
    from app.services.invitation import InvitationService

    inv_service = InvitationService(db_session)
    with pytest.raises(ValidationError, match="Invitations cannot be issued for the Owner role"):
        await inv_service.create_invitation(
            actor_membership_id=owner_mem.id,
            actor_user_id=owner_user.id,
            organization_id=org_read.id,
            data=InvitationCreateInput(email=f"new_owner_{uid}@example.com", role_id=owner_role.id),
        )


@pytest.mark.asyncio
async def test_strictly_tenant_scoped_permission_evaluation(db_session: AsyncSession):
    """Verify that PermissionService.get_effective_permissions requires matching organization context."""
    org_service = OrganizationService(db_session)
    perm_service = PermissionService(db_session)
    mem_repo = MembershipRepository(db_session)

    uid_a = uuid.uuid4().hex[:8]
    data_a = ProvisionOrganizationInput(
        organization_name=f"Tenant A {uid_a}",
        organization_slug=f"tenant-a-{uid_a}",
        user_email=f"owner_a_{uid_a}@example.com",
        user_display_name="Owner A",
        default_workspace_name="Default Workspace",
        default_workspace_slug="default",
    )
    org_a, user_a, _ = await org_service.provision_organization_atomic(data_a)
    mem_a = await mem_repo.get(org_a.id, user_a.id)
    assert mem_a is not None

    uid_b = uuid.uuid4().hex[:8]
    data_b = ProvisionOrganizationInput(
        organization_name=f"Tenant B {uid_b}",
        organization_slug=f"tenant-b-{uid_b}",
        user_email=f"owner_b_{uid_b}@example.com",
        user_display_name="Owner B",
        default_workspace_name="Default Workspace",
        default_workspace_slug="default",
    )
    org_b, user_b, _ = await org_service.provision_organization_atomic(data_b)
    mem_b = await mem_repo.get(org_b.id, user_b.id)
    assert mem_b is not None

    # Valid tenant context
    perms_a = await perm_service.get_effective_permissions(mem_a.id, organization_id=org_a.id)
    assert "ownership.transfer" in perms_a
    assert "organization.settings.update" in perms_a

    # Mismatched tenant context (evaluating Org A's membership in Org B's context) -> fails closed
    mismatched_perms = await perm_service.get_effective_permissions(
        mem_a.id, organization_id=org_b.id
    )
    assert mismatched_perms == set(), "Cross-tenant evaluation must return empty permission set"

    # Non-existent membership in Org A -> fails closed
    fake_perms = await perm_service.get_effective_permissions(
        uuid.uuid4(), organization_id=org_a.id
    )
    assert fake_perms == set(), "Non-existent membership must return empty permission set"

    # check_membership_has_permission with mismatched tenant -> False
    has_perm = await perm_service.check_membership_has_permission(
        mem_a.id, "ownership.transfer", organization_id=org_b.id
    )
    assert has_perm is False


@pytest.mark.asyncio
async def test_concurrent_ownership_transfers_serialized():
    """Verify that concurrent ownership transfers by the same owner serialize and only one commits."""
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        org_repo = OrganizationRepository(session)
        user_repo = UserRepository(session)
        membership_repo = MembershipRepository(session)
        role_repo = RoleRepository(session)
        role_service = RoleService(session)

        uid = uuid.uuid4().hex[:8]
        org = Organization(name=f"Conc Transfer Org {uid}", slug=f"conc-trans-{uid}")
        await org_repo.create(org)
        await role_service.seed_default_roles_for_organization(org.id)
        owner_role = await role_repo.get_by_slug("owner", org.id)
        member_role = await role_repo.get_by_slug("member", org.id)
        assert owner_role is not None and member_role is not None

        # Owner and two potential successor members
        u_owner = User(
            email=f"cur_owner_{uid}@example.com",
            normalized_email=f"cur_owner_{uid}@example.com",
            display_name="Current Owner",
            email_verified_at=utc_now(),
        )
        u_cand1 = User(
            email=f"cand1_{uid}@example.com",
            normalized_email=f"cand1_{uid}@example.com",
            display_name="Candidate 1",
            email_verified_at=utc_now(),
        )
        u_cand2 = User(
            email=f"cand2_{uid}@example.com",
            normalized_email=f"cand2_{uid}@example.com",
            display_name="Candidate 2",
            email_verified_at=utc_now(),
        )
        await user_repo.create(u_owner)
        await user_repo.create(u_cand1)
        await user_repo.create(u_cand2)

        m_owner = OrganizationMembership(organization_id=org.id, user_id=u_owner.id)
        m_cand1 = OrganizationMembership(organization_id=org.id, user_id=u_cand1.id)
        m_cand2 = OrganizationMembership(organization_id=org.id, user_id=u_cand2.id)
        await membership_repo.create(m_owner)
        await membership_repo.create(m_cand1)
        await membership_repo.create(m_cand2)

        await role_repo.set_membership_roles(m_owner.id, [owner_role.id], organization_id=org.id)
        await role_repo.set_membership_roles(m_cand1.id, [member_role.id], organization_id=org.id)
        await role_repo.set_membership_roles(m_cand2.id, [member_role.id], organization_id=org.id)
        await session.commit()

        org_id = org.id
        m_owner_id = m_owner.id
        m_cand1_id = m_cand1.id
        m_cand2_id = m_cand2.id

    barrier = asyncio.Barrier(2)

    async def execute_transfer(target_id: uuid.UUID):
        async with session_factory() as local_session:
            await barrier.wait()
            srv = OwnershipService(local_session)
            actor = await local_session.get(OrganizationMembership, m_owner_id)
            assert actor is not None
            return await srv.transfer_ownership(
                actor_membership=actor,
                organization_id=org_id,
                target_membership_id=target_id,
                confirmation="TRANSFER",
            )

    results = await asyncio.gather(
        execute_transfer(m_cand1_id),
        execute_transfer(m_cand2_id),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1, f"Expected exactly 1 transfer success, got {len(successes)}"
    assert len(failures) == 1, f"Expected exactly 1 transfer failure, got {len(failures)}"

    # Verify database state has exactly 1 active owner and never 0
    async with session_factory() as verify_session:
        m_repo = MembershipRepository(verify_session)
        owners = await m_repo.get_owner_memberships(org_id)
        assert len(owners) == 1, f"Organization must have exactly 1 active Owner, got {len(owners)}"

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_ownership_transfer_racing_with_owner_removal():
    """Verify that ownership transfer racing with owner removal serializes cleanly without zero owners."""
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        org_repo = OrganizationRepository(session)
        user_repo = UserRepository(session)
        membership_repo = MembershipRepository(session)
        role_repo = RoleRepository(session)
        role_service = RoleService(session)

        uid = uuid.uuid4().hex[:8]
        org = Organization(name=f"Race Org {uid}", slug=f"race-org-{uid}")
        await org_repo.create(org)
        await role_service.seed_default_roles_for_organization(org.id)
        owner_role = await role_repo.get_by_slug("owner", org.id)
        admin_role = await role_repo.get_by_slug("admin", org.id)
        assert owner_role is not None and admin_role is not None

        u_owner = User(
            email=f"race_owner_{uid}@example.com",
            normalized_email=f"race_owner_{uid}@example.com",
            display_name="Race Owner",
            email_verified_at=utc_now(),
        )
        u_target = User(
            email=f"race_target_{uid}@example.com",
            normalized_email=f"race_target_{uid}@example.com",
            display_name="Race Target",
            email_verified_at=utc_now(),
        )
        await user_repo.create(u_owner)
        await user_repo.create(u_target)

        m_owner = OrganizationMembership(organization_id=org.id, user_id=u_owner.id)
        m_target = OrganizationMembership(organization_id=org.id, user_id=u_target.id)
        await membership_repo.create(m_owner)
        await membership_repo.create(m_target)

        await role_repo.set_membership_roles(m_owner.id, [owner_role.id], organization_id=org.id)
        await role_repo.set_membership_roles(m_target.id, [admin_role.id], organization_id=org.id)
        await session.commit()

        org_id = org.id
        m_owner_id = m_owner.id
        m_target_id = m_target.id

    barrier = asyncio.Barrier(2)

    async def transfer_task():
        async with session_factory() as local_session:
            await barrier.wait()
            srv = OwnershipService(local_session)
            actor = await local_session.get(OrganizationMembership, m_owner_id)
            assert actor is not None
            return await srv.transfer_ownership(
                actor_membership=actor,
                organization_id=org_id,
                target_membership_id=m_target_id,
                confirmation="TRANSFER",
            )

    async def remove_task():
        async with session_factory() as local_session:
            await barrier.wait()
            # Attempting to remove the owner directly via locked removal
            async with transaction(local_session):
                from app.models.organization import Organization

                await local_session.execute(
                    select(Organization.id).where(Organization.id == org_id).with_for_update()
                )
                m_repo = MembershipRepository(local_session)
                target = await m_repo.get_by_id(m_owner_id, organization_id=org_id)
                if not target:
                    return None
                owners = await m_repo.get_owner_memberships(org_id)
                if len(owners) <= 1:
                    raise ValidationError("Cannot remove the last Owner of an organization")
                await m_repo.delete(target)
                return True

    await asyncio.gather(
        transfer_task(),
        remove_task(),
        return_exceptions=True,
    )

    # Regardless of which serializes first, the organization must NEVER end up with zero owners
    async with session_factory() as verify_session:
        m_repo = MembershipRepository(verify_session)
        owners = await m_repo.get_owner_memberships(org_id)
        assert len(owners) >= 1, (
            "Organization must NEVER end up with 0 owners after concurrent race"
        )

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_invitation_acceptance_serialized():
    """Verify that concurrent acceptance of the same invitation produces exactly 1 membership."""
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        org_repo = OrganizationRepository(session)
        user_repo = UserRepository(session)
        membership_repo = MembershipRepository(session)
        role_repo = RoleRepository(session)
        role_service = RoleService(session)

        uid = uuid.uuid4().hex[:8]
        org = Organization(name=f"Invite Conc Org {uid}", slug=f"inv-conc-{uid}")
        await org_repo.create(org)
        await role_service.seed_default_roles_for_organization(org.id)
        owner_role = await role_repo.get_by_slug("owner", org.id)
        member_role = await role_repo.get_by_slug("member", org.id)
        assert owner_role is not None and member_role is not None

        u_owner = User(
            email=f"inv_owner_{uid}@example.com",
            normalized_email=f"inv_owner_{uid}@example.com",
            display_name="Inv Owner",
            email_verified_at=utc_now(),
        )
        await user_repo.create(u_owner)
        m_owner = OrganizationMembership(organization_id=org.id, user_id=u_owner.id)
        await membership_repo.create(m_owner)
        await role_repo.set_membership_roles(m_owner.id, [owner_role.id], organization_id=org.id)

        # Create invitation
        from app.schemas.invitation import InvitationCreateInput
        from app.services.invitation import InvitationService

        inv_service = InvitationService(session)
        invited_email = f"acceptor_{uid}@example.com"
        invitation, raw_token = await inv_service.create_invitation(
            actor_membership_id=m_owner.id,
            actor_user_id=u_owner.id,
            organization_id=org.id,
            data=InvitationCreateInput(email=invited_email, role_id=member_role.id),
        )

        # Create the user who will accept the invitation
        u_acceptor = User(
            email=invited_email,
            normalized_email=normalize_email_address(invited_email),
            display_name="Acceptor User",
            email_verified_at=utc_now(),
        )
        await user_repo.create(u_acceptor)
        await session.commit()

        org_id = org.id
        acceptor_id = u_acceptor.id

    barrier = asyncio.Barrier(2)

    async def accept_task():
        async with session_factory() as local_session:
            await barrier.wait()
            srv = InvitationService(local_session)
            u = await local_session.get(User, acceptor_id)
            assert u is not None
            async with transaction(local_session):
                return await srv.accept_invitation(raw_token, u)

    results = await asyncio.gather(
        accept_task(),
        accept_task(),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1, f"Expected exactly 1 successful acceptance, got {len(successes)}"
    assert len(failures) == 1, f"Expected exactly 1 failed acceptance, got {len(failures)}"
    assert isinstance(failures[0], ValidationError)

    # Verify in DB: exactly 1 membership exists for this user in this org
    async with session_factory() as verify_session:
        res = await verify_session.execute(
            text(
                "SELECT count(*) FROM organization_memberships WHERE organization_id = :org_id AND user_id = :u_id"
            ),
            {"org_id": org_id, "u_id": acceptor_id},
        )
        assert res.scalar_one() == 1

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_invitation_failure_during_role_assignment_preserves_token(db_session: AsyncSession):
    """Verify that failure during invitation acceptance rolls back and leaves invitation usable."""
    org_service = OrganizationService(db_session)
    role_repo = RoleRepository(db_session)
    mem_repo = MembershipRepository(db_session)
    user_repo = UserRepository(db_session)

    uid = uuid.uuid4().hex[:8]
    data = ProvisionOrganizationInput(
        organization_name=f"Preserve Inv Org {uid}",
        organization_slug=f"pres-inv-org-{uid}",
        user_email=f"pres_owner_{uid}@example.com",
        user_display_name="Pres Owner",
        default_workspace_name="Default Workspace",
        default_workspace_slug="default",
    )
    org_read, owner_user, _ = await org_service.provision_organization_atomic(data)
    owner_mem = await mem_repo.get(org_read.id, owner_user.id)
    assert owner_mem is not None

    member_role = await role_repo.get_by_slug("member", org_read.id)
    assert member_role is not None

    from app.schemas.invitation import InvitationCreateInput
    from app.services.invitation import InvitationService

    inv_service = InvitationService(db_session)
    invited_email = f"preserve_target_{uid}@example.com"
    invitation, raw_token = await inv_service.create_invitation(
        actor_membership_id=owner_mem.id,
        actor_user_id=owner_user.id,
        organization_id=org_read.id,
        data=InvitationCreateInput(email=invited_email, role_id=member_role.id),
    )

    inv_id = invitation.id
    org_id = org_read.id
    raw_token_val = raw_token

    accepting_user = User(
        email=invited_email,
        normalized_email=normalize_email_address(invited_email),
        display_name="Acceptor User",
        email_verified_at=utc_now(),
    )
    await user_repo.create(accepting_user)
    await db_session.commit()
    db_session.expunge_all()

    # Re-fetch user in session
    target_user = await user_repo.get_by_normalized_email(normalize_email_address(invited_email))
    assert target_user is not None

    # Simulate failure during role assignment
    with patch.object(
        RoleRepository,
        "set_membership_roles",
        side_effect=RuntimeError("Simulated assignment error"),
    ):
        with pytest.raises(RuntimeError, match="Simulated assignment error"):
            async with transaction(db_session):
                await inv_service.accept_invitation(raw_token_val, target_user)

    db_session.expunge_all()

    # Verify invitation was rolled back and is NOT permanently consumed
    refreshed_inv = await inv_service.invitation_repo.get_by_id(inv_id, org_id)
    assert refreshed_inv is not None
    assert refreshed_inv.accepted_at is None
    assert refreshed_inv.is_valid is True

    # Now accept successfully
    target_user_fresh = await user_repo.get_by_normalized_email(
        normalize_email_address(invited_email)
    )
    assert target_user_fresh is not None
    async with transaction(db_session):
        success_mem = await inv_service.accept_invitation(raw_token_val, target_user_fresh)
        assert success_mem is not None

    db_session.expunge_all()
    refreshed_inv_after = await inv_service.invitation_repo.get_by_id(inv_id, org_id)
    assert refreshed_inv_after is not None
    assert refreshed_inv_after.accepted_at is not None


@pytest.mark.asyncio
async def test_audit_event_retained_on_organization_deletion(db_session: AsyncSession):
    """Verify that deleting an organization preserves historical audit records with SET NULL."""
    org_repo = OrganizationRepository(db_session)
    audit_service = AuditService(db_session)
    user_repo = UserRepository(db_session)

    uid = uuid.uuid4().hex[:8]
    org = Organization(name=f"Retain Org {uid}", slug=f"retain-org-{uid}")
    await org_repo.create(org)

    u = User(
        email=f"retain_user_{uid}@example.com",
        normalized_email=f"retain_user_{uid}@example.com",
        display_name="Retain User",
        email_verified_at=utc_now(),
    )
    await user_repo.create(u)

    event = await audit_service.record_event(
        organization_id=org.id,
        organization_slug=org.slug,
        actor_user_id=u.id,
        action="organization.settings_updated",
        target_type="organization",
        target_id=str(org.id),
        metadata={"key": "test_retention"},
    )
    await db_session.commit()

    event_id = event.id
    org_slug = org.slug

    # Delete the organization
    await db_session.execute(
        text("DELETE FROM organizations WHERE id = :org_id"), {"org_id": org.id}
    )
    await db_session.commit()
    db_session.expunge_all()

    # Verify audit event is still in the database
    res = await db_session.execute(
        select(AdministrativeAuditEvent).where(AdministrativeAuditEvent.id == event_id)
    )
    loaded_event = res.scalar_one_or_none()
    assert loaded_event is not None, "Audit event must be retained after organization deletion"
    assert loaded_event.organization_id is None, "Foreign key must be SET NULL"
    assert loaded_event.organization_slug == org_slug, "Tenant slug must remain attributed"


@pytest.mark.asyncio
async def test_organization_settings_mutation_api(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Verify PATCH /api/v1/organization/settings mutation, permissions, CSRF, and audit record."""
    uid = uuid.uuid4().hex[:8]
    owner_email = f"settings_owner_{uid}@example.com"
    owner_data, owner_csrf = await register_and_login_helper(
        api_client, email=owner_email, org_name=f"Settings Org {uid}", org_slug=f"set-org-{uid}"
    )

    # 1. Successful settings mutation by Owner
    patch_res = await api_client.patch(
        "/api/v1/organization/settings",
        json={"name": "Renamed Enterprise"},
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["name"] == "Renamed Enterprise"

    # 2. Verify GET /api/v1/organization/settings
    get_res = await api_client.get("/api/v1/organization/settings")
    assert get_res.status_code == 200
    assert get_res.json()["name"] == "Renamed Enterprise"

    # 3. Verify audit log was recorded and readable via BOTH /audit-logs and /audit-events
    logs_res = await api_client.get("/api/v1/organization/audit-logs")
    assert logs_res.status_code == 200
    logs_data = logs_res.json()
    assert any(e["action"] == "organization.settings_updated" for e in logs_data)

    events_res = await api_client.get("/api/v1/organization/audit-events")
    assert events_res.status_code == 200
    events_data = events_res.json()
    assert any(e["action"] == "organization.settings_updated" for e in events_data)

    # 4. Reject mutation without CSRF token
    no_csrf_res = await api_client.patch(
        "/api/v1/organization/settings",
        json={"name": "Hacked Name"},
        headers={"Origin": "http://localhost:3000"},
    )
    assert no_csrf_res.status_code == 403

    # 5. Invite viewer into the main organization while Owner session is active
    viewer_email = f"viewer_{uid}@example.com"
    role_repo = RoleRepository(db_session)
    org_repo = OrganizationRepository(db_session)
    org = await org_repo.get_by_slug(f"set-org-{uid}")
    assert org is not None
    viewer_role = await role_repo.get_by_slug("viewer", org.id)
    assert viewer_role is not None

    inv_res = await api_client.post(
        "/api/v1/organization/invitations",
        json={"email": viewer_email, "role_id": str(viewer_role.id)},
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    assert inv_res.status_code in (200, 201)
    res_payload = inv_res.json()
    raw_token = res_payload.get("token") or (res_payload.get("data") or {}).get("token")
    assert raw_token is not None

    # Now register and login as viewer (swapping active session in client)
    viewer_data, viewer_csrf = await register_and_login_helper(
        api_client, email=viewer_email, org_name=f"Other Org {uid}", org_slug=f"other-org-{uid}"
    )

    # Mark viewer email verified
    v_user = await UserRepository(db_session).get_by_normalized_email(
        normalize_email_address(viewer_email)
    )
    assert v_user is not None
    v_user.email_verified_at = utc_now()
    await db_session.commit()

    # Viewer accepts invitation
    accept_res = await api_client.post(
        "/api/v1/organization/invitations/accept",
        json={"token": raw_token},
        headers={"X-CSRF-Token": viewer_csrf, "Origin": "http://localhost:3000"},
    )
    assert accept_res.status_code == 200

    # Switch tenant to main org
    switch_res = await api_client.post(
        "/api/v1/auth/select-tenant",
        json={"organization_id": str(org.id)},
        headers={"X-CSRF-Token": viewer_csrf, "Origin": "http://localhost:3000"},
    )
    assert switch_res.status_code == 200

    # Viewer attempts to patch organization settings -> 403 Forbidden
    viewer_patch_res = await api_client.patch(
        "/api/v1/organization/settings",
        json={"name": "Viewer Takeover"},
        headers={"X-CSRF-Token": viewer_csrf, "Origin": "http://localhost:3000"},
    )
    assert viewer_patch_res.status_code == 403


@pytest.mark.asyncio
async def test_audit_api_role_matrix_four_roles(api_client: AsyncClient, db_session: AsyncSession):
    """Verify exact role-to-permission matrix for administrative audit endpoints.

    Requirements:
    - Owner: allowed (200) for /audit-logs and /audit-events
    - Admin: allowed (200) for /audit-logs and /audit-events
    - Member: denied (403) for /audit-logs and /audit-events
    - Viewer: denied (403) for /audit-logs and /audit-events
    """
    uid = uuid.uuid4().hex[:8]
    owner_email = f"audit_owner_{uid}@example.com"
    owner_data, owner_csrf = await register_and_login_helper(
        api_client, email=owner_email, org_name=f"Audit Matrix Org {uid}", org_slug=f"aud-mat-{uid}"
    )
    org_id = uuid.UUID(owner_data["active_organization"]["id"])

    # 1. Owner: allowed (200)
    owner_logs = await api_client.get("/api/v1/organization/audit-logs")
    assert owner_logs.status_code == 200, (
        f"Owner must be permitted audit access, got {owner_logs.status_code}"
    )

    owner_events = await api_client.get("/api/v1/organization/audit-events")
    assert owner_events.status_code == 200, (
        f"Owner must be permitted audit access via alias, got {owner_events.status_code}"
    )

    # Fetch available role IDs while authenticated as Owner
    roles_res = await api_client.get("/api/v1/organization/roles")
    assert roles_res.status_code == 200
    roles = {r["slug"]: r["id"] for r in roles_res.json()}

    # Create invitations for admin, member, and viewer while owner session is active
    role_tokens: dict[str, str] = {}
    for role_slug in ["admin", "member", "viewer"]:
        invitee_email = f"{role_slug}_{uid}@example.com"
        inv_res = await api_client.post(
            "/api/v1/organization/invitations",
            json={"email": invitee_email, "role_id": roles[role_slug]},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
        )
        assert inv_res.status_code in (200, 201), f"Failed to invite {role_slug}"
        payload = inv_res.json()
        token = payload.get("token") or (payload.get("data") or {}).get("token")
        assert token is not None
        role_tokens[role_slug] = token

    user_repo = UserRepository(db_session)

    # 2. Test Admin role -> allowed (200)
    admin_email = f"admin_{uid}@example.com"
    _, admin_csrf = await register_and_login_helper(
        api_client, email=admin_email, org_name=f"Org Admin {uid}", org_slug=f"org-adm-{uid}"
    )
    admin_user = await user_repo.get_by_normalized_email(normalize_email_address(admin_email))
    assert admin_user is not None
    admin_user.email_verified_at = utc_now()
    await db_session.commit()

    accept_res = await api_client.post(
        "/api/v1/organization/invitations/accept",
        json={"token": role_tokens["admin"]},
        headers={"X-CSRF-Token": admin_csrf, "Origin": "http://localhost:3000"},
    )
    assert accept_res.status_code == 200
    switch_res = await api_client.post(
        "/api/v1/auth/select-tenant",
        json={"organization_id": str(org_id)},
        headers={"X-CSRF-Token": admin_csrf, "Origin": "http://localhost:3000"},
    )
    assert switch_res.status_code == 200

    admin_logs = await api_client.get("/api/v1/organization/audit-logs")
    assert admin_logs.status_code == 200, (
        f"Admin must have audit.read access, got {admin_logs.status_code}"
    )
    admin_events = await api_client.get("/api/v1/organization/audit-events")
    assert admin_events.status_code == 200, (
        f"Admin must have audit.read access via alias, got {admin_events.status_code}"
    )

    # 3. Test Member role -> denied (403)
    member_email = f"member_{uid}@example.com"
    _, member_csrf = await register_and_login_helper(
        api_client, email=member_email, org_name=f"Org Member {uid}", org_slug=f"org-mem-{uid}"
    )
    member_user = await user_repo.get_by_normalized_email(normalize_email_address(member_email))
    assert member_user is not None
    member_user.email_verified_at = utc_now()
    await db_session.commit()

    accept_res = await api_client.post(
        "/api/v1/organization/invitations/accept",
        json={"token": role_tokens["member"]},
        headers={"X-CSRF-Token": member_csrf, "Origin": "http://localhost:3000"},
    )
    assert accept_res.status_code == 200
    switch_res = await api_client.post(
        "/api/v1/auth/select-tenant",
        json={"organization_id": str(org_id)},
        headers={"X-CSRF-Token": member_csrf, "Origin": "http://localhost:3000"},
    )
    assert switch_res.status_code == 200

    member_logs = await api_client.get("/api/v1/organization/audit-logs")
    assert member_logs.status_code == 403, (
        f"Member must be DENIED audit access (403), got {member_logs.status_code}"
    )
    member_events = await api_client.get("/api/v1/organization/audit-events")
    assert member_events.status_code == 403, (
        f"Member must be DENIED audit access via alias (403), got {member_events.status_code}"
    )

    # 4. Test Viewer role -> denied (403)
    viewer_email = f"viewer_{uid}@example.com"
    _, viewer_csrf = await register_and_login_helper(
        api_client, email=viewer_email, org_name=f"Org Viewer {uid}", org_slug=f"org-viw-{uid}"
    )
    viewer_user = await user_repo.get_by_normalized_email(normalize_email_address(viewer_email))
    assert viewer_user is not None
    viewer_user.email_verified_at = utc_now()
    await db_session.commit()

    accept_res = await api_client.post(
        "/api/v1/organization/invitations/accept",
        json={"token": role_tokens["viewer"]},
        headers={"X-CSRF-Token": viewer_csrf, "Origin": "http://localhost:3000"},
    )
    assert accept_res.status_code == 200
    switch_res = await api_client.post(
        "/api/v1/auth/select-tenant",
        json={"organization_id": str(org_id)},
        headers={"X-CSRF-Token": viewer_csrf, "Origin": "http://localhost:3000"},
    )
    assert switch_res.status_code == 200

    viewer_logs = await api_client.get("/api/v1/organization/audit-logs")
    assert viewer_logs.status_code == 403, (
        f"Viewer must be DENIED audit access (403), got {viewer_logs.status_code}"
    )
    viewer_events = await api_client.get("/api/v1/organization/audit-events")
    assert viewer_events.status_code == 403, (
        f"Viewer must be DENIED audit access via alias (403), got {viewer_events.status_code}"
    )
