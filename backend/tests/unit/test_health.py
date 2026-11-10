from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.db.session import get_db_session
from app.main import app


@pytest.mark.asyncio
async def test_health_check_unit():
    """Unit test: checks /api/v1/health with zero infrastructure dependencies."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "timestamp" in data


@pytest.mark.asyncio
async def test_readiness_check_mocked():
    """Unit test: checks /api/v1/ready with mocked DB & Redis to guarantee isolated unit test behavior."""
    mock_db = AsyncMock()
    mock_result = AsyncMock()
    mock_result.scalar = lambda: 1
    mock_db.execute.return_value = mock_result

    async def override_get_db_session():
        yield mock_db

    app.dependency_overrides[get_db_session] = override_get_db_session

    with patch("redis.asyncio.from_url") as mock_redis_from_url:
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_redis.aclose = AsyncMock()
        mock_redis_from_url.return_value = mock_redis

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/ready")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ready"
            assert "connected" in data["database"]
            assert "connected" in data["redis"]

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cross_tenant_access_handler_returns_safe_404():
    """Unit test: verifies that cross-tenant access exception handler returns public 404

    to prevent sensitive resource existence enumeration.
    """
    from app.core.exceptions import CrossTenantAccessError

    # Register temporary test endpoint to trigger exception
    @app.get("/api/v1/test-cross-tenant-security")
    async def trigger_cross_tenant():
        raise CrossTenantAccessError("Workspace", "secret-ws-id", "attacker-org-id")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/test-cross-tenant-security")
        assert response.status_code == 404
        data = response.json()
        assert data["success"] is False
        assert data["error"]["code"] == "ENTITY_NOT_FOUND"
        assert "not found" in data["error"]["message"].lower()
