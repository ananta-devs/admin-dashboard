"""Pydantic schemas for dashboards, widgets, and Polars aggregation queries."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WidgetCreateInput(BaseModel):
    """Payload to create a new visualization widget in a dashboard."""

    title: str = Field(..., max_length=255)
    widget_type: str = Field(
        ...,
        description="One of: bar, stacked_bar, line, area, pie, donut, scatter, histogram, heatmap, correlation_matrix, box_plot, kpi_card, table, summary_stats",
    )
    source_type: str = Field("sheet", description="'sheet' or 'analytics_result'")
    source_id: uuid.UUID
    query_config: dict[str, Any] = Field(default_factory=dict)
    position_x: int = 0
    position_y: int = 0
    width: int = 6
    height: int = 4
    sort_order: int = 0


class WidgetUpdateInput(BaseModel):
    """Payload to update an existing widget."""

    title: str | None = Field(None, max_length=255)
    widget_type: str | None = None
    source_type: str | None = None
    source_id: uuid.UUID | None = None
    query_config: dict[str, Any] | None = None
    position_x: int | None = None
    position_y: int | None = None
    width: int | None = None
    height: int | None = None
    sort_order: int | None = None


class WidgetPositionItem(BaseModel):
    """Single item in a batch layout positioning update."""

    widget_id: uuid.UUID
    position_x: int
    position_y: int
    width: int
    height: int
    sort_order: int = 0


class WidgetLayoutUpdateInput(BaseModel):
    """Batch payload updating multiple widget positions simultaneously."""

    positions: list[WidgetPositionItem]


class WidgetRead(BaseModel):
    """Public representation of a visualization widget."""

    id: uuid.UUID
    dashboard_id: uuid.UUID
    organization_id: uuid.UUID
    title: str
    widget_type: str
    source_type: str
    source_id: uuid.UUID
    query_config: dict[str, Any] = Field(default_factory=dict)
    position_x: int
    position_y: int
    width: int
    height: int
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DashboardCreateInput(BaseModel):
    """Payload to create a new analytical dashboard."""

    workspace_id: uuid.UUID
    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    layout_config: dict[str, Any] = Field(default_factory=dict)
    is_shared: bool = True


class DashboardUpdateInput(BaseModel):
    """Payload to update dashboard metadata or layout configuration."""

    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    layout_config: dict[str, Any] | None = None
    is_shared: bool | None = None
    expected_updated_at: datetime | None = None


class DashboardShareInput(BaseModel):
    """Payload to share/unshare a dashboard with the workspace."""

    is_shared: bool = True


class DashboardDuplicateInput(BaseModel):
    """Payload to duplicate a dashboard with an optional custom title."""

    title: str | None = Field(None, min_length=1, max_length=255)


class DashboardRead(BaseModel):
    """Public read model for dashboards in summary listings."""

    id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    created_by_user_id: uuid.UUID | None = None
    title: str
    description: str | None = None
    layout_config: dict[str, Any] = Field(default_factory=dict)
    is_shared: bool
    widget_count: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DashboardDetailRead(BaseModel):
    """Detailed dashboard model including child widgets."""

    id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    created_by_user_id: uuid.UUID | None = None
    title: str
    description: str | None = None
    layout_config: dict[str, Any] = Field(default_factory=dict)
    is_shared: bool
    widgets: list[WidgetRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WidgetDataQueryInput(BaseModel):
    """Optional payload to test or preview query execution for a widget."""

    custom_query_config: dict[str, Any] | None = None


class WidgetDataQueryResponse(BaseModel):
    """Tabular query aggregation results rendered into charts/KPIs."""

    columns: list[str]
    data: list[dict[str, Any]]
    total_rows: int
    summary: dict[str, Any] = Field(default_factory=dict)

