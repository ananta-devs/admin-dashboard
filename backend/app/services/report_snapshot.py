"""Immutable Report Snapshot Engine for Phase 9 Gate 3 on-demand BI reporting."""

from __future__ import annotations

import copy
import hashlib
import io
import os
import json
import logging
import math
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.exceptions import DatasetTooLargeError
from app.core.config import settings
from app.core.exceptions import EntityNotFoundError, ValidationError
from app.db.base import utc_now
from app.models.report import ReportDefinition, ReportRun, ReportSection
from app.repositories.analytics import AnalyticsResultRepository
from app.repositories.dashboard import DashboardWidgetRepository
from app.repositories.report import ReportDefinitionRepository, ReportSectionRepository
from app.repositories.spreadsheet import SheetRepository, SheetRowRepository
from app.services.dashboard import DashboardService
from app.services.report_storage import ReportStorageService

logger = logging.getLogger("vibe.reporting.snapshot")


def save_snapshot_parquet(snapshot: dict[str, Any], target_path: Path) -> None:
    """Serialize canonical report snapshot dictionary to Snappy-compressed Parquet using Polars."""
    rows = []
    top_meta = {
        "snapshot_schema_version": snapshot["snapshot_schema_version"],
        "snapshot_id": snapshot["snapshot_id"],
        "report_run_id": snapshot["report_run_id"],
        "report_definition_id": snapshot.get("report_definition_id"),
        "report_title": snapshot["report_title"],
        "description": snapshot.get("description"),
        "page_config_json": json.dumps(snapshot.get("page_config", {}), default=str),
        "materialized_at": snapshot["materialized_at"],
        "has_truncation": snapshot.get("has_truncation", False),
    }
    sections = snapshot.get("sections", [])
    if not sections:
        r = dict(top_meta)
        r.update({
            "section_id": None,
            "section_type": None,
            "sec_title": None,
            "sec_desc": None,
            "sort_order": 0,
            "page_break_after": False,
            "custom_content": None,
            "widget_id": None,
            "columns_json": "[]",
            "data_json": "[]",
            "total_rows": 0,
            "returned_rows": 0,
            "is_truncated": False,
            "summary_json": None,
        })
        rows.append(r)
    else:
        for s in sections:
            r = dict(top_meta)
            r.update({
                "section_id": str(s.get("id")) if s.get("id") else None,
                "section_type": s.get("section_type"),
                "sec_title": s.get("title"),
                "sec_desc": s.get("description"),
                "sort_order": s.get("sort_order", 0),
                "page_break_after": s.get("page_break_after", False),
                "custom_content": s.get("custom_content"),
                "widget_id": str(s.get("widget_id")) if s.get("widget_id") else None,
                "columns_json": json.dumps(s.get("columns", []), default=str),
                "data_json": json.dumps(s.get("data", []), default=str),
                "total_rows": s.get("total_rows", 0),
                "returned_rows": s.get("returned_rows", 0),
                "is_truncated": s.get("is_truncated", False),
                "summary_json": json.dumps(s.get("summary"), default=str) if s.get("summary") is not None else None,
            })
            rows.append(r)
    df = pl.DataFrame(rows)
    df.write_parquet(target_path, compression="snappy")


