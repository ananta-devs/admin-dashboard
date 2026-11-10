"""Unit tests for Python Analytics Engine, Allowlisted Operation Registry, and Polars/NumPy operations."""

from __future__ import annotations

import polars as pl
import pytest

from app.analytics.exceptions import (
    InvalidParametersError,
    OperationCancelledError,
    OperationNotFoundError,
)
from app.analytics.operations.aggregate import GroupByAggregateInput, execute_group_by_aggregate
from app.analytics.operations.correlation import CorrelationMatrixInput, execute_correlation_matrix
from app.analytics.operations.data_quality import (
    DataQualityProfileInput,
    execute_data_quality_profile,
)
from app.analytics.operations.describe import DescribeColumnsInput, execute_describe_columns
from app.analytics.operations.filter import (
    FilterCondition,
    FilterDatasetInput,
    execute_filter_dataset,
)
from app.analytics.operations.profile import ProfileDatasetInput, execute_profile_dataset
from app.analytics.registry import registry


@pytest.fixture
def sample_dataframe() -> pl.DataFrame:
    """Fixture providing a multi-type test dataset."""
    return pl.DataFrame(
        {
            "department": [
                "Engineering",
                "Engineering",
                "Sales",
                "Sales",
                "Marketing",
                "Marketing",
                None,
            ],
            "salary": [
                120000.0,
                140000.0,
                90000.0,
                95000.0,
                80000.0,
                85000.0,
                500000.0,
            ],  # 500k is outlier
            "tenure_years": [3, 5, 2, 4, 1, 2, 10],
            "is_manager": [False, True, False, True, False, False, True],
            "notes": ["lead", "mgr", "rep", "lead", "spec", "spec", ""],
        }
    )


def test_operation_registry_allowlist() -> None:
    """Verify registry only exposes allowlisted operations and rejects arbitrary strings."""
    ops = registry.list_operations()
    op_ids = {op.id for op in ops}
    assert "profile_dataset" in op_ids
    assert "describe_columns" in op_ids
    assert "group_by_aggregate" in op_ids
    assert "filter_dataset" in op_ids
    assert "correlation_matrix" in op_ids
    assert "data_quality_profile" in op_ids

    # Unregistered operation rejection
    with pytest.raises(OperationNotFoundError):
        registry.get("arbitrary_eval_code")


def test_profile_dataset_operation(sample_dataframe: pl.DataFrame) -> None:
    """Test profile_dataset calculates counts, nulls, distincts, and distributions."""
    result = execute_profile_dataset(sample_dataframe, ProfileDatasetInput())
    assert result["total_rows"] == 7
    assert result["total_columns"] == 5

    dept_prof = result["columns"]["department"]
    assert dept_prof["null_count"] == 1
    assert round(dept_prof["null_percentage"], 1) == 14.3
    assert dept_prof["distinct_count"] == 4  # 3 distinct + null

    salary_prof = result["columns"]["salary"]
    assert salary_prof["null_count"] == 0
    assert salary_prof["min_value"] == 80000.0
    assert salary_prof["max_value"] == 500000.0
    assert salary_prof["mean"] is not None


def test_describe_columns_operation(sample_dataframe: pl.DataFrame) -> None:
    """Test describe_columns calculates standard statistical metrics."""
    result = execute_describe_columns(
        sample_dataframe,
        DescribeColumnsInput(columns=["salary", "tenure_years"], percentiles=[0.25, 0.5, 0.75]),
    )
    assert result["total_rows"] == 7
    assert "salary" in result["columns"]
    salary_stats = result["columns"]["salary"]
    assert salary_stats["count"] == 7
    assert salary_stats["min"] == 80000.0
    assert salary_stats["max"] == 500000.0
    assert "p25" in salary_stats["percentiles"]
    assert "p50" in salary_stats["percentiles"]
    assert "p75" in salary_stats["percentiles"]


