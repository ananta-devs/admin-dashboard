"""Celery worker task for executing analytical jobs with independent heartbeat, filesystem fencing, and crash-safe publication."""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
from celery.utils.log import get_task_logger  # type: ignore[import-untyped]
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.analytics.exceptions import (
    AnalyticsError,
    DatasetTooLargeError,
    InvalidParametersError,
    OperationCancelledError,
    WorkerFencingViolationError,
)
from app.analytics.registry import registry
from app.analytics.snapshot import capture_sheet_snapshot, load_sheet_snapshot
from app.analytics.storage import AnalyticsStorageService
from app.core.config import settings
from app.db.transaction import transaction
from app.models.analytics import AnalyticsJobStatus, AnalyticsResult
from app.repositories.analytics import AnalyticsJobRepository, AnalyticsResultRepository
from app.tasks.celery_app import celery_app

logger = get_task_logger(__name__)

# Use NullPool for Celery worker tasks to prevent connection reuse across ephemeral event loops
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


def check_crash_hook(stage_name: str) -> None:
    """Test hook enabling OS process-level termination at designated lifecycle stages."""
    target = os.environ.get("VIBE_CRASH_STAGE")
    trigger_file = os.environ.get("VIBE_CRASH_ONESHOT_FILE")

    if not target or not trigger_file:
        for cand in [
            Path("/app/uploads/analytics/.vibe_crash_trigger"),
            Path("uploads/analytics/.vibe_crash_trigger"),
            Path(__file__).resolve().parents[2] / "uploads/analytics/.vibe_crash_trigger",
            Path("D:/admin-dashboard/backend/uploads/analytics/.vibe_crash_trigger"),
            Path("D:/vibe/backend/uploads/analytics/.vibe_crash_trigger"),
        ]:
            if cand.is_file():
                trigger_file = str(cand)
                try:
                    target = cand.read_text().strip().upper()
                except OSError:
                    pass
                break

    if target and target.strip().upper() == stage_name.strip().upper():
        if trigger_file:
            p = Path(trigger_file)
            if p.is_file():
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass
                sys.stderr.write(
                    f"\n>>> TEST HOOK: ONE-SHOT SIMULATED WORKER CRASH AT {stage_name} (PID={os.getpid()}) <<<\n"
                )
                sys.stderr.flush()
                os._exit(99)
            else:
                return
        sys.stderr.write(
            f"\n>>> TEST HOOK: SIMULATED WORKER CRASH AT {stage_name} (PID={os.getpid()}) <<<\n"
        )
        sys.stderr.flush()
        os._exit(99)