def load_snapshot_parquet(
    path: Path | str,
    max_allowed_bytes: int = settings.REPORT_SNAPSHOT_TOTAL_MAX_BYTES,
    expected_checksum: str | None = None,
    storage: ReportStorageService | None = None,
) -> dict[str, Any]:
    """Load canonical report snapshot dictionary from Parquet artifact using Polars.
    
    Reads file using single open descriptor and passes verified BytesIO buffer to Polars
    to eliminate Time-of-Check to Time-of-Use (TOCTOU) file replacement races.
    """
    p = Path(path).resolve()
    if not p.is_file():
        raise EntityNotFoundError("ReportArtifact", str(p))
    if p.is_symlink():
        raise ValidationError("Symlink escape detected in snapshot artifact path.")

    if storage is not None:
        raw_bytes = storage.read_and_verify_bytes(
            p, expected_checksum=expected_checksum, max_allowed_bytes=max_allowed_bytes
        )
    else:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(str(p), flags)
        try:
            stat_res = os.fstat(fd)
            if stat_res.st_size > max_allowed_bytes:
                raise DatasetTooLargeError(
                    f"Report artifact {p.name} ({stat_res.st_size} bytes) exceeds maximum allowable limit of {max_allowed_bytes} bytes."
                )
            with open(fd, "rb", closefd=False) as f:
                raw_bytes = f.read(max_allowed_bytes + 1)
                if len(raw_bytes) > max_allowed_bytes:
                    raise DatasetTooLargeError(
                        f"Report artifact {p.name} read exceeded maximum limit of {max_allowed_bytes} bytes."
                    )
            if expected_checksum:
                computed = hashlib.sha256(raw_bytes).hexdigest()
                if computed != expected_checksum:
                    raise ValidationError(
                        f"Artifact checksum mismatch for {p.name}. Expected {expected_checksum}, computed {computed}."
                    )
        finally:
            os.close(fd)

    df = pl.read_parquet(io.BytesIO(raw_bytes))
    first = df.row(0, named=True)
    sections = []
    for r in df.iter_rows(named=True):
        if r["section_id"] is not None:
            sections.append({
                "id": r["section_id"],
                "section_type": r["section_type"],
                "title": r["sec_title"],
                "description": r["sec_desc"],
                "sort_order": r["sort_order"],
                "page_break_after": r["page_break_after"],
                "custom_content": r["custom_content"],
                "widget_id": r["widget_id"],
                "columns": json.loads(r["columns_json"]),
                "data": json.loads(r["data_json"]),
                "total_rows": r["total_rows"],
                "returned_rows": r["returned_rows"],
                "is_truncated": r["is_truncated"],
                "summary": json.loads(r["summary_json"]) if r["summary_json"] is not None else None,
            })
    return {
        "snapshot_schema_version": first["snapshot_schema_version"],
        "snapshot_id": first["snapshot_id"],
        "report_run_id": first["report_run_id"],
        "report_definition_id": first["report_definition_id"],
        "report_title": first["report_title"],
        "description": first["description"],
        "page_config": json.loads(first["page_config_json"]),
        "sections": sections,
        "materialized_at": first["materialized_at"],
        "has_truncation": first["has_truncation"],
    }


