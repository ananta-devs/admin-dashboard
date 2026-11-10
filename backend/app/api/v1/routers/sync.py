"""FastAPI router for external spreadsheet import, export, and synchronization."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    get_current_membership,
    require_permission,
    verify_csrf_token,
)
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership
from app.schemas.sync import (
    GoogleExportInput,
    GoogleExportResponse,
    GoogleImportInput,
    GoogleImportResponse,
    SyncConflictRead,
    SyncConflictResolveInput,
    SyncMappingCreateInput,
    SyncMappingRead,
    SyncRunRead,
)
from app.services.sync import (
    GoogleSpreadsheetExportService,
    GoogleSpreadsheetImportService,
    SpreadsheetSyncService,
)
from app.tasks.sync import execute_spreadsheet_sync

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post(
    "/import",
    response_model=GoogleImportResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Import external Google Sheet into a VIBE workbook",
    dependencies=[
        Depends(require_permission("integrations.manage")),
        Depends(verify_csrf_token),
    ],
)
async def import_google_sheet(
    payload: GoogleImportInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> GoogleImportResponse:
    service = GoogleSpreadsheetImportService(session)
    return await service.import_sheet(payload, organization_id=membership.organization_id)


@router.post(
    "/export",
    response_model=GoogleExportResponse,
    status_code=status.HTTP_200_OK,
    summary="Export a VIBE sheet to Google Sheets",
    dependencies=[
        Depends(require_permission("integrations.manage")),
        Depends(verify_csrf_token),
    ],
)
async def export_vibe_sheet(
    payload: GoogleExportInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> GoogleExportResponse:
    service = GoogleSpreadsheetExportService(session)
    return await service.export_sheet(payload, organization_id=membership.organization_id)


@router.post(
    "/mappings",
    response_model=SyncMappingRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a spreadsheet synchronization mapping",
    dependencies=[
        Depends(require_permission("integrations.manage")),
        Depends(verify_csrf_token),
    ],
)
async def create_sync_mapping(
    payload: SyncMappingCreateInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> SyncMappingRead:
    service = SpreadsheetSyncService(session)
    mapping = await service.create_mapping(payload, organization_id=membership.organization_id)
    return SyncMappingRead.model_validate(mapping)


@router.get(
    "/mappings",
    response_model=list[SyncMappingRead],
    summary="List spreadsheet synchronization mappings",
    dependencies=[Depends(require_permission("integrations.read"))],
)
async def list_sync_mappings(
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> list[SyncMappingRead]:
    service = SpreadsheetSyncService(session)
    mappings = await service.list_mappings(membership.organization_id)
    return [SyncMappingRead.model_validate(m) for m in mappings]


@router.get(
    "/mappings/{mapping_id}",
    response_model=SyncMappingRead,
    summary="Get spreadsheet synchronization mapping details",
    dependencies=[Depends(require_permission("integrations.read"))],
)
async def get_sync_mapping(
    mapping_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> SyncMappingRead:
    service = SpreadsheetSyncService(session)
    mapping = await service.get_mapping(mapping_id, membership.organization_id)
    return SyncMappingRead.model_validate(mapping)


@router.delete(
    "/mappings/{mapping_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a synchronization mapping",
    dependencies=[
        Depends(require_permission("integrations.manage")),
        Depends(verify_csrf_token),
    ],
)
async def delete_sync_mapping(
    mapping_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> None:
    service = SpreadsheetSyncService(session)
    await service.delete_mapping(mapping_id, membership.organization_id)


@router.post(
    "/mappings/{mapping_id}/trigger",
    response_model=dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Trigger synchronization execution",
    dependencies=[
        Depends(require_permission("integrations.sync")),
        Depends(verify_csrf_token),
    ],
)
async def trigger_sync(
    mapping_id: uuid.UUID,
    run_async: bool = Query(default=False),
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> dict[str, Any]:
    if run_async:
        task = execute_spreadsheet_sync.delay(
            str(mapping_id),
            str(membership.organization_id),
            str(membership.user_id),
            "manual",
        )
        return {"status": "queued", "task_id": task.id}
    else:
        service = SpreadsheetSyncService(session)
        run = await service.execute_sync(
            mapping_id=mapping_id,
            organization_id=membership.organization_id,
            triggered_by_user_id=membership.user_id,
            trigger_type="manual",
        )
        return {
            "status": run.status,
            "run_id": str(run.id),
            "rows_transferred": run.rows_transferred,
            "cells_updated": run.cells_updated,
            "conflict_count": run.conflict_count,
        }


@router.get(
    "/mappings/{mapping_id}/runs",
    response_model=list[SyncRunRead],
    summary="List runs for a synchronization mapping",
    dependencies=[Depends(require_permission("integrations.read"))],
)
async def list_sync_runs(
    mapping_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> list[SyncRunRead]:
    service = SpreadsheetSyncService(session)
    runs = await service.list_runs(mapping_id, membership.organization_id)
    return [SyncRunRead.model_validate(r) for r in runs]


@router.get(
    "/mappings/{mapping_id}/conflicts",
    response_model=list[SyncConflictRead],
    summary="List conflicts for a synchronization mapping",
    dependencies=[Depends(require_permission("integrations.read"))],
)
async def list_sync_conflicts(
    mapping_id: uuid.UUID,
    status_filter: str | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> list[SyncConflictRead]:
    service = SpreadsheetSyncService(session)
    conflicts = await service.list_conflicts(
        mapping_id, membership.organization_id, status=status_filter
    )
    return [SyncConflictRead.model_validate(c) for c in conflicts]


@router.post(
    "/conflicts/{conflict_id}/resolve",
    response_model=SyncConflictRead,
    summary="Resolve a detected cell conflict",
    dependencies=[
        Depends(require_permission("integrations.manage")),
        Depends(verify_csrf_token),
    ],
)
async def resolve_sync_conflict(
    conflict_id: uuid.UUID,
    payload: SyncConflictResolveInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> SyncConflictRead:
    service = SpreadsheetSyncService(session)
    conflict = await service.resolve_conflict(
        conflict_id=conflict_id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        resolution_choice=payload.resolution_choice,
    )
    return SyncConflictRead.model_validate(conflict)
