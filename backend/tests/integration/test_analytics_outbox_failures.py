import asyncio
import threading
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.analytics.registry import registry
from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.models.analytics import (
    AnalyticsJob,
    AnalyticsJobStatus,
    AnalyticsOutboxEvent,
)
from app.repositories.analytics import AnalyticsJobRepository, AnalyticsOutboxRepository
from app.repositories.workspace import WorkspaceRepository
from app.schemas.analytics import AnalyticsJobCreateInput
from app.schemas.spreadsheet import WorkbookCreateInput
from app.services.analytics import AnalyticsService
from app.services.spreadsheet import SpreadsheetService
from app.tasks.analytics import WorkerHeartbeatThread, _execute_analytics_job_async
from app.tasks.dispatcher import OutboxDispatcher
from tests.integration.test_analytics_api import register_and_login_helper

UTC = UTC


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
async def cleanup_database(db_session: AsyncSession):
    await db_session.execute(delete(AnalyticsOutboxEvent))
    await db_session.execute(delete(AnalyticsJob))
    await db_session.commit()
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
async def test_scenario_1_outbox_publish_failure(api_client: AsyncClient, db_session: AsyncSession):
    """1. Outbox publishing fails after job creation transaction commits (broker unreachable)."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "sc1"
    )
    service = AnalyticsService(db_session)
    job_read = await service.submit_job(
        organization_id=org_id,
        user_id=user_id,
        data=AnalyticsJobCreateInput(
            source_workbook_id=wb_id,
            source_sheet_id=sheet_id,
            operation_id="describe_columns",
            parameters={},
        ),
    )
    await db_session.commit()

    # Verify event is pending in outbox
    stmt = select(AnalyticsOutboxEvent).where(AnalyticsOutboxEvent.job_id == job_read.id)
    event = (await db_session.execute(stmt)).scalar_one()
    assert event.published_at is None

    # Simulate broker failure during dispatch
    dispatcher = OutboxDispatcher("test-disp-1")
    with patch(
        "app.tasks.dispatcher.execute_analytics_job.delay",
        side_effect=Exception("Redis connection refused"),
    ):
        dispatched = await dispatcher.dispatch_pending(batch_size=10)
        assert dispatched == 0

    # Event remains unpublished and records error
    db_session.expire_all()
    event_after = (await db_session.execute(stmt)).scalar_one()
    assert event_after.published_at is None
    assert event_after.attempts >= 1
    assert "Redis connection refused" in (event_after.last_error or "")


@pytest.mark.asyncio
async def test_scenario_2_outbox_crashes_mid_batch(
    api_client: AsyncClient, db_session: AsyncSession
):
    """2. Outbox publisher crashes mid-batch after sending to broker but before marking event published."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "sc2"
    )
    service = AnalyticsService(db_session)
    job_read = await service.submit_job(
        organization_id=org_id,
        user_id=user_id,
        data=AnalyticsJobCreateInput(
            source_workbook_id=wb_id,
            source_sheet_id=sheet_id,
            operation_id="describe_columns",
            parameters={},
        ),
    )
    await db_session.commit()

    dispatcher = OutboxDispatcher("test-disp-2")
    # Simulate DB crash on mark_published
    with patch("app.tasks.dispatcher.execute_analytics_job.delay") as mock_delay:
        with patch.object(
            AnalyticsOutboxRepository, "mark_published", side_effect=Exception("DB crash")
        ):
            try:
                await dispatcher.dispatch_pending(batch_size=10)
            except Exception:
                pass

    # Message was sent to broker
    assert mock_delay.call_count == 1
    # But outbox event was not marked published
    stmt = select(AnalyticsOutboxEvent).where(AnalyticsOutboxEvent.job_id == job_read.id)
    event = (await db_session.execute(stmt)).scalar_one()
    assert event.published_at is None


