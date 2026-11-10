# VIBE Platform — Human Manual Testing Checklist
**Phase:** 12 — Production Readiness, End-to-End Testing & Deployment Verification  
**Stage:** 12.2 — Final Implementation Freeze & Manual Chrome Verification  
**Audience:** Human QA Engineers, Release Managers, Product Stakeholders  
**Execution Environment:** Google Chrome on Desktop / Mobile Emulation  
**Target URL:** `http://localhost:3000`  
**Prerequisites:** Docker containers `vibe_postgres` & `vibe_redis` running; Isolated test backend on port 8000 (`E2E_EXPECTED_DATABASE=vibe_enterprise_test`); Next.js production server on port 3000.

---

## 1. Test Preparation & Pre-Flight Checks

Before beginning human manual testing, verify the following prerequisites:

- [ ] **Database Isolation Assertion:** Ensure `vibe_enterprise` is untouched.
  ```powershell
  docker exec vibe_postgres psql -U vibe_admin -d vibe_enterprise -c "SELECT count(*) FROM users;"
  # Must equal exactly 10,217
  ```
- [ ] **Backend Health Check:**
  ```powershell
  Invoke-RestMethod -Uri "http://localhost:3000/api/v1/ready"
  # Must return status: "ready", e2e_mode: true
  ```
- [ ] **Clean Browser Profile:** Open Google Chrome in an Incognito window or dedicated profile to avoid cached state.

---

## 2. Human-Executable Verification Scenarios

### Section A: Authentication, Session Lifecycle & Shell Navigation

#### Scenario A.1: User Registration & Session Establishment
- **URL:** `http://localhost:3000`
- **Steps:**
  1. Open Chrome DevTools (Press `F12` or `Ctrl+Shift+I`) and go to the Console tab.
  2. In the console, execute the registration fetch:
     ```js
     await fetch('/api/v1/auth/register', {
       method: 'POST',
       headers: { 'Content-Type': 'application/json' },
       body: JSON.stringify({
         email: 'human_tester@vibe-test.internal',
         password: 'TestPassword2026!Secure',
         display_name: 'Human Tester',
         organization_name: 'Vibe QA Testing Org',
         organization_slug: 'vibe-qa-testing-org'
       })
     });
     ```
  3. Execute the login fetch:
     ```js
     await fetch('/api/v1/auth/login', {
       method: 'POST',
       headers: { 'Content-Type': 'application/json' },
       body: JSON.stringify({
         email: 'human_tester@vibe-test.internal',
         password: 'TestPassword2026!Secure'
       })
     });
     ```
  4. Reload the page (`F5`).
- **Expected Results:**
  - [ ] DevTools Application tab -> Cookies (`http://localhost:3000`) displays `vibe_session` with `HttpOnly: true` and `SameSite: Lax`.
  - [ ] The topbar displays user avatar `H` and label `Human Tester` or `Admin (Demo)`.
  - [ ] No `401 Unauthorized` errors appear in the console.

#### Scenario A.2: Shell Responsive Navigation & Theme Toggle
- **URL:** `http://localhost:3000/`
- **Steps:**
  1. Click the `Collapse sidebar` button (`EL-002`) in the top-left of the sidebar.
  2. Observe the sidebar shrinks to 64px icon-only mode.
  3. Click the `Expand sidebar` button (`EL-002`) to restore the 240px drawer.
  4. Click the `Toggle theme` button (`EL-017`) in the topbar.
  5. Press `Ctrl + K` or click the search bar (`EL-016`) to open the Command Palette.
  6. Press `Escape` or click the `Close dialog` button to dismiss.
- **Expected Results:**
  - [ ] Sidebar collapses and expands with smooth CSS transitions without layout shifts.
  - [ ] Document switches from light mode to dark mode (background turns to dark slate `#020617`).
  - [ ] Command Palette dialog opens with focus inside the search input.

---

### Section B: Executive Command Center (`/`)

#### Scenario B.1: Telemetry KPI Cards & Operational Subsystems
- **URL:** `http://localhost:3000/`
- **Steps:**
  1. Inspect the 4 top-level metric cards:
     - Active Spreadsheets
     - Analytics Executions
     - Storage Utilization
     - Pinned Dashboards
  2. Scroll down to `Subsystem Telemetry` and inspect the node health badges.
  3. Click `Refresh Telemetry` (`EL-022`).
- **Expected Results:**
  - [ ] Metrics render without NaN, undefined, or missing units.
  - [ ] Subsystem telemetry shows:
    - FastAPI Core API: `Ready` (Green)
    - PostgreSQL 16: `Connected` (Green)
    - Redis 7 State & Cache: `Active` (Green)
    - Report Renderer (Sandbox-Disabled): `Isolated` (Indigo)
  - [ ] Refresh button shows a brief loading animation and refreshes telemetry cleanly.

---

### Section C: Spreadsheet Studio (`/spreadsheets`)

#### Scenario C.1: 50,000-Row Virtual Grid & Formula Computation
- **URL:** `http://localhost:3000/spreadsheets`
- **Steps:**
  1. Scroll down the grid rapidly to row 500, row 1,000, and row 5,000.
  2. Click on cell `B2` (value `12000`).
  3. Inspect the Formula Bar (`EL-035` and `EL-036`).
  4. In the formula bar input (`EL-036`), type `25000` and press `Enter`.
  5. Observe the Total cell `D2` and SUM cell `B6`.
  6. Click `Undo edit (Ctrl+Z)` (`EL-032`).
- **Expected Results:**
  - [ ] Grid virtualizer smoothly mounts and unmounts DOM rows at 60 FPS without memory bloat.
  - [ ] Formula bar displays active coordinate `B2` and value `12000`.
  - [ ] Updating `B2` to `25000` immediately recomputes `D2` to `39500` and `B6` to `62700`.
  - [ ] Clicking Undo restores `B2` back to `12000` and re-evaluates all dependent formulas.

