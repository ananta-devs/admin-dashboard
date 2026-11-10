import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import EntityNotFoundError, ForbiddenError, ValidationError
from app.models.membership import OrganizationMembership
from app.models.role import MembershipRole, OrganizationRole
from app.repositories.membership import MembershipRepository
from app.repositories.permission import PermissionRepository
from app.repositories.role import RoleRepository
from app.schemas.role import RoleCreateInput, RoleUpdateInput
from app.services.permission import PermissionService

# Role permission code mappings for system roles
SYSTEM_ROLES_PERMISSIONS: dict[str, list[str]] = {
    "owner": [
        "organization.read",
        "organization.update",
        "organization.settings.update",
        "members.read",
        "members.invite",
        "members.update",
        "members.roles.assign",
        "members.delete",
        "members.remove",
        "roles.read",
        "roles.create",
        "roles.update",
        "roles.delete",
        "workspaces.read",
        "workspaces.create",
        "workspaces.update",
        "workspaces.delete",
        "audit.read",
        "ownership.transfer",
        "spreadsheets.read",
        "spreadsheets.create",
        "spreadsheets.update",
        "spreadsheets.delete",
        "analytics.read",
        "analytics.create",
        "analytics.cancel",
        "analytics.export",
        "integrations.read",
        "integrations.manage",
        "integrations.sync",
        "dashboards.read",
        "dashboards.create",
        "dashboards.update",
        "dashboards.delete",
        "dashboards.share",
        "dashboards.export",
        "analytics.scripts.read",
        "analytics.scripts.create",
        "analytics.scripts.update",
        "analytics.scripts.execute",
        "analytics.scripts.delete",
        "analytics.scripts.manage",
        "analytics.scripts.manage_dependencies",
        "reports.read",
        "reports.create",
        "reports.update",
        "reports.delete",
        "reports.run",
        "reports.export",
    ],
    "admin": [
        "organization.read",
        "organization.update",
        "organization.settings.update",
        "members.read",
        "members.invite",
        "members.update",
        "members.roles.assign",
        "members.delete",
        "members.remove",
        "roles.read",
        "roles.create",
        "roles.update",
        "roles.delete",
        "workspaces.read",
        "workspaces.create",
        "workspaces.update",
        "workspaces.delete",
        "audit.read",
        "spreadsheets.read",
        "spreadsheets.create",
        "spreadsheets.update",
        "spreadsheets.delete",
        "analytics.read",
        "analytics.create",
        "analytics.cancel",
        "analytics.export",
        "integrations.read",
        "integrations.manage",
        "integrations.sync",
        "dashboards.read",
        "dashboards.create",
        "dashboards.update",
        "dashboards.delete",
        "dashboards.share",
        "dashboards.export",
        "analytics.scripts.read",
        "analytics.scripts.create",
        "analytics.scripts.update",
        "analytics.scripts.execute",
        "analytics.scripts.delete",
        "analytics.scripts.manage",
        "analytics.scripts.manage_dependencies",
        "reports.read",
        "reports.create",
        "reports.update",
        "reports.delete",
        "reports.run",
        "reports.export",
    ],
    "member": [
        "organization.read",
        "members.read",
        "roles.read",
        "workspaces.read",
        "workspaces.create",
        "workspaces.update",
        "spreadsheets.read",
        "spreadsheets.create",
        "spreadsheets.update",
        "analytics.read",
        "analytics.create",
        "analytics.cancel",
        "analytics.export",
        "integrations.read",
        "integrations.sync",
        "dashboards.read",
        "dashboards.create",
        "dashboards.update",
        "dashboards.share",
        "dashboards.export",
        "analytics.scripts.read",
        "analytics.scripts.create",
        "analytics.scripts.update",
        "analytics.scripts.execute",
        "reports.read",
        "reports.create",
        "reports.update",
        "reports.run",
        "reports.export",
    ],
    "viewer": [
        "organization.read",
        "members.read",
        "roles.read",
        "workspaces.read",
        "spreadsheets.read",
        "analytics.read",
        "dashboards.read",
        "dashboards.export",
        "analytics.scripts.read",
        "reports.read",
        "reports.export",
    ],
}

SYSTEM_ROLES_CONFIG = [
    {
        "slug": "owner",
        "name": "Owner",
        "description": "Full administrative control of the organization including ownership transfer.",
    },
    {
        "slug": "admin",
        "name": "Admin",
        "description": "Manage organization settings, members, custom roles, and workspaces.",
    },
    {
        "slug": "member",
        "name": "Member",
        "description": "Standard collaboration and workspace creation access.",
    },
    {
        "slug": "viewer",
        "name": "Viewer",
        "description": "Read-only access across the organization.",
    },
]


