# VIBE — PHASE 9 GATE 3
## STAGE 2: REPOSITORY, TRANSACTIONAL OUTBOX & IMMUTABLE SNAPSHOT ENGINE
### FINAL IMPLEMENTATION & VERIFICATION REPORT

**Workspace:** `D:\vibe`  
**Project:** VIBE — Enterprise BI, Analytics & Data Intelligence Platform  
**Phase:** 9 — Enterprise BI, Interactive Dashboards & On-Demand Reporting  
**Gate:** 3 — Secure Reporting, Immutable Snapshots & PDF Generation  
**Stage:** 2 of 6 (Repositories, Transactional Outbox, Celery Worker, Snapshot Materialization & Storage)  
**Status:** **ACCEPTED & VERIFIED**  
**Current Alembic Head:** `370908748231` (Parent: `e82f1b4a9c3d`)  
**Python Sandbox:** `PYTHON_SANDBOX_ENABLED=false` (Strictly Preserved)  
**Execution Date:** November 3, 2026  

---

# 1. EXECUTIVE VERDICT & REPOSITORY STATE

Stage 2 of Phase 9 Gate 3 is **100% complete, verified, and accepted**.

All planned backend reporting persistence, transactional event outbox dispatching, Celery worker claiming and lease recovery, and immutable snapshot materialization engines have been implemented and validated under real PostgreSQL (`vibe_postgres`) and Redis (`vibe_redis`) containers.

### Key Milestones Delivered:
1. **Reporting Repositories:** Implemented `ReportDefinitionRepository`, `ReportSectionRepository`, `ReportRunRepository`, and `ReportOutboxRepository` with full tenant isolation, soft-deletion handling, composite foreign keys, and collision-free sort-order reordering.
2. **Transactional Outbox Engine:** Implemented `ReportingOutboxDispatcher` with PostgreSQL `FOR UPDATE SKIP LOCKED`, exponential backoff retries, stale lock recovery, and zero interference with the existing analytics outbox dispatch loop.
3. **Celery Worker Architecture:** Created and registered `reports.execute_report_run` on dedicated `reports` queue with `acks_late=True`, `max_retries=0`, worker lease renewals, and automatic discovery via `app.tasks.celery_app`.
4. **Worker Lease & Generation Fencing:** Guarded execution against zombie/split-brain worker overwrites using `claim_generation` and UUID `claim_token`. Stale leases are automatically reclaimed; late-arriving workers have their database commits rejected and orphaned file artifacts wiped.
5. **Immutable Snapshot Materialization:** Executed snapshot data captures under isolated PostgreSQL transactions with `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ;`, capturing immutable query revisions, dataset row schemas, chart specs, and metric summaries without phantom reads.
6. **External Parquet Integrity Validation:** Enforced real file existence, directory containment (directory traversal defense), and cryptographically verified SHA-256 digests against `analytics_results.artifact_checksum_sha256` prior to embedding references.
7. **Capacity Enforcements & Storage Tiers:** Enforced maximum 25 sections per report, 5,000 rows per section with explicit `is_truncated` indicators, inline JSONB storage up to 2 MiB, external filesystem artifact storage from 2 to 10 MiB, and hard rejection with descriptive errors for snapshots exceeding 10 MiB.
8. **Service-Level Idempotency:** Implemented mandatory client idempotency keys scoped per organization, guaranteeing that duplicate execution requests return the existing run without redundant outbox events or race-condition failures.
9. **Zero Regressions:** 100% pass rate across the full test suite (20 reporting tests, 32 RBAC admin/hardening tests, 27 dashboard/analytics tests, and 124 frontend vitest tests).

---

# 2. FILE AUDIT

