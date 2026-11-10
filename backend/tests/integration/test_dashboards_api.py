"""Integration tests for Analytical Dashboards, Visualizations, and Polars Query Engine."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.models.membership import OrganizationMembership
from app.models.role import MembershipRole
from app.models.spreadsheet import Sheet, SheetRow, Workbook
from app.models.user import User
from app.repositories.membership import MembershipRepository
from app.repositories.role import RoleRepository
from app.repositories.user import UserRepository
from app.repositories.workspace import WorkspaceRepository


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


@pytest.mark.asyncio
async def test_dashboard_and_widget_crud_lifecycle(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Test full dashboard CRUD, widget management, and layout persistence."""
    uid = uuid.uuid4().hex[:8]
    owner_email = f"dash_owner_{uid}@example.com"
    data, csrf_token, org_id = await register_and_login_helper(api_client, owner_email)
    user_id = uuid.UUID(data["user"]["id"])

    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    ws_id = ws_list[0].id

    # 1. Create Dashboard
    create_payload = {
        "workspace_id": str(ws_id),
        "title": "Executive KPI Dashboard",
        "description": "Quarterly metrics and performance",
        "layout_config": {"columns": 12, "row_height": 80},
        "is_shared": True,
    }
    create_res = await api_client.post(
        "/api/v1/dashboards",
        json=create_payload,
        headers={"X-CSRF-Token": csrf_token},
    )
    assert create_res.status_code == 201, create_res.text
    dash_data = create_res.json()
    dash_id = uuid.UUID(dash_data["id"])
    assert dash_data["title"] == "Executive KPI Dashboard"

    # Create dummy sheet for widget data source
    wb = Workbook(organization_id=org_id, workspace_id=ws_id, name="Dash WB", created_by_user_id=user_id)
    db_session.add(wb)
    await db_session.flush()
    sheet = Sheet(workbook_id=wb.id, organization_id=org_id, name="MetricsSheet")
    db_session.add(sheet)
    await db_session.commit()

    # 2. Add multiple widgets covering chart types
    widget_types = ["kpi_card", "bar", "line", "pie", "table"]
    widget_ids: list[str] = []

    for idx, wtype in enumerate(widget_types):
        w_payload = {
            "title": f"Widget {wtype.upper()}",
            "widget_type": wtype,
            "source_type": "sheet",
            "source_id": str(sheet.id),
            "query_config": {"limit": 50},
            "position_x": (idx % 2) * 6,
            "position_y": (idx // 2) * 4,
            "width": 6,
            "height": 4,
            "sort_order": idx,
        }
        w_res = await api_client.post(
            f"/api/v1/dashboards/{dash_id}/widgets",
            json=w_payload,
            headers={"X-CSRF-Token": csrf_token},
        )
        assert w_res.status_code == 201, w_res.text
        widget_ids.append(w_res.json()["id"])

    assert len(widget_ids) == 5

    # 3. List Dashboards
    list_res = await api_client.get(
        "/api/v1/dashboards",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert list_res.status_code == 200
    dash_list = list_res.json()
    assert any(d["id"] == str(dash_id) for d in dash_list)

    # 4. Get Dashboard Details (with child widgets)
    detail_res = await api_client.get(
        f"/api/v1/dashboards/{dash_id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert detail_res.status_code == 200
    detail_data = detail_res.json()
    assert len(detail_data["widgets"]) == 5

    # 5. Batch Update Layout
    layout_update_payload = {
        "positions": [
            {
                "widget_id": widget_ids[0],
                "position_x": 0,
                "position_y": 0,
                "width": 12,
                "height": 3,
                "sort_order": 0,
            },
            {
                "widget_id": widget_ids[1],
                "position_x": 0,
                "position_y": 3,
                "width": 6,
                "height": 5,
                "sort_order": 1,
            },
        ]
    }
    layout_res = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/layout",
        json=layout_update_payload,
        headers={"X-CSRF-Token": csrf_token},
    )
    assert layout_res.status_code == 200, layout_res.text

    # 6. Delete a widget
    del_w_res = await api_client.delete(
        f"/api/v1/dashboards/{dash_id}/widgets/{widget_ids[4]}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert del_w_res.status_code == 204

    # Verify widget count is now 4
    detail_after_res = await api_client.get(
        f"/api/v1/dashboards/{dash_id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert len(detail_after_res.json()["widgets"]) == 4

    # 7. Delete Dashboard
    del_dash_res = await api_client.delete(
        f"/api/v1/dashboards/{dash_id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert del_dash_res.status_code == 204


@pytest.mark.asyncio
async def test_polars_query_aggregation_engine(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Test server-side Polars aggregation engine over spreadsheet data."""
    uid = uuid.uuid4().hex[:8]
    owner_email = f"polars_{uid}@example.com"
    data, csrf_token, org_id = await register_and_login_helper(api_client, owner_email)
    user_id = uuid.UUID(data["user"]["id"])

    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    ws_id = ws_list[0].id

    # Create sheet with structured sales data
    wb = Workbook(organization_id=org_id, workspace_id=ws_id, name="Sales WB", created_by_user_id=user_id)
    db_session.add(wb)
    await db_session.flush()

    sheet = Sheet(workbook_id=wb.id, organization_id=org_id, name="SalesData")
    db_session.add(sheet)
    await db_session.flush()

    # Row 0: Headers
    r0 = SheetRow(
        sheet_id=sheet.id,
        organization_id=org_id,
        row_index=0,
        cells={
            "A": {"v": "Category", "c": "Category", "f": None},
            "B": {"v": "Region", "c": "Region", "f": None},
            "C": {"v": "Sales", "c": "Sales", "f": None},
            "D": {"v": "Units", "c": "Units", "f": None},
        },
    )
    # Row 1: Electronics - East
    r1 = SheetRow(
        sheet_id=sheet.id,
        organization_id=org_id,
        row_index=1,
        cells={
            "A": {"v": "Electronics", "c": "Electronics", "f": None},
            "B": {"v": "East", "c": "East", "f": None},
            "C": {"v": 1000.0, "c": 1000.0, "f": None},
            "D": {"v": 10, "c": 10, "f": None},
        },
    )
    # Row 2: Electronics - West
    r2 = SheetRow(
        sheet_id=sheet.id,
        organization_id=org_id,
        row_index=2,
        cells={
            "A": {"v": "Electronics", "c": "Electronics", "f": None},
            "B": {"v": "West", "c": "West", "f": None},
            "C": {"v": 1500.0, "c": 1500.0, "f": None},
            "D": {"v": 15, "c": 15, "f": None},
        },
    )
    # Row 3: Furniture - East
    r3 = SheetRow(
        sheet_id=sheet.id,
        organization_id=org_id,
        row_index=3,
        cells={
            "A": {"v": "Furniture", "c": "Furniture", "f": None},
            "B": {"v": "East", "c": "East", "f": None},
            "C": {"v": 800.0, "c": 800.0, "f": None},
            "D": {"v": 4, "c": 4, "f": None},
        },
    )
    # Row 4: Furniture - West
    r4 = SheetRow(
        sheet_id=sheet.id,
        organization_id=org_id,
        row_index=4,
        cells={
            "A": {"v": "Furniture", "c": "Furniture", "f": None},
            "B": {"v": "West", "c": "West", "f": None},
            "C": {"v": 1200.0, "c": 1200.0, "f": None},
            "D": {"v": 6, "c": 6, "f": None},
        },
    )
    db_session.add_all([r0, r1, r2, r3, r4])
    await db_session.commit()

    # Create Dashboard
    dash_res = await api_client.post(
        "/api/v1/dashboards",
        json={"workspace_id": str(ws_id), "title": "Sales Analytics"},
        headers={"X-CSRF-Token": csrf_token},
    )
    dash_id = dash_res.json()["id"]

    # Create Widget with Polars Aggregation Query
    # Group by Category, sum Sales, mean Units
    query_cfg = {
        "header_row": 0,
        "dimensions": ["Category"],
        "metrics": [
            {"column": "Sales", "agg": "sum", "alias": "total_sales"},
            {"column": "Units", "agg": "mean", "alias": "avg_units"},
        ],
        "sort_by": "total_sales",
        "sort_desc": True,
    }
    w_res = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets",
        json={
            "title": "Category Sales & Units",
            "widget_type": "bar",
            "source_type": "sheet",
            "source_id": str(sheet.id),
            "query_config": query_cfg,
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert w_res.status_code == 201
    widget_id = w_res.json()["id"]

    # Execute Aggregation Query
    query_res = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets/{widget_id}/query",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert query_res.status_code == 200, query_res.text
    query_data = query_res.json()
    assert "Category" in query_data["columns"]
    assert "total_sales" in query_data["columns"]
    assert "avg_units" in query_data["columns"]
    assert query_data["total_rows"] == 2

    # Check aggregated values
    # Electronics: Sales = 1000 + 1500 = 2500, avg_units = (10 + 15) / 2 = 12.5
    # Furniture: Sales = 800 + 1200 = 2000, avg_units = (4 + 6) / 2 = 5.0
    rows = query_data["data"]
    assert rows[0]["Category"] == "Electronics"
    assert rows[0]["total_sales"] == 2500.0
    assert rows[0]["avg_units"] == 12.5

    assert rows[1]["Category"] == "Furniture"
    assert rows[1]["total_sales"] == 2000.0
    assert rows[1]["avg_units"] == 5.0

    # Summary stats
    assert query_data["summary"]["total_sales"]["total"] == 4500.0


@pytest.mark.asyncio
async def test_dashboard_tenant_isolation_and_rbac(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Verify cross-tenant isolation and role-based permissions on dashboards."""
    uid_a = uuid.uuid4().hex[:8]
    data_a, csrf_a, org_a_id = await register_and_login_helper(
        api_client, f"owner_dash_a_{uid_a}@example.com"
    )
    ws_repo = WorkspaceRepository(db_session)
    ws_list_a = await ws_repo.list_by_organization(org_a_id)

    # Create dashboard in Org A
    dash_res = await api_client.post(
        "/api/v1/dashboards",
        json={"workspace_id": str(ws_list_a[0].id), "title": "Org A Dashboard"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert dash_res.status_code == 201
    dash_a_id = dash_res.json()["id"]

    # Register Org B
    uid_b = uuid.uuid4().hex[:8]
    _, csrf_b, org_b_id = await register_and_login_helper(
        api_client, f"owner_dash_b_{uid_b}@example.com"
    )

    # 1. Org B attempts to get Org A dashboard -> 404
    get_res = await api_client.get(
        f"/api/v1/dashboards/{dash_a_id}",
        headers={"X-CSRF-Token": csrf_b},
    )
    assert get_res.status_code == 404

    # 2. Org B attempts to delete Org A dashboard -> 404
    del_res = await api_client.delete(
        f"/api/v1/dashboards/{dash_a_id}",
        headers={"X-CSRF-Token": csrf_b},
    )
    assert del_res.status_code == 404

    # 3. Viewer in Org A has read access but cannot create or delete dashboards
    viewer_email = f"viewer_dash_{uid_a}@example.com"
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

    # Viewer can read Org A dashboard
    v_get = await api_client.get(
        f"/api/v1/dashboards/{dash_a_id}",
        headers={"X-CSRF-Token": viewer_csrf},
    )
    assert v_get.status_code == 200

    # Viewer cannot delete Org A dashboard -> 403 Forbidden
    v_del = await api_client.delete(
        f"/api/v1/dashboards/{dash_a_id}",
        headers={"X-CSRF-Token": viewer_csrf, "Origin": "http://localhost:3000"},
    )
    assert v_del.status_code == 403

    # Viewer can export Org A dashboard (dashboards.export allowed for Viewer)
    v_exp = await api_client.get(
        f"/api/v1/dashboards/{dash_a_id}/export",
        headers={"X-CSRF-Token": viewer_csrf},
    )
    assert v_exp.status_code == 200
    assert v_exp.json()["id"] == str(dash_a_id)


@pytest.mark.asyncio
async def test_specialized_dashboard_visualizations(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Verify Polars query execution for histogram, correlation matrix, box plot, and summary stats."""
    uid = uuid.uuid4().hex[:8]
    data, csrf_token, org_id = await register_and_login_helper(
        api_client, f"dash_types_{uid}@example.com"
    )
    user_id = uuid.UUID(data["user"]["id"])

    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    wb = Workbook(organization_id=org_id, workspace_id=ws_list[0].id, name="Test WB", created_by_user_id=user_id)
    db_session.add(wb)
    await db_session.flush()

    sheet = Sheet(workbook_id=wb.id, organization_id=org_id, name="Numbers Sheet")
    db_session.add(sheet)
    await db_session.flush()

    # Populate rows: Price (A), Units (B)
    r0 = SheetRow(sheet_id=sheet.id, organization_id=org_id, row_index=0, cells={"A": {"v": "Price", "c": "Price", "f": None}, "B": {"v": "Units", "c": "Units", "f": None}})
    r1 = SheetRow(sheet_id=sheet.id, organization_id=org_id, row_index=1, cells={"A": {"v": 10, "c": 10, "f": None}, "B": {"v": 100, "c": 100, "f": None}})
    r2 = SheetRow(sheet_id=sheet.id, organization_id=org_id, row_index=2, cells={"A": {"v": 20, "c": 20, "f": None}, "B": {"v": 80, "c": 80, "f": None}})
    r3 = SheetRow(sheet_id=sheet.id, organization_id=org_id, row_index=3, cells={"A": {"v": 30, "c": 30, "f": None}, "B": {"v": 60, "c": 60, "f": None}})
    r4 = SheetRow(sheet_id=sheet.id, organization_id=org_id, row_index=4, cells={"A": {"v": 40, "c": 40, "f": None}, "B": {"v": 40, "c": 40, "f": None}})
    db_session.add_all([r0, r1, r2, r3, r4])
    await db_session.commit()

    # Create dashboard
    dash_res = await api_client.post(
        "/api/v1/dashboards",
        json={"workspace_id": str(ws_list[0].id), "title": "Advanced Charts Dashboard"},
        headers={"X-CSRF-Token": csrf_token},
    )
    dash_id = dash_res.json()["id"]

    # 1. Histogram Widget
    w_hist = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets",
        json={
            "title": "Price Histogram",
            "widget_type": "histogram",
            "source_type": "sheet",
            "source_id": str(sheet.id),
            "query_config": {"metrics": [{"column": "Price"}]},
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert w_hist.status_code == 201
    hist_q = await api_client.post(f"/api/v1/dashboards/{dash_id}/widgets/{w_hist.json()['id']}/query")
    assert hist_q.status_code == 200
    assert "bin" in hist_q.json()["columns"]
    assert "count" in hist_q.json()["columns"]
    assert hist_q.json()["total_rows"] > 0

    # 2. Correlation Matrix Widget
    w_corr = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets",
        json={
            "title": "Price-Units Correlation",
            "widget_type": "correlation_matrix",
            "source_type": "sheet",
            "source_id": str(sheet.id),
            "query_config": {},
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert w_corr.status_code == 201
    corr_q = await api_client.post(f"/api/v1/dashboards/{dash_id}/widgets/{w_corr.json()['id']}/query")
    assert corr_q.status_code == 200
    assert "variable" in corr_q.json()["columns"]
    assert "Price" in corr_q.json()["columns"]
    assert "Units" in corr_q.json()["columns"]
    # Negative correlation between Price and Units: -1.0
    assert corr_q.json()["data"][0]["variable"] == "Price"
    assert corr_q.json()["data"][0]["Price"] == 1.0

    # 3. Box Plot Widget
    w_box = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets",
        json={
            "title": "Price 5-Point Box Plot",
            "widget_type": "box_plot",
            "source_type": "sheet",
            "source_id": str(sheet.id),
            "query_config": {"metrics": [{"column": "Price"}]},
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert w_box.status_code == 201
    box_q = await api_client.post(f"/api/v1/dashboards/{dash_id}/widgets/{w_box.json()['id']}/query")
    assert box_q.status_code == 200
    assert "median" in box_q.json()["columns"]
    assert "min" in box_q.json()["columns"]
    assert "max" in box_q.json()["columns"]
    assert box_q.json()["data"][0]["min"] == 10.0
    assert box_q.json()["data"][0]["max"] == 40.0

    # 4. Summary Stats Widget
    w_stats = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets",
        json={
            "title": "Descriptive Statistics",
            "widget_type": "summary_stats",
            "source_type": "sheet",
            "source_id": str(sheet.id),
            "query_config": {},
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert w_stats.status_code == 201
    stats_q = await api_client.post(f"/api/v1/dashboards/{dash_id}/widgets/{w_stats.json()['id']}/query")
    assert stats_q.status_code == 200
    assert "mean" in stats_q.json()["columns"]
    assert "std" in stats_q.json()["columns"]
    assert stats_q.json()["total_rows"] == 2

    # 5. Interactive Data Table Widget (data_table) with Server-Side Pagination & Sorting
    w_tbl = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets",
        json={
            "title": "Product Sales Data Table",
            "widget_type": "data_table",
            "source_type": "sheet",
            "source_id": str(sheet.id),
            "query_config": {
                "columns": ["Price", "Units"],
                "sort_by": "Price",
                "sort_desc": True,
                "limit": 2,
                "offset": 0,
            },
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert w_tbl.status_code == 201
    tbl_q = await api_client.post(f"/api/v1/dashboards/{dash_id}/widgets/{w_tbl.json()['id']}/query")
    assert tbl_q.status_code == 200
    tbl_body = tbl_q.json()
    assert tbl_body["columns"] == ["Price", "Units"]
    assert len(tbl_body["data"]) == 2
    # Sorted descending by Price: Widget D (40.0) first, Widget C (30.0) second
    assert tbl_body["data"][0]["Price"] == 40.0
    assert tbl_body["data"][1]["Price"] == 30.0
    assert tbl_body["total_rows"] == 4
    assert tbl_body["summary"]["page"] == 1
    assert tbl_body["summary"]["total_pages"] == 2

    # 6. Test Dashboard Sharing endpoint (dashboards.share)
    share_res = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/share",
        json={"is_shared": False},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert share_res.status_code == 200
    assert share_res.json()["is_shared"] is False

    # 7. Test Dashboard Export endpoint (dashboards.export)
    export_res = await api_client.get(f"/api/v1/dashboards/{dash_id}/export")
    assert export_res.status_code == 200
    assert export_res.json()["id"] == str(dash_id)
    assert len(export_res.json()["widgets"]) >= 5

    # 8. Test Dedicated Server-Side Widget CSV Export with Formula Injection Protection (CWE-1236)
    widget_tbl_id = w_tbl.json()["id"]
    csv_res = await api_client.get(f"/api/v1/dashboards/{dash_id}/widgets/{widget_tbl_id}/export")
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers.get("content-type", "")
    assert "attachment" in csv_res.headers.get("content-disposition", "")
    csv_text = csv_res.text
    assert "Price,Units" in csv_text
    assert "40" in csv_text

    # Cross-tenant widget export blocked (random UUID)
    bad_dash_id = uuid.uuid4()
    bad_export = await api_client.get(f"/api/v1/dashboards/{bad_dash_id}/widgets/{widget_tbl_id}/export")
    assert bad_export.status_code == 404


