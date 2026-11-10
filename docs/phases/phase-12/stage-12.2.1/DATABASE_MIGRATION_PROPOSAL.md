# VIBE — PHASE 12 STAGE 12.2.1
# DATABASE MIGRATION PROPOSAL & SCHEMA REVIEW REPORT
## Evaluation of Schema Requirements for File Storage, Workspaces, Reports & System Health

**Workspace:** `D:\vibe`  
**Governing Document:** `docs/IMPLEMENTATION_PLAN.md` v2.0.0  
**Phase:** 12 — Production Readiness, E2E Testing & Deployment Verification  
**Stage:** 12.2.1 — Implementation of Missing Sidebar Modules  
**Date:** November 4, 2026  
**Status:** PROPOSED — STRICT READ-ONLY / AWAITING HUMAN AUTHORIZATION  
**Current Alembic Head:** `370908748232`  
**Preservation Constraint:** DO NOT APPLY MIGRATIONS WITHOUT EXPLICIT HUMAN AUTHORIZATION  

---

## 1. Executive Summary & Schema Inventory

A comprehensive schema inspection was executed across `vibe_enterprise` and `vibe_enterprise_test` to determine whether database modifications are required to support the four sidebar modules.

### Current Database Inventory Across the 4 Modules

| Module | Table Name | Existing Schema Status | Schema Modification Required? |
| :--- | :--- | :--- | :---: |
| **Workspaces** | `public.workspaces` | Exists with `id`, `organization_id`, `name`, `slug`, `created_at`, `updated_at`. Constraints: `uq_workspaces_org_slug`, `uq_workspaces_id_org`. | **NONE (0 changes)** |
| **Reports** | `report_definitions`<br>`report_sections`<br>`report_runs`<br>`report_download_tokens`<br>`report_outbox_events` | All 5 tables exist from Phase 9/10 with full composite keys, indexes, and cascades. | **NONE (0 changes)** |
| **System Health** | *None* | Operational monitoring queries live PostgreSQL pool, Redis, and disk volume stats. | **NONE (0 changes)** |
| **File Storage** | `public.stored_files` | Does NOT exist. Currently, only job-specific storage exists (`analytics_jobs`, `report_runs`). | **PROPOSED BELOW** |

---

## 2. File Storage Schema Design: `stored_files`

To provide true enterprise-grade file management (ACID transactions, relational tenant isolation, metadata search, sorting, and pagination), a dedicated `stored_files` table is proposed.

### 2.1 Proposed Table Specification: `stored_files`

```sql
CREATE TABLE public.stored_files (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    workspace_id UUID REFERENCES public.workspaces(id) ON DELETE SET NULL,
    uploaded_by_user_id UUID REFERENCES public.users(id) ON DELETE SET NULL,
    filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    size_bytes BIGINT NOT NULL,
    storage_path VARCHAR(500) NOT NULL,
    checksum_sha256 VARCHAR(64) NOT NULL,
    folder VARCHAR(255) NOT NULL DEFAULT '/',
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),

    CONSTRAINT uq_stored_files_org_folder_filename UNIQUE (organization_id, folder, filename)
);

CREATE INDEX ix_stored_files_org_id ON public.stored_files(organization_id);
CREATE INDEX ix_stored_files_workspace_id ON public.stored_files(workspace_id);
CREATE INDEX ix_stored_files_created_at ON public.stored_files(created_at DESC);
CREATE INDEX ix_stored_files_org_folder ON public.stored_files(organization_id, folder);
```

### 2.2 Column Justifications & Invariants
1. `id`: UUID primary key, generated server-side.
2. `organization_id`: Strict tenant containment. Foreign key with `ON DELETE CASCADE` ensures that deleting a tenant cleans up all file metadata cleanly.
3. `workspace_id`: Optional workspace association. If a workspace is deleted, `ON DELETE SET NULL` preserves file ownership at the organization level without losing user data.
4. `uploaded_by_user_id`: Audit tracking of uploader. `ON DELETE SET NULL` preserves file records if a user is offboarded.
5. `filename`: Original sanitized display filename (max 255 chars).
6. `content_type`: Verified MIME type (e.g. `text/csv`, `application/pdf`).
7. `size_bytes`: Stored file size in bytes for rapid quota aggregation without disk I/O scans.
8. `storage_path`: Internal isolated filesystem relative path (e.g. `user_files/{org_id}/{file_id}.dat`).
9. `checksum_sha256`: SHA-256 hash for end-to-end data integrity verification and deduplication detection.
10. `folder`: Virtual folder hierarchy (default `/`), enabling folder navigation without nested directory complexity.
11. `uq_stored_files_org_folder_filename`: Enforces that duplicate filenames within the same folder and tenant are rejected or versioned cleanly.

