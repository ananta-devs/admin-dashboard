from typing import Any

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    POSTGRES_DB: str = Field(default="vibe_enterprise")
    POSTGRES_USER: str = Field(default="vibe_admin")
    POSTGRES_PASSWORD: str = Field(default="dev_secret_postgres_pass_change_in_prod")
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://vibe_admin:dev_secret_postgres_pass_change_in_prod@127.0.0.1:5432/vibe_enterprise"
    )
    DATABASE_SYNC_URL: str = Field(
        default="postgresql+psycopg://vibe_admin:dev_secret_postgres_pass_change_in_prod@127.0.0.1:5432/vibe_enterprise"
    )
    E2E_EXPECTED_DATABASE: str | None = Field(
        default=None, description="Expected database identity for E2E fail-closed gate"
    )

    # Configurable SQLAlchemy pool settings
    DB_POOL_SIZE: int = Field(default=10, description="SQLAlchemy pool size")
    DB_MAX_OVERFLOW: int = Field(default=20, description="SQLAlchemy max overflow connections")
    DB_POOL_TIMEOUT: int = Field(
        default=30, description="Seconds to wait before giving up on acquiring a connection"
    )
    DB_POOL_RECYCLE: int = Field(
        default=1800, description="Recycle connections older than this number of seconds"
    )
    DB_ECHO: bool = Field(default=False, description="Log SQLAlchemy raw statements")

    REDIS_PASSWORD: str = Field(default="dev_secret_redis_pass_change_in_prod")
    REDIS_URL: str = Field(default="redis://:dev_secret_redis_pass_change_in_prod@127.0.0.1:6379/0")
    REDIS_CACHE_URL: str = Field(
        default="redis://:dev_secret_redis_pass_change_in_prod@127.0.0.1:6379/1"
    )

    SESSION_SECRET_KEY: str = Field(
        default="dev_0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        description="Dedicated secret key for session signing and authentication tokens",
    )
    ENCRYPTION_MASTER_KEY: str = Field(
        default="dev_fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210",
        description="Master encryption key for database-stored secrets (e.g. TOTP MFA)",
    )
    RATE_LIMIT_HMAC_KEY: str = Field(
        default="dev_rate_limit_hmac_key_min_32_chars_long_0123456789abcdef",
        description="Dedicated HMAC key for rate limiter identifier hashing in Redis",
    )

    FILE_STORAGE_PATH: str = Field(default="./uploads")
    ANALYTICS_STORAGE_DIR: str = Field(default="./uploads/analytics")

    # Celery & Analytics Worker Settings
    CELERY_BROKER_URL: str = Field(
        default="redis://:dev_secret_redis_pass_change_in_prod@127.0.0.1:6379/1",
        description="Redis broker URL for Celery background tasks (DB 1)",
    )
    CELERY_RESULT_BACKEND: str | None = Field(
        default=None,
        description="Disabled - PostgreSQL is the sole authoritative store for job status and results",
    )
    CELERY_TASK_DEFAULT_QUEUE: str = Field(default="analytics")
    ANALYTICS_MAX_SOURCE_ROWS: int = Field(default=100000)
    ANALYTICS_MAX_SOURCE_COLS: int = Field(default=100)
    ANALYTICS_MAX_CELLS: int = Field(default=2000000)
    ANALYTICS_MAX_NON_EMPTY_CELLS: int = Field(default=500000)
    ANALYTICS_MAX_SNAPSHOT_BYTES: int = Field(default=52428800)  # 50 MB
    ANALYTICS_MAX_RESULT_ROWS: int = Field(default=50000)
    ANALYTICS_MAX_RESULT_PAYLOAD_BYTES: int = Field(default=10485760)  # 10 MB
    ANALYTICS_MAX_ARTIFACT_BYTES: int = Field(default=104857600)  # 100 MB
    ANALYTICS_MAX_ORG_STORAGE_BYTES: int = Field(default=1073741824)  # 1 GB
    ANALYTICS_JOB_TIMEOUT_SECONDS: int = Field(default=120)
    ANALYTICS_WORKER_LEASE_SECONDS: int = Field(default=60)
    ANALYTICS_HEARTBEAT_INTERVAL_SECONDS: int = Field(default=15)
    ANALYTICS_DISPATCH_TIMEOUT_SECONDS: int = Field(default=60)
    ANALYTICS_RETENTION_DAYS: int = Field(default=30)
    ANALYTICS_MAX_CONCURRENT_JOBS_PER_ORG: int = Field(default=5)
    ANALYTICS_MAX_CONCURRENT_JOBS_PER_USER: int = Field(default=2)

    # Phase 9 Gate 3: Reporting & Snapshot Engine Settings
    REPORTS_STORAGE_DIR: str = Field(default="./uploads/reports")
    REPORT_SNAPSHOT_INLINE_MAX_BYTES: int = Field(default=2097152)  # 2 MiB
    REPORT_SNAPSHOT_TOTAL_MAX_BYTES: int = Field(default=10485760)  # 10 MiB
    REPORT_MAX_SECTIONS: int = Field(default=25)
    REPORT_MAX_ROWS_PER_SECTION: int = Field(default=5000)
    REPORT_JOB_TIMEOUT_SECONDS: int = Field(default=120)
    REPORT_WORKER_LEASE_SECONDS: int = Field(default=60)
    REPORT_DISPATCH_TIMEOUT_SECONDS: int = Field(default=60)
    REPORT_RENDERER_SERVICE_URL: str | None = Field(
        default=None,
        description="Internal HTTP URL for isolated report renderer service, e.g. http://report-renderer:8080",
    )
    REPORT_RENDERER_SECRET: str = Field(
        default="dev_report_renderer_secret_key_change_in_production",
        description="HMAC-SHA256 shared secret for authenticating internal renderer requests",
    )
    REPORT_RENDERER_ALLOW_LEGACY_V1: bool = Field(
        default=False,
        description="Migration-only flag to allow legacy V1 signatures; strictly prohibited in production",
    )
    REPORT_RENDER_TIMEOUT_SECONDS: int = Field(
        default=45, description="Maximum rendering timeout in seconds"
    )
    REPORT_MAX_PDF_BYTES: int = Field(
        default=26214400, description="Maximum allowed PDF artifact size (25 MiB)"
    )
    REPORT_DOWNLOAD_TOKEN_TTL_SECONDS: int = Field(
        default=300, description="Single-use PDF download token expiration in seconds (5 min)"
    )

    # Phase 10: Automated Backup & Disaster Recovery Settings
    BACKUP_STORAGE_DIR: str = Field(default="./uploads/backups")
    WAL_SPOOL_DIR: str = Field(default="./uploads/wal_spool")
    BACKUP_RESTORE_SCRATCH_DIR: str = Field(default="./uploads/restore_scratch")
    BACKUP_TRUSTED_KEYS_DIR: str = Field(default="./trusted_keys")
    BACKUP_MASTER_KEY_ID: str = Field(default="vibe-kek-2026-v1")
    BACKUP_SIGNING_KEY_ID: str = Field(default="vibe-signer-2026-v1")
    BACKUP_SPOOL_MAX_BYTES: int = Field(default=53687091200)  # 50 GiB

    # Phase 4: Authentication & Security Configuration
    ENVIRONMENT: str = Field(
        default="development", description="Environment mode: development, test, or production"
    )
    COOKIE_NAME: str = Field(
        default="vibe_session",
        description="Cookie name: vibe_session in dev, __Host-vibe_session in prod",
    )
    COOKIE_SECURE: bool = Field(default=False, description="Enforce HTTPS Secure cookie attribute")
    COOKIE_SAMESITE: str = Field(default="lax", description="SameSite cookie policy: lax or strict")
    COOKIE_DOMAIN: str | None = Field(
        default=None, description="Cookie domain (must be None for __Host- cookies)"
    )

    # Cookie & Session aliases
    SESSION_COOKIE_NAME: str = Field(default="vibe_session")
    SESSION_COOKIE_SECURE: bool = Field(default=False)
    SESSION_COOKIE_SAMESITE: str = Field(default="lax")

    SESSION_IDLE_TIMEOUT_SECONDS: int = Field(
        default=1800, description="Idle session timeout (default 30 mins)"
    )
    SESSION_ABSOLUTE_LIFETIME_SECONDS: int = Field(
        default=43200, description="Absolute session lifetime (default 12 hours)"
    )
    SESSION_ABSOLUTE_TIMEOUT_SECONDS: int = Field(default=43200)
    SESSION_ACTIVITY_UPDATE_INTERVAL_SECONDS: int = Field(
        default=60, description="Throttle last_seen_at updates (default 60s)"
    )
    SESSION_TOUCH_INTERVAL_SECONDS: int = Field(default=60)

    # Argon2id Password Hashing Parameters
    ARGON2_MEMORY_COST: int = Field(
        default=65536, description="Argon2id memory cost in KiB (default 64 MiB)"
    )
    ARGON2_TIME_COST: int = Field(default=3, description="Argon2id iteration time cost")
    ARGON2_PARALLELISM: int = Field(default=1, description="Argon2id threads parallelism")
    ARGON2_SALT_LEN: int = Field(default=16, description="Argon2id salt length in bytes")
    ARGON2_HASH_LEN: int = Field(default=32, description="Argon2id hash output length in bytes")

    # Multi-Factor Authentication (TOTP AES-256-GCM)
    MFA_ENCRYPTION_KEY: str = Field(
        default="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        description="32-byte hex-encoded AES-256 key for encrypting TOTP secrets",
    )
    MFA_KEY_VERSION: int = Field(
        default=1, description="Current encryption key version for rotation"
    )
    MFA_AES_KEY_VERSION: int = Field(default=1)
    MFA_CHALLENGE_TIMEOUT_SECONDS: int = Field(
        default=300, description="MFA login challenge timeout (default 5 mins)"
    )
    MFA_CHALLENGE_LIFETIME_SECONDS: int = Field(default=300)

    # Email Delivery Settings
    EMAIL_VERIFICATION_REQUIRED: bool = Field(default=False)
    EMAIL_BACKEND: str = Field(default="memory", description="Email backend: memory or smtp")
    SMTP_HOST: str = Field(default="localhost")
    SMTP_PORT: int = Field(default=587)
    SMTP_USER: str = Field(default="")
    SMTP_PASSWORD: str = Field(default="")
    SMTP_TLS: bool = Field(default=True)
    EMAILS_FROM_EMAIL: str = Field(default="no-reply@vibe.enterprise")
    EMAILS_FROM_NAME: str = Field(default="VIBE Platform")

    # Action Tokens Lifetime
    EMAIL_VERIFICATION_TOKEN_EXPIRE_SECONDS: int = Field(
        default=86400, description="Email verification token lifetime (24 hours)"
    )
    EMAIL_VERIFICATION_TOKEN_LIFETIME_SECONDS: int = Field(default=86400)
    PASSWORD_RESET_TOKEN_EXPIRE_SECONDS: int = Field(
        default=1800, description="Password reset token lifetime (30 mins)"
    )
    PASSWORD_RESET_TOKEN_LIFETIME_SECONDS: int = Field(default=1800)

    # CORS & Security
    FRONTEND_URL: str = Field(
        default="http://localhost:3000", description="Frontend base URL for invitation links"
    )
    ALLOWED_ORIGINS: list[str] = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000"],
        description="Explicit allowed CORS origins",
    )

    # Redis Rate Limiting Parameters (per-minute limits)
    RATE_LIMIT_LOGIN_PER_MINUTE: int = Field(
        default=10, description="Max login attempts per minute"
    )
    RATE_LIMIT_LOGIN_MAX_REQUESTS: int = Field(default=10)
    RATE_LIMIT_LOGIN_WINDOW_SECONDS: int = Field(default=60)

    RATE_LIMIT_REGISTER_PER_MINUTE: int = Field(
        default=5, description="Max registrations per minute"
    )
    RATE_LIMIT_REGISTER_MAX_REQUESTS: int = Field(default=5)
    RATE_LIMIT_REGISTER_WINDOW_SECONDS: int = Field(default=60)

    RATE_LIMIT_PASSWORD_RESET_PER_MINUTE: int = Field(
        default=5, description="Max forgot-password requests per minute"
    )
    RATE_LIMIT_PASSWORD_RESET_MAX_REQUESTS: int = Field(default=5)
    RATE_LIMIT_PASSWORD_RESET_WINDOW_SECONDS: int = Field(default=60)

    RATE_LIMIT_VERIFY_EMAIL_PER_MINUTE: int = Field(
        default=10, description="Max email verification requests per minute"
    )
    RATE_LIMIT_EMAIL_VERIFY_MAX_REQUESTS: int = Field(default=10)
    RATE_LIMIT_EMAIL_VERIFY_WINDOW_SECONDS: int = Field(default=60)

    RATE_LIMIT_MFA_PER_MINUTE: int = Field(
        default=10, description="Max MFA verification attempts per minute"
    )
    RATE_LIMIT_MFA_MAX_REQUESTS: int = Field(default=10)
    RATE_LIMIT_MFA_WINDOW_SECONDS: int = Field(default=60)
    RATE_LIMIT_FAIL_CLOSED: bool = Field(
        default=True, description="Fail closed on security operations if Redis unavailable"
    )

    # Phase 8: External Integrations & Governed Execution
    GOOGLE_CLIENT_ID: str = Field(default="", description="Google Cloud OAuth Client ID")
    GOOGLE_CLIENT_SECRET: str = Field(default="", description="Google Cloud OAuth Client Secret")
    GOOGLE_REDIRECT_URI: str = Field(
        default="http://localhost:3000/integrations/callback",
        description="Google OAuth authorized redirect URI",
    )
    PYTHON_SANDBOX_TIMEOUT_SECONDS: int = Field(
        default=15, description="Governed Python execution timeout"
    )
    PYTHON_SANDBOX_MAX_MEMORY_MB: int = Field(
        default=256, description="Memory limit for isolated execution"
    )
    PYTHON_SANDBOX_MAX_CELLS: int = Field(
        default=500000, description="Max cells processed in script run"
    )
    PYTHON_SANDBOX_ENABLED: bool = Field(
        default=False, description="Enable user-defined Python script execution"
    )
    PYTHON_SANDBOX_ISOLATION_TYPE: str = Field(
        default="docker_oci", description="Isolation runtime: docker_oci or disabled"
    )
    PYTHON_SANDBOX_IMAGE_REPO: str = Field(
        default="vibe-python-sandbox", description="Repository name of sandbox image"
    )
    PYTHON_SANDBOX_IMAGE: str = Field(
        default="vibe-python-sandbox:latest",
        description="Pinned OCI container image for sandbox execution",
    )
    PYTHON_SANDBOX_IMAGE_DIGEST: str = Field(
        default="sha256:e4ba810cbecd430e97de30e389c9d88099a73a8f0efe13935a566a8ac8db2ab4",
        description="Immutable digest of pinned sandbox image for cryptographic attestation",
    )
    GOOGLE_OAUTH_USE_DRIVE_FILE: bool = Field(
        default=False,
        description="Use non-restricted drive.file scope with Google Picker instead of drive.readonly",
    )

    @field_validator("ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_allowed_origins(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            import json

            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed]
            except Exception:
                pass
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @model_validator(mode="after")
    def validate_keys_and_origins(self) -> "Settings":
        if self.REPORT_RENDERER_ALLOW_LEGACY_V1:
            raise ValueError(
                "REPORT_RENDERER_ALLOW_LEGACY_V1 is strictly prohibited in production and all environments; legacy V1 signatures are completely removed."
            )

        core_keys = {
            "SESSION_SECRET_KEY": self.SESSION_SECRET_KEY,
            "ENCRYPTION_MASTER_KEY": self.ENCRYPTION_MASTER_KEY,
            "RATE_LIMIT_HMAC_KEY": self.RATE_LIMIT_HMAC_KEY,
        }
        for name, key in core_keys.items():
            if not key or len(key) < 32:
                raise ValueError(f"Cryptographic key {name} must be at least 32 characters long.")

        if self.REPORT_RENDERER_SECRET and len(self.REPORT_RENDERER_SECRET) < 32:
            raise ValueError(
                "Cryptographic key REPORT_RENDERER_SECRET must be at least 32 characters long."
            )

        all_keys = list(core_keys.values())
        if (
            self.REPORT_RENDERER_SECRET
            and self.REPORT_RENDERER_SECRET != "dev_report_renderer_secret_key_change_in_production"
        ):
            all_keys.append(self.REPORT_RENDERER_SECRET)

        if len(all_keys) != len(set(all_keys)):
            raise ValueError(
                "Cryptographic key separation violation: SESSION_SECRET_KEY, "
                "ENCRYPTION_MASTER_KEY, RATE_LIMIT_HMAC_KEY, and REPORT_RENDERER_SECRET must be pairwise distinct."
            )

        if self.ENVIRONMENT.lower() == "production":
            for name, key in core_keys.items():
                if key.startswith("dev_") or "change_in_prod" in key or "0123456789" in key:
                    raise ValueError(
                        f"Insecure default key detected for {name} in production environment."
                    )
            infra_secrets = {
                "POSTGRES_PASSWORD": self.POSTGRES_PASSWORD,
                "REDIS_PASSWORD": self.REDIS_PASSWORD,
            }
            for name, secret in infra_secrets.items():
                if (
                    name in self.model_fields_set
                    and secret
                    and (secret.startswith("dev_") or "change_in_prod" in secret)
                ):
                    raise ValueError(
                        f"Insecure default password detected for {name} in production environment."
                    )
            if "SESSION_COOKIE_SECURE" in self.model_fields_set and not self.SESSION_COOKIE_SECURE:
                raise ValueError("SESSION_COOKIE_SECURE must be True in production environment.")
            if self.REPORT_RENDERER_SERVICE_URL:
                url = self.REPORT_RENDERER_SERVICE_URL.lower().strip()
                if not (url.startswith("http://") or url.startswith("https://")):
                    raise ValueError(
                        "REPORT_RENDERER_SERVICE_URL must use http:// or https:// scheme in production."
                    )
                sec = self.REPORT_RENDERER_SECRET
                if (
                    not sec
                    or sec.startswith("dev_")
                    or "change_in_prod" in sec
                    or "0123456789" in sec
                ):
                    raise ValueError(
                        "Insecure default key detected for REPORT_RENDERER_SECRET in production environment."
                    )
                if not self.REDIS_URL or not (
                    self.REDIS_URL.startswith("redis://") or self.REDIS_URL.startswith("rediss://")
                ):
                    raise ValueError(
                        "Valid REDIS_URL must be configured in production for distributed replay protection."
                    )
            if "*" in self.ALLOWED_ORIGINS:
                raise ValueError(
                    "Wildcard origin '*' cannot be used with credential-aware CORS in production."
                )

        return self

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
