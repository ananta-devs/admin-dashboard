"""Google OAuth 2.0 integration service for Google Sheets and Drive APIs."""

from __future__ import annotations

import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import EntityNotFoundError, ValidationError
from app.core.logging import logger
from app.core.security import (
    decrypt_integration_token,
    encrypt_integration_token,
    generate_secure_token,
)
from app.db.transaction import transaction
from app.models.integration import ExternalIntegration, OAuthState
from app.repositories.integration import IntegrationRepository
from app.schemas.integration import (
    GoogleAuthUrlResponse,
    GoogleSheetTabItem,
    GoogleSpreadsheetDetail,
    GoogleSpreadsheetItem,
    GoogleSpreadsheetValuesResponse,
)

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_DRIVE_FILES_ENDPOINT = "https://www.googleapis.com/drive/v3/files"
GOOGLE_SHEETS_BASE_ENDPOINT = "https://sheets.googleapis.com/v4/spreadsheets"


def get_required_google_scopes() -> list[str]:
    """Returns exact Google OAuth 2.0 scopes, allowing selection of drive.file vs drive.readonly."""
    drive_scope = (
        "https://www.googleapis.com/auth/drive.file"
        if getattr(settings, "GOOGLE_OAUTH_USE_DRIVE_FILE", False)
        else "https://www.googleapis.com/auth/drive.readonly"
    )
    return [
        "https://www.googleapis.com/auth/spreadsheets",
        drive_scope,
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]


REQUIRED_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]


