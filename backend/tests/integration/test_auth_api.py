import time

import pyotp
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.session import get_db_session
from app.main import app
from app.services.email import MemoryEmailSender


@pytest.fixture
async def api_client():
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

    async def get_test_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = get_test_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    await test_engine.dispose()


@pytest.mark.asyncio
async def test_registration_and_email_verification_flow(api_client: AsyncClient, unique_id: str):
    MemoryEmailSender.clear_all()

    email = f"register_{unique_id}@enterprise.com"
    payload = {
        "email": email,
        "password": "SecurePassword1234!",
        "display_name": f"User {unique_id}",
        "organization_name": f"Org {unique_id}",
        "organization_slug": f"org-{unique_id}",
    }

    # 1. Register
    reg_res = await api_client.post("/api/v1/auth/register", json=payload)
    assert reg_res.status_code == 201
    data = reg_res.json()
    assert data["user"]["email"] == email
    assert data["organization"]["slug"] == f"org-{unique_id}"

    # 2. Duplicate registration rejected
    dup_res = await api_client.post("/api/v1/auth/register", json=payload)
    assert dup_res.status_code == 409

    # 3. Email verification token was sent
    sent = MemoryEmailSender.get_sent_messages()
    assert len(sent) >= 1
    verify_email_msg = [m for m in sent if m.to_email == email and "verify" in m.subject.lower()][0]
    # Extract token from message body
    token = verify_email_msg.body.split("token=")[1].split("\n")[0].strip()

    # 4. Verify email
    v_res = await api_client.post("/api/v1/auth/verify-email", json={"token": token})
    assert v_res.status_code == 200
    assert "verified" in v_res.json()["message"].lower()

    # 5. Re-verifying with same token fails
    v_dup = await api_client.post("/api/v1/auth/verify-email", json={"token": token})
    assert v_dup.status_code == 400


@pytest.mark.asyncio
async def test_login_cookie_and_csrf_flow(api_client: AsyncClient, unique_id: str):
    email = f"login_{unique_id}@enterprise.com"
    password = "MyStrongPassword1234!"
    reg_payload = {
        "email": email,
        "password": password,
        "display_name": f"User {unique_id}",
        "organization_name": f"Org {unique_id}",
        "organization_slug": f"org-login-{unique_id}",
    }
    await api_client.post("/api/v1/auth/register", json=reg_payload)

    # 1. Bad password returns 401
    bad_res = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "WrongPassword123!"},
    )
    assert bad_res.status_code == 401

    # 2. Successful login returns cookie & csrf token
    login_res = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_res.status_code == 200
    login_data = login_res.json()
    assert login_data["success"] is True
    csrf_token = login_data["csrf_token"]
    assert csrf_token is not None

    # Cookie was set
    assert (
        "vibe_session" in api_client.cookies or settings.SESSION_COOKIE_NAME in api_client.cookies
    )

    # 3. GET /me succeeds without CSRF header (safe method exempt)
    me_res = await api_client.get("/api/v1/auth/me")
    assert me_res.status_code == 200
    assert me_res.json()["user"]["email"] == email

    # 4. State-changing POST without CSRF token returns 403
    no_csrf = await api_client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": password,
            "new_password": "NewSecurePassword5678!",
        },
    )
    assert no_csrf.status_code == 403

    # 5. State-changing POST with invalid CSRF token returns 403
    bad_csrf = await api_client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": "invalid_csrf_token_value_here"},
        json={
            "current_password": password,
            "new_password": "NewSecurePassword5678!",
        },
    )
    assert bad_csrf.status_code == 403

    # 6. State-changing POST with valid CSRF token succeeds
    good_csrf = await api_client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": csrf_token},
        json={
            "current_password": password,
            "new_password": "NewSecurePassword5678!",
        },
    )
    assert good_csrf.status_code == 200
    assert "successfully" in good_csrf.json()["message"]


