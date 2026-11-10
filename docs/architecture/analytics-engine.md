# VIBE Analytics Engine & Background Processing Infrastructure

**Document Version:** 1.0.0  
**Phase:** 7  
**Status:** Approved & Implemented  
**Date:** November 2026  

---

## 1. Executive Summary

The **VIBE Analytics Subsystem** provides resource-controlled, tenant-isolated Python analytical processing against tabular spreadsheet datasets. The architecture decouples interactive HTTP request processing from high-compute vector analytical workloads via the **PostgreSQL Transactional Outbox Pattern**, **Celery 5.6**, and **Redis 7**. 

Analytical routines are implemented using **Apache Arrow-backed Polars 2.1**, **Pandas 3.1**, and **NumPy 2.5** running exclusively in pre-compiled, allowlisted operations. User-supplied arbitrary code execution and direct SQL execution are strictly barred by design.

---

## 2. Architectural Design

```
+---------------------------------------------------------------------------------------+
|                                    Client Tier                                        |
|  Next.js 16 (App Router) - /analytics Dashboard, Job Poller, Result Visualizer        |
+---------------------------------------------------------------------------------------+
                                           |
                                  POST /api/v1/analytics/jobs
                                           |
+---------------------------------------------------------------------------------------+
|                                  FastAPI Core Tier                                    |
|                                                                                       |
|  1. Authorization: Verify membership + analytics.create                               |
|  2. Concurrency Checks: Max 5 active jobs/org, Max 2 active jobs/user                 |
|  3. Idempotency Check: (organization_id, idempotency_key)                             |
|  4. Atomic DB Transaction:                                                            |
|     - INSERT INTO analytics_jobs (status='queued')                                    |
|     - INSERT INTO analytics_outbox_events (event_type='analytics.job.scheduled')      |
|  5. Return HTTP 202 Accepted                                                          |
+---------------------------------------------------------------------------------------+
         |                                                                      |
    PostgreSQL Commit                                                    PostgreSQL Outbox
         |                                                                      |
+------------------------------------+                +---------------------------------+
|   Outbox Dispatcher Daemon         |                |       PostgreSQL Database       |
|                                    |                |                                 |
|  - SELECT ... FOR UPDATE           | <============> |  - analytics_jobs (State FSM)   |
|    SKIP LOCKED                     |                |  - analytics_outbox_events      |
|  - Celery.delay(job_id)            |                |  - analytics_results            |
|  - Lease Recovery Sweep (stale)    |                |  - composite foreign keys       |
+------------------------------------+                +---------------------------------+
         |
    Redis Broker (db=1)
         |
+---------------------------------------------------------------------------------------+
|                              Celery Worker Pool Tier                                  |
|                                                                                       |
|  1. Atomic Claim: UPDATE analytics_jobs SET status='running', lease_expires_at=...    |
|  2. Snapshot Extraction: Read sheet cells -> Immutable Parquet snapshot artifact      |
|  3. Operation Execution: Allowlist OperationRegistry (Polars / NumPy / Pandas)        |
|  4. Cooperative Cancellation Checkpoints                                              |
|  5. Result Persistence: Save payload to PostgreSQL + large data to Parquet artifact   |
|  6. Mark Succeeded: UPDATE analytics_jobs SET status='succeeded', progress=100%       |
+---------------------------------------------------------------------------------------+
```

---

## 3. Transactional Outbox & Job Lifecycle FSM

### 3.1 PostgreSQL-Authoritative State Machine
Job state transitions are strictly governed by PostgreSQL transactions with atomic conditional updates:

```
                  +------------+
                  |   QUEUED   |
                  +------------+
                   /          \
  Worker Claim    /            \ Immediate Cancel
                 v              v
          +-----------+    +-----------+
          |  RUNNING  |    | CANCELLED |
          +-----------+    +-----------+
          /     |     \          ^
         /      |      \         | Cooperative Cancel
        v       |       v        | Checkpoint
   +---------+  |   +--------------------+
   |SUCCEEDED|  |   |  CANCEL_REQUESTED  |
   +---------+  |   +--------------------+
                v
        [Lease Expired]
        [Worker Crash]
                |
                v
         +------------+
         |  RETRYING  | ---> (Re-queued to Celery if attempt < max_attempts)
         +------------+
                |
          (Max attempts reached)
                v
          +------------+
          |   FAILED   |
          +------------+
```

