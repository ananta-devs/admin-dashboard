import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.transaction import transaction
from app.models.organization import Organization
from app.schemas.organization import OrganizationCreate
from app.services.organization import OrganizationService


@pytest.mark.asyncio
async def test_nested_transaction_rollback_preserves_outer_transaction(
    db_session: AsyncSession, unique_id: str
):
    """Test A: Verifies that a rollback in a nested transaction (savepoint) does not

    abort or corrupt the surrounding outer transaction, allowing subsequent outer
    operations to commit successfully.
    """
    slug_outer_1 = f"outer-1-{unique_id}"
    slug_nested = f"nested-{unique_id}"
    slug_outer_2 = f"outer-2-{unique_id}"

    # 1. Begin an outer root transaction
    async with transaction(db_session):
        # 2. Insert outer test record 1
        org_outer_1 = Organization(name=f"Outer Org 1 {unique_id}", slug=slug_outer_1)
        db_session.add(org_outer_1)
        await db_session.flush()

        # 3. Enter transaction helper again to establish a nested savepoint
        nested_error_caught = False
        try:
            async with transaction(db_session):
                # 4. Insert nested test record
                org_nested = Organization(name=f"Nested Org {unique_id}", slug=slug_nested)
                db_session.add(org_nested)
                await db_session.flush()

                # 5. Raise a controlled exception inside the nested transaction
                raise RuntimeError("Controlled nested savepoint failure")
        except RuntimeError as e:
            # 6. Catch exception outside nested transaction
            assert "Controlled nested savepoint failure" in str(e)
            nested_error_caught = True

        assert nested_error_caught is True

        # 7. Insert outer test record 2
        org_outer_2 = Organization(name=f"Outer Org 2 {unique_id}", slug=slug_outer_2)
        db_session.add(org_outer_2)
        await db_session.flush()

    # 8. Transaction committed upon exiting the outer transaction block
    # 9. Query real PostgreSQL: outer records exist, nested record does NOT exist
    res_outer_1 = await db_session.execute(
        select(Organization).where(Organization.slug == slug_outer_1)
    )
    assert res_outer_1.scalar_one_or_none() is not None

    res_nested = await db_session.execute(
        select(Organization).where(Organization.slug == slug_nested)
    )
    assert res_nested.scalar_one_or_none() is None

    res_outer_2 = await db_session.execute(
        select(Organization).where(Organization.slug == slug_outer_2)
    )
    assert res_outer_2.scalar_one_or_none() is not None


