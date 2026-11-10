# VIBE — PHASE 12 STAGE 12.2.1
# INITIAL SOURCE AUDIT & ARCHITECTURAL INVENTORY
## Deep-Dive Analysis of "Soon" Sidebar Modules: Workspaces, Reports, File Storage & System Health

**Workspace:** `D:\vibe`  
**Governing Document:** `docs/IMPLEMENTATION_PLAN.md` v2.0.0  
**Phase:** 12 — Production Readiness, E2E Testing & Deployment Verification  
**Stage:** 12.2.1 — Implementation of Missing Sidebar Modules  
**Inspection Date:** November 4, 2026  
**Baseline Alembic Revision Head:** `370908748232`  
**Environment Baseline:** `PYTHON_SANDBOX_ENABLED=false`, AWS Integration DEFERRED  

---

## 1. Executive Summary & Audit Baseline

In accordance with Phase 12 Stage 12.2.1 Gate A, an exhaustive, strictly read-only inspection was conducted across the VIBE backend (`backend/app/`), frontend (`frontend/src/`), database schema (`vibe_enterprise` and `vibe_enterprise_test`), and existing test suites.

The objective is to establish the ground truth for the four sidebar navigation modules currently flagged with `"coming_soon"` badges in `frontend/src/components/layout/sidebar.tsx`:
1. **Workspaces** (`href: "/workspaces"`)
2. **Reports** (`href: "/reports"`)
3. **File Storage** (`href: "/files"`)
4. **System Health** (`href: "/system"`)

### Key Ground Truth Findings

| Module | DB Table Exists? | Backend Model/Service Exists? | FastAPI Router Exists? | Frontend Route Exists? | Current Operational State |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **Workspaces** (`/workspaces`) | **Yes** (`workspaces`) | **Yes** (`Workspace`, `WorkspaceService`, `WorkspaceRepo`) | **No** (Missing `workspaces.py` router) | **No** (Route 404) | Domain logic 80% implemented; router omitted from `main.py`; causes `DEF-002` nil UUID fallback in dashboards/spreadsheets. |
| **Reports** (`/reports`) | **Yes** (5 tables: `report_definitions`, `report_sections`, `report_runs`, etc.) | **Yes** (`ReportService`, `ReportStorageService`, `ReportRendererService`) | **Yes** (`routers/reports.py` with 15 endpoints) | **No** (Route 404, `GAP-01`) | Backend is 100% complete and tested. Zero frontend UI exists. Needs `frontend/src/app/reports/page.tsx`. |
| **File Storage** (`/files`) | **No** (No general user files table) | **Partial** (`AnalyticsStorageService`, `ReportStorageService` exist for job artifacts) | **Partial** (`/api/v1/analytics/storage` quota only) | **No** (Route 404) | Storage engines for analytics/reports exist with path traversal checks, but no general user-facing file management router or table exists. |
| **System Health** (`/system`) | **N/A** (Operational telemetry) | **Partial** (`health.py` with `/live` & `/ready`, `backups.py` with `/status`) | **Partial** (`/ready` checks DB/Redis; `/backups/status` checks RPO/RTO) | **No** (Route 404) | Unauthenticated readiness exists; authenticated deep subsystem telemetry (DB pool, Celery queue depth, disk usage, backup freshness) is missing. |

---

## 2. Module A — Workspaces Architectural Audit

### 2.1 Existing Database Architecture
- **Table:** `public.workspaces` (Table 41 in PostgreSQL).
- **Primary Key:** `id` (`UUID`, default `gen_random_uuid()`).
- **Tenant Scope:** `organization_id` (`UUID`, foreign key `organizations.id` `ON DELETE CASCADE`, indexed).
- **Columns:**
  - `name`: `VARCHAR(255)` NOT NULL
  - `slug`: `VARCHAR(100)` NOT NULL, indexed
  - `created_at`: `TIMESTAMPTZ` NOT NULL
  - `updated_at`: `TIMESTAMPTZ` NOT NULL
- **Constraints:**
  - `uq_workspaces_org_slug`: Unique constraint on `(organization_id, slug)`.
  - `uq_workspaces_id_org`: Composite unique constraint on `(id, organization_id)`.

### 2.2 Dependent Foreign Keys & Cascade Deletion Risks
Inspection of PostgreSQL foreign key references revealed that `workspaces` is a foundational foreign key target across five major application domains:
1. `workbooks.workspace_id` -> `workspaces(id, organization_id)` (`ON DELETE CASCADE`, NOT NULL)
2. `dashboards.workspace_id` -> `workspaces(id)` (`ON DELETE CASCADE`, NOT NULL)
3. `analytics_jobs.workspace_id` -> `workspaces(id, organization_id)` (`ON DELETE CASCADE`, NOT NULL)
4. `report_definitions.workspace_id` -> `workspaces(id, organization_id)` (`ON DELETE CASCADE`, NOT NULL)
5. `report_runs.workspace_id` -> `workspaces(id, organization_id)` (`ON DELETE SET NULL`, nullable)

