"""Dataset Profiling Operation."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import polars as pl
from pydantic import BaseModel, Field

from app.analytics.exceptions import OperationCancelledError


class ProfileDatasetInput(BaseModel):
    columns: list[str] | None = Field(
        default=None, description="Subset of columns to profile (profiles all if omitted)"
    )
    max_categories: int = Field(
        default=10, ge=1, le=50, description="Maximum top frequent categories to report"
    )


def execute_profile_dataset(
    df: pl.DataFrame,
    params: ProfileDatasetInput,
    check_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Execute dataset profiling using Polars."""
    total_rows = df.height
    total_cols = df.width

    target_cols = [c for c in (params.columns or df.columns) if c in df.columns]
    if not target_cols:
        target_cols = df.columns

    column_profiles: dict[str, Any] = {}

    for col_name in target_cols:
        if check_cancelled and check_cancelled():
            raise OperationCancelledError()

        series = df[col_name]
        null_count = int(series.null_count())
        null_pct = round((null_count / total_rows * 100.0), 2) if total_rows > 0 else 0.0
        distinct_count = int(series.n_unique())

        profile: dict[str, Any] = {
            "name": col_name,
            "dtype": str(series.dtype),
            "null_count": null_count,
            "null_percentage": null_pct,
            "distinct_count": distinct_count,
            "min_value": None,
            "max_value": None,
            "mean": None,
            "median": None,
            "std_dev": None,
            "top_values": None,
        }

        if series.dtype.is_numeric() and series.drop_nulls().len() > 0:
            cleaned = series.drop_nulls()
            profile["min_value"] = float(cleaned.min()) if cleaned.min() is not None else None  # type: ignore[arg-type]
            profile["max_value"] = float(cleaned.max()) if cleaned.max() is not None else None  # type: ignore[arg-type]
            profile["mean"] = (
                round(float(cleaned.mean()), 4) if cleaned.mean() is not None else None  # type: ignore[arg-type]
            )
            profile["median"] = (
                round(float(cleaned.median()), 4) if cleaned.median() is not None else None  # type: ignore[arg-type]
            )
            try:
                std_val = cleaned.std()
                profile["std_dev"] = round(float(std_val), 4) if std_val is not None else None  # type: ignore[arg-type]
            except Exception:
                profile["std_dev"] = None
        elif (
            series.dtype == pl.String or series.dtype == pl.Boolean
        ) and series.drop_nulls().len() > 0:
            cleaned = series.drop_nulls()
            val_counts = cleaned.value_counts(sort=True).head(params.max_categories).to_dicts()
            profile["top_values"] = val_counts
            try:
                profile["min_value"] = str(cleaned.min()) if cleaned.min() is not None else None
                profile["max_value"] = str(cleaned.max()) if cleaned.max() is not None else None
            except Exception:
                pass

        column_profiles[col_name] = profile

    return {
        "operation": "profile_dataset",
        "total_rows": total_rows,
        "total_columns": total_cols,
        "analyzed_columns_count": len(target_cols),
        "columns": column_profiles,
    }
