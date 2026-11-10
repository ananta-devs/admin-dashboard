import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import generate_opaque_token, hash_token
from app.models.session import AuthenticationSession
from app.repositories.session import SessionRepository


class SessionService:
    """Service governing server-side authentication sessions, lifetimes, and CSRF tokens."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = SessionRepository(session)

    async def create_session(
        self,
        user_id: uuid.UUID,
        user_agent: str | None = None,
        ip_address: str | None = None,
        active_organization_id: uuid.UUID | None = None,
    ) -> tuple[AuthenticationSession, str, str]:
        """Create a new authoritative session record in PostgreSQL.

        Returns:
            Tuple of (AuthenticationSession, raw_session_token, raw_csrf_token).
        """
        raw_session_token = generate_opaque_token()
        token_hash = hash_token(raw_session_token)

        raw_csrf_token = generate_opaque_token()
        csrf_token_hash = hash_token(raw_csrf_token)

        now = datetime.now(UTC)
        idle_expires_at = now + timedelta(seconds=settings.SESSION_IDLE_TIMEOUT_SECONDS)
        absolute_expires_at = now + timedelta(seconds=settings.SESSION_ABSOLUTE_TIMEOUT_SECONDS)

        auth_session = AuthenticationSession(
            user_id=user_id,
            token_hash=token_hash,
            csrf_token_hash=csrf_token_hash,
            active_organization_id=active_organization_id,
            created_at=now,
            last_seen_at=now,
            idle_expires_at=idle_expires_at,
            absolute_expires_at=absolute_expires_at,
            user_agent=user_agent[:500] if user_agent else None,
            ip_address=ip_address[:64] if ip_address else None,
        )

        saved = await self.repo.create(auth_session)
        return saved, raw_session_token, raw_csrf_token

    async def validate_session_token(self, raw_session_token: str) -> AuthenticationSession | None:
        """Validate raw token against PostgreSQL. Returns None if invalid or expired."""
        if not raw_session_token:
            return None

        token_hash = hash_token(raw_session_token)
        auth_session = await self.repo.get_by_token_hash(token_hash)
        if not auth_session:
            return None

        if auth_session.revoked_at is not None:
            return None

        now = datetime.now(UTC)
        if now >= auth_session.idle_expires_at or now >= auth_session.absolute_expires_at:
            return None

        if auth_session.user is None or not auth_session.user.is_active:
            return None

        return auth_session

    async def touch_session(self, auth_session: AuthenticationSession) -> None:
        """Throttled touch: extends idle lifetime if interval has elapsed."""
        now = datetime.now(UTC)
        elapsed = (now - auth_session.last_seen_at).total_seconds()
        if elapsed >= settings.SESSION_TOUCH_INTERVAL_SECONDS:
            new_idle = min(
                now + timedelta(seconds=settings.SESSION_IDLE_TIMEOUT_SECONDS),
                auth_session.absolute_expires_at,
            )
            await self.repo.update_last_seen(
                auth_session.id, last_seen_at=now, idle_expires_at=new_idle
            )
            auth_session.last_seen_at = now
            auth_session.idle_expires_at = new_idle

    async def rotate_csrf_token(self, auth_session: AuthenticationSession) -> str:
        """Generate a new CSRF token for the session and persist its hash (multi-tab safe)."""
        return await self.rotate_csrf_token_multitab_safe(auth_session)

    async def rotate_csrf_token_multitab_safe(
        self,
        auth_session: AuthenticationSession,
        window_seconds: int = 10,
        grace_seconds: int = 60,
    ) -> str:
        """Rotate the CSRF token for a session in a multi-tab safe, deterministic manner.

        If a token was issued for this session within the active window (e.g. 10s),
        simultaneous requests from multiple browser tabs receive the identical active token.
        When rotating to a new token, the previous token hash is placed in a Redis grace set
        for 60 seconds so in-flight requests or sibling tabs do not fail validation.
        """
        active_cache_key = f"csrf_active:{auth_session.id}"
        lock_key = f"csrf_lock:{auth_session.id}"

        # 1. Check if an active token was recently generated for this session
        try:
            r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            async with r:
                cached_token = await r.get(active_cache_key)
                if cached_token:
                    # Deterministic reuse for simultaneous multi-tab requests
                    return cached_token

                # Coordinate simultaneous requests via generation lock
                acquired = await r.set(lock_key, "1", nx=True, ex=5)
                if not acquired:
                    # Sibling tab is actively generating the token; wait for it to finish
                    for _ in range(20):
                        await asyncio.sleep(0.05)
                        cached_token = await r.get(active_cache_key)
                        if cached_token:
                            return cached_token
        except Exception:
            pass

        # 2. Generate new token and track old hash
        raw_csrf_token = generate_opaque_token()
        old_hash = auth_session.csrf_token_hash
        new_hash = hash_token(raw_csrf_token)

        auth_session.csrf_token_hash = new_hash
        await self.session.flush()

        # 3. Store active token (10s) and old token grace period (60s) in Redis
        try:
            r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            async with r:
                await r.set(active_cache_key, raw_csrf_token, ex=window_seconds)
                if old_hash and old_hash != new_hash:
                    grace_key = f"csrf_grace:{auth_session.id}:{old_hash}"
                    await r.set(grace_key, "1", ex=grace_seconds)
                await r.delete(lock_key)
        except Exception:
            pass

        return raw_csrf_token

    async def revoke_session(self, session_id: uuid.UUID) -> bool:
        """Revoke a specific session."""
        return await self.repo.revoke(session_id)

    async def revoke_all_for_user(
        self, user_id: uuid.UUID, except_session_id: uuid.UUID | None = None
    ) -> int:
        """Revoke all sessions for a user (optionally keeping one)."""
        return await self.repo.revoke_all_for_user(user_id, except_session_id=except_session_id)

    async def set_active_organization(
        self, session_id: uuid.UUID, organization_id: uuid.UUID | None
    ) -> None:
        """Switch or clear the active organization context for the session."""
        await self.repo.set_active_organization(session_id, organization_id)

    async def list_active_sessions(self, user_id: uuid.UUID) -> list[AuthenticationSession]:
        """List unexpired, unrevoked sessions for user."""
        return await self.repo.list_active_by_user_id(user_id)
