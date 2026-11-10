# Stage 12.2.2 — Comprehensive Defect Register

**Project:** VIBE — Enterprise BI, Analytics & Data Intelligence Platform  
**Phase:** 12 — Production Readiness, End-to-End Testing & Deployment Verification  
**Stage:** 12.2.2 — Independent Read-Only Implementation, Security & Data-Integrity Audit  
**Audit Date:** November 4, 2026  
**Auditor Mode:** STRICT READ-ONLY (No Defect Modifications Executed)  

---

## 1. Defect Severity Summary Matrix

| Defect ID | Affected Module | Severity | Summary | Remediation Required Before |
|:---|:---|:---|:---|:---|
| **DEF-ISO-001** | Test Infrastructure | **CRITICAL** | Test fixture database isolation failure (25 users / 25 orgs leaked to `vibe_enterprise`) | Stage 12.3 / Production |
| **DEF-SEC-001** | File Storage | **HIGH** | Quota TOCTOU concurrency race condition allows tenant quota bypass | Stage 12.3 / Production |
| **DEF-SEC-002** | File Storage | **HIGH** | Unvalidated `content_type` passthrough on inline preview allows Stored XSS | Stage 12.3 / Production |
| **DEF-SEC-003** | File Storage | **HIGH** | Disk write occurs prior to DB commit, causing orphaned physical files on rollback | Stage 12.3 / Production |
| **DEF-SEC-004** | File Storage | **HIGH** | Upload and deletion endpoints protected only by `organization.read` (Viewer access) | Stage 12.3 / Production |
| **DEF-TEL-001** | System Health | **MEDIUM** | Disaster recovery RPO/RTO metrics return hardcoded drill benchmarks | Stage 12.3 |
| **DEF-DOC-001** | Documentation | **LOW** | Stage 12.2.1 report claimed 38 frontend tests, but only 11 tests exist in new suites | Documentation Correction |

---

## 2. Comprehensive Defect Specifications

### 2.1 DEF-ISO-001: Test Fixture Database Infiltration (CRITICAL)
- **Defect ID:** `DEF-ISO-001`
- **Affected Module:** Backend Integration Test Infrastructure (`backend/tests/integration/`)
- **Severity:** **CRITICAL**
- **Exact File & Line:**
  `backend/tests/integration/test_workspaces_router.py`: lines 30, 48–56;
  `backend/tests/integration/test_files_router.py`: lines 28–45;
  `backend/tests/integration/test_reports_api.py`: lines 32–50.
- **Reproduction / Verification Method:**
  Query `vibe_enterprise` for users created between 15:49 UTC and 16:21 UTC on 2026-11-04:
  `SELECT email, created_at FROM users WHERE email LIKE '%@example.com' ORDER BY created_at DESC;`
- **Actual Observed Behavior:**
  25 synthetic test users and 25 synthetic test organizations exist in the development database (`vibe_enterprise`).
- **Expected Behavior:**
  Integration test fixtures must fail-closed: if `settings.DATABASE_URL` does not explicitly target `vibe_enterprise_test`, the test run must abort immediately before creating any session or fixtures.
- **Security / Business Impact:**
  Breach of database preservation invariants. In production environments, running automated test suites without strict URL fences risks catastrophic data contamination or destruction of production customer records.
- **Recommended Correction:**
  Add a session-level pre-run assertion in `conftest.py` and all integration fixtures:
  ```python
  assert "vibe_enterprise_test" in str(settings.DATABASE_URL), \
      "FATAL: Integration tests must target vibe_enterprise_test!"
  ```
- **Evidence Reference:** `docs/phase-12/stage-12.2.2/BASELINE_AND_DATABASE_RECONCILIATION.md`, Section 4.

---

### 2.2 DEF-SEC-001: Storage Quota TOCTOU Concurrency Race Condition (HIGH)
- **Defect ID:** `DEF-SEC-001`
- **Affected Module:** File Storage (`backend/app/services/file_storage.py`)
- **Severity:** **HIGH**
- **Exact File & Line:** `backend/app/services/file_storage.py`, lines 138–145
- **Reproduction / Verification Method:**
  Execute two simultaneous upload requests of 15 MB to an organization that currently has 480 MB consumed (out of a 500 MB quota).
