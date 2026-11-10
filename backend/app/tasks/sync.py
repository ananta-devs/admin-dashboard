"""Celery background tasks for spreadsheet synchronization."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from celery.utils.log import get_task_logger  # type: ignore[import-untyped]

from app.db.session import async_session_maker
from app.services.sync import SpreadsheetSyncService
from app.tasks.celery_app import celery_app

logger = get_task_logger(__name__)


@celery_app.task(
    name="sync.execute_mapping",
    bind=True,
    acks_late=True,
    max_retries=1,
)
def execute_spreadsheet_sync(
    self: Any,
    mapping_id_str: str,
    org_id_str: str,
    user_id_str: str | None = None,
    trigger_type: str = "scheduled",
) -> dict[str, Any]:
    """Execute spreadsheet synchronization in background worker process."""
    mapping_id = uuid.UUID(mapping_id_str)
    org_id = uuid.UUID(org_id_str)
    user_id = uuid.UUID(user_id_str) if user_id_str else None

    async def _run() -> dict[str, Any]:
        async with async_session_maker() as session:
            service = SpreadsheetSyncService(session)
            run = await service.execute_sync(
                mapping_id=mapping_id,
                organization_id=org_id,
                triggered_by_user_id=user_id,
                trigger_type=trigger_type,
            )
            return {
                "run_id": str(run.id),
                "status": run.status,
                "rows_transferred": run.rows_transferred,
                "cells_updated": run.cells_updated,
                "conflict_count": run.conflict_count,
            }

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()