#### Scenario C.2: Known Gap 2 Verification (Column Sorting/Filtering)
- **URL:** `http://localhost:3000/spreadsheets`
- **Steps:**
  1. Click on Column Header `A`, `B`, or `C` (`EL-039`).
  2. Right-click on a column header.
- **Expected Results:**
  - [ ] Confirm: No sort or filter dropdown appears. Classified as `NOT IMPLEMENTED` (Known Gap #2).

---

### Section D: Dashboard Studio (`/dashboards`)

#### Scenario D.1: Dashboard Layout, Widget Rendering & Duplicate Flow
- **URL:** `http://localhost:3000/dashboards`
- **Steps:**
  1. Ensure an active dashboard is selected in `Select Active Dashboard` (`EL-041`).
  2. Inspect the 12-column widget grid.
  3. Click `Duplicate` (`EL-045`).
  4. In the modal, leave title as `... (Copy)` and click `Duplicate Dashboard` (`EL-046`).
  5. Verify the duplicated dashboard is cloned and selected in the dropdown.
  6. Click `Delete` (`EL-047`).
  7. In the confirmation dialog, click `Delete Permanently` (`EL-048`).
- **Expected Results:**
  - [ ] Active dashboard displays KPI card and source metadata.
  - [ ] Duplicate modal clones dashboard with all widget geometries intact.
  - [ ] Delete modal permanently deletes the cloned dashboard and switches back to the primary dashboard.

---

### Section E: Analytics Engine & Routines (`/analytics`)

#### Scenario E.1: Operations Catalogue & Job Queue Inspection
- **URL:** `http://localhost:3000/analytics`
- **Steps:**
  1. Click `New Analysis` (`EL-055`).
  2. In the modal, review the 6 allowlisted operations:
     - Dataset Profiling
     - Descriptive Statistics
     - Grouped Aggregation
     - Declarative Filtering
     - Correlation Analysis
     - Data Quality Profiling
  3. Click `Cancel` (`EL-023`) to close.
  4. Click the `Governed Python Scripts (Sandbox)` tab (`EL-057`).
  5. Review the warning notice: "AST validation is an advisory linting filter and does not constitute an operating-system sandbox."
- **Expected Results:**
  - [ ] Operations catalogue displays all 6 routines with complete descriptions.
  - [ ] Governed scripts tab displays code hash audit ledger and creation modal.

---

### Section F: Known Gap 1 Verification (Reports Route `/reports`)

#### Scenario F.1: Verify Missing Route Returns 404
- **URL:** `http://localhost:3000/reports`
- **Steps:**
  1. Navigate directly to `http://localhost:3000/reports`.
  2. Observe the sidebar link `Reports Soon` (`EL-009`).
- **Expected Results:**
  - [ ] Page renders `404: This page could not be found.`
  - [ ] Sidebar link displays `"Soon"` badge.
  - [ ] Confirm: Classified as `NOT IMPLEMENTED` (Known Gap #1).

---

### Section G: Automated Backup & Disaster Recovery (`/settings/backups`)

#### Scenario G.1: Live Telemetry & Known Gap 3 Verification
- **URL:** `http://localhost:3000/settings/backups`
- **Steps:**
  1. Review the `Disaster Recovery Posture` card:
     - Posture status badge: `HEALTHY`
     - Operational RPO: `2.1 min`
     - Operational RTO: `7.87 min`
     - WAL Spool Capacity: `0%`
     - WORM Immutability: `COMPLIANCE` (GFS: `14D / 8W / 12M`)
     - 16-Point Integrity Matrix: `16 / 16 Passed (100%)`
  2. Click `Refresh Status` (`EL-062`).
  3. Scan the page for any manual backup trigger buttons.
- **Expected Results:**
  - [ ] Telemetry refreshes via live query to `/api/v1/backups/status`.
  - [ ] Confirm: No manual backup trigger button exists. Classified as `NOT IMPLEMENTED` (Known Gap #3).

---

### Section H: Organization Roles & Governance (`/roles`, `/users`, `/settings`, `/audit`)

#### Scenario H.1: Roles Catalogue & 403 Simulation
- **URL:** `http://localhost:3000/roles`
- **Steps:**
  1. Inspect the 4 system roles: Admin, Member, Owner, Viewer.
  2. Click `Create Custom Role` (`EL-066`).
  3. Review the granular capability checkboxes (Organization, Members, Roles, Workspaces, Audit, Ownership).
  4. Click `Cancel`.
  5. Click `Simulate 403` (`EL-064`).
  6. Observe the `403 Forbidden: Insufficient Permissions` state.
  7. Click `Reset to Authorized State` (`EL-065`).
- **Expected Results:**
  - [ ] 4 system roles render with their capability counts.
  - [ ] 403 simulation renders an accessible alert banner and restores state cleanly on reset.

#### Scenario H.2: Audit Ledger & Settings
- **URL:** `http://localhost:3000/audit` & `http://localhost:3000/settings`
- **Steps:**
  1. In `/audit`, type `ownership` into the search box (`EL-079`). Verify filtered results.
  2. Click `Inspect` (`EL-080`) on an event to view JSON metadata.
  3. In `/settings`, verify the tenant organization name input (`EL-074`) and immutable disabled slug (`EL-075`).
  4. Inspect the `Transfer Primary Organization Ownership` card (`EL-077`).
- **Expected Results:**
  - [ ] Audit search filters rows in real time. Metadata inspector renders structured JSON.
  - [ ] Settings enforces immutable subdomain slug and requires explicit confirmation phrase `TRANSFER`.

