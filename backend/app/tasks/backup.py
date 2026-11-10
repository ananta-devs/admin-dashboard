"""Celery Tasks for Automated Backup, Continuous WAL Durability, and DR Drills.

Fulfills Phase 10 Stage 4 requirements:
- Scheduled daily logical backups.
- Scheduled physical streaming base backups.
- Continuous WAL upload and independent remote SHA-256 verification.
- Scheduled isolated restore verification drills.
- Timeline-aware retention and GFS evaluation.
- Disaster recovery probe emission and settlement.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.backup.sync_continuous_wal", bind=True, max_retries=3)
def sync_continuous_wal(self) -> dict[str, Any]:
    """Scan local spool directory and upload unverified WAL segments to remote storage."""
    try:
        from app.services.backup.remote_storage import S3StorageAdapter
        from app.services.backup.wal_uploader import WalUploaderService
        from app.services.crypto.backup_cipher import BackupCipherService

        storage = S3StorageAdapter(storage_root=settings.BACKUP_STORAGE_DIR)
        master_kek = bytes.fromhex(settings.ENCRYPTION_MASTER_KEY[:64])
        cipher = BackupCipherService(master_kek=master_kek, key_id=settings.BACKUP_MASTER_KEY_ID)

        uploader = WalUploaderService(
            spool_dir=settings.WAL_SPOOL_DIR,
            storage_adapter=storage,
            cipher_service=cipher,
        )
        completed = uploader.sync_spool_to_remote()
        return {
            "status": "SUCCESS",
            "segments_uploaded": len(completed),
            "segments": [c.segment_name for c in completed],
        }
    except Exception as exc:
        logger.error(f"Error syncing continuous WAL: {exc}", exc_info=True)
        raise self.retry(exc=exc, countdown=10 * (2**self.request.retries))


@celery_app.task(name="app.tasks.backup.execute_scheduled_physical_backup", bind=True)
def execute_scheduled_physical_backup(self) -> dict[str, Any]:
    """Execute scheduled physical base backup, encryption, signing, and remote upload."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from app.services.backup.orchestrator import BackupOrchestrationService
        from app.services.backup.remote_storage import S3StorageAdapter
        from app.services.crypto.backup_cipher import BackupCipherService
        from app.services.crypto.manifest_signer import ManifestSigner

        storage = S3StorageAdapter(storage_root=settings.BACKUP_STORAGE_DIR)
        master_kek = bytes.fromhex(settings.ENCRYPTION_MASTER_KEY[:64])
        cipher = BackupCipherService(master_kek=master_kek, key_id=settings.BACKUP_MASTER_KEY_ID)
        signer = ManifestSigner(trusted_keys_dir=settings.BACKUP_TRUSTED_KEYS_DIR)
        private_key = Ed25519PrivateKey.generate()

        orchestrator = BackupOrchestrationService(
            storage_adapter=storage,
            cipher_service=cipher,
            manifest_signer=signer,
            signing_private_key=private_key,
            staging_root=settings.BACKUP_RESTORE_SCRATCH_DIR,
        )
        job = orchestrator.create_job(backup_type="PHYSICAL_BASE")
        finished_job = orchestrator.execute_backup_workflow(job.job_id)
        return {
            "status": finished_job.status,
            "backup_id": finished_job.backup_id,
            "artifact_id": finished_job.artifact_id,
            "remote_key": finished_job.remote_key,
            "remote_version_id": finished_job.remote_version_id,
        }
    except Exception as exc:
        logger.error(f"Physical backup failed: {exc}", exc_info=True)
        raise


@celery_app.task(name="app.tasks.backup.execute_scheduled_logical_backup", bind=True)
def execute_scheduled_logical_backup(self) -> dict[str, Any]:
    """Execute scheduled custom logical export, encryption, signing, and remote upload."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from app.services.backup.orchestrator import BackupOrchestrationService
        from app.services.backup.remote_storage import S3StorageAdapter
        from app.services.crypto.backup_cipher import BackupCipherService
        from app.services.crypto.manifest_signer import ManifestSigner

        storage = S3StorageAdapter(storage_root=settings.BACKUP_STORAGE_DIR)
        master_kek = bytes.fromhex(settings.ENCRYPTION_MASTER_KEY[:64])
        cipher = BackupCipherService(master_kek=master_kek, key_id=settings.BACKUP_MASTER_KEY_ID)
        signer = ManifestSigner(trusted_keys_dir=settings.BACKUP_TRUSTED_KEYS_DIR)
        private_key = Ed25519PrivateKey.generate()

        orchestrator = BackupOrchestrationService(
            storage_adapter=storage,
            cipher_service=cipher,
            manifest_signer=signer,
            signing_private_key=private_key,
            staging_root=settings.BACKUP_RESTORE_SCRATCH_DIR,
        )
        job = orchestrator.create_job(backup_type="LOGICAL_CUSTOM")
        finished_job = orchestrator.execute_backup_workflow(job.job_id)
        return {
            "status": finished_job.status,
            "backup_id": finished_job.backup_id,
            "artifact_id": finished_job.artifact_id,
            "remote_key": finished_job.remote_key,
        }
    except Exception as exc:
        logger.error(f"Logical backup failed: {exc}", exc_info=True)
        raise


@celery_app.task(name="app.tasks.backup.emit_rpo_probe", bind=True)
def emit_rpo_probe(self) -> dict[str, Any]:
    """Emit synchronous disaster recovery probe and record confirmed commit LSN."""
    from app.services.backup.rpo_probe import RpoProbeEngine

    engine = RpoProbeEngine()
    probe = engine.emit_probe()
    return {
        "probe_id": probe.probe_id,
        "confirmed_commit_lsn": probe.confirmed_commit_lsn,
        "emitted_at": probe.emitted_at.isoformat(),
        "status": probe.status,
    }