class GoogleOAuthService:
    """Orchestrates Google OAuth 2.0 authentication, token lifecycle, and Sheets API interactions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = IntegrationRepository(session)

    async def generate_auth_url(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        redirect_uri: str | None = None,
    ) -> GoogleAuthUrlResponse:
        """Create secure state token and return authorization URL."""
        if not settings.GOOGLE_CLIENT_ID:
            logger.warning("GOOGLE_CLIENT_ID not configured; using development placeholder.")

        client_id = settings.GOOGLE_CLIENT_ID or "mock-client-id.apps.googleusercontent.com"
        eff_redirect_uri = redirect_uri or settings.GOOGLE_REDIRECT_URI

        state_token = generate_secure_token(32)
        expires_at = datetime.now(UTC) + timedelta(minutes=15)

        oauth_state = OAuthState(
            organization_id=organization_id,
            user_id=user_id,
            provider="google",
            state_token=state_token,
            redirect_uri=eff_redirect_uri,
            expires_at=expires_at,
        )
        async with transaction(self.session):
            await self.repo.create_oauth_state(oauth_state)

        params = {
            "client_id": client_id,
            "redirect_uri": eff_redirect_uri,
            "response_type": "code",
            "scope": " ".join(REQUIRED_GOOGLE_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state_token,
            "include_granted_scopes": "true",
        }
        auth_url = f"{GOOGLE_AUTH_ENDPOINT}?{urllib.parse.urlencode(params)}"
        return GoogleAuthUrlResponse(
            auth_url=auth_url,
            state_token=state_token,
            expires_at=expires_at,
        )

    async def handle_oauth_callback(
        self,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        code: str,
        state_token: str,
        redirect_uri: str | None = None,
    ) -> ExternalIntegration:
        """Validate state token, exchange code for tokens, fetch profile, and persist integration."""
        async with transaction(self.session):
            oauth_state = await self.repo.consume_oauth_state(state_token, organization_id)
            if not oauth_state:
                raise ValidationError("Invalid, mismatched, or expired OAuth state token.")

            eff_redirect_uri = redirect_uri or oauth_state.redirect_uri

            # 1. Exchange code for access & refresh tokens
            client_id = settings.GOOGLE_CLIENT_ID or "mock-client-id.apps.googleusercontent.com"
            client_secret = settings.GOOGLE_CLIENT_SECRET or "mock-client-secret"

            token_data: dict[str, Any]
            user_info: dict[str, Any]

            # Allow test environments to pass mock tokens or execute mock flows
            if code.startswith("mock_code_"):
                token_data = {
                    "access_token": f"mock_access_token_{uuid.uuid4().hex[:16]}",
                    "refresh_token": f"mock_refresh_token_{uuid.uuid4().hex[:16]}",
                    "expires_in": 3600,
                    "scope": " ".join(REQUIRED_GOOGLE_SCOPES),
                }
                user_info = {
                    "email": f"google_user_{uuid.uuid4().hex[:6]}@example.com",
                    "name": "Google Workspace User",
                    "picture": "https://lh3.googleusercontent.com/mock-avatar",
                }
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    token_res = await client.post(
                        GOOGLE_TOKEN_ENDPOINT,
                        data={
                            "code": code,
                            "client_id": client_id,
                            "client_secret": client_secret,
                            "redirect_uri": eff_redirect_uri,
                            "grant_type": "authorization_code",
                        },
                    )
                    if token_res.status_code != 200:
                        raise ValidationError(
                            f"Google OAuth token exchange failed: {token_res.text}"
                        )
                    token_data = token_res.json()

                    access_token = token_data.get("access_token")
                    userinfo_res = await client.get(
                        GOOGLE_USERINFO_ENDPOINT,
                        headers={"Authorization": f"Bearer {access_token}"},
                    )
                    if userinfo_res.status_code != 200:
                        raise ValidationError(
                            f"Failed to fetch Google user profile: {userinfo_res.text}"
                        )
                    user_info = userinfo_res.json()

            account_email = user_info.get("email")
            if not account_email:
                raise ValidationError("Google account email could not be determined.")

            raw_access_token = token_data["access_token"]
            raw_refresh_token = token_data.get("refresh_token")
            expires_in = int(token_data.get("expires_in", 3600))
            token_expires_at = datetime.now(UTC) + timedelta(seconds=expires_in)
            granted_scopes = token_data.get("scope", "").split() or REQUIRED_GOOGLE_SCOPES

            aad = str(organization_id).encode("utf-8")
            encrypted_access = encrypt_integration_token(raw_access_token, associated_data=aad)
            encrypted_refresh = (
                encrypt_integration_token(raw_refresh_token, associated_data=aad)
                if raw_refresh_token
                else None
            )

            existing = await self.repo.get_by_provider(organization_id, "google")
            if existing and existing.account_email == account_email:
                existing.encrypted_access_token = encrypted_access
                if encrypted_refresh:
                    existing.encrypted_refresh_token = encrypted_refresh
                existing.token_expires_at = token_expires_at
                existing.authorized_scopes = granted_scopes
                existing.status = "active"
                existing.last_used_at = datetime.now(UTC)
                existing.account_name = user_info.get("name")
                existing.account_avatar_url = user_info.get("picture")
                return existing

            integration = ExternalIntegration(
                organization_id=organization_id,
                connected_by_user_id=user_id,
                provider="google",
                account_email=account_email,
                account_name=user_info.get("name"),
                account_avatar_url=user_info.get("picture"),
                encrypted_access_token=encrypted_access,
                encrypted_refresh_token=encrypted_refresh,
                token_expires_at=token_expires_at,
                authorized_scopes=granted_scopes,
                status="active",
                last_used_at=datetime.now(UTC),
            )
            return await self.repo.create(integration)

    async def get_valid_access_token(self, integration: ExternalIntegration) -> str:
        """Decrypt access token, refreshing automatically via refresh token if nearing expiration."""
        now = datetime.now(UTC)
        is_expired = integration.token_expires_at is not None and integration.token_expires_at <= (
            now + timedelta(seconds=60)
        )

        aad = str(integration.organization_id).encode("utf-8")
        if not is_expired:
            return decrypt_integration_token(
                integration.encrypted_access_token, associated_data=aad
            )

        # Token is expired; attempt refresh
        if not integration.encrypted_refresh_token:
            integration.status = "expired"
            await self.session.flush()
            raise ValidationError(
                "Google integration token expired and no refresh token available. Reconnect account."
            )

        refresh_token = decrypt_integration_token(
            integration.encrypted_refresh_token, associated_data=aad
        )
        client_id = settings.GOOGLE_CLIENT_ID or "mock-client-id.apps.googleusercontent.com"
        client_secret = settings.GOOGLE_CLIENT_SECRET or "mock-client-secret"

        if refresh_token.startswith("mock_refresh_token_"):
            new_access_token = f"mock_access_token_{uuid.uuid4().hex[:16]}"
            new_expires_at = now + timedelta(seconds=3600)
        else:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    GOOGLE_TOKEN_ENDPOINT,
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                )
                if res.status_code != 200:
                    integration.status = "expired"
                    await self.session.flush()
                    raise ValidationError(f"Google token refresh failed: {res.text}")
                data = res.json()
                new_access_token = data["access_token"]
                expires_in = int(data.get("expires_in", 3600))
                new_expires_at = now + timedelta(seconds=expires_in)

        integration.encrypted_access_token = encrypt_integration_token(
            new_access_token, associated_data=aad
        )
        integration.token_expires_at = new_expires_at
        integration.status = "active"
        integration.last_used_at = now
        await self.session.flush()
        return new_access_token

    async def disconnect(self, integration_id: uuid.UUID, organization_id: uuid.UUID) -> bool:
        """Revoke Google credentials if possible and remove integration."""
        integration = await self.repo.get_by_id(integration_id, organization_id)
        if not integration:
            raise EntityNotFoundError("ExternalIntegration", str(integration_id))

        aad = str(organization_id).encode("utf-8")
        try:
            token = decrypt_integration_token(
                integration.encrypted_access_token, associated_data=aad
            )
            if not token.startswith("mock_"):
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(GOOGLE_REVOKE_ENDPOINT, params={"token": token})
        except Exception as e:
            logger.warning(f"Error revoking Google token: {e}")

        async with transaction(self.session):
            return await self.repo.delete(integration_id, organization_id)

    async def list_spreadsheets(
        self,
        integration_id: uuid.UUID,
        organization_id: uuid.UUID,
        page_size: int = 20,
    ) -> list[GoogleSpreadsheetItem]:
        """Browse Google Drive to list spreadsheets accessible to the connected account."""
        integration = await self.repo.get_by_id(integration_id, organization_id)
        if not integration:
            raise EntityNotFoundError("ExternalIntegration", str(integration_id))

        access_token = await self.get_valid_access_token(integration)

        if access_token.startswith("mock_"):
            return [
                GoogleSpreadsheetItem(
                    id="mock_sheet_1",
                    name="Quarterly Financials 2026",
                    modified_time="2026-10-15T12:00:00Z",
                ),
                GoogleSpreadsheetItem(
                    id="mock_sheet_2",
                    name="Marketing Campaign Performance",
                    modified_time="2026-10-20T14:30:00Z",
                ),
                GoogleSpreadsheetItem(
                    id="mock_sheet_3",
                    name="Customer Churn & Retention Model",
                    modified_time="2026-10-28T09:15:00Z",
                ),
            ]

        query = "mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                GOOGLE_DRIVE_FILES_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
                params={
                    "q": query,
                    "fields": "files(id, name, modifiedTime)",
                    "pageSize": page_size,
                    "orderBy": "modifiedTime desc",
                },
            )
            if res.status_code != 200:
                raise ValidationError(f"Google Drive API error: {res.text}")

            files = res.json().get("files", [])
            return [
                GoogleSpreadsheetItem(
                    id=f["id"],
                    name=f["name"],
                    modified_time=f.get("modifiedTime"),
                )
                for f in files
            ]

    async def get_spreadsheet_metadata(
        self,
        integration_id: uuid.UUID,
        organization_id: uuid.UUID,
        external_spreadsheet_id: str,
    ) -> GoogleSpreadsheetDetail:
        """Fetch title and worksheets (tabs) specification for a Google Spreadsheet."""
        integration = await self.repo.get_by_id(integration_id, organization_id)
        if not integration:
            raise EntityNotFoundError("ExternalIntegration", str(integration_id))

        access_token = await self.get_valid_access_token(integration)

        if access_token.startswith("mock_"):
            return GoogleSpreadsheetDetail(
                spreadsheet_id=external_spreadsheet_id,
                title="Mock Spreadsheet Model",
                sheets=[
                    GoogleSheetTabItem(sheet_id=0, title="Summary", row_count=100, column_count=10),
                    GoogleSheetTabItem(
                        sheet_id=1, title="Revenue Details", row_count=500, column_count=15
                    ),
                    GoogleSheetTabItem(
                        sheet_id=2, title="Raw Data", row_count=2000, column_count=8
                    ),
                ],
            )

        url = f"{GOOGLE_SHEETS_BASE_ENDPOINT}/{external_spreadsheet_id}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                params={"fields": "spreadsheetId,properties.title,sheets.properties"},
            )
            if res.status_code != 200:
                raise ValidationError(f"Google Sheets API error: {res.text}")

            data = res.json()
            sheets = []
            for s in data.get("sheets", []):
                p = s.get("properties", {})
                grid = p.get("gridProperties", {})
                sheets.append(
                    GoogleSheetTabItem(
                        sheet_id=p.get("sheetId", 0),
                        title=p.get("title", "Sheet"),
                        row_count=grid.get("rowCount", 1000),
                        column_count=grid.get("columnCount", 26),
                    )
                )

            return GoogleSpreadsheetDetail(
                spreadsheet_id=data.get("spreadsheetId", external_spreadsheet_id),
                title=data.get("properties", {}).get("title", "Untitled Spreadsheet"),
                sheets=sheets,
            )

    async def get_sheet_values(
        self,
        integration_id: uuid.UUID,
        organization_id: uuid.UUID,
        external_spreadsheet_id: str,
        range_name: str,
    ) -> GoogleSpreadsheetValuesResponse:
        """Fetch cell values in A1 notation from a Google Spreadsheet worksheet."""
        integration = await self.repo.get_by_id(integration_id, organization_id)
        if not integration:
            raise EntityNotFoundError("ExternalIntegration", str(integration_id))

        access_token = await self.get_valid_access_token(integration)

        if access_token.startswith("mock_"):
            mock_values: list[list[Any]] = [
                ["Date", "Region", "Category", "Revenue", "Cost", "Margin"],
                ["2026-01-01", "North America", "Enterprise", 15000.0, 7500.0, 0.50],
                ["2026-01-02", "Europe", "Mid-Market", 9800.0, 4200.0, 0.57],
                ["2026-01-03", "Asia-Pacific", "Enterprise", 22000.0, 11000.0, 0.50],
                ["2026-01-04", "North America", "SMB", 4500.0, 2000.0, 0.55],
            ]
            return GoogleSpreadsheetValuesResponse(
                spreadsheet_id=external_spreadsheet_id,
                sheet_name=range_name.split("!")[0] if "!" in range_name else range_name,
                values=mock_values,
                total_rows=len(mock_values),
                total_columns=len(mock_values[0]) if mock_values else 0,
            )

        encoded_range = urllib.parse.quote(range_name)
        url = f"{GOOGLE_SHEETS_BASE_ENDPOINT}/{external_spreadsheet_id}/values/{encoded_range}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                params={"valueRenderOption": "UNFORMATTED_VALUE"},
            )
            if res.status_code != 200:
                raise ValidationError(f"Google Sheets values API error: {res.text}")

            raw_data = res.json()
            raw_values = raw_data.get("values", [])
            values: list[list[Any]] = [row if isinstance(row, list) else [] for row in raw_values]
            max_cols = max((len(row) for row in values), default=0)
            return GoogleSpreadsheetValuesResponse(
                spreadsheet_id=external_spreadsheet_id,
                sheet_name=range_name.split("!")[0] if "!" in range_name else range_name,
                values=values,
                total_rows=len(values),
                total_columns=max_cols,
            )

    async def update_sheet_values(
        self,
        integration_id: uuid.UUID,
        organization_id: uuid.UUID,
        external_spreadsheet_id: str,
        range_name: str,
        values: list[list[Any]],
    ) -> dict[str, Any]:
        """Write tabular cell values to an existing Google Spreadsheet worksheet."""
        integration = await self.repo.get_by_id(integration_id, organization_id)
        if not integration:
            raise EntityNotFoundError("ExternalIntegration", str(integration_id))

        access_token = await self.get_valid_access_token(integration)

        if access_token.startswith("mock_"):
            return {
                "updatedRange": range_name,
                "updatedRows": len(values),
                "updatedColumns": max((len(r) for r in values), default=0),
                "updatedCells": sum(len(r) for r in values),
            }

        encoded_range = urllib.parse.quote(range_name)
        url = f"{GOOGLE_SHEETS_BASE_ENDPOINT}/{external_spreadsheet_id}/values/{encoded_range}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.put(
                url,
                headers={"Authorization": f"Bearer {access_token}"},
                params={"valueInputOption": "USER_ENTERED"},
                json={"values": values},
            )
            if res.status_code != 200:
                raise ValidationError(f"Google Sheets values update API error: {res.text}")
            return res.json()

    async def create_spreadsheet(
        self,
        integration_id: uuid.UUID,
        organization_id: uuid.UUID,
        title: str,
        sheet_titles: list[str] | None = None,
    ) -> GoogleSpreadsheetDetail:
        """Create a new Google Spreadsheet on the user's connected Google account."""
        integration = await self.repo.get_by_id(integration_id, organization_id)
        if not integration:
            raise EntityNotFoundError("ExternalIntegration", str(integration_id))

        access_token = await self.get_valid_access_token(integration)

        if access_token.startswith("mock_"):
            titles = sheet_titles or ["Sheet1"]
            return GoogleSpreadsheetDetail(
                spreadsheet_id=f"created_{uuid.uuid4().hex[:12]}",
                title=title,
                sheets=[
                    GoogleSheetTabItem(sheet_id=idx, title=t, row_count=1000, column_count=26)
                    for idx, t in enumerate(titles)
                ],
            )

        payload: dict[str, Any] = {
            "properties": {"title": title},
        }
        if sheet_titles:
            payload["sheets"] = [{"properties": {"title": st}} for st in sheet_titles]

        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post(
                GOOGLE_SHEETS_BASE_ENDPOINT,
                headers={"Authorization": f"Bearer {access_token}"},
                json=payload,
            )
            if res.status_code != 200:
                raise ValidationError(f"Google Spreadsheet creation failed: {res.text}")

            data = res.json()
            sheets = []
            for s in data.get("sheets", []):
                p = s.get("properties", {})
                grid = p.get("gridProperties", {})
                sheets.append(
                    GoogleSheetTabItem(
                        sheet_id=p.get("sheetId", 0),
                        title=p.get("title", "Sheet"),
                        row_count=grid.get("rowCount", 1000),
                        column_count=grid.get("columnCount", 26),
                    )
                )

            return GoogleSpreadsheetDetail(
                spreadsheet_id=data.get("spreadsheetId"),
                title=data.get("properties", {}).get("title", title),
                sheets=sheets,
            )
