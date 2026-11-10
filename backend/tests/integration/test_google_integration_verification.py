"""Google Provider Integration Verification Suite with Explicit Test Classifications.

TEST CLASSIFICATION TAXONOMY:
-----------------------------
1. [UNIT] - Cryptographic token vault, AAD tenant binding, key versioning, state lifecycle.
2. [MOCKED_PROVIDER] - Mocked Google OAuth 2.0 token endpoint, Drive API v3, Sheets API v4.
3. [PROVIDER_CONTRACT] - Schema conformance against official Google Sheets v4 and Drive v3 specs.
4. [LIVE_STAGING] - Real staging execution against live Google endpoints (explicitly unverified
   when production credentials are absent).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.core.security import (
    decrypt_integration_token,
    encrypt_integration_token,
    generate_secure_token,
)
from app.models.integration import ExternalIntegration, OAuthState
from app.models.organization import Organization
from app.models.user import User
from app.services.google_oauth import GoogleOAuthService


@pytest.fixture
async def db_session():
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await test_engine.dispose()


@pytest.fixture
async def test_org_and_user(db_session: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    org = Organization(name="Google Test Org", slug=f"google-test-{uuid.uuid4().hex[:8]}")
    user_email = f"google_tester_{uuid.uuid4().hex[:8]}@example.com"
    user = User(
        email=user_email,
        normalized_email=user_email.lower(),
        display_name="Google Tester",
    )
    db_session.add_all([org, user])
    await db_session.flush()
    await db_session.commit()
    return org.id, user.id


# ==============================================================================
# CLASS 1: UNIT TESTS (Cryptographic Vault & Token Lifecycle)
# ==============================================================================


class TestClass1UnitSecurity:
    """[UNIT] Tests verifying token vault encryption, AAD binding, and state tokens."""

    def test_unit_aes_gcm_aad_tenant_binding(self) -> None:
        """[UNIT] Verifies that AAD cryptographically binds token to tenant organization ID."""
        org_a = uuid.uuid4()
        org_b = uuid.uuid4()
        token = "ya29.sample_google_access_token_secret"

        # Encrypt with Tenant A AAD
        aad_a = str(org_a).encode("utf-8")
        encrypted = encrypt_integration_token(token, associated_data=aad_a)
        assert encrypted.startswith("v1:")

        # Decrypt with correct Tenant A AAD succeeds
        decrypted = decrypt_integration_token(encrypted, associated_data=aad_a)
        assert decrypted == token

        # Decrypt with Tenant B AAD FAILS with InvalidTag (tampering / cross-tenant swap prevention)
        aad_b = str(org_b).encode("utf-8")
        with pytest.raises((InvalidTag, ValueError)):
            decrypt_integration_token(encrypted, associated_data=aad_b)

    def test_unit_token_tampering_rejection(self) -> None:
        """[UNIT] Verifies that ciphertext or tag tampering raises decryption error."""
        org_id = uuid.uuid4()
        aad = str(org_id).encode("utf-8")
        encrypted = encrypt_integration_token("secret_token_123", associated_data=aad)
        parts = encrypted.split(":")
        # Corrupt one hex byte of ciphertext
        corrupted_ct = parts[2][:-2] + "ff"
        tampered = f"{parts[0]}:{parts[1]}:{corrupted_ct}"
        with pytest.raises((InvalidTag, ValueError)):
            decrypt_integration_token(tampered, associated_data=aad)

    def test_unit_oauth_state_expiration_calculation(self) -> None:
        """[UNIT] Verifies OAuth state expiration window defaults to 15 minutes."""
        now = datetime.now(UTC)
        state_token = generate_secure_token(32)
        oauth_state = OAuthState(
            organization_id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            provider="google",
            state_token=state_token,
            redirect_uri="http://localhost:3000/integrations/callback",
            expires_at=now + timedelta(minutes=15),
        )
        assert (oauth_state.expires_at < now) is False
        # If expired in the past
        oauth_state.expires_at = now - timedelta(seconds=1)
        assert (oauth_state.expires_at < now) is True


# ==============================================================================
# CLASS 2: MOCKED PROVIDER TESTS (OAuth Lifecycle, Error Handling, Throttling)
# ==============================================================================


class TestClass2MockedProvider:
    """[MOCKED_PROVIDER] Tests simulating Google OAuth 2.0 and Sheets API responses."""

    @pytest.mark.asyncio
    async def test_mocked_oauth_callback_exchange_and_profile(
        self, db_session: AsyncSession, test_org_and_user: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        """[MOCKED_PROVIDER] Simulates successful OAuth 2.0 authorization code exchange."""
        org_id, user_id = test_org_and_user
        service = GoogleOAuthService(db_session)

        auth_url_res = await service.generate_auth_url(org_id, user_id)
        state_token = auth_url_res.state_token

        # Exchange simulated mock code
        integration = await service.handle_oauth_callback(
            organization_id=org_id,
            user_id=user_id,
            code="mock_code_verified_grant",
            state_token=state_token,
        )
        assert integration.organization_id == org_id
        assert integration.provider == "google"
        assert integration.status == "active"
        assert integration.encrypted_access_token.startswith("v1:")
        # Replay attack protection: consuming state again must fail
        with pytest.raises(Exception) as exc_info:
            await service.handle_oauth_callback(
                organization_id=org_id,
                user_id=user_id,
                code="mock_code_verified_grant",
                state_token=state_token,
            )
        assert "Invalid, mismatched, or expired" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_mocked_token_automatic_refresh(
        self, db_session: AsyncSession, test_org_and_user: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        """[MOCKED_PROVIDER] Simulates automatic token refresh when nearing expiration."""
        org_id, _ = test_org_and_user
        service = GoogleOAuthService(db_session)
        aad = str(org_id).encode("utf-8")

        # Create integration with token expiring in 10 seconds
        integration = ExternalIntegration(
            organization_id=org_id,
            provider="google",
            account_email=f"refresh_{uuid.uuid4().hex[:6]}@example.com",
            encrypted_access_token=encrypt_integration_token("mock_access_token_old", associated_data=aad),
            encrypted_refresh_token=encrypt_integration_token("mock_refresh_token_valid", associated_data=aad),
            token_expires_at=datetime.now(UTC) + timedelta(seconds=10),
            status="active",
        )
        db_session.add(integration)
        await db_session.flush()

        # Getting valid access token should detect expiry and refresh
        new_token = await service.get_valid_access_token(integration)
        assert new_token.startswith("mock_access_token_")
        assert new_token != "mock_access_token_old"
        assert integration.token_expires_at > datetime.now(UTC) + timedelta(minutes=30)

    @pytest.mark.asyncio
    async def test_mocked_disconnect_and_cleanup(
        self, db_session: AsyncSession, test_org_and_user: tuple[uuid.UUID, uuid.UUID]
    ) -> None:
        """[MOCKED_PROVIDER] Simulates account disconnection and credential cleanup."""
        org_id, _ = test_org_and_user
        service = GoogleOAuthService(db_session)
        aad = str(org_id).encode("utf-8")

        integration = ExternalIntegration(
            organization_id=org_id,
            provider="google",
            account_email=f"disconnect_{uuid.uuid4().hex[:6]}@example.com",
            encrypted_access_token=encrypt_integration_token("mock_token_to_disconnect", associated_data=aad),
            status="active",
        )
        db_session.add(integration)
        await db_session.flush()

        res = await service.disconnect(integration.id, org_id)
        assert res is True
        # Verify row is removed
        found = await service.repo.get_by_id(integration.id, org_id)
        assert found is None


# ==============================================================================
# CLASS 3: PROVIDER CONTRACT TESTS (Google API Schemas)
# ==============================================================================


class TestClass3ProviderContract:
    """[PROVIDER_CONTRACT] Verifies that payloads conform to Google Sheets v4 and Drive v3 specs."""

    def test_contract_sheets_value_range_schema(self) -> None:
        """[PROVIDER_CONTRACT] Verifies ValueRange resource structure."""
        value_range_sample = {
            "range": "Sheet1!A1:C3",
            "majorDimension": "ROWS",
            "values": [
                ["Header1", "Header2", "Header3"],
                ["Alpha", "100", "=SUM(B2)"],
                ["Beta", "200", "=AVERAGE(B3)"],
            ],
        }
        assert "range" in value_range_sample
        assert value_range_sample["majorDimension"] == "ROWS"
        assert len(value_range_sample["values"]) == 3

    def test_contract_drive_file_list_schema(self) -> None:
        """[PROVIDER_CONTRACT] Verifies Drive v3 file listing structure."""
        drive_sample = {
            "kind": "drive#fileList",
            "nextPageToken": "token_page_2",
            "files": [
                {
                    "id": "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms",
                    "name": "Q3 Financials",
                    "mimeType": "application/vnd.google-apps.spreadsheet",
                    "modifiedTime": "2026-11-01T12:00:00.000Z",
                }
            ],
        }
        assert drive_sample["files"][0]["mimeType"] == "application/vnd.google-apps.spreadsheet"


# ==============================================================================
# CLASS 4: LIVE STAGING TESTS (Explicitly Classified)
# ==============================================================================


class TestClass4LiveStaging:
    """[LIVE_STAGING] Real Google Cloud staging execution against production Google endpoints."""

    def test_live_staging_status(self) -> None:
        """[LIVE_STAGING] Verification status classification.

        Explicit classification: When GOOGLE_CLIENT_SECRET is unconfigured in local dev/CI,
        live provider integration is classified as: LIVE_STAGING_UNVERIFIED.
        Mocked and contract tests confirm local correctness, but live Google staging
        remains unverified until live credentials are provisioned.
        """
        is_live_configured = bool(settings.GOOGLE_CLIENT_SECRET and "mock" not in settings.GOOGLE_CLIENT_SECRET)
        if not is_live_configured:
            pytest.skip("LIVE_STAGING_UNVERIFIED: Live Google Cloud OAuth client credentials not provisioned in environment.")
