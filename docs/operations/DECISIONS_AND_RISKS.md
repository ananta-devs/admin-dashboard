# TECHNICAL DECISIONS, RISKS & OUTSTANDING ITEMS
## Enterprise Admin & Data Intelligence Platform

**Document Version:** 2.0.0  
**Status:** Phase 0 Review Checkpoint

---

## 1. Technical Decisions & Trade-off Matrix

| Decision Area | Selected Strategy | Rejected Alternatives | Technical Justification |
| :--- | :--- | :--- | :--- |
| **Spreadsheet Storage** | **Hybrid Model:** JSONB row payload + Column Metadata + targeted B-Tree indexes on indexed expressions. | 1. Pure Row JSONB<br>2. Normalized Cell EAV | EAV creates 2.5 million rows for 50k spreadsheets, crippling queries. Pure JSONB cannot accelerate sorting. Hybrid gives sub-millisecond filtering and instant column creation. |
| **Spreadsheet Grid** | **TanStack Table v9 + TanStack Virtual** | 1. Handsontable<br>2. AG Grid Enterprise | Handsontable is commercially restricted and carries GPLv3 HyperFormula risks. AG Grid Enterprise costs $999/dev/yr and fights custom CSS. TanStack gives 100% UI control with MIT license. |
| **Authentication** | **Opaque 256-bit server-managed tokens** in HttpOnly cookies, cached in Redis DB 1. | JWT (PyJWT / python-jose) | Avoids token revocation complexity. Server sessions provide instant single-click revocation across all devices without complex token blacklists. |
| **Formula Engine** | **Deterministic AST Parser & DAG Evaluator** (zero eval) | JavaScript `eval()` or Python `eval()` | Complete prevention of code injection vulnerabilities; full control over cycle detection and Excel-compatible error reporting. |
| **Background Processing** | **Celery + Redis** with PostgreSQL **Transactional Outbox** | FastAPI `BackgroundTasks` alone | Guarantees durable execution, automatic retries with exponential backoff, and zero dropped jobs during Redis restarts. |
| **Disaster Recovery** | **Logical Dumps (Dev)** + **Continuous WAL Archiving & Base Backups (Prod)** | Simple daily database dumps | True Point-in-Time Recovery (PITR) to any second; empirically verifiable RPO ≤ 15 minutes. |

---

## 2. Technical Risk Management

| Risk | Severity | Impact | Mitigation Strategy |
| :--- | :--- | :--- | :--- |
| **16 GB RAM Host Memory Constraints** | High | System thrashing during concurrent builds. | Docker containers have hard resource caps in `docker-compose.yml`: PostgreSQL max 1.5 GB, Redis max 512 MB. Retains > 13.5 GB for the OS and local processes. |
| **Large Grid Virtualization Lag** | Medium | Scrolling jank on 50,000 rows. | TanStack Virtual restricts rendered DOM nodes to only visible viewport elements (< 150 `<tr>` elements); cell inputs are debounced. |
| **Spreadsheet Row Concurrency Collisions** | Medium | Silent overwrites during concurrent edits. | Row-level Optimistic Concurrency Control (OCC) via `version` column. Mismatched version returns HTTP 409 Conflict with diff resolution prompt. |
| **Google Sheets OAuth Credentials** | Low | Blocked third-party testing. | Modular mock adapter pattern enables 100% offline unit/integration test coverage; real OAuth keys unlock seamlessly when provided. |

---

## 3. Outstanding Technical Decisions

Below is the explicit register of technical decisions requiring documentation and review before Phase 1:

### Decision 1: Redis Instance Architecture (Development vs. Production)
- **Options:**
  - *Option A (Recommended for Dev):* Single Redis 7 container partitioned by logical database index (DB 0 = Celery Broker, DB 1 = Session Cache & Rate Limiting).
  - *Option B (Recommended for Prod):* Two isolated Redis container instances (one configured with `noeviction` for task persistence; one with `volatile-lru` for caching).
- **Technical Reasoning:** Option A saves significant RAM on the 16 GB development machine while enforcing logical separation. Production will deploy Option B for total fault isolation.
- **Blocks Phase 1?** **No.** Docker Compose is already configured for Option A.

### Decision 2: Google Sheets Live Credentials vs. Mock Adapter
- **Options:**
  - *Option A (Recommended):* Build Phase 7 with a complete, fully tested Mock Google Sheets Provider that implements the exact OAuth and API interfaces, allowing seamless injection of live Google Cloud Client ID/Secret when available.
  - *Option B:* Wait for user to create and provide live Google Cloud credentials before beginning integration architecture.
- **Technical Reasoning:** Option A prevents project blocking and guarantees 100% testable automated integration suites in CI/CD environments.
- **Blocks Phase 1?** **No.** Integrations are scheduled for Phase 7.

### Decision 3: 3D Visualization Scope (Three.js / React Three Fiber)
- **Options:**
  - *Option A (Recommended):* Completely exclude Three.js from the primary administrative dashboard, data grids, and navigation; include an optional, lazy-loaded interactive 3D topology map exclusively in the Analytics Overview module.
  - *Option B:* Omit Three.js entirely to minimize bundle size.
- **Technical Reasoning:** Option A respects the master prompt's aesthetic ambition while strictly preventing heavy 3D bundles from impacting initial page load or dashboard responsiveness.
- **Blocks Phase 1?** **No.** Visualizations are scheduled for Phase 9.