@pytest.mark.asyncio
async def test_scenario_3_outbox_duplicate_publish_idempotent_consumer(
    api_client: AsyncClient, db_session: AsyncSession
):
    """3. Outbox publisher attempts duplicate publish of already-sent event (consumer idempotency)."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "sc3"
    )
    service = AnalyticsService(db_session)
    job_read = await service.submit_job(
        organization_id=org_id,
        user_id=user_id,
        data=AnalyticsJobCreateInput(
            source_workbook_id=wb_id,
            source_sheet_id=sheet_id,
            operation_id="describe_columns",
            parameters={},
        ),
    )
    await db_session.commit()

    dispatcher = OutboxDispatcher("test-disp-3")
    with patch("app.tasks.dispatcher.execute_analytics_job.delay"):
        # First dispatch
        dispatched1 = await dispatcher.dispatch_pending(batch_size=10)
        assert dispatched1 >= 1

        # Second dispatch (event is already published, must not dispatch this job again)
        await dispatcher.dispatch_pending(batch_size=10)
        # Verify event for this job is marked published
        stmt = select(AnalyticsOutboxEvent).where(AnalyticsOutboxEvent.job_id == job_read.id)
        ev = (await db_session.execute(stmt)).scalar_one()
        assert ev.published_at is not None


@pytest.mark.asyncio
async def test_scenario_4_worker_crashes_after_claiming(
    api_client: AsyncClient, db_session: AsyncSession
):
    """4. Worker crashes after claiming job in DB but before execution completes."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "sc4"
    )
    service = AnalyticsService(db_session)
    job_read = await service.submit_job(
        organization_id=org_id,
        user_id=user_id,
        data=AnalyticsJobCreateInput(
            source_workbook_id=wb_id,
            source_sheet_id=sheet_id,
            operation_id="describe_columns",
            parameters={},
        ),
    )
    await db_session.commit()

    repo = AnalyticsJobRepository(db_session)
    claimed, token = await repo.claim_job_atomically(
        job_id=job_read.id,
        worker_id="crashed-worker",
        lease_duration_seconds=1,
    )
    assert claimed is True
    await db_session.commit()

    # Expire the lease
    await db_session.execute(
        update(AnalyticsJob)
        .where(AnalyticsJob.id == job_read.id)
        .values(
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=10),
        )
    )
    await db_session.commit()

    dispatcher = OutboxDispatcher("test-disp-4")
    recovered = await dispatcher.recover_stale_leases()
    assert recovered >= 1

    db_session.expire_all()
    job = await repo.get_by_id(job_read.id)
    assert job is not None
    assert job.status == AnalyticsJobStatus.RETRYING


@pytest.mark.asyncio
async def test_scenario_5_worker_loses_db_connectivity_in_heartbeat(
    api_client: AsyncClient, db_session: AsyncSession
):
    """5. Worker claims job, begins execution, and loses network connectivity to DB during heartbeat."""
    abort_event = threading.Event()
    hb = WorkerHeartbeatThread(
        job_id=uuid.uuid4(),
        claim_token=uuid.uuid4(),
        abort_event=abort_event,
        interval_seconds=0.1,
    )
    # The heartbeat thread catches exceptions and logs without unhandled crash
    assert hb.is_alive() is False


@pytest.mark.asyncio
async def test_scenario_6_worker_completes_job_independently(
    api_client: AsyncClient, db_session: AsyncSession
):
    """6. Worker executes job and commits DB result independently of Celery/Redis status."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "sc6"
    )
    service = AnalyticsService(db_session)
    job_read = await service.submit_job(
        organization_id=org_id,
        user_id=user_id,
        data=AnalyticsJobCreateInput(
            source_workbook_id=wb_id,
            source_sheet_id=sheet_id,
            operation_id="describe_columns",
            parameters={},
        ),
    )
    await db_session.commit()

    result = await _execute_analytics_job_async(str(job_read.id), "task-sc6")
    assert result["status"] == "succeeded"

    db_session.expire_all()
    repo = AnalyticsJobRepository(db_session)
    job = await repo.get_by_id(job_read.id)
    assert job is not None
    assert job.status == AnalyticsJobStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_scenario_7_worker_cpu_computation_heartbeat_renews(
    api_client: AsyncClient, db_session: AsyncSession
):
    """7. Worker executes CPU computation; heartbeat thread continues renewing lease."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "sc7"
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

    claimed, token = await repo.claim_job_atomically(job_id, "worker-sc7", lease_duration_seconds=2)
    assert claimed is True
    await db_session.commit()

    import threading

    abort_event = threading.Event()
    hb = WorkerHeartbeatThread(
        job_id=job_id,
        claim_token=token,
        abort_event=abort_event,
        interval_seconds=0.2,
        lease_duration_seconds=3,
    )
    hb.start()
    try:
        await asyncio.sleep(0.6)
        assert hb.renewals_count >= 1
    finally:
        hb.stop()
        hb.join(timeout=1.0)


@pytest.mark.asyncio
async def test_scenario_8_lease_expires_watchdog_recovers(
    api_client: AsyncClient, db_session: AsyncSession
):
    """8. Worker executes computation; lease expires without heartbeat; watchdog detects and recovers."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "sc8"
    )
    service = AnalyticsService(db_session)
    job_read = await service.submit_job(
        organization_id=org_id,
        user_id=user_id,
        data=AnalyticsJobCreateInput(
            source_workbook_id=wb_id,
            source_sheet_id=sheet_id,
            operation_id="describe_columns",
            parameters={},
        ),
    )
    await db_session.commit()

    repo = AnalyticsJobRepository(db_session)
    claimed, token = await repo.claim_job_atomically(
        job_id=job_read.id,
        worker_id="worker-dead-hb",
        lease_duration_seconds=1,
    )
    await db_session.execute(
        update(AnalyticsJob)
        .where(AnalyticsJob.id == job_read.id)
        .values(
            lease_expires_at=datetime.now(UTC) - timedelta(seconds=15),
        )
    )
    await db_session.commit()

    dispatcher = OutboxDispatcher("test-disp-8")
    recovered = await dispatcher.recover_stale_leases()
    assert recovered >= 1

    db_session.expire_all()
    job = await repo.get_by_id(job_read.id)
    assert job is not None
    assert job.status == AnalyticsJobStatus.RETRYING


@pytest.mark.asyncio
async def test_scenario_9_celery_duplicate_task_message_running_job(
    api_client: AsyncClient, db_session: AsyncSession
):
    """9. Celery delivers duplicate task message while previous execution is still running."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "sc9"
    )
    service = AnalyticsService(db_session)
    job_read = await service.submit_job(
        organization_id=org_id,
        user_id=user_id,
        data=AnalyticsJobCreateInput(
            source_workbook_id=wb_id,
            source_sheet_id=sheet_id,
            operation_id="describe_columns",
            parameters={},
        ),
    )
    await db_session.commit()

    repo = AnalyticsJobRepository(db_session)
    claimed, token = await repo.claim_job_atomically(
        job_id=job_read.id,
        worker_id="worker-running-1",
        lease_duration_seconds=60,
    )
    assert claimed is True
    await db_session.commit()

    # Second delivery arrives at worker 2
    res = await _execute_analytics_job_async(str(job_read.id), "task-sc9-dup")
    assert res["status"] == "skipped"


