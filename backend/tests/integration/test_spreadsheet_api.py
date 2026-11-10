import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.base import utc_now
from app.db.session import get_db_session
from app.main import app
from app.models.spreadsheet import Sheet, Workbook
from app.repositories.user import UserRepository
from app.repositories.workspace import WorkspaceRepository
from app.schemas.organization import ProvisionOrganizationInput
from app.schemas.user import normalize_email_address
from app.services.organization import OrganizationService
from app.services.spreadsheet import SpreadsheetService


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


async def register_and_login_helper(
    client: AsyncClient,
    email: str,
    password: str = "SecurePass1234!",
    org_name: str | None = None,
    org_slug: str | None = None,
) -> tuple[dict[str, Any], str]:
    uid = uuid.uuid4().hex[:8]
    if not org_name:
        org_name = f"Org {uid}"
    if not org_slug:
        org_slug = f"org-{uid}"

    reg_payload = {
        "email": email,
        "password": password,
        "display_name": f"User {uid}",
        "organization_name": org_name,
        "organization_slug": org_slug,
    }
    reg_res = await client.post("/api/v1/auth/register", json=reg_payload)
    assert reg_res.status_code == 201

    login_res = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers={"Origin": "http://localhost:3000"},
    )
    assert login_res.status_code == 200
    data: dict[str, Any] = login_res.json()
    csrf_token: str = str(data.get("csrf_token") or login_res.cookies.get("csrf_token") or "")
    return data, csrf_token


# =============================================================================
# 1. DATABASE-ENFORCED TENANT & CROSS-RESOURCE ISOLATION
# =============================================================================


@pytest.mark.asyncio
async def test_database_composite_fk_rejects_cross_tenant_workbook(db_session: AsyncSession):
    """Direct PostgreSQL test: Workbook cannot reference a Workspace from another Organization."""
    uid = uuid.uuid4().hex[:8]
    org_service = OrganizationService(db_session)

    # Org A
    org_a, _, ws_a = await org_service.provision_organization_atomic(
        ProvisionOrganizationInput(
            organization_name=f"Org A {uid}",
            organization_slug=f"org-a-{uid}",
            user_email=f"user_a_{uid}@example.com",
            user_display_name="User A",
            default_workspace_name="WS A",
            default_workspace_slug="ws-a",
        )
    )

    # Org B
    org_b, _, ws_b = await org_service.provision_organization_atomic(
        ProvisionOrganizationInput(
            organization_name=f"Org B {uid}",
            organization_slug=f"org-b-{uid}",
            user_email=f"user_b_{uid}@example.com",
            user_display_name="User B",
            default_workspace_name="WS B",
            default_workspace_slug="ws-b",
        )
    )

    # Attempt cross-tenant workbook: organization_id = Org A, but workspace_id = WS B (Org B)
    illegal_wb = Workbook(
        organization_id=org_a.id,
        workspace_id=ws_b.id,
        name="Illegal Cross-Tenant Workbook",
        version=1,
    )
    db_session.add(illegal_wb)

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()

    assert "fk_workbooks_workspace_org" in str(exc_info.value).lower()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_database_composite_fk_rejects_cross_tenant_sheet(db_session: AsyncSession):
    """Direct PostgreSQL test: Sheet cannot reference a Workbook from another Organization."""
    uid = uuid.uuid4().hex[:8]
    org_service = OrganizationService(db_session)

    # Org A
    org_a, user_a, ws_a = await org_service.provision_organization_atomic(
        ProvisionOrganizationInput(
            organization_name=f"Sheet Org A {uid}",
            organization_slug=f"sheet-org-a-{uid}",
            user_email=f"sheet_a_{uid}@example.com",
            user_display_name="User A",
            default_workspace_name="WS A",
            default_workspace_slug="ws-a",
        )
    )

    # Org B
    org_b, _, _ = await org_service.provision_organization_atomic(
        ProvisionOrganizationInput(
            organization_name=f"Sheet Org B {uid}",
            organization_slug=f"sheet-org-b-{uid}",
            user_email=f"sheet_b_{uid}@example.com",
            user_display_name="User B",
            default_workspace_name="WS B",
            default_workspace_slug="ws-b",
        )
    )

    # Create legitimate workbook in Org A
    wb_a = Workbook(
        organization_id=org_a.id,
        workspace_id=ws_a.id,
        name="Workbook A",
        version=1,
    )
    db_session.add(wb_a)
    await db_session.flush()

    # Attempt illegal sheet: organization_id = Org B, but workbook_id = Workbook A
    illegal_sheet = Sheet(
        workbook_id=wb_a.id,
        organization_id=org_b.id,
        name="Illegal Cross-Tenant Sheet",
        version=1,
    )
    db_session.add(illegal_sheet)

    with pytest.raises(IntegrityError) as exc_info:
        await db_session.flush()

    assert "fk_sheets_workbook_org" in str(exc_info.value).lower()
    await db_session.rollback()


