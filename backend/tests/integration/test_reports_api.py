"""Integration tests for the reporting REST API endpoints."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.repositories.workspace import WorkspaceRepository
from app.tasks.reporting import _execute_report_run_async


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
    org_name = f"Report Org {uid}"
    org_slug = f"report-org-{uid}"

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
async def test_report_definitions_and_sections_api_lifecycle(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Test full CRUD lifecycle for report definitions and section management via HTTP."""
    uid = uuid.uuid4().hex[:8]
    owner_email = f"rep_owner_{uid}@example.com"
    data, csrf_token, org_id = await register_and_login_helper(api_client, owner_email)

    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    ws_id = ws_list[0].id

    headers = {"X-CSRF-Token": csrf_token}

    # 1. Create Report Definition
    def_payload = {
        "workspace_id": str(ws_id),
        "title": "Quarterly Operations Report",
        "description": "Multi-section enterprise metrics",
        "format": "pdf",
        "page_orientation": "portrait",
        "page_size": "a4",
        "header_text": "Confidential",
        "footer_text": "Enterprise Internal",
    }
    create_res = await api_client.post(
        "/api/v1/reports/definitions",
        json=def_payload,
        headers=headers,
    )
    assert create_res.status_code == 201
    created_def = create_res.json()
    def_id = created_def["id"]
    assert created_def["title"] == "Quarterly Operations Report"

    # 2. Add Section
    sec_payload = {
        "section_type": "text",
        "title": "Executive Summary",
        "custom_content": "Operational efficiency increased by 18%.",
        "sort_order": 0,
        "page_break_after": False,
    }
    sec_res = await api_client.post(
        f"/api/v1/reports/definitions/{def_id}/sections",
        json=sec_payload,
        headers=headers,
    )
    assert sec_res.status_code == 201
    sec_data = sec_res.json()
    sec_id = sec_data["id"]
    assert sec_data["title"] == "Executive Summary"

    # 3. Update Section
    update_sec_res = await api_client.patch(
        f"/api/v1/reports/definitions/{def_id}/sections/{sec_id}",
        json={"title": "Updated Executive Summary"},
        headers=headers,
    )
    assert update_sec_res.status_code == 200
    assert update_sec_res.json()["title"] == "Updated Executive Summary"

    # 4. Read Report Definition with sections
    get_res = await api_client.get(f"/api/v1/reports/definitions/{def_id}")
    assert get_res.status_code == 200
    fetched_def = get_res.json()
    assert len(fetched_def["sections"]) == 1
    assert fetched_def["sections"][0]["title"] == "Updated Executive Summary"

    # 5. List Definitions
    list_res = await api_client.get("/api/v1/reports/definitions")
    assert list_res.status_code == 200
    assert len(list_res.json()) >= 1

    # 6. Delete Section
    del_sec_res = await api_client.delete(
        f"/api/v1/reports/definitions/{def_id}/sections/{sec_id}",
        headers=headers,
    )
    assert del_sec_res.status_code == 204

    # 7. Delete (Archive) Definition
    del_def_res = await api_client.delete(
        f"/api/v1/reports/definitions/{def_id}",
        headers=headers,
    )
    assert del_def_res.status_code == 204


