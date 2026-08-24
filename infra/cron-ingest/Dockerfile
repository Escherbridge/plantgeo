# Scheduled ingestion: Railway runs this container on the cronSchedule in railway.json.
# It runs every ingestion source to completion and exits non-zero if any source failed,
# so a failed FIRMS fetch is a failed cron run rather than a line in a server log.
#
# THE SCHEDULE IS CURRENTLY REMOVED, ON PURPOSE (2026-08-24, owner call). `cronSchedule` is gone
# from railway.json while the Parquet backlog drains, because the two jobs collide: measured against
# production, a `signal` lane-day costs ~8 s with the database to itself and ~25 MINUTES while a cron
# tick runs beside it -- both processes sitting on IO/DataFileRead, competing for the same disk. The
# same verbs now run continuously and SEQUENTIALLY from
# services/agri-data-service/continuous-warehouse-loop.sh, which is what removes the collision.
#
# TO SWITCH BACK, once the backlog is drained: restore `"cronSchedule": "0 * * * *"` to
# infra/cron-ingest/railway.json and stop the loop script. Nothing else here changed, and nothing
# needs undoing. Note the schedule reads hourly but has never behaved that way -- `ingest-all` alone
# is ~86 minutes, so each tick overruns the hour and Railway skips the next trigger; the observed
# cadence was every two hours (markers landed at 05, 07, 09, 11 and 13 UTC on 2026-08-24).
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
# All THREE verbs run unconditionally and the exit code is their AND -- deliberately not `&&`.
# `&&` would skip the later verbs whenever any single ingestion source failed, which is exactly
# backwards: the durable archive lanes and the dispatchable lanes have nothing to do with
# whether FIRMS answered, and starving them for an hour over an unrelated upstream outage
# is how a backfill silently stops advancing. So each verb gets its turn, each verdict is
# captured, and the run is green only if all were.
#
# `parquet-gap-fill` (added 2026-08-22) is the Parquet warehouse's ONLY writer, and it is both the
# incremental load and the historical backfill -- not two jobs. Each lane declares a history floor
# and a publication lag; the window is [floor, today - lag]; gap detection over that window returns
# missing days NEWEST-FIRST, so a newly published day is simply the newest gap. The driver walks
# lanes round-robin, one day per lane per round, because sequential order would let
# fire-detections' ~9,400-day window consume the whole tick before signal wrote anything.
#
# 900s, and the three budgets are sized to fit inside the hourly schedule with restartPolicyType
# NEVER as the concurrency guard. A partially drained backlog is the EXPECTED steady state, not a
# failure: at ~15,000 missing days across twelve streams this converges over many ticks, and the
# verb exits zero while a backlog remains. It exits non-zero only on real failure.
#
# It needs the six OBJECT_STORE_* variables, already set on this service (RUNBOOK section 0.26.2).
# A tick with object storage unconfigured fails closed, naming every variable still missing.
ENTRYPOINT ["/bin/sh", "-c", "agri-cli ingest-all; ingest_status=$?; agri-cli jobs-pulse --time-budget-seconds 600; pulse_status=$?; agri-cli parquet-gap-fill --time-budget-seconds 900; parquet_status=$?; [ $ingest_status -eq 0 ] && [ $pulse_status -eq 0 ] && [ $parquet_status -eq 0 ]"]
