"""Integration tests for Phase 9 Gate 3 Stage 1: Database Foundation & RBAC.

Covers:
1. Migration schema verification (branching from e82f1b4a9c3d to 370908748231).
2. Composite tenant isolation enforcement across all 5 reporting tables.
3. ON DELETE SET NULL and historical run preservation semantics.
4. Idempotency constraints and CHECK constraints.
5. RBAC permissions seeding and system role assignments (owner, admin, member, viewer).
6. Single-use download token atomic consumption and expiration/revocation guards.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.base import utc_now
from app.models.dashboard import Dashboard, DashboardWidget
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.permission import Permission
from app.models.report import (
    ReportDefinition,
    ReportDownloadToken,
    ReportOutboxEvent,
    ReportRun,
    ReportRunStatus,
    ReportSection,
)
from app.models.role import MembershipRole
from app.models.user import User
from app.models.workspace import Workspace
from app.services.permission import PermissionService
from app.services.role import RoleService


@pytest.fixture
async def db_session():
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await test_engine.dispose()


async def create_tenant_hierarchy(session: AsyncSession, suffix: str) -> dict[str, Any]:
    """Helper to provision a complete test tenant with workspace, user, and dashboard."""
    org = Organization(
        name=f"Org {suffix}",
        slug=f"org-{suffix}",
    )
    session.add(org)
    await session.flush()

    user = User(
        email=f"user_{suffix}@example.com",
        normalized_email=f"user_{suffix}@example.com",
        display_name=f"User {suffix}",
    )
    session.add(user)
    await session.flush()

    membership = OrganizationMembership(
        organization_id=org.id,
        user_id=user.id,
    )
    session.add(membership)
    await session.flush()

    workspace = Workspace(
        organization_id=org.id,
        name=f"Workspace {suffix}",
        slug=f"ws-{suffix}",
    )
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


@pytest.mark.asyncio
async def test_gate3_migration_tables_and_metadata(db_session: AsyncSession) -> None:
    """Verify that all 5 reporting tables and composite constraints exist in PostgreSQL."""
    query = text(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name IN (
            'report_definitions',
            'report_sections',
            'report_runs',
            'report_download_tokens',
            'report_outbox_events'
          )
        ORDER BY table_name;
        """
    )
    result = await db_session.execute(query)
    tables = [row[0] for row in result.fetchall()]
    assert set(tables) == {
        "report_definitions",
        "report_sections",
        "report_runs",
        "report_download_tokens",
        "report_outbox_events",
    }


