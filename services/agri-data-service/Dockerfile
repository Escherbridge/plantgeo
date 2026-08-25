FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS base

COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Quality checks run ad-hoc via scripts/check.py; pytest needs a live disposable PostgreSQL environment.

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

RUN groupadd --system --gid 10001 plantgeo \
    && useradd --system --uid 10001 --gid plantgeo --home-dir /nonexistent \
      --shell /usr/sbin/nologin plantgeo

# DuckDB's httpfs and spatial are NOT bundled in the Python wheel: `INSTALL` downloads them into
# `$HOME/.duckdb`, and this user's home is /nonexistent. Verified in a container 2026-08-25 -- every
# serving read failed with `IOException: Can't find the home directory at '/nonexistent'`. So they
# are installed HERE, into the directory `duckdb_session.SERVING_EXTENSION_DIRECTORY` names, and the
# session only ever LOADs. `DUCKDB_EXTENSION_DIRECTORY` as an env var is not honoured by DuckDB
# 1.5.4 (measured: it still resolved $HOME); the directory has to arrive as a SETTING.
RUN python -c "from agri_data_service.interface.http.duckdb_session import install_serving_extensions; install_serving_extensions()" \
    && chown -R plantgeo:plantgeo /opt/duckdb-extensions

USER plantgeo

# Fails the BUILD, not the first request, if the pre-install did not land where the session looks.
RUN python -c "from agri_data_service.interface.http.duckdb_session import open_guarded_connection; open_guarded_connection().close()"

EXPOSE 8000

CMD ["/bin/sh", "-c", "exec sanic agri_data_service.app:create_app --factory --host 0.0.0.0 --port \"${PORT:-8000}\""]
