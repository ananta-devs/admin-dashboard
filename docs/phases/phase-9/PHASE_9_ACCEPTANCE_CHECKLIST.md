# VIBE — PHASE 9 ACCEPTANCE CHECKLIST & VERIFICATION MATRIX
## Enterprise Business Intelligence, Interactive Dashboards & On-Demand Reporting

**Document Version:** 2.0.0 (Hardened & Refined)  
**Target Migration:** Incremental forward migration from `e82f1b4a9c3d`  
**Purpose:** Objective, measurable sign-off criteria for Phase 9 completion  

---

## 1. Automated Test & Code Quality Gates

- [ ] **Full Backend Non-Live Regression Suite:**
  - Command: `backend\.venv\Scripts\python -m pytest backend/tests -m "not live" -q`
  - Pass condition: $\ge 450$ passed, $\le 1$ skipped, **0 failed**.
- [ ] **Frontend Vitest Unit & Component Suite:**
  - Command: `npm --prefix frontend test -- --run`
  - Pass condition: $\ge 55$ passed across all test files, **0 failed**.
- [ ] **Python Static Type Checking:**
  - Command: `backend\.venv\Scripts\mypy backend/app`
  - Pass condition: **Success: no issues found in $\ge 138$ source files**.
- [ ] **Python Linting & Formatting:**
  - Command: `backend\.venv\Scripts\ruff check backend/app backend/tests`
  - Pass condition: **0 errors**.
- [ ] **Frontend Linting:**
  - Command: `npm --prefix frontend run lint`
  - Pass condition: **0 errors** (compilation passes cleanly).
- [ ] **Next.js Production Build:**
  - Command: `npm --prefix frontend run build`
  - Pass condition: Compiled successfully with Turbopack, static page generation succeeds for all routes including `/reports`, `/dashboards`, `/spreadsheets`, `/analytics`.

---

## 2. Gate 1 Acceptance: Apache ECharts Visualization Engine

- [x] **Package Resolution & Tree-Shaking:**
  - `echarts` successfully installed in `frontend/package.json` with modular core imports (`echarts/core`, `echarts/charts`, `echarts/components`, `echarts/renderers`) and dynamic `next/dynamic` lazy loading (`ssr: false`).
  - Initial application bundle size on unauthenticated routes does not load ECharts.
- [x] **10 Core Enterprise Chart Types Verified:**
  - Standard Bar, Stacked/Grouped Bar, Horizontal Bar, Standard Line, Smooth Multi-Axis Line, Stacked Area, Pie/Donut, Scatter Plot, Heatmap Matrix, Multi-Metric KPI Card.
  - Every chart renders with Polars query response data without runtime NaN or coordinate errors.
- [x] **Zero Arbitrary JavaScript in Configurations:**
  - Tooltips and value formatting strictly use declarative tokens (`"percentage"`, `"currency_usd"`, `"number_compact"`, `"integer"`, `"decimal_2"`).
  - No raw JavaScript code strings (`eval()`, `new Function()`) are accepted by schemas or executed on client.
- [x] **Theme Switching Compliance:**
  - Toggling between Light and Dark themes dynamically re-applies ECharts color palettes and axis label contrasts conforming to WCAG 2.2 AA.
- [x] **Interactive Visual Controls:**
  - Hover tooltip displays formatted values with unit symbols.
  - Clicking legend items toggles series visibility.
  - Data zoom slider / brush operates smoothly on time series datasets $\ge 1,000$ points.
- [x] **Rendering Performance Budget:**
  - High performance Canvas rendering with debounced `ResizeObserver` lifecycle management.
  - Zero memory leaks: `chartInstance.dispose()` cleanly unmounts.

---

## 3. Gate 2 Acceptance: BI Dashboard Studio & Global Filtering

- [ ] **Responsive Grid Layout:**
  - Dashboard widgets render in 4-column layout on desktop ($\ge 1280\text{px}$), 2-column on tablet ($\ge 768\text{px}$), and 1-column on mobile ($< 768\text{px}$).
  - Dragging/resizing widgets updates `dashboards.layout_config` in PostgreSQL via `POST /api/v1/dashboards/{id}/layout`.
- [ ] **Typed Server-Side Filter Schema Enforced:**
  - All global and widget filters conform to Pydantic `DashboardFilterInput`.
  - Dimension names validated with regex `^[a-zA-Z0-9_]{1,64}$`.
  - Filter operators strictly restricted to allowlist (`eq`, `neq`, `gt`, `gte`, `lt`, `lte`, `in`, `between`).
  - Dates and timestamps normalized to ISO 8601 UTC.
- [ ] **Zero SQL / Polars Injection:**
  - Queries construct via compiled Polars methods (`pl.col(dim) == val`); zero string concatenation or code evaluation.
