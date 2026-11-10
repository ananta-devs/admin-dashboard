from datetime import UTC, datetime, timedelta

import pyotp
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories.user import UserRepository
from app.services.action_token import ActionTokenService
from app.services.credentials import CredentialsService
from app.services.mfa import MfaService
from app.services.session import SessionService


@pytest.mark.asyncio
async def test_credentials_service_lifecycle(db_session: AsyncSession, unique_id: str):
    user_repo = UserRepository(db_session)
    user = User(
        email=f"auth_{unique_id}@enterprise.com",
        normalized_email=f"auth_{unique_id}@enterprise.com",
        display_name="Auth Test User",
    )
    await user_repo.create(user)

    cred_service = CredentialsService(db_session)
    password = "StrongPassword123!Safe"

    # Create credentials
    creds = await cred_service.create_credentials(user.id, password)
    assert creds.user_id == user.id

    # Authenticate via service
    authenticated_user = await cred_service.authenticate(user.email, password)
    assert authenticated_user is not None
    assert authenticated_user.id == user.id

    # Authenticate failure with wrong password
    bad_auth = await cred_service.authenticate(user.email, "WrongPassword999!")
    assert bad_auth is None

    # Authenticate non-existent email (executes dummy verify)
    non_existent = await cred_service.authenticate("ghost@enterprise.com", password)
    assert non_existent is None

    # Change password
    new_password = "EvenBetterPassphrase456!"
    assert await cred_service.change_password(user.id, password, new_password) is True

    # Old password no longer works
    assert await cred_service.authenticate(user.email, password) is None
    # New password works
    assert await cred_service.authenticate(user.email, new_password) is not None


@pytest.mark.asyncio
async def test_session_service_lifecycle(db_session: AsyncSession, unique_id: str):
    user_repo = UserRepository(db_session)
    user = User(
        email=f"session_{unique_id}@enterprise.com",
        normalized_email=f"session_{unique_id}@enterprise.com",
        display_name="Session User",
    )
    await user_repo.create(user)

    session_service = SessionService(db_session)

    # 1. Create session
    auth_session, raw_token, raw_csrf = await session_service.create_session(
        user_id=user.id,
        user_agent="Pytest Agent 1.0",
        ip_address="127.0.0.1",
    )
    assert auth_session.user_id == user.id
    assert raw_token is not None
    assert raw_csrf is not None

    # 2. Authoritative PostgreSQL validation
    validated = await session_service.validate_session_token(raw_token)
    assert validated is not None
    assert validated.id == auth_session.id
    assert validated.user.id == user.id

    # 3. Invalid token returns None
    assert await session_service.validate_session_token("invalid_raw_token_value") is None

    # 4. Touch session extends idle expiry
    initial_last_seen = auth_session.last_seen_at - timedelta(seconds=70)
    auth_session.last_seen_at = initial_last_seen
    await session_service.touch_session(auth_session)
    assert auth_session.last_seen_at > initial_last_seen

    # 5. Revoke session
    revoked = await session_service.revoke_session(auth_session.id)
    assert revoked is True

    # Validating revoked session returns None
    assert await session_service.validate_session_token(raw_token) is None


@pytest.mark.asyncio
async def test_session_service_expiration(db_session: AsyncSession, unique_id: str):
    user_repo = UserRepository(db_session)
    user = User(
        email=f"expire_{unique_id}@enterprise.com",
        normalized_email=f"expire_{unique_id}@enterprise.com",
        display_name="Expiry User",
    )
    await user_repo.create(user)

    session_service = SessionService(db_session)

    # Test idle expiration
    s1, token1, _ = await session_service.create_session(user.id)
    s1.idle_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    assert await session_service.validate_session_token(token1) is None

    # Test absolute expiration
    s2, token2, _ = await session_service.create_session(user.id)
    s2.absolute_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    assert await session_service.validate_session_token(token2) is None


@pytest.mark.asyncio
async def test_action_token_service_lifecycle(db_session: AsyncSession, unique_id: str):
    user_repo = UserRepository(db_session)
    user = User(
        email=f"token_{unique_id}@enterprise.com",
        normalized_email=f"token_{unique_id}@enterprise.com",
        display_name="Token User",
    )
    await user_repo.create(user)

    action_service = ActionTokenService(db_session)

    # 1. Create and atomically consume email verification token
    raw_v_token = await action_service.create_email_verification_token(user.id)
    consumed_v = await action_service.consume_email_verification_token(raw_v_token)
    assert consumed_v is not None
    assert consumed_v.user_id == user.id

    # Re-consuming the same token must fail (atomic single-use)
    assert await action_service.consume_email_verification_token(raw_v_token) is None

    # 2. Invalidation of older pending tokens when a fresh one is issued
    token_old = await action_service.create_password_reset_token(user.id)
    token_new = await action_service.create_password_reset_token(user.id)

    # Old token was marked consumed/invalidated by issuing the new one
    assert await action_service.consume_password_reset_token(token_old) is None

    # New token consumes successfully
    consumed_new = await action_service.consume_password_reset_token(token_new)
    assert consumed_new is not None
    assert consumed_new.user_id == user.id


