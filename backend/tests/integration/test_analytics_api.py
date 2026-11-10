"""Integration tests for Analytics API, Transactional Outbox, Celery Worker, and Spreadsheet Publishing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.models.analytics import AnalyticsJob, AnalyticsJobStatus, AnalyticsOutboxEvent
from app.models.role import MembershipRole, OrganizationRole
from app.repositories.user import UserRepository
from app.repositories.workspace import WorkspaceRepository
from app.schemas.organization import ProvisionOrganizationInput
from app.schemas.spreadsheet import BatchCellUpdateInput, CellEditInput, WorkbookCreateInput
from app.services.organization import OrganizationService
from app.services.spreadsheet import SpreadsheetService
from app.tasks.analytics import _execute_analytics_job_async
from app.tasks.dispatcher import OutboxDispatcher


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
    org_name: str | None = None,
    org_slug: str | None = None,
) -> tuple[dict[str, Any], str, uuid.UUID]:
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


# =============================================================================
# 1. OPERATIONS CATALOGUE
# =============================================================================


@pytest.mark.asyncio
async def test_analytics_operations_catalogue(api_client: AsyncClient):
    """Test listing and querying registered operations catalogue."""
    uid = uuid.uuid4().hex[:8]
    _, _, _ = await register_and_login_helper(api_client, f"owner_{uid}@example.com")

    # List operations
    res = await api_client.get("/api/v1/analytics/operations")
    assert res.status_code == 200
    ops = res.json()
    assert len(ops) >= 6
    op_ids = [o["id"] for o in ops]
    assert "profile_dataset" in op_ids
    assert "describe_columns" in op_ids
    assert "group_by_aggregate" in op_ids
    assert "filter_dataset" in op_ids
    assert "correlation_matrix" in op_ids
    assert "data_quality_profile" in op_ids

    # Query single operation
    res_single = await api_client.get("/api/v1/analytics/operations/describe_columns")
    assert res_single.status_code == 200
    single = res_single.json()
    assert single["id"] == "describe_columns"
    assert "parameters_schema" in single


# =============================================================================
# 2. RBAC PERMISSIONS ENFORCEMENT ACROSS 4 ROLES
# =============================================================================


@pytest.mark.asyncio
async def test_analytics_rbac_permissions_four_roles(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Verify analytics permissions: Owner, Admin, Member can create jobs; Viewer is 403."""
    uid = uuid.uuid4().hex[:8]
    owner_email = f"owner_{uid}@example.com"
    login_data, csrf_token, org_id = await register_and_login_helper(api_client, owner_email)

    # Create workspace, workbook and sheet as Owner
    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    ws_id = ws_list[0].id

    spreadsheet_svc = SpreadsheetService(db_session)
    wb = await spreadsheet_svc.create_workbook(
        organization_id=org_id,
        created_by_user_id=uuid.UUID(login_data["user"]["id"]),
        data=WorkbookCreateInput(name="Test WB", workspace_id=ws_id),
    )
    sheets = await spreadsheet_svc.list_sheets(wb.id, org_id)
    sheet_id = sheets[0].id

    # Create Viewer user in Org
    from app.models.user import User

    user_repo = UserRepository(db_session)
    viewer_email = f"viewer_{uid}@example.com"
    viewer_user = await user_repo.create(
        User(
            email=viewer_email,
            normalized_email=viewer_email.lower(),
            display_name="Viewer User",
        )
    )
    from app.models.membership import OrganizationMembership
    from app.repositories.membership import MembershipRepository

    mem_repo = MembershipRepository(db_session)
    viewer_mem = await mem_repo.create(
        OrganizationMembership(organization_id=org_id, user_id=viewer_user.id)
    )

    from sqlalchemy import select

    role_stmt = select(OrganizationRole).where(
        OrganizationRole.organization_id == org_id,
        OrganizationRole.slug == "viewer",
    )
    viewer_role = (await db_session.execute(role_stmt)).scalar_one()
    db_session.add(
        MembershipRole(
            membership_id=viewer_mem.id,
            role_id=viewer_role.id,
            organization_id=org_id,
        )
    )
    await db_session.commit()

    # 1. Owner can submit job
    job_payload = {
        "source_workbook_id": str(wb.id),
        "source_sheet_id": str(sheet_id),
        "operation_id": "profile_dataset",
        "parameters": {},
    }
    owner_job_res = await api_client.post(
        "/api/v1/analytics/jobs",
        json=job_payload,
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )
    assert owner_job_res.status_code == 202

    # 2. Authenticate as Viewer with active org session
    from app.services.session import SessionService

    sess_service = SessionService(db_session)
    v_session, viewer_token, viewer_csrf = await sess_service.create_session(
        user_id=viewer_user.id,
        ip_address="127.0.0.1",
        user_agent="pytest",
        active_organization_id=org_id,
    )
    await db_session.commit()

    api_client.cookies.set(settings.COOKIE_NAME, viewer_token)

    # Viewer can view jobs (analytics.read)
    v_list = await api_client.get("/api/v1/analytics/jobs")
    assert v_list.status_code == 200

    # Viewer CANNOT submit job (lacks analytics.create) -> 403 Forbidden
    v_submit = await api_client.post(
        "/api/v1/analytics/jobs",
        json=job_payload,
        headers={"X-CSRF-Token": viewer_csrf, "Origin": "http://localhost:3000"},
    )
    assert v_submit.status_code == 403


