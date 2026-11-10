"""Chunked AES-256-GCM Streaming Cipher Protocol for Phase 10 Disaster Recovery.

Specification conformance: Phase 10 Revision 4.
Exact 128-byte binary header, RFC 5869 HKDF key derivation, RFC 3394 AES Key Wrap,
deterministic nonce derivation (NIST SP 800-38D), and 43-byte AEAD frame authentication.
"""

from __future__ import annotations

import hmac
import hashlib
import os
import struct
import uuid
from typing import BinaryIO, Iterator, Tuple

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDFExpand
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.keywrap import aes_key_wrap, aes_key_unwrap

# Header Constants
HEADER_MAGIC = b"VIBEENC1"
PROTOCOL_VERSION = 1
CIPHER_ID_AES_256_GCM_CHUNKED = 1
DEFAULT_CHUNK_SIZE = 1048576  # 1 MiB
HEADER_SIZE = 128
HEADER_PREFIX_SIZE = 116
HEADER_MAC_SIZE = 12
WRAPPED_DEK_SIZE = 40
AAD_SIZE = 43

# NIST SP 800-38D Limits
MAX_CHUNKS_PER_DEK = (1 << 32) - 1  # 4,294,967,295 invocations
HKDF_INFO_KEK_WRAP = b"vibe-backup-kek-wrap-v1"
HKDF_INFO_HEADER_MAC = b"vibe-backup-header-mac-v1"


class CryptoProtocolError(Exception):
    """Raised when encryption or decryption violates protocol specification."""


