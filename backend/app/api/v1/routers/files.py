"""FastAPI router for tenant-isolated File Storage, dataset uploads, downloads, and quota management."""

from __future__ import annotations

import base64
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    get_current_membership,
    require_any_permission,
    verify_csrf_token,
)
from app.db.session import get_db_session
from app.models.membership import OrganizationMembership
from app.schemas.stored_file import StorageQuotaRead, StoredFileRead
from app.services.file_storage import FileStorageService

router = APIRouter(prefix="/files", tags=["files"])


def _sanitize_header_filename(filename: str, fallback: str = "file") -> str:
    """Sanitize filename to prevent HTTP response header injection (CRLF, quotes, control chars)."""
    # Remove control characters, quotes, and newlines
    cleaned = re.sub(r'[\r\n"\x00-\x1f\x7f\\]', "", filename).strip()
    return cleaned if cleaned else fallback


@router.get(
    "",
    response_model=list[StoredFileRead],
    summary="List stored files for active organization",
    dependencies=[Depends(require_any_permission("files.read", "organization.read"))],
)
async def list_files(
    folder: str | None = Query(None, description="Filter by folder path"),
    search: str | None = Query(None, description="Search by filename"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> list[StoredFileRead]:
    """Retrieve metadata for all files strictly scoped within active tenant."""
    service = FileStorageService(session)
    return await service.list_files(
        organization_id=membership.organization_id,
        folder=folder,
        search=search,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/quota/usage",
    response_model=StorageQuotaRead,
    summary="Get storage consumption and quota metrics",
    dependencies=[Depends(require_any_permission("files.read", "organization.read"))],
)
async def get_storage_quota(
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> StorageQuotaRead:
    """Retrieve authoritative storage quota breakdown and utilization percentage."""
    service = FileStorageService(session)
    return await service.get_quota_usage(membership.organization_id)


@router.post(
    "/upload",
    response_model=StoredFileRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new dataset or document",
    dependencies=[
        Depends(
            require_any_permission(
                "files.create",
                "workspaces.create",
                "workspaces.update",
                "organization.settings.update",
            )
        ),
        Depends(verify_csrf_token),
    ],
)
async def upload_file(
    request: Request,
    filename: str | None = Query(None),
    folder: str = Query("/"),
    workspace_id: uuid.UUID | None = Query(None),
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> StoredFileRead:
    """Upload a file via JSON base64 or raw binary with server-derived MIME validation and atomic quota reservation."""
    content_type_header = request.headers.get("content-type", "")

    if "application/json" in content_type_header:
        body = await request.json()
        raw_name = body.get("filename") or filename or "uploaded_file"
        target_folder = body.get("folder") or folder
        ws_id_raw = body.get("workspace_id")
        ws_id = uuid.UUID(ws_id_raw) if ws_id_raw else workspace_id
        content_b64 = body.get("content_base64", "")
        content = base64.b64decode(content_b64)
    else:
        raw_name = request.headers.get("x-filename") or filename or "uploaded_file"
        target_folder = request.headers.get("x-folder") or folder
        ws_id_header = request.headers.get("x-workspace-id")
        ws_id = uuid.UUID(ws_id_header) if ws_id_header else workspace_id
        content = await request.body()

    service = FileStorageService(session)
    return await service.upload_bytes(
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        filename=raw_name,
        content=content,
        workspace_id=ws_id,
        folder=target_folder,
    )


@router.get(
    "/{file_id}",
    response_model=StoredFileRead,
    summary="Get metadata for a specific file",
    dependencies=[Depends(require_any_permission("files.read", "organization.read"))],
)
async def get_file_metadata(
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> StoredFileRead:
    """Retrieve metadata for a specific file with anti-enumeration protection."""
    service = FileStorageService(session)
    return await service.get_file_metadata(file_id, membership.organization_id)


@router.get(
    "/{file_id}/download",
    summary="Download file artifact securely",
    dependencies=[Depends(require_any_permission("files.read", "organization.read"))],
)
async def download_file(
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> FileResponse:
    """Download stored file artifact with safe Content-Disposition attachment header."""
    service = FileStorageService(session)
    physical_path, file_meta = await service.get_file_for_download(
        file_id, membership.organization_id
    )

    safe_name = _sanitize_header_filename(file_meta.filename, fallback="download")
    headers = {
        "Content-Disposition": f'attachment; filename="{safe_name}"',
        "X-Content-Type-Options": "nosniff",
        "ETag": f'"{file_meta.checksum_sha256}"',
    }
    return FileResponse(
        path=str(physical_path),
        media_type=file_meta.content_type,
        headers=headers,
    )


@router.get(
    "/{file_id}/preview",
    summary="Preview safe text, data, or image file",
    dependencies=[Depends(require_any_permission("files.read", "organization.read"))],
)
async def preview_file(
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> FileResponse:
    """Serve file inline for safe preview rendering with restrictive Content-Security-Policy."""
    service = FileStorageService(session)
    physical_path, file_meta = await service.get_file_for_download(
        file_id, membership.organization_id
    )

    safe_name = _sanitize_header_filename(file_meta.filename, fallback="preview")
    ext = Path(file_meta.filename).suffix.lower()

    # Determine safe preview media type: for CSV/TXT/JSON, always render as text/plain to prevent browser HTML parsing
    if ext in (".csv", ".txt", ".json"):
        preview_media_type = "text/plain; charset=utf-8"
    else:
        preview_media_type = file_meta.content_type

    headers = {
        "Content-Disposition": f'inline; filename="{safe_name}"',
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        "X-Frame-Options": "SAMEORIGIN",
        "ETag": f'"{file_meta.checksum_sha256}"',
    }
    return FileResponse(
        path=str(physical_path),
        media_type=preview_media_type,
        headers=headers,
    )


@router.delete(
    "/{file_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a stored file",
    dependencies=[
        Depends(
            require_any_permission(
                "files.delete",
                "workspaces.delete",
                "organization.settings.update",
            )
        ),
        Depends(verify_csrf_token),
    ],
)
async def delete_file(
    file_id: uuid.UUID,
    session: AsyncSession = Depends(get_db_session),
    membership: OrganizationMembership = Depends(get_current_membership),
) -> Response:
    """Permanently delete file metadata and physical artifact. Requires workspace deletion or admin privilege."""
    service = FileStorageService(session)
    await service.delete_file(file_id, membership.organization_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
