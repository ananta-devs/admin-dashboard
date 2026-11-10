from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkbookCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    workspace_id: uuid.UUID


class WorkbookUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)


class WorkbookRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    description: str | None
    created_by_user_id: uuid.UUID | None
    version: int
    sheet_count: int = 0
    created_at: datetime
    updated_at: datetime


class SheetCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=255)
    row_count: int = Field(default=1000, ge=1, le=100000)
    column_count: int = Field(default=26, ge=1, le=256)


class SheetUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    row_count: int | None = Field(default=None, ge=1, le=100000)
    column_count: int | None = Field(default=None, ge=1, le=256)
    position: int | None = Field(default=None, ge=0)


class SheetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workbook_id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    position: int
    row_count: int
    column_count: int
    version: int
    created_at: datetime
    updated_at: datetime


class CellData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    raw: Any = None
    computed: Any = None
    formula: str | None = None
    type: str = "blank"  # 'number', 'string', 'boolean', 'error', 'blank'
    error_code: str | None = None
    version: int = 1


class CellEditInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cell_ref: str = Field(..., min_length=2, max_length=20)
    value: Any = None
    expected_cell_version: int | None = None
    expected_row_version: int | None = None


class BatchCellUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updates: list[CellEditInput] = Field(..., min_length=1, max_length=500)


class BatchCellUpdateResponse(BaseModel):
    sheet_id: uuid.UUID
    updated_cells: dict[str, CellData]
    recalculated_count: int
    row_versions: dict[int, int]


class SheetRangeRead(BaseModel):
    sheet_id: uuid.UUID
    start_row: int
    end_row: int
    start_col: int
    end_col: int
    cells: dict[str, CellData]
    row_versions: dict[int, int]


class SheetRowItem(BaseModel):
    row_index: int
    version: int
    cells: dict[str, CellData]


class SheetRowsPage(BaseModel):
    sheet_id: uuid.UUID
    total_rows: int
    offset: int
    limit: int
    rows: list[SheetRowItem]


class CellHistoryItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sheet_id: uuid.UUID
    batch_id: uuid.UUID
    cell_ref: str
    row_index: int
    column_index: int
    previous_raw_value: str | None
    previous_computed_value: str | None
    previous_formula: str | None
    new_raw_value: str | None
    new_computed_value: str | None
    new_formula: str | None
    cell_version_before: int
    cell_version_after: int
    row_version_before: int
    row_version_after: int
    edited_by_user_id: uuid.UUID | None
    is_undone: bool
    created_at: datetime
