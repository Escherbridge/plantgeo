# Scheduled ingestion: Railway runs this container on the cronSchedule in railway.json.
# It runs every ingestion source to completion and exits non-zero if any source failed,
# so a failed FIRMS fetch is a failed cron run rather than a line in a server log.
#
# ONE IMAGE, MANY SERVICES. Every infra/cron-*/railway.json points its dockerfilePath here
# and overrides the ENTRYPOINT below with its own `startCommand`, so the verb -- not the
# image -- is what distinguishes the services. That now includes the durable archive lanes
# (`jobs-run`) and the completeness report (`validate-streams`), whose exit rules differ
# from `ingest-all`'s deliberately: see docs/runbooks/durable-backfill-lanes.md.
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

# Source only. The migration machinery (alembic/, db/, alembic.ini) is deliberately NOT
# copied: this container must never run a migration, and omitting the files makes that true
# by construction rather than by trusting the ENTRYPOINT string. `agri-cli ingest-all` never
# reads them -- cli.py:19 imports the alembic *package* (a locked dependency), while
# alembic.ini is read only inside _alembic_config() (cli.py:266-267), which only the
# migration verbs call. alembic.ini is also untracked, so copying it broke the build.
#
# This one line already carries `agri_data_service/jobs/` and `agri_data_service/ingest/`,
# so the durable-lane services need no build change of their own -- only a startCommand.
COPY services/agri-data-service/src/ src/
RUN uv sync --locked --no-dev

RUN groupadd --system --gid 10001 plantgeo \
    && useradd --system --uid 10001 --gid plantgeo --home-dir /nonexistent \
      --shell /usr/sbin/nologin plantgeo

USER plantgeo

# No CRON_SECRET and no HTTP hop: this container reaches Postgres directly and its exit
# code is the run's verdict. restartPolicyType NEVER plus the hourly schedule is now the
# concurrency guard that the deleted in-memory `ingestionInFlight` flag used to be.
ENTRYPOINT ["agri-cli", "ingest-all"]
