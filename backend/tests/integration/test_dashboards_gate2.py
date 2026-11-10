"""Integration tests for Phase 9 Gate 2: Advanced Polars Filters, Duplication, and Validation."""

from __future__ import annotations

import uuid
from datetime import UTC
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.models.spreadsheet import Sheet, SheetRow, Workbook
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


async def register_and_login_helper(
    client: AsyncClient,
    email: str,
    password: str = "SecurePass1234!",
) -> tuple[dict[str, Any], str, uuid.UUID]:
    uid = uuid.uuid4().hex[:8]
    reg_payload = {
        "email": email,
        "password": password,
        "display_name": f"User {uid}",
        "organization_name": f"Org {uid}",
        "organization_slug": f"org-{uid}",
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
async def test_gate2_polars_filter_operators_and_validation(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Verify Gate 2 Polars operators: gte, lte, in, between, and strict backend validation."""
    uid = uuid.uuid4().hex[:8]
    data, csrf, org_id = await register_and_login_helper(
        api_client, f"gate2_user_{uid}@example.com"
    )
    user_id = uuid.UUID(data["user"]["id"])

    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    ws_id = ws_list[0].id

    # Create sheet with test data
    wb = Workbook(
        organization_id=org_id, workspace_id=ws_id, name="Gate2 WB", created_by_user_id=user_id
    )
    db_session.add(wb)
    await db_session.flush()

    sheet = Sheet(workbook_id=wb.id, organization_id=org_id, name="ProductSales")
    db_session.add(sheet)
    await db_session.flush()

    # Headers: Category, Price, Quantity, Status
    rows_data = [
        {"A": {"v": "Category"}, "B": {"v": "Price"}, "C": {"v": "Quantity"}, "D": {"v": "Status"}},
        {"A": {"v": "Electronics"}, "B": {"v": 100.0}, "C": {"v": 5}, "D": {"v": "active"}},
        {"A": {"v": "Electronics"}, "B": {"v": 250.0}, "C": {"v": 10}, "D": {"v": "active"}},
        {"A": {"v": "Apparel"}, "B": {"v": 50.0}, "C": {"v": 20}, "D": {"v": "pending"}},
        {"A": {"v": "Apparel"}, "B": {"v": 80.0}, "C": {"v": 15}, "D": {"v": "active"}},
        {"A": {"v": "Home"}, "B": {"v": 300.0}, "C": {"v": 2}, "D": {"v": "archived"}},
    ]
    for idx, r_cells in enumerate(rows_data):
        r = SheetRow(sheet_id=sheet.id, organization_id=org_id, row_index=idx, cells=r_cells)
        db_session.add(r)
    await db_session.commit()

    # Create Dashboard and Widget
    dash_res = await api_client.post(
        "/api/v1/dashboards",
        json={"workspace_id": str(ws_id), "title": "Gate 2 Test Studio"},
        headers={"X-CSRF-Token": csrf},
    )
    assert dash_res.status_code == 201
    dash_id = dash_res.json()["id"]

    w_res = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets",
        json={
            "title": "Product Metrics",
            "widget_type": "data_table",
            "source_type": "sheet",
            "source_id": str(sheet.id),
            "query_config": {"columns": ["Category", "Price", "Quantity", "Status"]},
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert w_res.status_code == 201
    widget_id = w_res.json()["id"]

    # 1. Test GTE and LTE
    q_res = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets/{widget_id}/query",
        json={
            "custom_query_config": {
                "filters": [
                    {"column": "Price", "operator": "gte", "value": 80.0},
                    {"column": "Price", "operator": "lte", "value": 250.0},
                ]
            }
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert q_res.status_code == 200
    res_data = q_res.json()["data"]
    # Should include 100.0, 250.0, 80.0 (3 rows)
    assert len(res_data) == 3
    prices = [r["Price"] for r in res_data]
    assert 100.0 in prices and 250.0 in prices and 80.0 in prices

    # 2. Test IN operator
    q_in_res = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets/{widget_id}/query",
        json={
            "custom_query_config": {
                "filters": [{"column": "Status", "operator": "in", "value": ["active", "archived"]}]
            }
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert q_in_res.status_code == 200
    in_data = q_in_res.json()["data"]
    assert len(in_data) == 4
    for r in in_data:
        assert r["Status"] in ("active", "archived")

    # 3. Test BETWEEN operator
    q_between_res = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets/{widget_id}/query",
        json={
            "custom_query_config": {
                "filters": [{"column": "Price", "operator": "between", "value": [70.0, 260.0]}]
            }
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert q_between_res.status_code == 200
    between_data = q_between_res.json()["data"]
    assert len(between_data) == 3

    # 4. Test Validation: Invalid operator rejects with 400
    invalid_op_res = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets/{widget_id}/query",
        json={
            "custom_query_config": {
                "filters": [{"column": "Price", "operator": "DROP_TABLE", "value": 100}]
            }
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert invalid_op_res.status_code == 400

    # 5. Test Validation: Non-existent column rejects with 400
    invalid_col_res = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets/{widget_id}/query",
        json={
            "custom_query_config": {
                "filters": [{"column": "NonExistentColumn", "operator": "eq", "value": 100}]
            }
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert invalid_col_res.status_code == 400

    # 6. Test Validation: Empty IN list yields 0 rows cleanly
    empty_in_res = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets/{widget_id}/query",
        json={
            "custom_query_config": {
                "filters": [{"column": "Category", "operator": "in", "value": []}]
            }
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert empty_in_res.status_code == 200
    assert len(empty_in_res.json()["data"]) == 0


@pytest.mark.asyncio
async def test_gate2_atomic_dashboard_duplication(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Verify atomic dashboard duplication creates independent copies with all widgets."""
    uid = uuid.uuid4().hex[:8]
    data, csrf, org_id = await register_and_login_helper(
        api_client, f"duplicate_user_{uid}@example.com"
    )
    user_id = uuid.UUID(data["user"]["id"])

    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    ws_id = ws_list[0].id

    # Create dummy sheet
    wb = Workbook(
        organization_id=org_id, workspace_id=ws_id, name="Dup WB", created_by_user_id=user_id
    )
    db_session.add(wb)
    await db_session.flush()
    sheet = Sheet(workbook_id=wb.id, organization_id=org_id, name="DupSheet")
    db_session.add(sheet)
    await db_session.commit()

    # Create original dashboard
    create_res = await api_client.post(
        "/api/v1/dashboards",
        json={
            "workspace_id": str(ws_id),
            "title": "Original Executive Studio",
            "description": "Original description",
            "layout_config": {"version": 1, "timezone": "America/New_York", "grid_columns": 12},
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert create_res.status_code == 201
    source_dash_id = create_res.json()["id"]

    # Add 2 widgets
    for i in range(2):
        await api_client.post(
            f"/api/v1/dashboards/{source_dash_id}/widgets",
            json={
                "title": f"Widget {i}",
                "widget_type": "bar",
                "source_type": "sheet",
                "source_id": str(sheet.id),
                "query_config": {"dimensions": ["Category"], "limit": 25},
                "position_x": i * 6,
                "position_y": 0,
                "width": 6,
                "height": 4,
                "sort_order": i,
            },
            headers={"X-CSRF-Token": csrf},
        )

    # Call Duplicate endpoint
    dup_res = await api_client.post(
        f"/api/v1/dashboards/{source_dash_id}/duplicate",
        json={"title": "Cloned Executive Studio"},
        headers={"X-CSRF-Token": csrf},
    )
    assert dup_res.status_code == 201, dup_res.text
    dup_data = dup_res.json()

    assert dup_data["id"] != source_dash_id
    assert dup_data["title"] == "Cloned Executive Studio"
    assert dup_data["layout_config"]["timezone"] == "America/New_York"
    assert len(dup_data["widgets"]) == 2

    # Verify widget IDs are newly created and distinct
    orig_detail = (
        await api_client.get(f"/api/v1/dashboards/{source_dash_id}", headers={"X-CSRF-Token": csrf})
    ).json()
    orig_w_ids = {w["id"] for w in orig_detail["widgets"]}
    dup_w_ids = {w["id"] for w in dup_data["widgets"]}
    assert orig_w_ids.isdisjoint(dup_w_ids)


@pytest.mark.asyncio
async def test_gate2_temporal_and_date_filtering(
    api_client: AsyncClient,
    db_session: AsyncSession,
    tmp_path: Any,
) -> None:
    """DEF-02 Regression: Verify Polars filtering on pl.Date, pl.Datetime, tz-aware and naive timestamps."""
    from datetime import date, datetime

    import polars as pl

    from app.models.analytics import AnalyticsJob, AnalyticsJobStatus, AnalyticsResult

    uid = uuid.uuid4().hex[:8]
    data, csrf, org_id = await register_and_login_helper(api_client, f"temporal_{uid}@example.com")
    user_id = uuid.UUID(data["user"]["id"])

    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    ws_id = ws_list[0].id

    # Create Parquet artifact with temporal columns: pl.Datetime (UTC), pl.Datetime (naive), pl.Date
    parquet_path = tmp_path / f"analytics_{uid}.parquet"
    df = pl.DataFrame(
        {
            "event_id": [1, 2, 3, 4],
            "created_at": [
                datetime(2026, 11, 2, 8, 0, tzinfo=UTC),
                datetime(2026, 11, 2, 18, 0, tzinfo=UTC),
                datetime(2026, 11, 3, 0, 0, tzinfo=UTC),  # On boundary
                datetime(2026, 11, 3, 12, 0, tzinfo=UTC),
            ],
            "naive_dt": [
                datetime(2026, 11, 2, 8, 0),
                datetime(2026, 11, 2, 18, 0),
                datetime(2026, 11, 3, 0, 0),
                datetime(2026, 11, 3, 12, 0),
            ],
            "event_date": [
                date(2026, 11, 2),
                date(2026, 11, 2),
                date(2026, 11, 3),
                date(2026, 11, 4),
            ],
            "revenue": [100.0, 200.0, 300.0, 400.0],
        }
    )
    df.write_parquet(parquet_path)

    # Create dummy workbook and sheet for AnalyticsJob foreign keys
    wb = Workbook(
        organization_id=org_id, workspace_id=ws_id, name="Analytics WB", created_by_user_id=user_id
    )
    db_session.add(wb)
    await db_session.flush()
    sheet = Sheet(workbook_id=wb.id, organization_id=org_id, name="AnalyticsSheet")
    db_session.add(sheet)
    await db_session.flush()

    # Insert AnalyticsJob and AnalyticsResult
    job = AnalyticsJob(
        organization_id=org_id,
        workspace_id=ws_id,
        source_workbook_id=wb.id,
        source_sheet_id=sheet.id,
        requested_by_user_id=user_id,
        operation_id="dataset_export",
        status=AnalyticsJobStatus.SUCCEEDED,
    )
    db_session.add(job)
    await db_session.flush()

    res_item = AnalyticsResult(
        job_id=job.id,
        organization_id=org_id,
        result_type="dataset",
        result_payload={},
        artifact_path=str(parquet_path),
        row_count=4,
    )
    db_session.add(res_item)
    await db_session.commit()

    # Create Dashboard and Widget pointing to analytics_result
    dash_res = await api_client.post(
        "/api/v1/dashboards",
        json={"workspace_id": str(ws_id), "title": "Temporal Dashboard"},
        headers={"X-CSRF-Token": csrf},
    )
    assert dash_res.status_code == 201
    dash_id = dash_res.json()["id"]

    w_res = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets",
        json={
            "title": "Temporal Events",
            "widget_type": "data_table",
            "source_type": "analytics_result",
            "source_id": str(res_item.id),
            "query_config": {
                "columns": ["event_id", "created_at", "naive_dt", "event_date", "revenue"]
            },
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert w_res.status_code == 201
    widget_id = w_res.json()["id"]

    # 1. Test BETWEEN half-open [start, end) on aware pl.Datetime: should match row 1 and 2, but NOT row 3 (on boundary)
    q1 = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets/{widget_id}/query",
        json={
            "custom_query_config": {
                "filters": [
                    {
                        "column": "created_at",
                        "operator": "between",
                        "value": ["2026-11-02T00:00:00.000Z", "2026-11-03T00:00:00.000Z"],
                    }
                ]
            }
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert q1.status_code == 200, q1.text
    q1_data = q1.json()["data"]
    assert len(q1_data) == 2
    assert {r["event_id"] for r in q1_data} == {1, 2}

    # 2. Test BETWEEN on naive pl.Datetime
    q2 = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets/{widget_id}/query",
        json={
            "custom_query_config": {
                "filters": [
                    {
                        "column": "naive_dt",
                        "operator": "between",
                        "value": ["2026-11-02T00:00:00.000Z", "2026-11-03T00:00:00.000Z"],
                    }
                ]
            }
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert q2.status_code == 200, q2.text
    assert len(q2.json()["data"]) == 2

    # 3. Test GTE and LT on pl.Date
    q3 = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets/{widget_id}/query",
        json={
            "custom_query_config": {
                "filters": [
                    {"column": "event_date", "operator": "gte", "value": "2026-11-03"},
                    {"column": "event_date", "operator": "lte", "value": "2026-11-04"},
                ]
            }
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert q3.status_code == 200, q3.text
    q3_data = q3.json()["data"]
    assert len(q3_data) == 2
    assert {r["event_id"] for r in q3_data} == {3, 4}

    # 4. Test Malformed temporal input returns 400
    q_bad = await api_client.post(
        f"/api/v1/dashboards/{dash_id}/widgets/{widget_id}/query",
        json={
            "custom_query_config": {
                "filters": [{"column": "created_at", "operator": "eq", "value": "not-a-valid-date"}]
            }
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert q_bad.status_code == 400


@pytest.mark.asyncio
async def test_gate2_cross_tenant_source_authorization_security(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """DEF-03 Security Regression: Verify cross-tenant widget source authorization and data isolation."""
    uid_a = uuid.uuid4().hex[:8]
    data_a, csrf_a, org_a_id = await register_and_login_helper(
        api_client, f"org_a_{uid_a}@example.com"
    )
    user_a_id = uuid.UUID(data_a["user"]["id"])

    uid_b = uuid.uuid4().hex[:8]
    data_b, csrf_b, org_b_id = await register_and_login_helper(
        api_client, f"org_b_{uid_b}@example.com"
    )
    user_b_id = uuid.UUID(data_b["user"]["id"])

    ws_repo = WorkspaceRepository(db_session)
    ws_a = (await ws_repo.list_by_organization(org_a_id))[0]
    ws_b = (await ws_repo.list_by_organization(org_b_id))[0]

    # 1. Create sensitive Sheet in Org A
    wb_a = Workbook(
        organization_id=org_a_id,
        workspace_id=ws_a.id,
        name="Org A Secrets",
        created_by_user_id=user_a_id,
    )
    db_session.add(wb_a)
    await db_session.flush()

    sheet_a = Sheet(workbook_id=wb_a.id, organization_id=org_a_id, name="SecretFinancials")
    db_session.add(sheet_a)
    await db_session.flush()

    row_a = SheetRow(
        sheet_id=sheet_a.id,
        organization_id=org_a_id,
        row_index=0,
        cells={"A": {"v": "Confidential Metric"}, "B": {"v": "OrgA_Secret_9999"}},
    )
    db_session.add(row_a)

    # Create legitimate Sheet in Org B
    wb_b = Workbook(
        organization_id=org_b_id,
        workspace_id=ws_b.id,
        name="Org B Public",
        created_by_user_id=user_b_id,
    )
    db_session.add(wb_b)
    await db_session.flush()

    sheet_b = Sheet(workbook_id=wb_b.id, organization_id=org_b_id, name="PublicSheet")
    db_session.add(sheet_b)
    await db_session.flush()

    row_b = SheetRow(
        sheet_id=sheet_b.id,
        organization_id=org_b_id,
        row_index=0,
        cells={"A": {"v": "Public Metric"}, "B": {"v": "OrgB_Data_1111"}},
    )
    db_session.add(row_b)
    await db_session.commit()

    # 2. In Org B: Create Dashboard and legitimate Widget
    dash_b_res = await api_client.post(
        "/api/v1/dashboards",
        json={"workspace_id": str(ws_b.id), "title": "Org B Dashboard"},
        headers={"X-CSRF-Token": csrf_b},
    )
    assert dash_b_res.status_code == 201
    dash_b_id = dash_b_res.json()["id"]

    w_b_res = await api_client.post(
        f"/api/v1/dashboards/{dash_b_id}/widgets",
        json={
            "title": "Org B Widget",
            "widget_type": "data_table",
            "source_type": "sheet",
            "source_id": str(sheet_b.id),
        },
        headers={"X-CSRF-Token": csrf_b},
    )
    assert w_b_res.status_code == 201
    widget_b_id = w_b_res.json()["id"]

    # 3. ATTACK: Org B attempts to update Widget B source_id to Sheet A
    attack_patch = await api_client.patch(
        f"/api/v1/dashboards/{dash_b_id}/widgets/{widget_b_id}",
        json={"source_id": str(sheet_a.id)},
        headers={"X-CSRF-Token": csrf_b},
    )
    # Must be rejected with non-disclosing 404 EntityNotFoundError
    assert attack_patch.status_code == 404

    # 4. Verify legitimate Org B query still works and retrieves Org B data
    q_b = await api_client.post(
        f"/api/v1/dashboards/{dash_b_id}/widgets/{widget_b_id}/query",
        json={},
        headers={"X-CSRF-Token": csrf_b},
    )
    assert q_b.status_code == 200
    q_b_text = str(q_b.json())
    assert "OrgB_Data_1111" in q_b_text
    assert "OrgA_Secret_9999" not in q_b_text

    # 5. Test supplying a non-existent random UUID
    random_uuid = str(uuid.uuid4())
    bad_source_patch = await api_client.patch(
        f"/api/v1/dashboards/{dash_b_id}/widgets/{widget_b_id}",
        json={"source_id": random_uuid},
        headers={"X-CSRF-Token": csrf_b},
    )
    assert bad_source_patch.status_code == 404


@pytest.mark.asyncio
async def test_gate2_widget_to_dashboard_url_binding(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """DEF-06 Regression: Verify widget-to-dashboard URL binding verification."""
    uid = uuid.uuid4().hex[:8]
    data, csrf, org_id = await register_and_login_helper(api_client, f"binding_{uid}@example.com")
    user_id = uuid.UUID(data["user"]["id"])

    ws_repo = WorkspaceRepository(db_session)
    ws_id = (await ws_repo.list_by_organization(org_id))[0].id

    wb = Workbook(
        organization_id=org_id, workspace_id=ws_id, name="Binding WB", created_by_user_id=user_id
    )
    db_session.add(wb)
    await db_session.flush()
    sheet = Sheet(workbook_id=wb.id, organization_id=org_id, name="BindingSheet")
    db_session.add(sheet)
    await db_session.commit()

    # Create Dashboard 1 and Dashboard 2
    d1 = (
        await api_client.post(
            "/api/v1/dashboards",
            json={"workspace_id": str(ws_id), "title": "Dash 1"},
            headers={"X-CSRF-Token": csrf},
        )
    ).json()["id"]
    d2 = (
        await api_client.post(
            "/api/v1/dashboards",
            json={"workspace_id": str(ws_id), "title": "Dash 2"},
            headers={"X-CSRF-Token": csrf},
        )
    ).json()["id"]

    # Create Widget in Dashboard 1
    w1 = (
        await api_client.post(
            f"/api/v1/dashboards/{d1}/widgets",
            json={
                "title": "W1 in D1",
                "widget_type": "bar",
                "source_type": "sheet",
                "source_id": str(sheet.id),
            },
            headers={"X-CSRF-Token": csrf},
        )
    ).json()["id"]

    # Valid query on Dash 1
    ok_q = await api_client.post(
        f"/api/v1/dashboards/{d1}/widgets/{w1}/query", headers={"X-CSRF-Token": csrf}
    )
    assert ok_q.status_code == 200

    # Invalid query: mismatched dashboard in URL (Dash 2)
    mismatch_q = await api_client.post(
        f"/api/v1/dashboards/{d2}/widgets/{w1}/query", headers={"X-CSRF-Token": csrf}
    )
    assert mismatch_q.status_code == 404

    # Invalid update: mismatched dashboard in URL
    mismatch_patch = await api_client.patch(
        f"/api/v1/dashboards/{d2}/widgets/{w1}",
        json={"title": "Hacked Title"},
        headers={"X-CSRF-Token": csrf},
    )
    assert mismatch_patch.status_code == 404

    # Invalid delete: mismatched dashboard in URL
    mismatch_del = await api_client.delete(
        f"/api/v1/dashboards/{d2}/widgets/{w1}", headers={"X-CSRF-Token": csrf}
    )
    assert mismatch_del.status_code == 404


@pytest.mark.asyncio
async def test_gate2_optimistic_concurrency_dashboard_update(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """DEF-07: Verify optimistic concurrency conflict rejection on stale expected_updated_at."""
    from datetime import datetime, timedelta

    uid = uuid.uuid4().hex[:8]
    data, csrf, org_id = await register_and_login_helper(
        api_client, f"concurrency_{uid}@example.com"
    )
    ws_repo = WorkspaceRepository(db_session)
    ws_id = (await ws_repo.list_by_organization(org_id))[0].id

    create_res = await api_client.post(
        "/api/v1/dashboards",
        json={"workspace_id": str(ws_id), "title": "Concurrency Studio"},
        headers={"X-CSRF-Token": csrf},
    )
    assert create_res.status_code == 201
    dash_id = create_res.json()["id"]
    actual_updated_at = create_res.json()["updated_at"]

    # 1. Attempt update with stale expected_updated_at (1 hour in the past) -> 409 Conflict
    stale_dt = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    conflict_res = await api_client.patch(
        f"/api/v1/dashboards/{dash_id}",
        json={"title": "Stale Edit", "expected_updated_at": stale_dt},
        headers={"X-CSRF-Token": csrf},
    )
    assert conflict_res.status_code == 409

    # 2. Attempt update with actual matching expected_updated_at -> 200 OK
    ok_res = await api_client.patch(
        f"/api/v1/dashboards/{dash_id}",
        json={"title": "Valid Edit", "expected_updated_at": actual_updated_at},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok_res.status_code == 200
    assert ok_res.json()["title"] == "Valid Edit"
