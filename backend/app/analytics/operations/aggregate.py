"""Grouped Aggregations Operation using Polars."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import polars as pl
from pydantic import BaseModel, Field

from app.analytics.exceptions import InvalidParametersError, OperationCancelledError


class AggregationSpec(BaseModel):
    column: str = Field(..., description="Target column to aggregate")
    function: str = Field(..., description="Aggregation function: count, sum, average, min, max")
    alias: str | None = Field(default=None, description="Optional custom output column name")


class GroupByAggregateInput(BaseModel):
    group_by: list[str] = Field(..., min_length=1, description="List of columns to group by")
    aggregations: list[AggregationSpec] = Field(
        ..., min_length=1, description="List of aggregations to perform"
    )
    limit: int = Field(default=1000, ge=1, le=10000, description="Maximum result rows to return")


def execute_group_by_aggregate(
    df: pl.DataFrame,
    params: GroupByAggregateInput,
    check_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Execute grouped aggregation using Polars."""
    if check_cancelled and check_cancelled():
        raise OperationCancelledError()

    for col in params.group_by:
        if col not in df.columns:
            raise InvalidParametersError(f"Grouping column '{col}' does not exist in dataset.")

    agg_exprs: list[pl.Expr] = []
    output_columns: list[str] = list(params.group_by)

    for spec in params.aggregations:
        if spec.column not in df.columns:
            raise InvalidParametersError(
                f"Aggregation target column '{spec.column}' does not exist."
            )

        col_expr = pl.col(spec.column)
        fn_lower = spec.function.lower().strip()
        alias = spec.alias or f"{spec.column}_{fn_lower}"
        output_columns.append(alias)

        is_numeric = df[spec.column].dtype.is_numeric()

        if fn_lower == "count":
            agg_exprs.append(col_expr.count().alias(alias))
        elif fn_lower == "sum":
            if not is_numeric:
                raise InvalidParametersError(
                    f"Cannot calculate SUM on non-numeric column '{spec.column}'."
                )
            agg_exprs.append(col_expr.sum().alias(alias))
        elif fn_lower in ("average", "avg", "mean"):
            if not is_numeric:
                raise InvalidParametersError(
                    f"Cannot calculate AVERAGE on non-numeric column '{spec.column}'."
                )
            agg_exprs.append(col_expr.mean().alias(alias))
        elif fn_lower == "min":
            agg_exprs.append(col_expr.min().alias(alias))
        elif fn_lower == "max":
            agg_exprs.append(col_expr.max().alias(alias))
        else:
            raise InvalidParametersError(
                f"Unsupported aggregation function '{spec.function}'. Allowed: count, sum, average, min, max."
            )

    result_df = df.group_by(params.group_by).agg(agg_exprs).head(params.limit)

    if check_cancelled and check_cancelled():
        raise OperationCancelledError()

    rows = result_df.to_dicts()

    return {
        "operation": "group_by_aggregate",
        "group_by_columns": params.group_by,
        "output_columns": output_columns,
        "row_count": len(rows),
        "data": rows,
    }