class RoleService:
    """Service governing role lifecycle, system role bootstrapping, and privilege delegation checks."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.role_repo = RoleRepository(session)
        self.perm_repo = PermissionRepository(session)
        self.membership_repo = MembershipRepository(session)
        self.permission_service = PermissionService(session)

    async def seed_default_roles_for_organization(
        self, organization_id: uuid.UUID
    ) -> dict[str, OrganizationRole]:
        """Bootstrap default system roles (owner, admin, member, viewer) for an organization."""
        all_perms = {p.code: p.id for p in await self.perm_repo.list_all()}
        roles: dict[str, OrganizationRole] = {}

        for cfg in SYSTEM_ROLES_CONFIG:
            slug = cfg["slug"]
            existing = await self.role_repo.get_by_slug(slug, organization_id)
            if existing:
                roles[slug] = existing
                continue

            role = OrganizationRole(
                organization_id=organization_id,
                name=cfg["name"],
                slug=slug,
                description=cfg["description"],
                is_system=True,
            )
            await self.role_repo.create(role)

            perm_codes = SYSTEM_ROLES_PERMISSIONS.get(slug, [])
            perm_ids = [all_perms[c] for c in perm_codes if c in all_perms]
            await self.role_repo.bootstrap_system_role_permissions(role.id, perm_ids)
            roles[slug] = role

        return roles

    async def list_roles(self, organization_id: uuid.UUID) -> Sequence[OrganizationRole]:
        """List all roles scoped to an organization."""
        return await self.role_repo.list_by_organization(organization_id)

    async def get_role(self, role_id: uuid.UUID, organization_id: uuid.UUID) -> OrganizationRole:
        """Get role by ID within organization."""
        role = await self.role_repo.get_by_id(role_id, organization_id)
        if not role:
            raise EntityNotFoundError("Role", str(role_id))
        return role

    async def create_custom_role(
        self,
        actor_membership_id: uuid.UUID,
        organization_id: uuid.UUID,
        data: RoleCreateInput,
    ) -> OrganizationRole:
        """Create a custom organization role with delegation checks."""
        # 1. Anti-escalation: Actor must possess all permissions being granted
        actor_perms = await self.permission_service.get_effective_permissions(
            actor_membership_id, organization_id=organization_id
        )
        unauthorized = set(data.permission_codes) - actor_perms
        if unauthorized:
            raise ForbiddenError(
                f"Cannot grant permissions you do not possess: {', '.join(sorted(unauthorized))}"
            )

        # 2. Check slug uniqueness within organization
        existing = await self.role_repo.get_by_slug(data.slug, organization_id)
        if existing:
            raise ValidationError(f"Role slug '{data.slug}' already exists in this organization")

        # 3. Resolve permissions
        permissions = await self.perm_repo.list_by_codes(data.permission_codes)
        found_codes = {p.code for p in permissions}
        missing = set(data.permission_codes) - found_codes
        if missing:
            raise ValidationError(f"Unknown permission codes: {', '.join(sorted(missing))}")

        # 4. Create role
        role = OrganizationRole(
            organization_id=organization_id,
            name=data.name.strip(),
            slug=data.slug.strip().lower(),
            description=data.description.strip() if data.description else None,
            is_system=False,
        )
        await self.role_repo.create(role)
        await self.role_repo.set_role_permissions(role.id, [p.id for p in permissions])

        # Return refreshed role
        refreshed = await self.role_repo.get_by_id(role.id, organization_id)
        assert refreshed is not None
        return refreshed

    async def update_custom_role(
        self,
        actor_membership_id: uuid.UUID,
        organization_id: uuid.UUID,
        role_id: uuid.UUID,
        data: RoleUpdateInput,
    ) -> OrganizationRole:
        """Update a custom organization role. System roles cannot be modified."""
        role = await self.get_role(role_id, organization_id)
        if role.is_system:
            raise ValidationError("System roles cannot be modified or weakened")

        if data.permission_codes is not None:
            actor_perms = await self.permission_service.get_effective_permissions(
                actor_membership_id, organization_id=organization_id
            )
            unauthorized = set(data.permission_codes) - actor_perms
            if unauthorized:
                raise ForbiddenError(
                    f"Cannot grant permissions you do not possess: {', '.join(sorted(unauthorized))}"
                )

            permissions = await self.perm_repo.list_by_codes(data.permission_codes)
            found_codes = {p.code for p in permissions}
            missing = set(data.permission_codes) - found_codes
            if missing:
                raise ValidationError(f"Unknown permission codes: {', '.join(sorted(missing))}")

            await self.role_repo.set_role_permissions(role.id, [p.id for p in permissions])

        if data.name is not None:
            role.name = data.name.strip()
        if data.description is not None:
            role.description = data.description.strip()

        await self.session.flush()
        refreshed = await self.role_repo.get_by_id(role.id, organization_id)
        assert refreshed is not None
        return refreshed

    async def delete_custom_role(self, organization_id: uuid.UUID, role_id: uuid.UUID) -> None:
        """Delete a custom organization role. System roles cannot be deleted."""
        role = await self.get_role(role_id, organization_id)
        if role.is_system:
            raise ValidationError("System roles cannot be deleted")

        # Check if role is currently assigned to any member
        from sqlalchemy import func, select

        stmt = select(func.count(MembershipRole.membership_id)).where(
            MembershipRole.role_id == role_id
        )
        res = await self.session.execute(stmt)
        count = res.scalar_one() or 0
        if count > 0:
            raise ValidationError(
                f"Cannot delete role '{role.name}' because it is assigned to {count} member(s). Reassign them first."
            )

        await self.role_repo.delete(role_id, organization_id)

    async def assign_roles_to_member(
        self,
        actor_membership_id: uuid.UUID,
        actor_user_id: uuid.UUID | None,
        organization_id: uuid.UUID,
        target_membership_id: uuid.UUID,
        role_ids: list[uuid.UUID],
    ) -> OrganizationMembership:
        """Assign roles to a member with privilege escalation checks and last-owner protection."""
        target_membership = await self.membership_repo.get_by_id(
            target_membership_id, organization_id=organization_id
        )
        if not target_membership:
            raise EntityNotFoundError("OrganizationMembership", str(target_membership_id))

        # 1. Fetch actor's effective permissions strictly scoped to organization
        actor_perms = await self.permission_service.get_effective_permissions(
            actor_membership_id, organization_id=organization_id
        )

        # 2. Acquire organization-level lock to serialize security-critical role mutations
        from sqlalchemy import select

        from app.models.organization import Organization

        await self.session.execute(
            select(Organization.id).where(Organization.id == organization_id).with_for_update()
        )

        # 3. Validate all target roles belong to this organization
        target_roles: list[OrganizationRole] = []
        for r_id in role_ids:
            r = await self.role_repo.get_by_id(r_id, organization_id)
            if not r:
                raise ValidationError(f"Role {r_id} does not exist in this organization")
            target_roles.append(r)

        # 4. Privilege escalation checks:
        # A user cannot assign roles to themselves to escalate permissions
        # Admins cannot grant permissions they do not possess
        for role in target_roles:
            role_perm_codes = {p.code for p in role.permissions}
            unauthorized = role_perm_codes - actor_perms
            if unauthorized:
                raise ForbiddenError(
                    f"Cannot assign role '{role.name}' because it grants permissions you lack: {', '.join(sorted(unauthorized))}"
                )

        # 5. Owner Protection under organization-level lock:
        # Re-fetch target membership under the organization lock to inspect fresh roles
        target_membership_locked = await self.membership_repo.get_by_id(
            target_membership_id, organization_id=organization_id
        )
        if not target_membership_locked:
            raise EntityNotFoundError("OrganizationMembership", str(target_membership_id))

        target_current_role_slugs = {r.slug for r in target_membership_locked.roles}
        new_role_slugs = {r.slug for r in target_roles}

        # Enforce that generic role assignment NEVER grants the Owner role
        if "owner" in new_role_slugs:
            raise ValidationError(
                "The Owner role cannot be granted via generic role assignment; use the dedicated ownership service."
            )

        # Enforce that generic role assignment NEVER modifies or strips Owner status
        if "owner" in target_current_role_slugs:
            raise ValidationError(
                "Owner memberships cannot be modified or demoted via generic role assignment; use the dedicated ownership service."
            )

        # 6. Set membership roles with explicit organization_id
        await self.role_repo.set_membership_roles(
            target_membership_id,
            role_ids,
            organization_id=organization_id,
            assigned_by_user_id=actor_user_id,
        )

        refreshed = await self.membership_repo.get_by_id(
            target_membership_id, organization_id=organization_id
        )
        assert refreshed is not None
        return refreshed
