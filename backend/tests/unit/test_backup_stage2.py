"""Unit tests for Phase 10 Stage 2: PostgreSQL Backup and WAL Archival Engine."""

import os
import tempfile
from pathlib import Path

import pytest

from app.services.backup.logical_runner import LogicalBackupRunner
from app.services.backup.physical_runner import PhysicalBackupRunner
from app.services.backup.spool_manager import SpoolManagerService
from app.services.backup.wal_archiver import (
    WAL_SEGMENT_SIZE,
    WalArchivalError,
    WalArchiverService,
    WalConflictError,
)


def test_wal_archiver_atomic_publication_and_idempotency():
    """Verify WAL segment archival: atomic creation, fsync, and idempotent retry."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        spool_dir = Path(tmp_dir) / "spool"
        src_dir = Path(tmp_dir) / "pg_wal"
        src_dir.mkdir(parents=True)

        archiver = WalArchiverService(spool_dir)

        # 1. Create a simulated 16 MiB WAL segment
        segment_name = "00000001000000000000001A"
        src_file = src_dir / segment_name
        src_data = b"W" * WAL_SEGMENT_SIZE
        src_file.write_bytes(src_data)

        # 2. Archive segment
        is_new, digest = archiver.archive_segment(src_file, segment_name)
        assert is_new is True
        assert (spool_dir / segment_name).is_file()
        assert (spool_dir / segment_name).stat().st_size == WAL_SEGMENT_SIZE

        # 3. Idempotent retry: archive same file again
        is_new_retry, digest_retry = archiver.archive_segment(src_file, segment_name)
        assert is_new_retry is False
        assert digest_retry == digest

        # 4. Fatal conflict: archive different file with same name
        conflicting_src = src_dir / "conflicting_wal"
        conflicting_src.write_bytes(b"C" * WAL_SEGMENT_SIZE)

        with pytest.raises(WalConflictError, match="Fatal WAL collision"):
            archiver.archive_segment(conflicting_src, segment_name)


def test_wal_archiver_path_traversal_rejection():
    """Verify segment names attempting path traversal are rejected."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        archiver = WalArchiverService(tmp_dir)
        src = Path(tmp_dir) / "dummy"
        src.write_bytes(b"X" * WAL_SEGMENT_SIZE)

        with pytest.raises(WalArchivalError, match="Invalid WAL segment name"):
            archiver.archive_segment(src, "../evil_segment")

        with pytest.raises(WalArchivalError, match="Invalid WAL segment name"):
            archiver.archive_segment(src, "sub/dir/segment")


def test_wal_archiver_invalid_segment_size():
    """Verify partial or corrupted segments are rejected."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        archiver = WalArchiverService(tmp_dir)
        src = Path(tmp_dir) / "partial"
        src.write_bytes(b"Too short")

        with pytest.raises(WalArchivalError, match="Invalid WAL segment size"):
            archiver.archive_segment(src, "00000001000000000000001B")


def test_spool_manager_watermarks_and_exhaustion():
    """Verify spool capacity monitoring and multi-tier watermarks."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        spool_dir = Path(tmp_dir)
        # 100 MiB capacity for test
        capacity = 100 * 1024 * 1024
        mgr = SpoolManagerService(spool_dir, capacity_bytes=capacity)

        # Baseline: empty spool
        metrics = mgr.get_spool_metrics()
        assert metrics["used_bytes"] == 0
        assert metrics["watermark_level"] == "NORMAL"
        assert metrics["usage_ratio"] == 0.0

        # Simulate 85 MiB used (85% - High Watermark)
        (spool_dir / "seg1").write_bytes(b"0" * int(85 * 1024 * 1024))
        metrics_high = mgr.get_spool_metrics()
        assert metrics_high["watermark_level"] == "HIGH"
        assert "Elevate upload worker concurrency" in metrics_high["action_required"]

        # Simulate 92 MiB used (92% - Critical Watermark)
        (spool_dir / "seg2").write_bytes(b"0" * int(7 * 1024 * 1024))
        metrics_crit = mgr.get_spool_metrics()
        assert metrics_crit["watermark_level"] == "CRITICAL"
        assert "Pause asynchronous ETL" in metrics_crit["action_required"]

        # Simulate 96 MiB used (96% - Emergency Watermark)
        (spool_dir / "seg3").write_bytes(b"0" * int(4 * 1024 * 1024))
        metrics_emerg = mgr.get_spool_metrics()
        assert metrics_emerg["watermark_level"] == "EMERGENCY"
        assert "CRITICAL" in metrics_emerg["action_required"]


