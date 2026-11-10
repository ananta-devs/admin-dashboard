# Database Records Wipe Verification Report

## 1. Summary of Operation
Per user request, all records across the **`vibe_enterprise`** PostgreSQL database have been cleared while retaining the entire schema table structure.

---

## 2. Safety Backup Completed
Before executing the truncation, a complete data dump of all 5,687,179 records was exported to:
- **Backup File:** `D:\vibe\backups\wal_archive\vibe_enterprise_data_backup.sql`
- **File Size:** ~3.35 GB
- **Timestamp:** 2026-11-05 18:20:19

---

## 3. Schema & Record Status
- **Total Tables in Public Schema:** **43 tables** (100% retained)
- **Truncated Tables (41 tables):**
  - `administrative_audit_events`
  - `analytics_jobs`
  - `analytics_outbox_events`
  - `analytics_quota_reservations`
  - `analytics_results`
  - `authentication_action_tokens`
  - `authentication_sessions`
  - `dashboard_widgets`
  - `dashboards`
  - `external_integrations`
  - `membership_roles`
  - `mfa_credentials`
  - `mfa_login_challenges`
  - `mfa_recovery_codes`
  - `oauth_states`
  - `organization_invitations`
  - `organization_memberships`
  - `organization_roles`
  - `organizations`
  - `persistence_test_ledger`
  - `python_script_runs`
  - `python_scripts`
  - `report_definitions`
  - `report_download_tokens`
  - `report_outbox_events`
  - `report_runs`
  - `report_sections`
  - `role_permissions`
  - `sheet_cell_dependencies`
  - `sheet_cell_history`
  - `sheet_columns`
  - `sheet_rows`
  - `sheets`
  - `spreadsheet_sync_conflicts`
  - `spreadsheet_sync_mappings`
  - `spreadsheet_sync_runs`
  - `stored_files`
  - `user_credentials`
  - `users`
  - `workbooks`
  - `workspaces`
- **System Definition Tables Retained:**
  - `alembic_version` (1 row — tracks migration state)
  - `permissions` (46 system permission codes required for RBAC bootstrap on registration)

---

## 4. Test Suite Validation
- **Vitest Suite**: 18/18 test suites passing (145 tests).
