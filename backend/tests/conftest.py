import asyncio
import uuid
from collections.abc import AsyncGenerator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from tests.isolation_guard import (
    assert_test_database_connection,
    get_enforced_test_database_url,
    validate_database_url,
)


def pytest_sessionstart(session):
    """Fail-closed session hook: verifies test database target before any test executes."""
    test_url = get_enforced_test_database_url()
    validate_database_url(test_url)

    # Strictly override application settings so any legacy fixture reading settings.DATABASE_URL is isolated
    settings.DATABASE_URL = test_url
    settings.DATABASE_SYNC_URL = test_url.replace("+asyncpg", "+psycopg")
    import os

    os.environ["DATABASE_URL"] = test_url
    os.environ["TEST_DATABASE_URL"] = test_url
    os.environ["POSTGRES_DB"] = "vibe_enterprise_test"
    os.environ["E2E_EXPECTED_DATABASE"] = "vibe_enterprise_test"

    # Synchronously verify connection identity using asyncio.run
    async def _check():
        engine = create_async_engine(test_url, poolclass=NullPool)
        try:
            await assert_test_database_connection(engine)
        finally:
            await engine.dispose()

    asyncio.run(_check())


@pytest.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Provide a session factory strictly bound to the verified test database."""
    test_url = get_enforced_test_database_url()
    validate_database_url(test_url)
    engine = create_async_engine(test_url, poolclass=NullPool)
    # Double check connection identity on engine
    await assert_test_database_connection(engine)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
async def db_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """Provide an isolated, real PostgreSQL session verified on vibe_enterprise_test."""
    async with session_factory() as session:
        await assert_test_database_connection(session)
        yield session
        if session.in_transaction():
            await session.rollback()
        await session.close()


@pytest.fixture
def unique_id() -> str:
    """Generate a short unique identifier for test data isolation."""
    return uuid.uuid4().hex[:8]
