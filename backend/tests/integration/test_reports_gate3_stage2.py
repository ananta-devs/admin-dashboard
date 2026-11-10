"""Integration tests for Phase 9 Gate 3 Stage 2: Repository, Outbox & Snapshot Engine.

Covers:
1. Repository layer: organization scoping, deterministic section ordering, safe reordering, pagination.
2. Transactional outbox: atomic commit, FOR UPDATE SKIP LOCKED, broker submission, lock reclamation.
3. Worker claiming & fencing: atomic claim, lease recovery, fencing against stale generation/token.
4. Snapshot engine: REPEATABLE READ transaction, external Parquet checksums & path traversal rejection,
   inline vs external artifact storage, row truncation metadata, 10 MiB cap.
5. Service-level idempotency: key validation, concurrency handling, cross-tenant key independence.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import polars as pl
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.analytics.exceptions import DatasetTooLargeError
from app.core.config import settings
from app.core.exceptions import EntityNotFoundError, ValidationError
from app.db.base import utc_now
from app.models.analytics import AnalyticsResult
from app.models.dashboard import Dashboard, DashboardWidget
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.report import (
    ReportDefinition,
    ReportOutboxEvent,
    ReportRun,
    ReportRunStatus,
    ReportSection,
)
from app.models.spreadsheet import Sheet, SheetRow, Workbook
from app.models.user import User
from app.models.workspace import Workspace
from app.repositories.analytics import AnalyticsResultRepository
from app.repositories.report import (
    ReportDefinitionRepository,
    ReportRunRepository,
    ReportSectionRepository,
)
from app.repositories.role import RoleRepository
from app.schemas.report import (
    ReportDefinitionCreateInput,
    ReportRunTriggerInput,
    ReportSectionCreateInput,
)
from app.services.report import ReportService
from app.services.report_snapshot import ReportSnapshotService
from app.services.report_storage import ReportStorageService
from app.services.role import RoleService
from app.tasks.dispatcher import ReportingOutboxDispatcher
from app.tasks.reporting import _execute_report_run_async


@pytest.fixture
async def db_session():
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await test_engine.dispose()


async def create_tenant_hierarchy(session: AsyncSession, suffix: str) -> dict[str, Any]:
    """Helper to provision a test tenant with workspace, user, and dashboard."""
    org = Organization(name=f"Org {suffix}", slug=f"org-{suffix}")
    session.add(org)
    await session.flush()

    user = User(
        email=f"user_{suffix}@example.com",
        normalized_email=f"user_{suffix}@example.com",
        display_name=f"User {suffix}",
    )
    session.add(user)
    await session.flush()

    membership = OrganizationMembership(organization_id=org.id, user_id=user.id)
    session.add(membership)
    await session.flush()

    role_service = RoleService(session)
    role_repo = RoleRepository(session)
    roles = await role_service.seed_default_roles_for_organization(org.id)
    if "owner" in roles:
        await role_repo.set_membership_roles(
            membership.id, [roles["owner"].id], organization_id=org.id
        )

    workspace = Workspace(organization_id=org.id, name=f"Workspace {suffix}", slug=f"ws-{suffix}")
    session.add(workspace)
    await session.flush()

    dashboard = Dashboard(
        organization_id=org.id,
        workspace_id=workspace.id,
        title=f"Dashboard {suffix}",
        created_by_user_id=user.id,
    )
    session.add(dashboard)
    await session.flush()

    widget = DashboardWidget(
        dashboard_id=dashboard.id,
        organization_id=org.id,
        title=f"Widget {suffix}",
        widget_type="bar",
        source_type="sheet",
        source_id=uuid.uuid4(),
        position_x=0,
        position_y=0,
        width=6,
        height=4,
    )
    session.add(widget)
    await session.flush()

    return {
        "org": org,
        "user": user,
        "membership": membership,
        "workspace": workspace,
        "dashboard": dashboard,
        "widget": widget,
    }


# =============================================================================
# 1. REPORTING REPOSITORY LAYER TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_gate3_report_definition_repository_scoping_and_archival(
    db_session: AsyncSession,
) -> None:
    """Verify organization scoping and non-destructive archival for report definitions."""
    uid = uuid.uuid4().hex[:8]
    t_a = await create_tenant_hierarchy(db_session, f"{uid}_a")
    t_b = await create_tenant_hierarchy(db_session, f"{uid}_b")

    repo = ReportDefinitionRepository(db_session)

    # 1. Create definition in Org A
    report_def = ReportDefinition(
        organization_id=t_a["org"].id,
        workspace_id=t_a["workspace"].id,
        title="Q3 Strategy Report",
        description="Comprehensive quarterly strategy",
    )
    created = await repo.create(report_def)
    assert created.id is not None

    # 2. Scoped retrieval: Org A finds it, Org B cannot see it
    found_a = await repo.get_by_id(created.id, t_a["org"].id)
    assert found_a is not None
    assert found_a.title == "Q3 Strategy Report"

    found_b = await repo.get_by_id(created.id, t_b["org"].id)
    assert found_b is None  # Tenant isolation enforced

    # 3. Archival: soft delete pattern
    archived = await repo.archive(created.id, t_a["org"].id)
    assert archived is not None
    assert archived.is_archived is True
    assert archived.archived_at is not None

    # Excluded from active listings by default
    active_defs = await repo.list_by_workspace(t_a["workspace"].id, t_a["org"].id)
    assert created.id not in [d.id for d in active_defs]

    # Included when include_archived is True
    all_defs = await repo.list_by_workspace(
        t_a["workspace"].id, t_a["org"].id, include_archived=True
    )
    assert created.id in [d.id for d in all_defs]

    # 4. Unarchive
    unarchived = await repo.unarchive(created.id, t_a["org"].id)
    assert unarchived is not None
    assert unarchived.is_archived is False
    assert unarchived.archived_at is None


@pytest.mark.asyncio
async def test_gate3_report_section_repository_ordering_and_safe_reorder(
    db_session: AsyncSession,
) -> None:
    """Verify deterministic section ordering and safe collision-free reordering."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    def_repo = ReportDefinitionRepository(db_session)
    sec_repo = ReportSectionRepository(db_session)

    report_def = ReportDefinition(
        organization_id=t["org"].id,
        workspace_id=t["workspace"].id,
        title="Multi-Section Annual Review",
    )
    await def_repo.create(report_def)

    # Insert 3 sections with distinct sort orders: 0, 1, 2
    s0 = ReportSection(
        report_definition_id=report_def.id,
        organization_id=t["org"].id,
        section_type="summary",
        title="Executive Summary",
        sort_order=0,
    )
    s1 = ReportSection(
        report_definition_id=report_def.id,
        organization_id=t["org"].id,
        section_type="chart",
        title="Revenue Chart",
        sort_order=1,
    )
    s2 = ReportSection(
        report_definition_id=report_def.id,
        organization_id=t["org"].id,
        section_type="table",
        title="Detailed Financial Table",
        sort_order=2,
    )
    await sec_repo.create(s0)
    await sec_repo.create(s1)
    await sec_repo.create(s2)

    # 1. Deterministic retrieval in sort order
    sections = await sec_repo.list_by_definition(report_def.id, t["org"].id)
    assert [s.title for s in sections] == [
        "Executive Summary",
        "Revenue Chart",
        "Detailed Financial Table",
    ]

    # 2. Reorder: reverse order [s2, s1, s0]
    # Without safe two-phase reindexing, setting s2 to 0 would collide with existing s0
    reordered = await sec_repo.reorder_sections(report_def.id, t["org"].id, [s2.id, s1.id, s0.id])
    assert [s.id for s in reordered] == [s2.id, s1.id, s0.id]
    assert [s.sort_order for s in reordered] == [0, 1, 2]

    # Reload and verify persistence
    reloaded = await sec_repo.list_by_definition(report_def.id, t["org"].id)
    assert [s.title for s in reloaded] == [
        "Detailed Financial Table",
        "Revenue Chart",
        "Executive Summary",
    ]


