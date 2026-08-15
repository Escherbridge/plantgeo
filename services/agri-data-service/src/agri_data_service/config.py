"""Application configuration via Pydantic settings."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_MAX_PUBLISH_OUTPUTS = 1_000
_MIN_TOKEN_LENGTH = 32
_MIN_TOKEN_DIVERSITY = 10


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    service_profile: Literal["combined_local", "receiver_writer", "published_reader"] = "combined_local"
    # Local compatibility only; production HTTP profiles require one of the explicit DSNs below.
    database_url: str | None = None
    receiver_writer_database_url: str | None = None
    published_reader_database_url: str | None = None
    # Optional overrides that let one command target a database other than DATABASE_URL. Each
    # falls back to DATABASE_URL when unset and may name the same DSN; see the resolver below.
    local_source_loader_database_url: str | None = None
    forecast_mv_refresh_database_url: str | None = None
    forecast_iteration_database_url: str | None = None

    @field_validator(
        "database_url",
        "receiver_writer_database_url",
        "published_reader_database_url",
        "local_source_loader_database_url",
        "forecast_mv_refresh_database_url",
        "forecast_iteration_database_url",
    )
    @classmethod
    def fix_database_url_schema(cls, value: str | None) -> str | None:
        if value and value.startswith("postgres://"):
            return value.replace("postgres://", "postgresql+asyncpg://", 1)
        if value and value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    def require_receiver_writer_database_url(self) -> str:
        """Return the explicit production receiver/writer DSN without a shared fallback."""
        return self._require_profile_database_url(
            self.receiver_writer_database_url,
            "RECEIVER_WRITER_DATABASE_URL",
            "receiver_writer",
        )

    def require_combined_local_database_url(self) -> str:
        """Return the local compatibility DSN only inside the combined profile."""
        if self.service_profile != "combined_local":
            raise ValueError("DATABASE_URL is available only for the combined_local service profile")
        if not self.database_url:
            raise ValueError("combined_local service profile requires DATABASE_URL")
        return self.database_url

    def require_published_reader_database_url(self) -> str:
        """Return the explicit published-only reader DSN without a shared fallback."""
        return self._require_profile_database_url(
            self.published_reader_database_url,
            "PUBLISHED_READER_DATABASE_URL",
            "published_reader",
        )

    def _require_profile_database_url(
        self,
        value: str | None,
        field_name: str,
        required_profile: Literal["receiver_writer", "published_reader"],
    ) -> str:
        if self.service_profile != required_profile:
            raise ValueError(f"{field_name} is available only for the {required_profile} service profile")
        if not value:
            raise ValueError(f"{required_profile} service profile requires {field_name}")
        return self._require_complete_database_url(value, field_name)

    @staticmethod
    def _require_complete_database_url(value: str, field_name: str) -> str:
        """Reject anything that is not a complete `postgresql+asyncpg` DSN. The only DSN parser."""
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"{field_name} has an invalid port") from exc
        if (
            parsed.scheme != "postgresql+asyncpg"
            or not parsed.hostname
            or port is None
            or parsed.path in {"", "/"}
            or not parsed.username
        ):
            raise ValueError(f"{field_name} must be a complete postgresql+asyncpg database URL")
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

    # Copernicus CDS credentials for the ERA5-Land & AgERA5 lanes. Declared here so `.env` works; a real
    # CDSAPI_URL/CDSAPI_KEY in the process environment still wins. See execution/AGENTS.md.
    cdsapi_url: str | None = None
    cdsapi_key: SecretStr | None = None

    # Copernicus EWDS credentials for the CEMS fire danger indices lane.
    ewds_url: str | None = None
    ewds_api_key: SecretStr | None = None

    def _require_command_database_url(self, override: str | None, field_name: str) -> str:
        """Return `override` when set, else DATABASE_URL; blank/whitespace is unset. See db/AGENTS.md."""
        resolved = (override or "").strip()
        source = field_name
        if not resolved:
            resolved = (self.database_url or "").strip()
            source = "DATABASE_URL"
        if not resolved:
            raise ValueError(f"set {field_name} or DATABASE_URL")
        return self._require_complete_database_url(resolved, source)

    def require_local_source_loader_database_url(self) -> str:
        """Return the DSN every ingest/loader command connects with."""
        return self._require_command_database_url(
            self.local_source_loader_database_url,
            "LOCAL_SOURCE_LOADER_DATABASE_URL",
        )

    def require_forecast_mv_refresh_database_url(self) -> str:
        """Return the DSN the forecast materialized-view refresh connects with."""
        return self._require_command_database_url(
            self.forecast_mv_refresh_database_url,
            "FORECAST_MV_REFRESH_DATABASE_URL",
        )

    def require_forecast_iteration_database_url(self) -> str:
        """Return the DSN the evaluation-only forecast iteration writes connect with."""
        return self._require_command_database_url(
            self.forecast_iteration_database_url,
            "FORECAST_ITERATION_DATABASE_URL",
        )

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

    # The location-analysis agent is opt-in: absent this key /agent/analyze answers 503 and
    # every other route is unaffected. See agent/AGENTS.md for the deploy note.
    anthropic_api_key: SecretStr | None = None

    # Typed historical promotion is a distinct, private receiver from phase-one publication.
    historical_promotion_api_url: str | None = None
    historical_promotion_token: SecretStr | None = None
    historical_promotion_receiver_enabled: bool = False
    historical_promotion_actor: str | None = Field(default=None, max_length=255)
    historical_promotion_max_manifest_bytes: int = 8_000_000
    historical_promotion_max_chunk_bytes: int = 8_000_000
    historical_promotion_max_artifact_bytes: int = 8_000_000
    historical_promotion_request_overhead_bytes: int = 64_000
    historical_promotion_retry_attempts: int = 5
    historical_promotion_retry_base_seconds: float = 0.5

    def require_historical_promotion_client(self) -> tuple[str, str]:
        """Return the explicit secure receiver coordinates for a local promotion client."""
        if self.historical_promotion_api_url is None or self.historical_promotion_token is None:
            raise ValueError(
                "historical promotion requires HISTORICAL_PROMOTION_API_URL and HISTORICAL_PROMOTION_TOKEN"
            )
        parsed = urlsplit(self.historical_promotion_api_url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("HISTORICAL_PROMOTION_API_URL must be a credential-free HTTPS URL")
        return self.historical_promotion_api_url.rstrip("/"), self.historical_promotion_token.get_secret_value()

    @field_validator("local_publish_actor", "historical_promotion_actor", mode="before")
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
            self.historical_promotion_max_manifest_bytes,
            self.historical_promotion_max_chunk_bytes + self.historical_promotion_request_overhead_bytes,
            self.historical_promotion_max_artifact_bytes + self.historical_promotion_request_overhead_bytes,
            64_000,
        )

    @model_validator(mode="after")
    def enforce_local_phase_one(self) -> "Settings":  # noqa: PLR0912
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
            "HISTORICAL_PROMOTION_MAX_MANIFEST_BYTES": self.historical_promotion_max_manifest_bytes,
            "HISTORICAL_PROMOTION_MAX_CHUNK_BYTES": self.historical_promotion_max_chunk_bytes,
            "HISTORICAL_PROMOTION_MAX_ARTIFACT_BYTES": self.historical_promotion_max_artifact_bytes,
            "HISTORICAL_PROMOTION_REQUEST_OVERHEAD_BYTES": self.historical_promotion_request_overhead_bytes,
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
        if self.historical_promotion_retry_attempts <= 0:
            raise ValueError("HISTORICAL_PROMOTION_RETRY_ATTEMPTS must be positive")
        if self.historical_promotion_retry_base_seconds < 0:
            raise ValueError("HISTORICAL_PROMOTION_RETRY_BASE_SECONDS cannot be negative")
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
        if self.historical_promotion_token is not None:
            token = self.historical_promotion_token.get_secret_value()
            if (
                len(token) < _MIN_TOKEN_LENGTH
                or token != token.strip()
                or any(character.isspace() for character in token)
                or len(set(token)) < _MIN_TOKEN_DIVERSITY
            ):
                raise ValueError(
                    "HISTORICAL_PROMOTION_TOKEN must contain at least 32 diverse non-whitespace characters"
                )
        if self.historical_promotion_actor is not None and self.historical_promotion_token is None:
            raise ValueError("HISTORICAL_PROMOTION_ACTOR requires HISTORICAL_PROMOTION_TOKEN")
        if self.historical_promotion_receiver_enabled and (
            self.historical_promotion_token is None or self.historical_promotion_actor is None
        ):
            raise ValueError(
                "enabled historical promotion receiver requires HISTORICAL_PROMOTION_TOKEN "
                "and HISTORICAL_PROMOTION_ACTOR"
            )
        if self.service_profile == "receiver_writer" and self.published_reader_database_url is not None:
            raise ValueError("receiver_writer profile must not receive PUBLISHED_READER_DATABASE_URL")
        if self.service_profile == "published_reader" and self.receiver_writer_database_url is not None:
            raise ValueError("published_reader profile must not receive RECEIVER_WRITER_DATABASE_URL")
        if self.service_profile != "combined_local" and self.database_url is not None:
            raise ValueError("production service profiles must not receive DATABASE_URL")
        return self


settings = Settings()
