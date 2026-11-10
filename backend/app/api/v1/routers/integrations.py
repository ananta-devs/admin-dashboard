"""API router for external integrations and Google Sheets connectivity."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    get_current_membership,
    get_current_session,
    require_permission,
    verify_csrf_token,
)
from app.core.exceptions import EntityNotFoundError
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership
from app.models.session import AuthenticationSession
from app.schemas.integration import (
    ExternalIntegrationRead,
    GoogleAuthUrlResponse,
    GoogleSpreadsheetDetail,
    GoogleSpreadsheetItem,
    GoogleSpreadsheetValuesResponse,
    OAuthCallbackInput,
)
from app.services.google_oauth import GoogleOAuthService

router = APIRouter(prefix="/integrations", tags=["Integrations"])


@router.get(
    "/google/auth-url",
    response_model=GoogleAuthUrlResponse,
    dependencies=[Depends(require_permission("integrations.manage"))],
)
async def get_google_auth_url(
    redirect_uri: str | None = Query(default=None),
    membership: OrganizationMembership = Depends(get_current_membership),
    auth_session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> GoogleAuthUrlResponse:
    """Generate state token and return Google OAuth 2.0 authorization URL."""
    service = GoogleOAuthService(db)
    return await service.generate_auth_url(
        organization_id=membership.organization_id,
        user_id=auth_session.user_id,
        redirect_uri=redirect_uri,
    )


@router.post(
    "/google/callback",
    response_model=ExternalIntegrationRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(require_permission("integrations.manage")),
        Depends(verify_csrf_token),
    ],
)
async def handle_google_callback(
    data: OAuthCallbackInput,
    membership: OrganizationMembership = Depends(get_current_membership),
    auth_session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> ExternalIntegrationRead:
    """Exchange authorization code for encrypted tokens and persist integration."""
    service = GoogleOAuthService(db)
    integration = await service.handle_oauth_callback(
        organization_id=membership.organization_id,
        user_id=auth_session.user_id,
        code=data.code,
        state_token=data.state,
        redirect_uri=data.redirect_uri,
    )
    return ExternalIntegrationRead.model_validate(integration)


@router.get(
    "",
    response_model=list[ExternalIntegrationRead],
    dependencies=[Depends(require_permission("integrations.read"))],
)
async def list_integrations(
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> list[ExternalIntegrationRead]:
    """List all connected external integrations in the active organization."""
    service = GoogleOAuthService(db)
    items = await service.repo.list_by_organization(membership.organization_id)
    return [ExternalIntegrationRead.model_validate(i) for i in items]


@router.get(
    "/{integration_id}",
    response_model=ExternalIntegrationRead,
    dependencies=[Depends(require_permission("integrations.read"))],
)
async def get_integration(
    integration_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> ExternalIntegrationRead:
    """Get metadata for a specific connected integration."""
    service = GoogleOAuthService(db)
    integration = await service.repo.get_by_id(integration_id, membership.organization_id)
    if not integration:
        raise EntityNotFoundError("ExternalIntegration", str(integration_id))
    return ExternalIntegrationRead.model_validate(integration)


@router.delete(
    "/{integration_id}",
    dependencies=[
        Depends(require_permission("integrations.manage")),
        Depends(verify_csrf_token),
    ],
)
async def disconnect_integration(
    integration_id: uuid.UUID,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Disconnect Google account, revoke credentials where supported, and remove integration."""
    service = GoogleOAuthService(db)
    await service.disconnect(integration_id, membership.organization_id)
    return {"success": True, "message": "Integration successfully disconnected."}


@router.get(
    "/{integration_id}/spreadsheets",
    response_model=list[GoogleSpreadsheetItem],
    dependencies=[Depends(require_permission("integrations.read"))],
)
async def list_google_spreadsheets(
    integration_id: uuid.UUID,
    page_size: int = Query(default=20, ge=1, le=100),
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> list[GoogleSpreadsheetItem]:
    """Browse spreadsheets accessible to the connected Google account via Drive API."""
    service = GoogleOAuthService(db)
    return await service.list_spreadsheets(
        integration_id=integration_id,
        organization_id=membership.organization_id,
        page_size=page_size,
    )


@router.get(
    "/{integration_id}/spreadsheets/{spreadsheet_id}",
    response_model=GoogleSpreadsheetDetail,
    dependencies=[Depends(require_permission("integrations.read"))],
)
async def get_google_spreadsheet_detail(
    integration_id: uuid.UUID,
    spreadsheet_id: str,
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> GoogleSpreadsheetDetail:
    """Inspect sheets and grid structure of an external Google Spreadsheet."""
    service = GoogleOAuthService(db)
    return await service.get_spreadsheet_metadata(
        integration_id=integration_id,
        organization_id=membership.organization_id,
        external_spreadsheet_id=spreadsheet_id,
    )


@router.get(
    "/{integration_id}/spreadsheets/{spreadsheet_id}/values",
    response_model=GoogleSpreadsheetValuesResponse,
    dependencies=[Depends(require_permission("integrations.read"))],
)
async def get_google_spreadsheet_values(
    integration_id: uuid.UUID,
    spreadsheet_id: str,
    range_name: str = Query(default="Sheet1!A1:Z100"),
    membership: OrganizationMembership = Depends(get_current_membership),
    db: AsyncSession = Depends(get_db_session),
) -> GoogleSpreadsheetValuesResponse:
    """Preview cell values in a specified worksheet range."""
    service = GoogleOAuthService(db)
    return await service.get_sheet_values(
        integration_id=integration_id,
        organization_id=membership.organization_id,
        external_spreadsheet_id=spreadsheet_id,
        range_name=range_name,
    )