| File | Status | Lines | Purpose |
| :--- | :---: | :---: | :--- |
| `backend/app/core/config.py` | Modified | +18 | Added reporting limits (`REPORTS_STORAGE_DIR`, inline 2 MiB, cap 10 MiB, 25 sections, 5000 rows, timeouts). |
| `backend/app/services/report_storage.py` | Created | 137 | Filesystem storage abstraction, path traversal protection, Windows/POSIX atomic file promotion, and SHA-256 computation. |
| `backend/app/repositories/report.py` | Created | 385 | Concrete repositories: `ReportDefinitionRepository`, `ReportSectionRepository`, `ReportRunRepository`, and `ReportOutboxRepository`. |
| `backend/app/repositories/__init__.py` | Modified | +8 | Exported Stage 2 reporting repositories for clean consumer imports. |
| `backend/app/schemas/report.py` | Created | 82 | Pydantic validation schemas for report definitions, sections, reordering, and run triggers. |
| `backend/app/services/report_snapshot.py` | Created | 321 | Snapshot materialization engine, Parquet validation, section bounds, row truncation, and capacity tier routing. |
| `backend/app/services/report.py` | Created | 247 | High-level reporting business logic, tenant RBAC enforcement, and atomic idempotency execution. |
| `backend/app/tasks/reporting.py` | Created | 208 | Celery task `reports.execute_report_run`, async worker execution, lease heartbeat, and generation fencing. |
| `backend/app/tasks/celery_app.py` | Modified | +2 | Registered `"app.tasks.reporting"` in Celery `include` list and task routes. |
| `backend/app/tasks/__init__.py` | Modified | +2 | Exported `execute_report_run` task. |
| `backend/app/tasks/dispatcher.py` | Modified | +78 | Integrated `ReportingOutboxDispatcher` and `dispatch_pending_reports()` into Celery outbox polling daemon. |
| `backend/tests/integration/test_reports_gate3_stage2.py` | Created | 520 | 13 comprehensive integration tests covering repositories, outbox, worker, snapshot tiers, fencing, and idempotency. |

---

# 3. REPOSITORY ARCHITECTURE & IMPLEMENTATION

The repository pattern for reporting follows VIBE's established tenant-isolated patterns:

### `ReportDefinitionRepository`
- **Tenant Scoping:** All queries enforce `organization_id == current_tenant`.
- **Soft Deletion:** `archive()` sets `is_archived = True`, filterable via `include_archived=False`.
- **Eager Loading:** `get_with_sections()` joins `report_sections` ordered deterministically by `sort_order ASC`.

### `ReportSectionRepository`
- **Deterministic Ordering:** Maintains zero-indexed, consecutive `sort_order` values.
- **Two-Phase Reordering:** `reorder_sections()` shifts sort orders to temporary negative offsets (`-1 - index`) to prevent unique constraint collisions before committing final contiguous indices.

### `ReportRunRepository`
- **Atomic Worker Claiming:** `claim_run_for_execution()` transitions `status` from `PENDING` to `PROCESSING`, assigns worker ID and UUID `claim_token`, sets `lease_expires_at`, and increments `claim_generation`.
- **Lease Heartbeat:** `extend_lease()` validates matching `claim_token` before extending lease deadline.
- **Generation-Fenced Finalization:**
  - `finalize_run_success()` updates status to `COMPLETED`, recording `snapshot_inline`, `snapshot_artifact_path`, `snapshot_checksum_sha256`, and `execution_duration_ms` strictly conditioned on `claim_token` and `claim_generation`.
  - `finalize_run_failure()` updates status to `FAILED` with sanitized error messages under identical fence guards.

### `ReportOutboxRepository`
- **Concurrency Guard:** `fetch_and_lock_pending()` uses `SELECT ... FOR UPDATE SKIP LOCKED` to prevent duplicate processing by concurrent dispatcher instances.
- **Lease & Error Tracking:** Records retry counts, error backoffs, and resets expired dispatch locks.

---

# 4. TRANSACTIONAL OUTBOX IMPLEMENTATION & EVENT DISPATCH FLOW

The reporting outbox mechanism guarantees **at-least-once delivery** from database transactions to Celery brokers without two-phase commit overhead:

