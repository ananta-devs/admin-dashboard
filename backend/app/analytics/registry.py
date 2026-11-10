"""Centralized allowlist analytics operation registry."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import polars as pl
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from app.analytics.exceptions import InvalidParametersError, OperationNotFoundError
from app.analytics.operations.aggregate import GroupByAggregateInput, execute_group_by_aggregate
from app.analytics.operations.correlation import CorrelationMatrixInput, execute_correlation_matrix
from app.analytics.operations.data_quality import (
    DataQualityProfileInput,
    execute_data_quality_profile,
)
from app.analytics.operations.describe import DescribeColumnsInput, execute_describe_columns
from app.analytics.operations.filter import FilterDatasetInput, execute_filter_dataset
from app.analytics.operations.profile import ProfileDatasetInput, execute_profile_dataset


@dataclass(frozen=True, slots=True)
class OperationDefinition:
    """Metadata specification for a registered, allowlisted analytics operation."""

    id: str
    name: str
    description: str
    input_schema: type[BaseModel]
    required_permission: str
    max_input_rows: int
    max_input_cols: int
    timeout_seconds: int
    handler: Callable[[pl.DataFrame, Any, Callable[[], bool] | None], dict[str, Any]]


class OperationRegistry:
    """Singleton registry of authorized, allowlisted analytics operations."""

    def __init__(self) -> None:
        self._operations: dict[str, OperationDefinition] = {}
        self._register_defaults()

    def register(self, definition: OperationDefinition) -> None:
        self._operations[definition.id] = definition

    def get(self, operation_id: str) -> OperationDefinition:
        if operation_id not in self._operations:
            raise OperationNotFoundError(operation_id)
        return self._operations[operation_id]

    def list_operations(self) -> list[OperationDefinition]:
        return list(self._operations.values())

    def validate_parameters(self, operation_id: str, raw_params: dict[str, Any]) -> BaseModel:
        definition = self.get(operation_id)
        try:
            return definition.input_schema.model_validate(raw_params or {})
        except PydanticValidationError as e:
            raise InvalidParametersError(f"Invalid parameters for '{operation_id}': {e}") from e

    def execute(
        self,
        operation_id: str,
        df: pl.DataFrame,
        raw_params: dict[str, Any],
        check_cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        definition = self.get(operation_id)
        validated_params = self.validate_parameters(operation_id, raw_params)
        return definition.handler(df, validated_params, check_cancelled)

    def _register_defaults(self) -> None:
        self.register(
            OperationDefinition(
                id="profile_dataset",
                name="Dataset Profiling",
                description="Comprehensive profile of row counts, column types, null percentages, distinct counts, and distributions.",
                input_schema=ProfileDatasetInput,
                required_permission="analytics.create",
                max_input_rows=100000,
                max_input_cols=100,
                timeout_seconds=60,
                handler=execute_profile_dataset,
            )
        )
        self.register(
            OperationDefinition(
                id="describe_columns",
                name="Descriptive Statistics",
                description="Calculates count, mean, median, standard deviation, min, max, and percentiles for numeric columns.",
                input_schema=DescribeColumnsInput,
                required_permission="analytics.create",
                max_input_rows=100000,
                max_input_cols=100,
                timeout_seconds=60,
                handler=execute_describe_columns,
            )
        )
        self.register(
            OperationDefinition(
                id="group_by_aggregate",
                name="Grouped Aggregation",
                description="Aggregates data by one or more grouping columns with COUNT, SUM, AVERAGE, MIN, and MAX.",
                input_schema=GroupByAggregateInput,
                required_permission="analytics.create",
                max_input_rows=100000,
                max_input_cols=100,
                timeout_seconds=60,
                handler=execute_group_by_aggregate,
            )
        )
        self.register(
            OperationDefinition(
                id="filter_dataset",
                name="Declarative Filtering",
                description="Filters dataset using safe allowlisted operators (equals, greater/less than, contains, is null, etc.).",
                input_schema=FilterDatasetInput,
                required_permission="analytics.create",
                max_input_rows=100000,
                max_input_cols=100,
                timeout_seconds=60,
                handler=execute_filter_dataset,
            )
        )
        self.register(
            OperationDefinition(
                id="correlation_matrix",
                name="Correlation Analysis",
                description="Computes pairwise Pearson correlation matrix across numeric columns with constant-column safeguards.",
                input_schema=CorrelationMatrixInput,
                required_permission="analytics.create",
                max_input_rows=100000,
                max_input_cols=50,
                timeout_seconds=60,
                handler=execute_correlation_matrix,
            )
        )
        self.register(
            OperationDefinition(
                id="data_quality_profile",
                name="Data Quality Profiling",
                description="Detects duplicate rows, null-heavy columns, inconsistent data formatting, and numeric outliers via IQR.",
                input_schema=DataQualityProfileInput,
                required_permission="analytics.create",
                max_input_rows=100000,
                max_input_cols=100,
                timeout_seconds=60,
                handler=execute_data_quality_profile,
            )
        )


registry = OperationRegistry()