class BackupCipherService:
    """Implements authenticated Chunked AES-256-GCM encryption and decryption."""

    def __init__(self, master_kek: bytes, key_id: str) -> None:
        if len(master_kek) != 32:
            raise CryptoProtocolError("Master KEK must be exactly 32 bytes (256 bits).")
        key_id_bytes = key_id.encode("ascii")
        if len(key_id_bytes) > 16:
            raise CryptoProtocolError("Key ID must not exceed 16 ASCII characters.")
        self.master_kek = master_kek
        self.key_id = key_id_bytes.ljust(16, b"\x00")

    def derive_keys(self, master_salt: bytes) -> Tuple[bytes, bytes]:
        """Derive KEK_wrap and Key_header_mac using HKDF-SHA256 (RFC 5869)."""
        if len(master_salt) != 16:
            raise CryptoProtocolError("Master salt must be exactly 16 bytes.")
        prk = hmac.new(master_salt, self.master_kek, hashlib.sha256).digest()
        kek_wrap = HKDFExpand(
            algorithm=hashes.SHA256(), length=32, info=HKDF_INFO_KEK_WRAP, backend=None
        ).derive(prk)
        key_header_mac = HKDFExpand(
            algorithm=hashes.SHA256(), length=32, info=HKDF_INFO_HEADER_MAC, backend=None
        ).derive(prk)
        return kek_wrap, key_header_mac

    def build_header(
        self,
        artifact_id: bytes,
        master_salt: bytes,
        base_nonce: bytes,
        wrapped_dek: bytes,
        key_header_mac: bytes,
    ) -> bytes:
        """Construct the canonical 128-byte header."""
        if len(artifact_id) != 16:
            raise CryptoProtocolError("Artifact ID must be 16 bytes UUID.")
        if len(base_nonce) != 12:
            raise CryptoProtocolError("Base nonce must be 12 bytes.")
        if len(wrapped_dek) != WRAPPED_DEK_SIZE:
            raise CryptoProtocolError(f"Wrapped DEK must be {WRAPPED_DEK_SIZE} bytes.")

        prefix = (
            HEADER_MAGIC
            + struct.pack(">H", PROTOCOL_VERSION)
            + struct.pack(">H", CIPHER_ID_AES_256_GCM_CHUNKED)
            + struct.pack(">I", DEFAULT_CHUNK_SIZE)
            + artifact_id
            + self.key_id
            + master_salt
            + base_nonce
            + wrapped_dek
        )
        if len(prefix) != HEADER_PREFIX_SIZE:
            raise CryptoProtocolError(f"Header prefix length mismatch: {len(prefix)} != {HEADER_PREFIX_SIZE}")

        mac = hmac.new(key_header_mac, prefix, hashlib.sha256).digest()[:HEADER_MAC_SIZE]
        header = prefix + mac
        if len(header) != HEADER_SIZE:
            raise CryptoProtocolError(f"Total header size mismatch: {len(header)} != {HEADER_SIZE}")
        return header

    def parse_header(self, header_bytes: bytes) -> Tuple[bytes, bytes, bytes, bytes, bytes]:
        """Parse and verify the 128-byte header. Returns (artifact_id, key_id, base_nonce, dek, header_mac)."""
        if len(header_bytes) != HEADER_SIZE:
            raise CryptoProtocolError(f"Invalid header size: expected {HEADER_SIZE}, got {len(header_bytes)}")

        magic = header_bytes[0:8]
        if magic != HEADER_MAGIC:
            raise CryptoProtocolError(f"Invalid magic identifier: {magic!r}")

        version, cipher_id = struct.unpack(">HH", header_bytes[8:12])
        if version != PROTOCOL_VERSION:
            raise CryptoProtocolError(f"Unsupported protocol version: {version}")
        if cipher_id != CIPHER_ID_AES_256_GCM_CHUNKED:
            raise CryptoProtocolError(f"Unsupported cipher ID: {cipher_id}")

        chunk_size = struct.unpack(">I", header_bytes[12:16])[0]
        if chunk_size != DEFAULT_CHUNK_SIZE:
            raise CryptoProtocolError(f"Unsupported chunk size: {chunk_size}")

        artifact_id = header_bytes[16:32]
        key_id = header_bytes[32:48]
        master_salt = header_bytes[48:64]
        base_nonce = header_bytes[64:76]
        wrapped_dek = header_bytes[76:116]
        header_mac = header_bytes[116:128]

        # Verify key ID matches this cipher instance
        if key_id != self.key_id:
            raise CryptoProtocolError(f"Key ID mismatch: header has {key_id!r}, expected {self.key_id!r}")

        # Derive subkeys and verify header authentication
        kek_wrap, key_header_mac = self.derive_keys(master_salt)
        calculated_mac = hmac.new(key_header_mac, header_bytes[:116], hashlib.sha256).digest()[:HEADER_MAC_SIZE]
        if not hmac.compare_digest(calculated_mac, header_mac):
            raise CryptoProtocolError("Header HMAC authentication failed.")

        # Unwrap DEK
        try:
            dek = aes_key_unwrap(kek_wrap, wrapped_dek)
        except Exception as e:
            raise CryptoProtocolError(f"Failed to unwrap DEK: {e}") from e

        if len(dek) != 32:
            raise CryptoProtocolError("Unwrapped DEK is not 32 bytes.")

        return artifact_id, key_id, base_nonce, dek, header_mac

    @staticmethod
    def derive_chunk_iv(base_nonce: bytes, chunk_idx: int) -> bytes:
        """Derive 12-byte IV for chunk index: base_nonce XOR (0x00000000 || uint64_be(chunk_idx))."""
        if chunk_idx < 0 or chunk_idx > MAX_CHUNKS_PER_DEK:
            raise CryptoProtocolError(f"Chunk index {chunk_idx} exceeds maximum allowed invocations.")
        iv_mask = struct.pack(">I", 0) + struct.pack(">Q", chunk_idx)
        return bytes(a ^ b for a, b in zip(base_nonce, iv_mask))

    @staticmethod
    def construct_aad(
        artifact_id: bytes, header_mac: bytes, chunk_idx: int, is_final: int, length: int
    ) -> bytes:
        """Construct exact 43-byte AAD for chunk."""
        aad = (
            struct.pack(">H", PROTOCOL_VERSION)
            + artifact_id
            + header_mac
            + struct.pack(">Q", chunk_idx)
            + struct.pack(">B", is_final)
            + struct.pack(">I", length)
        )
        if len(aad) != AAD_SIZE:
            raise CryptoProtocolError(f"AAD size mismatch: {len(aad)} != {AAD_SIZE}")
        return aad

    def encrypt_stream(
        self,
        in_stream: BinaryIO,
        out_stream: BinaryIO,
        artifact_id: bytes | None = None,
        master_salt: bytes | None = None,
        base_nonce: bytes | None = None,
        dek: bytes | None = None,
    ) -> Tuple[int, str]:
        """Encrypt input stream to output stream. Returns (total_bytes_written, sha256_digest)."""
        artifact_id = artifact_id or uuid.uuid4().bytes
        master_salt = master_salt or os.urandom(16)
        base_nonce = base_nonce or os.urandom(12)
        dek = dek or os.urandom(32)

        kek_wrap, key_header_mac = self.derive_keys(master_salt)
        wrapped_dek = aes_key_wrap(kek_wrap, dek)

        header = self.build_header(artifact_id, master_salt, base_nonce, wrapped_dek, key_header_mac)
        header_mac = header[116:128]

        hasher = hashlib.sha256()
        out_stream.write(header)
        hasher.update(header)
        total_written = len(header)

        chunk_idx = 0
        read_buffer = in_stream.read(DEFAULT_CHUNK_SIZE)

        # Handle empty input stream (Vector 1 special case)
        if not read_buffer:
            is_final = 1
            length = 0
            iv = self.derive_chunk_iv(base_nonce, chunk_idx)
            aad = self.construct_aad(artifact_id, header_mac, chunk_idx, is_final, length)
            enc = Cipher(algorithms.AES(dek), modes.GCM(iv), backend=None).encryptor()
            enc.authenticate_additional_data(aad)
            ct = enc.update(b"") + enc.finalize()
            tag = enc.tag

            frame = struct.pack(">Q", chunk_idx) + struct.pack(">B", is_final) + struct.pack(">I", length) + ct + tag
            out_stream.write(frame)
            hasher.update(frame)
            total_written += len(frame)
            return total_written, hasher.hexdigest()

        while read_buffer:
            next_buffer = in_stream.read(DEFAULT_CHUNK_SIZE)
            is_final = 1 if len(next_buffer) == 0 else 0
            length = len(read_buffer)

            iv = self.derive_chunk_iv(base_nonce, chunk_idx)
            aad = self.construct_aad(artifact_id, header_mac, chunk_idx, is_final, length)
            enc = Cipher(algorithms.AES(dek), modes.GCM(iv), backend=None).encryptor()
            enc.authenticate_additional_data(aad)
            ct = enc.update(read_buffer) + enc.finalize()
            tag = enc.tag

            frame = struct.pack(">Q", chunk_idx) + struct.pack(">B", is_final) + struct.pack(">I", length) + ct + tag
            out_stream.write(frame)
            hasher.update(frame)
            total_written += len(frame)

            chunk_idx += 1
            read_buffer = next_buffer

        return total_written, hasher.hexdigest()

    def decrypt_stream(self, in_stream: BinaryIO, out_stream: BinaryIO) -> Tuple[int, bytes]:
        """Decrypt input stream to output stream. Returns (total_plaintext_bytes, artifact_id)."""
        header_bytes = in_stream.read(HEADER_SIZE)
        if len(header_bytes) != HEADER_SIZE:
            raise CryptoProtocolError("Truncated header in encrypted stream.")

        artifact_id, _, base_nonce, dek, header_mac = self.parse_header(header_bytes)

        expected_chunk_idx = 0
        terminal_encountered = False
        total_plaintext = 0

        while True:
            # Frame header is: chunk_idx (8B) + is_final (1B) + length L (4B) = 13 bytes
            frame_header = in_stream.read(13)
            if not frame_header:
                if not terminal_encountered:
                    raise CryptoProtocolError("Stream truncated: EOF reached before terminal frame.")
                break

            if terminal_encountered:
                raise CryptoProtocolError("Trailing data encountered after terminal frame.")

            if len(frame_header) != 13:
                raise CryptoProtocolError(f"Incomplete frame header: expected 13 bytes, got {len(frame_header)}")

            chunk_idx = struct.unpack(">Q", frame_header[0:8])[0]
            is_final = frame_header[8]
            length = struct.unpack(">I", frame_header[9:13])[0]

            if chunk_idx != expected_chunk_idx:
                raise CryptoProtocolError(
                    f"Chunk sequence discontinuity: expected index {expected_chunk_idx}, got {chunk_idx}"
                )

            if length > DEFAULT_CHUNK_SIZE:
                raise CryptoProtocolError(f"Chunk payload length {length} exceeds maximum chunk size.")

            # Read ciphertext (length bytes) + tag (16 bytes)
            ct_and_tag = in_stream.read(length + 16)
            if len(ct_and_tag) != length + 16:
                raise CryptoProtocolError("Truncated chunk ciphertext or tag.")

            ct = ct_and_tag[:length]
            tag = ct_and_tag[length:]

            iv = self.derive_chunk_iv(base_nonce, chunk_idx)
            aad = self.construct_aad(artifact_id, header_mac, chunk_idx, is_final, length)

            try:
                dec = Cipher(algorithms.AES(dek), modes.GCM(iv, tag), backend=None).decryptor()
                dec.authenticate_additional_data(aad)
                pt = dec.update(ct) + dec.finalize()
            except Exception as e:
                raise CryptoProtocolError(f"AEAD authentication failed on chunk {chunk_idx}: {e}") from e

            out_stream.write(pt)
            total_plaintext += len(pt)

            if is_final == 1:
                terminal_encountered = True

            expected_chunk_idx += 1

        return total_plaintext, artifact_id