@pytest.mark.asyncio
async def test_mfa_service_lifecycle(db_session: AsyncSession, unique_id: str):
    user_repo = UserRepository(db_session)
    user = User(
        email=f"mfa_{unique_id}@enterprise.com",
        normalized_email=f"mfa_{unique_id}@enterprise.com",
        display_name="MFA User",
    )
    await user_repo.create(user)

    mfa_service = MfaService(db_session)

    # 1. Initiate TOTP enrollment
    raw_secret, uri = await mfa_service.begin_totp_enrollment(user.id, user.email)
    assert raw_secret is not None
    assert "otpauth://totp/" in uri
    assert await mfa_service.is_mfa_enabled(user.id) is False

    # 2. Confirm enrollment with authenticator code
    totp = pyotp.TOTP(raw_secret)
    code = totp.now()

    recovery_codes = await mfa_service.confirm_totp_enrollment(user.id, code)
    assert recovery_codes is not None
    assert len(recovery_codes) == 10
    assert await mfa_service.is_mfa_enabled(user.id) is True

    # 3. Create Login Challenge
    challenge_token, _ = await mfa_service.create_login_challenge(user.id)

    # 4. Verify login challenge via recovery code
    used_recovery_code = recovery_codes[0]
    challenge_user_id = await mfa_service.verify_challenge_recovery_code(
        challenge_token, used_recovery_code
    )
    assert challenge_user_id == user.id

    # 5. Recovery code cannot be reused (single-use)
    c2_token, _ = await mfa_service.create_login_challenge(user.id)
    reused = await mfa_service.verify_challenge_recovery_code(c2_token, used_recovery_code)
    assert reused is None

    # 6. Verify second challenge with code for the next time step (within ±1 window)
    import time

    current_step = int(time.time()) // 30
    fresh_code = totp.generate_otp(input=current_step + 1)
    c3_token, _ = await mfa_service.create_login_challenge(user.id)
    c3_user_id = await mfa_service.verify_challenge_totp(c3_token, fresh_code)
    assert c3_user_id == user.id

    # 7. Disable TOTP
    assert await mfa_service.disable_totp(user.id) is True
    assert await mfa_service.is_mfa_enabled(user.id) is False


@pytest.mark.asyncio
async def test_argon2id_rehash_on_login(db_session: AsyncSession, unique_id: str):
    """Prove that an outdated password hash is automatically rehashed on successful login,

    without overwriting concurrent password changes.
    """
    from argon2 import PasswordHasher

    from app.core.security import check_needs_rehash, verify_password
    from app.models.credentials import UserCredentials
    from app.repositories.credentials import CredentialsRepository

    # Generate an outdated hash with lower parameters
    low_cost_hasher = PasswordHasher(time_cost=1, memory_cost=8192, parallelism=1)
    password = "MySecurePassword123!"
    old_hash = low_cost_hasher.hash(password)
    assert check_needs_rehash(old_hash) is True

    user_repo = UserRepository(db_session)
    user = User(
        email=f"rehash_{unique_id}@enterprise.com",
        normalized_email=f"rehash_{unique_id}@enterprise.com",
        display_name="Rehash User",
    )
    await user_repo.create(user)

    cred_repo = CredentialsRepository(db_session)
    creds = UserCredentials(user_id=user.id, password_hash=old_hash)
    await cred_repo.create(creds)
    await db_session.flush()

    cred_service = CredentialsService(db_session)

    # 1. Failed authentication does NOT rehash
    failed = await cred_service.authenticate(user.email, "WrongPassword!")
    assert failed is None
    c_after_fail = await cred_repo.get_by_user_id(user.id)
    assert c_after_fail is not None
    assert c_after_fail.password_hash == old_hash

    # 2. Successful authentication transparently rehashes to current cost parameters
    authed = await cred_service.authenticate(user.email, password)
    assert authed is not None
    assert authed.id == user.id

    c_after_success = await cred_repo.get_by_user_id(user.id)
    assert c_after_success is not None
    assert c_after_success.password_hash != old_hash
    assert check_needs_rehash(c_after_success.password_hash) is False
    assert verify_password(password, c_after_success.password_hash) is True

    # 3. Test CAS concurrency protection: if old hash doesn't match, upgrade returns False
    upgraded = await cred_repo.upgrade_password_hash_if_matches(
        user.id, expected_old_hash=old_hash, new_hash="dummy"
    )
    assert upgraded is False
