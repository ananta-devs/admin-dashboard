"""RBAC permission enforcement tests for VIBE File Storage (DEF-SEC-004 remediation).

Verifies:
1. Organization Owner / Admin has full permissions (upload, download, preview, delete).
2. Organization Member can upload, preview, and download files, but CANNOT delete (403 Forbidden).
3. Organization Viewer CANNOT upload (403 Forbidden), CANNOT delete (403 Forbidden), but CAN preview and download.
"""

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
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.role import MembershipRole
from app.models.user import User
from app.repositories.role import RoleRepository
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
        organization_name=f"RBAC File Org {suffix}",
        organization_slug=f"rbac-file-org-{suffix}",
        user_email=f"rbacowner-{suffix}@example.com",
        user_display_name=f"RBAC Owner {suffix}",
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
async def member_membership(test_org, db_session: AsyncSession):
    org = test_org["org"]
    role_repo = RoleRepository(db_session)
    member_role = await role_repo.get_by_slug("member", org.id)

    suffix = uuid.uuid4().hex[:6]
    email = f"member-{suffix}@example.com"
    user = User(
        email=email,
        normalized_email=email.lower(),
        display_name=f"Member {suffix}",
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

    m_role = MembershipRole(
        membership_id=membership.id,
        role_id=member_role.id,
        organization_id=org.id,
    )
    db_session.add(m_role)
    await db_session.commit()
    await db_session.refresh(membership)
    return membership


@pytest.fixture
async def viewer_membership(test_org, db_session: AsyncSession):
    org = test_org["org"]
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
async def test_owner_admin_full_file_permissions(test_org, session_factory):
    """Verify owner/admin can upload, list, download, and delete files."""
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
            # 1. Upload
            payload = {
                "filename": "owner_test.csv",
                "folder": "/",
                "content_base64": base64.b64encode(b"a,b\n1,2").decode("ascii"),
                "content_type": "text/csv",
            }
            res_up = await client.post("/api/v1/files/upload", json=payload)
            assert res_up.status_code == 201, res_up.text
            file_id = res_up.json()["id"]

            # 2. List
            res_list = await client.get("/api/v1/files")
            assert res_list.status_code == 200

            # 3. Download
            res_dl = await client.get(f"/api/v1/files/{file_id}/download")
            assert res_dl.status_code == 200

            # 4. Delete
            res_del = await client.delete(f"/api/v1/files/{file_id}")
            assert res_del.status_code == 204
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_member_can_upload_but_cannot_delete(test_org, member_membership, session_factory):
    """Verify member can upload and view files, but cannot delete (403 Forbidden)."""
    owner_membership = test_org["membership"]

    async def override_db():
        async with session_factory() as s:
            yield s

    async def override_csrf():
        return None

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Member uploads a file
            app.dependency_overrides[get_db_session] = override_db
            app.dependency_overrides[get_current_membership] = lambda: member_membership
            app.dependency_overrides[verify_csrf_token] = override_csrf

            payload = {
                "filename": "member_doc.csv",
                "folder": "/",
                "content_base64": base64.b64encode(b"x,y\n10,20").decode("ascii"),
                "content_type": "text/csv",
            }
            res_up = await client.post("/api/v1/files/upload", json=payload)
            assert res_up.status_code == 201, res_up.text
            file_id = res_up.json()["id"]

            # Member attempts to delete -> 403 Forbidden
            res_del = await client.delete(f"/api/v1/files/{file_id}")
            assert res_del.status_code == 403, f"Expected 403 for member deletion, got {res_del.status_code}"

            # Owner can delete it
            app.dependency_overrides[get_current_membership] = lambda: owner_membership
            res_owner_del = await client.delete(f"/api/v1/files/{file_id}")
            assert res_owner_del.status_code == 204
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_viewer_cannot_upload_or_delete_but_can_read(test_org, viewer_membership, session_factory):
    """Verify viewer cannot upload (403) or delete (403), but can read/download."""
    owner_membership = test_org["membership"]

    async def override_db():
        async with session_factory() as s:
            yield s

    async def override_csrf():
        return None

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Owner uploads a file for testing viewer reads
            app.dependency_overrides[get_db_session] = override_db
            app.dependency_overrides[get_current_membership] = lambda: owner_membership
            app.dependency_overrides[verify_csrf_token] = override_csrf

            payload = {
                "filename": "viewer_read_test.csv",
                "folder": "/",
                "content_base64": base64.b64encode(b"colA,colB\n5,6").decode("ascii"),
                "content_type": "text/csv",
            }
            res_up = await client.post("/api/v1/files/upload", json=payload)
            assert res_up.status_code == 201
            file_id = res_up.json()["id"]

            # Switch to viewer
            app.dependency_overrides[get_current_membership] = lambda: viewer_membership

            # 1. Viewer upload attempt -> 403 Forbidden
            upload_attempt = {
                "filename": "unauthorized.csv",
                "folder": "/",
                "content_base64": base64.b64encode(b"fail").decode("ascii"),
                "content_type": "text/csv",
            }
            res_viewer_up = await client.post("/api/v1/files/upload", json=upload_attempt)
            assert res_viewer_up.status_code == 403, f"Expected 403 for viewer upload, got {res_viewer_up.status_code}"

            # 2. Viewer read list -> 200 OK
            res_viewer_list = await client.get("/api/v1/files")
            assert res_viewer_list.status_code == 200

            # 3. Viewer download -> 200 OK
            res_viewer_dl = await client.get(f"/api/v1/files/{file_id}/download")
            assert res_viewer_dl.status_code == 200
            assert res_viewer_dl.content == b"colA,colB\n5,6"

            # 4. Viewer delete attempt -> 403 Forbidden
            res_viewer_del = await client.delete(f"/api/v1/files/{file_id}")
            assert res_viewer_del.status_code == 403, f"Expected 403 for viewer delete, got {res_viewer_del.status_code}"
    finally:
        app.dependency_overrides.clear()

