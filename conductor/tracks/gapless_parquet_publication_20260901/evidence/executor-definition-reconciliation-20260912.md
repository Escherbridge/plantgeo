---
type: track-evidence
track: gapless_parquet_publication_20260901
audited_on: 2026-09-12
status: local_reconciliation_complete_runtime_and_candidate_intake_open
source_commit: 843b4b313e03447594b23a67f75c3062b2b1a024
source_tree: 9533bb9e5423240630935df0cd012cd8ead15504
branch: codex/executor-definition-reconciliation-20260912
---

# Executor definition and custody reconciliation

There is one intended scheduler service, `plantgeo-job-executor`, with one
versioned durable definition per executable lane. There is no verified current
effective activation/configuration packet in the audited main tree. A separate
committed recovery candidate contains a later, bounded cutoff receipt and fixes
an executor cleanup gap that is still present in this base. Its original owner
must supply the reconciliation handoff before anyone treats it as integrated.

This audit used local source, Git objects, registered-worktree metadata and
retained receipts only. It did not access Railway, databases (including `pgt`),
object storage, writers, schedulers or deployment state. It copied or edited no
other task's uncommitted work and ran no executor command. This is a local
evidence deliverable, not an operational acceptance or deployment receipt.

## Authority and scope

The [registry](../../../tracks.md) assigns scheduler ownership, recovery and
scheduled advances to this gapless track. The
[environmental retirement plan](../../environmental_postgres_retirement_20260904/plan.md)
owns the environmental database cutoff and replacement paths. Production
acceptance consumes their complete packets. Gapless metadata's p5 partition is
the retained implementation ownership for executor/job-definition changes; p6
owns recovery/burn-in evidence. Dated wave assignments do not identify a current
task author or an effective lane allowlist.

The [task ledger](../../platform_experience_qa_20260911/evidence/task-ledger.md)
assigns the shared ledger, registry and runbook to QA orchestration task
`01a0904b-756b-7961-b991-cab666123be2`. This packet changes neither those files nor
track status. The requesting task is `01a09475-01c4-7252-8710-a8a57559c919`.

All source line references below refer to the frozen source commit above.
`execution/`, `jobs/`, `sql/` and `pipeline/` abbreviate
`services/agri-data-service/src/agri_data_service/`; `db/` and `tests/` abbreviate
`services/agri-data-service/`. The companion
[custody inventory](executor-definition-custody-20260912.json) retains full
worktree heads/trees, statuses, comparison scope and selected source blob IDs.

## One intended service; versioned definitions underneath it

The coordinated deployment contract is explicit in
[`infra/job-executor/AGENTS.md`](../../../../infra/job-executor/AGENTS.md:3):

| Field | Intended committed value |
| --- | --- |
| Service | `plantgeo-job-executor` |
| Repository root | `/` |
| Config-as-code | `/services/agri-data-service/railway.job-executor.json` |
| Dockerfile | `infra/job-executor/Dockerfile` |
| Start command | `agri-service ops jobs-executor` |
| Lifecycle | Continuous, `ON_FAILURE`, maximum 10 retries; no Railway cron |
| Runtime authority | Python service plus existing Node SoilGrids driver; no Alembic/migration runtime authority |

The [committed config](../../../../services/agri-data-service/railway.job-executor.json)
does not establish effective root/config selection, command overrides, instances
or environment. A different resolved root/config/Dockerfile is not this candidate.

Static declaration inspection finds **59 registry entries, 58 executable and one
snapshot-only responsibility**: three `_POSTGRES_SPECS`, nine `_JOBS_SPECS`, 32
generic Parquet registrations and 15 migration/input specifications
(`execution/job_executor_service.py:513`, `:534`, `:625`, `:629`, `:981`;
`pipeline/parquet/lane_registry.py:1230`). The sole nonexecutable entry is
`soil-moisture-parquet-backfill` (`:968`), whose completed immutable snapshot must
never become scheduled work. These counts describe declarations, not activation.

Each executable definition has name `plantgeo.executor.<lane_id>`, version `2`,
handler `plantgeo.executor.command.v1`, work-item kind `scheduled-command`, queue
`default`, timezone `UTC`, and concurrency key `plantgeo-executor:<lane_id>`.
The code specification sets five attempts, lease = command timeout + 120 seconds,
worker budget = command timeout + 30 seconds, and exponential retry backoff
starting at 30 seconds, multiplier 2, capped at 3,600 seconds
(`execution/job_executor_service.py:74`, `:195`; `jobs/registry.py:147`).

