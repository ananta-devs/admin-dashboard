# VIBE — PHASE 9 DISCOVERY & ARCHITECTURAL REVIEW REPORT
## Enterprise Admin & Data Intelligence Platform

**Document Version:** 2.0.0 (Hardened & Refined)  
**Inspection Date:** November 2026  
**Current Status:** Phases 1–8 Formally Accepted  
**Alembic Migration Head:** `e82f1b4a9c3d`  
**Scope:** Architectural Discovery, Security Hardening & Platform Inventory  

---

## 1. Executive Summary & Assessment Methodology

A comprehensive architectural and source-code inspection of the **VIBE** enterprise platform was performed across all backend modules, frontend components, database migrations, security boundaries, and operational runbooks.

VIBE has achieved remarkable engineering rigor across its initial eight phases:
- Complete multi-tenant relational isolation with composite foreign keys.
- Opaque server-managed session authentication with Argon2id, RFC 6238 TOTP MFA, and privacy-preserving sliding-window Redis rate limiting.
- High-performance hybrid spreadsheet storage with a deterministic AST formula parser, DAG recalculation engine, and 50,000-row 60fps virtualized data grid.
- Vectorized analytics engine powered by Polars 2.1, Pandas 3.1, and Apache Arrow Parquet snapshots, dispatched via a transactional outbox to Celery worker pools.
- Governed OCI Python sandbox execution with direct PID 1 isolation, structured Docker Engine API absence verification, and cryptographic 7-point image provenance attestation (`PYTHON_SANDBOX_ENABLED=false` default strictly preserved).

However, the platform currently exhibits a functional gap between its **analytical compute engine** and its **executive visual presentation and on-demand reporting layers**:
- `/dashboards` currently relies on brittle, hand-crafted inline SVG geometry (`<path>`, `<rect>`) without a professional charting engine, interactive drilldowns, cross-widget filtering, or zoom/pan capabilities.
- `/reports` is a dead placeholder marked `coming_soon` with zero database models, zero PDF rendering services, and no report artifact storage.
- Root `/` renders the Phase 2 UI component test harness rather than an operational executive command center.

---

## 2. Confirmed Phase 1–8 Architecture & Capability Inventory

### 2.1 Backend Architecture (FastAPI & Async SQLAlchemy)
- **Application Core (`backend/app/main.py`):**
  - FastAPI 0.141 with asynchronous lifespan management validating cryptographic key separation at startup (`SESSION_SECRET_KEY`, `ENCRYPTION_MASTER_KEY`, `RATE_LIMIT_HMAC_KEY`).
  - Strict CORS middleware with credential awareness, explicit allowed origins, and strict prohibition of wildcard origins in production.
  - Exception handler hierarchy mapping domain exceptions (`EntityNotFoundError`, `ConflictError`, `CrossTenantAccessError`, `RateLimitExceededError`, `CsrfError`) to standard HTTP responses without leaking internal stack traces.
- **Authentication & Security (`backend/app/core/security.py`, `backend/app/services/session.py`):**
  - Argon2id password hashing with constant-time dummy verification for non-existent users.
  - 256-bit cryptographically secure opaque session tokens persisted as SHA-256 digests in PostgreSQL and cached in Redis DB 1.
  - TOTP MFA with AES-256-GCM encrypted secrets, replay protection via atomic CAS compare-and-swap on `last_accepted_step`, and single-use recovery codes.
  - Sliding-window Redis rate limiting using HMAC-SHA256 privacy-preserving key hashing; fail-closed on security-sensitive paths.
- **Multi-Tenant Relational Foundation (`backend/app/models/`):**
  - 18 core tables organized under `Organization` root.
  - Tenant integrity enforced via composite foreign keys `(id, organization_id)` across workbooks, sheets, analytics jobs, sync mappings, dashboards, and scripts.
  - Asynchronous Unit of Work (`app/db/transaction.py`) with explicit depth tracking (`session.info["_explicit_tx_depth"]`) and savepoint boundary management.
- **Spreadsheet Subsystem (`backend/app/formula/`, `backend/app/services/spreadsheet.py`):**
  - Hybrid storage model (ADR 001): Dense JSONB row storage (`cells.<col>.{v,c,f,t,err,ver}`) combined with relational column metadata and indexed dependency edges (`sheet_cell_dependencies`).
  - Pure-Python recursive descent AST parser and DAG topological sort engine supporting arithmetic, comparisons, SUM, AVERAGE, COUNT, MIN, MAX, and short-circuit IF.
  - Optimistic concurrency control via per-cell integer revisions; mutating operations acquire row-level locks on the parent `Sheet`.
  - Reversible multi-cell batch undo/redo (`SheetCellHistory`) with revision conflict rejection and bounded retention (100 batches).