# =============================================================================
# 3. END-TO-END JOB LIFECYCLE, OUTBOX DISPATCH & WORKER EXECUTION
# =============================================================================


@pytest.mark.asyncio
async def test_analytics_job_lifecycle_outbox_and_worker_execution(
    api_client: AsyncClient, db_session: AsyncSession
):
    """End-to-end lifecycle: submit job (202), outbox event, worker execution, result query."""
    uid = uuid.uuid4().hex[:8]
    login_data, csrf_token, org_id = await register_and_login_helper(
        api_client, f"analyst_{uid}@example.com"
    )
    user_id = uuid.UUID(login_data["user"]["id"])

    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    ws_id = ws_list[0].id

    # 1. Create workbook & sheet with data
    spreadsheet_svc = SpreadsheetService(db_session)
    wb = await spreadsheet_svc.create_workbook(
        organization_id=org_id,
        created_by_user_id=user_id,
        data=WorkbookCreateInput(name="Sales WB", workspace_id=ws_id),
    )
    sheets = await spreadsheet_svc.list_sheets(wb.id, org_id)
    sheet = sheets[0]

    # Populate: Col A = Department, Col B = Sales
    # Row 0 (Headers): A1="Department", B1="Sales"
    # Row 1: A2="Engineering", B2=100
    # Row 2: A3="Engineering", B3=200
    # Row 3: A4="Sales", B4=300
    await spreadsheet_svc.update_cells_batch(
        sheet_id=sheet.id,
        organization_id=org_id,
        user_id=user_id,
        data=BatchCellUpdateInput(
            updates=[
                CellEditInput(cell_ref="A1", value="Engineering"),
                CellEditInput(cell_ref="B1", value=100.0),
                CellEditInput(cell_ref="A2", value="Engineering"),
                CellEditInput(cell_ref="B2", value=200.0),
                CellEditInput(cell_ref="A3", value="Sales"),
                CellEditInput(cell_ref="B3", value=300.0),
            ]
        ),
    )
    await db_session.commit()

    # 2. Submit Group By Aggregate Job
    job_payload = {
        "source_workbook_id": str(wb.id),
        "source_sheet_id": str(sheet.id),
        "operation_id": "group_by_aggregate",
        "parameters": {
            "group_by": ["A"],
            "aggregations": [
                {"column": "B", "function": "sum", "alias": "total_sales"},
                {"column": "B", "function": "count", "alias": "count_sales"},
            ],
        },
    }
    submit_res = await api_client.post(
        "/api/v1/analytics/jobs",
        json=job_payload,
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )
    assert submit_res.status_code == 202
    job_info = submit_res.json()
    job_id = uuid.UUID(job_info["id"])
    assert job_info["status"] == "queued"

    # 3. Verify Outbox event was created in PostgreSQL
    from sqlalchemy import select

    outbox_stmt = select(AnalyticsOutboxEvent).where(AnalyticsOutboxEvent.job_id == job_id)
    outbox_ev = (await db_session.execute(outbox_stmt)).scalar_one_or_none()
    assert outbox_ev is not None
    assert outbox_ev.published_at is None

    # 4. Worker execution
    worker_res = await _execute_analytics_job_async(str(job_id), "test-task-1")
    assert worker_res["status"] == "succeeded"

    # 5. Query Job status via API
    get_job_res = await api_client.get(f"/api/v1/analytics/jobs/{job_id}")
    assert get_job_res.status_code == 200
    job_data = get_job_res.json()
    assert job_data["status"] == "succeeded"
    assert job_data["progress_percent"] == 100
    assert job_data["result_id"] is not None

    # 6. Query Job Result via API
    res_query = await api_client.get(f"/api/v1/analytics/jobs/{job_id}/result")
    assert res_query.status_code == 200
    res_json = res_query.json()
    assert res_json["result_type"] == "group_by_aggregate"
    data_rows = {r["A"]: r for r in res_json["result_payload"]["data"]}

    assert "Engineering" in data_rows
    assert data_rows["Engineering"]["total_sales"] == 300.0
    assert data_rows["Engineering"]["count_sales"] == 2
    assert "Sales" in data_rows
    assert data_rows["Sales"]["total_sales"] == 300.0
    assert data_rows["Sales"]["count_sales"] == 1