Generic Parquet argv is `agri-service data parquet-gap-fill --layer <slug>
--max-days-per-lane 1 --time-budget-seconds 900`. Its outer command timeout is
1,200 seconds, lease 1,320 seconds and worker budget 1,230 seconds (`:437`).
Other lanes have source-specific argv, cadence, phase and timeout overrides.
Effective comparison must retain the full argv and those overrides per lane.

## Effective configuration that remains missing

| Setting | Code contract | Required effective evidence |
| --- | --- | --- |
| `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` | Empty default; every lane starts shadow. | Exact resolved list plus timestamped process tick identifying each active, shadow, held or paused lane. |
| `PLANTGEO_JOB_EXECUTOR_HANDOFF_ACKNOWLEDGEMENTS` | Empty default; exact `lane=token` sets for selected lanes. Unknown/inactive acknowledgements or missing tokens refuse. | Exact nonsecret token set, reconciled with the active list and actual predecessor termination evidence. |
| `PLANTGEO_JOB_EXECUTOR_POLL_SECONDS` | Default 30; explicit nonpositive values refuse. | Resolved value and observed tick interval. |
| `PLANTGEO_JOB_EXECUTOR_MAX_LANES_PER_TICK` | Default/minimum two, with incremental/backlog fairness. | Resolved value and selected due lanes. |
| Ledger destination | `LOCAL_SOURCE_LOADER_DATABASE_URL`, falling back to `DATABASE_URL`. | Credential-free host/database/schema identity and which resolver branch was used; never infer the target from a supplied DSN's syntactic validity. |
| Worker identity | `RAILWAY_REPLICA_ID`, otherwise hostname/PID. | Every old/new instance and child identity tied to deployment and leader interval. |
| Output destination | `OBJECT_STORE_ENDPOINT_URL`, `OBJECT_STORE_BUCKET`, `OBJECT_STORE_PREFIX` (default empty), `OBJECT_STORE_REGION` (default `auto`). | Credential-free resolved destination and credential-presence result, plus per-lane source/config pins and publication root. |

Anchors: `.env.example:33`; `execution/job_executor_service.py:93`, `:1071`,
`:1518`, `:2073`, `:2124`; `config.py:182`, `:267`. No separate
`REQUIRED_LANES` setting was found in the committed executor. The prior receipts'
"active/required" wording must be resolved into the active list, each spec's
`required_handoff_acknowledgements`, and actual deployment configuration; it must
not prompt an invented environment variable. Changing an active list requires
checking acknowledgement compatibility too. This audit made neither change.

Registration is deliberately **insert-only**. `_load_or_register_definition`
preserves existing rows and pause switches; it does not overwrite stored schedule,
handler, parameters or budgets to match source (`execution/job_executor_service.py:1334`).
Execution uses the stored definition's budget (`:1542`). Later versions start
disabled and open prior-version work is considered before current-version work
(`:1619`). Therefore the packet must bind **all stored versions**, including
superseded/disabled rows, to the intended spec. Include ID, name/version, handler,
enabled/pause state, schedule/timezone, queue/concurrency key, attempts, lease,
budget, retry policy and complete parameters: cadence/phase, source lag/cadence,
selection/catch-up policy, writer floor/ceiling and handoff tokens.

This is not hypothetical drift: the
[MTBS rollout receipt](../../environmental_postgres_retirement_20260904/evidence/mtbs-live-rollout-20260911.md:37)
records that a persisted version-2 burn-forward row retained weekly cadence and
shorter budgets after code changed. Its separate reconciliation recorded daily
08:55 UTC, worker budget 2,130 seconds, lease 2,220 seconds and capture every seven
days. That bounded historical readback does not cover the other definitions.

## Dated ownership and cutoff receipts

