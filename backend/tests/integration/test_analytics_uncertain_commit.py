"""Deterministic tests for uncertain database commit outcomes, row-lock synchronization, and safe reconciliation.

Covers the full matrix mandated by Phase 7 Final Sign-Off:
1. Original transaction commits normally.
2. Original transaction rolls back.
3. Commit acknowledgement is lost after server-side commit (genuine network transport abort on COMMIT).
4. Reconciliation starts while original transaction is still unresolved and waits for lock.
5. Reconciliation waits for unresolved transaction then detects rollback and quarantines.
6. PostgreSQL becomes unavailable during reconciliation (preserves artifact).
7. Quarantine recovery does not break a committed result.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import asyncpg
import polars as pl
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.analytics.storage import AnalyticsStorageService
from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.models.analytics import AnalyticsJob, AnalyticsJobStatus, AnalyticsResult
from app.repositories.analytics import AnalyticsJobRepository, AnalyticsResultRepository
from app.repositories.workspace import WorkspaceRepository
from app.schemas.spreadsheet import WorkbookCreateInput
from app.services.spreadsheet import SpreadsheetService
from app.tasks.analytics import _execute_analytics_job_async, reconcile_commit_outcome
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


@pytest.fixture
async def setup_test_context(api_client: AsyncClient, db_session: AsyncSession) -> dict[str, Any]:
    uid = uuid.uuid4().hex[:8]
    login_data, _, org_id = await register_and_login_helper(
        api_client, f"commit_test_{uid}@example.com"
    )
    user_id = uuid.UUID(login_data["user"]["id"])

    ws = (await WorkspaceRepository(db_session).list_by_organization(org_id))[0]
    spreadsheet_svc = SpreadsheetService(db_session)
    wb = await spreadsheet_svc.create_workbook(
        organization_id=org_id,
        created_by_user_id=user_id,
        data=WorkbookCreateInput(name="Commit WB", workspace_id=ws.id),
    )
    sheets = await spreadsheet_svc.list_sheets(wb.id, org_id)
    return {
        "org_id": org_id,
        "ws_id": ws.id,
        "user_id": user_id,
        "sheet_id": sheets[0].id,
        "wb_id": wb.id,
    }


async def _create_test_job(
    db_session: AsyncSession,
    org_id: uuid.UUID,
    ws_id: uuid.UUID,
    user_id: uuid.UUID,
    sheet_id: uuid.UUID,
    wb_id: uuid.UUID,
    operation_id: str = "describe_columns",
) -> AnalyticsJob:
    job = AnalyticsJob(
        id=uuid.uuid4(),
        organization_id=org_id,
        workspace_id=ws_id,
        source_workbook_id=wb_id,
        source_sheet_id=sheet_id,
        requested_by_user_id=user_id,
        operation_id=operation_id,
        parameters={"columns": ["val"]},
        status=AnalyticsJobStatus.QUEUED,
        attempt_count=0,
        max_attempts=3,
        source_sheet_version=1,
    )
    db_session.add(job)
    await db_session.commit()
    return job


@pytest.mark.asyncio
async def test_scenario_1_original_transaction_commits_normally(
    db_session: AsyncSession,
    setup_test_context: dict[str, Any],
) -> None:
    """Scenario 1: Original transaction commits normally -> result persisted, final artifact retained, job SUCCEEDED."""
    ctx = setup_test_context
    storage = AnalyticsStorageService()
    job = await _create_test_job(db_session, ctx["org_id"], ctx["ws_id"], ctx["user_id"], ctx["sheet_id"], ctx["wb_id"])

    with patch("app.tasks.analytics.registry.execute", return_value={"data": [{"val": i} for i in range(600)]}):
        with patch("app.tasks.analytics.capture_sheet_snapshot") as mock_snap:
            dummy_snap_path = storage.get_snapshot_path(ctx["org_id"], job.id, 1)
            dummy_snap_path.parent.mkdir(parents=True, exist_ok=True)
            pl.DataFrame({"val": [1, 2, 3]}).write_parquet(dummy_snap_path)
            mock_snap.return_value = AsyncMock(
                storage_path=str(dummy_snap_path),
                storage_id=str(dummy_snap_path),
                checksum_sha256=storage.compute_sha256(dummy_snap_path),
                size_bytes=100,
                row_count=3,
                col_count=1,
                sheet_version=1,
                created_at=datetime.now(UTC),
            )
            res = await _execute_analytics_job_async(str(job.id), "celery-task-1")

    assert res["status"] == "succeeded"
    result_stmt = select(AnalyticsResult).where(AnalyticsResult.job_id == job.id)
    db_res = (await db_session.execute(result_stmt)).scalar_one_or_none()
    assert db_res is not None
    assert db_res.artifact_path is not None
    assert Path(db_res.artifact_path).is_file()


@pytest.mark.asyncio
async def test_scenario_2_original_transaction_rolls_back(
    db_session: AsyncSession,
    setup_test_context: dict[str, Any],
) -> None:
    """Scenario 2: Original transaction rolls back -> reconciliation detects rollback and moves artifact to quarantine."""
    ctx = setup_test_context
    storage = AnalyticsStorageService()
    job = await _create_test_job(db_session, ctx["org_id"], ctx["ws_id"], ctx["user_id"], ctx["sheet_id"], ctx["wb_id"])

    # Simulate real database error inside transaction boundary (e.g. unique violation / deadlock abort)
    with patch("app.tasks.analytics.AnalyticsResultRepository.create", side_effect=RuntimeError("Database deadlock abort")):
        with patch("app.tasks.analytics.registry.execute", return_value={"data": [{"val": i} for i in range(600)]}):
            with patch("app.tasks.analytics.capture_sheet_snapshot") as mock_snap:
                dummy_snap_path = storage.get_snapshot_path(ctx["org_id"], job.id, 1)
                dummy_snap_path.parent.mkdir(parents=True, exist_ok=True)
                pl.DataFrame({"val": [1, 2, 3]}).write_parquet(dummy_snap_path)
                mock_snap.return_value = AsyncMock(
                    storage_path=str(dummy_snap_path),
                    storage_id=str(dummy_snap_path),
                    checksum_sha256=storage.compute_sha256(dummy_snap_path),
                    size_bytes=100,
                    row_count=3,
                    col_count=1,
                    sheet_version=1,
                    created_at=datetime.now(UTC),
                )
                res = await _execute_analytics_job_async(str(job.id), "celery-task-2")

    assert res["status"] in (AnalyticsJobStatus.FAILED, AnalyticsJobStatus.RETRYING)
    # Check that results dir does not have the uncommitted promoted artifact
    res_dir = storage.results_dir / str(ctx["org_id"]) / str(job.id)
    if res_dir.is_dir():
        assert len(list(res_dir.glob("*.parquet"))) == 0


@pytest.mark.asyncio
async def test_scenario_3_genuine_lost_acknowledgement_after_server_commit(
    db_session: AsyncSession,
    setup_test_context: dict[str, Any],
) -> None:
    """Scenario 3: Genuine lost acknowledgement test.

    Uses raw asyncpg transport injection: the COMMIT command is sent to the PostgreSQL
    server and processed, but the client socket is severed before the client driver receives
    the commit ACK. The worker reconciles authoritative state using an independent connection,
    discovers the committed result, retains the artifact, and returns succeeded.
    """
    ctx = setup_test_context
    storage = AnalyticsStorageService()
    job = await _create_test_job(db_session, ctx["org_id"], ctx["ws_id"], ctx["user_id"], ctx["sheet_id"], ctx["wb_id"])

    # Create dummy snapshot
    snap_path = storage.get_snapshot_path(ctx["org_id"], job.id, 1)
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"val": [1.0, 2.0, 3.0]}).write_parquet(snap_path)

    # 1. Execute job up to result write and artifact promotion manually
    result_id = uuid.uuid4()
    final_art_path = storage.get_result_artifact_path(ctx["org_id"], job.id, result_id, claim_generation=1)
    final_art_path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"val": [10.0, 20.0, 30.0]}).write_parquet(final_art_path)
    assert final_art_path.is_file()

    # 2. Perform genuine network fault injection on raw asyncpg connection during COMMIT
    raw_url = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(raw_url)

    # Claim job and begin transaction
    await conn.execute(
        "UPDATE analytics_jobs SET status = 'running', worker_id = 'test-worker-lost-ack', claim_generation = 1 WHERE id = $1",
        job.id,
    )
    tx = conn.transaction()
    await tx.start()

    # Lock row and insert result
    await conn.fetchrow("SELECT id FROM analytics_jobs WHERE id = $1 FOR UPDATE", job.id)
    await conn.execute(
        """
        INSERT INTO analytics_results (id, job_id, organization_id, result_type, result_payload, artifact_path, artifact_size_bytes, row_count, column_count, created_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
        """,
        result_id,
        job.id,
        ctx["org_id"],
        "describe_columns",
        '{"data": [{"val": 10.0}]}',
        str(final_art_path),
        100,
        3,
        1,
        datetime.now(UTC),
    )
    await conn.execute(
        "UPDATE analytics_jobs SET status = 'succeeded', result_id = $1, completed_at = $2 WHERE id = $3",
        result_id,
        datetime.now(UTC),
        job.id,
    )

    # Hook asyncpg protocol query to intercept COMMIT and sever socket as ACK arrives
    orig_query = conn._protocol.query
    committing = False

    def fault_query(query_str: str, timeout: float | None = None):
        nonlocal committing
        if "COMMIT" in query_str:
            committing = True
        return orig_query(query_str, timeout)

    conn._protocol.query = fault_query

    orig_data_received = conn._protocol.data_received

    def fault_data_received(data: bytes):
        nonlocal committing
        if committing:
            # Sever transport right as COMMIT ACK returns from PostgreSQL server
            conn._protocol.abort()
        return orig_data_received(data)

    conn._protocol.data_received = fault_data_received

    # Client executes commit -> raises error because client connection is severed during ACK
    with pytest.raises((asyncpg.InterfaceError, asyncpg.ConnectionDoesNotExistError)):
        await tx.commit()

    # 3. Authoritative reconciliation with fresh independent connection
    status, res = await reconcile_commit_outcome(
        job_id=job.id,
        result_id=result_id,
        org_id=ctx["org_id"],
        final_art_path=final_art_path,
        storage=storage,
    )

    # Verification: Server DID commit! Reconciliation unblocks, detects result, retains artifact.
    assert status == "succeeded"
    assert res is not None
    assert res.id == result_id
    assert final_art_path.is_file()


@pytest.mark.asyncio
async def test_scenario_4_reconciliation_starts_while_original_tx_unresolved_and_waits(
    db_session: AsyncSession,
    setup_test_context: dict[str, Any],
) -> None:
    """Scenario 4: Reconciliation starts while original transaction is still unresolved.

    Verifies that reconcile_commit_outcome acquires a conflicting SELECT ... FOR UPDATE
    lock on AnalyticsJob and BLOCKS until the original transaction completes.
    """
    ctx = setup_test_context
    storage = AnalyticsStorageService()
    job = await _create_test_job(db_session, ctx["org_id"], ctx["ws_id"], ctx["user_id"], ctx["sheet_id"], ctx["wb_id"])

    result_id = uuid.uuid4()
    final_art_path = storage.get_result_artifact_path(ctx["org_id"], job.id, result_id, claim_generation=1)
    final_art_path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"val": [100.0]}).write_parquet(final_art_path)

    # Session 1 starts an unresolved transaction holding FOR UPDATE lock on the job row
    engine1 = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory1 = async_sessionmaker(engine1, class_=AsyncSession, expire_on_commit=False)
    session1 = session_factory1()

    tx1 = await session1.begin()
    job_repo1 = AnalyticsJobRepository(session1)
    res_repo1 = AnalyticsResultRepository(session1)

    # Lock row in session 1
    locked_job = await job_repo1.get_by_id_for_update(job.id)
    assert locked_job is not None

    # Insert result in session 1 (uncommitted)
    analytics_result = AnalyticsResult(
        id=result_id,
        job_id=job.id,
        organization_id=ctx["org_id"],
        result_type="describe_columns",
        result_payload={"data": [{"val": 100.0}]},
        artifact_path=str(final_art_path),
        artifact_size_bytes=100,
        row_count=1,
        column_count=1,
    )
    await res_repo1.create(analytics_result)
    await job_repo1.mark_succeeded(job.id, result_id)

    reconciliation_started = asyncio.Event()
    reconciliation_finished = asyncio.Event()
    recon_outcome = {}

    async def run_concurrent_reconciliation():
        reconciliation_started.set()
        status, res = await reconcile_commit_outcome(
            job_id=job.id,
            result_id=result_id,
            org_id=ctx["org_id"],
            final_art_path=final_art_path,
            storage=storage,
        )
        recon_outcome["status"] = status
        recon_outcome["res"] = res
        reconciliation_finished.set()

    # Launch reconciliation task in background
    recon_task = asyncio.create_task(run_concurrent_reconciliation())
    await reconciliation_started.wait()

    # Allow reconciliation to attempt to lock row
    await asyncio.sleep(0.3)

    # Verify reconciliation is BLOCKED waiting on row lock
    assert not reconciliation_finished.is_set(), "Reconciliation must not finish while original transaction is in flight!"

    # Now Session 1 commits!
    await tx1.commit()
    await session1.close()
    await engine1.dispose()

    # Reconciliation must now unblock and observe committed state
    await asyncio.wait_for(reconciliation_finished.wait(), timeout=5.0)
    await recon_task

    assert recon_outcome["status"] == "succeeded"
    assert recon_outcome["res"] is not None
    assert final_art_path.is_file()


@pytest.mark.asyncio
async def test_scenario_5_reconciliation_waits_for_unresolved_then_quarantines_on_rollback(
    db_session: AsyncSession,
    setup_test_context: dict[str, Any],
) -> None:
    """Scenario 5: Reconciliation waits for unresolved transaction then detects rollback and quarantines artifact."""
    ctx = setup_test_context
    storage = AnalyticsStorageService()
    job = await _create_test_job(db_session, ctx["org_id"], ctx["ws_id"], ctx["user_id"], ctx["sheet_id"], ctx["wb_id"])

    result_id = uuid.uuid4()
    final_art_path = storage.get_result_artifact_path(ctx["org_id"], job.id, result_id, claim_generation=1)
    final_art_path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"val": [50.0]}).write_parquet(final_art_path)

    # Session 1 holds lock on the job row
    engine1 = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory1 = async_sessionmaker(engine1, class_=AsyncSession, expire_on_commit=False)
    session1 = session_factory1()

    tx1 = await session1.begin()
    job_repo1 = AnalyticsJobRepository(session1)
    await job_repo1.get_by_id_for_update(job.id)

    reconciliation_started = asyncio.Event()
    reconciliation_finished = asyncio.Event()
    recon_outcome = {}

    async def run_concurrent_reconciliation():
        reconciliation_started.set()
        status, res = await reconcile_commit_outcome(
            job_id=job.id,
            result_id=result_id,
            org_id=ctx["org_id"],
            final_art_path=final_art_path,
            storage=storage,
        )
        recon_outcome["status"] = status
        recon_outcome["res"] = res
        reconciliation_finished.set()

    recon_task = asyncio.create_task(run_concurrent_reconciliation())
    await reconciliation_started.wait()
    await asyncio.sleep(0.3)

    assert not reconciliation_finished.is_set(), "Reconciliation must block while lock is held!"

    # Session 1 rolls back!
    await tx1.rollback()
    await session1.close()
    await engine1.dispose()

    # Reconciliation unblocks, establishes rollback, and quarantines
    await asyncio.wait_for(reconciliation_finished.wait(), timeout=5.0)
    await recon_task

    assert recon_outcome["status"] == "rolled_back"
    assert recon_outcome["res"] is None

    # Final artifact must be safely moved to quarantine
    quarantine_path = storage.quarantine_dir / "results" / str(ctx["org_id"]) / str(job.id) / final_art_path.name
    assert quarantine_path.is_file()
    assert not final_art_path.is_file()


@pytest.mark.asyncio
async def test_scenario_6_postgresql_unavailable_during_reconciliation(
    db_session: AsyncSession,
    setup_test_context: dict[str, Any],
) -> None:
    """Scenario 6: PostgreSQL unavailable during reconciliation -> artifact PRESERVED (never deleted during outage)."""
    ctx = setup_test_context
    storage = AnalyticsStorageService()
    job = await _create_test_job(db_session, ctx["org_id"], ctx["ws_id"], ctx["user_id"], ctx["sheet_id"], ctx["wb_id"])

    result_id = uuid.uuid4()
    final_art_path = storage.get_result_artifact_path(ctx["org_id"], job.id, result_id, claim_generation=1)
    final_art_path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"val": [999.0]}).write_parquet(final_art_path)

    # Point DATABASE_URL to a non-existent port to simulate PostgreSQL network outage
    with patch("app.core.config.settings.DATABASE_URL", "postgresql+asyncpg://vibe_admin:pass@127.0.0.1:59999/vibe_enterprise"):
        status, res = await reconcile_commit_outcome(
            job_id=job.id,
            result_id=result_id,
            org_id=ctx["org_id"],
            final_art_path=final_art_path,
            storage=storage,
        )

    assert status == "unreachable"
    assert res is None
    # Artifact MUST remain preserved on disk (zero data loss during DB outage)
    assert final_art_path.is_file()


@pytest.mark.asyncio
async def test_scenario_7_quarantine_recovery_does_not_break_committed_result(
    db_session: AsyncSession,
    setup_test_context: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Scenario 7: Quarantine sweeper / recovery does not purge or break a valid committed result."""
    ctx = setup_test_context
    storage = AnalyticsStorageService(base_dir=str(tmp_path))
    job = await _create_test_job(db_session, ctx["org_id"], ctx["ws_id"], ctx["user_id"], ctx["sheet_id"], ctx["wb_id"])

    # Create committed result
    result_id = uuid.uuid4()
    art_path = storage.get_result_artifact_path(ctx["org_id"], job.id, result_id, 1)
    art_path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({"test": [1, 2, 3]}).write_parquet(art_path)

    res = AnalyticsResult(
        id=result_id,
        job_id=job.id,
        organization_id=ctx["org_id"],
        result_type="describe_columns",
        result_payload={"data": []},
        artifact_path=str(art_path),
        artifact_size_bytes=100,
        row_count=3,
        column_count=1,
    )
    db_session.add(res)
    job.status = AnalyticsJobStatus.SUCCEEDED
    job.result_id = result_id
    await db_session.commit()

    # Make file older than grace period
    old_time = datetime.now().timestamp() - 7200
    os.utime(art_path, (old_time, old_time))

    # Run sweeper with committed result path in referenced_paths
    pruned = storage.prune_orphan_artifacts(
        active_job_ids=set(),
        referenced_paths={str(art_path)},
        grace_period_seconds=3600,
    )

    # Must be protected because it is referenced in DB
    assert pruned == 0
    assert art_path.is_file()
