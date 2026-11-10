"""Concurrency and race condition tests for VIBE File Storage quota reservation (DEF-SEC-001 remediation).

Verifies:
1. Transaction-level PostgreSQL advisory lock serializes concurrent uploads for the same tenant.
2. Concurrent uploads attempting to exceed tenant quota: exactly one succeeds and one is rejected.
3. Database quota is never breached even under race conditions.
4. Independent tenants execute concurrently without contention.
"""

from __future__ import annotations

import asyncio
import uuid
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.exceptions import ValidationError
from app.models.organization import Organization
from app.models.user import User
from app.schemas.organization import ProvisionOrganizationInput
from app.services.file_storage import FileStorageService
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
        organization_name=f"Quota Org {suffix}",
        organization_slug=f"quota-org-{suffix}",
        user_email=f"quotauser-{suffix}@example.com",
        user_display_name=f"Quota User {suffix}",
        default_workspace_name="Default Workspace",
        default_workspace_slug="default",
    )
    org_read, user_read, ws_read = await service.provision_organization_atomic(payload)
    org = await db_session.get(Organization, org_read.id)
    user = await db_session.get(User, user_read.id)
    return {"org": org, "user": user}


@pytest.fixture
async def second_org(db_session: AsyncSession):
    service = OrganizationService(db_session)
    suffix = uuid.uuid4().hex[:6]
    payload = ProvisionOrganizationInput(
        organization_name=f"Quota Org2 {suffix}",
        organization_slug=f"quota-org2-{suffix}",
        user_email=f"quotauser2-{suffix}@example.com",
        user_display_name=f"Quota User 2 {suffix}",
        default_workspace_name="Default Workspace",
        default_workspace_slug="default",
    )
    org_read, user_read, ws_read = await service.provision_organization_atomic(payload)
    org = await db_session.get(Organization, org_read.id)
    user = await db_session.get(User, user_read.id)
    return {"org": org, "user": user}


@pytest.mark.asyncio
async def test_concurrent_uploads_prevent_quota_overrun(test_org, session_factory, monkeypatch):
    """Test that two simultaneous uploads competing for the same tenant quota are serialized by the PostgreSQL advisory lock."""
    org = test_org["org"]
    user = test_org["user"]

    quota_limit = 800
    monkeypatch.setattr("app.services.file_storage.DEFAULT_ORG_QUOTA_BYTES", quota_limit)

    file_content_1 = b"id,val\n" + (b"1,row_data_alpha_payload\n" * 20)  # ~500 bytes
    file_content_2 = b"id,val\n" + (b"2,row_data_beta_payload\n" * 20)   # ~500 bytes

    async def upload_task(fname: str, content: bytes):
        async with session_factory() as session:
            service = FileStorageService(session)
            return await service.upload_bytes(
                organization_id=org.id,
                user_id=user.id,
                filename=fname,
                content=content,
                content_type="text/csv",
                folder="/",
            )

    results = await asyncio.gather(
        upload_task("concurrent_1.csv", file_content_1),
        upload_task("concurrent_2.csv", file_content_2),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    errors = [r for r in results if isinstance(r, Exception)]

    assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}: {results}"
    assert len(errors) == 1, f"Expected exactly 1 error, got {len(errors)}: {errors}"
    assert isinstance(errors[0], ValidationError)
    assert "quota exceeded" in str(errors[0]).lower()

    # Verify database state has exactly 1 file and does not exceed quota
    async with session_factory() as session:
        service = FileStorageService(session)
        quota_read = await service.get_quota_usage(org.id)
        assert quota_read.file_count == 1
        assert quota_read.usage_bytes <= quota_limit
        assert quota_read.usage_bytes in (len(file_content_1), len(file_content_2))


@pytest.mark.asyncio
async def test_cross_tenant_uploads_execute_independently(test_org, second_org, session_factory):
    """Test that concurrent uploads from different tenants do not block each other."""
    org1 = test_org["org"]
    user1 = test_org["user"]
    org2 = second_org["org"]
    user2 = second_org["user"]

    csv_data = b"col1,col2\nval1,val2\n"

    async def upload_tenant(org_id, user_id, fname):
        async with session_factory() as session:
            service = FileStorageService(session)
            return await service.upload_bytes(
                organization_id=org_id,
                user_id=user_id,
                filename=fname,
                content=csv_data,
                content_type="text/csv",
            )

    t1, t2 = await asyncio.gather(
        upload_tenant(org1.id, user1.id, "t1.csv"),
        upload_tenant(org2.id, user2.id, "t2.csv"),
    )

    assert t1.organization_id == org1.id
    assert t2.organization_id == org2.id
    assert t1.filename == "t1.csv"
    assert t2.filename == "t2.csv"
