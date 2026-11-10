"""Private organization-isolated reporting artifact and snapshot file storage."""

from __future__ import annotations

import ctypes
import hashlib
import os
import stat
import sys
import uuid
from pathlib import Path

from app.analytics.exceptions import DatasetTooLargeError
from app.core.config import settings
from app.core.exceptions import EntityNotFoundError, ValidationError


class ReportStorageService:
    """Manages private file storage for report execution snapshots and PDFs with generation fencing and traversal protection."""

    def __init__(self, base_dir: str | None = None) -> None:
        self.base_dir = Path(base_dir or settings.REPORTS_STORAGE_DIR).resolve()
        self.runs_dir = self.base_dir / "runs"
        self.staging_dir = self.base_dir / "staging"
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    def resolve_safe_path(self, parent_dir: Path, organization_id: uuid.UUID, run_id: uuid.UUID, filename: str) -> Path:
        """Resolve path strictly preventing directory traversal and symlink escapes."""
        safe_filename = Path(filename).name
        if safe_filename != filename or ".." in filename or "/" in filename or "\\" in filename:
            raise ValidationError("Path traversal attempt detected in reporting storage.")

        target_dir = parent_dir / str(organization_id) / str(run_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_dir_res = target_dir.resolve()
        if not target_dir_res.is_relative_to(self.base_dir):
            raise ValidationError("Path traversal attempt detected in reporting storage.")

        target_path = (target_dir_res / safe_filename).resolve()

        # Strict containment check
        if not target_path.is_relative_to(self.base_dir):
            raise ValidationError("Path traversal attempt detected in reporting storage.")

        # Reject external symlinks if file exists
        if target_path.is_symlink() or os.path.islink(str(target_path)):
            resolved_link = target_path.resolve()
            if not resolved_link.is_relative_to(self.base_dir):
                raise ValidationError("Symlink escape attempt detected in reporting storage.")

        return target_path

    def resolve_safe_run_path(
        self, organization_id: uuid.UUID, run_id: uuid.UUID, filename: str
    ) -> Path:
        """Resolve worker-owned run path strictly preventing traversal."""
        return self.resolve_safe_path(self.runs_dir, organization_id, run_id, filename)

    def resolve_safe_staging_path(
        self, organization_id: uuid.UUID, run_id: uuid.UUID, filename: str
    ) -> Path:
        """Resolve renderer-owned staging path strictly preventing traversal."""
        return self.resolve_safe_path(self.staging_dir, organization_id, run_id, filename)

    def get_snapshot_artifact_path(
        self,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        claim_generation: int = 1,
        ext: str = "parquet",
    ) -> Path:
        """Generate attempt-fenced immutable external snapshot path in final run storage."""
        filename = f"snapshot_gen_{claim_generation}.{ext}"
        return self.resolve_safe_run_path(organization_id, run_id, filename)

    def get_staging_artifact_path(
        self,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        claim_generation: int = 1,
        ext: str = "parquet",
    ) -> Path:
        """Generate generation-isolated temporary staging path."""
        filename = f"snapshot_gen_{claim_generation}.{ext}.tmp"
        return self.resolve_safe_staging_path(organization_id, run_id, filename)

    def get_pdf_artifact_path(
        self,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        claim_generation: int = 1,
    ) -> Path:
        """Generate attempt-fenced immutable final PDF path in worker storage."""
        filename = f"report_gen_{claim_generation}.pdf"
        return self.resolve_safe_run_path(organization_id, run_id, filename)

    def get_pdf_staging_path(
        self,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        claim_generation: int = 1,
    ) -> Path:
        """Generate generation-isolated temporary staging path for PDF renderer."""
        filename = f"report_gen_{claim_generation}.pdf.tmp"
        return self.resolve_safe_staging_path(organization_id, run_id, filename)

    def read_and_verify_bytes(
        self,
        path: Path | str,
        expected_checksum: str | None = None,
        max_allowed_bytes: int = settings.REPORT_SNAPSHOT_TOTAL_MAX_BYTES,
    ) -> bytes:
        """Read file bytes using a single open handle with strict traversal, symlink, and checksum validation."""
        p = Path(path).resolve()

        if not p.is_relative_to(self.base_dir):
            raise ValidationError("Path traversal attempt detected during artifact read.")
        if p.is_symlink():
            raise ValidationError("Symlink escape attempt detected during artifact read.")
        if not p.is_file():
            raise EntityNotFoundError("ReportArtifact", str(p))

        # Open single file descriptor with O_NOFOLLOW where supported
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW

        fd = os.open(str(p), flags)
        try:
            stat_res = os.fstat(fd)
            if stat_res.st_size > max_allowed_bytes:
                raise DatasetTooLargeError(
                    f"Artifact {p.name} ({stat_res.st_size} bytes) exceeds maximum allowable limit of {max_allowed_bytes} bytes."
                )

            with open(fd, "rb", closefd=False) as f:
                raw_bytes = f.read(max_allowed_bytes + 1)
                if len(raw_bytes) > max_allowed_bytes:
                    raise DatasetTooLargeError(
                        f"Artifact {p.name} read exceeded maximum limit of {max_allowed_bytes} bytes."
                    )

            if expected_checksum:
                computed = hashlib.sha256(raw_bytes).hexdigest()
                if computed != expected_checksum:
                    raise ValidationError(
                        f"Artifact checksum mismatch for {p.name}. Expected {expected_checksum}, computed {computed}."
                    )

            return raw_bytes
        finally:
            os.close(fd)

    def _open_trusted_dir(self, dir_path: Path) -> int | None:
        """
        Open a directory descriptor with O_DIRECTORY, O_CLOEXEC, and O_NOFOLLOW (where available),
        and strictly verify that it is a directory on the same filesystem within storage root.
        Returns the integer file descriptor, or None on platforms without dirfd support.
        """
        if sys.platform == "win32":
            return None

        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW

        fd = os.open(str(dir_path), flags)
        try:
            st = os.fstat(fd)
            if not stat.S_ISDIR(st.st_mode):
                raise ValidationError(f"Target path {dir_path} is not a directory.")

            # Validate device boundary against base_dir to prevent mount-point escape
            base_st = os.stat(str(self.base_dir))
            if st.st_dev != base_st.st_dev:
                raise ValidationError("Cross-device directory mount escape detected.")

            return fd
        except Exception:
            os.close(fd)
            raise

    @classmethod
    def _atomic_no_replace(
        cls,
        src: Path,
        dst: Path,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        """
        Execute an atomic filesystem operation that succeeds if destination does not exist,
        and fails if destination exists, without any check-then-act race window.
        Uses directory-descriptor pinning on Linux to defeat parent-directory TOCTOU races.
        Falls back strictly to descriptor-relative linkat+unlinkat if renameat2 returns ENOSYS or EINVAL.
        """
        src_str = str(src)
        dst_str = str(dst)

        # 1. Windows: os.rename calls MoveFileExW without MOVEFILE_REPLACE_EXISTING
        if sys.platform == "win32":
            try:
                os.rename(src_str, dst_str)
                return
            except FileExistsError as e:
                raise FileExistsError(f"Cannot overwrite existing immutable reporting artifact: {dst_str}") from e

        # 2. Linux: renameat2(src_dfd, src_name, dst_dfd, dst_name, RENAME_NOREPLACE)
        if sys.platform.startswith("linux"):
            try:
                libc = ctypes.CDLL(None, use_errno=True)
                if hasattr(libc, "renameat2") and src_dir_fd is not None and dst_dir_fd is not None:
                    RENAME_NOREPLACE = 1
                    ret = libc.renameat2(
                        src_dir_fd,
                        src.name.encode("utf-8"),
                        dst_dir_fd,
                        dst.name.encode("utf-8"),
                        ctypes.c_uint(RENAME_NOREPLACE),
                    )
                    if ret == 0:
                        return
                    errno = ctypes.get_errno()
                    if errno in (17, 39):  # EEXIST, ENOTEMPTY
                        raise FileExistsError(f"Cannot overwrite existing immutable reporting artifact: {dst_str}")
                    if errno not in (22, 38):  # Not EINVAL, ENOSYS -> fail with actual errno
                        raise OSError(errno, os.strerror(errno), dst_str)
                    # If EINVAL (22) or ENOSYS (38), fall through to descriptor-relative fallback below
            except (AttributeError, OSError) as e:
                if isinstance(e, FileExistsError):
                    raise

        # 3. Descriptor-relative hard link fallback (linkat + unlinkat)
        if (
            src_dir_fd is not None
            and dst_dir_fd is not None
            and hasattr(os, "supports_dir_fd")
            and os.link in os.supports_dir_fd
        ):
            try:
                os.link(src.name, dst.name, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
            except FileExistsError as e:
                raise FileExistsError(f"Cannot overwrite existing immutable reporting artifact: {dst_str}") from e
            except OSError as e:
                raise OSError(f"Descriptor-relative hard link promotion failed from {src_str} to {dst_str}: {e}") from e

            try:
                os.unlink(src.name, dir_fd=src_dir_fd)
            except OSError:
                # Hard link succeeded: destination inode is published. Suppress staging unlink failure.
                pass
            return

        # 4. Fail closed if platform cannot provide descriptor-relative guarantees
        raise ValidationError("Platform cannot guarantee atomic descriptor-relative publication.")

    def atomic_promote(self, tmp_path: Path, final_path: Path, overwrite: bool = False) -> None:
        """Atomically promote temporary artifact to immutable final path without check-then-act races."""
        if overwrite:
            raise ValidationError("Overwriting finalized reporting artifacts is strictly prohibited.")

        # Reject symlinks directly on un-resolved paths
        if Path(tmp_path).is_symlink() or os.path.islink(str(tmp_path)):
            raise ValidationError("Symlink temporary artifact rejected during promotion.")
        if Path(final_path).is_symlink() or os.path.islink(str(final_path)):
            raise ValidationError("Symlink final destination rejected during promotion.")

        tmp_res = Path(tmp_path).resolve()
        final_res = Path(final_path).resolve()

        if not tmp_res.is_relative_to(self.base_dir):
            raise ValidationError("Temporary artifact path escapes storage root.")
        if not final_res.is_relative_to(self.base_dir):
            raise ValidationError("Final artifact path escapes storage root.")
        if not final_res.parent.resolve().is_relative_to(self.base_dir):
            raise ValidationError("Target directory escapes storage root.")
        if not tmp_res.parent.resolve().is_relative_to(self.base_dir):
            raise ValidationError("Source directory escapes storage root.")
        if tmp_res.is_symlink() or os.path.islink(str(tmp_res)):
            raise ValidationError("Symlink temporary artifact rejected during promotion.")
        if final_res.is_symlink() or os.path.islink(str(final_res)):
            raise ValidationError("Symlink final destination rejected during promotion.")
        if not tmp_res.is_file():
            raise ValidationError(f"Temporary artifact not found for promotion: {tmp_path}")

        # Ensure parent directory exists
        final_res.parent.mkdir(parents=True, exist_ok=True)

        # Crash Durability: flush and fsync source artifact file contents before promotion
        try:
            s_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            if hasattr(os, "O_NOFOLLOW"):
                s_flags |= os.O_NOFOLLOW
            src_file_fd = os.open(str(tmp_res), s_flags)
            try:
                os.fsync(src_file_fd)
            finally:
                os.close(src_file_fd)
        except OSError:
            pass

        # Open pinned directory descriptors where supported
        src_dir_fd: int | None = None
        dst_dir_fd: int | None = None
        try:
            try:
                src_dir_fd = self._open_trusted_dir(tmp_res.parent)
                dst_dir_fd = self._open_trusted_dir(final_res.parent)
            except OSError as e:
                raise ValidationError(f"Failed to open secure directory descriptor: {e}") from e

            try:
                self._atomic_no_replace(tmp_res, final_res, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)
            except FileExistsError as e:
                # Clean up temporary staging file on failed promotion
                try:
                    tmp_res.unlink(missing_ok=True)
                except OSError:
                    pass
                raise ValidationError(f"Cannot overwrite existing immutable reporting artifact: {final_path}") from e
            except OSError as e:
                try:
                    tmp_res.unlink(missing_ok=True)
                except OSError:
                    pass
                raise ValidationError(f"Atomic publication failed from {tmp_path} to {final_path}: {e}") from e

            # Crash Durability: fsync destination parent directory
            if dst_dir_fd is not None:
                try:
                    os.fsync(dst_dir_fd)
                except OSError:
                    pass
            elif sys.platform == "win32":
                try:
                    p_fd = os.open(str(final_res.parent), os.O_RDONLY)
                    try:
                        os.fsync(p_fd)
                    finally:
                        os.close(p_fd)
                except OSError:
                    pass

            # Sync source directory if needed
            if src_dir_fd is not None:
                try:
                    os.fsync(src_dir_fd)
                except OSError:
                    pass

            # Write Protection: enforce read-only permissions on published artifact
            try:
                os.chmod(str(final_res), stat.S_IRUSR | stat.S_IRGRP)
            except OSError:
                pass

        finally:
            if src_dir_fd is not None:
                try:
                    os.close(src_dir_fd)
                except OSError:
                    pass
            if dst_dir_fd is not None:
                try:
                    os.close(dst_dir_fd)
                except OSError:
                    pass

        # Sync parent directory to persist directory entry changes where supported
        try:
            parent_fd = os.open(str(final_res.parent), os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        except (AttributeError, OSError):
            pass

    def compute_sha256(self, path: Path) -> str:
        """Compute SHA-256 checksum of an artifact."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    def cleanup_generation_artifacts(
        self,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        claim_generation: int,
        ext: str = "parquet",
    ) -> None:
        """Safely remove artifacts and temporary files belonging strictly to a specific worker generation."""
        target_run_dir = (self.runs_dir / str(organization_id) / str(run_id)).resolve()
        target_staging_dir = (self.staging_dir / str(organization_id) / str(run_id)).resolve()

        gen_files = [
            target_run_dir / f"snapshot_gen_{claim_generation}.{ext}",
            target_run_dir / f"snapshot_gen_{claim_generation}.json",
            target_run_dir / f"report_gen_{claim_generation}.pdf",
            target_staging_dir / f"snapshot_gen_{claim_generation}.{ext}.tmp",
            target_staging_dir / f"snapshot_gen_{claim_generation}.json.tmp",
            target_staging_dir / f"report_gen_{claim_generation}.pdf.tmp",
            target_run_dir / f"report_gen_{claim_generation}.pdf.tmp",
        ]
        for f in gen_files:
            if f.is_relative_to(self.base_dir) and f.is_file():
                try:
                    if sys.platform == "win32":
                        try:
                            os.chmod(str(f), stat.S_IWRITE | stat.S_IREAD)
                        except OSError:
                            pass
                    f.unlink(missing_ok=True)
                except OSError:
                    pass

    def cleanup_temp_files(
        self,
        organization_id: uuid.UUID,
        run_id: uuid.UUID,
        claim_generation: int,
        ext: str = "parquet",
    ) -> None:
        """Safely remove temporary working files strictly belonging to a specific worker generation without touching published artifacts."""
        target_staging_dir = (self.staging_dir / str(organization_id) / str(run_id)).resolve()
        target_run_dir = (self.runs_dir / str(organization_id) / str(run_id)).resolve()

        staging_files = [
            target_staging_dir / f"snapshot_gen_{claim_generation}.{ext}.tmp",
            target_staging_dir / f"snapshot_gen_{claim_generation}.json.tmp",
            target_staging_dir / f"report_gen_{claim_generation}.pdf.tmp",
            target_run_dir / f"report_gen_{claim_generation}.pdf.tmp",
            target_run_dir / f"snapshot_gen_{claim_generation}.{ext}.tmp",
        ]
        for f in staging_files:
            if f.is_relative_to(self.base_dir) and f.is_file():
                try:
                    f.unlink(missing_ok=True)
                except OSError:
                    pass

    def delete_artifact(self, path: Path | str) -> None:
        """Safely delete an artifact file if it exists."""
        p = Path(path).resolve()
        if p.is_relative_to(self.base_dir) and p.is_file():
            if sys.platform == "win32":
                try:
                    os.chmod(str(p), stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    pass
            p.unlink(missing_ok=True)
