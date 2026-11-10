# VIBE — PHASE 9 GATE 3
## STAGE 2.1: PRE-IMPLEMENTATION ARCHITECTURE & CODEBASE INSPECTION REPORT

**Workspace:** `D:\vibe`  
**Current Alembic Head:** `370908748231`  
**Parent Revision:** `e82f1b4a9c3d`  
**Python Sandbox:** `PYTHON_SANDBOX_ENABLED=false`  
**Date:** November 3, 2026  

---

### 1. Complete Execution Path Trace

The current execution flow was traced end-to-end across models, repositories, services, outbox dispatcher, and worker:

1. **Triggering:** Client requests report execution via `ReportService.trigger_report_run()`. It validates `idempotency_key` and creates a `ReportRun` (status: `QUEUED`) and `ReportOutboxEvent` (event: `report.run.dispatched`) atomically in PostgreSQL.
2. **Outbox Dispatch:** `OutboxDispatcher.dispatch_pending_reports()` polls `report_outbox_events` with `SELECT ... FOR UPDATE SKIP LOCKED`, emits `execute_report_run.delay(run_id)` to Celery on queue `reports`, and marks the outbox event `published_at = NOW()`.
3. **Worker Claiming:** Celery worker receives task `reports.execute_report_run`. `_execute_report_run_async` calls `ReportRunRepository.claim_run_for_execution()`, which sets status to `RUNNING`, generates UUID `claim_token`, increments `claim_generation` (e.g. 0 -> 1), and sets `lease_expires_at = NOW() + 60s`.
4. **Snapshot Materialization:** In a dedicated session with `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ;`, `ReportSnapshotService.materialize_snapshot()` fetches definitions, sections, and source data (widgets, sheets, analytics results with Parquet checksum verification).
5. **Artifact Promotion:** If payload $> 2\text{ MiB}$, it writes to a temporary file and atomically replaces it to `runs/{org_id}/{run_id}/snapshot_gen_{claim_generation}.*`.
6. **Worker Finalization:** Calls `ReportRunRepository.finalize_run_success()` conditioned strictly on `WHERE id = :run_id AND claim_token = :token AND claim_generation = :gen`.
7. **Cleanup / Fencing:** If fencing check fails (0 rows updated), worker logs violation and deletes its staged artifact.

---

### 2. Pre-Implementation Findings by Correction

#### Correction A — Generation-Specific Artifact Isolation
- **Inspection Finding:** `ReportStorageService.get_snapshot_artifact_path` already formatted filename with `snapshot_gen_{claim_generation}`. However:
  - `cleanup_temp_files` globbed `*.tmp` indiscriminately within the run directory. If Generation 2 was actively writing `snapshot_gen_2.tmp`, Generation 1 crashing or cleaning up could unlink Generation 2's staging file.
  - Staging paths were named `.json.tmp` instead of explicitly including the generation (`snapshot_gen_{claim_generation}.parquet.tmp`).
- **Correction Action:** Add `get_staging_artifact_path(...)` and `cleanup_generation_artifacts(..., claim_generation)` to ensure cleanup operates strictly and exclusively on the files belonging to the stale generation.

#### Correction B — Snapshot Format Decision (JSON vs Parquet)
- **Inspection Finding:** Stage 2 serialized external snapshots to JSON bytes.
- **Dependency & Polars Inspection:** `polars` version 2.1.0 is installed in `backend/.venv`. Polars has native Rust-based Parquet read/write capabilities with snappy compression and does NOT require `pyarrow` or any new dependencies.
- **Measured Comparison:**
  - On a sample 1,000-row snapshot: JSON size = 20,064 bytes; Snappy Parquet size = 2,640 bytes (**86.8% size reduction**).
  - Parquet provides strict column types and fast selective scanning for the future PDF engine.
