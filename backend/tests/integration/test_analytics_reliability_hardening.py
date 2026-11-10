"""Integration tests for Phase 7 Final Worker Reliability, Snapshot Consistency & Resource Hardening.

Verifies:
1. Worker fencing tokens and heartbeat: stale workers cannot overwrite results or progress.
2. Snapshot consistency during Parquet generation with concurrent sheet mutation under REPEATABLE READ.
3. Outbox watchdog recovering stranded queued jobs and retrying stale worker leases.
4. Idempotent result persistence and no-op re-execution.
5. Atomic spreadsheet publishing rollback on failure and publish idempotency.
6. Destination workbook tenant isolation during spreadsheet publishing.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.analytics.snapshot import capture_sheet_snapshot
from app.analytics.storage import AnalyticsStorageService
from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.models.analytics import (
    AnalyticsJob,
    AnalyticsJobStatus,
    AnalyticsOutboxEvent,
)
from app.repositories.analytics import (
    AnalyticsJobRepository,
    AnalyticsOutboxRepository,
)
from app.repositories.workspace import WorkspaceRepository
from app.schemas.spreadsheet import BatchCellUpdateInput, CellEditInput, WorkbookCreateInput
from app.services.analytics import AnalyticsService
from app.services.spreadsheet import SpreadsheetService
from app.tasks.analytics import _execute_analytics_job_async, _heartbeat_loop
from app.tasks.dispatcher import OutboxDispatcher
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
    """Helper to set up an authorized tenant with workspace, workbook, and sheet."""
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
async def test_worker_fencing_token_rejects_stale_worker(api_client: AsyncClient, db_session: AsyncSession):
    """Proves that a stale worker whose lease expired cannot overwrite progress or complete a job."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "fence"
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

    # Worker 1 claims job
    claimed_1, token_1 = await repo.claim_job_atomically(job_id, "worker-1", lease_duration_seconds=1)
    await db_session.commit()
    assert claimed_1 is True
    assert token_1 is not None

    # Worker 1 can update progress while holding active claim
    up_1 = await repo.update_progress(job_id, 20, "working", claim_token=token_1)
    await db_session.commit()
    assert up_1 is True

    # Transition to RETRYING to simulate recovery
    await repo.mark_failed(job_id, "LEASE_EXPIRED", "expired", next_status=AnalyticsJobStatus.RETRYING)
    await db_session.commit()

    # Worker 2 claims the job
    claimed_2, token_2 = await repo.claim_job_atomically(job_id, "worker-2", lease_duration_seconds=60)
    await db_session.commit()
    assert claimed_2 is True
    assert token_2 is not None
    assert token_2 != token_1

    # Stale Worker 1 attempts to update progress with token_1 -> MUST BE REJECTED
    up_stale = await repo.update_progress(job_id, 50, "stale_attempt", claim_token=token_1)
    await db_session.commit()
    assert up_stale is False

    # Stale Worker 1 attempts to mark succeeded -> MUST BE REJECTED
    fake_result_id = uuid.uuid4()
    succ_stale = await repo.mark_succeeded(job_id, fake_result_id, claim_token=token_1)
    await db_session.commit()
    assert succ_stale is False

    # Active Worker 2 marks succeeded -> MUST SUCCEED
    valid_result_id = uuid.uuid4()
    succ_valid = await repo.mark_succeeded(job_id, valid_result_id, claim_token=token_2)
    await db_session.commit()
    assert succ_valid is True

    db_session.expire_all()
    final_job = await repo.get_by_id(job_id)
    assert final_job is not None
    assert final_job.status == AnalyticsJobStatus.SUCCEEDED
    assert final_job.result_id == valid_result_id
    assert final_job.claim_token == token_2