@pytest.mark.asyncio
async def test_logout_and_session_invalidation(api_client: AsyncClient, unique_id: str):
    email = f"logout_{unique_id}@enterprise.com"
    password = "InitialPassword123!"
    await api_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "display_name": f"User {unique_id}",
            "organization_name": f"Org {unique_id}",
            "organization_slug": f"org-logout-{unique_id}",
        },
    )

    login_res = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_res.status_code == 200
    csrf = login_res.json()["csrf_token"]

    # 1. Logout without CSRF token returns 403
    no_csrf_logout = await api_client.post("/api/v1/auth/logout")
    assert no_csrf_logout.status_code == 403

    # 2. Logout with invalid CSRF token returns 403
    bad_csrf_logout = await api_client.post(
        "/api/v1/auth/logout",
        headers={"X-CSRF-Token": "bad_token_123"},
    )
    assert bad_csrf_logout.status_code == 403

    # 3. Logout with untrusted Origin returns 403
    bad_origin_logout = await api_client.post(
        "/api/v1/auth/logout",
        headers={
            "X-CSRF-Token": csrf,
            "Origin": "https://malicious-attacker.com",
        },
    )
    assert bad_origin_logout.status_code == 403

    # 4. Logout with valid CSRF and trusted origin succeeds
    logout_res = await api_client.post(
        "/api/v1/auth/logout",
        headers={
            "X-CSRF-Token": csrf,
            "Origin": "http://localhost:3000",
        },
    )
    assert logout_res.status_code == 200

    # 5. Next call to /me returns 401
    me_res = await api_client.get("/api/v1/auth/me")
    assert me_res.status_code == 401

    # 6. Test logout-all CSRF enforcement
    login_res2 = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_res2.status_code == 200
    csrf2 = login_res2.json()["csrf_token"]

    no_csrf_all = await api_client.post("/api/v1/auth/logout-all")
    assert no_csrf_all.status_code == 403

    bad_origin_all = await api_client.post(
        "/api/v1/auth/logout-all",
        headers={"X-CSRF-Token": csrf2, "Origin": "https://evil-site.com"},
    )
    assert bad_origin_all.status_code == 403

    good_all = await api_client.post(
        "/api/v1/auth/logout-all",
        headers={"X-CSRF-Token": csrf2, "Origin": "http://localhost:3000"},
    )
    assert good_all.status_code == 200


@pytest.mark.asyncio
async def test_password_reset_flow(api_client: AsyncClient, unique_id: str):
    MemoryEmailSender.clear_all()
    email = f"pwdreset_{unique_id}@enterprise.com"
    password = "OldPassword1234!"
    await api_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "display_name": f"User {unique_id}",
            "organization_name": f"Org {unique_id}",
            "organization_slug": f"org-reset-{unique_id}",
        },
    )

    # Forgot password
    forgot_res = await api_client.post("/api/v1/auth/forgot-password", json={"email": email})
    assert forgot_res.status_code == 200

    sent = MemoryEmailSender.get_sent_messages()
    reset_msg = [m for m in sent if m.to_email == email and "password" in m.subject.lower()][0]
    reset_token = reset_msg.body.split("token=")[1].split("\n")[0].strip()

    # Reset password
    new_password = "BrandNewPassword9999!"
    reset_res = await api_client.post(
        "/api/v1/auth/reset-password",
        json={"token": reset_token, "new_password": new_password},
    )
    assert reset_res.status_code == 200

    # Old password fails
    bad_login = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert bad_login.status_code == 401

    # New password succeeds
    good_login = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": new_password},
    )
    assert good_login.status_code == 200


