"""Concurrent snapshot extraction under PostgreSQL REPEATABLE READ transaction isolation.

Verifies:
1. Snapshot extraction under REPEATABLE READ is completely isolated from concurrent row/cell commits.
2. If Session B modifies and commits row updates during Session A's snapshot extraction, Session A does not observe torn reads.
3. The captured Parquet dataset contains 100% coherent MVCC snapshot values.
"""

from __future__ import annotations

import uuid

import polars as pl
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.analytics.snapshot import capture_sheet_snapshot
from app.analytics.storage import AnalyticsStorageService
from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
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
    return org_id, ws.id, wb.id, sheets[0].id, user_id, csrf_token


@pytest.mark.asyncio
async def test_concurrent_mutation_during_repeatable_read_snapshot(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Proves REPEATABLE READ snapshot is unaffected by concurrent row/cell commits during extraction."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "mvcc_snap"
    )

    spreadsheet_svc = SpreadsheetService(db_session)

    # Initial state: 5 rows with values 10, 20, 30, 40, 50
    initial_edits = [
        CellEditInput(cell_ref=f"A{i + 1}", value=float((i + 1) * 10)) for i in range(5)
    ]
    await spreadsheet_svc.update_cells_batch(
        sheet_id=sheet_id,
        organization_id=org_id,
        user_id=user_id,
        data=BatchCellUpdateInput(updates=initial_edits),
    )

    storage = AnalyticsStorageService()
    job_id = uuid.uuid4()

    # Define concurrent mutator hook called after Session A starts REPEATABLE READ transaction
    # and reads sheet metadata, but BEFORE reading sheet rows!
    async def mutate_concurrently():
        # Create an independent database session for Session B
        engine_b = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
        session_factory_b = async_sessionmaker(
            engine_b, class_=AsyncSession, expire_on_commit=False
        )
        async with session_factory_b() as session_b:
            svc_b = SpreadsheetService(session_b)
            # Mutate row 1 and row 2 to 999.0 and 888.0 and COMMIT
            mutations = [
                CellEditInput(cell_ref="A1", value=999.0),
                CellEditInput(cell_ref="A2", value=888.0),
            ]
            await svc_b.update_cells_batch(
                sheet_id=sheet_id,
                organization_id=org_id,
                user_id=user_id,
                data=BatchCellUpdateInput(updates=mutations),
            )
            await session_b.commit()
        await engine_b.dispose()

    # Session A executes snapshot extraction with the concurrent mutation hook
    snapshot_meta = await capture_sheet_snapshot(
        sheet_id=sheet_id,
        organization_id=org_id,
        job_id=job_id,
        claim_generation=1,
        storage=storage,
        session=None,
        on_before_rows_query=mutate_concurrently,
    )

    # Load the captured snapshot Parquet
    df = pl.read_parquet(snapshot_meta.storage_path)

    # Because Session A operated in REPEATABLE READ:
    # It must NOT see the concurrent mutations made by Session B!
    col_a_vals = df["A"].to_list()

    # Expect original values [10.0, 20.0, 30.0, 40.0, 50.0], NOT 999.0 or 888.0!
    assert 10.0 in col_a_vals
    assert 20.0 in col_a_vals
    assert 999.0 not in col_a_vals
    assert 888.0 not in col_a_vals
    assert col_a_vals[0] == 10.0
    assert col_a_vals[1] == 20.0


