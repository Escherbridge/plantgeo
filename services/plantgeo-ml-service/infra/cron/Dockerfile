# The `plantgeo-ml-cron` image: the same tree as the service, run as ONE bounded turn, not a server.
#
# Build context is the service root (`services/plantgeo-ml-service`), the same as `Dockerfile`, so
# the two images are the same source at the same commit and cannot answer differently about what a
# lane holds. Everything here that looks duplicated is duplicated ON PURPOSE: a shared base image
# would have to be built and pushed somewhere first, and Railway builds each service from the repo.
#
# Railway settings for the service that runs this image live in `railway.cron.json`, which documents
# them; Railway rejects a config-as-code path as deprecated, so the dashboard is the source of truth.

FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS base

COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# --- quality-receipt gate: refuse a tree whose locked receipt was not refreshed by a green sweep. ---
#
# Identical in intent to the service image's gate: the digest over `src/**`, `tests/**`, `scripts/**`
# and the four config files must still equal what `scripts/check.py --write-receipt` recorded after
# its last all-green run. `runtime` copies the verified receipt back out, and that COPY is what
# forces this stage to build at all -- BuildKit prunes an unreferenced stage, and a pruned gate is
# not a gate.
FROM base AS quality-receipt

COPY pyproject.toml uv.lock QUALITY_RECEIPT.json ./
COPY mypy.ini ruff.toml ./
COPY src/ src/
COPY tests/ tests/
COPY scripts/ scripts/
RUN python scripts/verify_quality_receipt.py

# --- runtime stage: lean, --no-dev, no port, no server. ---
FROM base AS runtime

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src/ src/
RUN uv sync --locked --no-dev

# Both DuckDB extensions are LOADED and never INSTALLED on a run path, so the image installs them
# here. Without this every read refuses with `DuckDbExtensionError` -- the correct failure, and one
# that would make every turn a refusal (see `pipeline/duckdb_session.py`).
COPY --from=quality-receipt /app/QUALITY_RECEIPT.json ./QUALITY_RECEIPT.json

RUN groupadd --system --gid 10001 plantgeo \
    && useradd --system --uid 10001 --gid plantgeo --home-dir /nonexistent \
      --shell /usr/sbin/nologin plantgeo

ENV PLANTGEO_ML_DUCKDB_EXTENSION_DIRECTORY=/opt/duckdb-extensions
RUN python -c "from plantgeo_ml_service.pipeline.duckdb_session import install_extensions; install_extensions(directory='/opt/duckdb-extensions')" \
    && chown -R plantgeo:plantgeo /opt/duckdb-extensions \
    && chmod -R a+rX /opt/duckdb-extensions

USER plantgeo

# Fails the BUILD, not the first turn, if the wheel did not land where the interpreter looks.
RUN python -c "import plantgeo_ml_service; print(plantgeo_ml_service.__version__)"

# Fails the BUILD, not the first turn, if the extensions did not land where the SESSION looks.
RUN python -c "from plantgeo_ml_service.pipeline.duckdb_session import open_guarded_connection; open_guarded_connection().close()"

# One bounded turn. Exits 0 even when every lane refused (owner rule 2026-09-04); non-zero only when
# the bucket itself could not be reached, which is the only failure a schedule should page on.
CMD ["plantgeo-ml", "predict-daily"]
