"""Integration tests verifying atomic optimistic concurrency and row-level locking for dashboards.

These tests prove:
1. PostgreSQL SELECT ... FOR UPDATE row-level write locking prevents concurrent lost updates.
2. Contention is confirmed via PostgreSQL kernel statistics (pg_stat_activity / pg_blocking_pids),
   proving Session B is genuinely blocked by Session A's row lock before Session A commits.
3. Strictly increasing timestamps: new_updated_at > previous_updated_at is strictly enforced
   at microsecond precision even under an identical or frozen clock.
4. Mutation paths including layout updates and sharing strictly advance updated_at.
5. Two genuinely independent database sessions overlapping in an active transaction result
   in exactly one winner (commit) and one loser (ConflictError / HTTP 409).
6. The loser's modifications never overwrite the winner's modifications.
7. Database sessions remain clean and fully usable after a conflict.
8. Organization isolation, stale sequential updates, matching updates, and None timestamp
   compatibility all behave deterministically.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.services.dashboard as dashboard_module
from app.core.exceptions import ConflictError, EntityNotFoundError
from app.models.dashboard import Dashboard, DashboardWidget
from app.models.organization import Organization
from app.models.spreadsheet import Sheet, Workbook
from app.models.user import User
from app.models.workspace import Workspace
from app.repositories.dashboard import DashboardRepository
from app.schemas.dashboard import (
    DashboardCreateInput,
    DashboardUpdateInput,
    WidgetLayoutUpdateInput,
    WidgetPositionItem,
)
from app.services.dashboard import DashboardService


async def create_test_hierarchy(
    session: AsyncSession,
) -> tuple[Organization, Workspace, User, Dashboard]:
    """Helper to seed an isolated organization, workspace, user, and initial dashboard."""
    suffix = uuid.uuid4().hex[:6]
    org = Organization(
        id=uuid.uuid4(),
        name=f"Concurrency Org {suffix}",
        slug=f"conc-org-{suffix}",
    )
    session.add(org)
    await session.flush()

    ws = Workspace(
        id=uuid.uuid4(),
        organization_id=org.id,
        name=f"Workspace {suffix}",
        slug=f"ws-{suffix}",
    )
    session.add(ws)
    await session.flush()

    user = User(
        id=uuid.uuid4(),
        email=f"user_{suffix}@example.com",
        normalized_email=f"user_{suffix}@example.com",
        display_name=f"User {suffix}",
    )
    session.add(user)
    await session.flush()

    svc = DashboardService(session)
    dashboard = await svc.create_dashboard(
        DashboardCreateInput(
            workspace_id=ws.id,
            title="Initial Baseline Dashboard",
            description="Initial Description",
            layout_config={"theme": "dark"},
        ),
        organization_id=org.id,
        created_by_user_id=user.id,
    )
    return org, ws, user, dashboard


@pytest.mark.asyncio
async def test_concurrent_dashboard_updates_row_lock_race(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """DEF-07: Verify deterministic atomic concurrency with PostgreSQL lock contention proof.

    Verification sequence:
    1. Session 1 seeds a dashboard and commits with initial updated_at = T0.
    2. Session A and Session B obtain backend PIDs from PostgreSQL.
    3. Session A begins transaction and acquires row lock via SELECT ... FOR UPDATE.
    4. Session B begins transaction and attempts SELECT ... FOR UPDATE with expected_updated_at = T0.
    5. An independent Inspector session inspects pg_stat_activity and confirms Session B is
       actively blocked by Session A (pg_blocking_pids(pid_b) contains pid_a, wait_event_type = 'Lock').
    6. Only AFTER PostgreSQL contention is confirmed does Session A commit ('Winner: Updated by Client A').
    7. Session B unblocks, reads committed row with T1 > T0, and raises ConflictError (HTTP 409).
    8. Exactly one update succeeds. The winner's changes are retained. Session B is usable afterwards.
    """
    # 1. Setup initial dashboard using an independent session
    async with session_factory() as setup_session:
        org, ws, user, dash = await create_test_hierarchy(setup_session)
        dash_id = dash.id
        org_id = org.id
        initial_updated_at = dash.updated_at

    # Coordination synchronization primitives
    lock_acquired_by_a = asyncio.Event()
    contention_confirmed_by_inspector = asyncio.Event()
    allow_a_to_commit = asyncio.Event()

    # Track PIDs and evidence
    pids: dict[str, int] = {}
    contention_evidence: dict[str, object] = {}
    result_a: dict[str, Dashboard | Exception] = {}
    result_b: dict[str, Dashboard | Exception] = {}

    # Custom repository wrapper on Session A to hold the row lock deterministically
    class CoordinatedDashboardRepo(DashboardRepository):
        async def get_by_id_for_update(
            self, dashboard_id: uuid.UUID, organization_id: uuid.UUID
        ) -> Dashboard | None:
            locked_dashboard = await super().get_by_id_for_update(dashboard_id, organization_id)
            # The row lock is now actively held in PostgreSQL by Session A
            lock_acquired_by_a.set()
            # Wait until inspector confirms that Session B is blocked waiting on this lock
            await allow_a_to_commit.wait()
            return locked_dashboard

    # Worker A: Independent DB connection / session
    async def worker_a() -> None:
        async with session_factory() as session_a:
            pids["pid_a"] = (await session_a.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            svc_a = DashboardService(session_a)
            svc_a.dashboard_repo = CoordinatedDashboardRepo(session_a)
            try:
                res = await svc_a.update_dashboard(
                    dash_id,
                    org_id,
                    DashboardUpdateInput(
                        title="Winner: Updated by Client A",
                        expected_updated_at=initial_updated_at,
                    ),
                )
                result_a["outcome"] = res
            except Exception as exc:
                result_a["outcome"] = exc

    # Worker B: Independent DB connection / session
    async def worker_b() -> None:
        # Wait until Worker A has successfully acquired the row lock inside PostgreSQL
        await lock_acquired_by_a.wait()

        async with session_factory() as session_b:
            pids["pid_b"] = (await session_b.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            svc_b = DashboardService(session_b)
            try:
                res = await svc_b.update_dashboard(
                    dash_id,
                    org_id,
                    DashboardUpdateInput(
                        title="Loser: Overwrite Attempt by Client B",
                        expected_updated_at=initial_updated_at,
                    ),
                )
                result_b["outcome"] = res
            except Exception as exc:
                result_b["outcome"] = exc

            # Verify session_b remains fully functional and usable after conflict
            refetched = await svc_b.get_dashboard(dash_id, org_id)
            result_b["session_usable_check"] = refetched

    # Inspector Task: Query PostgreSQL pg_stat_activity to prove lock contention occurred
    async def inspector_worker() -> None:
        await lock_acquired_by_a.wait()
        async with session_factory() as inspector_session:
            # Poll pg_stat_activity for up to 5 seconds to observe Session B waiting on Session A
            for _ in range(100):
                await asyncio.sleep(0.02)
                pid_a = pids.get("pid_a")
                pid_b = pids.get("pid_b")
                if not pid_a or not pid_b:
                    continue

                stmt = text(
                    "SELECT pid, wait_event_type, wait_event, pg_blocking_pids(pid) "
                    "FROM pg_stat_activity WHERE pid = :target_pid"
                )
                row = (await inspector_session.execute(stmt, {"target_pid": pid_b})).fetchone()
                if row and row[3] and pid_a in row[3]:
                    contention_evidence["detected"] = True
                    contention_evidence["pid_a"] = pid_a
                    contention_evidence["pid_b"] = pid_b
                    contention_evidence["wait_event_type"] = row[1]
                    contention_evidence["wait_event"] = row[2]
                    contention_evidence["blocking_pids"] = row[3]
                    contention_confirmed_by_inspector.set()
                    break

    # Launch workers and inspector concurrently
    task_a = asyncio.create_task(worker_a())
    task_b = asyncio.create_task(worker_b())
    task_inspect = asyncio.create_task(inspector_worker())

    # Wait for inspector to confirm that Session B is actively blocked by Session A in PostgreSQL
    await contention_confirmed_by_inspector.wait()
    assert contention_evidence.get("detected") is True, "PostgreSQL contention was not observed!"
    assert contention_evidence["wait_event_type"] == "Lock"
    assert pids["pid_a"] in contention_evidence["blocking_pids"]

    # Now allow Worker A to finish its update and commit
    allow_a_to_commit.set()

    # Wait for all tasks to complete
    await asyncio.gather(task_a, task_b, task_inspect)

    # Assertions
    # 1. Worker A succeeded
    assert isinstance(result_a["outcome"], Dashboard), f"Worker A failed: {result_a['outcome']}"
    assert result_a["outcome"].title == "Winner: Updated by Client A"
    assert result_a["outcome"].updated_at > initial_updated_at

    # 2. Worker B was rejected with ConflictError (HTTP 409)
    assert isinstance(result_b["outcome"], ConflictError), (
        f"Worker B did not raise ConflictError! Outcome: {result_b['outcome']}"
    )
    assert "Dashboard has been modified by another user" in str(result_b["outcome"])

    # 3. Session B was usable after the conflict
    assert "session_usable_check" in result_b
    assert isinstance(result_b["session_usable_check"], Dashboard)
    assert result_b["session_usable_check"].title == "Winner: Updated by Client A"

    # 4. Independent verification via a third fresh session
    async with session_factory() as verify_session:
        verify_svc = DashboardService(verify_session)
        final_dash = await verify_svc.get_dashboard(dash_id, org_id)
        assert final_dash.title == "Winner: Updated by Client A"
        assert final_dash.updated_at == result_a["outcome"].updated_at


@pytest.mark.asyncio
async def test_strictly_increasing_timestamps_under_frozen_clock(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEF-07: Verify strictly increasing timestamps and 409 rejection even under an identical/frozen clock."""
    async with session_factory() as session:
        org, ws, user, dash = await create_test_hierarchy(session)
        dash_id = dash.id
        org_id = org.id
        t0 = dash.updated_at

        svc = DashboardService(session)

        # Freeze utc_now to return exactly t0 for all subsequent calls
        monkeypatch.setattr(dashboard_module, "utc_now", lambda: t0)

        # First update with expected_updated_at = t0
        up1 = await svc.update_dashboard(
            dash_id,
            org_id,
            DashboardUpdateInput(title="Update 1 with Frozen Clock", expected_updated_at=t0),
        )
        t1 = up1.updated_at
        assert up1.title == "Update 1 with Frozen Clock"
        # Must be strictly greater by at least 1 microsecond despite frozen clock
        assert t1 > t0
        assert t1 >= t0 + timedelta(microseconds=1)

        # Second update attempting to use stale t0 must fail with 409 Conflict
        with pytest.raises(ConflictError) as exc_info:
            await svc.update_dashboard(
                dash_id,
                org_id,
                DashboardUpdateInput(title="Stale Update 2", expected_updated_at=t0),
            )
        assert "Dashboard has been modified by another user" in str(exc_info.value)

        # Third update using matching t1 succeeds and strictly advances again
        up3 = await svc.update_dashboard(
            dash_id,
            org_id,
            DashboardUpdateInput(title="Update 3 with Frozen Clock", expected_updated_at=t1),
        )
        t3 = up3.updated_at
        assert up3.title == "Update 3 with Frozen Clock"
        assert t3 > t1
        assert t3 >= t1 + timedelta(microseconds=1)


