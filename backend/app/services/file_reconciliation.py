"""File storage crash recovery and orphan reconciliation service.

Identifies abandoned staging files, unreferenced physical artifacts, and metadata
inconsistencies without performing destructive cleanup unless explicitly authorized.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import logger
from app.models.stored_file import StoredFile


@dataclass
class ReconciliationReport:
    inspected_at: float = field(default_factory=time.time)
    base_storage_dir: str = ""
    total_physical_files: int = 0
    total_db_records: int = 0
    active_referenced_files: list[str] = field(default_factory=list)
    orphaned_files: list[str] = field(default_factory=list)
    abandoned_staging_files: list[str] = field(default_factory=list)
    missing_physical_files: list[str] = field(default_factory=list)
    bytes_reclaimable: int = 0
    dry_run: bool = True
    pruned_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "inspected_at": self.inspected_at,
            "base_storage_dir": self.base_storage_dir,
            "total_physical_files": self.total_physical_files,
            "total_db_records": self.total_db_records,
            "active_referenced_count": len(self.active_referenced_files),
            "orphaned_count": len(self.orphaned_files),
            "abandoned_staging_count": len(self.abandoned_staging_files),
            "missing_physical_count": len(self.missing_physical_files),
            "bytes_reclaimable": self.bytes_reclaimable,
            "dry_run": self.dry_run,
            "pruned_count": len(self.pruned_files),
            "orphaned_files": self.orphaned_files,
            "abandoned_staging_files": self.abandoned_staging_files,
            "missing_physical_files": self.missing_physical_files,
        }


class FileReconciliationService:
    """Service auditing and reconciling physical storage artifacts against database records."""

    def __init__(self, session: AsyncSession, base_dir: str | None = None) -> None:
        self.session = session
        self.base_dir = Path(
            str(base_dir or getattr(settings, "USER_FILES_STORAGE_DIR", "./storage/user_files"))
        ).resolve()

    async def audit_storage(self, max_staging_age_seconds: int = 3600) -> ReconciliationReport:
        """Inspect physical filesystem and database to detect orphaned or abandoned files (dry run)."""
        report = ReconciliationReport(
            base_storage_dir=str(self.base_dir),
            dry_run=True,
        )

        if not self.base_dir.exists():
            return report

        # 1. Fetch all committed storage paths from DB
        stmt = select(
            StoredFile.id,
            StoredFile.organization_id,
            StoredFile.storage_path,
            StoredFile.size_bytes,
        )
        res = await self.session.execute(stmt)
        db_records = res.all()
        report.total_db_records = len(db_records)

        db_path_map: dict[str, Any] = {}
        for r in db_records:
            # Map canonical path on disk
            rel = r.storage_path.replace("user_files/", "")
            full_path = (self.base_dir / rel).resolve()
            db_path_map[str(full_path)] = {
                "id": str(r.id),
                "org_id": str(r.organization_id),
                "size": r.size_bytes,
            }

        # 2. Check for missing physical files for DB records
        for p_str, _meta in db_path_map.items():
            if not os.path.exists(p_str):
                report.missing_physical_files.append(p_str)

        # 3. Walk physical filesystem
        staging_dir = (self.base_dir / "staging").resolve()
        now = time.time()

        for root, _dirs, files in os.walk(self.base_dir):
            root_path = Path(root).resolve()
            # Do not follow symlinks outside base
            if not root_path.is_relative_to(self.base_dir):
                continue

            is_staging = root_path == staging_dir or staging_dir in root_path.parents

            for fname in files:
                fpath = (root_path / fname).resolve()
                if not fpath.is_relative_to(self.base_dir):
                    continue

                report.total_physical_files += 1
                fpath_str = str(fpath)

                if is_staging:
                    # Check age of staging file
                    try:
                        mtime = fpath.stat().st_mtime
                        if now - mtime > max_staging_age_seconds:
                            report.abandoned_staging_files.append(fpath_str)
                            report.bytes_reclaimable += fpath.stat().st_size
                    except OSError:
                        pass
                else:
                    # Tenant storage file
                    if fpath_str in db_path_map:
                        report.active_referenced_files.append(fpath_str)
                    else:
                        # Orphaned file with no DB metadata
                        report.orphaned_files.append(fpath_str)
                        try:
                            report.bytes_reclaimable += fpath.stat().st_size
                        except OSError:
                            pass

        return report

    async def prune_orphans(
        self,
        destructive_confirmed: bool = False,
        max_staging_age_seconds: int = 3600,
    ) -> ReconciliationReport:
        """Safely prune abandoned staging and unreferenced orphaned files if explicitly confirmed."""
        report = await self.audit_storage(max_staging_age_seconds=max_staging_age_seconds)

        if not destructive_confirmed:
            logger.info("Prune requested in dry-run mode. Zero files removed.")
            return report

        report.dry_run = False
        candidates = report.abandoned_staging_files + report.orphaned_files

        for c_path in candidates:
            p = Path(c_path).resolve()
            # Safety check: must be inside base_dir and must not be a DB-referenced file
            if p.is_relative_to(self.base_dir) and c_path not in report.active_referenced_files:
                try:
                    p.unlink(missing_ok=True)
                    report.pruned_files.append(c_path)
                    logger.info(f"Reconciled and pruned orphaned file: {c_path}")
                except OSError as e:
                    logger.warning(f"Could not prune orphaned file '{c_path}': {e}")

        return report
