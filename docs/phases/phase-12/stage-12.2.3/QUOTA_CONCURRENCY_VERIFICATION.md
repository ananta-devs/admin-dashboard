# VIBE — STAGE 12.2.3 QUOTA CONCURRENCY VERIFICATION
## PostgreSQL Advisory Locking & Atomic Quota Reservation

**Workspace:** `D:\vibe`  
**Phase:** 12 — Production Readiness, End-to-End Testing & Deployment Verification  
**Stage:** 12.2.3 — Security, Transaction Integrity & Test Isolation Remediation  
**Status:** VERIFIED & CONCURRENCY SAFE  
**Date:** November 4, 2026  

---

## 1. Objective & Challenge

In multi-tenant SaaS architectures, per-organization storage quotas prevent resource exhaustion and noisy-neighbor denial-of-service.

However, standard application-level quota checks suffer from Time-of-Check to Time-of-Use (TOCTOU) race conditions:
1. Two upload requests ($A$ and $B$) for the same tenant arrive simultaneously.
2. Both read the database: current usage = 0 MB, quota limit = 500 MB.
3. Request $A$ uploads 400 MB.
4. Request $B$ uploads 400 MB.
5. Both pass their application-level checks ($0 + 400 \le 500$).
6. Both commit, resulting in 800 MB used, breaching the 500 MB tenant quota by 60%.

To solve this without altering database schemas or introducing heavy table locks, VIBE utilizes PostgreSQL Transaction-Scoped Advisory Locks.

---

## 2. Technical Implementation

### 2.1 Transactional Advisory Locking Architecture
PostgreSQL advisory locks provide application-defined mutexes managed by the PostgreSQL lock manager.

In `backend/app/services/file_storage.py`:
```python
async with transaction(self.session):
    # Acquire organization-scoped transactional advisory lock to prevent TOCTOU quota race condition
    await self.session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext('file_quota_' || :org_id));"),
        {"org_id": str(organization_id)},
    )

    current_usage, _ = await self.repo.get_organization_usage(organization_id)
    if current_usage + file_size > quota_limit:
        raise ValidationError(
            f"Organization storage quota exceeded. Limit: {quota_limit} bytes, "
            f"Current: {current_usage} bytes, File: {file_size} bytes."
        )

    # Publish file and insert DB record
    ...
```

### 2.2 Key Properties of `pg_advisory_xact_lock`
1. **Transaction Scope:** Automatically acquired inside the transaction and automatically released on `COMMIT` or `ROLLBACK`. No danger of leaked locks if worker dies.
2. **Tenant Isolation:** The lock key is derived from `hashtext('file_quota_' || :org_id)`. Different organizations hash to different lock IDs and run concurrently with zero contention.
3. **Cross-Process Serialization:** The lock is enforced by PostgreSQL engine across all Python backend workers, Celery task workers, and API nodes.
4. **Zero Schema Migration:** No database schema modification or DDL is required.

---

## 3. Adversarial Concurrency Test Design & Results

### 3.1 Test Case: Concurrent Quota Overrun Race Condition
File: `backend/tests/integration/test_file_storage_concurrency.py` (`test_concurrent_uploads_prevent_quota_overrun`)
- **Setup:**
  - Tenant quota set to 800 bytes.
  - Two concurrent uploads initiated via `asyncio.gather()`:
    - Payload 1: 507 bytes
    - Payload 2: 487 bytes
  - Combined size: 994 bytes (> 800 bytes limit).
- **Behavior Under Test:**
  - One task acquires the advisory lock first, reads current usage (0 B), verifies $0 + 507 \le 800$, commits the record, and releases the lock.
  - The second task acquires the advisory lock next, reads current usage (507 B), computes $507 + 487 = 994 > 800$, raises `ValidationError("Organization storage quota exceeded")`, rolls back, and unlinks its staged file.
- **Verification Assertions:**
  - `len(successes) == 1`
  - `len(errors) == 1`
  - Error is `ValidationError` with message `"quota exceeded"`.
  - Database usage query returns exactly 1 file and does not exceed 800 bytes.

### 3.2 Test Case: Cross-Tenant Concurrency
File: `backend/tests/integration/test_file_storage_concurrency.py` (`test_cross_tenant_uploads_execute_independently`)
- **Setup:** Two different organizations execute concurrent uploads simultaneously.
- **Result:** Both complete immediately without deadlock or blocking.

---

## 4. Test Execution Summary

```
backend\tests\integration\test_file_storage_concurrency.py::test_concurrent_uploads_prevent_quota_overrun PASSED [ 50%]
backend\tests\integration\test_file_storage_concurrency.py::test_cross_tenant_uploads_execute_independently PASSED [100%]
============================== 2 passed in 1.11s ==============================
```

**Verdict:** PASS. Concurrency and TOCTOU vulnerability completely eliminated.

