"""Unit tests for HMAC-SHA256 request authentication, V2 canonical binding, and replay protection."""

import json
import time
import uuid

import pytest

from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.services.renderer_auth import RendererAuthService


def test_hmac_v2_sign_and_verify_success():
    secret = "test_shared_secret_key"
    body = b'{"report_def": {"title": "Test"}}'
    tenant_id = str(uuid.uuid4())
    report_id = str(uuid.uuid4())

    sig, ts, nonce, version = RendererAuthService.generate_signature(
        secret=secret,
        method="POST",
        path="/render",
        body=body,
        tenant_id=tenant_id,
        report_id=report_id,
    )

    assert version == "2"

    # Verification must succeed with all matching fields
    RendererAuthService.verify_request(
        secret=secret,
        method="POST",
        path="/render",
        body=body,
        signature=sig,
        timestamp=ts,
        nonce=nonce,
        version=version,
        tenant_id=tenant_id,
        report_id=report_id,
    )


def test_hmac_verify_fails_missing_version():
    secret = "test_shared_secret_key"
    body = b'{"action": "test"}'
    sig, ts, nonce, _ = RendererAuthService.generate_signature(
        secret=secret,
        method="POST",
        path="/render",
        body=body,
    )

    with pytest.raises(AuthenticationError, match="Unsupported or missing signature version"):
        RendererAuthService.verify_request(
            secret=secret,
            method="POST",
            path="/render",
            body=body,
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=None,
        )


def test_hmac_verify_fails_unknown_version():
    secret = "test_shared_secret_key"
    body = b'{"action": "test"}'
    sig, ts, nonce, _ = RendererAuthService.generate_signature(
        secret=secret,
        method="POST",
        path="/render",
        body=body,
    )

    with pytest.raises(AuthenticationError, match="Unsupported or missing signature version: '3'"):
        RendererAuthService.verify_request(
            secret=secret,
            method="POST",
            path="/render",
            body=body,
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version="3",
        )


def test_hmac_verify_fails_legacy_v1_signature():
    secret = "test_shared_secret_key"
    body = b'{"action": "legacy"}'
    ts = int(time.time())
    nonce = "test-nonce-1"

    # Legacy V1 signature format
    msg_v1 = f"{ts}:{nonce}:POST:/render:".encode() + body
    import hashlib
    import hmac

    sig_v1 = hmac.new(secret.encode("utf-8"), msg_v1, hashlib.sha256).hexdigest()

    # Even if client claims version 2, the V1 signature payload is rejected
    with pytest.raises(AuthenticationError, match="Invalid renderer HMAC signature"):
        RendererAuthService.verify_request(
            secret=secret,
            method="POST",
            path="/render",
            body=body,
            signature=sig_v1,
            timestamp=ts,
            nonce=nonce,
            version="2",
        )


def test_hmac_verify_fails_tampered_tenant_header():
    secret = "test_shared_secret_key"
    body = b'{"action": "test"}'
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())
    report_id = str(uuid.uuid4())

    sig, ts, nonce, version = RendererAuthService.generate_signature(
        secret=secret,
        method="POST",
        path="/render",
        body=body,
        tenant_id=tenant_a,
        report_id=report_id,
    )

    with pytest.raises(AuthenticationError, match="Invalid renderer HMAC signature"):
        RendererAuthService.verify_request(
            secret=secret,
            method="POST",
            path="/render",
            body=body,
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=version,
            tenant_id=tenant_b,  # Tampered tenant!
            report_id=report_id,
        )


def test_hmac_verify_fails_tampered_report_header():
    secret = "test_shared_secret_key"
    body = b'{"action": "test"}'
    tenant_id = str(uuid.uuid4())
    report_a = str(uuid.uuid4())
    report_b = str(uuid.uuid4())

    sig, ts, nonce, version = RendererAuthService.generate_signature(
        secret=secret,
        method="POST",
        path="/render",
        body=body,
        tenant_id=tenant_id,
        report_id=report_a,
    )

    with pytest.raises(AuthenticationError, match="Invalid renderer HMAC signature"):
        RendererAuthService.verify_request(
            secret=secret,
            method="POST",
            path="/render",
            body=body,
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=version,
            tenant_id=tenant_id,
            report_id=report_b,  # Tampered report!
        )


def test_hmac_verify_fails_tampered_body():
    secret = "test_shared_secret_key"
    body = b'{"report_def": {"title": "Test"}}'
    sig, ts, nonce, version = RendererAuthService.generate_signature(
        secret=secret,
        method="POST",
        path="/render",
        body=body,
    )

    with pytest.raises(AuthenticationError, match="Invalid renderer HMAC signature"):
        RendererAuthService.verify_request(
            secret=secret,
            method="POST",
            path="/render",
            body=b'{"report_def": {"title": "Tampered"}}',
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=version,
        )


