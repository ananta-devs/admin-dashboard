"""Pydantic schemas for Google Sheets Import/Export and Spreadsheet Synchronization."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class GoogleImportInput(BaseModel):
    """Payload to import an external Google worksheet into a VIBE workbook."""

    integration_id: uuid.UUID
    external_spreadsheet_id: str
    external_sheet_name: str = "Sheet1"
    target_workbook_id: uuid.UUID
    target_sheet_name: str | None = None


class GoogleImportResponse(BaseModel):
    """Result summary of a successful Google Sheets import operation."""

    workbook_id: uuid.UUID
    sheet_id: uuid.UUID
    sheet_name: str
    rows_imported: int
    columns_imported: int
    cells_imported: int


class GoogleExportInput(BaseModel):
    """Payload to export a VIBE sheet into Google Sheets."""

    integration_id: uuid.UUID
    source_sheet_id: uuid.UUID
    destination_mode: str = "create_new"  # 'create_new' or 'existing'
    destination_spreadsheet_id: str | None = None
    destination_sheet_name: str | None = "Sheet1"
    new_spreadsheet_title: str | None = None


class GoogleExportResponse(BaseModel):
    """Result summary of a successful VIBE-to-Google export operation."""

    external_spreadsheet_id: str
    external_sheet_name: str
    rows_exported: int
    columns_exported: int
    spreadsheet_url: str


class SyncMappingCreateInput(BaseModel):
    """Input payload to establish a persistent spreadsheet synchronization mapping."""

    integration_id: uuid.UUID
    workbook_id: uuid.UUID
    sheet_id: uuid.UUID
    external_spreadsheet_id: str
    external_sheet_id: str = "0"
    external_sheet_name: str = "Sheet1"
    direction: str = "bidirectional"  # 'vibe_to_google', 'google_to_vibe', 'bidirectional'
    conflict_policy: str = "manual"  # 'manual', 'vibe_wins', 'external_wins'
    schedule_interval_minutes: int | None = None


class SyncMappingRead(BaseModel):
    """Public representation of a configured spreadsheet sync mapping."""

    id: uuid.UUID
    organization_id: uuid.UUID
    integration_id: uuid.UUID
    workbook_id: uuid.UUID
    sheet_id: uuid.UUID
    external_spreadsheet_id: str
    external_sheet_id: str
    external_sheet_name: str | None = None
    direction: str
    status: str
    conflict_policy: str
    last_sync_at: datetime | None = None
    last_checkpoint_vibe_version: int
    last_checkpoint_external_version: str | None = None
    schedule_interval_minutes: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SyncRunRead(BaseModel):
    """Detailed execution record of a synchronization run."""

    id: uuid.UUID
    mapping_id: uuid.UUID
    organization_id: uuid.UUID
    triggered_by_user_id: uuid.UUID | None = None
    trigger_type: str
    status: str
    direction: str
    rows_transferred: int
    cells_updated: int
    conflict_count: int
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SyncConflictRead(BaseModel):
    """Detailed record of a detected cell conflict."""

    id: uuid.UUID
    run_id: uuid.UUID
    mapping_id: uuid.UUID
    organization_id: uuid.UUID
    row_index: int
    column_letter: str
    vibe_value: dict[str, Any] | None = None
    external_value: dict[str, Any] | None = None
    status: str
    resolution_choice: str | None = None
    resolved_by_user_id: uuid.UUID | None = None
    resolved_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SyncConflictResolveInput(BaseModel):
    """Payload to resolve an outstanding cell conflict."""

    resolution_choice: str  # 'keep_vibe', 'keep_external', 'manual'
    custom_value: Any | None = None