# =============================================================================
# 4. IDEMPOTENT JOB SUBMISSION
# =============================================================================


@pytest.mark.asyncio
async def test_idempotent_job_submission(api_client: AsyncClient, db_session: AsyncSession):
    """Submitting with identical idempotency_key returns existing job."""
    uid = uuid.uuid4().hex[:8]
    login_data, csrf_token, org_id = await register_and_login_helper(
        api_client, f"idemp_{uid}@example.com"
    )
    user_id = uuid.UUID(login_data["user"]["id"])

    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    ws_id = ws_list[0].id

    spreadsheet_svc = SpreadsheetService(db_session)
    wb = await spreadsheet_svc.create_workbook(
        organization_id=org_id,
        created_by_user_id=user_id,
        data=WorkbookCreateInput(name="Idemp WB", workspace_id=ws_id),
    )
    sheets = await spreadsheet_svc.list_sheets(wb.id, org_id)
    sheet = sheets[0]

    job_payload = {
        "source_workbook_id": str(wb.id),
        "source_sheet_id": str(sheet.id),
        "operation_id": "profile_dataset",
        "parameters": {},
        "idempotency_key": f"key-{uid}",
    }

    # Submit 1
    res1 = await api_client.post(
        "/api/v1/analytics/jobs",
        json=job_payload,
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )
    assert res1.status_code == 202
    job1_id = res1.json()["id"]

    # Submit 2 (identical idempotency key)
    res2 = await api_client.post(
        "/api/v1/analytics/jobs",
        json=job_payload,
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )
    assert res2.status_code == 202
    job2_id = res2.json()["id"]

    assert job1_id == job2_id, "Idempotent submission must return identical job ID"


# =============================================================================
# 5. COOPERATIVE CANCELLATION
# =============================================================================


@pytest.mark.asyncio
async def test_cooperative_cancellation_flow(api_client: AsyncClient, db_session: AsyncSession):
    """Verify queued job cancellation immediately transitions to cancelled."""
    uid = uuid.uuid4().hex[:8]
    login_data, csrf_token, org_id = await register_and_login_helper(
        api_client, f"cancel_{uid}@example.com"
    )
    user_id = uuid.UUID(login_data["user"]["id"])

    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    ws_id = ws_list[0].id

    spreadsheet_svc = SpreadsheetService(db_session)
    wb = await spreadsheet_svc.create_workbook(
        organization_id=org_id,
        created_by_user_id=user_id,
        data=WorkbookCreateInput(name="Cancel WB", workspace_id=ws_id),
    )
    sheets = await spreadsheet_svc.list_sheets(wb.id, org_id)
    sheet = sheets[0]

    job_res = await api_client.post(
        "/api/v1/analytics/jobs",
        json={
            "source_workbook_id": str(wb.id),
            "source_sheet_id": str(sheet.id),
            "operation_id": "profile_dataset",
            "parameters": {},
        },
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )
    assert job_res.status_code == 202
    job_id = job_res.json()["id"]

    # Request cancellation
    cancel_res = await api_client.post(
        f"/api/v1/analytics/jobs/{job_id}/cancel",
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )
    assert cancel_res.status_code == 200
    assert cancel_res.json()["cancellation_accepted"] is True

    # Check status
    verify_res = await api_client.get(f"/api/v1/analytics/jobs/{job_id}")
    assert verify_res.json()["status"] == "cancelled"


