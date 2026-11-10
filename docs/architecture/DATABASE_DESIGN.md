# DATABASE DESIGN & SCHEMA ARCHITECTURE
## Enterprise Admin & Data Intelligence Platform

**Document Version:** 2.0.0  
**RDBMS:** PostgreSQL 16 (Relational Core + Indexed JSONB)  
**Isolation Strategy:** Tenant Isolation via `org_id` + Row-Level Security (RLS)

---

## 1. Schema Partitioning Roadmap (Phased Migration Strategy)

Rather than executing a single, unmanageable 24-table migration, the database schema is divided into 9 discrete, testable migration modules:

1. **Migration 001 - Organization & Multi-Tenancy:** `organizations`, `workspaces`
2. **Migration 002 - Identity & Authentication:** `users`, `user_sessions`, `mfa_credentials`
3. **Migration 003 - RBAC & Permissions:** `roles`, `permissions`, `role_permissions`, `organization_memberships`, `membership_roles`
4. **Migration 004 - Spreadsheet Engine Core:** `workbooks`, `sheets`, `sheet_columns`, `sheet_records`
5. **Migration 005 - Spreadsheet Views & Revisions:** `sheet_views`, `sheet_revisions`, `sheet_locks`
6. **Migration 006 - External Integrations:** `integrations`, `oauth_credentials`, `sync_jobs`
7. **Migration 007 - Analytics & BI:** `data_sources`, `analytics_jobs`, `dashboards`, `dashboard_widgets`, `reports`
8. **Migration 008 - Files & Notification System:** `files`, `file_versions`, `notifications`
9. **Migration 009 - Operations, Outbox & Audit:** `background_jobs`, `backup_records`, `audit_events`

---

## 2. Table Schemas & Definitions

### 2.1 Identity, Organizations & Authentication

```sql
-- Organizations (Tenants)
CREATE TABLE organizations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    slug VARCHAR(100) UNIQUE NOT NULL,
    plan VARCHAR(50) DEFAULT 'enterprise' NOT NULL,
    settings JSONB DEFAULT '{}'::jsonb NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Users
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL, -- Argon2id
    full_name VARCHAR(255) NOT NULL,
    avatar_url TEXT,
    is_active BOOLEAN DEFAULT TRUE NOT NULL,
    is_verified BOOLEAN DEFAULT FALSE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- User Sessions (Server-Managed Opaque Tokens)
CREATE TABLE user_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash VARCHAR(64) UNIQUE NOT NULL, -- SHA-256 of opaque token
    ip_address VARCHAR(45) NOT NULL,
    user_agent TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);
CREATE INDEX idx_user_sessions_lookup ON user_sessions(token_hash) WHERE revoked_at IS NULL;
CREATE INDEX idx_user_sessions_user ON user_sessions(user_id);
```

### 2.2 RBAC & Permissions

```sql
-- Roles
CREATE TABLE roles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE, -- NULL indicates global system roles
    name VARCHAR(100) NOT NULL,
    description TEXT,
    is_system BOOLEAN DEFAULT FALSE NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Permissions
CREATE TABLE permissions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    resource VARCHAR(100) NOT NULL,
    action VARCHAR(50) NOT NULL,
    description TEXT,
    CONSTRAINT uq_perm_resource_action UNIQUE (resource, action)
);

-- Role Permissions
CREATE TABLE role_permissions (
    role_id UUID NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id UUID NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_id)
);

-- Organization Memberships
CREATE TABLE organization_memberships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    CONSTRAINT uq_org_user UNIQUE (org_id, user_id)
);
```

### 2.3 Spreadsheet Engine (Hybrid Storage Model)

