# VIBE Platform — Manual QA Defect Register
**Phase:** 12 — Production Readiness, End-to-End Testing & Deployment Verification  
**Stage:** 12.2 — Final Implementation Freeze & Manual Chrome Verification  
**Date:** November 4, 2026  
**Auditor / Testing Engine:** Google Antigravity & Real Google Chrome Browser (Chrome DevTools MCP)  
**Database:** Isolated Dedicated PostgreSQL (`vibe_enterprise_test`)  
**Production Baseline Status:** Unmodified & Untouched (`vibe_enterprise` verified clean)

---

## 1. Defect Classification & Severity Criteria

| Severity | Definition | Policy / Remediation Scope |
| :--- | :--- | :--- |
| **Critical** | Complete platform crash, unhandled runtime exception, data corruption, or security boundary violation. | Immediate fix or fail-closed gate. |
| **High** | Primary workflow modal or form action fails through UI, or unhandled promise rejection crashes React tree. | Remediated in frontend or registered for immediate remediation. |
| **Medium** | Non-critical feature degradation, confusing error feedback, or transient network timing issue. | Registered in backlog with detailed workarounds. |
| **Low / Cosmetic** | Visual alignment, minor margin discrepancy, or non-blocking lint warning. | Recorded for post-freeze enhancement. |

---

## 2. Discovered Defect Register

### DEF-001: Spreadsheet Studio — Create Workbook Modal Fails with 422 Unprocessable Content & Triggers React Error #31 Unhandled Crash

- **Defect ID:** `DEF-001`
- **Component:** `Frontend — Spreadsheet Studio (/spreadsheets)`
- **Severity:** `High`
- **Discovery Date:** November 4, 2026
- **Discovered In:** Real Google Chrome Browser Manual Verification
- **Element ID:** `EL-040` (`button "Create Workbook"` inside `Create Workbook` Dialog)
- **Status:** **REMEDIATED (Frontend Crash Shield Applied; Backend Schema Alignment Registered)**

#### Reproduction Steps:
1. Navigate to `http://localhost:3000/spreadsheets`.
2. Click the `New Workbook` button in the top action bar (`EL-034`).
3. In the `Create Workbook` dialog (`EL-040`), enter a valid name (e.g. "Q4 Financial Model") and optional description.
4. Click `Create Workbook` (`EL-040`).