- **Actual Observed Behavior:**
  Both requests concurrently execute `current_used, _ = await self.repo.get_organization_usage(organization_id)` outside of a row-level lock. Both evaluate `480 + 15 <= 500` as true and proceed to write to disk and insert database records, resulting in 510 MB consumed.
- **Expected Behavior:**
  Quota calculation and record creation must be atomic or protected by transactional row-locking (`SELECT ... FOR UPDATE`) or an advisory lock on the organization UUID.
- **Security / Business Impact:**
  Malicious or high-frequency tenants can bypass storage quotas, exhausting host disk space and causing Denial of Service (DoS) across other tenants.
- **Recommended Correction:**
  Lock the parent organization row during upload processing:
  ```python
  await session.execute(select(Organization).where(Organization.id == organization_id).with_for_update())
  ```
- **Evidence Reference:** `docs/phase-12/stage-12.2.2/FILE_STORAGE_SECURITY_AUDIT.md`, Section 3.1.

---

### 2.3 DEF-SEC-002: Inline Preview MIME Confusion / Stored XSS Risk (HIGH)
- **Defect ID:** `DEF-SEC-002`
- **Affected Module:** File Storage (`backend/app/services/file_storage.py`, `backend/app/api/v1/routers/files.py`)
- **Severity:** **HIGH**
- **Exact File & Line:**
  `backend/app/services/file_storage.py`, lines 173–174;
  `backend/app/api/v1/routers/files.py`, lines 163–184.
- **Reproduction / Verification Method:**
  Upload a file named `dataset.csv` containing HTML/JS with JSON payload `{"content_type": "text/html", ...}`. Send a GET request to `/api/v1/files/{file_id}/preview`.
- **Actual Observed Behavior:**
  FastAPI returns `FileResponse` with `Content-Type: text/html` and `Content-Disposition: inline`. The browser renders and executes the HTML/JS payload in the application origin.
- **Expected Behavior:**
  1. `upload_bytes()` must validate that client-provided `content_type` belongs to `ALLOWED_EXTENSIONS[ext]`.
  2. The `/preview` endpoint must force `text/plain` for CSV/TXT/JSON or enforce `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'`.
- **Security / Business Impact:**
  Stored Cross-Site Scripting (XSS). An attacker can upload a malicious file that steals session tokens or performs unauthorized state-changing operations when previewed by an administrator.
- **Recommended Correction:**
  Sanitize and restrict MIME types in `_validate_filename_and_extension()`:
  ```python
  if content_type and content_type not in ALLOWED_EXTENSIONS[ext]:
      content_type = ALLOWED_EXTENSIONS[ext][0]
  ```
- **Evidence Reference:** `docs/phase-12/stage-12.2.2/FILE_STORAGE_SECURITY_AUDIT.md`, Section 3.2.

---

### 2.4 DEF-SEC-003: Physical Filesystem Artifact Abandonment on DB Failure (HIGH)
- **Defect ID:** `DEF-SEC-003`
- **Affected Module:** File Storage (`backend/app/services/file_storage.py`)
- **Severity:** **HIGH**
- **Exact File & Line:** `backend/app/services/file_storage.py`, lines 163–182
- **Reproduction / Verification Method:**
  Simulate a database failure during `self.repo.create(stored_file)` (e.g. unique constraint violation or forced disconnection).
- **Actual Observed Behavior:**
  The physical file was already written to disk via `target_path.write_bytes(content)`. When the database transaction rolls back, the physical `.dat` file remains on disk unreferenced by any database record.
- **Expected Behavior:**
  Filesystem mutations and database commits must be atomic: if the database transaction fails, the physical file must be unlinked.
- **Security / Business Impact:**
  Host disk space leakage. Uncontrolled accumulation of orphaned files that cannot be enumerated or reclaimed via database operations.
