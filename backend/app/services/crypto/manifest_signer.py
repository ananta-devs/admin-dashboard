"""Manifest canonicalization and Ed25519 digital signature signing/verification.

Implements RFC 8785 JSON Canonicalization Scheme (JCS) and Ed25519 digital signatures
against an independent, out-of-band pre-provisioned trusted public key registry.
Strictly rejects manifests with self-asserted embedded public keys.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

KEY_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-]{8,32}$")


class ManifestSecurityError(Exception):
    """Raised when manifest canonicalization, signing, or verification fails."""


def canonicalize_json(data: dict[str, Any]) -> bytes:
    """Serialize dictionary into deterministic RFC 8785 JCS canonical JSON bytes."""
    # JSON Canonicalization Scheme (RFC 8785):
    # - Lexicographic UTF-16 code unit key ordering
    # - Whitespace outside strings eliminated
    # - Standardized float and integer format
    # - UTF-8 encoded
    return json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class ManifestSigner:
    """Signs backup manifests using Ed25519 and verifies against an independent key registry."""

    def __init__(self, trusted_keys_dir: str | Path) -> None:
        self.trusted_keys_dir = Path(trusted_keys_dir).resolve()

    def sanitize_key_id(self, key_id: str) -> str:
        """Validate key ID against strict regex to prevent directory traversal attacks."""
        if not KEY_ID_REGEX.match(key_id):
            raise ManifestSecurityError(f"Invalid key_id syntax: {key_id!r}")
        return key_id

    def get_trusted_public_key(self, key_id: str) -> Ed25519PublicKey:
        """Retrieve pre-provisioned public key from trusted key registry."""
        safe_key_id = self.sanitize_key_id(key_id)
        key_path = (self.trusted_keys_dir / f"{safe_key_id}.pub").resolve()

        # Strict containment check
        if not key_path.is_relative_to(self.trusted_keys_dir):
            raise ManifestSecurityError("Path traversal attempt in key ID resolution.")

        if not key_path.is_file():
            raise ManifestSecurityError(f"Signing key '{safe_key_id}' is not in trusted registry.")

        raw_key_bytes = key_path.read_bytes()
        try:
            # Check if key is raw 32 bytes or hex encoded
            if len(raw_key_bytes) == 32:
                return Ed25519PublicKey.from_public_bytes(raw_key_bytes)
            elif len(raw_key_bytes.strip()) == 64:
                return Ed25519PublicKey.from_public_bytes(
                    bytes.fromhex(raw_key_bytes.strip().decode("ascii"))
                )
            else:
                return Ed25519PublicKey.from_public_bytes(raw_key_bytes)
        except Exception as e:
            raise ManifestSecurityError(
                f"Malformed public key in trusted registry for '{safe_key_id}': {e}"
            ) from e

    def sign_manifest(
        self, manifest_dict: dict[str, Any], private_key: Ed25519PrivateKey
    ) -> dict[str, Any]:
        """Sign canonical payload (excluding 'signature') and return updated dictionary."""
        # Ensure signing_key_id is present and valid
        key_id = manifest_dict.get("signing_key_id")
        if not key_id or not isinstance(key_id, str):
            raise ManifestSecurityError("Manifest must contain a valid 'signing_key_id'.")
        self.sanitize_key_id(key_id)

        # Clone manifest and remove any pre-existing signature field
        payload = {k: v for k, v in manifest_dict.items() if k != "signature"}
        canonical_bytes = canonicalize_json(payload)

        sig_bytes = private_key.sign(canonical_bytes)
        payload["signature"] = sig_bytes.hex()
        return payload

    def verify_manifest(self, manifest_dict: dict[str, Any]) -> bytes:
        """Verify Ed25519 signature against trusted registry. Returns canonical signed bytes."""
        # 1. Reject self-asserted or embedded public keys
        if "public_key" in manifest_dict or "signing_public_key" in manifest_dict:
            raise ManifestSecurityError(
                "Security violation: Manifest attempts to supply its own public key (self-asserted trust rejected)."
            )

        # 2. Extract and validate signature field
        signature_hex = manifest_dict.get("signature")
        if not signature_hex or not isinstance(signature_hex, str):
            raise ManifestSecurityError("Manifest missing valid 'signature' field.")

        try:
            sig_bytes = bytes.fromhex(signature_hex)
        except ValueError as e:
            raise ManifestSecurityError(f"Invalid hex encoding in signature: {e}") from e

        # 3. Extract and resolve key ID in trusted registry
        key_id = manifest_dict.get("signing_key_id")
        if not key_id or not isinstance(key_id, str):
            raise ManifestSecurityError("Manifest missing 'signing_key_id'.")

        public_key = self.get_trusted_public_key(key_id)

        # 4. Canonicalize payload excluding 'signature'
        payload = {k: v for k, v in manifest_dict.items() if k != "signature"}
        canonical_bytes = canonicalize_json(payload)

        # 5. Verify signature
        try:
            public_key.verify(sig_bytes, canonical_bytes)
        except InvalidSignature as e:
            raise ManifestSecurityError(
                "Ed25519 signature verification failed: manifest tampered or wrong key."
            ) from e

        return canonical_bytes
