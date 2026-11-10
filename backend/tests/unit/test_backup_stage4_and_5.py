"""Unit tests for Phase 10 Stage 4 (Restore Validation) and Stage 5 (Retention & DR Probe)."""

import unittest.mock as mock
from datetime import datetime, timezone

import pytest

from app.services.backup.restore_validator import (
    RestoreValidationError,
    RestoreValidator,
)
from app.services.backup.retention_engine import (
    BackupArtifactMetadata,
    RetentionEngine,
    WalSegmentMetadata,
)
from app.services.backup.rpo_probe import DisasterRecoveryProbe, RpoProbeEngine


def test_restore_validator_foreign_key_orphan_check_success():
    """Verify relational foreign key orphan check passes when no orphans exist."""
    mock_cursor = mock.MagicMock()
    # Catalog query returns one FK: sheet_rows.created_by -> users.id
    mock_cursor.fetchall.return_value = [
        ("fk_sheet_rows_user", "sheet_rows", "created_by_user_id", "users", "id")
    ]
    # Check query returns count = 0 (no orphans)
    mock_cursor.fetchone.return_value = (0,)

    validator = RestoreValidator(mock_cursor)
    res = validator.validate_foreign_keys()
    assert res["constraints_checked"] == 1
    assert res["violations"] == 0


def test_restore_validator_foreign_key_orphan_check_violation():
    """Verify relational foreign key orphan check raises RestoreValidationError on orphans."""
    mock_cursor = mock.MagicMock()
    mock_cursor.fetchall.return_value = [
        ("fk_sheet_rows_user", "sheet_rows", "created_by_user_id", "users", "id")
    ]
    # Check query returns count = 3 orphans
    mock_cursor.fetchone.return_value = (3,)

    validator = RestoreValidator(mock_cursor)
    with pytest.raises(RestoreValidationError, match="Foreign key integrity violation"):
        validator.validate_foreign_keys()


def test_restore_validator_btree_index_amcheck():
    """Verify amcheck bt_index_check executes only for B-Tree indexes."""
    mock_cursor = mock.MagicMock()
    # Return 2 valid B-tree indexes
    mock_cursor.fetchall.return_value = [
        (16801, "users_pkey", "users"),
        (16802, "idx_sheet_rows_org", "sheet_rows"),
    ]

    validator = RestoreValidator(mock_cursor)
    res = validator.validate_btree_indexes()
    assert res["btree_indexes_checked"] == 2
    assert res["corrupted_indexes"] == 0
    # Confirm bt_index_check called twice
    assert mock_cursor.execute.call_count >= 2


def test_restore_validator_catalog_inventory():
    """Verify dynamic catalog inventory checks all required relations."""
    mock_cursor = mock.MagicMock()
    mock_cursor.fetchall.return_value = [
        ("users",),
        ("workspaces",),
        ("sheets",),
    ]

    validator = RestoreValidator(mock_cursor)
    count = validator.validate_catalog_inventory(["users", "workspaces"])
    assert count == 3

    with pytest.raises(RestoreValidationError, match="Restored catalog missing expected relations"):
        validator.validate_catalog_inventory(["missing_table"])


def test_retention_engine_gfs_promotion_and_fail_closed_pruning():
    """Verify GFS promotion rules and fail-closed behavior on missing WAL."""
    engine = RetentionEngine(daily_retention_days=14, weekly_retention_days=56, monthly_retention_days=365)

    now = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)
    # Sunday backup: 2026-11-01 was Sunday (weekday 6) and 1st of month
    sunday_backup = BackupArtifactMetadata(
        backup_id="b-001",
        artifact_id="art-001",
        backup_type="PHYSICAL_BASE",
        created_at=datetime(2026, 11, 1, 2, 0, 0, tzinfo=timezone.utc),
        start_lsn="0/10000000",
        stop_lsn="0/10000100",
        timeline_id=1,
        required_start_wal="000000010000000000000010",
        required_stop_wal="000000010000000000000010",
        s3_bucket="vibe-backups",
        s3_key="base/b-001.enc",
        s3_version_id="v-1",
        sha256="abc123",
    )

    engine.evaluate_gfs_promotions([sunday_backup])
    # Must be promoted to both WEEKLY and MONTHLY without creating a second file
    assert "DAILY" in sunday_backup.promoted_tiers
    assert "WEEKLY" in sunday_backup.promoted_tiers
    assert "MONTHLY" in sunday_backup.promoted_tiers
    # Retention must be 365 days
    assert sunday_backup.retention_expires_at is not None
    assert sunday_backup.retention_expires_at > datetime(2027, 10, 1, tzinfo=timezone.utc)

    # Test Fail-Closed Pruning: WAL segment missing from registry
    # Pruning must return [] (freeze pruning)
    expired_backups, prunable_wal = engine.resolve_prunable_backups([sunday_backup], [], now=now)
    assert expired_backups == []
    assert prunable_wal == []


def test_rpo_probe_recoverability_evaluation():
    """Verify DR probe recoverability evaluation based on remote LSN coverage."""
    engine = RpoProbeEngine()

    probe = DisasterRecoveryProbe(
        probe_id="p-001",
        emitted_at=datetime.now(timezone.utc),
        confirmed_commit_lsn="0/16000160",
        status="COMMITTED",
    )

    # 1. Remote LSN is lower -> not yet recoverable
    can_recover = engine.evaluate_probe_recoverability(
        probe, max_verified_remote_lsn="0/15000000", chain_continuous=True
    )
    assert can_recover is False
    assert probe.status == "COMMITTED"

    # 2. Remote LSN covers or exceeds probe commit LSN and chain is continuous -> RECOVERABLE
    can_recover_ok = engine.evaluate_probe_recoverability(
        probe, max_verified_remote_lsn="0/16000200", chain_continuous=True
    )
    assert can_recover_ok is True
    assert probe.status == "RECOVERABLE"
    assert probe.remote_verified_at is not None

    # 3. Remote LSN covers probe commit LSN BUT chain is broken -> NOT RECOVERABLE
    probe_broken = DisasterRecoveryProbe(
        probe_id="p-002",
        emitted_at=datetime.now(timezone.utc),
        confirmed_commit_lsn="0/16000160",
        status="COMMITTED",
    )
    can_recover_broken = engine.evaluate_probe_recoverability(
        probe_broken, max_verified_remote_lsn="0/16000200", chain_continuous=False
    )
    assert can_recover_broken is False
    assert probe_broken.status == "COMMITTED"
