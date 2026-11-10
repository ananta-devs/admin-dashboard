"""Spreadsheet import, export, and synchronization service layer."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, EntityNotFoundError, ValidationError
from app.db.base import utc_now
from app.db.transaction import transaction
from app.formula import col_to_letter, letter_to_col
from app.models.spreadsheet import Sheet
from app.models.sync import (
    SpreadsheetSyncConflict,
    SpreadsheetSyncMapping,
    SpreadsheetSyncRun,
)
from app.repositories.integration import IntegrationRepository
from app.repositories.spreadsheet import (
    SheetRepository,
    SheetRowRepository,
    WorkbookRepository,
)
from app.repositories.sync import SpreadsheetSyncRepository
from app.schemas.sync import (
    GoogleExportInput,
    GoogleExportResponse,
    GoogleImportInput,
    GoogleImportResponse,
    SyncMappingCreateInput,
)
from app.services.google_oauth import GoogleOAuthService


def _cell_display_value(cell_dict: dict[str, Any] | None) -> Any:
    """Extract raw display or formula representation of a cell for export."""
    if not cell_dict:
        return ""
    if cell_dict.get("f"):
        return cell_dict["f"]
    if cell_dict.get("c") is not None:
        return cell_dict["c"]
    if cell_dict.get("v") is not None:
        return cell_dict["v"]
    return ""


def _parse_incoming_cell(val: Any) -> dict[str, Any]:
    """Parse raw value from Google Sheets into VIBE cell structure."""
    if val is None or val == "":
        return {"v": None, "c": None, "f": None}
    val_str = str(val).strip()
    if val_str.startswith("="):
        return {"v": None, "c": None, "f": val_str}
    # Check if number
    try:
        if "." in val_str:
            num = float(val_str)
            return {"v": num, "c": num, "f": None}
        num_int = int(val_str)
        return {"v": num_int, "c": num_int, "f": None}
    except (ValueError, TypeError):
        pass
    # Boolean check
    if val_str.upper() == "TRUE":
        return {"v": True, "c": True, "f": None}
    if val_str.upper() == "FALSE":
        return {"v": False, "c": False, "f": None}
    return {"v": val, "c": val, "f": None}


class GoogleSpreadsheetImportService:
    """Imports external Google Sheet worksheets into VIBE workbooks."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.integration_repo = IntegrationRepository(session)
        self.oauth_service = GoogleOAuthService(session)
        self.workbook_repo = WorkbookRepository(session)
        self.sheet_repo = SheetRepository(session)
        self.row_repo = SheetRowRepository(session)

    async def import_sheet(
        self, input_data: GoogleImportInput, organization_id: uuid.UUID
    ) -> GoogleImportResponse:
        integration = await self.integration_repo.get_by_id(
            input_data.integration_id, organization_id
        )
        if not integration or integration.status != "connected":
            raise ValidationError("Valid and connected Google integration required.")

        workbook = await self.workbook_repo.get_by_id(
            input_data.target_workbook_id, organization_id
        )
        if not workbook:
            raise EntityNotFoundError("Workbook", str(input_data.target_workbook_id))

        # Read external values from Google
        range_a1 = f"'{input_data.external_sheet_name}'!A1:ZZ5000"
        values_resp = await self.oauth_service.get_sheet_values(
            integration_id=integration.id,
            organization_id=organization_id,
            external_spreadsheet_id=input_data.external_spreadsheet_id,
            range_name=range_a1,
        )

        rows = values_resp.values
        rows_imported = len(rows)
        cols_imported = max((len(r) for r in rows), default=0)
        cells_imported = 0

        async with transaction(self.session):
            target_sheet_name = input_data.target_sheet_name or input_data.external_sheet_name
            # Create new sheet in workbook
            sheet = Sheet(
                workbook_id=workbook.id,
                organization_id=organization_id,
                name=target_sheet_name,
                row_count=max(rows_imported, 100),
                column_count=max(cols_imported, 26),
            )
            created_sheet = await self.sheet_repo.create(sheet)

            for r_idx, row in enumerate(rows):
                if not row:
                    continue
                row_cells: dict[str, Any] = {}
                for c_idx, val in enumerate(row):
                    if val is None or val == "":
                        continue
                    col_letter = col_to_letter(c_idx)
                    row_cells[col_letter] = _parse_incoming_cell(val)
                    cells_imported += 1
                if row_cells:
                    await self.row_repo.upsert_row(
                        sheet_id=created_sheet.id,
                        organization_id=organization_id,
                        row_index=r_idx,
                        cells=row_cells,
                    )

        return GoogleImportResponse(
            workbook_id=workbook.id,
            sheet_id=created_sheet.id,
            sheet_name=created_sheet.name,
            rows_imported=rows_imported,
            columns_imported=cols_imported,
            cells_imported=cells_imported,
        )


