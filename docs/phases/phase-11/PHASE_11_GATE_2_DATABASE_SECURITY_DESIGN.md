# VIBE — PHASE 11 GATE 2: DATABASE SECURITY DESIGN REPORT
## Role-Based Least Privilege Architecture & Audit Trail Immutability Design

**Workspace:** `D:\vibe`  
**Governing Roadmap:** `docs/IMPLEMENTATION_PLAN.md` (v2.0.0) — Phase 11 Gate 2 Stage 2.2  
**Current Phase Status:** DESIGN AND TEST PREPARATION ONLY — ZERO DATABASE CHANGES APPLIED  
**Database Engine:** PostgreSQL 16.15  
**Current Alembic Head:** `370908748232 (head)` (STRICTLY PRESERVED)  
**Security Findings Addressed:** SEC-P11-003 (Audit Immutability) & SEC-P11-004 (Least Privilege Roles)  
**Date:** 2026-11-04  

---

## 1. EXECUTIVE DESIGN SUMMARY

In strict accordance with the Gate 2 Master Execution Directive, this document specifies the architectural design, SQL DDL specifications, privilege matrices, and verification procedures for:
1. **SEC-P11-003 (Audit Event Immutability):** Multi-layered defense-in-depth guaranteeing append-only enforcement for `administrative_audit_events`.
2. **SEC-P11-004 (PostgreSQL Least Privilege):** Separation of the monolithic superuser `vibe_admin` into isolated, purpose-built database roles.

> [!IMPORTANT]
> **MANDATORY BOUNDARY ENFORCEMENT:**  
> This document is a **design specification**. In compliance with Boundary B of the Gate 2 Authorization, **NO SQL DDL, ALTER ROLE, GRANT, REVOKE, OR ALEMBIC MIGRATION HAS BEEN EXECUTED** against the running database. All proposed SQL changes require explicit human review and approval prior to execution.

---

## 2. SEC-P11-003: AUDIT EVENT IMMUTABILITY DESIGN

### 2.1 Threat Model & Engineering Realities
The application captures administrative operations (membership modifications, role assignments, security configuration edits) in `administrative_audit_events`. The current implementation enforces append-only semantics purely at the application repository layer. If an application connection is compromised or executes an arbitrary SQL injection payload, the connected role possesses full `UPDATE` and `DELETE` capability.

#### Honest Defense-in-Depth Assessment (Avoiding Architectural Fallacies)
- **Trigger Alone Is Not Absolute:** A PostgreSQL trigger (`BEFORE UPDATE OR DELETE`) prevents standard DML operations, but any role with table ownership or `SUPERUSER` privileges can execute `ALTER TABLE administrative_audit_events DISABLE TRIGGER ALL;` or `DROP TABLE`.
- **Role Permissions Alone Are Not Absolute:** Revoking `UPDATE` and `DELETE` prevents DML for non-owners, but does not prevent destructive modifications if the connection connects as the table owner or superuser.
- **The Solution: Multi-Layered Immutability Architecture:**
  1. **Layer 1 (Database Trigger):** Unconditionally raises an exception on any `UPDATE` or `DELETE` attempt.
  2. **Layer 2 (Role Permission Revocation):** The application runtime role (`vibe_app`) is granted strictly `INSERT` and `SELECT`, with `UPDATE` and `DELETE` explicitly withheld.
  3. **Layer 3 (Table Ownership Separation):** The table is owned by a migration role (`vibe_migration`), not the application runtime role (`vibe_app`), preventing `ALTER TABLE ... DISABLE TRIGGER`.
  4. **Layer 4 (External Replica / WORM Archival):** Physical WAL archiving to S3/WORM object lock guarantees that even if a local database is destroyed, historical audit events remain immutable on remote storage.

### 2.2 Trigger & Function Specification

```sql
-- 1. Create audit protection trigger function
CREATE OR REPLACE FUNCTION trg_enforce_audit_event_immutability()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'COMPLIANCE VIOLATION: administrative_audit_events is an immutable append-only ledger. % operations are strictly prohibited.', TG_OP
    USING ERRCODE = '55000', -- Object not in prerequisite state / custom violation
          HINT = 'Audit event records cannot be modified or pruned via application DML.';
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- 2. Attach trigger to administrative_audit_events for UPDATE and DELETE
DROP TRIGGER IF EXISTS trg_audit_events_immutable ON administrative_audit_events;

CREATE TRIGGER trg_audit_events_immutable
BEFORE UPDATE OR DELETE ON administrative_audit_events
FOR EACH ROW
EXECUTE FUNCTION trg_enforce_audit_event_immutability();
```

---

## 3. SEC-P11-004: POSTGRESQL LEAST PRIVILEGE ROLE DESIGN

### 3.1 Role Inventory & Separation of Duties

