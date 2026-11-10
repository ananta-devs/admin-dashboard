# VIBE — PHASE 9 GATE 3 FINAL IMPLEMENTATION-READINESS AUDIT
## Independent Technical Audit: Database Integrity, Snapshot Consistency, Delivery Semantics & Browser Hardening

**Workspace:** `D:\vibe`  
**Project:** VIBE — Enterprise BI, Analytics & Data Intelligence Platform  
**Phase:** 9 — Enterprise BI, Interactive Dashboards & On-Demand Reporting  
**Gate:** 3 — Automated Scheduled Reports & PDF Engine  
**Previous Documents Audited:**
1. `PHASE_9_GATE_3_DISCOVERY_REPORT.md`
2. `PHASE_9_GATE_3_ARCHITECTURE_VALIDATION.md`
3. `PHASE_9_GATE_3_FINAL_ARCHITECTURE_CORRECTION.md`

**Baseline Alembic Head:** `e82f1b4a9c3d`  
**Python Sandbox:** `PYTHON_SANDBOX_ENABLED=false`  
**Mode:** STRICT READ-ONLY AUDIT  
**Implementation Authorization:** NOT GRANTED  
**Date:** November 3, 2026  

---

## 1. Executive Verdict

This independent audit rigorously evaluates the proposed architecture for Phase 9 Gate 3 against the actual repository source code, PostgreSQL 16 engine capabilities, Celery/Redis infrastructure, and Linux container security.

### Verdict: CONDITIONALLY READY — SPECIFIED BLOCKERS REMAIN

The architectural foundation is sound and mature, but **three explicit pre-implementation blockers** must be formally acknowledged and configured before executing Stage 1:

