"""HMAC-SHA256 request authentication and distributed replay protection for the isolated renderer service."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import uuid
from threading import Lock

import redis.asyncio as aioredis

from app.core.config import settings
from app.core.exceptions import AuthenticationError

logger = logging.getLogger(__name__)


class RendererAuthService:
    """Provides symmetric HMAC-SHA256 request signing and constant-time verification with distributed replay prevention."""

    SIGNATURE_VERSION = "2"
    VERSION_HEADER = "X-Renderer-Signature-Version"
    LEGACY_VERSION_HEADER = "X-Report-Signature-Version"
    SIGNATURE_HEADER = "X-Report-Signature"
    TIMESTAMP_HEADER = "X-Report-Timestamp"
    NONCE_HEADER = "X-Report-Nonce"
    TENANT_HEADER = "X-Tenant-Id"
    REPORT_HEADER = "X-Report-Id"

    MAX_CLOCK_SKEW_SECONDS = 30
    NONCE_TTL_SECONDS = 65  # 2 * MAX_CLOCK_SKEW_SECONDS + 5
    _nonce_lock = Lock()
    _seen_nonces: dict[str, float] = {}

    @classmethod
    def normalize_identifier(cls, val: str | None, field_name: str) -> str:
        """Validate and normalize tenant or report identifier to prevent injection."""
        if val is None or not str(val).strip():
            return ""
        raw_str = str(val)
        if "\n" in raw_str or "\r" in raw_str or "\0" in raw_str or ";" in raw_str:
            raise AuthenticationError(f"Control characters or newlines not allowed in {field_name}")
        val_str = raw_str.strip()
        if len(val_str) > 128:
            raise AuthenticationError(f"{field_name} exceeds maximum allowable length of 128 characters")
        try:
            # If valid UUID, normalize to lowercase canonical string
            return str(uuid.UUID(val_str)).lower()
        except (ValueError, AttributeError):
            return val_str.lower()

    @classmethod
    def build_canonical_payload(
        cls,
        method: str,
        path: str,
        tenant_id: str | None,
        report_id: str | None,
        timestamp: int,
        nonce: str,
        body_sha256: str,
    ) -> bytes:
        """
        Construct canonical V2 signature payload:
        VIBE-RENDERER-V2\nMETHOD\nPATH\nTENANT_ID\nREPORT_ID\nTIMESTAMP\nNONCE\nBODY_SHA256
        """
        if "\n" in method or "\r" in method or "\0" in method:
            raise AuthenticationError("Control characters or newlines not allowed in method")
        if "\n" in path or "\r" in path or "\0" in path:
            raise AuthenticationError("Control characters or newlines not allowed in path")
        if "\n" in nonce or "\r" in nonce or "\0" in nonce:
            raise AuthenticationError("Control characters or newlines not allowed in nonce")

        norm_method = method.upper().strip()
        norm_path = path.strip()
        norm_tenant = cls.normalize_identifier(tenant_id, "tenant_id")
        norm_report = cls.normalize_identifier(report_id, "report_id")
        norm_nonce = nonce.strip()

        lines = [
            "VIBE-RENDERER-V2",
            norm_method,
            norm_path,
            norm_tenant,
            norm_report,
            str(timestamp),
            norm_nonce,
            body_sha256,
        ]
        return "\n".join(lines).encode("utf-8")

    @classmethod
    def generate_signature(
        cls,
        secret: str,
        method: str,
        path: str,
        body: bytes,
        tenant_id: str | None = None,
        report_id: str | None = None,
        timestamp: int | None = None,
        nonce: str | None = None,
        version: str = "2",
    ) -> tuple[str, int, str, str]:
        """Generate canonical HMAC-SHA256 signature, returning (signature_hex, timestamp, nonce, version)."""
        if version != cls.SIGNATURE_VERSION:
            raise ValueError(f"Only signature version {cls.SIGNATURE_VERSION} can be generated")
        ts = int(time.time()) if timestamp is None else timestamp
        n = uuid.uuid4().hex if nonce is None else nonce
        body_sha256 = hashlib.sha256(body).hexdigest()

        msg = cls.build_canonical_payload(
            method=method,
            path=path,
            tenant_id=tenant_id,
            report_id=report_id,
            timestamp=ts,
            nonce=n,
            body_sha256=body_sha256,
        )
        sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
        return sig, ts, n, cls.SIGNATURE_VERSION

    @classmethod
    def _verify_signature_and_skew(
        cls,
        secret: str,
        method: str,
        path: str,
        body: bytes,
        signature: str,
        timestamp: int,
        nonce: str,
        version: str | None = None,
        tenant_id: str | None = None,
        report_id: str | None = None,
    ) -> None:
        """Verify protocol version, clock skew, and HMAC signature with constant-time comparison."""
        # 1. Version enforcement
        if version != cls.SIGNATURE_VERSION:
            raise AuthenticationError(
                f"Unsupported or missing signature version: '{version}' (version {cls.SIGNATURE_VERSION} required)"
            )

        # 2. Clock skew verification
        now = int(time.time())
        if abs(now - timestamp) > cls.MAX_CLOCK_SKEW_SECONDS:
            raise AuthenticationError(
                f"Renderer request timestamp skew exceeded (+/- {cls.MAX_CLOCK_SKEW_SECONDS}s)"
            )

        # 3. Canonical signature verification (V2)
        body_sha256 = hashlib.sha256(body).hexdigest()
        msg_v2 = cls.build_canonical_payload(
            method=method,
            path=path,
            tenant_id=tenant_id,
            report_id=report_id,
            timestamp=timestamp,
            nonce=nonce,
            body_sha256=body_sha256,
        )
        expected_sig_v2 = hmac.new(secret.encode("utf-8"), msg_v2, hashlib.sha256).hexdigest()

        if not hmac.compare_digest(signature, expected_sig_v2):
            raise AuthenticationError("Invalid renderer HMAC signature")

    @classmethod
    def _verify_nonce_memory(cls, nonce: str) -> None:
        """In-memory nonce verification with thread lock and bounded TTL eviction for non-production."""
        now = time.time()
        with cls._nonce_lock:
            cutoff = now - cls.NONCE_TTL_SECONDS
            expired_keys = [k for k, exp in cls._seen_nonces.items() if exp < cutoff]
            for k in expired_keys:
                del cls._seen_nonces[k]

            if nonce in cls._seen_nonces:
                raise AuthenticationError("Renderer request nonce already consumed (replay detected)")

            cls._seen_nonces[nonce] = now

    @classmethod
    async def _verify_nonce_distributed(
        cls,
        nonce: str,
        redis_client: aioredis.Redis | None = None,
    ) -> None:
        """
        Verify nonce is fresh and record it atomically across replicas and restarts.
        Uses Redis SET report:nonce:{nonce} 1 NX EX ttl.
        In production, fails closed on Redis connection or reservation error.
        """
        client = redis_client
        created_client = False
        if client is None and settings.REDIS_URL:
            try:
                client = aioredis.from_url(
                    settings.REDIS_URL,
                    decode_responses=True,
                    socket_connect_timeout=1.0,
                )
                created_client = True
            except Exception as e:
                logger.warning(f"Could not connect to Redis for distributed replay protection: {e}")
                client = None

        if client is not None:
            try:
                # Atomic SET report:nonce:{nonce} 1 NX EX ttl
                key = f"report:nonce:{nonce}"
                acquired = await client.set(key, "1", nx=True, ex=cls.NONCE_TTL_SECONDS)
                if not acquired:
                    raise AuthenticationError("Renderer request nonce already consumed (replay detected)")
                return
            except AuthenticationError:
                raise
            except Exception as e:
                logger.error(f"Redis distributed replay protection error: {e}")
                if settings.ENVIRONMENT == "production":
                    raise AuthenticationError("Authentication storage unavailable (replay protection failure)") from e
            finally:
                if created_client and client:
                    await client.aclose()

        # If Redis is unavailable in production, fail closed
        if settings.ENVIRONMENT == "production":
            raise AuthenticationError("Authentication storage unavailable (replay protection failure)")

        # Development / non-production fallback to in-memory store
        cls._verify_nonce_memory(nonce)

    @classmethod
    async def verify_request_async(
        cls,
        secret: str,
        method: str,
        path: str,
        body: bytes,
        signature: str,
        timestamp: int,
        nonce: str,
        version: str | None = "2",
        tenant_id: str | None = None,
        report_id: str | None = None,
        redis_client: aioredis.Redis | None = None,
    ) -> None:
        """
        Asynchronous verification of version, signature, tenant/report binding, clock skew, and distributed atomic nonce.
        Used by FastAPI service endpoints and distributed clients.
        """
        cls._verify_signature_and_skew(
            secret=secret,
            method=method,
            path=path,
            body=body,
            signature=signature,
            timestamp=timestamp,
            nonce=nonce,
            version=version,
            tenant_id=tenant_id,
            report_id=report_id,
        )
        await cls._verify_nonce_distributed(nonce, redis_client=redis_client)

    @classmethod
    def verify_request(
        cls,
        secret: str,
        method: str,
        path: str,
        body: bytes,
        signature: str,
        timestamp: int,
        nonce: str,
        version: str | None = "2",
        tenant_id: str | None = None,
        report_id: str | None = None,
        redis_client: aioredis.Redis | None = None,
    ) -> None:
        """
        Synchronous verification helper supporting synchronous unit tests and development fallback.
        In production, raises if Redis is required but unavailable.
        """
        cls._verify_signature_and_skew(
            secret=secret,
            method=method,
            path=path,
            body=body,
            signature=signature,
            timestamp=timestamp,
            nonce=nonce,
            version=version,
            tenant_id=tenant_id,
            report_id=report_id,
        )

        if settings.ENVIRONMENT == "production":
            raise AuthenticationError("Synchronous nonce verification is prohibited in production; use verify_request_async")

        cls._verify_nonce_memory(nonce)