def test_hmac_verify_fails_altered_method():
    secret = "test_shared_secret_key"
    body = b'{"action": "test"}'
    sig, ts, nonce, version = RendererAuthService.generate_signature(
        secret=secret,
        method="POST",
        path="/render",
        body=body,
    )

    with pytest.raises(AuthenticationError, match="Invalid renderer HMAC signature"):
        RendererAuthService.verify_request(
            secret=secret,
            method="GET",
            path="/render",
            body=body,
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=version,
        )


def test_hmac_verify_fails_altered_path():
    secret = "test_shared_secret_key"
    body = b'{"action": "test"}'
    sig, ts, nonce, version = RendererAuthService.generate_signature(
        secret=secret,
        method="POST",
        path="/render",
        body=body,
    )

    with pytest.raises(AuthenticationError, match="Invalid renderer HMAC signature"):
        RendererAuthService.verify_request(
            secret=secret,
            method="POST",
            path="/cancel",
            body=body,
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=version,
        )


def test_hmac_verify_fails_clock_skew():
    secret = "test_shared_secret_key"
    body = b'{"action": "test"}'
    stale_ts = int(time.time()) - 60  # 60s in past

    sig, ts, nonce, version = RendererAuthService.generate_signature(
        secret=secret,
        method="POST",
        path="/render",
        body=body,
        timestamp=stale_ts,
    )

    with pytest.raises(AuthenticationError, match="timestamp skew exceeded"):
        RendererAuthService.verify_request(
            secret=secret,
            method="POST",
            path="/render",
            body=body,
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=version,
        )


def test_hmac_verify_fails_newline_injection():
    secret = "test_shared_secret_key"
    body = b'{"action": "test"}'

    with pytest.raises(
        AuthenticationError, match="Control characters or newlines not allowed in tenant_id"
    ):
        RendererAuthService.generate_signature(
            secret=secret,
            method="POST",
            path="/render",
            body=body,
            tenant_id="tenant-1\nINJECTED",
        )

    with pytest.raises(
        AuthenticationError, match="Control characters or newlines not allowed in report_id"
    ):
        RendererAuthService.generate_signature(
            secret=secret,
            method="POST",
            path="/render",
            body=body,
            report_id="report-1\r\nINJECTED",
        )


def test_hmac_verify_fails_replayed_nonce():
    secret = "test_shared_secret_key"
    body = b'{"action": "test"}'
    sig, ts, nonce, version = RendererAuthService.generate_signature(
        secret=secret,
        method="POST",
        path="/render",
        body=body,
    )

    # First call succeeds
    RendererAuthService.verify_request(
        secret=secret,
        method="POST",
        path="/render",
        body=body,
        signature=sig,
        timestamp=ts,
        nonce=nonce,
        version=version,
    )

    # Second call with same nonce must be rejected
    with pytest.raises(AuthenticationError, match="replay detected"):
        RendererAuthService.verify_request(
            secret=secret,
            method="POST",
            path="/render",
            body=body,
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=version,
        )


def test_production_config_rejects_legacy_v1_flag():
    """Verify that Settings validation rejects REPORT_RENDERER_ALLOW_LEGACY_V1 in production."""
    with pytest.raises(
        ValueError, match="REPORT_RENDERER_ALLOW_LEGACY_V1 is strictly prohibited in production"
    ):
        Settings(
            ENVIRONMENT="production",
            REPORT_RENDERER_ALLOW_LEGACY_V1=True,
            SESSION_SECRET_KEY="a" * 32,
            ENCRYPTION_MASTER_KEY="b" * 32,
            RATE_LIMIT_HMAC_KEY="c" * 32,
        )


@pytest.mark.asyncio
async def test_distributed_replay_with_real_redis():
    import redis.asyncio as aioredis

    from app.core.config import settings

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        await redis_client.ping()
    except Exception:
        pytest.skip("Real Redis instance not reachable for integration test")

    secret = "test_distributed_secret"
    body = b'{"distributed": true}'
    nonce = uuid.uuid4().hex
    tenant_id = str(uuid.uuid4())
    report_id = str(uuid.uuid4())

    sig, ts, _, version = RendererAuthService.generate_signature(
        secret=secret,
        method="POST",
        path="/render",
        body=body,
        tenant_id=tenant_id,
        report_id=report_id,
        nonce=nonce,
    )

    # First call must succeed
    await RendererAuthService.verify_request_async(
        secret=secret,
        method="POST",
        path="/render",
        body=body,
        signature=sig,
        timestamp=ts,
        nonce=nonce,
        version=version,
        tenant_id=tenant_id,
        report_id=report_id,
        redis_client=redis_client,
    )

    # Cross-replica replay simulation: a second independent client with same nonce must be rejected
    second_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    with pytest.raises(AuthenticationError, match="replay detected"):
        await RendererAuthService.verify_request_async(
            secret=secret,
            method="POST",
            path="/render",
            body=body,
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=version,
            tenant_id=tenant_id,
            report_id=report_id,
            redis_client=second_client,
        )

    # Replay after process restart simulation: clean local memory, re-query Redis
    RendererAuthService._seen_nonces.clear()
    third_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    with pytest.raises(AuthenticationError, match="replay detected"):
        await RendererAuthService.verify_request_async(
            secret=secret,
            method="POST",
            path="/render",
            body=body,
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=version,
            tenant_id=tenant_id,
            report_id=report_id,
            redis_client=third_client,
        )

    await redis_client.aclose()
    await second_client.aclose()
    await third_client.aclose()


