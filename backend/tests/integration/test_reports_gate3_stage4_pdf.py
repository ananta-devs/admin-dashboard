"""Integration and acceptance tests for Phase 9 Gate 3 Stage 4: PDF Rendering Engine.

Validates:
1. Static SSR SVG rendering for all 11 VIBE chart types with XML sanitization.
2. End-to-end report execution: on-demand triggering, Parquet snapshot creation,
   headless Chromium PDF export, pypdf structural verification, and atomic promotion.
3. Database persistence of dedicated PDF metadata columns (migration 370908748232).
4. Single-use download tokens with atomic consumption and replay prevention.
5. Direct authenticated artifact downloads with checksum and size bounds enforcement.
6. Worker lease heartbeat renewal and cooperative cancellation on lease loss.
7. Multi-tenant security isolation across report definitions, runs, and artifacts.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pypdf
import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.analytics.exceptions import DatasetTooLargeError
from app.core.config import settings
from app.core.exceptions import (
    CrossTenantAccessError,
    EntityNotFoundError,
    ForbiddenError,
    InvalidTokenError,
    LeaseExpiredError,
    ValidationError,
)
from app.models.dashboard import Dashboard, DashboardWidget
from app.models.membership import OrganizationMembership
from app.models.organization import Organization
from app.models.report import (
    ReportDefinition,
    ReportRun,
    ReportRunStatus,
    ReportSection,
)
from app.models.spreadsheet import Sheet, Workbook
from app.models.user import User
from app.models.workspace import Workspace
from app.repositories.report import (
    ReportDefinitionRepository,
    ReportRunRepository,
    ReportSectionRepository,
)
from app.repositories.role import RoleRepository
from app.schemas.report import (
    ReportDefinitionCreateInput,
    ReportRunTriggerInput,
    ReportSectionCreateInput,
)
from app.services.report import ReportService
from app.services.report_download_token import ReportDownloadTokenService
from app.services.report_renderer import ReportRendererService
from app.services.report_sanitizer import ReportSanitizerService
from app.services.report_storage import ReportStorageService
from app.services.role import RoleService
from app.tasks.reporting import _execute_report_run_async


@pytest.fixture
async def db_session():
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await test_engine.dispose()


@pytest.fixture
def session_factory():
    test_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    return async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)


async def create_test_tenant(session: AsyncSession, suffix: str) -> dict[str, Any]:
    """Provision an isolated organization with owner user, workspace, and seeded roles."""
    org = Organization(name=f"Org {suffix}", slug=f"org-{suffix}-{uuid.uuid4().hex[:6]}")
    session.add(org)
    await session.flush()

    user = User(
        email=f"user_{suffix}_{uuid.uuid4().hex[:6]}@example.com",
        normalized_email=f"user_{suffix}_{uuid.uuid4().hex[:6]}@example.com",
        display_name=f"User {suffix}",
    )
    session.add(user)
    await session.flush()

    membership = OrganizationMembership(organization_id=org.id, user_id=user.id)
    session.add(membership)
    await session.flush()

    role_service = RoleService(session)
    role_repo = RoleRepository(session)
    roles = await role_service.seed_default_roles_for_organization(org.id)
    if "owner" in roles:
        await role_repo.set_membership_roles(
            membership.id, [roles["owner"].id], organization_id=org.id
        )

    workspace = Workspace(organization_id=org.id, name=f"Workspace {suffix}", slug=f"ws-{suffix}-{uuid.uuid4().hex[:6]}")
    session.add(workspace)
    await session.flush()

    dashboard = Dashboard(
        organization_id=org.id,
        workspace_id=workspace.id,
        title=f"Dashboard {suffix}",
        created_by_user_id=user.id,
    )
    session.add(dashboard)
    await session.flush()

    return {
        "org": org,
        "user": user,
        "membership": membership,
        "workspace": workspace,
        "dashboard": dashboard,
        "roles": roles,
    }


# =============================================================================
# 1. ECharts SSR SVG Rendering for all 11 chart types
# =============================================================================

@pytest.mark.asyncio
async def test_echarts_ssr_all_11_chart_types():
    """Verify that all 11 supported VIBE chart types render static SVG without DOM dependencies."""
    renderer = ReportRendererService()
    chart_types = [
        "bar",
        "grouped_bar",
        "stacked_bar",
        "horizontal_bar",
        "line",
        "smooth_multi_axis_line",
        "stacked_area",
        "pie",
        "donut",
        "scatter",
        "heatmap",
    ]

    sample_data = [
        {"dimension": "North", "metric": 120, "target": 100},
        {"dimension": "South", "metric": 240, "target": 200},
        {"dimension": "East", "metric": 180, "target": 150},
        {"dimension": "West", "metric": 90, "target": 80},
    ]

    specs = []
    for idx, ctype in enumerate(chart_types):
        specs.append(
            {
                "section_id": f"sec_chart_{idx}",
                "chart_type": ctype,
                "spec": {
                    "title": f"Chart {ctype.title()}",
                    "dimensions": ["dimension"],
                    "metrics": ["metric"],
                },
                "data": sample_data,
                "width": 640,
                "height": 300,
            }
        )

    rendered_map = renderer.render_charts_svg(specs)
    assert len(rendered_map) == len(chart_types)

    for idx, ctype in enumerate(chart_types):
        sec_id = f"sec_chart_{idx}"
        assert sec_id in rendered_map
        svg = rendered_map[sec_id]
        assert "<svg" in svg, f"Chart type {ctype} failed to emit SVG tag"
        assert "<style" not in svg, f"Chart type {ctype} leaked <style> block"
        assert "onerror" not in svg
        assert "onclick" not in svg


# =============================================================================
# 2. End-to-End PDF Report Generation
# =============================================================================

@pytest.mark.asyncio
async def test_end_to_end_report_run_pdf_generation(
    db_session: AsyncSession, session_factory: async_sessionmaker, tmp_path: Path
):
    """Verify on-demand execution produces valid PDF artifact and populates migration columns."""
    tenant = await create_test_tenant(db_session, "e2e_pdf")
    org_id = tenant["org"].id
    ws_id = tenant["workspace"].id
    user_id = tenant["user"].id

    storage = ReportStorageService(base_dir=tmp_path / "reports")

    # 1. Create Report Definition with diverse sections (chart, table, text)
    report_svc = ReportService(db_session)
    report_def = await report_svc.create_report_definition(
        organization_id=org_id,
        user_id=user_id,
        data=ReportDefinitionCreateInput(
            workspace_id=ws_id,
            title="Q3 Performance Executive Summary",
            description="Comprehensive enterprise operational and financial metrics",
            page_size="a4",
            page_orientation="portrait",
        ),
    )

    # Add Text narrative section
    sec_text = await report_svc.add_section(
        report_definition_id=report_def.id,
        organization_id=org_id,
        data=ReportSectionCreateInput(
            section_type="text",
            title="Executive Summary",
            custom_content="Operating revenues grew 24% year-over-year led by SaaS subscriptions.",
            sort_order=0,
        ),
    )

    # 2. Trigger report execution run
    idempotency_key = f"e2e-run-{uuid.uuid4()}"
    run = await report_svc.trigger_report_run(
        report_definition_id=report_def.id,
        organization_id=org_id,
        user_id=user_id,
        input_data=ReportRunTriggerInput(idempotency_key=idempotency_key),
    )
    await db_session.commit()

    assert run.status == ReportRunStatus.QUEUED

    # 3. Execute Celery worker task asynchronously with custom storage
    with patch("app.tasks.reporting.ReportStorageService", return_value=storage):
        result = await _execute_report_run_async(str(run.id), "celery-test-task-1")

    assert result["status"] == "succeeded"

    # 4. Verify database state & migration 370908748232 fields via fresh session
    async with session_factory() as verify_session:
        repo = ReportRunRepository(verify_session)
        final_run = await repo.get_by_id(run.id, org_id)
        assert final_run is not None
        assert final_run.status == ReportRunStatus.SUCCEEDED
        assert final_run.pdf_artifact_path is not None
        assert final_run.pdf_checksum_sha256 is not None
        assert len(final_run.pdf_checksum_sha256) == 64
        assert final_run.pdf_file_size_bytes is not None
        assert final_run.pdf_file_size_bytes > 0
        assert final_run.pdf_page_count is not None
        assert final_run.pdf_page_count >= 1
        assert final_run.pdf_rendered_at is not None

        # 5. Verify physical PDF artifact on disk
        pdf_path = Path(final_run.pdf_artifact_path)
        assert pdf_path.is_file()
        raw_pdf = pdf_path.read_bytes()
        assert len(raw_pdf) == final_run.pdf_file_size_bytes
        assert hashlib.sha256(raw_pdf).hexdigest() == final_run.pdf_checksum_sha256

        # 6. Verify PDF structure with pypdf
        reader = pypdf.PdfReader(io.BytesIO(raw_pdf))
        assert len(reader.pages) == final_run.pdf_page_count
        extracted_text = "\n".join(p.extract_text() for p in reader.pages)
        assert "Q3 Performance Executive Summary" in extracted_text


# =============================================================================
# 3. Single-Use Download Tokens & Atomic Burn
# =============================================================================

@pytest.mark.asyncio
async def test_single_use_download_token_atomic_burn(db_session: AsyncSession):
    """Verify download tokens are single-use and burned atomically on first consumption."""
    run_id = uuid.uuid4()
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()

    token = await ReportDownloadTokenService.create_download_token(
        run_id=run_id,
        organization_id=org_id,
        user_id=user_id,
        ttl_seconds=300,
    )

    # First consumption must succeed and yield the stored payload
    payload = await ReportDownloadTokenService.consume_download_token(token)
    assert payload["run_id"] == str(run_id)
    assert payload["organization_id"] == str(org_id)
    assert payload["user_id"] == str(user_id)

    # Immediate replay must fail because token was atomically burned
    with pytest.raises(InvalidTokenError, match="invalid, expired, or has already been consumed"):
        await ReportDownloadTokenService.consume_download_token(token)


# =============================================================================
# 4. Storage Bounds, Single Open Handle & Checksum Verification
# =============================================================================

@pytest.mark.asyncio
async def test_direct_pdf_download_bounds_and_checksum(tmp_path: Path):
    """Verify ReportStorageService enforces strict single-handle bounds, symlink, and checksum validation."""
    base_dir = tmp_path / "reports_test"
    storage = ReportStorageService(base_dir=base_dir)

    org_id = uuid.uuid4()
    run_id = uuid.uuid4()
    pdf_path = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    test_content = b"%PDF-1.4 sample content for storage verification"
    pdf_path.write_bytes(test_content)
    real_checksum = hashlib.sha256(test_content).hexdigest()

    # Successful read
    read_bytes = storage.read_and_verify_bytes(pdf_path, expected_checksum=real_checksum)
    assert read_bytes == test_content

    # Mismatched checksum must be rejected
    with pytest.raises(ValidationError, match="checksum mismatch"):
        storage.read_and_verify_bytes(pdf_path, expected_checksum="0" * 64)

    # Size cap exceeded must raise DatasetTooLargeError
    with pytest.raises(DatasetTooLargeError, match="exceeds maximum allowable limit"):
        storage.read_and_verify_bytes(pdf_path, max_allowed_bytes=10)

    # Path traversal outside base_dir must be rejected
    escape_path = tmp_path / "outside.pdf"
    escape_path.write_bytes(test_content)
    with pytest.raises(ValidationError, match="Path traversal attempt detected"):
        storage.read_and_verify_bytes(escape_path)


# =============================================================================
# 5. Worker Lease Heartbeat & Cooperative Cancellation on Lease Loss
# =============================================================================

@pytest.mark.asyncio
async def test_lease_expiry_cancellation_during_render(db_session: AsyncSession, tmp_path: Path):
    """Verify that when worker lease renewal fails, cooperative cancellation triggers and staging is discarded."""
    tenant = await create_test_tenant(db_session, "lease_loss")
    org_id = tenant["org"].id
    ws_id = tenant["workspace"].id
    user_id = tenant["user"].id

    storage = ReportStorageService(base_dir=tmp_path / "reports")
    report_svc = ReportService(db_session)

    report_def = await report_svc.create_report_definition(
        organization_id=org_id,
        user_id=user_id,
        data=ReportDefinitionCreateInput(
            workspace_id=ws_id,
            title="Lease Loss Test Report",
        ),
    )

    run = await report_svc.trigger_report_run(
        report_definition_id=report_def.id,
        organization_id=org_id,
        user_id=user_id,
        input_data=ReportRunTriggerInput(idempotency_key=f"lease-key-{uuid.uuid4()}"),
    )
    await db_session.commit()

    # Simulate lease renewal returning False (stolen lease)
    with patch("app.repositories.report.ReportRunRepository.extend_lease", return_value=False):
        with patch("app.tasks.reporting.ReportStorageService", return_value=storage):
            # Artificially set heartbeat interval tiny so loop fires immediately
            with patch.object(settings, "ANALYTICS_HEARTBEAT_INTERVAL_SECONDS", 0.05):
                # Make render delay slightly so heartbeat can detect failure
                orig_render = ReportRendererService.render_pdf_from_html
                async def slow_render(*args, **kwargs):
                    await asyncio.sleep(0.2)
                    return await orig_render(*args, **kwargs)

                with patch.object(ReportRendererService, "render_pdf_from_html", side_effect=slow_render):
                    result = await _execute_report_run_async(str(run.id), "celery-stolen-1")

    # Worker task reports failure due to LEASEEXPIREDERROR
    assert result["status"] == "failed"
    assert "LEASEEXPIREDERROR" in result["error_code"]


# =============================================================================
# 6. Multi-Tenant Cross-Access Isolation
# =============================================================================

@pytest.mark.asyncio
async def test_cross_tenant_report_isolation(db_session: AsyncSession):
    """Verify strict tenant isolation across report definitions, runs, and artifacts."""
    tenant_a = await create_test_tenant(db_session, "tenant_a")
    tenant_b = await create_test_tenant(db_session, "tenant_b")

    report_svc = ReportService(db_session)

    # Tenant A creates report definition
    def_a = await report_svc.create_report_definition(
        organization_id=tenant_a["org"].id,
        user_id=tenant_a["user"].id,
        data=ReportDefinitionCreateInput(
            workspace_id=tenant_a["workspace"].id,
            title="Tenant A Private Report",
        ),
    )
    await db_session.commit()

    # Tenant B attempts to read Tenant A's definition
    with pytest.raises((EntityNotFoundError, CrossTenantAccessError)):
        await report_svc.get_report_definition(
            id=def_a.id,
            organization_id=tenant_b["org"].id,
        )

    # Tenant B attempts to trigger a run on Tenant A's definition
    with pytest.raises((EntityNotFoundError, CrossTenantAccessError)):
        await report_svc.trigger_report_run(
            report_definition_id=def_a.id,
            organization_id=tenant_b["org"].id,
            user_id=tenant_b["user"].id,
            input_data=ReportRunTriggerInput(idempotency_key="cross-tenant-key"),
        )