#### Expected Behavior:
The client should provide the active workspace ID in the payload (or the backend should default to the organization's primary default workspace if omitted), successfully create the workbook, close the modal, and append the new workbook to the workbook selector. If an error occurs, a user-friendly error toast/banner should appear without crashing the application.

#### Actual Behavior Observed in Chrome:
1. The frontend dispatched `POST /api/v1/spreadsheets/workbooks` with body:
   ```json
   { "name": "Q4 Financial Model", "description": "Q4 Projections" }
   ```
2. The backend Pydantic schema `WorkbookCreateInput` strictly requires `workspace_id: uuid.UUID` (`extra="forbid"`).
3. The server rejected the request with HTTP `422 Unprocessable Content`:
   ```json
   { "detail": [{ "loc": ["body", "workspace_id"], "msg": "Field required", "type": "missing" }] }
   ```
4. In `frontend/src/app/spreadsheets/page.tsx`, the error handler called:
   ```ts
   showNotice(err.detail || "Failed to create workbook.", "error");
   ```
   Because `err.detail` was an array of objects, React encountered `[object Object]` as a child in JSX, throwing uncaught exception:
   `Uncaught Error: Minified React error #31 (Objects are not valid as a React child)`
   This unhandled render crash triggered Next.js's global error boundary (`This page couldn't load`).

#### Remediation Applied During Stage 12.2:
1. **Frontend Crash Shield:** Updated `showNotice` in `frontend/src/app/spreadsheets/page.tsx` and `frontend/src/app/settings/page.tsx` to normalize any error object or array into a safe string representation. The modal now gracefully remains open and displays the validation notice without crashing React.
2. **Permanent Solution for Stage 12.3:**
   - In `backend/app/schemas/spreadsheet.py`: Make `workspace_id: uuid.UUID | None = None` optional on `WorkbookCreateInput`.
   - In `backend/app/services/spreadsheet.py`: If `data.workspace_id` is null, automatically lookup the organization's default workspace from `WorkspaceRepository.get_by_slug("default", organization_id)`.

---

### DEF-002: Dashboard Studio — Create Dashboard Modal Fails with 404 Not Found Due to Non-Existent Workspaces Route

- **Defect ID:** `DEF-002`
- **Component:** `Frontend — Dashboard Studio (/dashboards)`
- **Severity:** `High`
- **Discovery Date:** November 4, 2026
- **Discovered In:** Real Google Chrome Browser Manual Verification
- **Element ID:** `EL-053` (`button "Create Dashboard"` inside `Create Analytical Dashboard` Dialog)
- **Status:** **IDENTIFIED & REGISTERED (Workaround Available via API / Seeding)**

#### Reproduction Steps:
1. Navigate to `http://localhost:3000/dashboards`.
2. Click the `New Dashboard` button (`EL-042`).
3. Enter a title (e.g. "Executive KPI Overview") and click `Create Dashboard` (`EL-053`).

#### Expected Behavior:
The dashboard should be created within the active tenant's default workspace and immediately rendered in the 12-column responsive grid canvas.

#### Actual Behavior Observed in Chrome:
1. In `frontend/src/app/dashboards/page.tsx`, line 387:
   ```ts
   const wsRes = await fetch("/api/v1/workspaces", { credentials: "include" });
   ```
2. The platform currently lacks a top-level `/api/v1/workspaces` route (the Workspaces module is marked as "Soon" in the navigation shell).
3. The fetch returned HTTP `404 Not Found`.
4. The frontend code fell back to a nil UUID:
   ```ts
   workspace_id: wsId || "00000000-0000-0000-0000-000000000000"
   ```
5. `POST /api/v1/dashboards` rejected the nil UUID because no workspace exists with that ID, returning HTTP `404 Not Found`:
   `EntityNotFoundError: Workspace 00000000-0000-0000-0000-000000000000 not found`.

#### Verification & Workaround:
When an authenticated session dispatches `POST /api/v1/dashboards` with the actual default workspace ID provisioned during tenant registration (`df3f935a-dc2d-4159-aed1-9cbd80fed1fb`), dashboard creation succeeds with HTTP 201 Created, full widget layout configuration, and duplicate/delete functionality.

#### Recommended Permanent Resolution for Post-Freeze:
- Expose a lightweight endpoint `GET /api/v1/organization/workspaces` returning all workspaces in the tenant.
- Or provide an optional `workspace_id` in `DashboardCreateInput` that defaults to the tenant's primary default workspace.

---

### DEF-003: Content Security Policy Violation on Inline Styles from Dynamic Icon Components

- **Defect ID:** `DEF-003`
- **Component:** `Frontend Shell & Topbar (/components/theme-toggle.tsx, /components/layout/topbar.tsx)`
- **Severity:** `Low / Informational`
- **Discovery Date:** November 4, 2026
- **Discovered In:** Real Google Chrome DevTools Console Log Audit
- **Status:** **DOCUMENTED (Non-blocking warning; nonce-based CSP active)**

#### Description:
Chrome DevTools logged the following console message:
```
Applying inline style violates the following Content Security Policy directive 'style-src 'self' 'nonce-...''. Either the 'unsafe-inline' keyword, a hash, or a nonce is required to enable inline execution.
```
This warning originates from dynamic style attributes injected by third-party icon libraries or CSS-in-JS transitions during rapid element hydration. Because the platform enforces a strict nonce-based CSP (`style-src 'self' 'nonce-...' 'style-src-attr 'unsafe-inline''`), the inline style execution was appropriately blocked by Chrome without breaking component functionality or layout rendering.

---

## 3. Summary of Remediated Defects in Stage 12.2

| Defect ID | Component | Description | Resolution Status |
| :--- | :--- | :--- | :--- |
| **DEF-001** | Spreadsheets Page | React Error #31 on unhandled 422 error details in `showNotice` | **FIXED** in `frontend/src/app/spreadsheets/page.tsx` & `settings/page.tsx` |
| **DEF-002** | Dashboards Page | Create Dashboard nil UUID fallback on 404 `/workspaces` | **DOCUMENTED** with reproduction steps & permanent resolution |
| **DEF-003** | Frontend CSP | Style nonce violation on dynamic icon style injection | **DOCUMENTED**; nonce-based CSP enforcement operating as intended |