| Receipt | What it establishes | What it does not establish |
| --- | --- | --- |
| [September 2 scheduler handoff](scheduler-handoff-20260902.md:38) | Release `e4490c3c2f2e23f75cc9d6e297f4be646e0e00a1`, deployment `b1f35a20-6e05-48ff-9801-5235c9753a01`, historical 37 executable active lanes and snapshot-only responsibility. | Current activation. Its configFile claim is explicitly retracted by the September 3 correction at line 192. |
| [September 3–4 tick receipt](post-deploy-tick-2026-09-03.md:297) | Later 37-to-26 active-lane and 36-to-26 acknowledgement changes; an old perimeter run was still open. | Present process quiescence, required-token parity or a current environmental cutoff. |
| [September 10 repair receipt](../../environmental_postgres_retirement_20260904/evidence/parquet-runtime-repair-20260910.md:284) | At `2026-09-10T13:48:23.7340624Z`, the running 28-lane list matched the prepared prior list; only ACTIVE_LANES was changed to 20 with `skipDeploys=true`, `staged=false`. | It explicitly says `configured_pending_deployment`; neither actual activation nor termination follows from a later successful build. |
| [September 11 operational retrospective](../../../retros/parquet_operational_checkpoints_20260911/README.md:15) | Temperature history and bounded current MTBS publication are completed slices. | Temperature forward burn-in or future MTBS scheduled execution. |
| Separate `341d44b` recovery packet, described below | Reports 20 effective lanes and no owned cutoff-group lease in its September 11 snapshot. | Integration into this main tree, a current all-worker drain, or recovery of its undeployed changes. |

The exact eight-lane pause scope from the
[database-boundary receipt](../../environmental_postgres_retirement_20260904/evidence/active-lane-database-boundary-20260910.md:55)
is `jobs-firms-archive`, `jobs-streamflow-archive`, `mtbs-forward`,
`vegetation-catch-up`, `maintenance-firms-archive-plan-gaps`,
`maintenance-firms-archive-reconcile`, `maintenance-streamflow-archive-plan-gaps`
and `maintenance-streamflow-archive-reconcile`. The two materialized-view lanes
were outside this pause. No ninth pause or authority to resume old database
archive commands follows from these records.

Current main lacks the detailed files named `.omc/research/executor-ingestion-cutoff-20260910.json`,
`mtbs-paused-definition-reconciliation-applied-20260911.json` and
`mtbs-lane-post-rollout-inspection-20260911.json`. The planned `forward-burn-in.md`
is absent too. Original owners must supply immutable receipts, or separately
authorized runtime work must recapture the missing facts. The separate recovery
candidate's retained evidence is a possible bounded input, not an automatic
replacement for those files or a fresh global certificate.

## Cursor, lease and fencing requirements

The inventory label `agri.job_work_item.cursor` at
`execution/job_executor_service.py:248` is inaccurate. The actual work item has
`checkpoint_sequence`, not a cursor (`db/agri/tables/job_work_item.sql:7`).
`agri.job_checkpoint` holds `cursor`, `cursor_checksum`, sequence, work-item ID,
attempt ID and fencing token (`db/agri/tables/job_checkpoint.sql:7`).
`jobs/lease.py:404` loads the latest checkpoint; `:437` advances the fenced
sequence and appends canonical cursor JSON/checksum. A readback must join these
identities rather than query a nonexistent work-item cursor column.

The outer cursor records `ready` plus scheduled bucket before launch, and
`completed` plus bucket/completion time after zero exit
(`execution/job_executor_service.py:1977`). It is not a source-data progress
receipt. A crash after side effects but before outer completion replays the
command, so source/domain checkpoints, manifests, immutable part/marker receipts
and availability generation/pointer remain independently required.

Implemented protections include a pinned connection and session advisory leader
lock `plantgeo:unified-job-executor:v1`, sequential awaited lane invocation
(`execution/job_executor_service.py:1800`), atomic claim/fence increment under
`FOR UPDATE SKIP LOCKED` (`sql/jobs/claim_work_item.sql:138`), and fence/owner/state
checks on heartbeat, checkpoint and terminal transitions
(`sql/jobs/extend_work_item_lease.sql:51`, `advance_checkpoint_sequence.sql:64`,
`complete_work_item.sql:54`, `close_attempt_succeeded.sql:70`).

These are not a blanket no-overlap guarantee. `concurrency_key` has no enforcement
(`jobs/AGENTS.md:303`). Handoff tokens assert `disabled-and-no-run-in-flight` but
do not measure it (`execution/AGENTS.md:1752`). The serial-tick test mocks actual
lane execution (`tests/test_job_executor_service.py:1985`), and shutdown tests use
fake processes (`:2177`); neither is an OS-process or production concurrency test.

