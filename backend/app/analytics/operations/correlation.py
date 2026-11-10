"""Correlation Analysis Operation."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import numpy as np
import polars as pl
from pydantic import BaseModel, Field

from app.analytics.exceptions import InvalidParametersError, OperationCancelledError


class CorrelationMatrixInput(BaseModel):
    columns: list[str] | None = Field(
        default=None, description="Numeric columns to correlate (correlates all numeric if omitted)"
    )
    method: str = Field(default="pearson", description="Correlation method: pearson")


def execute_correlation_matrix(
    df: pl.DataFrame,
    params: CorrelationMatrixInput,
    check_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Calculate correlation matrix across numeric columns."""
    all_numeric = [c for c in df.columns if df[c].dtype.is_numeric()]

    if params.columns:
        target_cols = [c for c in params.columns if c in all_numeric]
    else:
        target_cols = all_numeric

    if len(target_cols) < 2:
        raise InvalidParametersError(
            f"Correlation requires at least 2 numeric columns (found {len(target_cols)})."
        )

    matrix: dict[str, dict[str, float | None]] = {c: {} for c in target_cols}

    for i, col_a in enumerate(target_cols):
        if check_cancelled and check_cancelled():
            raise OperationCancelledError()

        matrix[col_a][col_a] = 1.0

        for col_b in target_cols[i + 1 :]:
            # Pairwise drop nulls
            pair_df = df.select([col_a, col_b]).drop_nulls()

            if pair_df.height < 2:
                matrix[col_a][col_b] = None
                matrix[col_b][col_a] = None
                continue

            arr_a = pair_df[col_a].to_numpy()
            arr_b = pair_df[col_b].to_numpy()

            std_a = np.std(arr_a)
            std_b = np.std(arr_b)

            # If either column is constant (zero variance), correlation is mathematically undefined
            if std_a == 0.0 or std_b == 0.0 or np.isnan(std_a) or np.isnan(std_b):
                matrix[col_a][col_b] = None
                matrix[col_b][col_a] = None
                continue

            try:
                corr_matrix = np.corrcoef(arr_a, arr_b)
                c_val = float(corr_matrix[0, 1])
                if math.isnan(c_val) or math.isinf(c_val):
                    corr_val: float | None = None
                else:
                    corr_val = round(c_val, 4)
            except Exception:
                corr_val = None

            matrix[col_a][col_b] = corr_val
            matrix[col_b][col_a] = corr_val

    return {
        "operation": "correlation_matrix",
        "method": params.method,
        "columns": target_cols,
        "matrix": matrix,
    }
