"""Analytics service orchestrating job creation, outbox scheduling, and result access."""

from __future__ import annotations

import math
import os
import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.exceptions import DatasetTooLargeError
from app.analytics.registry import registry
from app.core.config import settings
from app.core.exceptions import EntityNotFoundError, ForbiddenError, ValidationError
from app.db.transaction import transaction
from app.models.analytics import (
    AnalyticsJob,
    AnalyticsJobStatus,
    AnalyticsOutboxEvent,
    AnalyticsResult,
)
from app.repositories.analytics import (
    AnalyticsJobRepository,
    AnalyticsOutboxRepository,
    AnalyticsResultRepository,
)
from app.repositories.spreadsheet import (
    SheetRepository,
    SheetRowRepository,
    WorkbookRepository,
)
from app.schemas.analytics import (
    AnalyticsJobCreateInput,
    AnalyticsJobRead,
    AnalyticsResultRead,
    PublishToSheetResponse,
)
from app.schemas.spreadsheet import BatchCellUpdateInput, CellEditInput, SheetCreateInput
from app.services.spreadsheet import SpreadsheetService


class AnalyticsService:
    """Core domain service for Analytics jobs and results."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.job_repo = AnalyticsJobRepository(session)
        self.outbox_repo = AnalyticsOutboxRepository(session)
        self.result_repo = AnalyticsResultRepository(session)
        self.wb_repo = WorkbookRepository(session)
        self.sheet_repo = SheetRepository(session)
        self.row_repo = SheetRowRepository(session)

    async def submit_job(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None,
        data: AnalyticsJobCreateInput,
    ) -> AnalyticsJobRead:
        """Submit an analytics job with transactional outbox scheduling."""
        # 1. Verify operation exists in allowlist and validate parameters
        registry.validate_parameters(data.operation_id, data.parameters)

        # 2. Check idempotency
        if data.idempotency_key:
            existing = await self.job_repo.get_by_idempotency_key(
                organization_id, data.idempotency_key
            )
            if existing:
                return self._to_job_read(existing)

        # 3. Verify source workbook and sheet ownership
        sheet = await self.sheet_repo.get_by_id(data.source_sheet_id, organization_id)
        if not sheet or sheet.workbook_id != data.source_workbook_id:
            raise EntityNotFoundError("Sheet", str(data.source_sheet_id))

        wb = await self.wb_repo.get_by_id(data.source_workbook_id, organization_id)
        if not wb:
            raise EntityNotFoundError("Workbook", str(data.source_workbook_id))

        # 4. Enforce dataset admission limits before any job or outbox event creation
        if len(sheet.columns) > settings.ANALYTICS_MAX_SOURCE_COLS:
            raise DatasetTooLargeError(
                f"Sheet exceeds maximum allowable columns: {len(sheet.columns)} > {settings.ANALYTICS_MAX_SOURCE_COLS}"
            )

        total_rows = await self.row_repo.count_populated_rows(data.source_sheet_id)
        if total_rows > settings.ANALYTICS_MAX_SOURCE_ROWS:
            raise DatasetTooLargeError(
                f"Sheet exceeds maximum allowable rows: {total_rows} > {settings.ANALYTICS_MAX_SOURCE_ROWS}"
            )

        total_non_empty_cells = await self.row_repo.count_non_empty_cells(data.source_sheet_id)
        if total_non_empty_cells > settings.ANALYTICS_MAX_NON_EMPTY_CELLS:
            raise DatasetTooLargeError(
                f"Sheet exceeds maximum allowable non-empty cells: {total_non_empty_cells} > {settings.ANALYTICS_MAX_NON_EMPTY_CELLS}"
            )

        # 5. Check concurrency limits
        active_org_jobs = await self.job_repo.count_active_jobs_for_org(organization_id)
        if active_org_jobs >= settings.ANALYTICS_MAX_CONCURRENT_JOBS_PER_ORG:
            raise ValidationError(
                f"Organization active jobs limit ({settings.ANALYTICS_MAX_CONCURRENT_JOBS_PER_ORG}) reached."
            )

        if user_id:
            active_user_jobs = await self.job_repo.count_active_jobs_for_user(
                organization_id, user_id
            )
            if active_user_jobs >= settings.ANALYTICS_MAX_CONCURRENT_JOBS_PER_USER:
                raise ValidationError(
                    f"User active jobs limit ({settings.ANALYTICS_MAX_CONCURRENT_JOBS_PER_USER}) reached."
                )

        # 6. Atomic Transaction: create AnalyticsJob and AnalyticsOutboxEvent together
        from sqlalchemy.exc import IntegrityError

        try:
            async with transaction(self.session):
                job = AnalyticsJob(
                    organization_id=organization_id,
                    workspace_id=wb.workspace_id,
                    source_workbook_id=data.source_workbook_id,
                    source_sheet_id=data.source_sheet_id,
                    requested_by_user_id=user_id,
                    operation_id=data.operation_id,
                    parameters=data.parameters,
                    status=AnalyticsJobStatus.QUEUED,
                    source_sheet_version=sheet.version,
                    idempotency_key=data.idempotency_key,
                )
                await self.job_repo.create(job)

                outbox_event = AnalyticsOutboxEvent(
                    job_id=job.id,
                    organization_id=organization_id,
                    event_type="analytics.job.scheduled",
                    payload={"job_id": str(job.id), "organization_id": str(organization_id)},
                )
                await self.outbox_repo.create(outbox_event)
        except IntegrityError:
            if data.idempotency_key:
                existing = await self.job_repo.get_by_idempotency_key(
                    organization_id, data.idempotency_key
                )
                if existing:
                    return self._to_job_read(existing)
            raise

        return self._to_job_read(job)

    async def get_job(self, job_id: uuid.UUID, organization_id: uuid.UUID) -> AnalyticsJobRead:
        job = await self.job_repo.get_by_id_scoped(job_id, organization_id)
        if not job:
            raise EntityNotFoundError("AnalyticsJob", str(job_id))
        return self._to_job_read(job)

    async def list_jobs(
        self,
        organization_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> tuple[list[AnalyticsJobRead], int]:
        jobs, total = await self.job_repo.list_for_organization(
            organization_id=organization_id,
            limit=limit,
            offset=offset,
            status=status,
        )
        return [self._to_job_read(j) for j in jobs], total

    async def cancel_job(
        self,
        job_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None,
        can_cancel_any: bool = False,
    ) -> bool:
        job = await self.job_repo.get_by_id_scoped(job_id, organization_id)
        if not job:
            raise EntityNotFoundError("AnalyticsJob", str(job_id))

        # Check own-job permission if user cannot cancel any
        if not can_cancel_any and job.requested_by_user_id != user_id:
            raise ForbiddenError("You may only cancel your own analytical jobs.")

        if job.status in AnalyticsJobStatus.TERMINAL_STATUSES:
            return False

        async with transaction(self.session):
            return await self.job_repo.request_cancellation(job_id, organization_id)

    async def get_result(
        self, job_id: uuid.UUID, organization_id: uuid.UUID
    ) -> AnalyticsResultRead:
        result = await self.result_repo.get_by_job_id_scoped(job_id, organization_id)
        if not result:
            raise EntityNotFoundError("AnalyticsResult", str(job_id))
        return self._to_result_read(result)

    async def get_result_by_id(
        self, result_id: uuid.UUID, organization_id: uuid.UUID
    ) -> AnalyticsResultRead:
        result = await self.result_repo.get_by_id_scoped(result_id, organization_id)
        if not result:
            raise EntityNotFoundError("AnalyticsResult", str(result_id))
        return self._to_result_read(result)

    async def publish_result_to_sheet(
        self,
        result_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None,
        destination_workbook_id: uuid.UUID,
        sheet_name: str | None = None,
        idempotency_key: str | None = None,
    ) -> PublishToSheetResponse:
        """Publish tabular analytics result into a new spreadsheet sheet atomically and idempotently."""
        # 1. Re-validate destination workbook belongs to caller's organization
        workbook = await self.wb_repo.get_by_id(destination_workbook_id, organization_id)
        if not workbook:
            raise EntityNotFoundError("Workbook", str(destination_workbook_id))

        # 2. Idempotency check via publication key
        if idempotency_key:
            existing_pub = await self.result_repo.get_by_publish_idempotency_key(
                organization_id, idempotency_key
            )
            if existing_pub and existing_pub.published_sheet_id:
                return PublishToSheetResponse(
                    workbook_id=destination_workbook_id,
                    sheet_id=existing_pub.published_sheet_id,
                    sheet_name=existing_pub.published_sheet_name or "Published_Analysis",
                    rows_written=existing_pub.row_count or 0,
                    columns_written=existing_pub.column_count or 0,
                )

        result = await self.result_repo.get_by_id_scoped(result_id, organization_id)
        if not result:
            raise EntityNotFoundError("AnalyticsResult", str(result_id))

        if result.published_sheet_id is not None and not idempotency_key:
            return PublishToSheetResponse(
                workbook_id=destination_workbook_id,
                sheet_id=result.published_sheet_id,
                sheet_name=result.published_sheet_name or f"Analysis_{result.result_type[:15]}",
                rows_written=result.row_count or 0,
                columns_written=result.column_count or 0,
            )

        # Extract tabular data
        payload = result.result_payload or {}
        rows_data: list[dict[str, Any]] = []

        if "data" in payload and isinstance(payload["data"], list):
            rows_data = payload["data"]
        elif result.result_type == "describe_columns" and "columns" in payload:
            metrics = ["count", "mean", "std", "min", "median", "max"]
            col_names = list(payload["columns"].keys())
            for m in metrics:
                row_dict: dict[str, Any] = {"metric": m}
                for c in col_names:
                    row_dict[c] = payload["columns"][c].get(m)
                rows_data.append(row_dict)
        elif result.result_type == "correlation_matrix" and "matrix" in payload:
            col_names = payload.get("columns", [])
            matrix = payload.get("matrix", {})
            for c1 in col_names:
                row_dict = {"variable": c1}
                for c2 in col_names:
                    row_dict[c2] = matrix.get(c1, {}).get(c2)
                rows_data.append(row_dict)
        else:
            raise ValidationError(
                f"Result type '{result.result_type}' does not produce tabular data for spreadsheets."
            )

        if not rows_data:
            raise ValidationError("Analytics result contains 0 rows to publish.")

        final_sheet_name = sheet_name or f"Analysis_{result.result_type[:15]}"

        from app.formula import CellCoord, format_cell_ref

        headers = list(rows_data[0].keys())
        cell_edits: list[CellEditInput] = []

        # Header row 0
        for col_idx, h in enumerate(headers):
            ref = format_cell_ref(CellCoord(row=0, col=col_idx))
            cell_edits.append(CellEditInput(cell_ref=ref, value=h))

        # Data rows (capped at 500 cells per batch safety limit)
        for row_idx, r_dict in enumerate(rows_data[:400]):
            for col_idx, h in enumerate(headers[:20]):
                val = r_dict.get(h)
                if val is not None:
                    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
                        val = None
                    ref = format_cell_ref(CellCoord(row=row_idx + 1, col=col_idx))
                    cell_edits.append(CellEditInput(cell_ref=ref, value=val))

        # 3. Sheet creation and cell population in a single atomic database transaction
        try:
            async with transaction(self.session):
                spreadsheet_svc = SpreadsheetService(self.session)
                created_sheet = await spreadsheet_svc.create_sheet(
                    workbook_id=destination_workbook_id,
                    organization_id=organization_id,
                    data=SheetCreateInput(
                        name=final_sheet_name,
                        row_count=max(len(rows_data) + 1, 100),
                        column_count=max(len(rows_data[0].keys()), 10),
                    ),
                )

                # Stage 12 crash hook: between destination sheet creation and cell write
                if os.environ.get("VIBE_CRASH_STAGE") == "STAGE_12_DURING_PUBLISH":
                    import sys

                    sys.stderr.write(">>> TEST HOOK: CRASH AT STAGE_12_DURING_PUBLISH <<<\n")
                    sys.stderr.flush()
                    os._exit(99)

                chunk_size = 500
                for i in range(0, len(cell_edits), chunk_size):
                    chunk = cell_edits[i : i + chunk_size]
                    await spreadsheet_svc.update_cells_batch(
                        sheet_id=created_sheet.id,
                        organization_id=organization_id,
                        user_id=user_id,
                        data=BatchCellUpdateInput(updates=chunk),
                    )

                # Record publication metadata on result
                result.published_sheet_id = created_sheet.id
                result.published_sheet_name = final_sheet_name
                if idempotency_key:
                    result.publish_idempotency_key = idempotency_key
        except IntegrityError:
            # Concurrent race condition: another request published simultaneously
            await self.session.rollback()
            if idempotency_key:
                existing_pub = await self.result_repo.get_by_publish_idempotency_key(
                    organization_id, idempotency_key
                )
                if existing_pub and existing_pub.published_sheet_id:
                    return PublishToSheetResponse(
                        workbook_id=destination_workbook_id,
                        sheet_id=existing_pub.published_sheet_id,
                        sheet_name=existing_pub.published_sheet_name or final_sheet_name,
                        rows_written=len(rows_data) + 1,
                        columns_written=len(headers),
                    )
            refreshed = await self.result_repo.get_by_id_scoped(result_id, organization_id)
            if refreshed and refreshed.published_sheet_id:
                return PublishToSheetResponse(
                    workbook_id=destination_workbook_id,
                    sheet_id=refreshed.published_sheet_id,
                    sheet_name=refreshed.published_sheet_name or final_sheet_name,
                    rows_written=len(rows_data) + 1,
                    columns_written=len(headers),
                )
            raise

        return PublishToSheetResponse(
            workbook_id=destination_workbook_id,
            sheet_id=created_sheet.id,
            sheet_name=final_sheet_name,
            rows_written=len(rows_data) + 1,
            columns_written=len(headers),
        )

    def _to_job_read(self, job: AnalyticsJob) -> AnalyticsJobRead:
        return AnalyticsJobRead(
            id=job.id,
            organization_id=job.organization_id,
            workspace_id=job.workspace_id,
            source_workbook_id=job.source_workbook_id,
            source_sheet_id=job.source_sheet_id,
            requested_by_user_id=job.requested_by_user_id,
            operation_id=job.operation_id,
            parameters=job.parameters,
            status=job.status,
            progress_percent=job.progress_percent,
            current_stage=job.current_stage,
            source_sheet_version=job.source_sheet_version,
            result_id=job.result_id,
            error_code=job.error_code,
            safe_error_message=job.safe_error_message,
            attempt_count=job.attempt_count,
            max_attempts=job.max_attempts,
            claim_generation=job.claim_generation,
            snapshot_row_count=job.snapshot_row_count,
            snapshot_col_count=job.snapshot_col_count,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
        )

    def _to_result_read(self, res: AnalyticsResult) -> AnalyticsResultRead:
        return AnalyticsResultRead(
            id=res.id,
            job_id=res.job_id,
            organization_id=res.organization_id,
            result_type=res.result_type,
            result_payload=res.result_payload,
            artifact_size_bytes=res.artifact_size_bytes,
            row_count=res.row_count,
            column_count=res.column_count,
            has_artifact=res.artifact_path is not None,
            published_sheet_id=res.published_sheet_id,
            created_at=res.created_at,
        )
