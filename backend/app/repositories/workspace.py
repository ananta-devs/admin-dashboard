import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import AnalyticsJob
from app.models.dashboard import Dashboard
from app.models.report import ReportDefinition
from app.models.spreadsheet import Workbook
from app.models.workspace import Workspace


class WorkspaceRepository:
    """Persistence operations for Workspaces strictly bound to an organization scope."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, workspace: Workspace) -> Workspace:
        """Add workspace to session and flush."""
        self.session.add(workspace)
        await self.session.flush()
        return workspace

    async def get_by_id(
        self, workspace_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Workspace | None:
        """Retrieve workspace strictly within its organization scope."""
        stmt = select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.organization_id == organization_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str, organization_id: uuid.UUID) -> Workspace | None:
        """Retrieve workspace by slug within its organization scope."""
        stmt = select(Workspace).where(
            Workspace.slug == slug,
            Workspace.organization_id == organization_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_organization(
        self,
        organization_id: uuid.UUID,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Workspace]:
        """List workspaces belonging strictly to the specified organization."""
        stmt = select(Workspace).where(Workspace.organization_id == organization_id)
        if search and search.strip():
            term = f"%{search.strip().lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(Workspace.name).like(term),
                    func.lower(Workspace.slug).like(term),
                )
            )
        stmt = stmt.order_by(Workspace.created_at.desc()).limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_resources(
        self, workspace_id: uuid.UUID, organization_id: uuid.UUID
    ) -> dict[str, int]:
        """Count active dependent resources associated with this workspace within the organization."""
        # Dashboards
        dash_stmt = (
            select(func.count())
            .select_from(Dashboard)
            .where(
                Dashboard.workspace_id == workspace_id,
                Dashboard.organization_id == organization_id,
            )
        )
        dash_count = (await self.session.execute(dash_stmt)).scalar() or 0

        # Workbooks
        wb_stmt = (
            select(func.count())
            .select_from(Workbook)
            .where(
                Workbook.workspace_id == workspace_id,
                Workbook.organization_id == organization_id,
            )
        )
        wb_count = (await self.session.execute(wb_stmt)).scalar() or 0

        # Analytics Jobs
        job_stmt = (
            select(func.count())
            .select_from(AnalyticsJob)
            .where(
                AnalyticsJob.workspace_id == workspace_id,
                AnalyticsJob.organization_id == organization_id,
            )
        )
        job_count = (await self.session.execute(job_stmt)).scalar() or 0

        # Report Definitions
        rep_stmt = (
            select(func.count())
            .select_from(ReportDefinition)
            .where(
                ReportDefinition.workspace_id == workspace_id,
                ReportDefinition.organization_id == organization_id,
            )
        )
        rep_count = (await self.session.execute(rep_stmt)).scalar() or 0

        return {
            "dashboards": dash_count,
            "workbooks": wb_count,
            "analytics_jobs": job_count,
            "report_definitions": rep_count,
        }

    async def update(
        self, workspace: Workspace, name: str | None = None, slug: str | None = None
    ) -> Workspace:
        """Update workspace fields and flush."""
        if name is not None:
            workspace.name = name.strip()
        if slug is not None:
            workspace.slug = slug.strip().lower()
        await self.session.flush()
        return workspace

    async def delete(self, workspace: Workspace) -> None:
        """Delete workspace."""
        await self.session.delete(workspace)
        await self.session.flush()
