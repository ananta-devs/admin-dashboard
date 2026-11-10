import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
)
from app.models.workspace import Workspace
from app.schemas.organization import OrganizationCreate
from app.schemas.user import UserCreate
from app.schemas.workspace import WorkspaceCreate
from app.services.organization import OrganizationService
from app.services.user import UserService
from app.services.workspace import WorkspaceService


@pytest.mark.asyncio
async def test_user_service_duplicate_email():
    """Unit test: verifies UserService raises EntityAlreadyExistsError on duplicate normalized email."""
    mock_session = AsyncMock()
    service = UserService(mock_session)
    service.repo.get_by_normalized_email = AsyncMock(return_value=MagicMock())

    with pytest.raises(EntityAlreadyExistsError) as exc_info:
        await service.create_user(UserCreate(email="duplicate@acme.com", display_name="Dup"))

    assert exc_info.value.code == "ENTITY_ALREADY_EXISTS"
    assert "duplicate@acme.com" in exc_info.value.message


@pytest.mark.asyncio
async def test_user_service_not_found():
    """Unit test: verifies UserService raises EntityNotFoundError when user does not exist."""
    mock_session = AsyncMock()
    service = UserService(mock_session)
    service.repo.get_by_id = AsyncMock(return_value=None)

    fake_id = uuid.uuid4()
    with pytest.raises(EntityNotFoundError) as exc_info:
        await service.get_user_by_id(fake_id)

    assert exc_info.value.code == "ENTITY_NOT_FOUND"


@pytest.mark.asyncio
async def test_organization_service_duplicate_slug():
    """Unit test: verifies OrganizationService raises EntityAlreadyExistsError on slug conflict."""
    mock_session = AsyncMock()
    service = OrganizationService(mock_session)
    service.org_repo.get_by_slug = AsyncMock(return_value=MagicMock())

    with pytest.raises(EntityAlreadyExistsError):
        await service.create_organization(OrganizationCreate(name="Acme", slug="acme"))


@pytest.mark.asyncio
async def test_organization_service_not_found():
    """Unit test: verifies OrganizationService raises EntityNotFoundError when org not found."""
    mock_session = AsyncMock()
    service = OrganizationService(mock_session)
    service.org_repo.get_by_id = AsyncMock(return_value=None)

    with pytest.raises(EntityNotFoundError):
        await service.get_organization(uuid.uuid4())


@pytest.mark.asyncio
async def test_workspace_service_org_not_found():
    """Unit test: verifies WorkspaceService raises EntityNotFoundError when org missing."""
    mock_session = AsyncMock()
    service = WorkspaceService(mock_session)
    service.org_repo.get_by_id = AsyncMock(return_value=None)

    with pytest.raises(EntityNotFoundError) as exc_info:
        await service.create_workspace(uuid.uuid4(), WorkspaceCreate(name="WS", slug="ws"))

    assert "Organization" in exc_info.value.message


@pytest.mark.asyncio
async def test_workspace_service_cross_tenant_access_detection():
    """Unit test: verifies WorkspaceService raises EntityNotFoundError on cross-tenant access to prevent enumeration."""
    mock_session = AsyncMock()
    service = WorkspaceService(mock_session)

    target_org_id = uuid.uuid4()
    actual_org_id = uuid.uuid4()
    ws_id = uuid.uuid4()

    # Repository returns None for the scoped query
    service.workspace_repo.get_by_id = AsyncMock(return_value=None)

    # Session returns workspace owned by a DIFFERENT org
    existing_ws = Workspace(
        id=ws_id,
        organization_id=actual_org_id,
        name="Confidential Workspace",
        slug="confidential",
    )
    mock_session.get.return_value = existing_ws

    with pytest.raises(EntityNotFoundError) as exc_info:
        await service.get_workspace(ws_id, organization_id=target_org_id)

    assert exc_info.value.code == "ENTITY_NOT_FOUND"
    assert str(ws_id) in exc_info.value.message