@pytest.mark.asyncio
async def test_report_runs_and_download_flow_api(
    api_client: AsyncClient,
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    """Test triggering run, status polling, token generation, and single-use streaming."""
    uid = uuid.uuid4().hex[:8]
    owner_email = f"run_owner_{uid}@example.com"
    data, csrf_token, org_id = await register_and_login_helper(api_client, owner_email)

    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    ws_id = ws_list[0].id

    headers = {"X-CSRF-Token": csrf_token}

    # 1. Create Report Definition
    def_res = await api_client.post(
        "/api/v1/reports/definitions",
        json={"workspace_id": str(ws_id), "title": "API Download Test Report"},
        headers=headers,
    )
    assert def_res.status_code == 201
    def_id = def_res.json()["id"]

    # Add section
    await api_client.post(
        f"/api/v1/reports/definitions/{def_id}/sections",
        json={
            "section_type": "text",
            "title": "Intro",
            "custom_content": "Content for PDF testing.",
        },
        headers=headers,
    )

    # 2. Trigger Run
    trigger_payload = {"idempotency_key": f"key-{uuid.uuid4()}", "trigger_type": "manual"}
    run_res = await api_client.post(
        f"/api/v1/reports/definitions/{def_id}/runs",
        json=trigger_payload,
        headers=headers,
    )
    assert run_res.status_code == 202
    run_data = run_res.json()
    run_id = run_data["id"]
    assert run_data["status"] == "queued"

    # 3. Simulate background Celery worker execution
    storage_dir = str(tmp_path / "reports_api")
    with patch.object(settings, "REPORTS_STORAGE_DIR", storage_dir):
        exec_result = await _execute_report_run_async(run_id, "celery-api-task-1")
        assert exec_result["status"] == "succeeded"

        # 4. Poll Run status via API
        poll_res = await api_client.get(f"/api/v1/reports/runs/{run_id}")
        assert poll_res.status_code == 200
        poll_data = poll_res.json()
        assert poll_data["status"] == "succeeded"
        assert poll_data["pdf_artifact_path"] is not None
        assert poll_data["pdf_page_count"] >= 1
        assert poll_data["pdf_file_size_bytes"] > 0

        # 5. Direct authenticated download
        direct_res = await api_client.get(f"/api/v1/reports/runs/{run_id}/download")
        assert direct_res.status_code == 200
        assert direct_res.headers["content-type"] == "application/pdf"
        assert len(direct_res.content) == poll_data["pdf_file_size_bytes"]
        assert hashlib.sha256(direct_res.content).hexdigest() == poll_data["pdf_checksum_sha256"]

        # 6. Generate single-use download token
        tok_res = await api_client.post(
            f"/api/v1/reports/runs/{run_id}/download-token",
            headers=headers,
        )
        assert tok_res.status_code == 200
        tok_data = tok_res.json()
        download_token = tok_data["download_token"]
        assert download_token is not None
        download_url = tok_data["download_url"]

        # 7. Consume token via public download endpoint
        # First request: 200 OK with PDF bytes
        pub_res_1 = await api_client.get(download_url)
        assert pub_res_1.status_code == 200
        assert pub_res_1.headers["content-type"] == "application/pdf"
        assert len(pub_res_1.content) == poll_data["pdf_file_size_bytes"]

        # Second request with SAME token: 400 Bad Request (token burned!)
        pub_res_2 = await api_client.get(download_url)
        assert pub_res_2.status_code == 400
        assert "consumed" in pub_res_2.json()["error"]["message"]

        # 8. Test concurrent simultaneous consumption of a single token
        tok_res_concurrent = await api_client.post(
            f"/api/v1/reports/runs/{run_id}/download-token",
            headers=headers,
        )
        assert tok_res_concurrent.status_code == 200
        concurrent_url = tok_res_concurrent.json()["download_url"]

        # Fire two concurrent GET requests simultaneously
        res_a, res_b = await asyncio.gather(
            api_client.get(concurrent_url),
            api_client.get(concurrent_url),
        )
        statuses = [res_a.status_code, res_b.status_code]
        assert statuses.count(200) == 1, f"Expected exactly one 200 OK, got {statuses}"
        assert statuses.count(400) == 1, f"Expected exactly one 400 Bad Request, got {statuses}"

        # 9. Test production fail-closed semantics for token consumption
        from app.core.exceptions import InvalidTokenError
        from app.services.report_download_token import ReportDownloadTokenService

        with (
            patch.object(settings, "ENVIRONMENT", "production"),
            patch.object(ReportDownloadTokenService, "get_redis_client", return_value=None),
        ):
            with pytest.raises(InvalidTokenError, match="storage unavailable"):
                await ReportDownloadTokenService.consume_download_token("some_token")
