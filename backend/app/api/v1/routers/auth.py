import uuid
from datetime import UTC, datetime
from typing import Literal, cast

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import (
    get_current_session,
    verify_auth_request_origin,
    verify_csrf_bootstrap_origin,
    verify_csrf_token,
)
from app.core.config import settings
from app.core.exceptions import (
    AuthenticationError,
    EmailNotVerifiedError,
    EntityNotFoundError,
    ForbiddenError,
    InvalidTokenError,
)
from app.core.rate_limit import check_rate_limit
from app.core.security import validate_password_policy
from app.db.session import get_db_session
from app.db.transaction import transaction
from app.models.session import AuthenticationSession
from app.repositories.membership import MembershipRepository
from app.repositories.user import UserRepository
from app.schemas.auth import (
    AuthResponse,
    ChangePasswordInput,
    ForgotPasswordInput,
    LoginInput,
    MessageResponse,
    MfaChallengeResponse,
    MfaEnrollmentResponse,
    MfaRecoveryCodesRegenerateInput,
    MfaRecoveryCodesResponse,
    MfaTotpConfirmInput,
    MfaTotpDisableInput,
    MfaTotpSetupInput,
    MfaVerifyInput,
    RegisterInput,
    RegisterResponse,
    ResendVerificationInput,
    ResetPasswordInput,
    SelectActiveOrganizationInput,
    SessionRead,
    UpdateProfileInput,
    VerifyEmailInput,
)
from app.schemas.organization import OrganizationRead
from app.schemas.user import UserRead, normalize_email_address
from app.services.action_token import ActionTokenService
from app.services.credentials import CredentialsService
from app.services.email import EmailService
from app.services.mfa import MfaService
from app.services.organization import OrganizationService
from app.services.session import SessionService

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_auth_cookies(response: Response, raw_session_token: str) -> None:
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=raw_session_token,
        max_age=settings.SESSION_ABSOLUTE_TIMEOUT_SECONDS,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=cast(Literal["lax", "strict", "none"], settings.SESSION_COOKIE_SAMESITE),
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(settings.SESSION_COOKIE_NAME, path="/")
    if settings.SESSION_COOKIE_NAME != "vibe_session":
        # Clean up legacy fallback cookie if present from previous sessions
        response.delete_cookie("vibe_session", path="/")