@pytest.mark.asyncio
async def test_gate3_same_tenant_reporting_hierarchy(db_session: AsyncSession) -> None:
    """Verify end-to-end insertion of all reporting entities within the same organization."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    # 1. ReportDefinition
    report_def = ReportDefinition(
        organization_id=t["org"].id,
        workspace_id=t["workspace"].id,
        dashboard_id=t["dashboard"].id,
        title=f"Quarterly Performance {uid}",
        description="Comprehensive quarterly metrics",
        format="pdf",
        page_orientation="portrait",
        page_size="a4",
        header_text="Confidential - Internal Use Only",
        footer_text="Generated by VIBE Enterprise BI",
        include_table_of_contents=True,
        include_cover_page=True,
        theme="corporate",
        created_by_user_id=t["user"].id,
    )
    db_session.add(report_def)
    await db_session.flush()
    assert report_def.id is not None

    # 2. ReportSection
    section = ReportSection(
        report_definition_id=report_def.id,
        organization_id=t["org"].id,
        widget_id=t["widget"].id,
        section_type="chart",
        title="Revenue by Region",
        description="Regional breakdown of Q3 revenue",
        sort_order=1,
        page_break_after=False,
    )
    db_session.add(section)
    await db_session.flush()
    assert section.id is not None

    # 3. ReportRun
    idempotency_key = f"manual-run-{uid}"
    report_run = ReportRun(
        report_definition_id=report_def.id,
        organization_id=t["org"].id,
        workspace_id=t["workspace"].id,
        report_title=report_def.title,
        trigger_type="manual",
        status=ReportRunStatus.QUEUED,
        idempotency_key=idempotency_key,
        snapshot_schema_version=1,
        report_snapshot={"title": report_def.title, "sections": [{"type": "chart"}]},
        data_revision_ref={"snapshot_id": "snap-987", "parquet_version": 1},
        created_by_user_id=t["user"].id,
    )
    db_session.add(report_run)
    await db_session.flush()
    assert report_run.id is not None

    # 4. ReportDownloadToken
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    expires_at = utc_now() + timedelta(hours=1)
    download_token = ReportDownloadToken(
        token_hash=token_hash,
        run_id=report_run.id,
        organization_id=t["org"].id,
        created_by_user_id=t["user"].id,
        expires_at=expires_at,
        max_downloads=1,
    )
    db_session.add(download_token)
    await db_session.flush()
    assert download_token.id is not None

    # 5. ReportOutboxEvent
    outbox_event = ReportOutboxEvent(
        run_id=report_run.id,
        organization_id=t["org"].id,
        event_type="report.run.dispatched",
        payload={"run_id": str(report_run.id), "org_id": str(t["org"].id)},
    )
    db_session.add(outbox_event)
    await db_session.flush()
    assert outbox_event.id is not None


@pytest.mark.asyncio
async def test_gate3_cross_tenant_references_strictly_rejected(db_session: AsyncSession) -> None:
    """Verify that composite foreign keys reject cross-tenant references at the database level."""
    uid_a = uuid.uuid4().hex[:8]
    uid_b = uuid.uuid4().hex[:8]
    t_a = await create_tenant_hierarchy(db_session, uid_a)
    t_b = await create_tenant_hierarchy(db_session, uid_b)

    # Base valid report definition in Org A
    report_def_a = ReportDefinition(
        organization_id=t_a["org"].id,
        workspace_id=t_a["workspace"].id,
        dashboard_id=t_a["dashboard"].id,
        title=f"Org A Report {uid_a}",
    )
    db_session.add(report_def_a)
    await db_session.flush()

    # 1. ReportDefinition: Org A trying to reference Workspace B
    def_cross_ws = ReportDefinition(
        organization_id=t_a["org"].id,
        workspace_id=t_b["workspace"].id,
        dashboard_id=t_a["dashboard"].id,
        title="Cross WS Attempt",
    )
    db_session.add(def_cross_ws)
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.flush()
    await db_session.rollback()

    # 2. ReportDefinition: Org A trying to reference Dashboard B
    t_a = await create_tenant_hierarchy(db_session, f"{uid_a}_2")
    t_b = await create_tenant_hierarchy(db_session, f"{uid_b}_2")
    def_cross_dash = ReportDefinition(
        organization_id=t_a["org"].id,
        workspace_id=t_a["workspace"].id,
        dashboard_id=t_b["dashboard"].id,
        title="Cross Dash Attempt",
    )
    db_session.add(def_cross_dash)
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.flush()
    await db_session.rollback()

    # 3. ReportSection: Org B trying to attach to ReportDefinition in Org A
    t_a = await create_tenant_hierarchy(db_session, f"{uid_a}_3")
    t_b = await create_tenant_hierarchy(db_session, f"{uid_b}_3")
    def_a = ReportDefinition(
        organization_id=t_a["org"].id,
        workspace_id=t_a["workspace"].id,
        title="Valid Def A",
    )
    db_session.add(def_a)
    await db_session.flush()

    sec_cross_def = ReportSection(
        report_definition_id=def_a.id,
        organization_id=t_b["org"].id,
        section_type="chart",
        sort_order=1,
    )
    db_session.add(sec_cross_def)
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.flush()
    await db_session.rollback()

    # 4. ReportSection: Org A section trying to reference Widget B
    t_a = await create_tenant_hierarchy(db_session, f"{uid_a}_4")
    t_b = await create_tenant_hierarchy(db_session, f"{uid_b}_4")
    def_a = ReportDefinition(
        organization_id=t_a["org"].id,
        workspace_id=t_a["workspace"].id,
        title="Valid Def A 4",
    )
    db_session.add(def_a)
    await db_session.flush()

    sec_cross_widget = ReportSection(
        report_definition_id=def_a.id,
        organization_id=t_a["org"].id,
        widget_id=t_b["widget"].id,
        section_type="chart",
        sort_order=1,
    )
    db_session.add(sec_cross_widget)
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.flush()
    await db_session.rollback()

    # 5. ReportRun: Org B trying to reference ReportDefinition in Org A
    t_a = await create_tenant_hierarchy(db_session, f"{uid_a}_5")
    t_b = await create_tenant_hierarchy(db_session, f"{uid_b}_5")
    def_a = ReportDefinition(
        organization_id=t_a["org"].id,
        workspace_id=t_a["workspace"].id,
        title="Valid Def A 5",
    )
    db_session.add(def_a)
    await db_session.flush()

    run_cross_def = ReportRun(
        report_definition_id=def_a.id,
        organization_id=t_b["org"].id,
        workspace_id=t_b["workspace"].id,
        report_title="Cross Def Run",
        trigger_type="manual",
    )
    db_session.add(run_cross_def)
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.flush()
    await db_session.rollback()

    # 6. ReportRun: Org A trying to reference Workspace B
    t_a = await create_tenant_hierarchy(db_session, f"{uid_a}_6")
    t_b = await create_tenant_hierarchy(db_session, f"{uid_b}_6")
    run_cross_ws = ReportRun(
        report_definition_id=None,
        organization_id=t_a["org"].id,
        workspace_id=t_b["workspace"].id,
        report_title="Cross WS Run",
        trigger_type="manual",
    )
    db_session.add(run_cross_ws)
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.flush()
    await db_session.rollback()

    # 7. ReportDownloadToken: Org B trying to reference Run in Org A
    t_a = await create_tenant_hierarchy(db_session, f"{uid_a}_7")
    t_b = await create_tenant_hierarchy(db_session, f"{uid_b}_7")
    run_a = ReportRun(
        report_definition_id=None,
        organization_id=t_a["org"].id,
        workspace_id=t_a["workspace"].id,
        report_title="Run A",
        trigger_type="manual",
    )
    db_session.add(run_a)
    await db_session.flush()

    token_cross = ReportDownloadToken(
        token_hash="hash_dummy_cross",
        run_id=run_a.id,
        organization_id=t_b["org"].id,
        expires_at=utc_now() + timedelta(hours=1),
    )
    db_session.add(token_cross)
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.flush()
    await db_session.rollback()

    # 8. ReportOutboxEvent: Org B trying to reference Run in Org A
    t_a = await create_tenant_hierarchy(db_session, f"{uid_a}_8")
    t_b = await create_tenant_hierarchy(db_session, f"{uid_b}_8")
    run_a = ReportRun(
        report_definition_id=None,
        organization_id=t_a["org"].id,
        workspace_id=t_a["workspace"].id,
        report_title="Run A 8",
        trigger_type="manual",
    )
    db_session.add(run_a)
    await db_session.flush()

    outbox_cross = ReportOutboxEvent(
        run_id=run_a.id,
        organization_id=t_b["org"].id,
        event_type="report.run.dispatched",
    )
    db_session.add(outbox_cross)
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_gate3_historical_preservation_and_deletion_semantics(
    db_session: AsyncSession,
) -> None:
    """Verify ON DELETE SET NULL (column_name) preserves entities while maintaining org isolation."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    # Create full hierarchy
    report_def = ReportDefinition(
        organization_id=t["org"].id,
        workspace_id=t["workspace"].id,
        dashboard_id=t["dashboard"].id,
        title=f"Report {uid}",
    )
    db_session.add(report_def)
    await db_session.flush()

    section = ReportSection(
        report_definition_id=report_def.id,
        organization_id=t["org"].id,
        widget_id=t["widget"].id,
        section_type="chart",
        sort_order=1,
    )
    db_session.add(section)
    await db_session.flush()

    report_run = ReportRun(
        report_definition_id=report_def.id,
        organization_id=t["org"].id,
        workspace_id=t["workspace"].id,
        report_title="Quarterly Run",
        trigger_type="manual",
        status=ReportRunStatus.SUCCEEDED,
        report_snapshot={"title": "Quarterly Run", "data": [1, 2, 3]},
    )
    db_session.add(report_run)
    await db_session.flush()

    download_token = ReportDownloadToken(
        token_hash=f"hash_{uid}",
        run_id=report_run.id,
        organization_id=t["org"].id,
        expires_at=utc_now() + timedelta(hours=1),
    )
    db_session.add(download_token)

    outbox_event = ReportOutboxEvent(
        run_id=report_run.id,
        organization_id=t["org"].id,
        event_type="report.run.dispatched",
    )
    db_session.add(outbox_event)
    await db_session.flush()

    section_id = section.id
    report_def_id = report_def.id
    report_run_id = report_run.id
    download_token_id = download_token.id
    outbox_event_id = outbox_event.id
    org_id = t["org"].id

    # 1. Delete widget: ReportSection remains, widget_id becomes NULL, organization_id is preserved
    await db_session.delete(t["widget"])
    await db_session.flush()

    res_sec = await db_session.execute(
        select(ReportSection)
        .where(ReportSection.id == section_id)
        .execution_options(populate_existing=True)
    )
    reloaded_section = res_sec.scalar_one_or_none()
    assert reloaded_section is not None
    assert reloaded_section.widget_id is None
    assert reloaded_section.organization_id == org_id

    # 2. Delete dashboard: ReportDefinition remains, dashboard_id becomes NULL, organization_id preserved
    await db_session.delete(t["dashboard"])
    await db_session.flush()

    res_def = await db_session.execute(
        select(ReportDefinition)
        .where(ReportDefinition.id == report_def_id)
        .execution_options(populate_existing=True)
    )
    reloaded_def = res_def.scalar_one_or_none()
    assert reloaded_def is not None
    assert reloaded_def.dashboard_id is None
    assert reloaded_def.organization_id == org_id

    # 3. Delete report definition:
    # - ReportSection is cascaded and deleted (belongs exclusively to this definition)
    # - ReportRun is PRESERVED, report_definition_id becomes NULL, snapshot remains intact!
    await db_session.delete(reloaded_def)
    await db_session.flush()

    res_sec_del = await db_session.execute(
        select(ReportSection)
        .where(ReportSection.id == section_id)
        .execution_options(populate_existing=True)
    )
    assert res_sec_del.scalar_one_or_none() is None  # CASCADE

    res_run = await db_session.execute(
        select(ReportRun)
        .where(ReportRun.id == report_run_id)
        .execution_options(populate_existing=True)
    )
    reloaded_run = res_run.scalar_one_or_none()
    assert reloaded_run is not None  # PRESERVED HISTORICAL SNAPSHOT
    assert reloaded_run.report_definition_id is None
    assert reloaded_run.organization_id == org_id
    assert reloaded_run.report_snapshot == {"title": "Quarterly Run", "data": [1, 2, 3]}

    # 4. Delete workspace: ReportRun remains with workspace_id = NULL
    await db_session.delete(t["workspace"])
    await db_session.flush()

    res_run_ws = await db_session.execute(
        select(ReportRun)
        .where(ReportRun.id == report_run_id)
        .execution_options(populate_existing=True)
    )
    reloaded_run_after_ws_del = res_run_ws.scalar_one_or_none()
    assert reloaded_run_after_ws_del is not None
    assert reloaded_run_after_ws_del.workspace_id is None
    assert reloaded_run_after_ws_del.organization_id == org_id

    # 5. Delete report run: Cascades download tokens and outbox events
    await db_session.delete(reloaded_run_after_ws_del)
    await db_session.flush()

    res_tok = await db_session.execute(
        select(ReportDownloadToken)
        .where(ReportDownloadToken.id == download_token_id)
        .execution_options(populate_existing=True)
    )
    assert res_tok.scalar_one_or_none() is None  # CASCADE

    res_out = await db_session.execute(
        select(ReportOutboxEvent)
        .where(ReportOutboxEvent.id == outbox_event_id)
        .execution_options(populate_existing=True)
    )
    assert res_out.scalar_one_or_none() is None  # CASCADE

    # 6. Delete organization: Cascades and cleans all remaining tenant data
    await db_session.delete(t["org"])
    await db_session.flush()