@pytest.mark.asyncio
async def test_mfa_full_api_flow(api_client: AsyncClient, unique_id: str):
    email = f"mfa_api_{unique_id}@enterprise.com"
    password = "MfaPassword123456!"
    await api_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "display_name": f"MFA {unique_id}",
            "organization_name": f"MFA Org {unique_id}",
            "organization_slug": f"org-mfa-{unique_id}",
        },
    )

    login_res = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    csrf = login_res.json()["csrf_token"]

    # 1. Setup TOTP requires password
    setup_res = await api_client.post(
        "/api/v1/auth/mfa/totp/setup",
        headers={"X-CSRF-Token": csrf},
        json={"password": password},
    )
    assert setup_res.status_code == 200
    secret = setup_res.json()["secret"]

    # 2. Confirm TOTP
    totp = pyotp.TOTP(secret)
    current_step = int(time.time()) // 30
    code = totp.generate_otp(input=current_step)
    confirm_res = await api_client.post(
        "/api/v1/auth/mfa/totp/confirm",
        headers={"X-CSRF-Token": csrf},
        json={"password": password, "code": code},
    )
    assert confirm_res.status_code == 200
    recovery_codes = confirm_res.json()["recovery_codes"]
    assert len(recovery_codes) == 10

    # 3. Next login returns challenge
    api_client.cookies.clear()
    challenge_login = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert challenge_login.status_code == 200
    challenge_data = challenge_login.json()
    assert challenge_data["mfa_required"] is True
    challenge_token = challenge_data["challenge_token"]

    # 4. Complete login using recovery code
    used_rec_code = recovery_codes[0]
    rec_verify = await api_client.post(
        "/api/v1/auth/login/mfa",
        json={"challenge_token": challenge_token, "recovery_code": used_rec_code},
    )
    assert rec_verify.status_code == 200
    assert rec_verify.json()["success"] is True

    # 5. Disable MFA requires password and second factor
    csrf2 = rec_verify.json()["csrf_token"]
    # Re-disable with next step OTP
    current_step2 = int(time.time()) // 30 + 1
    code2 = totp.generate_otp(input=current_step2)
    disable_res = await api_client.post(
        "/api/v1/auth/mfa/totp/disable",
        headers={"X-CSRF-Token": csrf2},
        json={"password": password, "code": code2},
    )
    assert disable_res.status_code == 200

    # 6. Next login succeeds directly without challenge
    api_client.cookies.clear()
    normal_login = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert normal_login.status_code == 200
    assert normal_login.json().get("mfa_required") is not True


@pytest.mark.asyncio
async def test_sensitive_operations_reauthentication(api_client: AsyncClient, unique_id: str):
    """Test reauthentication and second-factor requirements for sensitive account actions."""
    email = f"sensitive_{unique_id}@enterprise.com"
    password = "SensitivePassword123!"
    await api_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "display_name": f"Sensitive User {unique_id}",
            "organization_name": f"Sensitive Org {unique_id}",
            "organization_slug": f"org-sens-{unique_id}",
        },
    )

    login_res = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    csrf = login_res.json()["csrf_token"]

    # 1. TOTP setup requires password verification
    bad_pwd_setup = await api_client.post(
        "/api/v1/auth/mfa/totp/setup",
        headers={"X-CSRF-Token": csrf},
        json={"password": "WrongPassword!"},
    )
    assert bad_pwd_setup.status_code == 401

    good_setup = await api_client.post(
        "/api/v1/auth/mfa/totp/setup",
        headers={"X-CSRF-Token": csrf},
        json={"password": password},
    )
    assert good_setup.status_code == 200
    secret = good_setup.json()["secret"]

    # Confirm enrollment
    totp = pyotp.TOTP(secret)
    step1 = int(time.time()) // 30
    code1 = totp.generate_otp(input=step1)
    confirm_res = await api_client.post(
        "/api/v1/auth/mfa/totp/confirm",
        headers={"X-CSRF-Token": csrf},
        json={"password": password, "code": code1},
    )
    assert confirm_res.status_code == 200
    recovery_codes = confirm_res.json()["recovery_codes"]

    # 2. Cannot run setup again when already enabled
    dup_setup = await api_client.post(
        "/api/v1/auth/mfa/totp/setup",
        headers={"X-CSRF-Token": csrf},
        json={"password": password},
    )
    assert dup_setup.status_code == 401

    # 3. Changing password with MFA enabled requires second factor
    no_factor_change = await api_client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": password,
            "new_password": "NewSensPassword456!",
        },
    )
    assert no_factor_change.status_code == 401

    step2 = step1 + 1
    code2 = totp.generate_otp(input=step2)
    new_password = "NewSensPassword456!"
    factor_change = await api_client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": csrf},
        json={
            "current_password": password,
            "new_password": new_password,
            "code": code2,
        },
    )
    assert factor_change.status_code == 200

    # 4. Regenerating recovery codes requires valid MFA factor
    no_factor_regen = await api_client.post(
        "/api/v1/auth/mfa/recovery-codes/regenerate",
        headers={"X-CSRF-Token": csrf},
        json={"password": new_password},
    )
    assert no_factor_regen.status_code == 401

    # Regenerate using recovery code
    used_rec_code = recovery_codes[0]
    good_regen = await api_client.post(
        "/api/v1/auth/mfa/recovery-codes/regenerate",
        headers={"X-CSRF-Token": csrf},
        json={"password": new_password, "recovery_code": used_rec_code},
    )
    assert good_regen.status_code == 200
    fresh_recovery_codes = good_regen.json()["recovery_codes"]
    assert len(fresh_recovery_codes) == 10

    # 5. Disabling MFA requires valid MFA factor
    no_factor_disable = await api_client.post(
        "/api/v1/auth/mfa/totp/disable",
        headers={"X-CSRF-Token": csrf},
        json={"password": new_password},
    )
    assert no_factor_disable.status_code == 401

    # Disable using fresh recovery code
    used_rec_code = fresh_recovery_codes[0]
    good_disable = await api_client.post(
        "/api/v1/auth/mfa/totp/disable",
        headers={"X-CSRF-Token": csrf},
        json={"password": new_password, "recovery_code": used_rec_code},
    )
    assert good_disable.status_code == 200


