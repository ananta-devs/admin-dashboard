from collections.abc import Awaitable
from typing import Any, cast

import redis.asyncio as aioredis
from fastapi import Request

from app.core.config import settings
from app.core.exceptions import RateLimitExceededError, ServiceUnavailableError
from app.core.logging import logger
from app.core.security import compute_keyed_hash

# Atomic Lua script for sliding fixed-window counter with automatic expiration
_RATE_LIMIT_LUA = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
if current > tonumber(ARGV[2]) then
    return {0, ttl}
else
    return {1, ttl}
end
"""


async def get_redis_client() -> aioredis.Redis:
    """Return an async Redis client instance using the cache / broker URL."""
    return aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=False,
    )


async def check_rate_limit(
    action: str,
    identifier: str | None = None,
    max_requests: int | None = None,
    window_seconds: int = 60,
    request: Request | None = None,
    limit: int | None = None,
) -> None:
    """Enforce atomic rate limit using keyed hashes for privacy.

    Raises RateLimitExceededError if limit is reached or if Redis is unreachable (fail-closed).
    """
    effective_limit = (
        limit if limit is not None else (max_requests if max_requests is not None else 60)
    )

    # Derive identifier from client IP if not provided
    if identifier is None and request is not None:
        client_host = request.client.host if request.client else "127.0.0.1"
        identifier = client_host

    target_id = identifier or "anonymous"
    keyed_id = compute_keyed_hash(target_id)
    key = f"rl:{action}:{keyed_id}"

    client = None
    try:
        client = await get_redis_client()
        eval_op = client.eval(_RATE_LIMIT_LUA, 1, key, str(window_seconds), str(effective_limit))
        raw_res: Any = await cast(Awaitable[Any], eval_op)
        result = cast(list[int], raw_res)
        allowed, ttl = result[0], result[1]

        if allowed == 0:
            retry_after = max(int(ttl), 1)
            logger.warning(
                f"Rate limit exceeded for action '{action}' on keyed identifier (TTL: {retry_after}s)"
            )
            raise RateLimitExceededError(
                message="Too many attempts. Please try again later.",
                retry_after_seconds=retry_after,
            )
    except (aioredis.RedisError, OSError) as e:
        logger.error(f"Redis rate limiting failure on '{action}': {e!s}")
        if settings.RATE_LIMIT_FAIL_CLOSED:
            raise ServiceUnavailableError(
                message="Security rate limiting service temporarily unavailable. Please retry shortly."
            ) from e
    finally:
        if client:
            await client.aclose()


async def rate_limit_login(ip: str, normalized_email: str | None = None) -> None:
    """Rate limit login attempts by IP and optionally by keyed account digest."""
    await check_rate_limit("login_ip", ip, settings.RATE_LIMIT_LOGIN_PER_MINUTE)
    if normalized_email:
        await check_rate_limit("login_acct", normalized_email, settings.RATE_LIMIT_LOGIN_PER_MINUTE)


async def rate_limit_register(ip: str) -> None:
    """Rate limit account registration by client IP."""
    await check_rate_limit("register_ip", ip, settings.RATE_LIMIT_REGISTER_PER_MINUTE)


async def rate_limit_password_reset(ip: str, normalized_email: str | None = None) -> None:
    """Rate limit password reset requests by IP and keyed account digest."""
    await check_rate_limit("pwd_reset_ip", ip, settings.RATE_LIMIT_PASSWORD_RESET_PER_MINUTE)
    if normalized_email:
        await check_rate_limit(
            "pwd_reset_acct", normalized_email, settings.RATE_LIMIT_PASSWORD_RESET_PER_MINUTE
        )


async def rate_limit_email_verification(ip: str) -> None:
    """Rate limit email verification requests by client IP."""
    await check_rate_limit("verify_email_ip", ip, settings.RATE_LIMIT_VERIFY_EMAIL_PER_MINUTE)


async def rate_limit_mfa(user_id: str) -> None:
    """Rate limit MFA verification attempts by user ID."""
    await check_rate_limit("mfa_user", user_id, settings.RATE_LIMIT_MFA_PER_MINUTE)
