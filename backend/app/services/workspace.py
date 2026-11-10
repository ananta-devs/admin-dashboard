import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
)
from app.core.logging import logger
from app.db.transaction import transaction
from app.models.workspace import Workspace
from app.repositories.organization import OrganizationRepository
from app.repositories.workspace import WorkspaceRepository
from app.schemas.workspace import (
    WorkspaceCreate,
    WorkspaceDetailRead,
    WorkspaceRead,
    WorkspaceUpdate,
)


class WorkspaceService:
    """Service managing workspaces strictly scoped within their parent organization."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.workspace_repo = WorkspaceRepository(session)
        self.org_repo = OrganizationRepository(session)

    async def create_workspace(
        self, organization_id: uuid.UUID, data: WorkspaceCreate
    ) -> WorkspaceRead:
        """Create a workspace ensuring organization exists and slug is unique within it."""
        org = await self.org_repo.get_by_id(organization_id)
        if not org:
            raise EntityNotFoundError("Organization", str(organization_id))

        existing = await self.workspace_repo.get_by_slug(data.slug, organization_id)
        if existing:
            raise EntityAlreadyExistsError("Workspace", "slug", data.slug)

        workspace = Workspace(
            organization_id=organization_id,
            name=data.name.strip(),
            slug=data.slug.strip().lower(),
        )

        async with transaction(self.session):
            await self.workspace_repo.create(workspace)

        return WorkspaceRead.model_validate(workspace)

    async def get_workspace(
        self, workspace_id: uuid.UUID, organization_id: uuid.UUID
    ) -> WorkspaceRead:
        """Retrieve workspace with strict tenant scoping.

        Security Note: To prevent resource existence probing (enumeration attacks),
        if a workspace exists in another tenant's organization, we log an internal
        security warning and raise EntityNotFoundError (yielding a 404 response to
        external callers), hiding resource existence.
        """
        workspace = await self.workspace_repo.get_by_id(workspace_id, organization_id)
        if not workspace:
            # Check if workspace exists globally to audit cross-tenant probing
            stmt = await self.session.get(Workspace, workspace_id)
            if stmt and stmt.organization_id != organization_id:
                logger.warning(
                    f"Security Alert: Cross-tenant access attempt: workspace '{workspace_id}' "
                    f"belongs to org '{stmt.organization_id}', requested by org '{organization_id}'"
                )
            raise EntityNotFoundError("Workspace", str(workspace_id))

        return WorkspaceRead.model_validate(workspace)

    async def get_workspace_with_stats(
        self, workspace_id: uuid.UUID, organization_id: uuid.UUID
    ) -> WorkspaceDetailRead:
        """Retrieve workspace alongside authoritative counts of active dependent resources."""
        workspace = await self.workspace_repo.get_by_id(workspace_id, organization_id)
        if not workspace:
            stmt = await self.session.get(Workspace, workspace_id)
            if stmt and stmt.organization_id != organization_id:
                logger.warning(
                    f"Security Alert: Cross-tenant access attempt: workspace '{workspace_id}' "
                    f"belongs to org '{stmt.organization_id}', requested by org '{organization_id}'"
                )
            raise EntityNotFoundError("Workspace", str(workspace_id))

        stats = await self.workspace_repo.count_resources(workspace_id, organization_id)
        ws_read = WorkspaceDetailRead.model_validate(workspace)
        ws_read.resource_counts = stats
        return ws_read

    async def get_workspace_by_slug(self, slug: str, organization_id: uuid.UUID) -> WorkspaceRead:
        """Retrieve workspace by slug within organization."""
        workspace = await self.workspace_repo.get_by_slug(slug, organization_id)
        if not workspace:
            raise EntityNotFoundError("Workspace", slug)
        return WorkspaceRead.model_validate(workspace)

    async def list_workspaces(
        self,
        organization_id: uuid.UUID,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[WorkspaceDetailRead]:
        """List all workspaces belonging to an organization with resource statistics."""
        workspaces = await self.workspace_repo.list_by_organization(
            organization_id, search=search, limit=limit, offset=offset
        )
        results: list[WorkspaceDetailRead] = []
        for w in workspaces:
            stats = await self.workspace_repo.count_resources(w.id, organization_id)
            ws_detail = WorkspaceDetailRead.model_validate(w)
            ws_detail.resource_counts = stats
            results.append(ws_detail)
        return results

    async def update_workspace(
        self, workspace_id: uuid.UUID, organization_id: uuid.UUID, data: WorkspaceUpdate
    ) -> WorkspaceRead:
        """Update workspace details ensuring slug uniqueness if changed."""
        workspace = await self.workspace_repo.get_by_id(workspace_id, organization_id)
        if not workspace:
            raise EntityNotFoundError("Workspace", str(workspace_id))

        if data.slug is not None and data.slug.strip().lower() != workspace.slug:
            # Prevent renaming the default workspace's slug
            if workspace.slug == "default":
                raise ConflictError("Cannot modify the slug of the default workspace.")
            new_slug = data.slug.strip().lower()
            existing = await self.workspace_repo.get_by_slug(new_slug, organization_id)
            if existing and existing.id != workspace.id:
                raise EntityAlreadyExistsError("Workspace", "slug", new_slug)

        async with transaction(self.session):
            await self.workspace_repo.update(
                workspace,
                name=data.name,
                slug=data.slug,
            )

        return WorkspaceRead.model_validate(workspace)

    async def delete_workspace(
        self, workspace_id: uuid.UUID, organization_id: uuid.UUID
    ) -> None:
        """Safely delete a workspace enforcing default protection and dependency checks."""
        workspace = await self.workspace_repo.get_by_id(workspace_id, organization_id)
        if not workspace:
            raise EntityNotFoundError("Workspace", str(workspace_id))

        # 1. Protect default workspace
        if workspace.slug == "default":
            raise ConflictError("The default organization workspace cannot be deleted.")

        # 2. Check dependent active resources within transaction
        async with transaction(self.session):
            stats = await self.workspace_repo.count_resources(workspace_id, organization_id)
            blocking = [
                f"{kind} ({count})" for kind, count in stats.items() if count > 0
            ]
            if blocking:
                raise ConflictError(
                    f"Cannot delete workspace '{workspace.name}': active dependent resources exist: "
                    + ", ".join(blocking)
                    + ". Please move or delete these resources before deleting the workspace."
                )

            await self.workspace_repo.delete(workspace)
            logger.info(
                f"Workspace '{workspace.id}' ({workspace.slug}) safely deleted from org '{organization_id}'."
            )
