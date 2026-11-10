"""Authoritative PostgreSQL-backed repositories for Phase 9 Gate 3 reporting subsystem."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.base import utc_now
from app.models.report import (
    ReportDefinition,
    ReportOutboxEvent,
    ReportRun,
    ReportRunStatus,
    ReportSection,
)

logger = logging.getLogger("vibe.reporting.repository")


class ReportDefinitionRepository:
    """Repository for managing multi-section report definitions and templates."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, definition: ReportDefinition) -> ReportDefinition:
        self.session.add(definition)
        await self.session.flush()
        return definition

    async def get_by_id(self, id: uuid.UUID, organization_id: uuid.UUID) -> ReportDefinition | None:
        stmt = (
            select(ReportDefinition)
            .options(selectinload(ReportDefinition.sections))
            .where(
                ReportDefinition.id == id,
                ReportDefinition.organization_id == organization_id,
            )
        )
        res = await self.session.execute(stmt)
        report_def = res.scalar_one_or_none()
        if report_def and report_def.sections:
            report_def.sections.sort(key=lambda s: s.sort_order)
        return report_def

    async def get_by_id_with_sections(
        self, id: uuid.UUID, organization_id: uuid.UUID, include_archived: bool = False
    ) -> ReportDefinition | None:
        stmt = (
            select(ReportDefinition)
            .options(selectinload(ReportDefinition.sections))
            .where(
                ReportDefinition.id == id,
                ReportDefinition.organization_id == organization_id,
            )
        )
        if not include_archived:
            stmt = stmt.where(ReportDefinition.is_archived.is_(False))

        res = await self.session.execute(stmt)
        report_def = res.scalar_one_or_none()
        if report_def and report_def.sections:
            report_def.sections.sort(key=lambda s: s.sort_order)
        return report_def

    async def list_by_workspace(
        self,
        workspace_id: uuid.UUID,
        organization_id: uuid.UUID,
        include_archived: bool = False,
    ) -> Sequence[ReportDefinition]:
        stmt = (
            select(ReportDefinition)
            .options(selectinload(ReportDefinition.sections))
            .where(
                ReportDefinition.workspace_id == workspace_id,
                ReportDefinition.organization_id == organization_id,
            )
        )
        if not include_archived:
            stmt = stmt.where(ReportDefinition.is_archived.is_(False))
        stmt = stmt.order_by(ReportDefinition.created_at.desc())
        return (await self.session.execute(stmt)).scalars().all()

    async def list_by_organization(
        self,
        organization_id: uuid.UUID,
        include_archived: bool = False,
    ) -> Sequence[ReportDefinition]:
        stmt = (
            select(ReportDefinition)
            .options(selectinload(ReportDefinition.sections))
            .where(
                ReportDefinition.organization_id == organization_id,
            )
        )
        if not include_archived:
            stmt = stmt.where(ReportDefinition.is_archived.is_(False))
        stmt = stmt.order_by(ReportDefinition.created_at.desc())
        return (await self.session.execute(stmt)).scalars().all()

    async def update(self, definition: ReportDefinition) -> ReportDefinition:
        definition.updated_at = utc_now()
        await self.session.flush()
        return definition

    async def archive(self, id: uuid.UUID, organization_id: uuid.UUID) -> ReportDefinition | None:
        definition = await self.get_by_id(id, organization_id)
        if not definition:
            return None
        now = utc_now()
        definition.is_archived = True
        definition.archived_at = now
        definition.updated_at = now
        await self.session.flush()
        return definition

    async def unarchive(self, id: uuid.UUID, organization_id: uuid.UUID) -> ReportDefinition | None:
        definition = await self.get_by_id(id, organization_id)
        if not definition:
            return None
        definition.is_archived = False
        definition.archived_at = None
        definition.updated_at = utc_now()
        await self.session.flush()
        return definition

    async def delete(self, id: uuid.UUID, organization_id: uuid.UUID) -> bool:
        definition = await self.get_by_id(id, organization_id)
        if not definition:
            return False
        await self.session.delete(definition)
        await self.session.flush()
        return True


