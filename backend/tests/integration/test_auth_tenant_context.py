import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import get_db_session
from app.db.transaction import transaction
from app.main import app
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.repositories.membership import MembershipRepository
from app.repositories.organization import OrganizationRepository


@pytest.fixture
async def multi_tenant_client():
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def get_test_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = get_test_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, session_factory

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_tenant_selection_and_membership_revocation(multi_tenant_client, unique_id: str):
    client, session_factory = multi_tenant_client

    email = f"tenant_{unique_id}@enterprise.com"
    password = "TenantPassword123!"

    # 1. Register with initial Organization 1
    reg_res = await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "display_name": f"Tenant User {unique_id}",
            "organization_name": f"Org One {unique_id}",
            "organization_slug": f"org-1-{unique_id}",
        },
    )
    assert reg_res.status_code == 201
    user_id = uuid.UUID(reg_res.json()["user"]["id"])
    org1_id = uuid.UUID(reg_res.json()["organization"]["id"])

    # 2. Login with exactly 1 membership -> active_organization is AUTO-SELECTED
    login1_res = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert login1_res.status_code == 200
    assert login1_res.json()["active_organization"]["id"] == str(org1_id)
    assert login1_res.json()["csrf_token"] is not None

    # 3. In DB, create Organization 2 and add user as member (now user has 2 memberships)
    org2_id = uuid.uuid4()
    org_unauthorized_id = uuid.uuid4()
    async with session_factory() as session:
        org_repo = OrganizationRepository(session)
        mem_repo = MembershipRepository(session)
        async with transaction(session):
            org2 = Organization(
                id=org2_id,
                name=f"Org Two {unique_id}",
                slug=f"org-2-{unique_id}",
            )
            await org_repo.create(org2)
            await mem_repo.create(OrganizationMembership(organization_id=org2_id, user_id=user_id))

            # Organization 3: user is NOT a member
            org3 = Organization(
                id=org_unauthorized_id,
                name=f"Org Three {unique_id}",
                slug=f"org-3-{unique_id}",
            )
            await org_repo.create(org3)

    # 4. Login with MULTIPLE memberships -> active_organization is NONE (no arbitrary DB inference)
    client.cookies.clear()
    login_multi_res = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert login_multi_res.status_code == 200
    assert login_multi_res.json()["active_organization"] is None
    csrf2 = login_multi_res.json()["csrf_token"]

    # /me also reports no active organization until selected
    me_res_no_org = await client.get("/api/v1/auth/me")
    assert me_res_no_org.status_code == 200
    assert me_res_no_org.json()["active_organization"] is None

    # 5. Explicitly switch active tenant to Organization 2 (allowed)
    select_res = await client.post(
        "/api/v1/auth/select-tenant",
        headers={"X-CSRF-Token": csrf2},
        json={"organization_id": str(org2_id)},
    )
    assert select_res.status_code == 200
    assert select_res.json()["active_organization_id"] == str(org2_id)

    # Confirm /me reflects the switch
    me_res = await client.get("/api/v1/auth/me")
    assert me_res.status_code == 200
    assert me_res.json()["active_organization"]["id"] == str(org2_id)

    # 6. Attempt to select unauthorized Organization 3 (rejected with 403)
    unauth_select = await client.post(
        "/api/v1/auth/select-tenant",
        headers={"X-CSRF-Token": csrf2},
        json={"organization_id": str(org_unauthorized_id)},
    )
    assert unauth_select.status_code == 403

    # 7. Revoke membership in Organization 2 directly in DB
    async with session_factory() as session:
        async with transaction(session):
            await session.execute(
                delete(OrganizationMembership).where(
                    OrganizationMembership.organization_id == org2_id,
                    OrganizationMembership.user_id == user_id,
                )
            )

    # 8. Attempting to select tenant 2 again now fails authoritatively in PostgreSQL
    revoked_select = await client.post(
        "/api/v1/auth/select-tenant",
        headers={"X-CSRF-Token": csrf2},
        json={"organization_id": str(org2_id)},
    )
    assert revoked_select.status_code == 403

    # 9. Test 0 memberships: user with no organization memberships has active_organization = None
    async with session_factory() as session:
        async with transaction(session):
            await session.execute(
                delete(OrganizationMembership).where(
                    OrganizationMembership.user_id == user_id,
                )
            )
    client.cookies.clear()
    login_zero_res = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert login_zero_res.status_code == 200
    assert login_zero_res.json()["active_organization"] is None
