"""Service settings from the environment and an optional `services/strategy-knowledge/.env`."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import SecretStr

SERVICE_ROOT: Final = Path(__file__).resolve().parents[2]
ENV_FILE: Final = SERVICE_ROOT / ".env"
DEFAULT_CACHE_DIR: Final = SERVICE_ROOT / ".cache"
DEFAULT_PREFIX: Final = "strategy-knowledge/"
PRODUCTION_EMBEDDING_MODEL: Final = "all-MiniLM-L6-v2"
#: Documents each retriever (dense, BM25) contributes to fusion, whatever page is asked for (AGENTS.md "Retrieval").
DEFAULT_CANDIDATE_POOL: Final = 300
#: uvicorn `limit_concurrency` for HTTP mode (AGENTS.md "Observability"): excess concurrent requests fail fast
#: with a 503 instead of queuing behind a slow tool call.
DEFAULT_LIMIT_CONCURRENCY: Final = 16
MAXIMUM_LIMIT_CONCURRENCY: Final = 512

#: Same names as `services/agri-data-service/.env` (DESIGN.md section 12).
OBJECT_STORE_VARIABLES: Final = (
    "OBJECT_STORE_ENDPOINT_URL",
    "OBJECT_STORE_BUCKET",
    "OBJECT_STORE_REGION",
    "OBJECT_STORE_ACCESS_KEY_ID",
    "OBJECT_STORE_SECRET_ACCESS_KEY",
)


class ObjectStoreNotConfiguredError(RuntimeError):
    """Raised when a bucket operation runs with object-store variables unset; names them, never values."""


@dataclass(frozen=True, slots=True)
class ObjectStoreSettings:
    """Coordinates of the S3-compatible bucket; secrets stay `SecretStr` so a repr never leaks them."""

    endpoint_url: str
    bucket: str
    region: str
    access_key_id: SecretStr
    secret_access_key: SecretStr


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything the CLI and server read from the environment."""

    cache_dir: Path
    prefix: str
    embedding_model: str
    object_store_values: Mapping[str, str]
    candidate_pool: int = DEFAULT_CANDIDATE_POOL
    #: Host names `/mcp` accepts besides loopback and the Railway service host (AGENTS.md "HTTP transport").
    allowed_hosts: tuple[str, ...] = ()
    #: HTTP mode only; ignored over stdio (AGENTS.md "Observability").
    limit_concurrency: int = DEFAULT_LIMIT_CONCURRENCY

    def object_store(self) -> ObjectStoreSettings:
        """Return bucket coordinates, or raise naming every unset variable."""
        missing = [name for name in OBJECT_STORE_VARIABLES if not self.object_store_values.get(name)]
        if missing:
            raise ObjectStoreNotConfiguredError(f"object store not configured; unset: {', '.join(missing)}")
        values = self.object_store_values
        return ObjectStoreSettings(
            endpoint_url=values["OBJECT_STORE_ENDPOINT_URL"],
            bucket=values["OBJECT_STORE_BUCKET"],
            region=values["OBJECT_STORE_REGION"],
            access_key_id=SecretStr(values["OBJECT_STORE_ACCESS_KEY_ID"]),
            secret_access_key=SecretStr(values["OBJECT_STORE_SECRET_ACCESS_KEY"]),
        )

    def __repr__(self) -> str:
        """Show which object-store variables are set, never their values."""
        configured = sorted(name for name in OBJECT_STORE_VARIABLES if self.object_store_values.get(name))
        return (
            f"Settings(cache_dir={self.cache_dir!s}, prefix={self.prefix!r}, "
            f"embedding_model={self.embedding_model!r}, candidate_pool={self.candidate_pool}, "
            f"allowed_hosts={list(self.allowed_hosts)}, limit_concurrency={self.limit_concurrency}, "
            f"object_store_variables_set={configured})"
        )


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse `KEY=VALUE` lines; blank lines, comments and malformed lines are ignored."""
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.removeprefix("export ").split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load_settings(environment: Mapping[str, str] | None = None, env_file: Path = ENV_FILE) -> Settings:
    """Merge the `.env` file under the process environment (the environment wins) into `Settings`."""
    merged = {**parse_env_file(env_file), **(os.environ if environment is None else environment)}
    cache_dir = Path(merged.get("STRATEGY_KB_CACHE_DIR") or DEFAULT_CACHE_DIR).expanduser()
    prefix = merged.get("STRATEGY_KB_PREFIX") or DEFAULT_PREFIX
    pool = merged.get("STRATEGY_KB_CANDIDATE_POOL", "").strip()
    concurrency = merged.get("STRATEGY_KB_LIMIT_CONCURRENCY", "").strip()
    host_values = (merged.get("RAILWAY_PRIVATE_DOMAIN", ""), *merged.get("STRATEGY_KB_ALLOWED_HOSTS", "").split(","))
    return Settings(
        cache_dir=cache_dir,
        prefix=prefix if prefix.endswith("/") else f"{prefix}/",
        embedding_model=merged.get("STRATEGY_KB_EMBEDDING_MODEL") or PRODUCTION_EMBEDDING_MODEL,
        object_store_values={name: merged[name] for name in OBJECT_STORE_VARIABLES if merged.get(name)},
        candidate_pool=int(pool) if pool.isdigit() and int(pool) > 0 else DEFAULT_CANDIDATE_POOL,
        allowed_hosts=tuple(dict.fromkeys(host.strip() for host in host_values if host.strip())),
        limit_concurrency=(
            int(concurrency)
            if concurrency.isdigit() and 0 < int(concurrency) <= MAXIMUM_LIMIT_CONCURRENCY
            else DEFAULT_LIMIT_CONCURRENCY
        ),
    )
