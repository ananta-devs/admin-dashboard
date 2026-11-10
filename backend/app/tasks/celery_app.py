"""Celery application configuration for VIBE analytics background jobs."""

from __future__ import annotations

from celery import Celery  # type: ignore[import-untyped]

from app.core.config import settings

celery_app = Celery("vibe_analytics")

celery_app.conf.update(
    broker_url=settings.CELERY_BROKER_URL,
    result_backend=None,  # Disabled - PostgreSQL is sole authoritative store
    task_default_queue=settings.CELERY_TASK_DEFAULT_QUEUE,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    task_soft_time_limit=settings.ANALYTICS_JOB_TIMEOUT_SECONDS,
    task_time_limit=settings.ANALYTICS_JOB_TIMEOUT_SECONDS + 30,
    worker_max_tasks_per_child=50,
    broker_connection_retry_on_startup=True,
    imports=["app.tasks.analytics", "app.tasks.sync", "app.tasks.reporting", "app.tasks.backup"],
    beat_schedule={
        "sync-continuous-wal-every-minute": {
            "task": "app.tasks.backup.sync_continuous_wal",
            "schedule": 60.0,
        },
        "emit-rpo-probe-every-5-minutes": {
            "task": "app.tasks.backup.emit_rpo_probe",
            "schedule": 300.0,
        },
        "execute-daily-physical-backup": {
            "task": "app.tasks.backup.execute_scheduled_physical_backup",
            "schedule": 86400.0,
        },
        "execute-daily-logical-backup": {
            "task": "app.tasks.backup.execute_scheduled_logical_backup",
            "schedule": 86400.0,
        },
    },
)

# Auto-discover task modules
celery_app.autodiscover_tasks(["app.tasks"])