@pytest.mark.asyncio
async def test_concurrent_duplicate_nonce_reservation():
    import asyncio

    import redis.asyncio as aioredis

    from app.core.config import settings

    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    try:
        await redis_client.ping()
    except Exception:
        pytest.skip("Real Redis instance not reachable for integration test")

    secret = "concurrent_secret"
    body = b'{"concurrent": true}'
    nonce = uuid.uuid4().hex
    sig, ts, _, version = RendererAuthService.generate_signature(
        secret=secret, method="POST", path="/render", body=body, nonce=nonce
    )

    async def attempt_call():
        c = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            await RendererAuthService.verify_request_async(
                secret=secret,
                method="POST",
                path="/render",
                body=body,
                signature=sig,
                timestamp=ts,
                nonce=nonce,
                version=version,
                redis_client=c,
            )
            return "success"
        except AuthenticationError:
            return "failed"
        finally:
            await c.aclose()

    results = await asyncio.gather(attempt_call(), attempt_call(), attempt_call())
    assert results.count("success") == 1
    assert results.count("failed") == 2

    await redis_client.aclose()


@pytest.mark.asyncio
async def test_production_fails_closed_when_redis_unavailable(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "REDIS_URL", "redis://invalid-unreachable-host:6379/0")

    secret = "prod_secret"
    body = b'{"prod": true}'
    sig, ts, nonce, version = RendererAuthService.generate_signature(
        secret=secret, method="POST", path="/render", body=body
    )

    # When Redis is unreachable in production, MUST fail closed
    with pytest.raises(AuthenticationError, match="Authentication storage unavailable"):
        await RendererAuthService.verify_request_async(
            secret=secret,
            method="POST",
            path="/render",
            body=body,
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=version,
            redis_client=None,
        )