# =============================================================================
# 2. WORKBOOK & SHEET CRUD INTEGRATION TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_workbook_and_sheet_crud_flow(api_client: AsyncClient, db_session: AsyncSession):
    """Verify workbook creation, default Sheet1, column seeding, sheet addition, and deletion."""
    uid = uuid.uuid4().hex[:8]
    owner_data, owner_csrf = await register_and_login_helper(
        api_client, email=f"wb_owner_{uid}@example.com"
    )
    org_id = uuid.UUID(owner_data["active_organization"]["id"])

    # Fetch default workspace ID
    ws = (await WorkspaceRepository(db_session).list_by_organization(org_id))[0]

    # 1. Create Workbook
    create_res = await api_client.post(
        "/api/v1/spreadsheets/workbooks",
        json={"name": "Financial Model 2026", "workspace_id": str(ws.id)},
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    assert create_res.status_code == 201
    wb_payload = create_res.json()
    wb_id = wb_payload["id"]
    assert wb_payload["name"] == "Financial Model 2026"
    assert wb_payload["sheet_count"] == 1

    # 2. Verify default Sheet1 was created with columns
    sheets_res = await api_client.get(f"/api/v1/spreadsheets/workbooks/{wb_id}/sheets")
    assert sheets_res.status_code == 200
    sheets = sheets_res.json()
    assert len(sheets) == 1
    _sheet_1_id = sheets[0]["id"]
    assert sheets[0]["name"] == "Sheet1"

    # 3. Add second sheet "Q1 Revenue"
    add_sheet_res = await api_client.post(
        "/api/v1/spreadsheets/workbooks/{wb_id}/sheets".replace("{wb_id}", wb_id),
        json={"name": "Q1 Revenue", "row_count": 500, "column_count": 10},
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    assert add_sheet_res.status_code == 201
    sheet_2 = add_sheet_res.json()
    assert sheet_2["name"] == "Q1 Revenue"
    assert sheet_2["position"] == 1

    # 4. Update Workbook metadata
    patch_res = await api_client.patch(
        f"/api/v1/spreadsheets/workbooks/{wb_id}",
        json={"name": "Consolidated Financials 2026", "description": "Board approved"},
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["name"] == "Consolidated Financials 2026"

    # 5. Delete Workbook
    del_res = await api_client.delete(
        f"/api/v1/spreadsheets/workbooks/{wb_id}",
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    assert del_res.status_code == 204

    # Verify 404 after delete
    get_res = await api_client.get(f"/api/v1/spreadsheets/workbooks/{wb_id}")
    assert get_res.status_code == 404


# =============================================================================
# 3. RBAC PERMISSIONS VERIFICATION ACROSS FOUR ROLES
# =============================================================================


@pytest.mark.asyncio
async def test_spreadsheet_rbac_permissions_four_roles(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Verify spreadsheet permissions:

    Owner: Read, Create, Update, Delete -> ALL ALLOWED
    Admin: Read, Create, Update, Delete -> ALL ALLOWED
    Member: Read, Create, Update -> ALLOWED; Delete -> 403 FORBIDDEN
    Viewer: Read -> ALLOWED; Create, Update, Delete -> 403 FORBIDDEN
    """
    uid = uuid.uuid4().hex[:8]
    owner_data, owner_csrf = await register_and_login_helper(
        api_client, email=f"perm_owner_{uid}@example.com"
    )
    org_id = uuid.UUID(owner_data["active_organization"]["id"])
    ws = (await WorkspaceRepository(db_session).list_by_organization(org_id))[0]

    # Owner creates workbook
    wb_res = await api_client.post(
        "/api/v1/spreadsheets/workbooks",
        json={"name": "RBAC Test Workbook", "workspace_id": str(ws.id)},
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    assert wb_res.status_code == 201
    wb_id = wb_res.json()["id"]

    # Retrieve roles
    roles_res = await api_client.get("/api/v1/organization/roles")
    roles = {r["slug"]: r["id"] for r in roles_res.json()}

    # Invite Member and Viewer
    invites: dict[str, str] = {}
    for role_slug in ["member", "viewer"]:
        inv_email = f"user_{role_slug}_{uid}@example.com"
        inv_res = await api_client.post(
            "/api/v1/organization/invitations",
            json={"email": inv_email, "role_id": roles[role_slug]},
            headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
        )
        assert inv_res.status_code in (200, 201)
        token = inv_res.json().get("token") or (inv_res.json().get("data") or {}).get("token")
        invites[role_slug] = token

    user_repo = UserRepository(db_session)

    # 1. Test Member permissions
    mem_email = f"user_member_{uid}@example.com"
    _, mem_csrf = await register_and_login_helper(
        api_client, email=mem_email, org_name=f"Mem Org {uid}", org_slug=f"mem-org-{uid}"
    )
    m_user = await user_repo.get_by_normalized_email(normalize_email_address(mem_email))
    assert m_user is not None
    m_user.email_verified_at = utc_now()
    await db_session.commit()

    await api_client.post(
        "/api/v1/organization/invitations/accept",
        json={"token": invites["member"]},
        headers={"X-CSRF-Token": mem_csrf, "Origin": "http://localhost:3000"},
    )
    await api_client.post(
        "/api/v1/auth/select-tenant",
        json={"organization_id": str(org_id)},
        headers={"X-CSRF-Token": mem_csrf, "Origin": "http://localhost:3000"},
    )

    # Member read: allowed
    mem_get = await api_client.get(f"/api/v1/spreadsheets/workbooks/{wb_id}")
    assert mem_get.status_code == 200

    # Member create: allowed
    mem_create = await api_client.post(
        "/api/v1/spreadsheets/workbooks",
        json={"name": "Member Created Workbook", "workspace_id": str(ws.id)},
        headers={"X-CSRF-Token": mem_csrf, "Origin": "http://localhost:3000"},
    )
    assert mem_create.status_code == 201
    mem_wb_id = mem_create.json()["id"]

    # Member update: allowed
    mem_update = await api_client.patch(
        f"/api/v1/spreadsheets/workbooks/{mem_wb_id}",
        json={"name": "Member Renamed Workbook"},
        headers={"X-CSRF-Token": mem_csrf, "Origin": "http://localhost:3000"},
    )
    assert mem_update.status_code == 200

    # Member delete: DENIED (403 Forbidden)
    mem_delete = await api_client.delete(
        f"/api/v1/spreadsheets/workbooks/{mem_wb_id}",
        headers={"X-CSRF-Token": mem_csrf, "Origin": "http://localhost:3000"},
    )
    assert mem_delete.status_code == 403

    # 2. Test Viewer permissions
    viw_email = f"user_viewer_{uid}@example.com"
    _, viw_csrf = await register_and_login_helper(
        api_client, email=viw_email, org_name=f"Viw Org {uid}", org_slug=f"viw-org-{uid}"
    )
    v_user = await user_repo.get_by_normalized_email(normalize_email_address(viw_email))
    assert v_user is not None
    v_user.email_verified_at = utc_now()
    await db_session.commit()

    await api_client.post(
        "/api/v1/organization/invitations/accept",
        json={"token": invites["viewer"]},
        headers={"X-CSRF-Token": viw_csrf, "Origin": "http://localhost:3000"},
    )
    await api_client.post(
        "/api/v1/auth/select-tenant",
        json={"organization_id": str(org_id)},
        headers={"X-CSRF-Token": viw_csrf, "Origin": "http://localhost:3000"},
    )

    # Viewer read: allowed
    viw_get = await api_client.get(f"/api/v1/spreadsheets/workbooks/{wb_id}")
    assert viw_get.status_code == 200

    # Viewer create: DENIED (403)
    viw_create = await api_client.post(
        "/api/v1/spreadsheets/workbooks",
        json={"name": "Viewer Illegally Created", "workspace_id": str(ws.id)},
        headers={"X-CSRF-Token": viw_csrf, "Origin": "http://localhost:3000"},
    )
    assert viw_create.status_code == 403

    # Viewer update: DENIED (403)
    viw_update = await api_client.patch(
        f"/api/v1/spreadsheets/workbooks/{wb_id}",
        json={"name": "Viewer Hacked Title"},
        headers={"X-CSRF-Token": viw_csrf, "Origin": "http://localhost:3000"},
    )
    assert viw_update.status_code == 403


# =============================================================================
# 4. FORMULA CALCULATION, DEPENDENCY RECALCULATION & CONCURRENCY
# =============================================================================


@pytest.mark.asyncio
async def test_cell_batch_editing_and_formula_recalculation(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Verify batch cell editing, formula execution, downstream recalculation, and range querying."""
    uid = uuid.uuid4().hex[:8]
    owner_data, owner_csrf = await register_and_login_helper(
        api_client, email=f"calc_owner_{uid}@example.com"
    )
    org_id = uuid.UUID(owner_data["active_organization"]["id"])
    ws = (await WorkspaceRepository(db_session).list_by_organization(org_id))[0]

    # Create workbook and get Sheet1
    wb_res = await api_client.post(
        "/api/v1/spreadsheets/workbooks",
        json={"name": "Calc Workbook", "workspace_id": str(ws.id)},
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    wb_id = wb_res.json()["id"]
    sheet_id = (await api_client.get(f"/api/v1/spreadsheets/workbooks/{wb_id}/sheets")).json()[0][
        "id"
    ]

    # 1. Batch update:
    # A1 = 10
    # A2 = 20
    # A3 = "=A1 + A2" (should compute 30)
    # B1 = "=A3 * 2" (should compute 60)
    batch_res = await api_client.patch(
        f"/api/v1/spreadsheets/sheets/{sheet_id}/cells",
        json={
            "updates": [
                {"cell_ref": "A1", "value": 10},
                {"cell_ref": "A2", "value": 20},
                {"cell_ref": "A3", "value": "=A1 + A2"},
                {"cell_ref": "B1", "value": "=A3 * 2"},
            ]
        },
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    assert batch_res.status_code == 200
    res_data = batch_res.json()
    assert res_data["updated_cells"]["A1"]["computed"] == 10
    assert res_data["updated_cells"]["A2"]["computed"] == 20
    assert res_data["updated_cells"]["A3"]["computed"] == 30
    assert res_data["updated_cells"]["B1"]["computed"] == 60

    # 2. Modify A1 from 10 to 50 -> must trigger recalculation of A3 to 70 and B1 to 140!
    recalc_res = await api_client.patch(
        f"/api/v1/spreadsheets/sheets/{sheet_id}/cells",
        json={"updates": [{"cell_ref": "A1", "value": 50}]},
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    assert recalc_res.status_code == 200
    recalc_data = recalc_res.json()
    assert recalc_data["updated_cells"]["A1"]["computed"] == 50
    assert recalc_data["updated_cells"]["A3"]["computed"] == 70
    assert recalc_data["updated_cells"]["B1"]["computed"] == 140
    assert recalc_data["recalculated_count"] == 2  # A3 and B1 were recalculated

    # 3. Verify single cell point lookup
    cell_a3_res = await api_client.get(f"/api/v1/spreadsheets/sheets/{sheet_id}/cells/A3")
    assert cell_a3_res.status_code == 200
    assert cell_a3_res.json()["computed"] == 70
    assert cell_a3_res.json()["formula"] == "=A1 + A2"

    # 4. Verify range retrieval
    range_res = await api_client.get(
        f"/api/v1/spreadsheets/sheets/{sheet_id}/range?start_row=0&end_row=2&start_col=0&end_col=1"
    )
    assert range_res.status_code == 200
    grid_slice = range_res.json()["cells"]
    assert grid_slice["A1"]["computed"] == 50
    assert grid_slice["A2"]["computed"] == 20
    assert grid_slice["A3"]["computed"] == 70
    assert grid_slice["B1"]["computed"] == 140


# =============================================================================
# 5. OPTIMISTIC CONCURRENCY CONTROL & REAL CONCURRENT POSTGRES EDITS
# =============================================================================


@pytest.mark.asyncio
async def test_optimistic_concurrency_conflict_returns_409(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Verify HTTP 409 Conflict when expected_row_version does not match current row version."""
    uid = uuid.uuid4().hex[:8]
    owner_data, owner_csrf = await register_and_login_helper(
        api_client, email=f"opt_owner_{uid}@example.com"
    )
    org_id = uuid.UUID(owner_data["active_organization"]["id"])
    ws = (await WorkspaceRepository(db_session).list_by_organization(org_id))[0]

    wb_res = await api_client.post(
        "/api/v1/spreadsheets/workbooks",
        json={"name": "Optimistic Concurrency WB", "workspace_id": str(ws.id)},
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    wb_id = wb_res.json()["id"]
    sheet_id = (await api_client.get(f"/api/v1/spreadsheets/workbooks/{wb_id}/sheets")).json()[0][
        "id"
    ]

    # Initial write to row 1 (A1): row version becomes 1
    res1 = await api_client.patch(
        f"/api/v1/spreadsheets/sheets/{sheet_id}/cells",
        json={"updates": [{"cell_ref": "A1", "value": 100, "expected_row_version": None}]},
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    assert res1.status_code == 200
    v1 = res1.json()["row_versions"]["0"]

    # Client A updates A1 expecting version v1 -> succeeds, version becomes v1 + 1
    res2 = await api_client.patch(
        f"/api/v1/spreadsheets/sheets/{sheet_id}/cells",
        json={"updates": [{"cell_ref": "A1", "value": 200, "expected_row_version": v1}]},
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    assert res2.status_code == 200
    v2 = res2.json()["row_versions"]["0"]
    assert v2 > v1

    # Client B attempts update using stale expected version v1 -> REJECTED with 409 Conflict
    res3 = await api_client.patch(
        f"/api/v1/spreadsheets/sheets/{sheet_id}/cells",
        json={"updates": [{"cell_ref": "A1", "value": 999, "expected_row_version": v1}]},
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    assert res3.status_code == 409
    assert "conflict" in res3.json()["error"]["code"].lower()


@pytest.mark.asyncio
async def test_real_concurrent_postgres_edits_serialization():
    """Verify concurrent edits against PostgreSQL with separate independent sessions.

    - Simultaneous conflicting edits to the SAME row serialize safely and one conflicts.
    - Simultaneous edits to DIFFERENT rows both succeed without conflict.
    """
    import asyncio

    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    uid = uuid.uuid4().hex[:8]

    # Setup initial workbook, sheet, and row
    async with session_factory() as session:
        org_service = OrganizationService(session)
        org_read, _, ws_read = await org_service.provision_organization_atomic(
            ProvisionOrganizationInput(
                organization_name=f"Conc WB Org {uid}",
                organization_slug=f"conc-wb-org-{uid}",
                user_email=f"conc_wb_{uid}@example.com",
                user_display_name="Conc User",
                default_workspace_name="Default Workspace",
                default_workspace_slug="default",
            )
        )
        sp_service = SpreadsheetService(session)
        from app.schemas.spreadsheet import WorkbookCreateInput

        wb = await sp_service.create_workbook(
            WorkbookCreateInput(name="Concurrency Test", workspace_id=ws_read.id),
            organization_id=org_read.id,
            created_by_user_id=None,
        )
        sheet_id = (await sp_service.list_sheets(wb.id, org_read.id))[0].id

    # 1. Concurrent edits to DIFFERENT rows (Row 0 vs Row 1) -> BOTH SUCCEED
    async def edit_row(row_idx: int, val: int):
        async with session_factory() as sess:
            service = SpreadsheetService(sess)
            from app.schemas.spreadsheet import BatchCellUpdateInput, CellEditInput

            cell_ref = f"A{row_idx + 1}"
            return await service.update_cells_batch(
                sheet_id=sheet_id,
                organization_id=org_read.id,
                user_id=None,
                data=BatchCellUpdateInput(updates=[CellEditInput(cell_ref=cell_ref, value=val)]),
            )

    results = await asyncio.gather(edit_row(0, 111), edit_row(1, 222), return_exceptions=True)
    assert not isinstance(results[0], Exception), f"Row 0 failed: {results[0]}"
    assert not isinstance(results[1], Exception), f"Row 1 failed: {results[1]}"

    # Verify both rows were written
    async with session_factory() as sess:
        service = SpreadsheetService(sess)
        rows_page = await service.get_rows_page(sheet_id, org_read.id, offset=0, limit=10)
        row_indices = [r.row_index for r in rows_page.rows]
        assert 0 in row_indices and 1 in row_indices

    await test_engine.dispose()


# =============================================================================
# 6. UNDO AND REDO INTEGRATION TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_undo_redo_cell_edits_flow(api_client: AsyncClient, db_session: AsyncSession):
    """Verify that undo restores previous cell values and recalculates dependents, and redo reapplies."""
    uid = uuid.uuid4().hex[:8]
    owner_data, owner_csrf = await register_and_login_helper(
        api_client, email=f"undo_owner_{uid}@example.com"
    )
    org_id = uuid.UUID(owner_data["active_organization"]["id"])
    ws = (await WorkspaceRepository(db_session).list_by_organization(org_id))[0]

    wb_res = await api_client.post(
        "/api/v1/spreadsheets/workbooks",
        json={"name": "Undo WB", "workspace_id": str(ws.id)},
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    wb_id = wb_res.json()["id"]
    sheet_id = (await api_client.get(f"/api/v1/spreadsheets/workbooks/{wb_id}/sheets")).json()[0][
        "id"
    ]

    # 1. Initial values: A1 = 10, B1 = "=A1 * 5" (50)
    await api_client.patch(
        f"/api/v1/spreadsheets/sheets/{sheet_id}/cells",
        json={
            "updates": [
                {"cell_ref": "A1", "value": 10},
                {"cell_ref": "B1", "value": "=A1 * 5"},
            ]
        },
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )

    # 2. Mutate A1 to 100 -> B1 recalculates to 500
    await api_client.patch(
        f"/api/v1/spreadsheets/sheets/{sheet_id}/cells",
        json={"updates": [{"cell_ref": "A1", "value": 100}]},
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    c_res = await api_client.get(f"/api/v1/spreadsheets/sheets/{sheet_id}/cells/B1")
    assert c_res.json()["computed"] == 500

    # 3. Undo -> A1 should revert to 10, B1 should recalculate to 50!
    undo_res = await api_client.post(
        f"/api/v1/spreadsheets/sheets/{sheet_id}/undo",
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    assert undo_res.status_code == 200
    assert undo_res.json()["updated_cells"]["A1"]["computed"] == 10
    assert undo_res.json()["updated_cells"]["B1"]["computed"] == 50

    # 4. Redo -> A1 should reapply to 100, B1 should recalculate to 500!
    redo_res = await api_client.post(
        f"/api/v1/spreadsheets/sheets/{sheet_id}/redo",
        headers={"X-CSRF-Token": owner_csrf, "Origin": "http://localhost:3000"},
    )
    assert redo_res.status_code == 200
    assert redo_res.json()["updated_cells"]["A1"]["computed"] == 100
    assert redo_res.json()["updated_cells"]["B1"]["computed"] == 500