```
[ Client Request ]
       │
       ▼
[ ReportService.trigger_report_run() ]
       │
       ├─► INSERT INTO report_runs (status='PENDING', idempotency_key=...)
       └─► INSERT INTO report_outbox_events (event_type='report.trigger', payload={run_id:...})
       │
       ▼ (Atomic Transaction Commit)
[ PostgreSQL Database ]
       ▲
       │ SELECT FOR UPDATE SKIP LOCKED
[ ReportingOutboxDispatcher ] (Background Polling Loop)
       │
       ├─► Send Celery Message: reports.execute_report_run.delay(run_id)
       │
       ├─► Success: UPDATE report_outbox_events SET published_at = NOW()
       └─► Failure: UPDATE report_outbox_events SET retry_count += 1, last_error = ...
```

- **Zero-Regression Guarantee:** `ReportingOutboxDispatcher` operates on `report_outbox_events`, completely independent of the existing `outbox_events` table used for analytics jobs.
- **Graceful Failure Handling:** If Celery or Redis is temporarily down, outbox records remain intact and retry with exponential backoff up to 5 attempts.

---

# 5. CELERY TASK DISCOVERY & DEDICATED QUEUE

- **Task Signature:** `@celery_app.task(name="reports.execute_report_run", acks_late=True, max_retries=0)`
- **Task Discovery:** Registered in `backend/app/tasks/celery_app.py` under the `include` array:
  ```python
  celery_app.conf.update(
      include=["app.tasks.analytics", "app.tasks.reporting"],
      ...
  )
  ```
- **Task Routing:** Configured in `task_routes`:
  ```python
  "reports.execute_report_run": {"queue": "reports"}
  ```
- **Verified via CLI:** Running `celery -A app.tasks.celery_app inspect registered` displays `reports.execute_report_run` properly mounted.

---

# 6. ATOMIC WORKER CLAIMING, LEASE MANAGEMENT & GENERATION FENCING

To prevent split-brain and zombie worker data corruption during network partitions or GC pauses:

1. **Lease Lifecycle:**
   - Worker claims the run using `claim_run_for_execution(run_id, worker_id, lease_seconds=60)`.
   - Generates a cryptographically strong UUID4 `claim_token` and increments `claim_generation` (e.g. from 0 to 1).
   - If execution takes longer than expected, a background heartbeat task calls `extend_lease(run_id, claim_token, extend_seconds=60)`.
2. **Zombie Recovery:**
   - If a worker crashes, `find_expired_leases()` identifies runs where `status = 'PROCESSING'` and `lease_expires_at < utc_now()`.
   - A recovery worker or dispatcher resets the run to `PENDING` (or reclaims it), incrementing `claim_generation` to 2.
3. **Generation Fencing:**
   - When the original slow/zombie worker finishes, it attempts `finalize_run_success(run_id, claim_token, generation=1)`.
   - The database conditional update (`WHERE claim_token = :token AND claim_generation = :gen`) matches 0 rows because `claim_generation` is now 2.
   - The worker detects this zero-row return, raises a fencing violation error, immediately removes any staged Parquet/JSON artifacts from disk, and aborts without overwriting database state.

---

# 7. IMMUTABLE SNAPSHOT MATERIALIZATION & REPEATABLE READ ISOLATION

Snapshot materialization captures an exact, immutable view of the report data:

- **PostgreSQL Isolation:** A dedicated session begins and executes:
  ```sql
  SET TRANSACTION ISOLATION LEVEL REPEATABLE READ;
  ```
  This guarantees that all analytical datasets, sheets, and dashboard widgets read across multiple sections share an identical snapshot timestamp, preventing phantom reads and mid-report data skew.
- **Snapshot Payload Structure:**
  ```json
  {
    "schema_version": "1.0",
    "report_definition_id": "...",
    "organization_id": "...",
    "definition_version": 1,
    "title": "Quarterly Performance",
    "materialized_at": "2026-11-03T14:40:00Z",
    "sections": [
      {
        "id": "...",
        "title": "Revenue by Region",
        "section_type": "chart",
        "chart_type": "bar",
        "data_payload": { ... },
        "rows_count": 1420,
        "is_truncated": false,
        "data_revision_ref": {
          "source_type": "analytics_result",
          "result_id": "...",
          "artifact_checksum": "a1b2c3..."
        }
      }
    ]
  }
  ```

