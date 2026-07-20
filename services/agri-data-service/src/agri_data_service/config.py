"""Application configuration via Pydantic settings."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MAX_PUBLISH_OUTPUTS = 1_000
_MIN_TOKEN_LENGTH = 32
_MIN_TOKEN_DIVERSITY = 10
_LOCAL_SOURCE_LOADER_HOST = "127.0.0.1"
_LOCAL_SOURCE_LOADER_PORT = 5442
_LOCAL_SOURCE_LOADER_DATABASE = "plantgeo"
_LOCAL_SOURCE_LOADER_ROLE = "plantgeo_loader"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://geo:plantgeo@localhost:5432/plantgeo"
    # Never default this to DATABASE_URL: source ingestion has a separate local-only custody target.
    local_source_loader_database_url: str | None = None

    @field_validator("database_url", "local_source_loader_database_url")
    @classmethod
    def fix_database_url_schema(cls, value: str | None) -> str | None:
        if value and value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        if value and value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    database_url_sync: str = "postgresql://geo:plantgeo@localhost:5432/plantgeo"

    @field_validator("database_url_sync")
    @classmethod
    def require_sync_migration_driver(cls, value: str) -> str:
        if value.startswith("postgres://"):
            value = value.replace("postgres://", "postgresql://", 1)
        if not value.startswith(("postgresql://", "postgresql+psycopg2://")):
            raise ValueError("DATABASE_URL_SYNC must use the synchronous PostgreSQL driver")
        return value

    db_pool_min: int = 2
    db_pool_max: int = 5

    # Sanic
    sanic_host: str = "0.0.0.0"
    sanic_port: int = 8000
    sanic_debug: bool = False

    # CORS
    cors_origins: str = "http://localhost:3001"

    # Phase-one ETL, forecast, and model execution is local-only.
    execution_backend: Literal["local"] = "local"
    celery_dispatch_enabled: bool = False
    cloud_training_enabled: bool = False
    local_execution_root: Path = Path(".agri-local-runs")

    def require_local_source_loader_database_url(self) -> str:
        """Return the explicit local compose DSN allowed for source-ingest only."""
        value = self.local_source_loader_database_url
        if not value:
            raise ValueError(
                "source-ingest requires LOCAL_SOURCE_LOADER_DATABASE_URL; DATABASE_URL is never a loader fallback"
            )
        if value == self.database_url:
            raise ValueError("LOCAL_SOURCE_LOADER_DATABASE_URL must not reuse DATABASE_URL")
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("LOCAL_SOURCE_LOADER_DATABASE_URL has an invalid port") from exc
        if (
            parsed.scheme != "postgresql+asyncpg"
            or parsed.hostname != _LOCAL_SOURCE_LOADER_HOST
            or port != _LOCAL_SOURCE_LOADER_PORT
            or parsed.path != f"/{_LOCAL_SOURCE_LOADER_DATABASE}"
        ):
            raise ValueError(
                "LOCAL_SOURCE_LOADER_DATABASE_URL must target postgresql+asyncpg://127.0.0.1:5442/plantgeo"
            )
        if parsed.username == "plantgeo_owner":
            raise ValueError("LOCAL_SOURCE_LOADER_DATABASE_URL must not use the plantgeo_owner bootstrap role")
        if parsed.username != _LOCAL_SOURCE_LOADER_ROLE:
            raise ValueError("LOCAL_SOURCE_LOADER_DATABASE_URL must authenticate as plantgeo_loader")
        return value

    # Local clients need the URL/token; the receiver additionally needs its gate/actor.
    local_publish_api_url: str | None = None
    local_publish_token: SecretStr | None = None
    local_publication_receiver_enabled: bool = False
    local_publish_actor: str | None = Field(default=None, max_length=255)
    local_publish_max_artifact_bytes: int = 5_000_000
    local_publish_max_manifest_bytes: int = 512_000
    local_publish_max_validation_bytes: int = 256_000
    local_publish_max_outputs: int = 256
    local_publish_max_run_artifact_bytes: int = 100_000_000
    local_publish_max_run_validation_bytes: int = 10_000_000
    local_publish_request_overhead_bytes: int = 64_000
    local_publish_retry_attempts: int = 5
    local_publish_retry_base_seconds: float = 0.5

    @field_validator("local_publish_actor", mode="before")
    @classmethod
    def normalize_publish_actor(cls, value: object) -> object:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("LOCAL_PUBLISH_ACTOR cannot be blank")
        if any(not character.isprintable() for character in normalized):
            raise ValueError("LOCAL_PUBLISH_ACTOR cannot contain control characters")
        return normalized

    @property
    def local_publish_max_upload_request_bytes(self) -> int:
        artifact_encoded = 4 * ((self.local_publish_max_artifact_bytes + 2) // 3)
        validation_encoded = 4 * ((self.local_publish_max_validation_bytes + 2) // 3)
        return artifact_encoded + validation_encoded + self.local_publish_request_overhead_bytes

    @property
    def request_max_size(self) -> int:
        return max(
            self.local_publish_max_manifest_bytes,
            self.local_publish_max_upload_request_bytes,
            64_000,
        )

    @model_validator(mode="after")
    def enforce_local_phase_one(self) -> "Settings":
        if self.celery_dispatch_enabled or self.cloud_training_enabled:
            raise ValueError("Celery dispatch and cloud training are disabled for phase one")
        if self.db_pool_min <= 0 or self.db_pool_max < self.db_pool_min:
            raise ValueError("DB_POOL_MIN must be positive and no greater than DB_POOL_MAX")
        byte_limits = {
            "LOCAL_PUBLISH_MAX_ARTIFACT_BYTES": self.local_publish_max_artifact_bytes,
            "LOCAL_PUBLISH_MAX_MANIFEST_BYTES": self.local_publish_max_manifest_bytes,
            "LOCAL_PUBLISH_MAX_VALIDATION_BYTES": self.local_publish_max_validation_bytes,
            "LOCAL_PUBLISH_MAX_RUN_ARTIFACT_BYTES": self.local_publish_max_run_artifact_bytes,
            "LOCAL_PUBLISH_MAX_RUN_VALIDATION_BYTES": self.local_publish_max_run_validation_bytes,
            "LOCAL_PUBLISH_REQUEST_OVERHEAD_BYTES": self.local_publish_request_overhead_bytes,
        }
        invalid_limit = next((name for name, value in byte_limits.items() if value <= 0), None)
        if invalid_limit:
            raise ValueError(f"{invalid_limit} must be positive")
        if self.local_publish_retry_attempts <= 0:
            raise ValueError("LOCAL_PUBLISH_RETRY_ATTEMPTS must be positive")
        if self.local_publish_max_outputs <= 0 or self.local_publish_max_outputs > _MAX_PUBLISH_OUTPUTS:
            raise ValueError("LOCAL_PUBLISH_MAX_OUTPUTS must be between 1 and 1000")
        if self.local_publish_max_run_artifact_bytes < self.local_publish_max_artifact_bytes:
            raise ValueError("the aggregate artifact quota cannot be below the per-file quota")
        if self.local_publish_max_run_validation_bytes < self.local_publish_max_validation_bytes:
            raise ValueError("the aggregate validation quota cannot be below the per-file quota")
        if self.local_publish_retry_base_seconds < 0:
            raise ValueError("LOCAL_PUBLISH_RETRY_BASE_SECONDS cannot be negative")
        if self.local_publish_token is not None:
            token = self.local_publish_token.get_secret_value()
            if (
                len(token) < _MIN_TOKEN_LENGTH
                or token != token.strip()
                or any(character.isspace() for character in token)
                or len(set(token)) < _MIN_TOKEN_DIVERSITY
            ):
                raise ValueError("LOCAL_PUBLISH_TOKEN must contain at least 32 diverse non-whitespace characters")
        if self.local_publish_actor is not None and self.local_publish_token is None:
            raise ValueError("LOCAL_PUBLISH_ACTOR requires LOCAL_PUBLISH_TOKEN")
        if self.local_publication_receiver_enabled and (
            self.local_publish_token is None or self.local_publish_actor is None
        ):
            raise ValueError("enabled local publication receiver requires LOCAL_PUBLISH_TOKEN and LOCAL_PUBLISH_ACTOR")
        return self


settings = Settings()
