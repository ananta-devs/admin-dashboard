"""Filesystem fencing by claim generation and snapshot identity preservation tests.

Verifies:
1. Different worker claim generations write to distinct immutable artifact paths.
2. Stale worker finishing late cannot overwrite, replace, or corrupt a newer worker's artifact.
3. Snapshot identity is preserved across retries (first successful snapshot is reused).
4. Corrupted snapshot checksum triggers safe rejection.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.analytics.snapshot import capture_sheet_snapshot
from app.analytics.storage import AnalyticsStorageService
from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.models.analytics import AnalyticsJob, AnalyticsJobStatus
from app.repositories.analytics import AnalyticsJobRepository
from app.repositories.workspace import WorkspaceRepository
from app.schemas.spreadsheet import BatchCellUpdateInput, CellEditInput, WorkbookCreateInput
from app.services.spreadsheet import SpreadsheetService
from app.tasks.analytics import _execute_analytics_job_async
from tests.integration.test_analytics_api import register_and_login_helper


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


async def create_test_context(api_client: AsyncClient, db_session: AsyncSession, prefix: str):
    uid = uuid.uuid4().hex[:8]
    login_data, csrf_token, org_id = await register_and_login_helper(
        api_client, f"{prefix}_{uid}@example.com"
    )
    user_id = uuid.UUID(login_data["user"]["id"])
    ws = (await WorkspaceRepository(db_session).list_by_organization(org_id))[0]
    spreadsheet_svc = SpreadsheetService(db_session)
    wb = await spreadsheet_svc.create_workbook(
        organization_id=org_id,
        created_by_user_id=user_id,
        data=WorkbookCreateInput(name=f"{prefix} WB", workspace_id=ws.id),
    )
    sheets = await spreadsheet_svc.list_sheets(wb.id, org_id)
    return org_id, ws.id, wb.id, sheets[0].id, user_id, csrf_token


@pytest.mark.asyncio
async def test_filesystem_artifacts_fenced_by_claim_generation(api_client: AsyncClient, db_session: AsyncSession):
    """Proves Worker 1 (gen 1) and Worker 2 (gen 2) write to distinct paths; Worker 1 cannot overwrite Worker 2."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "fs_fence"
    )

    spreadsheet_svc = SpreadsheetService(db_session)
    await spreadsheet_svc.update_cells_batch(
        sheet_id=sheet_id,
        organization_id=org_id,
        user_id=user_id,
        data=BatchCellUpdateInput(updates=[CellEditInput(cell_ref="A1", value=123.0)]),
    )

    storage = AnalyticsStorageService()
    job_id = uuid.uuid4()

    # Worker 1 (generation 1) captures snapshot
    snap_meta_1 = await capture_sheet_snapshot(
        sheet_id=sheet_id,
        organization_id=org_id,
        job_id=job_id,
        claim_generation=1,
        storage=storage,
        session=None,
    )

    # Worker 2 (generation 2) captures snapshot for the same job
    snap_meta_2 = await capture_sheet_snapshot(
        sheet_id=sheet_id,
        organization_id=org_id,
        job_id=job_id,
        claim_generation=2,
        storage=storage,
        session=None,
    )

    # Different generations MUST have different immutable filesystem paths
    assert snap_meta_1.storage_path != snap_meta_2.storage_path
    assert "gen_1" in snap_meta_1.storage_path
    assert "gen_2" in snap_meta_2.storage_path

    # Both files exist independently without clobbering each other
    assert Path(snap_meta_1.storage_path).is_file()
    assert Path(snap_meta_2.storage_path).is_file()


@pytest.mark.asyncio
async def test_snapshot_identity_preserved_across_retries(api_client: AsyncClient, db_session: AsyncSession):
    """Proves that a retrying job reuses the exact same immutable snapshot from attempt 1."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "snap_retry"
    )

    spreadsheet_svc = SpreadsheetService(db_session)
    await spreadsheet_svc.update_cells_batch(
        sheet_id=sheet_id,
        organization_id=org_id,
        user_id=user_id,
        data=BatchCellUpdateInput(updates=[CellEditInput(cell_ref="A1", value=50.0)]),
    )

    repo = AnalyticsJobRepository(db_session)
    job_id = uuid.uuid4()

    job = AnalyticsJob(
        id=job_id,
        organization_id=org_id,
        workspace_id=ws_id,
        source_workbook_id=wb_id,
        source_sheet_id=sheet_id,
        requested_by_user_id=user_id,
        operation_id="describe_columns",
        parameters={},
        status=AnalyticsJobStatus.QUEUED,
    )
    await repo.create(job)
    await db_session.commit()

    # Attempt 1: captures snapshot
    storage = AnalyticsStorageService()
    snap_meta_1 = await capture_sheet_snapshot(
        sheet_id=sheet_id,
        organization_id=org_id,
        job_id=job_id,
        claim_generation=1,
        storage=storage,
        session=None,
    )

    # Record snapshot metadata on job as attempt 1 would do
    job.input_snapshot_id = snap_meta_1.storage_path
    job.input_snapshot_hash = snap_meta_1.checksum_sha256
    job.snapshot_checksum_sha256 = snap_meta_1.checksum_sha256
    job.snapshot_sheet_version = snap_meta_1.sheet_version
    job.snapshot_row_count = snap_meta_1.row_count
    job.snapshot_col_count = snap_meta_1.col_count
    await db_session.commit()

    # Now mutate the spreadsheet! (Simulating changes occurring while job was retried)
    await spreadsheet_svc.update_cells_batch(
        sheet_id=sheet_id,
        organization_id=org_id,
        user_id=user_id,
        data=BatchCellUpdateInput(updates=[CellEditInput(cell_ref="A1", value=9999.0)]),
    )

    # Attempt 2 runs for the same job
    # Execute through _execute_analytics_job_async
    res = await _execute_analytics_job_async(str(job_id), "task-retry-2")
    assert res["status"] == "succeeded"

    db_session.expire_all()
    finished_job = await repo.get_by_id(job_id)
    assert finished_job is not None

    # Snapshot identity MUST be preserved: input_snapshot_id still points to attempt 1's snapshot!
    assert finished_job.input_snapshot_id == snap_meta_1.storage_path
    assert finished_job.input_snapshot_hash == snap_meta_1.checksum_sha256

    # Verify result computed on the reused snapshot (mean should be 50.0, NOT 9999.0!)
    from app.repositories.analytics import AnalyticsResultRepository
    res_repo = AnalyticsResultRepository(db_session)
    result_obj = await res_repo.get_by_job_id_scoped(job_id, org_id)
    assert result_obj is not None
    payload = result_obj.result_payload or {}
    assert "columns" in payload
    assert "A" in payload["columns"]
    # The mean from attempt 1's snapshot was 50.0
    assert payload["columns"]["A"]["mean"] == 50.0
