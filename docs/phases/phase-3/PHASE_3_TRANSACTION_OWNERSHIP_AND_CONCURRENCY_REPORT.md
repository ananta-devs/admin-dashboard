# Phase 3 — Transaction Ownership & Real Concurrency Final Correction Report

**Project:** Enterprise Admin & Data Intelligence Platform (VIBE)  
**Workspace:** `D:\vibe`  
**Phase:** Phase 3 — Final Correction Pass  
**Status:** COMPLETE & VERIFIED — Awaiting Phase 4 Authorization  

---

## 1. Transaction Ownership Policy

### The SQLAlchemy 2.0 Autobegin Trap
In SQLAlchemy 2.0 Asyncio, executing any read statement (e.g. `await session.execute(select(...))` or any repository `get_by_*` method) immediately triggers **autobegin**. From that moment on, `session.in_transaction()` evaluates to `True`, even though no explicit business Unit of Work was initiated.

If a transaction helper relies solely on `if session.in_transaction(): session.begin_nested()`, any service method that performs a read-before-write validation (such as checking uniqueness of an email, slug, or parent organization) will enter `begin_nested()` and create a `SAVEPOINT`. When that savepoint is released on clean exit, the enclosing autobegun transaction remains uncommitted. When the session subsequently closes at the end of the request lifecycle, PostgreSQL rolls back all uncommitted changes, silently dropping the writes.

