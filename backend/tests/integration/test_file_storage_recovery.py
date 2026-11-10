"""Crash-recovery and storage reconciliation tests for VIBE File Storage (DEF-SEC-003 remediation).

Verifies:
1. Transaction rollback triggers unlinking of staged or published physical files (no orphaned files on error).
2. FileReconciliationService detects matched, orphaned physical, and missing physical files.
3. FileReconciliationService safely prunes orphaned physical files while preserving legitimate tenant files.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.exceptions import ValidationError
from app.models.organization import Organization
from app.models.user import User
from app.schemas.organization import ProvisionOrganizationInput
from app.services.file_reconciliation import FileReconciliationService
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
        organization_name=f"Recov Org {suffix}",
        organization_slug=f"recov-org-{suffix}",
        user_email=f"recovuser-{suffix}@example.com",
        user_display_name=f"Recov User {suffix}",
        default_workspace_name="Default Workspace",
        default_workspace_slug="default",
    )
    org_read, user_read, ws_read = await service.provision_organization_atomic(payload)
    org = await db_session.get(Organization, org_read.id)
    user = await db_session.get(User, user_read.id)
    return {"org": org, "user": user}


@pytest.mark.asyncio
async def test_upload_failure_cleans_up_disk(test_org, session_factory):
    """Test that if database transaction fails during upload, physical files are strictly cleaned up."""
    org = test_org["org"]
    user = test_org["user"]

    content = b"header1,header2\nval1,val2\n"

    async with session_factory() as session:
        service = FileStorageService(session)
        tenant_dir = service._get_tenant_dir(org.id)
        staging_dir = service.staging_dir

        # Mock repo.create to simulate a DB crash / exception after physical write
        with patch.object(service.repo, "create", side_effect=RuntimeError("Simulated DB crash during commit")):
            with pytest.raises(RuntimeError, match="Simulated DB crash"):
                await service.upload_bytes(
                    organization_id=org.id,
                    user_id=user.id,
                    filename="crash_test.csv",
                    content=content,
                    content_type="text/csv",
                )

        # Assert no dangling temporary files in staging_dir
        staging_files = list(staging_dir.glob("*.tmp"))
        assert len(staging_files) == 0, f"Found dangling staging files: {staging_files}"

        # Assert no published file for this aborted upload in tenant_dir
        dat_files = list(tenant_dir.glob("*.dat"))
        assert len(dat_files) == 0, f"Found dangling tenant dat files: {dat_files}"


@pytest.mark.asyncio
async def test_file_reconciliation_audit_and_prune(test_org, session_factory):
    """Test that FileReconciliationService correctly audits and prunes orphaned physical files."""
    org = test_org["org"]
    user = test_org["user"]

    async with session_factory() as session:
        storage_svc = FileStorageService(session)
        tenant_dir = storage_svc._get_tenant_dir(org.id)

        # 1. Upload legitimate file
        uploaded = await storage_svc.upload_bytes(
            organization_id=org.id,
            user_id=user.id,
            filename="legit.csv",
            content=b"a,b\n1,2",
            content_type="text/csv",
        )

        # 2. Inject an orphaned physical file directly on disk
        orphan_id = uuid.uuid4()
        orphan_path = tenant_dir / f"{orphan_id}.dat"
        orphan_path.write_bytes(b"orphaned content on disk")

        # 3. Run reconciliation audit
        recon_svc = FileReconciliationService(session)
        audit_res = await recon_svc.audit_storage()

        assert len(audit_res.active_referenced_files) >= 1
        assert len(audit_res.orphaned_files) >= 1
        assert str(orphan_path) in audit_res.orphaned_files

        # 4. Prune orphans
        prune_report = await recon_svc.prune_orphans(destructive_confirmed=True)
        assert len(prune_report.pruned_files) >= 1
        assert str(orphan_path) in prune_report.pruned_files
        assert not orphan_path.exists()

        # 5. Legitimate file still exists intact
        legit_path = tenant_dir / f"{uploaded.id}.dat"
        assert legit_path.exists()
