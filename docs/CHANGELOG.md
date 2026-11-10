# CHANGELOG & ARCHITECTURAL PROGRESS
## Enterprise Admin & Data Intelligence Platform

All notable architectural corrections and design decisions for this project are documented herein.

---

## [Phase 0 - Corrections Revision 2.0.0] - 2026-11-01

### Added
- **Hybrid Spreadsheet Storage Architecture:** Specified row-level JSONB records augmented by `sheet_columns` metadata and selective B-Tree expression indexing for 50,000+ row performance.
- **Formal Formula Engine Specification:** Designed deterministic Lexer, Recursive Descent Parser, and AST evaluator with Directed Acyclic Graph (DAG) dependency mapping, cycle detection (`#CYCLE!`), and explicit zero-`eval()` guarantee.
- **Production Disaster Recovery Model:** Engineered continuous WAL archiving strategy providing empirically verifiable RPO ≤ 15 minutes and automated sandbox restore testing.
- **Redis Partitioning & Transactional Outbox:** Configured logical database isolation (DB 0 for Celery, DB 1 for session caching) and implemented the Transactional Outbox pattern in PostgreSQL for zero dropped background jobs.
- **Nonce-Based Next.js Content Security Policy (CSP):** Designed strict CSP middleware utilizing per-request cryptographically generated nonces.
- **Storage Abstraction Strategy:** Defined unified `FileStorageService` interface mapping to local filesystem in development and private S3 buckets with signed URLs in production.
- **Phased Schema Migration Roadmap:** Partitioned database entities across 9 discrete, testable Alembic migrations instead of a single premature schema dump.
- **Outstanding Technical Decisions Section:** Fully documented alternatives, recommendations, technical reasoning, and risk assessments for open architectural items.

### Changed
- **Retired PyJWT:** Completely removed JWT handling from the primary authentication model; unified under 256-bit opaque server-managed sessions with Argon2id and Redis caching.
- **Refined Docker Compose:** Removed deprecated `version` key, eliminated insecure fallback credentials, bound ports to localhost, and enforced strict memory limits suitable for a 16 GB development machine.

### Verified
- **Docker Environment:** Verified Docker Desktop 4.97, Docker Engine 29.9.2, and Docker Compose v5.6.1 with test container execution.
- **Python 3.14 & Node.js 26:** Verified package resolution for FastAPI 0.141, Pydantic 2.13, Celery 5.6, Polars 2.1, and Pandas 3.0.


---

## [Phase 1 - Infrastructure Foundation & Hardening 1.0.0] - 2026-11-01

### Added
- **Docker Compose Production Foundation:** Provisioned PostgreSQL 16 and Redis 7 with container healthchecks, persistent named volumes, and host port bindings (`127.0.0.1:5432`, `127.0.0.1:6379`).
- **Redis Durability Configuration:** Enforced `maxmemory 256mb`, `maxmemory-policy noeviction`, `appendonly yes`, and `appendfsync everysec`.
- **FastAPI Core Foundation:** Configured async SQLAlchemy 2.x engine, structured JSON logging, exception handlers, and `/health` and `/ready` endpoints.
- **Next.js 16 Client Monorepo:** Configured Next.js 16 App Router with Turbopack, Tailwind CSS 4, TypeScript strict mode, and ESLint.
- **Automated Verification:** Added 5 backend tests (2 unit, 3 integration) verifying live database and cache connectivity.
- **CI Pipeline:** Added `.github/workflows/ci.yml` validating formatting, linting, type-checking, and tests.

---

## [Phase 2 - UI Foundation & Reusable Design System 1.0.0] - 2026-11-01

### Added
- **Design Token System:** Comprehensive semantic tokens in `frontend/src/app/globals.css` with light and dark mode variables, WCAG 2.2 AA contrast compliance, radius scales, shadow levels, and z-index conventions.
- **15 Accessible UI Primitives:** Created in `frontend/src/components/ui/`:
  - `Button` (6 variants, 4 sizes, loading spinner, disabled state)
  - `Input` (accessible focus rings, error validation state)
  - `Textarea` (accessible focus rings, error validation state)
  - `Checkbox` (Radix UI wrapper with checked and indeterminate states)
  - `Badge` (8 semantic variants: default, secondary, success, warning, destructive, info, outline, muted)
  - `Card` (modular Header, Title, Description, Content, Footer)
  - `Separator` (Radix UI horizontal and vertical divider)
  - `Skeleton` (pulse animation loading placeholder)
  - `EmptyState` (standard empty state container with icon and action callback)
  - `ErrorState` (accessible `role="alert"` container with retry callback)
  - `Table` (semantic TableHeader, TableBody, TableHead, TableRow, TableCell, TableCaption)
  - `Tooltip` (Radix UI tooltip provider with directional placement)
  - `Dialog` (Radix UI accessible modal dialog with focus trap and overlay)
  - `DropdownMenu` (Radix UI menu with items, checkboxes, radio items, and separators)
  - `Select` (Radix UI custom select with triggers, viewports, and checkmarks)
- **Application Shell Interactions:**
  - Collapsible desktop sidebar (240px expanded vs 64px collapsed with tooltips)
  - Mobile slide-out drawer (`w-72`) with backdrop overlay, touch dismiss, and Escape key listener
  - Topbar with mobile hamburger toggle, theme toggle, and search trigger
  - Command palette (`cmdk`) bound to `Ctrl+K` with form element input guard
- **Interactive Component Showcase:** Rendered full interactive showcase on `frontend/src/app/page.tsx` with live dialog, dropdown, form controls, badges, table, and empty/error states.
- **Comprehensive Test Suite:** Added 17 unit tests in `frontend/src/__tests__/design-system.test.tsx` and 4 tests in `frontend/src/__tests__/home.test.tsx` (21 passing frontend tests).
- **Design System Documentation:** Authored `docs/architecture/design-system.md` detailing token architecture, WCAG 2.2 AA contrast ratios, component APIs, and shell conventions.

---

