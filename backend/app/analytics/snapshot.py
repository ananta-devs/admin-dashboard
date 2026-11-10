"""Spreadsheet dataset snapshot capture and loading (ADR 003)."""

from __future__ import annotations

import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.analytics.exceptions import AnalyticsError, DatasetTooLargeError
from app.analytics.storage import AnalyticsStorageService
from app.core.config import settings
from app.formula import col_to_letter
from app.models.spreadsheet import Sheet, SheetColumn, SheetRow


@dataclass(frozen=True)
class SheetSnapshotMetadata:
    storage_path: str
    storage_id: str
    checksum_sha256: str
    size_bytes: int
    row_count: int
    col_count: int
    non_empty_cells: int
    sheet_version: int
    created_at: datetime


async def capture_sheet_snapshot(
    sheet_id: uuid.UUID,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    claim_generation: int = 1,
    snapshot_id: uuid.UUID | str | None = None,
    storage: AnalyticsStorageService | None = None,
    session: AsyncSession | None = None,
    on_before_rows_query: Callable[[], Awaitable[None]] | None = None,
) -> SheetSnapshotMetadata:
    """Capture evaluated sheet data into an immutable attempt-fenced Parquet snapshot under REPEATABLE READ."""
    if storage is None:
        storage = AnalyticsStorageService()

    snapshot_created_at = datetime.now(UTC)

    if session is not None:
        return await _read_and_write_snapshot(
            session=session,
            sheet_id=sheet_id,
            organization_id=organization_id,
            job_id=job_id,
            claim_generation=claim_generation,
            snapshot_id=snapshot_id,
            storage=storage,
            snapshot_created_at=snapshot_created_at,
            on_before_rows_query=on_before_rows_query,
        )

    isolated_engine = create_async_engine(
        settings.DATABASE_URL,
        poolclass=NullPool,
        echo=settings.DB_ECHO,
    )
    try:
        async with isolated_engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="REPEATABLE READ")
            async with AsyncSession(conn) as isolated_session:
                async with isolated_session.begin():
                    return await _read_and_write_snapshot(
                        session=isolated_session,
                        sheet_id=sheet_id,
                        organization_id=organization_id,
                        job_id=job_id,
                        claim_generation=claim_generation,
                        snapshot_id=snapshot_id,
                        storage=storage,
                        snapshot_created_at=snapshot_created_at,
                        on_before_rows_query=on_before_rows_query,
                    )
    finally:
        await isolated_engine.dispose()


