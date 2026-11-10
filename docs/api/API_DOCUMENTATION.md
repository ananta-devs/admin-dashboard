# VIBE Enterprise — REST API & Endpoint Contracts

**API Base URL:** `http://127.0.0.1:8000/api/v1`  
**Protocol:** RESTful JSON via FastAPI  
**Authentication:** HttpOnly Secure Session Cookie (`vibe_session`)  
**CSRF Defense:** Session-bound `X-CSRF-Token` header for state-changing methods (`POST`, `PUT`, `PATCH`, `DELETE`)  
**Error Envelope:** `{"success": false, "error": {"code": "...", "message": "...", "details": ...}}`

---

## Subsystem Index

| Domain | Router Prefix | Primary Capabilities |
| :--- | :--- | :--- |
| [1. Authentication & Identity](#1-authentication--identity) | `/api/v1/auth` | Login, 2FA/MFA, Session management, Profile, CSRF bootstrap, Passwords |
| [2. Organization Governance & RBAC](#2-organization-governance--rbac) | `/api/v1/organization` | Memberships, Roles, Permissions matrix, Audit logging, Settings |
| [3. Workspaces Management](#3-workspaces-management) | `/api/v1/workspaces` | Multi-tenant workspace partitions, naming, and lifecycle |
| [4. Spreadsheet Engine](#4-spreadsheet-engine) | `/api/v1/spreadsheets` | Workbooks, Sheets, Cell ranges, Reactive calculations, Undo/Redo |
| [5. Analytics & DuckDB Engine](#5-analytics--duckdb-engine) | `/api/v1/analytics` | Analytical query jobs, Parquet storage, Execution status, Sheet publishing |
| [6. Executive Dashboards & Studio](#6-executive-dashboards--studio) | `/api/v1/dashboards` | Interactive dashboard definitions, Grid layouts, Widget query bindings |
| [7. Intelligence Reports Engine](#7-intelligence-reports-engine) | `/api/v1/reports` | Multi-section scheduled report definitions, Run triggers, PDF/HTML downloads |
| [8. Enterprise File Storage](#8-enterprise-file-storage) | `/api/v1/files` | File uploads, Previews, Secure streaming downloads, Storage quota tracking |
| [9. Integrations & Google Connectors](#9-integrations--google-connectors) | `/api/v1/integrations` | OAuth2 connectors, Google Sheets import/export, Sheet data reading |
| [10. Data Sync & Conflict Resolution](#10-data-sync--conflict-resolution) | `/api/v1/sync` | Bi-directional data sync mappings, Sync execution runs, Conflict resolution |
| [11. Sandboxed Python Scripts](#11-sandboxed-python-scripts) | `/api/v1/scripts` | Isolated code execution environment, Runtime policies, Dependency installation |
| [12. Backups & Disaster Recovery](#12-backups--disaster-recovery) | `/api/v1/backups` | Database snapshot status, Cryptographic checksum verification, Restore history |
| [13. System Health & Telemetry](#13-system-health--telemetry) | `/api/v1/system` | Subsystem telemetry, Latency diagnostics, Memory utilization, Database stats |

---

## 1. Authentication & Identity (`/api/v1/auth`)

| Method | Endpoint | Description | Auth Required |
| :--- | :--- | :--- | :---: |
| `POST` | `/login` | Primary authentication via email and password | No |
| `POST` | `/login/mfa` | Complete login challenge using TOTP code | No |
| `POST` | `/register` | Provision new user account and default organization | No |
| `POST` | `/logout` | Revoke active session and clear browser session cookie | Yes |
| `POST` | `/logout-all` | Revoke all active sessions for the current user | Yes |
| `GET` | `/me` | Retrieve profile and active tenant context | Yes |
| `PATCH` | `/me` | Update profile information (display name, avatar) | Yes |
| `GET` | `/sessions` | List active user sessions with IP and User-Agent info | Yes |
| `DELETE` | `/sessions/{session_id}` | Terminate specific remote session | Yes |
| `POST` | `/csrf-token` | Bootstrap or rotate session-bound CSRF token | Yes |
| `POST` | `/change-password` | Update account password with current credential validation | Yes |
| `POST` | `/forgot-password` | Trigger email reset challenge token | No |
| `POST` | `/reset-password` | Finalize password reset via action token | No |
| `POST` | `/select-tenant` | Switch active tenant organization context | Yes |
| `POST` | `/mfa/totp/setup` | Generate TOTP QR seed for authenticator app | Yes |
| `POST` | `/mfa/totp/confirm` | Validate first TOTP code and enable 2FA | Yes |
| `POST` | `/mfa/totp/disable` | Disable 2FA with current password confirmation | Yes |
| `POST` | `/mfa/recovery-codes/regenerate` | Reissue set of 10 single-use emergency backup codes | Yes |

---

## 2. Organization Governance & RBAC (`/api/v1/organization`)

| Method | Endpoint | Description | Permission Scope |
| :--- | :--- | :--- | :--- |
| `GET` | `/members` | List organization members and their role assignments | `users:read` |
| `DELETE` | `/members/{member_id}` | Remove member from organization | `users:delete` |
| `PUT` | `/members/{member_id}/roles` | Assign roles to organization member | `roles:assign` |
| `GET` | `/roles` | List custom and system roles with permission sets | `roles:read` |
| `POST` | `/roles` | Create new custom organizational role | `roles:write` |
| `GET` | `/roles/{role_id}` | Get detailed permission matrix for specific role | `roles:read` |
| `PATCH` | `/roles/{role_id}` | Update role details or permission assignments | `roles:write` |
| `DELETE` | `/roles/{role_id}` | Remove custom role (system roles protected) | `roles:delete` |
| `GET` | `/permissions` | List all 46 system permission definitions | `roles:read` |
| `GET` | `/audit-logs` | Query immutable tenant audit trail with filters | `audit:read` |
| `GET` | `/settings` | Retrieve organizational settings and quotas | `org:read` |
| `PATCH` | `/settings` | Update organization display name and preferences | `org:write` |
| `POST` | `/invitations` | Issue invitation to join organization via email | `users:invite` |
| `POST` | `/invitations/accept` | Accept pending organization invitation | Public |
| `POST` | `/ownership/transfer` | Transfer primary organization ownership | Owner Only |

---

## 3. Workspaces Management (`/api/v1/workspaces`)

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | List all workspaces for active organization |
| `POST` | `/` | Create new workspace within organization |
| `GET` | `/{workspace_id}` | Retrieve workspace details and metadata |
| `PATCH` | `/{workspace_id}` | Update workspace name or description |
| `DELETE` | `/{workspace_id}` | Delete workspace (default workspace protected) |

---

## 4. Spreadsheet Engine (`/api/v1/spreadsheets`)

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/workbooks` | List workbooks within active workspace |
| `POST` | `/workbooks` | Create new workbook |
| `GET` | `/workbooks/{workbook_id}` | Retrieve workbook metadata |
| `PATCH` | `/workbooks/{workbook_id}` | Update workbook title or properties |
| `DELETE` | `/workbooks/{workbook_id}` | Delete workbook and associated sheets |
| `GET` | `/workbooks/{workbook_id}/sheets` | List sheets within workbook |
| `POST` | `/workbooks/{workbook_id}/sheets` | Add new sheet tab to workbook |
| `GET` | `/sheets/{sheet_id}` | Retrieve sheet metadata and column definitions |
| `PATCH` | `/sheets/{sheet_id}` | Update sheet properties or column schema |
| `GET` | `/sheets/{sheet_id}/range` | Query specific cell range (e.g. `A1:D50`) |
| `GET` | `/sheets/{sheet_id}/rows` | Paginated virtualized row data for viewport rendering |
| `GET` | `/sheets/{sheet_id}/cells/{ref}` | Get individual evaluated cell value, formula, and AST |
| `PATCH` | `/sheets/{sheet_id}/cells` | Batch cell updates with dependency graph recalculation |
| `POST` | `/sheets/{sheet_id}/undo` | Undo last transaction on spreadsheet mesh |
| `POST` | `/sheets/{sheet_id}/redo` | Redo previously undone transaction |
| `GET` | `/sheets/{sheet_id}/history` | Retrieve revision history and cell edit timeline |

---

## 5. Analytics & DuckDB Engine (`/api/v1/analytics`)

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/jobs` | List background analytics query executions |
| `POST` | `/jobs` | Submit new SQL query execution against analytical data |
| `GET` | `/jobs/{job_id}` | Query execution status, timing, and error logs |
| `POST` | `/jobs/{job_id}/cancel` | Abort running query execution in DuckDB |
| `GET` | `/jobs/{job_id}/result` | Fetch paginated query result rows |
| `GET` | `/jobs/{job_id}/artifact` | Download result dataset as Parquet / CSV |
| `POST` | `/jobs/{job_id}/publish-to-sheet`| Export analytical query results into a spreadsheet sheet |
| `GET` | `/storage` | Inspect tenant analytical parquet storage metrics |
| `GET` | `/operations` | View active DuckDB engine thread pool operations |

---

## 6. Executive Dashboards & Studio (`/api/v1/dashboards`)

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | List all available dashboards in organization |
| `POST` | `/` | Create new dashboard canvas |
| `GET` | `/{dashboard_id}` | Retrieve dashboard layout and widget configurations |
| `PATCH` | `/{dashboard_id}` | Update dashboard metadata or title |
| `DELETE` | `/{dashboard_id}` | Delete dashboard and layout bindings |
| `POST` | `/{dashboard_id}/layout` | Save updated responsive grid layout coordinates |
| `POST` | `/{dashboard_id}/duplicate` | Clone entire dashboard and widget definitions |
| `POST` | `/{dashboard_id}/share` | Update dashboard visibility and sharing permissions |
| `GET` | `/{dashboard_id}/widgets` | List all widgets mounted on dashboard |
| `POST` | `/{dashboard_id}/widgets` | Attach new visualization widget (ECharts/Table/KPI) |
| `PATCH` | `/{dashboard_id}/widgets/{widget_id}` | Update widget queries, chart options, or colors |
| `DELETE` | `/{dashboard_id}/widgets/{widget_id}` | Remove widget from dashboard canvas |
| `POST` | `/{dashboard_id}/widgets/{widget_id}/query` | Execute live backend query feeding widget |
| `GET` | `/{dashboard_id}/export` | Export dashboard report as PDF/Image summary |

---

## 7. Intelligence Reports Engine (`/api/v1/reports`)

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/definitions` | List configured automated report definitions |
| `POST` | `/definitions` | Create new report template definition |
| `GET` | `/definitions/{id}` | Retrieve report template with all sections |
| `PATCH` | `/definitions/{id}` | Update report schedule, orientation, or title |
| `DELETE` | `/definitions/{id}` | Delete report definition |
| `POST` | `/definitions/{id}/sections` | Add new section (markdown, table, chart) to report |
| `PATCH` | `/definitions/{id}/sections/{sec_id}` | Edit report section contents or query binding |
| `DELETE` | `/definitions/{id}/sections/{sec_id}` | Remove section from report template |
| `POST` | `/definitions/{id}/sections/reorder` | Update visual ordering of report sections |
| `GET` | `/definitions/{id}/runs` | List execution history for report |
| `POST` | `/definitions/{id}/runs` | Trigger on-demand generation run |
| `GET` | `/runs/{run_id}` | Check compilation status of report run |
| `POST` | `/runs/{run_id}/download-token` | Issue secure temporary download token |
| `GET` | `/download/{token}` | Stream rendered PDF / HTML report document |

---

## 8. Enterprise File Storage (`/api/v1/files`)

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | List uploaded files with size, type, and upload timestamps |
| `POST` | `/upload` | Multipart file upload with MIME verification and quota check |
| `GET` | `/{file_id}` | Get metadata and status for uploaded file |
| `GET` | `/{file_id}/download` | Stream raw file download with content disposition |
| `GET` | `/{file_id}/preview` | Fetch preview representation (thumbnails, text snippets) |
| `DELETE` | `/{file_id}` | Securely purge file from storage and release quota |
| `GET` | `/quota/usage` | Inspect current file storage consumption vs. plan quota |

---

## 9. Integrations & Google Connectors (`/api/v1/integrations`)

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/google/auth-url` | Generate Google OAuth2 authorization consent URL |
| `POST` | `/google/callback` | Exchange OAuth code for refresh/access tokens |
| `GET` | `/{integration_id}` | Inspect connection status and granted OAuth scopes |
| `DELETE` | `/{integration_id}` | Disconnect integration and revoke stored credentials |
| `GET` | `/{integration_id}/spreadsheets` | List Google Drive spreadsheets accessible by user |
| `GET` | `/{integration_id}/spreadsheets/{id}` | Fetch sheets and metadata from Google Spreadsheet |
| `GET` | `/{integration_id}/spreadsheets/{id}/values` | Read cell matrix values from remote Google Sheet |

---

## 10. Data Sync & Conflict Resolution (`/api/v1/sync`)

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/mappings` | List bi-directional synchronization mappings |
| `POST` | `/mappings` | Configure new sync pipeline between Vibe & external source |
| `GET` | `/mappings/{mapping_id}` | Get configuration details for sync mapping |
| `DELETE` | `/mappings/{mapping_id}` | Delete sync mapping |
| `POST` | `/mappings/{mapping_id}/trigger` | Initiate immediate execution of sync job |
| `GET` | `/mappings/{mapping_id}/runs` | Inspect history of past sync runs and diff stats |
| `GET` | `/mappings/{mapping_id}/conflicts` | List unresolved data divergence conflicts |
| `POST` | `/conflicts/{conflict_id}/resolve` | Apply resolution strategy (`source-wins`, `target-wins`, `manual`) |
| `POST` | `/import` | One-off bulk data import from external source |
| `POST` | `/export` | One-off bulk data export to external target |

---

## 11. Sandboxed Python Scripts (`/api/v1/scripts`)

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/` | List custom data processing scripts |
| `POST` | `/` | Register new Python calculation script |
| `GET` | `/{script_id}` | View script code, entrypoint, and environment config |
| `PATCH` | `/{script_id}` | Edit script code |
| `DELETE` | `/{script_id}` | Remove script |
| `POST` | `/{script_id}/run` | Execute script in isolated container sandbox |
| `GET` | `/{script_id}/runs` | List historical executions and exit statuses |
| `GET` | `/{script_id}/runs/{run_id}` | Fetch stdout, stderr, execution duration, and return values |
| `GET` | `/environment/runtime-policy` | Review resource limits (CPU, RAM, timeout) for scripts |
| `GET` | `/environment/policy` | Review network isolation and system call policies |
| `POST` | `/environment/dependencies` | Request pip package installation for sandbox container |

---

## 12. Backups & Disaster Recovery (`/api/v1/backups`)

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/status` | Active backup subsystem health and scheduler status |
| `GET` | `/history` | Chronological list of database snapshots with sizes & timestamps |
| `GET` | `/verifications` | Results of automated cryptographic restore drill verifications |

---

## 13. System Health & Telemetry (`/api/v1/system`)

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `GET` | `/telemetry` | Comprehensive diagnostics: DB pool stats, Redis latency, memory, uptime |
