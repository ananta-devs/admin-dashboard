"""Pydantic schemas for Phase 9 Gate 3 reporting subsystem."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReportDefinitionCreateInput(BaseModel):
    """Payload to create a new multi-section report definition."""

    workspace_id: uuid.UUID
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    dashboard_id: uuid.UUID | None = None
    format: str = Field("pdf", max_length=20)
    page_orientation: str = Field("portrait", max_length=20)
    page_size: str = Field("a4", max_length=20)
    header_text: str | None = Field(None, max_length=255)
    footer_text: str | None = Field(None, max_length=255)
    include_table_of_contents: bool = True
    include_cover_page: bool = True
    theme: str = Field("default", max_length=50)


class ReportDefinitionUpdateInput(BaseModel):
    """Payload to update an existing report definition."""

    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    dashboard_id: uuid.UUID | None = None
    format: str | None = Field(None, max_length=20)
    page_orientation: str | None = Field(None, max_length=20)
    page_size: str | None = Field(None, max_length=20)
    header_text: str | None = Field(None, max_length=255)
    footer_text: str | None = Field(None, max_length=255)
    include_table_of_contents: bool | None = None
    include_cover_page: bool | None = None
    theme: str | None = Field(None, max_length=50)


class ReportSectionCreateInput(BaseModel):
    """Payload to add a section block to a report definition."""

    section_type: str = Field(
        ..., max_length=50, description="'chart', 'table', 'summary', or 'markdown'"
    )
    title: str | None = Field(None, max_length=255)
    description: str | None = None
    widget_id: uuid.UUID | None = None
    custom_content: str | None = None
    override_query_config: dict[str, Any] = Field(default_factory=dict)
    sort_order: int = 0
    page_break_after: bool = False


class ReportSectionUpdateInput(BaseModel):
    """Payload to update a section block."""

    title: str | None = Field(None, max_length=255)
    description: str | None = None
    widget_id: uuid.UUID | None = None
    custom_content: str | None = None
    override_query_config: dict[str, Any] | None = None
    sort_order: int | None = None
    page_break_after: bool | None = None


class ReportSectionReorderInput(BaseModel):
    """Payload to reorder report sections deterministically."""

    ordered_section_ids: list[uuid.UUID]


class ReportRunTriggerInput(BaseModel):
    """Payload to trigger an on-demand report execution run."""

    idempotency_key: str = Field(..., min_length=1, max_length=100)
    trigger_type: str = Field("manual", max_length=30)


class ReportSectionRead(BaseModel):
    """Response schema for a report section."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    report_definition_id: uuid.UUID
    organization_id: uuid.UUID
    widget_id: uuid.UUID | None = None
    section_type: str
    title: str | None = None
    description: str | None = None
    custom_content: str | None = None
    override_query_config: dict[str, Any] = Field(default_factory=dict)
    sort_order: int
    page_break_after: bool


class ReportDefinitionRead(BaseModel):
    """Response schema for a report definition."""

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="before")
    @classmethod
    def check_unloaded_sections(cls, data: Any) -> Any:
        if hasattr(data, "__table__"):
            from sqlalchemy import inspect as sa_inspect

            try:
                insp = sa_inspect(data)
                if "sections" in insp.unloaded:
                    d = {c.name: getattr(data, c.name) for c in data.__table__.columns}
                    d["sections"] = []
                    return d
            except Exception:
                pass
        return data

    id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    dashboard_id: uuid.UUID | None = None
    title: str
    description: str | None = None
    format: str
    page_orientation: str
    page_size: str
    header_text: str | None = None
    footer_text: str | None = None
    include_table_of_contents: bool
    include_cover_page: bool
    theme: str
    is_archived: bool
    sections: list[ReportSectionRead] = Field(default_factory=list)


class ReportRunRead(BaseModel):
    """Response schema for a report execution run."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    report_definition_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None
    report_title: str
    trigger_type: str
    status: str
    idempotency_key: str | None = None
    attempt_count: int
    pdf_artifact_path: str | None = None
    pdf_file_size_bytes: int | None = None
    pdf_page_count: int | None = None
    pdf_checksum_sha256: str | None = None
    pdf_rendered_at: Any | None = None
    execution_duration_ms: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    queued_at: Any | None = None
    started_at: Any | None = None
    completed_at: Any | None = None


class ReportDownloadTokenResponse(BaseModel):
    """Response schema for single-use download token."""

    download_token: str
    download_url: str
    expires_in_seconds: int
