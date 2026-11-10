# VIBE — STAGE 12.2.3 KNOWN GAPS & REMAINING RISKS
## Architecture Gaps, Deferred Capabilities & Operational Risk Assessment

**Workspace:** `D:\vibe`  
**Phase:** 12 — Production Readiness, End-to-End Testing & Deployment Verification  
**Stage:** 12.2.3 — Security, Transaction Integrity & Test Isolation Remediation  
**Status:** MONITORED & DOCUMENTED  
**Date:** November 4, 2026  

---

## 1. Executive Summary

While all active defects registered in Stage 12.2.2 have been fully remediated and verified, production-grade engineering requires transparent documentation of architectural boundaries, deferred capabilities, and operational residual risks.

---

## 2. Deferred Capabilities (Intentional Scope Boundaries)

### 2.1 AWS S3 / Cloud Object Storage Integration
- **Status:** DEFERRED.
- **Current State:** File Storage stores encrypted/hashed artifacts on the host filesystem (`./storage/user_files/{org_id}/`).
- **Residual Risk:** Multi-node horizontal scaling of backend API instances requires shared network storage (e.g., NFS, AWS EFS, or S3-compatible object storage via MinIO/Ceph).
- **Mitigation Path:** An S3 storage driver conforming to `FileStorageDriver` abstract base class can be introduced in a future phase without breaking the service API or database model.

### 2.2 Governed Python Script Sandbox (`PYTHON_SANDBOX_ENABLED=false`)
- **Status:** DEFERRED.
- **Current State:** Python analytics execution is disabled by configuration invariant.
- **Residual Risk:** None in production currently, as code execution endpoints reject requests with `501 Not Implemented` or disabled feature error.
- **Mitigation Path:** Full OCI container isolation via gVisor/Firecracker will be evaluated when user-defined formula script execution is enabled.

### 2.3 Production Migration Application to `vibe_enterprise`
- **Status:** PENDING AUTHORIZED MAINTENANCE WINDOW.
- **Current State:**
  - `vibe_enterprise`: Revision `370908748232` (No `stored_files` table).
  - `vibe_enterprise_test`: Revision `481909849343` (`stored_files` table active).
- **Residual Risk:** If the application connects to `vibe_enterprise` in production before migration `481909849343` is applied, `/files` routes will return database relation errors (`relation "stored_files" does not exist`).
- **Operational Requirement:** An authorized human database maintenance window must apply `alembic upgrade head` to `vibe_enterprise` prior to enabling File Storage in live production.

---

## 3. Residual Operational Considerations

### 3.1 Historical 25 Synthetic Users in `vibe_enterprise`
- **Context:** The 25 synthetic accounts created during early Stage 12.2.1 tests remain preserved in `vibe_enterprise` per human directive.
- **Assessment:**
  - Blast radius is isolated: all 25 users belong to isolated test organizations with zero connections to enterprise data.
  - Test database guard (`isolation_guard.py`) prevents any future test writes to `vibe_enterprise`.
- **Recommendation:** When an official production database cleanup is authorized, a targeted script referencing `docs/phase-12/stage-12.2.3/forensic_data.json` can safely prune those 25 organizations and memberships.

### 3.2 File Storage Physical Disk Capacity & Monitoring
- **Context:** Local disk storage requires host volume monitoring.
- **Current Mechanism:** `SystemHealthService` reports `host_free_disk_gb` via `shutil.disk_usage()`.
- **Operational Requirement:** Configure infrastructure alerts (e.g. Prometheus / Datadog) when host disk free space falls below 20%.

### 3.3 Periodic Storage Reconciliation Cron
- **Context:** `FileReconciliationService` provides `audit_storage()` and `prune_orphans()`.
- **Operational Requirement:** A nightly Celery beat task should run `FileReconciliationService.prune_orphans(destructive_confirmed=True)` to clean up any abandoned staging files ($> 1$ hour old).

---

## 4. Risk Acceptance Matrix

| Risk / Gap Description | Likelihood | Impact | Severity | Disposition |
| :--- | :---: | :---: | :---: | :--- |
| Single-node storage backend (local disk) | High | Medium | Medium | Accepted for single-instance / staging deployments; EFS/S3 planned for multi-node scale. |
| Pending migration on `vibe_enterprise` | Low | High | Medium | Accepted until scheduled production maintenance window executes `alembic upgrade head`. |
| 25 historical synthetic users in DB | Low | Negligible | Low | Accepted; fully isolated and documented in forensics register. |
| Ingestion of unindexed physical files on abrupt OS power loss | Very Low | Low | Low | Mitigated by `FileReconciliationService` automated audits. |

**Verdict:** All residual risks are documented, monitored, and within acceptable operational parameters.

