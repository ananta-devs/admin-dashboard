"""Integration tests for Google OAuth 2.0 connection, token vault, and RBAC."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.security import decrypt_integration_token, encrypt_integration_token
from app.db.session import get_db_session
from app.main import app
from app.models.integration import ExternalIntegration
from app.models.role import MembershipRole
from app.models.user import User
from app.repositories.membership import MembershipRepository
from app.repositories.role import RoleRepository
from app.repositories.user import UserRepository
from app.services.google_oauth import GoogleOAuthService
from app.services.session import SessionService


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


@pytest.fixture(autouse=True)
async def cleanup_global_engine():
    yield
    from app.db.session import engine

    await engine.dispose()


async def register_and_login_helper(
    client: AsyncClient,
    email: str,
    password: str = "SecurePass1234!",
) -> tuple[dict[str, Any], str, uuid.UUID]:
    uid = uuid.uuid4().hex[:8]
    org_name = f"Org {uid}"
    org_slug = f"org-{uid}"

    reg_payload = {
        "email": email,
        "password": password,
        "display_name": f"User {uid}",
        "organization_name": org_name,
        "organization_slug": org_slug,
    }
    reg_res = await client.post(
        "/api/v1/auth/register",
        json=reg_payload,
        headers={"Origin": "http://localhost:3000"},
    )
    assert reg_res.status_code == 201

    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": "http://localhost:3000"},
    )
    assert login_res.status_code == 200
    data: dict[str, Any] = login_res.json()
    csrf_token: str = str(data.get("csrf_token") or login_res.cookies.get("csrf_token") or "")
    org_id = uuid.UUID(data["active_organization"]["id"])
    return data, csrf_token, org_id


@pytest.mark.asyncio
async def test_google_oauth_full_lifecycle(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Test full Google OAuth lifecycle: auth-url, callback, token vault, listing, spreadsheets, disconnect."""
    uid = uuid.uuid4().hex[:8]
    owner_email = f"owner_oauth_{uid}@example.com"
    _, csrf_token, org_id = await register_and_login_helper(api_client, owner_email)

    # 1. Generate Auth URL
    auth_url_res = await api_client.get("/api/v1/integrations/google/auth-url")
    assert auth_url_res.status_code == 200
    auth_data = auth_url_res.json()
    assert "https://accounts.google.com/o/oauth2/v2/auth" in auth_data["auth_url"]
    assert "state=" in auth_data["auth_url"]
    state_token = auth_data["state_token"]

    # 2. Invalid Callback (tampered state token) -> 400
    invalid_callback = await api_client.post(
        "/api/v1/integrations/google/callback",
        json={"code": f"mock_code_{uid}", "state": "invalid_tampered_state"},
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )
    assert invalid_callback.status_code == 400
    assert "Invalid, mismatched, or expired OAuth state token" in invalid_callback.text

    # 3. Valid Callback (single-use consumption of state)
    valid_callback = await api_client.post(
        "/api/v1/integrations/google/callback",
        json={"code": f"mock_code_{uid}", "state": state_token},
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )
    assert valid_callback.status_code == 201
    integration_data = valid_callback.json()
    integration_id = uuid.UUID(integration_data["id"])
    assert integration_data["provider"] == "google"
    assert "example.com" in integration_data["account_email"]
    assert integration_data["status"] == "active"
    # Never expose tokens to client
    assert "access_token" not in integration_data
    assert "refresh_token" not in integration_data
    assert "encrypted_access_token" not in integration_data

    # 4. State token cannot be reused
    reused_callback = await api_client.post(
        "/api/v1/integrations/google/callback",
        json={"code": f"mock_code_{uid}", "state": state_token},
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )
    assert reused_callback.status_code == 400

    # 5. List Integrations
    list_res = await api_client.get("/api/v1/integrations")
    assert list_res.status_code == 200
    items = list_res.json()
    assert len(items) == 1
    assert items[0]["id"] == str(integration_id)

    # 6. Browse Spreadsheets
    spreadsheets_res = await api_client.get(f"/api/v1/integrations/{integration_id}/spreadsheets")
    assert spreadsheets_res.status_code == 200
    sheets_list = spreadsheets_res.json()
    assert len(sheets_list) >= 1
    target_spreadsheet = sheets_list[0]
    spreadsheet_id = target_spreadsheet["id"]

    # 7. Get Spreadsheet Detail
    detail_res = await api_client.get(
        f"/api/v1/integrations/{integration_id}/spreadsheets/{spreadsheet_id}"
    )
    assert detail_res.status_code == 200
    detail = detail_res.json()
    assert len(detail["sheets"]) >= 1

    # 8. Get Worksheet Values Preview
    values_res = await api_client.get(
        f"/api/v1/integrations/{integration_id}/spreadsheets/{spreadsheet_id}/values?range_name=Summary!A1:Z100"
    )
    assert values_res.status_code == 200
    values_data = values_res.json()
    assert len(values_data["values"]) > 0

    # 9. Disconnect Integration
    del_res = await api_client.delete(
        f"/api/v1/integrations/{integration_id}",
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )
    assert del_res.status_code == 200

    # Verify integration was removed
    post_del_list = await api_client.get("/api/v1/integrations")
    assert len(post_del_list.json()) == 0


