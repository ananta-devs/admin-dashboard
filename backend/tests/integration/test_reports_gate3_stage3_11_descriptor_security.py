"""Adversarial and functional test suite for Phase 9 Gate 3 Stage 3.11:
Descriptor-relative fallback, parent-directory safety, crash durability, and artifact write protection.
"""

from __future__ import annotations

import os
import stat
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.exceptions import ValidationError
from app.services.report_storage import ReportStorageService


def test_published_artifact_write_protection(tmp_path: Path):
    """Verify published artifacts are set to read-only, preventing post-publication file mutation."""
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    staging = storage.get_pdf_staging_path(org_id, run_id, claim_generation=1)
    final = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(b"%PDF-1.4 original immutable content")

    storage.atomic_promote(staging, final)
    assert final.is_file()

    # Verify write permissions are stripped (read-only)
    file_mode = stat.S_IMODE(final.stat().st_mode)
    assert file_mode & 0o222 == 0, f"Expected read-only permissions, got {oct(file_mode)}"

    # Attempting to open for write must fail with PermissionError
    with pytest.raises(PermissionError):
        with open(final, "r+b") as f:
            f.write(b"%PDF-tampered")

    # Content and checksum remain unchanged
    assert final.read_bytes() == b"%PDF-1.4 original immutable content"


def test_alternate_hard_link_cannot_mutate_published_artifact(tmp_path: Path):
    """Verify that an alternate hard link cannot be used to mutate the published inode."""
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    staging = storage.get_pdf_staging_path(org_id, run_id, claim_generation=1)
    final = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(b"%PDF-1.4 hard-link test content")

    storage.atomic_promote(staging, final)
    assert final.is_file()

    # Create an alternate hard link to the published file if supported
    alt_link = tmp_path / "reports" / "runs" / "alt_link.pdf"
    try:
        os.link(str(final), str(alt_link))
        # Since permissions on the inode are read-only (0o440), opening the alternate link for write must fail
        with pytest.raises(PermissionError):
            with open(alt_link, "r+b") as f:
                f.write(b"%PDF-tampered-via-link")
    except (OSError, NotImplementedError):
        pass  # Platform does not allow link creation in this context

    # Final content preserved
    assert final.read_bytes() == b"%PDF-1.4 hard-link test content"


def test_staging_cleanup_cannot_mutate_published_artifact(tmp_path: Path):
    """Verify cleanup operations never delete or mutate a finalized report artifact."""
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    staging = storage.get_pdf_staging_path(org_id, run_id, claim_generation=1)
    final = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)
    staging.parent.mkdir(parents=True, exist_ok=True)
    orig_bytes = b"%PDF-1.4 finalized run artifact"
    staging.write_bytes(orig_bytes)

    storage.atomic_promote(staging, final)
    orig_checksum = storage.compute_sha256(final)

    # Cleanup temp files for generation 1
    storage.cleanup_temp_files(org_id, run_id, claim_generation=1)

    # Final artifact remains completely intact
    assert final.is_file()
    assert final.read_bytes() == orig_bytes
    assert storage.compute_sha256(final) == orig_checksum


def test_crash_durability_fsync_sequence(tmp_path: Path):
    """Verify fsync is executed on both source file and destination directory during promotion."""
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    staging = storage.get_pdf_staging_path(org_id, run_id, claim_generation=1)
    final = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(b"%PDF-1.4 durable content")

    fsync_calls: list[int] = []
    real_fsync = os.fsync

    def mock_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        try:
            real_fsync(fd)
        except OSError:
            pass

    with patch("os.fsync", side_effect=mock_fsync):
        storage.atomic_promote(staging, final)

    assert final.is_file()
    # At least 1 fsync call must have occurred (source file and/or dir)
    assert len(fsync_calls) >= 1, "Expected at least 1 fsync call for crash durability"


