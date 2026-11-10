"""Pydantic schemas for Analytics API requests and responses."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class OperationParameterField(BaseModel):
    name: str
    type: str
    description: str
    required: bool
    default: Any = None


class OperationCatalogueItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    name: str
    description: str
    required_permission: str
    max_input_rows: int
    max_input_cols: int
    timeout_seconds: int
    parameters_schema: dict[str, Any]


class AnalyticsJobCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_workbook_id: uuid.UUID
    source_sheet_id: uuid.UUID
    operation_id: str = Field(..., min_length=1, max_length=100)
    parameters: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=100)


class AnalyticsJobRead(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    source_workbook_id: uuid.UUID
    source_sheet_id: uuid.UUID
    requested_by_user_id: uuid.UUID | None
    operation_id: str
    parameters: dict[str, Any]
    status: str
    progress_percent: int
    current_stage: str | None
    source_sheet_version: int
    result_id: uuid.UUID | None
    error_code: str | None
    safe_error_message: str | None
    attempt_count: int
    max_attempts: int
    claim_generation: int = 0
    snapshot_row_count: int | None = None
    snapshot_col_count: int | None = None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class AnalyticsJobListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[AnalyticsJobRead]
    total: int
    limit: int
    offset: int


class AnalyticsResultRead(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID
    job_id: uuid.UUID
    organization_id: uuid.UUID
    result_type: str
    result_payload: dict[str, Any]
    artifact_size_bytes: int | None
    row_count: int | None
    column_count: int | None
    has_artifact: bool
    published_sheet_id: uuid.UUID | None = None
    created_at: datetime


class PublishToSheetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination_workbook_id: uuid.UUID
    sheet_name: str | None = Field(default=None, min_length=1, max_length=255)
    idempotency_key: str | None = Field(default=None, max_length=100)


class PublishToSheetResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workbook_id: uuid.UUID
    sheet_id: uuid.UUID
    sheet_name: str
    rows_written: int
    columns_written: int
