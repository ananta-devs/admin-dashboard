"""Cryptographic service identity, scoped authorization, and key rotation for Sandbox Agent."""

import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from typing import Any


class SandboxTokenError(Exception):
    """Raised when sandbox authentication or authorization fails."""

    pass


class TokenRevocationRegistry:
    """In-memory and durable revocation registry for expired/revoked nonces and execution IDs."""

    def __init__(self) -> None:
        self._revoked_nonces: set[str] = set()
        self._revoked_executions: set[str] = set()

    def revoke_nonce(self, nonce: str) -> None:
        self._revoked_nonces.add(nonce)

    def revoke_execution(self, execution_id: str) -> None:
        self._revoked_executions.add(execution_id)

    def is_revoked(self, nonce: str, execution_id: str) -> bool:
        return nonce in self._revoked_nonces or execution_id in self._revoked_executions


def create_scoped_execution_token(
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    execution_id: str,
    secret_key: str,
    ttl_seconds: int = 60,
) -> str:
    """Generate a short-lived HMAC-SHA256 scoped execution token.

    Binds the caller to specific organization, job, and execution identifiers.
    """
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": "vibe-celery-worker",
        "org_id": str(organization_id),
        "job_id": str(job_id),
        "exec_id": execution_id,
        "op": "execute",
        "iat": now,
        "exp": now + ttl_seconds,
        "nonce": secrets.token_hex(16),
    }
    raw_payload = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(raw_payload).decode("utf-8").rstrip("=")
    sig = hmac.new(secret_key.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")
    return f"{payload_b64}.{sig_b64}"


def verify_scoped_execution_token(
    token_str: str,
    expected_org_id: uuid.UUID,
    expected_job_id: uuid.UUID,
    expected_exec_id: str,
    active_keys: list[str],
    revocation_registry: TokenRevocationRegistry | None = None,
) -> dict[str, Any]:
    """Validate a scoped execution token against active rotation keys and revocation store."""
    if not token_str or "." not in token_str:
        raise SandboxTokenError("Malformed authorization token format")

    parts = token_str.split(".")
    if len(parts) != 2:
        raise SandboxTokenError("Invalid authorization token segments")

    payload_b64, sig_b64 = parts

    # Verify signature against any valid key in the rotation set
    valid_sig = False
    for key in active_keys:
        if not key:
            continue
        expected_sig = hmac.new(key.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256).digest()
        calc_sig_b64 = base64.urlsafe_b64encode(expected_sig).decode("utf-8").rstrip("=")
        if hmac.compare_digest(sig_b64, calc_sig_b64):
            valid_sig = True
            break

    if not valid_sig:
        raise SandboxTokenError("Invalid token cryptographic signature")

    # Decode payload
    try:
        padding = "=" * (-len(payload_b64) % 4)
        raw_payload = base64.urlsafe_b64decode(payload_b64 + padding)
        payload = json.loads(raw_payload.decode("utf-8"))
    except Exception as e:
        raise SandboxTokenError(f"Failed to decode token payload: {e}") from e

    now = int(time.time())
    if payload.get("exp", 0) < now:
        raise SandboxTokenError("Authorization token has expired")

    # Scope verification
    if payload.get("org_id") != str(expected_org_id):
        raise SandboxTokenError("Token organization ID scope mismatch")
    if payload.get("job_id") != str(expected_job_id):
        raise SandboxTokenError("Token job ID scope mismatch")
    if payload.get("exec_id") != expected_exec_id:
        raise SandboxTokenError("Token execution ID scope mismatch")
    if payload.get("op") != "execute":
        raise SandboxTokenError("Token operation scope is not 'execute'")

    nonce = payload.get("nonce", "")
    if revocation_registry and revocation_registry.is_revoked(nonce, expected_exec_id):
        raise SandboxTokenError("Authorization token or execution has been revoked")

    return payload

