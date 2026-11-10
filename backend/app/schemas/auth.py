import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.organization import OrganizationRead, validate_slug_format
from app.schemas.user import UserRead
from app.schemas.workspace import WorkspaceRead


class RegisterInput(BaseModel):
    """Payload for self-service account and initial organization registration."""

    email: EmailStr
    password: str = Field(min_length=12, max_length=1024)
    display_name: str = Field(min_length=1, max_length=255)
    organization_name: str = Field(min_length=1, max_length=255)
    organization_slug: str = Field(min_length=2, max_length=100)
    default_workspace_name: str = Field(default="Default Workspace", min_length=1, max_length=255)
    default_workspace_slug: str = Field(default="default", min_length=2, max_length=100)

    model_config = ConfigDict(str_strip_whitespace=True)

    def validate_slugs(self) -> None:
        validate_slug_format(self.organization_slug)
        validate_slug_format(self.default_workspace_slug)


class LoginInput(BaseModel):
    """Credentials payload for email & password authentication."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class VerifyEmailInput(BaseModel):
    """Payload to verify an account email address using a single-use token."""

    token: str = Field(min_length=16, max_length=256)


class ResendVerificationInput(BaseModel):
    """Payload to request a fresh verification link."""

    email: EmailStr


class ForgotPasswordInput(BaseModel):
    """Payload to request password reset instructions."""

    email: EmailStr


class ResetPasswordInput(BaseModel):
    """Payload to complete password reset using a single-use action token."""

    token: str = Field(min_length=16, max_length=256)
    new_password: str = Field(min_length=12, max_length=1024)


class ChangePasswordInput(BaseModel):
    """Payload for authenticated users to update their password."""

    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)
    code: str | None = Field(default=None, min_length=6, max_length=8)
    recovery_code: str | None = Field(default=None, min_length=8, max_length=32)


class UpdateProfileInput(BaseModel):
    """Payload to update personal profile information."""

    display_name: str = Field(min_length=1, max_length=255)

    model_config = ConfigDict(str_strip_whitespace=True)


class SelectActiveOrganizationInput(BaseModel):
    """Payload to set the active tenant context for the current session."""

    organization_id: uuid.UUID


class MfaVerifyInput(BaseModel):
    """Payload to complete an active MFA login challenge."""

    challenge_token: str = Field(min_length=16, max_length=256)
    code: str | None = Field(default=None, min_length=6, max_length=8)
    recovery_code: str | None = Field(default=None, min_length=8, max_length=32)


class MfaTotpSetupInput(BaseModel):
    """Payload to initiate TOTP enrollment requiring recent password verification."""

    password: str = Field(min_length=1, max_length=1024)


class MfaTotpConfirmInput(BaseModel):
    """Payload to confirm TOTP enrollment with current password and authenticator code."""

    password: str = Field(min_length=1, max_length=1024)
    code: str = Field(min_length=6, max_length=8)


class MfaTotpDisableInput(BaseModel):
    """Payload to disable TOTP requiring password verification and existing MFA factor."""

    password: str = Field(min_length=1, max_length=1024)
    code: str | None = Field(default=None, min_length=6, max_length=8)
    recovery_code: str | None = Field(default=None, min_length=8, max_length=32)


class MfaRecoveryCodesRegenerateInput(BaseModel):
    """Payload to invalidate and regenerate backup recovery codes requiring reauthentication."""

    password: str = Field(min_length=1, max_length=1024)
    code: str | None = Field(default=None, min_length=6, max_length=8)
    recovery_code: str | None = Field(default=None, min_length=8, max_length=32)


# --- Response Schemas ---


class MessageResponse(BaseModel):
    """Generic status response."""

    message: str


class RegisterResponse(BaseModel):
    """Response returned upon successful self-service registration."""

    message: str
    user: UserRead
    organization: OrganizationRead
    workspace: WorkspaceRead


class AuthResponse(BaseModel):
    """Public authentication state returned upon login or identity queries."""

    success: bool = True
    user: UserRead
    active_organization: OrganizationRead | None = None
    csrf_token: str | None = None

    model_config = ConfigDict(from_attributes=True)


class MfaChallengeResponse(BaseModel):
    """Response returned when primary credentials succeed but MFA challenge is required."""

    mfa_required: bool = True
    challenge_token: str
    expires_in_seconds: int = 300


class MfaEnrollmentResponse(BaseModel):
    """Response returning pending TOTP secret and provisioning URI."""

    secret: str
    provisioning_uri: str


class MfaRecoveryCodesResponse(BaseModel):
    """Response displaying newly generated plaintext recovery codes (once only)."""

    recovery_codes: list[str]


class SessionRead(BaseModel):
    """Public representation of an active authentication session."""

    id: uuid.UUID
    created_at: datetime
    last_seen_at: datetime
    user_agent: str | None = None
    ip_address: str | None = None
    is_current: bool = False

    model_config = ConfigDict(from_attributes=True)
