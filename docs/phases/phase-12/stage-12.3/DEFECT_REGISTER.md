# Stage 12.3: Defect Register (Pass A & Pass B)

**Workspace:** `D:\vibe`  
**Stage:** 12.3 — Full Cross-Browser & Operational Acceptance Verification  
**Evaluation Mode:** Two-Pass Audit and Targeted Remediation  
**Date:** November 5, 2026  
**Status:** ALL REGISTERED DEFECTS REMEDIATED & VERIFIED  

---

## 1. Defect Classification Scheme

- **P0 (Critical):** Data loss, security breach, production DB mutation, cross-tenant isolation breakdown.
- **P1 (High):** Major functional blockage, unhandled crashes, broken CRUD operations.
- **P2 (Medium):** Minor functional bug, non-fatal schema mismatch, missing UI indicator.
- **P3 (Low):** Cosmetic styling discrepancy, linting warning, non-breaking configuration.

---

## 2. Master Defect Register

| Defect ID | Severity | Category | Description | Discovery Pass | Resolution Status |
| :--- | :---: | :--- | :--- | :---: | :---: |
| **DEF-12.3-01** | **P1** | Test Isolation | Legacy integration tests connected to `settings.DATABASE_URL` instead of enforced test database when env vars were not passed in shell. | Pass A | **REMEDIATED (Pass B)** |
| **DEF-12.3-02** | **P2** | Frontend Routing | Production build `next.config.ts` required `BACKEND_INTERNAL_URL` at build time to populate rewrites manifest. | Pass A | **REMEDIATED (Pass B)** |
| **DEF-12.3-03** | **P2** | Responsive Drawer | Mobile viewport drawer brand text selector collided with hidden desktop sidebar text selector. | Pass A | **REMEDIATED (Pass B)** |
| **DEF-12.3-04** | **P3** | Linting | ESLint flagged CJS `require()` in `e2e/stage_12_3/` automation scripts. | Pass A | **REMEDIATED (Pass B)** |

---

## 3. Defect Analysis & Root Causes

### DEF-12.3-01: Integration Test Database Target Fallback
- **Root Cause:** In `backend/tests/integration/test_analytics_api.py`, fixtures constructed `test_engine = create_async_engine(settings.DATABASE_URL)`. `conftest.py` verified the test database URL in `pytest_sessionstart` but did not explicitly rebind `settings.DATABASE_URL = test_url`.
- **Remediation:** Updated `pytest_sessionstart` in `backend/tests/conftest.py` to forcefully set `settings.DATABASE_URL = test_url`, `settings.DATABASE_SYNC_URL = test_url.replace(...)`, and set environment variables. Any fixture reading `settings.DATABASE_URL` is now strictly forced to `vibe_enterprise_test`.
- **Verification:** Ran integration tests without environment variables; confirmed zero writes to `vibe_enterprise` and clean isolation to `vibe_enterprise_test`.

### DEF-12.3-02: Next.js API Reverse Proxy Rewrites Manifest
- **Root Cause:** Next.js compiles `rewrites` into `routes-manifest.json` during `next build`. If `BACKEND_INTERNAL_URL` was not supplied during the build phase, rewrites remained empty in production mode.
- **Remediation:** Built production frontend with `BACKEND_INTERNAL_URL=http://127.0.0.1:8000 npm run build`, baking the proxy rule into `routes-manifest.json`.
- **Verification:** Tested `curl.exe http://127.0.0.1:3000/api/v1/health` through the reverse proxy; verified HTTP 200 and successful backend proxying.

### DEF-12.3-03: Mobile Viewport Drawer Selector Collision
- **Root Cause:** When querying `page.locator("text=VIBE ENTERPRISE").first()`, Playwright selected the desktop sidebar element which is hidden on mobile (`width < 768px`).
- **Remediation:** Updated selector to query the mobile drawer container `page.locator("[role='dialog']").getByText("VIBE ENTERPRISE")`.
- **Verification:** Tested in Chrome at `393x851`; verified mobile drawer opens, brand is visible, and Escape key cleanly closes drawer.

### DEF-12.3-04: ESLint CJS Import Restrictions
- **Root Cause:** `@typescript-eslint/no-require-imports` was enforced on all files including Node.js scripts in `e2e/**`.
- **Remediation:** Updated `frontend/eslint.config.mjs` to disable `no-require-imports` for `e2e/**`.
- **Verification:** `npm run lint` and `npx tsc --noEmit` executed with 0 errors.

