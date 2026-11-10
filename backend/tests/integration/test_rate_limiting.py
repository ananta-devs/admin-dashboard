import pytest
import redis.asyncio as aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.exceptions import RateLimitExceededError, ServiceUnavailableError
from app.core.rate_limit import check_rate_limit
from app.core.security import compute_keyed_hash
from app.db.session import get_db_session
from app.main import app


@pytest.mark.asyncio
async def test_rate_limiting_enforcement_and_retry_after(unique_id: str):
    action = f"test_action_{unique_id}"
    ident = f"user_{unique_id}@enterprise.com"

    # Allow 2 requests, 3rd should fail with RateLimitExceededError
    await check_rate_limit(action=action, identifier=ident, max_requests=2, window_seconds=10)
    await check_rate_limit(action=action, identifier=ident, max_requests=2, window_seconds=10)

    with pytest.raises(RateLimitExceededError) as exc_info:
        await check_rate_limit(action=action, identifier=ident, max_requests=2, window_seconds=10)

    assert exc_info.value.retry_after_seconds > 0
    assert exc_info.value.code == "RATE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_rate_limiting_keyed_privacy_in_redis(unique_id: str):
    action = f"privacy_action_{unique_id}"
    sensitive_email = f"sensitive_vip_{unique_id}@enterprise.com"

    await check_rate_limit(
        action=action, identifier=sensitive_email, max_requests=5, window_seconds=10
    )

    expected_hash = compute_keyed_hash(sensitive_email)
    expected_redis_key = f"rl:{action}:{expected_hash}"

    r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    val = await r.get(expected_redis_key)
    assert val == "1"

    # Confirm raw email does not appear in any Redis key
    keys = await r.keys(f"*{sensitive_email}*")
    assert len(keys) == 0

    await r.delete(expected_redis_key)
    await r.aclose()


@pytest.mark.asyncio
async def test_api_rate_limiting_returns_429(unique_id: str):
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def get_test_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = get_test_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Email that will exceed RATE_LIMIT_LOGIN_MAX_REQUESTS (10 per minute)
        email = f"brute_{unique_id}@enterprise.com"
        for _ in range(settings.RATE_LIMIT_LOGIN_MAX_REQUESTS):
            res = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": "AnyPassword123!"}
            )
            assert res.status_code == 401

        # The (limit + 1)-th attempt must return 429 Too Many Requests
        rate_limited_res = await client.post(
            "/api/v1/auth/login", json={"email": email, "password": "AnyPassword123!"}
        )
        assert rate_limited_res.status_code == 429
        assert "Retry-After" in rate_limited_res.headers
        assert rate_limited_res.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_redis_outage_fails_closed_with_503(monkeypatch, unique_id: str):
    """When Redis is unreachable during rate limiting, the system fails closed with HTTP 503 Service Unavailable."""
    # Point Redis to an unreachable port
    monkeypatch.setattr(settings, "REDIS_URL", "redis://127.0.0.1:59999/0")

    # Direct check_rate_limit raises ServiceUnavailableError
    with pytest.raises(ServiceUnavailableError) as exc_info:
        await check_rate_limit(
            action="login",
            identifier=f"outage_{unique_id}@enterprise.com",
            max_requests=5,
            window_seconds=60,
        )
    assert exc_info.value.code == "SERVICE_UNAVAILABLE"

    # API request returns HTTP 503 with Retry-After header
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def get_test_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = get_test_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.post(
            "/api/v1/auth/login",
            json={"email": f"outage_{unique_id}@enterprise.com", "password": "AnyPassword123!"},
        )
        assert res.status_code == 503
        assert "Retry-After" in res.headers
        assert res.json()["error"]["code"] == "SERVICE_UNAVAILABLE"

    app.dependency_overrides.clear()
    await test_engine.dispose()