class GoogleSpreadsheetExportService:
    """Exports VIBE sheets to Google Sheets."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.integration_repo = IntegrationRepository(session)
        self.oauth_service = GoogleOAuthService(session)
        self.sheet_repo = SheetRepository(session)
        self.row_repo = SheetRowRepository(session)

    async def export_sheet(
        self, input_data: GoogleExportInput, organization_id: uuid.UUID
    ) -> GoogleExportResponse:
        integration = await self.integration_repo.get_by_id(
            input_data.integration_id, organization_id
        )
        if not integration or integration.status != "connected":
            raise ValidationError("Valid and connected Google integration required.")

        sheet = await self.sheet_repo.get_by_id(input_data.source_sheet_id, organization_id)
        if not sheet:
            raise EntityNotFoundError("Sheet", str(input_data.source_sheet_id))

        # Load all rows for sheet
        sheet_rows = await self.row_repo.list_rows_paginated(sheet.id, limit=5000, offset=0)

        # Build 2D matrix
        max_row = max((r.row_index for r in sheet_rows), default=-1)
        matrix: list[list[Any]] = []
        max_col_idx = 0

        for _r_idx in range(max_row + 1):
            matrix.append([])

        for r in sheet_rows:
            r_idx = r.row_index
            cells = r.cells or {}
            for col_letter, cell_dict in cells.items():
                c_idx = letter_to_col(col_letter)
                if c_idx > max_col_idx:
                    max_col_idx = c_idx
                while len(matrix[r_idx]) <= c_idx:
                    matrix[r_idx].append("")
                matrix[r_idx][c_idx] = _cell_display_value(cell_dict)

        dest_spreadsheet_id: str
        dest_sheet_name: str = input_data.destination_sheet_name or "Sheet1"

        if input_data.destination_mode == "create_new":
            title = input_data.new_spreadsheet_title or f"VIBE Export - {sheet.name}"
            new_sheet_resp = await self.oauth_service.create_spreadsheet(
                integration_id=integration.id,
                organization_id=organization_id,
                title=title,
            )
            dest_spreadsheet_id = new_sheet_resp.spreadsheet_id
        else:
            if not input_data.destination_spreadsheet_id:
                raise ValidationError("destination_spreadsheet_id is required for existing mode.")
            dest_spreadsheet_id = input_data.destination_spreadsheet_id

        # Update values in Google Sheets
        range_a1 = f"'{dest_sheet_name}'!A1"
        await self.oauth_service.update_sheet_values(
            integration_id=integration.id,
            organization_id=organization_id,
            external_spreadsheet_id=dest_spreadsheet_id,
            range_name=range_a1,
            values=matrix if matrix else [[""]],
        )

        spreadsheet_url = f"https://docs.google.com/spreadsheets/d/{dest_spreadsheet_id}/edit"

        return GoogleExportResponse(
            external_spreadsheet_id=dest_spreadsheet_id,
            external_sheet_name=dest_sheet_name,
            rows_exported=len(matrix),
            columns_exported=max_col_idx + 1 if matrix else 0,
            spreadsheet_url=spreadsheet_url,
        )


class SpreadsheetSyncService:
    """Manages synchronization mappings, background sync runs, and cell conflict resolution."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.sync_repo = SpreadsheetSyncRepository(session)
        self.integration_repo = IntegrationRepository(session)
        self.sheet_repo = SheetRepository(session)
        self.row_repo = SheetRowRepository(session)
        self.oauth_service = GoogleOAuthService(session)

    async def create_mapping(
        self, input_data: SyncMappingCreateInput, organization_id: uuid.UUID
    ) -> SpreadsheetSyncMapping:
        integration = await self.integration_repo.get_by_id(
            input_data.integration_id, organization_id
        )
        if not integration or integration.status != "connected":
            raise ValidationError("Valid and connected external integration required.")

        sheet = await self.sheet_repo.get_by_id(input_data.sheet_id, organization_id)
        if not sheet or sheet.workbook_id != input_data.workbook_id:
            raise ValidationError("Sheet does not belong to specified workbook or organization.")

        existing = await self.sync_repo.get_mapping_by_sheet(sheet.id, organization_id)
        if existing:
            raise ConflictError("A synchronization mapping already exists for this sheet.")

        async with transaction(self.session):
            mapping = SpreadsheetSyncMapping(
                organization_id=organization_id,
                integration_id=input_data.integration_id,
                workbook_id=input_data.workbook_id,
                sheet_id=input_data.sheet_id,
                external_spreadsheet_id=input_data.external_spreadsheet_id,
                external_sheet_id=input_data.external_sheet_id,
                external_sheet_name=input_data.external_sheet_name,
                direction=input_data.direction,
                status="idle",
                conflict_policy=input_data.conflict_policy,
                schedule_interval_minutes=input_data.schedule_interval_minutes,
            )
            created = await self.sync_repo.create_mapping(mapping)
        return created

    async def get_mapping(
        self, mapping_id: uuid.UUID, organization_id: uuid.UUID
    ) -> SpreadsheetSyncMapping:
        mapping = await self.sync_repo.get_mapping_by_id(mapping_id, organization_id)
        if not mapping:
            raise EntityNotFoundError("SpreadsheetSyncMapping", str(mapping_id))
        return mapping

    async def list_mappings(self, organization_id: uuid.UUID) -> Sequence[SpreadsheetSyncMapping]:
        return await self.sync_repo.list_mappings_by_org(organization_id)

    async def delete_mapping(self, mapping_id: uuid.UUID, organization_id: uuid.UUID) -> None:
        async with transaction(self.session):
            deleted = await self.sync_repo.delete_mapping(mapping_id, organization_id)
            if not deleted:
                raise EntityNotFoundError("SpreadsheetSyncMapping", str(mapping_id))

    async def list_runs(
        self, mapping_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Sequence[SpreadsheetSyncRun]:
        await self.get_mapping(mapping_id, organization_id)
        return await self.sync_repo.list_runs_by_mapping(mapping_id, organization_id)

    async def list_conflicts(
        self, mapping_id: uuid.UUID, organization_id: uuid.UUID, status: str | None = None
    ) -> Sequence[SpreadsheetSyncConflict]:
        await self.get_mapping(mapping_id, organization_id)
        return await self.sync_repo.list_conflicts_by_mapping(
            mapping_id, organization_id, status=status
        )

    async def execute_sync(
        self,
        mapping_id: uuid.UUID,
        organization_id: uuid.UUID,
        triggered_by_user_id: uuid.UUID | None = None,
        trigger_type: str = "manual",
    ) -> SpreadsheetSyncRun:
        """Executes a synchronization run between VIBE and Google Sheets."""
        mapping = await self.sync_repo.get_mapping_by_id(mapping_id, organization_id)
        if not mapping:
            raise EntityNotFoundError("SpreadsheetSyncMapping", str(mapping_id))

        if mapping.status == "syncing":
            raise ConflictError("A sync operation is already in progress for this mapping.")

        async with transaction(self.session):
            mapping.status = "syncing"
            run = SpreadsheetSyncRun(
                mapping_id=mapping.id,
                organization_id=organization_id,
                triggered_by_user_id=triggered_by_user_id,
                trigger_type=trigger_type,
                status="running",
                direction=mapping.direction,
                started_at=utc_now(),
            )
            run = await self.sync_repo.create_run(run)

        try:
            # 1. Fetch current VIBE state
            sheet_rows = await self.row_repo.list_rows_paginated(
                mapping.sheet_id, limit=5000, offset=0
            )
            vibe_cell_map: dict[tuple[int, str], dict[str, Any]] = {}
            for r in sheet_rows:
                for col_letter, cell_val in (r.cells or {}).items():
                    if (
                        cell_val.get("v") is not None
                        or cell_val.get("f") is not None
                        or cell_val.get("c") is not None
                    ):
                        vibe_cell_map[(r.row_index, col_letter)] = cell_val

            # 2. Fetch external state
            ext_sheet_name = mapping.external_sheet_name or "Sheet1"
            ext_values_resp = await self.oauth_service.get_sheet_values(
                integration_id=mapping.integration_id,
                organization_id=organization_id,
                external_spreadsheet_id=mapping.external_spreadsheet_id,
                range_name=f"'{ext_sheet_name}'!A1:ZZ5000",
            )
            ext_cell_map: dict[tuple[int, str], dict[str, Any]] = {}
            for r_idx, row in enumerate(ext_values_resp.values):
                for c_idx, val in enumerate(row):
                    if val is not None and val != "":
                        ext_cell_map[(r_idx, col_to_letter(c_idx))] = _parse_incoming_cell(val)

            rows_transferred = 0
            cells_updated = 0
            conflict_count = 0

            # 3. Synchronize according to direction
            if mapping.direction == "vibe_to_google":
                # Push VIBE to external
                max_r = max((k[0] for k in vibe_cell_map.keys()), default=-1)
                matrix: list[list[Any]] = [[] for _ in range(max_r + 1)]
                for (r_idx, col_letter), cell_dict in vibe_cell_map.items():
                    c_idx = letter_to_col(col_letter)
                    while len(matrix[r_idx]) <= c_idx:
                        matrix[r_idx].append("")
                    matrix[r_idx][c_idx] = _cell_display_value(cell_dict)
                    cells_updated += 1
                rows_transferred = len(matrix)
                await self.oauth_service.update_sheet_values(
                    integration_id=mapping.integration_id,
                    organization_id=organization_id,
                    external_spreadsheet_id=mapping.external_spreadsheet_id,
                    range_name=f"'{ext_sheet_name}'!A1",
                    values=matrix if matrix else [[""]],
                )

            elif mapping.direction == "google_to_vibe":
                # Pull external to VIBE
                rows_by_index: dict[int, dict[str, Any]] = {}
                for (r_idx, col_letter), cell_dict in ext_cell_map.items():
                    if r_idx not in rows_by_index:
                        rows_by_index[r_idx] = {}
                    rows_by_index[r_idx][col_letter] = cell_dict
                    cells_updated += 1

                async with transaction(self.session):
                    for r_idx, cells in rows_by_index.items():
                        await self.row_repo.upsert_row(
                            sheet_id=mapping.sheet_id,
                            organization_id=organization_id,
                            row_index=r_idx,
                            cells=cells,
                        )
                rows_transferred = len(rows_by_index)

            else:  # bidirectional
                all_coords = set(vibe_cell_map.keys()).union(set(ext_cell_map.keys()))
                detected_conflicts: list[SpreadsheetSyncConflict] = []
                vibe_updates: dict[int, dict[str, Any]] = {}
                ext_matrix_updates: dict[tuple[int, int], Any] = {}

                for coord in all_coords:
                    r_idx, col_letter = coord
                    v_cell = vibe_cell_map.get(coord)
                    e_cell = ext_cell_map.get(coord)

                    v_val = _cell_display_value(v_cell)
                    e_val = _cell_display_value(e_cell)

                    if v_val == e_val:
                        continue

                    # Both exist and differ: conflict
                    if v_cell is not None and e_cell is not None:
                        if mapping.conflict_policy == "vibe_wins":
                            # VIBE wins: update external
                            c_idx = letter_to_col(col_letter)
                            ext_matrix_updates[(r_idx, c_idx)] = v_val
                            cells_updated += 1
                        elif mapping.conflict_policy == "external_wins":
                            # External wins: update VIBE
                            if r_idx not in vibe_updates:
                                vibe_updates[r_idx] = {}
                            vibe_updates[r_idx][col_letter] = e_cell
                            cells_updated += 1
                        else:  # manual
                            detected_conflicts.append(
                                SpreadsheetSyncConflict(
                                    run_id=run.id,
                                    mapping_id=mapping.id,
                                    organization_id=organization_id,
                                    row_index=r_idx,
                                    column_letter=col_letter,
                                    vibe_value=v_cell,
                                    external_value=e_cell,
                                    status="detected",
                                )
                            )
                            conflict_count += 1
                    elif v_cell is not None and e_cell is None:
                        # Created in VIBE: push to external
                        c_idx = letter_to_col(col_letter)
                        ext_matrix_updates[(r_idx, c_idx)] = v_val
                        cells_updated += 1
                    elif e_cell is not None and v_cell is None:
                        # Created in external: pull to VIBE
                        if r_idx not in vibe_updates:
                            vibe_updates[r_idx] = {}
                        vibe_updates[r_idx][col_letter] = e_cell
                        cells_updated += 1

                # Apply VIBE updates if any
                if vibe_updates:
                    async with transaction(self.session):
                        for r_idx, c_dict in vibe_updates.items():
                            existing_r = await self.row_repo.get_row(mapping.sheet_id, r_idx)
                            merged_cells = dict(existing_r.cells or {}) if existing_r else {}
                            merged_cells.update(c_dict)
                            await self.row_repo.upsert_row(
                                sheet_id=mapping.sheet_id,
                                organization_id=organization_id,
                                row_index=r_idx,
                                cells=merged_cells,
                            )

                # Apply external matrix updates if any
                if ext_matrix_updates:
                    max_r = max((k[0] for k in ext_matrix_updates.keys()), default=-1)
                    # Re-fetch or overlay on existing values
                    ext_rows_matrix: list[list[Any]] = [list(r) for r in ext_values_resp.values]
                    while len(ext_rows_matrix) <= max_r:
                        ext_rows_matrix.append([])
                    for (r_idx, c_idx), val in ext_matrix_updates.items():
                        while len(ext_rows_matrix[r_idx]) <= c_idx:
                            ext_rows_matrix[r_idx].append("")
                        ext_rows_matrix[r_idx][c_idx] = val
                    await self.oauth_service.update_sheet_values(
                        integration_id=mapping.integration_id,
                        organization_id=organization_id,
                        external_spreadsheet_id=mapping.external_spreadsheet_id,
                        range_name=f"'{ext_sheet_name}'!A1",
                        values=ext_rows_matrix,
                    )

                if detected_conflicts:
                    async with transaction(self.session):
                        for conf in detected_conflicts:
                            await self.sync_repo.create_conflict(conf)

            # Compute state hash for checkpointing
            state_bytes = json.dumps(
                sorted([(k[0], k[1], str(v)) for k, v in vibe_cell_map.items()]),
                sort_keys=True,
            ).encode("utf-8")
            checkpoint_hash = hashlib.sha256(state_bytes).hexdigest()

            async with transaction(self.session):
                run.rows_transferred = rows_transferred
                run.cells_updated = cells_updated
                run.conflict_count = conflict_count
                run.completed_at = utc_now()

                if conflict_count > 0 and mapping.conflict_policy == "manual":
                    run.status = "conflict"
                    mapping.status = "conflict"
                else:
                    run.status = "completed"
                    mapping.status = "idle"
                    mapping.last_sync_at = utc_now()
                    mapping.last_checkpoint_hash = checkpoint_hash

                await self.sync_repo.update_run(run)
                await self.sync_repo.update_mapping(mapping)

            return run

        except Exception as e:
            async with transaction(self.session):
                run.status = "failed"
                run.error_message = str(e)
                run.completed_at = utc_now()
                mapping.status = "error"
                await self.sync_repo.update_run(run)
                await self.sync_repo.update_mapping(mapping)
            raise

    async def resolve_conflict(
        self,
        conflict_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        resolution_choice: str,
    ) -> SpreadsheetSyncConflict:
        """Resolve a detected cell conflict with 'keep_vibe' or 'keep_external'."""
        if resolution_choice not in ("keep_vibe", "keep_external"):
            raise ValidationError("resolution_choice must be 'keep_vibe' or 'keep_external'.")

        conflict = await self.sync_repo.get_conflict_by_id(conflict_id, organization_id)
        if not conflict:
            raise EntityNotFoundError("SpreadsheetSyncConflict", str(conflict_id))

        if conflict.status == "resolved":
            raise ConflictError("Conflict has already been resolved.")

        mapping = await self.sync_repo.get_mapping_by_id(conflict.mapping_id, organization_id)
        if not mapping:
            raise EntityNotFoundError("SpreadsheetSyncMapping", str(conflict.mapping_id))

        async with transaction(self.session):
            if resolution_choice == "keep_vibe":
                # Push vibe value to external
                ext_sheet_name = mapping.external_sheet_name or "Sheet1"
                cell_a1 = f"'{ext_sheet_name}'!{conflict.column_letter}{conflict.row_index + 1}"
                vibe_val = _cell_display_value(conflict.vibe_value)
                await self.oauth_service.update_sheet_values(
                    integration_id=mapping.integration_id,
                    organization_id=organization_id,
                    external_spreadsheet_id=mapping.external_spreadsheet_id,
                    range_name=cell_a1,
                    values=[[vibe_val]],
                )
            elif resolution_choice == "keep_external":
                # Apply external value to VIBE
                existing_r = await self.row_repo.get_row(mapping.sheet_id, conflict.row_index)
                cells = dict(existing_r.cells or {}) if existing_r else {}
                cells[conflict.column_letter] = conflict.external_value or {
                    "v": None,
                    "c": None,
                    "f": None,
                }
                await self.row_repo.upsert_row(
                    sheet_id=mapping.sheet_id,
                    organization_id=organization_id,
                    row_index=conflict.row_index,
                    cells=cells,
                )

            conflict.status = "resolved"
            conflict.resolution_choice = resolution_choice
            conflict.resolved_by_user_id = user_id
            conflict.resolved_at = utc_now()

            # Check if all conflicts for this mapping are now resolved
            unresolved = await self.sync_repo.count_unresolved_conflicts(mapping.id)
            if unresolved <= 1:  # including current one being resolved
                mapping.status = "idle"
                await self.sync_repo.update_mapping(mapping)

        return conflict