# =============================================================================
# 6. PUBLISH RESULT TO SPREADSHEET SHEET
# =============================================================================


@pytest.mark.asyncio
async def test_publish_result_to_spreadsheet(api_client: AsyncClient, db_session: AsyncSession):
    """Verify publishing tabular analytical results into a new spreadsheet sheet."""
    uid = uuid.uuid4().hex[:8]
    login_data, csrf_token, org_id = await register_and_login_helper(
        api_client, f"pub_{uid}@example.com"
    )
    user_id = uuid.UUID(login_data["user"]["id"])

    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    ws_id = ws_list[0].id

    spreadsheet_svc = SpreadsheetService(db_session)
    wb = await spreadsheet_svc.create_workbook(
        organization_id=org_id,
        created_by_user_id=user_id,
        data=WorkbookCreateInput(name="Publish WB", workspace_id=ws_id),
    )
    sheets = await spreadsheet_svc.list_sheets(wb.id, org_id)
    sheet = sheets[0]

    await spreadsheet_svc.update_cells_batch(
        sheet_id=sheet.id,
        organization_id=org_id,
        user_id=user_id,
        data=BatchCellUpdateInput(
            updates=[
                CellEditInput(cell_ref="A1", value="North"),
                CellEditInput(cell_ref="B1", value=500.0),
                CellEditInput(cell_ref="A2", value="South"),
                CellEditInput(cell_ref="B2", value=300.0),
            ]
        ),
    )
    await db_session.commit()

    # Submit aggregation job
    job_res = await api_client.post(
        "/api/v1/analytics/jobs",
        json={
            "source_workbook_id": str(wb.id),
            "source_sheet_id": str(sheet.id),
            "operation_id": "group_by_aggregate",
            "parameters": {
                "group_by": ["A"],
                "aggregations": [{"column": "B", "function": "sum", "alias": "sum_rev"}],
            },
        },
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )
    job_id = job_res.json()["id"]

    # Execute worker
    worker_res = await _execute_analytics_job_async(job_id, "pub-task-1")
    assert worker_res["status"] == "succeeded"
    result_id = worker_res["result_id"]

    # Publish result into new sheet
    pub_res = await api_client.post(
        f"/api/v1/analytics/results/{result_id}/publish-to-sheet",
        json={"destination_workbook_id": str(wb.id), "sheet_name": "Aggregated_Region"},
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )
    assert pub_res.status_code == 201
    pub_data = pub_res.json()
    assert pub_data["sheet_name"] == "Aggregated_Region"
    assert pub_data["rows_written"] > 1


# =============================================================================
# 7. DATABASE COMPOSITE FK TENANT ISOLATION
# =============================================================================


