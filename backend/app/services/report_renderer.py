"""Secure PDF rendering service and client for VIBE enterprise reports."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pypdf
from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import async_playwright

from app.core.config import settings
from app.core.exceptions import RenderingError, ValidationError
from app.services.renderer_auth import RendererAuthService
from app.services.report_sanitizer import ReportSanitizerService

logger = logging.getLogger(__name__)


class ReportRendererService:
    """Orchestrates HTML generation, static SVG chart rendering, and headless Chromium PDF export."""

    TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "reports"
    SSR_SCRIPT_PATH = (
        Path(__file__).resolve().parent.parent.parent
        / "renderers"
        / "echarts_ssr"
        / "render_charts.js"
    )

    def __init__(self) -> None:
        self.jinja_env = Environment(
            loader=FileSystemLoader(str(self.TEMPLATES_DIR)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def render_charts_svg(self, chart_specs: list[dict[str, Any]]) -> dict[str, str]:
        """
        Invoke the isolated Node.js ECharts SSR script to render SVGs for chart sections.
        Returns a dictionary mapping section_id to sanitized SVG string.
        """
        if not chart_specs:
            return {}

        payload = {"charts": chart_specs}
        try:
            proc = subprocess.run(
                ["node", str(self.SSR_SCRIPT_PATH)],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except Exception as e:
            logger.error(f"Failed to execute ECharts SSR script: {e}")
            raise RenderingError(f"ECharts SSR process invocation failed: {e}") from e

        if proc.returncode != 0:
            logger.error(f"ECharts SSR process failed: {proc.stderr}")
            raise RenderingError(f"ECharts SSR failed: {proc.stderr[:500]}")

        try:
            result_data = json.loads(proc.stdout)
        except Exception as e:
            logger.error(
                f"Invalid JSON returned from ECharts SSR: {e}, stdout: {proc.stdout[:200]}"
            )
            raise RenderingError("ECharts SSR emitted unparseable output") from e

        sanitized_svgs: dict[str, str] = {}
        for item in result_data.get("results", []):
            sec_id = item.get("section_id")
            if not sec_id:
                continue
            err = item.get("error")
            if err:
                logger.warning(f"Error rendering chart for section {sec_id}: {err}")
                sanitized_svgs[sec_id] = (
                    f'<div class="chart-error" style="color: #ef4444; padding: 20px; text-align: center;">'
                    f"Unable to render chart: {ReportSanitizerService.escape_text(err)}</div>"
                )
            else:
                raw_svg = item.get("svg") or ""
                try:
                    clean_svg = ReportSanitizerService.sanitize_svg(raw_svg)
                    sanitized_svgs[sec_id] = clean_svg
                except Exception as e:
                    logger.error(f"Sanitization failed for section {sec_id} SVG: {e}")
                    sanitized_svgs[sec_id] = (
                        '<div class="chart-error" style="color: #ef4444; padding: 20px; text-align: center;">'
                        "Chart SVG failed security validation.</div>"
                    )

        return sanitized_svgs

    def build_report_html(
        self,
        report_def: dict[str, Any],
        run_dict: dict[str, Any],
        snapshot_payload: dict[str, Any],
        sections: list[dict[str, Any]],
    ) -> str:
        """
        Assemble semantic HTML report using the Jinja2 template with pre-rendered sanitized SVGs.
        """
        # Separate chart sections to batch render
        chart_specs = []
        for s in sections:
            sec_type = s.get("section_type")
            if sec_type == "chart":
                chart_specs.append(
                    {
                        "section_id": str(s.get("id")),
                        "chart_type": s.get("chart_type", "bar"),
                        "spec": s.get("spec", {}),
                        "data": s.get("data", []),
                        "width": 680,
                        "height": 320,
                    }
                )

        rendered_charts = self.render_charts_svg(chart_specs)

        # Process each section for template presentation
        processed_sections = []
        for s in sections:
            sec_type = s.get("section_type")
            sec_id = str(s.get("id"))
            sec_data = s.get("data") or []
            page_break = bool(s.get("page_break_before", False))

            if sec_type == "chart":
                processed_sections.append(
                    {
                        "id": sec_id,
                        "section_type": "chart",
                        "title": s.get("title"),
                        "description": s.get("description"),
                        "page_break_before": page_break,
                        "svg_content": rendered_charts.get(sec_id, ""),
                    }
                )
            elif sec_type == "table":
                cols = s.get("columns", [])
                # If columns not explicitly given, infer from first row keys
                if not cols and sec_data and isinstance(sec_data, list):
                    first_row = sec_data[0]
                    if isinstance(first_row, dict):
                        cols = [
                            {"name": k, "label": k.replace("_", " ").title()}
                            for k in first_row.keys()
                        ]

                # Paginate table rows for PDF sanity (limit max rows in printed PDF)
                max_table_rows = 100
                total_rows = len(sec_data) if isinstance(sec_data, list) else 0
                display_rows_raw = sec_data[:max_table_rows] if isinstance(sec_data, list) else []

                # Format and sanitize each cell
                formatted_rows = []
                for row_data in display_rows_raw:
                    row_cells = []
                    if isinstance(row_data, dict):
                        for col in cols:
                            col_key = col.get("name") if isinstance(col, dict) else str(col)
                            val = row_data.get(col_key)
                            row_cells.append(ReportSanitizerService.sanitize_table_cell(val))
                    elif isinstance(row_data, list):
                        for val in row_data:
                            row_cells.append(ReportSanitizerService.sanitize_table_cell(val))
                    formatted_rows.append(row_cells)

                processed_sections.append(
                    {
                        "id": sec_id,
                        "section_type": "table",
                        "title": s.get("title"),
                        "description": s.get("description"),
                        "page_break_before": page_break,
                        "table_columns": cols,
                        "table_rows": formatted_rows,
                        "row_count": len(formatted_rows),
                        "total_source_rows": total_rows,
                        "is_truncated": total_rows > max_table_rows,
                    }
                )
            elif sec_type == "text":
                processed_sections.append(
                    {
                        "id": sec_id,
                        "section_type": "text",
                        "title": s.get("title"),
                        "description": s.get("description"),
                        "page_break_before": page_break,
                        "content": s.get("content", ""),
                    }
                )

        # Format timestamps
        gen_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")

        template = self.jinja_env.get_template("base_report.html")
        return template.render(
            report=report_def,
            run=run_dict,
            snapshot_metrics=snapshot_payload.get("metrics", {}),
            kpi_metrics=report_def.get("kpi_metrics", []),
            sections=processed_sections,
            generated_at_formatted=gen_at,
            triggered_by=run_dict.get("triggered_by", "system"),
            page_size=report_def.get("page_size", "A4"),
            page_orientation=report_def.get("page_orientation", "portrait"),
        )

    async def render_pdf_from_html(
        self, html_content: str, title: str | None = None, page_size: str = "A4"
    ) -> bytes:
        """
        Render HTML string directly to PDF bytes using Playwright headless Chromium.
        JavaScript execution inside Chromium is strictly disabled for defense in depth.
        """
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-gpu",
                    "--disable-software-rasterizer",
                    "--disable-extensions",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--allow-running-insecure-content=false",
                    "--disable-web-security=false",
                ],
            )
            try:
                context = await browser.new_context(
                    user_agent="VIBE-ReportRenderer/1.0",
                    java_script_enabled=False,  # Chromium acts strictly as an HTML/CSS layout engine
                )
                try:
                    page = await context.new_page()
                    # Bound timeout
                    page.set_default_timeout(settings.REPORT_RENDER_TIMEOUT_SECONDS * 1000)
                    await page.set_content(html_content, wait_until="load")

                    escaped_title = ReportSanitizerService.escape_text(title or "VIBE Report")
                    pdf_bytes = await page.pdf(
                        format=page_size,
                        print_background=True,
                        display_header_footer=True,
                        header_template=(
                            '<div style="font-size: 8px; width: 100%; text-align: right; '
                            'padding-right: 15mm; color: #94a3b8; font-family: sans-serif;">'
                            f"{escaped_title}</div>"
                        ),
                        footer_template=(
                            '<div style="font-size: 8px; width: 100%; text-align: center; '
                            'color: #94a3b8; font-family: sans-serif;">'
                            'Page <span class="pageNumber"></span> of <span class="totalPages"></span></div>'
                        ),
                        margin={"top": "20mm", "bottom": "20mm", "left": "15mm", "right": "15mm"},
                    )
                    return pdf_bytes
                finally:
                    await context.close()
            finally:
                await browser.close()

    @staticmethod
    def verify_and_measure_pdf(
        pdf_bytes: bytes, max_bytes: int = settings.REPORT_MAX_PDF_BYTES
    ) -> tuple[int, str, int]:
        """
        Inspect PDF structure using pypdf, enforce byte boundaries, and compute SHA-256.
        Returns: (page_count, checksum_sha256, file_size_bytes)
        """
        file_size = len(pdf_bytes)
        if file_size == 0:
            raise ValidationError("Rendered PDF artifact is empty (0 bytes)")
        if file_size > max_bytes:
            raise ValidationError(
                f"Rendered PDF artifact size ({file_size} bytes) exceeds limit ({max_bytes} bytes)"
            )

        try:
            reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
            page_count = len(reader.pages)
            if page_count < 1:
                raise ValidationError("Rendered PDF contains no pages")
        except Exception as e:
            raise ValidationError(
                f"Rendered PDF failed structural integrity verification: {e}"
            ) from e

        checksum = hashlib.sha256(pdf_bytes).hexdigest()
        return page_count, checksum, file_size


class ReportRendererClient:
    """Dispatches rendering requests either to the isolated internal HTTP service or local renderer."""

    def __init__(self, service_url: str | None = None, secret: str | None = None) -> None:
        self.service_url = service_url or settings.REPORT_RENDERER_SERVICE_URL
        self.secret = secret or settings.REPORT_RENDERER_SECRET
        self.local_renderer = ReportRendererService()

    async def render(
        self,
        report_def: dict[str, Any],
        run_dict: dict[str, Any],
        snapshot_payload: dict[str, Any],
        sections: list[dict[str, Any]],
    ) -> tuple[bytes, int, str, int]:
        """
        Render report definition and snapshot into PDF bytes.
        Returns: (pdf_bytes, page_count, checksum_sha256, file_size_bytes)
        """
        if self.service_url:
            # Dispatch to internal HTTP renderer service with HMAC signing
            return await self._render_remote(report_def, run_dict, snapshot_payload, sections)
        else:
            # Fall back to in-process renderer (local dev, direct test environments)
            return await self._render_local(report_def, run_dict, snapshot_payload, sections)

    async def _render_local(
        self,
        report_def: dict[str, Any],
        run_dict: dict[str, Any],
        snapshot_payload: dict[str, Any],
        sections: list[dict[str, Any]],
    ) -> tuple[bytes, int, str, int]:
        html = self.local_renderer.build_report_html(
            report_def, run_dict, snapshot_payload, sections
        )
        pdf_bytes = await self.local_renderer.render_pdf_from_html(
            html,
            title=report_def.get("title"),
            page_size=report_def.get("page_size", "A4"),
        )
        page_count, checksum, size = self.local_renderer.verify_and_measure_pdf(pdf_bytes)
        return pdf_bytes, page_count, checksum, size

    async def _render_remote(
        self,
        report_def: dict[str, Any],
        run_dict: dict[str, Any],
        snapshot_payload: dict[str, Any],
        sections: list[dict[str, Any]],
    ) -> tuple[bytes, int, str, int]:
        payload_bytes = json.dumps(
            {
                "report_def": report_def,
                "run_dict": run_dict,
                "snapshot_payload": snapshot_payload,
                "sections": sections,
            }
        ).encode("utf-8")

        if not self.service_url:
            raise RuntimeError("Remote report renderer service URL is not configured")
        endpoint = f"{self.service_url.rstrip('/')}/render"
        path = "/render"

        org_id = str(run_dict.get("organization_id", ""))
        report_id = str(report_def.get("id", ""))

        sig, ts, nonce, version = RendererAuthService.generate_signature(
            secret=self.secret,
            method="POST",
            path=path,
            body=payload_bytes,
            tenant_id=org_id,
            report_id=report_id,
        )

        headers = {
            "Content-Type": "application/json",
            "X-Renderer-Signature-Version": version,
            "X-Report-Signature": sig,
            "X-Report-Timestamp": str(ts),
            "X-Report-Nonce": nonce,
            "X-Tenant-Id": org_id,
            "X-Report-Id": report_id,
        }

        timeout = httpx.Timeout(settings.REPORT_RENDER_TIMEOUT_SECONDS + 10.0, connect=5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(endpoint, content=payload_bytes, headers=headers)
            if resp.status_code != 200:
                raise RenderingError(
                    f"Remote renderer service error ({resp.status_code}): {resp.text[:300]}"
                )
            pdf_bytes = resp.content

        # Even when receiving from the remote renderer, the worker independently validates the PDF!
        page_count, checksum, size = self.local_renderer.verify_and_measure_pdf(pdf_bytes)
        return pdf_bytes, page_count, checksum, size

    async def cancel(
        self,
        run_id: str,
        organization_id: str | None = None,
        report_id: str | None = None,
    ) -> None:
        """Send cooperative cancellation notice to the remote renderer if running."""
        if not self.service_url:
            return

        payload: dict[str, Any] = {"run_id": str(run_id)}
        if organization_id:
            payload["organization_id"] = str(organization_id)
        if report_id:
            payload["report_id"] = str(report_id)

        payload_bytes = json.dumps(payload).encode("utf-8")
        endpoint = f"{self.service_url.rstrip('/')}/cancel"
        path = "/cancel"

        sig, ts, nonce, version = RendererAuthService.generate_signature(
            secret=self.secret,
            method="POST",
            path=path,
            body=payload_bytes,
            tenant_id=organization_id,
            report_id=report_id,
        )

        headers = {
            "Content-Type": "application/json",
            "X-Renderer-Signature-Version": version,
            "X-Report-Signature": sig,
            "X-Report-Timestamp": str(ts),
            "X-Report-Nonce": nonce,
            "X-Tenant-Id": str(organization_id or ""),
            "X-Report-Id": str(report_id or ""),
        }

        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.post(endpoint, content=payload_bytes, headers=headers)
        except Exception as e:
            logger.warning(f"Failed to deliver cooperative cancellation to remote renderer: {e}")
