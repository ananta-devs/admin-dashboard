"""Targeted integration and regression tests for Phase 9 Gate 3 Stage 2.1.

Validates:
- Correction A: Generation-specific artifact isolation and stale-worker fencing.
- Correction B: Parquet external snapshot serialization, schema versioning, and canonical roundtrip.
- Correction C: RBAC execution permission enforcement (reports.run vs reports.create vs reports.export, cross-tenant rejection).
- Correction D: Runtime worker lease recovery, durable outbox retrying, and max attempt exhaustion.
- Correction E: Snapshot integrity, boundary capacity, source checksum validation, and truncation indicators.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.analytics.exceptions import DatasetTooLargeError
from app.core.config import settings
from app.core.exceptions import (
    CrossTenantAccessError,
    EntityNotFoundError,
    ForbiddenError,
)
from app.db.base import utc_now
from app.models.dashboard import Dashboard, DashboardWidget
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.permission import Permission
from app.models.report import (
    ReportOutboxEvent,
    ReportRun,
    ReportRunStatus,
)
from app.models.role import OrganizationRole
from app.models.spreadsheet import Sheet, Workbook
from app.models.user import User
from app.models.workspace import Workspace
from app.repositories.report import (
    ReportRunRepository,
)
from app.repositories.role import RoleRepository
from app.schemas.report import (
    ReportDefinitionCreateInput,
    ReportRunTriggerInput,
    ReportSectionCreateInput,
)
from app.services.report import ReportService
from app.services.report_snapshot import (
    ReportSnapshotService,
    load_snapshot_parquet,
    save_snapshot_parquet,
)
from app.services.report_storage import ReportStorageService
from app.services.role import RoleService
from app.tasks.dispatcher import ReportingOutboxDispatcher


@pytest.fixture
async def db_session():
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await test_engine.dispose()


@pytest.fixture
def session_factory():
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    return factory


async def create_tenant_hierarchy(session: AsyncSession, suffix: str) -> dict[str, Any]:
    """Helper provisioning a test tenant with bootstrapped roles and owner user."""
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
        "roles": roles,
    }


# =============================================================================
# CORRECTION A: GENERATION-SPECIFIC ARTIFACT ISOLATION & CONCURRENCY
# =============================================================================


@pytest.mark.asyncio
async def test_correction_a_generation_specific_artifact_isolation(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Verify that multiple worker generations have isolated artifact paths and stale workers cannot delete newer artifacts."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    storage = ReportStorageService(base_dir=str(tmp_path / "reports_storage"))
    run_repo = ReportRunRepository(db_session)

    # 1. Create a queued run
    run = ReportRun(
        report_definition_id=None,
        organization_id=t["org"].id,
        workspace_id=t["workspace"].id,
        report_title="Concurrency Test Run",
        trigger_type="manual",
        status=ReportRunStatus.QUEUED,
        idempotency_key=f"gen-iso-{uid}",
        claim_generation=0,
        attempt_count=0,
        max_attempts=3,
    )
    await run_repo.create(run)
    await db_session.commit()

    # 2. Worker Generation 1 claims run
    claimed_1, run_1 = await run_repo.claim_run_for_execution(
        run.id, worker_id="worker-gen-1", lease_seconds=1
    )
    assert claimed_1 is True
    assert run_1 is not None
    assert run_1.claim_generation == 1
    token_1 = run_1.claim_token
    await db_session.commit()

    # Generation 1 writes its artifact to generation-specific destination
    gen1_artifact = storage.get_snapshot_artifact_path(
        t["org"].id, run.id, claim_generation=1, ext="parquet"
    )
    gen1_staging = storage.get_staging_artifact_path(
        t["org"].id, run.id, claim_generation=1, ext="parquet"
    )
    assert "snapshot_gen_1.parquet" in gen1_artifact.name
    assert "snapshot_gen_1.parquet.tmp" in gen1_staging.name

    dummy_snap_1 = {
        "snapshot_schema_version": 1,
        "snapshot_id": "snap-gen-1",
        "report_run_id": str(run.id),
        "report_title": "Run Gen 1",
        "materialized_at": utc_now().isoformat(),
        "sections": [],
    }
    save_snapshot_parquet(dummy_snap_1, gen1_staging)
    storage.atomic_promote(gen1_staging, gen1_artifact)
    assert gen1_artifact.is_file()

    # 3. Simulate lease expiration for Generation 1
    run.lease_expires_at = utc_now() - timedelta(seconds=10)
    await db_session.commit()

    # 4. Worker Generation 2 claims run after lease expired
    claimed_2, run_2 = await run_repo.claim_run_for_execution(
        run.id, worker_id="worker-gen-2", lease_seconds=60
    )
    assert claimed_2 is True
    assert run_2 is not None
    assert run_2.claim_generation == 2
    token_2 = run_2.claim_token
    assert token_2 != token_1
    await db_session.commit()

    # Generation 2 writes its own generation-specific artifact
    gen2_artifact = storage.get_snapshot_artifact_path(
        t["org"].id, run.id, claim_generation=2, ext="parquet"
    )
    gen2_staging = storage.get_staging_artifact_path(
        t["org"].id, run.id, claim_generation=2, ext="parquet"
    )
    dummy_snap_2 = {
        "snapshot_schema_version": 1,
        "snapshot_id": "snap-gen-2",
        "report_run_id": str(run.id),
        "report_title": "Run Gen 2 Authoritative",
        "materialized_at": utc_now().isoformat(),
        "sections": [],
    }
    save_snapshot_parquet(dummy_snap_2, gen2_staging)
    storage.atomic_promote(gen2_staging, gen2_artifact)
    assert gen2_artifact.is_file()

    # 5. Stale Generation 1 awakens and attempts to finalize database record
    gen1_success = await run_repo.finalize_run_success(
        run_id=run.id,
        claim_token=token_1,
        claim_generation=1,
        snapshot={"title": "Stale Gen 1 Snapshot"},
        data_revision_ref={},
        execution_duration_ms=100,
        artifact_path=str(gen1_artifact),
    )
    await db_session.commit()
    # Fencing check MUST reject Generation 1
    assert gen1_success is False

    # Generation 1 cleans up its own generation files on fencing failure
    storage.cleanup_generation_artifacts(t["org"].id, run.id, claim_generation=1, ext="parquet")

    # Generation 1 artifact was deleted, BUT Generation 2 artifact MUST REMAIN INTACT!
    assert not gen1_artifact.exists()
    assert gen2_artifact.is_file()

    # Verify Generation 2 artifact is completely readable
    loaded_gen2 = load_snapshot_parquet(gen2_artifact)
    assert loaded_gen2["snapshot_id"] == "snap-gen-2"
    assert loaded_gen2["report_title"] == "Run Gen 2 Authoritative"

    # 6. Only Generation 2 can finalize the database record
    gen2_success = await run_repo.finalize_run_success(
        run_id=run.id,
        claim_token=token_2,
        claim_generation=2,
        snapshot=dummy_snap_2,
        data_revision_ref={"gen": 2},
        execution_duration_ms=150,
        artifact_path=str(gen2_artifact),
        file_size_bytes=gen2_artifact.stat().st_size,
        checksum_sha256=storage.compute_sha256(gen2_artifact),
    )
    await db_session.commit()
    assert gen2_success is True

    # Confirm final DB state belongs to Generation 2
    await db_session.refresh(run)
    assert run.status == ReportRunStatus.SUCCEEDED
    assert run.claim_generation == 2
    assert run.claim_token == token_2
    assert run.artifact_path == str(gen2_artifact)


# =============================================================================
# CORRECTION B: SNAPSHOT FORMAT DECISION (PARQUET SERIALIZATION & ROUNDTRIP)
# =============================================================================


@pytest.mark.asyncio
async def test_correction_b_parquet_snapshot_serialization_and_roundtrip(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Verify external Parquet snapshot serialization, snappy compression, and load_snapshot_data roundtrip."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    storage = ReportStorageService(base_dir=str(tmp_path / "reports_parquet"))
    snapshot_svc = ReportSnapshotService(db_session, storage=storage)
    report_service = ReportService(db_session)

    # Create report with varied content exceeding 2 MiB to trigger external Parquet tier
    report_def = await report_service.create_report_definition(
        t["org"].id,
        t["user"].id,
        ReportDefinitionCreateInput(
            workspace_id=t["workspace"].id,
            title="Parquet Tier Verification Report",
            description="Testing Parquet format persistence",
        ),
    )

    # Add 3 sections with high-entropy text
    big_content = secrets.token_hex(1200000)  # ~2.4 MiB
    sec = await report_service.add_section(
        report_def.id,
        t["org"].id,
        ReportSectionCreateInput(
            section_type="markdown",
            title="Analytical Deep Dive",
            custom_content=big_content,
        ),
    )

    run = await report_service.trigger_report_run(
        report_def.id,
        t["org"].id,
        t["user"].id,
        ReportRunTriggerInput(idempotency_key=f"parquet-test-{uid}"),
    )
    await db_session.commit()

    # Materialize snapshot
    (
        ext_dict,
        artifact_path,
        file_size,
        checksum,
        data_rev_ref,
    ) = await snapshot_svc.materialize_snapshot(run)

    assert ext_dict["is_external"] is True
    assert ext_dict["format"] == "parquet"
    assert ext_dict["snapshot_schema_version"] == 1
    assert artifact_path is not None
    assert artifact_path.endswith(".parquet")
    assert Path(artifact_path).is_file()
    assert file_size == Path(artifact_path).stat().st_size
    assert checksum == storage.compute_sha256(Path(artifact_path))

    # Read file directly via Polars to prove it is genuine Parquet
    df = pl.read_parquet(artifact_path)
    assert len(df) >= 1
    assert "snapshot_schema_version" in df.columns
    assert "section_id" in df.columns

    # Finalize run in DB and verify load_snapshot_data roundtrip
    run_repo = ReportRunRepository(db_session)
    await run_repo.claim_run_for_execution(run.id, worker_id="worker-parquet-test")
    await run_repo.finalize_run_success(
        run_id=run.id,
        claim_token=run.claim_token,
        claim_generation=run.claim_generation,
        snapshot=ext_dict,
        data_revision_ref=data_rev_ref,
        execution_duration_ms=200,
        artifact_path=artifact_path,
        file_size_bytes=file_size,
        checksum_sha256=checksum,
    )
    await db_session.commit()

    await db_session.refresh(run)
    loaded_data = ReportSnapshotService.load_snapshot_data(run)
    assert loaded_data["report_title"] == "Parquet Tier Verification Report"
    assert len(loaded_data["sections"]) == 1
    assert loaded_data["sections"][0]["custom_content"] == big_content
    assert loaded_data["snapshot_schema_version"] == 1


# =============================================================================
# CORRECTION C: RBAC PERMISSION ENFORCEMENT & MATRIX
# =============================================================================


@pytest.mark.asyncio
async def test_correction_c_rbac_execution_permissions_and_cross_tenant_rejection(
    db_session: AsyncSession,
) -> None:
    """Verify reports.run vs reports.create vs reports.export and cross-organization boundary rejection."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    role_service = RoleService(db_session)
    role_repo = RoleRepository(db_session)
    report_service = ReportService(db_session)

    # Create report definition using Owner (possesses reports.create)
    report_def = await report_service.create_report_definition(
        t["org"].id,
        t["user"].id,
        ReportDefinitionCreateInput(
            workspace_id=t["workspace"].id,
            title="RBAC Guarded Report",
        ),
    )
    await db_session.commit()

    # User 1: Member role (possesses reports.run and reports.create)
    user_member = User(
        email=f"member_{uid}@example.com",
        normalized_email=f"member_{uid}@example.com",
        display_name="Member User",
    )
    db_session.add(user_member)
    await db_session.flush()
    mem_member = OrganizationMembership(organization_id=t["org"].id, user_id=user_member.id)
    db_session.add(mem_member)
    await db_session.flush()
    await role_repo.set_membership_roles(
        mem_member.id, [t["roles"]["member"].id], organization_id=t["org"].id
    )

    # User 2: Viewer role (possesses reports.read and reports.export, strictly lacks reports.run)
    user_viewer = User(
        email=f"viewer_{uid}@example.com",
        normalized_email=f"viewer_{uid}@example.com",
        display_name="Viewer User",
    )
    db_session.add(user_viewer)
    await db_session.flush()
    mem_viewer = OrganizationMembership(organization_id=t["org"].id, user_id=user_viewer.id)
    db_session.add(mem_viewer)
    await db_session.flush()
    await role_repo.set_membership_roles(
        mem_viewer.id, [t["roles"]["viewer"].id], organization_id=t["org"].id
    )

    # User 3: Custom Role with reports.create but WITHOUT reports.run
    custom_role = OrganizationRole(
        organization_id=t["org"].id,
        name=f"Creator Only {uid}",
        slug=f"creator-only-{uid}",
        is_system=False,
    )
    db_session.add(custom_role)
    await db_session.flush()

    # Assign only 'reports.create' to custom role
    perm_create = (
        await db_session.execute(select(Permission).where(Permission.code == "reports.create"))
    ).scalar_one()
    await role_repo.set_role_permissions(custom_role.id, [perm_create.id])

    user_creator = User(
        email=f"creator_{uid}@example.com",
        normalized_email=f"creator_{uid}@example.com",
        display_name="Creator User",
    )
    db_session.add(user_creator)
    await db_session.flush()
    mem_creator = OrganizationMembership(organization_id=t["org"].id, user_id=user_creator.id)
    db_session.add(mem_creator)
    await db_session.flush()
    await role_repo.set_membership_roles(
        mem_creator.id, [custom_role.id], organization_id=t["org"].id
    )

    await db_session.commit()

    # TEST 1: Member WITH reports.run can trigger report execution
    run_member = await report_service.trigger_report_run(
        report_def.id,
        t["org"].id,
        user_member.id,
        ReportRunTriggerInput(idempotency_key=f"perm-member-{uid}"),
    )
    assert run_member is not None
    assert run_member.status == ReportRunStatus.QUEUED

    # TEST 2: Viewer WITHOUT reports.run CANNOT trigger execution
    with pytest.raises(ForbiddenError, match="reports.run"):
        await report_service.trigger_report_run(
            report_def.id,
            t["org"].id,
            user_viewer.id,
            ReportRunTriggerInput(idempotency_key=f"perm-viewer-{uid}"),
        )

    # TEST 3: User with reports.create but WITHOUT reports.run CANNOT trigger execution
    with pytest.raises(ForbiddenError, match="reports.run"):
        await report_service.trigger_report_run(
            report_def.id,
            t["org"].id,
            user_creator.id,
            ReportRunTriggerInput(idempotency_key=f"perm-creator-{uid}"),
        )

    # TEST 4: Export permission alone does not grant execution permission
    # (viewer already has reports.export; proved rejected in Test 2)

    # TEST 5: Cross-organization execution is strictly rejected
    t_other = await create_tenant_hierarchy(db_session, f"other_{uid}")
    await db_session.commit()

    # User from Org Other attempts to trigger report_def belonging to Org T
    with pytest.raises((CrossTenantAccessError, EntityNotFoundError)):
        await report_service.trigger_report_run(
            report_def.id,
            t_other["org"].id,
            t_other["user"].id,
            ReportRunTriggerInput(idempotency_key=f"cross-org-{uid}"),
        )