**Confirmed base limitation:** `_stop_process` attempts terminate, waits 30
seconds, attempts kill, then waits 10 seconds. A second timeout logs
`plantgeo_job_executor_subprocess_reap_timeout`, cancels its waiter and returns
without proving child exit (`execution/job_executor_service.py:1895`). The monitor
can then return shutdown/timeout/fence-loss (`:1932`), permitting later work after
unconfirmed cleanup. The separately owned candidate adds `JobExecutionAbortError`
and preserves unresolved work while aborting dispatch/service execution. It needs
owner intake and independent integration review; this evidence task applied no fix.

## Local custody and the candidate requiring its original owner

The companion JSON observed 13 registered worktrees at
`2026-09-12T07:26:42.3971357Z`. All status commands exited zero using command-scoped
`safe.directory` and `--no-optional-locks`; the inaccessible global ignore-file
warnings are retained. Only status paths and committed blobs were inspected.
This inventory does not cover ignored files or unregistered directories and is
not an atomic multi-worktree snapshot. Main advanced to `fe9098ae045dda7a74d8f955dcc8c8e097ea09c9`
during the task; the inspected definition surface still matched the frozen base.

Most inspected worktrees match the base's definition surface. The old weather
cutover worktree retains an earlier divergent implementation, not a current
executor candidate. Historical scheduler refs are also distinct: sole-scheduler
`e4490c3` and fixes `d763943` are ancestors of base; shadow-preflight `b4ec9c7`
is not. Their roles are dated in the handoff receipt, not promoted here.

The material unreconciled candidate is:

| Identity | Exact value |
| --- | --- |
| Original branch | `codex/gapless-publication-recovery-20260911` |
| Original commit / tree | `341d44b23c60512630c8a7c0ab778e1ffa008b27` / `23e36740a8223436b2b068261292f65fb8e6e89c` |
| Retained cherry-pick / tree | `b6eb3ae20fab1c670065d6bf01647faf3f96612d` / `89bdf37809bcd8340c712ba3c7f73ece6d054743` |
| Preserved custody checkout | `C:/Users/atooz/.codex/worktrees/3529/plantgeo` |
| Custody HEAD / tree | `2317ee3a2ac35de489d27503dfec91ea3285bd66` / `beb0178b35027131c599940e968d0bacaeef173a` |
| Integration disposition | Original and cherry-pick are not ancestors of the audited base; no cherry-pick, copy or application occurred here. |

The JSON binds the original and cherry-pick blobs for `generic-recovery-20260911.md`,
`live-reinventory-20260911.json`, `scheduled-soil-advances-20260911.json`,
`validation-20260911.json`, executor source and worker source; those corresponding
blobs are equal. The full candidate changes 27 files, including generic
publication behavior, tests, quality receipt and track records. It must not be
treated as a drop-in executor-only patch. The 3529 worktree's uncommitted paths
are QA/Conductor/OMC custody, not changed executor source; their bodies were not
read or copied. A current original-author task ID was not established by the
reviewed registry/metadata. The original branch owner and custodian must bind it.

The candidate's committed prose reports a September 11 `18:00:57`–`18:03:33 UTC`
read-only capture at deployed `fa202230958fb55521963e886eb031be5fc266c4`, deployment
`95304e24-16ed-4705-a615-c60b0e368a08`. It reports 20 effective active lanes, all
eight cutoff lanes shadow, and no owned cutoff-group lease. It also reports a
`parquet-calendar` lease, all 50 stored definitions enabled, and an attempt query
truncated at 1,000 rows. Thus even its own scope is not a global drain certificate.
Its three scheduled backlog advances cover six soil products, not all activated
products or three advancements of the newest selectable day. Its historical
6,016-pass Python receipt belongs to that candidate; it is not a test run or
approval of this base. No underlying live system was re-read here.

For exact ownership comparison, the committed candidate JSON's
`executor_ticks.last_active` identifies these 20 lanes at its last retained tick,
`2026-09-11T17:55:20.739555070Z`. This is the candidate's dated list, not a current
activation instruction. `not_due` means the bucket was already settled in that
tick; it does not mean the lane was disabled or published new data.