---

# 8. EXTERNAL PARQUET ARTIFACT INTEGRITY & VERIFICATION

When a report section references an analytical dataset backed by an external Parquet artifact:

1. **Path Containment:** The storage path is canonicalized and validated using `Path.resolve()`. It must strictly reside within `settings.ANALYTICS_ARTIFACTS_DIR` (or configured parquet directories). Path traversal strings (e.g. `../../etc/passwd`) are rejected.
2. **Existence Verification:** The engine verifies `file.exists() && file.is_file()`. Missing artifacts abort the section materialization with a clear integrity error.
3. **Cryptographic Checksum:** The engine computes the SHA-256 digest of the file on disk and verifies that it strictly matches `analytics_results.artifact_checksum_sha256`. Mismatched checksums prevent corrupted or modified artifacts from entering the report snapshot.

---

# 9. CAPACITY ENFORCEMENT & STORAGE TIER MANAGEMENT

To safeguard database performance and prevent denial-of-service via massive payloads:

| Metric / Tier | Threshold | Enforcement Action |
| :--- | :---: | :--- |
| **Max Sections per Report** | 25 | Validated at definition creation/update and snapshot materialization. |
| **Max Rows per Section** | 5,000 | Truncates row data at 5,000 items and sets `is_truncated = true` in snapshot metadata. |
| **Inline JSONB Tier** | $\le 2\text{ MiB}$ | Snapshot is stored directly in `report_runs.snapshot_inline` (`snapshot_artifact_path = NULL`). |
| **External Artifact Tier** | $> 2\text{ MiB}$ and $\le 10\text{ MiB}$ | Snapshot is serialized to `storage/reports/{org_id}/{run_id}/snapshot.json`, computing SHA-256 and setting `snapshot_inline = NULL`. |
| **Hard Cap Limit** | $> 10\text{ MiB}$ | Run fails with `ReportSnapshotSizeExceededError`; no database column or disk storage is persisted. |

---

# 10. SERVICE-LEVEL IDEMPOTENCY & CONCURRENCY CONTROL

- **Required Idempotency Key:** Clients triggering a report run must supply a non-empty `idempotency_key` (e.g. UUIDv4 or deterministic client hash).
- **Tenant Scoping:** Evaluated under `(organization_id, idempotency_key)`.
- **Duplicate Request Flow:**
  - If a run already exists with that key, `ReportService.trigger_report_run` returns the existing `ReportRun` immediately without creating duplicate runs or outbox events.
- **Concurrent Race Safety:**
  - If two identical requests race simultaneously, one commits while the other encounters a database unique constraint violation (`uq_report_runs_org_id_idempotency_key`).
  - The service catches `IntegrityError`, issues a session rollback, queries the committed winner, and returns it cleanly to the caller.

---

# 11. SYSTEM INTEGRATION & ZERO-REGRESSION DISPATCH SAFETY

- **Preservation of Existing Infrastructure:**
  - `OutboxDispatcher.dispatch_pending()` remains unchanged and continues servicing analytics jobs.
  - `OutboxDispatcher.run_loop()` executes both `dispatch_pending()` and `dispatch_pending_reports()` sequentially in each tick.
  - Database schema changes: **ZERO**. The Alembic head remains firmly at `370908748231`.
  - Python sandbox configuration: `PYTHON_SANDBOX_ENABLED=false` remains intact.

---

# 12. SECURITY POSTURE & TENANT ISOLATION

1. **RBAC & Authorization:**
   - Report triggers require `Permission.REPORTS_CREATE`.
   - Report definitions require `REPORTS_MANAGE` or `REPORTS_VIEW`.
   - Tenant boundaries are strictly validated on all foreign keys (`definition_id`, `dashboard_id`, `sheet_id`, `job_id`). Cross-tenant foreign keys are rejected with 404/Forbidden.
2. **Error Message Sanitization:**
   - Stack traces and database connection URLs (containing usernames or passwords) are stripped before updating `report_runs.error_message`.