def test_group_by_aggregate_operation(sample_dataframe: pl.DataFrame) -> None:
    """Test group_by_aggregate groups by columns and computes typed aggregations."""
    params = GroupByAggregateInput(
        group_by=["department"],
        aggregations=[
            {"column": "salary", "function": "sum", "alias": "total_payroll"},
            {"column": "salary", "function": "average", "alias": "avg_salary"},
            {"column": "salary", "function": "count", "alias": "headcount"},
            {"column": "tenure_years", "function": "max", "alias": "max_tenure"},
        ],
    )
    result = execute_group_by_aggregate(sample_dataframe, params)
    assert result["row_count"] > 0
    rows = {r["department"]: r for r in result["data"]}

    eng = rows.get("Engineering")
    assert eng is not None
    assert eng["total_payroll"] == 260000.0
    assert eng["avg_salary"] == 130000.0
    assert eng["headcount"] == 2
    assert eng["max_tenure"] == 5


def test_group_by_aggregate_type_validation(sample_dataframe: pl.DataFrame) -> None:
    """Verify sum/average rejects non-numeric columns."""
    params = GroupByAggregateInput(
        group_by=["department"],
        aggregations=[{"column": "notes", "function": "sum"}],
    )
    with pytest.raises(InvalidParametersError) as exc:
        execute_group_by_aggregate(sample_dataframe, params)
    assert "non-numeric" in str(exc.value)


def test_filter_dataset_operation(sample_dataframe: pl.DataFrame) -> None:
    """Test declarative filtering with allowlisted operators."""
    # 1. Salary > 90000 AND department is not null
    params = FilterDatasetInput(
        filters=[
            FilterCondition(column="salary", operator="greater_than", value=90000.0),
            FilterCondition(column="department", operator="is_not_null"),
        ],
        conjunction="and",
    )
    result = execute_filter_dataset(sample_dataframe, params)
    assert result["matched_rows"] == 3  # Eng (120k), Eng (140k), Sales (95k)

    # 2. String contains operator
    params_str = FilterDatasetInput(
        filters=[FilterCondition(column="notes", operator="contains", value="lead")],
    )
    res_str = execute_filter_dataset(sample_dataframe, params_str)
    assert res_str["matched_rows"] == 2


def test_correlation_matrix_operation(sample_dataframe: pl.DataFrame) -> None:
    """Test correlation matrix with numeric columns and constant column safeguard."""
    result = execute_correlation_matrix(
        sample_dataframe,
        CorrelationMatrixInput(columns=["salary", "tenure_years"]),
    )
    matrix = result["matrix"]
    assert matrix["salary"]["salary"] == 1.0
    assert matrix["tenure_years"]["tenure_years"] == 1.0
    assert matrix["salary"]["tenure_years"] is not None
    assert -1.0 <= matrix["salary"]["tenure_years"] <= 1.0

    # Test with constant column (zero variance)
    const_df = sample_dataframe.with_columns(pl.lit(42.0).alias("constant_col"))
    res_const = execute_correlation_matrix(
        const_df,
        CorrelationMatrixInput(columns=["salary", "constant_col"]),
    )
    # Undefined correlation must be None, never fabricated!
    assert res_const["matrix"]["salary"]["constant_col"] is None


def test_data_quality_profile_operation(sample_dataframe: pl.DataFrame) -> None:
    """Test data quality checks for nulls, duplicates, and IQR outliers."""
    result = execute_data_quality_profile(
        sample_dataframe,
        DataQualityProfileInput(null_threshold_pct=10.0, iqr_multiplier=1.5),
    )
    assert result["total_rows"] == 7
    # 500,000 in salary is an extreme outlier detected by IQR
    assert "salary" in result["outliers"]
    assert result["outliers"]["salary"]["count"] == 1
    assert result["outliers"]["salary"]["max_detected"] == 500000.0


def test_cancellation_checkpoint(sample_dataframe: pl.DataFrame) -> None:
    """Verify cooperative cancellation checkpoints raise OperationCancelledError."""

    def cancelled_check() -> bool:
        return True

    with pytest.raises(OperationCancelledError):
        execute_profile_dataset(
            sample_dataframe, ProfileDatasetInput(), check_cancelled=cancelled_check
        )
