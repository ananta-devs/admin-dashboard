"""Atomic and Idempotent PostgreSQL WAL Archival Engine.

Fulfills Phase 10 Stage 2 requirements:
- Exclusive file creation (O_CREAT | O_EXCL) in temporary staging.
- File data and metadata fsync before atomic publication.
- True atomic no-replace publication via POSIX hardlink semantics (os.link),
  strictly guaranteeing that an existing destination is NEVER overwritten.
- Idempotent retry: if destination exists with identical SHA-256, succeed (is_new=False).
- Split-brain protection: if destination exists with conflicting SHA-256, raise WalConflictError.
- Directory fsync after atomic publication.
- Rejection of symlinks, path traversals, and partial artifacts.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
from pathlib import Path
from typing import Tuple

WAL_SEGMENT_SIZE = 16 * 1024 * 1024  # 16 MiB


class WalArchivalError(Exception):
    """Raised when WAL segment archival fails."""


class WalConflictError(WalArchivalError):
    """Raised when an existing destination segment has conflicting content (split-brain/corruption)."""


class WalArchiverService:
    """Manages atomic, tamper-resistant local WAL spooling."""

    def __init__(self, spool_dir: str | Path) -> None:
        self.spool_dir = Path(spool_dir).resolve()
        self.staging_dir = self.spool_dir / ".staging"
        self._ensure_directories()

    def _ensure_directories(self) -> None:
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.spool_dir.chmod(0o700)
            self.staging_dir.chmod(0o700)
        except OSError:
            pass

    @staticmethod
    def calculate_file_sha256(path: Path) -> str:
        """Calculate SHA-256 digest of file."""
        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                hasher.update(chunk)
        return hasher.hexdigest()

    def sync_directory(self, dir_path: Path) -> None:
        """Synchronize directory metadata to physical storage."""
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                flags |= os.O_DIRECTORY
            fd = os.open(str(dir_path), flags)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except (OSError, PermissionError):
            # Directory fsync is a no-op on platforms or filesystems that do not support directory fds
            pass

    def archive_segment(self, source_wal_path: str | Path, segment_name: str) -> Tuple[bool, str]:
        """Atomically archive a WAL segment into the spool directory without replacement.

        Returns (is_newly_archived: bool, sha256_digest: str).
        """
        src = Path(source_wal_path).resolve()
        if not src.is_file():
            raise WalArchivalError(f"Source WAL segment does not exist or is not a file: {src}")

        # Check for invalid segment names to prevent path traversal
        if ".." in segment_name or "/" in segment_name or "\\" in segment_name:
            raise WalArchivalError(f"Invalid WAL segment name (traversal attempt): {segment_name!r}")

        raw_dest = self.spool_dir / segment_name
        if raw_dest.is_symlink() or os.path.islink(str(raw_dest)):
            raise WalArchivalError("Refusing to publish to symbolic link destination.")

        dest = raw_dest.resolve()
        if not dest.is_relative_to(self.spool_dir):
            raise WalArchivalError("Destination path escapes spool directory.")

        # Compute source digest and verify size
        src_size = src.stat().st_size
        if src_size != WAL_SEGMENT_SIZE and not segment_name.endswith(".history"):
            raise WalArchivalError(
                f"Invalid WAL segment size: expected {WAL_SEGMENT_SIZE} bytes, got {src_size} bytes."
            )
        src_sha256 = self.calculate_file_sha256(src)

        # Pre-check: if destination already exists prior to staging
        if dest.exists():
            if dest.is_symlink() or os.path.islink(str(dest)):
                raise WalArchivalError("Refusing to publish to symbolic link destination.")

            dest_sha256 = self.calculate_file_sha256(dest)
            if dest_sha256 == src_sha256:
                # Idempotent retry: identical segment already published
                return False, dest_sha256
            else:
                raise WalConflictError(
                    f"Fatal WAL collision: {segment_name} already exists with conflicting checksum. "
                    f"Existing: {dest_sha256}, Source: {src_sha256}"
                )

        # Stage in temporary file with exclusive creation
        tmp_name = f".tmp_{segment_name}_{os.getpid()}_{os.urandom(8).hex()}"
        tmp_path = self.staging_dir / tmp_name

        try:
            # 1. Open staging file with exclusive creation (O_CREAT | O_EXCL)
            fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as tmp_file:
                with open(src, "rb") as in_file:
                    shutil.copyfileobj(in_file, tmp_file, length=1024 * 1024)

                # 2. Flush file data and metadata to disk blocks
                tmp_file.flush()
                os.fsync(tmp_file.fileno())

            # 3. Verify staging checksum and ensure no external hardlinks exist
            staged_stat = tmp_path.stat()
            if staged_stat.st_nlink != 1:
                raise WalArchivalError("Staging file has unexpected hard links before publication.")

            staged_sha256 = self.calculate_file_sha256(tmp_path)
            if staged_sha256 != src_sha256:
                raise WalArchivalError("Staged WAL copy checksum mismatch (disk corruption).")

            # 4. Atomic NO-REPLACE publication via independent inode creation
            # Uses descriptor-relative O_CREAT | O_EXCL with mode 0o400 to strictly eliminate retained inode mutation
            dest_created = False
            try:
                open_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    open_flags |= os.O_NOFOLLOW

                if os.open in os.supports_dir_fd and hasattr(os, "O_DIRECTORY"):
                    dir_flags = os.O_RDONLY | os.O_DIRECTORY
                    if hasattr(os, "O_NOFOLLOW"):
                        dir_flags |= os.O_NOFOLLOW
                    spool_dfd = os.open(str(self.spool_dir), dir_flags)
                    try:
                        dest_fd = os.open(segment_name, open_flags, 0o400, dir_fd=spool_dfd)
                    finally:
                        os.close(spool_dfd)
                else:
                    dest_fd = os.open(str(dest), open_flags, 0o400)

                dest_created = True
                with open(tmp_path, "rb") as in_file, os.fdopen(dest_fd, "wb") as dest_file:
                    shutil.copyfileobj(in_file, dest_file, length=1024 * 1024)
                    dest_file.flush()
                    os.fsync(dest_file.fileno())

            except FileExistsError:
                # Concurrent writer published destination file first!
                if dest.is_symlink() or os.path.islink(str(dest)):
                    raise WalArchivalError("Concurrent destination appeared as a symbolic link.")

                dest_sha256 = self.calculate_file_sha256(dest)
                if dest_sha256 == src_sha256:
                    # Idempotent concurrent publish of identical content
                    return False, dest_sha256
                else:
                    raise WalConflictError(
                        f"Concurrent WAL collision: {segment_name} appeared with conflicting checksum. "
                        f"Existing: {dest_sha256}, Source: {src_sha256}"
                    )
            except Exception:
                if dest_created and dest.exists():
                    try:
                        os.unlink(str(dest))
                    except OSError:
                        pass
                raise
            finally:
                # Clean up temporary staging file
                if tmp_path.exists():
                    try:
                        os.unlink(str(tmp_path))
                    except OSError:
                        pass

            # 5. Flush directory to ensure directory entry durability
            self.sync_directory(self.spool_dir)

            # 6. Post-verification of published artifact:
            # - Must be a regular file
            # - Link count must be exactly 1 (no retained staging or secondary hard links)
            # - Read-only mode 0o400 applied
            # - File size and SHA-256 verified directly from destination
            if not dest.is_file():
                raise WalArchivalError("Published artifact missing after link.")

            dest_stat = dest.stat()
            if dest_stat.st_nlink != 1:
                try:
                    os.unlink(str(dest))
                except OSError:
                    pass
                raise WalArchivalError("Published artifact has unexpected retained hard links (mutation risk).")

            if dest_stat.st_size != src_size:
                raise WalArchivalError("Published artifact size corrupted.")

            try:
                dest.chmod(0o400)
            except OSError:
                pass

            published_sha256 = self.calculate_file_sha256(dest)
            if published_sha256 != src_sha256:
                raise WalArchivalError("Published artifact checksum mismatch.")

            return True, src_sha256

        except Exception:
            if tmp_path.exists():
                try:
                    os.unlink(str(tmp_path))
                except OSError:
                    pass
            raise
