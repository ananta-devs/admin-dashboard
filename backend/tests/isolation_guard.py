"""Fail-closed database and environment isolation guard for automated test suites.

Enforces that test runners and fixtures strictly target approved isolated test databases
(e.g., vibe_enterprise_test) and abort immediately if an unapproved, development, or
production database is configured.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

APPROVED_TEST_DATABASES = frozenset({"vibe_enterprise_test"})
PROHIBITED_DATABASES = frozenset(
    {
        "vibe_enterprise",
        "vibe_benchmark",
        "postgres",
        "template0",
        "template1",
    }
)


def extract_database_name(url: str) -> str:
    """Extract database name from connection URL."""
    if not url:
        return ""
    # Remove async driver prefix for standard urlparse if needed
    cleaned = url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )
    parsed = urlparse(cleaned)
    db_name = (parsed.path or "").lstrip("/")
    return db_name


def validate_database_url(url: str) -> str:
    """Validate database URL string against approved test database registry.

    Raises:
        RuntimeError: If URL points to a prohibited or unapproved database.
    """
    if not url:
        raise RuntimeError(
            "FATAL: No database URL provided for test execution! "
            "Integration tests must explicitly target an approved test database (e.g. 'vibe_enterprise_test')."
        )
    db_name = extract_database_name(url)
    if not db_name:
        raise RuntimeError(
            f"FATAL: Malformed database URL '{url}': unable to determine database name. "
            "Test execution aborted to prevent data corruption."
        )
    if db_name in PROHIBITED_DATABASES:
        raise RuntimeError(
            f"FATAL: Database isolation violation! Target database '{db_name}' is strictly PROHIBITED for tests! "
            "Tests must NEVER connect to development or production databases ('vibe_enterprise'). "
            "Test execution immediately aborted to prevent data contamination."
        )
    if db_name not in APPROVED_TEST_DATABASES:
        raise RuntimeError(
            f"FATAL: Database '{db_name}' is not in approved test database registry: {sorted(APPROVED_TEST_DATABASES)}. "
            "Test execution aborted."
        )
    return db_name


async def assert_test_database_connection(session_or_engine: AsyncSession | AsyncEngine) -> str:
    """Execute 'SELECT current_database()' on active connection and assert approved test identity.

    Raises:
        RuntimeError: If runtime database identity is not an approved test database.
    """
    if isinstance(session_or_engine, AsyncSession):
        res = await session_or_engine.execute(text("SELECT current_database();"))
        actual_db = res.scalar()
    else:
        async with session_or_engine.connect() as conn:
            res = await conn.execute(text("SELECT current_database();"))
            actual_db = res.scalar()

    if not actual_db:
        raise RuntimeError(
            "FATAL: Unable to determine active database identity via 'SELECT current_database()'."
        )

    if actual_db in PROHIBITED_DATABASES:
        raise RuntimeError(
            f"FATAL: Database connection identity mismatch! Active connection is connected to protected database '{actual_db}'! "
            "Immediate abort before executing any SQL mutations."
        )
    if actual_db not in APPROVED_TEST_DATABASES:
        raise RuntimeError(
            f"FATAL: Active connection database '{actual_db}' is not an approved test database: {sorted(APPROVED_TEST_DATABASES)}."
        )
    return actual_db


def get_enforced_test_database_url() -> str:
    """Retrieve test database URL from environment or settings, guaranteeing test isolation."""
    # Check explicit test database URL
    test_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_TEST_URL")
    if not test_url:
        # Check standard DATABASE_URL
        test_url = os.environ.get("DATABASE_URL")

    # If test_url is pointing to vibe_enterprise_test, validate and return
    if test_url:
        validate_database_url(test_url)
        return test_url

    # Fall back to default test database on local postgres
    default_test_url = "postgresql+asyncpg://vibe_admin:dev_secret_postgres_pass_change_in_prod@127.0.0.1:5432/vibe_enterprise_test"
    validate_database_url(default_test_url)
    return default_test_url
