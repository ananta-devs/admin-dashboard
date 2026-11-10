"""Data access repository for analytical dashboards and visualization widgets."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.dashboard import Dashboard, DashboardWidget


class DashboardRepository:
    """Repository handling persistence for dashboards strictly scoped to an organization."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, dashboard: Dashboard) -> Dashboard:
        dashboard.widgets = []
        self.session.add(dashboard)
        await self.session.flush()
        return dashboard

    async def get_by_id(
        self, dashboard_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Dashboard | None:
        stmt = (
            select(Dashboard)
            .where(
                Dashboard.id == dashboard_id,
                Dashboard.organization_id == organization_id,
            )
            .options(selectinload(Dashboard.widgets))
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_by_id_for_update(
        self, dashboard_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Dashboard | None:
        """Lock and retrieve Dashboard row using SELECT ... FOR UPDATE strictly scoped to organization."""
        stmt = (
            select(Dashboard)
            .where(
                Dashboard.id == dashboard_id,
                Dashboard.organization_id == organization_id,
            )
            .with_for_update()
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_by_organization(
        self, organization_id: uuid.UUID, workspace_id: uuid.UUID | None = None
    ) -> Sequence[Dashboard]:
        stmt = (
            select(Dashboard)
            .where(Dashboard.organization_id == organization_id)
            .options(selectinload(Dashboard.widgets))
        )
        if workspace_id:
            stmt = stmt.where(Dashboard.workspace_id == workspace_id)
        stmt = stmt.order_by(Dashboard.created_at.desc())
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def update(self, dashboard: Dashboard) -> Dashboard:
        await self.session.flush()
        return dashboard

    async def delete(self, dashboard_id: uuid.UUID, organization_id: uuid.UUID) -> bool:
        stmt = (
            delete(Dashboard)
            .where(
                Dashboard.id == dashboard_id,
                Dashboard.organization_id == organization_id,
            )
            .returning(Dashboard.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.scalar_one_or_none() is not None


class DashboardWidgetRepository:
    """Repository handling persistence for dashboard visualization widgets."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, widget: DashboardWidget) -> DashboardWidget:
        self.session.add(widget)
        await self.session.flush()
        return widget

    async def get_by_id(
        self, widget_id: uuid.UUID, organization_id: uuid.UUID
    ) -> DashboardWidget | None:
        stmt = select(DashboardWidget).where(
            DashboardWidget.id == widget_id,
            DashboardWidget.organization_id == organization_id,
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_by_dashboard(
        self, dashboard_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Sequence[DashboardWidget]:
        stmt = (
            select(DashboardWidget)
            .where(
                DashboardWidget.dashboard_id == dashboard_id,
                DashboardWidget.organization_id == organization_id,
            )
            .order_by(DashboardWidget.sort_order.asc(), DashboardWidget.created_at.asc())
        )
        res = await self.session.execute(stmt)
        return res.scalars().all()

    async def update(self, widget: DashboardWidget) -> DashboardWidget:
        await self.session.flush()
        return widget

    async def delete(self, widget_id: uuid.UUID, organization_id: uuid.UUID) -> bool:
        stmt = (
            delete(DashboardWidget)
            .where(
                DashboardWidget.id == widget_id,
                DashboardWidget.organization_id == organization_id,
            )
            .returning(DashboardWidget.id)
        )
        res = await self.session.execute(stmt)
        await self.session.flush()
        return res.scalar_one_or_none() is not None
