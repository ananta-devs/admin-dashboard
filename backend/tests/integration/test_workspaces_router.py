"""Integration tests for Workspaces Router: CRUD, RBAC, tenant isolation, cascade protection, and stats."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.dependencies.auth import get_current_membership, get_current_session, verify_csrf_token
from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.session import AuthenticationSession
from app.models.spreadsheet import Workbook
from app.models.user import User
from app.models.workspace import Workspace
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
    """Provision a clean test organization with default workspace and owner."""
    service = OrganizationService(db_session)
    suffix = uuid.uuid4().hex[:6]
    payload = ProvisionOrganizationInput(
        organization_name=f"Workspace Test Org {suffix}",
        organization_slug=f"ws-org-{suffix}",
        user_email=f"owner-{suffix}@example.com",
        user_display_name=f"Owner {suffix}",
        default_workspace_name="Default Workspace",
        default_workspace_slug="default",
    )
    org_read, user_read, ws_read = await service.provision_organization_atomic(payload)

    # Fetch full models
    org = await db_session.get(Organization, org_read.id)
    user = await db_session.get(User, user_read.id)
    ws = await db_session.get(Workspace, ws_read.id)
    from app.repositories.membership import MembershipRepository
    m_repo = MembershipRepository(db_session)
    membership = await m_repo.get(organization_id=org.id, user_id=user.id)

    return {"org": org, "user": user, "workspace": ws, "membership": membership}


@pytest.fixture
async def second_org(db_session: AsyncSession):
    """Provision a second tenant organization for isolation testing."""
    service = OrganizationService(db_session)
    suffix = uuid.uuid4().hex[:6]
    payload = ProvisionOrganizationInput(
        organization_name=f"Tenant Two Org {suffix}",
        organization_slug=f"tenant2-{suffix}",
        user_email=f"owner2-{suffix}@example.com",
        user_display_name=f"Owner Two {suffix}",
        default_workspace_name="Tenant 2 Default",
        default_workspace_slug="default",
    )
    org_read, user_read, ws_read = await service.provision_organization_atomic(payload)
    org = await db_session.get(Organization, org_read.id)
    user = await db_session.get(User, user_read.id)
    ws = await db_session.get(Workspace, ws_read.id)
    from app.repositories.membership import MembershipRepository
    m_repo = MembershipRepository(db_session)
    membership = await m_repo.get(organization_id=org.id, user_id=user.id)
    return {"org": org, "user": user, "workspace": ws, "membership": membership}


@pytest.mark.asyncio
async def test_list_and_create_workspaces(test_org, session_factory):
    """Test listing existing workspaces and creating a new one."""
    org = test_org["org"]
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. List workspaces
        res = await client.get("/api/v1/workspaces")
        assert res.status_code == 200, res.text
        data = res.json()
        assert len(data) >= 1
        assert any(w["slug"] == "default" for w in data)

        # 2. Create workspace
        create_payload = {
            "name": "Finance Analytics",
            "slug": "finance-analytics",
        }
        res_create = await client.post("/api/v1/workspaces", json=create_payload)
        assert res_create.status_code == 201, res_create.text
        created = res_create.json()
        assert created["name"] == "Finance Analytics"
        assert created["slug"] == "finance-analytics"
        assert created["organization_id"] == str(org.id)

        # 3. Duplicate slug fails
        res_dup = await client.post("/api/v1/workspaces", json=create_payload)
        assert res_dup.status_code == 409

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cross_tenant_workspace_isolation(test_org, second_org, session_factory):
    """Ensure user from Tenant 1 cannot read or modify Tenant 2 workspaces (yields 404)."""
    membership_1 = test_org["membership"]
    ws_2 = second_org["workspace"]

    async def override_db():
        async with session_factory() as s:
            yield s

    async def override_membership():
        return membership_1

    async def override_csrf():
        return None

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_current_membership] = override_membership
    app.dependency_overrides[verify_csrf_token] = override_csrf

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Attempt to access Tenant 2 workspace with Tenant 1 membership
        res = await client.get(f"/api/v1/workspaces/{ws_2.id}")
        assert res.status_code == 404, "Cross-tenant access must return 404 anti-enumeration"

        # Attempt to delete Tenant 2 workspace
        res_del = await client.delete(f"/api/v1/workspaces/{ws_2.id}")
        assert res_del.status_code == 404

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_safe_workspace_deletion_guardrails(test_org, session_factory, db_session):
    """Ensure default workspace is protected and workspaces with active resources cannot be deleted."""
    org = test_org["org"]
    user = test_org["user"]
    default_ws = test_org["workspace"]
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

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Attempt to delete default workspace -> 409 Conflict
        res_def = await client.delete(f"/api/v1/workspaces/{default_ws.id}")
        assert res_def.status_code == 409
        assert "default" in res_def.text.lower()

        # 2. Create custom workspace
        res_create = await client.post(
            "/api/v1/workspaces",
            json={"name": "Project Apollo", "slug": "project-apollo"},
        )
        assert res_create.status_code == 201
        custom_ws_id = res_create.json()["id"]

        # 3. Attach a workbook to Project Apollo
        wb = Workbook(
            organization_id=org.id,
            workspace_id=uuid.UUID(custom_ws_id),
            name="Apollo Budget",
            created_by_user_id=user.id,
        )
        db_session.add(wb)
        await db_session.commit()

        # 4. Attempt to delete Project Apollo with active workbook -> 409 Conflict
        res_del_blocked = await client.delete(f"/api/v1/workspaces/{custom_ws_id}")
        assert res_del_blocked.status_code == 409
        assert "workbooks" in res_del_blocked.text.lower()

        # 5. Remove workbook and retry deletion -> 204 No Content
        await db_session.delete(wb)
        await db_session.commit()

        res_del_ok = await client.delete(f"/api/v1/workspaces/{custom_ws_id}")
        assert res_del_ok.status_code == 204

    app.dependency_overrides.clear()
