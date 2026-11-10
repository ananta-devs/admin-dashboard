"""Analytics REST API router."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.exceptions import OperationNotFoundError
from app.analytics.registry import registry
from app.analytics.storage import AnalyticsStorageService
from app.api.dependencies.auth import (
    get_current_membership,
    get_current_session,
    require_permission,
    verify_csrf_token,
)
from app.core.config import settings
from app.core.exceptions import EntityNotFoundError
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership
from app.models.session import AuthenticationSession
from app.schemas.analytics import (
    AnalyticsJobCreateInput,
    AnalyticsJobListResponse,
    AnalyticsJobRead,
    AnalyticsResultRead,
    OperationCatalogueItem,
    PublishToSheetInput,
    PublishToSheetResponse,
)
from app.services.analytics import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["Analytics"])


# -----------------------------------------------------------------------------
# Operations Catalogue
# -----------------------------------------------------------------------------


@router.get(
    "/operations",
    response_model=list[OperationCatalogueItem],
    dependencies=[Depends(require_permission("analytics.read"))],
)
async def list_operations() -> list[OperationCatalogueItem]:
    """List all registered, allowlisted analytical operations."""
    items: list[OperationCatalogueItem] = []
    for op in registry.list_operations():
        items.append(
            OperationCatalogueItem(
                id=op.id,
                name=op.name,
                description=op.description,
                required_permission=op.required_permission,
                max_input_rows=op.max_input_rows,
                max_input_cols=op.max_input_cols,
                timeout_seconds=op.timeout_seconds,
                parameters_schema=op.input_schema.model_json_schema(),
            )
        )
    return items


@router.get(
    "/operations/{operation_id}",
    response_model=OperationCatalogueItem,
    dependencies=[Depends(require_permission("analytics.read"))],
)
async def get_operation(operation_id: str) -> OperationCatalogueItem:
    """Get metadata specification and parameters schema for a specific operation."""
    try:
        op = registry.get(operation_id)
    except OperationNotFoundError as e:
        raise EntityNotFoundError("AnalyticsOperation", operation_id) from e

    return OperationCatalogueItem(
        id=op.id,
        name=op.name,
        description=op.description,
        required_permission=op.required_permission,
        max_input_rows=op.max_input_rows,
        max_input_cols=op.max_input_cols,
        timeout_seconds=op.timeout_seconds,
        parameters_schema=op.input_schema.model_json_schema(),
    )


# -----------------------------------------------------------------------------
# Analytics Jobs
# -----------------------------------------------------------------------------


@router.post(
    "/jobs",
    response_model=AnalyticsJobRead,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[
        Depends(require_permission("analytics.create")),
        Depends(verify_csrf_token),
    ],
)
async def submit_job(
    data: AnalyticsJobCreateInput,
    membership: OrganizationMembership = Depends(get_current_membership),
    auth_session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> AnalyticsJobRead:
    """Submit an analytics calculation job. Returns HTTP 202 Accepted with job metadata."""
    service = AnalyticsService(db)
    return await service.submit_job(
        organization_id=membership.organization_id,
        user_id=auth_session.user_id,
        data=data,
    )


@router.get(
    "/jobs",
    response_model=AnalyticsJobListResponse,
    dependencies=[Depends(require_permission("analytics.read"))],
)
async def list_jobs(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    job_status: str | None = Query(default=None, alias="status"),
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> AnalyticsJobListResponse:
    """List recent analytics jobs in the active organization with status filtering."""
    service = AnalyticsService(db)
    items, total = await service.list_jobs(
        organization_id=membership.organization_id,
        limit=limit,
        offset=offset,
        status=job_status,
    )
    return AnalyticsJobListResponse(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/jobs/{job_id}",
    response_model=AnalyticsJobRead,
    dependencies=[Depends(require_permission("analytics.read"))],
)
async def get_job(
    job_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> AnalyticsJobRead:
    """Get status, progress percentage, current stage, and error details of an analytics job."""
    service = AnalyticsService(db)
    return await service.get_job(job_id, membership.organization_id)


@router.post(
    "/jobs/{job_id}/cancel",
    dependencies=[
        Depends(require_permission("analytics.cancel")),
        Depends(verify_csrf_token),
    ],
)
async def cancel_job(
    job_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    auth_session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Request cooperative cancellation of a queued or running job."""
    service = AnalyticsService(db)
    # Check if membership has admin/owner role (can cancel any job in org)
    from app.repositories.role import RoleRepository

    role_repo = RoleRepository(db)
    roles = await role_repo.get_membership_roles(membership.id)
    can_cancel_any = any(r.slug in ("owner", "admin") for r in roles)

    cancelled = await service.cancel_job(
        job_id=job_id,
        organization_id=membership.organization_id,
        user_id=auth_session.user_id,
        can_cancel_any=can_cancel_any,
    )
    return {"job_id": str(job_id), "cancellation_accepted": cancelled}


# -----------------------------------------------------------------------------
# Results
# -----------------------------------------------------------------------------