## [Phase 3 - Backend Repositories, Migrations & Service Architecture 1.0.0] - 2026-11-01

### Added
- **Foundational ORM Models:**
  - `Organization` (`app/models/organization.py`): Multi-tenant root entity with unique indexed slug and cascade relationships.
  - `User` (`app/models/user.py`): Identity profile with normalized email uniqueness.
  - `OrganizationMembership` (`app/models/membership.py`): Composite unique association between organization and user (`uq_organization_memberships_org_user`).
  - `Workspace` (`app/models/workspace.py`): Scoped tenant container with unique per-organization slug constraint (`uq_workspaces_org_slug`).
- **Declarative Base & Metadata Naming Convention (`app/db/base.py`):** Explicit naming convention for primary keys (`pk_*`), foreign keys (`fk_*`), unique constraints (`uq_*`), indexes (`ix_*`), and check constraints (`ck_*`).
- **Transaction Management (`app/db/transaction.py`):** Atomic unit-of-work context manager supporting nested savepoints (`begin_nested()`) and automatic rollback on unhandled exceptions.
- **Pydantic v2 Schemas (`app/schemas/`):** Strict input validation, regex slug rules, email normalization, and output mappings for Organizations, Users, Workspaces, and Memberships.
- **Repository Layer (`app/repositories/`):** Asynchronous query repositories for Organization, User, Membership, and Workspace with strict tenant filtering and pagination.
- **Service Layer (`app/services/`):**
  - `OrganizationService`: Single-transaction atomic organization provisioning (`provision_organization_atomic`) creating Organization, User, Membership, and default Workspace with verified full rollback on failure.
  - `UserService`: Email normalization and user profile deduplication.
  - `WorkspaceService`: Strict tenant-scoped workspace creation, retrieval, and cross-tenant access violation detection (`CrossTenantAccessError`).
- **Alembic Incremental Migration:** Added `5b9cdc88b118_create_organizations_users_workspaces.py` migrating baseline `f50478da1aca` to head. Tested upgrade and downgrade lifecycle on a disposable database.
- **Backend Test Suite Expansion:** 27 automated tests passing in 1.05s (13 unit tests, 14 integration tests).
- **Backend Architecture Documentation:** Created `docs/architecture/backend-architecture.md` detailing layered design, ER diagram, transaction conventions, and tenant isolation rules.

---

## [Phase 3 - Hardening & Final Verification Addendum 1.1.0] - 2026-11-01

### Hardened
- **Transaction Manager (`app/db/transaction.py`):** Verified explicit savepoint semantics (`session.begin_nested()`) when an outer transaction is active vs root transaction (`session.begin()`), guaranteeing that nested errors roll back only to the savepoint without corrupting or committing the outer transaction, and without swallowing exceptions.
- **Tenant Privacy & Enumeration Protection:** Updated `WorkspaceService.get_workspace` and `main.py` cross-tenant exception handling to return HTTP 404 (`ENTITY_NOT_FOUND`) rather than 403 on cross-tenant requests. This eliminates resource existence probing (enumeration attacks) while logging structured security warnings internally for SIEM auditing.
- **Concurrent Insert Race Condition Hardening:** Hardened `UserService.get_or_create_user` to catch database `IntegrityError` from unique constraint collisions (`ix_users_normalized_email`) within a savepoint, roll back the failed insert without corrupting the active transaction, and seamlessly retrieve the winning concurrently inserted record.
- **Phase 4 Multi-Tenant Directive:** Documented the security requirement that Phase 4 authentication must strictly resolve tenant context (`organization_id`) from validated session memberships, never from untrusted client inputs.

### Added Tests
- **PostgreSQL Nested Transaction Tests (`tests/integration/test_nested_transactions.py`):**
  - `test_nested_transaction_rollback_preserves_outer_transaction` (Test A): Proves that a nested savepoint rollback preserves outer uncommitted mutations and allows outer commits to succeed.
  - `test_outer_transaction_rollback_removes_all_nested_changes` (Test B): Proves that an outer transaction failure rolls back all changes, including previously successful nested operations.
- **4-Table Atomic Provisioning Rollback Test (`tests/integration/test_integration_services.py`):**
  - Directly queries `organizations`, `users`, `organization_memberships`, and `workspaces` tables to verify 0 records leaked upon mid-provisioning failure.
- **Concurrent Collision Integration Test (`tests/integration/test_integration_services.py`):**
  - Simulates concurrent user creation collisions and verifies clean savepoint rollback and recovery.
- **Cross-Tenant Safe 404 Test (`tests/unit/test_health.py`):**
  - Verifies that cross-tenant access exception handler returns HTTP 404 with an opaque error message.

### Verified
- **Backend Tests:** 31 tests passing (14 unit tests, 17 integration tests) against live PostgreSQL 16 in 2.71s.
- **Frontend Tests:** 31 tests passing (Vitest) in 5.64s.
- **Linting & Type-Checking:** Ruff (0 errors, 43 files formatted), Mypy (0 errors in 32 files), ESLint (0 errors), Next.js TypeScript (`npx tsc --noEmit` 0 errors).
- **Production Build:** Next.js 16.4.3 production build successfully generated via Turbopack.

---

## [Phase 3 - Transaction Ownership & Real Concurrency Correction 1.2.0] - 2026-11-01

### Fixed & Hardened
- **Explicit Unit of Work Depth Tracking (`app/db/transaction.py`):** Eliminated the SQLAlchemy 2.0 autobegin trap where read-before-write SELECT queries caused `session.in_transaction()` to be `True`, misleading the helper into opening savepoints without committing the root transaction upon session close. Implemented session-scoped depth tracking (`session.info["_explicit_tx_depth"]`) ensuring that root transactions commit all mutations to PostgreSQL upon block exit.
- **Audited Service Methods:** Inspected all read-before-write methods across `OrganizationService`, `UserService`, and `WorkspaceService`, ensuring reliable persistence across session lifecycles.
- **Relabeled Deterministic Collision Test:** Accurately classified the mocked-lookup uniqueness collision test as `test_user_service_get_or_create_deterministic_collision_recovery`.