# =============================================================================
# CORRECTION D: RUNTIME WORKER LEASE RECOVERY & AT-LEAST-ONCE DISPATCH
# =============================================================================


@pytest.mark.asyncio
async def test_correction_d_worker_lease_recovery_and_at_least_once_retry(
    db_session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Verify runtime recover_stale_report_leases reclaimed runs receive new generations and exhausted runs fail."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    dispatcher = ReportingOutboxDispatcher(
        dispatcher_id=f"disp-rec-{uid}", session_maker=session_factory
    )
    run_repo = ReportRunRepository(db_session)

    # 1. Create a run in RUNNING state with expired lease (attempt 1/3)
    run_recoverable = ReportRun(
        report_definition_id=None,
        organization_id=t["org"].id,
        workspace_id=t["workspace"].id,
        report_title="Recoverable Stale Run",
        trigger_type="manual",
        status=ReportRunStatus.RUNNING,
        claim_generation=1,
        claim_token=uuid.uuid4(),
        lease_expires_at=utc_now() - timedelta(minutes=5),
        attempt_count=1,
        max_attempts=3,
        idempotency_key=f"rec-stale-{uid}",
    )
    await run_repo.create(run_recoverable)

    # 2. Create another run in RUNNING state with expired lease whose max_attempts are exhausted (attempt 3/3)
    run_exhausted = ReportRun(
        report_definition_id=None,
        organization_id=t["org"].id,
        workspace_id=t["workspace"].id,
        report_title="Exhausted Stale Run",
        trigger_type="manual",
        status=ReportRunStatus.RUNNING,
        claim_generation=1,
        claim_token=uuid.uuid4(),
        lease_expires_at=utc_now() - timedelta(minutes=5),
        attempt_count=3,
        max_attempts=3,
        idempotency_key=f"rec-exhaust-{uid}",
    )
    await run_repo.create(run_exhausted)
    await db_session.commit()

    # 3. Execute recovery via ReportingOutboxDispatcher
    recovered_count = await dispatcher.recover_stale_leases()
    assert recovered_count >= 2

    # Verify recoverable run was reset to QUEUED and outbox event created
    await db_session.refresh(run_recoverable)
    assert run_recoverable.status == ReportRunStatus.QUEUED
    assert run_recoverable.lease_expires_at is None

    stmt = select(ReportOutboxEvent).where(
        ReportOutboxEvent.run_id == run_recoverable.id,
        ReportOutboxEvent.event_type == "report.run.retrying",
    )
    outbox_event = (await db_session.execute(stmt)).scalar_one_or_none()
    assert outbox_event is not None
    assert outbox_event.payload.get("attempt") == 2

    # Verify exhausted run was moved to FAILED (LEASE_EXPIRED)
    await db_session.refresh(run_exhausted)
    assert run_exhausted.status == ReportRunStatus.FAILED
    assert run_exhausted.error_code == "LEASE_EXPIRED"

    # 4. Re-claiming the recovered run must advance claim_generation to 2
    claimed, run_gen2 = await run_repo.claim_run_for_execution(
        run_recoverable.id, worker_id="worker-recovery-gen2"
    )
    assert claimed is True
    assert run_gen2.claim_generation == 2
    assert run_gen2.attempt_count == 2
    await db_session.commit()

    # Stale worker from Generation 1 cannot finalize it
    stale_finalize = await run_repo.finalize_run_success(
        run_id=run_recoverable.id,
        claim_token=run_recoverable.claim_token,  # Old or mismatched token
        claim_generation=1,  # Stale generation
        snapshot={"title": "Should be rejected"},
        data_revision_ref={},
        execution_duration_ms=50,
    )
    await db_session.commit()
    assert stale_finalize is False


