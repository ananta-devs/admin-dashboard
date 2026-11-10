"""Tests verifying thread-safe heartbeat signaling, engine/loop disposal, and GIL independence."""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.tasks.analytics import WorkerHeartbeatThread


@pytest.fixture
async def db_session():
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_heartbeat_thread_disposal_and_loop_closure() -> None:
    """Verifies that WorkerHeartbeatThread cleanly closes its event loop and disposes its engine."""
    job_id = uuid.uuid4()
    claim_token = uuid.uuid4()
    abort_event = threading.Event()
    main_loop = asyncio.get_running_loop()
    asyncio_abort_event = asyncio.Event()

    thread = WorkerHeartbeatThread(
        job_id=job_id,
        claim_token=claim_token,
        abort_event=abort_event,
        main_loop=main_loop,
        asyncio_abort_event=asyncio_abort_event,
        interval_seconds=0.05,
    )

    with patch(
        "app.repositories.analytics.AnalyticsJobRepository.renew_lease_with_fencing",
        return_value=True,
    ):
        thread.start()
        # Allow thread to run at least one tick
        await asyncio.sleep(0.12)
        assert thread.is_alive()
        assert thread.thread_loop is not None
        assert thread.thread_engine is not None

        # Stop thread
        thread.stop()
        thread.join(timeout=2.0)

        assert not thread.is_alive()
        assert thread.thread_loop is None
        assert thread.thread_engine is None
        assert thread.is_disposed is True


@pytest.mark.asyncio
async def test_heartbeat_thread_safe_signaling_unblocks_main_loop() -> None:
    """Verifies that thread-safe abort signaling triggers asyncio_abort_event and unblocks main event loop."""
    job_id = uuid.uuid4()
    claim_token = uuid.uuid4()
    abort_event = threading.Event()
    main_loop = asyncio.get_running_loop()
    asyncio_abort_event = asyncio.Event()

    thread = WorkerHeartbeatThread(
        job_id=job_id,
        claim_token=claim_token,
        abort_event=abort_event,
        main_loop=main_loop,
        asyncio_abort_event=asyncio_abort_event,
        interval_seconds=0.05,
    )

    # Simulate lease fencing violation on first renewal
    with patch(
        "app.repositories.analytics.AnalyticsJobRepository.renew_lease_with_fencing",
        return_value=False,
    ):
        thread.start()

        # Main loop awaits asyncio_abort_event
        start_wait = time.time()
        await asyncio.wait_for(asyncio_abort_event.wait(), timeout=2.0)
        elapsed = time.time() - start_wait

        assert asyncio_abort_event.is_set()
        assert abort_event.is_set()
        assert elapsed < 1.0  # Immediately signaled thread-safely

        thread.stop()
        thread.join(timeout=2.0)


@pytest.mark.asyncio
async def test_heartbeat_thread_max_failures_aborts_safely() -> None:
    """Verifies that exceeding max consecutive heartbeat failures triggers abort signaling."""
    job_id = uuid.uuid4()
    claim_token = uuid.uuid4()
    abort_event = threading.Event()
    main_loop = asyncio.get_running_loop()
    asyncio_abort_event = asyncio.Event()

    thread = WorkerHeartbeatThread(
        job_id=job_id,
        claim_token=claim_token,
        abort_event=abort_event,
        main_loop=main_loop,
        asyncio_abort_event=asyncio_abort_event,
        interval_seconds=0.02,
        max_consecutive_failures=2,
    )

    # Simulate database connection errors
    with patch(
        "app.repositories.analytics.AnalyticsJobRepository.renew_lease_with_fencing",
        side_effect=ConnectionResetError("DB dropped"),
    ):
        thread.start()

        await asyncio.wait_for(asyncio_abort_event.wait(), timeout=2.0)
        assert asyncio_abort_event.is_set()
        assert abort_event.is_set()

        thread.stop()
        thread.join(timeout=2.0)


@pytest.mark.asyncio
async def test_heartbeat_thread_termination_timeout_protection() -> None:
    """Verifies that thread stop and join do not hang the worker shutdown."""
    job_id = uuid.uuid4()
    claim_token = uuid.uuid4()
    abort_event = threading.Event()

    thread = WorkerHeartbeatThread(
        job_id=job_id,
        claim_token=claim_token,
        abort_event=abort_event,
        interval_seconds=10.0,
    )

    thread.start()
    start_t = time.time()
    thread.stop()
    thread.join(timeout=2.0)
    elapsed = time.time() - start_t

    assert not thread.is_alive()
    assert elapsed < 1.0  # Stopped within 100ms sleep chunk, not waiting 10s


def test_heartbeat_ticks_during_heavy_sync_computation() -> None:
    """Verifies GIL independence: heartbeat thread continues ticking during heavy synchronous CPU computation."""
    job_id = uuid.uuid4()
    claim_token = uuid.uuid4()
    abort_event = threading.Event()

    thread = WorkerHeartbeatThread(
        job_id=job_id,
        claim_token=claim_token,
        abort_event=abort_event,
        interval_seconds=0.05,
    )

    with patch(
        "app.repositories.analytics.AnalyticsJobRepository.renew_lease_with_fencing",
        return_value=True,
    ):
        thread.start()

        # Perform heavy synchronous Polars / CPU computation in main thread
        import polars as pl

        t0 = time.time()
        while time.time() - t0 < 0.3:
            df = pl.DataFrame({"a": list(range(10000))})
            _ = df.select([pl.col("a").sum().alias("s"), pl.col("a").mean().alias("m")])

        thread.stop()
        thread.join(timeout=2.0)

        # Verify renewals occurred despite synchronous main-thread execution
        assert thread.renewals_count >= 2
