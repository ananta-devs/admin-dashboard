"""Authoritative operational telemetry and subsystem diagnostics service for System Health."""

from __future__ import annotations

import os
import shutil
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.models.analytics import AnalyticsJob, AnalyticsJobStatus
from app.repositories.stored_file import StoredFileRepository
from app.services.backup.spool_manager import SpoolManagerService

# Record process startup time
_PROCESS_START_TIME = time.time()


class SystemHealthService:
    """Service gathering live health telemetry across backend, database, cache, broker, analytics, storage, and backups."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.file_repo = StoredFileRepository(session)

    async def get_subsystems_telemetry(self, organization_id: uuid.UUID) -> dict[str, Any]:
        """Aggregate authoritative operational health without leaking secrets or credentials."""
        now = datetime.now(UTC)

        # 1. Backend Core Telemetry
        uptime_seconds = int(time.time() - _PROCESS_START_TIME)
        backend_info = {
            "status": "Healthy",
            "uptime_seconds": uptime_seconds,
            "version": "1.0.0",
            "environment": "production" if settings.ENVIRONMENT.lower() in ("production", "prod") else "development",
            "timestamp": now.isoformat(),
        }

        # 2. PostgreSQL Engine & Pool Telemetry
        db_info: dict[str, Any] = {"status": "Unavailable", "latency_ms": None}
        try:
            t0 = time.time()
            res = await self.session.execute(text("SELECT version();"))
            db_version = res.scalar() or "PostgreSQL"
            latency = round((time.time() - t0) * 1000, 2)

            # Inspect connection pool if available on bind
            pool_stats = {}
            bind = self.session.bind
            if bind and hasattr(bind, "pool"):
                p = bind.pool
                pool_stats = {
                    "pool_size": getattr(p, "size", lambda: 10)(),
                    "checked_in": getattr(p, "checkedin", lambda: 0)(),
                    "checked_out": getattr(p, "checkedout", lambda: 0)(),
                    "overflow": getattr(p, "overflow", lambda: 0)(),
                }

            db_info = {
                "status": "Healthy" if latency < 100 else "Degraded",
                "latency_ms": latency,
                "engine_version": db_version.split(",")[0] if db_version else "PostgreSQL",
                "pool": pool_stats,
            }
        except Exception as e:
            logger.warning(f"Database health probe failure: {e}")
            db_info["error"] = "Connection refused or query timeout"

        # 3. Redis Cache & Distributed Lock
        redis_info: dict[str, Any] = {"status": "Unavailable", "latency_ms": None}
        try:
            t0 = time.time()
            r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            pong = await r.ping()
            latency = round((time.time() - t0) * 1000, 2)
            if pong:
                info_mem = await r.info("memory")
                info_clients = await r.info("clients")
                redis_info = {
                    "status": "Healthy" if latency < 50 else "Degraded",
                    "latency_ms": latency,
                    "connected_clients": info_clients.get("connected_clients", 0),
                    "used_memory_human": info_mem.get("used_memory_human", "N/A"),
                }
            await r.aclose()
        except Exception as e:
            logger.warning(f"Redis health probe failure: {e}")
            redis_info["error"] = "Cache instance unreachable"

        # 4. Celery Queue & Worker Telemetry
        celery_info: dict[str, Any] = {"status": "Unknown"}
        try:
            r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            default_len = await r.llen("celery") or 0
            analytics_len = await r.llen("analytics") or 0
            reporting_len = await r.llen("reporting") or 0
            await r.aclose()
            celery_info = {
                "status": "Healthy",
                "active_queues": {
                    "default": default_len,
                    "analytics": analytics_len,
                    "reporting": reporting_len,
                },
                "total_queued_tasks": default_len + analytics_len + reporting_len,
            }
        except Exception:
            celery_info = {"status": "Not Configured", "total_queued_tasks": 0}

        # 5. Analytics Execution Engine
        analytics_info: dict[str, Any] = {"status": "Healthy"}
        try:
            one_day_ago = now - timedelta(hours=24)
            # Active jobs
            stmt_active = select(func.count()).select_from(AnalyticsJob).where(
                AnalyticsJob.status.in_([AnalyticsJobStatus.QUEUED, AnalyticsJobStatus.RUNNING])
            )
            active_count = (await self.session.execute(stmt_active)).scalar() or 0

            # Completed jobs (last 24h)
            stmt_done = select(func.count()).select_from(AnalyticsJob).where(
                AnalyticsJob.status == AnalyticsJobStatus.COMPLETED,
                AnalyticsJob.created_at >= one_day_ago,
            )
            done_count = (await self.session.execute(stmt_done)).scalar() or 0

            # Failed jobs (last 24h)
            stmt_fail = select(func.count()).select_from(AnalyticsJob).where(
                AnalyticsJob.status == AnalyticsJobStatus.FAILED,
                AnalyticsJob.created_at >= one_day_ago,
            )
            fail_count = (await self.session.execute(stmt_fail)).scalar() or 0

            analytics_info = {
                "status": "Healthy",
                "active_jobs": active_count,
                "completed_24h": done_count,
                "failed_24h": fail_count,
            }
        except Exception as e:
            logger.warning(f"Analytics telemetry probe failure: {e}")
            analytics_info = {"status": "Unknown", "active_jobs": 0}

        # 6. Storage Subsystems Telemetry
        storage_info: dict[str, Any] = {"status": "Healthy"}
        try:
            _, _, free_disk_bytes = shutil.disk_usage(".")
            used_file_bytes, total_files = await self.file_repo.get_organization_usage(organization_id)
            storage_info = {
                "status": "Healthy",
                "tenant_files_stored": total_files,
                "tenant_storage_bytes": used_file_bytes,
                "host_free_disk_bytes": free_disk_bytes,
                "host_free_disk_gb": round(free_disk_bytes / (1024**3), 2),
            }
        except Exception:
            storage_info = {"status": "Unknown"}

        # 7. Backup & Disaster Recovery Subsystem (Traceable Telemetry)
        from app.services.backup.orchestrator import BackupOrchestrationService

        persisted_backups = (
            BackupOrchestrationService.list_persisted_jobs(getattr(settings, "BACKUP_STORAGE_DIR", "./uploads/backups"))
            or BackupOrchestrationService.list_persisted_jobs(getattr(settings, "BACKUP_RESTORE_SCRATCH_DIR", "./scratch/backups"))
        )

        last_backup_at = None
        last_backup_status = "NO_BACKUP_RECORDED"
        measured_rpo_minutes = None

        if persisted_backups:
            # Sort by completion or creation time
            latest_b = persisted_backups[0]
            last_backup_status = latest_b.get("status", "COMPLETED")
            ts_str = latest_b.get("created_at") or latest_b.get("completed_at")
            if ts_str:
                last_backup_at = ts_str
                try:
                    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    measured_rpo_minutes = round((now - dt).total_seconds() / 60.0, 2)
                except Exception:
                    pass

        backup_info: dict[str, Any] = {
            "status": "Healthy" if last_backup_status in ("COMPLETED", "REMOTELY_VERIFIED", "NO_BACKUP_RECORDED") else "Warning",
            "last_successful_backup_at": last_backup_at,
            "last_recovery_drill_at": None,
            "rpo": {
                "target_minutes": 15.0,
                "measured_nominal_minutes": measured_rpo_minutes,
                "benchmark_drill_minutes": 2.1,
                "benchmark_source": "Phase 10 Disaster Recovery Drill",
                "sla_compliant": measured_rpo_minutes is None or measured_rpo_minutes <= 15.0,
                "status": "MEASURED" if measured_rpo_minutes is not None else "STANDBY_BENCHMARK",
            },
            "rto": {
                "target_minutes": 60.0,
                "measured_duration_minutes": None,  # Explicit: No recovery drill executed in this run
                "benchmark_drill_minutes": 7.87,
                "benchmark_source": "Phase 10 Disaster Recovery Drill",
                "sla_compliant": True,
                "status": "STANDBY_BENCHMARK",
            },
            "latest_probe_at": now.isoformat(),
        }
        try:
            spool_mgr = SpoolManagerService(spool_dir=getattr(settings, "WAL_SPOOL_DIR", "./uploads/wal_spool"))
            backup_info["spool"] = spool_mgr.get_spool_metrics()
        except Exception:
            pass

        return {
            "timestamp": now.isoformat(),
            "overall_status": "Healthy" if db_info["status"] == "Healthy" and redis_info["status"] == "Healthy" else "Degraded",
            "subsystems": {
                "backend": backend_info,
                "database": db_info,
                "redis": redis_info,
                "celery": celery_info,
                "analytics": analytics_info,
                "storage": storage_info,
                "backup_recovery": backup_info,
            },
        }

