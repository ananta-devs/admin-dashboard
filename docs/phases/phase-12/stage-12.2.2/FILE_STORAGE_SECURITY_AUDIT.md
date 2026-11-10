# Stage 12.2.2 — File Storage Security & Architecture Audit Report

**Project:** VIBE — Enterprise BI, Analytics & Data Intelligence Platform  
**Phase:** 12 — Production Readiness, End-to-End Testing & Deployment Verification  
**Stage:** 12.2.2 — Independent Read-Only Implementation, Security & Data-Integrity Audit  
**Module Audited:** File Storage (`/files`, `backend/app/api/v1/routers/files.py`)  
**Audit Priority:** HIGH-PRIORITY SECURITY AUDIT  
**Audit Date:** November 4, 2026  
**Auditor Mode:** STRICT READ-ONLY  

---

## 1. Executive Summary

This independent security audit evaluates the File Storage implementation against enterprise tenant isolation, path traversal prevention, content validation, quota integrity, and concurrency safety.

**Module Verdict:** **SUBSTANTIALLY IMPLEMENTED BUT BLOCKED BY 4 HIGH-SEVERITY DEFECTS**  
While physical filesystem isolation, path generation, binary magic-byte checks, and composite database constraints are sound, the audit identified four significant security and data-integrity defects that require mandatory remediation prior to production authorization:
1. **DEF-SEC-001 (HIGH):** Time-of-Check to Time-of-Use (TOCTOU) concurrency race condition on tenant quota checks.
2. **DEF-SEC-002 (HIGH):** Unvalidated `content_type` passthrough enabling potential Stored XSS / MIME confusion on the inline `/preview` endpoint.
3. **DEF-SEC-003 (HIGH):** Unhandled physical filesystem artifact abandonment (orphaned files) on database commit failures.
4. **DEF-SEC-004 (HIGH):** Overly permissive RBAC: `upload_file` and `delete_file` require only `organization.read`.

---

## 2. Architecture & File Inventory

- **Model:** `backend/app/models/stored_file.py` (84 lines)
- **Service:** `backend/app/services/file_storage.py` (266 lines)
- **Router:** `backend/app/api/v1/routers/files.py` (202 lines)
- **Repository:** `backend/app/repositories/stored_file.py` (95 lines)
- **Schemas:** `backend/app/schemas/stored_file.py` (38 lines)
- **Migration:** `backend/alembic/versions/481909849343_add_stored_files_table.py` (84 lines)
- **Frontend Page:** `frontend/src/app/files/page.tsx` (567 lines)
- **Integration Test:** `backend/tests/integration/test_files_router.py` (230 lines)

---

## 3. Detailed Security Findings & Defect Analysis

### 3.1 DEF-SEC-001: Quota TOCTOU Concurrency Race Condition (Severity: HIGH)
- **Location:** `backend/app/services/file_storage.py`, lines 138–145
- **Mechanism:**
  ```python
  # 4. Check quota
  current_used, _ = await self.repo.get_organization_usage(organization_id)
  quota_limit = getattr(settings, "STORAGE_MAX_ORG_BYTES", DEFAULT_ORG_QUOTA_BYTES)
  if current_used + size_bytes > quota_limit:
      raise ValidationError("Storage quota exceeded...")
  ```
  `get_organization_usage()` executes a standard `SELECT coalesce(sum(size_bytes), 0) FROM stored_files WHERE organization_id = :org_id;`.
- **Vulnerability:**
  There is no transactional row-level lock (e.g. `SELECT ... FOR UPDATE` on `organizations`) or distributed Redis lock wrapping the quota check and file creation.
  If an organization has 480 MB consumed out of a 500 MB quota, and two concurrent upload requests of 15 MB each execute simultaneously:
  1. Request A reads `current_used = 480 MB` (480 + 15 = 495 <= 500) -> Allowed.
  2. Request B reads `current_used = 480 MB` (480 + 15 = 495 <= 500) -> Allowed.
  3. Both write files and commit records, resulting in 510 MB consumed, bypassing the 500 MB tenant quota limit.
- **Remediation:** Enforce atomic transactional quota locking (e.g., acquire a `FOR UPDATE` lock on the `organizations` table row in the database session during upload processing, or maintain an atomic quota counter table).

---

### 3.2 DEF-SEC-002: Inline Preview MIME Confusion / Stored XSS Risk (Severity: HIGH)
- **Location:** `backend/app/services/file_storage.py`, line 173; `backend/app/api/v1/routers/files.py`, lines 163–184
- **Mechanism:**
  In `upload_bytes()`:
  ```python
  stored_file = StoredFile(
      ...
      content_type=content_type or ALLOWED_EXTENSIONS[ext][0],
      ...
  )
  ```
  The incoming `content_type` parameter (from JSON payload or `Content-Type` header) is stored directly in PostgreSQL without validating whether it belongs to `ALLOWED_EXTENSIONS[ext]`.
  In `preview_file()`:
  ```python
  return FileResponse(
      path=str(physical_path),
      media_type=file_meta.content_type,
      headers={
          "Content-Disposition": f'inline; filename="{safe_ascii_name}"',
          "X-Content-Type-Options": "nosniff",
      }
  )
  ```
