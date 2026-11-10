"""Dataset Filtering Operation using declarative Polars expressions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import polars as pl
from pydantic import BaseModel, Field

from app.analytics.exceptions import InvalidParametersError, OperationCancelledError

VALID_OPERATORS = {
    "equals",
    "not_equals",
    "greater_than",
    "greater_than_or_equal",
    "less_than",
    "less_than_or_equal",
    "contains",
    "starts_with",
    "is_null",
    "is_not_null",
    "in_list",
}


class FilterCondition(BaseModel):
    column: str = Field(..., description="Column name to filter on")
    operator: str = Field(..., description="Filter operator")
    value: Any = Field(default=None, description="Operand value")


class FilterDatasetInput(BaseModel):
    filters: list[FilterCondition] = Field(
        ..., min_length=1, description="List of filter conditions"
    )
    conjunction: str = Field(default="and", description="'and' or 'or'")
    columns: list[str] | None = Field(default=None, description="Columns to project in output")
    limit: int = Field(default=1000, ge=1, le=10000, description="Maximum filtered rows to return")


def execute_filter_dataset(
    df: pl.DataFrame,
    params: FilterDatasetInput,
    check_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Execute declarative filtering on Polars DataFrame."""
    if check_cancelled and check_cancelled():
        raise OperationCancelledError()

    expr_list: list[pl.Expr] = []

    for cond in params.filters:
        col_name = cond.column
        if col_name not in df.columns:
            raise InvalidParametersError(f"Filter column '{col_name}' does not exist in dataset.")

        op = cond.operator.lower().strip()
        if op not in VALID_OPERATORS:
            raise InvalidParametersError(
                f"Unsupported operator '{cond.operator}'. Allowed: {sorted(VALID_OPERATORS)}"
            )

        col_expr = pl.col(col_name)
        val = cond.value

        if op == "equals":
            expr_list.append(col_expr == val)
        elif op == "not_equals":
            expr_list.append(col_expr != val)
        elif op == "greater_than":
            expr_list.append(col_expr > val)
        elif op == "greater_than_or_equal":
            expr_list.append(col_expr >= val)
        elif op == "less_than":
            expr_list.append(col_expr < val)
        elif op == "less_than_or_equal":
            expr_list.append(col_expr <= val)
        elif op == "contains":
            expr_list.append(col_expr.cast(pl.String).str.contains(str(val), literal=True))
        elif op == "starts_with":
            expr_list.append(col_expr.cast(pl.String).str.starts_with(str(val)))
        elif op == "is_null":
            expr_list.append(col_expr.is_null())
        elif op == "is_not_null":
            expr_list.append(col_expr.is_not_null())
        elif op == "in_list":
            if not isinstance(val, (list, tuple, set)):
                raise InvalidParametersError(
                    f"Operator 'in_list' requires a list value, got {type(val)}."
                )
            expr_list.append(col_expr.is_in(list(val)))

    if not expr_list:
        combined_expr = pl.lit(True)
    elif params.conjunction.lower() == "or":
        combined_expr = expr_list[0]
        for e in expr_list[1:]:
            combined_expr = combined_expr | e
    else:
        combined_expr = expr_list[0]
        for e in expr_list[1:]:
            combined_expr = combined_expr & e

    filtered_df = df.filter(combined_expr)

    if params.columns:
        valid_proj = [c for c in params.columns if c in filtered_df.columns]
        if valid_proj:
            filtered_df = filtered_df.select(valid_proj)

    matched_count = filtered_df.height
    result_df = filtered_df.head(params.limit)

    if check_cancelled and check_cancelled():
        raise OperationCancelledError()

    rows = result_df.to_dicts()

    return {
        "operation": "filter_dataset",
        "total_source_rows": df.height,
        "matched_rows": matched_count,
        "returned_rows": len(rows),
        "columns": result_df.columns,
        "data": rows,
    }