- [ ] **Workspace Authorization Boundaries:**
  - Client-supplied `workspace_id` parameters verified against caller's active `OrganizationMembership`.
  - Cross-workspace queries from unassigned users return HTTP 404 (blocking resource enumeration).
- [ ] **Global Filter Propagation Performance:**
  - Selecting a global date or categorical filter refreshes all dependent widgets on the dashboard within $\le 200\text{ ms}$.
- [ ] **Presentation Mode:**
  - Fullscreen toggle expands canvas to 100vw/100vh, hides navigation chrome, and supports automated 30-second widget cycling.

---

## 4. Gate 3 Acceptance: Executive Reporting & Isolated Server-Side PDF Engine

- [ ] **Database Migration Verification:**
  - New migration creates `reports`, `report_sections`, `report_runs`, and `report_outbox_events` with composite foreign keys to `organizations.id`.
  - Upgrading to Phase 9 head and downgrading back to `e82f1b4a9c3d` tested and verified clean on a disposable test database.
- [ ] **RBAC Permissions Registered & Seeded:**
  - System permissions `reports.read`, `reports.create`, `reports.update`, `reports.delete`, `reports.export` seeded and backfilled to Owner, Admin, Member, and Viewer roles.
- [ ] **Report Builder Studio (`/reports`):**
  - Users can create on-demand reports, add mixed sections (Markdown commentary, ECharts widget snapshots, spreadsheet tables, page breaks), and reorder sections.
- [ ] **Server-Side SVG Rendering (Zero-Browser JS):**
  - All chart snapshots rendered server-side as static XML `<svg>` strings via ECharts SSR.
  - PDF rendering engine runs with `--disable-javascript`.
- [ ] **PDF Sandbox Network Denial & SSRF Protection:**
  - PDF compilation subprocess runs with `--network none`.
  - Injected SSRF tags (`<img src="http://169.254.169.254">`, `<iframe src="file:///etc/passwd">`) and `<script>` tags are strictly stripped by `bleach` sanitization before PDF compilation.
  - Renderer inherits zero database connection strings or application encryption keys.
- [ ] **Hermetic Font & Asset Handling:**
  - Offline local fonts (Inter, Roboto Mono) bundled in container; zero remote `@import` or web font fetches permitted.
- [ ] **Artifact Storage Abstraction & Atomic Promotion:**
  - `ReportArtifactStorageService` implements `StorageBackendProtocol`.
  - PDF renders to `{run_id}.tmp` in an isolated temp directory (`0700`).
  - Pre-flight quota check in PostgreSQL confirms remaining quota $\ge 10\text{ MB}$.
  - Worker computes SHA-256 digest and file size, commits actual size to PostgreSQL `organizations.storage_used_bytes`, atomically promotes file to final path, and unlinks temp files in `finally:`.
- [ ] **Authorized Download Endpoints:**
  - `GET /api/v1/reports/{id}/runs/{run_id}/download` validates session authentication and `reports.read` permission.
  - Cross-tenant download attempts return HTTP 404.
  - Streams binary with `Content-Type: application/pdf` and `X-Content-Type-Options: nosniff`.
- [ ] **Immutable Run Snapshots & Data Revision Pinning:**
  - `report_runs.report_snapshot` freezes report layout, text, and widget parameters at trigger time.
  - `report_runs.data_revision_ref` records sheet cell versions and Parquet snapshot hashes.
- [ ] **Crash Recovery & Idempotency:**
  - Unique composite index `(organization_id, idempotency_key)` rejects duplicate trigger requests.
  - Outbox dispatcher daemon reclaims stale running jobs via `lease_expires_at`.

---

## 5. Gate 4 Acceptance: Executive Command Center & Scope Boundaries

- [ ] **Root Homepage Transformation (`/`):**
  - Homepage replaced with Executive Command Center.
  - Live metric widgets display total active spreadsheets, total analytics jobs, storage quota percentage, and recent audit activity.
  - Zero hardcoded mock text from the Phase 2 component showcase remaining.
- [ ] **Strict Reporting Scope Boundaries Respected:**
  - Scheduled periodic reports (cron / interval), automated email delivery, and threshold alert workflows are **strictly absent** from Phase 9 and deferred to Phase 10.
- [ ] **Audit Trail Completeness:**
  - Every report creation, edit, deletion, and PDF generation is immutably recorded in `administrative_audit_events`.
- [ ] **Phase 1–8 Production Guardrails Strictly Preserved:**
  - `PYTHON_SANDBOX_ENABLED=false` remains default.
  - Zero arbitrary Python sandbox code execution enabled.
  - WSL2 development verification is NOT mislabeled as production gVisor.
  - Google OAuth staging remains unverified until a real credentialed round trip succeeds.

---

## 6. Final Sign-Off Declaration

Phase 9 will be formally certified as complete when and only when all 30 verification checkboxes above have been empirically tested, documented with timestamped command outputs, and committed to the repository changelog.
