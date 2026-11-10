"""Verification tests for maximum dataset admission limits and cell bounds.

Verifies:
1. Actual configured resource limits match enterprise architecture specs.
2. A 1,000,000-cell dataset request is strictly rejected before expensive snapshot generation.
3. The maximum admitted workload of 500,000 cells (50,000 rows x 10 cols) is successfully admitted,
   snapshotted, and processed within resource constraints.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import polars as pl
import pytest

from app.analytics.exceptions import DatasetTooLargeError
from app.analytics.registry import registry
from app.analytics.snapshot import capture_sheet_snapshot
from app.analytics.storage import AnalyticsStorageService
from app.core.config import settings
from app.models.spreadsheet import Sheet


class _SimpleCol:
    __slots__ = ("column_index", "name")

    def __init__(self, column_index: int, name: str) -> None:
        self.column_index = column_index
        self.name = name


class _SimpleRow:
    __slots__ = ("row_index", "cells")

    def __init__(self, row_index: int, cells: dict) -> None:
        self.row_index = row_index
        self.cells = cells


def test_configured_resource_limits() -> None:
    """Verifies that all active analytics resource limits match specified enterprise configurations."""
    assert settings.ANALYTICS_MAX_SOURCE_ROWS == 100_000
    assert settings.ANALYTICS_MAX_SOURCE_COLS == 100
    assert settings.ANALYTICS_MAX_NON_EMPTY_CELLS == 500_000
    assert settings.ANALYTICS_MAX_SNAPSHOT_BYTES == 52_428_800  # 50 MB
    assert settings.ANALYTICS_MAX_RESULT_ROWS == 50_000
    assert settings.ANALYTICS_MAX_RESULT_PAYLOAD_BYTES == 10_485_760  # 10 MB
    assert settings.ANALYTICS_MAX_ARTIFACT_BYTES == 104_857_600  # 100 MB


@pytest.mark.asyncio
async def test_rejection_of_one_million_cells_before_snapshot(tmp_path: Path) -> None:
    """Verifies that a 1,000,000-cell sheet is rejected before any snapshot Parquet file is written."""
    storage = AnalyticsStorageService(base_dir=str(tmp_path))
    org_id = uuid.uuid4()
    sheet_id = uuid.uuid4()

    mock_session = AsyncMock()

    # Sheet metadata
    mock_sheet = MagicMock(spec=Sheet)
    mock_sheet.id = sheet_id
    mock_sheet.organization_id = org_id
    mock_sheet.version = 1
    mock_session.execute.side_effect = [
        # 1. Sheet lookup
        MagicMock(scalar_one_or_none=MagicMock(return_value=mock_sheet)),
        # 2. Columns lookup (10 columns)
        MagicMock(
            scalars=MagicMock(
                return_value=MagicMock(
                    all=MagicMock(return_value=[_SimpleCol(i, f"col_{i}") for i in range(10)])
                )
            )
        ),
        # 3. Rows lookup (100,000 rows with 10 non-empty cells each = 1,000,000 cells)
        MagicMock(
            scalars=MagicMock(
                return_value=MagicMock(
                    all=MagicMock(
                        return_value=[
                            _SimpleRow(r, {f"c_{c}": {"v": 42.0, "c": 42.0} for c in range(10)})
                            for r in range(100_000)
                        ]
                    )
                )
            )
        ),
    ]

    # Attempt snapshot capture
    with pytest.raises(DatasetTooLargeError) as exc_info:
        await capture_sheet_snapshot(
            session=mock_session,
            sheet_id=sheet_id,
            job_id=uuid.uuid4(),
            organization_id=org_id,
            storage=storage,
        )

    # Must fail on maximum non-empty cells bound
    assert "Sheet exceeds maximum allowable non-empty cells: 1000000 > 500000" in str(
        exc_info.value
    )

    # Verify no snapshot files were written to disk
    snapshot_files = list((tmp_path / "snapshots").glob("**/*.parquet"))
    assert len(snapshot_files) == 0, f"Expected no snapshots on disk, found: {snapshot_files}"


@pytest.mark.asyncio
async def test_admission_and_processing_of_maximum_allowed_cells(tmp_path: Path) -> None:
    """Verifies that the maximum admitted workload (500,000 cells) is snapshotted and computed safely."""
    storage = AnalyticsStorageService(base_dir=str(tmp_path))
    org_id = uuid.uuid4()
    sheet_id = uuid.uuid4()

    mock_session = AsyncMock()

    # 50,000 rows x 10 cols = 500,000 cells (exact upper limit)
    mock_sheet = MagicMock(spec=Sheet)
    mock_sheet.id = sheet_id
    mock_sheet.organization_id = org_id
    mock_sheet.version = 1

    mock_cols = [_SimpleCol(i, f"metric_{i}") for i in range(10)]

    mock_rows = [
        _SimpleRow(
            r, {f"c_{c}": {"v": float(r * 10 + c), "c": float(r * 10 + c)} for c in range(10)}
        )
        for r in range(50_000)
    ]

    mock_session.execute.side_effect = [
        MagicMock(scalar_one_or_none=MagicMock(return_value=mock_sheet)),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=mock_cols)))),
        MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=mock_rows)))),
    ]

    snapshot_meta = await capture_sheet_snapshot(
        session=mock_session,
        sheet_id=sheet_id,
        job_id=uuid.uuid4(),
        organization_id=org_id,
        storage=storage,
    )

    assert snapshot_meta.row_count == 50_000
    assert snapshot_meta.col_count == 10
    assert snapshot_meta.size_bytes < settings.ANALYTICS_MAX_SNAPSHOT_BYTES

    # Verify snapshot Parquet file exists on disk and is readable by Polars
    snapshot_file = Path(snapshot_meta.storage_path)
    assert snapshot_file.is_file()
    df = pl.read_parquet(snapshot_file)
    assert df.shape == (50_000, 10)

    # Execute analytics operation on the snapshotted dataset
    result = registry.execute(
        operation_id="describe_columns",
        df=df,
        raw_params={"columns": ["metric_0", "metric_1"]},
    )

    assert result["total_rows"] == 50_000
    assert "metric_0" in result["columns"]
    assert "metric_1" in result["columns"]
    assert result["columns"]["metric_0"]["count"] == 50_000
