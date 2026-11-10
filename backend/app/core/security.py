import hashlib
import hmac
import os
import secrets
import time

import argon2
import pyotp
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings
from app.core.exceptions import BusinessRuleViolationError


def _get_argon2_hasher() -> argon2.PasswordHasher:
    """Instantiate Argon2id hasher with configured operational costs."""
    return argon2.PasswordHasher(
        time_cost=settings.ARGON2_TIME_COST,
        memory_cost=settings.ARGON2_MEMORY_COST,
        parallelism=settings.ARGON2_PARALLELISM,
        hash_len=settings.ARGON2_HASH_LEN,
        salt_len=settings.ARGON2_SALT_LEN,
        type=argon2.Type.ID,
    )


_hasher = _get_argon2_hasher()

# Precompute a valid dummy Argon2id hash once at startup for constant-time
# verification when a requested email does not exist. This mitigates timing differences;
# it does not guarantee their complete elimination.
_DUMMY_PASSWORD_HASH: str = _hasher.hash(
    "dummy_fallback_constant_time_passphrase_vibe_platform_secure_123"
)


def hash_password(password: str) -> str:
    """Hash plaintext password using Argon2id."""
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify password against stored Argon2id hash."""
    try:
        return _hasher.verify(password_hash, password)
    except (argon2.exceptions.VerifyMismatchError, argon2.exceptions.VerificationError):
        return False


def verify_dummy_password(password: str) -> None:
    """Execute dummy password verification to mitigate timing differences between existing and non-existing accounts."""
    try:
        _hasher.verify(_DUMMY_PASSWORD_HASH, password)
    except (argon2.exceptions.VerifyMismatchError, argon2.exceptions.VerificationError):
        pass


def check_needs_rehash(password_hash: str) -> bool:
    """Check if password hash parameters need updating."""
    try:
        return _hasher.check_needs_rehash(password_hash)
    except Exception:
        return False


def validate_password_policy(password: str) -> None:
    """Enforce enterprise password policy: minimum 12 chars, max 1024 bytes.

    Permits Unicode and spaces without trimming or modifying submitted content.
    """
    if len(password) < 12:
        raise BusinessRuleViolationError("Password must be at least 12 characters in length.")

    encoded_bytes = password.encode("utf-8")
    if len(encoded_bytes) > 1024:
        raise BusinessRuleViolationError(
            "Password exceeds maximum allowed byte length (1024 bytes)."
        )


def generate_secure_token(nbytes: int = 32) -> str:
    """Generate cryptographically secure opaque URL-safe token (at least 256 bits)."""
    return secrets.token_urlsafe(nbytes)


# Alias
generate_opaque_token = generate_secure_token


def hash_token(token: str) -> str:
    """Compute SHA-256 hex digest of a token for authoritative database persistence."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def compare_token(token: str, token_hash: str) -> bool:
    """Verify raw token against expected SHA-256 digest in constant time."""
    return hmac.compare_digest(hash_token(token), token_hash)


# --- AES-256-GCM Key Management & MFA Secret Encryption ---


def _get_aes_key() -> bytes:
    """Decode MFA encryption key from settings into 32 bytes."""
    raw_key = settings.MFA_ENCRYPTION_KEY
    if len(raw_key) == 64:
        try:
            return bytes.fromhex(raw_key)
        except ValueError:
            pass
    return hashlib.sha256(raw_key.encode("utf-8")).digest()


def encrypt_mfa_secret(secret: str, key_version: int = settings.MFA_KEY_VERSION) -> str:
    """Encrypt a Base32 TOTP secret with AES-256-GCM.

    Format: v{key_version}:{nonce_hex}:{ciphertext_and_tag_hex}
    """
    aesgcm = AESGCM(_get_aes_key())
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, secret.encode("utf-8"), None)
    return f"v{key_version}:{nonce.hex()}:{ciphertext.hex()}"


def decrypt_mfa_secret(encrypted_data: str) -> str:
    """Decrypt an encrypted TOTP secret using AES-256-GCM."""
    parts = encrypted_data.split(":")
    if len(parts) != 3:
        raise ValueError("Invalid encrypted secret format.")

    # version_str, nonce_hex, ciphertext_hex
    _, nonce_hex, ciphertext_hex = parts
    nonce = bytes.fromhex(nonce_hex)
    ciphertext = bytes.fromhex(ciphertext_hex)

    aesgcm = AESGCM(_get_aes_key())
    decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, None)
    return decrypted_bytes.decode("utf-8")


encrypt_totp_secret = encrypt_mfa_secret
decrypt_totp_secret = decrypt_mfa_secret


