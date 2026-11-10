"""Repository layer for analytics jobs, transactional outbox, and results."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import (
    AnalyticsJob,
    AnalyticsJobStatus,
    AnalyticsOutboxEvent,
    AnalyticsResult,
)


class AnalyticsJobRepository:
    """PostgreSQL repository for AnalyticsJob lifecycle."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, job: AnalyticsJob) -> AnalyticsJob:
        self.session.add(job)
        await self.session.flush()
        return job

    async def get_by_id(self, job_id: uuid.UUID) -> AnalyticsJob | None:
        stmt = select(AnalyticsJob).where(AnalyticsJob.id == job_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_id_for_update(self, job_id: uuid.UUID) -> AnalyticsJob | None:
        """Lock and retrieve AnalyticsJob row using SELECT ... FOR UPDATE."""
        stmt = select(AnalyticsJob).where(AnalyticsJob.id == job_id).with_for_update()
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_id_scoped(
        self, job_id: uuid.UUID, organization_id: uuid.UUID
    ) -> AnalyticsJob | None:
        stmt = select(AnalyticsJob).where(
            AnalyticsJob.id == job_id,
            AnalyticsJob.organization_id == organization_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_idempotency_key(
        self, organization_id: uuid.UUID, idempotency_key: str
    ) -> AnalyticsJob | None:
        stmt = select(AnalyticsJob).where(
            AnalyticsJob.organization_id == organization_id,
            AnalyticsJob.idempotency_key == idempotency_key,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_for_organization(
        self,
        organization_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> tuple[list[AnalyticsJob], int]:
        query = select(AnalyticsJob).where(AnalyticsJob.organization_id == organization_id)
        if status:
            query = query.where(AnalyticsJob.status == status)

        count_stmt = select(func.count()).select_from(query.subquery())
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = query.order_by(AnalyticsJob.created_at.desc()).limit(limit).offset(offset)
        items = (await self.session.execute(stmt)).scalars().all()
        return list(items), total

    async def count_active_jobs_for_org(self, organization_id: uuid.UUID) -> int:
        stmt = select(func.count(AnalyticsJob.id)).where(
            AnalyticsJob.organization_id == organization_id,
            AnalyticsJob.status.in_(
                [
                    AnalyticsJobStatus.QUEUED,
                    AnalyticsJobStatus.RUNNING,
                    AnalyticsJobStatus.RETRYING,
                ]
            ),
        )
        return (await self.session.execute(stmt)).scalar() or 0

    async def count_active_jobs_for_user(
        self, organization_id: uuid.UUID, user_id: uuid.UUID
    ) -> int:
        stmt = select(func.count(AnalyticsJob.id)).where(
            AnalyticsJob.organization_id == organization_id,
            AnalyticsJob.requested_by_user_id == user_id,
            AnalyticsJob.status.in_(
                [
                    AnalyticsJobStatus.QUEUED,
                    AnalyticsJobStatus.RUNNING,
                    AnalyticsJobStatus.RETRYING,
                ]
            ),
        )
        return (await self.session.execute(stmt)).scalar() or 0

    async def claim_job_atomically(
        self, job_id: uuid.UUID, worker_id: str, lease_duration_seconds: int = 60
    ) -> tuple[bool, uuid.UUID | None]:
        """Atomically claim a queued or retrying job using a conditional update with a unique fencing token."""
        now = datetime.now(UTC)
        lease_expiry = now + timedelta(seconds=lease_duration_seconds)
        claim_token = uuid.uuid4()

        stmt = (
            update(AnalyticsJob)
            .where(
                AnalyticsJob.id == job_id,
                AnalyticsJob.status.in_([AnalyticsJobStatus.QUEUED, AnalyticsJobStatus.RETRYING]),
            )
            .values(
                status=AnalyticsJobStatus.RUNNING,
                worker_id=worker_id,
                claim_token=claim_token,
                claim_generation=AnalyticsJob.claim_generation + 1,
                started_at=now,
                lease_expires_at=lease_expiry,
                attempt_count=AnalyticsJob.attempt_count + 1,
                current_stage="initializing",
                updated_at=now,
            )
        )
        res = await self.session.execute(stmt)
        success = getattr(res, "rowcount", 0) > 0
        return (success, claim_token if success else None)

    async def renew_lease_with_fencing(
        self,
        job_id: uuid.UUID,
        claim_token: uuid.UUID,
        lease_duration_seconds: int = 60,
    ) -> bool:
        """Periodic worker heartbeat renewing lease, strictly validated against the active fencing token."""
        now = datetime.now(UTC)
        lease_expiry = now + timedelta(seconds=lease_duration_seconds)

        stmt = (
            update(AnalyticsJob)
            .where(
                AnalyticsJob.id == job_id,
                AnalyticsJob.claim_token == claim_token,
                AnalyticsJob.status == AnalyticsJobStatus.RUNNING,
            )
            .values(
                lease_expires_at=lease_expiry,
                updated_at=now,
            )
        )
        res = await self.session.execute(stmt)
        return getattr(res, "rowcount", 0) > 0

    async def update_snapshot_metadata(
        self,
        job_id: uuid.UUID,
        claim_token: uuid.UUID,
        snapshot_created_at: datetime,
        snapshot_sheet_id: uuid.UUID,
        snapshot_sheet_version: int,
        snapshot_row_count: int,
        snapshot_col_count: int,
        snapshot_checksum_sha256: str,
        snapshot_size_bytes: int,
        snapshot_storage_id: str,
    ) -> bool:
        """Persist snapshot audit metadata on AnalyticsJob with strict fencing."""
        now = datetime.now(UTC)
        stmt = (
            update(AnalyticsJob)
            .where(
                AnalyticsJob.id == job_id,
                AnalyticsJob.claim_token == claim_token,
                AnalyticsJob.status == AnalyticsJobStatus.RUNNING,
            )
            .values(
                input_snapshot_id=snapshot_storage_id,
                input_snapshot_hash=snapshot_checksum_sha256,
                snapshot_created_at=snapshot_created_at,
                snapshot_sheet_id=snapshot_sheet_id,
                snapshot_sheet_version=snapshot_sheet_version,
                snapshot_row_count=snapshot_row_count,
                snapshot_col_count=snapshot_col_count,
                snapshot_checksum_sha256=snapshot_checksum_sha256,
                snapshot_size_bytes=snapshot_size_bytes,
                snapshot_storage_id=snapshot_storage_id,
                updated_at=now,
            )
        )
        res = await self.session.execute(stmt)
        return getattr(res, "rowcount", 0) > 0

    async def update_progress(
        self,
        job_id: uuid.UUID,
        progress_percent: int,
        stage: str,
        lease_duration_seconds: int = 60,
        claim_token: uuid.UUID | None = None,
    ) -> bool:
        """Update progress and renew worker lease with optional fencing check."""
        now = datetime.now(UTC)
        lease_expiry = now + timedelta(seconds=lease_duration_seconds)

        conditions = [
            AnalyticsJob.id == job_id,
            AnalyticsJob.status == AnalyticsJobStatus.RUNNING,
        ]
        if claim_token is not None:
            conditions.append(AnalyticsJob.claim_token == claim_token)

        stmt = (
            update(AnalyticsJob)
            .where(*conditions)
            .values(
                progress_percent=progress_percent,
                current_stage=stage,
                lease_expires_at=lease_expiry,
                updated_at=now,
            )
        )
        res = await self.session.execute(stmt)
        return getattr(res, "rowcount", 0) > 0

    async def request_cancellation(self, job_id: uuid.UUID, organization_id: uuid.UUID) -> bool:
        """Cooperative cancellation request."""
        now = datetime.now(UTC)
        # Immediate cancel if still queued
        queued_cancel_stmt = (
            update(AnalyticsJob)
            .where(
                AnalyticsJob.id == job_id,
                AnalyticsJob.organization_id == organization_id,
                AnalyticsJob.status == AnalyticsJobStatus.QUEUED,
            )
            .values(
                status=AnalyticsJobStatus.CANCELLED,
                cancellation_requested_at=now,
                completed_at=now,
                updated_at=now,
            )
        )
        res_queued = await self.session.execute(queued_cancel_stmt)
        if getattr(res_queued, "rowcount", 0) > 0:
            return True

        # If running or retrying, set cancel_requested
        running_cancel_stmt = (
            update(AnalyticsJob)
            .where(
                AnalyticsJob.id == job_id,
                AnalyticsJob.organization_id == organization_id,
                AnalyticsJob.status.in_([AnalyticsJobStatus.RUNNING, AnalyticsJobStatus.RETRYING]),
            )
            .values(
                status=AnalyticsJobStatus.CANCEL_REQUESTED,
                cancellation_requested_at=now,
                updated_at=now,
            )
        )
        res_running = await self.session.execute(running_cancel_stmt)
        return getattr(res_running, "rowcount", 0) > 0

    async def mark_cancelled(self, job_id: uuid.UUID) -> bool:
        now = datetime.now(UTC)
        stmt = (
            update(AnalyticsJob)
            .where(
                AnalyticsJob.id == job_id,
                AnalyticsJob.status.in_(
                    [
                        AnalyticsJobStatus.CANCEL_REQUESTED,
                        AnalyticsJobStatus.QUEUED,
                    ]
                ),
            )
            .values(
                status=AnalyticsJobStatus.CANCELLED,
                completed_at=now,
                updated_at=now,
            )
        )
        res = await self.session.execute(stmt)
        return getattr(res, "rowcount", 0) > 0

    async def mark_succeeded(
        self,
        job_id: uuid.UUID,
        result_id: uuid.UUID,
        claim_token: uuid.UUID | None = None,
    ) -> bool:
        now = datetime.now(UTC)
        conditions = [
            AnalyticsJob.id == job_id,
            AnalyticsJob.status == AnalyticsJobStatus.RUNNING,
        ]
        if claim_token is not None:
            conditions.append(AnalyticsJob.claim_token == claim_token)

        stmt = (
            update(AnalyticsJob)
            .where(*conditions)
            .values(
                status=AnalyticsJobStatus.SUCCEEDED,
                result_id=result_id,
                progress_percent=100,
                current_stage="completed",
                completed_at=now,
                lease_expires_at=None,
                updated_at=now,
            )
        )
        res = await self.session.execute(stmt)
        return getattr(res, "rowcount", 0) > 0

    async def mark_failed(
        self,
        job_id: uuid.UUID,
        error_code: str,
        safe_message: str,
        next_status: str = AnalyticsJobStatus.FAILED,
        claim_token: uuid.UUID | None = None,
    ) -> bool:
        now = datetime.now(UTC)
        completed = now if next_status == AnalyticsJobStatus.FAILED else None
        conditions = [
            AnalyticsJob.id == job_id,
            AnalyticsJob.status.in_([AnalyticsJobStatus.RUNNING, AnalyticsJobStatus.RETRYING]),
        ]
        if claim_token is not None:
            conditions.append(AnalyticsJob.claim_token == claim_token)

        stmt = (
            update(AnalyticsJob)
            .where(*conditions)
            .values(
                status=next_status,
                error_code=error_code,
                safe_error_message=safe_message,
                completed_at=completed,
                lease_expires_at=None,
                updated_at=now,
            )
        )
        res = await self.session.execute(stmt)
        return getattr(res, "rowcount", 0) > 0

    async def find_expired_leases(self, cutoff: datetime) -> list[AnalyticsJob]:
        stmt = select(AnalyticsJob).where(
            AnalyticsJob.status == AnalyticsJobStatus.RUNNING,
            AnalyticsJob.lease_expires_at < cutoff,
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def find_stranded_queued_jobs(self, cutoff: datetime) -> list[AnalyticsJob]:
        """Find jobs stuck in queued or retrying state without active execution past dispatch timeout."""
        stmt = select(AnalyticsJob).where(
            AnalyticsJob.status.in_([AnalyticsJobStatus.QUEUED, AnalyticsJobStatus.RETRYING]),
            AnalyticsJob.created_at < cutoff,
            or_(
                AnalyticsJob.lease_expires_at.is_(None),
                AnalyticsJob.lease_expires_at < cutoff,
            ),
        )
        return list((await self.session.execute(stmt)).scalars().all())


class AnalyticsOutboxRepository:
    """Repository for Transactional Outbox event dispatching."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, event: AnalyticsOutboxEvent) -> AnalyticsOutboxEvent:
        self.session.add(event)
        await self.session.flush()
        return event

    async def get_by_job_id(self, job_id: uuid.UUID) -> AnalyticsOutboxEvent | None:
        stmt = (
            select(AnalyticsOutboxEvent)
            .where(AnalyticsOutboxEvent.job_id == job_id)
            .order_by(AnalyticsOutboxEvent.created_at.desc())
            .limit(1)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def fetch_and_lock_pending(
        self, batch_size: int = 10, locked_by: str = "dispatcher"
    ) -> list[AnalyticsOutboxEvent]:
        """Fetch unpublished outbox events using SELECT ... FOR UPDATE SKIP LOCKED."""
        now = datetime.now(UTC)
        lock_cutoff = now - timedelta(seconds=60)

        stmt = (
            select(AnalyticsOutboxEvent)
            .where(
                AnalyticsOutboxEvent.published_at.is_(None),
                or_(
                    AnalyticsOutboxEvent.locked_at.is_(None),
                    AnalyticsOutboxEvent.locked_at < lock_cutoff,
                ),
            )
            .order_by(AnalyticsOutboxEvent.created_at.asc())
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        events = list((await self.session.execute(stmt)).scalars().all())

        if events:
            for event in events:
                event.locked_at = now
                event.locked_by = locked_by
            await self.session.flush()

        return events

    async def mark_published(self, event_id: uuid.UUID) -> None:
        now = datetime.now(UTC)
        stmt = (
            update(AnalyticsOutboxEvent)
            .where(AnalyticsOutboxEvent.id == event_id)
            .values(
                published_at=now,
                locked_at=None,
                locked_by=None,
            )
        )
        await self.session.execute(stmt)

    async def record_publish_error(self, event_id: uuid.UUID, error: str) -> None:
        stmt = (
            update(AnalyticsOutboxEvent)
            .where(AnalyticsOutboxEvent.id == event_id)
            .values(
                attempts=AnalyticsOutboxEvent.attempts + 1,
                last_error=error,
                locked_at=None,
                locked_by=None,
            )
        )
        await self.session.execute(stmt)

    async def prune_published(self, retention_days: int = 7) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        stmt = select(AnalyticsOutboxEvent).where(
            AnalyticsOutboxEvent.published_at.isnot(None),
            AnalyticsOutboxEvent.published_at < cutoff,
        )
        events = (await self.session.execute(stmt)).scalars().all()
        for ev in events:
            await self.session.delete(ev)
        return len(events)


class AnalyticsResultRepository:
    """Repository for persistent AnalyticsResult entity."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, result: AnalyticsResult) -> AnalyticsResult:
        self.session.add(result)
        await self.session.flush()
        return result

    async def get_by_id_scoped(
        self, result_id: uuid.UUID, organization_id: uuid.UUID
    ) -> AnalyticsResult | None:
        stmt = select(AnalyticsResult).where(
            AnalyticsResult.id == result_id,
            AnalyticsResult.organization_id == organization_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_job_id_scoped(
        self, job_id: uuid.UUID, organization_id: uuid.UUID
    ) -> AnalyticsResult | None:
        stmt = select(AnalyticsResult).where(
            AnalyticsResult.job_id == job_id,
            AnalyticsResult.organization_id == organization_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_publish_idempotency_key(
        self, organization_id: uuid.UUID, idempotency_key: str
    ) -> AnalyticsResult | None:
        stmt = select(AnalyticsResult).where(
            AnalyticsResult.organization_id == organization_id,
            AnalyticsResult.publish_idempotency_key == idempotency_key,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()