### Added Tests
- **Autobegin Persistence Integration Tests (`tests/integration/test_integration_services.py`):**
  - `test_get_or_create_user_persistence_after_autobegin`: Executes the 9-step autobegin $\rightarrow$ insert $\rightarrow$ session close $\rightarrow$ new session verification sequence.
  - `test_create_organization_persistence_after_autobegin`: Proves root commit persistence for organizations following prior SELECT queries.
  - `test_create_workspace_persistence_after_autobegin`: Proves root commit persistence for workspaces following prior SELECT queries.
- **Genuine Concurrent Multi-Session Test (`tests/integration/test_integration_services.py`):**
  - `test_user_service_get_or_create_real_concurrency`: Spawns two independent `AsyncSession` workers synchronized with `asyncio.Event` barriers at the initial lookup stage. Both observe `None`, both concurrently attempt PostgreSQL insertion, and PostgreSQL enforces `ix_users_normalized_email`. Proves exactly one `created=True`, exactly one `created=False`, 1 row in database, and identical returned IDs with zero sleeps.

### Verified
- **Backend Tests:** 35 tests passing (14 unit tests, 21 integration tests) against live PostgreSQL 16 in 2.16s.
- **Frontend Tests:** 31 tests passing (Vitest) in 2.87s.
- **Linting & Type-Checking:** Ruff (0 errors), Mypy (0 errors in 32 files), ESLint (0 errors), Next.js TypeScript (0 errors).
- **Production Build:** Next.js 16.4.3 production build successfully generated via Turbopack.

---

## [Phase 3 - Final Transaction Safety Patch 1.3.0] - 2026-11-01

### Hardened
- **Cancellation-Safe Rollback (`app/db/transaction.py`):** Extended error handling to catch `BaseException` (`asyncio.CancelledError`), ensuring task cancellation cleanly rolls back the transaction before propagating the cancellation, without swallowing it and with reliable depth-counter reset in `finally:`.
- **Caller-Owned Transaction Protection (`app/db/transaction.py`):** Integrated inspection of `session.sync_session._transaction.origin`. When an external caller starts a transaction explicitly via `session.begin()` (`SessionTransactionOrigin.BEGIN`), the helper automatically creates a nested savepoint (`session.begin_nested()`), guaranteeing that service helpers never prematurely commit or close an outer caller-owned Unit of Work.

### Added Tests
- **`test_transaction_cancellation_safe_rollback` (`tests/integration/test_nested_transactions.py`):**
  - Triggers autobegin with prior SELECT, enters helper, performs a write, blocks at an `asyncio.Event` synchronization barrier, cancels the task, confirms `CancelledError` propagates, verifies transaction is inactive on session, and confirms in a fresh session that uncommitted data was rolled back in PostgreSQL.
- **`test_caller_owned_transaction_protection` (`tests/integration/test_nested_transactions.py`):**
  - Begins an explicit outer transaction using `session.begin()`, executes service mutation `OrganizationService.create_organization`, confirms mutation is visible inside the transaction, confirms outer transaction remains active upon service return, rolls back the outer transaction, and verifies in a fresh session that the mutation was not committed to PostgreSQL.

### Verified
- **Backend Tests:** 37 tests passing (14 unit tests, 23 integration tests) against live PostgreSQL 16 in 2.28s.
- **Frontend Tests:** 31 tests passing (Vitest) in 3.29s.
- **Linting & Type-Checking:** Ruff (0 errors), Mypy (0 errors in 32 files), ESLint (0 errors), Next.js TypeScript (0 errors).
- **Production Build:** Next.js 16.4.3 production build successfully generated via Turbopack.

---

## [Phase 4 - Authentication, Session Management & Account Security 1.0.0] - 2026-11-01

### Added
- **Core Cryptographic Primitives (`app/core/security.py`):**
  - Argon2id password hashing with configurable operational costs ($64\text{ MiB}$ memory, 3 iterations, 1 parallelism).
  - Precomputed dummy-hash constant-time verification for non-existent accounts, eliminating timing-based email enumeration.
  - Enterprise password policy validation: minimum 12 characters, maximum 1024 bytes, Unicode and spaces preserved.
  - 256-bit cryptographically secure opaque URL-safe token generator and SHA-256 persistence hashing.
  - AES-256-GCM authenticated encryption and decryption for TOTP secrets with key version prefixing (`v1:{nonce}:{ciphertext}`).
  - RFC 6238 TOTP verification via `pyotp` with exact ±1 time-step drift tolerance and time-step replay protection (`last_accepted_step`).
  - Backup recovery codes (10 single-use codes) with normalization and SHA-256 digest persistence.
  - HMAC-SHA256 privacy-preserving key computation for Redis keys (`rl:{action}:{keyed_digest}`).
- **Privacy-Preserving Sliding-Window Rate Limiting (`app/core/rate_limit.py`):**
  - Atomic Redis Lua script enforcing sliding fixed-window rate limits.
  - Keyed identifier hashing ensuring sensitive IP addresses and emails never touch Redis in cleartext.
  - Fail-closed security posture for critical authentication endpoints if Redis is unavailable.
  - Standard HTTP 429 response handling with `Retry-After` header.
- **Transactional Email Abstraction (`app/services/email.py`):**
  - `EmailSender` abstract interface with `MemoryEmailSender` for automated test suites and `SmtpEmailSender` for production.
  - Outgoing email formatting for account verification and password reset links.
- **Data Models & Database Schema (`app/models/`):**
  - `User.email_verified_at`: Timestamp column on foundational `users` identity table.
  - `UserCredentials` (`user_credentials` table): Argon2id password hash, password changed timestamp.
  - `AuthenticationSession` (`authentication_sessions` table): Authoritative server-side session in PostgreSQL with SHA-256 token digest, session-bound CSRF digest, active tenant reference, idle and absolute expiration timestamps, user agent, and IP address.
  - `AuthenticationActionToken` (`authentication_action_tokens` table): Single-use tokens for email verification and password reset.
  - `MfaCredential` (`mfa_credentials` table): AES-256-GCM encrypted secret, key version, enabled timestamp, last accepted step.
  - `MfaRecoveryCode` (`mfa_recovery_codes` table): Single-use recovery code SHA-256 digests with atomic consumption.
  - `MfaLoginChallenge` (`mfa_login_challenges` table): 5-minute login challenges for two-stage authentication.