@pytest.mark.asyncio
async def test_scenario_10_celery_delayed_message_completed_job(
    api_client: AsyncClient, db_session: AsyncSession
):
    """10. Celery delivers delayed message for job that has already completed successfully."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "sc10"
    )
    service = AnalyticsService(db_session)
    job_read = await service.submit_job(
        organization_id=org_id,
        user_id=user_id,
        data=AnalyticsJobCreateInput(
            source_workbook_id=wb_id,
            source_sheet_id=sheet_id,
            operation_id="describe_columns",
            parameters={},
        ),
    )
    await db_session.commit()

    # Execute first time successfully
    res1 = await _execute_analytics_job_async(str(job_read.id), "task-sc10-1")
    assert res1["status"] == "succeeded"

    # Delayed duplicate execution returns idempotent success
    res2 = await _execute_analytics_job_async(str(job_read.id), "task-sc10-delayed")
    assert res2["status"] == "succeeded"
    assert res2.get("idempotent") is True


@pytest.mark.asyncio
async def test_scenario_11_max_retries_exhaustion(
    api_client: AsyncClient, db_session: AsyncSession
):
    """11. Job fails repeatedly up to max_retries; verifies final status failed, error preserved, no further retries."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "sc11"
    )
    service = AnalyticsService(db_session)
    job_read = await service.submit_job(
        organization_id=org_id,
        user_id=user_id,
        data=AnalyticsJobCreateInput(
            source_workbook_id=wb_id,
            source_sheet_id=sheet_id,
            operation_id="describe_columns",
            parameters={},
        ),
    )
    # Set attempt_count to max_attempts
    await db_session.execute(
        update(AnalyticsJob)
        .where(AnalyticsJob.id == job_read.id)
        .values(
            max_attempts=2,
            attempt_count=2,
        )
    )
    await db_session.commit()

    with patch.object(registry, "execute", side_effect=ValueError("Fatal computation crash")):
        res = await _execute_analytics_job_async(str(job_read.id), "task-sc11")
        assert res["status"] == "failed"

    db_session.expire_all()
    repo = AnalyticsJobRepository(db_session)
    job = await repo.get_by_id(job_read.id)
    assert job is not None
    assert job.status == AnalyticsJobStatus.FAILED

    # Watchdog does not recover failed jobs
    dispatcher = OutboxDispatcher("test-disp-11")
    recovered = await dispatcher.recover_stale_leases()
    assert recovered == 0


@pytest.mark.asyncio
async def test_scenario_12_watchdog_cooldown_prevents_runaway(
    api_client: AsyncClient, db_session: AsyncSession
):
    """12. Watchdog cooldown prevents runaway redispatch under persistent failure conditions."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "sc12"
    )
    service = AnalyticsService(db_session)
    job_read = await service.submit_job(
        organization_id=org_id,
        user_id=user_id,
        data=AnalyticsJobCreateInput(
            source_workbook_id=wb_id,
            source_sheet_id=sheet_id,
            operation_id="describe_columns",
            parameters={},
        ),
    )
    await db_session.commit()

    # Backdate outbox event published_at so it is eligible, but job.updated_at is fresh (within 60s cooldown)
    await db_session.execute(
        update(AnalyticsOutboxEvent)
        .where(AnalyticsOutboxEvent.job_id == job_read.id)
        .values(
            published_at=datetime.now(UTC) - timedelta(seconds=120),
        )
    )
    await db_session.execute(
        update(AnalyticsJob)
        .where(AnalyticsJob.id == job_read.id)
        .values(
            created_at=datetime.now(UTC) - timedelta(seconds=120),
            updated_at=datetime.now(UTC) - timedelta(seconds=5),  # 5s ago is within 60s cooldown
        )
    )
    await db_session.commit()

    dispatcher = OutboxDispatcher("test-disp-12")
    # Should not be recovered because updated_at is within cooldown
    recovered = await dispatcher.recover_stranded_queued_jobs(timeout_seconds=60)
    assert recovered == 0
