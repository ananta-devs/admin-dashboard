# Stage 12.2: Discovered Gaps & Stage 12.2.1 Scope Definition

**Workspace:** `D:\vibe`  
**Phase:** 12 — Production Readiness, End-to-End Testing & Deployment Verification  
**Stage:** 12.2 — Comprehensive Playwright E2E Workflow Automation  
**Date:** November 4, 2026  

---

## 1. Context & Governance

During the comprehensive pre-authorization review and workflow implementation of Stage 12.2, three platform gaps were formally identified between backend capability and frontend user interface implementation.

In strict adherence to human authorization guidelines, Stage 12.2 was restricted to E2E test automation and isolation infrastructure. Implementing new application features or UI pages was strictly prohibited in Stage 12.2.

These three items are categorized, detailed, and carried directly into **Stage 12.2.1** for targeted frontend implementation.

---

## 2. Inventory of Discovered Gaps

### Gap 1: Frontend Reports Studio Page Missing
- **Status:** Backend Fully Functional; Frontend Page Missing.
- **Backend Capability:**
  The backend provides complete multi-section report authoring, formatting, reordering, run execution, and streaming PDF downloads:
  - `POST /api/v1/reports/definitions`
  - `GET /api/v1/reports/definitions`
  - `GET /api/v1/reports/definitions/{id}`
  - `PATCH /api/v1/reports/definitions/{id}`
  - `POST /api/v1/reports/definitions/{id}/sections`
  - `POST /api/v1/reports/definitions/{id}/sections/reorder`
  - `POST /api/v1/reports/definitions/{id}/runs`
  - `GET /api/v1/reports/runs/{id}`
  - `POST /api/v1/reports/runs/{id}/download-token`
  - `GET /api/v1/reports/download/{token}`
- **Current Frontend State:**
  There is no `src/app/reports/page.tsx` or `/reports` route in the Next.js frontend application.
- **Stage 12.2 Test Handling:**
  Tested exhaustively via Playwright API Contract tests (`frontend/e2e/workflows/07_reports.spec.ts`), validating all CRUD, reordering, run execution, and tokenized PDF streaming endpoints.
- **Stage 12.2.1 Implementation Mandate:**
  Create `src/app/reports/page.tsx` with Report Definition Builder, Section Reorder drag-and-drop, Run Execution history, and single-use PDF download triggers.

---

### Gap 2: Spreadsheet Column Sorting & Filtering Controls Missing
- **Status:** Backend Query Layer Ready; Frontend UI Controls Missing.
- **Frontend Capability:**
  `frontend/src/components/spreadsheet/virtual-grid.tsx` renders 50,000 virtual rows, 26 columns, Formula Bar editing, arithmetic, and formula recalculation (`SUM`, `AVERAGE`, `COUNT`, `MIN`, `MAX`, `IF`).
- **Missing Controls:**
  Column headers currently display simple text labels without interactive sort arrows (ascending/descending) or column filter popovers.
- **Stage 12.2 Test Handling:**
  Automated virtual grid dimensions, formula evaluations, cell editing, and undo/redo operations (`frontend/e2e/workflows/04_spreadsheets.spec.ts`).
- **Stage 12.2.1 Implementation Mandate:**
  Enhance `virtual-grid.tsx` column headers with sort toggle indicators and multi-select column value filter popovers.

---

### Gap 3: Manual Backup Drill Trigger Button in UI
- **Status:** Backend Operational; Frontend Telemetry Complete; Manual Action Button Missing.
- **Frontend Capability:**
  `frontend/src/components/admin/BackupRecoveryCard.tsx` renders real-time Operational RPO metrics (Target 15.0m), Operational RTO metrics (Ceiling 60.0m), WAL spool capacity, and WORM immutability posture.
- **Missing Action:**
  The component includes a `Refresh Status` button, but lacks a manual `Trigger Backup Drill` action button with confirmation dialog for administrators.
- **Stage 12.2 Test Handling:**
  Automated PITR RPO/RTO rendering, status refresh, and strict RBAC authorization controls (`frontend/e2e/workflows/08_backups_dr.spec.ts`).
- **Stage 12.2.1 Implementation Mandate:**
  Add a privileged "Trigger Manual Backup" button with dialog confirmation and audit logging in `BackupRecoveryCard.tsx`.

---

## 3. Transition to Stage 12.2.1

Stage 12.2 is concluded and conditionally accepted. Upon receiving human authorization for Stage 12.2.1, implementation will proceed systematically to resolve these three items.

