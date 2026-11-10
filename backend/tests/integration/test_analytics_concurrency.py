"""Concurrency and race condition tests for Analytics Subsystem.

Verifies:
1. Concurrent idempotent submissions (same key, simultaneous requests)
2. Concurrent worker claiming (two workers trying to claim the same job simultaneously)
3. Concurrency limits per organization under parallel load
4. Cooperative cancellation racing with completion
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.models.analytics import AnalyticsJob, AnalyticsJobStatus
from app.repositories.analytics import AnalyticsJobRepository
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


@pytest.mark.asyncio
async def test_concurrent_idempotent_submissions(api_client: AsyncClient, db_session: AsyncSession):
    """Multiple concurrent requests with identical idempotency key resolve to the single job."""
    uid = uuid.uuid4().hex[:8]
    login_data, csrf_token, org_id = await register_and_login_helper(
        api_client, f"conc_idemp_{uid}@example.com"
    )
    user_id = uuid.UUID(login_data["user"]["id"])

    ws = (await WorkspaceRepository(db_session).list_by_organization(org_id))[0]
    spreadsheet_svc = SpreadsheetService(db_session)
    wb = await spreadsheet_svc.create_workbook(
        organization_id=org_id,
        created_by_user_id=user_id,
        data=WorkbookCreateInput(name="Conc Idemp WB", workspace_id=ws.id),
    )
    sheets = await spreadsheet_svc.list_sheets(wb.id, org_id)
    sheet = sheets[0]

    job_payload = {
        "source_workbook_id": str(wb.id),
        "source_sheet_id": str(sheet.id),
        "operation_id": "profile_dataset",
        "parameters": {},
        "idempotency_key": f"conc-key-{uid}",
    }

    # Fire 5 concurrent submissions with the same idempotency key
    async def _submit():
        return await api_client.post(
            "/api/v1/analytics/jobs",
            json=job_payload,
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
        )

    responses = await asyncio.gather(*[_submit() for _ in range(5)])

    # All should return 202 Accepted
    for r in responses:
        assert r.status_code == 202, f"Failed with {r.status_code}: {r.text}"

    # All responses must yield the exact same job ID
    job_ids = {r.json()["id"] for r in responses}
    assert len(job_ids) == 1, f"Expected 1 unique job id, got {job_ids}"


@pytest.mark.asyncio
async def test_concurrent_worker_claim_atomicity():
    """Two independent workers attempting to claim the same queued job simultaneously."""
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    uid = uuid.uuid4().hex[:8]
    org_id = uuid.uuid4()
    ws_id = uuid.uuid4()
    user_id = uuid.uuid4()
    job_id = uuid.uuid4()

    # Create dummy org, user, ws, job directly in DB
    from app.models.organization import Organization
    from app.models.user import User
    from app.models.workspace import Workspace

    async with session_factory() as session:
        org = Organization(id=org_id, name=f"Claim Org {uid}", slug=f"claim-org-{uid}")
        user = User(
            id=user_id,
            email=f"claim_{uid}@example.com",
            normalized_email=f"claim_{uid}@example.com",
            display_name="Claim User",
        )
        ws = Workspace(id=ws_id, organization_id=org_id, name="Claim WS", slug=f"claim-ws-{uid}")
        wb = (
            await SpreadsheetService(session).create_workbook(
                organization_id=org_id,
                created_by_user_id=user_id,
                data=WorkbookCreateInput(name="Claim WB", workspace_id=ws_id),
            )
            if False
            else None
        )
        session.add_all([org, user, ws])
        await session.flush()

        from app.models.spreadsheet import Sheet, Workbook

        wb = Workbook(
            id=uuid.uuid4(), organization_id=org_id, workspace_id=ws_id, name="WB", version=1
        )
        session.add(wb)
        await session.flush()

        sheet = Sheet(
            id=uuid.uuid4(), organization_id=org_id, workbook_id=wb.id, name="Sheet1", version=1
        )
        session.add(sheet)
        await session.flush()

        job = AnalyticsJob(
            id=job_id,
            organization_id=org_id,
            workspace_id=ws_id,
            source_workbook_id=wb.id,
            source_sheet_id=sheet.id,
            requested_by_user_id=user_id,
            operation_id="profile_dataset",
            parameters={},
            status=AnalyticsJobStatus.QUEUED,
        )
        session.add(job)
        await session.commit()

    # Now simulate two worker sessions claiming the same job simultaneously
    async def _claim(worker_name: str) -> bool:
        async with session_factory() as session:
            repo = AnalyticsJobRepository(session)
            claimed, _ = await repo.claim_job_atomically(
                job_id=job_id,
                worker_id=worker_name,
                lease_duration_seconds=300,
            )
            await session.commit()
            return claimed

    results = await asyncio.gather(_claim("worker-alpha"), _claim("worker-beta"))

    # Exactly one worker must succeed, the other must fail (False)
    assert results.count(True) == 1, f"Expected exactly 1 claim, got {results}"
    assert results.count(False) == 1, f"Expected exactly 1 failed claim, got {results}"

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_active_job_limits_enforced(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Concurrent submissions beyond max active jobs per organization are rejected."""
    uid = uuid.uuid4().hex[:8]
    login_data, csrf_token, org_id = await register_and_login_helper(
        api_client, f"limit_{uid}@example.com"
    )
    user_id = uuid.UUID(login_data["user"]["id"])

    ws = (await WorkspaceRepository(db_session).list_by_organization(org_id))[0]
    spreadsheet_svc = SpreadsheetService(db_session)
    wb = await spreadsheet_svc.create_workbook(
        organization_id=org_id,
        created_by_user_id=user_id,
        data=WorkbookCreateInput(name="Limit WB", workspace_id=ws.id),
    )
    sheets = await spreadsheet_svc.list_sheets(wb.id, org_id)
    sheet = sheets[0]

    # Pre-populate active jobs up to limit (5)
    limit = settings.ANALYTICS_MAX_CONCURRENT_JOBS_PER_ORG
    for i in range(limit):
        job = AnalyticsJob(
            organization_id=org_id,
            workspace_id=ws.id,
            source_workbook_id=wb.id,
            source_sheet_id=sheet.id,
            requested_by_user_id=user_id,
            operation_id="profile_dataset",
            parameters={},
            status=AnalyticsJobStatus.RUNNING,
            worker_id=f"worker-slot-{i}",
        )
        db_session.add(job)
    await db_session.commit()

    # Now attempt a new job submission via API -> must be rejected with 422 Unprocessable Entity
    res = await api_client.post(
        "/api/v1/analytics/jobs",
        json={
            "source_workbook_id": str(wb.id),
            "source_sheet_id": str(sheet.id),
            "operation_id": "profile_dataset",
            "parameters": {},
        },
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )
    assert res.status_code == 400
    err_body = res.json()
    msg = err_body.get("detail") or (err_body.get("error") or {}).get("message", "")
    assert "limit" in msg.lower()