> [!WARNING]
> **Cascade Deletion Blast Radius:** Because `workbooks`, `dashboards`, `analytics_jobs`, and `report_definitions` declare `ON DELETE CASCADE` on `workspace_id`, deleting a workspace in PostgreSQL will automatically and permanently purge all dashboards, spreadsheets, and report definitions created inside that workspace!
> **Mandatory Safeguard:** The application must NEVER permit silent workspace deletion. Workspace deletion must enforce:
> 1. Pre-check: If any active dashboards, workbooks, or report definitions exist within the workspace, deletion must be blocked with an HTTP 409 Conflict error detailing the blocking dependencies.
> 2. Protection of Default Workspace: An organization's primary default workspace (slug: `default`) must be protected from deletion.
> 3. Frontend double-confirmation modal requiring typing the workspace slug to confirm deletion.

### 2.3 Existing Backend Components
- **Model:** `backend/app/models/workspace.py` — Fully declared with SQLAlchemy 2.0 mapped columns and `Organization` relationship.
- **Repository:** `backend/app/repositories/workspace.py` — Implements `create`, `get_by_id`, `get_by_slug`, `list_by_organization`, `delete`.
- **Schemas:** `backend/app/schemas/workspace.py` — Implements `WorkspaceBase`, `WorkspaceCreate`, `WorkspaceUpdate`, `WorkspaceRead`.
- **Service:** `backend/app/services/workspace.py` — Implements `create_workspace`, `get_workspace`, `get_workspace_by_slug`, `list_workspaces`. Includes anti-enumeration security logging on cross-tenant ID lookup attempts.
- **RBAC Permissions:** `backend/app/services/role.py` defines:
  - `workspaces.read` (Granted to: `owner`, `admin`, `member`, `viewer`)
  - `workspaces.create` (Granted to: `owner`, `admin`, `member`)
  - `workspaces.update` (Granted to: `owner`, `admin`, `member`)
  - `workspaces.delete` (Granted to: `owner`, `admin`)
- **Organization Provisioning Integration:** `OrganizationService` in `backend/app/services/organization.py` automatically creates a default workspace (`name="Default Workspace"`, `slug="default"`) for every new organization.

### 2.4 Missing Workspaces Components & Defects
1. **Missing Router:** `backend/app/api/v1/routers/workspaces.py` does not exist.
2. **Missing Registration:** `backend/app/main.py` has no `app.include_router(workspaces_router)`.
3. **Missing Frontend Route:** `frontend/src/app/workspaces/page.tsx` does not exist.
4. **Root Cause of Defect `DEF-002`:**
   - In `frontend/src/app/dashboards/page.tsx` (lines 386-402), dashboard creation attempted `fetch("/api/v1/workspaces")`. Because the endpoint returned 404, it fell back to nil UUID `00000000-0000-0000-0000-000000000000`, causing a foreign key violation 404/422 on creation.
   - In `frontend/src/app/spreadsheets/page.tsx` (lines 440-447), workbook creation omitted `workspace_id`, causing a 422 Unprocessable Entity error.
   - Once the real `workspaces` API is mounted and returns the organization's real workspaces (e.g. `Default Workspace`), the nil UUID fallback will be eliminated and both dashboard and workbook creation will pass the valid workspace ID.

---

## 3. Module B — Reports Architectural Audit

### 3.1 Existing Database Architecture
The VIBE reporting subsystem was implemented and migrated during Phase 9/10:
- `report_definitions`: Stores report metadata, orientation (`portrait`/`landscape`), layout settings, tenant scope (`organization_id`, `workspace_id`).
- `report_sections`: Stores modular report blocks (`text`, `chart`, `table`, `metric`, `divider`) with visual configuration and execution ordering (`display_order`).
- `report_runs`: Tracks execution lifecycle (`PENDING`, `GENERATING_SNAPSHOT`, `RENDERING_HTML`, `COMPILING_PDF`, `COMPLETED`, `FAILED`), artifact path, output size, and duration.
- `report_download_tokens`: Manages single-use or time-bound cryptographically hashed download authorization tokens.
- `report_outbox_events`: Provides transactional outbox event guarantees for reporting notifications.

