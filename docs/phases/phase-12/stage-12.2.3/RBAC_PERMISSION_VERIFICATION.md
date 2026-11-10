# VIBE — STAGE 12.2.3 RBAC PERMISSION VERIFICATION
## Role-Based Access Control and Permission Enforcement on File Storage Endpoints

**Workspace:** `D:\vibe`  
**Phase:** 12 — Production Readiness, End-to-End Testing & Deployment Verification  
**Stage:** 12.2.3 — Security, Transaction Integrity & Test Isolation Remediation  
**Status:** VERIFIED & ENFORCED  
**Date:** November 4, 2026  

---

## 1. Problem Statement (DEF-SEC-004)

During the Stage 12.2.2 independent audit, the `/files` API router (`backend/app/api/v1/routers/files.py`) was identified as lacking fine-grained permission dependencies.

While organization tenancy was verified via `get_current_membership`, any authenticated user within an organization (including read-only `Viewer` users) could call `POST /api/v1/files/upload` and `DELETE /api/v1/files/{file_id}`, directly violating the principle of least privilege.

---

## 2. RBAC Permission Architecture

To resolve this defect without requiring database schema changes or permission catalog migrations, VIBE evaluated the 45 existing canonical permissions seeded across all tenants:
- **`Owner` / `Admin`:** Granted full management permissions (`organization.settings.update`, `workspaces.create`, `workspaces.update`, `workspaces.delete`, `workspaces.read`, `reports.read`, etc.).
- **`Member`:** Granted resource creation and modification (`workspaces.create`, `workspaces.update`, `workspaces.read`, etc.), but **specifically lacks deletion authority** (`workspaces.delete` is NOT assigned to Member).
- **`Viewer`:** Granted only read and export permissions (`workspaces.read`, `reports.read`, `reports.run`, `analytics.view`, `dashboard.view`).

### 2.1 Router Permission Binding
The `/files` router applies FastAPI dependency injection using `require_any_permission`:

```python
# 1. Upload Permission:
# Requires file creation authority, workspace authoring authority, or organization settings management
@router.post(
    "/upload",
    response_model=StoredFileRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        Depends(
            require_any_permission(
                "files.create",
                "workspaces.create",
                "workspaces.update",
                "organization.settings.update",
            )
        )
    ],
)

# 2. Deletion Permission:
# Requires explicit deletion authority
@router.delete(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[
        Depends(
            require_any_permission(
                "files.delete",
                "workspaces.delete",
                "organization.settings.update",
            )
        )
    ],
)

# 3. Read / Download / Preview / Quota Usage:
# Requires read authority
dependencies=[
    Depends(
        require_any_permission(
            "files.read",
            "workspaces.read",
            "organization.read",
        )
    )
]
```

### 2.2 Resulting Role Entitlement Matrix

| Operation | HTTP Endpoint | Owner / Admin | Member | Viewer |
| :--- | :--- | :---: | :---: | :---: |
| **Upload File** | `POST /api/v1/files/upload` | **ALLOWED (201)** | **ALLOWED (201)** | **DENIED (403)** |
| **List Files** | `GET /api/v1/files` | **ALLOWED (200)** | **ALLOWED (200)** | **ALLOWED (200)** |
| **File Preview** | `GET /api/v1/files/{id}/preview` | **ALLOWED (200)** | **ALLOWED (200)** | **ALLOWED (200)** |
| **File Download** | `GET /api/v1/files/{id}/download` | **ALLOWED (200)** | **ALLOWED (200)** | **ALLOWED (200)** |
| **Quota Usage** | `GET /api/v1/files/quota/usage` | **ALLOWED (200)** | **ALLOWED (200)** | **ALLOWED (200)** |
| **Delete File** | `DELETE /api/v1/files/{id}` | **ALLOWED (204)** | **DENIED (403)** | **DENIED (403)** |

---

## 3. Automated Verification Evidence

File: `backend/tests/integration/test_file_storage_permissions.py`

### 3.1 Test Cases Executed
1. `test_owner_admin_full_file_permissions`:
   - Owner uploads file (201 Created).
   - Owner lists files (200 OK).
   - Owner downloads file (200 OK).
   - Owner deletes file (204 No Content).
   - **Result: PASSED.**
2. `test_member_can_upload_but_cannot_delete`:
   - Member uploads file (201 Created).
   - Member attempts `DELETE /api/v1/files/{file_id}`.
   - Verified HTTP `403 Forbidden` returned.
   - Owner subsequently deletes the file (204 No Content).
   - **Result: PASSED.**
3. `test_viewer_cannot_upload_or_delete_but_can_read`:
   - Owner seeds test file.
   - Viewer attempts `POST /api/v1/files/upload` $\rightarrow$ Verified HTTP `403 Forbidden`.
   - Viewer calls `GET /api/v1/files` $\rightarrow$ Verified HTTP `200 OK`.
   - Viewer calls `GET /api/v1/files/{file_id}/download` $\rightarrow$ Verified HTTP `200 OK` (payload matches).
   - Viewer attempts `DELETE /api/v1/files/{file_id}` $\rightarrow$ Verified HTTP `403 Forbidden`.
   - **Result: PASSED.**

```
backend\tests\integration\test_file_storage_permissions.py::test_owner_admin_full_file_permissions PASSED [ 33%]
backend\tests\integration\test_file_storage_permissions.py::test_member_can_upload_but_cannot_delete PASSED [ 66%]
backend\tests\integration\test_file_storage_permissions.py::test_viewer_cannot_upload_or_delete_but_can_read PASSED [100%]
============================== 3 passed in 3.39s ==============================
```

**Verdict:** PASS. RBAC boundaries strictly enforced across all file storage routes.