@pytest.mark.asyncio
async def test_share_update_strictly_advances_timestamp(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """DEF-07: Verify that share_dashboard strictly advances dashboard.updated_at."""
    async with session_factory() as session:
        org, ws, user, dash = await create_test_hierarchy(session)
        t0 = dash.updated_at

        svc = DashboardService(session)
        shared_dash = await svc.share_dashboard(dash.id, org.id, is_shared=False)
        assert shared_dash.is_shared is False
        assert shared_dash.updated_at > t0
        assert shared_dash.updated_at >= t0 + timedelta(microseconds=1)


@pytest.mark.asyncio
async def test_layout_update_strictly_advances_timestamp(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """DEF-07: Verify that batch_update_layout strictly advances dashboard.updated_at."""
    async with session_factory() as session:
        org, ws, user, dash = await create_test_hierarchy(session)
        t0 = dash.updated_at

        # Add a sheet and widget to dash
        wb = Workbook(id=uuid.uuid4(), organization_id=org.id, workspace_id=ws.id, name="WB")
        session.add(wb)
        await session.flush()
        sheet = Sheet(id=uuid.uuid4(), workbook_id=wb.id, organization_id=org.id, name="S")
        session.add(sheet)
        await session.flush()

        widget = DashboardWidget(
            id=uuid.uuid4(),
            dashboard_id=dash.id,
            organization_id=org.id,
            title="Widget A",
            widget_type="bar",
            source_type="sheet",
            source_id=sheet.id,
            position_x=0,
            position_y=0,
            width=6,
            height=4,
            sort_order=0,
        )
        session.add(widget)
        await session.commit()

        svc = DashboardService(session)
        layout_input = WidgetLayoutUpdateInput(
            positions=[
                WidgetPositionItem(
                    widget_id=widget.id,
                    position_x=2,
                    position_y=1,
                    width=8,
                    height=5,
                    sort_order=1,
                )
            ]
        )
        updated_widgets = await svc.batch_update_layout(dash.id, org.id, layout_input)
        assert len(updated_widgets) == 1
        assert updated_widgets[0].width == 8

        # Verify dashboard updated_at strictly advanced
        refetched = await svc.get_dashboard(dash.id, org.id)
        assert refetched.updated_at > t0
        assert refetched.updated_at >= t0 + timedelta(microseconds=1)


@pytest.mark.asyncio
async def test_sequential_stale_update_returns_409(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """DEF-07: Verify that sequential updates with a stale timestamp return 409 Conflict."""
    async with session_factory() as session:
        org, ws, user, dash = await create_test_hierarchy(session)
        dash_id = dash.id
        org_id = org.id
        t0 = dash.updated_at

        svc = DashboardService(session)

        # First update succeeds with matching t0
        up1 = await svc.update_dashboard(
            dash_id,
            org_id,
            DashboardUpdateInput(title="First Update", expected_updated_at=t0),
        )
        assert up1.title == "First Update"
        assert up1.updated_at > t0

        # Second update uses old stale t0 -> ConflictError
        with pytest.raises(ConflictError) as exc_info:
            await svc.update_dashboard(
                dash_id,
                org_id,
                DashboardUpdateInput(title="Stale Update", expected_updated_at=t0),
            )
        assert "Dashboard has been modified by another user" in str(exc_info.value)


@pytest.mark.asyncio
async def test_matching_timestamp_update_succeeds(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """DEF-07: Verify that an update with an exact matching timestamp succeeds."""
    async with session_factory() as session:
        org, ws, user, dash = await create_test_hierarchy(session)
        dash_id = dash.id
        org_id = org.id
        t0 = dash.updated_at

        svc = DashboardService(session)

        res = await svc.update_dashboard(
            dash_id,
            org_id,
            DashboardUpdateInput(title="Updated Title", expected_updated_at=t0),
        )
        assert res.title == "Updated Title"
        assert res.updated_at > t0


@pytest.mark.asyncio
async def test_missing_expected_updated_at_unconditional_update(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """DEF-07: Verify backward compatibility: missing expected_updated_at performs unconditional update."""
    async with session_factory() as session:
        org, ws, user, dash = await create_test_hierarchy(session)
        dash_id = dash.id
        org_id = org.id
        t0 = dash.updated_at

        svc = DashboardService(session)

        res = await svc.update_dashboard(
            dash_id,
            org_id,
            DashboardUpdateInput(title="Unconditional Edit", expected_updated_at=None),
        )
        assert res.title == "Unconditional Edit"
        assert res.updated_at > t0


@pytest.mark.asyncio
async def test_cross_organization_update_rejected(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """DEF-07: Verify that cross-organization dashboard update attempts are strictly rejected."""
    async with session_factory() as session:
        org1, ws1, user1, dash1 = await create_test_hierarchy(session)
        org2, ws2, user2, dash2 = await create_test_hierarchy(session)
        dash1_id = dash1.id
        org1_id = org1.id
        org2_id = org2.id

        svc = DashboardService(session)

        # Attempt to update dash1 using org2 ID
        with pytest.raises(EntityNotFoundError):
            await svc.update_dashboard(
                dash1_id,
                org2_id,
                DashboardUpdateInput(title="Cross-Org Snooping"),
            )

        # Confirm dash1 remains untouched
        fresh_dash1 = await svc.get_dashboard(dash1_id, org1_id)
        assert fresh_dash1.title == "Initial Baseline Dashboard"


@pytest.mark.asyncio
async def test_exception_rolls_back_changes(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """DEF-07: Verify that transaction rollback preserves existing dashboard state on error."""
    async with session_factory() as session:
        org, ws, user, dash = await create_test_hierarchy(session)
        dash_id = dash.id
        org_id = org.id
        t0 = dash.updated_at
        initial_title = dash.title

        svc = DashboardService(session)

        # Inject an error by monkey-patching dashboard_repo.update to raise RuntimeError
        original_update = svc.dashboard_repo.update

        async def failing_update(d: Dashboard) -> Dashboard:
            raise RuntimeError("Simulated Database I/O Failure")

        svc.dashboard_repo.update = failing_update  # type: ignore[assignment]

        with pytest.raises(RuntimeError):
            await svc.update_dashboard(
                dash_id,
                org_id,
                DashboardUpdateInput(title="Should Roll Back", expected_updated_at=t0),
            )

        svc.dashboard_repo.update = original_update

        # Verify dashboard remains untouched
        fresh = await svc.get_dashboard(dash_id, org_id)
        assert fresh.title == initial_title
        assert fresh.updated_at == t0
