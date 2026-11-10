"""Descriptive Statistics Operation using Polars & NumPy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import polars as pl
from pydantic import BaseModel, Field

from app.analytics.exceptions import OperationCancelledError


class DescribeColumnsInput(BaseModel):
    columns: list[str] | None = Field(
        default=None, description="List of columns to describe (defaults to all numeric columns)"
    )
    percentiles: list[float] = Field(
        default=[0.25, 0.5, 0.75],
        description="List of percentile fractions to calculate (between 0.01 and 0.99)",
    )


def execute_describe_columns(
    df: pl.DataFrame,
    params: DescribeColumnsInput,
    check_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Calculate descriptive statistics for numeric columns."""
    all_numeric_cols = [c for c in df.columns if df[c].dtype.is_numeric()]

    if params.columns:
        target_cols = [c for c in params.columns if c in all_numeric_cols]
    else:
        target_cols = all_numeric_cols

    summaries: dict[str, Any] = {}

    for col in target_cols:
        if check_cancelled and check_cancelled():
            raise OperationCancelledError()

        series = df[col].drop_nulls()
        n_obs = len(series)

        if n_obs == 0:
            summaries[col] = {
                "count": 0,
                "mean": None,
                "std": None,
                "min": None,
                "max": None,
                "median": None,
                "percentiles": {},
            }
            continue

        arr = series.to_numpy()
        mean_val = float(np.mean(arr))
        min_val = float(np.min(arr))
        max_val = float(np.max(arr))
        std_val = float(np.std(arr, ddof=1)) if n_obs > 1 else 0.0
        median_val = float(np.median(arr))

        pct_results: dict[str, float] = {}
        for p in params.percentiles:
            if 0.0 <= p <= 1.0:
                pct_key = f"p{int(p * 100)}"
                pct_results[pct_key] = round(float(np.percentile(arr, p * 100)), 4)

        summaries[col] = {
            "count": n_obs,
            "mean": round(mean_val, 4),
            "std": round(std_val, 4),
            "min": round(min_val, 4),
            "max": round(max_val, 4),
            "median": round(median_val, 4),
            "percentiles": pct_results,
        }

    return {
        "operation": "describe_columns",
        "total_rows": df.height,
        "analyzed_numeric_columns": len(target_cols),
        "columns": summaries,
    }
