"""FastAPI router for organizational workspaces and tenant-scoped workspace management."""

from __future__ import annotations

import uuid
from typing import Sequence

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    get_current_membership,
    require_permission,
    verify_csrf_token,
)
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership
from app.schemas.workspace import (
    WorkspaceCreate,
    WorkspaceDetailRead,
    WorkspaceRead,
    WorkspaceUpdate,
)
from app.services.workspace import WorkspaceService

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get(
    "",
    response_model=list[WorkspaceDetailRead],
    summary="List workspaces for active organization",
    dependencies=[Depends(require_permission("workspaces.read"))],
)
async def list_workspaces(
    search: str | None = Query(None, description="Search by workspace name or slug"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> list[WorkspaceDetailRead]:
    """Retrieve all workspaces strictly scoped within the active organization."""
    service = WorkspaceService(session)
    return await service.list_workspaces(
        organization_id=membership.organization_id,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=WorkspaceRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new workspace",
    dependencies=[
        Depends(require_permission("workspaces.create")),
        Depends(verify_csrf_token),
    ],
)
async def create_workspace(
    payload: WorkspaceCreate,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> WorkspaceRead:
    """Create a new workspace strictly within the active organization."""
    service = WorkspaceService(session)
    return await service.create_workspace(
        organization_id=membership.organization_id,
        data=payload,
    )


@router.get(
    "/{workspace_id}",
    response_model=WorkspaceDetailRead,
    summary="Get workspace details and aggregate statistics",
    dependencies=[Depends(require_permission("workspaces.read"))],
)
async def get_workspace(
    workspace_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> WorkspaceDetailRead:
    """Retrieve a single workspace with resource counts and tenant isolation."""
    service = WorkspaceService(session)
    return await service.get_workspace_with_stats(
        workspace_id=workspace_id,
        organization_id=membership.organization_id,
    )


@router.patch(
    "/{workspace_id}",
    response_model=WorkspaceRead,
    summary="Update workspace name or slug",
    dependencies=[
        Depends(require_permission("workspaces.update")),
        Depends(verify_csrf_token),
    ],
)
async def update_workspace(
    workspace_id: uuid.UUID,
    payload: WorkspaceUpdate,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> WorkspaceRead:
    """Update workspace details with slug uniqueness check."""
    service = WorkspaceService(session)
    return await service.update_workspace(
        workspace_id=workspace_id,
        organization_id=membership.organization_id,
        data=payload,
    )


@router.delete(
    "/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Safely delete a workspace",
    dependencies=[
        Depends(require_permission("workspaces.delete")),
        Depends(verify_csrf_token),
    ],
)
async def delete_workspace(
    workspace_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> Response:
    """Safely delete a workspace. Enforces default protection and blocks deletion if active dependencies exist."""
    service = WorkspaceService(session)
    await service.delete_workspace(
        workspace_id=workspace_id,
        organization_id=membership.organization_id,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)