| Lane IDs in the candidate's active set | Recorded tick state |
| --- | --- |
| `burn-severity-direct-forward`, `climate-nasa-power-direct-forward`, `drought-direct-forward`, `evacuation-zones-direct-forward`, `fire-detections-direct-forward` | `not_due` |
| `jobs-matview-refresh`, `jobs-strategy-mv-refresh`, `maintenance-validate-streams`, `parquet-calendar` | `not_due` |
| `parquet-fire-detections`, `parquet-fire-perimeters`, `parquet-soil-survey`, `parquet-water-gauges` | `not_due` |
| `soil-era5-land-direct-forward`, `vegetation-sentinel2-ndvi-direct-forward`, `water-gauges-direct-forward`, `watersheds-direct-forward`, `weather-observations-direct-forward` | `not_due` |
| `parquet-signal`, `sensors-direct-forward` | `failed`; activation is distinct from a successful invocation |

Required original-owner handoff: committed source/base/tree and complete path
set; comparison against this base including overlapping publication changes;
source/evidence blob pins and capture limits; candidate-specific quality receipt
and independent verdict; resolved current task custody; and an explicit decision
about which code and dated evidence to integrate. Preserve its immutable source
objects and every unrelated uncommitted file during that work.

## Exact remaining acceptance packet

| Gate | Evidence still needed | Owner |
| --- | --- | --- |
| E1 — effective definition | Service/deployment/image/revision/tree, resolved root/config/Dockerfile/argv, all instances, active/token settings and destination identities; full stored rows for every `plantgeo.executor.%` name/version compared to source. | Gapless executor owner; authorized runtime operator supplies readback. |
| E2 — effective cutoff | Timestamped before/after eight-lane selection, pauses and process ticks; old service/manual worker identities and explicit terminal/reaped outcomes; no old work dispatch after cutoff. Include required tokens, not only the active count. | Environmental retirement with gapless. |
| E3 — checkpoint/lease census | All open current/prior-version runs plus newest terminal bucket; run ID/key/status, work-item ID/status/available-at/retry-at/attempt-count/max-attempts/fence/lease-owner/expiry/heartbeat/sequence; attempt IDs/fences/start/finish/outcomes; checkpoint cursor/checksum/sequence; failure streak and any supersession incident/evidence. State snapshot interval, query bounds and truncation. | Gapless. |
| E4 — stale-worker exclusion | Expired-lease reclaim tied to old/new attempt and fencing tokens; rejected stale heartbeat/checkpoint/terminal writes; old child's confirmed termination and no late publication; replacement terminal output. Include the cleanup fix's integration/review status. | Gapless recovery owner. |
| E5 — stream/window no-overlap | Every legacy, generic, direct, manual and retry writer mapped to one product/window and bounded observation interval. Prove old termination before replacement start and no concurrent mutation. Bind writer receipts and all-rung/availability results. | Gapless plus source/product owners. |
| E6 — observed recovery and advances | Failure → retry → terminal publication; restart retaining exact definition/cursor and bounded catch-up; lease expiry recovery; three consecutive scheduled publication advances per activated product with exact buckets/runs/attempts/output identities. Preserve separate unchanged/unsettled/no-data outcomes. | Gapless p6; production acceptance reviews final packet. |

For E5, retain exact adjacent ownership boundaries: fire generic through
2026-08-24/direct from 2026-08-25; water generic through 2026-09-01/direct from
2026-09-02; vegetation generic/backfill through 2026-09-05/direct from 2026-09-06.
Vegetation still has a parser conflict preventing simultaneous generic/direct
activation. Climate and soil generic/direct conflicts and the other mapped
direct/generic pairs remain enforced by activation parsing
(`execution/job_executor_service.py:357`, `:412`, `:1114`). Date partitioning or
token validation alone is not old-worker exclusion. Each adjacent boundary also
needs terminal-day coverage so that no gap is stranded between owners.

Full historical acquisition ownership remains independently open: bounded
forward lookbacks and generic retained-input repair cannot acquire missing
upstream history. Follow the older-window owners and governed-absence gates in
[the local publication blocker receipt](local-publication-blockers-20260912.md:20).

## Verification boundary

Two independent read-only investigation lanes checked committed implementation
and retained receipts before this packet was written. A separate verifier reviews
the completed packet and custody pins before local handoff. Final checks are
documentation/JSON/link/blob/whitespace checks only. Per
[`docs/testing.md`](../../../../docs/testing.md:40), documentation-only changes
do not require application test execution. No passing runtime suite, physical
publication, current active set or operational gate closure is claimed.