- **Analytics Subsystem (`backend/app/analytics/`, `backend/app/tasks/`):**
  - Allowlisted `OperationRegistry` prohibiting arbitrary code execution; 6 registered routines (`profile_dataset`, `describe_columns`, `group_by_aggregate`, `filter_dataset`, `correlation_matrix`, `data_quality_profile`).
  - Transactional Outbox pattern (`analytics_outbox_events`) polled by `OutboxDispatcher` daemon with `SELECT ... FOR UPDATE SKIP LOCKED`.
  - Celery 5.6 worker pool on Redis DB 1; cooperative cancellation checkpoints and lease renewals.
  - Immutable Parquet snapshot extraction and result artifact persistence with SHA-256 verification.
  - Tabular result back-publishing to sheets (`POST /api/v1/analytics/results/{id}/publish-to-sheet`).
  - Quota authority enforcing storage and row ceilings in PostgreSQL before job admission.
- **Integrations & Governed Sandbox (`backend/app/services/sync.py`, `backend/app/analytics/python_sandbox.py`):**
  - Google Sheets OAuth 2.0 integration with encrypted token vault and bidirectional sync mapping.
  - Governed sandbox runner with direct PID 1 execution, complete capability drop, network namespace isolation, tmpfs, structured Docker Engine API absence verification, and 7-point OCI provenance attestation. (Default: `PYTHON_SANDBOX_ENABLED=false`).
  - Dashboard service (`app/services/dashboard.py`) executing Polars aggregation queries against sheets and Parquet artifacts.

### 2.2 Frontend Architecture (Next.js 16 & React 19)
- **Monorepo Structure (`frontend/src/`):**
  - Next.js 16.4.3 App Router with Turbopack, Tailwind CSS 4, and TypeScript strict mode.
  - Design system with 15 accessible Radix UI primitives, dark/light theme switching, and WCAG 2.2 AA contrast compliance.
  - Command palette (`cmdk`) bound to `Ctrl+K`.
  - Virtualized spreadsheet grid (`@tanstack/react-virtual`) handling 50,000 rows at 60 FPS with full keyboard navigation and formula bar synchronization.
  - Administrative management pages for Users (`/users`), Roles (`/roles`), Audit Ledger (`/audit`), and Settings (`/settings`).
  - Analytics interface (`/analytics`) with job submission, stage progress tracking, and result visualizers.
  - Prototype interfaces for Dashboards (`/dashboards`) and Integrations (`/integrations`).

### 2.3 Database Migration Status
- Current Alembic migration head: `e82f1b4a9c3d` (`create_phase8_integrations_dashboards_scripts`).
- Zero pending migrations. All 13 migrations verified bidirectional.

---

## 3. Scope Boundaries: Phase 9 vs. Phase 10

To ensure Phase 9 remains tightly governed and avoids uncontrolled scope creep, explicit product and architectural boundaries are defined:

```
+--------------------------------------------------------------------------------------------------+
|                                    PHASE SCOPE DELIMITATION                                      |
+----------------------------------------------------+---------------------------------------------+
| INCLUDED IN PHASE 9                                | EXPLICITLY DEFERRED TO PHASE 10 / BEYOND    |
+----------------------------------------------------+---------------------------------------------+
| 1. Interactive ECharts Visualization Engine        | 1. Scheduled Recurring Report Generation    |
| 2. BI Dashboard Studio with Cross-Filtering        | 2. Automated Email / Webhook Distribution   |
| 3. Fullscreen Boardroom Presentation Mode          | 3. Celery Beat Periodic Scheduling Daemon   |
| 4. Executive Operational Command Center (/)        | 4. Threshold & Metric Alert Notifications   |
| 5. On-Demand Report Builder Studio (/reports)      | 5. Automated Multi-Tenant Ingestion Connectors|
| 6. Asynchronous Server-Side PDF Generation         | 6. Real-Time Collaborative WebSockets       |
| 7. Authorized Artifact Download with Signed URLs   | 7. External Database Connectors (Snowflake) |
+----------------------------------------------------+---------------------------------------------+
```

