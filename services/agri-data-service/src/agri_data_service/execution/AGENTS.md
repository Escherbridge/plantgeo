# Execution runtime

`job_executor_service.py` is the single scheduler for PlantGeo data work. Railway runs it as the
continuous `plantgeo-job-executor` service with `agri-service ops jobs-executor`; Railway cron
schedules and per-source scheduler services are prohibited.

## Lane activation

`PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` is the only deployment activation control. An empty value keeps
all lanes in shadow mode. Every selected identifier must be present in `LANE_SPECS`, executable, and
free of a declared active-lane conflict. Removed services do not participate in runtime validation
and must not be represented by service IDs, owner constants, or acknowledgement variables.

Lane cadence, phase offset, command, timeout, catch-up policy, and publication contract live in
`LANE_SPECS`. Keep each current source-direct lane as a separate failure domain. New recurring work
must be registered there instead of adding a Railway cron.

## Durable execution

The executor uses the `agri.job_*` tables for definitions, logical runs, work items, attempts,
checkpoints, events, incidents, and outbox records. PostgreSQL advisory locking elects one scheduler
leader. Logical cadence buckets remain stable across restarts; incremental lanes coalesce downtime
to the current bucket and backlog lanes replay their oldest owed bucket.

A failed or partial bucket remains held according to its catch-up policy. Operators release a held
run with `agri-service ops jobs-supersede-run`; the resulting incident is the durable audit record.
The `blockers` field in tick output carries activation, executability, and operator-supersession
requirements.

## Command lifecycle

Commands run in their own process group with bounded timeouts, heartbeat updates, graceful
termination, and forced cleanup as the final fallback. Shutdown stops new launches and waits for
the active command boundary before releasing leadership. Never restore a deleted scheduler service
as rollback; remove a lane from the active allow-list or pause its durable definition instead.

## Quality receipt

Changes in this directory affect the Python quality fingerprint. Regenerate
`services/agri-data-service/QUALITY_RECEIPT.json` only after the final code and test edits are settled.
