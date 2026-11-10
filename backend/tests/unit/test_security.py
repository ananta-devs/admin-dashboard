import pyotp
import pytest

from app.core.exceptions import BusinessRuleViolationError
from app.core.security import (
    build_totp_uri,
    compare_token,
    compute_keyed_hash,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_opaque_token,
    generate_recovery_codes,
    generate_totp_secret,
    hash_password,
    hash_recovery_code,
    hash_token,
    normalize_recovery_code,
    validate_password_policy,
    verify_dummy_password,
    verify_password,
    verify_totp_code,
)


def test_password_hashing_and_verification():
    password = "CorrectHorseBatteryStaple123!"
    pw_hash = hash_password(password)

    assert pw_hash.startswith("$argon2id$")
    assert verify_password(password, pw_hash) is True
    assert verify_password("WrongPassword123!", pw_hash) is False


def test_dummy_password_verification():
    # Must run without throwing any error
    verify_dummy_password("arbitrary_password_123")


def test_password_policy_validation():
    # Less than 12 characters
    with pytest.raises(BusinessRuleViolationError):
        validate_password_policy("short123")

    # Exceeding 1024 bytes
    with pytest.raises(BusinessRuleViolationError):
        validate_password_policy("A" * 1025)

    # Valid: Exactly 12 characters with Unicode and spaces
    validate_password_policy("valid 1234 ⚡")
    validate_password_policy("LongerEnterprisePassphraseWithSpaces123!")


def test_secure_token_and_hashing():
    token1 = generate_opaque_token()
    token2 = generate_opaque_token()
    assert token1 != token2
    assert len(token1) >= 40

    token_hash = hash_token(token1)
    assert len(token_hash) == 64
    assert compare_token(token1, token_hash) is True
    assert compare_token(token2, token_hash) is False


def test_aes_gcm_mfa_secret_encryption():
    raw_secret = generate_totp_secret()
    encrypted = encrypt_totp_secret(raw_secret)

    assert encrypted.startswith("v1:")
    parts = encrypted.split(":")
    assert len(parts) == 3

    decrypted = decrypt_totp_secret(encrypted)
    assert decrypted == raw_secret


def test_totp_verification_and_replay_detection():
    secret = generate_totp_secret()
    totp = pyotp.TOTP(secret)

    uri = build_totp_uri(secret, "user@enterprise.com")
    assert "otpauth://totp/" in uri
    assert "user%40enterprise.com" in uri

    current_code = totp.now()
    is_valid, step = verify_totp_code(secret, current_code)
    assert is_valid is True
    assert step is not None

    # Replay protection: Submitting same step should be rejected
    is_replayed, _ = verify_totp_code(secret, current_code, last_accepted_step=step)
    assert is_replayed is False


def test_recovery_codes_generation_and_normalization():
    plain_codes, hashed_codes = generate_recovery_codes(10)
    assert len(plain_codes) == 10
    assert len(hashed_codes) == 10

    code = plain_codes[0]
    normalized = normalize_recovery_code(code.lower())
    assert "-" not in normalized
    assert " " not in normalized

    h = hash_recovery_code(code.lower())
    assert h == hashed_codes[0]


def test_keyed_hash_for_rate_limiting():
    ip1 = "192.168.1.100"
    ip2 = "192.168.1.101"
    h1 = compute_keyed_hash(ip1)
    h2 = compute_keyed_hash(ip2)

    assert len(h1) == 64
    assert h1 != h2
    assert compute_keyed_hash(ip1) == h1


def test_keyed_hash_uses_dedicated_rate_limit_key():
    """Verify compute_keyed_hash uses RATE_LIMIT_HMAC_KEY and produces a different digest than SESSION_SECRET_KEY."""
    import hashlib
    import hmac

    from app.core.config import settings

    val = "user_test_identity@enterprise.com"
    actual = compute_keyed_hash(val)
    expected_rate_limit = hmac.new(
        settings.RATE_LIMIT_HMAC_KEY.encode("utf-8"), val.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    session_key_digest = hmac.new(
        settings.SESSION_SECRET_KEY.encode("utf-8"), val.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    assert actual == expected_rate_limit
    assert actual != session_key_digest


def test_cryptographic_key_separation_and_startup_validation():
    """Verify startup validation rejects identical keys, short keys, and default keys in production."""
    from pydantic import ValidationError

    from app.core.config import Settings

    # 1. Reject keys shorter than 32 characters
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            SESSION_SECRET_KEY="short_key",
            ENCRYPTION_MASTER_KEY="dev_fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
            RATE_LIMIT_HMAC_KEY="dev_rate_limit_hmac_key_min_32_chars_long_0123456789abcdef",
        )
    assert "at least 32 characters" in str(exc_info.value)

    # 2. Reject key reuse across unrelated purposes (pairwise separation)
    shared_key = "shared_super_secure_key_that_is_at_least_32_chars_long_12345"
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            SESSION_SECRET_KEY=shared_key,
            ENCRYPTION_MASTER_KEY=shared_key,
            RATE_LIMIT_HMAC_KEY="dev_rate_limit_hmac_key_min_32_chars_long_0123456789abcdef",
        )
    assert "pairwise distinct" in str(exc_info.value)

    # 3. Reject dev keys in production environment
    with pytest.raises(ValidationError) as exc_info:
        Settings(
            ENVIRONMENT="production",
            SESSION_SECRET_KEY="dev_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            ENCRYPTION_MASTER_KEY="prod_encryption_master_key_min_32_chars_1234567890",
            RATE_LIMIT_HMAC_KEY="prod_rate_limit_hmac_key_min_32_chars_0987654321",
        )
    assert "Insecure default key" in str(exc_info.value)

    # 4. Valid distinct production configuration succeeds
    valid_prod = Settings(
        ENVIRONMENT="production",
        POSTGRES_PASSWORD="prod_secure_random_db_password_12345",
        REDIS_PASSWORD="prod_secure_random_redis_password_12345",
        SESSION_COOKIE_SECURE=True,
        SESSION_SECRET_KEY="prod_session_secret_key_at_least_32_chars_long_aaaaa",
        ENCRYPTION_MASTER_KEY="prod_encryption_master_key_at_least_32_chars_bbbbb",
        RATE_LIMIT_HMAC_KEY="prod_rate_limit_hmac_key_at_least_32_chars_ccccc",
        REPORT_RENDERER_SECRET="prod_report_renderer_secret_min_32_chars_xyz_abcdef",
        ALLOWED_ORIGINS=["https://app.vibe.enterprise"],
    )
    assert valid_prod.ENVIRONMENT == "production"
