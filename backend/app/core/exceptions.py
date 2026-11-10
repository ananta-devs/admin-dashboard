class AppException(Exception):
    """Base exception for application errors."""

    def __init__(self, message: str, code: str = "APPLICATION_ERROR") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class EntityNotFoundError(AppException):
    """Raised when a requested entity does not exist."""

    def __init__(self, entity_name: str, identifier: str) -> None:
        super().__init__(
            message=f"{entity_name} with identifier '{identifier}' was not found.",
            code="ENTITY_NOT_FOUND",
        )
        self.entity_name = entity_name
        self.identifier = identifier


class EntityAlreadyExistsError(AppException):
    """Raised when an entity with a unique field already exists."""

    def __init__(self, entity_name: str, field_name: str, value: str) -> None:
        super().__init__(
            message=f"{entity_name} with {field_name} '{value}' already exists.",
            code="ENTITY_ALREADY_EXISTS",
        )
        self.entity_name = entity_name
        self.field_name = field_name
        self.value = value


class CrossTenantAccessError(AppException):
    """Raised when an operation attempts to access an entity across organization boundaries."""

    def __init__(self, entity_name: str, entity_id: str, organization_id: str) -> None:
        super().__init__(
            message=(
                f"Unauthorized access: {entity_name} '{entity_id}' does not belong "
                f"to organization '{organization_id}'."
            ),
            code="CROSS_TENANT_ACCESS_DENIED",
        )
        self.entity_name = entity_name
        self.entity_id = entity_id
        self.organization_id = organization_id


class BusinessRuleViolationError(AppException):
    """Raised when a domain business rule is violated."""

    def __init__(self, message: str) -> None:
        super().__init__(message=message, code="BUSINESS_RULE_VIOLATION")


class ConflictError(AppException):
    """Raised when an optimistic concurrency conflict or stale revision occurs."""

    def __init__(self, message: str) -> None:
        super().__init__(message=message, code="CONFLICT")


class ValidationError(AppException):
    """Raised when request payload or business validation fails."""

    def __init__(self, message: str, code: str = "VALIDATION_ERROR") -> None:
        super().__init__(message=message, code=code)


class AuthenticationError(AppException):
    """Raised when authentication fails (credentials, session expired, invalid token)."""

    def __init__(
        self, message: str = "Authentication failed.", code: str = "AUTHENTICATION_FAILED"
    ) -> None:
        super().__init__(message=message, code=code)


class EmailNotVerifiedError(AuthenticationError):
    """Raised when an unverified user attempts an action requiring verification."""

    def __init__(self, message: str = "Email address has not been verified.") -> None:
        super().__init__(message=message, code="EMAIL_NOT_VERIFIED")


class ForbiddenError(AppException):
    """Raised when an authenticated identity lacks authorization for an operation."""

    def __init__(self, message: str = "Access forbidden.", code: str = "FORBIDDEN") -> None:
        super().__init__(message=message, code=code)


class CsrfError(ForbiddenError):
    """Raised when CSRF token validation fails on an unsafe request."""

    def __init__(self, message: str = "CSRF verification failed.") -> None:
        super().__init__(message=message, code="CSRF_VERIFICATION_FAILED")


class RateLimitExceededError(AppException):
    """Raised when request rate exceeds configured thresholds."""

    def __init__(
        self,
        message: str = "Too many requests. Please try again later.",
        retry_after_seconds: int = 60,
    ) -> None:
        super().__init__(message=message, code="RATE_LIMIT_EXCEEDED")
        self.retry_after_seconds = retry_after_seconds


class InvalidTokenError(AppException):
    """Raised when an action token is expired, consumed, or malformed."""

    def __init__(
        self, message: str = "Invalid or expired token.", code: str = "INVALID_TOKEN"
    ) -> None:
        super().__init__(message=message, code=code)


class MfaRequiredError(AppException):
    """Raised when primary credentials are valid but an MFA challenge must be completed."""

    def __init__(self, challenge_token: str, message: str = "MFA verification required.") -> None:
        super().__init__(message=message, code="MFA_REQUIRED")
        self.challenge_token = challenge_token


class ServiceUnavailableError(AppException):
    """Raised when an essential backend dependency (e.g. Redis) is unavailable."""

    def __init__(
        self,
        message: str = "Service temporarily unavailable. Please retry shortly.",
        code: str = "SERVICE_UNAVAILABLE",
    ) -> None:
        super().__init__(message=message, code=code)


class RenderingError(AppException):
    """Raised when report or PDF rendering fails."""

    def __init__(
        self,
        message: str = "Report rendering failed.",
        code: str = "RENDERING_ERROR",
    ) -> None:
        super().__init__(message=message, code=code)


class LeaseExpiredError(AppException):
    """Raised when a worker lease expires or is superseded by a newer claim generation."""

    def __init__(
        self,
        message: str = "Worker lease expired or stolen.",
        code: str = "LEASE_EXPIRED",
    ) -> None:
        super().__init__(message=message, code=code)
