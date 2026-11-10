# VIBE — PHASE 9 GATE 3
## STAGE 3.6: PRE-IMPLEMENTATION ARCHITECTURE & SECURITY AUDIT

**Workspace:** `D:\vibe`  
**Phase:** 9 — Enterprise BI, Interactive Dashboards & On-Demand Reporting  
**Gate:** 3 — Automated Report Execution & Secure PDF Generation  
**Stage:** 3.6 — Targeted Security Remediation & Independent Verification  
**Date:** November 3, 2026  
**Baseline Alembic Head:** `370908748232`  
**Python Sandbox Policy:** `PYTHON_SANDBOX_ENABLED=false`

---

## 1. CONFIRMED VULNERABILITIES & ARCHITECTURAL GAPS

1. **HMAC V1 Signature Downgrade Risk:**
   - In `backend/app/services/renderer_auth.py`, lines 71–75 explicitly evaluate `expected_sig_v1 = hmac.new(secret, f"{timestamp}:{nonce}:{method}:{path}:" + body)`.
   - No protocol version header (`X-Renderer-Signature-Version`) is required or verified.
   - Attackers or malformed clients can submit legacy signatures without method/path binding or version enforcement.

2. **Unbound Tenant and Report Identifiers:**
   - In `backend/app/services/renderer_auth.py`, `generate_signature` and `_verify_signature_and_skew` only canonicalize `METHOD\nPATH\nTIMESTAMP\nNONCE\nBODY_SHA256`.
   - `tenant_id` (`organization_id`) and `report_id` are passed as request headers or body fields but are **not cryptographically bound** to the signature.
   - An attacker or compromised caller could tamper with `X-Tenant-Id` or `X-Report-Id` without invalidating the cryptographic signature.

3. **Staging vs Final Storage Boundary & Publication Proof:**
   - The worker independently verifies the rendered PDF and promotes from staging to final, but explicit container boundary proofs and traversal attack regressions (symlinks, path escapes, cross-tenant staging poisoning) need automated verification against the storage and worker publication pipeline.

---

## 2. AFFECTED FILES & FUNCTIONS

| Component / File | Function(s) | Role & Vulnerability |
|---|---|---|
| `backend/app/services/renderer_auth.py` | `generate_signature`, `_verify_signature_and_skew`, `verify_request_async`, `verify_request` | Lacks version header check; accepts V1 fallback; does not sign `tenant_id` or `report_id`. |
| `backend/app/services/renderer_app.py` | `render_report`, `cancel_render` | Must extract, validate, and verify `X-Renderer-Signature-Version: 2`, `X-Tenant-Id`, `X-Report-Id`. Must verify body tenant/report match signed headers. |
| `backend/app/services/report_renderer.py` | `ReportRendererClient.render`, `ReportRendererClient.cancel` | Must supply `version="2"`, `tenant_id`, `report_id`, and appropriate headers during signature generation and HTTP dispatch. |
| `backend/app/tasks/reporting.py` | `_execute_report_run_async` | Ensures worker-owned independent PDF validation, checksumming, staging write, and atomic promotion with generation fencing. |
| `backend/app/services/report_storage.py` | `resolve_safe_path`, `atomic_promote`, `read_and_verify_bytes` | Traversal prevention, O_NOFOLLOW file descriptors, generation fencing. |

---

## 3. EXISTING SECURITY CONTROLS TO PRESERVE

- **Zero Container Privileges:** `vibe_report_renderer` runs as UID/GID 10001, read-only root, no docker socket, `no-new-privileges:true`.
- **Zero Credentials:** No `DATABASE_URL` or `REDIS_URL` in renderer container.
- **Network Isolation:** Dual-network bridge: renderer is on `renderer_net` (`internal: true`) with no database, Redis, or internet access.
- **Distributed Nonce Protection:** Redis atomic `SET report:nonce:{nonce} 1 NX EX 65` with production fail-closed semantics.
- **Python Sandbox Disabled:** `PYTHON_SANDBOX_ENABLED=false` strictly preserved across all environments.
- **Alembic Revision:** `370908748232` (head) preserved without extraneous migrations.

---

## 4. PROPOSED MINIMAL REMEDIATIONS

1. **Standardize V2 Canonical Signature:**
   ```text
   VIBE-RENDERER-V2
   METHOD
   PATH
   TENANT_ID
   REPORT_ID
   TIMESTAMP
   NONCE
   BODY_SHA256
   ```
2. **Strict Field Validation & Canonicalization:**
   - Normalize method to uppercase.
   - Normalize path to clean canonical form.
   - Validate `tenant_id` and `report_id`: reject newlines (`\r`, `\n`), control chars, or length > 128; normalize UUIDs to lowercase canonical string.
   - Exact raw body SHA-256 (`hashlib.sha256(raw_bytes).hexdigest()`).
3. **Strict Protocol Versioning & V1 Removal:**
   - Require `X-Renderer-Signature-Version: 2`. Reject missing header, unknown version, or V1 fallback.
   - Remove legacy V1 verification logic completely.
4. **Header and Body Consistency Checks:**
   - In `/render`: Verify `X-Tenant-Id` and `X-Report-Id` match the embedded JSON body's `organization_id` and `report_id`.
   - In `/cancel`: Verify `X-Tenant-Id` and `X-Report-Id` match the signed request and the active render job before cancelling.
5. **Worker-Owned Publication Rigor:**
   - Ensure the worker reads returned PDF bytes, enforces max size limits, verifies PDF structure with `pypdf`, computes SHA-256 independently, writes to staging, and promotes atomically under claim generation fencing.

---

## 5. REQUIRED REGRESSION TESTS

- **Auth Suite (`test_renderer_auth.py`):**
  - Reject V1 signature.
  - Reject missing version header.
  - Reject unknown version (e.g. "3" or "0").
  - Reject protocol downgrade attempt.
  - Reject tampered `X-Tenant-Id` or `X-Report-Id`.
  - Reject newline injection in tenant/report headers.
  - Reject body/header tenant mismatch.
  - Reject cross-tenant and cross-report cancellation.
  - Verify concurrent duplicate nonce reservation (Redis atomic NX).
  - Verify production fail-closed on Redis failure.
- **Artifact Security Suite:**
  - Verify renderer container cannot write to final run storage.
  - Verify path traversal and symlink escape attempts are rejected.
  - Verify stale generation cannot overwrite promoted artifact.
  - Verify cancelled run cannot publish final artifact.
- **Integration & Regression:**
  - Full pytest suite (508+ tests) passes with zero failures.
