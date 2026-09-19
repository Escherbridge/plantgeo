"""Application configuration via pydantic-settings. Zero-Postgres by owner decision D5."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Final, Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BUCKET_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.\-]{1,61}[a-z0-9]$")
_DUCKDB_MEMORY_LIMIT_PATTERN = re.compile(r"^\d+(?:\.\d+)?(?:B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)$")

#: A daily run is one bounded pass, not a serving pool. More threads buy nothing and multiply the
#: per-thread buffers against the same 2 GB ceiling.
MAX_DUCKDB_THREAD_COUNT: Final = 8

#: Substrings that mark an environment variable as naming a database connection. `DATABASE_URL`
#: alone missed libpq's own names (`PGHOST`, `PGDATABASE`) and the DSN spellings the sibling's
#: tooling uses, each of which is enough to grow the Postgres dependency decision D5 removed.
DATABASE_VARIABLE_MARKERS: Final[tuple[str, ...]] = (
    "DATABASE_URL",
    "DATABASE_DSN",
    "POSTGRES_DSN",
    "PGHOST",
    "PGDATABASE",
)

#: Quoted verbatim in the refusal so an operator reads the decision, not just a rejection.
DATABASE_REFUSAL_MESSAGE = "plantgeo-ml-service is zero-Postgres by owner decision D5 (2026-09-18)"

KernelImplementation = Literal["python", "mojo"]


def names_a_database_variable(variable_name: str) -> bool:
    """Return whether one environment variable name carries any database-connection marker."""
    upper_name = variable_name.upper()
    return any(marker in upper_name for marker in DATABASE_VARIABLE_MARKERS)


class ObjectStoreCredentials(BaseModel):
    """Complete, validated coordinates for the S3-compatible Parquet warehouse bucket."""

    model_config = ConfigDict(frozen=True)

    endpoint_url: str
    region: str
    bucket: str
    access_key_id: SecretStr
    secret_access_key: SecretStr


class Settings(BaseSettings):
    """Service settings loaded from environment variables, with the same names as the sibling."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Object store. Populated from the bucket service through Railway reference variables. Absent
    # values are not an error until a read or write is attempted; `/ready` reports them as not-ready.
    object_store_endpoint_url: str | None = None
    object_store_region: str = "auto"
    object_store_bucket: str | None = None
    object_store_access_key_id: SecretStr | None = None
    object_store_secret_access_key: SecretStr | None = None

    # Optional root inside the bucket, OUTSIDE the frozen `layer=.../kind=...` layout, so one bucket
    # can hold an isolated sandbox beside the real warehouse.
    object_store_prefix: str = ""

    # Where this service's artifacts, receipts and exported labels live inside the bucket.
    ml_prefix: str = "ml"

    # Which bucket object lists the cells the weather-forecast lane is fetched for. A KEY rather
    # than the list itself: the inventory is warehouse data with its own lifecycle, and an
    # environment variable holding a few hundred coordinates is a deployment nobody can review.
    # Unset is not "run with none" -- the turn reports `forecast_cells_unconfigured` and says so.
    forecast_cells_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PLANTGEO_ML_FORECAST_CELLS_KEY", "FORECAST_CELLS_KEY"),
    )

    # Which numeric kernel implementation to dispatch to. `python` is the default and the rollback;
    # `mojo` refuses to start when the built extension is absent. See method/kernels/AGENTS.md.
    kernels: KernelImplementation = "python"

    # DuckDB reads Parquet through httpfs. The ceiling is the whole non-functional budget from the
    # spec (a daily run fits in 2 GB) and the spill directory is pinned to zero in code, never here:
    # with spilling on, an over-budget query eats the disk instead of raising in a second.
    duckdb_memory_limit: str = "2GB"
    duckdb_thread_count: int = 2
    # Where the image pre-installs httpfs and spatial. Both are LOADED, never INSTALLED, so a
    # missing extension says so on the first read instead of fetching from the network mid-run.
    duckdb_extension_directory: str = "/opt/duckdb-extensions"

    # A container binds every interface; Railway's proxy is the only thing in front of it.
    sanic_host: str = "0.0.0.0"
    sanic_port: int = 8000
    sanic_debug: bool = False
    cors_origins: str = "http://localhost:3001"

    @field_validator("object_store_endpoint_url")
    @classmethod
    def require_credential_free_object_store_endpoint(cls, value: str | None) -> str | None:
        """Refuse an endpoint that carries userinfo or is not HTTPS."""
        if value is None or not value.strip():
            return None
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("OBJECT_STORE_ENDPOINT_URL must be a credential-free HTTPS URL")
        return value.rstrip("/")

    @field_validator("object_store_bucket")
    @classmethod
    def require_valid_bucket_name(cls, value: str | None) -> str | None:
        """Refuse anything that is not a legal S3 bucket name."""
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        if not _BUCKET_NAME_PATTERN.match(normalized):
            raise ValueError("OBJECT_STORE_BUCKET must be a 3-63 character lowercase S3 bucket name")
        return normalized

    @field_validator("duckdb_memory_limit")
    @classmethod
    def require_duckdb_memory_limit(cls, value: str) -> str:
        """Refuse a memory ceiling DuckDB cannot parse; an unparsed SET leaves the session unbounded."""
        normalized = value.strip()
        if not _DUCKDB_MEMORY_LIMIT_PATTERN.match(normalized):
            raise ValueError("PLANTGEO_ML_DUCKDB_MEMORY_LIMIT must look like '2GB', '512MB' or '1GiB'")
        return normalized

    @field_validator("duckdb_thread_count")
    @classmethod
    def require_duckdb_thread_count(cls, value: int) -> int:
        """Refuse a thread count outside the bounded serving budget."""
        if value < 1 or value > MAX_DUCKDB_THREAD_COUNT:
            raise ValueError(f"PLANTGEO_ML_DUCKDB_THREAD_COUNT must be between 1 and {MAX_DUCKDB_THREAD_COUNT}")
        return value

    @field_validator("object_store_region")
    @classmethod
    def require_object_store_region(cls, value: str) -> str:
        """Refuse a blank signing region; `auto` is the bucket's, not an absence."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("OBJECT_STORE_REGION cannot be blank; it must match the bucket's signing region")
        return normalized

    @field_validator("object_store_prefix", "ml_prefix")
    @classmethod
    def normalize_prefix(cls, value: str) -> str:
        """Return one bucket prefix as a trailing-slash segment, or empty when unset."""
        normalized = value.strip().strip("/")
        if not normalized:
            return ""
        if "\\" in normalized or ".." in normalized:
            raise ValueError("a bucket prefix must not contain backslashes or parent traversal")
        return f"{normalized}/"

    @model_validator(mode="after")
    def refuse_any_database_variable(self) -> Settings:
        """Refuse to boot while any DATABASE_URL-shaped variable is present in the environment.

        Fail-closed rather than ignore-silently: a deployment that inherits the sibling's variables
        would otherwise look configured and quietly grow a Postgres dependency the decision removed.
        """
        offending = sorted(name for name in os.environ if names_a_database_variable(name))
        if offending:
            raise ValueError(f"{DATABASE_REFUSAL_MESSAGE}; unset {', '.join(offending)}")
        return self

    def require_object_store(self) -> ObjectStoreCredentials:
        """Return complete bucket coordinates, naming every variable still missing."""
        endpoint_url = self.object_store_endpoint_url
        bucket = self.object_store_bucket
        access_key_id = self.object_store_access_key_id
        secret_access_key = self.object_store_secret_access_key
        missing = [
            name
            for name, value in (
                ("OBJECT_STORE_ENDPOINT_URL", endpoint_url),
                ("OBJECT_STORE_BUCKET", bucket),
                ("OBJECT_STORE_ACCESS_KEY_ID", access_key_id),
                ("OBJECT_STORE_SECRET_ACCESS_KEY", secret_access_key),
            )
            if value is None
        ]
        if endpoint_url is None or bucket is None or access_key_id is None or secret_access_key is None:
            raise ValueError(f"object storage is not configured; set {', '.join(missing)}")
        return ObjectStoreCredentials(
            endpoint_url=endpoint_url,
            region=self.object_store_region,
            bucket=bucket,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, constructed once."""
    return Settings()