- **Alembic Incremental Migration:**
  - `cdc9da2cb773_create_auth_and_session_tables.py` creating all 6 auth tables and adding `email_verified_at` column to `users`.
  - Applied to live PostgreSQL database; bidirectional upgrade $\rightarrow$ downgrade $\rightarrow$ re-upgrade verified on disposable database.
- **Repository Layer (`app/repositories/`):**
  - `CredentialsRepository`, `SessionRepository`, `ActionTokenRepository`, `MfaRepository`.
  - Atomic consumption via SQL `UPDATE ... WHERE ... AND consumed_at IS NULL RETURNING ...` for race-free single-use tokens and recovery codes.
- **Service Layer (`app/services/`):**
  - `CredentialsService`: Password verification, constant-time authentication, password change, and rehash checks.
  - `SessionService`: Authoritative session creation, validation, throttled touch updates, and single/all revocation.
  - `ActionTokenService`: Email verification and password reset token issuance and atomic consumption.
  - `MfaService`: Enrollment initiation, confirmation, login challenge lifecycle, code verification, recovery code consumption, and code regeneration.
  - `OrganizationService`: Atomic self-service registration method `register_user_and_organization_atomic` provisioning User, Credentials, Organization, Membership, Workspace, and verification token in a single atomic database transaction.
- **FastAPI Authentication Dependencies (`app/api/dependencies/auth.py`):**
  - `get_current_session`: Validates session cookie or bearer token authoritatively against PostgreSQL, checks idle and absolute lifetimes, and performs throttled touch.
  - `verify_csrf_token`: Enforces constant-time validation of `X-CSRF-Token` header on state-changing methods (`POST`, `PUT`, `PATCH`, `DELETE`) authenticated via cookie.
  - `get_current_user`: Returns authenticated user profile.
  - `get_current_tenant`: Authoritatively queries PostgreSQL `organization_memberships` to verify live tenant membership; immediately rejects with 403 Forbidden if membership was revoked.
- **API Router (`app/api/v1/routers/auth.py`):**
  - Complete 19-endpoint authentication suite registered under `/api/v1/auth`:
    - `POST /register`, `POST /login`, `POST /login/mfa`, `POST /logout`, `POST /logout-all`, `GET /me`, `GET /csrf-token`
    - `POST /verify-email`, `POST /resend-verification`, `POST /forgot-password`, `POST /reset-password`, `POST /change-password`
    - `POST /mfa/totp/setup`, `POST /mfa/totp/confirm`, `POST /mfa/totp/disable`, `POST /mfa/recovery-codes/regenerate`
    - `GET /sessions`, `DELETE /sessions/{session_id}`, `POST /select-tenant`
- **Documentation:**
  - Authored comprehensive architecture and threat model guide in `docs/security/authentication-security.md`.
  - Updated `docs/architecture/backend-architecture.md` and `.env.example`.

### Verified
- **Backend Test Suite:** 62 tests passing (16 unit tests, 46 integration tests) with 86% total statement coverage across `app` in 12.28s.
- **Frontend Test Suite:** 31 tests passing (Vitest) in 2.75s.
- **Frontend Type-Checking & Linting:** 0 errors on `npx tsc --noEmit` and `npm run lint`.
- **Frontend Production Build:** Successful Next.js 16.4.3 production build via Turbopack.
- **Backend Quality Gates:** 0 errors on `ruff check backend`, 0 errors on `ruff format --check backend`, 0 errors on `mypy app tests --explicit-package-bases` across 67 source files.
- **Concurrency & Replay Protections:** Verified atomic single-winner behavior on 10 concurrent requests consuming the same action token and 10 concurrent requests consuming the same backup recovery code.

---

## [Phase 4 - Authentication Security Hardening 1.1.0] - 2026-11-01

### Hardened & Corrected
- **CSRF & Origin Verification on Logout Endpoints (`app/api/v1/routers/auth.py`):**
  - Attached `verify_csrf_token` dependency to `POST /api/v1/auth/logout` and `POST /api/v1/auth/logout-all`.
  - Added request `Origin` header validation against `settings.ALLOWED_ORIGINS`, rejecting untrusted origins with `403 Forbidden` (`CsrfError`).
  - Added tests for missing CSRF, invalid CSRF, untrusted Origin, and valid CSRF on logout and logout-all.
- **Atomic TOTP Compare-and-Swap Replay Prevention (`app/repositories/mfa.py`, `app/services/mfa.py`):**
  - Implemented conditional SQL atomic CAS update: `WHERE user_id = :user_id AND (last_accepted_step IS NULL OR last_accepted_step < :new_step) RETURNING id`.
  - Added real concurrent test with two independent `AsyncSession` workers synchronized with `asyncio.Barrier` submitting identical valid TOTP step codes; PostgreSQL row locks ensure exactly 1 succeeds and 1 fails.
- **Mandatory Reauthentication on Sensitive Operations (`app/api/v1/routers/auth.py`, `app/schemas/auth.py`):**
  - `POST /mfa/totp/setup`: Enforced password verification and blocked duplicate enrollment.
  - `POST /change-password`: When MFA is active, mandatory verification of second factor (`code` or `recovery_code`).
  - `POST /mfa/totp/disable`: Mandatory verification of password and existing factor (`code` or `recovery_code`).
  - `POST /mfa/recovery-codes/regenerate`: Mandatory verification of password and second factor (`code` or `recovery_code`).