### 3.2 Existing Backend Implementation
- **FastAPI Router:** `backend/app/api/v1/routers/reports.py` (429 lines, mounted at `/api/v1/reports`).
  - Implements 15 REST endpoints:
    1. `POST /api/v1/reports/definitions` (Create report definition; requires `reports.create` + CSRF)
    2. `GET /api/v1/reports/definitions` (List definitions; filter by `workspace_id`; requires `reports.read`)
    3. `GET /api/v1/reports/definitions/{id}` (Get definition details + sections; requires `reports.read`)
    4. `PUT /api/v1/reports/definitions/{id}` (Update definition; requires `reports.update` + CSRF)
    5. `DELETE /api/v1/reports/definitions/{id}` (Delete definition; requires `reports.delete` + CSRF)
    6. `POST /api/v1/reports/definitions/{id}/sections` (Add section; requires `reports.update` + CSRF)
    7. `PUT /api/v1/reports/definitions/{id}/sections/{section_id}` (Update section; requires `reports.update` + CSRF)
    8. `DELETE /api/v1/reports/definitions/{id}/sections/{section_id}` (Delete section; requires `reports.update` + CSRF)
    9. `POST /api/v1/reports/definitions/{id}/sections/reorder` (Reorder sections; requires `reports.update` + CSRF)
    10. `POST /api/v1/reports/definitions/{id}/run` (Trigger asynchronous report run; requires `reports.run` + CSRF)
    11. `GET /api/v1/reports/definitions/{id}/runs` (List run history for definition; requires `reports.read`)
    12. `GET /api/v1/reports/runs/{run_id}` (Poll status of specific run; requires `reports.read`)
    13. `POST /api/v1/reports/runs/{run_id}/download-token` (Issue temporary download token; requires `reports.export` + CSRF)
    14. `GET /api/v1/reports/runs/{run_id}/download` (Direct token-authorized PDF download)
    15. `GET /api/v1/reports/runs/{run_id}/stream` (Direct token-authorized PDF streaming)
- **Services:**
  - `ReportService` (`backend/app/services/report.py`): CRUD and run coordination.
  - `ReportStorageService` (`backend/app/services/report_storage.py`): Filesystem storage in `storage/reports/` with path traversal rejection, symlink checks, and SHA256 checksumming.
  - `ReportDownloadTokenService` (`backend/app/services/report_download_token.py`): Secure HMAC token validation.
  - `ReportRendererService` (`backend/app/services/report_renderer.py`): Jinja2 HTML rendering and WeasyPrint PDF compilation.
  - `ReportingTask` (`backend/app/tasks/reporting.py`): Celery task `generate_report_run_task`.

### 3.3 Missing Reports Components
- **Missing Frontend Route:** `frontend/src/app/reports/page.tsx` does not exist (`GAP-01`).
- The entire backend is already operational and covered by unit/integration tests. We must build a responsive, feature-complete frontend that connects to these 15 endpoints.

---

## 4. Module C — File Storage Architectural Audit

### 4.1 Existing Storage Subsystem Analysis
- `AnalyticsStorageService` in `backend/app/analytics/storage.py`:
  - Base directory: `settings.ANALYTICS_STORAGE_DIR` (default: `./storage/analytics`).
  - Scoped directories: `snapshots/<org_id>/<job_id>/` and `results/<org_id>/<job_id>/`.
  - Enforces `_resolve_safe_path`: rejects traversal (`..`, absolute paths) and verifies path containment within `base_dir`.
  - Quota management: `get_organization_storage_bytes` calculates total directory disk usage for an organization; checks against `settings.ANALYTICS_MAX_ORG_STORAGE_BYTES` (default: 500 MB).
- `ReportStorageService` in `backend/app/services/report_storage.py`:
  - Base directory: `settings.REPORTS_STORAGE_DIR` (default: `./storage/reports`).
  - Scoped directories: `runs/<org_id>/<run_id>/`.
  - Enforces `resolve_safe_path`: rejects symlink escapes, path traversal, and enforces `is_relative_to(base_dir)`.

### 4.2 Gap in User File Storage
- Neither `AnalyticsStorageService` nor `ReportStorageService` handles general user-uploaded files (e.g. data files, CSVs, documentation, spreadsheets, images, schemas).
- There is currently **no database table** tracking user files.
- The sidebar defines `href: "/files"`.
- In `frontend/src/app/page.tsx` (line 181), the Executive Command Center fetches `/api/v1/analytics/storage` to obtain storage quota utilization.

### 4.3 Architectural Options for User File Storage
1. **Option 1 (Relational Database Entity — Recommended):**
   - Introduce a `stored_files` table in PostgreSQL.
   - Full ACID transaction guarantees, pagination, foreign-key cascade with organization, RBAC permissions, and SHA-256 integrity verification.
   - Requires an Alembic migration from head `370908748232`.
