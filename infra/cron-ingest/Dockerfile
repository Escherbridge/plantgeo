# Scheduled ingestion: Railway runs this container on the cronSchedule in railway.json.
# It runs every ingestion source to completion and exits non-zero if any source failed,
# so a failed FIRMS fetch is a failed cron run rather than a line in a server log.
#
# ONE SERVICE, ONE PULSE (2026-08-14). This image used to back a fan-out of one Railway cron
# service per verb; eleven of those were deleted and folded into the single hourly ENTRYPOINT
# below, because `ingest-all` already runs the eight forward sources plus the geometry repair
# and `jobs-pulse` already visits every dispatchable lane and every durable archive definition.
# See docs/runbooks/durable-backfill-lanes.md and execution/jobs_pulse_command.py's docstring.
#
# THE DATA-QUALITY LOOP IS INSIDE `jobs-pulse`, not a fourth verb on this line. The consolidation
# also deleted infra/cron-maintain-* and infra/cron-validate, which left `jobs-reconcile-lane`,
# `jobs-plan-gaps` and `validate-streams` on no schedule -- so a detected gap never became a
# claimable work item. `jobs-pulse` now runs all three as its third pass, deriving the lane set
# from the ledger rather than from a hard-coded list a shell string here would have to carry.
# Do not add them to this ENTRYPOINT as well; they would run twice per tick.
#
# TWO services deliberately survived that consolidation, and neither is a job-runner lane:
#   - plantgeo-cron-mtbs still overrides this ENTRYPOINT with `agri-cli ingest-mtbs` on a weekly
#     schedule. MTBS is quarterly-published, so hourly is the wrong shape, and `ingest-mtbs`
#     fails a run outright when a fire year has no established release date -- chaining it here
#     would turn that rare catalog gap into a permanently red hourly cron and mask real ingest
#     failures behind it. It keeps its own cadence and its own exit-code verdict.
#   - plantgeo-cron-soilgrids builds from infra/cron-soilgrids/Dockerfile, not this one: it is a
#     Node driver walking a finite ~1568-cell grid at 120 cells/hour, not a recurring lane.
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
#
# Both verbs run unconditionally and the exit code is their AND -- deliberately not `&&`.
# `&&` would skip the pulse whenever any single ingestion source failed, which is exactly
# backwards: the durable archive lanes and the dispatchable lanes have nothing to do with
# whether FIRMS answered, and starving them for an hour over an unrelated upstream outage
# is how a backfill silently stops advancing. So each verb gets its turn, each verdict is
# captured, and the run is green only if both were.
ENTRYPOINT ["/bin/sh", "-c", "agri-cli ingest-all; ingest_status=$?; agri-cli jobs-pulse --time-budget-seconds 600; pulse_status=$?; [ $ingest_status -eq 0 ] && [ $pulse_status -eq 0 ]"]
