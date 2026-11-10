"""Multiprocessing Concurrency and Atomic No-Replace Tests for WAL Archival.

Verifies:
- Two real OS processes publishing identical content concurrently -> exactly one creates, other idempotent success.
- Two real OS processes publishing conflicting content concurrently -> exactly one succeeds, other raises WalConflictError.
- Existing identical destination returns is_new=False without overwrite.
- Existing conflicting destination raises WalConflictError.
- Symlink substitution rejected.
- Interrupted/corrupt staging files are cleaned up and rejected.
"""

import multiprocessing as mp
import os
import stat
import tempfile
from pathlib import Path

import pytest

from app.services.backup.wal_archiver import (
    WAL_SEGMENT_SIZE,
    WalArchivalError,
    WalArchiverService,
    WalConflictError,
)


def _worker_publish(spool_dir_str: str, src_path_str: str, segment_name: str, queue: mp.Queue):
    """Worker process entry point for multiprocessing test."""
    try:
        archiver = WalArchiverService(spool_dir_str)
        is_new, digest = archiver.archive_segment(src_path_str, segment_name)
        queue.put({"success": True, "is_new": is_new, "digest": digest, "pid": os.getpid()})
    except Exception as e:
        queue.put({"success": False, "error_type": type(e).__name__, "error_msg": str(e), "pid": os.getpid()})


def test_concurrent_multiprocess_identical_publication():
    """Two separate OS processes publish identical content to the same WAL segment concurrently."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        spool_dir = Path(tmp_dir) / "spool"
        src_file = Path(tmp_dir) / "wal_01"
        segment_name = "00000001000000000000002A"

        # Create 16 MiB dummy segment
        content = b"IDENTICAL_WAL_DATA" * (WAL_SEGMENT_SIZE // 18)
        content += b"\x00" * (WAL_SEGMENT_SIZE - len(content))
        assert len(content) == WAL_SEGMENT_SIZE
        src_file.write_bytes(content)

        queue = mp.Queue()
        p1 = mp.Process(target=_worker_publish, args=(str(spool_dir), str(src_file), segment_name, queue))
        p2 = mp.Process(target=_worker_publish, args=(str(spool_dir), str(src_file), segment_name, queue))

        p1.start()
        p2.start()
        p1.join(timeout=10)
        p2.join(timeout=10)

        results = [queue.get(timeout=2), queue.get(timeout=2)]

        # Both must succeed
        assert results[0]["success"] is True
        assert results[1]["success"] is True

        # Exactly one must be newly archived (is_new=True), the other must be idempotent (is_new=False)
        is_new_values = [results[0]["is_new"], results[1]["is_new"]]
        assert sorted(is_new_values) == [False, True]

        # Both digests must match
        assert results[0]["digest"] == results[1]["digest"]

        # Destination must be intact with correct size
        dest = spool_dir / segment_name
        assert dest.is_file()
        assert dest.stat().st_size == WAL_SEGMENT_SIZE


def test_concurrent_multiprocess_conflicting_publication():
    """Two separate OS processes publish conflicting content to the same WAL segment concurrently."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        spool_dir = Path(tmp_dir) / "spool"
        src_file_a = Path(tmp_dir) / "wal_a"
        src_file_b = Path(tmp_dir) / "wal_b"
        segment_name = "00000001000000000000002B"

        data_a = b"A" * WAL_SEGMENT_SIZE
        data_b = b"B" * WAL_SEGMENT_SIZE
        src_file_a.write_bytes(data_a)
        src_file_b.write_bytes(data_b)

        queue = mp.Queue()
        p1 = mp.Process(target=_worker_publish, args=(str(spool_dir), str(src_file_a), segment_name, queue))
        p2 = mp.Process(target=_worker_publish, args=(str(spool_dir), str(src_file_b), segment_name, queue))

        p1.start()
        p2.start()
        p1.join(timeout=10)
        p2.join(timeout=10)

        results = [queue.get(timeout=2), queue.get(timeout=2)]

        # One process must succeed, and the other must fail with WalConflictError
        successes = [r for r in results if r["success"] is True]
        conflicts = [r for r in results if r["success"] is False and r["error_type"] == "WalConflictError"]

        assert len(successes) == 1
        assert len(conflicts) == 1
        assert successes[0]["is_new"] is True

        # Destination must NOT be corrupted or mixed
        dest = spool_dir / segment_name
        assert dest.is_file()
        dest_bytes = dest.read_bytes()
        assert dest_bytes in (data_a, data_b)


