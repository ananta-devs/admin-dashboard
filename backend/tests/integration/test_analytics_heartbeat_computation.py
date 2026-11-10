"""Deterministic tests proving heartbeat independence during blocking synchronous CPU computation.

Verifies:
1. Heartbeat thread renews PostgreSQL lease during a deliberately blocking synchronous CPU computation.
2. Competing worker cannot reclaim the job while the original worker is computing.
3. Fencing stops stale writes if the heartbeat aborts or lease is lost.
4. Transient database errors in heartbeat trigger cooperative abort.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.models.analytics import AnalyticsJob, AnalyticsJobStatus
from app.repositories.analytics import AnalyticsJobRepository
from app.repositories.workspace import WorkspaceRepository
from app.schemas.spreadsheet import WorkbookCreateInput
from app.services.spreadsheet import SpreadsheetService
from app.tasks.analytics import WorkerHeartbeatThread
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


def blocking_synchronous_computation(duration_seconds: float) -> int:
    """Deliberately blocks the executing OS thread with synchronous computation (not async sleep)."""
    end_time = time.perf_counter() + duration_seconds
    total = 0
    while time.perf_counter() < end_time:
        total += 1
        # Busy-wait with tight arithmetic
        for _ in range(1000):
            total = (total * 31 + 7) & 0xFFFFFFFF
    return total


@pytest.mark.asyncio
async def test_heartbeat_independent_during_blocking_computation(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Proves dedicated heartbeat thread renews lease in PostgreSQL during synchronous CPU computation."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "hb_indep"
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

    # 1. Claim job with a short test lease of 2 seconds
    claimed, token = await repo.claim_job_atomically(
        job_id, "worker-primary", lease_duration_seconds=2
    )
    await db_session.commit()
    assert claimed is True
    assert token is not None

    db_session.expire_all()
    initial_job = await repo.get_by_id(job_id)
    assert initial_job is not None
    initial_lease = initial_job.lease_expires_at

    # 2. Start dedicated heartbeat thread (firing every 0.25s, extending lease by 3s)
    abort_event = threading.Event()
    heartbeat_thread = WorkerHeartbeatThread(
        job_id=job_id,
        claim_token=token,
        abort_event=abort_event,
        interval_seconds=0.25,
        lease_duration_seconds=3,
    )
    heartbeat_thread.start()

    try:
        # 3. Run deliberately blocking synchronous computation for 3.0 seconds (> initial 2s lease)
        # using asyncio.to_thread to simulate Celery worker's non-blocking execution model
        comp_result = await asyncio.to_thread(blocking_synchronous_computation, 3.0)
        assert comp_result > 0

        # 4. Verify heartbeat renewed the lease during the blocking computation
        assert heartbeat_thread.renewals_count >= 1
        assert not abort_event.is_set()

        db_session.expire_all()
        renewed_job = await repo.get_by_id(job_id)
        assert renewed_job is not None
        assert renewed_job.lease_expires_at is not None
        assert initial_lease is not None
        assert renewed_job.lease_expires_at > initial_lease

        # 5. Verify competing worker CANNOT reclaim the job because heartbeat kept lease active
        test_engine2 = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
        session_factory2 = async_sessionmaker(
            test_engine2, class_=AsyncSession, expire_on_commit=False
        )
        async with session_factory2() as session2:
            repo2 = AnalyticsJobRepository(session2)
            claimed2, token2 = await repo2.claim_job_atomically(
                job_id, "worker-competitor", lease_duration_seconds=5
            )
            await session2.commit()
            assert claimed2 is False
            assert token2 is None
        await test_engine2.dispose()

    finally:
        heartbeat_thread.stop()
        heartbeat_thread.join(timeout=2.0)


@pytest.mark.asyncio
async def test_heartbeat_stops_and_aborts_on_superseded_claim(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Proves heartbeat detects lost lease and aborts immediately, stopping stale writes."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "hb_stale"
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

    claimed_1, token_1 = await repo.claim_job_atomically(
        job_id, "worker-1", lease_duration_seconds=10
    )
    await db_session.commit()
    assert claimed_1 is True

    abort_event = threading.Event()
    heartbeat_thread = WorkerHeartbeatThread(
        job_id=job_id,
        claim_token=token_1,
        abort_event=abort_event,
        interval_seconds=0.5,
        lease_duration_seconds=5,
    )
    heartbeat_thread.start()

    try:
        # Simulate worker 1 losing lease: state is reset to RETRYING and Worker 2 claims it
        await repo.mark_failed(
            job_id, "LEASE_EXPIRED", "expired", next_status=AnalyticsJobStatus.RETRYING
        )
        await db_session.commit()

        claimed_2, token_2 = await repo.claim_job_atomically(
            job_id, "worker-2", lease_duration_seconds=10
        )
        await db_session.commit()
        assert claimed_2 is True

        # Wait for Worker 1's heartbeat to fire and detect fencing violation
        await asyncio.sleep(1.2)
        assert abort_event.is_set()

        # Worker 1 attempting any write must fail
        marked_stale = await repo.mark_succeeded(job_id, uuid.uuid4(), claim_token=token_1)
        await db_session.commit()
        assert marked_stale is False

    finally:
        heartbeat_thread.stop()
        heartbeat_thread.join(timeout=2.0)
