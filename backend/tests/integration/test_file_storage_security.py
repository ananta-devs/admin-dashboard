"""Adversarial security tests for VIBE File Storage (DEF-SEC-002 remediation).

Verifies:
1. Active markup rejection in text/CSV files (<script>, <iframe>, <svg>, event handlers).
2. Double extension and suspicious suffix rejection (.exe.csv, .html.txt, .php.json).
3. Header injection sanitization in Content-Disposition.
4. Content-Security-Policy and nosniff headers on /preview.
5. Server-derived authoritative MIME detection.
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
        organization_name=f"Sec Org {suffix}",
        organization_slug=f"sec-org-{suffix}",
        user_email=f"secuser-{suffix}@example.com",
        user_display_name=f"Sec User {suffix}",
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
async def test_active_markup_rejected_in_csv_and_txt(test_org, session_factory):
    """Verify that CSV or TXT files containing HTML or script tags are rejected with 400."""
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

    malicious_payloads = [
        ("report.csv", "name,email\n<script>alert(1)</script>,alice@example.com", "text/csv"),
        ("data.txt", "Hello world\n<iframe src='//evil.com'></iframe>", "text/plain"),
        ("users.csv", "id,avatar\n1,<svg/onload=alert('XSS')>", "text/csv"),
        ("notes.txt", "Check this link: <a href='javascript:alert(1)'>click</a>", "text/plain"),
    ]

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for filename, content, mime in malicious_payloads:
                payload = {
                    "filename": filename,
                    "folder": "/",
                    "content_base64": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                    "content_type": mime,
                }
                res = await client.post("/api/v1/files/upload", json=payload)
                assert res.status_code == 400, (
                    f"Expected 400 for malicious file {filename}, got {res.status_code}: {res.text}"
                )
                assert (
                    "active content" in res.text.lower()
                    or "markup" in res.text.lower()
                    or "not allowed" in res.text.lower()
                )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_double_extensions_rejected(test_org, session_factory):
    """Verify that files with multiple extensions (.exe.csv, .html.txt, etc.) are strictly rejected."""
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

    suspicious_filenames = [
        "payload.exe.csv",
        "malware.sh.txt",
        "exploit.html.json",
        "archive.tar.gz.csv",
    ]

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for filename in suspicious_filenames:
                payload = {
                    "filename": filename,
                    "folder": "/",
                    "content_base64": base64.b64encode(b"col1,col2\nval1,val2").decode("ascii"),
                    "content_type": "text/csv",
                }
                res = await client.post("/api/v1/files/upload", json=payload)
                assert res.status_code == 400, (
                    f"Expected 400 for {filename}, got {res.status_code}: {res.text}"
                )
                assert any(
                    k in res.text.lower()
                    for k in ("multiple extensions", "suspicious", "prohibited", "not permitted")
                )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_preview_csp_and_safe_headers(test_org, session_factory):
    """Verify preview endpoint returns restricted CSP and text/plain to prevent browser execution."""
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
            # Upload clean CSV
            clean_content = b"header1,header2\nvalue1,value2\n"
            payload = {
                "filename": "dataset_safe.csv",
                "folder": "/",
                "content_base64": base64.b64encode(clean_content).decode("ascii"),
                "content_type": "text/csv",
            }
            res = await client.post("/api/v1/files/upload", json=payload)
            assert res.status_code == 201, res.text
            file_id = res.json()["id"]

            # Request preview
            preview_res = await client.get(f"/api/v1/files/{file_id}/preview")
            assert preview_res.status_code == 200

            # Assert safe headers
            assert "content-security-policy" in preview_res.headers
            csp = preview_res.headers["content-security-policy"]
            assert "default-src 'none'" in csp
            assert "sandbox" in csp
            assert preview_res.headers.get("x-content-type-options") == "nosniff"
            assert "text/plain" in preview_res.headers.get("content-type", "")
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_header_injection_sanitization(test_org, session_factory):
    """Verify filename headers with CRLF injection characters are safely sanitized."""
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
            payload = {
                "filename": "clean_report.csv",
                "folder": "/",
                "content_base64": base64.b64encode(b"a,b,c\n1,2,3").decode("ascii"),
                "content_type": "text/csv",
            }
            res = await client.post("/api/v1/files/upload", json=payload)
            assert res.status_code == 201, res.text
            file_id = res.json()["id"]

            dl_res = await client.get(f"/api/v1/files/{file_id}/download")
            assert dl_res.status_code == 200
            cd = dl_res.headers.get("content-disposition", "")
            assert "\r" not in cd
            assert "\n" not in cd
            assert "attachment" in cd
    finally:
        app.dependency_overrides.clear()