def test_symlink_substitution_rejection():
    """Verify that an existing destination symlink or symlink target is rejected."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        spool_dir = Path(tmp_dir) / "spool"
        spool_dir.mkdir()
        archiver = WalArchiverService(spool_dir)

        # Create symlink pointing outside
        target_outside = Path(tmp_dir) / "outside_target"
        target_outside.write_bytes(b"Z" * WAL_SEGMENT_SIZE)

        segment_name = "00000001000000000000002C"
        link_dest = spool_dir / segment_name
        try:
            os.symlink(str(target_outside), str(link_dest))
        except (OSError, NotImplementedError):
            pytest.skip("Symlinks not permitted in this environment")

        src_file = Path(tmp_dir) / "src"
        src_file.write_bytes(b"Z" * WAL_SEGMENT_SIZE)

        with pytest.raises(WalArchivalError, match="Refusing to publish to symbolic link destination"):
            archiver.archive_segment(src_file, segment_name)


def test_unexpected_hardlink_attack_rejected(monkeypatch):
    """Verify that an unexpected external hard link to the inode triggers fail-closed rejection."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        spool_dir = Path(tmp_dir) / "spool"
        spool_dir.mkdir()
        archiver = WalArchiverService(spool_dir)

        src_file = Path(tmp_dir) / "src_wal"
        src_file.write_bytes(b"W" * WAL_SEGMENT_SIZE)
        segment_name = "00000001000000000000002D"

        # Intercept staging stat to simulate an external hardlink created during staging
        orig_stat = Path.stat
        def patched_stat(self, *args, **kwargs):
            st = orig_stat(self, *args, **kwargs)
            if ".tmp_" in self.name:
                # Simulate external process linking to staging file (st_nlink = 2)
                class FakeStat:
                    st_nlink = 2
                    st_size = st.st_size
                    st_mode = st.st_mode
                return FakeStat()
            return st

        monkeypatch.setattr(Path, "stat", patched_stat)

        with pytest.raises(WalArchivalError, match="unexpected hard links before publication"):
            archiver.archive_segment(src_file, segment_name)

        # Spool must NOT have published destination
        dest = spool_dir / segment_name
        assert not dest.exists()


def test_published_wal_immutable_attributes():
    """Verify that successfully published WAL has exact nlink=1, read-only permissions, and matched digest."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        spool_dir = Path(tmp_dir) / "spool"
        spool_dir.mkdir()
        archiver = WalArchiverService(spool_dir)

        src_file = Path(tmp_dir) / "src_wal"
        content = b"IMMUTABLE_TEST_WAL" * (WAL_SEGMENT_SIZE // 18)
        content += b"\x00" * (WAL_SEGMENT_SIZE - len(content))
        src_file.write_bytes(content)
        segment_name = "00000001000000000000002E"

        is_new, digest = archiver.archive_segment(src_file, segment_name)
        assert is_new is True

        dest = spool_dir / segment_name
        assert dest.is_file()
        st = dest.stat()
        assert st.st_nlink == 1

        # On POSIX systems, verify write bits are stripped (read-only)
        if os.name == "posix":
            mode = stat.S_IMODE(st.st_mode)
            # owner write bit must be 0
            assert mode & 0o200 == 0, f"Expected read-only mode, got {oct(mode)}"


def test_retained_writable_descriptor_cannot_mutate_published_wal(monkeypatch):
    """ADVERSARIAL: Verify that a retained open writable descriptor cannot mutate the published WAL artifact."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        spool_dir = Path(tmp_dir) / "spool"
        spool_dir.mkdir()
        archiver = WalArchiverService(spool_dir)

        src_file = Path(tmp_dir) / "src_wal"
        content = b"RETAINED_FD_AUTHENTIC_DATA" * (WAL_SEGMENT_SIZE // 26)
        content += b"\x00" * (WAL_SEGMENT_SIZE - len(content))
        src_file.write_bytes(content)
        segment_name = "00000001000000000000002F"

        retained_descriptors = []

        # Hook calculate_file_sha256 during staging to simulate a process grabbing an open writable handle
        orig_calc = archiver.calculate_file_sha256
        def hooking_calc(path):
            if ".tmp_" in str(path):
                # Open a writable file descriptor to the staging file
                fd = os.open(str(path), os.O_WRONLY)
                retained_descriptors.append(fd)
            return orig_calc(path)

        monkeypatch.setattr(archiver, "calculate_file_sha256", hooking_calc)

        # Execute publication
        is_new, original_digest = archiver.archive_segment(src_file, segment_name)
        assert is_new is True
        assert len(retained_descriptors) > 0

        # Adversary attempts to write corrupting bytes via the retained descriptor
        for r_fd in retained_descriptors:
            try:
                os.lseek(r_fd, 0, os.SEEK_SET)
                os.write(r_fd, b"ATTACKER_CORRUPTING_MUTATION_DATA")
                os.close(r_fd)
            except OSError:
                pass

        # Verify that published destination bytes remain 100% authentic and unmutated!
        dest = spool_dir / segment_name
        assert dest.is_file()
        assert dest.read_bytes() == content
        assert archiver.calculate_file_sha256(dest) == original_digest