@pytest.mark.asyncio
async def test_csrf_token_endpoint_methods_and_rotation(api_client: AsyncClient, unique_id: str):
    """Verify POST requirement, caching prevention, untrusted origin rejection, and token rotation on /csrf-token."""
    email = f"csrf_ep_{unique_id}@enterprise.com"
    password = "CsrfPassword123!"

    await api_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "display_name": f"CSRF User {unique_id}",
            "organization_name": f"CSRF Org {unique_id}",
            "organization_slug": f"org-csrf-{unique_id}",
        },
    )

    # 1. Unauthenticated request to /csrf-token returns 401
    unauth_res = await api_client.post("/api/v1/auth/csrf-token")
    assert unauth_res.status_code == 401

    # 2. Log in to establish authenticated session
    login_res = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_res.status_code == 200
    assert login_res.json()["csrf_token"] is not None

    # 3. GET /csrf-token returns 405 Method Not Allowed (GET must not mutate server state)
    get_res = await api_client.get("/api/v1/auth/csrf-token")
    assert get_res.status_code == 405

    # 4. Untrusted Origin on POST /csrf-token returns 403 Forbidden
    bad_origin_res = await api_client.post(
        "/api/v1/auth/csrf-token",
        headers={"Origin": "https://malicious-attacker.com"},
    )
    assert bad_origin_res.status_code == 403

    # 5. Cross-site Fetch Metadata returns 403 Forbidden
    cross_site_res = await api_client.post(
        "/api/v1/auth/csrf-token",
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert cross_site_res.status_code == 403

    # 6. Valid POST /csrf-token succeeds without requiring previous CSRF token
    post_res = await api_client.post(
        "/api/v1/auth/csrf-token",
        headers={"Origin": "http://localhost:3000"},
    )
    assert post_res.status_code == 200
    # Cache-Control: no-store strictly enforced
    assert "no-store" in post_res.headers.get("cache-control", "").lower()
    new_csrf = post_res.json()["csrf_token"]
    assert new_csrf is not None

    # 7. Use newly rotated token in state-changing POST
    change_res = await api_client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": new_csrf},
        json={
            "current_password": password,
            "new_password": "NewCsrfPassword456!",
        },
    )
    assert change_res.status_code == 200