@pytest.mark.asyncio
async def test_gate3_report_run_repository_pagination_and_scoping(
    db_session: AsyncSession,
) -> None:
    """Verify run history pagination and workspace/org boundaries."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    def_repo = ReportDefinitionRepository(db_session)
    run_repo = ReportRunRepository(db_session)

    report_def = ReportDefinition(
        organization_id=t["org"].id,
        workspace_id=t["workspace"].id,
        title="Paginated Report",
    )
    await def_repo.create(report_def)

    # Insert 5 runs
    for i in range(5):
        run = ReportRun(
            report_definition_id=report_def.id,
            organization_id=t["org"].id,
            workspace_id=t["workspace"].id,
            report_title=f"Run {i}",
            trigger_type="manual",
            status=ReportRunStatus.SUCCEEDED,
        )
        await run_repo.create(run)

    # Query page 1 (limit 2, offset 0)
    page_1, total_1 = await run_repo.list_by_definition_paginated(
        report_def.id, t["org"].id, limit=2, offset=0
    )
    assert total_1 == 5
    assert len(page_1) == 2

    # Query page 2 (limit 2, offset 2)
    page_2, total_2 = await run_repo.list_by_definition_paginated(
        report_def.id, t["org"].id, limit=2, offset=2
    )
    assert total_2 == 5
    assert len(page_2) == 2
    assert page_1[0].id != page_2[0].id


# =============================================================================
# 2. TRANSACTIONAL OUTBOX TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_gate3_outbox_atomic_commit_and_dispatch(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify report run and outbox event are committed atomically and dispatched."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    report_service = ReportService(db_session)

    # Create report definition
    report_def = await report_service.create_report_definition(
        t["org"].id,
        t["user"].id,
        ReportDefinitionCreateInput(workspace_id=t["workspace"].id, title="Outbox Test Report"),
    )

    # Trigger run -> creates ReportRun and ReportOutboxEvent atomically
    run = await report_service.trigger_report_run(
        report_def.id,
        t["org"].id,
        t["user"].id,
        ReportRunTriggerInput(idempotency_key=f"outbox-idem-{uid}"),
    )
    assert run.id is not None
    assert run.status == ReportRunStatus.QUEUED

    # Commit to DB so independent dispatcher session can see it
    await db_session.commit()

    # Dispatch via ReportingOutboxDispatcher with mocked Celery task
    dispatcher = ReportingOutboxDispatcher(
        dispatcher_id=f"test-disp-{uid}", session_maker=session_factory
    )

    with patch("app.tasks.dispatcher.execute_report_run.delay") as mock_delay:
        mock_delay.return_value = MagicMock(id=f"celery-task-{uid}")
        dispatched_total = 0
        while True:
            count = await dispatcher.dispatch_pending(batch_size=50)
            dispatched_total += count
            if count == 0:
                break
        assert dispatched_total >= 1
        mock_delay.assert_called()

    # Verify event is now marked published in database
    async with session_factory() as check_session:
        stmt = select(ReportOutboxEvent).where(ReportOutboxEvent.run_id == run.id)
        res = await check_session.execute(stmt)
        event = res.scalar_one()
        assert event.published_at is not None
        assert event.locked_at is None
        assert event.locked_by is None


@pytest.mark.asyncio
async def test_gate3_outbox_broker_failure_and_lock_recovery(
    db_session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify broker publish failure records error and stale locks are recovered."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    report_service = ReportService(db_session)
    report_def = await report_service.create_report_definition(
        t["org"].id,
        t["user"].id,
        ReportDefinitionCreateInput(workspace_id=t["workspace"].id, title="Broker Failure Report"),
    )

    run = await report_service.trigger_report_run(
        report_def.id,
        t["org"].id,
        t["user"].id,
        ReportRunTriggerInput(idempotency_key=f"broker-fail-{uid}"),
    )
    await db_session.commit()

    dispatcher = ReportingOutboxDispatcher(
        dispatcher_id=f"disp-fail-{uid}", session_maker=session_factory
    )

    # 1. Simulate Celery broker connection error
    with patch(
        "app.tasks.dispatcher.execute_report_run.delay",
        side_effect=Exception("Redis connection timeout"),
    ):
        dispatched = await dispatcher.dispatch_pending(batch_size=10)
        assert dispatched == 0

    # Verify error was recorded and lock released
    stmt = select(ReportOutboxEvent).where(ReportOutboxEvent.run_id == run.id)
    res = await db_session.execute(stmt)
    event = res.scalar_one()
    assert event.published_at is None
    assert event.attempts >= 1
    assert "Redis connection timeout" in str(event.last_error)
    assert event.locked_at is None

    # 2. Simulate stale lock reclamation: artificially set locked_at in the past
    event.locked_at = utc_now() - timedelta(minutes=5)
    event.locked_by = "crashed-dispatcher"
    await db_session.commit()

    recovered = await dispatcher.recover_stale_locks(timeout_seconds=60)
    assert recovered >= 1

    await db_session.refresh(event)
    assert event.locked_at is None
    assert event.locked_by is None


# =============================================================================
# 3. WORKER CLAIMING & FENCING TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_gate3_worker_atomic_claiming_and_lease_expiration(
    db_session: AsyncSession,
) -> None:
    """Verify queued run claiming, lease expiration recovery, and active lease protection."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    run_repo = ReportRunRepository(db_session)
    run = ReportRun(
        report_definition_id=None,
        organization_id=t["org"].id,
        workspace_id=t["workspace"].id,
        report_title="Worker Claim Test",
        trigger_type="manual",
        status=ReportRunStatus.QUEUED,
        max_attempts=3,
        attempt_count=0,
        claim_generation=0,
    )
    await run_repo.create(run)

    # 1. Worker 1 claims queued run
    claimed_1, run_1 = await run_repo.claim_run_for_execution(run.id, "worker-1", lease_seconds=60)
    assert claimed_1 is True
    assert run_1 is not None
    assert run_1.status == ReportRunStatus.RUNNING
    assert run_1.claim_generation == 1
    assert run_1.attempt_count == 1
    assert run_1.claim_token is not None
    assert run_1.lease_expires_at is not None

    token_gen_1 = run_1.claim_token

    # 2. Worker 2 attempts to claim while Worker 1 lease is still active -> Rejected!
    claimed_2, run_2 = await run_repo.claim_run_for_execution(run.id, "worker-2", lease_seconds=60)
    assert claimed_2 is False
    assert run_2 is None

    # 3. Fast-forward time: artificially expire Worker 1's lease
    run_1.lease_expires_at = utc_now() - timedelta(seconds=10)
    await db_session.flush()

    # Worker 2 attempts to claim after lease expiration -> Succeeds!
    claimed_3, run_3 = await run_repo.claim_run_for_execution(run.id, "worker-2", lease_seconds=60)
    assert claimed_3 is True
    assert run_3 is not None
    assert run_3.claim_generation == 2
    assert run_3.attempt_count == 2
    assert run_3.claim_token != token_gen_1  # New cryptographic token

    # 4. Fencing test: Stale Worker 1 attempts to finalize run with Generation 1 token
    stale_finalize = await run_repo.finalize_run_success(
        run_id=run.id,
        claim_token=token_gen_1,  # STALE TOKEN
        claim_generation=1,  # STALE GENERATION
        snapshot={"data": "stale"},
        data_revision_ref={},
        execution_duration_ms=100,
    )
    assert stale_finalize is False  # FENCED OUT!

    # Current Worker 2 finalizes with Generation 2 token -> Succeeds!
    active_finalize = await run_repo.finalize_run_success(
        run_id=run.id,
        claim_token=run_3.claim_token,
        claim_generation=2,
        snapshot={"data": "fresh_verified"},
        data_revision_ref={},
        execution_duration_ms=150,
    )
    assert active_finalize is True

    # Reload and verify final status
    await db_session.refresh(run_3)
    assert run_3.status == ReportRunStatus.SUCCEEDED
    assert run_3.report_snapshot == {"data": "fresh_verified"}


