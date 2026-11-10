from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, EntityNotFoundError, ValidationError
from app.db.transaction import transaction
from app.formula import (
    CellCoord,
    DependencyGraph,
    Evaluator,
    FormulaError,
    Parser,
    col_to_letter,
    extract_precedents,
    format_cell_ref,
    parse_cell_ref,
)
from app.models.spreadsheet import (
    Sheet,
    SheetCellHistory,
    SheetColumn,
    SheetRow,
    Workbook,
)
from app.repositories.spreadsheet import (
    DependencyRepository,
    HistoryRepository,
    SheetRepository,
    SheetRowRepository,
    WorkbookRepository,
)
from app.repositories.workspace import WorkspaceRepository
from app.schemas.spreadsheet import (
    BatchCellUpdateInput,
    BatchCellUpdateResponse,
    CellData,
    CellHistoryItemRead,
    SheetCreateInput,
    SheetRangeRead,
    SheetRead,
    SheetRowItem,
    SheetRowsPage,
    SheetUpdateInput,
    WorkbookCreateInput,
    WorkbookRead,
    WorkbookUpdateInput,
)

MAX_VIEWPORT_ROWS = 200
MAX_VIEWPORT_COLS = 50
MAX_BATCH_CELLS = 500
MAX_RECALCULATION_NODES = 1000
MAX_HISTORY_BATCHES = 100


def _parse_raw(val_str: str | None) -> Any:
    if val_str is None:
        return None
    try:
        return float(val_str) if "." in val_str else int(val_str)
    except ValueError:
        return val_str


