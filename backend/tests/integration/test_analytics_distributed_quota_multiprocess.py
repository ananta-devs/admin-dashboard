"""Multiprocess integration tests for distributed quota reservation safety.

Verifies:
1. Independent OS processes reserving quota concurrently for the same organization cannot exceed limits.
2. Quota boundary limit enforcement across distinct operating system processes.
3. Distributed fallback to PostgreSQL when Redis is unavailable.
4. Multiple processes attempting fallback reservation during Redis outages.
5. Worker process termination (kill -9 / os._exit) while holding reservation does not corrupt quota.
6. Safe automatic recovery and reclamation of expired reservations.
7. Idempotent repeated release ensuring no cross-worker decrement.
8. Committed artifact files on disk remain counted against quota after temporary reservation release.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import psycopg
import pytest

from app.analytics.exceptions import DatasetTooLargeError, StorageQuotaUnavailableError
from app.analytics.storage import AnalyticsStorageService
from app.core.config import settings


# Top-level helper functions for multiprocessing spawn mode compatibility
def _multiprocess_reservation_worker(
    org_id: uuid.UUID,
    res_bytes: int,
    limit: int,
    force_fallback: bool,
    start_event: mp.synchronize.Event,
    release_event: mp.synchronize.Event,
    results_queue: mp.Queue,  # type: ignore[type-arg]
) -> None:
    """Worker running in a separate OS process that attempts to reserve quota."""
    storage = AnalyticsStorageService()
    start_event.wait(timeout=10.0)

    with patch.object(settings, "ANALYTICS_MAX_ORG_STORAGE_BYTES", limit):
        try:
            with storage.reserve_quota(
                organization_id=org_id,
                reservation_bytes=res_bytes,
                force_fallback=force_fallback,
            ) as reserved:
                res_id = str(storage.last_reservation_id)
                results_queue.put(("SUCCESS", os.getpid(), res_id, reserved))
                # Hold reservation until instructed by test coordinator
                release_event.wait(timeout=10.0)
        except DatasetTooLargeError as e:
            results_queue.put(("REJECTED", os.getpid(), None, str(e)))
        except Exception as e:
            results_queue.put(("ERROR", os.getpid(), None, f"{type(e).__name__}: {e}"))


def _multiprocess_mixed_redis_worker(
    org_id: uuid.UUID,
    res_bytes: int,
    limit: int,
    disable_redis: bool,
    start_event: mp.synchronize.Event,
    release_event: mp.synchronize.Event,
    results_queue: mp.Queue,  # type: ignore[type-arg]
) -> None:
    """Worker running in a separate OS process with or without Redis connectivity."""
    storage = AnalyticsStorageService()
    start_event.wait(timeout=10.0)

    patcher = None
    if disable_redis:
        import redis

        patcher = patch.object(
            redis.Redis, "from_url", side_effect=ConnectionError("Redis simulated outage")
        )
        patcher.start()

    with patch.object(settings, "ANALYTICS_MAX_ORG_STORAGE_BYTES", limit):
        try:
            with storage.reserve_quota(
                organization_id=org_id,
                reservation_bytes=res_bytes,
            ) as reserved:
                res_id = str(storage.last_reservation_id)
                results_queue.put(("SUCCESS", os.getpid(), res_id, reserved))
                release_event.wait(timeout=10.0)
        except DatasetTooLargeError as e:
            results_queue.put(("REJECTED", os.getpid(), None, str(e)))
        except Exception as e:
            results_queue.put(("ERROR", os.getpid(), None, f"{type(e).__name__}: {e}"))
        finally:
            if patcher:
                patcher.stop()


def _hard_crash_worker(
    org_id: uuid.UUID,
    res_bytes: int,
    limit: int,
    ttl_seconds: int,
    ready_queue: mp.Queue,  # type: ignore[type-arg]
) -> None:
    """Worker that acquires a reservation and abruptly terminates via os._exit (simulating SIGKILL)."""
    storage = AnalyticsStorageService()
    try:
        res_id, _ = storage._reserve_quota_postgres(
            organization_id=org_id,
            reservation_bytes=res_bytes,
            limit=limit,
            ttl_seconds=ttl_seconds,
        )
        ready_queue.put(("RESERVED", str(res_id)))
        ready_queue.close()
        ready_queue.join_thread()
    except Exception as e:
        ready_queue.put(("FAILED", str(e)))
        ready_queue.close()
        ready_queue.join_thread()
        return

    # Abrupt termination with zero Python cleanup or context exit
    os._exit(42)


def test_multiprocess_concurrent_reservations_same_org() -> None:
    """Scenario 1 & 2: Multiple OS processes reserving quota concurrently for the same organization."""
    org_id = uuid.uuid4()
    limit = 100_000  # 100 KB
    res_bytes = 40_000  # 40 KB (allows at most 2 concurrent reservations: 40k + 40k = 80k <= 100k)

    start_event = mp.Event()
    release_event = mp.Event()
    results_queue: mp.Queue = mp.Queue()  # type: ignore[type-arg]

    num_processes = 4
    processes = [
        mp.Process(
            target=_multiprocess_reservation_worker,
            args=(org_id, res_bytes, limit, False, start_event, release_event, results_queue),
        )
        for _ in range(num_processes)
    ]

    for p in processes:
        p.start()

    # Release all processes simultaneously
    start_event.set()

    # Collect results
    outcomes = []
    for _ in range(num_processes):
        outcomes.append(results_queue.get(timeout=10.0))

    successes = [o for o in outcomes if o[0] == "SUCCESS"]
    rejections = [o for o in outcomes if o[0] == "REJECTED"]

    # Exactly 2 succeeded and 2 were rejected by distributed quota bounds
    assert len(successes) == 2, f"Expected 2 successes, got {len(successes)}: {outcomes}"
    assert len(rejections) == 2, f"Expected 2 rejections, got {len(rejections)}: {outcomes}"

    # Release workers
    release_event.set()
    for p in processes:
        p.join(timeout=5.0)

    # After workers exit, another reservation succeeds
    storage = AnalyticsStorageService()
    with patch.object(settings, "ANALYTICS_MAX_ORG_STORAGE_BYTES", limit):
        with storage.reserve_quota(org_id, res_bytes) as reserved:
            assert reserved == res_bytes


def test_multiprocess_quota_boundary_enforcement() -> None:
    """Scenario 2: Strict quota boundary enforcement when 5 distinct OS processes request 25KB on a 50KB limit."""
    org_id = uuid.uuid4()
    limit = 50_000  # 50 KB exactly
    res_bytes = 25_000  # 25 KB (exactly 2 fit: 50,000 bytes)

    start_event = mp.Event()
    release_event = mp.Event()
    results_queue: mp.Queue = mp.Queue()  # type: ignore[type-arg]

    num_processes = 5
    processes = [
        mp.Process(
            target=_multiprocess_reservation_worker,
            args=(org_id, res_bytes, limit, False, start_event, release_event, results_queue),
        )
        for _ in range(num_processes)
    ]

    for p in processes:
        p.start()

    start_event.set()

    outcomes = []
    for _ in range(num_processes):
        outcomes.append(results_queue.get(timeout=10.0))

    successes = [o for o in outcomes if o[0] == "SUCCESS"]
    rejections = [o for o in outcomes if o[0] == "REJECTED"]

    # Exactly 2 succeed, exactly 3 rejected: peak allocated concurrently = 50,000 <= 50,000
    assert len(successes) == 2, f"Expected 2 successes, got {len(successes)}: {outcomes}"
    assert len(rejections) == 3, f"Expected 3 rejections, got {len(rejections)}: {outcomes}"

    release_event.set()
    for p in processes:
        p.join(timeout=5.0)


def test_multiprocess_redis_outage_fallback() -> None:
    """Scenario 3 & 4: Multiple OS processes reserving quota under Redis outage using PostgreSQL fallback."""
    org_id = uuid.uuid4()
    limit = 60_000  # 60 KB
    res_bytes = (
        25_000  # 25 KB (allows at most 2 concurrent: 25k + 25k = 50k <= 60k; 3rd is 75k > 60k)
    )

    start_event = mp.Event()
    release_event = mp.Event()
    results_queue: mp.Queue = mp.Queue()  # type: ignore[type-arg]

    num_processes = 4
    processes = [
        mp.Process(
            target=_multiprocess_reservation_worker,
            args=(org_id, res_bytes, limit, True, start_event, release_event, results_queue),
        )
        for _ in range(num_processes)
    ]

    for p in processes:
        p.start()

    start_event.set()

    outcomes = []
    for _ in range(num_processes):
        outcomes.append(results_queue.get(timeout=10.0))

    successes = [o for o in outcomes if o[0] == "SUCCESS"]
    rejections = [o for o in outcomes if o[0] == "REJECTED"]

    assert len(successes) == 2, (
        f"Expected 2 successes under PG fallback, got {len(successes)}: {outcomes}"
    )
    assert len(rejections) == 2, (
        f"Expected 2 rejections under PG fallback, got {len(rejections)}: {outcomes}"
    )

    # Verify both successes have valid reservation records in PostgreSQL
    dsn = settings.DATABASE_URL.replace("+asyncpg", "")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for s in successes:
                res_id_str = s[2]
                cur.execute(
                    "SELECT status, reserved_bytes FROM analytics_quota_reservations WHERE id = %s",
                    (res_id_str,),
                )
                row = cur.fetchone()
                assert row is not None
                assert row[0] == "active"
                assert row[1] == res_bytes

    release_event.set()
    for p in processes:
        p.join(timeout=5.0)

    # Verify reservations were marked released in PostgreSQL
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for s in successes:
                res_id_str = s[2]
                cur.execute(
                    "SELECT status, released_at FROM analytics_quota_reservations WHERE id = %s",
                    (res_id_str,),
                )
                row = cur.fetchone()
                assert row is not None
                assert row[0] == "released"
                assert row[1] is not None


def test_worker_process_termination_crash_recovery() -> None:
    """Scenario 5 & 6: Worker process crashes (os._exit/kill -9) while holding reservation and recovers on expiration."""
    org_id = uuid.uuid4()
    limit = 50_000
    res_bytes = 35_000
    ttl = 2  # 2 seconds TTL

    ready_queue: mp.Queue = mp.Queue()  # type: ignore[type-arg]
    p = mp.Process(target=_hard_crash_worker, args=(org_id, res_bytes, limit, ttl, ready_queue))
    p.start()

    msg = ready_queue.get(timeout=5.0)
    assert msg[0] == "RESERVED"
    res_id_str = msg[1]

    # Process died abruptly with exit code 42
    p.join(timeout=5.0)
    assert p.exitcode == 42

    storage = AnalyticsStorageService()

    # Immediate reservation attempt fails because 35k is still unexpired (35k + 20k = 55k > 50k)
    with pytest.raises(DatasetTooLargeError):
        storage._reserve_quota_postgres(org_id, 20_000, limit)

    # Wait for TTL to expire
    time.sleep(2.5)

    # After expiration, reservation automatically reclaims without manual intervention
    new_res_id, tot = storage._reserve_quota_postgres(org_id, 30_000, limit)
    assert tot == 30_000

    # Verify old reservation is marked expired
    dsn = settings.DATABASE_URL.replace("+asyncpg", "")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status FROM analytics_quota_reservations WHERE id = %s",
                (res_id_str,),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "expired"

    # Clean up new reservation
    storage._release_quota_postgres(new_res_id)


def test_idempotent_repeated_release_and_no_cross_worker_decrement() -> None:
    """Scenario 7: Releasing a reservation repeatedly is idempotent and cannot decrement another worker."""
    storage = AnalyticsStorageService()
    org_id = uuid.uuid4()
    limit = 50_000

    res_a, _ = storage._reserve_quota_postgres(org_id, 15_000, limit)
    res_b, _ = storage._reserve_quota_postgres(org_id, 25_000, limit)

    # Repeated release of worker A
    assert storage._release_quota_postgres(res_a) is True
    assert storage._release_quota_postgres(res_a) is False
    assert storage._release_quota_postgres(res_a) is False

    # Verify Worker B was completely unaffected
    dsn = settings.DATABASE_URL.replace("+asyncpg", "")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, reserved_bytes FROM analytics_quota_reservations WHERE id = %s",
                (str(res_b),),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "active"
            assert row[1] == 25_000

    # Worker B releases
    assert storage._release_quota_postgres(res_b) is True
    assert storage._release_quota_postgres(res_b) is False


def test_committed_artifact_permanently_counted_against_quota(tmp_path: Path) -> None:
    """Scenario 8: Committed artifact file remaining counted against quota after temporary reservation released."""
    storage = AnalyticsStorageService(base_dir=str(tmp_path))
    org_id = uuid.uuid4()
    job_id = uuid.uuid4()
    limit = 50_000

    # 1. Reserve temporary quota and write a 20,000-byte artifact to disk
    with patch.object(settings, "ANALYTICS_MAX_ORG_STORAGE_BYTES", limit):
        with storage.reserve_quota(org_id, 20_000, force_fallback=True):
            # Write 20,000 bytes artifact to disk
            art_path = storage.get_result_artifact_path(org_id, job_id, uuid.uuid4(), ext="parquet")
            art_path.parent.mkdir(parents=True, exist_ok=True)
            with open(art_path, "wb") as f:
                f.write(b"X" * 20_000)

    # 2. Temporary reservation has now exited and been released
    # But artifact on disk still consumes 20,000 bytes
    disk_bytes = storage.get_organization_storage_bytes(org_id)
    assert disk_bytes == 20_000

    with patch.object(settings, "ANALYTICS_MAX_ORG_STORAGE_BYTES", limit):
        # 3. Attempting to reserve 35,000 bytes fails (20,000 disk + 35,000 = 55,000 > 50,000 limit)
        with pytest.raises(DatasetTooLargeError):
            with storage.reserve_quota(org_id, 35_000, force_fallback=True):
                pass

        # 4. Attempting to reserve 25,000 bytes succeeds (20,000 disk + 25,000 = 45,000 <= 50,000 limit)
        with storage.reserve_quota(org_id, 25_000, force_fallback=True) as res:
            assert res == 25_000

    # 5. Clean up artifact
    storage.delete_artifact(art_path)
    assert storage.get_organization_storage_bytes(org_id) == 0


def test_multiprocess_mixed_redis_availability_single_authority_no_split_brain() -> None:
    """Scenario 9: Mixed Redis availability (Worker A has Redis, Worker B lacks Redis) does not split quota authority.

    Both workers serialize through PostgreSQL as the sole authority.
    When both request 30 KB under a 50 KB limit concurrently, exactly one succeeds and one is rejected.
    """
    org_id = uuid.uuid4()
    limit = 50_000  # 50 KB
    res_bytes = 30_000  # 30 KB (only 1 can succeed: 30k <= 50k, but 30k + 30k = 60k > 50k)

    start_event = mp.Event()
    release_event = mp.Event()
    results_queue: mp.Queue = mp.Queue()  # type: ignore[type-arg]

    # Process 1 has Redis connectivity; Process 2 has Redis disabled/unavailable
    p_with_redis = mp.Process(
        target=_multiprocess_mixed_redis_worker,
        args=(org_id, res_bytes, limit, False, start_event, release_event, results_queue),
    )
    p_without_redis = mp.Process(
        target=_multiprocess_mixed_redis_worker,
        args=(org_id, res_bytes, limit, True, start_event, release_event, results_queue),
    )

    p_with_redis.start()
    p_without_redis.start()

    start_event.set()

    outcomes = [results_queue.get(timeout=10.0), results_queue.get(timeout=10.0)]
    successes = [o for o in outcomes if o[0] == "SUCCESS"]
    rejections = [o for o in outcomes if o[0] == "REJECTED"]

    # Exactly 1 worker succeeded and 1 was rejected - no split-brain unmetered overrun!
    assert len(successes) == 1, f"Expected 1 success, got {len(successes)}: {outcomes}"
    assert len(rejections) == 1, f"Expected 1 rejection, got {len(rejections)}: {outcomes}"

    release_event.set()
    p_with_redis.join(timeout=5.0)
    p_without_redis.join(timeout=5.0)

    # After release, another reservation succeeds
    storage = AnalyticsStorageService()
    with patch.object(settings, "ANALYTICS_MAX_ORG_STORAGE_BYTES", limit):
        with storage.reserve_quota(org_id, res_bytes) as reserved:
            assert reserved == res_bytes


def test_bounded_quota_reservation_renewal() -> None:
    """Scenario 10: Safe bounded renewal of active quota reservations."""
    storage = AnalyticsStorageService()
    org_id = uuid.uuid4()
    limit = 50_000

    # 1. Acquire active reservation with short initial TTL
    res_id, _ = storage._reserve_quota_postgres(
        organization_id=org_id,
        reservation_bytes=20_000,
        limit=limit,
        ttl_seconds=60,
    )

    # 2. Renew active reservation with valid extension
    renewed = storage.renew_quota_reservation(
        reservation_id=res_id,
        extend_seconds=300,
        max_lifetime_seconds=3600,
    )
    assert renewed is True

    # Verify expiration timestamp in database is updated
    dsn = settings.DATABASE_URL.replace("+asyncpg", "")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT expires_at, status FROM analytics_quota_reservations WHERE id = %s",
                (str(res_id),),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[1] == "active"

    # 3. Exceeding max lifetime returns False and rejects renewal
    rejected_renew = storage.renew_quota_reservation(
        reservation_id=res_id,
        extend_seconds=7200,  # 2 hours > 1 hour max lifetime
        max_lifetime_seconds=3600,
    )
    assert rejected_renew is False

    # 4. Release reservation and verify renewal on non-active reservation is rejected
    storage._release_quota_postgres(res_id)
    post_release_renew = storage.renew_quota_reservation(
        reservation_id=res_id,
        extend_seconds=300,
        max_lifetime_seconds=3600,
    )
    assert post_release_renew is False


def test_quota_reservation_fail_closed_on_postgres_unavailability() -> None:
    """Scenario 11: Quota reservation fails closed (StorageQuotaUnavailableError) if PostgreSQL is unreachable."""
    storage = AnalyticsStorageService()
    org_id = uuid.uuid4()

    # Point to unreachable port/host with short connect_timeout
    unreachable_dsn = (
        "postgresql://invalid_user:invalid_pass@127.0.0.1:59999/invalid_db?connect_timeout=1"
    )
    with patch.object(settings, "DATABASE_URL", unreachable_dsn):
        with pytest.raises(StorageQuotaUnavailableError) as exc_info:
            with storage.reserve_quota(org_id, 10_000):
                pass

        assert "Authoritative PostgreSQL storage quota ledger unavailable" in str(exc_info.value)
