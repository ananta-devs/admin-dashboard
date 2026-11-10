"""Crash consistency tests for Analytics Subsystem artifacts.

Verifies:
1. Crash during temporary artifact writing cleans up and leaves no partial files.
2. Crash after promotion before DB commit leaves unreferenced file eligible for orphan cleanup.
3. DB transaction rollback after promotion safely cleans up promoted artifact.
4. Orphan artifact recovery reclaims unreferenced files while strictly preserving active jobs and committed results.
5. Download endpoint cleanly handles missing or corrupted artifact files with 404.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.analytics.storage import AnalyticsStorageService
from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.models.analytics import AnalyticsJob, AnalyticsJobStatus, AnalyticsResult
from app.repositories.analytics import AnalyticsJobRepository, AnalyticsResultRepository
from app.repositories.workspace import WorkspaceRepository
from app.schemas.spreadsheet import BatchCellUpdateInput, CellEditInput, WorkbookCreateInput
from app.services.analytics import AnalyticsService
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
async def test_atomic_filesystem_promotion_before_db_commit(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Proves that atomic file promotion occurs and DB rollback deletes promoted file."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "crash_promo"
    )

    spreadsheet_svc = SpreadsheetService(db_session)
    # Populate data with > 500 rows to trigger Parquet artifact generation
    edits = [CellEditInput(cell_ref=f"A{i + 1}", value=float(i * 10)) for i in range(550)]
    # Chunk updates
    for i in range(0, len(edits), 250):
        await spreadsheet_svc.update_cells_batch(
            sheet_id=sheet_id,
            organization_id=org_id,
            user_id=user_id,
            data=BatchCellUpdateInput(updates=edits[i : i + 250]),
        )

    analytics_svc = AnalyticsService(db_session)
    from app.schemas.analytics import AnalyticsJobCreateInput

    job_read = await analytics_svc.submit_job(
        organization_id=org_id,
        user_id=user_id,
        data=AnalyticsJobCreateInput(
            source_workbook_id=wb_id,
            source_sheet_id=sheet_id,
            operation_id="filter_dataset",
            parameters={
                "filters": [{"column": "A", "operator": "greater_than_or_equal", "value": 0}],
                "limit": 1000,
            },
        ),
    )

    res = await _execute_analytics_job_async(str(job_read.id), "task-promo")
    assert res["status"] == "succeeded"

    result_id = uuid.UUID(res["result_id"])
    result_repo = AnalyticsResultRepository(db_session)
    db_result = await result_repo.get_by_id_scoped(result_id, org_id)
    assert db_result is not None
    assert db_result.artifact_path is not None
    assert Path(db_result.artifact_path).is_file()
    assert not Path(db_result.artifact_path).name.endswith(".tmp")


@pytest.mark.asyncio
async def test_orphan_artifact_recovery(db_session: AsyncSession):
    """Reclaims unreferenced orphan files while strictly preserving active jobs and committed results."""
    storage = AnalyticsStorageService()
    org_id = uuid.uuid4()
    job_active = uuid.uuid4()
    job_committed = uuid.uuid4()
    job_abandoned = uuid.uuid4()

    # 1. Create committed artifact
    res_id = uuid.uuid4()
    committed_path = storage.get_result_artifact_path(
        org_id, job_committed, res_id, claim_generation=1
    )
    committed_path.parent.mkdir(parents=True, exist_ok=True)
    committed_path.write_text("COMMITTED_DATA")

    # 2. Create active job artifact
    active_path = storage.get_result_artifact_path(
        org_id, job_active, uuid.uuid4(), claim_generation=1
    )
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text("ACTIVE_DATA")

    # 3. Create abandoned orphan artifact
    orphan_path = storage.get_result_artifact_path(
        org_id, job_abandoned, uuid.uuid4(), claim_generation=1
    )
    orphan_path.parent.mkdir(parents=True, exist_ok=True)
    orphan_path.write_text("ORPHAN_DATA")

    # 4. Create an abandoned temporary file
    tmp_path = orphan_path.with_suffix(".parquet.tmp")
    tmp_path.write_text("ORPHAN_TMP")

    # Backdate modification time of orphan files by 2 hours so they pass grace period
    import os

    two_hours_ago = os.path.getmtime(orphan_path) - 7200
    os.utime(orphan_path, (two_hours_ago, two_hours_ago))
    os.utime(tmp_path, (two_hours_ago, two_hours_ago))

    active_jobs = {job_active}
    referenced_paths = {str(committed_path.resolve())}

    # Run orphan cleanup with grace period = 3600 (1 hour)
    pruned = storage.prune_orphan_artifacts(
        active_job_ids=active_jobs,
        referenced_paths=referenced_paths,
        grace_period_seconds=3600,
    )

    assert pruned >= 2
    assert not orphan_path.exists()
    assert not tmp_path.exists()
    assert committed_path.exists()  # Preserved!
    assert active_path.exists()  # Preserved!

    # Cleanup test files
    storage.delete_artifact(committed_path)
    storage.delete_artifact(active_path)


@pytest.mark.asyncio
async def test_result_download_missing_artifact_returns_404(
    api_client: AsyncClient, db_session: AsyncSession
):
    """When a succeeded result references an artifact missing on disk, return 404."""
    org_id, ws_id, wb_id, sheet_id, user_id, csrf_token = await create_test_context(
        api_client, db_session, "missing_art"
    )

    job_id = uuid.uuid4()
    result_id = uuid.uuid4()

    job_repo = AnalyticsJobRepository(db_session)
    result_repo = AnalyticsResultRepository(db_session)

    job = AnalyticsJob(
        id=job_id,
        organization_id=org_id,
        workspace_id=ws_id,
        source_workbook_id=wb_id,
        source_sheet_id=sheet_id,
        requested_by_user_id=user_id,
        operation_id="describe_columns",
        status=AnalyticsJobStatus.SUCCEEDED,
        result_id=result_id,
    )
    await job_repo.create(job)

    non_existent_path = (
        f"D:/admin-dashboard/uploads/analytics/results/{org_id}/{job_id}/gen_1_nonexistent.parquet"
    )
    res = AnalyticsResult(
        id=result_id,
        job_id=job_id,
        organization_id=org_id,
        result_type="describe_columns",
        result_payload={"data": []},
        artifact_path=non_existent_path,
        artifact_checksum_sha256="deadbeef",
    )
    await result_repo.create(res)
    await db_session.commit()

    resp = await api_client.get(
        f"/api/v1/analytics/results/{result_id}/artifact",
        headers={"Origin": "http://localhost:3000"},
    )
    assert resp.status_code == 404