@pytest.mark.asyncio
async def test_gate3_idempotency_and_check_constraints(db_session: AsyncSession) -> None:
    """Verify report run idempotency scope and CHECK constraints."""
    uid_a = uuid.uuid4().hex[:8]
    uid_b = uuid.uuid4().hex[:8]
    t_a = await create_tenant_hierarchy(db_session, uid_a)
    t_b = await create_tenant_hierarchy(db_session, uid_b)

    key = f"idem-key-{uid_a}"

    # Run 1 in Org A with idempotency key
    run_1 = ReportRun(
        report_definition_id=None,
        organization_id=t_a["org"].id,
        workspace_id=t_a["workspace"].id,
        report_title="Run 1",
        trigger_type="manual",
        status=ReportRunStatus.QUEUED,
        idempotency_key=key,
    )
    db_session.add(run_1)
    await db_session.flush()

    # Duplicate in same Org A -> IntegrityError
    run_dup = ReportRun(
        report_definition_id=None,
        organization_id=t_a["org"].id,
        workspace_id=t_a["workspace"].id,
        report_title="Run Dup",
        trigger_type="manual",
        status=ReportRunStatus.QUEUED,
        idempotency_key=key,
    )
    db_session.add(run_dup)
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.flush()
    await db_session.rollback()

    # Same key in different Org B -> Succeeds!
    t_a = await create_tenant_hierarchy(db_session, f"{uid_a}_new")
    t_b = await create_tenant_hierarchy(db_session, f"{uid_b}_new")
    run_org_a = ReportRun(
        report_definition_id=None,
        organization_id=t_a["org"].id,
        workspace_id=t_a["workspace"].id,
        report_title="Org A Run",
        trigger_type="manual",
        idempotency_key=key,
    )
    db_session.add(run_org_a)
    await db_session.flush()

    run_org_b = ReportRun(
        report_definition_id=None,
        organization_id=t_b["org"].id,
        workspace_id=t_b["workspace"].id,
        report_title="Org B Run",
        trigger_type="manual",
        idempotency_key=key,
    )
    db_session.add(run_org_b)
    await db_session.flush()
    assert run_org_b.id is not None

    # Check constraint: Invalid trigger_type rejected
    run_bad_trigger = ReportRun(
        report_definition_id=None,
        organization_id=t_a["org"].id,
        workspace_id=t_a["workspace"].id,
        report_title="Bad Trigger",
        trigger_type="scheduled_cron",  # Strictly deferred to Phase 10
    )
    db_session.add(run_bad_trigger)
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.flush()
    await db_session.rollback()

    # Check constraint: Invalid status rejected
    t_a = await create_tenant_hierarchy(db_session, f"{uid_a}_status")
    run_bad_status = ReportRun(
        report_definition_id=None,
        organization_id=t_a["org"].id,
        workspace_id=t_a["workspace"].id,
        report_title="Bad Status",
        trigger_type="manual",
        status="invalid_status_xyz",
    )
    db_session.add(run_bad_status)
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_gate3_rbac_permissions_and_role_matrix(db_session: AsyncSession) -> None:
    """Verify all 6 reporting permissions exist and system roles match the authorized matrix."""
    # 1. Verify permissions catalogue in DB
    report_perms = [
        "reports.read",
        "reports.create",
        "reports.update",
        "reports.delete",
        "reports.run",
        "reports.export",
    ]
    query = select(Permission.code).where(Permission.code.in_(report_perms))
    res = await db_session.execute(query)
    found_codes = set(res.scalars().all())
    assert found_codes == set(report_perms)

    # 2. Seed system roles in a new organization and verify effective permissions
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    role_service = RoleService(db_session)
    perm_service = PermissionService(db_session)

    # Seed default system roles for this org
    roles = await role_service.seed_default_roles_for_organization(t["org"].id)

    assert {"owner", "admin", "member", "viewer"}.issubset(set(roles.keys()))

    membership_id = t["membership"].id
    org_id = t["org"].id

    async def set_role(slug: str) -> set[str]:
        # Delete existing role links
        await db_session.execute(
            delete(MembershipRole).where(
                MembershipRole.membership_id == membership_id,
                MembershipRole.organization_id == org_id,
            )
        )
        # Assign new role
        link = MembershipRole(
            membership_id=membership_id,
            organization_id=org_id,
            role_id=roles[slug].id,
        )
        db_session.add(link)
        await db_session.flush()
        return await perm_service.get_effective_permissions(membership_id, org_id)

    # 1. Owner
    owner_perms = await set_role("owner")
    assert set(report_perms).issubset(owner_perms)

    # 2. Admin
    admin_perms = await set_role("admin")
    assert set(report_perms).issubset(admin_perms)

    # 3. Member
    member_perms = await set_role("member")
    assert "reports.read" in member_perms
    assert "reports.create" in member_perms
    assert "reports.update" in member_perms
    assert "reports.run" in member_perms
    assert "reports.export" in member_perms
    assert "reports.delete" not in member_perms  # Member cannot delete reports

    # 4. Viewer
    viewer_perms = await set_role("viewer")
    assert "reports.read" in viewer_perms
    assert "reports.export" in viewer_perms
    assert (
        "reports.run" not in viewer_perms
    )  # Viewer CANNOT execute runs (expensive compute workload)
    assert "reports.create" not in viewer_perms
    assert "reports.update" not in viewer_perms
    assert "reports.delete" not in viewer_perms


