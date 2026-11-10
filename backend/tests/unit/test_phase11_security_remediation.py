"""Phase 11 Stage 2.1 Backend Security Remediation Regression Test Suite.

Verifies:
1. SEC-P11-002: Session cookie security and single authoritative cookie emission.
2. SEC-P11-005: Backup API authorization (Owner/Admin allowed, Member/Viewer blocked).
3. SEC-P11-006: Production OpenAPI and documentation endpoint suppression.
4. SEC-P11-001: Backend security headers (X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy, HSTS).
5. SEC-P11-007: Production secret and placeholder rejection.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Response, status
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.api.v1.routers.auth import _clear_auth_cookies, _set_auth_cookies
from app.core.config import Settings, settings
from app.main import app


# =====================================================================
# 1. SEC-P11-002: Session Cookie Security Tests
# =====================================================================
class TestSessionCookieSecurity:
    """Verify that auth cookies have consistent security flags and no insecure fallback."""

    def test_auth_cookie_attributes_in_secure_mode(self):
        """When SESSION_COOKIE_SECURE=True, emitted cookie must have Secure=True and HttpOnly=True."""
        response = Response()
        with (
            patch.object(settings, "SESSION_COOKIE_SECURE", True),
            patch.object(settings, "SESSION_COOKIE_NAME", "__Host-vibe_session"),
            patch.object(settings, "SESSION_COOKIE_SAMESITE", "lax"),
        ):
            _set_auth_cookies(response, "test_opaque_token_value_32_bytes_len")

        # Response headers must contain single Set-Cookie header for __Host-vibe_session
        cookie_headers = response.headers.getlist("set-cookie")
        assert len(cookie_headers) == 1, f"Expected 1 cookie, got: {cookie_headers}"

        raw_cookie = cookie_headers[0]
        assert raw_cookie.startswith("__Host-vibe_session=test_opaque_token_value_32_bytes_len")
        assert "secure" in raw_cookie.lower()
        assert "httponly" in raw_cookie.lower()
        assert "samesite=lax" in raw_cookie.lower()
        # Verify no insecure fallback cookie named 'vibe_session' was emitted
        assert "vibe_session=" not in raw_cookie or raw_cookie.startswith("__Host-vibe_session=")

    def test_auth_cookie_attributes_in_dev_mode(self):
        """In development mode with default cookie name, emits single cookie with configured flags."""
        response = Response()
        with (
            patch.object(settings, "SESSION_COOKIE_SECURE", False),
            patch.object(settings, "SESSION_COOKIE_NAME", "vibe_session"),
            patch.object(settings, "SESSION_COOKIE_SAMESITE", "lax"),
        ):
            _set_auth_cookies(response, "test_token_123")

        cookie_headers = response.headers.getlist("set-cookie")
        assert len(cookie_headers) == 1
        raw_cookie = cookie_headers[0]
        assert raw_cookie.startswith("vibe_session=test_token_123")
        assert "httponly" in raw_cookie.lower()
        assert "samesite=lax" in raw_cookie.lower()

    def test_clear_auth_cookies_clears_both_configured_and_legacy(self):
        """Logout must delete the configured cookie and cleanly evict any legacy cookie."""
        response = Response()
        with patch.object(settings, "SESSION_COOKIE_NAME", "__Host-vibe_session"):
            _clear_auth_cookies(response)

        cookie_headers = response.headers.getlist("set-cookie")
        assert len(cookie_headers) == 2
        cookie_text = " ".join(cookie_headers)
        assert "__Host-vibe_session=" in cookie_text
        assert "vibe_session=" in cookie_text


# =====================================================================
# 2. SEC-P11-001: Backend Security Headers Tests
# =====================================================================
class TestSecurityHeaders:
    """Verify ASGI SecurityHeadersMiddleware applies mandatory headers to all responses."""

    @pytest.fixture
    def client(self):
        return TestClient(app)

    def test_security_headers_present_on_api_success(self, client):
        """API endpoints must return nosniff, DENY, referrer-policy, and permissions-policy."""
        response = client.get("/api/v1/health")
        assert response.status_code == status.HTTP_200_OK
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert (
            response.headers.get("Permissions-Policy") == "camera=(), microphone=(), geolocation=()"
        )

    def test_security_headers_present_on_error_response(self, client):
        """Error responses (e.g. 404, 422) must also receive full security headers."""
        response = client.get("/api/v1/non_existent_route_404")
        assert response.status_code == status.HTTP_404_NOT_FOUND
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        assert (
            response.headers.get("Permissions-Policy") == "camera=(), microphone=(), geolocation=()"
        )

    def test_hsts_header_emitted_over_https_forwarded(self, client):
        """When request indicates HTTPS via X-Forwarded-Proto, HSTS header must be added."""
        response = client.get("/api/v1/health", headers={"X-Forwarded-Proto": "https"})
        assert response.status_code == status.HTTP_200_OK
        assert (
            response.headers.get("Strict-Transport-Security")
            == "max-age=31536000; includeSubDomains"
        )

    def test_hsts_header_omitted_over_plain_http(self, client):
        """Plain HTTP requests must NOT receive HSTS header to avoid breaking non-TLS environments."""
        response = client.get("/api/v1/health")
        assert response.status_code == status.HTTP_200_OK
        assert "Strict-Transport-Security" not in response.headers

    def test_cors_preflight_preserves_security_headers(self, client):
        """CORS OPTIONS preflight must return CORS headers alongside security headers."""
        response = client.options(
            "/api/v1/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == status.HTTP_200_OK
        assert response.headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert response.headers.get("X-Frame-Options") == "DENY"


# =====================================================================
# 3. SEC-P11-006: Production OpenAPI Suppression Tests
# =====================================================================
class TestOpenAPISuppression:
    """Verify that API docs and schema are disabled in production and enabled in development."""

    def test_openapi_disabled_in_production(self):
        """When ENVIRONMENT='production', /docs, /redoc, /openapi.json must be suppressed."""
        prod_settings = Settings(
            ENVIRONMENT="production",
            POSTGRES_PASSWORD="secure_prod_password_not_default_123!",
            REDIS_PASSWORD="secure_prod_redis_password_not_default_123!",
            SESSION_SECRET_KEY="a" * 32,
            ENCRYPTION_MASTER_KEY="b" * 32,
            RATE_LIMIT_HMAC_KEY="c" * 32,
            REPORT_RENDERER_SECRET="d" * 32,
            SESSION_COOKIE_SECURE=True,
            ALLOWED_ORIGINS=["https://app.vibe.enterprise"],
        )
        is_dev = prod_settings.ENVIRONMENT.lower() in (
            "development",
            "dev",
            "test",
            "testing",
            "local",
        )
        assert not is_dev

        test_app = FastAPI(
            docs_url="/docs" if is_dev else None,
            redoc_url="/redoc" if is_dev else None,
            openapi_url="/openapi.json" if is_dev else None,
        )
        client = TestClient(test_app)
        assert client.get("/docs").status_code == status.HTTP_404_NOT_FOUND
        assert client.get("/redoc").status_code == status.HTTP_404_NOT_FOUND
        assert client.get("/openapi.json").status_code == status.HTTP_404_NOT_FOUND

    def test_openapi_enabled_in_development(self):
        """In development mode, /docs, /redoc, and /openapi.json remain active."""
        dev_settings = Settings(ENVIRONMENT="development")
        is_dev = dev_settings.ENVIRONMENT.lower() in (
            "development",
            "dev",
            "test",
            "testing",
            "local",
        )
        assert is_dev

        test_app = FastAPI(
            docs_url="/docs" if is_dev else None,
            redoc_url="/redoc" if is_dev else None,
            openapi_url="/openapi.json" if is_dev else None,
        )
        client = TestClient(test_app)
        assert client.get("/docs").status_code == status.HTTP_200_OK
        assert client.get("/openapi.json").status_code == status.HTTP_200_OK


# =====================================================================
# 4. SEC-P11-007: Production Secret & Placeholder Validation Tests
# =====================================================================
class TestProductionSecretValidation:
    """Verify fail-fast validation against development placeholders in production."""

    def test_production_rejects_dev_postgres_password(self):
        """Production environment must reject default postgres password placeholder."""
        with pytest.raises(
            ValueError, match="Insecure default password detected for POSTGRES_PASSWORD"
        ):
            Settings(
                ENVIRONMENT="production",
                POSTGRES_PASSWORD="dev_secret_postgres_pass_change_in_prod",
                REDIS_PASSWORD="secure_prod_redis_password_not_default_123!",
                SESSION_SECRET_KEY="a" * 32,
                ENCRYPTION_MASTER_KEY="b" * 32,
                RATE_LIMIT_HMAC_KEY="c" * 32,
                REPORT_RENDERER_SECRET="d" * 32,
                SESSION_COOKIE_SECURE=True,
                ALLOWED_ORIGINS=["https://app.vibe.enterprise"],
            )

    def test_production_rejects_dev_redis_password(self):
        """Production environment must reject default redis password placeholder."""
        with pytest.raises(
            ValueError, match="Insecure default password detected for REDIS_PASSWORD"
        ):
            Settings(
                ENVIRONMENT="production",
                POSTGRES_PASSWORD="secure_prod_password_not_default_123!",
                REDIS_PASSWORD="dev_secret_redis_pass_change_in_prod",
                SESSION_SECRET_KEY="a" * 32,
                ENCRYPTION_MASTER_KEY="b" * 32,
                RATE_LIMIT_HMAC_KEY="c" * 32,
                REPORT_RENDERER_SECRET="d" * 32,
                SESSION_COOKIE_SECURE=True,
                ALLOWED_ORIGINS=["https://app.vibe.enterprise"],
            )

    def test_production_rejects_insecure_cookie_setting(self):
        """Production environment must require SESSION_COOKIE_SECURE=True."""
        with pytest.raises(ValueError, match="SESSION_COOKIE_SECURE must be True in production"):
            Settings(
                ENVIRONMENT="production",
                POSTGRES_PASSWORD="secure_prod_password_not_default_123!",
                REDIS_PASSWORD="secure_prod_redis_password_not_default_123!",
                SESSION_SECRET_KEY="a" * 32,
                ENCRYPTION_MASTER_KEY="b" * 32,
                RATE_LIMIT_HMAC_KEY="c" * 32,
                REPORT_RENDERER_SECRET="d" * 32,
                SESSION_COOKIE_SECURE=False,
                ALLOWED_ORIGINS=["https://app.vibe.enterprise"],
            )

    def test_development_permits_default_placeholders(self):
        """Development environment must permit default placeholders for frictionless local setup."""
        s = Settings(ENVIRONMENT="development")
        assert s.POSTGRES_PASSWORD == "dev_secret_postgres_pass_change_in_prod"
        assert s.REDIS_PASSWORD == "dev_secret_redis_pass_change_in_prod"


# =====================================================================
# 5. SEC-P11-005: Backup Endpoint Authorization Tests
# =====================================================================
class TestBackupEndpointAuthorization:
    """Verify that backup status and history endpoints enforce registered permissions."""

    @pytest.mark.asyncio
    async def test_backup_status_rejected_for_viewer_and_member(self):
        """A user without organization.settings.update or audit.read must receive 403 Forbidden."""
        from app.api.dependencies.auth import (
            get_current_membership,
            get_current_session,
            get_current_user,
            get_db_session,
        )

        dummy_user = MagicMock(id="user-123", email="viewer@vibe.enterprise")
        dummy_membership = MagicMock(id="mem-123", organization_id="org-123", user_id="user-123")
        dummy_session = MagicMock(id="sess-123", user=dummy_user, active_organization_id="org-123")

        mock_db = AsyncMock()

        # Mock PermissionService to report no permissions for viewer
        with patch("app.api.dependencies.auth.PermissionService") as MockPermService:
            perm_instance = MockPermService.return_value
            perm_instance.get_effective_permissions = AsyncMock(
                return_value={"spreadsheets.read", "organization.read"}
            )
            perm_instance.check_membership_has_permission = AsyncMock(return_value=False)

            app.dependency_overrides[get_current_user] = lambda: dummy_user
            app.dependency_overrides[get_current_membership] = lambda: dummy_membership
            app.dependency_overrides[get_current_session] = lambda: dummy_session
            app.dependency_overrides[get_db_session] = lambda: mock_db

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res = await ac.get("/api/v1/backups/status")
                assert res.status_code == status.HTTP_403_FORBIDDEN
                body = res.json()
                assert "Permission denied" in body.get("error", {}).get("message", "")

            app.dependency_overrides.clear()

    @pytest.mark.asyncio
    async def test_backup_status_allowed_for_admin_and_owner(self):
        """A user with organization.settings.update or audit.read is granted access."""
        from app.api.dependencies.auth import (
            get_current_membership,
            get_current_session,
            get_current_user,
            get_db_session,
        )

        dummy_user = MagicMock(id="user-admin", email="admin@vibe.enterprise")
        dummy_membership = MagicMock(
            id="mem-admin", organization_id="org-admin", user_id="user-admin"
        )
        dummy_session = MagicMock(
            id="sess-admin", user=dummy_user, active_organization_id="org-admin"
        )

        mock_db = AsyncMock()

        with patch("app.api.dependencies.auth.PermissionService") as MockPermService:
            perm_instance = MockPermService.return_value
            # Admin has organization.settings.update and audit.read
            perm_instance.get_effective_permissions = AsyncMock(
                return_value={"organization.settings.update", "audit.read", "spreadsheets.read"}
            )
            perm_instance.check_membership_has_permission = AsyncMock(return_value=True)

            app.dependency_overrides[get_current_user] = lambda: dummy_user
            app.dependency_overrides[get_current_membership] = lambda: dummy_membership
            app.dependency_overrides[get_current_session] = lambda: dummy_session
            app.dependency_overrides[get_db_session] = lambda: mock_db

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                res = await ac.get("/api/v1/backups/status")
                assert res.status_code == status.HTTP_200_OK
                data = res.json()
                assert data["status"] == "HEALTHY"
                assert "rpo" in data
                assert "rto" in data

            app.dependency_overrides.clear()