@pytest.mark.asyncio
async def test_google_oauth_token_refresh_and_vault_encryption(
    db_session: AsyncSession,
) -> None:
    """Verify that tokens are encrypted with AES-256-GCM and refreshed transparently when expired."""
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()

    raw_access = "secret_access_token_12345"
    raw_refresh = "mock_refresh_token_abcdef"

    encrypted_access = encrypt_integration_token(raw_access)
    encrypted_refresh = encrypt_integration_token(raw_refresh)

    # Must be AES-GCM ciphertext format v1:nonce:ciphertext
    assert encrypted_access.startswith("v1:")
    assert encrypted_refresh.startswith("v1:")
    assert raw_access not in encrypted_access
    assert raw_refresh not in encrypted_refresh

    # Decrypt verification
    assert decrypt_integration_token(encrypted_access) == raw_access
    assert decrypt_integration_token(encrypted_refresh) == raw_refresh

    # Verify auto-refresh when expired
    service = GoogleOAuthService(db_session)
    expired_time = datetime.now(UTC) - timedelta(minutes=5)

    integration = ExternalIntegration(
        id=uuid.uuid4(),
        organization_id=org_id,
        connected_by_user_id=user_id,
        provider="google",
        account_email="refresh_test@example.com",
        encrypted_access_token=encrypted_access,
        encrypted_refresh_token=encrypted_refresh,
        token_expires_at=expired_time,
        status="active",
    )

    valid_token = await service.get_valid_access_token(integration)
    assert valid_token.startswith("mock_access_token_")
    assert integration.token_expires_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_google_oauth_tenant_isolation_and_rbac(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Verify tenant isolation and RBAC: Viewer cannot manage integrations, Tenant B cannot access Tenant A."""
    uid = uuid.uuid4().hex[:8]
    org_a_email = f"owner_a_{uid}@example.com"
    _, csrf_a, org_a_id = await register_and_login_helper(api_client, org_a_email)

    # Connect Google integration in Org A
    auth_res = await api_client.get("/api/v1/integrations/google/auth-url")
    state_token = auth_res.json()["state_token"]
    conn_res = await api_client.post(
        "/api/v1/integrations/google/callback",
        json={"code": f"mock_code_{uid}", "state": state_token},
        headers={"X-CSRF-Token": csrf_a, "Origin": "http://localhost:3000"},
    )
    int_a_id = conn_res.json()["id"]

    # Register Org B Owner
    org_b_email = f"owner_b_{uid}@example.com"
    _, csrf_b, org_b_id = await register_and_login_helper(api_client, org_b_email)

    # 1. Tenant B CANNOT see Org A integration
    list_b = await api_client.get("/api/v1/integrations")
    assert len(list_b.json()) == 0

    # 2. Tenant B CANNOT get Org A integration by ID -> 404
    get_cross = await api_client.get(f"/api/v1/integrations/{int_a_id}")
    assert get_cross.status_code == 404

    # 3. Tenant B CANNOT disconnect Org A integration -> 404
    del_cross = await api_client.delete(
        f"/api/v1/integrations/{int_a_id}",
        headers={"X-CSRF-Token": csrf_b, "Origin": "http://localhost:3000"},
    )
    assert del_cross.status_code == 404

    # 4. RBAC: Create Viewer in Org A
    viewer_email = f"viewer_{uid}@example.com"
    user_repo = UserRepository(db_session)
    viewer_user = await user_repo.create(
        User(
            email=viewer_email,
            normalized_email=viewer_email.lower(),
            display_name="Viewer User",
        )
    )
    role_repo = RoleRepository(db_session)
    viewer_role = await role_repo.get_by_slug("viewer", org_a_id)
    assert viewer_role is not None

    from app.models.membership import OrganizationMembership

    mem_repo = MembershipRepository(db_session)
    viewer_mem = await mem_repo.create(
        OrganizationMembership(
            user_id=viewer_user.id,
            organization_id=org_a_id,
        )
    )
    db_session.add(
        MembershipRole(
            membership_id=viewer_mem.id,
            role_id=viewer_role.id,
            organization_id=org_a_id,
        )
    )
    await db_session.commit()

    sess_service = SessionService(db_session)
    _, viewer_token, viewer_csrf = await sess_service.create_session(
        user_id=viewer_user.id,
        ip_address="127.0.0.1",
        user_agent="pytest",
        active_organization_id=org_a_id,
    )
    await db_session.commit()

    api_client.cookies.set(settings.COOKIE_NAME, viewer_token)

    # Viewer lacks integrations.manage -> 403 Forbidden
    v_auth = await api_client.get("/api/v1/integrations/google/auth-url")
    assert v_auth.status_code == 403

    v_disconnect = await api_client.delete(
        f"/api/v1/integrations/{int_a_id}",
        headers={"X-CSRF-Token": viewer_csrf, "Origin": "http://localhost:3000"},
    )
    assert v_disconnect.status_code == 403
