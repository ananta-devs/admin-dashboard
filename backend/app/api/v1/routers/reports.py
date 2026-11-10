"""FastAPI router for report definitions, section layout, asynchronous execution, and PDF export."""

from __future__ import annotations

import io
import uuid
from typing import Sequence

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    get_current_membership,
    require_permission,
    verify_csrf_token,
)
from app.core.config import settings
from app.core.exceptions import EntityNotFoundError, ValidationError
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership
from app.models.report import ReportRunStatus
from app.schemas.report import (
    ReportDefinitionCreateInput,
    ReportDefinitionRead,
    ReportDefinitionUpdateInput,
    ReportDownloadTokenResponse,
    ReportRunRead,
    ReportRunTriggerInput,
    ReportSectionCreateInput,
    ReportSectionRead,
    ReportSectionReorderInput,
    ReportSectionUpdateInput,
)
from app.services.report import ReportService
from app.services.report_download_token import ReportDownloadTokenService
from app.services.report_storage import ReportStorageService

router = APIRouter(prefix="/reports", tags=["reports"])


# =============================================================================
# Report Definitions
# =============================================================================

@router.post(
    "/definitions",
    response_model=ReportDefinitionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new report definition",
    dependencies=[
        Depends(require_permission("reports.create")),
        Depends(verify_csrf_token),
    ],
)
async def create_report_definition(
    payload: ReportDefinitionCreateInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> ReportDefinitionRead:
    service = ReportService(session)
    report_def = await service.create_report_definition(
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        data=payload,
        membership_id=membership.id,
    )
    return ReportDefinitionRead.model_validate(report_def)


@router.get(
    "/definitions",
    response_model=list[ReportDefinitionRead],
    summary="List report definitions in organization",
    dependencies=[Depends(require_permission("reports.read"))],
)
async def list_report_definitions(
    workspace_id: uuid.UUID | None = Query(None),
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> list[ReportDefinitionRead]:
    service = ReportService(session)
    items = await service.list_report_definitions(
        organization_id=membership.organization_id,
        workspace_id=workspace_id,
    )
    return [ReportDefinitionRead.model_validate(item) for item in items]


@router.get(
    "/definitions/{definition_id}",
    response_model=ReportDefinitionRead,
    summary="Get report definition with sections",
    dependencies=[Depends(require_permission("reports.read"))],
)
async def get_report_definition(
    definition_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> ReportDefinitionRead:
    service = ReportService(session)
    report_def = await service.get_report_definition(
        definition_id, organization_id=membership.organization_id
    )
    return ReportDefinitionRead.model_validate(report_def)


@router.patch(
    "/definitions/{definition_id}",
    response_model=ReportDefinitionRead,
    summary="Update report definition configuration",
    dependencies=[
        Depends(require_permission("reports.update")),
        Depends(verify_csrf_token),
    ],
)
async def update_report_definition(
    definition_id: uuid.UUID,
    payload: ReportDefinitionUpdateInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> ReportDefinitionRead:
    service = ReportService(session)
    updated = await service.update_report_definition(
        definition_id,
        organization_id=membership.organization_id,
        data=payload,
    )
    return ReportDefinitionRead.model_validate(updated)


@router.delete(
    "/definitions/{definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a report definition",
    dependencies=[
        Depends(require_permission("reports.delete")),
        Depends(verify_csrf_token),
    ],
)
async def delete_report_definition(
    definition_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> Response:
    service = ReportService(session)
    await service.archive_report_definition(
        definition_id, organization_id=membership.organization_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =============================================================================
# Report Sections
# =============================================================================

@router.post(
    "/definitions/{definition_id}/sections",
    response_model=ReportSectionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add section to report definition",
    dependencies=[
        Depends(require_permission("reports.update")),
        Depends(verify_csrf_token),
    ],
)
async def add_report_section(
    definition_id: uuid.UUID,
    payload: ReportSectionCreateInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> ReportSectionRead:
    service = ReportService(session)
    section = await service.add_section(
        report_definition_id=definition_id,
        organization_id=membership.organization_id,
        data=payload,
    )
    return ReportSectionRead.model_validate(section)


@router.patch(
    "/definitions/{definition_id}/sections/{section_id}",
    response_model=ReportSectionRead,
    summary="Update section configuration",
    dependencies=[
        Depends(require_permission("reports.update")),
        Depends(verify_csrf_token),
    ],
)
async def update_report_section(
    definition_id: uuid.UUID,
    section_id: uuid.UUID,
    payload: ReportSectionUpdateInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> ReportSectionRead:
    service = ReportService(session)
    updated = await service.update_section(
        section_id=section_id,
        organization_id=membership.organization_id,
        data=payload,
    )
    return ReportSectionRead.model_validate(updated)


@router.delete(
    "/definitions/{definition_id}/sections/{section_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete section from report definition",
    dependencies=[
        Depends(require_permission("reports.update")),
        Depends(verify_csrf_token),
    ],
)
async def delete_report_section(
    definition_id: uuid.UUID,
    section_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> Response:
    service = ReportService(session)
    await service.delete_section(
        section_id=section_id, organization_id=membership.organization_id
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/definitions/{definition_id}/sections/reorder",
    response_model=list[ReportSectionRead],
    summary="Reorder report sections deterministically",
    dependencies=[
        Depends(require_permission("reports.update")),
        Depends(verify_csrf_token),
    ],
)
async def reorder_report_sections(
    definition_id: uuid.UUID,
    payload: ReportSectionReorderInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> list[ReportSectionRead]:
    service = ReportService(session)
    sections = await service.reorder_sections(
        report_definition_id=definition_id,
        organization_id=membership.organization_id,
        ordered_section_ids=payload.ordered_section_ids,
    )
    return [ReportSectionRead.model_validate(s) for s in sections]


# =============================================================================
# Report Execution Runs
# =============================================================================

@router.post(
    "/definitions/{definition_id}/runs",
    response_model=ReportRunRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger on-demand report execution",
    dependencies=[
        Depends(require_permission("reports.run")),
        Depends(verify_csrf_token),
    ],
)
async def trigger_report_run(
    definition_id: uuid.UUID,
    payload: ReportRunTriggerInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> ReportRunRead:
    service = ReportService(session)
    run = await service.trigger_report_run(
        report_definition_id=definition_id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        input_data=payload,
        membership_id=membership.id,
    )
    return ReportRunRead.model_validate(run)


@router.get(
    "/definitions/{definition_id}/runs",
    response_model=list[ReportRunRead],
    summary="List historical execution runs for a report definition",
    dependencies=[Depends(require_permission("reports.read"))],
)
async def list_report_runs(
    definition_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> list[ReportRunRead]:
    service = ReportService(session)
    runs, _ = await service.list_report_runs(
        report_definition_id=definition_id,
        organization_id=membership.organization_id,
        limit=limit,
        offset=offset,
    )
    return [ReportRunRead.model_validate(r) for r in runs]


@router.get(
    "/runs/{run_id}",
    response_model=ReportRunRead,
    summary="Get status and details of a report execution run",
    dependencies=[Depends(require_permission("reports.read"))],
)
async def get_report_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> ReportRunRead:
    service = ReportService(session)
    run = await service.get_report_run(run_id, organization_id=membership.organization_id)
    return ReportRunRead.model_validate(run)


# =============================================================================
# PDF Artifact Downloads
# =============================================================================

@router.post(
    "/runs/{run_id}/download-token",
    response_model=ReportDownloadTokenResponse,
    summary="Generate single-use token for authenticated report download",
    dependencies=[
        Depends(require_permission("reports.export")),
        Depends(verify_csrf_token),
    ],
)
async def create_download_token(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> ReportDownloadTokenResponse:
    service = ReportService(session)
    run = await service.get_report_run(run_id, organization_id=membership.organization_id)

    if run.status != ReportRunStatus.SUCCEEDED or not run.pdf_artifact_path:
        raise ValidationError("Report run has not successfully generated a PDF artifact.")

    token = await ReportDownloadTokenService.create_download_token(
        run_id=run.id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        ttl_seconds=settings.REPORT_DOWNLOAD_TOKEN_TTL_SECONDS,
    )
    download_url = f"/api/v1/reports/download/{token}"
    return ReportDownloadTokenResponse(
        download_token=token,
        download_url=download_url,
        expires_in_seconds=settings.REPORT_DOWNLOAD_TOKEN_TTL_SECONDS,
    )


@router.get(
    "/runs/{run_id}/download",
    summary="Direct authenticated download of report PDF artifact",
    dependencies=[Depends(require_permission("reports.export"))],
)
async def download_report_run_direct(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> StreamingResponse:
    service = ReportService(session)
    run = await service.get_report_run(run_id, organization_id=membership.organization_id)

    if run.status != ReportRunStatus.SUCCEEDED or not run.pdf_artifact_path:
        raise ValidationError("Report run has not generated a PDF artifact.")

    storage = ReportStorageService()
    pdf_bytes = storage.read_and_verify_bytes(
        path=run.pdf_artifact_path,
        expected_checksum=run.pdf_checksum_sha256,
        max_allowed_bytes=settings.REPORT_MAX_PDF_BYTES,
    )

    filename = f"{run.report_title.replace(' ', '_')}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


@router.get(
    "/download/{token}",
    summary="Consume single-use token and stream PDF report artifact",
)
async def download_report_run_by_token(
    token: str,
    session: AsyncSession = Depends(get_db_session),
) -> StreamingResponse:
    """Public single-use token download endpoint with atomic burn."""
    token_data = await ReportDownloadTokenService.consume_download_token(token)
    run_id = uuid.UUID(token_data["run_id"])
    org_id = uuid.UUID(token_data["organization_id"])

    service = ReportService(session)
    run = await service.get_report_run(run_id, organization_id=org_id)

    if run.status != ReportRunStatus.SUCCEEDED or not run.pdf_artifact_path:
        raise ValidationError("Report run has no available PDF artifact.")

    storage = ReportStorageService()
    pdf_bytes = storage.read_and_verify_bytes(
        path=run.pdf_artifact_path,
        expected_checksum=run.pdf_checksum_sha256,
        max_allowed_bytes=settings.REPORT_MAX_PDF_BYTES,
    )

    filename = f"{run.report_title.replace(' ', '_')}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )
