from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.health import router as health_router
from app.api.v1.routers.analytics import router as analytics_router
from app.api.v1.routers.auth import router as auth_router
from app.api.v1.routers.backups import router as backups_router
from app.api.v1.routers.dashboards import router as dashboards_router
from app.api.v1.routers.files import router as files_router
from app.api.v1.routers.integrations import router as integrations_router
from app.api.v1.routers.notifications import router as notifications_router
from app.api.v1.routers.organization import router as organization_router
from app.api.v1.routers.reports import router as reports_router
from app.api.v1.routers.scripts import router as scripts_router
from app.api.v1.routers.spreadsheets import router as spreadsheets_router
from app.api.v1.routers.sync import router as sync_router
from app.api.v1.routers.system import router as system_router
from app.api.v1.routers.workspaces import router as workspaces_router
from app.core.config import settings
from app.core.exceptions import (
    AppException,
    AuthenticationError,
    BusinessRuleViolationError,
    ConflictError,
    CrossTenantAccessError,
    CsrfError,
    EmailNotVerifiedError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    ForbiddenError,
    InvalidTokenError,
    RateLimitExceededError,
    ServiceUnavailableError,
)
from app.core.logging import logger, setup_logging

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Enterprise Admin Backend Core...")
    # Validate cryptographic keys configuration at startup
    keys = {
        "SESSION_SECRET_KEY": settings.SESSION_SECRET_KEY,
        "ENCRYPTION_MASTER_KEY": settings.ENCRYPTION_MASTER_KEY,
        "RATE_LIMIT_HMAC_KEY": settings.RATE_LIMIT_HMAC_KEY,
    }
    for name, key in keys.items():
        if not key or len(key) < 32:
            raise RuntimeError(f"Cryptographic key {name} must be at least 32 characters long.")
    if len(set(keys.values())) != len(keys):
        raise RuntimeError("Cryptographic keys must be pairwise distinct.")
    logger.info("Cryptographic key separation validated successfully.")

    # Fail-closed Database Identity Gate for E2E Testing
    expected_db = settings.E2E_EXPECTED_DATABASE
    if expected_db:
        from sqlalchemy import text

        from app.db.session import async_session_maker

        async with async_session_maker() as session:
            result = await session.execute(text("SELECT current_database();"))
            actual_db = result.scalar()
            if actual_db != expected_db:
                raise RuntimeError(
                    f"FATAL: Database identity mismatch! Expected '{expected_db}', but connected to '{actual_db}'. "
                    "Startup aborted to prevent cross-database mutation."
                )
            if actual_db in ("vibe_enterprise", "vibe_benchmark", "postgres"):
                raise RuntimeError(
                    f"FATAL: Attempting to run E2E test backend against protected database '{actual_db}'! "
                    "Startup aborted."
                )
            logger.info(
                "Database identity verified: %s matches expected %s", actual_db, expected_db
            )

    yield
    logger.info("Shutting down Enterprise Admin Backend Core...")


class SecurityHeadersMiddleware:
    """ASGI middleware applying defense-in-depth security headers to all responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["x-content-type-options"] = "nosniff"
                headers["x-frame-options"] = "DENY"
                headers["referrer-policy"] = "strict-origin-when-cross-origin"
                headers["permissions-policy"] = "camera=(), microphone=(), geolocation=()"
                scheme = scope.get("scheme", "http")
                server_headers = dict(scope.get("headers", []))
                x_forwarded_proto = (
                    server_headers.get(b"x-forwarded-proto", b"").decode("latin1").lower()
                )
                if scheme == "https" or x_forwarded_proto == "https":
                    headers["strict-transport-security"] = "max-age=31536000; includeSubDomains"
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


is_dev = settings.ENVIRONMENT.lower() in ("development", "dev", "test", "testing", "local")

app = FastAPI(
    title="Enterprise Admin & Data Intelligence Platform API",
    version="1.0.0",
    description="Authoritative API and Database Owner",
    lifespan=lifespan,
    docs_url="/docs" if is_dev else None,
    redoc_url="/redoc" if is_dev else None,
    openapi_url="/openapi.json" if is_dev else None,
)

# Standard CORS configuration (Explicit origins, credentials enabled, no wildcard)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Authorization",
        "X-CSRF-Token",
        "X-Requested-With",
        "Accept",
        "Origin",
    ],
    expose_headers=["Content-Type", "Retry-After"],
    max_age=600,
)

app.add_middleware(SecurityHeadersMiddleware)


@app.exception_handler(EntityNotFoundError)
async def entity_not_found_handler(request: Request, exc: EntityNotFoundError):
    return JSONResponse(
        status_code=404,
        content={"success": False, "error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(EntityAlreadyExistsError)
async def entity_exists_handler(request: Request, exc: EntityAlreadyExistsError):
    return JSONResponse(
        status_code=409,
        content={"success": False, "error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(AuthenticationError)
async def authentication_error_handler(request: Request, exc: AuthenticationError):
    response = JSONResponse(
        status_code=401,
        content={"success": False, "error": {"code": exc.code, "message": exc.message}},
    )
    # If the client sent a session cookie that failed authentication (e.g. stale/revoked/server restart),
    # explicitly clear it from the client so the browser does not stay trapped in an unauthorized loop.
    cookie_name = settings.SESSION_COOKIE_NAME
    if request.cookies.get(cookie_name) or request.cookies.get("vibe_session"):
        response.delete_cookie(
            key=cookie_name,
            path="/",
            httponly=True,
            samesite="lax",
        )
        if cookie_name != "vibe_session":
            response.delete_cookie(
                key="vibe_session",
                path="/",
                httponly=True,
                samesite="lax",
            )
    return response


@app.exception_handler(CsrfError)
async def csrf_error_handler(request: Request, exc: CsrfError):
    logger.warning(
        f"CSRF verification failure on {request.method} {request.url.path}: {exc.message}"
    )
    return JSONResponse(
        status_code=403,
        content={"success": False, "error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(ForbiddenError)
async def forbidden_error_handler(request: Request, exc: ForbiddenError):
    return JSONResponse(
        status_code=403,
        content={"success": False, "error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(EmailNotVerifiedError)
async def email_not_verified_handler(request: Request, exc: EmailNotVerifiedError):
    return JSONResponse(
        status_code=403,
        content={"success": False, "error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(RateLimitExceededError)
async def rate_limit_error_handler(request: Request, exc: RateLimitExceededError):
    return JSONResponse(
        status_code=429,
        headers={"Retry-After": str(exc.retry_after_seconds)},
        content={
            "success": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                "retry_after_seconds": exc.retry_after_seconds,
            },
        },
    )


@app.exception_handler(ServiceUnavailableError)
async def service_unavailable_handler(request: Request, exc: ServiceUnavailableError):
    return JSONResponse(
        status_code=503,
        headers={"Retry-After": "10"},
        content={
            "success": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
            },
        },
    )


@app.exception_handler(InvalidTokenError)
async def invalid_token_handler(request: Request, exc: InvalidTokenError):
    return JSONResponse(
        status_code=400,
        content={"success": False, "error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(BusinessRuleViolationError)
async def business_rule_error_handler(request: Request, exc: BusinessRuleViolationError):
    return JSONResponse(
        status_code=422,
        content={"success": False, "error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(ConflictError)
async def conflict_handler(request: Request, exc: ConflictError):
    return JSONResponse(
        status_code=409,
        content={"success": False, "error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(CrossTenantAccessError)
async def cross_tenant_handler(request: Request, exc: CrossTenantAccessError):
    logger.warning(
        f"Security Alert: Cross-tenant access attempt on {request.url.path}: {exc.message}"
    )
    # Return 404 to external clients to prevent resource existence enumeration
    return JSONResponse(
        status_code=404,
        content={
            "success": False,
            "error": {
                "code": "ENTITY_NOT_FOUND",
                "message": "The requested resource was not found.",
            },
        },
    )


@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(
        status_code=400,
        content={"success": False, "error": {"code": exc.code, "message": exc.message}},
    )


# Global Fallback Exception Handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled error on {request.method} {request.url.path}: {exc!s}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An internal server error occurred.",
            },
        },
    )


# Register Routers
app.include_router(health_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(organization_router, prefix="/api/v1")
app.include_router(spreadsheets_router, prefix="/api/v1")
app.include_router(scripts_router, prefix="/api/v1")
app.include_router(analytics_router, prefix="/api/v1")
app.include_router(dashboards_router, prefix="/api/v1")
app.include_router(reports_router, prefix="/api/v1")
app.include_router(integrations_router, prefix="/api/v1")
app.include_router(sync_router, prefix="/api/v1")
app.include_router(backups_router, prefix="/api/v1")
app.include_router(workspaces_router, prefix="/api/v1")
app.include_router(files_router, prefix="/api/v1")
app.include_router(system_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1")
