"""FastAPI router for System Health diagnostics and operational telemetry."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    get_current_membership,
    require_any_permission,
)
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership
from app.services.system_health import SystemHealthService

router = APIRouter(prefix="/system", tags=["system-health"])


@router.get(
    "/telemetry",
    response_model=dict[str, Any],
    summary="Get authoritative operational telemetry across all subsystems",
    dependencies=[Depends(require_any_permission("organization.settings.update", "audit.read"))],
)
async def get_system_telemetry(
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> dict[str, Any]:
    """Retrieve live diagnostics across backend, database, cache, workers, analytics, storage, and disaster recovery.

    Strictly protected by administrative RBAC. No database secrets, passwords, filesystem paths,
    or internal tokens are returned.
    """
    service = SystemHealthService(session)
    return await service.get_subsystems_telemetry(membership.organization_id)
