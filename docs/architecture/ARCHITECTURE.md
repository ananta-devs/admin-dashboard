# ARCHITECTURE & SYSTEM DESIGN (REVISED & COMPREHENSIVE)
## Enterprise Admin & Data Intelligence Platform

**Document Version:** 2.0.0 (Phase 0 Post-Correction)  
**Status:** Awaiting Final Approval Before Phase 1  
**Topology:** Next.js App Router (Client & BFF Proxy) + FastAPI (Authoritative Business Logic & Database Owner) + Celery (Redis-backed Worker) + PostgreSQL 16 + Redis 7

---

## 1. System Topology & Service Boundaries

```
[ Browser / Client ]
       |
       | HTTPS (Same-Origin Session Cookie: `vibe_session`)
       v
[ Next.js 16 App Router (Port 3000) ]
  - React 19 Client Components & Server Layouts
  - TanStack Query v5 (client-side cache & deduplication)
  - Reverse Proxy Route Handlers (`/api/v1/*` -> `http://fastapi:8000/api/v1/*`)
  - Injects client IP & User-Agent (`X-Forwarded-For`, `X-Forwarded-Proto`)
  - Enforces Strict Nonce-based Content Security Policy (CSP)
       |
       | Internal HTTP (Private Network / Loopback)
       v
[ FastAPI Authoritative Business Core (Port 8000) ]
  - Security Guard: Authenticates `vibe_session` against Redis / PostgreSQL
  - RBAC & ABAC Policy Enforcement
  - Schema Validation (Pydantic v2 strict models)
  - Unit-of-Work & Asynchronous ORM (SQLAlchemy 2.x async engine)
  - Database Transaction Management
  - Transactional Outbox Pattern for Background Tasks
       |
       +------------------------------------+
       |                                    |
       v                                    v
[ PostgreSQL 16 (Port 5432) ]       [ Redis 7 (Port 6379) ]
  - Authoritative Relational DB       - DB 0: Celery Broker & Results
  - Hybrid Spreadsheet Records        - DB 1: Session Cache & Rate Limiting
  - Row-Level Security (RLS)          - Eviction Policy: volatile-lru
  - Immutable Audit Event Ledger      - Maxmemory: 384MB
       ^                                    ^
       |                                    |
       +-----------------+------------------+
                         |
                         v
[ Celery Background Worker Process ]
  - Analytics & Transformations (Polars / Pandas)
  - Google Sheets Synchronizer & Conflict Resolver
  - Large Dataset CSV/XLSX Import & Export
  - Database Backup & Restore Verification Engine
  - Isolated subprocess for formula recalculation
```

### 1.1 Strict Service Boundary Rules
1. **Frontend Isolation:** Next.js **never** connects directly to PostgreSQL or Redis. All data modifications and retrievals flow through FastAPI via authenticated HTTP requests.
2. **Authoritative Domain:** FastAPI owns all business rules, authorization decisions, audit event recording, and schema validations.
3. **Asynchronous Execution:** Celery workers handle tasks requiring more than 250ms of compute (large file parsing, data transformations, backup runs) via Redis message queues.
4. **Resilient Enqueueing:** FastAPI writes job records to PostgreSQL within the primary database transaction (`transactional_outbox` table), guaranteeing no lost jobs if Redis encounters a momentary disconnect.

---

## 2. Spreadsheet Storage Architecture: Hybrid Model

### 2.1 Comparative Analysis (50,000 Rows x 50 Columns)

| Evaluation Metric | Option 1: Row-based Pure JSONB | Option 2: Normalized Cell Table (EAV) | Option 3: Hybrid (JSONB + Generated Columns) ⭐ |
| :--- | :--- | :--- | :--- |
| **Storage Footprint** | Moderate (~40 MB per 50k rows). | **Massive** (2.5 million rows per 50k spreadsheet; ~220 MB with indexes). | **Optimal** (~48 MB per 50k rows including selective indexes). |
| **Numeric Sorting/Filtering** | Slow without specialized indexes. GIN index does NOT accelerate sorting (`ORDER BY (data->>'price')::numeric`). | Fast via indexed column, but queries require costly row aggregation (`GROUP BY row_id`). | **Extremely Fast** using targeted B-Tree indexes on indexed/generated virtual expressions. |
| **Date-Range Filtering** | Requires full table scan or expensive expression index per query. | Fast on single date, but joining across 50 columns explodes query complexity. | **Sub-millisecond** with dedicated B-Tree expression or generated date columns. |
| **Multi-Column Sorting** | Full table scan & in-memory PostgreSQL filesort (`SortMethod: external merge`). | Requires 2-5 complex self-joins, causing severe planner degradation. | **Hardware-accelerated multi-column B-tree indexes** on frequently queried columns. |
| **Bulk Updates** | Fast: updates single row JSON payload (`jsonb_set` or replace). | Very slow: requires 50 individual cell inserts/updates per row. | **Fast**: single-row JSONB update + auto-maintenance of virtual columns. |
| **Schema Evolution (New Columns)**| Instant (no table alter; just add key to JSONB). | Instant (insert rows), but degrades read performance. | **Instant**: new dynamic columns exist in JSONB without DDL locking. |

### 2.2 Selected Hybrid Architecture
To balance unlimited dynamic columns with high-speed querying on large spreadsheets (50k+ rows):
1. **Column Metadata (`sheet_columns`):** Normalizes column identity, data type, formula expressions, and indexing rules.
2. **Row Data (`sheet_records`):**
   - Stores cells as key-value pairs in a `data` JSONB column: `{"col_uuid_1": 1500.50, "col_uuid_2": "2026-11-01"}`.
   - Includes integer `row_index` for stable virtualization ordering.
   - Includes `version` (optimistic lock integer) and `sheet_id` (foreign key).
3. **Targeted PostgreSQL Indexes:**
   - **B-Tree Index on Row Index:** `CREATE INDEX idx_sheet_records_order ON sheet_records (sheet_id, row_index);`
   - **GIN Index (jsonb_path_ops):** Used strictly for equality checking and JSON containment (`data @> '{"status": "Active"}'`):
     `CREATE INDEX idx_sheet_records_gin ON sheet_records USING gin (data jsonb_path_ops);`
   - **Expression B-Tree Indexes on User-Indexed Columns:** When an administrator flags a column as "indexed/sorted frequently", FastAPI executes:
     `CREATE INDEX idx_sheet_col_<uuid> ON sheet_records (((data->>'<uuid>')::numeric)) WHERE sheet_id = '<sheet_uuid>';`

---

## 3. Spreadsheet Formula Engine

The formula engine is built as an independent, deterministic DAG (Directed Acyclic Graph) calculation module with zero dependencies on the DOM or browser window. **It explicitly prohibits `eval()`, `new Function()`, and Python `eval()`.**

```
[ Raw Formula String (e.g. "=SUM(A1:B10) + IF(C1 > 0, C1, 0)") ]
                       |
                       v
             [ Lexer / Tokenizer ]
                       |
                       v
             [ Recursive Descent Parser ]
                       |
                       v
             [ Abstract Syntax Tree (AST) ]
                       |
                       v
      [ Dependency Extractor & DAG Builder ]
                       |
         +-------------+-------------+
         |                           |
   [ Circular Check ]      [ Topological Sort ]
  (Tarjan's Algorithm)               |
         |                           v
   (Raise #CYCLE!)            [ Step Evaluator ]
                                     |
                       v-------------+-------------v
               [ Frontend Engine ]         [ Backend Engine ]
               (Typescript AST)            (Python AST Worker)
```

### 3.1 Supported MVP Grammar
- **Grammar Subset:** Numbers, Strings, Booleans, Arithmetic (`+`, `-`, `*`, `/`, `^`, `%`), Comparisons (`=`, `<>`, `>`, `<`, `>=`, `<=`), Cell References (`A1`, `$B$4`), Range References (`A1:C10`).
- **Core Built-in Functions:**
  - Math: `SUM(range)`, `AVERAGE(range)`, `MIN(range)`, `MAX(range)`, `ABS(val)`, `ROUND(val, digits)`
  - Counting: `COUNT(range)`, `COUNTA(range)`
  - Logic: `IF(condition, true_val, false_val)`, `AND(val1, val2, ...)`, `OR(val1, val2, ...)`, `NOT(val)`
  - Text: `CONCAT(str1, str2, ...)`, `LEN(str)`, `TRIM(str)`, `UPPER(str)`, `LOWER(str)`
- **Error Codes:** `#CYCLE!` (circular dependency), `#DIV/0!` (division by zero), `#VALUE!` (type mismatch), `#REF!` (invalid target), `#NAME?` (unrecognized function identifier).

---

## 4. Disaster Recovery & Backup Architecture (PostgreSQL)

```
[ PostgreSQL 16 Primary ]
        |
        |-- (Real-time WAL Streaming) --> [ Local /wal_archive ]
        |                                       |
        | (Every 15 min or 16MB)                | (Rotated & Encrypted)
        v                                       v
[ pg_dump Scheduled Logical Backup ]     [ Remote S3 / Backup Vault ]
        |                                 - Full Base Backups (Every 24h)
        v                                 - Compressed WAL Archive Logs
[ AES-256-GCM + SHA-256 Checksum ]        - 30-Day Retention Policy
```

### 4.1 RPO & RTO Guarantees
- **Local Development:**
  - Strategy: Automated daily logical dump (`pg_dump -Fc`) + manual trigger.
  - Target: RPO = 24 hours, RTO = 5 minutes.
- **Production Environment:**
  - Continuous WAL Archiving (`archive_mode = on`, `archive_command = 'test ! -f /wal_archive/%f && cp %p /wal_archive/%f'`).
  - Base Backup: Automated full base backup created daily at 02:00 UTC using `pg_basebackup`.
  - Point-in-Time Recovery (PITR): Enables restoring the cluster to any given millisecond within the last 30 days.
  - **Empirical Target:** Production RPO ≤ 15 minutes (bounded by WAL archive timeout).
  - **Empirical Target:** Production RTO ≤ 45 minutes for databases under 100 GB (measured base restore + WAL replay speed).

---

## 5. Redis Key Namespace, Caching & Task Queues

To guarantee data safety and prevent key collisions, Redis is partitioned across dedicated logical databases with strict memory management:

| Database ID | Function | Eviction Policy | Maxmemory | Key Patterns |
| :--- | :--- | :--- | :--- | :--- |
| **DB 0** | Celery Message Broker & Result Store | `noeviction` | 256 MB | `celery`, `celery-task-meta-*` |
| **DB 1** | Application Cache, Session Cache & Rate Limits | `volatile-lru` | 128 MB | `sess:<session_hash>`, `rl:<ip>:<endpoint>`, `cache:<entity>:<id>` |

### 5.1 Transactional Outbox Pattern
To prevent lost background tasks during partial network failures:
1. When a business mutation occurs (e.g., CSV uploaded for processing), FastAPI inserts the task payload into the `background_jobs` table in PostgreSQL **within the same ACID transaction**.
2. A lightweight queue publisher reads committed outbox rows and dispatches them to Celery via Redis.
3. Upon task completion or failure, the Celery worker updates the job status directly in PostgreSQL.

---

## 6. Authentication & Session Management

- **No Unjustified JWTs:** PyJWT has been removed from the primary architecture. Authentication uses **opaque 256-bit server-managed session tokens**.
- **Issuance:** Tokens are generated via `secrets.token_urlsafe(32)`.
- **Database Storage:** The SHA-256 hash of the token is stored in the `user_sessions` PostgreSQL table.
- **High-Speed Validation:** The session hash is cached in Redis DB 1 (`sess:<hash>`) with a 24-hour TTL, containing user ID, organization ID, and role set.
- **Client Delivery:** Issued strictly via an `HttpOnly`, `Secure`, `SameSite=Lax` cookie named `vibe_session`.
- **Invalidation:** Single logout deletes the Redis key and sets `revoked_at = NOW()` in PostgreSQL. "Logout from all devices" revokes all active session records for the user.

---

## 7. Next.js Nonce-Based CSP & Security Headers

Next.js 16 leverages dynamic server rendering with a generated per-request cryptographic nonce:

```
Content-Security-Policy:
  default-src 'self';
  script-src 'self' 'nonce-{RANDOM_NONCE}' 'strict-dynamic';
  style-src 'self' 'unsafe-inline';
  img-src 'self' data: blob: https:;
  font-src 'self' data:;
  connect-src 'self' http://localhost:8000;
  frame-ancestors 'none';
  object-src 'none';
  base-uri 'self';
  form-action 'self';
```

- **Development Exception:** In local development (`NODE_ENV === 'development'`), Next.js fast-refresh requires `'unsafe-eval'`. This is strictly stripped in production builds.

---

## 8. File Storage Strategy

- **Development:** Local directory storage mounted at `d:\vibe\uploads`, abstracted behind a generic `FileStorageService` interface. File metadata is saved in PostgreSQL with SHA-256 integrity hashes.
- **Production:** The identical `FileStorageService` interface maps to S3/Cloud Object Storage with private buckets and presigned, short-lived download URLs (5-minute TTL). Zero code changes required between environments.