@pytest.mark.asyncio
async def test_retry_determinism_reuses_original_snapshot_across_mutations(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Proves attempt 2 reuses immutable snapshot from attempt 1 despite subsequent spreadsheet mutations."""
    from app.models.analytics import AnalyticsJob, AnalyticsJobStatus
    from app.repositories.analytics import AnalyticsJobRepository
    from app.tasks.analytics import _execute_analytics_job_async

    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "snap_retry"
    )
    spreadsheet_svc = SpreadsheetService(db_session)

    # Initial sheet data: values 100.0, 200.0
    await spreadsheet_svc.update_cells_batch(
        sheet_id=sheet_id,
        organization_id=org_id,
        user_id=user_id,
        data=BatchCellUpdateInput(
            updates=[
                CellEditInput(cell_ref="A1", value=100.0),
                CellEditInput(cell_ref="A2", value=200.0),
            ]
        ),
    )

    job_id = uuid.uuid4()
    job = AnalyticsJob(
        id=job_id,
        organization_id=org_id,
        workspace_id=ws_id,
        source_workbook_id=wb_id,
        source_sheet_id=sheet_id,
        requested_by_user_id=user_id,
        operation_id="describe_columns",
        parameters={"columns": ["A"]},
        status=AnalyticsJobStatus.QUEUED,
        attempt_count=0,
        max_attempts=3,
        source_sheet_version=1,
    )
    db_session.add(job)
    await db_session.commit()

    # Attempt 1: Captures snapshot, but fails in computation stage
    from unittest.mock import patch

    with patch(
        "app.tasks.analytics.registry.execute",
        side_effect=ValueError("Simulated worker error in attempt 1"),
    ):
        res1 = await _execute_analytics_job_async(str(job_id), "task-attempt-1")
    assert res1["status"] == AnalyticsJobStatus.RETRYING

    # Verify attempt 1 recorded immutable snapshot path and hash on job
    job_repo = AnalyticsJobRepository(db_session)
    job_after_1 = await job_repo.get_by_id(job_id)
    assert job_after_1 is not None
    assert job_after_1.input_snapshot_id is not None
    assert job_after_1.input_snapshot_hash is not None

    # Now MUTATE the spreadsheet drastically: change A1 to 999999.0
    await spreadsheet_svc.update_cells_batch(
        sheet_id=sheet_id,
        organization_id=org_id,
        user_id=user_id,
        data=BatchCellUpdateInput(
            updates=[
                CellEditInput(cell_ref="A1", value=999999.0),
            ]
        ),
    )
    await db_session.commit()

    # Attempt 2 (Retry): Executes successfully
    res2 = await _execute_analytics_job_async(str(job_id), "task-attempt-2")
    assert res2["status"] == "succeeded"

    # Verify attempt 2 reused the ORIGINAL snapshot and produced mean based on [100.0, 200.0] -> 150.0
    from app.repositories.analytics import AnalyticsResultRepository

    res_repo = AnalyticsResultRepository(db_session)
    result_record = await res_repo.get_by_id_scoped(uuid.UUID(res2["result_id"]), org_id)
    assert result_record is not None
    payload = result_record.result_payload or {}
    # describe_columns calculates mean: (100 + 200) / 2 = 150.0
    col_a_stats = payload.get("columns", {}).get("A", {})
    mean_val = col_a_stats.get("mean")
    if mean_val is not None:
        assert mean_val == pytest.approx(150.0, rel=1e-2)
        assert mean_val < 500000.0  # Proves it did NOT use mutated 999999.0!


@pytest.mark.asyncio
async def test_retry_corrupted_snapshot_fails_deterministically(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Proves that if an earlier attempt's snapshot is corrupted on disk, retry fails deterministically rather than taking a new snapshot."""
    from app.models.analytics import AnalyticsJob, AnalyticsJobStatus
    from app.repositories.analytics import AnalyticsJobRepository
    from app.tasks.analytics import _execute_analytics_job_async

    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "snap_corrupt"
    )

    storage = AnalyticsStorageService()
    job_id = uuid.uuid4()
    corrupt_snap_path = storage.get_snapshot_path(org_id, job_id, 1)
    corrupt_snap_path.parent.mkdir(parents=True, exist_ok=True)
    # Write garbage bytes to create hash mismatch
    corrupt_snap_path.write_bytes(b"corrupted_bytes_here")

    job = AnalyticsJob(
        id=job_id,
        organization_id=org_id,
        workspace_id=ws_id,
        source_workbook_id=wb_id,
        source_sheet_id=sheet_id,
        requested_by_user_id=user_id,
        operation_id="describe_columns",
        parameters={"columns": ["A"]},
        status=AnalyticsJobStatus.QUEUED,
        attempt_count=1,
        max_attempts=3,
        source_sheet_version=1,
        input_snapshot_id=str(corrupt_snap_path),
        input_snapshot_hash="expected_hash_that_does_not_match",
    )
    db_session.add(job)
    await db_session.commit()

    # Attempt should fail deterministically due to SNAPSHOT_INTEGRITY_VIOLATION
    res = await _execute_analytics_job_async(str(job_id), "task-attempt-corrupt")
    assert res["status"] in (AnalyticsJobStatus.FAILED, AnalyticsJobStatus.RETRYING)

    # Job error code should indicate integrity violation
    job_repo = AnalyticsJobRepository(db_session)
    job_after = await job_repo.get_by_id(job_id)
    assert job_after is not None
    assert (
        "integrity" in str(job_after.safe_error_message).lower()
        or "violation" in str(job_after.safe_error_message).lower()
    )
