# Scheduled ingestion: Railway runs this container on the cronSchedule in railway.json.
# It runs every ingestion source to completion and exits non-zero if any source failed,
# so a failed FIRMS fetch is a failed cron run rather than a line in a server log.
#
# Build context is the REPOSITORY ROOT, not this directory: the image needs
# services/agri-data-service/{pyproject.toml,uv.lock,src/}. The Railway service's
# Root Directory must therefore be "/" with this file as its Dockerfile path.
# See infra/cron-ingest/railway.json and ingest/AGENTS.md for the deployment note.
FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c

COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Resolve only from the committed lock before adding frequently changing source.
COPY services/agri-data-service/pyproject.toml services/agri-data-service/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

COPY services/agri-data-service/src/ src/
COPY services/agri-data-service/alembic/ alembic/
COPY services/agri-data-service/db/ db/
COPY services/agri-data-service/alembic.ini ./
RUN uv sync --locked --no-dev

RUN groupadd --system --gid 10001 plantgeo \
    && useradd --system --uid 10001 --gid plantgeo --home-dir /nonexistent \
      --shell /usr/sbin/nologin plantgeo

USER plantgeo

# No CRON_SECRET and no HTTP hop: this container reaches Postgres directly and its exit
# code is the run's verdict. restartPolicyType NEVER plus the hourly schedule is now the
# concurrency guard that the deleted in-memory `ingestionInFlight` flag used to be.
ENTRYPOINT ["agri-cli", "ingest-all"]
