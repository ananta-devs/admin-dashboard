"""Cryptographic single-use token service for authenticated report downloads."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
import uuid
from threading import Lock
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.exceptions import InvalidTokenError

logger = logging.getLogger(__name__)


class ReportDownloadTokenService:
    """Manages ephemeral single-use PDF download tokens with atomic consumption and TTL expiration."""

    _memory_lock = Lock()
    _memory_store: dict[str, tuple[dict[str, Any], float]] = {}

    @classmethod
    async def get_redis_client(cls) -> aioredis.Redis | None:
        try:
            return aioredis.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=1.0,
            )
        except Exception as e:
            logger.debug(f"Could not connect to Redis for download tokens: {e}")
            return None

    @classmethod
    async def create_download_token(
        cls,
        run_id: uuid.UUID,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        ttl_seconds: int = settings.REPORT_DOWNLOAD_TOKEN_TTL_SECONDS,
    ) -> str:
        """Create a cryptographically random, single-use download token."""
        raw_token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        payload = {
            "run_id": str(run_id),
            "organization_id": str(organization_id),
            "user_id": str(user_id),
            "created_at": time.time(),
        }
        serialized = json.dumps(payload)

        # Attempt to store in Redis
        redis_client = await cls.get_redis_client()
        if redis_client:
            try:
                await redis_client.set(f"report:download:{token_hash}", serialized, ex=ttl_seconds)
                await redis_client.aclose()
            except Exception as e:
                logger.warning(f"Failed to store download token in Redis: {e}")
                if settings.ENVIRONMENT == "production":
                    raise RuntimeError("Failed to store download token in production cache.") from e
        elif settings.ENVIRONMENT == "production":
            raise RuntimeError("Redis token cache unavailable in production.")

        # In non-production, store in memory fallback as well
        if settings.ENVIRONMENT != "production":
            with cls._memory_lock:
                # Purge expired memory tokens
                now = time.time()
                expired_keys = [k for k, (_, exp) in cls._memory_store.items() if exp < now]
                for k in expired_keys:
                    del cls._memory_store[k]

                cls._memory_store[token_hash] = (payload, now + ttl_seconds)

        return raw_token

    @classmethod
    async def consume_download_token(cls, raw_token: str) -> dict[str, Any]:
        """
        Atomically consume (burn) a single-use download token.
        Raises InvalidTokenError if token does not exist, has already been burned, or has expired.
        In production, fails closed if Redis is unavailable.
        """
        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()

        # 1. Try atomic Redis GETDEL
        redis_client = await cls.get_redis_client()
        if redis_client:
            try:
                raw_json = await redis_client.getdel(f"report:download:{token_hash}")
                await redis_client.aclose()
                if raw_json:
                    # Also purge from memory fallback
                    with cls._memory_lock:
                        cls._memory_store.pop(token_hash, None)
                    return json.loads(raw_json)
                else:
                    # Token missing or already consumed in Redis
                    raise InvalidTokenError(
                        "Download token is invalid, expired, or has already been consumed."
                    )
            except InvalidTokenError:
                raise
            except Exception as e:
                logger.error(f"Redis getdel error: {e}")
                if settings.ENVIRONMENT == "production":
                    raise InvalidTokenError(
                        "Download token verification failed (storage unavailable)."
                    ) from e
        elif settings.ENVIRONMENT == "production":
            raise InvalidTokenError("Download token verification failed (storage unavailable).")

        # 2. Check memory fallback store with atomic pop (non-production only)
        if settings.ENVIRONMENT != "production":
            with cls._memory_lock:
                entry = cls._memory_store.pop(token_hash, None)
                if entry:
                    payload, expires_at = entry
                    if time.time() <= expires_at:
                        return payload

        raise InvalidTokenError("Download token is invalid, expired, or has already been consumed.")
