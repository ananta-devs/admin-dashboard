"""Integration tests for Google Sheets Import/Export and Spreadsheet Synchronization."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.security import encrypt_integration_token
from app.db.session import get_db_session
from app.main import app
from app.models.integration import ExternalIntegration
from app.models.membership import OrganizationMembership
from app.models.role import MembershipRole
from app.models.spreadsheet import Sheet, SheetRow, Workbook
from app.models.user import User
from app.repositories.membership import MembershipRepository
from app.repositories.role import RoleRepository
from app.repositories.user import UserRepository
from app.repositories.workspace import WorkspaceRepository
from app.schemas.integration import (
    GoogleSpreadsheetDetail,
    GoogleSpreadsheetValuesResponse,
)


@pytest.fixture
async def api_client():
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def get_test_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = get_test_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.fixture
async def db_session():
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await test_engine.dispose()


@pytest.fixture(autouse=True)
async def cleanup_global_engine():
    yield
    from app.db.session import engine

    await engine.dispose()


async def register_and_login_helper(
    client: AsyncClient,
    email: str,
    password: str = "SecurePass1234!",
) -> tuple[dict[str, Any], str, uuid.UUID]:
    uid = uuid.uuid4().hex[:8]
    org_name = f"Org {uid}"
    org_slug = f"org-{uid}"

    reg_payload = {
        "email": email,
        "password": password,
        "display_name": f"User {uid}",
        "organization_name": org_name,
        "organization_slug": org_slug,
    }
    reg_res = await client.post(
        "/api/v1/auth/register",
        json=reg_payload,
        headers={"Origin": "http://localhost:3000"},
    )
    assert reg_res.status_code == 201

    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": "http://localhost:3000"},
    )
    assert login_res.status_code == 200
    data: dict[str, Any] = login_res.json()
    csrf_token: str = str(data.get("csrf_token") or login_res.cookies.get("csrf_token") or "")
    org_id = uuid.UUID(data["active_organization"]["id"])
    return data, csrf_token, org_id


async def create_connected_integration(
    db_session: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
) -> ExternalIntegration:
    integration = ExternalIntegration(
        organization_id=org_id,
        connected_by_user_id=user_id,
        provider="google",
        account_email="sync_user@example.com",
        encrypted_access_token=encrypt_integration_token("mock-token"),
        encrypted_refresh_token=encrypt_integration_token("mock-refresh"),
        status="connected",
        authorized_scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    db_session.add(integration)
    await db_session.commit()
    await db_session.refresh(integration)
    return integration


@pytest.mark.asyncio
async def test_google_sheets_import(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Test importing Google Sheet values into a new VIBE sheet."""
    uid = uuid.uuid4().hex[:8]
    owner_email = f"importer_{uid}@example.com"
    data, csrf_token, org_id = await register_and_login_helper(api_client, owner_email)
    user_id = uuid.UUID(data["user"]["id"])

    integration = await create_connected_integration(db_session, org_id, user_id)

    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    ws_id = ws_list[0].id

    # Create target workbook
    wb = Workbook(
        organization_id=org_id, workspace_id=ws_id, name="Test WB", created_by_user_id=user_id
    )
    db_session.add(wb)
    await db_session.commit()
    await db_session.refresh(wb)

    mock_values = [
        ["Product", "Price", "Qty", "Total"],
        ["Widget A", 10.5, 4, "=B2*C2"],
        ["Widget B", 20.0, 2, "=B3*C3"],
    ]

    with patch(
        "app.services.google_oauth.GoogleOAuthService.get_sheet_values",
        new_callable=AsyncMock,
        return_value=GoogleSpreadsheetValuesResponse(
            spreadsheet_id="test-sheet-123",
            sheet_name="Sheet1",
            values=mock_values,
            total_rows=len(mock_values),
            total_columns=4,
        ),
    ):
        import_payload = {
            "integration_id": str(integration.id),
            "external_spreadsheet_id": "test-sheet-123",
            "external_sheet_name": "Sheet1",
            "target_workbook_id": str(wb.id),
            "target_sheet_name": "Imported Products",
        }
        res = await api_client.post(
            "/api/v1/sync/import",
            json=import_payload,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 201, res.text
        resp_data = res.json()
        assert resp_data["sheet_name"] == "Imported Products"
        assert resp_data["rows_imported"] == 3
        assert resp_data["columns_imported"] == 4
        assert resp_data["cells_imported"] == 12

        # Verify sheet created in DB
        created_sheet_id = uuid.UUID(resp_data["sheet_id"])
        sheet = await db_session.get(Sheet, created_sheet_id)
        assert sheet is not None
        assert sheet.workbook_id == wb.id


@pytest.mark.asyncio
async def test_google_sheets_export(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Test exporting a VIBE sheet to Google Sheets."""
    uid = uuid.uuid4().hex[:8]
    owner_email = f"exporter_{uid}@example.com"
    data, csrf_token, org_id = await register_and_login_helper(api_client, owner_email)
    user_id = uuid.UUID(data["user"]["id"])

    integration = await create_connected_integration(db_session, org_id, user_id)

    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    ws_id = ws_list[0].id

    # Create workbook, sheet, and rows in VIBE
    wb = Workbook(
        organization_id=org_id, workspace_id=ws_id, name="Export WB", created_by_user_id=user_id
    )
    db_session.add(wb)
    await db_session.flush()

    sheet = Sheet(workbook_id=wb.id, organization_id=org_id, name="SourceSheet")
    db_session.add(sheet)
    await db_session.flush()

    row0 = SheetRow(
        sheet_id=sheet.id,
        organization_id=org_id,
        row_index=0,
        cells={
            "A": {"v": "Name", "c": "Name", "f": None},
            "B": {"v": "Score", "c": "Score", "f": None},
        },
    )
    row1 = SheetRow(
        sheet_id=sheet.id,
        organization_id=org_id,
        row_index=1,
        cells={
            "A": {"v": "Alice", "c": "Alice", "f": None},
            "B": {"v": 95, "c": 95, "f": None},
        },
    )
    db_session.add_all([row0, row1])
    await db_session.commit()

    # Mock Google Drive/Sheets create & update calls
    with (
        patch(
            "app.services.google_oauth.GoogleOAuthService.create_spreadsheet",
            new_callable=AsyncMock,
            return_value=GoogleSpreadsheetDetail(
                spreadsheet_id="new-google-sheet-999",
                title="VIBE Export - SourceSheet",
            ),
        ),
        patch(
            "app.services.google_oauth.GoogleOAuthService.update_sheet_values",
            new_callable=AsyncMock,
            return_value={"updatedRows": 2},
        ) as mock_update,
    ):
        export_payload = {
            "integration_id": str(integration.id),
            "source_sheet_id": str(sheet.id),
            "destination_mode": "create_new",
            "destination_sheet_name": "Sheet1",
            "new_spreadsheet_title": "VIBE Export - SourceSheet",
        }
        res = await api_client.post(
            "/api/v1/sync/export",
            json=export_payload,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert res.status_code == 200, res.text
        resp_data = res.json()
        assert resp_data["external_spreadsheet_id"] == "new-google-sheet-999"
        assert resp_data["rows_exported"] == 2
        assert resp_data["columns_exported"] == 2
        mock_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_spreadsheet_synchronization_bidirectional_and_conflicts(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Test spreadsheet sync mapping lifecycle, conflict detection, and resolution."""
    uid = uuid.uuid4().hex[:8]
    owner_email = f"syncmaster_{uid}@example.com"
    data, csrf_token, org_id = await register_and_login_helper(api_client, owner_email)
    user_id = uuid.UUID(data["user"]["id"])

    integration = await create_connected_integration(db_session, org_id, user_id)

    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    ws_id = ws_list[0].id

    # 1. Create workbook & sheet
    wb = Workbook(
        organization_id=org_id, workspace_id=ws_id, name="Sync WB", created_by_user_id=user_id
    )
    db_session.add(wb)
    await db_session.flush()

    sheet = Sheet(workbook_id=wb.id, organization_id=org_id, name="SyncSheet")
    db_session.add(sheet)
    await db_session.flush()

    row0 = SheetRow(
        sheet_id=sheet.id,
        organization_id=org_id,
        row_index=0,
        cells={
            "A": {"v": "Old Vibe Value", "c": "Old Vibe Value", "f": None},
        },
    )
    db_session.add(row0)
    await db_session.commit()

    # 2. Create sync mapping
    mapping_payload = {
        "integration_id": str(integration.id),
        "workbook_id": str(wb.id),
        "sheet_id": str(sheet.id),
        "external_spreadsheet_id": "ext-sheet-777",
        "external_sheet_id": "0",
        "external_sheet_name": "Sheet1",
        "direction": "bidirectional",
        "conflict_policy": "manual",
    }
    create_map_res = await api_client.post(
        "/api/v1/sync/mappings",
        json=mapping_payload,
        headers={"X-CSRF-Token": csrf_token},
    )
    assert create_map_res.status_code == 201, create_map_res.text
    mapping_data = create_map_res.json()
    mapping_id = uuid.UUID(mapping_data["id"])

    # 3. Trigger sync with conflicting external value
    mock_ext_values = [["External Conflicting Value"]]
    with patch(
        "app.services.google_oauth.GoogleOAuthService.get_sheet_values",
        new_callable=AsyncMock,
        return_value=GoogleSpreadsheetValuesResponse(
            spreadsheet_id="ext-sheet-777",
            sheet_name="Sheet1",
            values=mock_ext_values,
            total_rows=1,
            total_columns=1,
        ),
    ):
        sync_res = await api_client.post(
            f"/api/v1/sync/mappings/{mapping_id}/trigger",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert sync_res.status_code == 200, sync_res.text
        sync_data = sync_res.json()
        assert sync_data["status"] == "conflict"
        assert sync_data["conflict_count"] == 1

        # 4. List conflicts
        conflicts_res = await api_client.get(
            f"/api/v1/sync/mappings/{mapping_id}/conflicts",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert conflicts_res.status_code == 200
        conflicts_list = conflicts_res.json()
        assert len(conflicts_list) == 1
        conflict_item = conflicts_list[0]
        assert conflict_item["row_index"] == 0
        assert conflict_item["column_letter"] == "A"
        assert conflict_item["status"] == "detected"
        conflict_id = conflict_item["id"]

        # 5. Resolve conflict with keep_vibe
        with patch(
            "app.services.google_oauth.GoogleOAuthService.update_sheet_values",
            new_callable=AsyncMock,
            return_value={"updatedCells": 1},
        ) as mock_update_val:
            resolve_res = await api_client.post(
                f"/api/v1/sync/conflicts/{conflict_id}/resolve",
                json={"resolution_choice": "keep_vibe"},
                headers={"X-CSRF-Token": csrf_token},
            )
            assert resolve_res.status_code == 200, resolve_res.text
            resolved_data = resolve_res.json()
            assert resolved_data["status"] == "resolved"
            assert resolved_data["resolution_choice"] == "keep_vibe"
            mock_update_val.assert_awaited_once()

        # 6. Verify mapping status returned to idle
        get_map_res = await api_client.get(
            f"/api/v1/sync/mappings/{mapping_id}",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert get_map_res.status_code == 200
        assert get_map_res.json()["status"] == "idle"

        # 7. Check sync run history
        runs_res = await api_client.get(
            f"/api/v1/sync/mappings/{mapping_id}/runs",
            headers={"X-CSRF-Token": csrf_token},
        )
        assert runs_res.status_code == 200
        runs_list = runs_res.json()
        assert len(runs_list) >= 1


@pytest.mark.asyncio
async def test_sync_tenant_isolation_and_rbac(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Ensure organization B cannot access org A's sync mapping, and Viewer cannot trigger sync."""
    # Org A Owner
    uid_a = uuid.uuid4().hex[:8]
    data_a, csrf_a, org_a_id = await register_and_login_helper(
        api_client, f"owner_a_{uid_a}@example.com"
    )
    user_a_id = uuid.UUID(data_a["user"]["id"])
    integration_a = await create_connected_integration(db_session, org_a_id, user_a_id)

    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_a_id)
    wb_a = Workbook(
        organization_id=org_a_id,
        workspace_id=ws_list[0].id,
        name="Org A WB",
        created_by_user_id=user_a_id,
    )
    db_session.add(wb_a)
    await db_session.flush()
    sheet_a = Sheet(workbook_id=wb_a.id, organization_id=org_a_id, name="SheetA")
    db_session.add(sheet_a)
    await db_session.commit()

    # Create mapping in Org A
    map_res = await api_client.post(
        "/api/v1/sync/mappings",
        json={
            "integration_id": str(integration_a.id),
            "workbook_id": str(wb_a.id),
            "sheet_id": str(sheet_a.id),
            "external_spreadsheet_id": "google-sheet-a",
            "external_sheet_id": "0",
            "external_sheet_name": "Sheet1",
            "direction": "bidirectional",
        },
        headers={"X-CSRF-Token": csrf_a},
    )
    assert map_res.status_code == 201
    mapping_a_id = map_res.json()["id"]

    # Org B Owner
    uid_b = uuid.uuid4().hex[:8]
    _, csrf_b, org_b_id = await register_and_login_helper(
        api_client, f"owner_b_{uid_b}@example.com"
    )

    # 1. Org B attempts to get mapping A -> 404
    get_res = await api_client.get(
        f"/api/v1/sync/mappings/{mapping_a_id}",
        headers={"X-CSRF-Token": csrf_b},
    )
    assert get_res.status_code == 404

    # 2. Org B attempts to trigger sync on mapping A -> 404
    trigger_res = await api_client.post(
        f"/api/v1/sync/mappings/{mapping_a_id}/trigger",
        headers={"X-CSRF-Token": csrf_b},
    )
    assert trigger_res.status_code == 404

    # 3. Org A Viewer attempts to delete mapping -> 403 Forbidden
    viewer_email = f"viewer_{uid_a}@example.com"
    user_repo = UserRepository(db_session)
    viewer_user = await user_repo.create(
        User(
            email=viewer_email,
            normalized_email=viewer_email.lower(),
            display_name="Viewer User",
        )
    )
    role_repo = RoleRepository(db_session)
    viewer_role = await role_repo.get_by_slug("viewer", org_a_id)
    assert viewer_role is not None

    mem_repo = MembershipRepository(db_session)
    viewer_mem = await mem_repo.create(
        OrganizationMembership(
            user_id=viewer_user.id,
            organization_id=org_a_id,
        )
    )
    db_session.add(
        MembershipRole(
            membership_id=viewer_mem.id,
            role_id=viewer_role.id,
            organization_id=org_a_id,
        )
    )
    await db_session.commit()

    from app.services.session import SessionService

    sess_service = SessionService(db_session)
    _, viewer_token, viewer_csrf = await sess_service.create_session(
        user_id=viewer_user.id,
        ip_address="127.0.0.1",
        user_agent="pytest",
        active_organization_id=org_a_id,
    )
    await db_session.commit()

    api_client.cookies.set(settings.COOKIE_NAME, viewer_token)

    del_res = await api_client.delete(
        f"/api/v1/sync/mappings/{mapping_a_id}",
        headers={"X-CSRF-Token": viewer_csrf, "Origin": "http://localhost:3000"},
    )
    assert del_res.status_code == 403
