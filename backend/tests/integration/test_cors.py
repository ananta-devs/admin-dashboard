import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_cors_allowed_origin_preflight():
    """Verify credential-aware CORS preflight succeeds with exact origin and allowed headers including X-CSRF-Token."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Preflight OPTIONS request from configured allowed origin
        res = await client.options(
            "/api/v1/auth/change-password",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type, X-CSRF-Token",
            },
        )
        assert res.status_code == 200
        # Exact origin matching (no wildcard)
        assert res.headers.get("access-control-allow-origin") == "http://localhost:3000"
        assert res.headers.get("access-control-allow-credentials") == "true"
        allow_headers = res.headers.get("access-control-allow-headers", "").lower()
        assert "x-csrf-token" in allow_headers
        assert "content-type" in allow_headers


@pytest.mark.asyncio
async def test_cors_rejected_origin_preflight():
    """Verify that untrusted origins do not receive Access-Control-Allow-Origin headers in preflight."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.options(
            "/api/v1/auth/change-password",
            headers={
                "Origin": "https://malicious-attacker.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type, X-CSRF-Token",
            },
        )
        # Untrusted origin must not be granted CORS access
        assert res.headers.get("access-control-allow-origin") is None
        assert res.headers.get("access-control-allow-origin") != "*"


@pytest.mark.asyncio
async def test_cors_actual_request_allowed_vs_rejected():
    """Verify actual request echoes Access-Control-Allow-Origin only for allowed origins."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Allowed origin echoes exact origin and credentials
        good_res = await client.get("/api/v1/health", headers={"Origin": "http://localhost:3000"})
        assert good_res.status_code == 200
        assert good_res.headers.get("access-control-allow-origin") == "http://localhost:3000"
        assert good_res.headers.get("access-control-allow-credentials") == "true"

        # 2. Secondary allowed origin (127.0.0.1:3000) also matches exactly
        good_res2 = await client.get("/api/v1/health", headers={"Origin": "http://127.0.0.1:3000"})
        assert good_res2.status_code == 200
        assert good_res2.headers.get("access-control-allow-origin") == "http://127.0.0.1:3000"

        # 3. Untrusted origin does not receive CORS headers
        bad_res = await client.get("/api/v1/health", headers={"Origin": "https://evil.org"})
        assert bad_res.status_code == 200
        assert bad_res.headers.get("access-control-allow-origin") is None


def test_cors_origins_configuration_parsing():
    """Verify comma-separated origins string from environment is parsed cleanly into a list of strings."""
    from app.core.config import Settings

    raw_env_value = "https://admin.vibe.enterprise, https://data.vibe.enterprise"
    cfg = Settings(ALLOWED_ORIGINS=raw_env_value)
    assert cfg.ALLOWED_ORIGINS == ["https://admin.vibe.enterprise", "https://data.vibe.enterprise"]
    assert "*" not in cfg.ALLOWED_ORIGINS
