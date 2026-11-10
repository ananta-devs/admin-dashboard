import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.security import hash_password
from app.db.session import async_session_maker
from app.models.credentials import UserCredentials
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.permission import Permission
from app.models.user import User
from app.models.workspace import Workspace
from app.services.audit import AuditService
from app.services.role import RoleService


async def seed_super_owner():
    email = "admin@vibe.enterprise"
    password = "Password1234!"
    display_name = "Enterprise Admin (Owner)"
    org_name = "Acme Corporation"
    org_slug = "acme-corporation"
    workspace_name = "Default Workspace"
    workspace_slug = "default"

    print("Connecting to database...")
    async with async_session_maker() as session:
        # 1. Fetch all system permissions from DB
        res = await session.execute(select(Permission))
        all_perms = res.scalars().all()
        perm_map = {p.code: p.id for p in all_perms}
        all_perm_ids = [p.id for p in all_perms]
        print(f"Loaded {len(all_perms)} permissions from DB.")

        # 2. Create Organization
        org = Organization(
            name=org_name,
            slug=org_slug,
        )
        session.add(org)
        await session.flush()
        print(f"Created Organization: {org.name} ({org.id})")

        # 3. Create User (marked email_verified_at so login is immediate)
        now = datetime.now(UTC)
        user = User(
            email=email,
            normalized_email=email.lower().strip(),
            display_name=display_name,
            email_verified_at=now,
        )
        session.add(user)
        await session.flush()
        print(f"Created User: {user.email} ({user.id})")

        # 4. Create UserCredentials (Argon2id password hash)
        credentials = UserCredentials(
            user_id=user.id,
            password_hash=hash_password(password),
        )
        session.add(credentials)
        await session.flush()
        print("Created UserCredentials with Argon2id hash.")

        # 5. Create Organization Membership
        membership = OrganizationMembership(
            organization_id=org.id,
            user_id=user.id,
        )
        session.add(membership)
        await session.flush()
        print(f"Created Membership: ({membership.id})")

        # 6. Bootstrap Default Roles for this Org
        # Bootstrap: owner, admin, member, viewer
        role_service = RoleService(session)
        created_roles = await role_service.seed_default_roles_for_organization(org.id)
        owner_role = created_roles["owner"]

        # Ensure Owner role has ALL 46 permissions in database explicitly
        await role_service.role_repo.bootstrap_system_role_permissions(owner_role.id, all_perm_ids)
        print(
            f"Assigned all {len(all_perm_ids)} permissions directly to Owner role ({owner_role.id})."
        )

        # 7. Bind Membership to Owner Role
        await role_service.role_repo.set_membership_roles(
            membership.id, [owner_role.id], organization_id=org.id
        )
        print("Assigned Owner role to user's membership.")

        # 8. Create Default Workspace
        workspace = Workspace(
            organization_id=org.id,
            name=workspace_name,
            slug=workspace_slug,
        )
        session.add(workspace)
        await session.flush()
        print(f"Created Default Workspace: {workspace.name} ({workspace.id})")

        # 9. Record Administrative Audit Events
        audit = AuditService(session)
        audit_events = [
            (
                "organization.created",
                "organization",
                str(org.id),
                {"name": org.name, "slug": org.slug},
            ),
            (
                "auth.credentials_registered",
                "user",
                str(user.id),
                {"email": user.email, "method": "argon2id"},
            ),
            (
                "role.bootstrap_completed",
                "role",
                "system_roles",
                {"roles": ["owner", "admin", "member", "viewer"]},
            ),
            (
                "role.permissions_assigned",
                "role",
                "owner",
                {"permission_count": len(all_perm_ids), "scope": "full_enterprise"},
            ),
            (
                "membership.owner_assigned",
                "membership",
                str(user.id),
                {"role": "owner", "user_id": str(user.id)},
            ),
        ]
        for action, target_type, target_id, meta in audit_events:
            await audit.record_event(
                organization_id=org.id,
                organization_slug=org.slug,
                actor_user_id=user.id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                metadata=meta,
            )
        print(f"Recorded {len(audit_events)} administrative audit events in ledger.")

        # Commit transaction
        await session.commit()
        print("\n=== SUCCESS: Sample Enterprise Owner Created Successfully ===")
        print(f"Email:    {email}")
        print(f"Password: {password}")
        print(f"Role:     Owner (has all {len(all_perm_ids)} permissions)")
        print(f"Org:      {org_name} (slug: {org_slug})")
        print(f"Workspace:{workspace_name} (slug: {workspace_slug})")


if __name__ == "__main__":
    asyncio.run(seed_super_owner())
