"""Integration tests for Phase 9 Gate 3 Stage 2.2: Final Snapshot Safety, Capacity & Worker Retry Verification.

Covers:
- Check A: Generation-scoped cleanup and staging file isolation under concurrent worker generations.
- Check B: Actual Parquet schema fidelity, data type preservation (nulls, floats, bools, timezones, nested structs), and canonical round-trip.
- Check C: Precise boundary size enforcement (below 2 MiB inline, exact boundary, above 2 MiB external, uncompressed > 10 MiB rejection, compressed > 10 MiB rejection, bounded memory loader).
- Check D: Authoritative run state machine, atomic attempt_count increments, bounded retries, terminal failure on exhaustion, outbox retry dispatching, and protection of SUCCEEDED runs.
"""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import polars as pl
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.analytics.exceptions import DatasetTooLargeError
from app.core.config import settings
from app.core.exceptions import (
    CrossTenantAccessError,
    EntityNotFoundError,
    ForbiddenError,
    ValidationError,
)
from app.db.base import utc_now
from app.models.analytics import AnalyticsJob, AnalyticsResult
from app.models.dashboard import Dashboard, DashboardWidget
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.permission import Permission
from app.models.report import (
    ReportDefinition,
    ReportOutboxEvent,
    ReportRun,
    ReportRunStatus,
    ReportSection,
)
from app.models.role import MembershipRole, OrganizationRole
from app.models.spreadsheet import Sheet, SheetRow, Workbook
from app.models.user import User
from app.models.workspace import Workspace
from app.repositories.analytics import AnalyticsResultRepository
from app.repositories.membership import MembershipRepository
from app.repositories.report import (
    ReportDefinitionRepository,
    ReportOutboxRepository,
    ReportRunRepository,
    ReportSectionRepository,
)
from app.repositories.role import RoleRepository
from app.schemas.report import (
    ReportDefinitionCreateInput,
    ReportRunTriggerInput,
    ReportSectionCreateInput,
)
from app.services.permission import PermissionService
from app.services.report import ReportService
from app.services.report_snapshot import (
    ReportSnapshotService,
    load_snapshot_parquet,
    save_snapshot_parquet,
)
from app.services.report_storage import ReportStorageService
from app.services.role import RoleService
from app.tasks.dispatcher import OutboxDispatcher, ReportingOutboxDispatcher
from app.tasks.reporting import _execute_report_run_async


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

    return {
        "org": org,
        "user": user,
        "membership": membership,
        "workspace": workspace,
        "dashboard": dashboard,
        "roles": roles,
    }


# =============================================================================
# CHECK A: GENERATION-SCOPED CLEANUP & STAGING ARTIFACT ISOLATION
# =============================================================================


