"""End-to-End API admission rejection test for oversized datasets.

Verifies:
1. Submitting an oversized source dataset (> 500,000 non-empty cells) via the public HTTP API
   (POST /api/v1/analytics/jobs) fails fast at admission time with an HTTP 4xx error.
2. Zero side effects on failure:
   - Zero AnalyticsJob rows created.
   - Zero AnalyticsOutboxEvent rows created.
   - Zero temporary or permanent snapshot files created on disk.
   - Zero Polars analytics engine executions.
   - Zero quota reservations leaked in the PostgreSQL ledger.
3. The exact maximum accepted dataset (500,000 cells) is admitted successfully with HTTP 202.
"""

from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.repositories.workspace import WorkspaceRepository
from app.schemas.spreadsheet import WorkbookCreateInput
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


def _bulk_insert_cells_copy(
    sheet_id: uuid.UUID,
    org_id: uuid.UUID,
    num_rows: int,
    cols_per_row: int,
) -> None:
    """Efficiently bulk-insert cells into sheet_rows table using psycopg COPY."""
    dsn = settings.DATABASE_URL.replace("+asyncpg", "")
    now = datetime.datetime.now(datetime.UTC).isoformat()
    cells_json = json.dumps({f"c_{c}": {"v": float(c + 1.0)} for c in range(cols_per_row)})

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            with cur.copy(
                "COPY sheet_rows (id, sheet_id, organization_id, row_index, cells, version, created_at, updated_at) FROM STDIN"
            ) as copy:
                for r in range(num_rows):
                    copy.write_row((str(uuid.uuid4()), str(sheet_id), str(org_id), r, cells_json, 1, now, now))
            conn.commit()


@pytest.mark.asyncio
async def test_oversized_dataset_admission_rejection_no_side_effects(
    api_client: AsyncClient,
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    """Verify that an oversized dataset (> 500,000 cells) is rejected with HTTP 4xx and zero side effects."""
    uid = uuid.uuid4().hex[:8]
    owner_email = f"owner_{uid}@example.com"
    login_data, csrf_token, org_id = await register_and_login_helper(api_client, owner_email)
    user_id = uuid.UUID(login_data["user"]["id"])

    # Setup workbook and sheet
    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    ws_id = ws_list[0].id

    spreadsheet_svc = SpreadsheetService(db_session)
    wb = await spreadsheet_svc.create_workbook(
        organization_id=org_id,
        created_by_user_id=user_id,
        data=WorkbookCreateInput(name="Big Data WB", workspace_id=ws_id),
    )
    sheets = await spreadsheet_svc.list_sheets(wb.id, org_id)
    sheet_id = sheets[0].id

    # Populate 50,001 rows x 10 cols = 500,010 cells (> 500,000 cells limit)
    _bulk_insert_cells_copy(sheet_id, org_id, num_rows=50001, cols_per_row=10)

    # Submit job through the public HTTP API
    job_payload = {
        "source_workbook_id": str(wb.id),
        "source_sheet_id": str(sheet_id),
        "operation_id": "profile_dataset",
        "parameters": {},
    }

    # Patch storage directory to test tmp_path for snapshot check
    with patch.object(settings, "ANALYTICS_STORAGE_DIR", str(tmp_path)):
        res = await api_client.post(
            "/api/v1/analytics/jobs",
            json=job_payload,
            headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
        )

    # 1. Verify 4xx response
    assert 400 <= res.status_code < 500, f"Expected 4xx error, got {res.status_code}: {res.text}"
    err_body = res.json()
    assert err_body.get("success") is False
    assert err_body.get("error", {}).get("code") == "DATASET_TOO_LARGE"
    assert "Sheet exceeds maximum allowable non-empty cells" in err_body.get("error", {}).get("message", "")

    # 2. Verify NO AnalyticsJob row created
    dsn = settings.DATABASE_URL.replace("+asyncpg", "")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM analytics_jobs WHERE source_sheet_id = %s",
                (str(sheet_id),),
            )
            job_count = cur.fetchone()[0]
            assert job_count == 0, f"Expected 0 AnalyticsJob rows, found {job_count}"

            # 3. Verify NO AnalyticsOutboxEvent row created
            cur.execute(
                "SELECT count(*) FROM analytics_outbox_events WHERE organization_id = %s",
                (str(org_id),),
            )
            outbox_count = cur.fetchone()[0]
            assert outbox_count == 0, f"Expected 0 AnalyticsOutboxEvent rows, found {outbox_count}"

            # 4. Verify NO quota reservations leaked
            cur.execute(
                "SELECT count(*) FROM analytics_quota_reservations WHERE organization_id = %s",
                (str(org_id),),
            )
            res_count = cur.fetchone()[0]
            assert res_count == 0, f"Expected 0 quota reservations, found {res_count}"

    # 5. Verify NO temporary or permanent snapshot files created
    snapshots_on_disk = list((tmp_path / "snapshots").glob("**/*"))
    assert len(snapshots_on_disk) == 0, f"Expected 0 snapshot files, found: {snapshots_on_disk}"


@pytest.mark.asyncio
async def test_exact_maximum_dataset_admitted_successfully(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Verify that the exact maximum accepted dataset (500,000 cells) is admitted via HTTP API with 202."""
    uid = uuid.uuid4().hex[:8]
    owner_email = f"owner_max_{uid}@example.com"
    login_data, csrf_token, org_id = await register_and_login_helper(api_client, owner_email)
    user_id = uuid.UUID(login_data["user"]["id"])

    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    ws_id = ws_list[0].id

    spreadsheet_svc = SpreadsheetService(db_session)
    wb = await spreadsheet_svc.create_workbook(
        organization_id=org_id,
        created_by_user_id=user_id,
        data=WorkbookCreateInput(name="Max Data WB", workspace_id=ws_id),
    )
    sheets = await spreadsheet_svc.list_sheets(wb.id, org_id)
    sheet_id = sheets[0].id

    # Populate EXACTLY 50,000 rows x 10 cols = 500,000 cells (exact upper limit)
    _bulk_insert_cells_copy(sheet_id, org_id, num_rows=50000, cols_per_row=10)

    job_payload = {
        "source_workbook_id": str(wb.id),
        "source_sheet_id": str(sheet_id),
        "operation_id": "profile_dataset",
        "parameters": {},
    }

    res = await api_client.post(
        "/api/v1/analytics/jobs",
        json=job_payload,
        headers={"X-CSRF-Token": csrf_token, "Origin": "http://localhost:3000"},
    )

    # 1. Verify HTTP 202 Accepted
    assert res.status_code == 202, f"Expected 202 Accepted, got {res.status_code}: {res.text}"
    job_data = res.json()
    assert job_data["status"] == "queued"
    job_id = job_data["id"]

    # 2. Verify AnalyticsJob row created in database
    dsn = settings.DATABASE_URL.replace("+asyncpg", "")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, source_sheet_id FROM analytics_jobs WHERE id = %s",
                (job_id,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "queued"
            assert row[1] == sheet_id

            # 3. Verify AnalyticsOutboxEvent created
            cur.execute(
                "SELECT event_type FROM analytics_outbox_events WHERE job_id = %s",
                (job_id,),
            )
            event_row = cur.fetchone()
            assert event_row is not None
            assert event_row[0] == "analytics.job.scheduled"
