"""Standalone HTTP microservice for the isolated Chromium report renderer container."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.exceptions import AuthenticationError
from app.services.renderer_auth import RendererAuthService
from app.services.report_renderer import ReportRendererService

logger = logging.getLogger(__name__)

renderer_app = FastAPI(
    title="VIBE Report Renderer Service",
    description="Isolated, zero-credential microservice for headless Chromium and ECharts SSR",
    version="1.0.0",
)

renderer_service = ReportRendererService()
active_render_runs: dict[str, dict[str, Any]] = {}

SECURITY_HEADERS = [
    "x-renderer-signature-version",
    "x-report-signature-version",
    "x-report-signature",
    "x-renderer-signature",
    "x-report-timestamp",
    "x-report-nonce",
    "x-tenant-id",
    "x-report-id",
]


def check_duplicate_security_headers(request: Request) -> str | None:
    """Return error string if any security header appears multiple times."""
    for header in SECURITY_HEADERS:
        values = request.headers.getlist(header)
        if len(values) > 1:
            return f"Duplicate security header '{header}' detected"
    return None


@renderer_app.get("/health")
async def health_check():
    """Health check endpoint for container probes."""
    return {"status": "ok", "service": "vibe-report-renderer"}


@renderer_app.post("/render")
async def render_report(request: Request):
    """
    Generate PDF report from signed declarative payload.
    Enforces HMAC-SHA256 V2 signature, tenant/report cryptographic binding,
    clock skew verification, and replay protection.
    """
    dup_error = check_duplicate_security_headers(request)
    if dup_error:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": dup_error},
        )

    raw_body = await request.body()
    sig = request.headers.get("X-Report-Signature") or request.headers.get("X-Renderer-Signature")
    ts_str = request.headers.get("X-Report-Timestamp")
    nonce = request.headers.get("X-Report-Nonce")
    version = request.headers.get("X-Renderer-Signature-Version") or request.headers.get("X-Report-Signature-Version")
    tenant_id_hdr = request.headers.get("X-Tenant-Id")
    report_id_hdr = request.headers.get("X-Report-Id")

    if not sig or not ts_str or not nonce:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Missing HMAC authentication headers"},
        )

    if not version:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Missing signature version header (X-Renderer-Signature-Version required)"},
        )

    try:
        ts = int(ts_str)
    except ValueError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Invalid timestamp header"},
        )

    # Verify HMAC signature and nonce replay protection
    try:
        await RendererAuthService.verify_request_async(
            secret=settings.REPORT_RENDERER_SECRET,
            method="POST",
            path="/render",
            body=raw_body,
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=version,
            tenant_id=tenant_id_hdr,
            report_id=report_id_hdr,
        )
    except AuthenticationError as e:
        logger.warning(f"Renderer authentication failed: {e}")
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(e)},
        )

    try:
        payload = json.loads(raw_body)
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": f"Malformed JSON payload: {e}"},
        )

    report_def = payload.get("report_def", {})
    run_dict = payload.get("run_dict", {})
    snapshot_payload = payload.get("snapshot_payload", {})
    sections = payload.get("sections", [])
    run_id = str(run_dict.get("id", ""))
    report_id = str(report_def.get("id", ""))
    org_id = str(run_dict.get("organization_id", ""))

    # Cross-validate signed headers against body payload
    norm_hdr_tenant = RendererAuthService.normalize_identifier(tenant_id_hdr, "X-Tenant-Id")
    norm_body_tenant = RendererAuthService.normalize_identifier(org_id, "body organization_id")
    if not norm_hdr_tenant or not norm_body_tenant or norm_hdr_tenant != norm_body_tenant:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": "Tenant ID header does not match payload organization_id"},
        )

    norm_hdr_report = RendererAuthService.normalize_identifier(report_id_hdr, "X-Report-Id")
    norm_body_report = RendererAuthService.normalize_identifier(report_id, "body report_id")
    if not norm_hdr_report or not norm_body_report or norm_hdr_report != norm_body_report:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": "Report ID header does not match payload report_id"},
        )

    effective_tenant = norm_hdr_tenant
    effective_report = norm_hdr_report

    if run_id:
        active_render_runs[run_id] = {
            "organization_id": effective_tenant,
            "report_id": effective_report,
            "task": asyncio.current_task(),
        }

    try:
        html = renderer_service.build_report_html(
            report_def=report_def,
            run_dict=run_dict,
            snapshot_payload=snapshot_payload,
            sections=sections,
        )

        pdf_bytes = await renderer_service.render_pdf_from_html(
            html_content=html,
            title=report_def.get("title"),
            page_size=report_def.get("page_size", "A4"),
        )

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{report_def.get("title", "report")}.pdf"',
                "Content-Length": str(len(pdf_bytes)),
            },
        )
    except asyncio.CancelledError:
        logger.info(f"Render job {run_id} cancelled during execution")
        return JSONResponse(
            status_code=499,
            content={"detail": f"Render job {run_id} cancelled by client"},
        )
    except Exception as e:
        logger.error(f"Render generation failed for run {run_id}: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": f"Rendering failure: {e}"},
        )
    finally:
        if run_id:
            active_render_runs.pop(run_id, None)


@renderer_app.post("/cancel")
async def cancel_render(request: Request):
    """
    Cooperative cancellation endpoint called when worker lease expires or job is aborted.
    Authenticated with HMAC-SHA256 V2 and verified for tenant and report binding.
    """
    dup_error = check_duplicate_security_headers(request)
    if dup_error:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": dup_error},
        )

    raw_body = await request.body()
    sig = request.headers.get("X-Report-Signature") or request.headers.get("X-Renderer-Signature")
    ts_str = request.headers.get("X-Report-Timestamp")
    nonce = request.headers.get("X-Report-Nonce")
    version = request.headers.get("X-Renderer-Signature-Version") or request.headers.get("X-Report-Signature-Version")
    tenant_id_hdr = request.headers.get("X-Tenant-Id")
    report_id_hdr = request.headers.get("X-Report-Id")

    if not sig or not ts_str or not nonce:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Missing HMAC authentication headers"},
        )

    if not version:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Missing signature version header (X-Renderer-Signature-Version required)"},
        )

    try:
        ts = int(ts_str)
        await RendererAuthService.verify_request_async(
            secret=settings.REPORT_RENDERER_SECRET,
            method="POST",
            path="/cancel",
            body=raw_body,
            signature=sig,
            timestamp=ts,
            nonce=nonce,
            version=version,
            tenant_id=tenant_id_hdr,
            report_id=report_id_hdr,
        )
    except AuthenticationError as e:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": str(e)},
        )
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(e)},
        )

    try:
        payload = json.loads(raw_body)
        run_id = str(payload.get("run_id", ""))
        org_id = str(payload.get("organization_id", ""))
        report_id = str(payload.get("report_id", ""))

        if not run_id:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "run_id is required for cancellation"},
            )

        norm_hdr_tenant = RendererAuthService.normalize_identifier(tenant_id_hdr, "X-Tenant-Id")
        norm_body_org = RendererAuthService.normalize_identifier(org_id, "body organization_id")
        if norm_hdr_tenant and norm_body_org and norm_hdr_tenant != norm_body_org:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Cross-tenant cancellation forbidden"},
            )

        norm_hdr_report = RendererAuthService.normalize_identifier(report_id_hdr, "X-Report-Id")
        norm_body_rep = RendererAuthService.normalize_identifier(report_id, "body report_id")
        if norm_hdr_report and norm_body_rep and norm_hdr_report != norm_body_rep:
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Report binding mismatch for cancellation"},
            )

        norm_tenant = norm_hdr_tenant or norm_body_org
        norm_report = norm_hdr_report or norm_body_rep

        if run_id in active_render_runs:
            registered = active_render_runs[run_id]
            # Cross-tenant and cross-report binding check against authoritative active job!
            if not norm_tenant or norm_tenant != registered.get("organization_id"):
                logger.warning(
                    f"Unauthorized cancellation attempt for run {run_id}: tenant {norm_tenant} != {registered.get('organization_id')}"
                )
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Cross-tenant cancellation forbidden"},
                )
            if not norm_report or norm_report != registered.get("report_id"):
                logger.warning(
                    f"Unauthorized cancellation attempt for run {run_id}: report {norm_report} != {registered.get('report_id')}"
                )
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Report binding mismatch for cancellation"},
                )

            task = registered.get("task")
            if task and not task.done():
                task.cancel()

            logger.info(f"Cancellation confirmed for active run {run_id}")
            del active_render_runs[run_id]
            return {"status": "cancelled", "run_id": run_id}

        return {"status": "not_running", "run_id": run_id}
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(e)},
        )