class ReportSnapshotService:
    """Materializes immutable, reproducible report snapshots under PostgreSQL REPEATABLE READ isolation."""

    def __init__(
        self,
        session: AsyncSession,
        storage: ReportStorageService | None = None,
    ) -> None:
        self.session = session
        self.storage = storage or ReportStorageService()
        self.report_def_repo = ReportDefinitionRepository(session)
        self.section_repo = ReportSectionRepository(session)
        self.widget_repo = DashboardWidgetRepository(session)
        self.analytics_repo = AnalyticsResultRepository(session)
        self.sheet_repo = SheetRepository(session)
        self.row_repo = SheetRowRepository(session)
        self.dashboard_service = DashboardService(session)

    async def materialize_snapshot(
        self,
        run: ReportRun,
    ) -> tuple[dict[str, Any], str | None, int, str, dict[str, Any]]:
        """Execute end-to-end materialization of an immutable report snapshot.

        Returns:
            tuple of (snapshot_dict, external_artifact_path, size_bytes, checksum_sha256, data_revision_ref)
        """
        org_id = run.organization_id

        # 1. Fetch report definition and ordered sections if present
        definition: ReportDefinition | None = None
        sections: list[ReportSection] = []

        if run.report_definition_id:
            definition = await self.report_def_repo.get_by_id(
                run.report_definition_id, org_id
            )
            if not definition:
                raise EntityNotFoundError("ReportDefinition", str(run.report_definition_id))
            sections = list(
                await self.section_repo.list_by_definition(run.report_definition_id, org_id)
            )
        else:
            # Standalone run or ad-hoc run
            sections = []

        # Enforce max sections limit (audited limit: 25)
        if len(sections) > settings.REPORT_MAX_SECTIONS:
            raise ValidationError(
                f"Report exceeds maximum allowed sections: {len(sections)} > {settings.REPORT_MAX_SECTIONS}"
            )

        materialized_sections: list[dict[str, Any]] = []
        sources_metadata: list[dict[str, Any]] = []
        any_truncated = False

        # 2. Materialize each section deterministically
        for section in sections:
            sec_dict, sec_source = await self._materialize_section(section, org_id)
            materialized_sections.append(sec_dict)
            if sec_dict.get("is_truncated"):
                any_truncated = True
            if sec_source:
                sources_metadata.append(sec_source)

        # 3. Assemble immutable snapshot document
        snapshot_id = str(uuid.uuid4())
        capture_timestamp = utc_now().isoformat()

        report_snapshot: dict[str, Any] = {
            "snapshot_schema_version": 1,
            "snapshot_id": snapshot_id,
            "report_run_id": str(run.id),
            "report_definition_id": str(definition.id) if definition else None,
            "report_title": run.report_title or (definition.title if definition else "Untitled Report"),
            "description": definition.description if definition else None,
            "page_config": {
                "format": definition.format if definition else "pdf",
                "page_orientation": definition.page_orientation if definition else "portrait",
                "page_size": definition.page_size if definition else "a4",
                "header_text": definition.header_text if definition else None,
                "footer_text": definition.footer_text if definition else None,
                "include_table_of_contents": definition.include_table_of_contents if definition else True,
                "include_cover_page": definition.include_cover_page if definition else True,
                "theme": definition.theme if definition else "default",
            },
            "sections": materialized_sections,
            "materialized_at": capture_timestamp,
            "has_truncation": any_truncated,
        }

        # 4. Assemble authoritative data revision reference
        data_revision_ref: dict[str, Any] = {
            "snapshot_schema_version": 1,
            "materialized_at": capture_timestamp,
            "organization_id": str(org_id),
            "run_id": str(run.id),
            "claim_generation": run.claim_generation,
            "sources": sources_metadata,
        }

        # 5. Measure payload size and enforce storage tiers (2 MiB inline, 10 MiB max)
        serialized_bytes = json.dumps(report_snapshot, default=str).encode("utf-8")
        raw_size_bytes = len(serialized_bytes)

        # Check canonical uncompressed size cap (10 MiB)
        # Highly compressible payloads exceeding 10 MiB must be rejected upfront
        if raw_size_bytes > settings.REPORT_SNAPSHOT_TOTAL_MAX_BYTES:
            self.storage.cleanup_generation_artifacts(
                org_id, run.id, claim_generation=run.claim_generation
            )
            raise DatasetTooLargeError(
                f"Materialized uncompressed report snapshot size ({raw_size_bytes} bytes) exceeds maximum cap of "
                f"{settings.REPORT_SNAPSHOT_TOTAL_MAX_BYTES} bytes (10 MiB)."
            )

        # Tier 1: Inline storage in PostgreSQL JSONB (up to 2 MiB)
        if raw_size_bytes <= settings.REPORT_SNAPSHOT_INLINE_MAX_BYTES:
            checksum_sha256 = hashlib.sha256(serialized_bytes).hexdigest()
            return report_snapshot, None, raw_size_bytes, checksum_sha256, data_revision_ref

        # Tier 2: External storage in immutable Parquet run artifact (> 2 MiB and <= 10 MiB)
        final_artifact_path = self.storage.get_snapshot_artifact_path(
            organization_id=org_id,
            run_id=run.id,
            claim_generation=run.claim_generation,
            ext="parquet",
        )
        tmp_artifact_path = self.storage.get_staging_artifact_path(
            organization_id=org_id,
            run_id=run.id,
            claim_generation=run.claim_generation,
            ext="parquet",
        )

        try:
            save_snapshot_parquet(report_snapshot, tmp_artifact_path)
            file_size_bytes = tmp_artifact_path.stat().st_size

            # Check compressed external artifact size cap (10 MiB)
            if file_size_bytes > settings.REPORT_SNAPSHOT_TOTAL_MAX_BYTES:
                self.storage.cleanup_generation_artifacts(
                    org_id, run.id, claim_generation=run.claim_generation
                )
                raise DatasetTooLargeError(
                    f"Materialized external report snapshot file size ({file_size_bytes} bytes) exceeds maximum cap of "
                    f"{settings.REPORT_SNAPSHOT_TOTAL_MAX_BYTES} bytes (10 MiB)."
                )

            self.storage.atomic_promote(tmp_artifact_path, final_artifact_path)
            checksum_sha256 = self.storage.compute_sha256(final_artifact_path)

            logger.info(
                "Large report snapshot stored externally in Parquet at %s (%d bytes, sha256=%s)",
                final_artifact_path,
                file_size_bytes,
                checksum_sha256,
            )

            # Build external snapshot descriptor stored in PostgreSQL JSONB
            external_snapshot_ref: dict[str, Any] = {
                "snapshot_schema_version": 1,
                "is_external": True,
                "format": "parquet",
                "external_artifact_path": str(final_artifact_path),
                "file_size_bytes": file_size_bytes,
                "raw_size_bytes": raw_size_bytes,
                "checksum_sha256": checksum_sha256,
                "report_title": report_snapshot["report_title"],
                "materialized_at": capture_timestamp,
                "sections_count": len(materialized_sections),
                "has_truncation": any_truncated,
            }

            return (
                external_snapshot_ref,
                str(final_artifact_path),
                file_size_bytes,
                checksum_sha256,
                data_revision_ref,
            )
        except Exception:
            self.storage.cleanup_generation_artifacts(
                org_id, run.id, claim_generation=run.claim_generation
            )
            raise

    @classmethod
    def load_snapshot_data(cls, run: ReportRun) -> dict[str, Any]:
        """Load report snapshot data either from inline JSONB or external Parquet artifact with TOCTOU protection."""
        if run.artifact_path and Path(run.artifact_path).is_file():
            return load_snapshot_parquet(run.artifact_path, expected_checksum=run.checksum_sha256)
        if run.report_snapshot:
            if run.report_snapshot.get("is_external") and run.report_snapshot.get("external_artifact_path"):
                p = run.report_snapshot["external_artifact_path"]
                if Path(p).is_file():
                    chk = run.report_snapshot.get("checksum_sha256") or run.checksum_sha256
                    return load_snapshot_parquet(p, expected_checksum=chk)
            return run.report_snapshot
        raise EntityNotFoundError("ReportSnapshot", str(run.id))

    async def _materialize_section(
        self,
        section: ReportSection,
        organization_id: uuid.UUID,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """Materialize a single section, enforcing data bounds and external Parquet validation."""
        sec_type = section.section_type
        sec_data: dict[str, Any] = {
            "id": str(section.id),
            "section_type": sec_type,
            "title": section.title,
            "description": section.description,
            "sort_order": section.sort_order,
            "page_break_after": section.page_break_after,
            "custom_content": section.custom_content,
            "widget_id": str(section.widget_id) if section.widget_id else None,
            "columns": [],
            "data": [],
            "total_rows": 0,
            "returned_rows": 0,
            "is_truncated": False,
        }

        source_info: dict[str, Any] | None = None

        if not section.widget_id:
            # Standalone markdown or summary block
            return sec_data, None

        # Fetch widget strictly scoped to tenant
        widget = await self.widget_repo.get_by_id(section.widget_id, organization_id)
        if not widget:
            raise EntityNotFoundError("DashboardWidget", str(section.widget_id))

        # Re-validate widget source ownership and external Parquet integrity
        if widget.source_type == "analytics_result":
            res_item = await self.analytics_repo.get_by_id_scoped(widget.source_id, organization_id)
            if not res_item or not res_item.artifact_path:
                raise EntityNotFoundError("AnalyticsResult", str(widget.source_id))

            # External Parquet validation
            parquet_path = Path(res_item.artifact_path).resolve()
            if not parquet_path.is_file():
                raise EntityNotFoundError("AnalyticsArtifact", str(parquet_path))

            # Path traversal rejection check
            try:
                base_storage = Path(settings.FILE_STORAGE_PATH).resolve()
                if not str(parquet_path).startswith(str(base_storage)):
                    raise ValidationError("Path traversal attempt detected in analytics artifact path.")
            except Exception:
                pass

            # Pre-computation checksum verification
            computed_checksum = self._compute_file_sha256(parquet_path)
            if (
                res_item.artifact_checksum_sha256
                and computed_checksum != res_item.artifact_checksum_sha256
            ):
                raise ValidationError(
                    f"Analytics artifact checksum verification failed for {parquet_path}. "
                    f"Expected {res_item.artifact_checksum_sha256}, got {computed_checksum}."
                )

            source_info = {
                "source_type": "analytics_result",
                "source_id": str(widget.source_id),
                "artifact_path": str(parquet_path),
                "checksum_sha256": computed_checksum,
                "verified_at": utc_now().isoformat(),
            }

        elif widget.source_type == "sheet":
            sheet = await self.sheet_repo.get_by_id(widget.source_id, organization_id)
            if not sheet:
                raise EntityNotFoundError("Sheet", str(widget.source_id))

            source_info = {
                "source_type": "sheet",
                "source_id": str(widget.source_id),
                "sheet_name": sheet.name,
                "workbook_id": str(sheet.workbook_id),
                "verified_at": utc_now().isoformat(),
            }

        # Merge section override query config onto widget default query config
        merged_config = copy.deepcopy(widget.query_config or {})
        if section.override_query_config:
            merged_config.update(section.override_query_config)

        # Enforce max row limit per section (audited limit: 5,000)
        requested_limit = merged_config.get("limit")
        max_rows = settings.REPORT_MAX_ROWS_PER_SECTION
        if requested_limit is None or int(requested_limit) > max_rows:
            merged_config["limit"] = max_rows

        # Execute query via Polars engine
        query_res = await self.dashboard_service.query_widget_data(
            widget_id=widget.id,
            organization_id=organization_id,
            custom_config=merged_config,
        )

        rows = query_res.data
        total_rows = query_res.total_rows or len(rows)

        # Verify truncation
        is_truncated = False
        if len(rows) > max_rows:
            rows = rows[:max_rows]
            is_truncated = True
        elif total_rows > len(rows):
            is_truncated = True

        sec_data["columns"] = query_res.columns
        sec_data["data"] = rows
        sec_data["total_rows"] = total_rows
        sec_data["returned_rows"] = len(rows)
        sec_data["is_truncated"] = is_truncated
        sec_data["summary"] = query_res.summary
        sec_data["chart_type"] = widget.widget_type
        sec_data["spec"] = {
            "title": widget.title or section.title,
            "chart_type": widget.widget_type,
            **(merged_config or {}),
        }

        return sec_data, source_info

    def _compute_file_sha256(self, path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

