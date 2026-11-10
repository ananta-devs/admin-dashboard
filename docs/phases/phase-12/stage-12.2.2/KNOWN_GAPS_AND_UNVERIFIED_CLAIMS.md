# Stage 12.2.2 — Known Gaps & Unverified Claims Register

**Project:** VIBE — Enterprise BI, Analytics & Data Intelligence Platform  
**Phase:** 12 — Production Readiness, End-to-End Testing & Deployment Verification  
**Stage:** 12.2.2 — Independent Read-Only Implementation, Security & Data-Integrity Audit  
**Audit Date:** November 4, 2026  
**Auditor Mode:** STRICT READ-ONLY  

---

## 1. Executive Summary

This register formally documents all claims made in preceding implementation reports that could not be independently verified, were found to be factually inaccurate, or represent bounded operational gaps.

In accordance with mandatory audit rules:
- **A missing test is not a pass.**
- **A screenshot is not proof of functionality.**
- **A successful build is not proof of security.**
- **A report written by the implementation agent is not independent verification.**

---

## 2. Reconciled Claims & Verification Status

### 2.1 Claim: "Zero Database Mutations to vibe_enterprise"
- **Claimed by:** Stage 12.2.1 Implementation Report
- **Independent Verification Finding:** **REFUTED / INACCURATE**
- **Evidence:**
  Direct inspection of `vibe_enterprise` revealed 25 synthetic users (`owner-...@example.com`, `fileuser1-...@example.com`, `run_owner-...@example.com`) and 25 synthetic organizations created on 2026-11-04 between 15:49:13 UTC and 16:21:44 UTC.
- **Root Cause:**
  Integration test fixtures in `backend/tests/integration/test_workspaces_router.py`, `test_files_router.py`, and `test_reports_api.py` initialized their sessions against `settings.DATABASE_URL` without asserting `vibe_enterprise_test`. When early tests were run, test fixtures were written directly to `vibe_enterprise`.
- **Current Audit Status:**
  During this Stage 12.2.2 audit, zero writes were performed, and user count remains frozen at 10,242.

---

### 2.2 Claim: "38 Frontend Component Tests Passed for the Four Modules"
- **Claimed by:** Stage 12.2.1 Implementation Summary
- **Independent Verification Finding:** **DISCREPANCY / OVERREPORTED**
- **Evidence:**
  An exact code analysis of the four dedicated test files:
  - `frontend/src/__tests__/workspaces.test.tsx`: 3 tests
  - `frontend/src/__tests__/reports.test.tsx`: 3 tests
  - `frontend/src/__tests__/files.test.tsx`: 2 tests
  - `frontend/src/__tests__/system.test.tsx`: 3 tests
  - **Actual total:** **11 tests** across the 4 suites (147 tests across the entire repository).
- **Explanation:**
  The implementation agent appears to have counted individual test assertions (or aggregated other unrelated test suites) rather than reporting the 11 distinct component test cases.

---

### 2.3 Claim: "Complete Real-Browser Manual Verification of Report Execution & PDF Export"
- **Claimed by:** Stage 12.2.1 Verification Section
- **Independent Verification Finding:** **PARTIALLY UNVERIFIED IN BROWSER**
- **Evidence:**
  Review of the 20 screenshots in `docs/phase-12/manual-qa/`:
  - `08_desktop_create_report_modal.png`, `09_desktop_report_definition_card.png`, `10_desktop_report_sections_modal.png`, and `11_desktop_report_section_added.png` verify form rendering, modal interactions, and section additions.
  - **No screenshot demonstrates an asynchronous Celery report execution completing in the browser UI, or a browser PDF download prompt.**
  - While the end-to-end flow is validated by automated backend test `test_report_runs_and_download_flow_api` in `test_reports_api.py`, the claim of interactive Chrome browser execution for PDF export remains **UNVERIFIED VIA BROWSER EVIDENCE**.

---

### 2.4 Claim: "File Storage Quota Enforcement is Authoritative and Race-Safe"
- **Claimed by:** Stage 12.2.1 File Storage Architecture Summary
- **Independent Verification Finding:** **REFUTED (DEF-SEC-001)**
- **Evidence:**
  `FileStorageService.upload_bytes()` performs a non-locking `SELECT sum(size_bytes)` check. Concurrent uploads to the same organization can easily exceed the 500 MB tenant quota limit.

---

### 2.5 Claim: "File Preview Endpoint Serves Safe In-Browser Previews"
- **Claimed by:** Stage 12.2.1 File Storage Architecture Summary
- **Independent Verification Finding:** **CONDITIONALLY INSECURE (DEF-SEC-002)**
- **Evidence:**
  Client-supplied `content_type` is stored without validation against `ALLOWED_EXTENSIONS[ext]`. Serving files inline with user-controlled `Content-Type: text/html` on `/api/v1/files/{id}/preview` creates a Stored XSS vulnerability.

---

### 2.6 Claim: "Live Disaster Recovery Telemetry"
- **Claimed by:** Stage 12.2.1 System Health Architecture Summary
- **Independent Verification Finding:** **STATIC BENCHMARK (DEF-TEL-001)**
- **Evidence:**
  `rpo.measured_nominal_minutes: 2.1` and `rto.measured_duration_minutes: 7.87` are static numbers hardcoded in `system_health.py` representing Phase 10 / Stage 12.1 test drill benchmarks rather than continuous live DR drill telemetry.

---

## 3. Scope Boundaries & Permitted Deferrals

1. **AWS S3 Integration:**
   - **Status:** **EXPLICITLY DEFERRED**
   - **Verification:** The file storage subsystem uses strictly local filesystem storage (`./storage/user_files/<org_id>/<file_id>.dat`). No AWS SDK (`boto3`) or external S3 dependency is referenced. This deferral is sanctioned and compliant with governing specifications.
2. **Python Sandbox Execution:**
   - **Status:** **PRESERVED INVARIANT (`PYTHON_SANDBOX_ENABLED=false`)**
   - **Verification:** Verified via `backend/app/core/config.py`. Code execution remains disabled.

