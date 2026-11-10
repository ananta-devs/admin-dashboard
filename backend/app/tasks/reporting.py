"""Celery background tasks for Phase 9 Gate 3 on-demand report execution."""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.exceptions import LeaseExpiredError
from app.db.base import utc_now
from app.models.report import ReportRunStatus
from app.repositories.report import ReportDefinitionRepository, ReportRunRepository
from app.services.report_renderer import ReportRendererClient
from app.services.report_snapshot import ReportSnapshotService
from app.services.report_storage import ReportStorageService
from app.tasks.celery_app import celery_app

logger = logging.getLogger("vibe.reporting.worker")

# Worker connection pool using NullPool to avoid leaked connection state across task processes
task_engine = create_async_engine(
    settings.DATABASE_URL,
    poolclass=NullPool,
    echo=settings.DB_ECHO,
)
task_session_maker = async_sessionmaker(
    task_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


@celery_app.task(
    name="reports.execute_report_run",
    bind=True,
    acks_late=True,
    max_retries=0,  # Managed explicitly via PostgreSQL FSM state
)
def execute_report_run(self: Any, run_id: str) -> dict[str, Any]:
    """Execute on-demand report materialization in Celery worker process."""
    return asyncio.run(_execute_report_run_async(run_id, self.request.id or str(uuid.uuid4())))


async def _execute_report_run_async(run_id_str: str, celery_task_id: str) -> dict[str, Any]:
    run_id = uuid.UUID(run_id_str)
    worker_id = f"report-worker-{os.getpid()}-{celery_task_id[:8]}"
    storage = ReportStorageService()
    start_time = time.time()

    # 1. Idempotency Check: if run is already SUCCEEDED, return cleanly
    async with task_session_maker() as check_session:
        run_repo_chk = ReportRunRepository(check_session)
        existing = await run_repo_chk.get_by_id(run_id, None)  # type: ignore[arg-type]
        if not existing:
            # Need to find run without org scope for internal worker execution
            from sqlalchemy import select

            from app.models.report import ReportRun

            stmt = select(ReportRun).where(ReportRun.id == run_id)
            res = await check_session.execute(stmt)
            existing = res.scalar_one_or_none()

        if existing and existing.status == ReportRunStatus.SUCCEEDED:
            logger.info("Report run %s already succeeded; idempotent no-op return.", run_id)
            return {
                "status": "succeeded",
                "run_id": str(run_id),
                "idempotent": True,
            }

    # 2. Atomically claim run with unique cryptographic fencing token and claim generation
    async with task_session_maker() as claim_session:
        run_repo = ReportRunRepository(claim_session)
        claimed, run = await run_repo.claim_run_for_execution(
            run_id=run_id,
            worker_id=worker_id,
            lease_seconds=settings.REPORT_WORKER_LEASE_SECONDS,
        )
        await claim_session.commit()

        if not claimed or run is None or run.claim_token is None:
            logger.info("Run %s could not be claimed (already claimed or exhausted).", run_id)
            return {"status": "skipped", "reason": "not_claimable"}

    claim_token: uuid.UUID = run.claim_token
    claim_generation = run.claim_generation
    org_id = run.organization_id

    # 3. Snapshot materialization under single PostgreSQL REPEATABLE READ transaction
    external_path: str | None = None
    try:
        # Dedicated session with explicit REPEATABLE READ isolation
        async with task_session_maker() as snapshot_session:
            # Set isolation level to REPEATABLE READ on this connection
            await snapshot_session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ;"))
            iso_res = await snapshot_session.execute(text("SHOW transaction_isolation;"))
            iso_level = iso_res.scalar_one()
            logger.info("Report run %s executing under isolation level: %s", run_id, iso_level)

            snapshot_svc = ReportSnapshotService(snapshot_session, storage=storage)
            (
                snapshot_dict,
                external_path,
                size_bytes,
                checksum,
                data_revision_ref,
            ) = await snapshot_svc.materialize_snapshot(run)
            await snapshot_session.commit()

        # 4. Render PDF with structured lease heartbeat renewal
        pdf_final_path_str: str | None = None
        pdf_size_bytes: int | None = None
        pdf_checksum: str | None = None
        pdf_page_count: int | None = None
        pdf_rendered_at = None

        cancel_event = asyncio.Event()

        async def _heartbeat_loop() -> None:
            try:
                while not cancel_event.is_set():
                    await asyncio.sleep(settings.ANALYTICS_HEARTBEAT_INTERVAL_SECONDS)
                    if cancel_event.is_set():
                        break
                    async with task_session_maker() as hb_session:
                        hb_repo = ReportRunRepository(hb_session)
                        extended = await hb_repo.extend_lease(
                            run_id=run_id,
                            claim_token=claim_token,
                            claim_generation=claim_generation,
                            extend_seconds=settings.REPORT_WORKER_LEASE_SECONDS,
                        )
                        await hb_session.commit()
                        if not extended:
                            logger.warning(
                                "Heartbeat failed to renew lease for run %s gen %d. Aborting render.",
                                run_id,
                                claim_generation,
                            )
                            cancel_event.set()
                            break
            except asyncio.CancelledError:
                pass

        heartbeat_task = asyncio.create_task(_heartbeat_loop())
        try:
            renderer_client = ReportRendererClient()
            report_def_meta = {
                "id": str(run.report_definition_id or ""),
                "title": run.report_title,
                "description": "",
                "page_size": "A4",
                "page_orientation": "portrait",
            }
            if run.report_definition_id:
                async with task_session_maker() as meta_session:
                    def_repo = ReportDefinitionRepository(meta_session)
                    d = await def_repo.get_by_id(run.report_definition_id, org_id)
                    if d:
                        report_def_meta["id"] = str(d.id)
                        report_def_meta["title"] = d.title
                        report_def_meta["description"] = d.description or ""
                        report_def_meta["page_size"] = d.page_size
                        report_def_meta["page_orientation"] = d.page_orientation

            run_dict = {
                "id": str(run_id),
                "organization_id": str(org_id),
                "snapshot_generation": run.claim_generation,
                "triggered_by": str(run.created_by_user_id or "system"),
            }

            render_task = asyncio.create_task(
                renderer_client.render(
                    report_def=report_def_meta,
                    run_dict=run_dict,
                    snapshot_payload=snapshot_dict,
                    sections=snapshot_dict.get("sections", []),
                )
            )

            done, pending = await asyncio.wait(
                [render_task, asyncio.create_task(cancel_event.wait())],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if cancel_event.is_set() and not render_task.done():
                render_task.cancel()
                await renderer_client.cancel(
                    str(run_id),
                    organization_id=str(org_id),
                    report_id=str(run.report_definition_id or ""),
                )
                raise LeaseExpiredError(
                    f"Worker lease expired during PDF rendering for run {run_id}"
                )

            pdf_bytes, pdf_page_count, pdf_checksum, pdf_size_bytes = await render_task

            # Write to staging path
            pdf_staging_path = storage.get_pdf_staging_path(org_id, run_id, claim_generation)
            pdf_staging_path.parent.mkdir(parents=True, exist_ok=True)
            with open(pdf_staging_path, "wb") as f:
                f.write(pdf_bytes)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass

            # Atomic promote
            pdf_final_path = storage.get_pdf_artifact_path(org_id, run_id, claim_generation)
            storage.atomic_promote(pdf_staging_path, pdf_final_path)
            pdf_final_path_str = str(pdf_final_path)
            pdf_rendered_at = utc_now()

        finally:
            cancel_event.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

        # 5. Finalize run with strict claim generation and token fencing
        async with task_session_maker() as final_session:
            final_repo = ReportRunRepository(final_session)
            duration_ms = int((time.time() - start_time) * 1000)

            success = await final_repo.finalize_run_success(
                run_id=run_id,
                claim_token=claim_token,
                claim_generation=claim_generation,
                snapshot=snapshot_dict,
                data_revision_ref=data_revision_ref,
                execution_duration_ms=duration_ms,
                artifact_path=external_path,
                file_size_bytes=size_bytes,
                checksum_sha256=checksum,
                pdf_artifact_path=pdf_final_path_str,
                pdf_checksum_sha256=pdf_checksum,
                pdf_file_size_bytes=pdf_size_bytes,
                pdf_page_count=pdf_page_count,
                pdf_rendered_at=pdf_rendered_at,
            )
            await final_session.commit()

            if not success:
                # Claim generation mismatch or lease expired; newer generation took over
                logger.warning(
                    "Claim fencing violation for run %s: Generation %d discarded. Discarding artifact.",
                    run_id,
                    claim_generation,
                )
                storage.cleanup_generation_artifacts(
                    org_id, run_id, claim_generation=claim_generation
                )
                return {"status": "fenced_out", "run_id": str(run_id)}

        logger.info(
            "Report run %s successfully materialized in %d ms (snapshot size: %d bytes).",
            run_id,
            duration_ms,
            size_bytes,
        )
        return {
            "status": "succeeded",
            "run_id": str(run_id),
            "claim_generation": claim_generation,
            "duration_ms": duration_ms,
        }

    except Exception as exc:
        logger.error("Report run %s execution failed: %s", run_id, exc, exc_info=True)
        # Clean up only artifacts and temporary files belonging to this specific generation
        storage.cleanup_generation_artifacts(org_id, run_id, claim_generation=claim_generation)
        if external_path:
            storage.delete_artifact(external_path)

        # Sanitize error message to prevent database credentials/secrets from leaking
        raw_msg = str(exc)
        safe_msg = raw_msg
        for secret_token in ["password", "secret", "bearer", "apikey", "credential"]:
            if secret_token in safe_msg.lower():
                safe_msg = "An internal error occurred during report generation."
                break

        # Record controlled failure state fenced by claim token and generation
        try:
            async with task_session_maker() as fail_session:
                fail_repo = ReportRunRepository(fail_session)
                duration_ms = int((time.time() - start_time) * 1000)
                await fail_repo.finalize_run_failure(
                    run_id=run_id,
                    claim_token=claim_token,
                    claim_generation=claim_generation,
                    error_code=type(exc).__name__.upper(),
                    error_message=safe_msg[:500],
                    execution_duration_ms=duration_ms,
                    is_retryable=False,
                )
                await fail_session.commit()
        except Exception as fail_err:
            logger.error("Failed to record failure state for run %s: %s", run_id, fail_err)

        return {
            "status": "failed",
            "run_id": str(run_id),
            "error_code": type(exc).__name__.upper(),
            "error_message": safe_msg[:500],
        }