- **Initial Organization Selection Policy (`app/api/v1/routers/auth.py`):**
  - Exactly 1 membership: auto-selects into `active_organization_id`.
  - Multiple memberships: sets `active_organization_id = None`, requiring explicit selection via `POST /select-tenant` (no arbitrary DB inference).
  - 0 memberships: sets `active_organization_id = None`.
  - Live PostgreSQL membership validation via `get_current_tenant`; revoked memberships immediately produce 403 Forbidden.
- **Redis Outage Fail-Closed Response (`app/core/rate_limit.py`, `app/main.py`):**
  - Unreachable Redis raises `ServiceUnavailableError` resulting in HTTP 503 with `Retry-After: 10`.
  - HTTP 429 is strictly reserved for requests exceeding configured rate limits.
- **Argon2id Hash Rehashing Concurrency Safety (`app/repositories/credentials.py`, `app/services/credentials.py`):**
  - On successful login with outdated hash, upgrades hash using CAS `WHERE user_id = :user_id AND password_hash = :expected_old_hash`.
  - Corrected documentation and comments to accurately state that dummy Argon2id hash verification *mitigates* (rather than eliminates) timing differences.
- **Factual Operational Scope Clarification:**
  - `MemoryEmailSender` is active for dev/test; real SMTP delivery requires external provider credentials.
  - Test suites and quality gates executed locally; GitHub Actions remote runner execution executes upon repository push.

### Verified
- **Backend Tests:** 66 tests passing (16 unit tests, 50 integration tests) with 88% statement coverage in 22.00s.
- **Backend Quality Gates:** 0 errors on `ruff check backend`, 0 errors on `ruff format --check backend`, 0 errors on `mypy app tests` across 67 source files.
- **Frontend Quality Gates:** 0 errors on `npm run lint`, 0 errors on `npx tsc --noEmit`.
- **Frontend Production Build:** Successful Next.js 16.4.3 production build via Turbopack.

---

## [Phase 4 - Final CSRF and Security Configuration Correction 1.2.0] - 2026-11-01

### Hardened & Corrected
- **CSRF Token Endpoint Conversion (`POST /api/v1/auth/csrf-token`):**
  - Converted CSRF token rotation from `GET` to `POST`. `GET /csrf-token` returns `405 Method Not Allowed`, preserving HTTP read-only idempotency semantics.
  - Returns `Cache-Control: no-store, no-cache, must-revalidate, private` and `Pragma: no-cache` to strictly eliminate browser token caching.
  - Validates `Origin`, `Referer`, and `Sec-Fetch-Site != "cross-site"` via `verify_csrf_bootstrap_origin` without requiring a previous CSRF token.
  - Multi-tab synchronization: concurrent requests within a 10s window deterministically receive identical tokens via Redis synchronization lock; previous tokens remain accepted during a 60-second grace window (`csrf_grace:{session.id}:{token_hash}`) to prevent sibling tab validation failures.
- **Login & Registration CSRF Defense (`POST /register`, `POST /login`, `POST /login/mfa`):**
  - Attached `verify_auth_request_origin` to unauthenticated cookie-issuing endpoints.
  - Rejects untrusted origins, untrusted referers, and `Sec-Fetch-Site: cross-site` with `403 Forbidden` (`CSRF_VERIFICATION_FAILED`).
  - Standard compliant defense defending against Login CSRF and forced account creation without requiring authenticated-session tokens.
- **Cryptographic Key Separation:**
  - Dedicated `RATE_LIMIT_HMAC_KEY` introduced for Redis rate-limiter identity hashing, completely decoupling it from `SESSION_SECRET_KEY` and `ENCRYPTION_MASTER_KEY`.
  - Enforced startup validation requiring minimum 32-character length, pairwise distinct values across all keys, and production rejection of default keys.
  - Updated `.env.example` to use placeholders only.
  - Documented safe key rotation implications for all three cryptographic keys.
- **CORS Configuration Hardening (`app/main.py`):**
  - Configured explicit origins from `settings.ALLOWED_ORIGINS` with credential awareness (`allow_credentials=True`).
  - Explicitly specified allowed headers (`Content-Type`, `Authorization`, `X-CSRF-Token`, `X-Requested-With`, `Accept`, `Origin`) and exposed headers (`Content-Type`, `Retry-After`).
  - Prohibited wildcard origins with credentials at startup validation.
  - Added integration tests covering preflight `OPTIONS` for allowed and rejected origins and actual request origin echoing.

### Verified
- **Backend Tests:** 75 tests passing (18 unit tests, 57 integration tests) with 88% overall statement coverage in 25.87s.
- **Backend Quality Gates:** 0 errors on `ruff check backend`, 0 errors on `ruff format --check backend`, 0 errors on `mypy app tests` across 68 source files.
- **Frontend Quality Gates:** 0 errors on `npm run lint`, 0 errors on `npx tsc --noEmit`.
- **Frontend Production Build:** Successful Next.js 16.4.3 production build via Turbopack.




### Hardened
- **Removed Private API Access (`app/db/transaction.py`):** Eliminated direct access to the private `_transaction` attribute. Replaced with documented public SQLAlchemy session inspection methods: `session.in_nested_transaction()`, `session.in_transaction()`, and `session.sync_session.get_transaction()`.
- **Caller-Owned Nested Transaction Protection (`app/db/transaction.py`):** When an external caller starts a nested transaction (`session.begin_nested()`), `session.in_nested_transaction()` returns `True`. The helper identifies this external savepoint boundary and creates another nested savepoint rather than misclassifying the session as an autobegun root transaction, ensuring caller savepoints are never committed by the service helper.

### Added Tests
- **`test_caller_owned_nested_transaction_protection` (`tests/integration/test_nested_transactions.py`):**
  - Executes the full 12-step test: opens fresh session $\rightarrow$ calls `session.begin_nested()` $\rightarrow$ confirms `in_transaction()` and `in_nested_transaction()` $\rightarrow$ executes `OrganizationService.create_organization()` $\rightarrow$ verifies record visible inside transaction $\rightarrow$ verifies nested transaction remains active $\rightarrow$ verifies root transaction not committed $\rightarrow$ rolls back caller nested transaction $\rightarrow$ confirms in a fresh session that the record was not persisted in PostgreSQL.