@pytest.mark.asyncio
async def test_cancellation_endpoint_and_tenant_binding():
    from httpx import ASGITransport, AsyncClient

    from app.core.config import settings
    from app.services.renderer_app import active_render_runs, renderer_app

    run_id = "test-run-12345"
    org_id = str(uuid.uuid4())
    report_id = str(uuid.uuid4())

    # Register active run
    active_render_runs[run_id] = {
        "organization_id": org_id,
        "report_id": report_id,
    }

    transport = ASGITransport(app=renderer_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Unauthenticated cancellation fails (401)
        resp = await client.post("/cancel", json={"run_id": run_id})
        assert resp.status_code == 401

        # 2. Missing version header fails (401)
        bad_payload = json.dumps(
            {"run_id": run_id, "organization_id": org_id, "report_id": report_id}
        ).encode()
        sig, ts, nonce, _ = RendererAuthService.generate_signature(
            secret=settings.REPORT_RENDERER_SECRET,
            method="POST",
            path="/cancel",
            body=bad_payload,
            tenant_id=org_id,
            report_id=report_id,
        )
        headers_no_ver = {
            "Content-Type": "application/json",
            "X-Report-Signature": sig,
            "X-Report-Timestamp": str(ts),
            "X-Report-Nonce": nonce,
            "X-Tenant-Id": org_id,
            "X-Report-Id": report_id,
        }
        resp = await client.post("/cancel", content=bad_payload, headers=headers_no_ver)
        assert resp.status_code == 401
        assert "Missing signature version" in resp.json()["detail"]

        # 3. Duplicate header fails (400)
        resp = await client.post(
            "/cancel",
            content=bad_payload,
            headers=[
                ("Content-Type", "application/json"),
                ("X-Renderer-Signature-Version", "2"),
                ("X-Report-Signature", sig),
                ("X-Report-Timestamp", str(ts)),
                ("X-Report-Nonce", nonce),
                ("X-Tenant-Id", org_id),
                ("X-Tenant-Id", org_id),  # Duplicate!
                ("X-Report-Id", report_id),
            ],
        )
        assert resp.status_code == 400
        assert "Duplicate security header" in resp.json()["detail"]

        # 4. Authenticated but mismatched tenant (Cross-tenant attack) fails (403)
        evil_org = str(uuid.uuid4())
        evil_payload = json.dumps(
            {"run_id": run_id, "organization_id": evil_org, "report_id": report_id}
        ).encode()
        evil_sig, ts, nonce, version = RendererAuthService.generate_signature(
            secret=settings.REPORT_RENDERER_SECRET,
            method="POST",
            path="/cancel",
            body=evil_payload,
            tenant_id=evil_org,
            report_id=report_id,
        )
        headers_evil = {
            "Content-Type": "application/json",
            "X-Renderer-Signature-Version": version,
            "X-Report-Signature": evil_sig,
            "X-Report-Timestamp": str(ts),
            "X-Report-Nonce": nonce,
            "X-Tenant-Id": evil_org,
            "X-Report-Id": report_id,
        }
        resp = await client.post("/cancel", content=evil_payload, headers=headers_evil)
        assert resp.status_code == 403
        assert "Cross-tenant" in resp.json()["detail"]

        # 5. Authenticated valid cancellation succeeds (200)
        valid_payload = json.dumps(
            {"run_id": run_id, "organization_id": org_id, "report_id": report_id}
        ).encode()
        sig, ts, nonce, version = RendererAuthService.generate_signature(
            secret=settings.REPORT_RENDERER_SECRET,
            method="POST",
            path="/cancel",
            body=valid_payload,
            tenant_id=org_id,
            report_id=report_id,
        )
        headers_valid = {
            "Content-Type": "application/json",
            "X-Renderer-Signature-Version": version,
            "X-Report-Signature": sig,
            "X-Report-Timestamp": str(ts),
            "X-Report-Nonce": nonce,
            "X-Tenant-Id": org_id,
            "X-Report-Id": report_id,
        }
        resp = await client.post("/cancel", content=valid_payload, headers=headers_valid)
        assert resp.status_code == 200
        assert resp.json() == {"status": "cancelled", "run_id": run_id}
        assert run_id not in active_render_runs


@pytest.mark.asyncio
async def test_render_endpoint_and_tenant_binding():
    from httpx import ASGITransport, AsyncClient

    from app.core.config import settings
    from app.services.renderer_app import renderer_app

    org_id = str(uuid.uuid4())
    report_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())

    render_payload = {
        "report_def": {"id": report_id, "title": "Quarterly Financials", "page_size": "A4"},
        "run_dict": {"id": run_id, "organization_id": org_id},
        "snapshot_payload": {"sheets": []},
        "sections": [],
    }
    raw_payload = json.dumps(render_payload).encode()

    transport = ASGITransport(app=renderer_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Unauthenticated render fails (401)
        resp = await client.post("/render", content=raw_payload)
        assert resp.status_code == 401

        # 2. Header tenant mismatch with body organization_id fails (403)
        diff_tenant = str(uuid.uuid4())
        sig, ts, nonce, version = RendererAuthService.generate_signature(
            secret=settings.REPORT_RENDERER_SECRET,
            method="POST",
            path="/render",
            body=raw_payload,
            tenant_id=diff_tenant,
            report_id=report_id,
        )
        headers_mismatch = {
            "Content-Type": "application/json",
            "X-Renderer-Signature-Version": version,
            "X-Report-Signature": sig,
            "X-Report-Timestamp": str(ts),
            "X-Report-Nonce": nonce,
            "X-Tenant-Id": diff_tenant,
            "X-Report-Id": report_id,
        }
        resp = await client.post("/render", content=raw_payload, headers=headers_mismatch)
        assert resp.status_code == 403
        assert "Tenant ID header does not match" in resp.json()["detail"]

        # 3. Header report mismatch with body report_id fails (403)
        diff_report = str(uuid.uuid4())
        sig_rep, ts, nonce, version = RendererAuthService.generate_signature(
            secret=settings.REPORT_RENDERER_SECRET,
            method="POST",
            path="/render",
            body=raw_payload,
            tenant_id=org_id,
            report_id=diff_report,
        )
        headers_rep_mismatch = {
            "Content-Type": "application/json",
            "X-Renderer-Signature-Version": version,
            "X-Report-Signature": sig_rep,
            "X-Report-Timestamp": str(ts),
            "X-Report-Nonce": nonce,
            "X-Tenant-Id": org_id,
            "X-Report-Id": diff_report,
        }
        resp = await client.post("/render", content=raw_payload, headers=headers_rep_mismatch)
        assert resp.status_code == 403
        assert "Report ID header does not match" in resp.json()["detail"]