class WorkerHeartbeatThread(threading.Thread):
    """Dedicated OS thread periodically renewing worker lease in PostgreSQL, immune to main thread blocking."""

    def __init__(
        self,
        job_id: uuid.UUID,
        claim_token: uuid.UUID,
        abort_event: threading.Event,
        main_loop: asyncio.AbstractEventLoop | None = None,
        asyncio_abort_event: asyncio.Event | None = None,
        interval_seconds: float = 15.0,
        lease_duration_seconds: int = 60,
        max_consecutive_failures: int = 3,
    ) -> None:
        super().__init__(daemon=True, name=f"heartbeat-{job_id}")
        self.job_id = job_id
        self.claim_token = claim_token
        self.abort_event = abort_event
        self.main_loop = main_loop
        self.asyncio_abort_event = asyncio_abort_event
        self.interval_seconds = interval_seconds
        self.lease_duration_seconds = lease_duration_seconds
        self.max_consecutive_failures = max_consecutive_failures
        self.stop_requested = threading.Event()
        self.renewals_count = 0
        self.last_renewed_at: datetime | None = None
        self.thread_loop: asyncio.AbstractEventLoop | None = None
        self.thread_engine: Any = None
        self.is_disposed = False

    def stop(self) -> None:
        self.stop_requested.set()

    def _signal_abort(self) -> None:
        """Thread-safely signal both OS threading event and main worker asyncio event loop."""
        self.abort_event.set()
        if (
            self.main_loop
            and self.asyncio_abort_event
            and not self.main_loop.is_closed()
            and self.main_loop.is_running()
        ):
            try:
                self.main_loop.call_soon_threadsafe(self.asyncio_abort_event.set)
            except RuntimeError:
                pass

    def run(self) -> None:
        logger.debug("Dedicated heartbeat thread started for job %s", self.job_id)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self.thread_loop = loop
        try:
            loop.run_until_complete(self._run_loop(loop))
        except Exception as e:
            logger.error("Unhandled exception in heartbeat thread for job %s: %s", self.job_id, e)
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            self.thread_loop = None
            self.is_disposed = True
            logger.debug("Dedicated heartbeat thread finished for job %s", self.job_id)

    async def _run_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        consecutive_failures = 0
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from app.core.config import settings

        thread_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
        self.thread_engine = thread_engine
        thread_session_maker = async_sessionmaker(
            thread_engine, class_=AsyncSession, expire_on_commit=False
        )

        try:
            while not self.stop_requested.is_set():
                elapsed = 0.0
                while elapsed < self.interval_seconds and not self.stop_requested.is_set():
                    await asyncio.sleep(min(0.1, max(0.01, self.interval_seconds - elapsed)))
                    elapsed += 0.1

                if self.stop_requested.is_set() or self.abort_event.is_set():
                    break

                try:
                    async with thread_session_maker() as session:
                        repo = AnalyticsJobRepository(session)
                        renewed = await repo.renew_lease_with_fencing(
                            job_id=self.job_id,
                            claim_token=self.claim_token,
                            lease_duration_seconds=self.lease_duration_seconds,
                        )
                        await session.commit()

                    if not renewed:
                        logger.warning(
                            "Heartbeat fencing violation for job %s: lease was lost or claimed by another worker.",
                            self.job_id,
                        )
                        self._signal_abort()
                        break

                    consecutive_failures = 0
                    self.renewals_count += 1
                    self.last_renewed_at = datetime.now(UTC)
                except Exception as e:
                    consecutive_failures += 1
                    logger.error(
                        "Heartbeat failure %s/%s for job %s: %s",
                        consecutive_failures,
                        self.max_consecutive_failures,
                        self.job_id,
                        e,
                    )
                    if consecutive_failures >= self.max_consecutive_failures:
                        logger.error(
                            "Max consecutive heartbeat failures reached for job %s. Aborting.",
                            self.job_id,
                        )
                        self._signal_abort()
                        break
        finally:
            await thread_engine.dispose()
            self.thread_engine = None


async def _heartbeat_loop(
    job_id: uuid.UUID,
    claim_token: uuid.UUID,
    abort_event: Any,
    interval_seconds: float = 15.0,
    lease_duration_seconds: int = 60,
    max_consecutive_failures: int = 3,
) -> None:
    """Asyncio heartbeat loop provided for backward compatibility with existing test suites."""
    consecutive_failures = 0
    while not abort_event.is_set():
        try:
            await asyncio.sleep(interval_seconds)
            if abort_event.is_set():
                break
            async with task_session_maker() as session:
                repo = AnalyticsJobRepository(session)
                renewed = await repo.renew_lease_with_fencing(
                    job_id=job_id,
                    claim_token=claim_token,
                    lease_duration_seconds=lease_duration_seconds,
                )
                await session.commit()
            if not renewed:
                abort_event.set()
                break
            consecutive_failures = 0
        except Exception:
            consecutive_failures += 1
            if consecutive_failures >= max_consecutive_failures:
                abort_event.set()
                break


