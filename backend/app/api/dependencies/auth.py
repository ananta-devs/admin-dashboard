from urllib.parse import urlparse

import redis.asyncio as aioredis
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import (
    AuthenticationError,
    CsrfError,
    EmailNotVerifiedError,
    ForbiddenError,
)
from app.core.security import compare_token, hash_token
from app.db.session import get_db_session
from app.db.transaction import transaction
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.session import AuthenticationSession
from app.models.user import User
from app.repositories.membership import MembershipRepository
from app.repositories.organization import OrganizationRepository
from app.services.permission import PermissionService
from app.services.session import SessionService


def _extract_token_from_request(request: Request) -> tuple[str | None, bool]:
    """Extract token from cookie or Authorization header.

    Returns:
        Tuple of (token_string, is_cookie_source).
    """
    # 1. Primary cookie name
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if token:
        return token, True

    # 2. Fallback cookie name
    if settings.SESSION_COOKIE_NAME != "vibe_session":
        token = request.cookies.get("vibe_session")
        if token:
            return token, True

    # 3. Authorization Bearer header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
        if token:
            return token, False

    return None, False


async def get_current_session(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> AuthenticationSession:
    """Dependency that extracts and authoritatively validates the current PostgreSQL session."""
    token, is_cookie = _extract_token_from_request(request)
    if not token:
        raise AuthenticationError("Authentication required")

    session_service = SessionService(db)
    auth_session = await session_service.validate_session_token(token)
    if not auth_session:
        raise AuthenticationError("Session invalid or expired")

    if settings.EMAIL_VERIFICATION_REQUIRED and auth_session.user.email_verified_at is None:
        raise EmailNotVerifiedError("Email address has not been verified")

    # Throttled touch activity
    async with transaction(db):
        await session_service.touch_session(auth_session)

    # Attach to request state for downstream handlers
    request.state.session = auth_session
    request.state.user = auth_session.user
    request.state.is_cookie_session = is_cookie

    return auth_session


def _validate_origin_and_referer(request: Request, action_label: str = "request") -> None:
    """Validate Origin, Referer, and Fetch Metadata headers against allowed origins."""
    # 1. Fetch Metadata check (browsers supporting Sec-Fetch-Site)
    sec_fetch_site = request.headers.get("sec-fetch-site") or request.headers.get("Sec-Fetch-Site")
    if sec_fetch_site and sec_fetch_site.lower() == "cross-site":
        raise CsrfError(f"Cross-site {action_label} forbidden by Fetch Metadata policy.")

    # 2. Strict Origin header validation (sent on POST / CORS)
    origin = request.headers.get("origin") or request.headers.get("Origin")
    if origin is not None:
        if origin not in settings.ALLOWED_ORIGINS:
            raise CsrfError(f"Untrusted request Origin '{origin}'. Access forbidden.")

    # 3. Referer header validation when Origin is absent
    referer = request.headers.get("referer") or request.headers.get("Referer")
    if origin is None and referer is not None:
        parsed = urlparse(referer)
        ref_origin = f"{parsed.scheme}://{parsed.netloc}"
        if ref_origin not in settings.ALLOWED_ORIGINS:
            raise CsrfError(f"Untrusted request Referer '{referer}'. Access forbidden.")


async def verify_auth_request_origin(request: Request) -> None:
    """Dependency enforcing Origin and Fetch Metadata validation on unauthenticated auth flows

    (POST /register, POST /login, POST /login/mfa) defending against Login CSRF and cross-site account creation.
    """
    _validate_origin_and_referer(request, action_label="authentication request")


async def verify_csrf_bootstrap_origin(request: Request) -> None:
    """Dependency for POST /auth/csrf-token verifying Origin without requiring an existing CSRF token."""
    _validate_origin_and_referer(request, action_label="CSRF token bootstrap")


async def verify_csrf_token(
    request: Request,
    session: AuthenticationSession = Depends(get_current_session),
) -> None:
    """Dependency that enforces CSRF validation and Origin verification for state-changing HTTP requests."""
    # Safe HTTP methods are exempt from CSRF checks
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return

    # Check if request was authenticated via session cookie
    is_cookie = getattr(request.state, "is_cookie_session", True)
    if not is_cookie:
        return

    # 1. Validate Origin / Referer / Fetch Metadata
    _validate_origin_and_referer(request, action_label="state-changing request")

    # 2. Enforce session-bound CSRF token
    csrf_header = request.headers.get("X-CSRF-Token") or request.headers.get("x-csrf-token")
    if not csrf_header:
        raise CsrfError("Missing X-CSRF-Token header")

    # 3. Constant-time comparison with current token hash
    if compare_token(csrf_header, session.csrf_token_hash):
        return

    # 4. Multi-tab concurrency grace period check via Redis
    try:
        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        async with r:
            token_hash = hash_token(csrf_header)
            if await r.exists(f"csrf_grace:{session.id}:{token_hash}"):
                return
    except Exception:
        pass

    raise CsrfError("Invalid CSRF token")


async def get_current_user(
    session: AuthenticationSession = Depends(get_current_session),
) -> User:
    """Dependency returning the authenticated user record."""
    return session.user


async def get_current_tenant(
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> Organization:
    """Dependency returning the active organization context, verifying membership in PostgreSQL."""
    if session.active_organization_id is None:
        raise ForbiddenError("No active organization selected in current session")

    # Authoritatively verify that the user still has an active membership in this organization
    membership_repo = MembershipRepository(db)
    membership = await membership_repo.get(
        organization_id=session.active_organization_id,
        user_id=session.user_id,
    )
    if not membership:
        raise ForbiddenError("User is not a member of the active organization")

    if session.active_organization:
        return session.active_organization

    org_repo = OrganizationRepository(db)
    org = await org_repo.get_by_id(session.active_organization_id)
    if not org:
        raise ForbiddenError("Active organization not found")

    return org


async def get_current_membership(
    session: AuthenticationSession = Depends(get_current_session),
    db: AsyncSession = Depends(get_db_session),
) -> OrganizationMembership:
    """Dependency returning the active OrganizationMembership for the authenticated user."""
    if session.active_organization_id is None:
        raise ForbiddenError("No active organization selected in current session")

    membership_repo = MembershipRepository(db)
    membership = await membership_repo.get(
        organization_id=session.active_organization_id,
        user_id=session.user_id,
    )
    if not membership:
        raise ForbiddenError("User is not a member of the active organization")

    return membership


def require_permission(permission_code: str):
    """Dependency factory checking that the active membership holds a specific permission."""

    async def _dependency(
        membership: OrganizationMembership = Depends(get_current_membership),
        db: AsyncSession = Depends(get_db_session),
    ) -> None:
        perm_service = PermissionService(db)
        has_perm = await perm_service.check_membership_has_permission(
            membership.id, permission_code, organization_id=membership.organization_id
        )
        if not has_perm:
            raise ForbiddenError(f"Permission denied: required '{permission_code}'")

    return _dependency


def require_any_permission(*permission_codes: str):
    """Dependency factory checking that the active membership holds at least one of the specified permissions."""

    async def _dependency(
        membership: OrganizationMembership = Depends(get_current_membership),
        db: AsyncSession = Depends(get_db_session),
    ) -> None:
        perm_service = PermissionService(db)
        effective = await perm_service.get_effective_permissions(
            membership.id, organization_id=membership.organization_id
        )
        if not any(code in effective for code in permission_codes):
            raise ForbiddenError(
                f"Permission denied: required at least one of {list(permission_codes)}"
            )

    return _dependency


def require_all_permissions(*permission_codes: str):
    """Dependency factory checking that the active membership holds all of the specified permissions."""

    async def _dependency(
        membership: OrganizationMembership = Depends(get_current_membership),
        db: AsyncSession = Depends(get_db_session),
    ) -> None:
        perm_service = PermissionService(db)
        effective = await perm_service.get_effective_permissions(
            membership.id, organization_id=membership.organization_id
        )
        missing = set(permission_codes) - effective
        if missing:
            raise ForbiddenError(
                f"Permission denied: missing required permissions: {sorted(missing)}"
            )

    return _dependency


# Standard dependency for backup & disaster recovery visibility
require_backup_read = require_any_permission("organization.settings.update", "audit.read")