async def _read_and_write_snapshot(
    session: AsyncSession,
    sheet_id: uuid.UUID,
    organization_id: uuid.UUID,
    job_id: uuid.UUID,
    claim_generation: int,
    snapshot_id: uuid.UUID | str | None,
    storage: AnalyticsStorageService,
    snapshot_created_at: datetime,
    on_before_rows_query: Callable[[], Awaitable[None]] | None = None,
) -> SheetSnapshotMetadata:
    # 1. Fetch sheet metadata
    sheet_stmt = select(Sheet).where(
        Sheet.id == sheet_id,
        Sheet.organization_id == organization_id,
    )
    sheet = (await session.execute(sheet_stmt)).scalar_one_or_none()
    if not sheet:
        raise AnalyticsError(f"Sheet {sheet_id} not found in organization {organization_id}.")

    sheet_version = sheet.version

    # 2. Fetch sheet columns definition
    cols_stmt = (
        select(SheetColumn)
        .where(SheetColumn.sheet_id == sheet_id)
        .order_by(SheetColumn.column_index.asc())
    )
    cols = (await session.execute(cols_stmt)).scalars().all()
    col_name_map: dict[int, str] = {c.column_index: c.name for c in cols}

    # Deterministic hook for testing concurrent mutations under REPEATABLE READ
    if on_before_rows_query:
        await on_before_rows_query()

    if os.environ.get("VIBE_CRASH_STAGE") == "STAGE_1_SNAPSHOT_EXTRACT":
        import sys

        sys.stderr.write(">>> TEST HOOK: CRASH AT STAGE_1_SNAPSHOT_EXTRACT <<<\n")
        sys.stderr.flush()
        os._exit(99)

    # 3. Fetch all sheet rows
    rows_stmt = (
        select(SheetRow).where(SheetRow.sheet_id == sheet_id).order_by(SheetRow.row_index.asc())
    )
    rows = (await session.execute(rows_stmt)).scalars().all()

    total_rows = len(rows)
    if total_rows > settings.ANALYTICS_MAX_SOURCE_ROWS:
        raise DatasetTooLargeError(
            f"Sheet exceeds maximum allowable rows: {total_rows} > {settings.ANALYTICS_MAX_SOURCE_ROWS}"
        )

    used_col_indices: set[int] = set(col_name_map.keys())
    total_non_empty_cells = 0

    for r in rows:
        if r.cells:
            for k, cell_dict in r.cells.items():
                clean_k = k[2:] if str(k).startswith("c_") else str(k)
                try:
                    used_col_indices.add(int(clean_k))
                except (ValueError, TypeError):
                    pass
                if isinstance(cell_dict, dict) and (
                    cell_dict.get("v") is not None or cell_dict.get("c") is not None
                ):
                    total_non_empty_cells += 1

    if total_non_empty_cells > settings.ANALYTICS_MAX_NON_EMPTY_CELLS:
        raise DatasetTooLargeError(
            f"Sheet exceeds maximum allowable non-empty cells: {total_non_empty_cells} > {settings.ANALYTICS_MAX_NON_EMPTY_CELLS}"
        )

    sorted_col_indices = sorted(used_col_indices)
    if len(sorted_col_indices) > settings.ANALYTICS_MAX_SOURCE_COLS:
        raise DatasetTooLargeError(
            f"Sheet exceeds maximum allowable columns: {len(sorted_col_indices)} > {settings.ANALYTICS_MAX_SOURCE_COLS}"
        )

    header_names: list[str] = [
        col_name_map.get(idx, col_to_letter(idx)) for idx in sorted_col_indices
    ]

    column_data: dict[str, list[Any]] = {h: [] for h in header_names}

    for r in rows:
        cells = r.cells or {}
        for idx, h in zip(sorted_col_indices, header_names, strict=True):
            c_dict = cells.get(f"c_{idx}") or cells.get(str(idx)) or {}
            val = (
                c_dict.get("c")
                if "c" in c_dict and c_dict.get("c") is not None
                else c_dict.get("v")
            )
            column_data[h].append(val)

    df = pl.DataFrame(column_data)

    # Resolve attempt-fenced immutable path and temporary path
    final_snapshot_path = storage.get_snapshot_path(
        organization_id=organization_id,
        job_id=job_id,
        claim_generation=claim_generation,
        snapshot_id=snapshot_id,
    )
    tmp_snapshot_path = final_snapshot_path.with_suffix(".parquet.tmp")

    # Validate quota reservation before generating snapshot
    storage.check_storage_quota(organization_id)

    # Write to temporary file
    df.write_parquet(tmp_snapshot_path, compression="snappy")

    # Validate snapshot size bounds
    size_bytes = tmp_snapshot_path.stat().st_size
    if size_bytes > settings.ANALYTICS_MAX_SNAPSHOT_BYTES:
        tmp_snapshot_path.unlink(missing_ok=True)
        raise DatasetTooLargeError(
            f"Generated snapshot size ({size_bytes} bytes) exceeds limit ({settings.ANALYTICS_MAX_SNAPSHOT_BYTES} bytes)"
        )

    # Compute checksum of validated temporary file
    sha256_hash = storage.compute_sha256(tmp_snapshot_path)

    # Atomically promote temporary file to attempt-fenced immutable final path
    storage.atomic_promote(tmp_snapshot_path, final_snapshot_path)

    storage_id = str(final_snapshot_path.resolve())

    return SheetSnapshotMetadata(
        storage_path=str(final_snapshot_path),
        storage_id=storage_id,
        checksum_sha256=sha256_hash,
        size_bytes=size_bytes,
        row_count=df.height,
        col_count=df.width,
        non_empty_cells=total_non_empty_cells,
        sheet_version=sheet_version,
        created_at=snapshot_created_at,
    )


def load_sheet_snapshot(
    organization_id: uuid.UUID | None = None,
    job_id: uuid.UUID | None = None,
    snapshot_path: str | Path | None = None,
    claim_generation: int = 1,
    expected_hash: str | None = None,
    storage: AnalyticsStorageService | None = None,
) -> pl.DataFrame:
    """Load immutable Parquet snapshot file and verify SHA-256 checksum."""
    if storage is None:
        storage = AnalyticsStorageService()

    if snapshot_path is not None:
        target_path = Path(snapshot_path).resolve()
    elif organization_id is not None and job_id is not None:
        target_path = storage.get_snapshot_path(
            organization_id, job_id, claim_generation=claim_generation
        )
    else:
        raise AnalyticsError("Must provide either snapshot_path or organization_id and job_id.")

    if not target_path.is_file():
        raise AnalyticsError(f"Snapshot file not found at {target_path}.")

    if expected_hash:
        actual_hash = storage.compute_sha256(target_path)
        if actual_hash != expected_hash:
            raise AnalyticsError(
                f"Snapshot integrity violation: expected hash {expected_hash}, found {actual_hash}.",
                code="SNAPSHOT_INTEGRITY_VIOLATION",
            )

    return pl.read_parquet(target_path)