### Verified
- **Backend Tests:** 38 tests passing (14 unit tests, 24 integration tests) against live PostgreSQL 16 in 2.21s.
- **Frontend Tests:** 31 tests passing (Vitest) in 3.14s.
- **Linting & Type-Checking:** Ruff (0 errors, 43 files formatted), Mypy (0 errors in 32 files), ESLint (0 errors), Next.js TypeScript (0 errors).
- **Production Build:** Next.js 16.4.3 production build successfully generated via Turbopack.

---

## [Phase 5 - Role-Based Access Control, Authorization & Organization Administration 1.0.0] - 2026-11-01

### Added
- **RBAC Relational Data Model (`backend/app/models/`):**
  - `Permission`: Fixed system permission catalogue with unique `code`, `name`, `description`, `category`.
  - `OrganizationRole`: Tenant-scoped role definitions (`name`, `slug`, `description`, `is_system`, unique constraint `[organization_id, slug]`).
  - `RolePermission`: Many-to-many junction between roles and permissions.
  - `MembershipRole`: Many-to-many junction between organization memberships and roles with `assigned_at` and `assigned_by_user_id`.
  - `OrganizationInvitation`: Secure invitations with cryptographic SHA-256 token hashing, expiration, and atomic single-use acceptance.
  - `AdministrativeAuditEvent`: Append-only audit events capturing `actor_user_id`, `action`, `target_type`, `target_id`, `occurred_at`, and sanitized metadata.
- **Alembic Database Migration (`03fa52a1f499`):**
  - Generated and verified migration with reversible downgrade.
  - Seeded 16 system permissions across 6 categories: `organization`, `members`, `roles`, `workspaces`, `audit`, `ownership`.
  - Automated bootstrapping of default system roles (`owner`, `admin`, `member`, `viewer`) for all tenant organizations.
  - Automatic assignment of the `owner` role to initial memberships during tenant provisioning and registration.
- **Backend Authorization Architecture (`backend/app/services/` & `backend/app/api/dependencies/auth.py`):**
  - `PermissionService`: Single-query indexed join across 5 tables resolving effective permissions without N+1 overhead or stale caching.
  - `RoleService`: System role protection, custom role CRUD, delegation checks, and last-owner protection.
  - `InvitationService`: Invitation issuance, cryptographic validation, and atomic `consume_invitation_atomically` state transitions.
  - `OwnershipService`: Atomic ownership transfer with PostgreSQL row locking (`FOR UPDATE`).
  - `AuditService`: Append-only event logging with recursive sensitive key masking (`password`, `token`, `secret`, `hash`).
  - Authorization Dependencies: `get_current_membership`, `require_permission`, `require_any_permission`, `require_all_permissions`.
- **Organization Administration REST APIs (`/api/v1/organization/`):**
  - `GET /me`: Current user membership and effective permissions query.
  - `GET /permissions`: System permissions catalogue.
  - `GET /roles`, `GET /roles/{id}`, `POST /roles`, `PATCH /roles/{id}`, `DELETE /roles/{id}`: Organization role lifecycle.
  - `GET /members`, `GET /members/{id}`, `PUT /members/{id}/roles`, `DELETE /members/{id}`: Member administration.
  - `GET /invitations`, `POST /invitations`, `DELETE /invitations/{id}`, `POST /invitations/accept`: Onboarding invitations.
  - `POST /ownership/transfer`: Atomic ownership transfer requiring confirmation phrase `TRANSFER`.
  - `GET /audit-logs`: Append-only administrative audit log viewer.
- **Frontend Organization Administration Views (`frontend/src/app/`):**
  - `/users`: Members list, role assignment dialog, member removal confirmation, pending invitations, invite modal.
  - `/roles`: Roles list, system role badges, detailed permission breakdown, custom role creator/editor modal.
  - `/audit`: Administrative audit ledger with search and metadata JSON inspector.
  - `/settings`: Organization profile management and Danger Zone ownership transfer modal.
  - 403 Forbidden Access Denied simulation states across all views.
- **Automated Verification:**
  - 7 integration test suites in `backend/tests/integration/test_rbac_admin.py` validating role lifecycle, delegation checks, last-owner concurrency protection, invitation security, ownership transfer, and audit isolation.
  - 5 component tests in `frontend/src/__tests__/rbac-pages.test.tsx` validating UI rendering and user interactions.

---

## [Phase 6 - Spreadsheet Engine, Formula Calculation & Virtualized Grid 1.0.0] - 2026-11-01

### Added
- **Storage Architecture Decision Record (ADR 001):**
  - Evaluated row-oriented JSONB, normalized cell-oriented, and hybrid storage with real PostgreSQL benchmarks on 50,000 rows × 20 columns (~255,000 cells).
  - Selected Hybrid Storage: 29.48 MB compact storage (+6.1% over raw JSONB), 1.12ms 100-row viewport queries, and 1.68ms formula precedent lookups (43.9x faster than JSONB scan).
- **Relational Spreadsheet Domain Model (`backend/app/models/spreadsheet.py`):**
  - `Workbook`: Workspace- and organization-scoped workbook container with `(id, organization_id)` composite foreign key.
  - `Sheet`: Sheet tabs within workbooks with ordering and row/col count metrics.
  - `SheetColumn`: Column configurations, display widths, and semantic types.
  - `SheetRow`: Dense JSONB storage of row cells (`v`, `c`, `f`, `t`, `err`) with integer optimistic concurrency `version`.
  - `SheetCellDependency`: Relational directed dependency edges indexed for upstream and downstream graph lookups.
  - `SheetCellHistory`: Bounded cell revision ledger supporting reversible `/undo` and `/redo`.