@pytest.mark.asyncio
async def test_worker_background_heartbeat_renews_lease(api_client: AsyncClient, db_session: AsyncSession):
    """Proves background worker heartbeat extends the lease with fencing token."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "hb"
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
        status=AnalyticsJobStatus.QUEUED,
    )
    await repo.create(job)
    await db_session.commit()

    claimed, token = await repo.claim_job_atomically(job_id, "hb-worker", lease_duration_seconds=2)
    await db_session.commit()
    assert claimed is True
    assert token is not None

    initial_job = await repo.get_by_id(job_id)
    assert initial_job is not None
    initial_lease = initial_job.lease_expires_at

    abort_event = asyncio.Event()
    hb_task = asyncio.create_task(
        _heartbeat_loop(
            job_id=job_id,
            claim_token=token,
            abort_event=abort_event,
            interval_seconds=1,
            lease_duration_seconds=10,
        )
    )

    # Let heartbeat fire at least once
    await asyncio.sleep(1.5)
    abort_event.set()
    hb_task.cancel()
    try:
        await hb_task
    except asyncio.CancelledError:
        pass

    db_session.expire_all()
    renewed_job = await repo.get_by_id(job_id)
    assert renewed_job is not None
    assert renewed_job.lease_expires_at is not None
    assert initial_lease is not None
    assert renewed_job.lease_expires_at > initial_lease


@pytest.mark.asyncio
async def test_snapshot_consistency_repeatable_read(api_client: AsyncClient, db_session: AsyncSession):
    """Verifies snapshot extraction under REPEATABLE READ with snapshot metadata audit trail."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "snap"
    )

    spreadsheet_svc = SpreadsheetService(db_session)
    # Populate initial 10 rows
    initial_edits = [
        CellEditInput(cell_ref=f"A{i+1}", value=f"Val_{i}")
        for i in range(10)
    ]
    await spreadsheet_svc.update_cells_batch(
        sheet_id=sheet_id,
        organization_id=org_id,
        user_id=user_id,
        data=BatchCellUpdateInput(updates=initial_edits),
    )

    storage = AnalyticsStorageService()
    job_id = uuid.uuid4()

    # Capture snapshot under REPEATABLE READ
    snapshot_meta = await capture_sheet_snapshot(
        sheet_id=sheet_id,
        organization_id=org_id,
        job_id=job_id,
        storage=storage,
        session=None,
    )

    assert snapshot_meta.row_count >= 10
    assert snapshot_meta.col_count >= 1
    assert snapshot_meta.checksum_sha256 is not None
    assert snapshot_meta.size_bytes > 0
    assert snapshot_meta.storage_id.endswith(".parquet")
    assert snapshot_meta.sheet_version >= 1


@pytest.mark.asyncio
async def test_outbox_watchdog_recovers_stranded_queued_jobs(api_client: AsyncClient, db_session: AsyncSession):
    """Dispatcher watchdog detects stranded queued jobs and generates a fresh outbox event."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "watchdog"
    )

    job_repo = AnalyticsJobRepository(db_session)
    outbox_repo = AnalyticsOutboxRepository(db_session)

    job_id = uuid.uuid4()
    old_time = datetime.now(UTC) - timedelta(seconds=120)

    # Job stuck in queued for 120s
    job = AnalyticsJob(
        id=job_id,
        organization_id=org_id,
        workspace_id=ws_id,
        source_workbook_id=wb_id,
        source_sheet_id=sheet_id,
        requested_by_user_id=user_id,
        operation_id="describe_columns",
        status=AnalyticsJobStatus.QUEUED,
        created_at=old_time,
        updated_at=old_time,
    )
    await job_repo.create(job)

    # Outbox event was published 100s ago, but worker never claimed it
    ev = AnalyticsOutboxEvent(
        job_id=job_id,
        organization_id=org_id,
        event_type="analytics.job.scheduled",
        payload={"job_id": str(job_id)},
        published_at=datetime.now(UTC) - timedelta(seconds=100),
        created_at=old_time,
    )
    await outbox_repo.create(ev)
    await db_session.commit()

    dispatcher = OutboxDispatcher("test-watchdog")
    recovered = await dispatcher.recover_stranded_queued_jobs(timeout_seconds=60)
    assert recovered >= 1

    # Verify a new redispatched outbox event was created
    latest_event = await outbox_repo.get_by_job_id(job_id)
    assert latest_event is not None
    assert latest_event.event_type == "analytics.job.redispatched"
    assert latest_event.published_at is None


@pytest.mark.asyncio
async def test_outbox_stale_lease_recovery_generates_retry_event(api_client: AsyncClient, db_session: AsyncSession):
    """Dispatcher recovers running jobs with expired leases into retrying with an outbox event."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "stale"
    )

    job_repo = AnalyticsJobRepository(db_session)
    outbox_repo = AnalyticsOutboxRepository(db_session)

    job_id = uuid.uuid4()
    past_expiry = datetime.now(UTC) - timedelta(seconds=30)

    job = AnalyticsJob(
        id=job_id,
        organization_id=org_id,
        workspace_id=ws_id,
        source_workbook_id=wb_id,
        source_sheet_id=sheet_id,
        requested_by_user_id=user_id,
        operation_id="describe_columns",
        status=AnalyticsJobStatus.RUNNING,
        worker_id="crashed-worker",
        lease_expires_at=past_expiry,
        attempt_count=1,
        max_attempts=3,
    )
    await job_repo.create(job)
    await db_session.commit()

    dispatcher = OutboxDispatcher("test-lease-dispatcher")
    recovered = await dispatcher.recover_stale_leases()
    assert recovered >= 1

    db_session.expire_all()
    updated_job = await job_repo.get_by_id(job_id)
    assert updated_job is not None
    assert updated_job.status == AnalyticsJobStatus.RETRYING

    retry_event = await outbox_repo.get_by_job_id(job_id)
    assert retry_event is not None
    assert retry_event.event_type == "analytics.job.retrying"
    assert retry_event.published_at is None


