"""Transactional Outbox Dispatcher polling and publishing analytics tasks to Celery."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.db.session import async_session_maker
from app.db.transaction import transaction
from app.models.analytics import AnalyticsJobStatus, AnalyticsOutboxEvent
from app.models.report import ReportOutboxEvent, ReportRun, ReportRunStatus
from app.repositories.analytics import AnalyticsJobRepository, AnalyticsOutboxRepository
from app.repositories.report import ReportOutboxRepository, ReportRunRepository
from app.tasks.analytics import execute_analytics_job
from app.tasks.reporting import execute_report_run

logger = logging.getLogger("vibe.analytics.dispatcher")


class OutboxDispatcher:
    """Polls transactional outbox and dispatches jobs to Redis/Celery with crash recovery watchdog."""

    def __init__(
        self,
        dispatcher_id: str | None = None,
        session_maker: Any = None,
    ) -> None:
        self.dispatcher_id = dispatcher_id or f"dispatcher-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        self.is_running = True
        self.session_maker = session_maker or async_session_maker

    async def dispatch_pending(self, batch_size: int = 10) -> int:
        """Fetch and dispatch pending outbox events using FOR UPDATE SKIP LOCKED."""
        dispatched_count = 0
        async with self.session_maker() as session:
            outbox_repo = AnalyticsOutboxRepository(session)

            events = await outbox_repo.fetch_and_lock_pending(
                batch_size=batch_size, locked_by=self.dispatcher_id
            )
            await session.commit()

            if not events:
                return 0

            for ev in events:
                job_id_str = str(ev.job_id)
                try:
                    # Publish to Celery broker
                    execute_analytics_job.delay(job_id_str)
                    await outbox_repo.mark_published(ev.id)
                    await session.commit()
                    dispatched_count += 1
                    logger.info("Dispatched job %s to Celery (outbox event %s)", job_id_str, ev.id)
                except Exception as e:
                    logger.error("Failed to publish job %s to Celery: %s", job_id_str, e)
                    await outbox_repo.record_publish_error(ev.id, str(e))
                    await session.commit()

        return dispatched_count

    async def dispatch_pending_reports(self, batch_size: int = 10) -> int:
        """Fetch and dispatch pending reporting outbox events using FOR UPDATE SKIP LOCKED."""
        dispatched_count = 0
        async with self.session_maker() as session:
            report_outbox_repo = ReportOutboxRepository(session)

            events = await report_outbox_repo.fetch_and_lock_pending(
                batch_size=batch_size, locked_by=self.dispatcher_id
            )
            await session.commit()

            if not events:
                return 0

            for ev in events:
                run_id_str = str(ev.run_id)
                try:
                    # Publish to Celery broker
                    execute_report_run.delay(run_id_str)
                    await report_outbox_repo.mark_published(ev.id)
                    await session.commit()
                    dispatched_count += 1
                    logger.info("Dispatched report run %s to Celery (outbox event %s)", run_id_str, ev.id)
                except Exception as e:
                    logger.error("Failed to publish report run %s to Celery: %s", run_id_str, e)
                    await report_outbox_repo.record_publish_error(ev.id, str(e))
                    await session.commit()

        return dispatched_count

    async def recover_stale_report_locks(self, timeout_seconds: int = 60) -> int:
        """Reclaim stale locks on reporting outbox events."""
        async with self.session_maker() as session:
            report_outbox_repo = ReportOutboxRepository(session)
            recovered = await report_outbox_repo.release_expired_locks(timeout_seconds=timeout_seconds)
            await session.commit()
            return recovered

    async def recover_stale_report_leases(self) -> int:
        """Sweep and recover report runs with expired worker leases, generating durable outbox events for retries."""
        recovered_count = 0
        cutoff = datetime.now(UTC)
        async with self.session_maker() as session:
            run_repo = ReportRunRepository(session)
            outbox_repo = ReportOutboxRepository(session)
            stale_runs = await run_repo.find_expired_leases(cutoff)

            for run in stale_runs:
                logger.warning(
                    "Recovering stale report run %s (lease expired at %s, attempt %d/%d)",
                    run.id,
                    run.lease_expires_at,
                    run.attempt_count,
                    run.max_attempts,
                )
                async with transaction(session):
                    if run.attempt_count < run.max_attempts:
                        run.status = ReportRunStatus.QUEUED
                        run.lease_expires_at = None
                        run.updated_at = datetime.now(UTC)
                        # Create durable outbox event for retry dispatch
                        retry_event = ReportOutboxEvent(
                            run_id=run.id,
                            organization_id=run.organization_id,
                            event_type="report.run.retrying",
                            payload={"run_id": str(run.id), "attempt": run.attempt_count + 1},
                        )
                        await outbox_repo.create_event(retry_event)
                    else:
                        run.status = ReportRunStatus.FAILED
                        run.lease_expires_at = None
                        run.error_code = "LEASE_EXPIRED"
                        run.error_message = "Worker lease expired and max retry attempts reached."
                        run.completed_at = datetime.now(UTC)
                        run.updated_at = datetime.now(UTC)
                recovered_count += 1

        return recovered_count

    async def recover_stranded_queued_jobs(
        self, timeout_seconds: int = settings.ANALYTICS_DISPATCH_TIMEOUT_SECONDS
    ) -> int:
        """Watchdog: detect and recover jobs stuck in queued/retrying without worker claim."""
        recovered_count = 0
        cutoff = datetime.now(UTC) - timedelta(seconds=timeout_seconds)

        async with async_session_maker() as session:
            job_repo = AnalyticsJobRepository(session)
            outbox_repo = AnalyticsOutboxRepository(session)

            stranded_jobs = await job_repo.find_stranded_queued_jobs(cutoff)

            for job in stranded_jobs:
                latest_event = await outbox_repo.get_by_job_id(job.id)
                # Only redispatch if latest published event is older than timeout cutoff and cooldown has elapsed
                if (
                    latest_event
                    and latest_event.published_at is not None
                    and latest_event.published_at < cutoff
                ):
                    if job.updated_at and job.updated_at >= cutoff:
                        continue
                    logger.warning(
                        "Watchdog recovering stranded job %s (queued since %s, last event %s published at %s)",
                        job.id,
                        job.created_at,
                        latest_event.id,
                        latest_event.published_at,
                    )
                    async with transaction(session):
                        new_event = AnalyticsOutboxEvent(
                            job_id=job.id,
                            organization_id=job.organization_id,
                            event_type="analytics.job.redispatched",
                            payload={"job_id": str(job.id)},
                        )
                        await outbox_repo.create(new_event)
                        job.updated_at = datetime.now(UTC)
                    recovered_count += 1

        return recovered_count

    async def recover_stale_leases(self) -> int:
        """Sweep and recover jobs with expired worker leases, generating durable outbox events for retries."""
        recovered_count = 0
        cutoff = datetime.now(UTC)
        async with async_session_maker() as session:
            job_repo = AnalyticsJobRepository(session)
            outbox_repo = AnalyticsOutboxRepository(session)
            stale_jobs = await job_repo.find_expired_leases(cutoff)

            for job in stale_jobs:
                logger.warning(
                    "Recovering stale job %s (lease expired at %s)", job.id, job.lease_expires_at
                )
                async with transaction(session):
                    if job.attempt_count < job.max_attempts:
                        await job_repo.mark_failed(
                            job_id=job.id,
                            error_code="LEASE_EXPIRED",
                            safe_message="Worker lease expired; re-queueing for retry.",
                            next_status=AnalyticsJobStatus.RETRYING,
                        )
                        # Create durable outbox event for retry dispatch
                        retry_event = AnalyticsOutboxEvent(
                            job_id=job.id,
                            organization_id=job.organization_id,
                            event_type="analytics.job.retrying",
                            payload={"job_id": str(job.id), "attempt": job.attempt_count + 1},
                        )
                        await outbox_repo.create(retry_event)
                    else:
                        await job_repo.mark_failed(
                            job_id=job.id,
                            error_code="LEASE_EXPIRED",
                            safe_message="Worker lease expired and max retry attempts reached.",
                            next_status=AnalyticsJobStatus.FAILED,
                        )
                recovered_count += 1

        return recovered_count

    async def run_loop(self, poll_interval_seconds: float = 1.0) -> None:
        """Continuous polling loop for standalone dispatcher process."""
        logger.info("Outbox Dispatcher started with ID: %s", self.dispatcher_id)
        last_lease_sweep = datetime.now(UTC)
        last_watchdog_sweep = datetime.now(UTC)

        while self.is_running:
            try:
                count_analytics = await self.dispatch_pending()
                count_reports = await self.dispatch_pending_reports()
                if count_analytics == 0 and count_reports == 0:
                    await asyncio.sleep(poll_interval_seconds)

                now = datetime.now(UTC)
                # Periodic lease sweep every 30 seconds
                if now - last_lease_sweep > timedelta(seconds=30):
                    await self.recover_stale_leases()
                    await self.recover_stale_report_leases()
                    await self.recover_stale_report_locks()
                    last_lease_sweep = now

                # Periodic watchdog sweep every 30 seconds
                if now - last_watchdog_sweep > timedelta(seconds=30):
                    await self.recover_stranded_queued_jobs()
                    last_watchdog_sweep = now

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in dispatcher loop: %s", e)
                await asyncio.sleep(poll_interval_seconds * 2)

        logger.info("Outbox Dispatcher %s shutting down cleanly.", self.dispatcher_id)


class ReportingOutboxDispatcher:
    """Dedicated dispatcher for reporting outbox events only."""

    def __init__(
        self, dispatcher_id: str | None = None, session_maker: Any = None
    ) -> None:
        self.dispatcher_id = (
            dispatcher_id
            or f"reporting-dispatcher-{os.getpid()}-{uuid.uuid4().hex[:6]}"
        )
        self.is_running = True
        self._inner = OutboxDispatcher(
            dispatcher_id=self.dispatcher_id, session_maker=session_maker
        )

    async def dispatch_pending(self, batch_size: int = 10) -> int:
        return await self._inner.dispatch_pending_reports(batch_size=batch_size)

    async def recover_stale_locks(self, timeout_seconds: int = 60) -> int:
        return await self._inner.recover_stale_report_locks(timeout_seconds=timeout_seconds)

    async def recover_stale_leases(self) -> int:
        return await self._inner.recover_stale_report_leases()


def run_dispatcher_cli() -> None:
    """CLI entrypoint for running outbox dispatcher container or process."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    dispatcher = OutboxDispatcher()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        loop.run_until_complete(dispatcher.run_loop())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    run_dispatcher_cli()