- **Vulnerability:**
  An attacker can upload a `.csv` or `.txt` file containing HTML/JavaScript (e.g. `<script>alert(document.cookie)</script>`) while passing `content_type: "text/html"`. Because `.csv` and `.txt` have no binary magic bytes, upload validation passes.
  When any authenticated user in that tenant views `/api/v1/files/{file_id}/preview`, FastAPI serves the file with `Content-Type: text/html` and `Content-Disposition: inline`. The browser parses and executes the script within the application's origin context (Stored Cross-Site Scripting).
- **Remediation:**
  1. Enforce that `content_type` MUST be in `ALLOWED_EXTENSIONS[ext]`; discard client-supplied MIME types that do not match the declared extension.
  2. In `preview_file()`, enforce a strict `Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'` response header, or force `text/plain` for all CSV and TXT preview responses.

---

### 3.3 DEF-SEC-003: Physical Filesystem Artifact Abandonment on DB Failure (Severity: HIGH)
- **Location:** `backend/app/services/file_storage.py`, lines 163–182
- **Mechanism:**
  ```python
  # Write to disk
  target_path.write_bytes(content)
  rel_storage_path = f"user_files/{organization_id}/{storage_filename}"

  # 8. Record in database
  stored_file = StoredFile(...)
  async with transaction(self.session):
      await self.repo.create(stored_file)
  ```
- **Vulnerability:**
  Physical bytes are written to disk before the database transaction commits. If a database disconnection, deadlock, or unique constraint violation (e.g., duplicate filename in folder) occurs during `self.repo.create()`, the transaction rolls back, but `target_path` remains written on disk.
  Over time, aborted uploads result in uncontrolled accumulation of unreferenced `.dat` files on the host filesystem that are completely invisible to the database and cannot be cleared by tenant deletion.
- **Remediation:** Wrap the database transaction in `try...except`, and invoke `target_path.unlink(missing_ok=True)` in the exception handler to ensure transactional atomicity across disk and database.

---

### 3.4 DEF-SEC-004: Overly Permissive RBAC on Upload and Deletion (Severity: HIGH)
- **Location:** `backend/app/api/v1/routers/files.py`, lines 71, 190
- **Mechanism:**
  ```python
  @router.post("/upload", dependencies=[Depends(require_permission("organization.read")), ...])
  ...
  @router.delete("/{file_id}", dependencies=[Depends(require_permission("organization.read")), ...])
  ```
- **Vulnerability:**
  `upload_file` and `delete_file` require only the `organization.read` permission.
  Under the VIBE RBAC model, read-only members (e.g. `Viewer` role) possess `organization.read`. Consequently, read-only viewers can upload arbitrary datasets and permanently delete any stored files belonging to the organization.
- **Remediation:** Elevate mutating permissions to `organization.settings.update`, `workspaces.update`, or introduce dedicated permissions `files.upload` / `files.delete`.

---

## 4. Validated Security Strengths

Despite the defects above, several core architectural safeguards were successfully verified:

1. **Path Traversal Prevention:**
   - User filenames are sanitized using `Path(raw_filename).name`.
   - Explicit checks reject filenames containing `..`, `/`, or `\`.
   - Physical storage filenames are generated exclusively by the server as UUIDs: `{uuid}.dat`.
   - Strict directory containment is verified on write and download:
     `target_path.is_relative_to(tenant_dir)`.
2. **Binary Magic-Byte Verification:**
   - Binary files are validated against byte signatures:
     - `.pdf`: `%PDF`
     - `.png`: `\x89PNG\r\n\x1a\n`
     - `.jpg`/`.jpeg`: `\xff\xd8\xff`
     - `.xlsx`: `PK\x03\x04`
     - `.xls`: `\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1`
     - `.parquet`: `PAR1`
   - Uploading an executable disguised as a PDF or image is rejected with `ValidationError`.
3. **Database-Level Multi-Tenant Fencing:**
   - The `stored_files` table enforces composite foreign key:
     `FOREIGN KEY (workspace_id, organization_id) REFERENCES workspaces(id, organization_id) ON DELETE SET NULL`
   - A file in Organization A cannot be attached to a workspace belonging to Organization B.
4. **No External Cloud Dependencies:**
   - Strictly local storage (`./storage/user_files/<org_id>/<file_id>.dat`).
   - No AWS SDK or S3 integration was introduced.
   - Storage directory is not exposed as a public static asset folder.

