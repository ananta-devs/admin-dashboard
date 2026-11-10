"""Integration tests for Governed User-Defined Python Analytics scripts and sandbox security."""

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
async def test_python_script_crud_and_versioning(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Test script definition CRUD and automatic version increment on code update."""
    uid = uuid.uuid4().hex[:8]
    owner_email = f"script_owner_{uid}@example.com"
    data, csrf_token, org_id = await register_and_login_helper(api_client, owner_email)

    code_v1 = "result = {'sum': sum([1, 2, 3])}"
    create_payload = {
        "title": "Basic Summation",
        "description": "Calculates sum of list",
        "code": code_v1,
        "parameters_schema": {},
    }

    # 1. Create Script
    create_res = await api_client.post(
        "/api/v1/scripts",
        json=create_payload,
        headers={"X-CSRF-Token": csrf_token},
    )
    assert create_res.status_code == 201, create_res.text
    script_data = create_res.json()
    script_id = script_data["id"]
    assert script_data["version"] == 1

    # 2. Update Script Code -> version increments to 2
    code_v2 = "result = {'sum': sum([1, 2, 3, 4])}"
    update_res = await api_client.patch(
        f"/api/v1/scripts/{script_id}",
        json={"code": code_v2},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert update_res.status_code == 200
    updated_data = update_res.json()
    assert updated_data["version"] == 2
    assert updated_data["code"] == code_v2

    # 3. List Scripts
    list_res = await api_client.get(
        "/api/v1/scripts",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert list_res.status_code == 200
    scripts = list_res.json()
    assert any(s["id"] == script_id for s in scripts)

    # 4. Delete Script
    del_res = await api_client.delete(
        f"/api/v1/scripts/{script_id}",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert del_res.status_code == 204


@pytest.mark.asyncio
async def test_governed_python_execution_against_sheet(
    api_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test executing a governed analytical script against sheet data in container sandbox."""
    monkeypatch.setattr(settings, "PYTHON_SANDBOX_ENABLED", True)
    uid = uuid.uuid4().hex[:8]
    owner_email = f"py_runner_{uid}@example.com"
    data, csrf_token, org_id = await register_and_login_helper(api_client, owner_email)
    user_id = uuid.UUID(data["user"]["id"])

    # Create sheet with records
    ws_repo = WorkspaceRepository(db_session)
    ws_list = await ws_repo.list_by_organization(org_id)
    wb = Workbook(organization_id=org_id, workspace_id=ws_list[0].id, name="Py WB", created_by_user_id=user_id)
    db_session.add(wb)
    await db_session.flush()

    sheet = Sheet(workbook_id=wb.id, organization_id=org_id, name="PySheet")
    db_session.add(sheet)
    await db_session.flush()

    r0 = SheetRow(sheet_id=sheet.id, organization_id=org_id, row_index=0, cells={"A": {"v": "Val", "c": "Val", "f": None}})
    r1 = SheetRow(sheet_id=sheet.id, organization_id=org_id, row_index=1, cells={"A": {"v": 10, "c": 10, "f": None}})
    r2 = SheetRow(sheet_id=sheet.id, organization_id=org_id, row_index=2, cells={"A": {"v": 20, "c": 20, "f": None}})
    r3 = SheetRow(sheet_id=sheet.id, organization_id=org_id, row_index=3, cells={"A": {"v": 30, "c": 30, "f": None}})
    db_session.add_all([r0, r1, r2, r3])
    await db_session.commit()

    # Create Script calculating stats
    user_code = """
def transform(data, params):
    records = data.get("records", [])
    vals = [float(r.get("Val", 0)) for r in records if r.get("Val")]
    multiplier = float(params.get("multiplier", 1.0))
    total = sum(vals) * multiplier
    return {
        "count": len(vals),
        "total": total,
        "average": total / len(vals) if vals else 0.0
    }
"""
    script_res = await api_client.post(
        "/api/v1/scripts",
        json={"title": "Stats Calculator", "code": user_code},
        headers={"X-CSRF-Token": csrf_token},
    )
    script_id = script_res.json()["id"]

    # Execute Script with parameters
    run_res = await api_client.post(
        f"/api/v1/scripts/{script_id}/run",
        json={"source_sheet_id": str(sheet.id), "parameters": {"multiplier": 2.0}},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert run_res.status_code == 200, run_res.text
    run_data = run_res.json()
    assert run_data["status"] == "completed"
    assert run_data["input_cell_count"] == 4
    assert run_data["execution_time_ms"] is not None

    payload = run_data["result_payload"]
    assert payload["count"] == 3
    # (10 + 20 + 30) * 2 = 120.0
    assert payload["total"] == 120.0
    assert payload["average"] == 40.0


@pytest.mark.asyncio
async def test_governed_sandbox_security_rejections(
    api_client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify security controls: hazardous imports, calls, and execution timeout."""
    monkeypatch.setattr(settings, "PYTHON_SANDBOX_ENABLED", True)
    uid = uuid.uuid4().hex[:8]
    owner_email = f"hacker_{uid}@example.com"
    data, csrf_token, org_id = await register_and_login_helper(api_client, owner_email)

    # 1. Prohibited import: subprocess
    bad_code_import = "import subprocess\nsubprocess.run(['dir'])"
    res1 = await api_client.post(
        "/api/v1/scripts",
        json={"title": "Malicious Import", "code": bad_code_import},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert res1.status_code == 201
    bad_script_1 = res1.json()["id"]

    run_res_1 = await api_client.post(
        f"/api/v1/scripts/{bad_script_1}/run",
        json={},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert run_res_1.status_code == 200
    assert run_res_1.json()["status"] == "rejected"
    assert "Security violation" in run_res_1.json()["error_message"]

    # 2. Prohibited call: eval()
    bad_code_call = "result = eval('10 + 20')"
    res2 = await api_client.post(
        "/api/v1/scripts",
        json={"title": "Malicious Eval", "code": bad_code_call},
        headers={"X-CSRF-Token": csrf_token},
    )
    bad_script_2 = res2.json()["id"]

    run_res_2 = await api_client.post(
        f"/api/v1/scripts/{bad_script_2}/run",
        json={},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert run_res_2.status_code == 200
    assert run_res_2.json()["status"] == "rejected"
    assert "eval()" in run_res_2.json()["error_message"]

    # 3. Timeout bounds: fast infinite loop
    loop_code = "while True:\n    pass"
    res3 = await api_client.post(
        "/api/v1/scripts",
        json={"title": "Infinite Loop", "code": loop_code},
        headers={"X-CSRF-Token": csrf_token},
    )
    bad_script_3 = res3.json()["id"]

    # Run with small timeout for testing
    from unittest.mock import patch
    with patch("app.services.script.settings.PYTHON_SANDBOX_TIMEOUT_SECONDS", 2):
        run_res_3 = await api_client.post(
            f"/api/v1/scripts/{bad_script_3}/run",
            json={},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert run_res_3.status_code == 200
        assert run_res_3.json()["status"] == "timeout"


@pytest.mark.asyncio
async def test_python_script_tenant_isolation_and_rbac(
    api_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Verify tenant isolation and viewer role execution permission checks."""
    uid_a = uuid.uuid4().hex[:8]
    data_a, csrf_a, org_a_id = await register_and_login_helper(
        api_client, f"owner_scr_a_{uid_a}@example.com"
    )

    create_res = await api_client.post(
        "/api/v1/scripts",
        json={"title": "Org A Script", "code": "result = 42"},
        headers={"X-CSRF-Token": csrf_a},
    )
    assert create_res.status_code == 201
    script_a_id = create_res.json()["id"]

    # Register Org B
    uid_b = uuid.uuid4().hex[:8]
    _, csrf_b, org_b_id = await register_and_login_helper(
        api_client, f"owner_scr_b_{uid_b}@example.com"
    )

    # Org B cannot access Org A script -> 404
    get_res = await api_client.get(
        f"/api/v1/scripts/{script_a_id}",
        headers={"X-CSRF-Token": csrf_b},
    )
    assert get_res.status_code == 404

    # Org B cannot execute Org A script -> 404
    exec_res = await api_client.post(
        f"/api/v1/scripts/{script_a_id}/run",
        json={},
        headers={"X-CSRF-Token": csrf_b},
    )
    assert exec_res.status_code == 404

    # Viewer in Org A has read access, but cannot delete or create
    viewer_email = f"viewer_scr_{uid_a}@example.com"
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

    # Viewer can read script
    v_get = await api_client.get(
        f"/api/v1/scripts/{script_a_id}",
        headers={"X-CSRF-Token": viewer_csrf},
    )
    assert v_get.status_code == 200

    # Viewer cannot delete script -> 403 Forbidden
    v_del = await api_client.delete(
        f"/api/v1/scripts/{script_a_id}",
        headers={"X-CSRF-Token": viewer_csrf, "Origin": "http://localhost:3000"},
    )
    assert v_del.status_code == 403

    # Viewer can read security policy (analytics.scripts.read)
    v_pol = await api_client.get(
        "/api/v1/scripts/environment/policy",
        headers={"X-CSRF-Token": viewer_csrf},
    )
    assert v_pol.status_code == 200
    assert v_pol.json()["sandbox_enabled"] is False
    assert "polars" in v_pol.json()["vetted_libraries"]

    # Member B in Org A (with analytics.scripts.update) cannot edit Member A's script
    member_b_email = f"member_b_{uid_a}@example.com"
    user_b = await user_repo.create(
        User(email=member_b_email, normalized_email=member_b_email.lower(), display_name="Member B")
    )
    # If member role has update permission
    mem_b = await mem_repo.create(
        OrganizationMembership(user_id=user_b.id, organization_id=org_a_id)
    )
    # Give member_b member role (has update)
    member_role = await role_repo.get_by_slug("member", org_a_id) or viewer_role
    db_session.add(MembershipRole(membership_id=mem_b.id, role_id=member_role.id, organization_id=org_a_id))
    await db_session.commit()

    _, b_token, b_csrf = await sess_service.create_session(
        user_id=user_b.id,
        ip_address="127.0.0.1",
        user_agent="pytest",
        active_organization_id=org_a_id,
    )
    await db_session.commit()

    api_client.cookies.set(settings.COOKIE_NAME, b_token)
    b_upd = await api_client.patch(
        f"/api/v1/scripts/{script_a_id}",
        json={"title": "Hijacked Script Title"},
        headers={"X-CSRF-Token": b_csrf, "Origin": "http://localhost:3000"},
    )
    # Either 403 Forbidden (no permission or not owner)
    assert b_upd.status_code in (403, 404)

    # Admin with analytics.scripts.manage CAN update or delete Member A's script
    admin_email = f"admin_mgr_{uid_a}@example.com"
    user_admin = await user_repo.create(
        User(email=admin_email, normalized_email=admin_email.lower(), display_name="Admin Manager")
    )
    mem_admin = await mem_repo.create(
        OrganizationMembership(user_id=user_admin.id, organization_id=org_a_id)
    )
    admin_role = await role_repo.get_by_slug("admin", org_a_id) or viewer_role
    db_session.add(MembershipRole(membership_id=mem_admin.id, role_id=admin_role.id, organization_id=org_a_id))
    await db_session.commit()

    _, adm_token, adm_csrf = await sess_service.create_session(
        user_id=user_admin.id,
        ip_address="127.0.0.1",
        user_agent="pytest",
        active_organization_id=org_a_id,
    )
    await db_session.commit()

    api_client.cookies.set(settings.COOKIE_NAME, adm_token)
    adm_upd = await api_client.patch(
        f"/api/v1/scripts/{script_a_id}",
        json={"title": "Admin Managed Title"},
        headers={"X-CSRF-Token": adm_csrf, "Origin": "http://localhost:3000"},
    )
    assert adm_upd.status_code == 200, adm_upd.text
    assert adm_upd.json()["title"] == "Admin Managed Title"