@pytest.mark.asyncio
async def test_database_composite_fk_rejects_cross_tenant_analytics_job(
    db_session: AsyncSession,
):
    """Direct PostgreSQL test: AnalyticsJob cannot point to a Sheet from another Organization."""
    uid = uuid.uuid4().hex[:8]
    org_service = OrganizationService(db_session)

    # Org A
    org_a, user_a, ws_a = await org_service.provision_organization_atomic(
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
    org_b, user_b, ws_b = await org_service.provision_organization_atomic(
        ProvisionOrganizationInput(
            organization_name=f"Org B {uid}",
            organization_slug=f"org-b-{uid}",
            user_email=f"user_b_{uid}@example.com",
            user_display_name="User B",
            default_workspace_name="WS B",
            default_workspace_slug="ws-b",
        )
    )

    spreadsheet_svc = SpreadsheetService(db_session)
    wb_b = await spreadsheet_svc.create_workbook(
        organization_id=org_b.id,
        created_by_user_id=user_b.id,
        data=WorkbookCreateInput(name="WB B", workspace_id=ws_b.id),
    )
    sheets_b = await spreadsheet_svc.list_sheets(wb_b.id, org_b.id)
    sheet_b = sheets_b[0]

    # Attempt cross-tenant job: organization_id = Org A, but source_sheet_id = Sheet from Org B
    illegal_job = AnalyticsJob(
        organization_id=org_a.id,
        workspace_id=ws_a.id,
        source_workbook_id=wb_b.id,  # From Org B!
        source_sheet_id=sheet_b.id,  # From Org B!
        requested_by_user_id=user_a.id,
        operation_id="profile_dataset",
        parameters={},
        status=AnalyticsJobStatus.QUEUED,
    )
    db_session.add(illegal_job)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


# =============================================================================
# 8. WORKER LEASE RECOVERY SWEEP
# =============================================================================


@pytest.mark.asyncio
async def test_worker_lease_recovery_sweep(db_session: AsyncSession):
    """Verify expired worker leases are recovered and re-queued by dispatcher."""
    uid = uuid.uuid4().hex[:8]
    org_service = OrganizationService(db_session)
    org, user, ws = await org_service.provision_organization_atomic(
        ProvisionOrganizationInput(
            organization_name=f"Org Rec {uid}",
            organization_slug=f"org-rec-{uid}",
            user_email=f"rec_{uid}@example.com",
            user_display_name="Rec User",
            default_workspace_name="WS Rec",
            default_workspace_slug="ws-rec",
        )
    )

    spreadsheet_svc = SpreadsheetService(db_session)
    wb = await spreadsheet_svc.create_workbook(
        organization_id=org.id,
        created_by_user_id=user.id,
        data=WorkbookCreateInput(name="WB Rec", workspace_id=ws.id),
    )
    sheets = await spreadsheet_svc.list_sheets(wb.id, org.id)
    sheet = sheets[0]

    # Create job in 'running' state with expired lease
    past_time = datetime.now(UTC) - timedelta(minutes=5)
    stale_job = AnalyticsJob(
        organization_id=org.id,
        workspace_id=ws.id,
        source_workbook_id=wb.id,
        source_sheet_id=sheet.id,
        requested_by_user_id=user.id,
        operation_id="profile_dataset",
        parameters={},
        status=AnalyticsJobStatus.RUNNING,
        worker_id="crashed-worker-999",
        lease_expires_at=past_time,
        attempt_count=1,
        max_attempts=3,
    )
    db_session.add(stale_job)
    await db_session.commit()

    dispatcher = OutboxDispatcher()
    recovered = await dispatcher.recover_stale_leases()
    assert recovered >= 1

    # Verify status transitioned to RETRYING by refreshing entity in db_session
    await db_session.refresh(stale_job)
    assert stale_job.status == AnalyticsJobStatus.RETRYING
    assert stale_job.error_code == "LEASE_EXPIRED"


@pytest.mark.asyncio
async def test_get_storage_usage_endpoint(api_client: AsyncClient) -> None:
    """Verify authenticated organization storage usage and quota endpoint returns authoritative metrics."""
    uid = uuid.uuid4().hex[:8]
    _, _, org_id = await register_and_login_helper(api_client, f"storage_user_{uid}@example.com")

    resp = await api_client.get("/api/v1/analytics/storage")
    assert resp.status_code == 200
    data = resp.json()
    assert data["organization_id"] == str(org_id)
    assert "usage_bytes" in data
    assert "quota_bytes" in data
    assert "available_bytes" in data
    assert "utilization_percent" in data
    assert data["quota_bytes"] == settings.ANALYTICS_MAX_ORG_STORAGE_BYTES
    assert data["usage_bytes"] >= 0
    assert data["available_bytes"] <= data["quota_bytes"]
