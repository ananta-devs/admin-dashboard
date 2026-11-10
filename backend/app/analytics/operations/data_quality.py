"""Data Quality Profiling Operation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import polars as pl
from pydantic import BaseModel, Field

from app.analytics.exceptions import OperationCancelledError


class DataQualityProfileInput(BaseModel):
    null_threshold_pct: float = Field(
        default=50.0, ge=0.0, le=100.0, description="Null percentage threshold to flag columns"
    )
    iqr_multiplier: float = Field(
        default=1.5,
        ge=1.0,
        le=5.0,
        description="IQR multiplier for outlier detection (default 1.5)",
    )


def execute_data_quality_profile(
    df: pl.DataFrame,
    params: DataQualityProfileInput,
    check_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Execute data quality profiling on Polars DataFrame."""
    total_rows = df.height
    total_cols = df.width

    # 1. Duplicate rows
    if total_rows > 0:
        unique_rows_count = df.n_unique()
        duplicate_rows_count = total_rows - unique_rows_count
        duplicate_pct = round((duplicate_rows_count / total_rows * 100.0), 2)
    else:
        duplicate_rows_count = 0
        duplicate_pct = 0.0

    # 2. Null-heavy columns & issues
    null_heavy_columns: list[dict[str, Any]] = []
    column_issues: dict[str, list[str]] = {}
    outliers: dict[str, Any] = {}

    for col in df.columns:
        if check_cancelled and check_cancelled():
            raise OperationCancelledError()

        series = df[col]
        null_count = int(series.null_count())
        null_pct = round((null_count / total_rows * 100.0), 2) if total_rows > 0 else 0.0

        issues: list[str] = []

        if null_pct >= params.null_threshold_pct:
            null_heavy_columns.append(
                {
                    "column": col,
                    "null_count": null_count,
                    "null_percentage": null_pct,
                }
            )
            issues.append(f"High null rate: {null_pct}%")

        if series.dtype == pl.String and total_rows > 0:
            cleaned = series.drop_nulls()
            empty_count = int((cleaned == "").sum())
            if empty_count > 0:
                issues.append(f"Contains {empty_count} empty string cells")

        # 3. Numeric Outliers via IQR
        if series.dtype.is_numeric() and series.drop_nulls().len() > 4:
            clean_arr = series.drop_nulls().to_numpy()
            q25 = float(np.percentile(clean_arr, 25))
            q75 = float(np.percentile(clean_arr, 75))
            iqr = q75 - q25

            if iqr > 0:
                lower_b = round(q25 - params.iqr_multiplier * iqr, 4)
                upper_b = round(q75 + params.iqr_multiplier * iqr, 4)
                outlier_mask = (clean_arr < lower_b) | (clean_arr > upper_b)
                outlier_count = int(np.sum(outlier_mask))

                if outlier_count > 0:
                    outliers[col] = {
                        "count": outlier_count,
                        "percentage": round(outlier_count / len(clean_arr) * 100.0, 2),
                        "lower_bound": lower_b,
                        "upper_bound": upper_b,
                        "min_detected": round(float(np.min(clean_arr)), 4),
                        "max_detected": round(float(np.max(clean_arr)), 4),
                    }
                    issues.append(f"Detected {outlier_count} potential outlier values via IQR")

        if issues:
            column_issues[col] = issues

    return {
        "operation": "data_quality_profile",
        "total_rows": total_rows,
        "total_columns": total_cols,
        "duplicate_rows": {
            "count": duplicate_rows_count,
            "percentage": duplicate_pct,
        },
        "null_heavy_columns": null_heavy_columns,
        "column_issues": column_issues,
        "outliers": outliers,
    }