class ReportSectionRepository:
    """Repository for managing individual ordered blocks within a report definition."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, section: ReportSection) -> ReportSection:
        self.session.add(section)
        await self.session.flush()
        return section

    async def get_by_id(self, id: uuid.UUID, organization_id: uuid.UUID) -> ReportSection | None:
        stmt = select(ReportSection).where(
            ReportSection.id == id,
            ReportSection.organization_id == organization_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_definition(
        self, report_definition_id: uuid.UUID, organization_id: uuid.UUID
    ) -> Sequence[ReportSection]:
        stmt = (
            select(ReportSection)
            .where(
                ReportSection.report_definition_id == report_definition_id,
                ReportSection.organization_id == organization_id,
            )
            .order_by(ReportSection.sort_order.asc(), ReportSection.created_at.asc())
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def update(self, section: ReportSection) -> ReportSection:
        section.updated_at = utc_now()
        await self.session.flush()
        return section

    async def reorder_sections(
        self,
        report_definition_id: uuid.UUID,
        organization_id: uuid.UUID,
        ordered_section_ids: list[uuid.UUID],
    ) -> list[ReportSection]:
        """Safely reorder sections without unique constraint collisions using two-phase indexing."""
        sections = list(await self.list_by_definition(report_definition_id, organization_id))
        section_map = {s.id: s for s in sections}

        # Validate that all requested IDs belong to this report definition
        if set(ordered_section_ids) != set(section_map.keys()):
            raise ValueError("ordered_section_ids must match exact set of existing section IDs")

        # Phase 1: Set temporary negative sort_order to break duplicate collisions
        for idx, sid in enumerate(ordered_section_ids):
            sec = section_map[sid]
            sec.sort_order = -(idx + 1)
        await self.session.flush()

        # Phase 2: Set target positive sort_order (0-indexed)
        result: list[ReportSection] = []
        for idx, sid in enumerate(ordered_section_ids):
            sec = section_map[sid]
            sec.sort_order = idx
            sec.updated_at = utc_now()
            result.append(sec)
        await self.session.flush()
        return result

    async def delete(self, id: uuid.UUID, organization_id: uuid.UUID) -> bool:
        section = await self.get_by_id(id, organization_id)
        if not section:
            return False
        await self.session.delete(section)
        await self.session.flush()
        return True


class ReportRunRepository:
    """Repository for on-demand report execution runs, atomic claims, and status transitions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, run: ReportRun) -> ReportRun:
        self.session.add(run)
        await self.session.flush()
        return run

    async def get_by_id(self, id: uuid.UUID, organization_id: uuid.UUID) -> ReportRun | None:
        stmt = select(ReportRun).where(
            ReportRun.id == id,
            ReportRun.organization_id == organization_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_idempotency_key(
        self, organization_id: uuid.UUID, idempotency_key: str
    ) -> ReportRun | None:
        stmt = select(ReportRun).where(
            ReportRun.organization_id == organization_id,
            ReportRun.idempotency_key == idempotency_key,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_definition_paginated(
        self,
        report_definition_id: uuid.UUID,
        organization_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[ReportRun], int]:
        count_stmt = select(func.count(ReportRun.id)).where(
            ReportRun.report_definition_id == report_definition_id,
            ReportRun.organization_id == organization_id,
        )
        total = (await self.session.execute(count_stmt)).scalar_one() or 0

        stmt = (
            select(ReportRun)
            .where(
                ReportRun.report_definition_id == report_definition_id,
                ReportRun.organization_id == organization_id,
            )
            .order_by(ReportRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        runs = (await self.session.execute(stmt)).scalars().all()
        return runs, total

    async def list_by_workspace_paginated(
        self,
        workspace_id: uuid.UUID,
        organization_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Sequence[ReportRun], int]:
        count_stmt = select(func.count(ReportRun.id)).where(
            ReportRun.workspace_id == workspace_id,
            ReportRun.organization_id == organization_id,
        )
        total = (await self.session.execute(count_stmt)).scalar_one() or 0

        stmt = (
            select(ReportRun)
            .where(
                ReportRun.workspace_id == workspace_id,
                ReportRun.organization_id == organization_id,
            )
            .order_by(ReportRun.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        runs = (await self.session.execute(stmt)).scalars().all()
        return runs, total

    async def claim_run_for_execution(
        self,
        run_id: uuid.UUID,
        worker_id: str,
        lease_seconds: int = 60,
    ) -> tuple[bool, ReportRun | None]:
        """Atomically claim a queued or expired running run for execution."""
        now = utc_now()
        stmt = select(ReportRun).where(ReportRun.id == run_id).with_for_update()
        run = (await self.session.execute(stmt)).scalar_one_or_none()
        if not run:
            return False, None

        # Rejection conditions:
        # 1. Attempt count exhausted
        if run.attempt_count >= run.max_attempts:
            return False, None

        # 2. Status is neither queued nor an expired running lease
        is_queued = run.status == ReportRunStatus.QUEUED
        is_expired_running = (
            run.status == ReportRunStatus.RUNNING
            and run.lease_expires_at is not None
            and run.lease_expires_at < now
        )

        if not (is_queued or is_expired_running):
            return False, None

        new_claim_token = uuid.uuid4()
        new_claim_generation = run.claim_generation + 1
        new_attempt_count = run.attempt_count + 1
        lease_expires = now + timedelta(seconds=lease_seconds)

        run.status = ReportRunStatus.RUNNING
        run.claim_token = new_claim_token
        run.claim_generation = new_claim_generation
        run.attempt_count = new_attempt_count
        run.lease_expires_at = lease_expires
        if not run.started_at:
            run.started_at = now
        run.updated_at = now

        await self.session.flush()
        return True, run

    async def extend_lease(
        self,
        run_id: uuid.UUID,
        claim_token: uuid.UUID,
        claim_generation: int,
        extend_seconds: int = 60,
    ) -> bool:
        now = utc_now()
        new_lease = now + timedelta(seconds=extend_seconds)
        stmt = (
            update(ReportRun)
            .where(
                ReportRun.id == run_id,
                ReportRun.claim_token == claim_token,
                ReportRun.claim_generation == claim_generation,
                ReportRun.status == ReportRunStatus.RUNNING,
            )
            .values(
                lease_expires_at=new_lease,
                updated_at=now,
            )
        )
        res = await self.session.execute(stmt)
        return getattr(res, "rowcount", 0) > 0

    async def finalize_run_success(
        self,
        run_id: uuid.UUID,
        claim_token: uuid.UUID,
        claim_generation: int,
        snapshot: dict[str, Any],
        data_revision_ref: dict[str, Any],
        execution_duration_ms: int,
        artifact_path: str | None = None,
        file_size_bytes: int | None = None,
        checksum_sha256: str | None = None,
        pdf_artifact_path: str | None = None,
        pdf_checksum_sha256: str | None = None,
        pdf_file_size_bytes: int | None = None,
        pdf_page_count: int | None = None,
        pdf_rendered_at: datetime | None = None,
    ) -> bool:
        """Atomically finalize a successful run guarded by claim generation and token fencing."""
        now = utc_now()
        stmt = (
            update(ReportRun)
            .where(
                ReportRun.id == run_id,
                ReportRun.claim_token == claim_token,
                ReportRun.claim_generation == claim_generation,
                ReportRun.status == ReportRunStatus.RUNNING,
            )
            .values(
                status=ReportRunStatus.SUCCEEDED,
                report_snapshot=snapshot,
                data_revision_ref=data_revision_ref,
                artifact_path=artifact_path,
                file_size_bytes=file_size_bytes,
                checksum_sha256=checksum_sha256,
                pdf_artifact_path=pdf_artifact_path,
                pdf_checksum_sha256=pdf_checksum_sha256,
                pdf_file_size_bytes=pdf_file_size_bytes,
                pdf_page_count=pdf_page_count,
                pdf_rendered_at=pdf_rendered_at,
                execution_duration_ms=execution_duration_ms,
                completed_at=now,
                lease_expires_at=None,
                updated_at=now,
            )
        )
        res = await self.session.execute(stmt)
        return getattr(res, "rowcount", 0) > 0

    async def finalize_run_failure(
        self,
        run_id: uuid.UUID,
        claim_token: uuid.UUID,
        claim_generation: int,
        error_code: str,
        error_message: str,
        execution_duration_ms: int | None = None,
        is_retryable: bool = False,
    ) -> bool:
        """Atomically finalize a failed or retrying run guarded by claim generation fencing."""
        now = utc_now()
        stmt = (
            select(ReportRun)
            .where(
                ReportRun.id == run_id,
                ReportRun.claim_token == claim_token,
                ReportRun.claim_generation == claim_generation,
                ReportRun.status == ReportRunStatus.RUNNING,
            )
            .with_for_update()
        )
        run = (await self.session.execute(stmt)).scalar_one_or_none()
        if not run:
            return False

        if is_retryable and run.attempt_count < run.max_attempts:
            run.status = ReportRunStatus.QUEUED
            run.lease_expires_at = None
        else:
            run.status = ReportRunStatus.FAILED
            run.completed_at = now
            run.lease_expires_at = None

        run.error_code = error_code
        run.error_message = error_message
        if execution_duration_ms is not None:
            run.execution_duration_ms = execution_duration_ms
        run.updated_at = now

        await self.session.flush()
        return True

    async def find_expired_leases(self, cutoff: datetime) -> Sequence[ReportRun]:
        stmt = select(ReportRun).where(
            ReportRun.status == ReportRunStatus.RUNNING,
            ReportRun.lease_expires_at.isnot(None),
            ReportRun.lease_expires_at < cutoff,
        )
        return (await self.session.execute(stmt)).scalars().all()


class ReportOutboxRepository:
    """Repository for durable transactional outbox events for asynchronous report dispatch."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_event(self, event: ReportOutboxEvent) -> ReportOutboxEvent:
        self.session.add(event)
        await self.session.flush()
        return event

    async def fetch_and_lock_pending(
        self,
        batch_size: int = 10,
        locked_by: str = "reporting-dispatcher",
        lock_timeout_seconds: int = 60,
    ) -> list[ReportOutboxEvent]:
        """Fetch and lock pending outbox events using FOR UPDATE SKIP LOCKED."""
        now = datetime.now(UTC)
        lock_cutoff = now - timedelta(seconds=lock_timeout_seconds)

        stmt = (
            select(ReportOutboxEvent)
            .where(
                ReportOutboxEvent.published_at.is_(None),
                or_(
                    ReportOutboxEvent.locked_at.is_(None),
                    ReportOutboxEvent.locked_at < lock_cutoff,
                ),
            )
            .order_by(ReportOutboxEvent.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        events = list((await self.session.execute(stmt)).scalars().all())

        if events:
            for ev in events:
                ev.locked_at = now
                ev.locked_by = locked_by
            await self.session.flush()

        return events

    async def mark_published(self, event_id: uuid.UUID) -> None:
        now = datetime.now(UTC)
        stmt = (
            update(ReportOutboxEvent)
            .where(ReportOutboxEvent.id == event_id)
            .values(
                published_at=now,
                locked_at=None,
                locked_by=None,
            )
        )
        await self.session.execute(stmt)

    async def record_publish_error(self, event_id: uuid.UUID, error: str) -> None:
        stmt = (
            update(ReportOutboxEvent)
            .where(ReportOutboxEvent.id == event_id)
            .values(
                attempts=ReportOutboxEvent.attempts + 1,
                last_error=error,
                locked_at=None,
                locked_by=None,
            )
        )
        await self.session.execute(stmt)

    async def release_expired_locks(self, timeout_seconds: int = 60) -> int:
        now = datetime.now(UTC)
        cutoff = now - timedelta(seconds=timeout_seconds)
        stmt = (
            update(ReportOutboxEvent)
            .where(
                ReportOutboxEvent.published_at.is_(None),
                ReportOutboxEvent.locked_at.isnot(None),
                ReportOutboxEvent.locked_at < cutoff,
            )
            .values(
                locked_at=None,
                locked_by=None,
            )
        )
        res = await self.session.execute(stmt)
        return getattr(res, "rowcount", 0)

    async def get_pending_count(self) -> int:
        stmt = select(func.count(ReportOutboxEvent.id)).where(
            ReportOutboxEvent.published_at.is_(None)
        )
        return (await self.session.execute(stmt)).scalar_one() or 0
