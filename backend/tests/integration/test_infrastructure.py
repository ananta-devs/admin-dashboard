import pytest
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import get_db_session
from app.main import app


@pytest.mark.asyncio
async def test_live_postgres_connectivity_integration():
    """Integration test: validates real PostgreSQL container connection & query execution."""
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        result = await session.execute(text("SELECT current_database(), version();"))
        row = result.fetchone()
        assert row is not None
        assert row[0] in ("vibe_enterprise", "vibe_enterprise_test")
        assert "PostgreSQL" in row[1]

    await test_engine.dispose()


@pytest.mark.asyncio
async def test_live_redis_connectivity_integration():
    """Integration test: validates real Redis container connection, set, get, and ping."""
    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    pong = await r.ping()
    assert pong is True

    test_key = "integration_live_test_key"
    await r.set(test_key, "active_value", ex=30)
    val = await r.get(test_key)
    assert val == "active_value"
    await r.delete(test_key)
    await r.aclose()


@pytest.mark.asyncio
async def test_live_ready_endpoint_integration():
    """Integration test: calls live /api/v1/ready with NullPool session to avoid cross-test loop closure."""
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def get_test_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = get_test_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert "connected" in data["database"]
        assert "connected" in data["redis"]

    app.dependency_overrides.clear()
    await test_engine.dispose()
