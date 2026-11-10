"""Integration tests for System Health telemetry router: RBAC enforcement, subsystem diagnostics, and secret scrubbing."""

from __future__ import annotations

import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.dependencies.auth import get_current_membership, verify_csrf_token
from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.user import User
from app.schemas.organization import ProvisionOrganizationInput
from app.services.organization import OrganizationService
from app.services.role import RoleService


from tests.isolation_guard import assert_test_database_connection, get_enforced_test_database_url


@pytest.fixture
async def session_factory():
    db_url = get_enforced_test_database_url()
    engine = create_async_engine(db_url, poolclass=NullPool)
    await assert_test_database_connection(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
async def db_session(session_factory):
    async with session_factory() as session:
        yield session
        if session.in_transaction():
            await session.rollback()


@pytest.fixture
async def test_org(db_session: AsyncSession):
    service = OrganizationService(db_session)
    suffix = uuid.uuid4().hex[:6]
    payload = ProvisionOrganizationInput(
        organization_name=f"SysHealth Org {suffix}",
        organization_slug=f"syshealth-org-{suffix}",
        user_email=f"sysadmin-{suffix}@example.com",
        user_display_name=f"Sys Admin {suffix}",
        default_workspace_name="Default Workspace",
        default_workspace_slug="default",
    )
    org_read, user_read, _ = await service.provision_organization_atomic(payload)
    org = await db_session.get(Organization, org_read.id)
    user = await db_session.get(User, user_read.id)
    from app.repositories.membership import MembershipRepository
    m_repo = MembershipRepository(db_session)
    membership = await m_repo.get(organization_id=org.id, user_id=user.id)
    return {"org": org, "user": user, "membership": membership}


@pytest.fixture
async def member_user(db_session: AsyncSession, test_org):
    """Create a user with only a viewer/member role (lacking admin/audit permissions)."""
    org = test_org["org"]
    from app.repositories.role import RoleRepository
    role_repo = RoleRepository(db_session)
    viewer_role = await role_repo.get_by_slug("viewer", org.id)
    
    suffix = uuid.uuid4().hex[:6]
    email = f"viewer-{suffix}@example.com"
    user = User(
        email=email,
        normalized_email=email.lower(),
        display_name=f"Viewer {suffix}",
        email_verified_at=org.created_at,
    )
    db_session.add(user)
    await db_session.flush()

    membership = OrganizationMembership(
        organization_id=org.id,
        user_id=user.id,
    )
    db_session.add(membership)
    await db_session.flush()

    from app.models.role import MembershipRole
    m_role = MembershipRole(
        membership_id=membership.id,
        role_id=viewer_role.id,
        organization_id=org.id,
    )
    db_session.add(m_role)
    await db_session.commit()
    await db_session.refresh(membership)
    return membership


@pytest.mark.asyncio
async def test_system_telemetry_authorized_admin(test_org, session_factory):
    """Test that organization owner/admin can successfully retrieve system diagnostics."""
    membership = test_org["membership"]

    async def override_db():
        async with session_factory() as s:
            yield s

    async def override_membership():
        return membership

    async def override_csrf():
        return None

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_current_membership] = override_membership
    app.dependency_overrides[verify_csrf_token] = override_csrf

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/api/v1/system/telemetry")
            assert res.status_code == 200, res.text
            data = res.json()
            assert "overall_status" in data
            assert "subsystems" in data
            sub = data["subsystems"]
            assert "backend" in sub
            assert "database" in sub
            assert "redis" in sub
            assert "celery" in sub
            assert "analytics" in sub
            assert "storage" in sub
            assert "backup_recovery" in sub

            # Verify no secret leakage
            dumped = str(data)
            assert "password" not in dumped.lower()
            assert "postgres:" not in dumped
            assert "secret" not in dumped.lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_system_telemetry_rbac_forbidden_for_viewer(member_user, session_factory):
    """Test that viewer without organization.settings.update or audit.read gets 403 Forbidden."""
    async def override_db():
        async with session_factory() as s:
            yield s

    async def override_membership():
        return member_user

    async def override_csrf():
        return None

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_current_membership] = override_membership
    app.dependency_overrides[verify_csrf_token] = override_csrf

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            res = await client.get("/api/v1/system/telemetry")
            assert res.status_code == 403
    finally:
        app.dependency_overrides.clear()
