"""Real OS Process-Kill crash tests across all 12 analytical pipeline stages."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
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
from app.services.spreadsheet import SpreadsheetService
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

    # Populate sheet with 600 rows (in 2 batches of 300 to respect 500 batch limit)
    # to ensure Parquet result artifact generation (>500 rows)
    row_updates_1 = [CellEditInput(cell_ref=f"A{i + 1}", value=float(i * 1.5)) for i in range(300)]
    row_updates_2 = [
        CellEditInput(cell_ref=f"A{i + 301}", value=float((i + 300) * 1.5)) for i in range(300)
    ]
    await spreadsheet_svc.update_cells_batch(
        sheet_id=sheets[0].id,
        organization_id=org_id,
        user_id=user_id,
        data=BatchCellUpdateInput(updates=row_updates_1),
    )
    await spreadsheet_svc.update_cells_batch(
        sheet_id=sheets[0].id,
        organization_id=org_id,
        user_id=user_id,
        data=BatchCellUpdateInput(updates=row_updates_2),
    )
    await db_session.commit()

    return org_id, ws.id, wb.id, sheets[0].id, user_id, csrf_token


def _spawn_worker_process(
    job_id: uuid.UUID, crash_stage: str | None = None
) -> tuple[int, str, str]:
    """Launch real independent OS process executing the analytics worker task."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path("backend").resolve())
    if crash_stage:
        env["VIBE_CRASH_STAGE"] = crash_stage
    else:
        env.pop("VIBE_CRASH_STAGE", None)

    python_exe = sys.executable

    proc = subprocess.Popen(
        [python_exe, "-m", "app.tasks.run_worker_job", str(job_id)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    stdout, stderr = proc.communicate(timeout=30)
    print("SUBPROCESS CODE:", proc.returncode, "OUT:", stdout, "ERR:", stderr)
    return proc.returncode, stdout, stderr


async def _create_queued_job(
    db_session: AsyncSession,
    org_id: uuid.UUID,
    ws_id: uuid.UUID,
    wb_id: uuid.UUID,
    sheet_id: uuid.UUID,
    user_id: uuid.UUID,
    operation_id: str = "filter_dataset",
) -> AnalyticsJob:
    job = AnalyticsJob(
        id=uuid.uuid4(),
        organization_id=org_id,
        workspace_id=ws_id,
        source_workbook_id=wb_id,
        source_sheet_id=sheet_id,
        requested_by_user_id=user_id,
        operation_id=operation_id,
        parameters={
            "filters": [{"column": "A", "operator": "greater_than_or_equal", "value": 0.0}]
        },
        status=AnalyticsJobStatus.QUEUED,
        attempt_count=0,
        max_attempts=3,
        source_sheet_version=1,
    )
    db_session.add(job)
    await db_session.commit()
    return job


async def get_fresh_job(job_id: uuid.UUID) -> AnalyticsJob:
    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        return await AnalyticsJobRepository(session).get_by_id(job_id)


async def get_fresh_result(job_id: uuid.UUID, org_id: uuid.UUID) -> AnalyticsResult | None:
    from app.db.session import async_session_maker

    async with async_session_maker() as session:
        return await AnalyticsResultRepository(session).get_by_job_id_scoped(job_id, org_id)


@pytest.mark.asyncio
async def test_crash_stage_1_during_snapshot_extraction(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Stage 1: Process killed during snapshot extraction (before DB commit)."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "kill_s1"
    )
    job = await _create_queued_job(db_session, org_id, ws_id, wb_id, sheet_id, user_id)

    code, stdout, stderr = _spawn_worker_process(job.id, "STAGE_1_SNAPSHOT_EXTRACT")
    assert code == 99  # Process terminated abruptly by test hook

    # Verify DB: snapshot metadata was NOT committed
    await db_session.rollback()
    job_repo = AnalyticsJobRepository(db_session)
    cur_job = await job_repo.get_by_id(job.id)
    assert cur_job is not None
    assert cur_job.input_snapshot_id is None
    assert cur_job.result_id is None


@pytest.mark.asyncio
async def test_crash_stage_2_after_snapshot_commit(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Stage 2: Process killed between snapshot DB commit and task dispatch/execution."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "kill_s2"
    )
    job = await _create_queued_job(db_session, org_id, ws_id, wb_id, sheet_id, user_id)

    code, stdout, stderr = _spawn_worker_process(job.id, "STAGE_2_AFTER_SNAPSHOT_COMMIT")
    assert code == 99

    await db_session.rollback()
    job_repo = AnalyticsJobRepository(db_session)
    cur_job = await job_repo.get_by_id(job.id)
    assert cur_job is not None
    assert cur_job.input_snapshot_id is not None
    assert Path(cur_job.input_snapshot_id).is_file()
    assert cur_job.result_id is None


@pytest.mark.asyncio
async def test_crash_stage_3_before_claim(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Stage 3: Process killed before worker claim transaction."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "kill_s3"
    )
    job = await _create_queued_job(db_session, org_id, ws_id, wb_id, sheet_id, user_id)

    code, stdout, stderr = _spawn_worker_process(job.id, "STAGE_3_BEFORE_CLAIM")
    assert code == 99

    cur_job = await get_fresh_job(job.id)
    assert cur_job is not None
    assert cur_job.status == AnalyticsJobStatus.QUEUED
    assert cur_job.worker_id is None


@pytest.mark.asyncio
async def test_crash_stage_4_during_claim(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Stage 4: Process killed during worker claim transaction."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "kill_s4"
    )
    job = await _create_queued_job(db_session, org_id, ws_id, wb_id, sheet_id, user_id)

    code, stdout, stderr = _spawn_worker_process(job.id, "STAGE_4_DURING_CLAIM")
    assert code == 99

    cur_job = await get_fresh_job(job.id)
    assert cur_job is not None
    # Rolled back by Postgres on socket termination -> still QUEUED
    assert cur_job.status == AnalyticsJobStatus.QUEUED


@pytest.mark.asyncio
async def test_crash_stage_5_before_heartbeat(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Stage 5: Process killed after claim, before heartbeat thread starts."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "kill_s5"
    )
    job = await _create_queued_job(db_session, org_id, ws_id, wb_id, sheet_id, user_id)

    code, stdout, stderr = _spawn_worker_process(job.id, "STAGE_5_BEFORE_HEARTBEAT")
    assert code == 99

    cur_job = await get_fresh_job(job.id)
    assert cur_job is not None
    assert cur_job.status == AnalyticsJobStatus.RUNNING
    assert cur_job.claim_token is not None


@pytest.mark.asyncio
async def test_crash_stage_6_during_compute(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Stage 6: Process killed during Polars analytical computation."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "kill_s6"
    )
    job = await _create_queued_job(db_session, org_id, ws_id, wb_id, sheet_id, user_id)

    code, stdout, stderr = _spawn_worker_process(job.id, "STAGE_6_DURING_COMPUTE")
    assert code == 99

    await db_session.rollback()
    storage = AnalyticsStorageService()
    res_dir = storage.results_dir / str(org_id) / str(job.id)
    # No result artifacts promoted
    if res_dir.is_dir():
        assert len(list(res_dir.glob("*.parquet"))) == 0


@pytest.mark.asyncio
async def test_crash_stage_7_during_parquet_write(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Stage 7: Process killed during Parquet write to temp path."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "kill_s7"
    )
    job = await _create_queued_job(db_session, org_id, ws_id, wb_id, sheet_id, user_id)

    code, stdout, stderr = _spawn_worker_process(job.id, "STAGE_7_DURING_PARQUET_WRITE")
    assert code == 99

    await db_session.rollback()
    storage = AnalyticsStorageService()
    res_dir = storage.results_dir / str(org_id) / str(job.id)
    # Final parquet does NOT exist
    if res_dir.is_dir():
        assert len(list(res_dir.glob("*.parquet"))) == 0


@pytest.mark.asyncio
async def test_crash_stage_8_before_promote(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Stage 8: Process killed between temp Parquet write and atomic promotion."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "kill_s8"
    )
    job = await _create_queued_job(db_session, org_id, ws_id, wb_id, sheet_id, user_id)

    code, stdout, stderr = _spawn_worker_process(job.id, "STAGE_8_BEFORE_PROMOTE")
    assert code == 99

    await db_session.rollback()
    storage = AnalyticsStorageService()
    res_dir = storage.results_dir / str(org_id) / str(job.id)
    if res_dir.is_dir():
        # Final promoted parquet must not exist
        assert len(list(res_dir.glob("*.parquet"))) == 0


@pytest.mark.asyncio
async def test_crash_stage_9_before_db_commit(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Stage 9: Process killed between atomic promotion and DB result commit."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "kill_s9"
    )
    job = await _create_queued_job(db_session, org_id, ws_id, wb_id, sheet_id, user_id)

    code, stdout, stderr = _spawn_worker_process(job.id, "STAGE_9_BEFORE_DB_COMMIT")
    assert code == 99

    await db_session.rollback()
    # DB result does not exist
    result_repo = AnalyticsResultRepository(db_session)
    res = await result_repo.get_by_job_id_scoped(job.id, org_id)
    assert res is None


@pytest.mark.asyncio
async def test_crash_stage_10_during_db_commit(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Stage 10: Process killed during DB result commit transaction."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "kill_s10"
    )
    job = await _create_queued_job(db_session, org_id, ws_id, wb_id, sheet_id, user_id)

    code, stdout, stderr = _spawn_worker_process(job.id, "STAGE_10_DURING_DB_COMMIT")
    assert code == 99

    await db_session.rollback()
    # Postgres rolled back transaction -> no result in DB
    result_repo = AnalyticsResultRepository(db_session)
    res = await result_repo.get_by_job_id_scoped(job.id, org_id)
    assert res is None


@pytest.mark.asyncio
async def test_crash_stage_11_after_db_commit(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Stage 11: Process killed between DB result commit and outbox completion."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "kill_s11"
    )
    job = await _create_queued_job(db_session, org_id, ws_id, wb_id, sheet_id, user_id)

    code, stdout, stderr = _spawn_worker_process(job.id, "STAGE_11_AFTER_DB_COMMIT")
    assert code == 99

    # DB result WAS committed and job marked succeeded before crash!
    cur_job = await get_fresh_job(job.id)
    assert cur_job is not None
    assert cur_job.status == AnalyticsJobStatus.SUCCEEDED
    assert cur_job.result_id is not None

    res = await get_fresh_result(job.id, org_id)
    assert res is not None
    assert res.artifact_path is not None
    assert Path(res.artifact_path).is_file()


@pytest.mark.asyncio
async def test_crash_stage_12_during_publishing_rollback(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Stage 12: Process killed during publishing between sheet creation and cell write."""
    org_id, ws_id, wb_id, sheet_id, user_id, csrf_token = await create_test_context(
        api_client, db_session, "kill_s12"
    )

    # Create succeeded job and result
    job = await _create_queued_job(db_session, org_id, ws_id, wb_id, sheet_id, user_id)
    job.status = AnalyticsJobStatus.SUCCEEDED
    res_id = uuid.uuid4()
    job.result_id = res_id
    res = AnalyticsResult(
        id=res_id,
        job_id=job.id,
        organization_id=org_id,
        result_type="filter_dataset",
        result_payload={"data": [{"val": 10.0}, {"val": 20.0}]},
        row_count=2,
        column_count=1,
    )
    db_session.add(res)
    await db_session.commit()

    # Launch subprocess attempting to publish with STAGE_12_DURING_PUBLISH
    script = f"""
import os, sys, asyncio, uuid
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool
from app.core.config import settings
from app.services.analytics import AnalyticsService

async def run():
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        svc = AnalyticsService(session)
        await svc.publish_result_to_sheet(
            result_id=uuid.UUID('{res_id}'),
            organization_id=uuid.UUID('{org_id}'),
            user_id=uuid.UUID('{user_id}'),
            destination_workbook_id=uuid.UUID('{wb_id}'),
            sheet_name='CrashSheet12',
        )
    await engine.dispose()

asyncio.run(run())
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path("backend").resolve())
    env["VIBE_CRASH_STAGE"] = "STAGE_12_DURING_PUBLISH"

    proc = subprocess.Popen(
        [sys.executable, "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdout, stderr = proc.communicate(timeout=30)
    assert proc.returncode == 99

    # Verify transaction rollback: CrashSheet12 does NOT exist in the workbook!
    spreadsheet_svc = SpreadsheetService(db_session)
    sheets = await spreadsheet_svc.list_sheets(wb_id, org_id)
    sheet_names = [s.name for s in sheets]
    assert "CrashSheet12" not in sheet_names


@pytest.mark.asyncio
async def test_watchdog_recovers_dead_worker_and_second_worker_succeeds(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Verifies that when a worker process dies abruptly, watchdog recovers the lease and a second worker executes cleanly."""
    from app.db.session import async_session_maker

    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "kill_watchdog"
    )
    job = await _create_queued_job(db_session, org_id, ws_id, wb_id, sheet_id, user_id)

    # Worker 1 crashes at Stage 6 (during computation)
    code, _, _ = _spawn_worker_process(job.id, "STAGE_6_DURING_COMPUTE")
    assert code == 99

    # Use fresh session for lease expiry update and watchdog
    async with async_session_maker() as fresh_s:
        job_repo = AnalyticsJobRepository(fresh_s)
        dead_job = await job_repo.get_by_id(job.id)
        assert dead_job is not None
        assert dead_job.status == AnalyticsJobStatus.RUNNING

        # Expire lease timestamp in DB to simulate lease timeout detection
        dead_job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=120)
        dead_job.updated_at = datetime.now(UTC) - timedelta(seconds=120)
        await fresh_s.commit()

        # Run watchdog lease recovery
        from app.tasks.dispatcher import OutboxDispatcher

        dispatcher = OutboxDispatcher()
        await dispatcher.recover_stale_leases()

    recovered_job = await get_fresh_job(job.id)
    assert recovered_job is not None
    assert recovered_job.status == AnalyticsJobStatus.RETRYING
    assert recovered_job.attempt_count == 1
    assert recovered_job.claim_generation == 1

    # Worker 2 process spawns without crash hook
    code2, stdout2, stderr2 = _spawn_worker_process(job.id, crash_stage=None)
    assert code2 == 0
    assert "succeeded" in stdout2

    final_job = await get_fresh_job(job.id)
    assert final_job is not None
    assert final_job.status == AnalyticsJobStatus.SUCCEEDED
    assert final_job.result_id is not None
    assert final_job.claim_generation == 2