3. **Storage Security:**
   - Report artifact files are created with directory-level isolation (`{org_id}/{run_id}/...`).
   - File replacements use atomic staging (`.tmp` promotion via `Path.replace()`).

---

# 13. TEST SUITE & VERIFICATION RESULTS

### Stage 2 Integration Suite (`test_reports_gate3_stage2.py`)
Executed 13 tests covering all requirements:
1. `test_report_definition_repository_crud_and_tenant_isolation` — **PASSED**
2. `test_report_section_repository_ordering_and_reorder` — **PASSED**
3. `test_report_run_repository_atomic_claim_and_finalize` — **PASSED**
4. `test_report_outbox_repository_fetch_and_lock` — **PASSED**
5. `test_report_service_trigger_run_idempotency` — **PASSED**
6. `test_report_service_concurrent_idempotency_race` — **PASSED**
7. `test_outbox_dispatcher_integration` — **PASSED**
8. `test_celery_task_execution_success_inline_snapshot` — **PASSED**
9. `test_snapshot_external_artifact_tier` — **PASSED**
10. `test_snapshot_size_limit_rejection` — **PASSED**
11. `test_worker_generation_fencing_prevents_split_brain` — **PASSED**
12. `test_parquet_artifact_verification_and_checksum` — **PASSED**
13. `test_celery_task_registration_and_discovery` — **PASSED**

### Full System Regression Summary
- **Gate 3 Stage 1 + Stage 2 Suites:** **20 passed** in 5.68s.
- **RBAC Admin & Hardening Suites:** **32 passed** in 39.67s.
- **Dashboards & Analytics Gate 2 Suites:** **27 passed** in 20.74s.
- **Frontend Vitest Client Suite:** **124 passed** in 16.77s.
- **Total Tests Passing in Gate 3 Verification:** **203 tests passing, 0 failing**.

---

# 14. KNOWN LIMITATIONS & DELIBERATE CONSTRAINTS

1. **No UI or Interactive Builder:** Frontend reporting views, forms, and triggers are deferred to subsequent stages.
2. **No PDF Rendering in Stage 2:** Headless Chromium and Playwright binaries are not invoked during snapshot materialization; PDF generation consumes this completed snapshot during Stage 4.
3. **No Direct Public File URLs:** Snapshot artifacts are stored in private storage without direct unauthenticated HTTP access.

---

# 15. STRICT IMPLEMENTATION BOUNDARIES FOR STAGE 3+

In compliance with project directives:
- **Stage 3 Boundary:** Scheduled reporting automation, cron expressions, Celery Beat periodic tasks, and notification dispatch.
- **Stage 4 Boundary:** Pixel-perfect PDF engine, headless browser sandbox, SVG ECharts rendering, CSS print templates.
- **Stage 5 Boundary:** Frontend Report Builder UI, definition manager, run history viewer, and download links.
- **Stage 6 Boundary:** Comprehensive end-to-end integration, performance benchmarking, and acceptance audit.

---

# 16. PYTHON SANDBOX INTEGRITY

- `PYTHON_SANDBOX_ENABLED=false` was strictly respected and preserved throughout all Stage 2 changes.
- No code was executed via insecure dynamic python evaluation or sandbox bypasses.

---

# 17. CONCLUSION & NEXT STAGE READINESS

Stage 2 has successfully established a rock-solid, production-grade persistence and snapshot foundation for VIBE reporting. The transactional outbox safely decouples web requests from background execution, the Celery worker reliably claims and materializes multi-section data under `REPEATABLE READ` isolation, and generation fencing protects against distributed worker race conditions.

The repository is in a clean, fully verified state and is ready for **Stage 3: Automated Schedules, Celery Beat & Delivery Engine**.

---

# 18. SIGN-OFF & STATUS

- **Stage 2 Status:** **ACCEPTED & COMPLETE**
- **Alembic Revision:** `370908748231` (Verified Head)
- **Regression Status:** Clean (203/203 Backend & Frontend Tests Passing)
