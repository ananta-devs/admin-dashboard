from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.session import SessionTransactionOrigin


@asynccontextmanager
async def transaction(session: AsyncSession) -> AsyncGenerator[AsyncSession, None]:
    """Provide an explicit Unit of Work transaction boundary.

    Ownership Policy:
    1. Root Transaction Owner (depth == 0 & no external caller transaction):
       - If no explicit transaction is active on the session, this block owns the
         root Unit of Work.
       - If the session was idle (`not session.in_transaction()`), uses `session.begin()`
         to begin and commit the root transaction.
       - If a transaction was already autobegun by prior SELECT queries (where
         `session.begin()` would raise an InvalidRequestError), manages the root
         transaction explicitly: commits upon clean block exit, or rolls back on error
         or task cancellation.
       - Guarantees all writes are committed to the database upon exit.

    2. Caller-Owned or Nested Transaction / Savepoint:
       - If an outer explicit transaction boundary is already active—either via a
         prior helper block (`depth > 0`) or an explicit caller-owned transaction
         (`tx.origin in (BEGIN, BEGIN_NESTED)`)—this block creates a nested SAVEPOINT
         via `session.begin_nested()`.
       - Clean exit releases the savepoint without prematurely committing the outer
         caller's transaction.
       - Exceptions or cancellation roll back only to the savepoint and re-raise,
         preserving the surrounding caller's Unit of Work.
    """
    depth = session.info.get("_explicit_tx_depth", 0)
    root_tx = session.sync_session.get_transaction()

    # 1. Nested helper-managed operation
    is_helper_nested = depth > 0

    # 2. Already active external nested transaction (caller-owned savepoint)
    is_external_nested = session.in_nested_transaction()

    # 3. Explicit caller-owned root transaction with origin BEGIN
    is_external_begin = (
        session.in_transaction()
        and root_tx is not None
        and root_tx.origin == SessionTransactionOrigin.BEGIN
    )

    if is_helper_nested or is_external_nested or is_external_begin:
        session.info["_explicit_tx_depth"] = depth + 1
        try:
            async with session.begin_nested():
                yield session
        finally:
            session.info["_explicit_tx_depth"] = depth
    else:
        session.info["_explicit_tx_depth"] = 1
        if not session.in_transaction():
            try:
                async with session.begin():
                    yield session
            finally:
                session.info["_explicit_tx_depth"] = 0
        else:
            try:
                yield session
                await session.commit()
            except BaseException:
                if session.in_transaction():
                    await session.rollback()
                raise
            finally:
                session.info["_explicit_tx_depth"] = 0
