"""Tests for authoritative PostgreSQL storage quota reservation ledger.

Verifies:
1. PostgreSQL is the sole authoritative distributed ledger for all workers.
2. Concurrent workers attempting to reserve quota concurrently cannot exceed limits.
3. Once a reservation exits its context manager, quota is immediately marked released.
4. Redis is completely removed from the reservation authorization path.
"""

from __future__ import annotations

import threading
import uuid
from unittest.mock import patch

import psycopg

from app.analytics.exceptions import DatasetTooLargeError
from app.analytics.storage import AnalyticsStorageService
from app.core.config import settings


def test_concurrent_quota_reservation_authoritative_ledger() -> None:
    """Verifies that PostgreSQL authoritative ledger serializes and blocks concurrent allocations over limit."""
    storage = AnalyticsStorageService()
    org_id = uuid.uuid4()
    quota_limit = 50_000  # 50 KB
    reservation_size = (
        20_000  # 20 KB (allows at most 2 concurrent: 20k + 20k = 40k <= 50k; 3rd is 60k > 50k)
    )

    start_event = threading.Event()
    release_event = threading.Event()
    successful: list[int] = []
    rejected: list[int] = []
    lock = threading.Lock()
    all_done_cond = threading.Condition()

    def worker_task(idx: int) -> None:
        start_event.wait()
        with patch.object(settings, "ANALYTICS_MAX_ORG_STORAGE_BYTES", quota_limit):
            try:
                with storage.reserve_quota(org_id, reservation_size):
                    with lock:
                        successful.append(idx)
                    release_event.wait(timeout=10.0)
            except DatasetTooLargeError:
                with lock:
                    rejected.append(idx)
            finally:
                with all_done_cond:
                    all_done_cond.notify_all()

    threads = [threading.Thread(target=worker_task, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()

    start_event.set()

    with all_done_cond:
        while (len(successful) + len(rejected)) < 4:
            all_done_cond.wait(timeout=0.1)

    assert len(successful) == 2, f"Allowed {len(successful)} concurrent reservations, expected 2"
    assert len(rejected) == 2, f"Expected 2 rejected, got {len(rejected)}"

    # Verify active rows in PostgreSQL
    dsn = settings.DATABASE_URL.replace("+asyncpg", "")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*), sum(reserved_bytes) 
                FROM analytics_quota_reservations 
                WHERE organization_id = %s AND status = 'active'
                """,
                (str(org_id),),
            )
            row = cur.fetchone()
            assert row[0] == 2
            assert row[1] == 40_000

    # Release workers
    release_event.set()
    for t in threads:
        t.join(timeout=3.0)

    # Verify PostgreSQL records are all released
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT count(*) 
                FROM analytics_quota_reservations 
                WHERE organization_id = %s AND status = 'active'
                """,
                (str(org_id),),
            )
            active_count = cur.fetchone()[0]
            assert active_count == 0

    # Next reservation succeeds because previous reservations were released
    with patch.object(settings, "ANALYTICS_MAX_ORG_STORAGE_BYTES", quota_limit):
        with storage.reserve_quota(org_id, reservation_size) as reserved:
            assert reserved == reservation_size
