"""Pydantic schemas for external integrations and Google Sheets endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExternalIntegrationRead(BaseModel):
    """Public read model for a connected external integration (never exposing raw/encrypted tokens)."""

    id: uuid.UUID
    organization_id: uuid.UUID
    provider: str
    account_email: str
    account_name: str | None = None
    account_avatar_url: str | None = None
    token_expires_at: datetime | None = None
    authorized_scopes: list[str] = Field(default_factory=list)
    status: str
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class GoogleAuthUrlResponse(BaseModel):
    """Authorization URL and state token response for initiating Google OAuth."""

    auth_url: str
    state_token: str
    expires_at: datetime


class OAuthCallbackInput(BaseModel):
    """Payload submitted when exchanging Google OAuth code for tokens."""

    code: str
    state: str
    redirect_uri: str | None = None


class GoogleSpreadsheetItem(BaseModel):
    """Summary of a Google Spreadsheet file accessible via Drive API."""

    id: str
    name: str
    modified_time: str | None = None


class GoogleSheetTabItem(BaseModel):
    """Specification of an individual tab/worksheet within a Google Spreadsheet."""

    sheet_id: int
    title: str
    row_count: int = 1000
    column_count: int = 26


class GoogleSpreadsheetDetail(BaseModel):
    """Detailed metadata and tab specification of a Google Spreadsheet."""

    spreadsheet_id: str
    title: str
    sheets: list[GoogleSheetTabItem] = Field(default_factory=list)


class GoogleSpreadsheetValuesResponse(BaseModel):
    """Tabular preview values extracted from a Google Spreadsheet worksheet."""

    spreadsheet_id: str
    sheet_name: str
    values: list[list[Any]] = Field(default_factory=list)
    total_rows: int
    total_columns: int