@pytest.mark.asyncio
async def test_outer_transaction_rollback_removes_all_nested_changes(
    db_session: AsyncSession, unique_id: str
):
    """Test B: Verifies that when an outer transaction fails, all changes—including

    those made inside successful nested transactions—are completely rolled back.
    """
    slug_outer = f"outer-b-{unique_id}"
    slug_nested = f"nested-b-{unique_id}"

    outer_error_caught = False
    try:
        # 1. Begin outer transaction
        async with transaction(db_session):
            # 2. Insert outer test record
            org_outer = Organization(name=f"Outer Org B {unique_id}", slug=slug_outer)
            db_session.add(org_outer)
            await db_session.flush()

            # 3. Enter nested transaction
            async with transaction(db_session):
                # 4. Insert nested test record
                org_nested = Organization(name=f"Nested Org B {unique_id}", slug=slug_nested)
                db_session.add(org_nested)
                await db_session.flush()
                # 5. Nested transaction succeeds and exits cleanly

            # 6. Force outer transaction failure before commit
            raise RuntimeError("Forced outer failure before root commit")
    except RuntimeError as e:
        assert "Forced outer failure" in str(e)
        outer_error_caught = True

    assert outer_error_caught is True

    # 7. Verify: neither outer nor nested records exist in PostgreSQL
    res_outer = await db_session.execute(
        select(Organization).where(Organization.slug == slug_outer)
    )
    assert res_outer.scalar_one_or_none() is None

    res_nested = await db_session.execute(
        select(Organization).where(Organization.slug == slug_nested)
    )
    assert res_nested.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_transaction_cancellation_safe_rollback(
    session_factory: async_sessionmaker[AsyncSession], unique_id: str
):
    """Integration test: verifies that task cancellation triggers transaction rollback

    in the autobegin-adopted branch without swallowing CancelledError or leaving the
    session in an active/corrupted transaction state.
    """
    slug = f"cancel-tx-{unique_id}"
    sync_point = asyncio.Event()

    async with session_factory() as session:
        # 1. Trigger autobegin with a prior SELECT
        await session.execute(select(Organization).limit(1))
        assert session.in_transaction() is True

        # 2 & 3. Define worker entering transaction and writing record
        async def worker():
            async with transaction(session):
                org = Organization(name=f"Cancel Test {unique_id}", slug=slug)
                session.add(org)
                await session.flush()
                # 4. Signal write is complete, then block indefinitely awaiting cancellation
                sync_point.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(worker())

        # Wait until write is flushed and task is blocked
        await sync_point.wait()

        # 5. Cancel running task
        task.cancel()

        # 6. Confirm cancellation propagates
        with pytest.raises(asyncio.CancelledError):
            await task

        # 7. Confirm transaction is no longer active on the session
        assert session.in_transaction() is False

    # 8. Open a new session and verify uncommitted write does not exist in PostgreSQL
    async with session_factory() as verify_session:
        res = await verify_session.execute(select(Organization).where(Organization.slug == slug))
        assert res.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_caller_owned_transaction_protection(
    session_factory: async_sessionmaker[AsyncSession], unique_id: str
):
    """Integration test: verifies that an explicitly caller-owned transaction (via session.begin())

    is never prematurely committed by a service transaction helper.
    """
    slug = f"caller-owned-{unique_id}"

    async with session_factory() as session:
        # 1. Start an explicit outer transaction using session.begin()
        async with session.begin():
            assert session.in_transaction() is True

            # 2. Call service mutation inside caller-owned transaction
            org_service = OrganizationService(session)
            org_read = await org_service.create_organization(
                OrganizationCreate(name=f"Caller Owned {unique_id}", slug=slug)
            )
            assert org_read.slug == slug

            # 3. Verify mutation is visible inside the outer transaction
            res = await session.execute(select(Organization).where(Organization.slug == slug))
            assert res.scalar_one_or_none() is not None

            # 4. Verify the outer transaction remains active after the service returns
            assert session.in_transaction() is True

            # 5. Roll back the outer transaction
            await session.rollback()

    # 6 & 7. Open another session and verify service mutation was NOT committed to PostgreSQL
    async with session_factory() as verify_session:
        res = await verify_session.execute(select(Organization).where(Organization.slug == slug))
        assert res.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_caller_owned_nested_transaction_protection(
    session_factory: async_sessionmaker[AsyncSession], unique_id: str
):
    """Integration test: verifies that an externally started nested transaction (via session.begin_nested())

    is never misclassified as an autobegun root transaction and is not prematurely committed.

    Test procedure:
    1. Open a fresh AsyncSession.
    2. Enter async with session.begin_nested().
    3. Verify that session.in_transaction() is true.
    4. Verify that session.in_nested_transaction() is true.
    5. Call OrganizationService.create_organization().
    6. Verify that the created organization is visible inside the active transaction.
    7. Verify that the caller's nested transaction remains active after the service returns.
    8. Verify that the helper did not commit the root transaction.
    9. Roll back the caller-owned nested transaction.
    10. Close or complete the remaining session lifecycle correctly.
    11. Open an independent session.
    12. Verify that the organization was not persisted in PostgreSQL.
    """
    slug = f"caller-nested-{unique_id}"

    # 1. Open a fresh AsyncSession
    async with session_factory() as session:
        # 2. Enter async with session.begin_nested()
        async with session.begin_nested() as nested_tx:
            # 3. Verify that session.in_transaction() is true
            assert session.in_transaction() is True

            # 4. Verify that session.in_nested_transaction() is true
            assert session.in_nested_transaction() is True

            # 5. Call OrganizationService.create_organization()
            org_service = OrganizationService(session)
            org_read = await org_service.create_organization(
                OrganizationCreate(name=f"Caller Nested {unique_id}", slug=slug)
            )
            assert org_read.slug == slug

            # 6. Verify that the created organization is visible inside the active transaction
            res = await session.execute(select(Organization).where(Organization.slug == slug))
            assert res.scalar_one_or_none() is not None

            # 7. Verify that the caller's nested transaction remains active after the service returns
            assert session.in_nested_transaction() is True

            # 8. Verify that the helper did not commit the root transaction
            assert session.in_transaction() is True

            # 9. Roll back the caller-owned nested transaction
            await nested_tx.rollback()

        # 10. Complete remaining session lifecycle correctly (roll back root transaction)
        if session.in_transaction():
            await session.rollback()

    # 11. Open an independent session
    async with session_factory() as verify_session:
        # 12. Verify that the organization was not persisted in PostgreSQL
        res = await verify_session.execute(select(Organization).where(Organization.slug == slug))
        assert res.scalar_one_or_none() is None