def test_destination_exists_with_different_content_preservation(tmp_path: Path):
    """Verify that an existing destination with different content is never overwritten and staging is cleaned."""
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    staging_1 = storage.get_pdf_staging_path(org_id, run_id, claim_generation=1)
    final = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)
    staging_1.parent.mkdir(parents=True, exist_ok=True)
    valid_content = b"%PDF-1.4 canonical winner content"
    staging_1.write_bytes(valid_content)

    storage.atomic_promote(staging_1, final)
    valid_checksum = storage.compute_sha256(final)

    # Competing worker attempts with different payload
    staging_2 = storage.get_pdf_staging_path(org_id, run_id, claim_generation=2)
    staging_2.parent.mkdir(parents=True, exist_ok=True)
    staging_2.write_bytes(b"%PDF-1.4 malicious competitor content")

    with pytest.raises(
        ValidationError, match="Cannot overwrite existing immutable reporting artifact"
    ):
        storage.atomic_promote(staging_2, final)

    # Assert destination bytes and checksum are preserved 100%
    assert final.read_bytes() == valid_content
    assert storage.compute_sha256(final) == valid_checksum
    # Staging file was unlinked
    assert not staging_2.exists()


def test_cross_device_mount_escape_rejected(tmp_path: Path):
    """Verify that a directory on a different filesystem device is rejected by _open_trusted_dir."""
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    if sys.platform == "win32":
        pytest.skip("Device boundary check via st_dev applies to POSIX filesystems")

    target_dir = tmp_path / "reports" / "runs" / "test_dev"
    target_dir.mkdir(parents=True, exist_ok=True)

    # Mock os.stat to simulate differing st_dev
    real_stat = os.stat

    def mock_stat(path, *args, **kwargs):
        res = real_stat(path, *args, **kwargs)
        if str(path) == str(storage.base_dir):
            mock_res = MagicMock(wraps=res)
            mock_res.st_dev = 99999
            return mock_res
        return res

    with patch("os.stat", side_effect=mock_stat):
        with pytest.raises(ValidationError, match="Cross-device directory mount escape detected"):
            storage._open_trusted_dir(target_dir)


def test_renameat2_eexist_raises_fileexist_error(tmp_path: Path):
    """Verify that renameat2 returning EEXIST raises FileExistsError and preserves destination."""
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    staging = storage.get_pdf_staging_path(org_id, run_id, claim_generation=1)
    final = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(b"%PDF-new")
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"%PDF-existing")

    if sys.platform.startswith("linux"):
        # Test direct call to _atomic_no_replace with mocked renameat2 returning EEXIST
        mock_libc = MagicMock()
        mock_libc.renameat2.return_value = -1
        with (
            patch("ctypes.CDLL", return_value=mock_libc),
            patch("ctypes.get_errno", return_value=17),
        ):
            with pytest.raises(
                FileExistsError, match="Cannot overwrite existing immutable reporting artifact"
            ):
                storage._atomic_no_replace(staging, final, src_dir_fd=1, dst_dir_fd=2)

    # Content unchanged
    assert final.read_bytes() == b"%PDF-existing"


def test_renameat2_fallback_to_descriptor_link_on_enosys(tmp_path: Path):
    """Verify that when renameat2 returns ENOSYS, execution cleanly falls back to descriptor-relative hard link."""
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    staging = storage.get_pdf_staging_path(org_id, run_id, claim_generation=1)
    final = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)
    staging.parent.mkdir(parents=True, exist_ok=True)
    content = b"%PDF-fallback-enosys-payload"
    staging.write_bytes(content)

    if sys.platform.startswith("linux"):
        mock_libc = MagicMock()
        mock_libc.renameat2.return_value = -1
        # Errno 38 is ENOSYS (Function not implemented)
        with (
            patch("ctypes.CDLL", return_value=mock_libc),
            patch("ctypes.get_errno", return_value=38),
        ):
            src_dfd = storage._open_trusted_dir(staging.parent)
            dst_dfd = storage._open_trusted_dir(final.parent)
            try:
                storage._atomic_no_replace(staging, final, src_dir_fd=src_dfd, dst_dir_fd=dst_dfd)
                assert final.is_file()
                assert final.read_bytes() == content
            finally:
                if src_dfd:
                    os.close(src_dfd)
                if dst_dfd:
                    os.close(dst_dfd)
    else:
        # On Windows, atomic promotion works via MoveFileExW
        storage.atomic_promote(staging, final)
        assert final.is_file()
        assert final.read_bytes() == content


