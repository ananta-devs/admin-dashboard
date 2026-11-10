from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    get_current_membership,
    get_current_session,
    require_permission,
    verify_csrf_token,
)
from app.db.session import get_db_session
from app.formula import parse_cell_ref
from app.models.membership import OrganizationMembership
from app.models.session import AuthenticationSession
from app.schemas.spreadsheet import (
    BatchCellUpdateInput,
    BatchCellUpdateResponse,
    CellData,
    CellHistoryItemRead,
    SheetCreateInput,
    SheetRangeRead,
    SheetRead,
    SheetRowsPage,
    SheetUpdateInput,
    WorkbookCreateInput,
    WorkbookRead,
    WorkbookUpdateInput,
)
from app.services.spreadsheet import SpreadsheetService

router = APIRouter(prefix="/spreadsheets", tags=["Spreadsheets"])


# -----------------------------------------------------------------------------
# Workbooks
# -----------------------------------------------------------------------------


@router.get(
    "/workbooks",
    response_model=list[WorkbookRead],
    dependencies=[Depends(require_permission("spreadsheets.read"))],
)
async def list_workbooks(
    workspace_id: uuid.UUID | None = Query(default=None),
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> list[WorkbookRead]:
    """List workbooks in the active organization, optionally filtered by workspace."""
    service = SpreadsheetService(db)
    return await service.list_workbooks(membership.organization_id, workspace_id=workspace_id)


@router.post(
    "/workbooks",
    response_model=WorkbookRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(verify_csrf_token),
        Depends(require_permission("spreadsheets.create")),
    ],
)
async def create_workbook(
    data: WorkbookCreateInput,
    membership: OrganizationMembership = Depends(get_current_membership),
    auth_session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> WorkbookRead:
    """Create a new workbook with default Sheet1 and 26 standard columns."""
    service = SpreadsheetService(db)
    return await service.create_workbook(
        data=data,
        organization_id=membership.organization_id,
        created_by_user_id=auth_session.user_id,
    )


@router.get(
    "/workbooks/{workbook_id}",
    response_model=WorkbookRead,
    dependencies=[Depends(require_permission("spreadsheets.read"))],
)
async def get_workbook(
    workbook_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> WorkbookRead:
    """Retrieve single workbook details and sheet count."""
    service = SpreadsheetService(db)
    return await service.get_workbook(workbook_id, membership.organization_id)


@router.patch(
    "/workbooks/{workbook_id}",
    response_model=WorkbookRead,
    dependencies=[
        Depends(verify_csrf_token),
        Depends(require_permission("spreadsheets.update")),
    ],
)
async def update_workbook(
    workbook_id: uuid.UUID,
    data: WorkbookUpdateInput,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> WorkbookRead:
    """Update workbook name and description."""
    service = SpreadsheetService(db)
    return await service.update_workbook(workbook_id, membership.organization_id, data)


@router.delete(
    "/workbooks/{workbook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[
        Depends(verify_csrf_token),
        Depends(require_permission("spreadsheets.delete")),
    ],
)
async def delete_workbook(
    workbook_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete a workbook and cascade delete all its sheets and cell records."""
    service = SpreadsheetService(db)
    await service.delete_workbook(workbook_id, membership.organization_id)


# -----------------------------------------------------------------------------
# Sheets
# -----------------------------------------------------------------------------


@router.get(
    "/workbooks/{workbook_id}/sheets",
    response_model=list[SheetRead],
    dependencies=[Depends(require_permission("spreadsheets.read"))],
)
async def list_sheets(
    workbook_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> list[SheetRead]:
    """List all sheets within a workbook."""
    service = SpreadsheetService(db)
    return await service.list_sheets(workbook_id, membership.organization_id)


@router.post(
    "/workbooks/{workbook_id}/sheets",
    response_model=SheetRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(verify_csrf_token),
        Depends(require_permission("spreadsheets.create")),
    ],
)
async def create_sheet(
    workbook_id: uuid.UUID,
    data: SheetCreateInput,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> SheetRead:
    """Add a new sheet to an existing workbook."""
    service = SpreadsheetService(db)
    return await service.create_sheet(workbook_id, membership.organization_id, data)


@router.get(
    "/sheets/{sheet_id}",
    response_model=SheetRead,
    dependencies=[Depends(require_permission("spreadsheets.read"))],
)
async def get_sheet(
    sheet_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> SheetRead:
    """Retrieve single sheet metadata."""
    service = SpreadsheetService(db)
    return await service.get_sheet(sheet_id, membership.organization_id)


@router.patch(
    "/sheets/{sheet_id}",
    response_model=SheetRead,
    dependencies=[
        Depends(verify_csrf_token),
        Depends(require_permission("spreadsheets.update")),
    ],
)
async def update_sheet(
    sheet_id: uuid.UUID,
    data: SheetUpdateInput,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> SheetRead:
    """Update sheet name, row/column dimensions, or display position."""
    service = SpreadsheetService(db)
    return await service.update_sheet(sheet_id, membership.organization_id, data)


# -----------------------------------------------------------------------------
# Grid Range & Virtualized Rows
# -----------------------------------------------------------------------------


@router.get(
    "/sheets/{sheet_id}/range",
    response_model=SheetRangeRead,
    dependencies=[Depends(require_permission("spreadsheets.read"))],
)
async def get_sheet_range(
    sheet_id: uuid.UUID,
    start_row: int = Query(default=0, ge=0),
    end_row: int = Query(default=99, ge=0),
    start_col: int = Query(default=0, ge=0),
    end_col: int = Query(default=25, ge=0),
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> SheetRangeRead:
    """Retrieve a bounded rectangular slice of cells for virtualized rendering."""
    service = SpreadsheetService(db)
    return await service.get_range(
        sheet_id=sheet_id,
        organization_id=membership.organization_id,
        start_row=start_row,
        end_row=end_row,
        start_col=start_col,
        end_col=end_col,
    )


@router.get(
    "/sheets/{sheet_id}/rows",
    response_model=SheetRowsPage,
    dependencies=[Depends(require_permission("spreadsheets.read"))],
)
async def get_sheet_rows(
    sheet_id: uuid.UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> SheetRowsPage:
    """Paginated retrieval of populated row-oriented JSONB records."""
    service = SpreadsheetService(db)
    return await service.get_rows_page(
        sheet_id=sheet_id,
        organization_id=membership.organization_id,
        offset=offset,
        limit=limit,
    )


@router.patch(
    "/sheets/{sheet_id}/cells",
    response_model=BatchCellUpdateResponse,
    dependencies=[
        Depends(verify_csrf_token),
        Depends(require_permission("spreadsheets.update")),
    ],
)
async def update_sheet_cells(
    sheet_id: uuid.UUID,
    data: BatchCellUpdateInput,
    membership: OrganizationMembership = Depends(get_current_membership),
    auth_session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> BatchCellUpdateResponse:
    """Atomically update cells, evaluate formulas, recalculate dependents, and verify row versions."""
    service = SpreadsheetService(db)
    return await service.update_cells_batch(
        sheet_id=sheet_id,
        organization_id=membership.organization_id,
        user_id=auth_session.user_id,
        data=data,
    )


@router.get(
    "/sheets/{sheet_id}/cells/{cell_reference}",
    response_model=CellData,
    dependencies=[Depends(require_permission("spreadsheets.read"))],
)
async def get_cell(
    sheet_id: uuid.UUID,
    cell_reference: str,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> CellData:
    """Fetch data for an individual cell reference (e.g. 'A1')."""
    coord = parse_cell_ref(cell_reference)
    service = SpreadsheetService(db)
    slice_data = await service.get_range(
        sheet_id=sheet_id,
        organization_id=membership.organization_id,
        start_row=coord.row,
        end_row=coord.row,
        start_col=coord.col,
        end_col=coord.col,
    )
    clean_ref = cell_reference.upper()
    return slice_data.cells.get(clean_ref, CellData(raw=None, computed=None, type="blank"))


# -----------------------------------------------------------------------------
# History & Undo / Redo
# -----------------------------------------------------------------------------


@router.get(
    "/sheets/{sheet_id}/history",
    response_model=list[CellHistoryItemRead],
    dependencies=[Depends(require_permission("spreadsheets.read"))],
)
async def list_sheet_history(
    sheet_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> list[CellHistoryItemRead]:
    """Audit log of cell mutations and revisions on this sheet."""
    service = SpreadsheetService(db)
    return await service.list_history(
        sheet_id, membership.organization_id, limit=limit, offset=offset
    )


@router.post(
    "/sheets/{sheet_id}/undo",
    response_model=BatchCellUpdateResponse,
    dependencies=[
        Depends(verify_csrf_token),
        Depends(require_permission("spreadsheets.update")),
    ],
)
async def undo_sheet_edit(
    sheet_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    auth_session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> BatchCellUpdateResponse:
    """Revert the most recent cell mutation on this sheet."""
    service = SpreadsheetService(db)
    return await service.undo(sheet_id, membership.organization_id, auth_session.user_id)


@router.post(
    "/sheets/{sheet_id}/redo",
    response_model=BatchCellUpdateResponse,
    dependencies=[
        Depends(verify_csrf_token),
        Depends(require_permission("spreadsheets.update")),
    ],
)
async def redo_sheet_edit(
    sheet_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    auth_session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> BatchCellUpdateResponse:
    """Reapply the most recently undone cell mutation on this sheet."""
    service = SpreadsheetService(db)
    return await service.redo(sheet_id, membership.organization_id, auth_session.user_id)
