# Stage 12.2.2 — Baseline Preservation & Database Reconciliation Report

**Project:** VIBE — Enterprise BI, Analytics & Data Intelligence Platform  
**Phase:** 12 — Production Readiness, End-to-End Testing & Deployment Verification  
**Stage:** 12.2.2 — Independent Read-Only Implementation, Security & Data-Integrity Audit  
**Audit Date:** November 4, 2026  
**Auditor Mode:** STRICT READ-ONLY (Zero Code Changes, Zero Migrations, Zero Mutations)  
**Governing Documents:** `docs/IMPLEMENTATION_PLAN.md` v2.0.0, Stage 12.2.1 Reports  

---

## 1. Executive Summary & Audit Mode Statement

This report constitutes the independent baseline preservation and database reconciliation audit for Phase 12 Stage 12.2.1 deliverables (Workspaces, Reports, File Storage, and System Health).

The audit was executed under **STRICT READ-ONLY** controls:
- **Zero code modifications** were made to application frontend, backend, or configurations.
- **Zero migrations** were created, modified, upgraded, or downgraded.
- **Zero database mutations** (no `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `DROP`, or `ALTER`) were executed.
- All SQL inspections were conducted over read-only transactions (`SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY;`).
- The Python execution sandbox invariant (`PYTHON_SANDBOX_ENABLED=false`) and AWS deferral were strictly verified and preserved.

---

## 2. Environment & Configuration Verification

### 2.1 Git Working Tree & Repository State
- **Workspace:** `D:\vibe`
- **Git Branch:** `main`
- **Head Commit:** Clean repository status (initial commit staged, working tree unmodified during audit).
- **Environment Invariant:** `PYTHON_SANDBOX_ENABLED = False` (confirmed via `app.core.config.settings.PYTHON_SANDBOX_ENABLED`).
- **AWS Integration:** Explicitly DEFERRED (no AWS credentials, SDK, or S3 dependencies configured).
- **Environment:** `development` (backend default; production simulation active).

### 2.2 Connection Targets & Endpoints
- **PostgreSQL Host:** `127.0.0.1:5432`
  - Development Connection: `vibe_admin@127.0.0.1:5432/vibe_enterprise`
  - Dedicated Test Connection: `vibe_admin@127.0.0.1:5432/vibe_enterprise_test`
- **Redis Target:** `127.0.0.1:6379/0`
  - Key namespaces: `celery`, `analytics`, `reporting`, `session:*`, `rate:*`
- **Backend API Prefix:** `/api/v1` (114 total registered OpenAPI operations across all domains).
- **Frontend Routes:** 19 compiled Next.js routes (`/`, `/workspaces`, `/reports`, `/files`, `/system`, `/spreadsheets`, `/dashboards`, `/analytics`, `/users`, `/roles`, `/audit`, `/settings`, `/integrations`, `/login`, etc.).

---

## 3. Independent Database State Inspection

Both databases were queried using strictly read-only connections via Psycopg 3 with transaction-level read-only enforcement.

### 3.1 Comparative Database Reconciliation Table

| Metric / Object | `vibe_enterprise` (Dev/Prod Core) | `vibe_enterprise_test` (Dedicated Test DB) | Reconciliation Status |
|:---|:---|:---|:---|
| **Alembic Revision Head** | `370908748232` | `481909849343` | **VERIFIED ISOLATED** |
| **Total Public Base Tables** | 42 | 42 | **RECONCILED** |
| **Users Count (`users`)** | 10,242 | 29 | **DISCREPANCY INVESTIGATED** |
| **Organizations Count (`organizations`)** | 9,404 | 26 | **DISCREPANCY INVESTIGATED** |
| **Workspaces Count (`workspaces`)** | 8,497 | 27 | Verified Isolated |
| **Workbooks Count (`workbooks`)** | 4,524 | 1 | Verified Isolated |
| **Dashboards Count (`dashboards`)** | 1,196 | 1 | Verified Isolated |
| **Report Definitions (`report_definitions`)** | 667 | 1 | Verified Isolated |
| **Report Runs (`report_runs`)** | 858 | 0 | Verified Isolated |
| **Stored Files Metadata (`stored_files`)** | **TABLE_NOT_FOUND** | 4 rows | **VERIFIED MIGRATION ISOLATION** |
| **Analytics Jobs (`analytics_jobs`)** | 39 | 0 | Verified Isolated |
| **Analytics Scripts (`analytics_scripts`)** | N/A (Integrated in Jobs) | N/A | Reconciled |

---

## 4. Deep Investigation of the 25-User Discrepancy

### 4.1 The Issue
- In the Stage 12.2 acceptance report, `vibe_enterprise` was documented as containing **10,217 users**.
- In the Stage 12.2.1 report, `vibe_enterprise` was documented as containing **10,242 users** (+25 users) and **9,404 organizations** (+21 organizations).
- The Stage 12.2.1 report claimed zero database writes occurred against `vibe_enterprise`.
- This discrepancy of 25 users was subjected to an exhaustive chronological audit of `users` and `organizations` records.

### 4.2 Forensics & Chronological Evidence

Inspection of the 35 most recently created users in `vibe_enterprise` (`SELECT id, email, display_name, created_at FROM users ORDER BY created_at DESC;`) revealed the following:

1. **Pre-Stage 12.2.1 Baseline:**
   The last legitimate user created prior to Stage 12.2.1 was:
   - ID: `45f0aa14-0080-4eb6-8713-5e30b5bf1902`
   - Email: `admin_mgr_7d0a1252@example.com`
   - Timestamp: `2026-11-04T12:17:11.893831+00:00` (created during Stage 12.1/12.2 analytics testing).
   - User count up to this timestamp: **10,217**.

2. **Stage 12.2.1 Infiltration Period:**
   Between `2026-11-04T15:49:13 UTC` and `2026-11-04T16:21:44 UTC`, exactly **25 new users** and **25 new organizations** were created in `vibe_enterprise`.

3. **Complete Inventory of the 25 Infiltrated Users:**

| # | User ID | Email | Created Timestamp (UTC) | Associated Test Fixture |
|:---|:---|:---|:---|:---|
| 1 | `fd3f0293-682c-4276-9120-8f278f7945e6` | `owner-b77e6b@example.com` | `2026-11-04 15:49:13.336` | `Workspace Test Org b77e6b` |
| 2 | `5a5180ed-4401-4592-8f61-008be2e420ff` | `owner-f61102@example.com` | `2026-11-04 15:49:14.095` | `Workspace Test Org f61102` |
| 3 | `5e0b2728-6b6e-4372-b24d-f37661fe0e0e` | `owner2-5aff43@example.com` | `2026-11-04 15:49:14.313` | `Tenant Two Org 5aff43` |
| 4 | `1e58671d-1e93-4224-99b5-45011ae55d80` | `owner-740e8f@example.com` | `2026-11-04 15:49:14.627` | `Workspace Test Org 740e8f` |
| 5 | `08cb2a05-8874-4ae9-a954-8456404ddc48` | `fileuser1-cfb32e@example.com` | `2026-11-04 16:05:14.448` | `File Org 1 cfb32e` |
| 6 | `b1bba8d9-723b-421a-8dcc-ed697b2bf7d9` | `fileuser1-89050b@example.com` | `2026-11-04 16:05:17.103` | `File Org 1 89050b` |
| 7 | `bcba8b4b-8a45-4f59-891c-92ebdd10a458` | `fileuser1-4ad540@example.com` | `2026-11-04 16:05:18.357` | `File Org 1 4ad540` |
| 8 | `871912fa-9690-4ef2-8ed7-de8dfaf4d984` | `fileuser2-4d05d1@example.com` | `2026-11-04 16:05:18.673` | `File Org 2 4d05d1` |
| 9 | `ff3e825e-d7db-42a6-b00f-c9941c379ee3` | `fileuser1-4e9286@example.com` | `2026-11-04 16:05:34.520` | `File Org 1 4e9286` |
| 10 | `1f51e32e-c246-4226-beff-c77baea10444` | `fileuser1-754e3b@example.com` | `2026-11-04 16:06:08.527` | `File Org 1 754e3b` |
| 11 | `0393be54-e104-4109-ae9b-1264fa8085d1` | `fileuser1-1083ae@example.com` | `2026-11-04 16:06:12.182` | `File Org 1 1083ae` |
| 12 | `b3bbf38f-185e-494f-b005-2bd812d3d35d` | `fileuser1-c88a11@example.com` | `2026-11-04 16:06:14.653` | `File Org 1 c88a11` |
| 13 | `41a027a3-38d5-443a-8402-0e0efd2239af` | `fileuser2-482eab@example.com` | `2026-11-04 16:06:15.085` | `File Org 2 482eab` |
| 14 | `99ad5a4d-92b7-4297-84dd-36b87c9ea9e3` | `owner-688fc3@example.com` | `2026-11-04 16:19:48.427` | `Workspace Test Org 688fc3` |
| 15 | `5d29e481-4aa0-426e-a13d-e2205dff0126` | `owner-0f1792@example.com` | `2026-11-04 16:19:49.778` | `Workspace Test Org 0f1792` |
| 16 | `56715a8e-b5fe-4017-9937-e36b48c5b5f8` | `owner2-f25773@example.com` | `2026-11-04 16:19:50.138` | `Tenant Two Org f25773` |
| 17 | `587cba48-1f92-4168-b3ac-d59dabfd1efb` | `owner-424f40@example.com` | `2026-11-04 16:19:50.724` | `Workspace Test Org 424f40` |
| 18 | `a64ddb49-ee65-479b-bc87-a46d5343f256` | `rep_owner_77e7de7d@example.com` | `2026-11-04 16:19:55.937` | `Report Org f8c8dc68` |
| 19 | `152045f6-3e6c-41f3-b5fe-8160c19fd17a` | `run_owner_5cc5b032@example.com` | `2026-11-04 16:19:59.207` | `Report Org 37cb78ad` |
| 20 | `b8bc05a7-bbee-4e10-b422-e22e34e38965` | `owner-32acab@example.com` | `2026-11-04 16:21:33.354` | `Workspace Test Org 32acab` |
| 21 | `15ed9a89-811f-4061-b3b7-067a45e450a0` | `owner-b6dad3@example.com` | `2026-11-04 16:21:34.667` | `Workspace Test Org b6dad3` |
| 22 | `cec072cd-137a-4298-92ed-fd73e1c26603` | `owner2-c946fe@example.com` | `2026-11-04 16:21:34.981` | `Tenant Two Org c946fe` |
| 23 | `da0aef07-960c-4ec2-8ad7-f3fd2b98f9a4` | `owner-113d25@example.com` | `2026-11-04 16:21:35.547` | `Workspace Test Org 113d25` |
| 24 | `7cfdaa9f-af73-4135-98ef-9c48545bf289` | `rep_owner_72d892c1@example.com` | `2026-11-04 16:21:41.301` | `Report Org 616ff970` |
| 25 | `96e75768-7fe2-4be3-8d82-8982a59dc791` | `run_owner_6f7dcd45@example.com` | `2026-11-04 16:21:44.694` | `Report Org bf52d7bb` |

### 4.3 Root Cause Analysis
- In `backend/tests/integration/test_workspaces_router.py`, `test_files_router.py`, and `test_reports_api.py`, the pytest fixtures (`session_factory` and `test_org`) connect to `settings.DATABASE_URL`.
- During early implementation in Stage 12.2.1, when the tests were executed between 15:49 UTC and 16:21 UTC, the test execution runner did NOT inject an overridden `DATABASE_URL` pointing to `vibe_enterprise_test`.
- Consequently, the test runner connected to the default development database (`vibe_enterprise`) and executed `OrganizationService.provision_organization_atomic()`, inserting 25 synthetic test organizations and 25 synthetic test users.
- When `test_files_router.py` was executed, the tests failed because table `stored_files` did not exist on `vibe_enterprise`.
- At that point, the developer migrated `vibe_enterprise_test` using `scripts/migrate_test_database.py` and subsequently ran tests with the test database URL.
- **Finding:** The claim in the Stage 12.2.1 report that "zero writes occurred against vibe_enterprise" is **FALSE**. Database preservation was breached during Stage 12.2.1.
- **Current State:** During this Stage 12.2.2 audit, zero writes were performed, and the user count remains exactly at **10,242**.

---

## 5. Migration Isolation Verification

### 5.1 Revision Invariant
- **`vibe_enterprise` Alembic Version:** `370908748232` (verified via `alembic_version` table).
- **`vibe_enterprise_test` Alembic Version:** `481909849343` (verified via `alembic_version` table).
- Migration `481909849343` was **never applied to `vibe_enterprise`**, and the `stored_files` table does not exist in the development database.

### 5.2 Schema Audit of `stored_files` (on `vibe_enterprise_test`)
Inspection of `information_schema.columns`, `pg_constraint`, and `pg_indexes` on `vibe_enterprise_test`:

1. **Columns & Nullability:**
   - `id`: `uuid`, NOT NULL, Primary Key
   - `organization_id`: `uuid`, NOT NULL, FK to `organizations(id)` ON DELETE CASCADE
   - `workspace_id`: `uuid`, NULLABLE (allows org-level files), composite FK to `workspaces(id, organization_id)` ON DELETE SET NULL
   - `uploaded_by_user_id`: `uuid`, NULLABLE, FK to `users(id)` ON DELETE SET NULL
   - `filename`: `varchar(255)`, NOT NULL
   - `content_type`: `varchar(100)`, NOT NULL
   - `size_bytes`: `bigint`, NOT NULL, Check Constraint `size_bytes >= 0`
   - `storage_path`: `varchar(500)`, NOT NULL, UNIQUE
   - `checksum_sha256`: `varchar(64)`, NOT NULL
   - `folder`: `varchar(255)`, NOT NULL, DEFAULT `'/'`
   - `created_at`: `timestamp with time zone`, NOT NULL, DEFAULT `clock_timestamp()`
   - `updated_at`: `timestamp with time zone`, NOT NULL, DEFAULT `clock_timestamp()`

2. **Constraints & Multi-Tenant Boundaries:**
   - `pk_stored_files`: `PRIMARY KEY (id)`
   - `ck_stored_files_size_bytes_nonnegative`: `CHECK ((size_bytes >= 0))`
   - `uq_stored_files_storage_path`: `UNIQUE (storage_path)`
   - `uq_stored_files_org_folder_filename`: `UNIQUE (organization_id, folder, filename)` (prevents duplicate filenames within the same folder per tenant)
   - `fk_stored_files_workspace_composite`: `FOREIGN KEY (workspace_id, organization_id) REFERENCES workspaces(id, organization_id) ON DELETE SET NULL` (guarantees cross-tenant workspace referencing is impossible at the database engine level)

3. **Indexes:**
   - `ix_stored_files_organization_id`: btree index on `organization_id`
   - `ix_stored_files_workspace_id`: btree index on `workspace_id`
   - `ix_stored_files_created_at`: btree index on `created_at`
   - `ix_stored_files_org_folder`: btree index on `(organization_id, folder)`

The database schema matches the Alembic migration specification and the SQLAlchemy ORM model `app.models.stored_file.StoredFile`.