### 3.2 Outbox Dispatcher
The `OutboxDispatcher` daemon operates an asynchronous loop:
1. Locks pending outbox events using `SELECT * FROM analytics_outbox_events WHERE published_at IS NULL ORDER BY created_at ASC LIMIT 50 FOR UPDATE SKIP LOCKED`.
2. Publishes messages to Redis broker via Celery task protocol.
3. Stamps `published_at = utc_now()` within the transaction.
4. Executes a periodic sweep discovering running jobs whose `lease_expires_at < utc_now()`. If `attempt_count < max_attempts`, transitions to `retrying` and re-dispatches; otherwise transitions to `failed` with error code `LEASE_EXPIRED`.

---

## 4. Operation Registry (Allowlist)

No dynamic Python `eval()` or unvetted scripts are permitted. All capabilities reside in pre-registered, strongly typed operation handlers in `app/analytics/operations/`:

| Operation ID | Category | Description | Primary Engine |
| :--- | :--- | :--- | :--- |
| `profile_dataset` | descriptive | Structural shape, column types, null %, cardinality, min/max/mean | Polars |
| `describe_columns` | descriptive | Five-number summary, mean, std, variance across numeric columns | Polars / NumPy |
| `group_by_aggregate` | aggregation | Group by categorical dimensions, evaluate sum, mean, count, min, max | Polars |
| `filter_dataset` | transformation | Row filtering via compound comparison and null predicates | Polars |
| `correlation_matrix` | statistics | Pairwise Pearson and Spearman correlation matrices with heatmap data | NumPy / Polars |
| `data_quality_profile`| quality | Missing value ratios, duplicate row detection, anomaly alerts | Polars / NumPy |

---

## 5. Input Snapshot & Storage Strategy

- **Immutability:** When a job is claimed, a point-in-time snapshot of the source sheet cells is compiled into an immutable Apache Parquet file stored at `uploads/analytics/snapshots/{organization_id}/{job_id}.parquet`.
- **Integrity Validation:** A SHA-256 hash is computed and stored on the job record. Before execution, the worker recalculates and compares the hash.
- **Concurrent Edits:** Subsequent edits made to the source spreadsheet sheet do not interfere with running analytics.
- **Large Result Offloading:** Result payloads exceeding 500 rows are offloaded to private Parquet artifacts at `uploads/analytics/results/{organization_id}/{result_id}.parquet`.

---

## 6. Spreadsheet Integration: Publishing Results

Analytical outputs can be published directly back into the user's spreadsheet workbook:
- Endpoint: `POST /api/v1/analytics/results/{result_id}/publish-to-sheet`
- Behavior:
  1. Verifies caller holds `spreadsheets.create` and `analytics.export`.
  2. Creates a new sheet in the target workbook (`SheetCreateInput`).
  3. Formats tabular analytical results (headers in Row 0, values in subsequent rows).
  4. Dispatches atomic batch cell update `update_cells_batch()`.
  5. Returns target sheet ID and rows written.

---

## 7. Security & Tenant Isolation

- **Composite Foreign Keys:** All analytics tables enforce PostgreSQL-level composite foreign keys tying references to `organization_id`:
  - `(workspace_id, organization_id) REFERENCES workspaces(id, organization_id)`
  - `(source_workbook_id, organization_id) REFERENCES workbooks(id, organization_id)`
  - `(source_sheet_id, organization_id) REFERENCES sheets(id, organization_id)`
- **RBAC Permissions:**
  - `analytics.read`: View jobs catalogue, list jobs, inspect job results.
  - `analytics.create`: Submit new analytical jobs.
  - `analytics.cancel`: Cancel queued/running jobs (Owner/Admin can cancel any; Member can cancel own).
  - `analytics.export`: Download Parquet result artifacts and publish results back to workbooks.
- **Resource Constraints:**
  - Max concurrent jobs per organization: 5 (default).
  - Max concurrent jobs per user: 2 (default).
  - Execution timeout: 300 seconds.
  - Max source rows: 500,000.
  - Worker memory ceiling: 1024MB.

