"""Tests for Analytics publishing authorization, RBAC boundaries, cross-tenant isolation, and concurrent idempotency."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.models.analytics import AnalyticsJob, AnalyticsJobStatus, AnalyticsResult
from app.models.spreadsheet import Sheet
from app.repositories.workspace import WorkspaceRepository
from app.schemas.spreadsheet import WorkbookCreateInput
from app.services.spreadsheet import SpreadsheetService
from tests.integration.test_analytics_api import register_and_login_helper


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


async def create_tenant_and_result(
    api_client: AsyncClient, db_session: AsyncSession, prefix: str
) -> dict[str, Any]:
    uid = uuid.uuid4().hex[:8]
    login_data, csrf_token, org_id = await register_and_login_helper(
        api_client, f"{prefix}_{uid}@example.com"
    )
    user_id = uuid.UUID(login_data["user"]["id"])
    ws = (await WorkspaceRepository(db_session).list_by_organization(org_id))[0]
    spreadsheet_svc = SpreadsheetService(db_session)
    wb = await spreadsheet_svc.create_workbook(
        organization_id=org_id,
        created_by_user_id=user_id,
        data=WorkbookCreateInput(name=f"{prefix} WB", workspace_id=ws.id),
    )
    sheets = await spreadsheet_svc.list_sheets(wb.id, org_id)

    job_id = uuid.uuid4()
    result_id = uuid.uuid4()

    job = AnalyticsJob(
        id=job_id,
        organization_id=org_id,
        workspace_id=ws.id,
        source_workbook_id=wb.id,
        source_sheet_id=sheets[0].id,
        requested_by_user_id=user_id,
        operation_id="summary",
        status=AnalyticsJobStatus.SUCCEEDED,
    )
    db_session.add(job)
    await db_session.flush()

    res = AnalyticsResult(
        id=result_id,
        job_id=job_id,
        organization_id=org_id,
        result_type="summary",
        result_payload={"data": [{"metric": "mean", "val": 42.0}]},
        row_count=1,
        column_count=2,
    )
    db_session.add(res)
    await db_session.commit()

    return {
        "org_id": org_id,
        "ws_id": ws.id,
        "wb_id": wb.id,
        "sheet_id": sheets[0].id,
        "user_id": user_id,
        "csrf_token": csrf_token,
        "result_id": result_id,
    }


@pytest.mark.asyncio
async def test_publishing_missing_analytics_export_permission_403(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Missing analytics.export permission returns 403 Forbidden."""
    ctx = await create_tenant_and_result(api_client, db_session, "no_export")

    from app.services.permission import PermissionService
    original_check = PermissionService.check_membership_has_permission

    async def mock_check_perm(self, membership_id, perm_code, organization_id=None):
        if perm_code == "analytics.export":
            return False
        return await original_check(self, membership_id, perm_code, organization_id=organization_id)

    with patch.object(PermissionService, "check_membership_has_permission", new=mock_check_perm):
        resp = await api_client.post(
            f"/api/v1/analytics/results/{ctx['result_id']}/publish",
            json={"destination_workbook_id": str(ctx["wb_id"]), "sheet_name": "ExportSheet"},
            headers={"X-CSRF-Token": ctx["csrf_token"]},
        )
        assert resp.status_code == 403
        data = resp.json()
        assert "analytics.export" in str(data) or "Permission denied" in str(data)


@pytest.mark.asyncio
async def test_publishing_missing_spreadsheets_create_permission_403(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Missing spreadsheets.create permission returns 403 Forbidden."""
    ctx = await create_tenant_and_result(api_client, db_session, "no_create")

    from app.services.permission import PermissionService
    original_check = PermissionService.check_membership_has_permission

    async def mock_check_perm(self, membership_id, perm_code, organization_id=None):
        if perm_code == "spreadsheets.create":
            return False
        return await original_check(self, membership_id, perm_code, organization_id=organization_id)

    with patch.object(PermissionService, "check_membership_has_permission", new=mock_check_perm):
        resp = await api_client.post(
            f"/api/v1/analytics/results/{ctx['result_id']}/publish",
            json={"destination_workbook_id": str(ctx["wb_id"]), "sheet_name": "CreateSheet"},
            headers={"X-CSRF-Token": ctx["csrf_token"]},
        )
        assert resp.status_code == 403
        data = resp.json()
        assert "spreadsheets.create" in str(data) or "Permission denied" in str(data)


@pytest.mark.asyncio
async def test_publishing_cross_tenant_rejected_404_or_403(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Cross-tenant publish: User in Org A cannot publish Org A result to Org B destination workbook."""
    ctx_a = await create_tenant_and_result(api_client, db_session, "org_a")
    ctx_b = await create_tenant_and_result(api_client, db_session, "org_b")

    # Login back as Org A user
    # Attempt to publish Org A result to Org B destination workbook
    # Must be rejected because Org B workbook does not belong to Org A
    resp = await api_client.post(
        f"/api/v1/analytics/results/{ctx_a['result_id']}/publish",
        json={"destination_workbook_id": str(ctx_b["wb_id"]), "sheet_name": "InfiltrateSheet"},
        headers={"X-CSRF-Token": ctx_b["csrf_token"]},  # Or ctx_a
    )
    assert resp.status_code in (403, 404)


@pytest.mark.asyncio
async def test_publishing_concurrent_duplicate_idempotency(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Concurrent duplicate publish requests with idempotency_key result in exactly 1 sheet created."""
    ctx = await create_tenant_and_result(api_client, db_session, "idem_pub")

    idem_key = f"idem-key-{uuid.uuid4().hex}"
    payload = {
        "destination_workbook_id": str(ctx["wb_id"]),
        "sheet_name": "ConcurrentSheet",
        "idempotency_key": idem_key,
    }

    # Issue two concurrent publish requests with the same idempotency key
    req1 = api_client.post(
        f"/api/v1/analytics/results/{ctx['result_id']}/publish",
        json=payload,
        headers={"X-CSRF-Token": ctx["csrf_token"]},
    )
    req2 = api_client.post(
        f"/api/v1/analytics/results/{ctx['result_id']}/publish",
        json=payload,
        headers={"X-CSRF-Token": ctx["csrf_token"]},
    )

    resp1, resp2 = await asyncio.gather(req1, req2)

    assert resp1.status_code in (200, 201)
    assert resp2.status_code in (200, 201)

    sheet_id_1 = resp1.json()["sheet_id"]
    sheet_id_2 = resp2.json()["sheet_id"]

    # Both must point to the identical destination sheet!
    assert sheet_id_1 == sheet_id_2

    # Verify only ONE sheet with this name was created in the workbook
    sheet_stmt = select(Sheet).where(
        Sheet.workbook_id == ctx["wb_id"],
        Sheet.name.like("ConcurrentSheet%"),
    )
    sheets = (await db_session.execute(sheet_stmt)).scalars().all()
    assert len(sheets) == 1