def _get_master_key() -> bytes:
    """Derive 32-byte key from ENCRYPTION_MASTER_KEY for external tokens."""
    raw_key = settings.ENCRYPTION_MASTER_KEY
    if len(raw_key) == 64:
        try:
            return bytes.fromhex(raw_key)
        except ValueError:
            pass
    return hashlib.sha256(raw_key.encode("utf-8")).digest()


def encrypt_integration_token(
    token: str,
    associated_data: bytes | None = None,
    key_version: int = 1,
) -> str:
    """Encrypt external integration OAuth token with AES-256-GCM using ENCRYPTION_MASTER_KEY.

    Format: v{key_version}:{nonce_hex}:{ciphertext_and_tag_hex}
    Optionally binds ciphertext to tenant/integration identity via associated_data (AAD).
    """
    aesgcm = AESGCM(_get_master_key())
    nonce = os.urandom(12)
    ciphertext = aesgcm.encrypt(nonce, token.encode("utf-8"), associated_data)
    return f"v{key_version}:{nonce.hex()}:{ciphertext.hex()}"


def decrypt_integration_token(
    encrypted_token: str,
    associated_data: bytes | None = None,
) -> str:
    """Decrypt external integration OAuth token with AES-256-GCM using ENCRYPTION_MASTER_KEY.

    Validates authentication tag and optional associated_data (AAD).
    """
    parts = encrypted_token.split(":")
    if len(parts) != 3:
        raise ValueError("Invalid encrypted token format.")
    version_str, nonce_hex, ciphertext_hex = parts
    if not version_str.startswith("v"):
        raise ValueError("Unknown token key version header.")
    nonce = bytes.fromhex(nonce_hex)
    ciphertext = bytes.fromhex(ciphertext_hex)
    aesgcm = AESGCM(_get_master_key())
    try:
        decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, associated_data)
    except Exception:
        # Fallback without AAD if token was created before AAD binding
        if associated_data is not None:
            decrypted_bytes = aesgcm.decrypt(nonce, ciphertext, None)
        else:
            raise
    return decrypted_bytes.decode("utf-8")


# --- TOTP RFC 6238 & Recovery Codes ---


def generate_totp_secret() -> str:
    """Generate a random Base32 TOTP secret."""
    return pyotp.random_base32()


def get_totp_provisioning_uri(secret: str, email: str) -> str:
    """Generate standard otpauth URI for authenticator applications."""
    totp = pyotp.TOTP(secret)
    return totp.provisioning_uri(name=email, issuer_name=settings.EMAILS_FROM_NAME)


build_totp_uri = get_totp_provisioning_uri


def verify_totp_code(
    secret: str, code: str, last_accepted_step: int | None = None
) -> tuple[bool, int | None]:
    """Verify 6-digit TOTP code and detect code replay across time steps.

    Returns:
        (is_valid, matched_step)
    """
    totp = pyotp.TOTP(secret)
    current_time = int(time.time())
    current_step = current_time // 30

    matched_step: int | None = None
    for offset in (0, -1, 1):
        candidate = current_step + offset
        if totp.generate_otp(input=candidate) == code.strip():
            matched_step = candidate
            break

    if matched_step is None:
        return False, None

    # Enforce replay protection: candidate step must be strictly greater than last accepted
    if last_accepted_step is not None and matched_step <= last_accepted_step:
        return False, None

    return True, matched_step


def normalize_recovery_code(code: str) -> str:
    """Normalize recovery code by stripping dashes and whitespace and upper-casing."""
    return code.replace("-", "").replace(" ", "").strip().upper()


def hash_recovery_code(code: str) -> str:
    """Compute normalized SHA-256 digest of a recovery code."""
    return hash_token(normalize_recovery_code(code))


def generate_recovery_codes(count: int = 10) -> tuple[list[str], list[str]]:
    """Generate single-use random recovery codes.

    Returns:
        (plain_codes, hashed_codes)
    """
    plain_codes: list[str] = []
    hashed_codes: list[str] = []

    for _ in range(count):
        part1 = secrets.token_hex(2).upper()
        part2 = secrets.token_hex(2).upper()
        part3 = secrets.token_hex(2).upper()
        code = f"{part1}-{part2}-{part3}"
        plain_codes.append(code)
        hashed_codes.append(hash_token(normalize_recovery_code(code)))

    return plain_codes, hashed_codes


def compute_keyed_hash(value: str) -> str:
    """Compute HMAC-SHA256 of sensitive identifier (e.g. IP or email) for rate limiting keys

    using dedicated RATE_LIMIT_HMAC_KEY.
    """
    key = settings.RATE_LIMIT_HMAC_KEY.encode("utf-8")
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()