async def reconcile_commit_outcome(
    job_id: uuid.UUID,
    result_id: uuid.UUID,
    org_id: uuid.UUID,
    final_art_path: Path | None,
    storage: AnalyticsStorageService,
) -> tuple[str, AnalyticsResult | None]:
    """Reconcile ambiguous database commit outcome using a fresh, independent DB connection with transaction synchronization via row lock."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.config import settings

    recon_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    recon_session_maker = async_sessionmaker(
        recon_engine, class_=AsyncSession, expire_on_commit=False
    )
    try:
        async with recon_session_maker() as recon_session:
            async with recon_session.begin():
                j_repo = AnalyticsJobRepository(recon_session)
                r_repo = AnalyticsResultRepository(recon_session)

                # Acquire conflicting row lock on AnalyticsJob using SELECT ... FOR UPDATE.
                # If the original transaction is still unresolved, this waits until that transaction commits or rolls back.
                job = await j_repo.get_by_id_for_update(job_id)

                # Check if result record was committed
                existing_result = await r_repo.get_by_id_scoped(result_id, org_id)
                if existing_result and existing_result.job_id == job_id:
                    logger.info(
                        "Reconciliation confirmed: result %s was committed successfully.", result_id
                    )
                    return ("succeeded", existing_result)

                if (
                    job
                    and job.status == AnalyticsJobStatus.SUCCEEDED
                    and job.result_id == result_id
                ):
                    res = await r_repo.get_by_id_scoped(job.result_id, org_id)
                    logger.info(
                        "Reconciliation confirmed: job %s marked succeeded with result %s.",
                        job_id,
                        result_id,
                    )
                    return ("succeeded", res)

                # The row lock was acquired (original transaction definitively ended) and result was NOT committed -> rolled back
                logger.info(
                    "Reconciliation confirmed: original transaction ended without committing result %s. Rolled back.",
                    result_id,
                )
                if final_art_path and final_art_path.is_file():
                    storage.quarantine_artifact(final_art_path)
                return ("rolled_back", None)
    except Exception as recon_err:
        logger.error(
            "Database unreachable during commit reconciliation for job %s: %s", job_id, recon_err
        )
        return ("unreachable", None)
    finally:
        await recon_engine.dispose()


@celery_app.task(
    name="analytics.execute_job",
    bind=True,
    acks_late=True,
    max_retries=0,  # Managed explicitly via PostgreSQL FSM state
)
def execute_analytics_job(self: Any, job_id: str) -> dict[str, Any]:
    """Execute analytical job in Celery worker process."""
    return asyncio.run(_execute_analytics_job_async(job_id, self.request.id or str(uuid.uuid4())))


async def _execute_analytics_job_async(job_id_str: str, celery_task_id: str) -> dict[str, Any]:
    job_id = uuid.UUID(job_id_str)
    worker_id = f"worker-{os.getpid()}-{celery_task_id[:8]}"
    storage = AnalyticsStorageService()

    # 1. Idempotency Check: if already succeeded, no-op return existing result
    async with task_session_maker() as check_session:
        job_repo_chk = AnalyticsJobRepository(check_session)
        res_repo_chk = AnalyticsResultRepository(check_session)
        existing_job = await job_repo_chk.get_by_id(job_id)
        if existing_job and existing_job.status == AnalyticsJobStatus.SUCCEEDED:
            existing_res = await res_repo_chk.get_by_job_id_scoped(
                job_id, existing_job.organization_id
            )
            if existing_res:
                logger.info("Job %s already succeeded; idempotent no-op return.", job_id)
                return {
                    "status": "succeeded",
                    "job_id": str(job_id),
                    "result_id": str(existing_res.id),
                    "idempotent": True,
                }

    # Crash hook Stage 3: Between task dispatch and worker claim
    check_crash_hook("STAGE_3_BEFORE_CLAIM")

    # 2. Atomically claim job with unique cryptographic fencing token and claim generation
    async with task_session_maker() as session:
        job_repo = AnalyticsJobRepository(session)
        result_repo = AnalyticsResultRepository(session)

        # Crash hook Stage 4: During worker claim transaction
        check_crash_hook("STAGE_4_DURING_CLAIM")

        claimed, claim_token = await job_repo.claim_job_atomically(
            job_id=job_id,
            worker_id=worker_id,
            lease_duration_seconds=settings.ANALYTICS_WORKER_LEASE_SECONDS,
        )
        await session.commit()

        if not claimed or claim_token is None:
            logger.info(
                "Job %s could not be claimed (already running, cancelled, or terminal).", job_id
            )
            return {"status": "skipped", "job_id": str(job_id)}

        job = await job_repo.get_by_id(job_id)
        if not job:
            return {"status": "error", "message": "Job not found"}

        org_id = job.organization_id
        claim_gen = job.claim_generation
        attempt_count = job.attempt_count
        max_attempts = job.max_attempts

        # Crash hook Stage 5: After claim, before heartbeat thread starts
        check_crash_hook("STAGE_5_BEFORE_HEARTBEAT")

        # Start truly independent OS thread heartbeat with thread-safe loop signaling
        main_loop = asyncio.get_running_loop()
        asyncio_abort_event = asyncio.Event()
        abort_event = threading.Event()
        heartbeat_thread = WorkerHeartbeatThread(
            job_id=job_id,
            claim_token=claim_token,
            abort_event=abort_event,
            main_loop=main_loop,
            asyncio_abort_event=asyncio_abort_event,
            interval_seconds=settings.ANALYTICS_HEARTBEAT_INTERVAL_SECONDS,
            lease_duration_seconds=settings.ANALYTICS_WORKER_LEASE_SECONDS,
        )
        heartbeat_thread.start()

        def assert_not_fenced() -> None:
            if abort_event.is_set() or asyncio_abort_event.is_set():
                raise WorkerFencingViolationError(
                    f"Worker {worker_id} fencing violation for job {job_id}. Lost lease or superseded."
                )

        async def is_cancelled() -> bool:
            assert_not_fenced()
            async with task_session_maker() as c_session:
                repo = AnalyticsJobRepository(c_session)
                curr = await repo.get_by_id(job_id)
                return curr is not None and curr.status == AnalyticsJobStatus.CANCEL_REQUESTED

        tmp_art_path: Path | None = None
        final_art_path: Path | None = None

        try:
            # 3. Stage: Snapshot Capture or Reuse (Preserve Snapshot Identity across Retries)
            assert_not_fenced()
            updated = await job_repo.update_progress(
                job_id, 10, "capturing_snapshot", claim_token=claim_token
            )
            if not updated:
                raise WorkerFencingViolationError()
            await session.commit()

            if await is_cancelled():
                raise OperationCancelledError()

            # Check if this job already has a validated snapshot from an earlier attempt
            reused_snapshot = False
            snapshot_path_str = job.input_snapshot_id
            snapshot_hash = job.input_snapshot_hash

            if snapshot_path_str and snapshot_hash:
                cand = Path(snapshot_path_str)
                if not cand.is_file():
                    raise AnalyticsError(
                        f"Immutable snapshot file {cand} from earlier attempt was deleted or missing.",
                        code="SNAPSHOT_INTEGRITY_VIOLATION",
                    )
                actual_hash = storage.compute_sha256(cand)
                if actual_hash != snapshot_hash:
                    raise AnalyticsError(
                        f"Snapshot integrity violation: expected hash {snapshot_hash}, found {actual_hash}.",
                        code="SNAPSHOT_INTEGRITY_VIOLATION",
                    )
                reused_snapshot = True
                logger.info(
                    "Job %s reusing immutable snapshot %s from attempt 1 (hash verified)",
                    job_id,
                    cand,
                )

            if not reused_snapshot:
                # Capture immutable snapshot fenced by claim generation
                snapshot_meta = await capture_sheet_snapshot(
                    sheet_id=job.source_sheet_id,
                    organization_id=org_id,
                    job_id=job_id,
                    claim_generation=claim_gen,
                    storage=storage,
                    session=None,
                )
                snapshot_path_str = snapshot_meta.storage_path
                snapshot_hash = snapshot_meta.checksum_sha256

                assert_not_fenced()
                persisted = await job_repo.update_snapshot_metadata(
                    job_id=job_id,
                    claim_token=claim_token,
                    snapshot_created_at=snapshot_meta.created_at,
                    snapshot_sheet_id=job.source_sheet_id,
                    snapshot_sheet_version=snapshot_meta.sheet_version,
                    snapshot_row_count=snapshot_meta.row_count,
                    snapshot_col_count=snapshot_meta.col_count,
                    snapshot_checksum_sha256=snapshot_meta.checksum_sha256,
                    snapshot_size_bytes=snapshot_meta.size_bytes,
                    snapshot_storage_id=snapshot_meta.storage_id,
                )
                if not persisted:
                    raise WorkerFencingViolationError()
                await session.commit()

                # Crash hook Stage 2: Between snapshot DB commit and task dispatch / data load
                check_crash_hook("STAGE_2_AFTER_SNAPSHOT_COMMIT")

            # 4. Stage: Load into Polars DataFrame with Checksum Validation
            assert_not_fenced()
            updated = await job_repo.update_progress(
                job_id, 30, "loading_dataset", claim_token=claim_token
            )
            if not updated:
                raise WorkerFencingViolationError()
            await session.commit()

            if await is_cancelled():
                raise OperationCancelledError()

            df = load_sheet_snapshot(
                snapshot_path=snapshot_path_str,
                expected_hash=snapshot_hash,
                storage=storage,
            )

            # 5. Stage: Execute Analytical Computation in worker thread (non-blocking)
            assert_not_fenced()
            updated = await job_repo.update_progress(
                job_id, 50, "computing_analytics", claim_token=claim_token
            )
            if not updated:
                raise WorkerFencingViolationError()
            await session.commit()

            if await is_cancelled():
                raise OperationCancelledError()

            # Crash hook Stage 6: During Polars analytical computation
            check_crash_hook("STAGE_6_DURING_COMPUTE")

            # Run heavy CPU-bound Polars/NumPy computation in thread pool so event loop and heartbeat remain free
            result_data = await asyncio.to_thread(
                registry.execute,
                operation_id=job.operation_id,
                df=df,
                raw_params=job.parameters,
                check_cancelled=lambda: abort_event.is_set(),
            )

            assert_not_fenced()

            # 6. Stage: Crash-Safe Result Publication Protocol
            updated = await job_repo.update_progress(
                job_id, 80, "persisting_results", claim_token=claim_token
            )
            if not updated:
                raise WorkerFencingViolationError()
            await session.commit()

            if await is_cancelled():
                raise OperationCancelledError()

            result_id = uuid.uuid4()
            artifact_path_str: str | None = None
            artifact_size = None
            artifact_hash = None
            result_row_count = (
                result_data.get("row_count") or result_data.get("total_rows") or df.height
            )
            result_col_count = len(result_data.get("columns", [])) or df.width

            if result_row_count > settings.ANALYTICS_MAX_RESULT_ROWS:
                raise DatasetTooLargeError(
                    f"Result row count ({result_row_count}) exceeds limit of {settings.ANALYTICS_MAX_RESULT_ROWS}."
                )

            # If tabular data is large, write to attempt-fenced Parquet artifact
            data_rows = result_data.get("data")
            if isinstance(data_rows, list) and len(data_rows) > 500:
                # Validate quota reservation before generating large artifact
                storage.check_storage_quota(org_id)

                result_df = pl.DataFrame(data_rows)
                final_art_path = storage.get_result_artifact_path(
                    org_id, job_id, result_id, claim_generation=claim_gen, ext="parquet"
                )
                tmp_art_path = final_art_path.with_suffix(".parquet.tmp")

                # Step 1: Write complete artifact to temporary file
                result_df.write_parquet(tmp_art_path)

                # Crash hook Stage 7: After computation, during Parquet write to temp path
                check_crash_hook("STAGE_7_DURING_PARQUET_WRITE")

                # Step 2: Validate size, format, and checksum
                artifact_size = tmp_art_path.stat().st_size
                if artifact_size > settings.ANALYTICS_MAX_ARTIFACT_BYTES:
                    tmp_art_path.unlink(missing_ok=True)
                    raise DatasetTooLargeError(
                        f"Result artifact size ({artifact_size} bytes) exceeds limit of {settings.ANALYTICS_MAX_ARTIFACT_BYTES} bytes."
                    )

                artifact_hash = storage.compute_sha256(tmp_art_path)
                artifact_path_str = str(final_art_path.resolve())

                # Crash hook Stage 8: Between temp Parquet write and atomic promotion
                check_crash_hook("STAGE_8_BEFORE_PROMOTE")

                # Step 3: Flush, sync, and atomically promote temporary artifact to immutable final path
                # BEFORE the database transaction commits
                storage.atomic_promote(tmp_art_path, final_art_path)

                # Keep preview of first 100 rows in JSONB
                result_data["data"] = data_rows[:100]
                result_data["truncated"] = True
                result_data["total_data_rows"] = len(data_rows)

            # Step 4: Validate JSON payload size bounds
            import json

            serialized_payload = json.dumps(result_data)
            if (
                len(serialized_payload.encode("utf-8"))
                > settings.ANALYTICS_MAX_RESULT_PAYLOAD_BYTES
            ):
                if final_art_path and final_art_path.is_file():
                    final_art_path.unlink(missing_ok=True)
                raise DatasetTooLargeError(
                    f"Result JSON payload ({len(serialized_payload.encode('utf-8'))} bytes) exceeds limit of {settings.ANALYTICS_MAX_RESULT_PAYLOAD_BYTES} bytes."
                )

            # Crash hook Stage 9: Between atomic promotion and DB result commit
            check_crash_hook("STAGE_9_BEFORE_DB_COMMIT")

            # Step 5: Commit result record and mark job succeeded in a single fenced database transaction
            assert_not_fenced()
            try:
                async with transaction(session):
                    # Crash hook Stage 10: During DB result commit (simulated kill via test hook)
                    check_crash_hook("STAGE_10_DURING_DB_COMMIT")

                    # Lock AnalyticsJob row to establish conflicting transaction boundary for reconciliation
                    job_row = await job_repo.get_by_id_for_update(job_id)
                    if not job_row or (claim_token and job_row.claim_token != claim_token):
                        raise WorkerFencingViolationError(
                            "Fencing check failed during result commit. Worker was superseded."
                        )

                    analytics_result = AnalyticsResult(
                        id=result_id,
                        job_id=job_id,
                        organization_id=org_id,
                        result_type=job.operation_id,
                        result_payload=result_data,
                        artifact_path=artifact_path_str,
                        artifact_size_bytes=artifact_size,
                        artifact_checksum_sha256=artifact_hash,
                        row_count=result_row_count,
                        column_count=result_col_count,
                    )
                    await result_repo.create(analytics_result)
                    marked = await job_repo.mark_succeeded(
                        job_id, result_id, claim_token=claim_token
                    )
                    if not marked:
                        raise WorkerFencingViolationError(
                            "Fencing check failed during mark_succeeded. Worker was superseded."
                        )
            except WorkerFencingViolationError:
                if final_art_path and final_art_path.is_file():
                    storage.delete_artifact(final_art_path)
                raise
            except Exception as commit_err:
                # Ambiguous commit outcome! Reconcile authoritative state with independent connection
                recon_status, recon_res = await reconcile_commit_outcome(
                    job_id=job_id,
                    result_id=result_id,
                    org_id=org_id,
                    final_art_path=final_art_path,
                    storage=storage,
                )
                if recon_status == "succeeded":
                    logger.info(
                        "Ambiguous commit reconciled successfully for job %s: result %s exists.",
                        job_id,
                        result_id,
                    )
                elif recon_status == "rolled_back":
                    if final_art_path and final_art_path.is_file():
                        storage.quarantine_artifact(final_art_path)
                    raise commit_err
                else:  # unreachable
                    logger.warning(
                        "Preserving artifact %s on disk due to unreachable DB during commit reconciliation.",
                        final_art_path,
                    )
                    raise commit_err

            # Crash hook Stage 11: Between DB result commit and outbox task completion
            check_crash_hook("STAGE_11_AFTER_DB_COMMIT")

            logger.info("Analytics job %s succeeded successfully.", job_id)
            return {"status": "succeeded", "job_id": str(job_id), "result_id": str(result_id)}

        except WorkerFencingViolationError as fe:
            logger.warning(
                "Analytics job %s aborted due to worker fencing violation: %s", job_id, fe
            )
            if tmp_art_path and tmp_art_path.is_file():
                tmp_art_path.unlink(missing_ok=True)
            if final_art_path and final_art_path.is_file():
                final_art_path.unlink(missing_ok=True)
            return {"status": "aborted", "job_id": str(job_id), "error": "FENCING_VIOLATION"}

        except OperationCancelledError:
            logger.info("Analytics job %s was cancelled.", job_id)
            if tmp_art_path and tmp_art_path.is_file():
                tmp_art_path.unlink(missing_ok=True)
            if final_art_path and final_art_path.is_file():
                final_art_path.unlink(missing_ok=True)
            await job_repo.mark_cancelled(job_id)
            await session.commit()
            return {"status": "cancelled", "job_id": str(job_id)}

        except (InvalidParametersError, DatasetTooLargeError) as pe:
            logger.warning("Analytics job %s failed with permanent error: %s", job_id, pe.message)
            if tmp_art_path and tmp_art_path.is_file():
                tmp_art_path.unlink(missing_ok=True)
            if final_art_path and final_art_path.is_file():
                final_art_path.unlink(missing_ok=True)
            await job_repo.mark_failed(
                job_id=job_id,
                error_code=pe.code,
                safe_message=pe.message,
                next_status=AnalyticsJobStatus.FAILED,
                claim_token=claim_token,
            )
            await session.commit()
            return {"status": "failed", "job_id": str(job_id), "error": pe.message}

        except Exception as e:
            logger.error(
                "Analytics job %s encountered unhandled error: %s\n%s",
                job_id,
                e,
                traceback.format_exc(),
            )
            if tmp_art_path and tmp_art_path.is_file():
                tmp_art_path.unlink(missing_ok=True)

            # Reconcile final artifact before touching it
            if final_art_path and final_art_path.is_file():
                try:
                    recon_status, _ = await reconcile_commit_outcome(
                        job_id=job_id,
                        result_id=result_id,
                        org_id=org_id,
                        final_art_path=final_art_path,
                        storage=storage,
                    )
                    if recon_status == "rolled_back":
                        storage.quarantine_artifact(final_art_path)
                    # If "succeeded" or "unreachable", leave artifact untouched on disk!
                except Exception:
                    pass

            can_retry = attempt_count < max_attempts
            next_status = AnalyticsJobStatus.RETRYING if can_retry else AnalyticsJobStatus.FAILED

            safe_msg = "An internal analytical error occurred during calculation."
            if isinstance(e, AnalyticsError):
                safe_msg = e.message

            try:
                await job_repo.mark_failed(
                    job_id=job_id,
                    error_code="CALCULATION_ERROR",
                    safe_message=safe_msg,
                    next_status=next_status,
                    claim_token=claim_token,
                )
                await session.commit()
            except Exception:
                pass
            return {"status": next_status, "job_id": str(job_id), "error": safe_msg}

        finally:
            abort_event.set()
            heartbeat_thread.stop()
            heartbeat_thread.join(timeout=2.0)
