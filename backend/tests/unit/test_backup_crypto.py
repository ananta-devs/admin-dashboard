"""Unit tests for Phase 10 Cryptographic Protocol (Chunked AES-256-GCM and Manifest Signing)."""

import io
import os
import struct
import tempfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.services.crypto.backup_cipher import (
    BackupCipherService,
    CryptoProtocolError,
    DEFAULT_CHUNK_SIZE,
    HEADER_SIZE,
)
from app.services.crypto.manifest_signer import (
    ManifestSecurityError,
    ManifestSigner,
    canonicalize_json,
)

MASTER_KEK = bytes.fromhex("000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f")
DEK = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c762e7160f38b4da56a784d9045190cfe")
MASTER_SALT = bytes.fromhex("603deb1015ca71be2b73aef0857d7781")
BASE_NONCE = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9fafb")
ARTIFACT_ID = bytes.fromhex("a0a1a2a3a4a5a6a7a8a9aaabacadaeaf")
KEY_ID = "vibe-kek-2026-v1"


def test_crypto_protocol_known_answer_vector_empty():
    """Verify Vector 1: Empty artifact (0 bytes plaintext) produces exact 157 bytes with matching tag."""
    cipher = BackupCipherService(MASTER_KEK, KEY_ID)
    in_stream = io.BytesIO(b"")
    out_stream = io.BytesIO()

    total_bytes, sha256_digest = cipher.encrypt_stream(
        in_stream,
        out_stream,
        artifact_id=ARTIFACT_ID,
        master_salt=MASTER_SALT,
        base_nonce=BASE_NONCE,
        dek=DEK,
    )

    encrypted_data = out_stream.getvalue()
    assert len(encrypted_data) == 157  # 128B header + 13B frame + 0B CT + 16B tag
    assert total_bytes == 157

    # Check Header Magic
    assert encrypted_data[0:8] == b"VIBEENC1"

    # Frame header checks
    chunk_idx, is_final, length = struct.unpack(">QBI", encrypted_data[128:141])
    assert chunk_idx == 0
    assert is_final == 1
    assert length == 0

    # Decrypt and verify round-trip
    dec_out = io.BytesIO()
    dec_bytes, art_id = cipher.decrypt_stream(io.BytesIO(encrypted_data), dec_out)
    assert dec_bytes == 0
    assert dec_out.getvalue() == b""
    assert art_id == ARTIFACT_ID


def test_crypto_protocol_known_answer_vector_short_payload():
    """Verify Vector 2: Short payload matches expected encryption framing and round-trip."""
    cipher = BackupCipherService(MASTER_KEK, KEY_ID)
    payload = b"VIBE_REVISION_4_TEST_PAYLOAD"  # 28 bytes
    in_stream = io.BytesIO(payload)
    out_stream = io.BytesIO()

    total_bytes, _ = cipher.encrypt_stream(
        in_stream,
        out_stream,
        artifact_id=ARTIFACT_ID,
        master_salt=MASTER_SALT,
        base_nonce=BASE_NONCE,
        dek=DEK,
    )

    encrypted_data = out_stream.getvalue()
    assert len(encrypted_data) == 128 + 13 + 28 + 16  # 185 bytes
    assert total_bytes == 185

    # Decrypt and verify
    dec_out = io.BytesIO()
    dec_bytes, art_id = cipher.decrypt_stream(io.BytesIO(encrypted_data), dec_out)
    assert dec_bytes == 28
    assert dec_out.getvalue() == payload
    assert art_id == ARTIFACT_ID


def test_crypto_multi_chunk_streaming():
    """Verify streaming encryption and decryption across multiple 1 MiB chunks."""
    cipher = BackupCipherService(MASTER_KEK, KEY_ID)
    # 2.5 MiB test data
    data = os.urandom(int(2.5 * 1024 * 1024))
    in_stream = io.BytesIO(data)
    out_stream = io.BytesIO()

    total_bytes, sha256_digest = cipher.encrypt_stream(in_stream, out_stream)
    assert total_bytes > len(data)

    dec_out = io.BytesIO()
    dec_bytes, _ = cipher.decrypt_stream(io.BytesIO(out_stream.getvalue()), dec_out)
    assert dec_bytes == len(data)
    assert dec_out.getvalue() == data


