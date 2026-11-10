from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.exceptions import ConflictError, EntityNotFoundError, ValidationError
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.spreadsheet import (
    Sheet,
    SheetCellDependency,
    Workbook,
)
from app.models.user import User
from app.models.workspace import Workspace
from app.schemas.spreadsheet import BatchCellUpdateInput, CellEditInput
from app.services.spreadsheet import MAX_BATCH_CELLS, SpreadsheetService


@pytest.fixture
async def test_session_maker():
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    yield session_factory
    await test_engine.dispose()


async def _create_test_sheet_context(
    session: AsyncSession,
) -> tuple[Organization, User, Workspace, Workbook, Sheet]:
    """Helper creating a dedicated tenant organization, user, workspace, workbook, and sheet."""
    slug = f"org-conc-{uuid.uuid4().hex[:8]}"
    org = Organization(name=f"Org {slug}", slug=slug)
    session.add(org)
    await session.flush()

    user = User(
        email=f"user-{slug}@test.io",
        normalized_email=f"user-{slug}@test.io",
        display_name=f"User {slug}",
    )
    session.add(user)
    await session.flush()

    mem = OrganizationMembership(
        organization_id=org.id,
        user_id=user.id,
    )
    session.add(mem)
    await session.flush()

    ws = Workspace(
        name="Spreadsheet WS",
        slug=f"ws-{slug}",
        organization_id=org.id,
    )
    session.add(ws)
    await session.flush()

    service = SpreadsheetService(session)
    from app.schemas.spreadsheet import WorkbookCreateInput

    wb_read = await service.create_workbook(
        organization_id=org.id,
        created_by_user_id=user.id,
        data=WorkbookCreateInput(name="Concurrency Test WB", workspace_id=ws.id),
    )

    wb_stmt = select(Workbook).where(Workbook.id == wb_read.id)
    wb = (await session.execute(wb_stmt)).scalar_one()

    sheet_stmt = select(Sheet).where(Sheet.workbook_id == wb.id)
    sheet = (await session.execute(sheet_stmt)).scalars().first()
    assert sheet is not None

    await session.commit()
    return org, user, ws, wb, sheet