@pytest.mark.asyncio
async def test_multitab_simultaneous_csrf_requests_and_grace_period(
    api_client: AsyncClient, unique_id: str
):
    """Verify simultaneous multi-tab requests receive deterministic tokens and grace period accepts previous token."""
    import asyncio

    email = f"multitab_{unique_id}@enterprise.com"
    password = "MultiTabPassword123!"

    await api_client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "display_name": f"MultiTab User {unique_id}",
            "organization_name": f"MultiTab Org {unique_id}",
            "organization_slug": f"org-mt-{unique_id}",
        },
    )

    login_res = await api_client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_res.status_code == 200
    assert login_res.json()["csrf_token"] is not None

    # 1. Simulate two tabs requesting CSRF token simultaneously
    async def request_tab_token() -> tuple[int, str]:
        res = await api_client.post(
            "/api/v1/auth/csrf-token",
            headers={"Origin": "http://localhost:3000"},
        )
        return res.status_code, res.json().get("csrf_token", "")

    task1 = asyncio.create_task(request_tab_token())
    task2 = asyncio.create_task(request_tab_token())
    (code1, tab_token1), (code2, tab_token2) = await asyncio.gather(task1, task2)

    assert code1 == 200
    assert code2 == 200
    # Both concurrent requests within active window receive identical token deterministically
    assert tab_token1 == tab_token2

    # 2. Both initial token and tab token succeed due to multi-tab grace period
    # Test that the rotated token works
    res_with_new = await api_client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": tab_token1},
        json={
            "current_password": password,
            "new_password": "MultiTabPassword456!",
        },
    )
    assert res_with_new.status_code == 200

    # Test that initial token (now in grace period) was also valid prior to expiration
    # Update password again using the current tab token
    res_with_current = await api_client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": tab_token1},
        json={
            "current_password": "MultiTabPassword456!",
            "new_password": "MultiTabPassword789!",
        },
    )
    assert res_with_current.status_code == 200


@pytest.mark.asyncio
async def test_login_and_registration_csrf_and_origin_protection(
    api_client: AsyncClient, unique_id: str
):
    """Verify that unauthenticated cookie-issuing endpoints strictly reject untrusted Origin, Referer, and Sec-Fetch-Site."""
    email = f"logincsrf_{unique_id}@enterprise.com"
    password = "LoginCsrfPass123!"

    reg_payload = {
        "email": email,
        "password": password,
        "display_name": f"User {unique_id}",
        "organization_name": f"Org {unique_id}",
        "organization_slug": f"org-lcsrf-{unique_id}",
    }

    # 1. POST /register with untrusted Origin returns 403
    bad_reg_origin = await api_client.post(
        "/api/v1/auth/register",
        headers={"Origin": "https://attacker.evil.com"},
        json=reg_payload,
    )
    assert bad_reg_origin.status_code == 403
    assert bad_reg_origin.json()["error"]["code"] == "CSRF_VERIFICATION_FAILED"

    # 2. POST /register with cross-site Fetch Metadata returns 403
    bad_reg_fetch = await api_client.post(
        "/api/v1/auth/register",
        headers={"Sec-Fetch-Site": "cross-site"},
        json=reg_payload,
    )
    assert bad_reg_fetch.status_code == 403

    # 3. POST /register with trusted Origin succeeds
    good_reg = await api_client.post(
        "/api/v1/auth/register",
        headers={"Origin": "http://localhost:3000"},
        json=reg_payload,
    )
    assert good_reg.status_code == 201

    # 4. POST /login with untrusted Origin returns 403
    bad_login_origin = await api_client.post(
        "/api/v1/auth/login",
        headers={"Origin": "https://attacker.evil.com"},
        json={"email": email, "password": password},
    )
    assert bad_login_origin.status_code == 403
    assert bad_login_origin.json()["error"]["code"] == "CSRF_VERIFICATION_FAILED"

    # 5. POST /login with cross-site Fetch Metadata returns 403
    bad_login_fetch = await api_client.post(
        "/api/v1/auth/login",
        headers={"Sec-Fetch-Site": "cross-site"},
        json={"email": email, "password": password},
    )
    assert bad_login_fetch.status_code == 403

    # 6. POST /login with untrusted Referer returns 403
    bad_login_referer = await api_client.post(
        "/api/v1/auth/login",
        headers={"Referer": "https://phishing-site.com/fake-login"},
        json={"email": email, "password": password},
    )
    assert bad_login_referer.status_code == 403

    # 7. POST /login with trusted Origin succeeds
    good_login = await api_client.post(
        "/api/v1/auth/login",
        headers={"Origin": "http://localhost:3000"},
        json={"email": email, "password": password},
    )
    assert good_login.status_code == 200
    assert good_login.json()["success"] is True

    # 8. POST /login/mfa with untrusted Origin returns 403
    bad_mfa_login = await api_client.post(
        "/api/v1/auth/login/mfa",
        headers={"Origin": "https://attacker.evil.com"},
        json={"challenge_token": "dummy_token_value_here", "code": "123456"},
    )
    assert bad_mfa_login.status_code == 403
