FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c AS base

COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# --- checks stage: dev deps available, runs the quality gates that used to live in CI. ---
FROM base AS checks

# Resolve only from the committed lock before adding frequently changing source.
# "dev" is a project extra (not a PEP 735 dependency-group), so it must be
# requested explicitly via --extra; --no-dev has no effect on it.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --extra dev --no-install-project

COPY ruff.toml mypy.ini ./
COPY src/ src/
COPY tests/ tests/
COPY alembic/ alembic/
COPY db/ db/
COPY alembic.ini ./
RUN uv sync --locked --extra dev

# NOTE: pytest is intentionally NOT run here. The suite needs a live
# PostgreSQL 16 (PostGIS/TimescaleDB/pgvector/pgcrypto) instance and disposable
# databases, which a plain image build cannot provide. Run it out-of-band
# (e.g. against the docker-compose PG service) — it has no automated runner now.
RUN uv run ruff format --check src tests \
    && uv run ruff check . \
    && uv run mypy src \
    && touch /checks-passed

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

# Forces the checks stage (and its RUN gates) to execute during a normal
# `docker build`, even though its output is otherwise unused here.
COPY --from=checks /checks-passed /checks-passed

RUN groupadd --system --gid 10001 plantgeo \
    && useradd --system --uid 10001 --gid plantgeo --home-dir /nonexistent \
      --shell /usr/sbin/nologin plantgeo

USER plantgeo
EXPOSE 8000

CMD ["/bin/sh", "-c", "exec sanic agri_data_service.app:create_app --factory --host 0.0.0.0 --port \"${PORT:-8000}\""]