def test_physical_runner_command_builder():
    """Verify pg_basebackup uses -D/--pgdata and never unsupported options."""
    runner = PhysicalBackupRunner(
        pg_host="db.internal",
        pg_port=5432,
        pg_user="replicator",
        max_rate="100M",
    )
    cmd = runner.build_basebackup_command(
        target_dir="/staging/backup_01",
        tablespace_mappings=[("/old/analytics", "/staging/ts_analytics")],
    )

    assert "pg_basebackup" in cmd
    assert "-D" in cmd
    assert "/staging/backup_01" in cmd
    # Ensure unsupported --target-dir is NOT present
    assert "--target-dir" not in cmd
    assert "-Fp" in cmd
    assert "-X" in cmd and "stream" in cmd
    assert "-T" in cmd and "/old/analytics=/staging/ts_analytics" in cmd


def test_logical_runner_command_builder_and_sanitization():
    """Verify pg_dump and pg_dumpall options, and SQL globals sanitization."""
    runner = LogicalBackupRunner(
        pg_host="db.internal",
        pg_port=5432,
        pg_user="vibe_backup_dump",
        pg_database="vibe_enterprise",
    )
    dump_cmd = runner.build_dump_command(output_file="/dev/stdout")
    assert "pg_dump" in dump_cmd
    assert "-Fc" in dump_cmd
    assert "-Z" in dump_cmd and "0" in dump_cmd
    assert "--lock-wait-timeout=10000" in dump_cmd
    assert "--no-owner" in dump_cmd
    assert "--no-privileges" in dump_cmd

    globals_cmd = runner.build_globals_command(output_file="/dev/stdout")
    assert "pg_dumpall" in globals_cmd
    assert "--globals-only" in globals_cmd
    assert "--no-role-passwords" in globals_cmd

    # Test globals sanitization: strips SUPERUSER and session_preload_libraries
    unsanitized_sql = (
        "-- Roles export\n"
        "CREATE ROLE attacker WITH SUPERUSER LOGIN;\n"
        "ALTER ROLE admin SET session_preload_libraries = 'malicious_lib';\n"
        "CREATE ROLE vibe_viewer WITH NOSUPERUSER LOGIN;\n"
    )
    sanitized = runner.sanitize_globals_sql(unsanitized_sql)
    assert "SUPERUSER" not in sanitized or "NOSUPERUSER" in sanitized
    assert "CREATE ROLE attacker WITH NOSUPERUSER LOGIN;" in sanitized
    assert "session_preload_libraries" not in sanitized
    assert "CREATE ROLE vibe_viewer WITH NOSUPERUSER LOGIN;" in sanitized


def test_globals_sanitization_comprehensive_matrix():
    """Verify globals SQL sanitizer against complex PostgreSQL 16 privilege elevation attacks."""
    runner = LogicalBackupRunner()
    complex_globals_sql = (
        "-- PostgreSQL database cluster dump\n"
        "CREATE ROLE analyst LOGIN INHERIT;\n"
        "CREATE ROLE data_reader NOINHERIT;\n"
        "GRANT data_reader TO analyst;\n"
        "CREATE ROLE admin_user WITH SUPERUSER LOGIN CREATEDB;\n"
        "ALTER ROLE dev_service WITH BYPASSRLS;\n"
        "ALTER ROLE repl_service WITH REPLICATION;\n"
        "ALTER ROLE rogue_worker SET shared_preload_libraries = 'pwn_ext';\n"
        "GRANT \"pg_execute_server_program\" TO \"analyst\";\n"
        "GRANT \"pg_read_server_files\" TO \"analyst\";\n"
        "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT ON TABLES TO data_reader;\n"
    )

    clean_sql = runner.sanitize_globals_sql(complex_globals_sql)

    # 1. Privileged attributes stripped
    assert "SUPERUSER" not in clean_sql or "NOSUPERUSER" in clean_sql
    assert "WITH NOSUPERUSER LOGIN CREATEDB" in clean_sql
    assert "BYPASSRLS" not in clean_sql or "NOBYPASSRLS" in clean_sql
    assert "REPLICATION" not in clean_sql or "NOREPLICATION" in clean_sql

    # 2. Dangerous library preload dropped
    assert "shared_preload_libraries" not in clean_sql

    # 3. Dangerous predefined server role grants dropped
    assert "pg_execute_server_program" not in clean_sql
    assert "pg_read_server_files" not in clean_sql

    # 4. Standard roles, memberships, and default privileges preserved intact
    assert "CREATE ROLE analyst LOGIN INHERIT;" in clean_sql
    assert "CREATE ROLE data_reader NOINHERIT;" in clean_sql
    assert "GRANT data_reader TO analyst;" in clean_sql
    assert "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT ON TABLES TO data_reader;" in clean_sql