---

## 4. Architectural Hardening Discoveries

### 4.1 PDF Rendering Security & SSRF Protection
- **Vulnerability Surface:** Rendering HTML/CSS to PDF via a headless browser or renderer introduces server-side request forgery (SSRF) and local file inclusion (LFI) risks if user-controlled markdown or HTML contains `<iframe src="file:///etc/passwd">`, `<img src="http://169.254.169.254/latest/meta-data/">`, or malicious `<script>` tags.
- **Renderer Process Isolation:** The PDF compilation process must run under a dedicated, unprivileged non-root identity with total network denial (`--network none` in container or seccomp socket restrictions). It must never inherit backend database connection pools or application secrets.
- **Server-Side SVG Elimination of Client JS:** Apache ECharts supports server-side SVG rendering (`echarts.init(null, { renderer: 'svg', ssr: true })`). By rendering charts to pure, static XML SVG strings server-side, the PDF rendering engine (whether WeasyPrint or headless Chromium with `--disable-javascript`) requires zero browser JavaScript execution. This completely neutralizes client-side script execution attacks during document compilation.
- **Asset Hermeticity:** Zero external network asset fetches. All fonts (Inter, Roboto Mono) and standard corporate logo SVG assets are bundled locally in the container filesystem. Remote `@import` and `url()` directives are stripped during HTML sanitization.

### 4.2 Multi-Worker Artifact Storage Abstraction
- **Defect of Local `uploads/` in Clustered Environments:** In multi-node production deployments, storing PDF artifacts in a local container directory (`./uploads/reports`) breaks when request workers and download workers reside on distinct hosts.
- **Storage Service Architecture:** Artifacts must be managed through an abstract `StorageBackend` protocol (reusing and extending `AnalyticsStorageService` / `FileStorageService`). In local development, the provider maps to a local directory; in production, it maps to an S3-compatible object store (AWS S3, MinIO, Cloudflare R2) issuing short-lived HMAC-signed download URLs.
- **Atomic Artifact Promotion:** PDF generation renders to a temporary quarantine file (`.tmp`). Once rendering completes, the worker calculates the SHA-256 digest and file size, confirms quota headroom in PostgreSQL, atomically moves/uploads the file to its final immutable path, and commits the database record.

### 4.3 Immutable Reporting & Reproducibility
- **Snapshot Integrity:** A report run must be an immutable point-in-time snapshot. Editing a report definition must never alter the metadata, text, or query parameters of a previously generated run.
- **Data Revision Pinning:** At run trigger time, the worker captures the sheet cell versions (`last_checkpoint_vibe_version`) or Parquet snapshot hash. If a user inspects a historical report, the exact data state at generation time is deterministically referenced.
- **Idempotency & Crash Recovery:** Reusing the proven Phase 7 Transactional Outbox pattern (`report_outbox_events`) prevents dropped tasks. Unique constraints on `(organization_id, idempotency_key)` block accidental double-submissions. Worker crashes are detected via heartbeat leases (`lease_expires_at`), with automatic lease reclamation by the outbox dispatcher daemon.

### 4.4 Multi-Tenant Dashboard Filter Security
- **Injection Vulnerability Surface:** Allowing arbitrary client-specified filter expressions against Polars or SQL exposes the backend to code injection and denial-of-service (e.g. unindexed wildcard scans).
- **Typed Parameterized Allowlist:** All dashboard filters must conform to a strict Pydantic schema:
  - Allowed operators: `eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `in`, `between`.
  - Date and timezone normalization: All timestamps parsed to ISO 8601 UTC.
  - Parameterized mapping: Operators map deterministically to compiled Polars expressions (`pl.col(dim).is_in(...)`), eliminating string concatenation.
- **Strict Authorization Boundaries:** Workspace context passed in query requests is strictly cross-referenced against the caller's validated `OrganizationMembership`. Requesting data from a workspace outside the user's membership returns HTTP 404 (preventing resource enumeration).

---

## 5. Phase 9 Opportunity Recommendation Summary

The direction **Enterprise BI, Interactive Dashboards & On-Demand Reporting** is formally certified as the optimal Phase 9 scope. It directly bridges VIBE's back-end horsepower with executive utility, preserves all Phase 1–8 hardened foundations, and establishes the exact visual and document foundation required before Phase 10 introduces automated recurring scheduling.
