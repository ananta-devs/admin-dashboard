"""Analytics engine exceptions and structured failure classifications."""

from __future__ import annotations

from app.core.exceptions import AppException


class AnalyticsError(AppException):
    """Base exception for all analytical operations."""

    def __init__(self, message: str, code: str = "ANALYTICS_ERROR"):
        super().__init__(message=message, code=code)


class OperationNotFoundError(AnalyticsError):
    def __init__(self, operation_id: str):
        super().__init__(
            f"Operation '{operation_id}' is not registered in the allowlisted operation catalogue.",
            code="OPERATION_NOT_FOUND",
        )


class InvalidParametersError(AnalyticsError):
    def __init__(self, message: str):
        super().__init__(message, code="INVALID_PARAMETERS")


class DatasetTooLargeError(AnalyticsError):
    def __init__(self, message: str):
        super().__init__(message, code="DATASET_TOO_LARGE")


class OperationCancelledError(AnalyticsError):
    def __init__(self, message: str = "Analytical operation was cancelled by user request."):
        super().__init__(message, code="OPERATION_CANCELLED")


class DataProcessingError(AnalyticsError):
    def __init__(self, message: str, code: str = "DATA_PROCESSING_ERROR"):
        super().__init__(message, code=code)


class WorkerFencingViolationError(AnalyticsError):
    def __init__(
        self, message: str = "Worker lost lease or fencing token invalidated by newer worker."
    ):
        super().__init__(message, code="FENCING_VIOLATION")


class StorageQuotaUnavailableError(AnalyticsError):
    """Raised when the authoritative quota storage ledger cannot be reached, failing closed safely."""

    def __init__(
        self, message: str = "Authoritative storage quota service is temporarily unavailable."
    ):
        super().__init__(message, code="STORAGE_QUOTA_UNAVAILABLE")