- **Recommended Correction:**
  Wrap database operations in `try...except`:
  ```python
  try:
      async with transaction(self.session):
          await self.repo.create(stored_file)
  except Exception:
      target_path.unlink(missing_ok=True)
      raise
  ```
- **Evidence Reference:** `docs/phase-12/stage-12.2.2/FILE_STORAGE_SECURITY_AUDIT.md`, Section 3.3.

---

### 2.5 DEF-SEC-004: Overly Permissive RBAC on Upload and Deletion (HIGH)
- **Defect ID:** `DEF-SEC-004`
- **Affected Module:** File Storage Router (`backend/app/api/v1/routers/files.py`)
- **Severity:** **HIGH**
- **Exact File & Line:** `backend/app/api/v1/routers/files.py`, lines 71, 190
- **Reproduction / Verification Method:**
  Authenticate as a user with only the `Viewer` role (possessing `organization.read`). Send `POST /api/v1/files/upload` or `DELETE /api/v1/files/{file_id}`.
- **Actual Observed Behavior:**
  The request succeeds because the endpoints only require `organization.read`.
- **Expected Behavior:**
  Mutating operations (upload, delete) should require write or admin permissions (e.g. `organization.admin` or dedicated `files.upload` / `files.delete`).
- **Security / Business Impact:**
  Privilege escalation. Read-only viewers can deplete tenant storage quotas or permanently delete mission-critical datasets.
- **Recommended Correction:**
  Update route dependencies to require administrative or write permissions:
  `dependencies=[Depends(require_permission("organization.settings.update")), Depends(verify_csrf_token)]`.
- **Evidence Reference:** `docs/phase-12/stage-12.2.2/FILE_STORAGE_SECURITY_AUDIT.md`, Section 3.4.

---

### 2.6 DEF-TEL-001: Disaster Recovery Telemetry Hardcoded Nominal Benchmarks (MEDIUM)
- **Defect ID:** `DEF-TEL-001`
- **Affected Module:** System Health Service (`backend/app/services/system_health.py`)
- **Severity:** **MEDIUM**
- **Exact File & Line:** `backend/app/services/system_health.py`, lines 168–181
- **Reproduction / Verification Method:**
  Inspect the `backup_recovery` payload returned by `/api/v1/system/telemetry`.
- **Actual Observed Behavior:**
  Fields `rpo.measured_nominal_minutes: 2.1` and `rto.measured_duration_minutes: 7.87` are hardcoded static numbers.
- **Expected Behavior:**
  Telemetry should clearly label benchmark values as historical drill metrics and expose `last_drill_at` rather than presenting them as real-time measurements.
- **Security / Business Impact:**
  Misleading operational status if disaster recovery mechanisms degrade unnoticed.
- **Recommended Correction:**
  Rename fields to `nominal_benchmark_rpo_minutes` and add `source: "phase_10_drill_benchmark"`.
- **Evidence Reference:** `docs/phase-12/stage-12.2.2/SYSTEM_HEALTH_INDEPENDENT_AUDIT.md`, Section 3.

---

### 2.7 DEF-DOC-001: Frontend Component Test Count Overreporting (LOW)
- **Defect ID:** `DEF-DOC-001`
- **Affected Module:** Documentation / Stage 12.2.1 Implementation Summary
- **Severity:** **LOW**
- **Exact File & Line:** `STAGE_12_2_1_ALL_IN_ONE_MASTER_REPORT.md`, Section 1
- **Actual Observed Behavior:**
  The Stage 12.2.1 report states "38 frontend component tests passed". The 4 new test files contain exactly 11 component tests total.
- **Expected Behavior:**
  Accurate reporting of test case totals distinguishing module-specific tests from assertion counts or full repository test suites.
- **Security / Business Impact:**
  Inaccurate audit tracking.
- **Recommended Correction:**
  Update documentation to record 11 module-specific component tests and 147 total Vitest tests across the repository.
- **Evidence Reference:** `docs/phase-12/stage-12.2.2/TEST_COVERAGE_AND_EVIDENCE_AUDIT.md`, Section 2.2.