- **Alembic Database Migration (`f19c3b8a4d2e`):**
  - Created spreadsheet subsystem tables with composite foreign keys enforcing strict database-level tenant isolation.
  - Added spreadsheet application permissions: `spreadsheets.read`, `spreadsheets.create`, `spreadsheets.update`, `spreadsheets.delete`.
  - Backfilled system roles: Owner (full), Admin (full), Member (read/create/update), Viewer (read-only).
- **Pure Python Deterministic Formula Engine (`backend/app/formula/`):**
  - Tokenizer and Recursive Descent AST Parser with operator precedence, parentheses, and right-associative exponentiation (`^`).
  - Evaluator supporting arithmetic (`+`, `-`, `*`, `/`, `^`), comparison (`=`, `<>`, `<`, `<=`, `>`, `>=`), functions (`SUM`, `AVERAGE`, `COUNT`, `MIN`, `MAX`, `IF`), cell references, and range references.
  - Safety limits: 1,000 char length limit, 250 AST nodes, 30 recursion depth, 10,000 cells per range.
  - Typed formula errors: `#DIV/0!`, `#REF!`, `#VALUE!`, `#NAME?`, `#CYCLE!`, `#NUM!`.
  - Dependency Graph: Precedent extraction, DFS cycle detection, and Kahn's / DFS topological ordering for cascading downstream recalculation.
- **Optimistic Concurrency Control:**
  - Multi-user editing protected with integer row versions.
  - Stale mutations safely aborted and returned with HTTP 409 Conflict.
- **Undo / Redo Mechanism:**
  - Audit revision history allowing atomic single-step undo and redo with automatic dependent formula recalculation.
- **Versioned REST API (`/api/v1/spreadsheets/*`):**
  - Full CRUD for workbooks and sheets.
  - Viewport range windowing (`GET /sheets/{id}/range`), paginated rows (`GET /sheets/{id}/rows`), batch cell mutations (`POST /sheets/{id}/cells/batch`), undo (`POST /sheets/{id}/undo`), redo (`POST /sheets/{id}/redo`), and revision history (`GET /sheets/{id}/history`).
  - Strict protection with RBAC permissions, CSRF tokens, and organization tenancy scoping.
- **Virtualized Frontend Grid (`frontend/src/`):**
  - 50,000-row 60fps virtualized rendering via `@tanstack/react-virtual`.
  - Fixed column headers (`A`–`Z`) and fixed row headers (`1`–`50,000`).
  - Bi-directionally synchronized `FormulaBar` with active cell coordinates and computed previews.
  - Full keyboard navigation: arrow keys, Enter / Shift+Enter, Tab / Shift+Tab, Escape, F2, and direct typing.
  - Clipboard copy & paste (TSV table parsing) and undo/redo shortcuts (`Ctrl+Z`, `Ctrl+Y`).
  - Light and Dark mode styling conforming to Phase 2 semantic tokens.

---

## [Phase 6 - Final Concurrency, Formula Consistency & Performance Hardening 1.1.0] - 2026-11-01

### Added
- **Per-Cell Revision Versioning & Granular Optimistic Concurrency:**
  - Transitioned from coarse row-level integer versioning to granular per-cell revisions stored within JSONB (`cells.<col_idx>.ver`).
  - Added `expected_cell_version` to `CellEditInput` and `version` to `CellData`.
  - Serialized mutating transactions on PostgreSQL via parent `Sheet` row lock (`with_for_update()`), enabling concurrent edits on distinct cells within the exact same row without conflict or silent overwrites.
  - Implemented deterministic HTTP 409 Conflict rejection for stale cell revisions.
  - Added atomic rollback of overlapping batch edits: stale cell edits abort the entire transaction, ensuring zero partial mutations.
- **Formula Short-Circuit Lazy Evaluation Semantics:**
  - Formally implemented and verified lazy short-circuit evaluation for `=IF(condition, true_branch, false_branch)`.
  - Evaluates only the chosen branch; prevents evaluation of unused branches (e.g. `=IF(1=1, 42, 1/0)` evaluates to `42.0` and avoids `#DIV/0!`).
- **Circular Reference Persistence & Error Propagation:**
  - Circular formula references (direct and transitive) persist `#CYCLE!` error codes into `sheet_rows.cells` instead of aborting the transaction.
  - Downstream dependent formulas propagate `#CYCLE!` and other error codes (`#DIV/0!`, `#VALUE!`, `#REF!`, `#NAME?`, `#NUM!`) deterministically.
  - Removed obsolete dependency graph edges upon formula modification or replacement with static literals.
- **Bounded Multi-Cell Undo/Redo & Batch History:**
  - Added `batch_id`, `cell_version_before`, and `cell_version_after` to `SheetCellHistory` with Alembic migration `a71c8e2d4b5f`.
  - Grouped all cell mutations from a single request under a shared `batch_id`.
  - Enforced revision conflict policy: undo fails with `ConflictError` (HTTP 409) if any cell in the target batch was subsequently modified by a newer active edit, preventing silent overwrite.
  - Enforced redo invalidation: any new edit automatically purges pending redo records.
  - Enforced history retention bound: maximum 100 batches retained per sheet; older batches are automatically pruned.
  - Enforced database-level tenant isolation across all history operations.
- **Resource Safety Caps:**
  - `MAX_VIEWPORT_ROWS = 200`
  - `MAX_VIEWPORT_COLS = 50`
  - `MAX_BATCH_CELLS = 500` (enforced at both Pydantic schema and service layer)
  - `MAX_RECALCULATION_NODES = 1000`
  - `MAX_HISTORY_BATCHES = 100`
- **Reproducible Storage Benchmark Suite:**
  - Created standalone benchmark script `backend/scripts/benchmark_storage_reproducible.py`.
  - Clarified 1.12ms metric as a 100-row viewport range query against a 50,000-row dataset in ADR 001.
  - Verified 35-repetition distribution on PostgreSQL 16: cold cache 2.07 ms, warm min 1.02 ms, median 1.19 ms, p95 2.01 ms, max 3.89 ms.
  - Verified `EXPLAIN (ANALYZE, BUFFERS)`: index scan on `ix_sheet_rows_sheet_id_row_idx`, 13 shared hit buffers, 0 disk reads, 0.052 ms engine execution time.
  - Verified storage breakdown: 33 MB table heap, 9.45 MB index, 0 bytes TOAST (compact JSONB fits on-page).