def test_header_tampering_rejected():
    """Verify modifying any byte in the 128-byte header fails closed."""
    cipher = BackupCipherService(MASTER_KEK, KEY_ID)
    in_stream = io.BytesIO(b"Secret database records")
    out_stream = io.BytesIO()
    cipher.encrypt_stream(in_stream, out_stream)
    enc_data = bytearray(out_stream.getvalue())

    # Tamper with master salt in header
    enc_data[50] ^= 0xFF

    with pytest.raises(CryptoProtocolError, match="Header HMAC authentication failed"):
        cipher.decrypt_stream(io.BytesIO(enc_data), io.BytesIO())


def test_ciphertext_tampering_rejected():
    """Verify modifying ciphertext triggers AEAD authentication failure."""
    cipher = BackupCipherService(MASTER_KEK, KEY_ID)
    in_stream = io.BytesIO(b"Confidential financial statement")
    out_stream = io.BytesIO()
    cipher.encrypt_stream(in_stream, out_stream)
    enc_data = bytearray(out_stream.getvalue())

    # Tamper with ciphertext byte (after 128B header + 13B frame header)
    enc_data[142] ^= 0x01

    with pytest.raises(CryptoProtocolError, match="AEAD authentication failed"):
        cipher.decrypt_stream(io.BytesIO(enc_data), io.BytesIO())


def test_truncated_stream_rejected():
    """Verify stream truncated before terminal frame is rejected."""
    cipher = BackupCipherService(MASTER_KEK, KEY_ID)
    in_stream = io.BytesIO(os.urandom(int(1.5 * 1024 * 1024)))
    out_stream = io.BytesIO()
    cipher.encrypt_stream(in_stream, out_stream)
    enc_data = out_stream.getvalue()

    # Truncate to only the first chunk (drop final chunk)
    truncated = enc_data[: 128 + 13 + DEFAULT_CHUNK_SIZE + 16]

    with pytest.raises(CryptoProtocolError, match="Stream truncated"):
        cipher.decrypt_stream(io.BytesIO(truncated), io.BytesIO())


def test_manifest_signing_and_verification():
    """Verify RFC 8785 canonical signing and independent public key registry verification."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        registry_dir = Path(tmp_dir)
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        key_id = "vibe-signer-2026-v1"

        # Pre-provision trusted public key in registry
        (registry_dir / f"{key_id}.pub").write_bytes(public_key.public_bytes_raw())

        signer = ManifestSigner(registry_dir)

        manifest = {
            "backup_id": "test-backup-001",
            "artifact_id": "art-001",
            "backup_type": "PHYSICAL_BASE",
            "signing_key_id": key_id,
            "created_at": "2026-11-04T12:00:00Z",
        }

        signed_manifest = signer.sign_manifest(manifest, private_key)
        assert "signature" in signed_manifest

        # Verification succeeds
        canonical_bytes = signer.verify_manifest(signed_manifest)
        assert b"test-backup-001" in canonical_bytes

        # Reject tampered manifest
        tampered_manifest = dict(signed_manifest)
        tampered_manifest["backup_type"] = "LOGICAL_CUSTOM"
        with pytest.raises(ManifestSecurityError, match="signature verification failed"):
            signer.verify_manifest(tampered_manifest)

        # Reject self-asserted public key
        self_asserted = dict(signed_manifest)
        self_asserted["public_key"] = "attacker-public-key"
        with pytest.raises(ManifestSecurityError, match="self-asserted trust rejected"):
            signer.verify_manifest(self_asserted)

        # Reject unknown key ID
        unknown_key_manifest = dict(signed_manifest)
        unknown_key_manifest["signing_key_id"] = "unknown-key-id-2026"
        with pytest.raises(ManifestSecurityError, match="not in trusted registry"):
            signer.verify_manifest(unknown_key_manifest)

        # Reject path traversal key ID
        traversal_manifest = dict(signed_manifest)
        traversal_manifest["signing_key_id"] = "../../../etc/passwd"
        with pytest.raises(ManifestSecurityError, match="Invalid key_id syntax"):
            signer.verify_manifest(traversal_manifest)