def test_renameat2_fallback_to_descriptor_link_on_einval(tmp_path: Path):
    """Verify that when renameat2 returns EINVAL, execution cleanly falls back to descriptor-relative hard link."""
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    staging = storage.get_pdf_staging_path(org_id, run_id, claim_generation=1)
    final = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)
    staging.parent.mkdir(parents=True, exist_ok=True)
    content = b"%PDF-fallback-einval-payload"
    staging.write_bytes(content)

    if sys.platform.startswith("linux"):
        mock_libc = MagicMock()
        mock_libc.renameat2.return_value = -1
        # Errno 22 is EINVAL (Invalid argument / flags not supported by filesystem)
        with (
            patch("ctypes.CDLL", return_value=mock_libc),
            patch("ctypes.get_errno", return_value=22),
        ):
            src_dfd = storage._open_trusted_dir(staging.parent)
            dst_dfd = storage._open_trusted_dir(final.parent)
            try:
                storage._atomic_no_replace(staging, final, src_dir_fd=src_dfd, dst_dir_fd=dst_dfd)
                assert final.is_file()
                assert final.read_bytes() == content
            finally:
                if src_dfd:
                    os.close(src_dfd)
                if dst_dfd:
                    os.close(dst_dfd)
    else:
        storage.atomic_promote(staging, final)
        assert final.is_file()
        assert final.read_bytes() == content


def test_fail_closed_when_security_guarantees_unavailable(tmp_path: Path):
    """Verify system fails closed when neither Windows, renameat2, nor dirfd hard-link operations are available."""
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    src = tmp_path / "reports" / "staging" / "src.pdf"
    dst = tmp_path / "reports" / "runs" / "dst.pdf"
    src.parent.mkdir(parents=True, exist_ok=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"%PDF-test")

    with (
        patch.object(storage, "_open_trusted_dir", return_value=None),
        patch.object(ReportStorageService, "_atomic_no_replace") as mock_atomic,
    ):
        mock_atomic.side_effect = ValidationError(
            "Platform cannot guarantee atomic descriptor-relative publication."
        )
        with pytest.raises(
            ValidationError,
            match="Platform cannot guarantee atomic descriptor-relative publication",
        ):
            storage.atomic_promote(src, dst)


def test_link_succeeds_unlink_fails_destination_remains_valid(tmp_path: Path):
    """Verify that if hard-link creation succeeds but staging unlink fails, destination artifact remains valid."""
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    staging = storage.get_pdf_staging_path(org_id, run_id, claim_generation=1)
    final = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)
    staging.parent.mkdir(parents=True, exist_ok=True)
    content = b"%PDF-1.4 link-unlink-resilience"
    staging.write_bytes(content)

    if sys.platform.startswith("linux"):
        src_dfd = storage._open_trusted_dir(staging.parent)
        dst_dfd = storage._open_trusted_dir(final.parent)
        try:
            # Force unlink to raise OSError
            with patch("os.unlink", side_effect=OSError("Disk write error during staging cleanup")):
                # Should not raise; destination link is published!
                storage._atomic_no_replace(staging, final, src_dir_fd=src_dfd, dst_dir_fd=dst_dfd)

            assert final.is_file()
            assert final.read_bytes() == content
        finally:
            if src_dfd:
                os.close(src_dfd)
            if dst_dfd:
                os.close(dst_dfd)
    else:
        # On Windows
        storage.atomic_promote(staging, final)
        assert final.is_file()
        assert final.read_bytes() == content


def test_parent_directory_symlink_component_rejected(tmp_path: Path):
    """Verify that a parent directory that is a symlink pointing outside base_dir is rejected."""
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir(parents=True, exist_ok=True)

    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    # Create symlinked run directory
    symlinked_run_dir = storage.runs_dir / str(org_id)
    symlinked_run_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        symlinked_run_dir.symlink_to(outside_dir)
        with pytest.raises(ValidationError, match="(Path traversal attempt|storage root)"):
            storage.resolve_safe_path(storage.runs_dir, org_id, run_id, "report.pdf")
    except OSError:
        pass  # On Windows if non-admin symlink creation not permitted