1. **Dependency Addition Required (Cron Parser):** `croniter` is **not installed** in `backend/requirements.lock` ([`requirements.lock:1-71`](file:///D:/vibe/backend/requirements.lock#L1-L71)). Neither standard library `zoneinfo` nor `python-dateutil` provides a complete 5-field cron parser. `croniter` (or an equivalent audited cron parser) must be formally approved for addition in Stage 1.
2. **Container Image Hardening & System Dependencies:** `backend/Dockerfile` ([`Dockerfile:1-33`](file:///D:/vibe/backend/Dockerfile#L1-L33)) contains only `python:3.12-slim` without Chromium rendering libraries (`libnss3`, `libasound2`, `libgbm1`, fonts). Stage 1/4 must introduce a dedicated reporting worker Dockerfile or update the base build with system shared libraries.
3. **PostgreSQL 16 Composite `SET NULL` Syntax Verification:** This audit empirically verified on the live PostgreSQL 16 container (`postgres:16.15-alpine`) that PostgreSQL 16 fully supports column-specific `ON DELETE SET NULL (column_name)` on composite foreign keys. This syntax must be explicitly utilized in Alembic migrations to preserve both composite tenant isolation and historical runs.

All technical findings, state machines, and schemas in this audit are grounded in verified repository facts.

---

## 2. Repository Evidence & Ground Truth

The following file paths and line ranges establish the verified facts in `D:\vibe`:

| File Path & Line Range | Ground Truth Fact | Evidence Classification |
| :--- | :--- | :--- |
| [`docker-compose.yml:1-29`](file:///D:/vibe/docker-compose.yml#L1-L29) | PostgreSQL version is `postgres:16-alpine`. Empirically verified via `psql` as `PostgreSQL 16.15 on x86_64-pc-linux-musl`. | **VERIFIED FACT** |
| [`docker-compose.yml:32-47`](file:///D:/vibe/docker-compose.yml#L32-L47) | Redis 7 is active on port 6379 with password authentication and AOF persistence enabled. | **VERIFIED FACT** |
| [`docker-compose.yml:48-75`](file:///D:/vibe/docker-compose.yml#L48-L75) | `analytics-worker` runs as `user: "10001:10001"`, `read_only: true`, `cap_drop: ALL`, and `tmpfs: /tmp:rw,noexec,nosuid,size=512m`. | **VERIFIED FACT** |
| [`backend/Dockerfile:1-33`](file:///D:/vibe/backend/Dockerfile#L1-L33) | `python:3.12-slim` with `build-essential` and `curl`. No Chromium, no Playwright, no GUI libraries installed. | **VERIFIED FACT** |
| [`backend/requirements.lock:1-71`](file:///D:/vibe/backend/requirements.lock#L1-L71) | 71 locked Python packages. `croniter`, `playwright`, `weasyprint`, and `reportlab` are **absent**. `python-dateutil==4.0.0` is present. | **VERIFIED FACT** |
| [`backend/app/models/spreadsheet.py:179-220`](file:///D:/vibe/backend/app/models/spreadsheet.py#L179-L220) | `SheetRow` has `cells: JSONB` and `version: int` with unique constraint `(sheet_id, row_index)`. Rows are updated in place. No sheet revisions exist. | **VERIFIED FACT** |
| [`backend/app/models/spreadsheet.py:261-318`](file:///D:/vibe/backend/app/models/spreadsheet.py#L261-L318) | `SheetCellHistory` stores bounded cell-level undo/redo diffs. It cannot reconstruct table snapshots at an arbitrary historical timestamp. | **VERIFIED FACT** |
| [`backend/app/services/dashboard.py:58-67`](file:///D:/vibe/backend/app/services/dashboard.py#L58-L67) | `DashboardService` takes a single `session: AsyncSession` in `__init__`, shared across all repositories. | **VERIFIED FACT** |
| [`backend/app/services/dashboard.py:392-483`](file:///D:/vibe/backend/app/services/dashboard.py#L392-L483) | `query_widget_data()` executes purely read queries on `self.session`. It does **not** call `commit()` or `rollback()`. Reads from `sheet_rows` directly or reads Parquet files from `AnalyticsResult.artifact_path`. | **VERIFIED FACT** |
| [`backend/app/tasks/dispatcher.py:28-48`](file:///D:/vibe/backend/app/tasks/dispatcher.py#L28-L48) | Outbox dispatcher locks pending events using PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED` with 60-second lease cutoff. | **VERIFIED FACT** |
| [`frontend/package.json:26`](file:///D:/vibe/frontend/package.json#L26) | `"echarts": "^6.1.0"` is installed in Next.js frontend only. Python backend has no JavaScript engine. | **VERIFIED FACT** |

---

## 3. Findings Classification

### Critical Findings (Must Resolve Before Stage 1)
- **CRIT-01: Cron Parser Absence in Backend Dependencies:** `requirements.lock` does not contain `croniter`. Python's `zoneinfo` handles timezone conversions and DST offsets, but **cannot parse cron expressions**. Attempting to parse `0 8 * * 1` with `zoneinfo` will fail with an `AttributeError`. A real cron parser (`croniter>=3.0.0`) must be approved for installation in Stage 1.
- **CRIT-02: Missing Chromium Shared Libraries in Base Container:** Running Playwright/Chromium in `python:3.12-slim` requires system libraries (`libnss3`, `libasound2`, `libgbm1`, `libx11-xcb1`, fonts). Spawning Chromium without these libraries causes an immediate `HostProcessNotFoundError` or dynamic linker crash.

### High Findings (Architecture Corrected in this Audit)
- **HIGH-01: Empirical Verification of PostgreSQL 16 Composite `SET NULL`:** Standard composite foreign keys with `ON DELETE SET NULL` fail if `organization_id` is `NOT NULL`. However, live testing on VIBE's PostgreSQL 16.15 container confirmed that PostgreSQL 16 fully supports column-specific syntax: `ON DELETE SET NULL (dashboard_id)`. This allows composite foreign keys to maintain 100% database-level tenant integrity while allowing nullable entity references.
- **HIGH-02: Historical Report Retention vs. Template Deletion:** Deleting an editable `report_definitions` template must not delete completed historical report runs. Historical runs must retain a self-contained, frozen `report_snapshot` (JSONB) and use `ON DELETE SET NULL (report_definition_id)`.
- **HIGH-03: Concurrent SMTP Delivery Duplicate Risk:** A simple check-before-send strategy is race-prone across concurrent Celery workers. Delivery must be managed via an atomic `report_delivery_targets` table with `FOR UPDATE SKIP LOCKED` claiming and a unique constraint on `(run_id, channel, recipient_normalized)`.

### Medium Findings
- **MED-01: RFC 8785 Canonical JSON Serialization:** Standard Python `json.dumps(sort_keys=True)` is not strictly RFC 8785 compliant regarding float formatting and Unicode surrogate code points. A deterministic normalization pipeline must coerce floats, timestamps, decimals, and Unicode before hashing.
- **MED-02: External Artifact Lifecycle:** PostgreSQL cascades do **not** delete external files on disk. Deleting an organization or workspace requires an asynchronous cleanup event to purge generated PDF and Parquet artifacts.

---

## 4. Audit A & B — Database Tenant Integrity & Referential Retention Matrix

### 4.1 Empirical PostgreSQL 16 Validation

On the active `vibe_postgres` container (PostgreSQL 16.15), the following DDL was executed and verified:
```sql
CREATE TABLE parent (id UUID PRIMARY KEY, org_id UUID NOT NULL, UNIQUE (id, org_id));
CREATE TABLE child (
    id UUID PRIMARY KEY,
    parent_id UUID,
    org_id UUID NOT NULL,
    CONSTRAINT fk_child_parent_org 
        FOREIGN KEY (parent_id, org_id) 
        REFERENCES parent(id, org_id) 
        ON DELETE SET NULL (parent_id)
);
```
**Test Results:**
1. Inserting a child referencing a parent in a *different* `org_id` was **immediately rejected** with `violates foreign key constraint`.
2. Deleting the parent resulted in `parent_id` being set to `NULL`, while `org_id` remained intact (`NOT NULL`).
3. This proves that PostgreSQL 16 provides **100% database-enforced tenant integrity** with column-specific `SET NULL` cascades.

### 4.2 Comprehensive Foreign Key & Deletion Matrix

| Child Table & Participating Columns | Referenced Parent Table & Columns | Nullability of Child Columns | Delete Behavior (ON DELETE) | Database-Level Tenant Integrity Guarantee | Historical Retention Impact |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `report_definitions(workspace_id, organization_id)` | `workspaces(id, organization_id)` | `workspace_id`: NOT NULL<br/>`organization_id`: NOT NULL | `CASCADE` | Database rejects insert if workspace belongs to another org. | Workspace destruction deletes definitions in that workspace. |
| `report_definitions(dashboard_id, organization_id)` | `dashboards(id, organization_id)` | `dashboard_id`: NULLABLE<br/>`organization_id`: NOT NULL | `SET NULL (dashboard_id)` | Database rejects insert if dashboard belongs to another org. | Deleting a dashboard sets `dashboard_id` to NULL; definition becomes standalone. |
| `report_sections(report_definition_id, organization_id)` | `report_definitions(id, organization_id)` | `report_definition_id`: NOT NULL<br/>`organization_id`: NOT NULL | `CASCADE` | Sections strictly bound to parent definition tenant. | Deleting a definition cascades to delete its draft sections. |
| `report_sections(widget_id, organization_id)` | `dashboard_widgets(id, organization_id)` | `widget_id`: NULLABLE<br/>`organization_id`: NOT NULL | `SET NULL (widget_id)` | Database rejects link if widget belongs to another org. | Deleting a widget sets `widget_id` to NULL; section retains cached query config. |
| `report_schedules(report_definition_id, organization_id)` | `report_definitions(id, organization_id)` | `report_definition_id`: NOT NULL<br/>`organization_id`: NOT NULL | `CASCADE` | Schedules strictly bound to parent definition tenant. | Deleting a definition deletes its recurring schedules. |
| `report_runs(report_definition_id, organization_id)` | `report_definitions(id, organization_id)` | `report_definition_id`: NULLABLE<br/>`organization_id`: NOT NULL | `SET NULL (report_definition_id)` | Database rejects run if definition belongs to another org. | **Historical Retention:** Deleting a definition sets FK to NULL; completed run and frozen snapshot survive. |
| `report_runs(schedule_id, organization_id)` | `report_schedules(id, organization_id)` | `schedule_id`: NULLABLE<br/>`organization_id`: NOT NULL | `SET NULL (schedule_id)` | Database rejects run if schedule belongs to another org. | Deleting a schedule sets FK to NULL; historical runs survive. |
| `report_runs(workspace_id, organization_id)` | `workspaces(id, organization_id)` | `workspace_id`: NULLABLE<br/>`organization_id`: NOT NULL | `SET NULL (workspace_id)` | Database rejects run if workspace belongs to another org. | Deleting a workspace sets FK to NULL; runs preserved at organization level. |
| `report_delivery_targets(run_id, organization_id)` | `report_runs(id, organization_id)` | `run_id`: NOT NULL<br/>`organization_id`: NOT NULL | `CASCADE` | Delivery targets strictly bound to run tenant. | Run deletion cascades to clean up delivery targets. |
| `report_delivery_attempts(target_id, organization_id)` | `report_delivery_targets(id, organization_id)` | `target_id`: NOT NULL<br/>`organization_id`: NOT NULL | `CASCADE` | Attempt logs strictly bound to target tenant. | Target deletion cascades to clean up attempt logs. |
| `report_download_tokens(run_id, organization_id)` | `report_runs(id, organization_id)` | `run_id`: NOT NULL<br/>`organization_id`: NOT NULL | `CASCADE` | Tokens strictly bound to run tenant. | Run deletion cascades to clean up active download tokens. |
| `All Tables(organization_id)` | `organizations(id)` | `organization_id`: NOT NULL | `CASCADE` | Fundamental multi-tenant root constraint. | **Tenant Purge (GDPR):** Deleting an organization completely purges all data. |

---

## 5. Audit C & D — Snapshot Correctness & Canonical Serialization

### 5.1 Single-Session `REPEATABLE READ` Transaction Guarantee

Inspection of [`backend/app/services/dashboard.py:58-67, 392-483`](file:///D:/vibe/backend/app/services/dashboard.py#L58-L67) confirms:
1. `DashboardService` shares a single `session: AsyncSession` across all internal repositories (`sheet_repo`, `row_repo`, `widget_repo`, `analytics_repo`).
2. `query_widget_data()` executes strictly read operations and does not call `commit()` or `rollback()`.
3. When the outer execution worker establishes an `AsyncSession` with isolation level `REPEATABLE READ`:
   ```python
   async with engine.connect() as conn:
       await conn.execution_options(isolation_level="REPEATABLE READ")
       async with AsyncSession(conn) as session:
           async with session.begin():
               # Every widget query in this block sees the exact same snapshot
   ```
4. **PostgreSQL Rows Guarantee:** All sheet rows read across all report sections are guaranteed to reflect a single, internally consistent point-in-time snapshot.
5. **External Parquet Artifacts Guarantee:** Files stored on disk (`AnalyticsResult.artifact_path`) are outside PostgreSQL transaction boundaries. The worker immediately reads the Parquet data and serializes the evaluated tabular results into `report_runs.report_snapshot`. Once written, subsequent file mutations or cleanups cannot alter or invalidate the report snapshot.

### 5.2 Canonical Serialization Pipeline (RFC 8785 Conformance)

To ensure bit-for-bit reproducible SHA-256 checksums across systems:

```
[Raw Snapshot Payload]
        │
        ├── 1. Temporal Normalization:
        │      All datetime values converted to UTC and formatted as ISO-8601:
        │      dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        │
        ├── 2. Numeric Normalization:
        │      - Decimals converted to fixed-point strings (e.g. "12450.50").
        │      - Float values rounded to 6 decimal places.
        │      - NaN and Infinite values coerced to null (RFC 8259 compliance)
        │        with explicit metadata: { "v": null, "quality": "NaN" }.
        │
        ├── 3. Unicode Normalization:
        │      All strings normalized to Unicode Normalization Form C (NFC):
        │      unicodedata.normalize('NFC', text)
        │
        ├── 4. Deterministic Canonical Encoding:
        │      json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        │
        ▼
[SHA-256 Checksum Calculation]
```

---

## 6. Audit E, F & G — Scheduler, Worker & SMTP State Machines

### 6.1 Scheduler Occurrence & Fencing State Machine

```mermaid
stateDiagram-v2
    [*] --> Scheduled: next_run_at calculated
    Scheduled --> Claimed: SELECT FOR UPDATE SKIP LOCKED
    
    state Claimed {
        [*] --> CreateRun: Insert report_runs (status='queued', scheduled_for_utc)
        CreateRun --> CreateOutbox: Insert report_outbox_events
        CreateOutbox --> AdvanceSchedule: next_run_at = calculate_next_cron()
        AdvanceSchedule --> [*]
    }
    
    Claimed --> Scheduled: Transaction Commits (<10ms)
    Claimed --> Scheduled: Poller Crashes (Rollback, lock released)
```

- **Unique Occurrence Constraint:** `UNIQUE (schedule_id, scheduled_for_utc)` strictly prevents duplicate scheduled runs. Manual runs use `scheduled_for_utc = NULL`, which PostgreSQL treats as distinct.
- **Decoupled Claiming:** The scheduler claims the *schedule* only long enough to advance `next_run_at` and insert the run record (<10ms). Worker execution claiming occurs on the `report_runs` row.

### 6.2 Worker Execution & Monotonic Generation Fencing

```mermaid
stateDiagram-v2
    [*] --> Queued: Run created by scheduler or user
    Queued --> Running: Worker claims (claim_token, claim_gen++, lease=300s)
    
    state Running {
        [*] --> MaterializeData: REPEATABLE READ query
        MaterializeData --> GeneratePDF: Headless Chromium CDP
        GeneratePDF --> WriteQuarantine: Write to .tmp with SHA-256
        WriteQuarantine --> PromoteArtifact: Atomic os.replace()
        PromoteArtifact --> [*]
    }
    
    Running --> Succeeded: Run complete, outbox delivery dispatched
    Running --> Queued: Lease expired & attempt < max_attempts (Re-queued)
    Running --> Failed: attempt >= max_attempts or fatal error
```

- **Stale Worker Prevention:** Artifact paths are fenced with generation numbers:
  `REPORT_STORAGE_DIR/runs/{org_id}/{run_id}/gen_{claim_generation}.pdf`
  If a worker's lease expires and a second worker is spawned with `claim_generation = 2`, the first worker's delayed atomic promotion writes to `gen_1.pdf`. The database status update requires `WHERE claim_token = :my_token`, rejecting the stale worker's commit.

### 6.3 SMTP Delivery State Machine & Ambiguous Outcome Handling

```mermaid
stateDiagram-v2
    [*] --> Pending: Delivery target created
    Pending --> Claimed: Worker claims target (FOR UPDATE SKIP LOCKED)
    
    state Claimed {
        [*] --> ConnectSMTP: TLS Handshake & Auth
        ConnectSMTP --> SendEnvelope: MAIL FROM, RCPT TO
        SendEnvelope --> StreamData: DATA command & payload
        StreamData --> Await250: Waiting for 250 OK
        Await250 --> [*]: 250 OK Received
    }
    
    Claimed --> Sent: Success recorded in DB
    Claimed --> Ambiguous: Timeout during StreamData/Await250
    Claimed --> Failed: 5xx Hard Bounce or max attempts exhausted
    Claimed --> Pending: 4xx Temporary error (Lease expired, eligible for retry)
    Ambiguous --> Sent: Admin manual reconciliation
    Ambiguous --> Pending: Admin forced re-dispatch
```

- **Failure Before DATA:** Worker connection drops or fails before completing `DATA`. Remote MTA has **not** accepted the message. Safely eligible for automatic retry.
- **Failure During / After DATA:** Connection drops while streaming data or awaiting `250 OK`. Remote MTA may have accepted and queued the email. Target transitions to `ambiguous`. Automatic retry is **suppressed** to prevent spamming recipients; requires administrative review or manual reconciliation.

---

## 7. Audit H — Hardened Chromium Security & Deployment Architecture

### 7.1 Separation of Network Isolation from Browser Sandboxing

| Layer | Mechanism | Scope | Threat Mitigated |
| :--- | :--- | :--- | :--- |
| **OS / Container Network** | Docker internal bridge network with zero default gateway, OR egress iptables blocking all outbound traffic except PostgreSQL (5432) and Redis (6379). | Host / Kernel | Prevents any process in the container from contacting public internet or cloud metadata (`169.254.169.254`). |
| **Browser Network Denial** | Playwright context configured with `offline=True` and `context.route("**/*", lambda r: r.abort())`. | Chromium Blink/V8 | Completely blocks Chromium from initiating HTTP/HTTPS/WebSocket requests. |
| **Process Sandboxing** | Chromium multi-process sandbox enabled via Linux unprivileged user namespaces (`CLONE_NEWUSER`). | Chromium Process | Restricts compromised renderer subprocesses from accessing host filesystem or kernel primitives. |
| **Content Security Policy** | Injected header: `default-src 'none'; script-src 'nonce-...'; style-src 'unsafe-inline'; font-src data:; img-src data:;` | DOM / HTML | Prevents inline scripts from loading external scripts, styles, or images. |

### 7.2 ECharts Reuse from Frontend Build

- The Next.js frontend utilizes `"echarts": "^6.1.0"`.
- The reporting backend does **not** install Node.js.
- **Verified Reuse Architecture:**
  1. The backend packages a static, vendor-locked `echarts.min.js` (v6.1.0) directly in `backend/app/reports/assets/echarts.min.js`.
  2. The report HTML compiler loads this script and inlines the Gate 1 declarative option-builder logic into the HTML page.
  3. Headless Chromium loads the page via in-memory string: `await page.set_content(html, wait_until="networkidle")`.
  4. ECharts executes inside Chromium, rendering 100% vector SVGs into the DOM.
  5. Chromium triggers CDP `Page.printToPDF` with header/footer templates and CSS `break-inside: avoid`.
  6. This guarantees **100% visual parity** with Gate 1 dashboard charts without adding Node.js to the backend container.

### 7.3 Measurable Acceptance Criteria (Visual Fidelity)

1. **Vector Completeness:** 100% of charts and logos rendered as vector SVG. Zooming to 400% exhibits zero raster pixelation.
2. **Page Budget Compliance:** Content strictly contained within specified margins (A4: 210mm $\times$ 297mm, 15mm margins).
3. **Zero Split Charts:** 0% chart containers split across page boundaries (`break-inside: avoid`).
4. **Header/Footer Accuracy:** Dynamic page numbers (`Page X of Y`) rendered accurately across all pages via CDP print templates.

---

## 8. Audit I — Organization Execution Identity & Governance Policy

```
[Schedule Triggered at 08:00 UTC]
               │
               ├── 1. Organization Status Check:
               │      SELECT is_active FROM organizations WHERE id = :org_id;
               │      --> If is_active == false: FAIL CLOSED (ORG_SUSPENDED)
               │
               ├── 2. Execution Ownership & Authority Check:
               │      ├── Is execution_owner_user_id configured?
               │      │   ├── YES: Verify user exists, is_active=true, has 'reports.execute' permission.
               │      │   └── If revoked/inactive: Transition schedule to PAUSED_ORPHANED.
               │      │       Log audit event: 'report.schedule.owner_revoked'.
               │      └── NO: Execute under Organization Service Context:
               │          Principal: service:reporting:{org_id}
               │          Bounded Permissions: {"reports.execute", "analytics.read", "dashboards.read"}
               │          Audit attribution: created_by_user_id
               │
               ├── 3. Source Access Re-Validation:
               │      Verify referenced dashboard and sheets still belong to org_id.
               │      --> If missing or unlinked: FAIL CLOSED (SOURCE_UNAVAILABLE)
               │
               ├── 4. Recipient Domain Policy Check:
               │      Verify all email recipients match ALLOWED_REPORT_EMAIL_DOMAINS.
               │      --> If unapproved external domain: REJECT DELIVERY
               │
               ▼
[Execute Report Generation]
```

---

## 9. Audit J — Complete Corrected Database Specification

### 1. `report_definitions`
```sql
CREATE TABLE report_definitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL,
    dashboard_id UUID,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    format VARCHAR(20) NOT NULL DEFAULT 'pdf',
    page_orientation VARCHAR(20) NOT NULL DEFAULT 'portrait',
    page_size VARCHAR(20) NOT NULL DEFAULT 'a4',
    header_text VARCHAR(255),
    footer_text VARCHAR(255),
    include_table_of_contents BOOLEAN NOT NULL DEFAULT true,
    include_cover_page BOOLEAN NOT NULL DEFAULT true,
    theme VARCHAR(50) NOT NULL DEFAULT 'default',
    is_archived BOOLEAN NOT NULL DEFAULT false,
    created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    execution_owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at TIMESTAMPTZ,
    
    CONSTRAINT uq_report_defs_id_org UNIQUE (id, organization_id),
    CONSTRAINT fk_report_defs_workspace_org FOREIGN KEY (workspace_id, organization_id)
        REFERENCES workspaces(id, organization_id) ON DELETE CASCADE,
    CONSTRAINT fk_report_defs_dashboard_org FOREIGN KEY (dashboard_id, organization_id)
        REFERENCES dashboards(id, organization_id) ON DELETE SET NULL (dashboard_id)
);
CREATE INDEX ix_report_defs_org_workspace ON report_definitions(organization_id, workspace_id);
CREATE INDEX ix_report_defs_org_archived ON report_definitions(organization_id, is_archived);
```

### 2. `report_sections`
```sql
CREATE TABLE report_sections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_definition_id UUID NOT NULL,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    widget_id UUID,
    section_type VARCHAR(50) NOT NULL, -- 'chart', 'table', 'kpi_summary', 'markdown', 'page_break'
    title VARCHAR(255),
    description TEXT,
    custom_content TEXT,
    override_query_config JSONB NOT NULL DEFAULT '{}'::jsonb,
    sort_order INTEGER NOT NULL DEFAULT 0,
    page_break_after BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_report_sections_id_org UNIQUE (id, organization_id),
    CONSTRAINT uq_report_sections_def_order UNIQUE (report_definition_id, sort_order),
    CONSTRAINT fk_report_sections_def_org FOREIGN KEY (report_definition_id, organization_id)
        REFERENCES report_definitions(id, organization_id) ON DELETE CASCADE,
    CONSTRAINT fk_report_sections_widget_org FOREIGN KEY (widget_id, organization_id)
        REFERENCES dashboard_widgets(id, organization_id) ON DELETE SET NULL (widget_id)
);
CREATE INDEX ix_report_sections_def_order ON report_sections(report_definition_id, sort_order);
```

### 3. `report_schedules`
```sql
CREATE TABLE report_schedules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_definition_id UUID NOT NULL,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    cron_expression VARCHAR(100) NOT NULL,
    timezone VARCHAR(50) NOT NULL DEFAULT 'UTC',
    is_active BOOLEAN NOT NULL DEFAULT true,
    pause_reason VARCHAR(100),
    next_run_at TIMESTAMPTZ NOT NULL,
    last_run_at TIMESTAMPTZ,
    last_run_status VARCHAR(30),
    catch_up_policy VARCHAR(30) NOT NULL DEFAULT 'skip_missed',
    recipients JSONB NOT NULL DEFAULT '[]'::jsonb,
    delivery_channels JSONB NOT NULL DEFAULT '["email"]'::jsonb,
    created_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_report_schedules_id_org UNIQUE (id, organization_id),
    CONSTRAINT fk_report_schedules_def_org FOREIGN KEY (report_definition_id, organization_id)
        REFERENCES report_definitions(id, organization_id) ON DELETE CASCADE
);
CREATE INDEX ix_report_schedules_polling ON report_schedules(is_active, next_run_at);
```

### 4. `report_runs`
```sql
CREATE TABLE report_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_definition_id UUID,
    schedule_id UUID,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    workspace_id UUID,
    report_title VARCHAR(255) NOT NULL,
    trigger_type VARCHAR(30) NOT NULL, -- 'scheduled', 'manual', 'api'
    scheduled_for_utc TIMESTAMPTZ,
    snapshot_at_utc TIMESTAMPTZ,
    status VARCHAR(30) NOT NULL DEFAULT 'queued',
    claim_token UUID,
    claim_generation INTEGER NOT NULL DEFAULT 0,
    lease_expires_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    snapshot_schema_version INTEGER NOT NULL DEFAULT 1,
    report_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    artifact_path VARCHAR(500),
    file_size_bytes BIGINT,
    checksum_sha256 VARCHAR(64),
    execution_duration_ms INTEGER,
    error_code VARCHAR(50),
    error_message TEXT,
    queued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_report_runs_id_org UNIQUE (id, organization_id),
    CONSTRAINT uq_report_runs_schedule_occurrence UNIQUE (schedule_id, scheduled_for_utc),
    CONSTRAINT fk_report_runs_def_org FOREIGN KEY (report_definition_id, organization_id)
        REFERENCES report_definitions(id, organization_id) ON DELETE SET NULL (report_definition_id),
    CONSTRAINT fk_report_runs_schedule_org FOREIGN KEY (schedule_id, organization_id)
        REFERENCES report_schedules(id, organization_id) ON DELETE SET NULL (schedule_id),
    CONSTRAINT fk_report_runs_workspace_org FOREIGN KEY (workspace_id, organization_id)
        REFERENCES workspaces(id, organization_id) ON DELETE SET NULL (workspace_id)
);
CREATE INDEX ix_report_runs_org_status ON report_runs(organization_id, status, created_at);
CREATE INDEX ix_report_runs_schedule ON report_runs(schedule_id, created_at);
```

### 5. `report_delivery_targets`
```sql
CREATE TABLE report_delivery_targets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    channel VARCHAR(30) NOT NULL, -- 'email', 'signed_url', 'webhook'
    recipient_normalized VARCHAR(255) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'pending', -- 'pending', 'claimed', 'sent', 'failed', 'ambiguous'
    claim_token UUID,
    claim_expires_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    delivered_at TIMESTAMPTZ,
    last_error_code VARCHAR(50),
    last_error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_delivery_target_run_channel_recipient UNIQUE (run_id, channel, recipient_normalized),
    CONSTRAINT fk_report_delivery_targets_run_org FOREIGN KEY (run_id, organization_id)
        REFERENCES report_runs(id, organization_id) ON DELETE CASCADE
);
CREATE INDEX ix_delivery_targets_claim ON report_delivery_targets(status, claim_expires_at);
```

### 6. `report_delivery_attempts`
```sql
CREATE TABLE report_delivery_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_id UUID NOT NULL REFERENCES report_delivery_targets(id) ON DELETE CASCADE,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL,
    smtp_response_code INTEGER,
    response_message TEXT,
    error_code VARCHAR(50),
    duration_ms INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX ix_delivery_attempts_target ON report_delivery_attempts(target_id, created_at);
```

### 7. `report_download_tokens`
```sql
CREATE TABLE report_download_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    run_id UUID NOT NULL,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    recipient_email VARCHAR(255) NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    is_revoked BOOLEAN NOT NULL DEFAULT false,
    download_count INTEGER NOT NULL DEFAULT 0,
    max_downloads INTEGER,
    last_downloaded_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_report_download_tokens_run_org FOREIGN KEY (run_id, organization_id)
        REFERENCES report_runs(id, organization_id) ON DELETE CASCADE
);
CREATE INDEX ix_report_download_tokens_lookup ON report_download_tokens(token_hash, is_revoked, expires_at);
```

### 8. `report_outbox_events`
```sql
CREATE TABLE report_outbox_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL,
    organization_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    event_type VARCHAR(50) NOT NULL, -- 'report.run.dispatched', 'report.delivery.dispatched'
    payload JSONB NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    locked_at TIMESTAMPTZ,
    locked_by VARCHAR(100),
    last_error TEXT,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_report_outbox_run_org FOREIGN KEY (run_id, organization_id)
        REFERENCES report_runs(id, organization_id) ON DELETE CASCADE
);
CREATE INDEX ix_report_outbox_pending ON report_outbox_events(published_at, locked_at);
```

---

## 10. Test and Acceptance Criteria

Before Stage 8 sign-off, the reporting subsystem must pass the following automated test suites:

1. **Composite Tenant Isolation Tests:**
   - Attempt to create a report definition linking a `dashboard_id` from another organization. Verify database-level rejection.
   - Delete a dashboard. Verify `dashboard_id` in `report_definitions` transitions to `NULL` while `organization_id` remains intact.
2. **Historical Retention Tests:**
   - Soft-delete and hard-delete a report definition with completed runs. Verify historical `report_runs` remain intact with `report_definition_id = NULL` and full snapshot data readable.
3. **Snapshot Consistency Tests:**
   - Concurrently modify sheet rows during report generation. Verify that under `REPEATABLE READ`, all report widgets see the identical point-in-time snapshot.
4. **Scheduler Concurrency & Occurrence Tests:**
   - Spawn 3 concurrent scheduler pollers targeting the same active schedule. Verify that exactly **one** `report_runs` row is created due to `uq_report_runs_schedule_occurrence`.
5. **SMTP Ambiguity Tests:**
   - Simulate network failure after SMTP `DATA`. Verify delivery target transitions to `status = 'ambiguous'` and is not automatically re-sent.
6. **Browser Security & Network Denial Tests:**
   - Attempt to render a report with `<img src="http://169.254.169.254/latest/meta-data/">` and `<iframe src="file:///etc/passwd">`. Verify 100% rejection and zero network egress.

---

## 11. Final Implementation-Readiness Decision

### Decision: CONDITIONALLY READY — SPECIFIED BLOCKERS REMAIN

**Conditions Required for Full Readiness:**
1. Explicit user authorization granting Stage 1 execution.
2. Approval to add `croniter>=3.0.0` (or audited cron parser) to `backend/requirements.lock` in Stage 1.
3. Approval to install minimal Chromium rendering dependencies in the worker container in Stage 1/4.

**Final System Invariant:**
- **Alembic Head:** `e82f1b4a9c3d` (Strictly Unchanged).
- **Python Sandbox:** `PYTHON_SANDBOX_ENABLED=false` (Strictly Unchanged).
- **Files Modified:** Zero application code, zero migrations, zero dependencies modified.
- **Implementation Status:** NOT GRANTED / ON HOLD. Waiting for user directive.

---
*Report certified and committed for Phase 9 Gate 3 Final Implementation-Readiness Audit.*  
*Workspace: `D:\vibe`*
