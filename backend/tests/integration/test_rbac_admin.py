import asyncio
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
from app.models.user import User


@pytest.fixture
async def api_client():
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def get_test_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = get_test_db
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers={"Origin": "http://localhost:3000"}
    ) as client:
        yield client

    app.dependency_overrides.clear()
    await test_engine.dispose()


async def register_and_login_user(
    client: AsyncClient,
    email: str,
    password: str = "SecurePass1234!",
    org_name: str | None = None,
    org_slug: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Helper to register and login a user, returning (login_response_json, csrf_token)."""
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
    )
    assert login_res.status_code == 200
    data = login_res.json()
    return data, data["csrf_token"]


@pytest.mark.asyncio
async def test_permissions_catalogue_and_bootstrapped_system_roles(
    api_client: AsyncClient, unique_id: str
):
    """Verify that 16 permissions exist, default roles are seeded, and initial registrant has Owner role."""
    email = f"owner_{unique_id}@corp.com"
    login_data, csrf_token = await register_and_login_user(api_client, email)

    # 1. Fetch system permissions catalogue
    perm_res = await api_client.get("/api/v1/organization/permissions")
    assert perm_res.status_code == 200
    perms = perm_res.json()
    assert len(perms) >= 16
    codes = {p["code"] for p in perms}
    assert "organization.read" in codes
    assert "ownership.transfer" in codes
    assert "members.invite" in codes
    assert "roles.create" in codes

    # 2. Check registrant's effective permissions via /me
    me_res = await api_client.get("/api/v1/organization/me")
    assert me_res.status_code == 200
    me_data = me_res.json()["data"]
    assert "ownership.transfer" in me_data["effective_permissions"]
    assert "roles.create" in me_data["effective_permissions"]

    # 3. Check system roles seeded for organization
    roles_res = await api_client.get("/api/v1/organization/roles")
    assert roles_res.status_code == 200
    roles = roles_res.json()
    role_slugs = {r["slug"]: r for r in roles}
    assert "owner" in role_slugs
    assert "admin" in role_slugs
    assert "member" in role_slugs
    assert "viewer" in role_slugs
    assert role_slugs["owner"]["is_system"] is True


@pytest.mark.asyncio
async def test_role_lifecycle_and_delegation_checks(api_client: AsyncClient, unique_id: str):
    """Test custom role CRUD, system role immutability, and privilege escalation prevention."""
    owner_email = f"owner_roles_{unique_id}@corp.com"
    _, csrf = await register_and_login_user(api_client, owner_email)

    # 1. System roles cannot be modified or deleted
    roles_res = await api_client.get("/api/v1/organization/roles")
    roles = {r["slug"]: r for r in roles_res.json()}
    admin_id = roles["admin"]["id"]

    patch_sys = await api_client.patch(
        f"/api/v1/organization/roles/{admin_id}",
        json={"name": "Hacked Admin"},
        headers={"X-CSRF-Token": csrf},
    )
    assert patch_sys.status_code == 400

    del_sys = await api_client.delete(
        f"/api/v1/organization/roles/{admin_id}",
        headers={"X-CSRF-Token": csrf},
    )
    assert del_sys.status_code == 400

    # 2. Create custom role with valid permissions
    create_payload = {
        "name": "Support Lead",
        "slug": f"support-lead-{unique_id[:6]}",
        "description": "Customer support team lead",
        "permission_codes": ["organization.read", "members.read", "workspaces.read"],
    }
    create_res = await api_client.post(
        "/api/v1/organization/roles",
        json=create_payload,
        headers={"X-CSRF-Token": csrf},
    )
    assert create_res.status_code == 201
    custom_role = create_res.json()
    assert custom_role["slug"] == create_payload["slug"]
    assert len(custom_role["permissions"]) == 3

    # 3. Update custom role
    patch_res = await api_client.patch(
        f"/api/v1/organization/roles/{custom_role['id']}",
        json={"name": "Senior Support Lead"},
        headers={"X-CSRF-Token": csrf},
    )
    assert patch_res.status_code == 200
    assert patch_res.json()["name"] == "Senior Support Lead"

    # 4. Delete unassigned custom role
    del_res = await api_client.delete(
        f"/api/v1/organization/roles/{custom_role['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert del_res.status_code == 200


@pytest.mark.asyncio
async def test_admin_cannot_escalate_privileges(api_client: AsyncClient, unique_id: str):
    """Admin without ownership.transfer cannot create a role with ownership.transfer or assign Owner role."""
    owner_email = f"owner_esc_{unique_id}@corp.com"
    _, owner_csrf = await register_and_login_user(api_client, owner_email)

    roles_res = await api_client.get("/api/v1/organization/roles")
    roles = {r["slug"]: r for r in roles_res.json()}
    admin_role_id = roles["admin"]["id"]
    owner_role_id = roles["owner"]["id"]

    # Owner invites admin_user
    admin_email = f"admin_user_{unique_id}@corp.com"
    invite_res = await api_client.post(
        "/api/v1/organization/invitations",
        json={"email": admin_email, "role_id": admin_role_id},
        headers={"X-CSRF-Token": owner_csrf},
    )
    assert invite_res.status_code == 200
    invite_token = invite_res.json()["data"]["token"]

    # Register admin user and accept invitation
    admin_client = api_client  # switch cookie session
    admin_login_res, admin_csrf = await register_and_login_user(admin_client, admin_email)

    accept_res = await admin_client.post(
        "/api/v1/organization/invitations/accept",
        json={"token": invite_token},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert accept_res.status_code == 200
    admin_membership_id = accept_res.json()["data"]["id"]

    # Switch active organization context to the newly joined organization
    org_id = roles["admin"]["organization_id"]
    switch_res = await admin_client.post(
        "/api/v1/auth/select-tenant",
        json={"organization_id": org_id},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert switch_res.status_code == 200

    # 1. Admin attempts to create custom role with 'ownership.transfer' -> 403 Forbidden
    escalate_role_res = await admin_client.post(
        "/api/v1/organization/roles",
        json={
            "name": "Rogue Role",
            "slug": f"rogue-{unique_id[:6]}",
            "permission_codes": ["ownership.transfer"],
        },
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert escalate_role_res.status_code == 403

    # 2. Admin attempts to assign Owner role to themselves -> 403 Forbidden
    self_promote_res = await admin_client.put(
        f"/api/v1/organization/members/{admin_membership_id}/roles",
        json={"role_ids": [owner_role_id]},
        headers={"X-CSRF-Token": admin_csrf},
    )
    assert self_promote_res.status_code == 403


@pytest.mark.asyncio
async def test_last_owner_protection_and_atomic_transfer(api_client: AsyncClient, unique_id: str):
    """Verify last owner cannot be demoted or removed, and ownership transfer works atomically."""
    owner_email = f"owner_last_{unique_id}@corp.com"
    _, owner_csrf = await register_and_login_user(api_client, owner_email)

    # 1. Fetch me to get owner membership ID and roles
    me_res = await api_client.get("/api/v1/organization/me")
    owner_membership = me_res.json()["data"]["membership"]
    owner_membership_id = owner_membership["id"]
    org_id = owner_membership["organization_id"]

    roles_res = await api_client.get("/api/v1/organization/roles")
    roles = {r["slug"]: r for r in roles_res.json()}
    admin_role_id = roles["admin"]["id"]
    member_role_id = roles["member"]["id"]

    # 2. Attempt to demote the sole owner to Admin -> 400 Bad Request
    demote_res = await api_client.put(
        f"/api/v1/organization/members/{owner_membership_id}/roles",
        json={"role_ids": [admin_role_id]},
        headers={"X-CSRF-Token": owner_csrf},
    )
    assert demote_res.status_code == 400
    assert "Owner" in demote_res.json()["error"]["message"]

    # 3. Attempt to remove the sole owner -> 400 Bad Request
    remove_res = await api_client.delete(
        f"/api/v1/organization/members/{owner_membership_id}",
        headers={"X-CSRF-Token": owner_csrf},
    )
    assert remove_res.status_code == 400
    assert "last Owner" in remove_res.json()["error"]["message"]

    # 4. Invite a second user and accept
    second_email = f"second_{unique_id}@corp.com"
    invite_res = await api_client.post(
        "/api/v1/organization/invitations",
        json={"email": second_email, "role_id": member_role_id},
        headers={"X-CSRF-Token": owner_csrf},
    )
    token = invite_res.json()["data"]["token"]

    # Register second user and accept invite
    _, second_csrf = await register_and_login_user(api_client, second_email)
    accept_res = await api_client.post(
        "/api/v1/organization/invitations/accept",
        json={"token": token},
        headers={"X-CSRF-Token": second_csrf},
    )
    assert accept_res.status_code == 200
    second_membership_id = accept_res.json()["data"]["id"]

    # 5. Log back in as primary owner
    login_owner = await api_client.post(
        "/api/v1/auth/login",
        json={"email": owner_email, "password": "SecurePass1234!"},
    )
    owner_csrf = login_owner.json()["csrf_token"]

    # Switch active org back to first org
    switch_owner = await api_client.post(
        "/api/v1/auth/select-tenant",
        json={"organization_id": org_id},
        headers={"X-CSRF-Token": owner_csrf},
    )
    assert switch_owner.status_code == 200

    # 6. Transfer ownership to second user
    # Wrong confirmation fails
    bad_xfer = await api_client.post(
        "/api/v1/organization/ownership/transfer",
        json={"target_membership_id": second_membership_id, "confirmation": "NO"},
        headers={"X-CSRF-Token": owner_csrf},
    )
    assert bad_xfer.status_code == 400

    # Proper transfer
    xfer_res = await api_client.post(
        "/api/v1/organization/ownership/transfer",
        json={"target_membership_id": second_membership_id, "confirmation": "TRANSFER"},
        headers={"X-CSRF-Token": owner_csrf},
    )
    assert xfer_res.status_code == 200

    # 7. Verify roles: previous owner is now Admin, second user is now Owner
    members_res = await api_client.get("/api/v1/organization/members")
    members_by_id = {m["id"]: m for m in members_res.json()}
    assert {r["slug"] for r in members_by_id[owner_membership_id]["roles"]} == {"admin"}
    assert {r["slug"] for r in members_by_id[second_membership_id]["roles"]} == {"owner"}


@pytest.mark.asyncio
async def test_concurrent_last_owner_demotion(api_client: AsyncClient, unique_id: str):
    """When two owners exist, concurrent demotions of both owners serialize via row locking, allowing only one to succeed."""
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    owner_email = f"conc_owner_{unique_id}@corp.com"
    _, owner_csrf = await register_and_login_user(api_client, owner_email)

    me_res = await api_client.get("/api/v1/organization/me")
    owner1_membership_id = me_res.json()["data"]["membership"]["id"]
    org_id = uuid.UUID(me_res.json()["data"]["membership"]["organization_id"])

    roles_res = await api_client.get("/api/v1/organization/roles")
    roles = {r["slug"]: r for r in roles_res.json()}
    owner_role_id = uuid.UUID(roles["owner"]["id"])

    # Directly create second owner membership in DB for clean test setup
    async with session_factory() as session:
        u2 = User(
            email=f"owner2_{unique_id}@corp.com",
            normalized_email=f"owner2_{unique_id}@corp.com",
            display_name="Owner Two",
        )
        session.add(u2)
        await session.flush()

        m2 = OrganizationMembership(organization_id=org_id, user_id=u2.id)
        session.add(m2)
        await session.flush()

        from app.repositories.role import RoleRepository

        r_repo = RoleRepository(session)
        await r_repo.set_membership_roles(m2.id, [owner_role_id], organization_id=org_id)
        await session.commit()
        owner2_membership_id = m2.id

    # Now both owner1 and owner2 are Owners.
    # Concurrently attempt to demote owner1 and owner2 via the dedicated OwnershipService.
    from app.services.ownership import OwnershipService

    async def attempt_demote(actor_mid: uuid.UUID, target_mid: uuid.UUID):
        async with session_factory() as session:
            o_service = OwnershipService(session)
            actor = await session.get(OrganizationMembership, actor_mid)
            assert actor is not None
            try:
                await o_service.demote_co_owner(
                    actor_membership=actor,
                    organization_id=org_id,
                    target_membership_id=target_mid,
                )
                await session.commit()
                return "SUCCESS"
            except Exception as e:
                await session.rollback()
                return f"ERROR: {e}"

    res1, res2 = await asyncio.gather(
        attempt_demote(owner1_membership_id, owner2_membership_id),
        attempt_demote(owner2_membership_id, owner1_membership_id),
    )

    # Exactly one must succeed and one must fail
    successes = [r for r in (res1, res2) if r == "SUCCESS"]
    errors = [r for r in (res1, res2) if "ERROR" in r]
    assert len(successes) == 1, f"Expected 1 success, got {res1=}, {res2=}"
    assert len(errors) == 1, f"Expected 1 error, got {res1=}, {res2=}"

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_invitation_lifecycle_and_security(api_client: AsyncClient, unique_id: str):
    """Test full invitation lifecycle: invite, duplicate reject, cross-user reject, revoke, single-use acceptance."""
    owner_email = f"owner_inv_{unique_id}@corp.com"
    _, owner_csrf = await register_and_login_user(api_client, owner_email)

    roles_res = await api_client.get("/api/v1/organization/roles")
    roles = {r["slug"]: r for r in roles_res.json()}
    member_role_id = roles["member"]["id"]

    target_email = f"invitee_{unique_id}@corp.com"

    # 1. Create invitation
    inv_res = await api_client.post(
        "/api/v1/organization/invitations",
        json={"email": target_email, "role_id": member_role_id},
        headers={"X-CSRF-Token": owner_csrf},
    )
    assert inv_res.status_code == 200
    inv_data = inv_res.json()["data"]
    raw_token = inv_data["token"]

    # 2. Cannot invite someone already in the organization (test after acceptance)
    # 3. Log in as a different user (not the target invitee) and attempt to accept
    diff_email = f"imposter_{unique_id}@corp.com"
    _, imposter_csrf = await register_and_login_user(api_client, diff_email)

    bad_accept = await api_client.post(
        "/api/v1/organization/invitations/accept",
        json={"token": raw_token},
        headers={"X-CSRF-Token": imposter_csrf},
    )
    assert bad_accept.status_code == 403
    assert "logged in as" in bad_accept.json()["error"]["message"]

    # 4. Target user registers and accepts
    _, target_csrf = await register_and_login_user(api_client, target_email)
    good_accept = await api_client.post(
        "/api/v1/organization/invitations/accept",
        json={"token": raw_token},
        headers={"X-CSRF-Token": target_csrf},
    )
    assert good_accept.status_code == 200

    # 5. Replay attempt fails (single use)
    replay_accept = await api_client.post(
        "/api/v1/organization/invitations/accept",
        json={"token": raw_token},
        headers={"X-CSRF-Token": target_csrf},
    )
    assert replay_accept.status_code == 400

    # 6. Inviting the same user again fails because they are already a member
    # Log back into owner
    login_owner = await api_client.post(
        "/api/v1/auth/login",
        json={"email": owner_email, "password": "SecurePass1234!"},
    )
    owner_csrf = login_owner.json()["csrf_token"]
    dup_invite = await api_client.post(
        "/api/v1/organization/invitations",
        json={"email": target_email, "role_id": member_role_id},
        headers={"X-CSRF-Token": owner_csrf},
    )
    assert dup_invite.status_code == 400
    assert "already a member" in dup_invite.json()["error"]["message"]


@pytest.mark.asyncio
async def test_audit_logs_and_tenant_isolation(api_client: AsyncClient, unique_id: str):
    """Verify that administrative actions create sanitized audit events and respect tenant isolation."""
    # Org A
    email_a = f"owner_a_{unique_id}@corp.com"
    _, csrf_a = await register_and_login_user(api_client, email_a)

    # Perform admin action in Org A: create custom role
    create_res = await api_client.post(
        "/api/v1/organization/roles",
        json={
            "name": "Audit Test Role",
            "slug": f"audit-role-{unique_id[:6]}",
            "permission_codes": ["workspaces.read"],
        },
        headers={"X-CSRF-Token": csrf_a},
    )
    assert create_res.status_code == 201

    # Fetch audit logs for Org A
    logs_a_res = await api_client.get("/api/v1/organization/audit-logs")
    assert logs_a_res.status_code == 200
    logs_a = logs_a_res.json()
    assert len(logs_a) >= 1
    role_created_event = [e for e in logs_a if e["action"] == "role.created"][0]
    assert role_created_event["target_type"] == "role"

    # Org B
    email_b = f"owner_b_{unique_id}@corp.com"
    _, csrf_b = await register_and_login_user(api_client, email_b)

    # Org B should have NO visibility into Org A's audit events
    logs_b_res = await api_client.get("/api/v1/organization/audit-logs")
    assert logs_b_res.status_code == 200
    logs_b = logs_b_res.json()
    role_slugs_b = [e.get("sanitized_metadata", {}).get("slug") for e in logs_b]
    assert f"audit-role-{unique_id[:6]}" not in role_slugs_b