class SpreadsheetService:
    """Service governing workbooks, sheets, virtualized grid data, formula calculation, and optimistic concurrency."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.workbook_repo = WorkbookRepository(session)
        self.sheet_repo = SheetRepository(session)
        self.row_repo = SheetRowRepository(session)
        self.dep_repo = DependencyRepository(session)
        self.history_repo = HistoryRepository(session)
        self.workspace_repo = WorkspaceRepository(session)

    # --------------------------------------------------------------------------
    # Workbook Lifecycle
    # --------------------------------------------------------------------------

    async def create_workbook(
        self,
        data: WorkbookCreateInput,
        organization_id: uuid.UUID,
        created_by_user_id: uuid.UUID | None,
    ) -> WorkbookRead:
        """Create a new workbook and an initial default sheet ('Sheet1')."""
        # Validate workspace exists and belongs to the same organization
        workspace = await self.workspace_repo.get_by_id(
            data.workspace_id, organization_id=organization_id
        )
        if not workspace:
            raise EntityNotFoundError("Workspace", str(data.workspace_id))

        async with transaction(self.session):
            wb = Workbook(
                organization_id=organization_id,
                workspace_id=data.workspace_id,
                name=data.name.strip(),
                description=data.description.strip() if data.description else None,
                created_by_user_id=created_by_user_id,
                version=1,
            )
            await self.workbook_repo.create(wb)

            # Create default Sheet1
            initial_sheet = Sheet(
                workbook_id=wb.id,
                organization_id=organization_id,
                name="Sheet1",
                position=0,
                row_count=1000,
                column_count=26,
                version=1,
            )
            await self.sheet_repo.create(initial_sheet)

            # Create standard 26 columns (A-Z)
            for c in range(26):
                col_letter = col_to_letter(c)
                col = SheetColumn(
                    sheet_id=initial_sheet.id,
                    column_index=c,
                    name=col_letter,
                    data_type="text",
                    width=100,
                )
                self.session.add(col)
            await self.session.flush()

        refreshed = await self.workbook_repo.get_by_id(wb.id, organization_id)
        assert refreshed is not None
        read_model = WorkbookRead.model_validate(refreshed)
        read_model.sheet_count = len(refreshed.sheets)
        return read_model

    async def get_workbook(
        self, workbook_id: uuid.UUID, organization_id: uuid.UUID
    ) -> WorkbookRead:
        wb = await self.workbook_repo.get_by_id(workbook_id, organization_id)
        if not wb:
            raise EntityNotFoundError("Workbook", str(workbook_id))
        read_model = WorkbookRead.model_validate(wb)
        read_model.sheet_count = len(wb.sheets)
        return read_model

    async def list_workbooks(
        self, organization_id: uuid.UUID, workspace_id: uuid.UUID | None = None
    ) -> list[WorkbookRead]:
        if workspace_id:
            wbs = await self.workbook_repo.list_by_workspace(workspace_id, organization_id)
        else:
            wbs = await self.workbook_repo.list_by_organization(organization_id)

        result: list[WorkbookRead] = []
        for wb in wbs:
            item = WorkbookRead.model_validate(wb)
            item.sheet_count = len(wb.sheets)
            result.append(item)
        return result

    async def update_workbook(
        self,
        workbook_id: uuid.UUID,
        organization_id: uuid.UUID,
        data: WorkbookUpdateInput,
    ) -> WorkbookRead:
        async with transaction(self.session):
            wb = await self.workbook_repo.get_by_id(workbook_id, organization_id)
            if not wb:
                raise EntityNotFoundError("Workbook", str(workbook_id))

            if data.name is not None:
                wb.name = data.name.strip()
            if data.description is not None:
                wb.description = data.description.strip()
            wb.version += 1
            await self.session.flush()

        refreshed = await self.workbook_repo.get_by_id(workbook_id, organization_id)
        assert refreshed is not None
        item = WorkbookRead.model_validate(refreshed)
        item.sheet_count = len(refreshed.sheets)
        return item

    async def delete_workbook(self, workbook_id: uuid.UUID, organization_id: uuid.UUID) -> None:
        async with transaction(self.session):
            deleted = await self.workbook_repo.delete(workbook_id, organization_id)
            if not deleted:
                raise EntityNotFoundError("Workbook", str(workbook_id))

    # --------------------------------------------------------------------------
    # Sheet Lifecycle
    # --------------------------------------------------------------------------

    async def create_sheet(
        self,
        workbook_id: uuid.UUID,
        organization_id: uuid.UUID,
        data: SheetCreateInput,
    ) -> SheetRead:
        wb = await self.workbook_repo.get_by_id(workbook_id, organization_id)
        if not wb:
            raise EntityNotFoundError("Workbook", str(workbook_id))

        existing_sheets = await self.sheet_repo.list_by_workbook(workbook_id, organization_id)
        if any(s.name.lower() == data.name.strip().lower() for s in existing_sheets):
            raise ValidationError(
                f"Sheet with name '{data.name.strip()}' already exists in this workbook"
            )

        async with transaction(self.session):
            new_position = len(existing_sheets)
            sheet = Sheet(
                workbook_id=workbook_id,
                organization_id=organization_id,
                name=data.name.strip(),
                position=new_position,
                row_count=data.row_count,
                column_count=data.column_count,
                version=1,
            )
            await self.sheet_repo.create(sheet)

            # Create default columns
            for c in range(min(data.column_count, 100)):
                col = SheetColumn(
                    sheet_id=sheet.id,
                    column_index=c,
                    name=col_to_letter(c),
                    data_type="text",
                    width=100,
                )
                self.session.add(col)
            await self.session.flush()

        refreshed = await self.sheet_repo.get_by_id(sheet.id, organization_id)
        assert refreshed is not None
        return SheetRead.model_validate(refreshed)

    async def get_sheet(self, sheet_id: uuid.UUID, organization_id: uuid.UUID) -> SheetRead:
        sheet = await self.sheet_repo.get_by_id(sheet_id, organization_id)
        if not sheet:
            raise EntityNotFoundError("Sheet", str(sheet_id))
        return SheetRead.model_validate(sheet)

    async def list_sheets(
        self, workbook_id: uuid.UUID, organization_id: uuid.UUID
    ) -> list[SheetRead]:
        wb = await self.workbook_repo.get_by_id(workbook_id, organization_id)
        if not wb:
            raise EntityNotFoundError("Workbook", str(workbook_id))
        sheets = await self.sheet_repo.list_by_workbook(workbook_id, organization_id)
        return [SheetRead.model_validate(s) for s in sheets]

    async def update_sheet(
        self,
        sheet_id: uuid.UUID,
        organization_id: uuid.UUID,
        data: SheetUpdateInput,
    ) -> SheetRead:
        async with transaction(self.session):
            sheet = await self.sheet_repo.get_by_id(sheet_id, organization_id)
            if not sheet:
                raise EntityNotFoundError("Sheet", str(sheet_id))

            if data.name is not None:
                sheet.name = data.name.strip()
            if data.row_count is not None:
                sheet.row_count = data.row_count
            if data.column_count is not None:
                sheet.column_count = data.column_count
            if data.position is not None:
                sheet.position = data.position

            sheet.version += 1
            await self.session.flush()

        refreshed = await self.sheet_repo.get_by_id(sheet.id, organization_id)
        assert refreshed is not None
        return SheetRead.model_validate(refreshed)

    # --------------------------------------------------------------------------
    # Grid Data Retrieval
    # --------------------------------------------------------------------------

    async def get_range(
        self,
        sheet_id: uuid.UUID,
        organization_id: uuid.UUID,
        start_row: int,
        end_row: int,
        start_col: int,
        end_col: int,
    ) -> SheetRangeRead:
        """Fetch a bounded window of cells for the virtualized grid."""
        sheet = await self.sheet_repo.get_by_id(sheet_id, organization_id)
        if not sheet:
            raise EntityNotFoundError("Sheet", str(sheet_id))

        # Enforce range limits to prevent unbounded memory consumption
        num_rows = end_row - start_row + 1
        num_cols = end_col - start_col + 1
        if num_rows > MAX_VIEWPORT_ROWS or num_cols > MAX_VIEWPORT_COLS:
            raise ValidationError(
                f"Requested range ({num_rows} rows x {num_cols} cols) exceeds maximum allowable viewport window ({MAX_VIEWPORT_ROWS} x {MAX_VIEWPORT_COLS})"
            )

        rows = await self.row_repo.get_rows_in_range(sheet_id, start_row, end_row)
        cells_map: dict[str, CellData] = {}
        row_versions: dict[int, int] = {}

        for row in rows:
            row_versions[row.row_index] = row.version
            cells_dict = row.cells or {}
            for col_idx in range(start_col, end_col + 1):
                col_key = f"c_{col_idx}"
                cell_ref = f"{col_to_letter(col_idx)}{row.row_index + 1}"
                if col_key in cells_dict:
                    raw_cell = cells_dict[col_key]
                    cells_map[cell_ref] = CellData(
                        raw=raw_cell.get("v"),
                        computed=raw_cell.get("c"),
                        formula=raw_cell.get("f"),
                        type=raw_cell.get("t", "blank"),
                        error_code=raw_cell.get("err"),
                        version=raw_cell.get("ver", 1),
                    )

        return SheetRangeRead(
            sheet_id=sheet_id,
            start_row=start_row,
            end_row=end_row,
            start_col=start_col,
            end_col=end_col,
            cells=cells_map,
            row_versions=row_versions,
        )

    async def get_rows_page(
        self,
        sheet_id: uuid.UUID,
        organization_id: uuid.UUID,
        offset: int = 0,
        limit: int = 100,
    ) -> SheetRowsPage:
        sheet = await self.sheet_repo.get_by_id(sheet_id, organization_id)
        if not sheet:
            raise EntityNotFoundError("Sheet", str(sheet_id))

        capped_limit = min(limit, 200)
        rows = await self.row_repo.list_rows_paginated(sheet_id, limit=capped_limit, offset=offset)
        total_rows = await self.row_repo.count_populated_rows(sheet_id)

        items: list[SheetRowItem] = []
        for r in rows:
            cells_map = {}
            for col_key, cell_val in (r.cells or {}).items():
                cells_map[col_key] = CellData(
                    raw=cell_val.get("v"),
                    computed=cell_val.get("c"),
                    formula=cell_val.get("f"),
                    type=cell_val.get("t", "blank"),
                    error_code=cell_val.get("err"),
                    version=cell_val.get("ver", 1),
                )
            items.append(SheetRowItem(row_index=r.row_index, version=r.version, cells=cells_map))

        return SheetRowsPage(
            sheet_id=sheet_id,
            total_rows=total_rows,
            offset=offset,
            limit=capped_limit,
            rows=items,
        )

    # --------------------------------------------------------------------------
    # Cell Mutation, Formula Recalculation & Optimistic Locking
    # --------------------------------------------------------------------------

    async def update_cells_batch(
        self,
        sheet_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None,
        data: BatchCellUpdateInput,
    ) -> BatchCellUpdateResponse:
        """Batch update cells, check optimistic concurrency, evaluate formulas, and recalculate dependents."""
        if len(data.updates) > MAX_BATCH_CELLS:
            raise ValidationError(
                f"Batch update exceeds safety limit of {MAX_BATCH_CELLS} cells (received {len(data.updates)})"
            )

        async with transaction(self.session):
            sheet = await self.sheet_repo.get_by_id_for_update(sheet_id, organization_id)
            if not sheet:
                raise EntityNotFoundError("Sheet", str(sheet_id))

            return await self._apply_mutation_batch(
                sheet=sheet,
                user_id=user_id,
                updates=data.updates,
                record_history=True,
            )

    async def _apply_mutation_batch(
        self,
        sheet: Sheet,
        user_id: uuid.UUID | None,
        updates: list[Any],
        record_history: bool = True,
    ) -> BatchCellUpdateResponse:
        """Apply cell updates, verify version concurrency, evaluate formulas, and commit rows."""
        sheet_id = sheet.id
        organization_id = sheet.organization_id

        # 1. Parse all modified coordinates and identify affected rows
        # Stores: cell_ref -> (coord, value, expected_cell_version, expected_row_version)
        modified_coords: dict[str, tuple[CellCoord, Any, int | None, int | None]] = {}
        affected_row_indices: set[int] = set()

        for edit in updates:
            coord = parse_cell_ref(edit.cell_ref)
            cell_ref = format_cell_ref(coord)
            exp_cell_ver = getattr(edit, "expected_cell_version", None)
            exp_row_ver = getattr(edit, "expected_row_version", None)
            modified_coords[cell_ref] = (coord, edit.value, exp_cell_ver, exp_row_ver)
            affected_row_indices.add(coord.row)

        # 2. Load entire sheet formula dependency graph
        dep_records = await self.dep_repo.get_all_for_sheet(sheet_id)
        dep_graph = DependencyGraph()
        for dep in dep_records:
            current_precs = dep_graph.precedents[dep.dependent_cell_ref]
            current_precs.add(dep.precedent_cell_ref)
            dep_graph.set_dependencies(dep.dependent_cell_ref, current_precs)

        # 3. Identify all rows required for evaluation (modified rows, downstream formulas, and precedents)
        all_needed_row_indices: set[int] = set(affected_row_indices)

        for _cell_ref, (_coord, raw_input, _, _) in modified_coords.items():
            if isinstance(raw_input, str) and raw_input.strip().startswith("="):
                try:
                    ast = Parser.parse_formula(raw_input.strip())
                    for prec in extract_precedents(ast):
                        try:
                            all_needed_row_indices.add(parse_cell_ref(prec).row)
                        except Exception:
                            pass
                except Exception:
                    pass

        downstream_cells = dep_graph.get_all_downstream(list(modified_coords.keys()))
        for ds_ref in downstream_cells:
            try:
                all_needed_row_indices.add(parse_cell_ref(ds_ref).row)
            except Exception:
                pass
            for p_cell in dep_graph.precedents.get(ds_ref, set()):
                try:
                    all_needed_row_indices.add(parse_cell_ref(p_cell).row)
                except Exception:
                    pass

        # Fetch all needed rows from database
        rows_by_index: dict[int, SheetRow] = {
            r.row_index: r
            for r in await self.row_repo.get_rows(sheet_id, list(all_needed_row_indices))
        }

        # 4. Verify optimistic concurrency at per-cell and per-row granularity
        for cell_ref, (
            coord,
            _,
            expected_cell_version,
            expected_row_version,
        ) in modified_coords.items():
            existing_row = rows_by_index.get(coord.row)
            if expected_cell_version is not None:
                col_key = f"c_{coord.col}"
                existing_cell_dict = (
                    existing_row.cells.get(col_key, {})
                    if existing_row and existing_row.cells
                    else {}
                )
                curr_cell_version = existing_cell_dict.get("ver", 1)
                if curr_cell_version != expected_cell_version:
                    raise ConflictError(
                        f"Concurrent edit conflict on {cell_ref}: expected cell version {expected_cell_version}, but found {curr_cell_version}"
                    )
            if expected_row_version is not None:
                curr_row_version = existing_row.version if existing_row else 1
                if curr_row_version != expected_row_version:
                    raise ConflictError(
                        f"Concurrent edit conflict on {cell_ref}: expected row version {expected_row_version}, but found {curr_row_version}"
                    )

        # In-memory working grid cache (row, col) -> raw / computed value
        grid_cache: dict[tuple[int, int], dict[str, Any]] = {}

        def get_cell_from_row_dict(r_idx: int, c_idx: int) -> dict[str, Any]:
            key = (r_idx, c_idx)
            if key in grid_cache:
                return grid_cache[key]
            row_obj = rows_by_index.get(r_idx)
            if row_obj and row_obj.cells:
                col_key = f"c_{c_idx}"
                cell_dict = row_obj.cells.get(col_key, {})
                grid_cache[key] = dict(cell_dict)
                return grid_cache[key]
            grid_cache[key] = {}
            return grid_cache[key]

        def cell_value_getter(coord: CellCoord) -> Any:
            c_dict = get_cell_from_row_dict(coord.row, coord.col)
            if "c" in c_dict and c_dict["c"] is not None:
                return c_dict["c"]
            return c_dict.get("v")

        evaluator = Evaluator(cell_getter=cell_value_getter)

        # 5. Apply user edits and record history
        updated_cell_results: dict[str, CellData] = {}
        new_or_updated_dependencies: dict[str, set[str]] = {}
        batch_id = uuid.uuid4()

        for cell_ref, (coord, raw_input, _, _) in modified_coords.items():
            existing_cell = get_cell_from_row_dict(coord.row, coord.col)

            prev_raw = existing_cell.get("v")
            prev_comp = existing_cell.get("c")
            prev_formula = existing_cell.get("f")
            cell_ver_before = existing_cell.get("ver", 1)
            cell_ver_after = cell_ver_before + 1
            row_obj = rows_by_index.get(coord.row)
            ver_before = row_obj.version if row_obj else 1

            is_formula = isinstance(raw_input, str) and raw_input.strip().startswith("=")
            if is_formula:
                formula_str = raw_input.strip()
                ast = Parser.parse_formula(formula_str)
                precedents = extract_precedents(ast)

                if cell_ref in precedents:
                    from app.formula.types import CYCLE_ERROR

                    eval_result = evaluator.evaluate(ast)
                    eval_result.type = "error"
                    eval_result.error_code = CYCLE_ERROR
                    eval_result.value = CYCLE_ERROR
                else:
                    eval_result = evaluator.evaluate(ast)

                new_cell_dict = {
                    "v": None,
                    "f": formula_str,
                    "c": eval_result.value,
                    "t": eval_result.type,
                    "err": eval_result.error_code,
                    "ver": cell_ver_after,
                }
                new_or_updated_dependencies[cell_ref] = precedents
            else:
                if raw_input is None or raw_input == "":
                    new_cell_dict = {}
                else:
                    val_type = (
                        "number"
                        if isinstance(raw_input, (int, float)) and not isinstance(raw_input, bool)
                        else ("boolean" if isinstance(raw_input, bool) else "string")
                    )
                    new_cell_dict = {
                        "v": raw_input,
                        "c": raw_input,
                        "f": None,
                        "t": val_type,
                        "err": None,
                        "ver": cell_ver_after,
                    }
                new_or_updated_dependencies[cell_ref] = set()

            grid_cache[(coord.row, coord.col)] = new_cell_dict
            updated_cell_results[cell_ref] = CellData(
                raw=new_cell_dict.get("v"),
                computed=new_cell_dict.get("c"),
                formula=new_cell_dict.get("f"),
                type=new_cell_dict.get("t", "blank"),
                error_code=new_cell_dict.get("err"),
                version=cell_ver_after,
            )

            if record_history:
                history_entry = SheetCellHistory(
                    sheet_id=sheet_id,
                    organization_id=organization_id,
                    batch_id=batch_id,
                    cell_ref=cell_ref,
                    row_index=coord.row,
                    column_index=coord.col,
                    previous_raw_value=str(prev_raw) if prev_raw is not None else None,
                    previous_computed_value=str(prev_comp) if prev_comp is not None else None,
                    previous_formula=prev_formula,
                    new_raw_value=str(new_cell_dict.get("v"))
                    if new_cell_dict.get("v") is not None
                    else None,
                    new_computed_value=str(new_cell_dict.get("c"))
                    if new_cell_dict.get("c") is not None
                    else None,
                    new_formula=new_cell_dict.get("f"),
                    cell_version_before=cell_ver_before,
                    cell_version_after=cell_ver_after,
                    row_version_before=ver_before,
                    row_version_after=ver_before + 1,
                    edited_by_user_id=user_id,
                    is_undone=False,
                )
                self.session.add(history_entry)

        # 6. Update formula dependency graph
        for cell_ref, precs in new_or_updated_dependencies.items():
            dep_graph.set_dependencies(cell_ref, precs)
            await self.dep_repo.sync_cell_dependencies(
                sheet_id=sheet_id,
                organization_id=organization_id,
                dependent_cell_ref=cell_ref,
                precedent_refs=precs,
            )

        # 7. Topological recalculation of all downstream dependent formulas
        try:
            recalc_order = dep_graph.get_recalculation_order(list(modified_coords.keys()))
        except FormulaError as fe:
            if fe.code == CYCLE_ERROR:
                # Identify cycle cells and mark them with #CYCLE!
                downstream = dep_graph.get_all_downstream(list(modified_coords.keys()))
                cyclic_refs = [
                    ref for ref in downstream if dep_graph.detect_cycle_from(ref) is not None
                ]
                for m_ref in modified_coords.keys():
                    if dep_graph.detect_cycle_from(m_ref) is not None and m_ref not in cyclic_refs:
                        cyclic_refs.append(m_ref)

                cyclic_coords = {ref: parse_cell_ref(ref) for ref in cyclic_refs}
                missing_cyclic_rows = [
                    c.row for c in cyclic_coords.values() if c.row not in rows_by_index
                ]
                if missing_cyclic_rows:
                    extra_cyclic_rows = await self.row_repo.get_rows(
                        sheet_id, list(set(missing_cyclic_rows))
                    )
                    for er in extra_cyclic_rows:
                        rows_by_index[er.row_index] = er

                for c_ref, c_coord in cyclic_coords.items():
                    c_cell = get_cell_from_row_dict(c_coord.row, c_coord.col)
                    c_cell["c"] = CYCLE_ERROR
                    c_cell["t"] = "error"
                    c_cell["err"] = CYCLE_ERROR
                    grid_cache[(c_coord.row, c_coord.col)] = c_cell
                    updated_cell_results[c_ref] = CellData(
                        raw=c_cell.get("v"),
                        computed=CYCLE_ERROR,
                        formula=c_cell.get("f"),
                        type="error",
                        error_code=CYCLE_ERROR,
                        version=c_cell.get("ver", 1),
                    )
                    affected_row_indices.add(c_coord.row)
                recalc_order = []
            else:
                raise
        if len(recalc_order) > MAX_RECALCULATION_NODES:
            raise ValidationError(
                f"Recalculation cascade ({len(recalc_order)} cells) exceeds safety limit of {MAX_RECALCULATION_NODES} cells"
            )

        downstream_coords: dict[str, CellCoord] = {ref: parse_cell_ref(ref) for ref in recalc_order}
        missing_row_indices = [
            c.row for c in downstream_coords.values() if c.row not in rows_by_index
        ]
        if missing_row_indices:
            extra_rows = await self.row_repo.get_rows(sheet_id, list(set(missing_row_indices)))
            for er in extra_rows:
                rows_by_index[er.row_index] = er

        for dep_cell_ref in recalc_order:
            d_coord = downstream_coords[dep_cell_ref]
            d_cell = get_cell_from_row_dict(d_coord.row, d_coord.col)
            formula_str = d_cell.get("f")
            if formula_str:
                ast = Parser.parse_formula(formula_str)
                eval_res = evaluator.evaluate(ast)
                d_cell["c"] = eval_res.value
                d_cell["t"] = eval_res.type
                d_cell["err"] = eval_res.error_code
                grid_cache[(d_coord.row, d_coord.col)] = d_cell

                updated_cell_results[dep_cell_ref] = CellData(
                    raw=d_cell.get("v"),
                    computed=d_cell.get("c"),
                    formula=d_cell.get("f"),
                    type=d_cell.get("t", "blank"),
                    error_code=d_cell.get("err"),
                    version=d_cell.get("ver", 1),
                )
                affected_row_indices.add(d_coord.row)

        # 8. Persist updated rows into database
        final_row_versions: dict[int, int] = {}
        for r_idx in affected_row_indices:
            row_obj = rows_by_index.get(r_idx)
            existing_cells = dict(row_obj.cells) if row_obj and row_obj.cells else {}

            for (cached_r, cached_c), cell_data in grid_cache.items():
                if cached_r == r_idx:
                    col_key = f"c_{cached_c}"
                    if cell_data:
                        existing_cells[col_key] = cell_data
                    else:
                        existing_cells.pop(col_key, None)

            saved_row = await self.row_repo.upsert_row(
                sheet_id=sheet_id,
                organization_id=organization_id,
                row_index=r_idx,
                cells=existing_cells,
            )
            final_row_versions[r_idx] = saved_row.version

        # 9. Maintain history bounds and purge redoable states on new edits
        if record_history:
            await self.history_repo.purge_redo_history(sheet_id)
            await self.history_repo.prune_history(sheet_id, max_batches=MAX_HISTORY_BATCHES)

        return BatchCellUpdateResponse(
            sheet_id=sheet_id,
            updated_cells=updated_cell_results,
            recalculated_count=len(recalc_order),
            row_versions=final_row_versions,
        )

    # --------------------------------------------------------------------------
    # Undo / Redo Mechanism
    # --------------------------------------------------------------------------

    async def undo(
        self, sheet_id: uuid.UUID, organization_id: uuid.UUID, user_id: uuid.UUID | None
    ) -> BatchCellUpdateResponse:
        """Revert the most recent batch edit and recalculate affected formulas."""
        async with transaction(self.session):
            sheet = await self.sheet_repo.get_by_id_for_update(sheet_id, organization_id)
            if not sheet:
                raise EntityNotFoundError("Sheet", str(sheet_id))

            history_entries = await self.history_repo.get_latest_undoable_batch(sheet_id)
            if not history_entries:
                raise ValidationError("Nothing to undo on this sheet")

            # Revision conflict policy: ensure no cell in this batch was modified by a newer active edit
            for entry in history_entries:
                has_newer = await self.history_repo.has_newer_active_edit(
                    sheet_id=sheet_id,
                    cell_ref=entry.cell_ref,
                    after_timestamp=entry.created_at,
                )
                if has_newer:
                    raise ConflictError(
                        f"Cannot undo: cell {entry.cell_ref} was modified by a subsequent edit"
                    )

            # Mark all entries in this batch as undone
            for entry in history_entries:
                entry.is_undone = True
            await self.session.flush()

            # Restore previous values
            from app.schemas.spreadsheet import CellEditInput

            restore_updates = []
            for entry in history_entries:
                restore_val = (
                    entry.previous_formula
                    if entry.previous_formula
                    else _parse_raw(entry.previous_raw_value)
                )
                restore_updates.append(CellEditInput(cell_ref=entry.cell_ref, value=restore_val))

            return await self._apply_mutation_batch(
                sheet=sheet,
                user_id=user_id,
                updates=restore_updates,
                record_history=False,
            )

    async def redo(
        self, sheet_id: uuid.UUID, organization_id: uuid.UUID, user_id: uuid.UUID | None
    ) -> BatchCellUpdateResponse:
        """Reapply the most recently undone batch edit."""
        async with transaction(self.session):
            sheet = await self.sheet_repo.get_by_id_for_update(sheet_id, organization_id)
            if not sheet:
                raise EntityNotFoundError("Sheet", str(sheet_id))

            history_entries = await self.history_repo.get_latest_redoable_batch(sheet_id)
            if not history_entries:
                raise ValidationError("Nothing to redo on this sheet")

            # Mark all entries in this batch as no longer undone
            for entry in history_entries:
                entry.is_undone = False
            await self.session.flush()

            from app.schemas.spreadsheet import CellEditInput

            redo_updates = []
            for entry in history_entries:
                restore_val = (
                    entry.new_formula if entry.new_formula else _parse_raw(entry.new_raw_value)
                )
                redo_updates.append(CellEditInput(cell_ref=entry.cell_ref, value=restore_val))

            return await self._apply_mutation_batch(
                sheet=sheet,
                user_id=user_id,
                updates=redo_updates,
                record_history=False,
            )

    async def list_history(
        self, sheet_id: uuid.UUID, organization_id: uuid.UUID, limit: int = 50, offset: int = 0
    ) -> list[CellHistoryItemRead]:
        sheet = await self.sheet_repo.get_by_id(sheet_id, organization_id)
        if not sheet:
            raise EntityNotFoundError("Sheet", str(sheet_id))
        records = await self.history_repo.list_history(sheet_id, limit=limit, offset=offset)
        return [CellHistoryItemRead.model_validate(r) for r in records]