- **Live Chrome DevTools Performance Trace:**
  - Next.js production build (`npm run build`) and production server (`npm run start`).
  - Recorded sustained scroll trace over 50,000 rows x 26 cols: 60 FPS, 0 dropped frames, CLS 0.00.
  - Verified DOM virtualization: exactly 56 rows and 1,430 cells mounted at any time.
  - Verified memory stability: 18.25 MB before scroll, 8.59 MB during scroll, 9.52 MB after scroll.
  - Verified operational latencies: cell editing 2.60 ms, batch paste 0.30 ms, formula recalculation 0.10 ms.

### Verified
- **Backend Test Suite:** 140 passed in 56.48s (0 failures):
  - 16 formula engine unit tests
  - 8 spreadsheet API integration tests
  - 9 concurrency hardening integration tests (`test_spreadsheet_concurrency_hardening.py`)
  - 107 existing auth, RBAC, tenant, and transaction tests
- **Frontend Test Suite:** 43 passed (Vitest) in 5.95s across 4 test suites.
- **Code Quality:**
  - 0 ruff errors on `ruff check app tests`
  - 105 files formatted cleanly on `ruff format --check app tests`
  - 0 mypy errors on `mypy app` across 85 source files
  - 0 eslint errors on `npm run lint`
- **Database Migrations:**
  - Downgrade from `a71c8e2d4b5f` to `f19c3b8a4d2e` and re-upgrade to `a71c8e2d4b5f` verified on `vibe_migration_test`.

---

## [Phase 7 - Analytics Engine, Python Data Processing & Background Job Infrastructure 1.0.0] - 2026-11-01

### Added
- **Python Data Processing Foundation (Polars 2.1, Pandas 3.1, NumPy 2.5):**
  - Integrated Polars, Pandas, and NumPy for vector analytics running on Python 3.14.7.
  - Pre-registered allowlist `OperationRegistry` prohibiting arbitrary client code execution and direct SQL.
  - 6 initial allowlisted analytical routines:
    - `profile_dataset`: Structural summary, column dtypes, null percentages, cardinality, min/max/mean.
    - `describe_columns`: Five-number summary, mean, std, variance across numeric columns.
    - `group_by_aggregate`: Group by categorical dimensions, evaluate sum, mean, count, min, max.
    - `filter_dataset`: Compound comparisons (`==`, `!=`, `>`, `<`, `>=`, `<=`, `is_null`, `not_null`).
    - `correlation_matrix`: Pairwise Pearson and Spearman correlation matrices with heatmap data.
    - `data_quality_profile`: Missing value ratios, duplicate row detection, anomaly alerts.
- **Durable Background Job Infrastructure (Celery 5.6 & Redis 7):**
  - Celery application configured with Redis broker (db=1) and Redis backend (db=2).
  - Explicit Celery task `analytics.execute_job` with cooperative cancellation checkpoints.
  - Worker lease management (`lease_expires_at`) with automatic lease renewal during stage progress.
- **PostgreSQL Transactional Outbox Pattern:**
  - Created `analytics_outbox_events` table capturing job scheduling events within the client request transaction.
  - Standalone `OutboxDispatcher` daemon reading pending events with `SELECT ... FOR UPDATE SKIP LOCKED`.
  - Guaranteed at-least-once delivery to Celery with automated lease recovery sweeps for crashed workers.
- **Idempotency & Concurrency Governance:**
  - Unique composite index `(organization_id, idempotency_key)` preventing duplicate job submissions.
  - Atomic database-level concurrency limits: maximum 5 active jobs per organization, maximum 2 per user.
- **Immutable Input Snapshots & Result Persistence:**
  - Version-bound Apache Parquet snapshots (`uploads/analytics/snapshots/`) with SHA-256 integrity validation.
  - Large analytical results offloaded to private Parquet artifacts (`uploads/analytics/results/`).
  - PostgreSQL `analytics_results` persisting tabular metadata and JSON payloads.
- **Spreadsheet Integration (Publishing to Sheets):**
  - Endpoint `POST /api/v1/analytics/results/{id}/publish-to-sheet` writing tabular results back into workbook sheets via batch cell mutation.
- **Tenant Isolation & RBAC:**
  - Composite foreign keys on `analytics_jobs`, `analytics_outbox_events`, and `analytics_results` strictly bound to `organization_id`.
  - System permissions: `analytics.read`, `analytics.create`, `analytics.cancel`, `analytics.export`.
- **Next.js Analytics Interface (`/analytics`):**
  - Overview dashboard with active job polling, status badges, and search/filter controls.
  - "New Analysis" modal supporting dynamic parameter configuration and validation.
  - "Job Detail & Results" modal with progress stage bar, error diagnostics, and interactive result visualizers (data tables, correlation matrix heatmaps, quality profiles).
  - One-click "Publish to Sheet" and Parquet artifact download.

### Verified
- **Backend Test Suite:** 160 passed in 73.48s (0 failures):
  - 9 unit tests (`test_analytics_engine.py`)
  - 8 integration tests (`test_analytics_api.py`)
  - 3 concurrency & race condition tests (`test_analytics_concurrency.py`)
  - 140 preexisting regression tests across auth, RBAC, spreadsheets, and transactions
- **Frontend Test Suite:** 46 passed (Vitest) in 3.38s across 5 test suites.
- **Frontend Production Build:** `next build` compiled cleanly with 0 errors and static prerendering for `/analytics`.
- **Code Quality:**
  - 0 ruff errors on `ruff check app tests`
  - 126 files formatted cleanly on `ruff format --check app tests`
  - 0 mypy errors on `mypy app` across 103 source files
  - 0 eslint errors on `npm run lint`
- **Database Migrations:**
  - Alembic migration `b82d9f3e5c7a` applied and verified.