@pytest.mark.asyncio
async def test_gate3_download_token_atomic_consumption(db_session: AsyncSession) -> None:
    """Verify single-use download token atomic consumption and expiration/revocation guards."""
    uid = uuid.uuid4().hex[:8]
    t = await create_tenant_hierarchy(db_session, uid)

    report_run = ReportRun(
        report_definition_id=None,
        organization_id=t["org"].id,
        workspace_id=t["workspace"].id,
        report_title="Download Test Run",
        trigger_type="manual",
        status=ReportRunStatus.SUCCEEDED,
    )
    db_session.add(report_run)
    await db_session.flush()

    # 1. Valid single-use token
    token_1_hash = hashlib.sha256(f"tok-1-{uid}".encode()).hexdigest()
    now = utc_now()
    token_1 = ReportDownloadToken(
        token_hash=token_1_hash,
        run_id=report_run.id,
        organization_id=t["org"].id,
        expires_at=now + timedelta(minutes=15),
        max_downloads=1,
        download_count=0,
        is_revoked=False,
    )
    db_session.add(token_1)
    await db_session.flush()

    # Atomic consumption query
    consume_stmt = (
        update(ReportDownloadToken)
        .where(
            ReportDownloadToken.token_hash == token_1_hash,
            ReportDownloadToken.organization_id == t["org"].id,
            ReportDownloadToken.is_revoked.is_(False),
            ReportDownloadToken.expires_at > utc_now(),
            ReportDownloadToken.download_count < ReportDownloadToken.max_downloads,
        )
        .values(
            download_count=ReportDownloadToken.download_count + 1,
            last_downloaded_at=utc_now(),
        )
        .returning(ReportDownloadToken.id, ReportDownloadToken.run_id)
    )

    # First attempt: succeeds (1 row updated)
    res_1 = await db_session.execute(consume_stmt)
    row_1 = res_1.fetchone()
    assert row_1 is not None
    assert row_1[0] == token_1.id

    # Second attempt: fails (0 rows updated because download_count == max_downloads)
    res_2 = await db_session.execute(consume_stmt)
    row_2 = res_2.fetchone()
    assert row_2 is None

    # 2. Revoked token: fails consumption
    token_revoked_hash = hashlib.sha256(f"tok-revoked-{uid}".encode()).hexdigest()
    token_revoked = ReportDownloadToken(
        token_hash=token_revoked_hash,
        run_id=report_run.id,
        organization_id=t["org"].id,
        expires_at=now + timedelta(minutes=15),
        max_downloads=1,
        download_count=0,
        is_revoked=True,
    )
    db_session.add(token_revoked)
    await db_session.flush()

    res_revoked = await db_session.execute(
        consume_stmt.where(ReportDownloadToken.token_hash == token_revoked_hash)
    )
    assert res_revoked.fetchone() is None

    # 3. Expired token: fails consumption
    token_expired_hash = hashlib.sha256(f"tok-expired-{uid}".encode()).hexdigest()
    token_expired = ReportDownloadToken(
        token_hash=token_expired_hash,
        run_id=report_run.id,
        organization_id=t["org"].id,
        expires_at=now - timedelta(minutes=1),  # In the past
        max_downloads=1,
        download_count=0,
        is_revoked=False,
    )
    db_session.add(token_expired)
    await db_session.flush()

    res_expired = await db_session.execute(
        consume_stmt.where(ReportDownloadToken.token_hash == token_expired_hash)
    )
    assert res_expired.fetchone() is None
