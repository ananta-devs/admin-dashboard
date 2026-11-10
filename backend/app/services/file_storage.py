"""Tenant-isolated File Storage Service enforcing magic-byte validation, atomic quota reservation, traversal defenses, and crash recovery."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    ConflictError,
    EntityNotFoundError,
    ValidationError,
)
from app.core.logging import logger
from app.db.transaction import transaction
from app.models.stored_file import StoredFile
from app.repositories.stored_file import StoredFileRepository
from app.repositories.workspace import WorkspaceRepository
from app.schemas.stored_file import StorageQuotaRead, StoredFileRead

# Security policies
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB
DEFAULT_ORG_QUOTA_BYTES = 500 * 1024 * 1024  # 500 MB

ALLOWED_EXTENSIONS: dict[str, str] = {
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xls": "application/vnd.ms-excel",
    ".json": "application/json",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".parquet": "application/vnd.apache.parquet",
}

DISALLOWED_EXTENSIONS = frozenset({
    ".exe", ".bat", ".cmd", ".sh", ".ps1", ".vbs", ".js", ".mjs",
    ".py", ".pyc", ".dll", ".so", ".bin", ".html", ".htm", ".xhtml",
    ".php", ".jsp", ".asp", ".aspx", ".cgi", ".pl", ".svg", ".xml"
})

# Binary magic byte signatures
MAGIC_BYTES = {
    ".pdf": b"%PDF",
    ".png": b"\x89PNG\r\n\x1a\n",
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".xlsx": b"PK\x03\x04",
    ".xls": b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
    ".parquet": b"PAR1",
}

# Regex to detect active markup/script payloads in text files (CSV, TXT)
ACTIVE_MARKUP_REGEX = re.compile(
    r"(?i)(?:<\s*(?:script|html|svg|iframe|body|head|object|embed|applet|form|input|button|link|meta|a|\?xml|!doctype)|javascript\s*:|data\s*:\s*text\/html|on[a-z]+\s*=)"
)


class FileStorageService:
    """Service governing file upload, download, metadata management, and storage quotas."""

    def __init__(self, session: AsyncSession, base_dir: str | None = None) -> None:
        self.session = session
        self.repo = StoredFileRepository(session)
        self.workspace_repo = WorkspaceRepository(session)
        self.base_dir = Path(
            base_dir or getattr(settings, "USER_FILES_STORAGE_DIR", "./storage/user_files")
        ).resolve()
        self.staging_dir = (self.base_dir / "staging").resolve()
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    def _get_tenant_dir(self, organization_id: uuid.UUID) -> Path:
        tenant_dir = (self.base_dir / str(organization_id)).resolve()
        tenant_dir.mkdir(parents=True, exist_ok=True)
        return tenant_dir

    def _validate_filename_and_extension(self, raw_filename: str) -> tuple[str, str]:
        """Validate filename against path traversal, control characters, and dangerous extensions."""
        if not raw_filename or not raw_filename.strip():
            raise ValidationError("Filename cannot be empty.")

        # Check for control characters, null bytes, CR, LF
        if any(c in raw_filename for c in ("\x00", "\r", "\n", "\t", "\x0b", "\x0c")):
            raise ValidationError("Filename contains invalid or dangerous control characters.")

        if ".." in raw_filename or "/" in raw_filename or "\\" in raw_filename:
            raise ValidationError("Path traversal attempt detected in filename.")

        sanitized = Path(raw_filename).name.strip()
        if not sanitized or sanitized in (".", ".."):
            raise ValidationError("Invalid filename.")

        # Inspect all suffixes (e.g. file.exe.csv or file.html.txt) to prevent double-extension bypasses
        path_obj = Path(sanitized)
        if len(path_obj.suffixes) > 1:
            raise ValidationError(
                f"Multiple file extensions detected in '{sanitized}'. Files with multiple extensions are prohibited for security reasons."
            )
        for s in path_obj.suffixes:
            s_clean = s.lower()
            if s_clean in DISALLOWED_EXTENSIONS:
                raise ValidationError(f"File extension '{s_clean}' is prohibited for security reasons.")

        ext = path_obj.suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ValidationError(
                f"File type '{ext}' is not permitted. Allowed extensions: {sorted(ALLOWED_EXTENSIONS.keys())}"
            )

        return sanitized, ext

    def _detect_and_verify_content(self, content: bytes, declared_ext: str) -> str:
        """Independently determine authoritative MIME type and reject active or mismatched content."""
        # 1. Binary magic-byte validation
        expected_sig = MAGIC_BYTES.get(declared_ext)
        if expected_sig:
            if not content.startswith(expected_sig):
                raise ValidationError(
                    f"File signature mismatch for '{declared_ext}'. Uploaded content does not match declared extension."
                )
            return ALLOWED_EXTENSIONS[declared_ext]

        # 2. Format-specific inspection for text-based formats
        if declared_ext == ".json":
            try:
                decoded_str = content.decode("utf-8")
                json.loads(decoded_str)
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise ValidationError(f"Invalid JSON content: {e}") from e
            return "application/json"

        if declared_ext in (".csv", ".txt"):
            try:
                # Must be decodable as utf-8 or ascii
                text_content = content.decode("utf-8")
            except UnicodeDecodeError as e:
                raise ValidationError(f"File must be valid UTF-8 text for extension '{declared_ext}'.") from e

            # Reject binary executables disguised as text (e.g. PE, ELF)
            if content.startswith(b"MZ") or content.startswith(b"\x7fELF"):
                raise ValidationError("Binary executable content disguised as text is strictly rejected.")

            # Reject active HTML/SVG/Script tags in CSV or TXT to prevent Stored XSS / MIME confusion
            if ACTIVE_MARKUP_REGEX.search(text_content):
                raise ValidationError(
                    f"File contains prohibited active markup or script content incompatible with '{declared_ext}'."
                )

            return "text/csv" if declared_ext == ".csv" else "text/plain"

        # Fallback to configured authoritative mapping
        return ALLOWED_EXTENSIONS[declared_ext]

    async def upload_bytes(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None,
        filename: str,
        content: bytes,
        content_type: str | None = None,  # Client-supplied type is IGNORED for security
        workspace_id: uuid.UUID | None = None,
        folder: str = "/",
    ) -> StoredFileRead:
        """Process, validate, hash, stage, and atomically commit uploaded file with transactional quota enforcement."""
        sanitized_filename, ext = self._validate_filename_and_extension(filename)

        # 1. Validate size boundaries
        size_bytes = len(content)
        if size_bytes <= 0:
            raise ValidationError("Cannot upload an empty file.")

        if size_bytes > MAX_FILE_SIZE_BYTES:
            raise ValidationError(
                f"File size ({size_bytes // (1024*1024)} MB) exceeds maximum permitted limit ({MAX_FILE_SIZE_BYTES // (1024*1024)} MB)."
            )

        # 2. Server-derived authoritative MIME type (never trust client)
        authoritative_mime = self._detect_and_verify_content(content, ext)

        # 3. Verify workspace belongs to organization if provided
        if workspace_id:
            ws = await self.workspace_repo.get_by_id(workspace_id, organization_id)
            if not ws:
                raise ValidationError(f"Workspace '{workspace_id}' does not belong to your organization.")

        # 4. Compute SHA-256
        sha256 = hashlib.sha256(content).hexdigest()

        # 5. Secure Staging: write to temporary staging directory first
        staging_id = uuid.uuid4()
        staging_path = (self.staging_dir / f"{staging_id}.tmp").resolve()
        if not staging_path.is_relative_to(self.staging_dir):
            raise ValidationError("Staging path security violation.")

        try:
            staging_path.write_bytes(content)
        except OSError as e:
            raise ValidationError(f"Failed to stage file to disk: {e}") from e

        # Final target path variables
        file_id = uuid.uuid4()
        tenant_dir = self._get_tenant_dir(organization_id)
        storage_filename = f"{file_id}.dat"
        target_path = (tenant_dir / storage_filename).resolve()

        if not target_path.is_relative_to(tenant_dir):
            staging_path.unlink(missing_ok=True)
            raise ValidationError("Target storage path traversal violation.")

        normalized_folder = "/" + folder.strip("/ ")
        if normalized_folder != "/":
            normalized_folder += "/"

        published = False
        quota_limit = getattr(settings, "STORAGE_MAX_ORG_BYTES", DEFAULT_ORG_QUOTA_BYTES)

        # 6. Atomic Transaction: PostgreSQL Advisory Lock + Quota Validation + Publication + DB Commit
        try:
            async with transaction(self.session):
                # Acquire organization-scoped transactional advisory lock to prevent TOCTOU quota race condition
                await self.session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtext('file_quota_' || :org_id));"),
                    {"org_id": str(organization_id)},
                )

                # Authoritative usage calculation under exclusive lock
                current_used, _ = await self.repo.get_organization_usage(organization_id)
                if current_used + size_bytes > quota_limit:
                    available_mb = max(0, (quota_limit - current_used) // (1024 * 1024))
                    required_mb = (size_bytes + 1024 * 1024 - 1) // (1024 * 1024)
                    raise ValidationError(
                        f"Storage quota exceeded. Available: {available_mb} MB, required: {required_mb} MB."
                    )

                # Check duplicate filename within the same folder
                existing = await self.repo.get_by_filename(sanitized_filename, normalized_folder, organization_id)
                if existing:
                    raise ConflictError(f"File '{sanitized_filename}' already exists in folder '{normalized_folder}'.")

                # Atomically publish from staging to final tenant storage path
                staging_path.replace(target_path)
                published = True

                rel_storage_path = f"user_files/{organization_id}/{storage_filename}"
                stored_file = StoredFile(
                    id=file_id,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    uploaded_by_user_id=user_id,
                    filename=sanitized_filename,
                    content_type=authoritative_mime,
                    size_bytes=size_bytes,
                    storage_path=rel_storage_path,
                    checksum_sha256=sha256,
                    folder=normalized_folder,
                )
                await self.repo.create(stored_file)

        except Exception:
            # Rollback cleanup: remove published file if moved, or remove staging file if still in staging
            if published and target_path.exists():
                try:
                    target_path.unlink(missing_ok=True)
                except OSError as err:
                    logger.warning(f"Could not remove rolled-back target file '{target_path}': {err}")
            elif staging_path.exists():
                try:
                    staging_path.unlink(missing_ok=True)
                except OSError as err:
                    logger.warning(f"Could not remove abandoned staging file '{staging_path}': {err}")
            raise

        logger.info(
            f"File '{sanitized_filename}' ({file_id}) successfully committed in org '{organization_id}'. SHA-256: {sha256[:12]}..."
        )
        return StoredFileRead.model_validate(stored_file)

    async def list_files(
        self,
        organization_id: uuid.UUID,
        folder: str | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[StoredFileRead]:
        """List files with tenant isolation."""
        files = await self.repo.list_by_organization(
            organization_id, folder=folder, search=search, limit=limit, offset=offset
        )
        return [StoredFileRead.model_validate(f) for f in files]

    async def get_file_metadata(
        self, file_id: uuid.UUID, organization_id: uuid.UUID
    ) -> StoredFileRead:
        """Retrieve metadata for a specific file with anti-enumeration protection."""
        entity = await self.repo.get_by_id(file_id, organization_id)
        if not entity:
            raise EntityNotFoundError("StoredFile", str(file_id))
        return StoredFileRead.model_validate(entity)

    async def get_file_for_download(
        self, file_id: uuid.UUID, organization_id: uuid.UUID
    ) -> tuple[Path, StoredFile]:
        """Verify existence and return local physical path strictly verifying containment."""
        entity = await self.repo.get_by_id(file_id, organization_id)
        if not entity:
            raise EntityNotFoundError("StoredFile", str(file_id))

        tenant_dir = self._get_tenant_dir(organization_id)
        storage_filename = f"{entity.id}.dat"
        file_path = (tenant_dir / storage_filename).resolve()

        if not file_path.is_relative_to(tenant_dir) or not file_path.exists():
            raise EntityNotFoundError("StoredFile", str(file_id))

        return file_path, entity

    async def delete_file(
        self, file_id: uuid.UUID, organization_id: uuid.UUID
    ) -> None:
        """Safely delete file record and remove physical artifact."""
        entity = await self.repo.get_by_id(file_id, organization_id)
        if not entity:
            raise EntityNotFoundError("StoredFile", str(file_id))

        tenant_dir = self._get_tenant_dir(organization_id)
        storage_filename = f"{entity.id}.dat"
        file_path = (tenant_dir / storage_filename).resolve()

        async with transaction(self.session):
            await self.repo.delete(entity)

        # Delete physical file
        if file_path.exists() and file_path.is_relative_to(tenant_dir):
            try:
                file_path.unlink()
            except OSError as e:
                logger.warning(f"Could not remove physical file '{file_path}': {e}")

        logger.info(f"File '{file_id}' ({entity.filename}) safely deleted from org '{organization_id}'.")

    async def get_quota_usage(self, organization_id: uuid.UUID) -> StorageQuotaRead:
        """Return authoritative quota usage for the active organization."""
        used_bytes, file_count = await self.repo.get_organization_usage(organization_id)
        quota_bytes = getattr(settings, "STORAGE_MAX_ORG_BYTES", DEFAULT_ORG_QUOTA_BYTES)
        available = max(0, quota_bytes - used_bytes)
        pct = round((used_bytes / quota_bytes) * 100, 2) if quota_bytes > 0 else 0.0

        return StorageQuotaRead(
            organization_id=organization_id,
            usage_bytes=used_bytes,
            quota_bytes=quota_bytes,
            available_bytes=available,
            file_count=file_count,
            utilization_percent=pct,
        )