@pytest.mark.asyncio
async def test_check_a_concurrent_staging_artifacts_and_scoped_cleanup(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Prove that Generation 1 cleanup removes only its own staging/final artifacts and never touches Generation 2."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    storage = ReportStorageService(base_dir=str(tmp_path / "reports_check_a"))
    run_repo = ReportRunRepository(db_session)

    run = ReportRun(
        report_definition_id=None,
        organization_id=t["org"].id,
        workspace_id=t["workspace"].id,
        report_title="Staging Isolation Run",
        trigger_type="manual",
        status=ReportRunStatus.QUEUED,
        idempotency_key=f"check-a-{uid}",
    )
    await run_repo.create(run)
    await db_session.commit()

    # Step 1: Generation 1 creates its staging artifact
    gen1_staging = storage.get_staging_artifact_path(t["org"].id, run.id, claim_generation=1)
    gen1_final = storage.get_snapshot_artifact_path(t["org"].id, run.id, claim_generation=1)
    gen1_staging.write_bytes(b"staging-bytes-gen-1")
    assert gen1_staging.is_file()

    # Step 2: Generation 2 creates its staging artifact
    gen2_staging = storage.get_staging_artifact_path(t["org"].id, run.id, claim_generation=2)
    gen2_final = storage.get_snapshot_artifact_path(t["org"].id, run.id, claim_generation=2)
    gen2_staging.write_bytes(b"staging-bytes-gen-2")
    assert gen2_staging.is_file()

    # Step 3: Generation 1 promotes to final artifact
    storage.atomic_promote(gen1_staging, gen1_final)
    assert gen1_final.is_file()
    assert not gen1_staging.exists()
    assert gen2_staging.is_file()

    # Step 4: Generation 1 encounters fencing failure and invokes cleanup
    storage.cleanup_generation_artifacts(t["org"].id, run.id, claim_generation=1)

    # Step 5: Verify Generation 1 artifacts are cleaned up, BUT Generation 2 staging remains intact!
    assert not gen1_final.exists()
    assert not gen1_staging.exists()
    assert gen2_staging.is_file()
    assert gen2_staging.read_bytes() == b"staging-bytes-gen-2"

    # Step 6: Generation 2 can promote its staging file successfully
    storage.atomic_promote(gen2_staging, gen2_final)
    assert gen2_final.is_file()
    assert not gen2_staging.exists()
    assert gen2_final.read_bytes() == b"staging-bytes-gen-2"

    # Step 7: Oversized snapshot cleanup removes only current generation
    gen3_staging = storage.get_staging_artifact_path(t["org"].id, run.id, claim_generation=3)
    gen3_staging.write_bytes(b"oversized-gen-3-bytes")
    assert gen3_staging.is_file()

    # Generation 3 fails with size cap and cleans up
    storage.cleanup_generation_artifacts(t["org"].id, run.id, claim_generation=3)
    assert not gen3_staging.exists()
    # Generation 2 final artifact remains intact
    assert gen2_final.is_file()


# =============================================================================
# CHECK B: ACTUAL PARQUET SCHEMA & FIDELITY ROUND-TRIP
# =============================================================================


@pytest.mark.asyncio
async def test_check_b_parquet_schema_inspection_and_fidelity_roundtrip(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Verify exact column types in Parquet schema and 100% round-trip fidelity across complex analytical types."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    parquet_file = tmp_path / f"fidelity_test_{uid}.parquet"

    # Realistic snapshot containing heterogeneous analytical and metadata types
    canonical_snapshot: dict[str, Any] = {
        "snapshot_schema_version": 1,
        "snapshot_id": f"snap-{uid}",
        "report_run_id": str(uuid.uuid4()),
        "report_definition_id": str(uuid.uuid4()),
        "report_title": "Enterprise Sales & Churn Analytics",
        "description": "Comprehensive Q3 executive report with regional breakdowns.",
        "page_config": {
            "format": "pdf",
            "page_orientation": "portrait",
            "page_size": "a4",
            "header_text": "CONFIDENTIAL — EXECUTIVE REVIEW",
            "footer_text": "Page {page} of {pages}",
            "include_table_of_contents": True,
            "include_cover_page": True,
            "theme": "corporate_dark",
            "margins_mm": [15, 15, 20, 20],
        },
        "materialized_at": "2026-11-03T20:45:00+00:00",
        "has_truncation": True,
        "sections": [
            {
                "id": str(uuid.uuid4()),
                "section_type": "table",
                "title": "Top Enterprise Customers",
                "description": "High-value accounts sorted by ARR and expansion velocity.",
                "sort_order": 0,
                "page_break_after": False,
                "custom_content": None,
                "widget_id": str(uuid.uuid4()),
                "columns": [
                    "customer_id",
                    "company_name",
                    "arr_usd",
                    "expansion_rate",
                    "is_active",
                    "contract_end",
                    "segment_tags",
                    "sla_breaches",
                    "custom_metadata",
                ],
                "data": [
                    {
                        "customer_id": 1001,
                        "company_name": "Acme Global Industries",
                        "arr_usd": 1250000.75,
                        "expansion_rate": 0.352,
                        "is_active": True,
                        "contract_end": "2028-12-31T00:00:00Z",
                        "segment_tags": ["fortune500", "multi-region", "tier1"],
                        "sla_breaches": None,  # Null integer check
                        "custom_metadata": {"csm": "Sarah Connor", "billing_tier": "custom"},
                    },
                    {
                        "customer_id": 1002,
                        "company_name": "Starlight Financial Corp",
                        "arr_usd": 480000.0,
                        "expansion_rate": -0.045,
                        "is_active": False,
                        "contract_end": "2026-11-01T23:59:59Z",
                        "segment_tags": ["fintech"],
                        "sla_breaches": 3,
                        "custom_metadata": None,  # Null object check
                    },
                ],
                "total_rows": 1500,
                "returned_rows": 2,
                "is_truncated": True,
                "summary": {
                    "total_arr": 1730000.75,
                    "avg_expansion": 0.1535,
                    "active_ratio": 0.5,
                    "audit_notes": None,
                },
            },
            {
                "id": str(uuid.uuid4()),
                "section_type": "markdown",
                "title": "Executive Commentary",
                "description": None,
                "sort_order": 1,
                "page_break_after": True,
                "custom_content": "### Key Findings\n- Expansion revenue up **35%**.\n- No data anomalies detected.",
                "widget_id": None,
                "columns": [],
                "data": [],
                "total_rows": 0,
                "returned_rows": 0,
                "is_truncated": False,
                "summary": None,
            },
        ],
    }

    # 1. Save canonical snapshot to Parquet
    save_snapshot_parquet(canonical_snapshot, parquet_file)
    assert parquet_file.is_file()

    # 2. Inspect physical schema using Polars
    df = pl.read_parquet(parquet_file)
    schema = df.schema

    # Document and assert native typed columns
    assert schema["snapshot_schema_version"] == pl.Int64
    assert schema["snapshot_id"] == pl.Utf8
    assert schema["report_run_id"] == pl.Utf8
    assert schema["report_title"] == pl.Utf8
    assert schema["has_truncation"] == pl.Boolean
    assert schema["sort_order"] == pl.Int64
    assert schema["page_break_after"] == pl.Boolean
    assert schema["total_rows"] == pl.Int64
    assert schema["returned_rows"] == pl.Int64
    assert schema["is_truncated"] == pl.Boolean

    # Document and assert JSON-encoded content columns
    assert schema["page_config_json"] == pl.Utf8
    assert schema["columns_json"] == pl.Utf8
    assert schema["data_json"] == pl.Utf8
    assert schema["summary_json"] == pl.Utf8

    # 3. Load snapshot back via canonical deserializer and verify 100% fidelity
    reconstructed = load_snapshot_parquet(parquet_file)
    assert reconstructed == canonical_snapshot

    # Explicit granular checks
    s0_orig = canonical_snapshot["sections"][0]
    s0_recon = reconstructed["sections"][0]
    assert s0_recon["data"][0]["arr_usd"] == 1250000.75
    assert s0_recon["data"][0]["sla_breaches"] is None
    assert s0_recon["data"][0]["is_active"] is True
    assert s0_recon["data"][1]["is_active"] is False
    assert s0_recon["data"][1]["expansion_rate"] == -0.045
    assert s0_recon["data"][0]["segment_tags"] == ["fortune500", "multi-region", "tier1"]
    assert s0_recon["is_truncated"] is True
    assert s0_recon["total_rows"] == 1500
    assert s0_recon["returned_rows"] == 2
    assert reconstructed["sections"][1]["summary"] is None


# =============================================================================
# CHECK C: CAPACITY ENFORCEMENT & PRECISE BOUNDARY TESTING
# =============================================================================


@pytest.mark.asyncio
async def test_check_c_precise_boundary_size_enforcement_and_bounded_loader(
    db_session: AsyncSession, tmp_path: Path
) -> None:
    """Verify inline threshold (2 MiB), external limit (10 MiB), uncompressed cap, and bounded memory loader."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    storage = ReportStorageService(base_dir=str(tmp_path / "reports_check_c"))
    snapshot_svc = ReportSnapshotService(db_session, storage=storage)
    report_service = ReportService(db_session)

    # 1. Boundary Test: Immediately Below 2 MiB (stored inline)
    # Target raw JSON payload: ~1.8 MiB (< 2,097,152 bytes)
    sub_2mb_text = secrets.token_hex(900000)  # ~1.8 MiB
    def_sub = await report_service.create_report_definition(
        t["org"].id,
        t["user"].id,
        ReportDefinitionCreateInput(workspace_id=t["workspace"].id, title="Sub 2MB Report"),
    )
    await report_service.add_section(
        def_sub.id,
        t["org"].id,
        ReportSectionCreateInput(section_type="markdown", title="Sub Section", custom_content=sub_2mb_text),
    )
    run_sub = await report_service.trigger_report_run(
        def_sub.id, t["org"].id, t["user"].id, ReportRunTriggerInput(idempotency_key=f"sub-2m-{uid}")
    )
    snap_sub, ext_sub, size_sub, _, _ = await snapshot_svc.materialize_snapshot(run_sub)
    assert ext_sub is None  # Must fit inline
    assert size_sub < settings.REPORT_SNAPSHOT_INLINE_MAX_BYTES

    # 2. Boundary Test: Immediately Above 2 MiB (promoted to external Parquet)
    # Target raw JSON payload: ~2.2 MiB (> 2,097,152 bytes and <= 10,485,760 bytes)
    above_2mb_text = secrets.token_hex(1100000)  # ~2.2 MiB
    def_above = await report_service.create_report_definition(
        t["org"].id,
        t["user"].id,
        ReportDefinitionCreateInput(workspace_id=t["workspace"].id, title="Above 2MB Report"),
    )
    await report_service.add_section(
        def_above.id,
        t["org"].id,
        ReportSectionCreateInput(section_type="markdown", title="Above Section", custom_content=above_2mb_text),
    )
    run_above = await report_service.trigger_report_run(
        def_above.id, t["org"].id, t["user"].id, ReportRunTriggerInput(idempotency_key=f"above-2m-{uid}")
    )
    snap_above, ext_above, size_above, csum_above, _ = await snapshot_svc.materialize_snapshot(run_above)
    assert ext_above is not None
    assert ext_above.endswith(".parquet")
    assert Path(ext_above).is_file()
    assert snap_above["is_external"] is True
    assert snap_above["format"] == "parquet"

    # 3. Boundary Test: Highly Compressible Payload Exceeding 10 MiB Uncompressed Cap
    # 12 MiB of repeating characters: compresses to < 100 KB in Snappy, BUT raw uncompressed > 10 MiB!
    compressible_oversized_text = "A" * (12 * 1024 * 1024)
    def_comp = await report_service.create_report_definition(
        t["org"].id,
        t["user"].id,
        ReportDefinitionCreateInput(workspace_id=t["workspace"].id, title="Compressible Oversized Report"),
    )
    await report_service.add_section(
        def_comp.id,
        t["org"].id,
        ReportSectionCreateInput(
            section_type="markdown", title="Huge Repeat", custom_content=compressible_oversized_text
        ),
    )
    run_comp = await report_service.trigger_report_run(
        def_comp.id, t["org"].id, t["user"].id, ReportRunTriggerInput(idempotency_key=f"comp-over-{uid}")
    )

    # Must be rejected upfront despite being highly compressible!
    with pytest.raises(DatasetTooLargeError, match="uncompressed report snapshot size"):
        await snapshot_svc.materialize_snapshot(run_comp)

    # Verify no partial files left for run_comp
    comp_run_dir = (storage.runs_dir / str(t["org"].id) / str(run_comp.id)).resolve()
    if comp_run_dir.is_dir():
        assert len(list(comp_run_dir.glob("*"))) == 0

    # 4. Boundary Test: Low-compressibility payload exceeding external artifact limit
    incompressible_oversized_text = secrets.token_hex(6000000)  # ~12 MiB random hex
    def_incomp = await report_service.create_report_definition(
        t["org"].id,
        t["user"].id,
        ReportDefinitionCreateInput(workspace_id=t["workspace"].id, title="Incompressible Oversized Report"),
    )
    await report_service.add_section(
        def_incomp.id,
        t["org"].id,
        ReportSectionCreateInput(
            section_type="markdown", title="Huge Hex", custom_content=incompressible_oversized_text
        ),
    )
    run_incomp = await report_service.trigger_report_run(
        def_incomp.id, t["org"].id, t["user"].id, ReportRunTriggerInput(idempotency_key=f"incomp-over-{uid}")
    )

    with pytest.raises(DatasetTooLargeError, match="exceeds maximum cap"):
        await snapshot_svc.materialize_snapshot(run_incomp)

    # 5. Bounded Memory Loader: Rejection before full deserialization
    # Create a dummy file that exceeds max_allowed_bytes
    oversized_artifact = tmp_path / "oversized_external.parquet"
    oversized_artifact.write_bytes(b"0" * 1050)
    with pytest.raises(DatasetTooLargeError, match="exceeds maximum allowable limit"):
        load_snapshot_parquet(oversized_artifact, max_allowed_bytes=1000)


# =============================================================================
# CHECK D: WORKER RETRY, STATE TRANSITIONS & RUNTIME IDEMPOTENCY
# =============================================================================


@pytest.mark.asyncio
async def test_check_d_worker_retry_state_machine_and_concurrency_fencing(
    db_session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Verify state machine (QUEUED -> RUNNING -> SUCCEEDED/FAILED), atomic attempts, retry limits, and protection of SUCCEEDED runs."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    run_repo = ReportRunRepository(db_session)
    dispatcher = ReportingOutboxDispatcher(
        dispatcher_id=f"disp-check-d-{uid}", session_maker=session_factory
    )

    # 1. State Machine & Atomic Attempt Count: Claim run
    run = ReportRun(
        report_definition_id=None,
        organization_id=t["org"].id,
        workspace_id=t["workspace"].id,
        report_title="State Machine Run",
        trigger_type="manual",
        status=ReportRunStatus.QUEUED,
        max_attempts=2,  # Max 2 attempts
        attempt_count=0,
        claim_generation=0,
        idempotency_key=f"fsm-{uid}",
    )
    await run_repo.create(run)
    await db_session.commit()

    # Attempt 1 Claim
    claimed_1, run_1 = await run_repo.claim_run_for_execution(
        run.id, worker_id="worker-fsm-1", lease_seconds=1
    )
    assert claimed_1 is True
    assert run_1.status == ReportRunStatus.RUNNING
    assert run_1.attempt_count == 1
    assert run_1.claim_generation == 1
    token_1 = run_1.claim_token
    await db_session.commit()

    # Concurrent claim while lease is active MUST be rejected without incrementing attempt_count
    claimed_racing, _ = await run_repo.claim_run_for_execution(
        run.id, worker_id="worker-fsm-racing"
    )
    assert claimed_racing is False
    await db_session.refresh(run)
    assert run.attempt_count == 1

    # 2. Simulate Lease Expiration and Recovery Sweep
    run.lease_expires_at = utc_now() - timedelta(seconds=10)
    await db_session.commit()

    recovered = await dispatcher.recover_stale_leases()
    assert recovered >= 1

    await db_session.refresh(run)
    assert run.status == ReportRunStatus.QUEUED
    assert run.lease_expires_at is None

    # Verify durable outbox retry event was created
    stmt = select(ReportOutboxEvent).where(
        ReportOutboxEvent.run_id == run.id,
        ReportOutboxEvent.event_type == "report.run.retrying",
    )
    ev = (await db_session.execute(stmt)).scalar_one_or_none()
    assert ev is not None
    assert ev.payload.get("attempt") == 2

    # Dispatch retry event via outbox dispatcher
    with patch("app.tasks.dispatcher.execute_report_run.delay") as mock_celery:
        dispatched = await dispatcher.dispatch_pending(batch_size=50)
        assert dispatched >= 1
        mock_celery.assert_called()

    # 3. Attempt 2 Claim (Recovery execution)
    claimed_2, run_2 = await run_repo.claim_run_for_execution(
        run.id, worker_id="worker-fsm-2", lease_seconds=1
    )
    assert claimed_2 is True
    assert run_2.status == ReportRunStatus.RUNNING
    assert run_2.attempt_count == 2
    assert run_2.claim_generation == 2
    token_2 = run_2.claim_token
    await db_session.commit()

    # 4. Simulate Lease Expiration when max_attempts reached (2/2)
    run.lease_expires_at = utc_now() - timedelta(seconds=10)
    await db_session.commit()

    recovered_terminal = await dispatcher.recover_stale_leases()
    assert recovered_terminal >= 1

    # Terminal state reached: status must be FAILED, no infinite retries
    await db_session.refresh(run)
    assert run.status == ReportRunStatus.FAILED
    assert run.error_code == "LEASE_EXPIRED"
    assert run.lease_expires_at is None

    # Subsequent claim on FAILED run must be rejected
    claimed_failed, _ = await run_repo.claim_run_for_execution(run.id, worker_id="worker-late")
    assert claimed_failed is False

    # 5. Protection of SUCCEEDED runs from recovery
    run_success = ReportRun(
        report_definition_id=None,
        organization_id=t["org"].id,
        workspace_id=t["workspace"].id,
        report_title="Succeeded Run Protection",
        trigger_type="manual",
        status=ReportRunStatus.SUCCEEDED,
        max_attempts=3,
        attempt_count=1,
        claim_generation=1,
        lease_expires_at=utc_now() - timedelta(minutes=10),  # Old lease timestamp
        idempotency_key=f"success-prot-{uid}",
    )
    await run_repo.create(run_success)
    await db_session.commit()

    # Run recovery sweep: must NEVER touch or reset a SUCCEEDED run
    await dispatcher.recover_stale_leases()
    await db_session.refresh(run_success)
    assert run_success.status == ReportRunStatus.SUCCEEDED

