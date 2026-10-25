from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_BACKEND_DIR / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Application
    APP_NAME: str = "AMU Student Resource Management System"
    APP_ENV: str = Field(default="development", description="development | staging | production")
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.lower() == "production"

    @property
    def is_development(self) -> bool:
        return self.APP_ENV.lower() in ("development", "dev", "local", "test", "testing")

    # PostgreSQL Database
    DATABASE_URL: str | None = None
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "amu_user"
    POSTGRES_PASSWORD: str = Field(default="", repr=False)
    POSTGRES_DB: str = "amu_resources_db"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    @property
    def ASYNC_DATABASE_URI(self) -> str:
        if self.DATABASE_URL:
            url = self.DATABASE_URL
            if url.startswith("postgresql://"):
                url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
            # Remote cloud hosts like Supabase require SSL
            if ("supabase" in url or "aws" in url) and "ssl=" not in url and "sslmode=" not in url:
                separator = "&" if "?" in url else "?"
                url = f"{url}{separator}ssl=require"
            return url
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def ASYNC_DATABASE_URI_SAFE(self) -> str:
        """Database URI with masked password for safe logging and debugging."""
        if self.DATABASE_URL:
            import re

            return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", self.DATABASE_URL)
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:***@"
            f"{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # S3 / MinIO Object Storage
    S3_ENDPOINT_URL: str = "http://localhost:9000"
    S3_ACCESS_KEY_ID: str = "minioadmin"
    S3_SECRET_ACCESS_KEY: str = Field(default="", repr=False)
    S3_BUCKET_NAME: str = "academic-resources"
    S3_REGION: str = "us-east-1"

    # JWT Authentication
    JWT_SECRET_KEY: str = Field(default="", repr=False)
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Storage Quota Policies (in bytes)
    STUDENT_DEFAULT_QUOTA_BYTES: int = 1_073_741_824  # 1 GiB
    TEACHER_DEFAULT_QUOTA_BYTES: int = 16_106_127_360  # 15 GiB
    UPLOAD_RESERVATION_TIMEOUT_MINUTES: int = 30

    # OpenTelemetry / PostHog Logging
    OTEL_ENABLED: bool = True
    OTEL_SERVICE_NAME: str = "my-app"
    POSTHOG_LOGS_ENDPOINT: str = "https://us.i.posthog.com/i/v1/logs"
    POSTHOG_API_KEY: str = Field(default="", repr=False)

    # Logging and Crash Monitoring
    LOG_DIR: str = "logs"
    LOG_LEVEL: str = "INFO"
    ENABLE_FILE_LOGGING: bool = True
    SERVER_LOG_FILE: str = "server.log"
    CRASH_LOG_FILE: str = "server_crash.log"
    MAX_LOG_BYTES: int = 10_485_760  # 10 MB per log file
    BACKUP_LOG_COUNT: int = 5

    # Email / SMTP Configuration
    EMAIL_HOST_USER: str = Field(default="", description="SMTP username/email")
    EMAIL_HOST_PASSWORD: str = Field(
        default="", repr=False, description="SMTP password/app password"
    )
    EMAIL_SERVER: str = "smtp.gmail.com"
    EMAIL_PORT: int = 587
    EMAIL_USE_TLS: bool = True
    MAIL_FROM: str = Field(default="", description="Sender email address")
    MAIL_FROM_NAME: str = "Kunal"
    FRONTEND_URL: str = "http://localhost:3000"
    EMAIL_VERIFICATION_TOKEN_EXPIRE_HOURS: int = 24
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 30

    # OTP Verification Configuration
    OTP_EXPIRE_MINUTES: int = 15
    OTP_MAX_ATTEMPTS: int = 5
    OTP_RESEND_COOLDOWN_SECONDS: int = 60
    # Supabase Storage Configuration
    SUPABASE_URL: str = Field(default="", description="Supabase project URL")
    SUPABASE_KEY: str = Field(default="", repr=False, description="Supabase anon or service key")
    SUPABASE_SERVICE_ROLE_KEY: str = Field(
        default="",
        repr=False,
        description="Supabase service role key for backend operations",
        validation_alias=AliasChoices("SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_SERVICE_ROLE"),
    )
    SUPABASE_BUCKET: str = "resources"
    SUPABASE_REGION: str = "ap-southeast-2"
    SUPABASE_WEBHOOK_SECRET: str = Field(
        default="",
        repr=False,
        description="Secret token to verify incoming Supabase webhook requests",
    )


settings = Settings()
