"""Service layer for governed user-defined Python analytics scripts and sandbox runs."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.python_sandbox import PythonSandboxRunner, validate_python_ast
from app.core.config import settings
from app.core.exceptions import EntityNotFoundError, ForbiddenError, ValidationError
from app.db.base import utc_now
from app.db.transaction import transaction
from app.formula import col_to_letter, letter_to_col
from app.models.script import PythonScript, PythonScriptRun
from app.repositories.script import PythonScriptRepository, PythonScriptRunRepository
from app.repositories.spreadsheet import SheetRepository, SheetRowRepository
from app.schemas.script import (
    PythonScriptCreateInput,
    PythonScriptRunInput,
    PythonScriptUpdateInput,
)
from app.services.sync import _cell_display_value


def _compute_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class PythonScriptService:
    """Manages Python script definitions and governed subprocess execution."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.script_repo = PythonScriptRepository(session)
        self.run_repo = PythonScriptRunRepository(session)
        self.sheet_repo = SheetRepository(session)
        self.row_repo = SheetRowRepository(session)

    async def create_script(
        self,
        input_data: PythonScriptCreateInput,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> PythonScript:
        # Check AST for syntax errors upfront
        ast_err = validate_python_ast(input_data.code)
        if ast_err and "Syntax Error" in ast_err:
            raise ValidationError(ast_err)

        code_hash = _compute_sha256(input_data.code)

        async with transaction(self.session):
            script = PythonScript(
                organization_id=organization_id,
                created_by_user_id=user_id,
                title=input_data.title,
                description=input_data.description,
                code=input_data.code,
                parameters_schema=input_data.parameters_schema,
                version=1,
                code_hash=code_hash,
            )
            created = await self.script_repo.create(script)
        return created

    async def get_script(
        self, script_id: uuid.UUID, organization_id: uuid.UUID
    ) -> PythonScript:
        script = await self.script_repo.get_by_id(script_id, organization_id)
        if not script:
            raise EntityNotFoundError("PythonScript", str(script_id))
        return script

    async def list_scripts(
        self, organization_id: uuid.UUID
    ) -> Sequence[PythonScript]:
        return await self.script_repo.list_by_organization(organization_id)

    async def update_script(
        self,
        script_id: uuid.UUID,
        organization_id: uuid.UUID,
        input_data: PythonScriptUpdateInput,
        user_id: uuid.UUID | None = None,
        can_manage_any: bool = False,
    ) -> PythonScript:
        script = await self.get_script(script_id, organization_id)
        if script.created_by_user_id and user_id and script.created_by_user_id != user_id and not can_manage_any:
            raise ForbiddenError("You do not have permission to modify another user's script.")

        async with transaction(self.session):
            if input_data.title is not None:
                script.title = input_data.title
            if input_data.description is not None:
                script.description = input_data.description
            if input_data.parameters_schema is not None:
                script.parameters_schema = input_data.parameters_schema
            if input_data.code is not None and input_data.code != script.code:
                ast_err = validate_python_ast(input_data.code)
                if ast_err and "Syntax Error" in ast_err:
                    raise ValidationError(ast_err)
                script.code = input_data.code
                script.code_hash = _compute_sha256(input_data.code)
                script.version += 1
            await self.script_repo.update(script)
        return script

    async def delete_script(
        self,
        script_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        can_manage_any: bool = False,
    ) -> None:
        script = await self.get_script(script_id, organization_id)
        if script.created_by_user_id and user_id and script.created_by_user_id != user_id and not can_manage_any:
            raise ForbiddenError("You do not have permission to delete another user's script.")

        async with transaction(self.session):
            deleted = await self.script_repo.delete(script_id, organization_id)
            if not deleted:
                raise EntityNotFoundError("PythonScript", str(script_id))

    async def execute_script(
        self,
        script_id: uuid.UUID,
        organization_id: uuid.UUID,
        input_data: PythonScriptRunInput,
        user_id: uuid.UUID | None = None,
    ) -> PythonScriptRun:
        """Executes a governed script run in a secured subprocess sandbox."""
        script = await self.get_script(script_id, organization_id)

        # 1. Load tabular dataset if source_sheet_id provided
        dataset: dict[str, Any] = {"headers": [], "records": []}
        cell_count = 0

        if input_data.source_sheet_id:
            sheet = await self.sheet_repo.get_by_id(input_data.source_sheet_id, organization_id)
            if not sheet:
                raise EntityNotFoundError("Sheet", str(input_data.source_sheet_id))

            rows = await self.row_repo.list_rows_paginated(sheet.id, limit=5000, offset=0)
            if rows:
                raw_matrix: dict[int, dict[int, Any]] = {}
                max_c_idx = 0
                for r in rows:
                    r_cells: dict[int, Any] = {}
                    for col_letter, c_dict in (r.cells or {}).items():
                        c_idx = letter_to_col(col_letter)
                        if c_idx > max_c_idx:
                            max_c_idx = c_idx
                        r_cells[c_idx] = _cell_display_value(c_dict)
                        cell_count += 1
                    raw_matrix[r.row_index] = r_cells

                if cell_count > settings.PYTHON_SANDBOX_MAX_CELLS:
                    raise ValidationError(
                        f"Input dataset cell count ({cell_count}) exceeds maximum sandbox limit of {settings.PYTHON_SANDBOX_MAX_CELLS} cells."
                    )

                headers = [col_to_letter(c) for c in range(max_c_idx + 1)]
                # If first row contains strings, treat as headers
                header_row = 0
                if 0 in raw_matrix:
                    headers = [str(raw_matrix[0].get(c, col_to_letter(c))) for c in range(max_c_idx + 1)]
                    header_row = 1

                records: list[dict[str, Any]] = []
                for r_idx in sorted(raw_matrix.keys()):
                    if r_idx < header_row:
                        continue
                    r_dict: dict[str, Any] = {}
                    for c_idx, h in enumerate(headers):
                        r_dict[h] = raw_matrix[r_idx].get(c_idx, None)
                    records.append(r_dict)

                dataset = {"headers": headers, "records": records}

        # 2. Record initial run
        async with transaction(self.session):
            run = PythonScriptRun(
                script_id=script.id,
                organization_id=organization_id,
                requested_by_user_id=user_id,
                source_sheet_id=input_data.source_sheet_id,
                status="running",
                input_cell_count=cell_count,
                started_at=utc_now(),
            )
            run = await self.run_repo.create(run)

        # 3. Execute in governed sandbox
        runner = PythonSandboxRunner(
            timeout_seconds=settings.PYTHON_SANDBOX_TIMEOUT_SECONDS,
            memory_limit_mb=settings.PYTHON_SANDBOX_MAX_MEMORY_MB,
        )
        sandbox_res = runner.run(
            code_str=script.code,
            input_data=dataset,
            parameters=input_data.parameters,
        )

        # 4. Save results and audit execution
        async with transaction(self.session):
            run.status = sandbox_res.status
            run.execution_time_ms = sandbox_res.execution_time_ms
            run.memory_peak_mb = sandbox_res.memory_peak_mb
            run.stdout_logs = sandbox_res.stdout
            run.stderr_logs = sandbox_res.stderr
            run.error_message = sandbox_res.error_message
            run.result_payload = sandbox_res.result_payload
            run.completed_at = utc_now()
            await self.run_repo.update(run)

        return run

    async def list_runs(
        self, script_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Sequence[PythonScriptRun]:
        await self.get_script(script_id, organization_id)
        return await self.run_repo.list_by_script(script_id, organization_id)

    async def get_run(
        self, run_id: uuid.UUID, organization_id: uuid.UUID
    ) -> PythonScriptRun:
        run = await self.run_repo.get_by_id(run_id, organization_id)
        if not run:
            raise EntityNotFoundError("PythonScriptRun", str(run_id))
        return run
