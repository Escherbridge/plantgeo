FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS base

COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Quality checks run ad-hoc via scripts/check.py; pytest needs a live disposable PostgreSQL environment.

# --- quality-receipt gate: refuse a tree whose locked receipt was not refreshed by a green sweep. ---
#
# The 2026-08-07 owner ruling dropped the in-image `checks` STAGE (a Docker build has no disposable
# PostgreSQL, so pytest could never run here). This is the newer 2026-09-01 audit directive and a
# different thing: it does not RE-RUN the checks, it verifies that the sha256 over
# `src/**`, `tests/**`, `scripts/**`, `pyproject.toml` and `uv.lock` still equals the digest
# `scripts/check.py --write-receipt` recorded after its last all-green run. Editing source without
# re-running the sweep therefore fails the BUILD instead of shipping unjudged code.
#
# `tests/` and `scripts/` are digest inputs, so they are copied HERE and never into `runtime` --
# a deleting `RUN` would not shrink an already-written layer, so the only way to keep tests out of
# the production image is to keep them out of its stage. `runtime` copies the verified receipt back
# out, and that COPY is what forces this stage to build at all: BuildKit prunes an unreferenced
# stage, and a pruned gate is not a gate.
#
# The verifier is deliberately stdlib-only, so it runs on the base interpreter with no virtualenv.
FROM base AS quality-receipt

COPY pyproject.toml uv.lock QUALITY_RECEIPT.json ./
COPY src/ src/
COPY tests/ tests/
COPY scripts/ scripts/
RUN python scripts/verify_quality_receipt.py

# --- runtime stage: lean, --no-dev, unchanged from before the gates were added. ---
FROM base AS runtime

# Resolve only from the committed lock before adding frequently changing source.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY src/ src/
COPY alembic/ alembic/
COPY db/ db/
COPY alembic.ini ./
RUN uv sync --locked --no-dev

# The proof this image's source passed a green sweep, taken from the stage that verified it so the
# gate cannot be skipped. Kept in the image so a running container can name its own quality receipt.
COPY --from=quality-receipt /app/QUALITY_RECEIPT.json ./QUALITY_RECEIPT.json

RUN groupadd --system --gid 10001 plantgeo \
    && useradd --system --uid 10001 --gid plantgeo --home-dir /nonexistent \
      --shell /usr/sbin/nologin plantgeo

# DuckDB's httpfs and spatial are NOT bundled in the Python wheel: `INSTALL` downloads them into
# `$HOME/.duckdb`, and this user's home is /nonexistent. Verified in a container 2026-08-25 -- every
# serving read failed with `IOException: Can't find the home directory at '/nonexistent'`. So they
# are installed HERE, into the directory `duckdb_session.SERVING_EXTENSION_DIRECTORY` names, and the
# session only ever LOADs. `DUCKDB_EXTENSION_DIRECTORY` as an env var is not honoured by DuckDB
# 1.5.4 (measured: it still resolved $HOME); the directory has to arrive as a SETTING.
RUN python -c "from agri_data_service.parquet_ops.duckdb_session import install_serving_extensions; install_serving_extensions()" \
    && chown -R plantgeo:plantgeo /opt/duckdb-extensions

USER plantgeo

# Fails the BUILD, not the first request, if the pre-install did not land where the session looks.
RUN python -c "from agri_data_service.parquet_ops.duckdb_session import open_guarded_connection; open_guarded_connection().close()"

EXPOSE 8000

CMD ["/bin/sh", "-c", "exec sanic agri_data_service.app:create_app --factory --host 0.0.0.0 --port \"${PORT:-8000}\""]