---

## 3. Alternative Architecture: Zero-Migration Metadata Manifest

If the project requires preserving Alembic head `370908748232` without any schema migrations, File Storage can be implemented using a **Tenant-Isolated Filesystem Manifest**:

### How It Works:
- Storage root: `backend/storage/user_files/{organization_id}/`
- Metadata file: `backend/storage/user_files/{organization_id}/.manifest.json`
- An atomic file lock (`storage/user_files/{organization_id}/.lock`) synchronizes reads and writes.
- File metadata (id, filename, size, mime, sha256, uploaded_by, timestamps) is stored in the manifest.
- Physical files are stored by UUID: `storage/user_files/{organization_id}/{file_id}.dat`.

### Architectural Comparison

| Criteria | Option A: PostgreSQL `stored_files` (Proposed) | Option B: Filesystem JSON Manifest (Zero Migration) |
| :--- | :--- | :--- |
| **Alembic Head** | Advances from `370908748232` to new revision | **Preserves `370908748232` 100% unchanged** |
| **Database Blast Radius** | New table in public schema | **Zero changes to PostgreSQL** |
| **Query Performance** | Fast SQL indexing, pagination, and sorting | Memory/JSON parse per tenant |
| **ACID Integrity** | Full PostgreSQL transaction guarantees | File-level atomic rename |
| **Audit FK Integrity** | Foreign keys link to `users`, `workspaces`, `organizations` | Loose UUID references |

---

## 4. Draft Alembic Migration Script (Option A)

```python
"""add_stored_files_table

Revision ID: 481909849343
Revises: 370908748232
Create Date: 2026-11-04 21:15:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '481909849343'
down_revision = '370908748232'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'stored_files',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('workspace_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('workspaces.id', ondelete='SET NULL'), nullable=True),
        sa.Column('uploaded_by_user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=100), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('storage_path', sa.String(length=500), nullable=False),
        sa.Column('checksum_sha256', sa.String(length=64), nullable=False),
        sa.Column('folder', sa.String(length=255), nullable=False, server_default='/'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('clock_timestamp()'), nullable=False),
        sa.UniqueConstraint('organization_id', 'folder', 'filename', name='uq_stored_files_org_folder_filename')
    )
    op.create_index('ix_stored_files_organization_id', 'stored_files', ['organization_id'])
    op.create_index('ix_stored_files_workspace_id', 'stored_files', ['workspace_id'])
    op.create_index('ix_stored_files_created_at', 'stored_files', [sa.text('created_at DESC')])


def downgrade() -> None:
    op.drop_index('ix_stored_files_created_at', table_name='stored_files')
    op.drop_index('ix_stored_files_workspace_id', table_name='stored_files')
    op.drop_index('ix_stored_files_organization_id', table_name='stored_files')
    op.drop_table('stored_files')
```

---

## 5. Rollback & Safety Assessment

### 5.1 Blast Radius Analysis
- **Target Databases:**
  - `vibe_enterprise` (Production baseline: 10,217 users, 9,379 orgs): **UNTOUCHED**. No migrations will be executed against `vibe_enterprise`.
  - `vibe_enterprise_test` (Test baseline: 1 user, 1 org): Target for verified testing if authorized.
- **Table Isolation:** The proposed table is purely additive. It adds one new table (`stored_files`) without modifying or dropping any existing tables, columns, constraints, or rows.
- **Rollback Safety:** `downgrade()` executes `DROP TABLE stored_files` cleanly without leaving orphaned indexes or dangling foreign keys.

---

## 6. Mandatory Gate A Halt & Decision Request

> [!IMPORTANT]
> **GATE A STOP NOTIFICATION:**
> In accordance with Phase 12 Stage 12.2.1 Operating Rules:
> **NO MIGRATIONS HAVE BEEN APPLIED.**
> **THE CURRENT ALEMBIC HEAD REMAINS UNTOUCHED AT `370908748232`.**
>
> The user is requested to select one of the following approaches for File Storage data persistence:
> - **Option A:** Approve the creation of the `stored_files` table and authorized Alembic migration against `vibe_enterprise_test`.
> - **Option B:** Proceed with the Zero-Migration Filesystem JSON Manifest (`.manifest.json`), preserving Alembic head `370908748232` completely unchanged.