@router.get(
    "/jobs/{job_id}/result",
    response_model=AnalyticsResultRead,
    dependencies=[Depends(require_permission("analytics.read"))],
)
async def get_job_result(
    job_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> AnalyticsResultRead:
    """Get completed analytical result for a job."""
    service = AnalyticsService(db)
    return await service.get_result(job_id, membership.organization_id)


@router.get(
    "/results/{result_id}",
    response_model=AnalyticsResultRead,
    dependencies=[Depends(require_permission("analytics.read"))],
)
async def get_result_by_id(
    result_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> AnalyticsResultRead:
    """Get analytical result by result ID."""
    service = AnalyticsService(db)
    return await service.get_result_by_id(result_id, membership.organization_id)


@router.get(
    "/results/{result_id}/artifact",
    dependencies=[Depends(require_permission("analytics.export"))],
)
async def download_result_artifact(
    result_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> FileResponse:
    """Download large tabular analytical result artifact in Parquet format with checksum & security validation."""
    service = AnalyticsService(db)
    result = await service.result_repo.get_by_id_scoped(result_id, membership.organization_id)
    if not result:
        raise EntityNotFoundError("AnalyticsResult", str(result_id))

    if not result.artifact_path:
        raise EntityNotFoundError("Artifact", str(result_id))

    art_path = Path(result.artifact_path).resolve()
    storage_root = Path(settings.ANALYTICS_STORAGE_DIR).resolve()

    if not str(art_path).startswith(str(storage_root)) or not art_path.is_file():
        raise EntityNotFoundError("Artifact", str(result_id))

    # Verify checksum integrity
    if result.artifact_checksum_sha256:
        actual_hash = AnalyticsStorageService.compute_sha256(art_path)
        if actual_hash != result.artifact_checksum_sha256:
            raise EntityNotFoundError("Artifact", str(result_id))

    return FileResponse(
        path=str(art_path),
        media_type="application/octet-stream",
        filename=f"analytics_{result.result_type}_{result_id}.parquet",
    )


@router.get(
    "/jobs/{job_id}/artifact",
    dependencies=[Depends(require_permission("analytics.export"))],
)
async def download_job_result_artifact(
    job_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> FileResponse:
    """Download large tabular analytical result artifact by job ID."""
    service = AnalyticsService(db)
    result = await service.result_repo.get_by_job_id_scoped(job_id, membership.organization_id)
    if not result:
        raise EntityNotFoundError("AnalyticsResult", str(job_id))

    return await download_result_artifact(result.id, membership, db)


# -----------------------------------------------------------------------------
# Spreadsheet Publication
# -----------------------------------------------------------------------------


@router.post(
    "/results/{result_id}/publish",
    response_model=PublishToSheetResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_permission("analytics.export")),
        Depends(require_permission("spreadsheets.create")),
        Depends(verify_csrf_token),
    ],
)
@router.post(
    "/results/{result_id}/publish-to-sheet",
    response_model=PublishToSheetResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_permission("analytics.export")),
        Depends(require_permission("spreadsheets.create")),
        Depends(verify_csrf_token),
    ],
)
async def publish_result_to_sheet(
    result_id: uuid.UUID,
    data: PublishToSheetInput,
    membership: OrganizationMembership = Depends(get_current_membership),
    auth_session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> PublishToSheetResponse:
    """Publish tabular analytical results into a new spreadsheet sheet within a workbook."""
    service = AnalyticsService(db)
    return await service.publish_result_to_sheet(
        result_id=result_id,
        organization_id=membership.organization_id,
        user_id=auth_session.user_id,
        destination_workbook_id=data.destination_workbook_id,
        sheet_name=data.sheet_name,
        idempotency_key=data.idempotency_key,
    )


@router.post(
    "/jobs/{job_id}/publish-to-sheet",
    response_model=PublishToSheetResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_permission("analytics.export")),
        Depends(require_permission("spreadsheets.create")),
        Depends(verify_csrf_token),
    ],
)
async def publish_job_result_to_sheet(
    job_id: uuid.UUID,
    data: PublishToSheetInput,
    membership: OrganizationMembership = Depends(get_current_membership),
    auth_session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> PublishToSheetResponse:
    """Publish tabular analytical results for a job into a spreadsheet sheet."""
    service = AnalyticsService(db)
    res = await service.result_repo.get_by_job_id_scoped(job_id, membership.organization_id)
    if not res:
        raise EntityNotFoundError("AnalyticsResult", str(job_id))

    return await service.publish_result_to_sheet(
        result_id=res.id,
        organization_id=membership.organization_id,
        user_id=auth_session.user_id,
        destination_workbook_id=data.destination_workbook_id,
        sheet_name=data.sheet_name,
        idempotency_key=data.idempotency_key,
    )


# -----------------------------------------------------------------------------
# Storage & Quota
# -----------------------------------------------------------------------------


@router.get(
    "/storage",
    summary="Get storage usage and quota for active organization",
    dependencies=[Depends(require_permission("analytics.read"))],
)
async def get_storage_usage(
    membership: OrganizationMembership = Depends(get_current_membership),
) -> dict[str, Any]:
    """Retrieve authoritative storage quota and usage breakdown for the active organization."""
    storage = AnalyticsStorageService()
    usage_bytes = storage.get_organization_storage_bytes(membership.organization_id)
    quota_bytes = settings.ANALYTICS_MAX_ORG_STORAGE_BYTES
    return {
        "organization_id": str(membership.organization_id),
        "usage_bytes": usage_bytes,
        "quota_bytes": quota_bytes,
        "available_bytes": max(0, quota_bytes - usage_bytes),
        "utilization_percent": round((usage_bytes / quota_bytes) * 100, 2) if quota_bytes > 0 else 0.0,
    }