```sql
-- Workspaces
CREATE TABLE workspaces (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    icon VARCHAR(50) DEFAULT 'folder',
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Workbooks
CREATE TABLE workbooks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Sheets
CREATE TABLE sheets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workbook_id UUID NOT NULL REFERENCES workbooks(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    order_index INT DEFAULT 0 NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);

-- Sheet Columns (Metadata Definition)
CREATE TABLE sheet_columns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sheet_id UUID NOT NULL REFERENCES sheets(id) ON DELETE CASCADE,
    name VARCHAR(255) NOT NULL,
    key VARCHAR(100) NOT NULL,
    data_type VARCHAR(50) NOT NULL, -- text, number, date, boolean, select, currency, formula
    formula_expression TEXT,
    width INT DEFAULT 160 NOT NULL,
    order_index INT DEFAULT 0 NOT NULL,
    is_indexed BOOLEAN DEFAULT FALSE NOT NULL,
    validation_rules JSONB DEFAULT '{}'::jsonb NOT NULL,
    CONSTRAINT uq_sheet_column_key UNIQUE (sheet_id, key)
);
CREATE INDEX idx_sheet_cols_order ON sheet_columns(sheet_id, order_index);

-- Sheet Records (Row Data in Hybrid JSONB Format)
CREATE TABLE sheet_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    sheet_id UUID NOT NULL REFERENCES sheets(id) ON DELETE CASCADE,
    row_index INT NOT NULL,
    data JSONB DEFAULT '{}'::jsonb NOT NULL,
    version INT DEFAULT 1 NOT NULL, -- Optimistic Concurrency Control
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    CONSTRAINT uq_sheet_row_index UNIQUE (sheet_id, row_index)
);
-- Fast sequential ordering & pagination
CREATE INDEX idx_sheet_records_order ON sheet_records(sheet_id, row_index);
-- GIN index for fast containment & equality matching
CREATE INDEX idx_sheet_records_gin ON sheet_records USING GIN (data jsonb_path_ops);
```

### 2.4 Immutable Audit Events & Transactional Outbox

```sql
-- Transactional Outbox for Durable Background Enqueueing
CREATE TABLE background_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id UUID REFERENCES organizations(id) ON DELETE CASCADE,
    task_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    status VARCHAR(50) DEFAULT 'PENDING' NOT NULL, -- PENDING, PROCESSING, COMPLETED, FAILED
    retry_count INT DEFAULT 0 NOT NULL,
    max_retries INT DEFAULT 3 NOT NULL,
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL,
    processed_at TIMESTAMPTZ
);
CREATE INDEX idx_bg_jobs_pending ON background_jobs(status, created_at) WHERE status = 'PENDING';

-- Immutable Audit Ledger (Tamper-Resistant)
CREATE TABLE audit_events (
    id BIGSERIAL PRIMARY KEY,
    org_id UUID REFERENCES organizations(id),
    actor_id UUID REFERENCES users(id),
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(100) NOT NULL,
    resource_id VARCHAR(100) NOT NULL,
    ip_address VARCHAR(45) NOT NULL,
    user_agent TEXT NOT NULL,
    payload_snapshot JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW() NOT NULL
);
CREATE INDEX idx_audit_org_time ON audit_events(org_id, created_at DESC);
```

#### Enforcing Immutability via Database Trigger:
```sql
CREATE OR REPLACE FUNCTION prevent_audit_tampering()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit event rows are immutable and cannot be updated or deleted.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_protect_audit_events
BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION prevent_audit_tampering();
```

---

## 3. PostgreSQL Row-Level Security (RLS) Strategy

Tenant boundaries are enforced at two levels:
1. **Application Level:** FastAPI repositories filter queries by `org_id` based on verified session context.
2. **Database Engine Defense-in-Depth:** Tables containing `org_id` enable Row-Level Security:
```sql
ALTER TABLE workspaces ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_policy ON workspaces
    FOR ALL
    USING (org_id = NULLIF(current_setting('app.current_org_id', TRUE), '')::UUID);
```
FastAPI sets `SET LOCAL app.current_org_id = :org_id;` upon acquiring a connection from the connection pool.