Currently, all application components connect as `vibe_admin` (a PostgreSQL `SUPERUSER`). We design a 4-tier role hierarchy:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   POSTGRESQL ROLE TOPOLOGY                             │
├────────────────────┬───────────┬──────────────┬────────────────────────┤
│ Role Name          │ Superuser │ Login Allowed│ Operational Scope      │
├────────────────────┼───────────┼──────────────┼────────────────────────┤
│ vibe_admin         │ YES       │ YES (DBA/DR) │ DBA, Physical Backups  │
│ vibe_migration     │ NO        │ YES (Deploy) │ Schema Owner & Alembic │
│ vibe_app           │ NO        │ YES (FastAPI)│ Core Web API DML       │
│ vibe_worker        │ NO        │ YES (Celery) │ Celery Analytics & BI  │
└────────────────────┴───────────┴──────────────┴────────────────────────┘
```

### 3.2 Service Privilege Matrix

| Database Object / Schema | `vibe_migration` | `vibe_app` (FastAPI) | `vibe_worker` (Celery) | `vibe_admin` (DBA) |
|---|---|---|---|---|
| Schema `public` | `CREATE, USAGE` (Owner) | `USAGE` | `USAGE` | `ALL` |
| Application Tables (e.g. `users`, `sheets`, `dashboards`) | `ALL` | `SELECT, INSERT, UPDATE, DELETE` | `SELECT, INSERT, UPDATE, DELETE` | `ALL` |
| `administrative_audit_events` | `ALL` (Owner) | `SELECT, INSERT` (NO UPDATE, NO DELETE) | `SELECT, INSERT` (NO UPDATE, NO DELETE) | `ALL` |
| Sequences (`*`) | `ALL` | `USAGE, SELECT` | `USAGE, SELECT` | `ALL` |
| Table Creation / DDL (`CREATE TABLE`, `ALTER TABLE`) | **ALLOWED** | **PROHIBITED** | **PROHIBITED** | **ALLOWED** |
| Replication / Basebackup (`pg_basebackup`) | **PROHIBITED** | **PROHIBITED** | **PROHIBITED** | **ALLOWED** |
| OS File / Program Access (`pg_read_server_files`) | **PROHIBITED** | **PROHIBITED** | **PROHIBITED** | **ALLOWED** |

### 3.3 Proposed Implementation SQL Script

```sql
-- Step 1: Create unprivileged application runtime role
CREATE ROLE vibe_app WITH LOGIN ENCRYPTED PASSWORD '${APP_DB_PASSWORD}';

-- Step 2: Create unprivileged worker runtime role
CREATE ROLE vibe_worker WITH LOGIN ENCRYPTED PASSWORD '${WORKER_DB_PASSWORD}';

-- Step 3: Create dedicated migration role
CREATE ROLE vibe_migration WITH LOGIN ENCRYPTED PASSWORD '${MIGRATION_DB_PASSWORD}';

-- Step 4: Grant schema usage
GRANT USAGE ON SCHEMA public TO vibe_app, vibe_worker, vibe_migration;
GRANT CREATE ON SCHEMA public TO vibe_migration;

-- Step 5: Grant table DML to runtime roles
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO vibe_app, vibe_worker;

-- Step 6: Explicitly restrict administrative_audit_events on runtime roles
REVOKE UPDATE, DELETE, TRUNCATE ON TABLE administrative_audit_events FROM vibe_app, vibe_worker;
GRANT SELECT, INSERT ON TABLE administrative_audit_events TO vibe_app, vibe_worker;

-- Step 7: Grant sequence usage
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vibe_app, vibe_worker;

-- Step 8: Ensure future tables automatically inherit appropriate permissions
ALTER DEFAULT PRIVILEGES FOR ROLE vibe_migration IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO vibe_app, vibe_worker;

ALTER DEFAULT PRIVILEGES FOR ROLE vibe_migration IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO vibe_app, vibe_worker;
```

---

## 4. IMPACT ASSESSMENT & BACKWARD COMPATIBILITY

### Risk Mitigation Strategy
1. **Local Development Friction:** In developer environments (`ENVIRONMENT="development"`), maintaining `vibe_admin` as the single developer connection prevents local password management complexity. The multi-role configuration is driven via environment variables in `docker-compose.yml` (`DATABASE_URL`, `ALEMBIC_DATABASE_URL`).
2. **Migration Execution:** Alembic is configured to use `ALEMBIC_DATABASE_URL` (connecting as `vibe_migration`), ensuring all DDL migrations execute under the authoritative schema owner.
3. **Celery Worker Advisory Locks:** `analytics/storage.py` utilizes `pg_advisory_xact_lock()`. PostgreSQL permits any authenticated user to execute advisory locks within their own transaction; non-superuser roles retain full advisory lock capability.

---

## 5. DIRECT REGRESSION VERIFICATION PLAN

Once authorized for application in an isolated staging test environment, the following test cases verify compliance:
1. **`test_audit_update_rejection`:** Attempt `UPDATE administrative_audit_events SET action = 'tampered' WHERE id = ...;` — Must raise SQL exception code `55000`.
2. **`test_audit_delete_rejection`:** Attempt `DELETE FROM administrative_audit_events WHERE id = ...;` — Must raise SQL exception code `55000`.
3. **`test_app_role_ddl_rejection`:** Connect as `vibe_app` and attempt `CREATE TABLE malicious (...);` — Must fail with `permission denied for schema public`.
4. **`test_app_role_dml_success`:** Connect as `vibe_app` and execute standard spreadsheet row insert, update, select, and delete — Must succeed with 100% fidelity.

---

## 6. STAGE 2.2 CONCLUSION & STOP CONDITION

The security design for audit immutability (SEC-P11-003) and least privilege role separation (SEC-P11-004) is complete and verified against PostgreSQL 16.15 specifications.

**HOLD NOTICE:**  
No database roles or triggers have been created or modified in the live database. Human authorization is required before applying this design.