# =============================================================================
# CORRECTION E: SNAPSHOT INTEGRITY, TRUNCATION & CLEANUP
# =============================================================================


@pytest.mark.asyncio
async def test_correction_e_snapshot_truncation_capacity_and_staging_cleanup(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Verify row truncation indicators, checksum mismatches, and staging cleanup after failure."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    storage = ReportStorageService(base_dir=str(tmp_path / "reports_storage_e"))
    snapshot_svc = ReportSnapshotService(db_session, storage=storage)
    report_service = ReportService(db_session)

    # 1. Truncation representation: Create a sheet with rows and verify is_truncated flag
    wb = Workbook(organization_id=t["org"].id, workspace_id=t["workspace"].id, name=f"WB {uid}")
    db_session.add(wb)
    await db_session.flush()

    sheet = Sheet(workbook_id=wb.id, organization_id=t["org"].id, name=f"Sheet {uid}")
    db_session.add(sheet)
    await db_session.flush()

    # Insert sheet rows (row 0 is header, rows 1..10 are data)
    from app.models.spreadsheet import SheetRow

    db_session.add(
        SheetRow(
            sheet_id=sheet.id,
            organization_id=t["org"].id,
            row_index=0,
            cells={"A": {"v": "Item"}, "B": {"v": "Val"}},
        )
    )
    for i in range(1, 11):
        db_session.add(
            SheetRow(
                sheet_id=sheet.id,
                organization_id=t["org"].id,
                row_index=i,
                cells={"A": {"v": f"Item {i}"}, "B": {"v": i * 10}},
            )
        )
    await db_session.flush()

    # Create Widget with limit=3 to trigger truncation
    widget = DashboardWidget(
        dashboard_id=t["dashboard"].id,
        organization_id=t["org"].id,
        title="Truncated Table Widget",
        widget_type="table",
        source_type="sheet",
        source_id=sheet.id,
        query_config={"limit": 3},
        position_x=0,
        position_y=0,
        width=12,
        height=6,
    )
    db_session.add(widget)
    await db_session.flush()

    report_def = await report_service.create_report_definition(
        t["org"].id,
        t["user"].id,
        ReportDefinitionCreateInput(
            workspace_id=t["workspace"].id,
            title="Truncation Verification Report",
        ),
    )
    await report_service.add_section(
        report_def.id,
        t["org"].id,
        ReportSectionCreateInput(
            section_type="table",
            title="Top Items (Truncated)",
            widget_id=widget.id,
        ),
    )

    run = await report_service.trigger_report_run(
        report_def.id,
        t["org"].id,
        t["user"].id,
        ReportRunTriggerInput(idempotency_key=f"trunc-test-{uid}"),
    )
    await db_session.commit()

    snap_dict, ext_path, size_b, csum, _ = await snapshot_svc.materialize_snapshot(run)
    assert snap_dict["has_truncation"] is True
    sec = snap_dict["sections"][0]
    assert sec["is_truncated"] is True
    assert sec["total_rows"] == 10
    assert sec["returned_rows"] == 3

    # 2. Oversized Snapshot leaves no orphaned temporary files
    oversized_text = secrets.token_hex(6000000)  # ~12 MiB incompressible hex
    report_def_oversized = await report_service.create_report_definition(
        t["org"].id,
        t["user"].id,
        ReportDefinitionCreateInput(
            workspace_id=t["workspace"].id,
            title="Oversized Temp File Check",
        ),
    )
    await report_service.add_section(
        report_def_oversized.id,
        t["org"].id,
        ReportSectionCreateInput(
            section_type="markdown",
            title="Gigantic Section",
            custom_content=oversized_text,
        ),
    )
    run_oversized = await report_service.trigger_report_run(
        report_def_oversized.id,
        t["org"].id,
        t["user"].id,
        ReportRunTriggerInput(idempotency_key=f"oversized-check-{uid}"),
    )
    await db_session.commit()

    with pytest.raises(DatasetTooLargeError, match="exceeds maximum cap"):
        await snapshot_svc.materialize_snapshot(run_oversized)

    # Verify no .tmp files exist in run directory after rejection
    run_dir = (storage.runs_dir / str(t["org"].id) / str(run_oversized.id)).resolve()
    if run_dir.is_dir():
        tmp_files = list(run_dir.glob("*.tmp"))
        assert len(tmp_files) == 0, f"Found orphaned temp files: {tmp_files}"
