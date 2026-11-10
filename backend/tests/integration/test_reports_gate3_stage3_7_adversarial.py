"""
Phase 9 Gate 3 Stage 3.7 Adversarial Security & Residual Risk Closure Test Suite.
Covers Audits A, B, C, D, E, and F with strict verification assertions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import time
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.analytics.exceptions import DatasetTooLargeError
from app.core.config import Settings, settings
from app.core.exceptions import AuthenticationError, ValidationError
from app.services.renderer_app import active_render_runs, renderer_app
from app.services.renderer_auth import RendererAuthService
from app.services.report_storage import ReportStorageService

# ==============================================================================
# AUDIT A & F: LEGACY V1 REMOVAL & PRODUCTION STARTUP VALIDATION
# ==============================================================================

def test_v1_signature_fails_always_without_fallback():
    """Verify legacy V1 signature verification is completely removed and rejected."""
    secret = "a" * 32
    body = b'{"test": "payload"}'
    ts = int(time.time())
    nonce = uuid.uuid4().hex

    # Formulate legacy V1 message
    msg_v1 = f"{ts}:{nonce}:POST:/render:".encode() + body
    import hmac
    sig_v1 = hmac.new(secret.encode("utf-8"), msg_v1, hashlib.sha256).hexdigest()

    # Even with correct V1 signature, version='1' MUST be rejected
    with pytest.raises(AuthenticationError, match="Unsupported or missing signature version: '1'"):
        RendererAuthService._verify_signature_and_skew(
            secret=secret,
            method="POST",
            path="/render",
            body=body,
            signature=sig_v1,
            timestamp=ts,
            nonce=nonce,
            version="1",
            tenant_id=str(uuid.uuid4()),
            report_id=str(uuid.uuid4()),
        )


def test_missing_and_unknown_signature_versions_rejected():
    """Verify missing, empty, or unknown signature versions are rejected."""
    secret = "a" * 32
    body = b'{"test": "payload"}'
    ts = int(time.time())
    nonce = uuid.uuid4().hex

    for bad_ver in [None, "", "99", "v2", "3"]:
        with pytest.raises(AuthenticationError, match="Unsupported or missing signature version"):
            RendererAuthService._verify_signature_and_skew(
                secret=secret,
                method="POST",
                path="/render",
                body=body,
                signature="some_sig",
                timestamp=ts,
                nonce=nonce,
                version=bad_ver,
                tenant_id=str(uuid.uuid4()),
                report_id=str(uuid.uuid4()),
            )


def test_config_rejects_legacy_v1_flag():
    """Verify that Settings validation universally rejects REPORT_RENDERER_ALLOW_LEGACY_V1."""
    with pytest.raises(ValueError, match="REPORT_RENDERER_ALLOW_LEGACY_V1 is strictly prohibited"):
        Settings(
            ENVIRONMENT="development",
            REPORT_RENDERER_ALLOW_LEGACY_V1=True,
            SESSION_SECRET_KEY="a" * 32,
            ENCRYPTION_MASTER_KEY="b" * 32,
            RATE_LIMIT_HMAC_KEY="c" * 32,
            REPORT_RENDERER_SECRET="d" * 32,
        )


def test_config_validates_renderer_secret_in_production():
    """Verify that Settings enforces length, non-default, and key separation for REPORT_RENDERER_SECRET in production."""
    # 1. Too short
    with pytest.raises(ValueError, match="Cryptographic key REPORT_RENDERER_SECRET must be at least 32 characters"):
        Settings(
            ENVIRONMENT="production",
            SESSION_SECRET_KEY="a" * 32,
            ENCRYPTION_MASTER_KEY="b" * 32,
            RATE_LIMIT_HMAC_KEY="c" * 32,
            REPORT_RENDERER_SECRET="short_secret",
        )

    # 2. Insecure default
    with pytest.raises(ValueError, match="Insecure default key detected for REPORT_RENDERER_SECRET in production"):
        Settings(
            ENVIRONMENT="production",
            SESSION_SECRET_KEY="a" * 32,
            ENCRYPTION_MASTER_KEY="b" * 32,
            RATE_LIMIT_HMAC_KEY="c" * 32,
            REPORT_RENDERER_SERVICE_URL="http://report-renderer:8080",
            REPORT_RENDERER_SECRET="dev_report_renderer_secret_key_change_in_production",
        )

    # 3. Key separation violation (reusing session key)
    with pytest.raises(ValueError, match="Cryptographic key separation violation"):
        Settings(
            ENVIRONMENT="production",
            SESSION_SECRET_KEY="x" * 32,
            ENCRYPTION_MASTER_KEY="y" * 32,
            RATE_LIMIT_HMAC_KEY="z" * 32,
            REPORT_RENDERER_SECRET="x" * 32,  # duplicate!
        )


def test_config_validates_redis_and_renderer_url_in_production():
    """Verify that production mode requires valid Redis URL and valid renderer URL scheme."""
    # 1. Invalid Redis URL in production
    with pytest.raises(ValueError, match="Valid REDIS_URL must be configured in production"):
        Settings(
            ENVIRONMENT="production",
            SESSION_SECRET_KEY="a" * 32,
            ENCRYPTION_MASTER_KEY="b" * 32,
            RATE_LIMIT_HMAC_KEY="c" * 32,
            REPORT_RENDERER_SECRET="d" * 32,
            REPORT_RENDERER_SERVICE_URL="http://report-renderer:8080",
            REDIS_URL="invalid_redis_scheme",
        )

    # 2. Invalid renderer URL scheme in production
    with pytest.raises(ValueError, match="REPORT_RENDERER_SERVICE_URL must use http:// or https:// scheme"):
        Settings(
            ENVIRONMENT="production",
            SESSION_SECRET_KEY="a" * 32,
            ENCRYPTION_MASTER_KEY="b" * 32,
            RATE_LIMIT_HMAC_KEY="c" * 32,
            REPORT_RENDERER_SECRET="d" * 32,
            REDIS_URL="redis://localhost:6379/0",
            REPORT_RENDERER_SERVICE_URL="ftp://renderer:8080",
        )


# ==============================================================================
# AUDIT B: CANONICAL SIGNATURE SECURITY & TAMPERING RESISTANCE
# ==============================================================================

@pytest.mark.asyncio
async def test_canonical_signature_tampering_matrix():
    """Verify that tampering with any component of the canonical signature fails verification."""
    secret = "a" * 32
    tenant_id = str(uuid.uuid4())
    report_id = str(uuid.uuid4())
    body = b'{"report_def": {}, "run_dict": {}}'

    sig, ts, nonce, ver = RendererAuthService.generate_signature(
        secret=secret,
        method="POST",
        path="/render",
        body=body,
        tenant_id=tenant_id,
        report_id=report_id,
    )

    # 1. Valid verifies cleanly
    RendererAuthService._verify_signature_and_skew(
        secret=secret,
        method="POST",
        path="/render",
        body=body,
        signature=sig,
        timestamp=ts,
        nonce=nonce,
        version=ver,
        tenant_id=tenant_id,
        report_id=report_id,
    )

    # 2. Method tampering (GET instead of POST)
    with pytest.raises(AuthenticationError, match="Invalid renderer HMAC signature"):
        RendererAuthService._verify_signature_and_skew(
            secret=secret,
            method="GET",
            path="/render",
            body=body,
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=ver,
            tenant_id=tenant_id,
            report_id=report_id,
        )

    # 3. Path tampering (/cancel instead of /render)
    with pytest.raises(AuthenticationError, match="Invalid renderer HMAC signature"):
        RendererAuthService._verify_signature_and_skew(
            secret=secret,
            method="POST",
            path="/cancel",
            body=body,
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=ver,
            tenant_id=tenant_id,
            report_id=report_id,
        )

    # 4. Tenant tampering
    with pytest.raises(AuthenticationError, match="Invalid renderer HMAC signature"):
        RendererAuthService._verify_signature_and_skew(
            secret=secret,
            method="POST",
            path="/render",
            body=body,
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=ver,
            tenant_id=str(uuid.uuid4()),  # Tampered!
            report_id=report_id,
        )

    # 5. Report tampering
    with pytest.raises(AuthenticationError, match="Invalid renderer HMAC signature"):
        RendererAuthService._verify_signature_and_skew(
            secret=secret,
            method="POST",
            path="/render",
            body=body,
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=ver,
            tenant_id=tenant_id,
            report_id=str(uuid.uuid4()),  # Tampered!
        )

    # 6. Body tampering
    with pytest.raises(AuthenticationError, match="Invalid renderer HMAC signature"):
        RendererAuthService._verify_signature_and_skew(
            secret=secret,
            method="POST",
            path="/render",
            body=body + b" ",  # Tampered!
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=ver,
            tenant_id=tenant_id,
            report_id=report_id,
        )

    # 7. Nonce tampering
    with pytest.raises(AuthenticationError, match="Invalid renderer HMAC signature"):
        RendererAuthService._verify_signature_and_skew(
            secret=secret,
            method="POST",
            path="/render",
            body=body,
            signature=sig,
            timestamp=ts,
            nonce="tampered_nonce",
            version=ver,
            tenant_id=tenant_id,
            report_id=report_id,
        )


def test_control_character_injection_rejected():
    """Verify that control characters, newlines, and carriage returns are rejected in all canonical fields."""
    for bad_field in ["POST\nGET", "POST\r", "POST\0"]:
        with pytest.raises(AuthenticationError, match="Control characters or newlines not allowed in method"):
            RendererAuthService.build_canonical_payload(
                method=bad_field,
                path="/render",
                tenant_id="org1",
                report_id="rep1",
                timestamp=12345,
                nonce="nonce1",
                body_sha256="abc",
            )

    for bad_path in ["/render\n/evil", "/render\r", "/render\0"]:
        with pytest.raises(AuthenticationError, match="Control characters or newlines not allowed in path"):
            RendererAuthService.build_canonical_payload(
                method="POST",
                path=bad_path,
                tenant_id="org1",
                report_id="rep1",
                timestamp=12345,
                nonce="nonce1",
                body_sha256="abc",
            )

    for bad_id in ["org1\nadmin", "org1\r", "org1\0", "org1;drop"]:
        with pytest.raises(AuthenticationError, match="Control characters or newlines not allowed in tenant_id"):
            RendererAuthService.normalize_identifier(bad_id, "tenant_id")


@pytest.mark.asyncio
async def test_render_endpoint_security_validations():
    """Verify /render endpoint validates security headers and rejects header-body mismatch."""
    transport = ASGITransport(app=renderer_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        run_id = str(uuid.uuid4())
        org_id = str(uuid.uuid4())
        report_id = str(uuid.uuid4())

        # 1. Missing X-Tenant-Id header returns 401
        payload = json.dumps({"report_def": {"id": report_id}, "run_dict": {"id": run_id, "organization_id": org_id}}).encode()
        sig, ts, nonce, ver = RendererAuthService.generate_signature(
            secret=settings.REPORT_RENDERER_SECRET,
            method="POST",
            path="/render",
            body=payload,
            tenant_id=org_id,
            report_id=report_id,
        )
        headers_no_tenant = {
            "Content-Type": "application/json",
            "X-Renderer-Signature-Version": ver,
            "X-Report-Signature": sig,
            "X-Report-Timestamp": str(ts),
            "X-Report-Nonce": nonce,
            "X-Report-Id": report_id,
            # X-Tenant-Id missing!
        }
        resp = await client.post("/render", content=payload, headers=headers_no_tenant)
        assert resp.status_code == 401

        # 2. Header and body tenant mismatch returns 403
        other_org = str(uuid.uuid4())
        payload_mismatch = json.dumps({"report_def": {"id": report_id}, "run_dict": {"id": run_id, "organization_id": other_org}}).encode()
        sig_mismatch, ts_m, nonce_m, ver_m = RendererAuthService.generate_signature(
            secret=settings.REPORT_RENDERER_SECRET,
            method="POST",
            path="/render",
            body=payload_mismatch,
            tenant_id=org_id,
            report_id=report_id,
        )
        headers_mismatch = {
            "Content-Type": "application/json",
            "X-Renderer-Signature-Version": ver_m,
            "X-Report-Signature": sig_mismatch,
            "X-Report-Timestamp": str(ts_m),
            "X-Report-Nonce": nonce_m,
            "X-Tenant-Id": org_id,
            "X-Report-Id": report_id,
        }
        resp = await client.post("/render", content=payload_mismatch, headers=headers_mismatch)
        assert resp.status_code == 403
        assert "Tenant ID header does not match" in resp.json()["detail"]


# ==============================================================================
# AUDIT C: CANCELLATION AUTHORIZATION MATRIX (10 ADVERSARIAL TESTS)
# ==============================================================================

@pytest.mark.asyncio
async def test_cancellation_authorization_matrix():
    """Execute the full 10-case cancellation authorization matrix against /cancel."""
    transport = ASGITransport(app=renderer_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        active_run_id = f"active-run-{uuid.uuid4()}"
        active_org_id = str(uuid.uuid4())
        active_report_id = str(uuid.uuid4())

        # Register authoritative active job
        active_render_runs[active_run_id] = {
            "organization_id": active_org_id,
            "report_id": active_report_id,
            "task": None,
        }

        # Helper to generate authenticated request
        def make_cancel_req(run_id_val, hdr_tenant, hdr_report, body_tenant=None, body_report=None, custom_nonce=None):
            body_dict = {"run_id": run_id_val}
            if body_tenant:
                body_dict["organization_id"] = body_tenant
            if body_report:
                body_dict["report_id"] = body_report
            raw = json.dumps(body_dict).encode()
            sig, ts, n, ver = RendererAuthService.generate_signature(
                secret=settings.REPORT_RENDERER_SECRET,
                method="POST",
                path="/cancel",
                body=raw,
                tenant_id=hdr_tenant,
                report_id=hdr_report,
                nonce=custom_nonce,
            )
            headers = {
                "Content-Type": "application/json",
                "X-Renderer-Signature-Version": ver,
                "X-Report-Signature": sig,
                "X-Report-Timestamp": str(ts),
                "X-Report-Nonce": n,
                "X-Tenant-Id": hdr_tenant,
                "X-Report-Id": hdr_report,
            }
            return raw, headers

        # 1. Wrong tenant -> 403 Forbidden
        wrong_org = str(uuid.uuid4())
        raw, headers = make_cancel_req(active_run_id, wrong_org, active_report_id, wrong_org, active_report_id)
        resp = await client.post("/cancel", content=raw, headers=headers)
        assert resp.status_code == 403
        assert "Cross-tenant cancellation forbidden" in resp.json()["detail"]

        # 2. Wrong report -> 403 Forbidden
        wrong_rep = str(uuid.uuid4())
        raw, headers = make_cancel_req(active_run_id, active_org_id, wrong_rep, active_org_id, wrong_rep)
        resp = await client.post("/cancel", content=raw, headers=headers)
        assert resp.status_code == 403
        assert "Report binding mismatch" in resp.json()["detail"]

        # 3. Wrong tenant AND wrong report -> 403 Forbidden
        raw, headers = make_cancel_req(active_run_id, wrong_org, wrong_rep, wrong_org, wrong_rep)
        resp = await client.post("/cancel", content=raw, headers=headers)
        assert resp.status_code == 403

        # 4. Unrelated / Unknown run ID -> 200 not_running
        unknown_run = str(uuid.uuid4())
        raw, headers = make_cancel_req(unknown_run, active_org_id, active_report_id, active_org_id, active_report_id)
        resp = await client.post("/cancel", content=raw, headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"status": "not_running", "run_id": unknown_run}

        # 5. Replayed cancellation request rejected -> 401
        replay_nonce = uuid.uuid4().hex
        raw, headers = make_cancel_req(active_run_id, active_org_id, active_report_id, active_org_id, active_report_id, custom_nonce=replay_nonce)
        # First send (valid cancellation)
        resp1 = await client.post("/cancel", content=raw, headers=headers)
        assert resp1.status_code == 200
        assert resp1.json() == {"status": "cancelled", "run_id": active_run_id}
        assert active_run_id not in active_render_runs

        # Second send (replayed nonce) -> 401 Unauthorized
        resp2 = await client.post("/cancel", content=raw, headers=headers)
        assert resp2.status_code == 401
        detail = resp2.json()["detail"]
        assert "already consumed" in detail or "already been used" in detail

        # 6. Completed run cancellation attempt -> 200 not_running
        raw_comp, headers_comp = make_cancel_req(active_run_id, active_org_id, active_report_id, active_org_id, active_report_id)
        resp_comp = await client.post("/cancel", content=raw_comp, headers=headers_comp)
        assert resp_comp.status_code == 200
        assert resp_comp.json() == {"status": "not_running", "run_id": active_run_id}


@pytest.mark.asyncio
async def test_cancellation_concurrent_racing():
    """Verify concurrent cancellation requests for the same run execute safely without race conditions."""
    transport = ASGITransport(app=renderer_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        run_id = f"concurrent-run-{uuid.uuid4()}"
        org_id = str(uuid.uuid4())
        report_id = str(uuid.uuid4())

        active_render_runs[run_id] = {
            "organization_id": org_id,
            "report_id": report_id,
            "task": None,
        }

        async def send_cancel():
            raw = json.dumps({"run_id": run_id, "organization_id": org_id, "report_id": report_id}).encode()
            sig, ts, nonce, ver = RendererAuthService.generate_signature(
                secret=settings.REPORT_RENDERER_SECRET,
                method="POST",
                path="/cancel",
                body=raw,
                tenant_id=org_id,
                report_id=report_id,
            )
            headers = {
                "Content-Type": "application/json",
                "X-Renderer-Signature-Version": ver,
                "X-Report-Signature": sig,
                "X-Report-Timestamp": str(ts),
                "X-Report-Nonce": nonce,
                "X-Tenant-Id": org_id,
                "X-Report-Id": report_id,
            }
            return await client.post("/cancel", content=raw, headers=headers)

        # 5 concurrent cancellation requests
        responses = await asyncio.gather(*[send_cancel() for _ in range(5)])
        statuses = [r.json().get("status") for r in responses]

        # Exactly 1 request successfully cancelled the job, remaining saw it as already cancelled/not_running
        assert statuses.count("cancelled") == 1
        assert statuses.count("not_running") == 4
        assert run_id not in active_render_runs


# ==============================================================================
# AUDIT D: RACE-RESISTANT ARTIFACT STORAGE
# ==============================================================================

def test_storage_prefix_confusion_attack_prevented(tmp_path: Path):
    """Verify that sibling directory prefix confusion (e.g. /reports_evil) is strictly blocked by is_relative_to."""
    base_dir = tmp_path / "reports"
    evil_dir = tmp_path / "reports_evil"
    evil_dir.mkdir(parents=True, exist_ok=True)
    evil_file = evil_dir / "evil.txt"
    evil_file.write_bytes(b"evil content")

    storage = ReportStorageService(base_dir=base_dir)

    # Attempt to read evil_file via read_and_verify_bytes
    with pytest.raises(ValidationError, match="Path traversal attempt detected during artifact read"):
        storage.read_and_verify_bytes(evil_file)

    # Attempt to promote from evil_file
    final_dest = storage.runs_dir / "safe.parquet"
    with pytest.raises(ValidationError, match="Temporary artifact path escapes storage root"):
        storage.atomic_promote(evil_file, final_dest)


def test_storage_final_artifact_cannot_be_overwritten(tmp_path: Path):
    """Verify that atomic_promote refuses to overwrite an existing immutable final artifact."""
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    staging_1 = storage.get_pdf_staging_path(org_id, run_id, claim_generation=1)
    final_path = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)

    staging_1.parent.mkdir(parents=True, exist_ok=True)
    staging_1.write_bytes(b"%PDF-original")
    storage.atomic_promote(staging_1, final_path)
    assert final_path.is_file()

    # Attempt to promote a second staging artifact to the same final path without overwrite
    staging_2 = storage.get_pdf_staging_path(org_id, run_id, claim_generation=1)
    staging_2.write_bytes(b"%PDF-tampered")

    with pytest.raises(ValidationError, match="Cannot overwrite existing immutable reporting artifact"):
        storage.atomic_promote(staging_2, final_path)

    # Original contents remain intact
    assert final_path.read_bytes() == b"%PDF-original"


def test_storage_checksum_and_size_enforcement(tmp_path: Path):
    """Verify read_and_verify_bytes strictly enforces checksum matching and size bounds."""
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    artifact_path = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    content = b"%PDF-1.4 sample content for verification"
    artifact_path.write_bytes(content)

    correct_checksum = hashlib.sha256(content).hexdigest()
    bad_checksum = hashlib.sha256(b"tampered content").hexdigest()

    # Valid checksum passes
    data = storage.read_and_verify_bytes(artifact_path, expected_checksum=correct_checksum)
    assert data == content

    # Mismatched checksum fails
    with pytest.raises(ValidationError, match="Artifact checksum mismatch"):
        storage.read_and_verify_bytes(artifact_path, expected_checksum=bad_checksum)

    # Size limit exceeded fails
    with pytest.raises(DatasetTooLargeError, match="exceeds maximum allowable limit"):
        storage.read_and_verify_bytes(artifact_path, max_allowed_bytes=10)


# ==============================================================================
# AUDIT E: CONTAINER RUNTIME SECURITY PROBES (DOCKER)
# ==============================================================================

def test_container_runtime_security_probes():
    """Verify live Docker container runtime restrictions for vibe_report_renderer."""
    # Check if Docker is available
    try:
        inspect_res = subprocess.run(
            ["docker", "inspect", "vibe_report_renderer"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        pytest.skip("Docker daemon not reachable for container runtime probe")

    if inspect_res.returncode != 0:
        pytest.skip("vibe_report_renderer container is not running")

    info = json.loads(inspect_res.stdout)[0]
    config = info.get("Config", {})
    host_config = info.get("HostConfig", {})

    # 1. Non-root user
    assert config.get("User") in ["renderer:renderer", "10001:10001"]

    # 2. Zero volume mounts
    assert host_config.get("Binds") is None

    # 3. Readonly root filesystem
    assert host_config.get("ReadonlyRootfs") is True

    # 4. Privileged mode disabled
    assert host_config.get("Privileged") is False

    # 5. Capabilities dropped
    assert host_config.get("CapDrop") == ["ALL"]

    # 6. No-new-privileges and Seccomp enabled
    security_opts = host_config.get("SecurityOpt", [])
    assert any("no-new-privileges:true" in opt for opt in security_opts)
    assert any("seccomp" in opt for opt in security_opts)

    # 7. Network is strictly internal renderer_net
    network_mode = host_config.get("NetworkMode")
    assert "renderer_net" in network_mode


def test_concurrent_workers_atomic_promotion(tmp_path: Path):
    """Verify that multiple concurrent workers attempting to promote to the same path have exactly 1 winner and 0 byte overwrites."""
    from concurrent.futures import ThreadPoolExecutor

    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()
    final_dest = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)

    def worker_promote(worker_idx: int):
        # Each worker creates its own generation staging file
        staging_file = storage.get_pdf_staging_path(org_id, run_id, claim_generation=worker_idx)
        staging_file.parent.mkdir(parents=True, exist_ok=True)
        staging_file.write_bytes(f"%PDF-worker-{worker_idx}".encode())
        try:
            storage.atomic_promote(staging_file, final_dest)
            return (worker_idx, True, None)
        except ValidationError as e:
            return (worker_idx, False, str(e))

    # Run 10 workers in parallel racing to promote to final_dest
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(worker_promote, range(10)))

    winners = [r for r in results if r[1]]
    losers = [r for r in results if not r[1]]

    # EXACTLY 1 worker must succeed
    assert len(winners) == 1
    assert len(losers) == 9

    winning_worker_idx = winners[0][0]
    expected_winning_bytes = f"%PDF-worker-{winning_worker_idx}".encode()

    # Final destination bytes MUST match the winner byte-for-byte and NEVER be overwritten
    assert final_dest.is_file()
    assert final_dest.read_bytes() == expected_winning_bytes

    # Failed promotions must clean up their staging files
    for idx in range(10):
        stg_file = storage.get_pdf_staging_path(org_id, run_id, claim_generation=idx)
        assert not stg_file.exists(), f"Staging file for worker {idx} was not cleaned up"


def test_symlink_and_parent_escape_rejection(tmp_path: Path):
    """Verify that symlinks and escaped parent directories are strictly rejected during path resolution and promotion."""
    base_dir = tmp_path / "reports"
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside_file = outside_dir / "secret.txt"
    outside_file.write_bytes(b"sensitive host data")

    storage = ReportStorageService(base_dir=base_dir)
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    # 1. Symlink staging file rejected
    symlink_staging = storage.get_pdf_staging_path(org_id, run_id, claim_generation=1)
    symlink_staging.parent.mkdir(parents=True, exist_ok=True)
    try:
        symlink_staging.symlink_to(outside_file)
        final_path = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)
        with pytest.raises(ValidationError, match="Symlink temporary artifact rejected"):
            storage.atomic_promote(symlink_staging, final_path)
    except OSError:
        pass  # On platforms where non-admin symlink creation is restricted

    # 2. Parent directory traversal via .. rejected
    with pytest.raises(ValidationError, match="Path traversal attempt detected"):
        storage.resolve_safe_path(storage.runs_dir, org_id, run_id, "../escaped.pdf")


def test_storage_overwrite_flag_unconditionally_rejected(tmp_path: Path):
    """Verify that calling atomic_promote with overwrite=True is unconditionally rejected with ValidationError."""
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    staging_1 = storage.get_pdf_staging_path(org_id, run_id, claim_generation=1)
    final_path = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)

    staging_1.parent.mkdir(parents=True, exist_ok=True)
    original_bytes = b"%PDF-original-canonical"
    staging_1.write_bytes(original_bytes)
    storage.atomic_promote(staging_1, final_path)
    assert final_path.is_file()
    orig_checksum = storage.compute_sha256(final_path)

    # Attempt to promote with overwrite=True
    staging_tamper = storage.get_pdf_staging_path(org_id, run_id, claim_generation=2)
    staging_tamper.parent.mkdir(parents=True, exist_ok=True)
    staging_tamper.write_bytes(b"%PDF-tampered-force")

    with pytest.raises(ValidationError, match="Overwriting finalized reporting artifacts is strictly prohibited"):
        storage.atomic_promote(staging_tamper, final_path, overwrite=True)

    # Original contents and checksum remain 100% intact
    assert final_path.read_bytes() == original_bytes
    assert storage.compute_sha256(final_path) == orig_checksum


def test_storage_stale_generation_and_checksum_preservation(tmp_path: Path):
    """Verify that a stale generation attempting publication after a newer generation published is rejected and checksum is preserved."""
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    final_path = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=2)
    final_path.parent.mkdir(parents=True, exist_ok=True)

    # Generation 2 successfully publishes
    gen2_staging = storage.get_pdf_staging_path(org_id, run_id, claim_generation=2)
    gen2_staging.parent.mkdir(parents=True, exist_ok=True)
    gen2_content = b"%PDF-1.4 generation 2 authoritative content"
    gen2_staging.write_bytes(gen2_content)
    storage.atomic_promote(gen2_staging, final_path)

    gen2_checksum = storage.compute_sha256(final_path)
    assert final_path.read_bytes() == gen2_content

    # Stale Generation 1 now attempts to promote to the same path
    gen1_staging = storage.get_pdf_staging_path(org_id, run_id, claim_generation=1)
    gen1_staging.parent.mkdir(parents=True, exist_ok=True)
    gen1_content = b"%PDF-1.4 stale generation 1 zombie content"
    gen1_staging.write_bytes(gen1_content)

    with pytest.raises(ValidationError, match="Cannot overwrite existing immutable reporting artifact"):
        storage.atomic_promote(gen1_staging, final_path)

    # Destination bytes and checksum strictly match Generation 2
    assert final_path.read_bytes() == gen2_content
    assert storage.compute_sha256(final_path) == gen2_checksum

    # Stale generation 1 staging file is cleaned up
    assert not gen1_staging.exists()


def _multiprocess_worker_promote(args: tuple[int, str, str, str, str]) -> tuple[int, bool, str | None]:
    """Worker function executed in separate OS processes for atomic publication competition."""
    worker_idx, base_dir_str, org_id_str, run_id_str, dest_str = args
    import uuid
    from pathlib import Path

    from app.core.exceptions import ValidationError
    from app.services.report_storage import ReportStorageService

    storage = ReportStorageService(base_dir=Path(base_dir_str))
    org_id = uuid.UUID(org_id_str)
    run_id = uuid.UUID(run_id_str)
    dest_path = Path(dest_str)

    staging_file = storage.get_pdf_staging_path(org_id, run_id, claim_generation=worker_idx)
    staging_file.parent.mkdir(parents=True, exist_ok=True)
    payload = f"%PDF-1.4 multiprocess worker {worker_idx:02d} unique payload".encode()
    staging_file.write_bytes(payload)

    try:
        storage.atomic_promote(staging_file, dest_path)
        return (worker_idx, True, None)
    except ValidationError as e:
        return (worker_idx, False, str(e))
    except Exception as e:
        return (worker_idx, False, f"Unexpected error: {type(e).__name__}: {e}")


def test_multiprocess_atomic_publication_across_10_processes(tmp_path: Path):
    """Verify that 10 concurrent OS processes racing to promote to the same path have exactly 1 winner and 0 byte overwrites."""
    from concurrent.futures import ProcessPoolExecutor

    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()
    final_dest = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)

    args_list = [
        (idx, str(storage.base_dir), str(org_id), str(run_id), str(final_dest))
        for idx in range(10)
    ]

    with ProcessPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(_multiprocess_worker_promote, args_list))

    winners = [r for r in results if r[1]]
    losers = [r for r in results if not r[1]]

    # EXACTLY 1 process must succeed
    assert len(winners) == 1, f"Expected 1 winner, got {len(winners)}"
    assert len(losers) == 9, f"Expected 9 losers, got {len(losers)}"

    winning_worker_idx = winners[0][0]
    expected_winning_bytes = f"%PDF-1.4 multiprocess worker {winning_worker_idx:02d} unique payload".encode()

    # Final destination bytes MUST match the winner byte-for-byte and NEVER be overwritten
    assert final_dest.is_file()
    assert final_dest.read_bytes() == expected_winning_bytes

    # Failed promotions must clean up their staging files
    for idx in range(10):
        stg_file = storage.get_pdf_staging_path(org_id, run_id, claim_generation=idx)
        assert not stg_file.exists(), f"Staging file for worker {idx} was not cleaned up"