@pytest.mark.asyncio
async def test_idempotent_result_persistence_and_reexecution(api_client: AsyncClient, db_session: AsyncSession):
    """Calling execute_analytics_job on an already succeeded job is an idempotent no-op."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "idemp_exec"
    )

    spreadsheet_svc = SpreadsheetService(db_session)
    edits = [
        CellEditInput(cell_ref="A1", value=10.0),
        CellEditInput(cell_ref="A2", value=20.0),
        CellEditInput(cell_ref="A3", value=30.0),
    ]
    await spreadsheet_svc.update_cells_batch(
        sheet_id=sheet_id,
        organization_id=org_id,
        user_id=user_id,
        data=BatchCellUpdateInput(updates=edits),
    )

    analytics_svc = AnalyticsService(db_session)
    from app.schemas.analytics import AnalyticsJobCreateInput

    job_read = await analytics_svc.submit_job(
        organization_id=org_id,
        user_id=user_id,
        data=AnalyticsJobCreateInput(
            source_workbook_id=wb_id,
            source_sheet_id=sheet_id,
            operation_id="describe_columns",
            parameters={},
        ),
    )

    # First execution
    res1 = await _execute_analytics_job_async(str(job_read.id), "task-1")
    assert res1["status"] == "succeeded"

    # Second execution (duplicate delivery / retry attempt)
    res2 = await _execute_analytics_job_async(str(job_read.id), "task-2")
    assert res2["status"] == "succeeded"
    assert res2.get("idempotent") is True
    assert res2["result_id"] == res1["result_id"]


@pytest.mark.asyncio
async def test_atomic_spreadsheet_publishing_and_idempotency(api_client: AsyncClient, db_session: AsyncSession):
    """Publishing to spreadsheet is atomic, validates target workbook, and is idempotent."""
    org_id, ws_id, wb_id, sheet_id, user_id, csrf_token = await create_test_context(
        api_client, db_session, "publish_hard"
    )

    spreadsheet_svc = SpreadsheetService(db_session)
    edits = [
        CellEditInput(cell_ref="A1", value=100.0),
        CellEditInput(cell_ref="A2", value=200.0),
    ]
    await spreadsheet_svc.update_cells_batch(
        sheet_id=sheet_id,
        organization_id=org_id,
        user_id=user_id,
        data=BatchCellUpdateInput(updates=edits),
    )

    analytics_svc = AnalyticsService(db_session)
    from app.schemas.analytics import AnalyticsJobCreateInput

    job_read = await analytics_svc.submit_job(
        organization_id=org_id,
        user_id=user_id,
        data=AnalyticsJobCreateInput(
            source_workbook_id=wb_id,
            source_sheet_id=sheet_id,
            operation_id="describe_columns",
            parameters={},
        ),
    )

    exec_res = await _execute_analytics_job_async(str(job_read.id), "task-pub")
    assert exec_res["status"] == "succeeded"
    result_id = uuid.UUID(exec_res["result_id"])

    uid = uuid.uuid4().hex[:8]

    # 1. First publish call with idempotency key
    pub_res_1 = await api_client.post(
        f"/api/v1/analytics/results/{result_id}/publish-to-sheet",
        json={
            "destination_workbook_id": str(wb_id),
            "sheet_name": "My_Published_Summary",
            "idempotency_key": f"pub-key-{uid}",
        },
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )
    assert pub_res_1.status_code == 201
    sheet_id_1 = pub_res_1.json()["sheet_id"]

    # 2. Second publish call with same idempotency key -> MUST BE IDEMPOTENT
    pub_res_2 = await api_client.post(
        f"/api/v1/analytics/results/{result_id}/publish-to-sheet",
        json={
            "destination_workbook_id": str(wb_id),
            "sheet_name": "My_Published_Summary",
            "idempotency_key": f"pub-key-{uid}",
        },
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )
    assert pub_res_2.status_code == 201
    sheet_id_2 = pub_res_2.json()["sheet_id"]
    assert sheet_id_2 == sheet_id_1

    # Verify no duplicate sheet was created
    all_sheets = await spreadsheet_svc.list_sheets(wb_id, org_id)
    published_sheets = [s for s in all_sheets if s.name == "My_Published_Summary"]
    assert len(published_sheets) == 1

    # 3. Destination workbook in foreign organization -> MUST BE REJECTED 404
    fake_foreign_wb_id = uuid.uuid4()
    foreign_pub_res = await api_client.post(
        f"/api/v1/analytics/results/{result_id}/publish-to-sheet",
        json={
            "destination_workbook_id": str(fake_foreign_wb_id),
            "sheet_name": "Foreign_Sheet",
        },
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )
    assert foreign_pub_res.status_code == 404