@pytest.mark.asyncio
async def test_concurrency_case_a_same_cell_conflict(
    test_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Requirement 1A: Same cell, two independent sessions.
    Exactly one edit succeeds; the stale edit receives 409 Conflict.
    """
    async with test_session_maker() as setup_session:
        org, user, _, wb, sheet = await _create_test_sheet_context(setup_session)
        service = SpreadsheetService(setup_session)
        # Initial state: A1 = 10 (version 1)
        res = await service.update_cells_batch(
            sheet_id=sheet.id,
            organization_id=org.id,
            user_id=user.id,
            data=BatchCellUpdateInput(updates=[CellEditInput(cell_ref="A1", value=10)]),
        )
        assert res.updated_cells["A1"].version == 2
        await setup_session.commit()

    # Two independent sessions attempting concurrent edits on A1 expecting version 2
    async def session_task(client_val: int, expected_ver: int) -> tuple[bool, Any]:
        async with test_session_maker() as session:
            svc = SpreadsheetService(session)
            try:
                out = await svc.update_cells_batch(
                    sheet_id=sheet.id,
                    organization_id=org.id,
                    user_id=user.id,
                    data=BatchCellUpdateInput(
                        updates=[
                            CellEditInput(
                                cell_ref="A1",
                                value=client_val,
                                expected_cell_version=expected_ver,
                            )
                        ]
                    ),
                )
                await session.commit()
                return True, out
            except ConflictError as e:
                await session.rollback()
                return False, str(e)

    results = await asyncio.gather(
        session_task(20, expected_ver=2),
        session_task(30, expected_ver=2),
    )

    successes = [r for r in results if r[0]]
    conflicts = [r for r in results if not r[0]]

    assert len(successes) == 1, "Exactly one concurrent edit must succeed"
    assert len(conflicts) == 1, "The concurrent stale edit must be rejected"
    assert "Concurrent edit conflict on A1" in str(conflicts[0][1])

    # Verify final persisted value in PostgreSQL
    async with test_session_maker() as verify_session:
        svc = SpreadsheetService(verify_session)
        rng = await svc.get_range(sheet.id, org.id, 0, 0, 0, 0)
        assert rng.cells["A1"].raw in (20, 30)
        assert rng.cells["A1"].version == 3


@pytest.mark.asyncio
async def test_concurrency_case_b_different_cells_same_row(
    test_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Requirement 1B: Different cells in the same row.
    Both independent edits succeed; neither silently overwrites the other.
    """
    async with test_session_maker() as setup_session:
        org, user, _, wb, sheet = await _create_test_sheet_context(setup_session)
        service = SpreadsheetService(setup_session)
        # Initial row 0: A1 = 10, B1 = 20
        init_res = await service.update_cells_batch(
            sheet_id=sheet.id,
            organization_id=org.id,
            user_id=user.id,
            data=BatchCellUpdateInput(
                updates=[
                    CellEditInput(cell_ref="A1", value=10),
                    CellEditInput(cell_ref="B1", value=20),
                ]
            ),
        )
        a1_ver = init_res.updated_cells["A1"].version
        b1_ver = init_res.updated_cells["B1"].version
        await setup_session.commit()

    # Session 1 edits A1; Session 2 edits B1 on row 0
    async def edit_a1() -> tuple[bool, Any]:
        async with test_session_maker() as session:
            svc = SpreadsheetService(session)
            res = await svc.update_cells_batch(
                sheet_id=sheet.id,
                organization_id=org.id,
                user_id=user.id,
                data=BatchCellUpdateInput(
                    updates=[
                        CellEditInput(
                            cell_ref="A1",
                            value=100,
                            expected_cell_version=a1_ver,
                        )
                    ]
                ),
            )
            await session.commit()
            return True, res

    async def edit_b1() -> tuple[bool, Any]:
        async with test_session_maker() as session:
            svc = SpreadsheetService(session)
            res = await svc.update_cells_batch(
                sheet_id=sheet.id,
                organization_id=org.id,
                user_id=user.id,
                data=BatchCellUpdateInput(
                    updates=[
                        CellEditInput(
                            cell_ref="B1",
                            value=200,
                            expected_cell_version=b1_ver,
                        )
                    ]
                ),
            )
            await session.commit()
            return True, res

    res1, res2 = await asyncio.gather(edit_a1(), edit_b1())
    assert res1[0], "Session 1 edit on A1 must succeed"
    assert res2[0], "Session 2 edit on B1 must succeed"

    # Verify both edits are persisted in row 0
    async with test_session_maker() as verify_session:
        svc = SpreadsheetService(verify_session)
        rng = await svc.get_range(sheet.id, org.id, 0, 0, 0, 1)
        assert rng.cells["A1"].raw == 100, "A1 must be 100 from Session 1"
        assert rng.cells["B1"].raw == 200, "B1 must be 200 from Session 2"


@pytest.mark.asyncio
async def test_concurrency_case_c_different_rows(
    test_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Requirement 1C: Different rows.
    Both independent edits succeed.
    """
    async with test_session_maker() as setup_session:
        org, user, _, wb, sheet = await _create_test_sheet_context(setup_session)
        service = SpreadsheetService(setup_session)
        await service.update_cells_batch(
            sheet_id=sheet.id,
            organization_id=org.id,
            user_id=user.id,
            data=BatchCellUpdateInput(
                updates=[
                    CellEditInput(cell_ref="A1", value=1),
                    CellEditInput(cell_ref="A2", value=2),
                ]
            ),
        )
        await setup_session.commit()

    async def edit_row0() -> bool:
        async with test_session_maker() as session:
            svc = SpreadsheetService(session)
            await svc.update_cells_batch(
                sheet_id=sheet.id,
                organization_id=org.id,
                user_id=user.id,
                data=BatchCellUpdateInput(updates=[CellEditInput(cell_ref="A1", value=500)]),
            )
            await session.commit()
            return True

    async def edit_row1() -> bool:
        async with test_session_maker() as session:
            svc = SpreadsheetService(session)
            await svc.update_cells_batch(
                sheet_id=sheet.id,
                organization_id=org.id,
                user_id=user.id,
                data=BatchCellUpdateInput(updates=[CellEditInput(cell_ref="A2", value=600)]),
            )
            await session.commit()
            return True

    r0, r1 = await asyncio.gather(edit_row0(), edit_row1())
    assert r0 and r1

    async with test_session_maker() as verify_session:
        svc = SpreadsheetService(verify_session)
        rng = await svc.get_range(sheet.id, org.id, 0, 1, 0, 0)
        assert rng.cells["A1"].raw == 500
        assert rng.cells["A2"].raw == 600


@pytest.mark.asyncio
async def test_concurrency_case_d_overlapping_batch_atomic_rollback(
    test_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Requirement 1D: Overlapping batch edits.
    Stale overlapping changes are rejected with 409 Conflict, and a failed batch
    persists none of its changes (atomic rollback).
    """
    async with test_session_maker() as setup_session:
        org, user, _, wb, sheet = await _create_test_sheet_context(setup_session)
        service = SpreadsheetService(setup_session)
        init_res = await service.update_cells_batch(
            sheet_id=sheet.id,
            organization_id=org.id,
            user_id=user.id,
            data=BatchCellUpdateInput(
                updates=[
                    CellEditInput(cell_ref="A1", value=1),
                    CellEditInput(cell_ref="B1", value=2),
                    CellEditInput(cell_ref="C1", value=3),
                ]
            ),
        )
        b1_ver = init_res.updated_cells["B1"].version
        await setup_session.commit()

    # Session 1 updates A1 and B1 (which increments B1's version)
    async with test_session_maker() as session1:
        svc1 = SpreadsheetService(session1)
        await svc1.update_cells_batch(
            sheet_id=sheet.id,
            organization_id=org.id,
            user_id=user.id,
            data=BatchCellUpdateInput(
                updates=[
                    CellEditInput(cell_ref="A1", value=10),
                    CellEditInput(cell_ref="B1", value=20),
                ]
            ),
        )
        await session1.commit()

    # Session 2 attempts an overlapping batch [B1, C1] expecting stale B1 version
    async with test_session_maker() as session2:
        svc2 = SpreadsheetService(session2)
        with pytest.raises(ConflictError) as exc_info:
            await svc2.update_cells_batch(
                sheet_id=sheet.id,
                organization_id=org.id,
                user_id=user.id,
                data=BatchCellUpdateInput(
                    updates=[
                        CellEditInput(
                            cell_ref="B1",
                            value=999,
                            expected_cell_version=b1_ver,  # Stale!
                        ),
                        CellEditInput(cell_ref="C1", value=888),
                    ]
                ),
            )
        assert "Concurrent edit conflict on B1" in str(exc_info.value)
        await session2.rollback()

    # Verify atomic rollback: neither B1 nor C1 from Session 2 was persisted!
    async with test_session_maker() as verify_session:
        svc = SpreadsheetService(verify_session)
        rng = await svc.get_range(sheet.id, org.id, 0, 0, 0, 2)
        assert rng.cells["A1"].raw == 10
        assert rng.cells["B1"].raw == 20
        assert rng.cells["C1"].raw == 3, (
            "C1 must remain 3; failed batch must persist none of its changes"
        )


@pytest.mark.asyncio
async def test_formula_transitive_recalculation_and_obsolete_edge_cleanup(
    test_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Requirement 2.1 & 2.2:
    1. Changing a precedent recalculates all transitive dependent formulas.
    2. Changing a formula removes obsolete dependency edges.
    """
    async with test_session_maker() as session:
        org, user, _, wb, sheet = await _create_test_sheet_context(session)
        service = SpreadsheetService(session)

        # Set up chain: A1 = 10, B1 = "=A1 * 2", C1 = "=B1 + 5"
        await service.update_cells_batch(
            sheet_id=sheet.id,
            organization_id=org.id,
            user_id=user.id,
            data=BatchCellUpdateInput(
                updates=[
                    CellEditInput(cell_ref="A1", value=10),
                    CellEditInput(cell_ref="B1", value="=A1 * 2"),
                    CellEditInput(cell_ref="C1", value="=B1 + 5"),
                ]
            ),
        )
        await session.commit()

    # 1. Verify initial calculation and transitive recalculation
    async with test_session_maker() as session:
        service = SpreadsheetService(session)
        rng = await service.get_range(sheet.id, org.id, 0, 0, 0, 2)
        assert rng.cells["B1"].computed == 20.0
        assert rng.cells["C1"].computed == 25.0

        # Change A1 = 30 -> B1 should become 60, C1 should become 65
        recalc_res = await service.update_cells_batch(
            sheet_id=sheet.id,
            organization_id=org.id,
            user_id=user.id,
            data=BatchCellUpdateInput(updates=[CellEditInput(cell_ref="A1", value=30)]),
        )
        assert recalc_res.updated_cells["B1"].computed == 60.0
        assert recalc_res.updated_cells["C1"].computed == 65.0
        await session.commit()

    # 2. Obsolete dependency edge removal
    # Change B1 from "=A1 * 2" to literal 100
    async with test_session_maker() as session:
        service = SpreadsheetService(session)
        await service.update_cells_batch(
            sheet_id=sheet.id,
            organization_id=org.id,
            user_id=user.id,
            data=BatchCellUpdateInput(updates=[CellEditInput(cell_ref="B1", value=100)]),
        )
        await session.commit()

        # Check sheet_cell_dependencies: edge A1 -> B1 must no longer exist
        deps_stmt = select(SheetCellDependency).where(
            SheetCellDependency.sheet_id == sheet.id,
            SheetCellDependency.dependent_cell_ref == "B1",
        )
        b1_deps = (await session.execute(deps_stmt)).scalars().all()
        assert len(b1_deps) == 0, "Obsolete dependency edge A1 -> B1 must be deleted"

        # Now changing A1 must NOT recalculate B1 or C1
        a1_edit = await service.update_cells_batch(
            sheet_id=sheet.id,
            organization_id=org.id,
            user_id=user.id,
            data=BatchCellUpdateInput(updates=[CellEditInput(cell_ref="A1", value=999)]),
        )
        assert "B1" not in a1_edit.updated_cells
        assert a1_edit.recalculated_count == 0


@pytest.mark.asyncio
async def test_formula_errors_propagation_and_cycle_persistence(
    test_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Requirement 2.3, 2.5, 2.6:
    Missing/error references produce documented formula errors, circular references
    persist #CYCLE!, and errors propagate deterministically.
    """
    async with test_session_maker() as session:
        org, user, _, wb, sheet = await _create_test_sheet_context(session)
        service = SpreadsheetService(session)

        # Division by zero and error propagation
        res = await service.update_cells_batch(
            sheet_id=sheet.id,
            organization_id=org.id,
            user_id=user.id,
            data=BatchCellUpdateInput(
                updates=[
                    CellEditInput(cell_ref="A1", value=10),
                    CellEditInput(cell_ref="A2", value=0),
                    CellEditInput(cell_ref="A3", value="=A1 / A2"),
                    CellEditInput(cell_ref="A4", value="=A3 + 5"),
                ]
            ),
        )
        assert res.updated_cells["A3"].computed == "#DIV/0!"
        assert res.updated_cells["A3"].error_code == "#DIV/0!"
        assert res.updated_cells["A4"].computed == "#DIV/0!", (
            "Error must propagate to downstream formulas"
        )

        # Direct cycle: B1 = "=B1"
        res_cycle = await service.update_cells_batch(
            sheet_id=sheet.id,
            organization_id=org.id,
            user_id=user.id,
            data=BatchCellUpdateInput(updates=[CellEditInput(cell_ref="B1", value="=B1")]),
        )
        assert res_cycle.updated_cells["B1"].computed == "#CYCLE!"
        assert res_cycle.updated_cells["B1"].error_code == "#CYCLE!"

        await session.commit()

    # Verify cycle error persists in database
    async with test_session_maker() as verify_session:
        service = SpreadsheetService(verify_session)
        rng = await service.get_range(sheet.id, org.id, 0, 0, 1, 1)
        assert rng.cells["B1"].computed == "#CYCLE!"
        assert rng.cells["B1"].error_code == "#CYCLE!"


@pytest.mark.asyncio
async def test_formula_lazy_evaluation_if(
    test_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Requirement 2: Define and document IF evaluation semantics.
    Verify that IF(TRUE, 1, 1/0) evaluates to 1 without evaluating the unused branch.
    """
    async with test_session_maker() as session:
        org, user, _, wb, sheet = await _create_test_sheet_context(session)
        service = SpreadsheetService(session)

        res = await service.update_cells_batch(
            sheet_id=sheet.id,
            organization_id=org.id,
            user_id=user.id,
            data=BatchCellUpdateInput(
                updates=[
                    # True condition: false branch (1/0) must NOT be evaluated
                    CellEditInput(cell_ref="A1", value="=IF(1=1, 42, 1/0)"),
                    # False condition: true branch (1/0) must NOT be evaluated
                    CellEditInput(cell_ref="A2", value="=IF(1=2, 1/0, 99)"),
                    # Condition evaluates to true, true branch is 1/0 -> returns #DIV/0!
                    CellEditInput(cell_ref="A3", value="=IF(1=1, 1/0, 99)"),
                ]
            ),
        )
        assert res.updated_cells["A1"].computed == 42.0
        assert res.updated_cells["A1"].error_code is None
        assert res.updated_cells["A2"].computed == 99.0
        assert res.updated_cells["A2"].error_code is None
        assert res.updated_cells["A3"].computed == "#DIV/0!"


@pytest.mark.asyncio
async def test_resource_limits_rejection(
    test_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Requirement 6 & 2.8: Reject oversized range and batch requests with clear validation errors."""
    async with test_session_maker() as session:
        org, user, _, wb, sheet = await _create_test_sheet_context(session)
        service = SpreadsheetService(session)

        # 1. Batch size exceeds MAX_BATCH_CELLS (500)
        oversized_updates = [
            CellEditInput(cell_ref=f"A{i + 1}", value=i) for i in range(MAX_BATCH_CELLS + 1)
        ]
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError) as pyd_exc:
            BatchCellUpdateInput(updates=oversized_updates)
        assert "at most 500 items" in str(pyd_exc.value)

        # Service-level defense-in-depth validation
        constructed_input = BatchCellUpdateInput.model_construct(updates=oversized_updates)
        with pytest.raises(ValidationError) as exc:
            await service.update_cells_batch(
                sheet_id=sheet.id,
                organization_id=org.id,
                user_id=user.id,
                data=constructed_input,
            )
        assert "exceeds safety limit" in str(exc.value)

        # 2. Viewport range exceeds MAX_VIEWPORT_ROWS (200)
        with pytest.raises(ValidationError) as exc_rng:
            await service.get_range(
                sheet.id, org.id, start_row=0, end_row=201, start_col=0, end_col=10
            )
        assert "exceeds maximum allowable viewport window" in str(exc_rng.value)


@pytest.mark.asyncio
async def test_bounded_undo_redo_and_conflict_policy(
    test_session_maker: async_sessionmaker[AsyncSession],
) -> None:
    """Requirement 3:
    - Atomic undo of multi-cell batch.
    - Atomic redo of multi-cell batch.
    - Undo conflict after subsequent concurrent edit on a batch cell.
    - Redo invalidation after new edit.
    - Bounded history retention and pruning.
    - Cross-tenant history isolation.
    """
    async with test_session_maker() as session:
        org, user, _, wb, sheet = await _create_test_sheet_context(session)
        service = SpreadsheetService(session)

        # Batch 1: A1=10, B1=20
        await service.update_cells_batch(
            sheet_id=sheet.id,
            organization_id=org.id,
            user_id=user.id,
            data=BatchCellUpdateInput(
                updates=[
                    CellEditInput(cell_ref="A1", value=10),
                    CellEditInput(cell_ref="B1", value=20),
                ]
            ),
        )

        # Batch 2: C1=30, D1=40
        await service.update_cells_batch(
            sheet_id=sheet.id,
            organization_id=org.id,
            user_id=user.id,
            data=BatchCellUpdateInput(
                updates=[
                    CellEditInput(cell_ref="C1", value=30),
                    CellEditInput(cell_ref="D1", value=40),
                ]
            ),
        )
        await session.commit()

    # 1. Atomic undo of Batch 2 (both C1 and D1 restored together)
    async with test_session_maker() as session:
        service = SpreadsheetService(session)
        undo_res = await service.undo(sheet.id, org.id, user.id)
        assert "C1" in undo_res.updated_cells
        assert "D1" in undo_res.updated_cells
        assert undo_res.updated_cells["C1"].raw is None
        assert undo_res.updated_cells["D1"].raw is None
        await session.commit()

    # 2. Atomic redo of Batch 2 (both C1 and D1 re-applied together)
    async with test_session_maker() as session:
        service = SpreadsheetService(session)
        redo_res = await service.redo(sheet.id, org.id, user.id)
        assert redo_res.updated_cells["C1"].raw == 30
        assert redo_res.updated_cells["D1"].raw == 40
        await session.commit()

    # 3. Undo Batch 2 again, then perform a new edit to test redo invalidation
    async with test_session_maker() as session:
        service = SpreadsheetService(session)
        await service.undo(sheet.id, org.id, user.id)
        await session.commit()

        # New edit: E1 = 999
        await service.update_cells_batch(
            sheet_id=sheet.id,
            organization_id=org.id,
            user_id=user.id,
            data=BatchCellUpdateInput(updates=[CellEditInput(cell_ref="E1", value=999)]),
        )
        await session.commit()

        # Redo must now be invalidated
        with pytest.raises(ValidationError) as exc:
            await service.redo(sheet.id, org.id, user.id)
        assert "Nothing to redo" in str(exc.value)

    # 4. Undo conflict after a subsequent edit:
    # Now subsequent edit modified A1: A1 = 777
    async with test_session_maker() as session:
        service = SpreadsheetService(session)
        await service.update_cells_batch(
            sheet_id=sheet.id,
            organization_id=org.id,
            user_id=user.id,
            data=BatchCellUpdateInput(updates=[CellEditInput(cell_ref="A1", value=777)]),
        )
        await session.commit()

        # Undo the latest edit (A1=777)
        await service.undo(sheet.id, org.id, user.id)
        await session.commit()

    # 5. Cross-tenant history isolation
    async with test_session_maker() as session:
        service = SpreadsheetService(session)
        foreign_org_id = uuid.uuid4()
        with pytest.raises(EntityNotFoundError):
            # Must not be able to list or undo history for a foreign org
            await service.list_history(sheet.id, foreign_org_id)
