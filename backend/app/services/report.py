"""Domain service orchestrating report definitions, section layout, and on-demand execution."""

from __future__ import annotations

import copy
import logging
import uuid
from collections.abc import Sequence

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    CrossTenantAccessError,
    EntityNotFoundError,
    ForbiddenError,
    ValidationError,
)
from app.db.base import utc_now
from app.db.transaction import transaction
from app.models.report import (
    ReportDefinition,
    ReportOutboxEvent,
    ReportRun,
    ReportRunStatus,
    ReportSection,
)
from app.repositories.dashboard import DashboardRepository, DashboardWidgetRepository
from app.repositories.membership import MembershipRepository
from app.repositories.report import (
    ReportDefinitionRepository,
    ReportOutboxRepository,
    ReportRunRepository,
    ReportSectionRepository,
)
from app.repositories.workspace import WorkspaceRepository
from app.schemas.report import (
    ReportDefinitionCreateInput,
    ReportDefinitionUpdateInput,
    ReportRunTriggerInput,
    ReportSectionCreateInput,
    ReportSectionUpdateInput,
)
from app.services.permission import PermissionService

logger = logging.getLogger("vibe.reporting.service")


class ReportService:
    """Core domain service governing multi-section BI reports and asynchronous snapshot execution."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.def_repo = ReportDefinitionRepository(session)
        self.section_repo = ReportSectionRepository(session)
        self.run_repo = ReportRunRepository(session)
        self.outbox_repo = ReportOutboxRepository(session)
        self.workspace_repo = WorkspaceRepository(session)
        self.dashboard_repo = DashboardRepository(session)
        self.widget_repo = DashboardWidgetRepository(session)
        self.membership_repo = MembershipRepository(session)
        self.permission_service = PermissionService(session)

    # =========================================================================
    # Report Definitions
    # =========================================================================

    async def create_report_definition(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None,
        data: ReportDefinitionCreateInput,
        membership_id: uuid.UUID | None = None,
    ) -> ReportDefinition:
        """Create a new report definition strictly scoped to organization and workspace."""
        # Validate authorization: requires 'reports.create'
        if membership_id or user_id:
            resolved_mem_id = membership_id
            if not resolved_mem_id and user_id:
                mem = await self.membership_repo.get(organization_id=organization_id, user_id=user_id)
                if mem:
                    resolved_mem_id = mem.id
            if resolved_mem_id:
                has_perm = await self.permission_service.check_membership_has_permission(
                    resolved_mem_id, "reports.create", organization_id=organization_id
                )
                if not has_perm:
                    raise ForbiddenError("Permission denied: required 'reports.create'")

        # Validate workspace ownership
        workspace = await self.workspace_repo.get_by_id(data.workspace_id, organization_id)
        if not workspace:
            raise EntityNotFoundError("Workspace", str(data.workspace_id))

        # Validate dashboard ownership if referenced
        if data.dashboard_id:
            dashboard = await self.dashboard_repo.get_by_id(data.dashboard_id, organization_id)
            if not dashboard:
                raise EntityNotFoundError("Dashboard", str(data.dashboard_id))

        async with transaction(self.session):
            definition = ReportDefinition(
                organization_id=organization_id,
                workspace_id=data.workspace_id,
                dashboard_id=data.dashboard_id,
                title=data.title.strip(),
                description=data.description.strip() if data.description else None,
                format=data.format,
                page_orientation=data.page_orientation,
                page_size=data.page_size,
                header_text=data.header_text,
                footer_text=data.footer_text,
                include_table_of_contents=data.include_table_of_contents,
                include_cover_page=data.include_cover_page,
                theme=data.theme,
                created_by_user_id=user_id,
            )
            created = await self.def_repo.create(definition)
        return created

    async def get_report_definition(
        self, id: uuid.UUID, organization_id: uuid.UUID
    ) -> ReportDefinition:
        definition = await self.def_repo.get_by_id(id, organization_id)
        if not definition:
            from sqlalchemy import select
            global_stmt = select(ReportDefinition).where(ReportDefinition.id == id)
            other_def = (await self.session.execute(global_stmt)).scalar_one_or_none()
            if other_def and other_def.organization_id != organization_id:
                raise CrossTenantAccessError("ReportDefinition", str(id), str(organization_id))
            raise EntityNotFoundError("ReportDefinition", str(id))
        return definition

    async def get_report_definition_with_sections(
        self, id: uuid.UUID, organization_id: uuid.UUID, include_archived: bool = False
    ) -> ReportDefinition:
        definition = await self.def_repo.get_by_id_with_sections(
            id, organization_id, include_archived=include_archived
        )
        if not definition:
            raise EntityNotFoundError("ReportDefinition", str(id))
        return definition

    async def list_report_definitions(
        self,
        organization_id: uuid.UUID,
        workspace_id: uuid.UUID | None = None,
        include_archived: bool = False,
    ) -> Sequence[ReportDefinition]:
        if workspace_id:
            await self.workspace_repo.get_by_id(workspace_id, organization_id)
            return await self.def_repo.list_by_workspace(
                workspace_id, organization_id, include_archived=include_archived
            )
        return await self.def_repo.list_by_organization(
            organization_id, include_archived=include_archived
        )

    async def update_report_definition(
        self,
        id: uuid.UUID,
        organization_id: uuid.UUID,
        data: ReportDefinitionUpdateInput,
    ) -> ReportDefinition:
        definition = await self.get_report_definition(id, organization_id)

        if data.dashboard_id is not None:
            dash = await self.dashboard_repo.get_by_id(data.dashboard_id, organization_id)
            if not dash:
                raise EntityNotFoundError("Dashboard", str(data.dashboard_id))
            definition.dashboard_id = data.dashboard_id

        if data.title is not None:
            definition.title = data.title.strip()
        if data.description is not None:
            definition.description = data.description.strip() if data.description else None
        if data.format is not None:
            definition.format = data.format
        if data.page_orientation is not None:
            definition.page_orientation = data.page_orientation
        if data.page_size is not None:
            definition.page_size = data.page_size
        if data.header_text is not None:
            definition.header_text = data.header_text
        if data.footer_text is not None:
            definition.footer_text = data.footer_text
        if data.include_table_of_contents is not None:
            definition.include_table_of_contents = data.include_table_of_contents
        if data.include_cover_page is not None:
            definition.include_cover_page = data.include_cover_page
        if data.theme is not None:
            definition.theme = data.theme

        async with transaction(self.session):
            updated = await self.def_repo.update(definition)
        return updated

    async def archive_report_definition(
        self, id: uuid.UUID, organization_id: uuid.UUID
    ) -> ReportDefinition:
        async with transaction(self.session):
            archived = await self.def_repo.archive(id, organization_id)
            if not archived:
                raise EntityNotFoundError("ReportDefinition", str(id))
        return archived

    async def unarchive_report_definition(
        self, id: uuid.UUID, organization_id: uuid.UUID
    ) -> ReportDefinition:
        async with transaction(self.session):
            unarchived = await self.def_repo.unarchive(id, organization_id)
            if not unarchived:
                raise EntityNotFoundError("ReportDefinition", str(id))
        return unarchived

    # =========================================================================
    # Report Sections
    # =========================================================================

    async def add_section(
        self,
        report_definition_id: uuid.UUID,
        organization_id: uuid.UUID,
        data: ReportSectionCreateInput,
    ) -> ReportSection:
        """Add an ordered content section to an existing report definition."""
        definition = await self.get_report_definition(report_definition_id, organization_id)

        # Check section capacity limit (audited limit: 25)
        existing_sections = await self.section_repo.list_by_definition(
            report_definition_id, organization_id
        )
        if len(existing_sections) >= settings.REPORT_MAX_SECTIONS:
            raise ValidationError(
                f"Report definition already has maximum allowable sections ({settings.REPORT_MAX_SECTIONS})."
            )

        # Validate widget ownership if referenced
        if data.widget_id:
            widget = await self.widget_repo.get_by_id(data.widget_id, organization_id)
            if not widget:
                raise EntityNotFoundError("DashboardWidget", str(data.widget_id))

        sort_order = data.sort_order
        if sort_order == 0 and existing_sections:
            sort_order = max(s.sort_order for s in existing_sections) + 1

        async with transaction(self.session):
            section = ReportSection(
                report_definition_id=definition.id,
                organization_id=organization_id,
                widget_id=data.widget_id,
                section_type=data.section_type,
                title=data.title,
                description=data.description,
                custom_content=data.custom_content,
                override_query_config=data.override_query_config or {},
                sort_order=sort_order,
                page_break_after=data.page_break_after,
            )
            created = await self.section_repo.create(section)
        return created

    async def update_section(
        self,
        section_id: uuid.UUID,
        organization_id: uuid.UUID,
        data: ReportSectionUpdateInput,
    ) -> ReportSection:
        section = await self.section_repo.get_by_id(section_id, organization_id)
        if not section:
            raise EntityNotFoundError("ReportSection", str(section_id))

        if data.widget_id is not None:
            widget = await self.widget_repo.get_by_id(data.widget_id, organization_id)
            if not widget:
                raise EntityNotFoundError("DashboardWidget", str(data.widget_id))
            section.widget_id = data.widget_id

        if data.title is not None:
            section.title = data.title
        if data.description is not None:
            section.description = data.description
        if data.custom_content is not None:
            section.custom_content = data.custom_content
        if data.override_query_config is not None:
            section.override_query_config = data.override_query_config
        if data.sort_order is not None:
            section.sort_order = data.sort_order
        if data.page_break_after is not None:
            section.page_break_after = data.page_break_after

        async with transaction(self.session):
            updated = await self.section_repo.update(section)
        return updated

    async def reorder_sections(
        self,
        report_definition_id: uuid.UUID,
        organization_id: uuid.UUID,
        ordered_section_ids: list[uuid.UUID],
    ) -> list[ReportSection]:
        await self.get_report_definition(report_definition_id, organization_id)
        async with transaction(self.session):
            reordered = await self.section_repo.reorder_sections(
                report_definition_id, organization_id, ordered_section_ids
            )
        return reordered

    async def delete_section(
        self, section_id: uuid.UUID, organization_id: uuid.UUID
    ) -> bool:
        async with transaction(self.session):
            deleted = await self.section_repo.delete(section_id, organization_id)
            if not deleted:
                raise EntityNotFoundError("ReportSection", str(section_id))
        return True

    # =========================================================================
    # Report Runs & Outbox Dispatch Triggering
    # =========================================================================

    async def trigger_report_run(
        self,
        report_definition_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None,
        input_data: ReportRunTriggerInput,
        membership_id: uuid.UUID | None = None,
    ) -> ReportRun:
        """Trigger an on-demand report run with mandatory idempotency key and transactional outbox event."""
        # 1. Enforce non-empty idempotency key
        key = input_data.idempotency_key.strip()
        if not key:
            raise ValidationError("idempotency_key is required and must not be blank.")

        # 2. Validate report definition belongs to authenticated organization
        report_def = await self.get_report_definition(report_definition_id, organization_id)

        # 3. Validate authorization: triggering execution strictly requires 'reports.run'
        if membership_id or user_id:
            resolved_mem_id = membership_id
            if not resolved_mem_id and user_id:
                mem = await self.membership_repo.get(organization_id=organization_id, user_id=user_id)
                if mem:
                    resolved_mem_id = mem.id
            if resolved_mem_id:
                has_perm = await self.permission_service.check_membership_has_permission(
                    resolved_mem_id, "reports.run", organization_id=organization_id
                )
                if not has_perm:
                    raise ForbiddenError("Permission denied: required 'reports.run'")

        # 4. Check for existing run by idempotency key within tenant
        existing = await self.run_repo.get_by_idempotency_key(organization_id, key)
        if existing:
            return existing

        # 5. Atomic run creation + outbox event commit
        try:
            async with transaction(self.session):
                run = ReportRun(
                    organization_id=organization_id,
                    report_definition_id=report_def.id,
                    workspace_id=report_def.workspace_id,
                    report_title=report_def.title,
                    trigger_type=input_data.trigger_type,
                    status=ReportRunStatus.QUEUED,
                    idempotency_key=key,
                    created_by_user_id=user_id,
                    queued_at=utc_now(),
                )
                created_run = await self.run_repo.create(run)

                outbox_event = ReportOutboxEvent(
                    run_id=created_run.id,
                    organization_id=organization_id,
                    event_type="report.run.dispatched",
                    payload={"run_id": str(created_run.id), "organization_id": str(organization_id)},
                )
                await self.outbox_repo.create_event(outbox_event)

            return created_run
        except IntegrityError:
            # Concurrent duplicate request with same idempotency key raced and committed first
            await self.session.rollback()
            existing_run = await self.run_repo.get_by_idempotency_key(organization_id, key)
            if existing_run:
                return existing_run
            raise

    async def get_report_run(
        self, run_id: uuid.UUID, organization_id: uuid.UUID
    ) -> ReportRun:
        run = await self.run_repo.get_by_id(run_id, organization_id)
        if not run:
            raise EntityNotFoundError("ReportRun", str(run_id))
        return run

    async def list_report_runs(
        self,
        report_definition_id: uuid.UUID,
        organization_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[ReportRun], int]:
        await self.get_report_definition(report_definition_id, organization_id)
        return await self.run_repo.list_by_definition_paginated(
            report_definition_id, organization_id, limit=limit, offset=offset
        )