- **Correction Action:** Implement native Polars Parquet serialization for snapshots $> 2\text{ MiB}$ and $\le 10\text{ MiB}$. Store `format="parquet"` in snapshot metadata. Provide symmetric `load_snapshot_data()` helper that seamlessly returns the canonical snapshot dictionary for consumers.

#### Correction C — Execution Permission & RBAC Role Matrix
- **Inspection Finding:**
  - Alembic migration `370908748231` seeded 6 explicit reporting permissions in table `permissions`:
    - `reports.read`: View reports
    - `reports.create`: Create reports
    - `reports.update`: Update reports
    - `reports.delete`: Delete reports
    - `reports.run`: Execute report generation
    - `reports.export`: Download report artifacts
  - Role assignments in `role_permissions`:
    - `owner`, `admin`: all 6 permissions.
    - `member`: 5 permissions (has `reports.run` and `reports.create`, lacks `reports.delete`).
    - `viewer`: 2 permissions (`reports.read`, `reports.export` — strictly lacks `reports.run` and `reports.create`).
  - **`REPORTS_MANAGE` Alias Check:** Zero occurrences in models, migrations, or database tables. It was merely informal phrasing in Stage 2 documentation; no permission bypass exists.
  - **Service Authorization Gap:** `ReportService.trigger_report_run()` did not perform an explicit RBAC check on `reports.run`.
- **Correction Action:** Add `membership_id: uuid.UUID | None = None` to `trigger_report_run()` and `create_report_definition()`. If `membership_id` or `user_id` is passed, enforce `reports.run` and `reports.create` via `PermissionService`, raising `ForbiddenError` if unauthorized.

#### Correction D — Worker Lease Recovery in Dispatcher Loop
- **Inspection Finding:**
  - `OutboxDispatcher.run_loop()` periodically calls `recover_stale_leases()` (for analytics jobs) and `recover_stale_report_locks()` (for outbox event locks).
  - It did NOT sweep expired leases on `report_runs` where `status = RUNNING` and `lease_expires_at < NOW()`.
- **Correction Action:** Implement `recover_stale_report_leases()` in `OutboxDispatcher` and `ReportingOutboxDispatcher`. When an expired lease is detected:
  - If `attempt_count < max_attempts`: reset status to `QUEUED`, clear `lease_expires_at`, and create a `report.run.retrying` outbox event to re-dispatch.
  - If `attempt_count >= max_attempts`: mark run as `FAILED` (`LEASE_EXPIRED`).
  - Reclaimed runs will receive `claim_generation = 2` upon subsequent worker claim, safely fencing out the stale worker.

#### Correction E — Snapshot Integrity and Capacity
- **Inspection Finding:**
  - Limits in `app.core.config` are `REPORT_SNAPSHOT_INLINE_MAX_BYTES = 2 * 1024 * 1024` and `REPORT_SNAPSHOT_TOTAL_MAX_BYTES = 10 * 1024 * 1024`.
  - Staging temp files must be cleaned up if the total size exceeds 10 MiB before raising `DatasetTooLargeError`.
  - `_materialize_section` already verifies external Parquet existence, path containment, and SHA-256 checksum against `analytics_results.artifact_checksum_sha256`.

---

### 3. Implementation Plan
1. Update `backend/app/services/report_storage.py` with generation-scoped staging paths and generation-specific cleanup.
2. Update `backend/app/services/report_snapshot.py` with Polars Parquet serialization for tier 2 snapshots, clean error cleanup, and `load_snapshot_data()`.
3. Update `backend/app/services/report.py` with explicit `reports.run` and `reports.create` authorization checks via `PermissionService`.
4. Update `backend/app/tasks/dispatcher.py` with `recover_stale_report_leases()` in `OutboxDispatcher` and `ReportingOutboxDispatcher`, called in `run_loop()`.
5. Author targeted test suite `backend/tests/integration/test_reports_gate3_stage2_1.py` covering all 5 corrections, and execute full regression verification.