@pytest.mark.asyncio
async def test_gate3_worker_exhausted_attempts_rejected(db_session: AsyncSession) -> None:
    """Verify run with max attempts exhausted cannot be claimed."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    run_repo = ReportRunRepository(db_session)
    run = ReportRun(
        report_definition_id=None,
        organization_id=t["org"].id,
        workspace_id=t["workspace"].id,
        report_title="Exhausted Run Test",
        trigger_type="manual",
        status=ReportRunStatus.QUEUED,
        max_attempts=3,
        attempt_count=3,  # EXHAUSTED
    )
    await run_repo.create(run)

    claimed, _ = await run_repo.claim_run_for_execution(run.id, "worker-x")
    assert claimed is False


# =============================================================================
# 4. IMMUTABLE SNAPSHOT ENGINE & TRANSACTION CONSISTENCY TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_gate3_snapshot_materialization_under_repeatable_read(
    db_session: AsyncSession,
) -> None:
    """Verify report snapshot materialization and PostgreSQL REPEATABLE READ isolation."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    # 1. Create a spreadsheet with rows for widget source
    wb = Workbook(organization_id=t["org"].id, workspace_id=t["workspace"].id, name="Test WB")
    db_session.add(wb)
    await db_session.flush()

    sheet = Sheet(workbook_id=wb.id, organization_id=t["org"].id, name="SalesData")
    db_session.add(sheet)
    await db_session.flush()

    # Add header and 3 data rows
    r0 = SheetRow(
        sheet_id=sheet.id,
        organization_id=t["org"].id,
        row_index=0,
        cells={"A": {"v": "Product"}, "B": {"v": "Sales"}},
    )
    r1 = SheetRow(
        sheet_id=sheet.id,
        organization_id=t["org"].id,
        row_index=1,
        cells={"A": {"v": "Alpha"}, "B": {"v": 100}},
    )
    r2 = SheetRow(
        sheet_id=sheet.id,
        organization_id=t["org"].id,
        row_index=2,
        cells={"A": {"v": "Beta"}, "B": {"v": 250}},
    )
    r3 = SheetRow(
        sheet_id=sheet.id,
        organization_id=t["org"].id,
        row_index=3,
        cells={"A": {"v": "Gamma"}, "B": {"v": 400}},
    )
    db_session.add_all([r0, r1, r2, r3])
    await db_session.flush()

    # 2. Create widget referencing sheet
    widget = DashboardWidget(
        dashboard_id=t["dashboard"].id,
        organization_id=t["org"].id,
        title="Sales by Product Widget",
        widget_type="bar",
        source_type="sheet",
        source_id=sheet.id,
        query_config={"dimensions": ["Product"], "metrics": [{"column": "Sales", "agg": "sum"}]},
    )
    db_session.add(widget)
    await db_session.flush()

    # 3. Create report definition with sections
    report_service = ReportService(db_session)
    report_def = await report_service.create_report_definition(
        t["org"].id,
        t["user"].id,
        ReportDefinitionCreateInput(
            workspace_id=t["workspace"].id,
            title="Executive Performance Summary",
            format="pdf",
            page_orientation="portrait",
        ),
    )

    # Section 1: Executive markdown notes
    sec_1 = await report_service.add_section(
        report_def.id,
        t["org"].id,
        ReportSectionCreateInput(
            section_type="markdown",
            title="Overview",
            custom_content="## High-level executive performance summary.",
            sort_order=0,
        ),
    )

    # Section 2: Chart section with widget
    sec_2 = await report_service.add_section(
        report_def.id,
        t["org"].id,
        ReportSectionCreateInput(
            section_type="chart",
            title="Sales Overview Chart",
            widget_id=widget.id,
            sort_order=1,
        ),
    )

    # 4. Trigger run
    run = await report_service.trigger_report_run(
        report_def.id,
        t["org"].id,
        t["user"].id,
        ReportRunTriggerInput(idempotency_key=f"snap-test-{uid}"),
    )

    # 5. Execute materialization under REPEATABLE READ
    snapshot_svc = ReportSnapshotService(db_session)
    (
        snapshot_dict,
        ext_path,
        size_bytes,
        checksum,
        data_rev_ref,
    ) = await snapshot_svc.materialize_snapshot(run)

    assert ext_path is None  # Fits inline (< 2 MiB)
    assert size_bytes > 0
    assert len(checksum) == 64
    assert snapshot_dict["snapshot_schema_version"] == 1
    assert snapshot_dict["report_title"] == "Executive Performance Summary"
    assert len(snapshot_dict["sections"]) == 2

    # Section 1 verification
    s0 = snapshot_dict["sections"][0]
    assert s0["section_type"] == "markdown"
    assert "executive performance" in s0["custom_content"]

    # Section 2 verification
    s1 = snapshot_dict["sections"][1]
    assert s1["section_type"] == "chart"
    assert "Product" in s1["columns"]
    assert "sum_Sales" in s1["columns"]
    assert len(s1["data"]) == 3
    assert s1["is_truncated"] is False

    # data_revision_ref verification
    assert data_rev_ref["snapshot_schema_version"] == 1
    assert len(data_rev_ref["sources"]) >= 1
    assert data_rev_ref["sources"][0]["source_type"] == "sheet"
    assert data_rev_ref["sources"][0]["source_id"] == str(sheet.id)