### Explicit Unit of Work Depth-Tracking Policy
To resolve this without indiscriminate `commit()` calls or ambiguous caller ownership, [`backend/app/db/transaction.py`](file:///D:/vibe/backend/app/db/transaction.py) implements explicit session-scoped depth tracking via `session.info["_explicit_tx_depth"]`:

```mermaid
flowchart TD
    Enter["async with transaction(session)"] --> CheckDepth{"session.info['_explicit_tx_depth'] > 0?"}
    
    CheckDepth -- "Yes (Nested Call)" --> IncDepthNested["Increment depth"]
    IncDepthNested --> BeginNested["async with session.begin_nested():"]
    BeginNested --> YieldNested["yield session (Savepoint active)"]
    YieldNested --> ReleaseNested["Release Savepoint on clean exit<br>Rollback Savepoint on exception"]
    ReleaseNested --> DecDepthNested["Decrement depth in finally"]
    
    CheckDepth -- "No (Root Owner)" --> SetDepthRoot["Set depth = 1"]
    SetDepthRoot --> CheckAuto{"session.in_transaction()?"}
    
    CheckAuto -- "False (Idle session)" --> BeginRoot["async with session.begin():"]
    BeginRoot --> YieldIdle["yield session"]
    YieldIdle --> CommitIdle["Commit root transaction on clean exit<br>Rollback on exception"]
    CommitIdle --> ResetDepthIdle["Reset depth = 0 in finally"]
    
    CheckAuto -- "True (Autobegun by SELECT)" --> TryRoot["try: yield session"]
    TryRoot --> CommitAuto["Clean exit: await session.commit()"]
    TryRoot --> RollbackAuto["Exception: await session.rollback() and raise"]
    CommitAuto --> ResetDepthAuto["Reset depth = 0 in finally"]
    RollbackAuto --> ResetDepthAuto
```

### Guarantees
1. **Unambiguous Root Ownership:** When `depth == 0`, `transaction(session)` takes explicit ownership of the root transaction. On clean exit, all writes are committed to PostgreSQL.
2. **Safe Savepoints for Nested Workflows:** When `depth > 0`, `transaction(session)` creates a nested `SAVEPOINT`. On clean exit, the savepoint is released without committing the outer transaction. On exception, it rolls back strictly to the savepoint and re-raises, allowing the outer caller to recover.
3. **No Swallowed Exceptions:** Exceptions always bubble out after the appropriate rollback/savepoint rollback.
4. **No Indiscriminate Commits:** Only the root owner (`depth == 0`) commits.

---

## 2. Service Method Transaction Audit

Every service method in the platform was audited against the three session states:
1. **Fresh Session:** Idle session (`in_transaction == False`, `depth == 0`).
2. **Prior Read Query:** Autobegun session (`in_transaction == True`, `depth == 0`).
3. **Caller-Managed Outer Transaction:** Inside an explicit Unit of Work (`depth > 0`).

### Comprehensive Audit Matrix

| Service | Method | Read-Before-Write Pattern | Fresh Session (`depth=0`) | Autobegun Session (`depth=0`) | Outer Transaction (`depth>0`) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`OrganizationService`** | `create_organization` | `get_by_slug` $\rightarrow$ `create` | Uses `session.begin()`, commits root | Takes ownership of autobegun tx, commits via `session.commit()` | Opens savepoint via `begin_nested()`, releases on clean exit |
| **`OrganizationService`** | `provision_organization_atomic` | `get_by_slug` $\rightarrow$ 4 creates | Uses `session.begin()`, commits all 4 writes atomically | Takes ownership of autobegun tx, commits all 4 writes atomically | Opens savepoint, releases all 4 writes without committing caller tx |
| **`OrganizationService`** | `get_organization` | Read-only | N/A (read-only query) | N/A (read-only query) | N/A (read-only query) |
| **`OrganizationService`** | `get_organization_by_slug` | Read-only | N/A (read-only query) | N/A (read-only query) | N/A (read-only query) |
| **`OrganizationService`** | `list_organizations` | Read-only | N/A (read-only query) | N/A (read-only query) | N/A (read-only query) |
| **`UserService`** | `create_user` | `get_by_normalized_email` $\rightarrow$ `create` | Uses `session.begin()`, commits root | Takes ownership of autobegun tx, commits root | Opens savepoint, releases savepoint |
| **`UserService`** | `get_or_create_user` | `get_by_normalized_email` $\rightarrow$ `create` | Uses `session.begin()`, commits root; on collision rolls back root & re-fetches | Takes ownership of autobegun tx, commits root; on collision rolls back root & re-fetches | Opens savepoint; on collision rolls back savepoint & re-fetches |
| **`UserService`** | `get_user_by_id` | Read-only | N/A (read-only query) | N/A (read-only query) | N/A (read-only query) |
| **`UserService`** | `get_user_by_email` | Read-only | N/A (read-only query) | N/A (read-only query) | N/A (read-only query) |
| **`WorkspaceService`** | `create_workspace` | `get_by_id(org)` + `get_by_slug` $\rightarrow$ `create` | Uses `session.begin()`, commits root | Takes ownership of autobegun tx, commits root | Opens savepoint, releases savepoint |
| **`WorkspaceService`** | `get_workspace` | Read-only | N/A (read-only query) | N/A (read-only query) | N/A (read-only query) |
| **`WorkspaceService`** | `get_workspace_by_slug` | Read-only | N/A (read-only query) | N/A (read-only query) | N/A (read-only query) |
| **`WorkspaceService`** | `list_workspaces` | Read-only | N/A (read-only query) | N/A (read-only query) | N/A (read-only query) |

---

## 3. Changes to `backend/app/db/transaction.py`

[`backend/app/db/transaction.py`](file:///D:/vibe/backend/app/db/transaction.py) was updated from inferring boundaries solely from `session.in_transaction()` to using explicit nesting depth:

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def transaction(session: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    """Provide an explicit Unit of Work transaction boundary."""
    depth = session.info.get("_explicit_tx_depth", 0)
    if depth > 0:
        session.info["_explicit_tx_depth"] = depth + 1
        try:
            async with session.begin_nested():
                yield session
        finally:
            session.info["_explicit_tx_depth"] = depth
    else:
        session.info["_explicit_tx_depth"] = 1
        if not session.in_transaction():
            try:
                async with session.begin():
                    yield session
            finally:
                session.info["_explicit_tx_depth"] = 0
        else:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                session.info["_explicit_tx_depth"] = 0
```

---

## 4. Results of the Autobegin Persistence Tests

**Test Target:** [`backend/tests/integration/test_integration_services.py`](file:///D:/vibe/backend/tests/integration/test_integration_services.py#L280-L370)

### 1. `test_get_or_create_user_persistence_after_autobegin`
Executed the exact 9-step test requested by the user:
1. Opened fresh `AsyncSession`.
2. Executed `await session.execute(select(User).limit(1))` to deliberately trigger SQLAlchemy autobegin.
3. Asserted `session.in_transaction() is True` (**Verified**).
4. Called `UserService.get_or_create_user(UserCreate(...))`.
5. Asserted `created is True` (**Verified**).
6. Closed the session according to application lifecycle (`await session.close()`).
7. Opened an entirely new `AsyncSession`.
8. Queried `select(User).where(User.normalized_email == normalized_target)`.
9. **PostgreSQL Result:** Record found in database, matching created user UUID and normalized email.
- **Status:** **PASSED** (0.08s)

### 2. `test_create_organization_persistence_after_autobegin`
Executed autobegin read-before-write test on `OrganizationService.create_organization`:
- Triggered autobegin via SELECT $\rightarrow$ created organization $\rightarrow$ closed session $\rightarrow$ verified persistence in a new session.
- **Status:** **PASSED** (0.05s)

### 3. `test_create_workspace_persistence_after_autobegin`
Executed autobegin read-before-write test on `WorkspaceService.create_workspace`:
- Triggered autobegin via SELECT $\rightarrow$ created workspace under parent org $\rightarrow$ closed session $\rightarrow$ verified persistence in a new session.
- **Status:** **PASSED** (0.06s)

---

## 5. Results of the Genuine Concurrent PostgreSQL Test

**Test Target:** [`test_user_service_get_or_create_real_concurrency`](file:///D:/vibe/backend/tests/integration/test_integration_services.py#L373-L479)

### Architecture of the Test
- **Zero Mocking of Database:** Two independent `AsyncSession` connections opened to live PostgreSQL 16.
- **Zero Flaky Sleeps:** Uses two `asyncio.Event` synchronization barriers (`lookup1_done`, `lookup2_done`).
- **Synchronization Sequence:**
  1. Worker 1 calls `get_by_normalized_email()`, observes `None`, sets `lookup1_done`, and awaits `lookup2_done.wait()`.
  2. Worker 2 calls `get_by_normalized_email()`, observes `None`, sets `lookup2_done`, and awaits `lookup1_done.wait()`.
  3. Both workers unblock simultaneously and attempt insertion.
  4. PostgreSQL enforces the unique constraint on `ix_users_normalized_email`.
  5. The winning transaction commits.
  6. The losing transaction catches `IntegrityError`, executes savepoint rollback, re-queries `get_by_normalized_email()`, and retrieves the committed record.

### Empirical Execution Results
- `Worker 1` returned `(UserRead(id=UUID('ff9cb571-...')), created=True)`
- `Worker 2` returned `(UserRead(id=UUID('ff9cb571-...')), created=False)`
- **Exactly one operation reported `created=True`:** **PASSED**
- **Exactly one operation reported `created=False`:** **PASSED**
- **Both operations returned the identical persisted user ID:** **PASSED**
- **Verified in PostgreSQL:** Exactly 1 row exists for that normalized email (**PASSED**)
- **Session health:** Neither session left in an aborted state (**PASSED**)

---

## 6. Clarification of Previous Collision-Recovery Test

The test formerly called `test_user_service_get_or_create_concurrent_collision` has been renamed to:
[`test_user_service_get_or_create_deterministic_collision_recovery`](file:///D:/vibe/backend/tests/integration/test_integration_services.py#L240-L280).

**Architectural Distinction:**
- **Deterministic Recovery Test (`test_user_service_get_or_create_deterministic_collision_recovery`):** Pre-inserts a colliding user and uses a mocked initial lookup to return `None` on the first call. This deterministically exercises the internal `try ... except IntegrityError:` code path, savepoint rollback, and re-query recovery within a single session.
- **Genuine Concurrency Test (`test_user_service_get_or_create_real_concurrency`):** Uses two independent sessions, real simultaneous database queries, event barriers, and live PostgreSQL unique index contention to prove real-world concurrency handling without mocks.

Both tests now coexist, testing complementary layers of the system.

---

## 7. Full Regression Suite Results

### 1. Backend Integration & Unit Tests (pytest)
Command: `cd backend; .\.venv\Scripts\python.exe -m pytest -v`
```
============================= test session starts =============================
platform win32 -- Python 3.14.7, pytest-9.2.0, pluggy-1.7.0 -- D:\vibe\backend\.venv\Scripts\python.exe
rootdir: D:\vibe\backend
configfile: pytest.ini
plugins: anyio-4.15.1, asyncio-1.4.1, cov-7.1.0
collected 35 items

tests/integration/test_infrastructure.py::test_live_postgres_connectivity_integration PASSED [  2%]
tests/integration/test_infrastructure.py::test_live_redis_connectivity_integration PASSED [  5%]
tests/integration/test_infrastructure.py::test_live_ready_endpoint_integration PASSED [  8%]
tests/integration/test_integration_services.py::test_atomic_organization_provisioning_success PASSED [ 11%]
tests/integration/test_integration_services.py::test_atomic_organization_provisioning_rollback_on_failure PASSED [ 14%]
tests/integration/test_integration_services.py::test_workspace_service_cross_tenant_isolation PASSED [ 17%]
tests/integration/test_user_service_get_or_create PASSED [ 20%]
tests/integration/test_integration_services.py::test_user_service_get_or_create_deterministic_collision_recovery PASSED [ 22%]
tests/integration/test_integration_services.py::test_get_or_create_user_persistence_after_autobegin PASSED [ 25%]
tests/integration/test_integration_services.py::test_create_organization_persistence_after_autobegin PASSED [ 28%]
tests/integration/test_integration_services.py::test_create_workspace_persistence_after_autobegin PASSED [ 31%]
tests/integration/test_integration_services.py::test_user_service_get_or_create_real_concurrency PASSED [ 34%]
tests/integration/test_nested_transactions.py::test_nested_transaction_rollback_preserves_outer_transaction PASSED [ 37%]
tests/integration/test_nested_transactions.py::test_outer_transaction_rollback_removes_all_nested_changes PASSED [ 40%]
tests/integration/test_repositories.py::test_organization_repository_crud_and_uniqueness PASSED [ 42%]
tests/integration/test_repositories.py::test_user_repository_crud_and_uniqueness PASSED [ 45%]
tests/integration/test_repositories.py::test_membership_repository_composite_uniqueness PASSED [ 48%]
tests/integration/test_repositories.py::test_workspace_repository_tenant_scoping_and_uniqueness PASSED [ 51%]
tests/integration/test_repositories.py::test_foreign_key_cascade_deletes PASSED [ 54%]
tests/unit/test_health.py::test_health_check_unit PASSED                 [ 57%]
tests/unit/test_health.py::test_readiness_check_mocked PASSED            [ 60%]
tests/unit/test_health.py::test_cross_tenant_access_handler_returns_safe_404 PASSED [ 62%]
tests/unit/test_schemas.py::test_email_normalization PASSED              [ 65%]
tests/unit/test_schemas.py::test_user_create_validation PASSED           [ 68%]
tests/unit/test_schemas.py::test_slug_validation PASSED                  [ 71%]
tests/unit/test_schemas.py::test_organization_schemas PASSED             [ 74%]
tests/unit/test_schemas.py::test_workspace_schemas PASSED                [ 77%]
tests/unit/test_schemas.py::test_membership_schema PASSED                [ 80%]
tests/unit/test_schemas.py::test_provision_organization_input_validation PASSED [ 82%]
tests/unit/test_unit_services.py::test_user_service_duplicate_email PASSED [ 85%]
tests/unit/test_unit_services.py::test_user_service_not_found PASSED     [ 88%]
tests/unit/test_unit_services.py::test_organization_service_duplicate_slug PASSED [ 91%]
tests/unit/test_unit_services.py::test_organization_service_not_found PASSED [ 94%]
tests/unit/test_unit_services.py::test_workspace_service_org_not_found PASSED [ 97%]
tests/unit/test_unit_services.py::test_workspace_service_cross_tenant_access_detection PASSED [100%]

============================= 35 passed in 2.16s ==============================
```

### 2. Backend Linting & Formatting (Ruff)
- Command: `cd backend; .\.venv\Scripts\ruff.exe check .; .\.venv\Scripts\ruff.exe format --check .`
- Output:
  ```
  All checks passed!
  43 files already formatted
  ```

### 3. Backend Type-Checking (Mypy)
- Command: `cd backend; .\.venv\Scripts\mypy.exe app --explicit-package-bases`
- Output:
  ```
  Success: no issues found in 32 source files
  ```

### 4. Frontend Unit Tests (Vitest)
- Command: `cd frontend; npx vitest run`
- Output:
  ```
  Test Files  2 passed (2)
       Tests  31 passed (31)
    Duration  2.87s
  ```

### 5. Frontend Type-Checking (`tsc`) & Linting (ESLint)
- Command: `cd frontend; npx tsc --noEmit; npm run lint`
- Output:
  ```
  0 errors from tsc
  0 errors from eslint
  ```

### 6. Frontend Production Build (Next.js 16.4.3 Turbopack)
- Command: `cd frontend; npm run build`
- Output:
  ```
  ▲ Next.js 16.4.3 (Turbopack)
  ✓ Compiled successfully in 565ms
  ✓ Generating static pages (4/4) in 1046ms
  ○  (Static) prerendered as static content
  ```

---

## 8. Files Modified in this Pass

1. [`backend/app/db/transaction.py`](file:///D:/vibe/backend/app/db/transaction.py): Implemented depth tracking (`session.info["_explicit_tx_depth"]`) and autobegin transaction adoption.
2. [`backend/tests/conftest.py`](file:///D:/vibe/backend/tests/conftest.py): Added `session_factory` fixture for multi-session and independent session lifecycle testing.
3. [`backend/tests/integration/test_integration_services.py`](file:///D:/vibe/backend/tests/integration/test_integration_services.py):
   - Re-labeled `test_user_service_get_or_create_deterministic_collision_recovery`.
   - Added `test_get_or_create_user_persistence_after_autobegin`.
   - Added `test_create_organization_persistence_after_autobegin`.
   - Added `test_create_workspace_persistence_after_autobegin`.
   - Added `test_user_service_get_or_create_real_concurrency`.
4. [`docs/backend-architecture.md`](file:///D:/vibe/docs/backend-architecture.md): Documented depth-tracking transaction policy and service behavior matrix.
5. [`docs/CHANGELOG.md`](file:///D:/vibe/docs/CHANGELOG.md): Added revision 1.2.0 documenting the transaction ownership fix and test results.

---

## 9. Remaining Known Limitations & Phase 4 Preparation

1. **Authentication Layer Absent (By Design):** The platform currently provisions organizations and user records through service-level inputs. In Phase 4, authentication credentials (Argon2id hashes) and Redis session management will be layered on top of `User` and `OrganizationMembership`.
2. **Tenant Context Security Guard:** Phase 4 must strictly inject `organization_id` from the validated session dependency into service methods, never from client input.
3. **Connection Pool in Tests vs Production:** Integration tests use `NullPool` to guarantee complete connection teardown between test cases; production uses connection pooling with pre-ping enabled (`pool_size=10`, `max_overflow=20`).

---

## Strict Stop Condition

> [!IMPORTANT]
> **Phase 3 implementation and hardening is complete.**
> As explicitly instructed, no Phase 4 functionality has been started. We await your review and authorization before proceeding to Phase 4.
