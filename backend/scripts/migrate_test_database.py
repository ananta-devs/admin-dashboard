"""Safely execute Alembic migration 481909849343 on vibe_enterprise_test with fail-closed identity checks."""

import os
import sys

# Ensure backend root is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

TEST_DB_NAME = "vibe_enterprise_test"
EXPECTED_PREDECESSOR = "370908748232"
TARGET_REVISION = "481909849343"

TEST_ASYNC_URL = "postgresql+asyncpg://vibe_admin:dev_secret_postgres_pass_change_in_prod@127.0.0.1:5432/vibe_enterprise_test"
TEST_SYNC_URL = "postgresql+psycopg://vibe_admin:dev_secret_postgres_pass_change_in_prod@127.0.0.1:5432/vibe_enterprise_test"

# Enforce environment variables so alembic/env.py connects to vibe_enterprise_test
os.environ["DATABASE_URL"] = TEST_ASYNC_URL
os.environ["DATABASE_SYNC_URL"] = TEST_SYNC_URL
os.environ["E2E_EXPECTED_DATABASE"] = TEST_DB_NAME

import asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


async def verify_and_migrate():
    print(f"--> Connecting to test database: {TEST_DB_NAME}...")
    engine = create_async_engine(TEST_ASYNC_URL)

    # 1. Identity Check
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT current_database();"))
        actual_db = res.scalar()
        print(f"Authoritative current_database(): '{actual_db}'")
        if actual_db != TEST_DB_NAME:
            print(f"FATAL: Database identity mismatch! Expected '{TEST_DB_NAME}', got '{actual_db}'. Aborting!")
            sys.exit(1)

        # 2. Predecessor Check
        res_ver = await conn.execute(text("SELECT version_num FROM alembic_version;"))
        actual_ver = res_ver.scalar()
        print(f"Authoritative alembic_version: '{actual_ver}'")
        if actual_ver != EXPECTED_PREDECESSOR:
            print(f"FATAL: Predecessor mismatch! Expected '{EXPECTED_PREDECESSOR}', got '{actual_ver}'. Aborting!")
            sys.exit(1)

    await engine.dispose()
    print("--> All fail-closed identity checks PASSED. Executing Alembic upgrade on vibe_enterprise_test...")

    alembic_cfg = Config("backend/alembic.ini")
    alembic_cfg.set_main_option("script_location", "backend/alembic")
    alembic_cfg.set_main_option("sqlalchemy.url", TEST_SYNC_URL)

    # Execute migration
    command.upgrade(alembic_cfg, TARGET_REVISION)
    print(f"--> Successfully upgraded {TEST_DB_NAME} to revision {TARGET_REVISION}!")

    # Verify post-migration state
    engine_post = create_async_engine(TEST_ASYNC_URL)
    async with engine_post.connect() as conn:
        res_post = await conn.execute(text("SELECT version_num FROM alembic_version;"))
        post_ver = res_post.scalar()
        print(f"--> Verified post-migration alembic_version on {TEST_DB_NAME}: '{post_ver}'")
        if post_ver != TARGET_REVISION:
            print(f"FATAL: Post-migration version mismatch! Expected '{TARGET_REVISION}', got '{post_ver}'.")
            sys.exit(1)

        # Verify table existence
        res_table = await conn.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_name = 'stored_files';")
        )
        if res_table.scalar() != 1:
            print("FATAL: Table 'stored_files' was not found in information_schema!")
            sys.exit(1)
        print("--> Verified table 'stored_files' created successfully in vibe_enterprise_test!")

    await engine_post.dispose()
    print("--> Migration execution and verification 100% COMPLETE.")


if __name__ == "__main__":
    asyncio.run(verify_and_migrate())