# 1. Register
@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=RegisterResponse,
    dependencies=[Depends(verify_auth_request_origin)],
)
async def register(
    input_data: RegisterInput,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> RegisterResponse:
    input_data.validate_slugs()

    # Rate limiting
    await check_rate_limit(
        request=request,
        action="register",
        limit=settings.RATE_LIMIT_REGISTER_MAX_REQUESTS,
        window_seconds=settings.RATE_LIMIT_REGISTER_WINDOW_SECONDS,
        identifier=input_data.email,
    )

    org_service = OrganizationService(db)
    user, org, workspace, raw_token = await org_service.register_user_and_organization_atomic(
        email=input_data.email,
        password=input_data.password,
        display_name=input_data.display_name,
        organization_name=input_data.organization_name,
        organization_slug=input_data.organization_slug,
        default_workspace_name=input_data.default_workspace_name,
        default_workspace_slug=input_data.default_workspace_slug,
    )

    # Dispatch verification email outside DB transaction
    email_service = EmailService()
    await email_service.send_verification_email(
        to_email=user.email,
        display_name=user.display_name,
        token=raw_token,
    )

    return RegisterResponse(
        message="Account created successfully. Please verify your email.",
        user=user,
        organization=org,
        workspace=workspace,
    )


# 2. Login
@router.post(
    "/login",
    response_model=AuthResponse | MfaChallengeResponse,
    dependencies=[Depends(verify_auth_request_origin)],
)
async def login(
    input_data: LoginInput,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
) -> AuthResponse | MfaChallengeResponse:
    # Rate limit check per IP and per account
    await check_rate_limit(
        request=request,
        action="login",
        limit=settings.RATE_LIMIT_LOGIN_MAX_REQUESTS,
        window_seconds=settings.RATE_LIMIT_LOGIN_WINDOW_SECONDS,
        identifier=input_data.email,
    )

    cred_service = CredentialsService(db)
    user = await cred_service.authenticate(input_data.email, input_data.password)
    if not user:
        raise AuthenticationError("Invalid email or password")

    if settings.EMAIL_VERIFICATION_REQUIRED and user.email_verified_at is None:
        raise EmailNotVerifiedError("Email address has not been verified")

    mfa_service = MfaService(db)
    if await mfa_service.is_mfa_enabled(user.id):
        async with transaction(db):
            challenge_token, _ = await mfa_service.create_login_challenge(user.id)
        return MfaChallengeResponse(
            mfa_required=True,
            challenge_token=challenge_token,
            expires_in_seconds=settings.MFA_CHALLENGE_LIFETIME_SECONDS,
        )

    # Create server session
    membership_repo = MembershipRepository(db)
    memberships = await membership_repo.list_by_user(user.id, limit=100)
    primary_org_id = memberships[0].organization_id if len(memberships) == 1 else None

    session_service = SessionService(db)
    user_agent = request.headers.get("User-Agent")
    ip_addr = request.client.host if request.client else None

    async with transaction(db):
        auth_session, raw_session_token, raw_csrf = await session_service.create_session(
            user_id=user.id,
            user_agent=user_agent,
            ip_address=ip_addr,
            active_organization_id=primary_org_id,
        )

    _set_auth_cookies(response, raw_session_token)

    return AuthResponse(
        success=True,
        user=UserRead.model_validate(user),
        active_organization=OrganizationRead.model_validate(auth_session.active_organization)
        if auth_session.active_organization
        else None,
        csrf_token=raw_csrf,
    )


# 3. Login MFA
@router.post(
    "/login/mfa",
    response_model=AuthResponse,
    dependencies=[Depends(verify_auth_request_origin)],
)
async def login_mfa(
    input_data: MfaVerifyInput,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
) -> AuthResponse:
    await check_rate_limit(
        request=request,
        action="mfa_verify",
        limit=settings.RATE_LIMIT_MFA_MAX_REQUESTS,
        window_seconds=settings.RATE_LIMIT_MFA_WINDOW_SECONDS,
    )

    mfa_service = MfaService(db)
    session_service = SessionService(db)
    user_repo = UserRepository(db)
    membership_repo = MembershipRepository(db)

    async with transaction(db):
        user_id: uuid.UUID | None = None
        if input_data.code:
            user_id = await mfa_service.verify_challenge_totp(
                input_data.challenge_token, input_data.code
            )
        elif input_data.recovery_code:
            user_id = await mfa_service.verify_challenge_recovery_code(
                input_data.challenge_token, input_data.recovery_code
            )
        else:
            raise AuthenticationError("Either TOTP code or recovery code must be provided")

        if not user_id:
            raise AuthenticationError("Invalid or expired MFA code")

        user = await user_repo.get_by_id(user_id)
        if not user or not user.is_active:
            raise AuthenticationError("Account is inactive")

        memberships = await membership_repo.list_by_user(user.id, limit=100)
        primary_org_id = memberships[0].organization_id if len(memberships) == 1 else None

        auth_session, raw_session_token, raw_csrf = await session_service.create_session(
            user_id=user.id,
            user_agent=request.headers.get("User-Agent"),
            ip_address=request.client.host if request.client else None,
            active_organization_id=primary_org_id,
        )

    _set_auth_cookies(response, raw_session_token)

    return AuthResponse(
        success=True,
        user=UserRead.model_validate(user),
        active_organization=OrganizationRead.model_validate(auth_session.active_organization)
        if auth_session.active_organization
        else None,
        csrf_token=raw_csrf,
    )


# 4. Logout
@router.post(
    "/logout",
    response_model=MessageResponse,
    dependencies=[Depends(verify_csrf_token)],
)
async def logout(
    response: Response,
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> MessageResponse:
    session_service = SessionService(db)
    async with transaction(db):
        await session_service.revoke_session(session.id)
    _clear_auth_cookies(response)
    return MessageResponse(message="Logged out successfully")


# 5. Logout All
@router.post(
    "/logout-all",
    response_model=MessageResponse,
    dependencies=[Depends(verify_csrf_token)],
)
async def logout_all(
    response: Response,
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> MessageResponse:
    session_service = SessionService(db)
    async with transaction(db):
        await session_service.revoke_all_for_user(session.user_id)
    _clear_auth_cookies(response)
    return MessageResponse(message="All sessions revoked")


# 6. Current User Me
@router.get("/me", response_model=AuthResponse)
async def me(
    session: AuthenticationSession = Depends(get_current_session),
) -> AuthResponse:
    return AuthResponse(
        success=True,
        user=UserRead.model_validate(session.user),
        active_organization=OrganizationRead.model_validate(session.active_organization)
        if session.active_organization
        else None,
    )


@router.patch("/me", response_model=AuthResponse, dependencies=[Depends(verify_csrf_token)])
async def update_me(
    input_data: UpdateProfileInput,
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> AuthResponse:
    user_repo = UserRepository(db)
    user = await user_repo.get_by_id(session.user_id)
    if not user:
        raise EntityNotFoundError("User", str(session.user_id))
    async with transaction(db):
        user.display_name = input_data.display_name.strip()
    return AuthResponse(
        success=True,
        user=UserRead.model_validate(user),
        active_organization=OrganizationRead.model_validate(session.active_organization)
        if session.active_organization
        else None,
    )


# 7. CSRF Token (POST required for token rotation; GET is rejected as unsafe)
@router.post(
    "/csrf-token",
    dependencies=[Depends(verify_csrf_bootstrap_origin)],
)
async def rotate_csrf_token(
    response: Response,
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, str]:
    # Strictly prevent caching of CSRF token responses
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"

    session_service = SessionService(db)
    async with transaction(db):
        new_csrf = await session_service.rotate_csrf_token(session)
    return {"csrf_token": new_csrf}


# 8. Verify Email
@router.post("/verify-email", response_model=MessageResponse)
async def verify_email(
    input_data: VerifyEmailInput,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> MessageResponse:
    await check_rate_limit(
        request=request,
        action="email_verify",
        limit=settings.RATE_LIMIT_EMAIL_VERIFY_MAX_REQUESTS,
        window_seconds=settings.RATE_LIMIT_EMAIL_VERIFY_WINDOW_SECONDS,
    )

    action_service = ActionTokenService(db)
    user_repo = UserRepository(db)

    async with transaction(db):
        token_rec = await action_service.consume_email_verification_token(input_data.token)
        if not token_rec:
            raise InvalidTokenError("Invalid or expired verification token")

        user = await user_repo.get_by_id(token_rec.user_id)
        if user:
            user.email_verified_at = datetime.now(UTC)

    return MessageResponse(message="Email verified successfully")


# 9. Resend Verification
@router.post("/resend-verification", response_model=MessageResponse)
async def resend_verification(
    input_data: ResendVerificationInput,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> MessageResponse:
    await check_rate_limit(
        request=request,
        action="resend_verify",
        limit=settings.RATE_LIMIT_EMAIL_VERIFY_MAX_REQUESTS,
        window_seconds=settings.RATE_LIMIT_EMAIL_VERIFY_WINDOW_SECONDS,
        identifier=input_data.email,
    )

    user_repo = UserRepository(db)
    normalized = normalize_email_address(input_data.email)
    user = await user_repo.get_by_normalized_email(normalized)
    if user and user.email_verified_at is None:
        action_service = ActionTokenService(db)
        async with transaction(db):
            raw_token = await action_service.create_email_verification_token(user.id)
        email_service = EmailService()
        await email_service.send_verification_email(user.email, user.display_name, raw_token)

    return MessageResponse(
        message="If that account exists and is unverified, a verification email has been sent"
    )


# 10. Forgot Password
@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    input_data: ForgotPasswordInput,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> MessageResponse:
    await check_rate_limit(
        request=request,
        action="forgot_password",
        limit=settings.RATE_LIMIT_PASSWORD_RESET_MAX_REQUESTS,
        window_seconds=settings.RATE_LIMIT_PASSWORD_RESET_WINDOW_SECONDS,
        identifier=input_data.email,
    )

    user_repo = UserRepository(db)
    normalized = normalize_email_address(input_data.email)
    user = await user_repo.get_by_normalized_email(normalized)
    if user and user.is_active:
        action_service = ActionTokenService(db)
        async with transaction(db):
            raw_token = await action_service.create_password_reset_token(user.id)
        email_service = EmailService()
        await email_service.send_password_reset_email(user.email, user.display_name, raw_token)

    return MessageResponse(
        message="If that account exists, password reset instructions have been sent"
    )


# 11. Reset Password
@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    input_data: ResetPasswordInput,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> MessageResponse:
    await check_rate_limit(
        request=request,
        action="reset_password",
        limit=settings.RATE_LIMIT_PASSWORD_RESET_MAX_REQUESTS,
        window_seconds=settings.RATE_LIMIT_PASSWORD_RESET_WINDOW_SECONDS,
    )

    validate_password_policy(input_data.new_password)

    action_service = ActionTokenService(db)
    cred_service = CredentialsService(db)
    session_service = SessionService(db)

    async with transaction(db):
        token_rec = await action_service.consume_password_reset_token(input_data.token)
        if not token_rec:
            raise InvalidTokenError("Invalid or expired password reset token")

        await cred_service.update_password(token_rec.user_id, input_data.new_password)
        # Invalidate all existing sessions upon password reset
        await session_service.revoke_all_for_user(token_rec.user_id)

    return MessageResponse(
        message="Password reset successfully. Please log in with your new password"
    )


# 12. Change Password
@router.post(
    "/change-password", response_model=MessageResponse, dependencies=[Depends(verify_csrf_token)]
)
async def change_password(
    input_data: ChangePasswordInput,
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> MessageResponse:
    cred_service = CredentialsService(db)
    user = await cred_service.authenticate(session.user.email, input_data.current_password)
    if not user:
        raise AuthenticationError("Current password is incorrect")

    mfa_service = MfaService(db)
    if await mfa_service.is_mfa_enabled(session.user_id):
        factor_verified = False
        if input_data.code:
            factor_verified = await mfa_service.verify_user_totp(session.user_id, input_data.code)
        elif input_data.recovery_code:
            async with transaction(db):
                factor_verified = await mfa_service.verify_and_consume_recovery_code(
                    session.user_id, input_data.recovery_code
                )
        if not factor_verified:
            raise AuthenticationError(
                "Valid MFA factor (code or recovery_code) is required to change password"
            )

    validate_password_policy(input_data.new_password)
    session_service = SessionService(db)

    async with transaction(db):
        await cred_service.update_password(session.user_id, input_data.new_password)
        # Revoke all other active sessions
        await session_service.revoke_all_for_user(session.user_id, except_session_id=session.id)

    return MessageResponse(message="Password changed successfully")


# 13. Setup TOTP MFA
@router.post(
    "/mfa/totp/setup",
    response_model=MfaEnrollmentResponse,
    dependencies=[Depends(verify_csrf_token)],
)
async def setup_totp(
    input_data: MfaTotpSetupInput,
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> MfaEnrollmentResponse:
    cred_service = CredentialsService(db)
    user = await cred_service.authenticate(session.user.email, input_data.password)
    if not user:
        raise AuthenticationError("Current password is incorrect")

    mfa_service = MfaService(db)
    if await mfa_service.is_mfa_enabled(session.user_id):
        raise AuthenticationError("Two-factor authentication is already enabled")

    async with transaction(db):
        secret, uri = await mfa_service.begin_totp_enrollment(
            user_id=session.user_id,
            user_email=session.user.email,
        )
    return MfaEnrollmentResponse(secret=secret, provisioning_uri=uri)


# 14. Confirm TOTP MFA
@router.post(
    "/mfa/totp/confirm",
    response_model=MfaRecoveryCodesResponse,
    dependencies=[Depends(verify_csrf_token)],
)
async def confirm_totp(
    input_data: MfaTotpConfirmInput,
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> MfaRecoveryCodesResponse:
    cred_service = CredentialsService(db)
    user = await cred_service.authenticate(session.user.email, input_data.password)
    if not user:
        raise AuthenticationError("Current password is incorrect")

    mfa_service = MfaService(db)
    async with transaction(db):
        recovery_codes = await mfa_service.confirm_totp_enrollment(session.user_id, input_data.code)
        if recovery_codes is None:
            raise AuthenticationError("Invalid authenticator code")

    return MfaRecoveryCodesResponse(recovery_codes=recovery_codes)


# 15. Disable TOTP MFA
@router.post(
    "/mfa/totp/disable",
    response_model=MessageResponse,
    dependencies=[Depends(verify_csrf_token)],
)
async def disable_totp(
    input_data: MfaTotpDisableInput,
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> MessageResponse:
    cred_service = CredentialsService(db)
    user = await cred_service.authenticate(session.user.email, input_data.password)
    if not user:
        raise AuthenticationError("Current password is incorrect")

    mfa_service = MfaService(db)
    if not await mfa_service.is_mfa_enabled(session.user_id):
        raise AuthenticationError("Two-factor authentication is not enabled")

    factor_verified = False
    if input_data.code:
        factor_verified = await mfa_service.verify_user_totp(session.user_id, input_data.code)
    elif input_data.recovery_code:
        async with transaction(db):
            factor_verified = await mfa_service.verify_and_consume_recovery_code(
                session.user_id, input_data.recovery_code
            )

    if not factor_verified:
        raise AuthenticationError(
            "Valid MFA factor (code or recovery_code) is required to disable two-factor authentication"
        )

    async with transaction(db):
        await mfa_service.disable_totp(session.user_id)
    return MessageResponse(message="Two-factor authentication disabled")


# 16. Regenerate Recovery Codes
@router.post(
    "/mfa/recovery-codes/regenerate",
    response_model=MfaRecoveryCodesResponse,
    dependencies=[Depends(verify_csrf_token)],
)
async def regenerate_recovery_codes(
    input_data: MfaRecoveryCodesRegenerateInput,
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> MfaRecoveryCodesResponse:
    cred_service = CredentialsService(db)
    user = await cred_service.authenticate(session.user.email, input_data.password)
    if not user:
        raise AuthenticationError("Current password is incorrect")

    mfa_service = MfaService(db)
    if not await mfa_service.is_mfa_enabled(session.user_id):
        raise AuthenticationError("Two-factor authentication is not enabled")

    factor_verified = False
    if input_data.code:
        factor_verified = await mfa_service.verify_user_totp(session.user_id, input_data.code)
    elif input_data.recovery_code:
        async with transaction(db):
            factor_verified = await mfa_service.verify_and_consume_recovery_code(
                session.user_id, input_data.recovery_code
            )

    if not factor_verified:
        raise AuthenticationError(
            "Valid MFA factor (code or recovery_code) is required to regenerate recovery codes"
        )

    async with transaction(db):
        codes = await mfa_service.regenerate_recovery_codes(session.user_id)
        if codes is None:
            raise AuthenticationError("Two-factor authentication is not enabled")

    return MfaRecoveryCodesResponse(recovery_codes=codes)


# 17. List Active Sessions
@router.get("/sessions", response_model=list[SessionRead])
async def list_sessions(
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> list[SessionRead]:
    session_service = SessionService(db)
    sessions = await session_service.list_active_sessions(session.user_id)
    return [
        SessionRead(
            id=s.id,
            created_at=s.created_at,
            last_seen_at=s.last_seen_at,
            user_agent=s.user_agent,
            ip_address=s.ip_address,
            is_current=(s.id == session.id),
        )
        for s in sessions
    ]


# 18. Revoke Specific Session
@router.delete(
    "/sessions/{session_id}",
    response_model=MessageResponse,
    dependencies=[Depends(verify_csrf_token)],
)
async def revoke_session(
    session_id: uuid.UUID,
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> MessageResponse:
    session_service = SessionService(db)
    # Ensure target session belongs to current user
    target = await session_service.repo.get_by_id(session_id)
    if not target or target.user_id != session.user_id:
        raise ForbiddenError("Session not found or belongs to another user")

    async with transaction(db):
        await session_service.revoke_session(session_id)
    return MessageResponse(message="Session revoked")


# 19. Select Active Organization / Tenant Context
@router.post("/select-tenant", dependencies=[Depends(verify_csrf_token)])
async def select_tenant(
    input_data: SelectActiveOrganizationInput,
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, str | uuid.UUID]:
    membership_repo = MembershipRepository(db)
    membership = await membership_repo.get(
        organization_id=input_data.organization_id,
        user_id=session.user_id,
    )
    if not membership:
        raise ForbiddenError("You are not a member of this organization")

    session_service = SessionService(db)
    async with transaction(db):
        await session_service.set_active_organization(session.id, input_data.organization_id)
    return {
        "message": "Active organization updated",
        "active_organization_id": input_data.organization_id,
    }