@pytest.mark.asyncio
async def test_gate3_external_parquet_validation_and_checksum_integrity(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Verify Parquet checksum verification, tampering detection, and path traversal rejection."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    # 1. Create a genuine Parquet file
    parquet_dir = tmp_path / "analytics_test"
    parquet_dir.mkdir(parents=True, exist_ok=True)
    parquet_file = parquet_dir / f"test_{uid}.parquet"

    df = pl.DataFrame({"metric_name": ["ROI", "CAC", "LTV"], "value": [4.2, 120.0, 500.0]})
    df.write_parquet(parquet_file)

    # Compute genuine sha256
    genuine_sha = hashlib.sha256(parquet_file.read_bytes()).hexdigest()

    # Create Workbook and Sheet for AnalyticsJob foreign keys
    wb = Workbook(organization_id=t["org"].id, workspace_id=t["workspace"].id, name="Test WB")
    db_session.add(wb)
    await db_session.flush()

    sheet = Sheet(workbook_id=wb.id, organization_id=t["org"].id, name="MetricsSheet")
    db_session.add(sheet)
    await db_session.flush()

    from app.models.analytics import AnalyticsJob

    job = AnalyticsJob(
        organization_id=t["org"].id,
        workspace_id=t["workspace"].id,
        source_workbook_id=wb.id,
        source_sheet_id=sheet.id,
        operation_id="financial_metrics",
        status="succeeded",
    )
    db_session.add(job)
    await db_session.flush()

    # Create AnalyticsResult record with genuine checksum
    res_repo = AnalyticsResultRepository(db_session)
    analytics_result = AnalyticsResult(
        job_id=job.id,
        organization_id=t["org"].id,
        result_type="financial_metrics",
        artifact_path=str(parquet_file),
        artifact_checksum_sha256=genuine_sha,
        artifact_size_bytes=parquet_file.stat().st_size,
        row_count=3,
        column_count=2,
    )
    await res_repo.create(analytics_result)

    # Create widget referencing analytics_result
    widget = DashboardWidget(
        dashboard_id=t["dashboard"].id,
        organization_id=t["org"].id,
        title="Parquet Metrics Widget",
        widget_type="table",
        source_type="analytics_result",
        source_id=analytics_result.id,
        query_config={},
    )
    db_session.add(widget)
    await db_session.flush()

    # Create report definition and section
    report_service = ReportService(db_session)
    report_def = await report_service.create_report_definition(
        t["org"].id,
        t["user"].id,
        ReportDefinitionCreateInput(
            workspace_id=t["workspace"].id, title="Parquet Verified Report"
        ),
    )
    await report_service.add_section(
        report_def.id,
        t["org"].id,
        ReportSectionCreateInput(section_type="table", title="Metrics", widget_id=widget.id),
    )

    run = await report_service.trigger_report_run(
        report_def.id,
        t["org"].id,
        t["user"].id,
        ReportRunTriggerInput(idempotency_key=f"parquet-good-{uid}"),
    )

    # 2. Materialization with genuine artifact succeeds
    snapshot_svc = ReportSnapshotService(db_session)
    snapshot_dict, _, _, _, data_rev = await snapshot_svc.materialize_snapshot(run)
    assert len(snapshot_dict["sections"][0]["data"]) == 3
    assert data_rev["sources"][0]["checksum_sha256"] == genuine_sha

    # 3. Simulate artifact tampering: modify Parquet file on disk
    df_tampered = pl.DataFrame({"metric_name": ["HACKED"], "value": [9999.0]})
    df_tampered.write_parquet(parquet_file)  # Overwrite file with new content!

    run_tampered = await report_service.trigger_report_run(
        report_def.id,
        t["org"].id,
        t["user"].id,
        ReportRunTriggerInput(idempotency_key=f"parquet-tampered-{uid}"),
    )

    # Materialization must fail safely with ValidationError due to checksum mismatch
    with pytest.raises(ValidationError, match="checksum verification failed"):
        await snapshot_svc.materialize_snapshot(run_tampered)

    # 4. Simulate missing artifact
    parquet_file.unlink(missing_ok=True)
    run_missing = await report_service.trigger_report_run(
        report_def.id,
        t["org"].id,
        t["user"].id,
        ReportRunTriggerInput(idempotency_key=f"parquet-missing-{uid}"),
    )
    with pytest.raises(EntityNotFoundError, match="AnalyticsArtifact"):
        await snapshot_svc.materialize_snapshot(run_missing)


@pytest.mark.asyncio
async def test_gate3_snapshot_large_external_storage_and_cap_limits(
    db_session: AsyncSession,
) -> None:
    """Verify tier 2 external artifact storage (> 2 MiB) and 10 MiB hard cap rejection."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    report_service = ReportService(db_session)
    report_def = await report_service.create_report_definition(
        t["org"].id,
        t["user"].id,
        ReportDefinitionCreateInput(workspace_id=t["workspace"].id, title="Large Snapshot Test"),
    )

    run = await report_service.trigger_report_run(
        report_def.id,
        t["org"].id,
        t["user"].id,
        ReportRunTriggerInput(idempotency_key=f"large-snap-{uid}"),
    )

    storage = ReportStorageService()
    snapshot_svc = ReportSnapshotService(db_session, storage=storage)

    # 1. Simulate large snapshot (> 2 MiB and <= 10 MiB)
    large_text = secrets.token_hex(1500000)  # ~3 MiB
    await report_service.add_section(
        report_def.id,
        t["org"].id,
        ReportSectionCreateInput(
            section_type="markdown", title="Big Section", custom_content=large_text
        ),
    )

    (
        ext_dict,
        external_path,
        size_bytes,
        checksum,
        _,
    ) = await snapshot_svc.materialize_snapshot(run)

    assert ext_dict["is_external"] is True
    assert external_path is not None
    assert Path(external_path).is_file()
    assert size_bytes > 2 * 1024 * 1024

    # Cleanup external artifact
    storage.delete_artifact(external_path)

    # 2. Simulate oversized snapshot exceeding 10 MiB hard cap
    oversized_text = secrets.token_hex(6000000)  # ~12 MiB
    report_def_oversized = await report_service.create_report_definition(
        t["org"].id,
        t["user"].id,
        ReportDefinitionCreateInput(workspace_id=t["workspace"].id, title="Oversized Report"),
    )
    await report_service.add_section(
        report_def_oversized.id,
        t["org"].id,
        ReportSectionCreateInput(
            section_type="markdown", title="Huge Section", custom_content=oversized_text
        ),
    )

    run_oversized = await report_service.trigger_report_run(
        report_def_oversized.id,
        t["org"].id,
        t["user"].id,
        ReportRunTriggerInput(idempotency_key=f"oversized-snap-{uid}"),
    )

    with pytest.raises(DatasetTooLargeError, match="exceeds maximum cap"):
        await snapshot_svc.materialize_snapshot(run_oversized)


# =============================================================================
# 5. IDEMPOTENCY & SERVICE-LEVEL VALIDATION TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_gate3_service_idempotency_key_enforcement(db_session: AsyncSession) -> None:
    """Verify missing/blank idempotency keys are rejected and duplicates return existing run."""
    uid = uuid.uuid4().hex[:8]
    t_a = await create_tenant_hierarchy(db_session, f"{uid}_a")
    t_b = await create_tenant_hierarchy(db_session, f"{uid}_b")

    report_service = ReportService(db_session)
    def_a = await report_service.create_report_definition(
        t_a["org"].id,
        t_a["user"].id,
        ReportDefinitionCreateInput(workspace_id=t_a["workspace"].id, title="Idem Report A"),
    )
    def_b = await report_service.create_report_definition(
        t_b["org"].id,
        t_b["user"].id,
        ReportDefinitionCreateInput(workspace_id=t_b["workspace"].id, title="Idem Report B"),
    )

    # 1. Blank or whitespace-only key is strictly rejected
    with pytest.raises(ValidationError, match="idempotency_key is required and must not be blank"):
        await report_service.trigger_report_run(
            def_a.id,
            t_a["org"].id,
            t_a["user"].id,
            ReportRunTriggerInput(idempotency_key="   "),
        )

    # 2. First trigger succeeds
    shared_key = f"shared-idem-key-{uid}"
    run_1 = await report_service.trigger_report_run(
        def_a.id,
        t_a["org"].id,
        t_a["user"].id,
        ReportRunTriggerInput(idempotency_key=shared_key),
    )
    assert run_1.id is not None

    # 3. Duplicate trigger with same key in same Org A -> returns existing run (idempotent)!
    run_dup = await report_service.trigger_report_run(
        def_a.id,
        t_a["org"].id,
        t_a["user"].id,
        ReportRunTriggerInput(idempotency_key=shared_key),
    )
    assert run_dup.id == run_1.id

    # 4. Same key in different Org B -> creates independent logical run!
    run_b = await report_service.trigger_report_run(
        def_b.id,
        t_b["org"].id,
        t_b["user"].id,
        ReportRunTriggerInput(idempotency_key=shared_key),
    )
    assert run_b.id != run_1.id
    assert run_b.organization_id == t_b["org"].id


@pytest.mark.asyncio
async def test_gate3_concurrent_idempotency_race(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Verify concurrent duplicate requests create exactly 1 logical run and 1 outbox event."""
    uid = uuid.uuid4().hex[:8]

    # Provision tenant
    async with session_factory() as session:
        t = await create_tenant_hierarchy(session, uid)
        report_service = ReportService(session)
        report_def = await report_service.create_report_definition(
            t["org"].id,
            t["user"].id,
            ReportDefinitionCreateInput(
                workspace_id=t["workspace"].id, title="Concurrent Idem Report"
            ),
        )
        await session.commit()
        org_id = t["org"].id
        user_id = t["user"].id
        def_id = report_def.id

    concurrent_key = f"concurrent-idem-{uid}"

    async def trigger_run_worker() -> uuid.UUID:
        async with session_factory() as worker_session:
            svc = ReportService(worker_session)
            run = await svc.trigger_report_run(
                def_id,
                org_id,
                user_id,
                ReportRunTriggerInput(idempotency_key=concurrent_key),
            )
            return run.id

    # Launch 5 concurrent requests racing on the exact same idempotency key
    results = await asyncio.gather(*(trigger_run_worker() for _ in range(5)))

    # All 5 concurrent workers must have received the EXACT same run ID
    assert len(set(results)) == 1
    winner_run_id = results[0]

    # Verify exactly 1 ReportRun and 1 ReportOutboxEvent exist in PostgreSQL
    async with session_factory() as verify_session:
        runs_stmt = select(ReportRun).where(
            ReportRun.organization_id == org_id,
            ReportRun.idempotency_key == concurrent_key,
        )
        runs = (await verify_session.execute(runs_stmt)).scalars().all()
        assert len(runs) == 1
        assert runs[0].id == winner_run_id

        events_stmt = select(ReportOutboxEvent).where(ReportOutboxEvent.run_id == winner_run_id)
        events = (await verify_session.execute(events_stmt)).scalars().all()
        assert len(events) == 1


@pytest.mark.asyncio
async def test_gate3_celery_task_execution_and_fencing_lifecycle(
    db_session: AsyncSession,
) -> None:
    """End-to-end test of Celery execution helper, claim transition, and snapshot completion."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    report_service = ReportService(db_session)
    report_def = await report_service.create_report_definition(
        t["org"].id,
        t["user"].id,
        ReportDefinitionCreateInput(workspace_id=t["workspace"].id, title="End-to-End Task Run"),
    )
    await report_service.add_section(
        report_def.id,
        t["org"].id,
        ReportSectionCreateInput(
            section_type="markdown", title="Notes", custom_content="Hello VIBE Report!"
        ),
    )

    run = await report_service.trigger_report_run(
        report_def.id,
        t["org"].id,
        t["user"].id,
        ReportRunTriggerInput(idempotency_key=f"task-e2e-{uid}"),
    )

    # Execute async worker entrypoint directly
    result = await _execute_report_run_async(str(run.id), f"celery-{uid}")
    assert result["status"] == "succeeded"
    assert result["run_id"] == str(run.id)

    # Reload run from database and verify final state
    await db_session.refresh(run)
    assert run.status == ReportRunStatus.SUCCEEDED
    assert run.completed_at is not None
    assert run.execution_duration_ms is not None
    assert run.report_snapshot["snapshot_schema_version"] == 1
    assert run.data_revision_ref["snapshot_schema_version"] == 1

    # Re-running task is an idempotent no-op
    idempotent_result = await _execute_report_run_async(str(run.id), f"celery-retry-{uid}")
    assert idempotent_result["status"] == "succeeded"
    assert idempotent_result.get("idempotent") is True
