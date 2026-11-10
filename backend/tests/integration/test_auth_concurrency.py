import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.transaction import transaction
from app.models.user import User
from app.repositories.user import UserRepository
from app.services.action_token import ActionTokenService
from app.services.mfa import MfaService


@pytest.mark.asyncio
async def test_concurrent_action_token_single_use(
    session_factory: async_sessionmaker[AsyncSession], unique_id: str
):
    """Prove that single-use action tokens can only be consumed once under real concurrent load.

    Atomic UPDATE ... WHERE consumed_at IS NULL RETURNING ... ensures exactly 1 winner.
    """
    user_id = uuid.uuid4()
    async with session_factory() as session:
        user_repo = UserRepository(session)
        user = User(
            id=user_id,
            email=f"concurrent_tok_{unique_id}@enterprise.com",
            normalized_email=f"concurrent_tok_{unique_id}@enterprise.com",
            display_name="Concurrency User",
        )
        async with transaction(session):
            await user_repo.create(user)

        action_service = ActionTokenService(session)
        async with transaction(session):
            raw_token = await action_service.create_email_verification_token(user_id)

    # Launch 10 simultaneous tasks competing to consume the same action token
    async def try_consume() -> bool:
        async with session_factory() as session:
            svc = ActionTokenService(session)
            async with transaction(session):
                rec = await svc.consume_email_verification_token(raw_token)
            return rec is not None

    tasks = [asyncio.create_task(try_consume()) for _ in range(10)]
    results = await asyncio.gather(*tasks)

    # Exactly 1 consumer succeeded; exactly 9 failed
    assert results.count(True) == 1
    assert results.count(False) == 9


@pytest.mark.asyncio
async def test_concurrent_recovery_code_single_use(
    session_factory: async_sessionmaker[AsyncSession], unique_id: str
):
    """Prove that backup recovery codes can only be consumed once under concurrent load.

    Atomic UPDATE ... WHERE consumed_at IS NULL RETURNING ... ensures exactly 1 winner.
    """
    user_id = uuid.uuid4()
    async with session_factory() as session:
        user_repo = UserRepository(session)
        user = User(
            id=user_id,
            email=f"concurrent_mfa_{unique_id}@enterprise.com",
            normalized_email=f"concurrent_mfa_{unique_id}@enterprise.com",
            display_name="Concurrency MFA User",
        )
        async with transaction(session):
            await user_repo.create(user)

        mfa_svc = MfaService(session)
        async with transaction(session):
            _, _ = await mfa_svc.begin_totp_enrollment(user_id, user.email)

        import pyotp

        cred = await mfa_svc.get_credential(user_id)
        assert cred is not None
        from app.core.security import decrypt_totp_secret

        secret = decrypt_totp_secret(cred.encrypted_secret)
        code = pyotp.TOTP(secret).now()

        async with transaction(session):
            recovery_codes = await mfa_svc.confirm_totp_enrollment(user_id, code)

        assert recovery_codes is not None
        target_code = recovery_codes[0]

    # Launch 10 simultaneous tasks competing to consume the same recovery code via login challenges
    async def try_consume_recovery() -> bool:
        async with session_factory() as session:
            svc = MfaService(session)
            async with transaction(session):
                challenge_token, _ = await svc.create_login_challenge(user_id)

            async with transaction(session):
                verified_user = await svc.verify_challenge_recovery_code(
                    challenge_token, target_code
                )
            return verified_user is not None

    tasks = [asyncio.create_task(try_consume_recovery()) for _ in range(10)]
    results = await asyncio.gather(*tasks)

    # Exactly 1 winner; exactly 9 failed
    assert results.count(True) == 1
    assert results.count(False) == 9


@pytest.mark.asyncio
async def test_concurrent_totp_atomic_replay_prevention(
    session_factory: async_sessionmaker[AsyncSession], unique_id: str
):
    """Prove that concurrent verification of the same TOTP step is atomically rejected.

    Uses atomic compare-and-swap (CAS):
    WHERE user_id = :user_id AND (last_accepted_step IS NULL OR last_accepted_step < :new_step)
    Two concurrent sessions with identical valid TOTP step: exactly 1 succeeds, 1 fails.
    """
    import time

    import pyotp

    from app.core.security import decrypt_totp_secret

    user_id = uuid.uuid4()
    async with session_factory() as session:
        user_repo = UserRepository(session)
        user = User(
            id=user_id,
            email=f"concurrent_totp_{unique_id}@enterprise.com",
            normalized_email=f"concurrent_totp_{unique_id}@enterprise.com",
            display_name="Concurrency TOTP User",
        )
        async with transaction(session):
            await user_repo.create(user)

        mfa_svc = MfaService(session)
        async with transaction(session):
            _, _ = await mfa_svc.begin_totp_enrollment(user_id, user.email)

        cred = await mfa_svc.get_credential(user_id)
        assert cred is not None
        secret = decrypt_totp_secret(cred.encrypted_secret)
        totp = pyotp.TOTP(secret)

        # Confirm enrollment with initial step
        initial_step = int(time.time()) // 30
        initial_code = totp.generate_otp(input=initial_step)
        async with transaction(session):
            await mfa_svc.confirm_totp_enrollment(user_id, initial_code)

    # Use a future time step to test concurrent submission of identical valid OTP
    target_step = initial_step + 1
    target_code = totp.generate_otp(input=target_step)

    # Create two separate challenge tokens
    async with session_factory() as session:
        mfa_svc = MfaService(session)
        async with transaction(session):
            chal1, _ = await mfa_svc.create_login_challenge(user_id)
            chal2, _ = await mfa_svc.create_login_challenge(user_id)

    # Launch two independent AsyncSession instances concurrently with the exact same step code
    barrier = asyncio.Barrier(2)

    async def verify_attempt(chal_token: str) -> bool:
        await barrier.wait()
        async with session_factory() as session:
            svc = MfaService(session)
            async with transaction(session):
                res = await svc.verify_challenge_totp(chal_token, target_code)
            return res is not None

    task1 = asyncio.create_task(verify_attempt(chal1))
    task2 = asyncio.create_task(verify_attempt(chal2))

    results = await asyncio.gather(task1, task2)

    # Exactly 1 succeeds, 1 fails due to atomic CAS replay rejection
    assert results.count(True) == 1
    assert results.count(False) == 1

    # Verify in DB that last_accepted_step was advanced to target_step
    async with session_factory() as session:
        mfa_svc = MfaService(session)
        updated_cred = await mfa_svc.get_credential(user_id)
        assert updated_cred is not None
        assert updated_cred.last_accepted_step == target_step
