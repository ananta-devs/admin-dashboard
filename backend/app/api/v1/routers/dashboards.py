"""FastAPI router for analytical dashboards, visualization widgets, and query execution."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    get_current_membership,
    require_permission,
    verify_csrf_token,
)
from app.core.exceptions import EntityNotFoundError
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership
from app.schemas.dashboard import (
    DashboardCreateInput,
    DashboardDetailRead,
    DashboardDuplicateInput,
    DashboardRead,
    DashboardShareInput,
    DashboardUpdateInput,
    WidgetCreateInput,
    WidgetDataQueryInput,
    WidgetDataQueryResponse,
    WidgetLayoutUpdateInput,
    WidgetRead,
    WidgetUpdateInput,
)
from app.services.dashboard import DashboardService

router = APIRouter(prefix="/dashboards", tags=["dashboards"])


@router.post(
    "",
    response_model=DashboardDetailRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new analytical dashboard",
    dependencies=[
        Depends(require_permission("dashboards.create")),
        Depends(verify_csrf_token),
    ],
)
async def create_dashboard(
    payload: DashboardCreateInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> DashboardDetailRead:
    service = DashboardService(session)
    dashboard = await service.create_dashboard(
        payload,
        organization_id=membership.organization_id,
        created_by_user_id=membership.user_id,
    )
    return DashboardDetailRead.model_validate(dashboard)


@router.get(
    "",
    response_model=list[DashboardRead],
    summary="List dashboards for current organization",
    dependencies=[Depends(require_permission("dashboards.read"))],
)
async def list_dashboards(
    workspace_id: uuid.UUID | None = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> list[DashboardRead]:
    service = DashboardService(session)
    dashboards = await service.list_dashboards(
        organization_id=membership.organization_id, workspace_id=workspace_id
    )
    result: list[DashboardRead] = []
    for d in dashboards:
        read_item = DashboardRead.model_validate(d)
        read_item.widget_count = len(d.widgets) if d.widgets else 0
        result.append(read_item)
    return result


@router.get(
    "/{dashboard_id}",
    response_model=DashboardDetailRead,
    summary="Get dashboard details and child widgets",
    dependencies=[Depends(require_permission("dashboards.read"))],
)
async def get_dashboard(
    dashboard_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> DashboardDetailRead:
    service = DashboardService(session)
    dashboard = await service.get_dashboard(dashboard_id, membership.organization_id)
    return DashboardDetailRead.model_validate(dashboard)


@router.patch(
    "/{dashboard_id}",
    response_model=DashboardDetailRead,
    summary="Update dashboard title, description, or layout",
    dependencies=[
        Depends(require_permission("dashboards.update")),
        Depends(verify_csrf_token),
    ],
)
async def update_dashboard(
    dashboard_id: uuid.UUID,
    payload: DashboardUpdateInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> DashboardDetailRead:
    service = DashboardService(session)
    dashboard = await service.update_dashboard(dashboard_id, membership.organization_id, payload)
    return DashboardDetailRead.model_validate(dashboard)


@router.post(
    "/{dashboard_id}/share",
    response_model=DashboardDetailRead,
    summary="Update dashboard sharing settings with organization",
    dependencies=[
        Depends(require_permission("dashboards.share")),
        Depends(verify_csrf_token),
    ],
)
async def share_dashboard(
    dashboard_id: uuid.UUID,
    payload: DashboardShareInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> DashboardDetailRead:
    service = DashboardService(session)
    dashboard = await service.share_dashboard(
        dashboard_id, membership.organization_id, payload.is_shared
    )
    return DashboardDetailRead.model_validate(dashboard)


@router.delete(
    "/{dashboard_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a dashboard",
    dependencies=[
        Depends(require_permission("dashboards.delete")),
        Depends(verify_csrf_token),
    ],
)
async def delete_dashboard(
    dashboard_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> None:
    service = DashboardService(session)
    await service.delete_dashboard(dashboard_id, membership.organization_id)


@router.post(
    "/{dashboard_id}/duplicate",
    response_model=DashboardDetailRead,
    status_code=status.HTTP_201_CREATED,
    summary="Duplicate a dashboard and all child widgets",
    dependencies=[
        Depends(require_permission("dashboards.create")),
        Depends(verify_csrf_token),
    ],
)
async def duplicate_dashboard(
    dashboard_id: uuid.UUID,
    payload: DashboardDuplicateInput | None = None,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> DashboardDetailRead:
    service = DashboardService(session)
    new_title = payload.title if payload else None
    dashboard = await service.duplicate_dashboard(
        dashboard_id=dashboard_id,
        organization_id=membership.organization_id,
        created_by_user_id=membership.user_id,
        new_title=new_title,
    )
    return DashboardDetailRead.model_validate(dashboard)


# =============================================================================
# Widget Endpoints
# =============================================================================


@router.post(
    "/{dashboard_id}/widgets",
    response_model=WidgetRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a visualization widget to a dashboard",
    dependencies=[
        Depends(require_permission("dashboards.create")),
        Depends(verify_csrf_token),
    ],
)
async def create_widget(
    dashboard_id: uuid.UUID,
    payload: WidgetCreateInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> WidgetRead:
    service = DashboardService(session)
    widget = await service.create_widget(dashboard_id, membership.organization_id, payload)
    return WidgetRead.model_validate(widget)


@router.get(
    "/{dashboard_id}/widgets",
    response_model=list[WidgetRead],
    summary="List widgets in a dashboard",
    dependencies=[Depends(require_permission("dashboards.read"))],
)
async def list_widgets(
    dashboard_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> list[WidgetRead]:
    service = DashboardService(session)
    widgets = await service.list_widgets(dashboard_id, membership.organization_id)
    return [WidgetRead.model_validate(w) for w in widgets]


@router.patch(
    "/{dashboard_id}/widgets/{widget_id}",
    response_model=WidgetRead,
    summary="Update widget configuration or dimensions",
    dependencies=[
        Depends(require_permission("dashboards.update")),
        Depends(verify_csrf_token),
    ],
)
async def update_widget(
    dashboard_id: uuid.UUID,
    widget_id: uuid.UUID,
    payload: WidgetUpdateInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> WidgetRead:
    service = DashboardService(session)
    widget = await service.update_widget(
        widget_id, membership.organization_id, payload, dashboard_id=dashboard_id
    )
    return WidgetRead.model_validate(widget)


@router.delete(
    "/{dashboard_id}/widgets/{widget_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a widget from a dashboard",
    dependencies=[
        Depends(require_permission("dashboards.delete")),
        Depends(verify_csrf_token),
    ],
)
async def delete_widget(
    dashboard_id: uuid.UUID,
    widget_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> None:
    service = DashboardService(session)
    await service.delete_widget(widget_id, membership.organization_id, dashboard_id=dashboard_id)


@router.post(
    "/{dashboard_id}/layout",
    response_model=list[WidgetRead],
    summary="Batch update widget positions in dashboard layout",
    dependencies=[
        Depends(require_permission("dashboards.update")),
        Depends(verify_csrf_token),
    ],
)
async def update_dashboard_layout(
    dashboard_id: uuid.UUID,
    payload: WidgetLayoutUpdateInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> list[WidgetRead]:
    service = DashboardService(session)
    updated = await service.batch_update_layout(dashboard_id, membership.organization_id, payload)
    return [WidgetRead.model_validate(w) for w in updated]


@router.post(
    "/{dashboard_id}/widgets/{widget_id}/query",
    response_model=WidgetDataQueryResponse,
    summary="Execute analytical query aggregation for a widget",
    dependencies=[Depends(require_permission("dashboards.read"))],
)
async def query_widget_data(
    dashboard_id: uuid.UUID,
    widget_id: uuid.UUID,
    payload: WidgetDataQueryInput | None = None,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> WidgetDataQueryResponse:
    service = DashboardService(session)
    custom_cfg = payload.custom_query_config if payload else None
    return await service.query_widget_data(
        widget_id=widget_id,
        organization_id=membership.organization_id,
        custom_config=custom_cfg,
        dashboard_id=dashboard_id,
    )


@router.get(
    "/{dashboard_id}/widgets/{widget_id}/export",
    summary="Export widget dataset as sanitized CSV (dashboards.export required)",
    dependencies=[Depends(require_permission("dashboards.export"))],
)
@router.post(
    "/{dashboard_id}/widgets/{widget_id}/export",
    summary="Export widget dataset as sanitized CSV (dashboards.export required)",
    dependencies=[Depends(require_permission("dashboards.export"))],
)
async def export_widget_data_csv(
    dashboard_id: uuid.UUID,
    widget_id: uuid.UUID,
    payload: WidgetDataQueryInput | None = None,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> Response:
    service = DashboardService(session)
    dash = await service.get_dashboard(dashboard_id, membership.organization_id)
    widget = await service.get_widget(widget_id, membership.organization_id)
    if widget.dashboard_id != dash.id:
        raise EntityNotFoundError("DashboardWidget", str(widget_id))

    custom_cfg = payload.custom_query_config if payload else None
    csv_content = await service.export_widget_csv(widget_id, membership.organization_id, custom_cfg)
    safe_title = "".join(c if c.isalnum() else "_" for c in widget.title.lower()).strip("_")
    filename = f"{safe_title or 'widget'}_export.csv"
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/{dashboard_id}/export",
    summary="Export dashboard structure and widget data snapshot",
    dependencies=[Depends(require_permission("dashboards.export"))],
)
async def export_dashboard(
    dashboard_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> dict[str, Any]:
    service = DashboardService(session)
    dash = await service.get_dashboard(dashboard_id, membership.organization_id)
    return {
        "id": str(dash.id),
        "title": dash.title,
        "description": dash.description,
        "layout_config": dash.layout_config,
        "widgets": [WidgetRead.model_validate(w).model_dump() for w in dash.widgets],
    }
