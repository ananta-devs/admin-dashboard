"""Integration tests for File Storage router: upload validation, quota tracking, cross-tenant isolation, and safe downloads."""

from __future__ import annotations

import base64
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.dependencies.auth import get_current_membership, verify_csrf_token
from app.db.session import get_db_session
from app.main import app
from app.models.organization import Organization
from app.models.user import User
from app.schemas.organization import ProvisionOrganizationInput
from app.services.organization import OrganizationService
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
        organization_name=f"File Org 1 {suffix}",
        organization_slug=f"file-org1-{suffix}",
        user_email=f"fileuser1-{suffix}@example.com",
        user_display_name=f"File User 1 {suffix}",
        default_workspace_name="Default Workspace",
        default_workspace_slug="default",
    )
    org_read, user_read, ws_read = await service.provision_organization_atomic(payload)
    org = await db_session.get(Organization, org_read.id)
    user = await db_session.get(User, user_read.id)
    from app.repositories.membership import MembershipRepository

    m_repo = MembershipRepository(db_session)
    membership = await m_repo.get(organization_id=org.id, user_id=user.id)
    return {"org": org, "user": user, "membership": membership}


@pytest.fixture
async def second_org(db_session: AsyncSession):
    service = OrganizationService(db_session)
    suffix = uuid.uuid4().hex[:6]
    payload = ProvisionOrganizationInput(
        organization_name=f"File Org 2 {suffix}",
        organization_slug=f"file-org2-{suffix}",
        user_email=f"fileuser2-{suffix}@example.com",
        user_display_name=f"File User 2 {suffix}",
        default_workspace_name="Default Workspace",
        default_workspace_slug="default",
    )
    org_read, user_read, ws_read = await service.provision_organization_atomic(payload)
    org = await db_session.get(Organization, org_read.id)
    user = await db_session.get(User, user_read.id)
    from app.repositories.membership import MembershipRepository

    m_repo = MembershipRepository(db_session)
    membership = await m_repo.get(organization_id=org.id, user_id=user.id)
    return {"org": org, "user": user, "membership": membership}


@pytest.mark.asyncio
async def test_file_upload_download_quota_lifecycle(test_org, session_factory):
    """Test full file upload, quota tracking, download, and deletion lifecycle."""
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
        # 1. Upload valid CSV file via JSON base64
        csv_bytes = b"id,name,revenue\n1,Alpha,1200\n2,Beta,3400\n"
        payload = {
            "filename": "dataset.csv",
            "folder": "/",
            "content_base64": base64.b64encode(csv_bytes).decode("ascii"),
            "content_type": "text/csv",
        }
        res_upload = await client.post("/api/v1/files/upload", json=payload)
        assert res_upload.status_code == 201, res_upload.text
        file_meta = res_upload.json()
        assert file_meta["filename"] == "dataset.csv"
        assert file_meta["size_bytes"] == len(csv_bytes)
        file_id = file_meta["id"]

        # 2. Check quota usage
        res_quota = await client.get("/api/v1/files/quota/usage")
        assert res_quota.status_code == 200
        quota_data = res_quota.json()
        assert quota_data["usage_bytes"] >= len(csv_bytes)
        assert quota_data["file_count"] >= 1

        # 3. Duplicate filename in same folder fails with 409
        res_dup = await client.post("/api/v1/files/upload", json=payload)
        assert res_dup.status_code == 409

        # 4. Download file
        res_download = await client.get(f"/api/v1/files/{file_id}/download")
        assert res_download.status_code == 200
        assert res_download.content == csv_bytes
        assert 'attachment; filename="dataset.csv"' in res_download.headers["content-disposition"]

        # 5. Delete file
        res_del = await client.delete(f"/api/v1/files/{file_id}")
        assert res_del.status_code == 204

        # 6. Verify file is deleted
        res_get_deleted = await client.get(f"/api/v1/files/{file_id}")
        assert res_get_deleted.status_code == 404

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_file_security_validations(test_org, session_factory):
    """Test rejection of prohibited extensions and signature mismatches."""
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
        # Prohibited executable extension
        payload_exe = {
            "filename": "malicious.exe",
            "content_base64": base64.b64encode(b"MZtestexecutable").decode("ascii"),
        }
        res_exe = await client.post("/api/v1/files/upload", json=payload_exe)
        assert res_exe.status_code in (400, 422)

        # Magic byte mismatch (claimed PNG but has plaintext)
        payload_bad_png = {
            "filename": "fake_image.png",
            "content_base64": base64.b64encode(b"NOT_A_PNG_FILE").decode("ascii"),
        }
        res_png = await client.post("/api/v1/files/upload", json=payload_bad_png)
        assert res_png.status_code in (400, 422)

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cross_tenant_file_isolation(test_org, second_org, session_factory):
    """Ensure Tenant 1 cannot access, download, or delete Tenant 2 files."""
    membership_1 = test_org["membership"]
    membership_2 = second_org["membership"]

    async def override_db():
        async with session_factory() as s:
            yield s

    async def override_csrf():
        return None

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[verify_csrf_token] = override_csrf

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Upload file as Tenant 2
        app.dependency_overrides[get_current_membership] = lambda: membership_2
        payload2 = {
            "filename": "tenant2_secret.json",
            "content_base64": base64.b64encode(b'{"secret": true}').decode("ascii"),
        }
        res_up = await client.post("/api/v1/files/upload", json=payload2)
        assert res_up.status_code == 201
        file_id_2 = res_up.json()["id"]

        # Switch to Tenant 1
        app.dependency_overrides[get_current_membership] = lambda: membership_1

        # Attempt to get metadata
        res_get = await client.get(f"/api/v1/files/{file_id_2}")
        assert res_get.status_code == 404

        # Attempt to download
        res_dl = await client.get(f"/api/v1/files/{file_id_2}/download")
        assert res_dl.status_code == 404

        # Attempt to delete
        res_del = await client.delete(f"/api/v1/files/{file_id_2}")
        assert res_del.status_code == 404

    app.dependency_overrides.clear()