2. **Option 2 (Filesystem Metadata Manifest — Zero Migration Alternative):**
   - Store user files in `storage/user_files/<organization_id>/` with an encrypted or atomic JSON metadata manifest per organization (`manifest.json`).
   - Requires zero changes to the database and preserves Alembic head `370908748232` without touching PostgreSQL.
   - Trade-off: Less relational query power for massive scale, but 100% compliant with strict migration freeze rules.

*Detailed evaluation and proposed migration scripts are documented in `DATABASE_MIGRATION_PROPOSAL.md`.*

---

## 5. Module D — System Health Architectural Audit

### 5.1 Existing Health Endpoints
- `backend/app/api/health.py` (mounted at `/api/v1`):
  - `GET /api/v1/health`: Unauthenticated liveness probe returning `{"status": "ok", "version": "1.0.0", "timestamp": "..."}`.
  - `GET /api/v1/ready`: Unauthenticated readiness probe returning:
    - PostgreSQL connection status and query roundtrip latency (`SELECT 1`).
    - Redis connection status and ping roundtrip latency (`r.ping()`).
    - Overall status (`ready` or `degraded`).
- `backend/app/api/v1/routers/backups.py` (mounted at `/api/v1/backups`):
  - `GET /api/v1/backups/status`: Authenticated endpoint (requires `organization.settings.update` or `audit.read`).
  - Returns RPO metrics, RTO metrics, WAL spool metrics, and recovery posture.

### 5.2 Missing Operational Health Capabilities
The current endpoints do not provide:
1. SQLAlchemy connection pool metrics (pool size, checked in, checked out, overflow).
2. Celery queue depth and active worker heartbeat status.
3. Storage volume breakdown (disk space used across analytics, reports, backups, user files, and free disk bytes).
4. Subsystem matrix summarizing status across:
   - Frontend Web Client
   - FastAPI Backend API
   - PostgreSQL Database Engine
   - Redis Cache & Lock Manager
   - Celery Task Workers
   - Analytics Compute Engine
   - File Storage Subsystem
   - Disaster Recovery & Backup Subsystem
5. Administrative UI route: `frontend/src/app/system/page.tsx` does not exist.

### 5.3 Security & Information Leakage Audit
The System Health module must strictly comply with enterprise security constraints:
- **Forbidden Exclusions:** Never expose environment variables (`.env`), connection strings containing passwords (`postgresql://...`, `redis://...`), secret keys (`SESSION_SECRET_KEY`, `ENCRYPTION_MASTER_KEY`), server internal absolute paths, or unhandled exception traces.
- **RBAC Requirement:** Subsystem operational telemetry must be restricted to authenticated users holding administrative permissions (`organization.settings.update`, `audit.read`, or `organization.read`).

---

## 6. Frontend Navigation & Shell Integration

### 6.1 `frontend/src/components/layout/sidebar.tsx` State
The sidebar currently renders:
- Line 40: `{ title: "Workspaces", href: "/workspaces", icon: Layers, status: "coming_soon" }`
- Line 50: `{ title: "Reports", href: "/reports", icon: FileText, status: "coming_soon" }`
- Line 59: `{ title: "File Storage", href: "/files", icon: FolderOpen, status: "coming_soon" }`
- Line 60: `{ title: "System Health", href: "/system", icon: Database, status: "coming_soon" }`

When `status === "coming_soon"`:
- The sidebar renders a small `"Soon"` badge (line 180).
- The link sets `prefetch={false}` (line 163).
- The tooltip in collapsed mode displays `(Phase 6)` or `(Phase 5+)` (line 197).
- In the manual verification in Stage 12.2, clicking these items navigated to routes that returned Next.js 404 pages.

### 6.2 Transition Plan
As each module is implemented and accredited through Gates B and C, its status in `sidebar.tsx` will transition from `"coming_soon"` to `"active"`, removing the "Soon" badge and linking directly to the real, functional module interface.

---

## 7. Audit Conclusion & Gate A Verdict

The architecture audit confirms:
1. **Workspaces:** Backend core models, repos, services, and tables are already fully realized. Missing only the FastAPI router and the Next.js page.
2. **Reports:** Backend is 100% complete and tested with 15 endpoints. Missing only the Next.js page.
3. **File Storage:** Local storage foundation with traversal security exists. Needs user file service, API router, frontend UI, and explicit human decision on the `stored_files` database migration proposal.
4. **System Health:** Basic liveness/readiness probes exist. Needs an authenticated deep telemetry router and administrative status page.

No source code has been modified during Gate A.
Proceed to review `docs/phase-12/stage-12.2.1/IMPLEMENTATION_PLAN.md` and `docs/phase-12/stage-12.2.1/DATABASE_MIGRATION_PROPOSAL.md`.

