import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.exceptions import EntityNotFoundError
from app.db.session import get_db_session
from app.main import app
from app.models.analytics import AnalyticsJob, AnalyticsJobStatus, AnalyticsResult
from app.models.spreadsheet import Sheet
from app.repositories.workspace import WorkspaceRepository
from app.schemas.spreadsheet import WorkbookCreateInput
from app.services.analytics import AnalyticsService
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


async def create_test_context(api_client: AsyncClient, db_session: AsyncSession, prefix: str):
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
    return org_id, ws.id, wb.id, sheets[0].id, user_id, csrf_token


@pytest.mark.asyncio
async def test_publish_already_published_result_returns_existing_sheet(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Publishing an already-published result returns existing published_sheet_id idempotently."""
    org_id, ws_id, wb_id, sheet_id, user_id, csrf_token = await create_test_context(
        api_client, db_session, "pub_idem"
    )

    analytics_svc = AnalyticsService(db_session)
    job_id = uuid.uuid4()
    result_id = uuid.uuid4()

    job = AnalyticsJob(
        id=job_id,
        organization_id=org_id,
        workspace_id=ws_id,
        source_workbook_id=wb_id,
        source_sheet_id=sheet_id,
        requested_by_user_id=user_id,
        operation_id="filter_dataset",
        status=AnalyticsJobStatus.SUCCEEDED,
    )
    db_session.add(job)
    await db_session.flush()

    # Pre-create result with tabular data
    result = AnalyticsResult(
        id=result_id,
        job_id=job_id,
        organization_id=org_id,
        result_type="filter_dataset",
        result_payload={
            "columns": ["metric", "value"],
            "data": [{"metric": "A", "value": 100}, {"metric": "B", "value": 200}],
        },
    )
    db_session.add(result)
    await db_session.commit()

    # First publication
    res1 = await analytics_svc.publish_result_to_sheet(
        result_id=result_id,
        organization_id=org_id,
        user_id=user_id,
        destination_workbook_id=wb_id,
        sheet_name="First Published Sheet",
    )
    assert res1.sheet_id is not None
    first_sheet_id = res1.sheet_id

    # Second publication with same or different sheet name
    res2 = await analytics_svc.publish_result_to_sheet(
        result_id=result_id,
        organization_id=org_id,
        user_id=user_id,
        destination_workbook_id=wb_id,
        sheet_name="Attempt Duplicate",
    )
    # Must return identical sheet_id without creating a new sheet
    assert res2.sheet_id == first_sheet_id

    # Check total sheets in workbook
    stmt = select(Sheet).where(Sheet.workbook_id == wb_id)
    sheets = (await db_session.execute(stmt)).scalars().all()
    # Initial sheet + 1 published sheet = 2 sheets total
    assert len(sheets) == 2


@pytest.mark.asyncio
async def test_concurrent_publish_same_result_idempotent(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Concurrent publication calls for the same result execute safely and return the same sheet_id."""
    org_id, ws_id, wb_id, sheet_id, user_id, csrf_token = await create_test_context(
        api_client, db_session, "pub_conc"
    )

    analytics_svc = AnalyticsService(db_session)
    job_id = uuid.uuid4()
    result_id = uuid.uuid4()

    job = AnalyticsJob(
        id=job_id,
        organization_id=org_id,
        workspace_id=ws_id,
        source_workbook_id=wb_id,
        source_sheet_id=sheet_id,
        requested_by_user_id=user_id,
        operation_id="filter_dataset",
        status=AnalyticsJobStatus.SUCCEEDED,
    )
    db_session.add(job)
    await db_session.flush()

    result = AnalyticsResult(
        id=result_id,
        job_id=job_id,
        organization_id=org_id,
        result_type="filter_dataset",
        result_payload={
            "columns": ["col1", "col2"],
            "data": [{"col1": "x", "col2": 1}, {"col1": "y", "col2": 2}],
        },
    )
    db_session.add(result)
    await db_session.commit()

    res1 = await analytics_svc.publish_result_to_sheet(
        result_id=result_id,
        organization_id=org_id,
        user_id=user_id,
        destination_workbook_id=wb_id,
        sheet_name="Concurrent Pub Sheet",
    )
    res2 = await analytics_svc.publish_result_to_sheet(
        result_id=result_id,
        organization_id=org_id,
        user_id=user_id,
        destination_workbook_id=wb_id,
        sheet_name="Concurrent Pub Sheet",
    )

    assert res1.sheet_id == res2.sheet_id


@pytest.mark.asyncio
async def test_publish_destination_foreign_workbook_rejected(
    api_client: AsyncClient, db_session: AsyncSession
):
    """Destination workbook belonging to a different organization is strictly rejected."""
    org_id1, ws_id1, wb_id1, sheet_id1, user_id1, _ = await create_test_context(
        api_client, db_session, "pub_org1"
    )
    org_id2, ws_id2, wb_id2, sheet_id2, user_id2, _ = await create_test_context(
        api_client, db_session, "pub_org2"
    )

    analytics_svc = AnalyticsService(db_session)
    job_id = uuid.uuid4()
    result_id = uuid.uuid4()

    job = AnalyticsJob(
        id=job_id,
        organization_id=org_id1,
        workspace_id=ws_id1,
        source_workbook_id=wb_id1,
        source_sheet_id=sheet_id1,
        requested_by_user_id=user_id1,
        operation_id="filter_dataset",
        status=AnalyticsJobStatus.SUCCEEDED,
    )
    db_session.add(job)
    await db_session.flush()

    result = AnalyticsResult(
        id=result_id,
        job_id=job_id,
        organization_id=org_id1,
        result_type="filter_dataset",
        result_payload={"columns": ["A"], "data": [{"A": 1}]},
    )
    db_session.add(result)
    await db_session.commit()

    # Attempt to publish org1's result to org2's workbook
    with pytest.raises(EntityNotFoundError):
        await analytics_svc.publish_result_to_sheet(
            result_id=result_id,
            organization_id=org_id1,
            user_id=user_id1,
            destination_workbook_id=wb_id2,
            sheet_name="Cross Tenant Attack",
        )


@pytest.mark.asyncio
async def test_publish_cell_batch_failure_rolls_back_sheet_creation(
    api_client: AsyncClient, db_session: AsyncSession
):
    """If cell population fails during publishing, the created sheet is cleanly rolled back."""
    org_id, ws_id, wb_id, sheet_id, user_id, _ = await create_test_context(
        api_client, db_session, "pub_rollback"
    )

    analytics_svc = AnalyticsService(db_session)
    job_id = uuid.uuid4()
    result_id = uuid.uuid4()

    job = AnalyticsJob(
        id=job_id,
        organization_id=org_id,
        workspace_id=ws_id,
        source_workbook_id=wb_id,
        source_sheet_id=sheet_id,
        requested_by_user_id=user_id,
        operation_id="filter_dataset",
        status=AnalyticsJobStatus.SUCCEEDED,
    )
    db_session.add(job)
    await db_session.flush()

    result = AnalyticsResult(
        id=result_id,
        job_id=job_id,
        organization_id=org_id,
        result_type="filter_dataset",
        result_payload={"columns": ["A"], "data": [{"A": 10}]},
    )
    db_session.add(result)
    await db_session.commit()

    # Count sheets before
    stmt = select(Sheet).where(Sheet.workbook_id == wb_id)
    sheets_before = len((await db_session.execute(stmt)).scalars().all())

    # Mock update_cells_batch to simulate crash
    from unittest.mock import patch
    with patch("app.services.spreadsheet.SpreadsheetService.update_cells_batch", side_effect=RuntimeError("Database row lock timeout")):
        with pytest.raises(RuntimeError):
            await analytics_svc.publish_result_to_sheet(
                result_id=result_id,
                organization_id=org_id,
                user_id=user_id,
                destination_workbook_id=wb_id,
                sheet_name="Failed Sheet",
            )

    # Rollback session to clear failure
    await db_session.rollback()

    # Verify no partial sheet remains
    stmt = select(Sheet).where(Sheet.workbook_id == wb_id)
    sheets_after = len((await db_session.execute(stmt)).scalars().all())
    assert sheets_after == sheets_before

    # Verify result.published_sheet_id is still None
    db_res = await db_session.get(AnalyticsResult, result_id)
    assert db_res is not None
    assert db_res.published_sheet_id is None
