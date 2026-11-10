"""Pydantic schemas for governed user-defined Python analytics scripts and runs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PythonScriptCreateInput(BaseModel):
    """Payload to create a new analytical Python script."""

    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    code: str = Field(..., min_length=1)
    parameters_schema: dict[str, Any] = Field(default_factory=dict)


class PythonScriptUpdateInput(BaseModel):
    """Payload to edit a script definition or code (increments version if code changes)."""

    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    code: str | None = Field(None, min_length=1)
    parameters_schema: dict[str, Any] | None = None


class PythonScriptRead(BaseModel):
    """Public read model for a Python script."""

    id: uuid.UUID
    organization_id: uuid.UUID
    created_by_user_id: uuid.UUID | None = None
    title: str
    description: str | None = None
    code: str
    parameters_schema: dict[str, Any] = Field(default_factory=dict)
    version: int
    code_hash: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PythonScriptRunInput(BaseModel):
    """Payload to execute a Python script against spreadsheet data."""

    source_sheet_id: uuid.UUID | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class PythonScriptRunRead(BaseModel):
    """Audit log and output representation of a script run."""

    id: uuid.UUID
    script_id: uuid.UUID
    organization_id: uuid.UUID
    requested_by_user_id: uuid.UUID | None = None
    source_sheet_id: uuid.UUID | None = None
    status: str
    input_cell_count: int
    execution_time_ms: int | None = None
    memory_peak_mb: float | None = None
    stdout_logs: str | None = None
    stderr_logs: str | None = None
    error_message: str | None = None
    result_payload: dict[str, Any] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
