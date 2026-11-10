"""FastAPI router for governed user-defined Python analytics scripts and runs."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    get_current_membership,
    require_permission,
    verify_csrf_token,
)
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership
from app.schemas.script import (
    PythonScriptCreateInput,
    PythonScriptRead,
    PythonScriptRunInput,
    PythonScriptRunRead,
    PythonScriptUpdateInput,
)
from app.services.audit import AuditService
from app.services.permission import PermissionService
from app.services.script import PythonScriptService

router = APIRouter(prefix="/scripts", tags=["scripts"])


@router.post(
    "",
    response_model=PythonScriptRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a governed Python analytics script",
    dependencies=[
        Depends(require_permission("analytics.scripts.create")),
        Depends(verify_csrf_token),
    ],
)
async def create_script(
    payload: PythonScriptCreateInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> PythonScriptRead:
    service = PythonScriptService(session)
    script = await service.create_script(
        payload,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
    )
    audit = AuditService(session)
    await audit.record_event(
        organization_id=membership.organization_id,
        actor_user_id=membership.user_id,
        action="script.created",
        target_type="python_script",
        target_id=str(script.id),
        metadata={"title": script.title},
    )
    return PythonScriptRead.model_validate(script)


@router.get(
    "",
    response_model=list[PythonScriptRead],
    summary="List Python analytics scripts for organization",
    dependencies=[Depends(require_permission("analytics.scripts.read"))],
)
async def list_scripts(
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> list[PythonScriptRead]:
    service = PythonScriptService(session)
    scripts = await service.list_scripts(membership.organization_id)
    return [PythonScriptRead.model_validate(s) for s in scripts]


@router.get(
    "/{script_id}",
    response_model=PythonScriptRead,
    summary="Get script details and source code",
    dependencies=[Depends(require_permission("analytics.scripts.read"))],
)
async def get_script(
    script_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> PythonScriptRead:
    service = PythonScriptService(session)
    script = await service.get_script(script_id, membership.organization_id)
    return PythonScriptRead.model_validate(script)


@router.patch(
    "/{script_id}",
    response_model=PythonScriptRead,
    summary="Update script title, description, or code",
    dependencies=[
        Depends(require_permission("analytics.scripts.update")),
        Depends(verify_csrf_token),
    ],
)
async def update_script(
    script_id: uuid.UUID,
    payload: PythonScriptUpdateInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> PythonScriptRead:
    perm_service = PermissionService(session)
    can_manage_any = await perm_service.check_membership_has_permission(
        membership.id, "analytics.scripts.manage", organization_id=membership.organization_id
    )
    service = PythonScriptService(session)
    script = await service.update_script(
        script_id,
        membership.organization_id,
        payload,
        user_id=membership.user_id,
        can_manage_any=can_manage_any,
    )
    audit = AuditService(session)
    await audit.record_event(
        organization_id=membership.organization_id,
        actor_user_id=membership.user_id,
        action="script.updated",
        target_type="python_script",
        target_id=str(script.id),
        metadata={"version": script.version},
    )
    return PythonScriptRead.model_validate(script)


@router.delete(
    "/{script_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a Python script",
    dependencies=[
        Depends(require_permission("analytics.scripts.delete")),
        Depends(verify_csrf_token),
    ],
)
async def delete_script(
    script_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> None:
    perm_service = PermissionService(session)
    can_manage_any = await perm_service.check_membership_has_permission(
        membership.id, "analytics.scripts.manage", organization_id=membership.organization_id
    )
    service = PythonScriptService(session)
    await service.delete_script(
        script_id,
        membership.organization_id,
        user_id=membership.user_id,
        can_manage_any=can_manage_any,
    )
    audit = AuditService(session)
    await audit.record_event(
        organization_id=membership.organization_id,
        actor_user_id=membership.user_id,
        action="script.deleted",
        target_type="python_script",
        target_id=str(script_id),
        metadata={},
    )


@router.post(
    "/{script_id}/run",
    response_model=PythonScriptRunRead,
    summary="Execute script in governed subprocess sandbox",
    dependencies=[
        Depends(require_permission("analytics.scripts.execute")),
        Depends(verify_csrf_token),
    ],
)
async def execute_script(
    script_id: uuid.UUID,
    payload: PythonScriptRunInput,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> PythonScriptRunRead:
    service = PythonScriptService(session)
    run = await service.execute_script(
        script_id=script_id,
        organization_id=membership.organization_id,
        input_data=payload,
        user_id=membership.user_id,
    )
    audit = AuditService(session)
    await audit.record_event(
        organization_id=membership.organization_id,
        actor_user_id=membership.user_id,
        action="script.executed",
        target_type="python_script_run",
        target_id=str(run.id),
        metadata={"script_id": str(script_id), "status": run.status},
    )
    return PythonScriptRunRead.model_validate(run)


@router.get(
    "/{script_id}/runs",
    response_model=list[PythonScriptRunRead],
    summary="List execution runs for a script",
    dependencies=[Depends(require_permission("analytics.scripts.read"))],
)
async def list_script_runs(
    script_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> list[PythonScriptRunRead]:
    service = PythonScriptService(session)
    runs = await service.list_runs(script_id, membership.organization_id)
    return [PythonScriptRunRead.model_validate(r) for r in runs]


@router.get(
    "/{script_id}/runs/{run_id}",
    response_model=PythonScriptRunRead,
    summary="Get execution run details, logs, and outputs",
    dependencies=[Depends(require_permission("analytics.scripts.read"))],
)
async def get_script_run(
    script_id: uuid.UUID,
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> PythonScriptRunRead:
    service = PythonScriptService(session)
    run = await service.get_run(run_id, membership.organization_id)
    return PythonScriptRunRead.model_validate(run)


@router.get(
    "/environment/policy",
    summary="Inspect script execution environment and security policy",
    dependencies=[Depends(require_permission("analytics.scripts.read"))],
)
async def get_script_security_policy(
    membership: OrganizationMembership = Depends(get_current_membership),
) -> dict[str, Any]:
    from app.core.config import settings

    return {
        "sandbox_enabled": settings.PYTHON_SANDBOX_ENABLED,
        "isolation_backend": settings.PYTHON_SANDBOX_ISOLATION_TYPE,
        "pinned_image": settings.PYTHON_SANDBOX_IMAGE,
        "image_digest": settings.PYTHON_SANDBOX_IMAGE_DIGEST,
        "max_memory_mb": settings.PYTHON_SANDBOX_MAX_MEMORY_MB,
        "timeout_seconds": settings.PYTHON_SANDBOX_TIMEOUT_SECONDS,
        "max_cells": settings.PYTHON_SANDBOX_MAX_CELLS,
        "vetted_libraries": ["polars", "pandas", "numpy", "math", "datetime"],
        "runtime_pip_install_allowed": False,
        "network_access_allowed": False,
    }


@router.post(
    "/environment/dependencies",
    summary="Manage or audit allowed script dependencies",
    dependencies=[
        Depends(require_permission("analytics.scripts.manage_dependencies")),
        Depends(verify_csrf_token),
    ],
)
async def manage_script_dependencies(
    payload: dict[str, Any],
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> dict[str, Any]:
    audit = AuditService(session)
    await audit.record_event(
        organization_id=membership.organization_id,
        actor_user_id=membership.user_id,
        action="script.dependencies_updated",
        target_type="script_dependency_policy",
        target_id=str(membership.organization_id),
        metadata=payload,
    )
    return {
        "status": "acknowledged",
        "action": payload.get("action", "audit"),
        "allowed_packages": ["polars", "pandas", "numpy", "math", "datetime"],
        "updated_by": str(membership.user_id),
    }


@router.get(
    "/environment/runtime-policy",
    summary="Inspect governed script execution environment and dependency policy",
    dependencies=[Depends(require_permission("analytics.scripts.manage_dependencies"))],
)
async def get_runtime_environment(
    membership: OrganizationMembership = Depends(get_current_membership),
) -> dict[str, Any]:
    from app.core.config import settings

    return {
        "sandbox_enabled": settings.PYTHON_SANDBOX_ENABLED,
        "isolation_backend": settings.PYTHON_SANDBOX_ISOLATION_TYPE,
        "pinned_image": settings.PYTHON_SANDBOX_IMAGE,
        "max_memory_mb": settings.PYTHON_SANDBOX_MAX_MEMORY_MB,
        "timeout_seconds": settings.PYTHON_SANDBOX_TIMEOUT_SECONDS,
        "max_cells": settings.PYTHON_SANDBOX_MAX_CELLS,
        "vetted_libraries": ["polars", "pandas", "numpy", "math", "datetime"],
        "runtime_pip_install_allowed": False,
        "network_access_allowed": False,
    }
