# Execution runtime

`job_executor_service.py` is the single scheduler for PlantGeo data work. Railway runs it as the
continuous `plantgeo-job-executor` service with `agri-service ops jobs-executor`; Railway cron
schedules and per-source scheduler services are prohibited.

## File organization (2026-09-18 split)

`job_executor_service.py` (~1,784 lines after the split) owns the scheduler leader lock, definition
registration, turn-report bookkeeping (`TurnReport`/`_LANE_TURN_REPORTS`/`record_turn_report`/
`summarize_turn_report`/`parse_terminal_report`), the subprocess wrapper and monitoring
(`CommandOutputTail`, `_monitor_subprocess`, `run_scheduled_command`), tick planning, and the
service loop. Three new sibling modules hold purely declarative or computational logic:

- `lane_specs.py` (634 lines) — `LaneExecutionSpec`, the `_JOBS_SPECS`/`_MIGRATION_INPUT_SPECS`/
  `LANE_SPECS` table, `ActivationConfig`/`parse_activation`, and constants. Zero database I/O.
- `lane_scheduling.py` (186 lines) — cadence-bucket math (`scheduled_bucket`, `fair_due_order`,
  `DueLane`, `LatestRun`, `CheckpointVerdict`, `judge_failed_checkpoint`, `supersession_command`).
  Pure functions and dataclasses.
- `turn_reports.py` (127 lines) — per-tick result containers (`LaneTickState`, `LaneTickResult`,
  `ExecutorTickSummary`, `OperatorAction`). No process-held mutable state.

`run_scheduled_command` stayed in `job_executor_service.py` on purpose: `tests/execution/test_command_stderr_capture.py`
monkeypatches `job_executor_service` module globals (`LANE_SPECS`, `parse_activation`, `_LANE_TURN_REPORTS`,
etc.) and expects `run_scheduled_command` to read those same rebound names. A `from x import y` at
import time binds a copy of the reference; monkeypatching the source after that has no effect on an
unqualified global inside the moved function. Moving it would be a test breakage. Every other
monkeypatch site (`test_gap_repair.py`, `test_lane_cadence.py`, `test_self_healing.py`,
`test_job_run_supersession_agri_db.py`) targets functions that stayed in `job_executor_service.py`
and are unaffected.

### The ML lane left this directory (2026-09-18)

Eighteen modules were deleted in the same push, not refactored: `analog_ensemble_cli.py`,
`analog_ensemble_model.py`, `analog_ensemble_persist.py`, `conformal_recalibration.py`,
`covariate_wind_lane.py`, `covariate_wind_model.py`, `covariate_wind_persist.py`,
`forecast_receipt_writer.py`, `recommendation_commands.py`, `recommendation_lane.py`,
`seasonal_benchmark.py`, `seasonal_command.py`, `seasonal_evaluation_export.py`,
`seasonal_evidence_report.py`, `seasonal_lineage_persist.py`, `seasonal_row_types.py`,
`strategy_selection.py` and `strategy_label_mapping.py`. None of them ran: no CLI verb registered
the five lane entry points, and seven of the ten `agri.*` tables their SQL named are absent from
`db/agri_baseline.sql`. Their pure halves are `plantgeo_ml_service.method.ml` and
`plantgeo_ml_service.pipeline.strategy_*` in `services/plantgeo-ml-service/`; their Postgres halves
are not ported anywhere and are re-expressed against Parquet in phase 2 of track
`plantgeo_ml_service_20260918` (owner decisions D2, D5, D6). Git history is the archive; do not
restore a module from it to re-add a database-backed training lane.

The vegetation modules that remain are NOT part of that lane. `vegetation_ndvi_forecast.py` here is
the retained observed-release copy imported by `vegetation_ndvi_plane.py` and
`vegetation_partition_promotion.py`; the copy that moved was `method/monte_carlo/`'s. The four
`sql/execution/insert_forecast_series|insert_forecast_iteration|insert_forecast_iteration_value|
reconcile_forecast_iteration_actuals` files stayed with it for the same reason.

## Lane activation

`PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` is the only deployment activation control. An empty value keeps
all lanes in shadow mode. Every selected identifier must be present in `LANE_SPECS`, executable, and
free of a declared active-lane conflict. Removed services do not participate in runtime validation
and must not be represented by service IDs, owner constants, or acknowledgement variables.

Lane cadence, phase offset, command, timeout, catch-up policy, and publication contract live in
`LANE_SPECS` (see `lane_specs.py`). Keep each current source-direct lane as a separate failure domain.
New recurring work must be registered there instead of adding a Railway cron.

`VEGETATION_NDVI_PROMOTION_LANE_ID` is registered and deliberately NOT in the deployed allow-list.
Activating it is a production mutation an owner makes by adding the identifier to that variable on
the job-executor service (the lane's own command carries no `--day`, so `promotion_ceiling` picks
the ceiling from the vegetation lane's own Parquet AVAILABILITY INDEX -- the newest day it states
SERVABLE at or before today -- and `default_promotion_days` promotes `--max-days` backwards from
there. It never reads Postgres `agri.vegetation`: that table is frozen since 2026-09-04, and the
lane's first activated tick raised exactly because its ceiling then came from
`pipeline/direct/vegetation/forward.py::settled_through`, which queries it. An index with no
servable day at all yields an empty day list, which the turn reports as `no_days_promoted`
rather than raising).

**Servable, not base-rung-published.** `layer-lanes.md` §4a: "a selectable day is their
intersection, not the observed union." The promoter READS the base rung -- that is where the bytes
it content-addresses live -- but `VegetationDayPartitionKey` is zoom-independent, so promoting a day
registers it on the governed plane as a whole day. A ceiling chosen off the base rung alone would
therefore register days serving cannot answer at the rungs above them, which is why
`availability_days_at_base_rung` now also carries `servable_days`: the days published at EVERY
`required_rungs` row of the generation it read.

**A ceiling has a maximum age, because a ceiling compared against itself is always current.** Under
the old rule a lane whose forward writer died re-confirmed the same ancient day every turn, reported
it as `unchanged` -- which counts as progress -- and exited 0 forever. A default-window turn now
refuses BEFORE it promotes anything when `today - ceiling_day` exceeds
`lane_specs.vegetation_promotion_stale_ceiling_days()`, with its own status `stale_ceiling`, its own
reason `newest_servable_day_is_older_than_this_lane_can_explain`, and a NON-ZERO exit. Every
default-window report carries `ceiling_day`, `ceiling_age_days` and `ceiling_stale_after_days`,
including the green ones, so "which day did it promote" is answerable from the report alone.

The threshold is TWO publication windows off the lane's registered `publication_lag_days` (7 -> 14
days), never a literal: `lane_registry.py`'s vegetation `floor_basis` records that 7 is a MEASURED
MEDIAN gap between usable Sentinel-2 days, widened past the nominal 5-day revisit by cloud
screening. Half a healthy lane's gaps are therefore wider than one window, so a one-window bound
would refuse at a provider edge the source cannot beat; two consecutive empty windows is the writer,
the ingest or the index having stopped. An operator naming `--day` explicitly is a bounded repair
and is never gated on freshness.

The turn's per-day outcome is decided by the vegetation lane's AVAILABILITY INDEX
(`layer-lanes.md` §4a), which the promoter reads and never writes, before it opens any object:

- index says `governed_absence` -> reported `status: "absent"` carrying the INDEX'S OWN
  `absence_reason`, no object read attempted, the remaining days still promote;
- index has no row for the day -> `status: "not_yet_indexed"`, skipped, neutral for the exit code;
- index says `published` but the store holds no part file -> the pointer is RE-READ once. ONLY a
  fresh `governed_absence` reclassifies: a prune or retention pass landed inside the turn's window
  and the winning generation records it with a reason, so the day is reported absent carrying
  `reclassified: "availability_index_advanced_during_turn"` (STYLE-REVIEW-W5 S4). Every other fresh
  verdict raises `AvailabilityPartitionConflictError` -- a still-`published` pointer because that
  disagreement is corruption, and a fresh `not_yet_indexed` because the index LOST a row it had,
  which reclassified would have exited 0 as `waiting_for_writer` and made an availability regression
  silently green (STYLE-REVIEW-W6 S2). The refusal names the day, the fresh verdict, BOTH generation
  SHAs and the `_LATEST.json` pointer key, because after a re-read two generations are in play and
  a stale snapshot and a real divergence otherwise read the same (STYLE-REVIEW-W6 S3);
- a day that was written and is empty still fails, naming the lane and the day.

A turn ends on one of four statuses (STYLE-REVIEW-W5 S3, W8 S3):

- `stale_ceiling` -- the newest servable day is more than two publication windows behind today, so
  no day was evaluated at all. **Exits non-zero.** Decided before the turn runs, because "this
  window is too old to be progress" is a statement about the window rather than about what its days
  held.

- `completed` -- at least one day promoted or confirmed unchanged. Exit 0.
- `waiting_for_writer` -- EVERY evaluated day was `not_yet_indexed`. Exit 0, `reason:
  "forward_writer_has_indexed_none_of_these_days"`, logged once per turn. This is the steady state
  of the lane as configured (writer not started, `--max-days` 1), and exiting non-zero for it would
  page every turn, indefinitely, for a lane behaving exactly as intended. It is deliberately not
  `completed`: nothing was promoted, and the report says so.
- `no_days_promoted` -- anything else with no progress: `reason: "all_days_absent"` when every
  requested day was a governed absence, otherwise `no_indexed_day_promoted`. **Exits non-zero**, so
  a scheduled lane cannot succeed vacuously against days that should have been there.

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

## Command stderr reaches the ledger

`run_scheduled_command` pipes ONLY the child's stderr (`stderr=asyncio.subprocess.PIPE`), drains it
concurrently through `CommandStderrTail`, re-emits every chunk on this process's stderr so the Railway
log stream is unchanged, and keeps a bounded TAIL (`COMMAND_STDERR_TAIL_BYTES`). Every failure reason --
non-zero exit, timeout, fence lost -- carries that tail after the headline, so
`agri.job_attempt.last_error_summary` states the child's actual exception rather than only
`command exited with status 1`. The sensors incident of 2026-09-12 needed log archaeology for exactly
that reason. The tail is content and therefore travels ONLY through `reason`, which
`jobs.lease.fail_work_item` redacts and clamps; `metrics` carries counts alone (`stderr_bytes`,
`stderr_truncated`) because metrics are stored unredacted. Stdout stays inherited: a writer's JSON turn
report is the log stream's, not the wrapper's. The drain is bounded by `COMMAND_STDERR_DRAIN_SECONDS`
after exit so a grandchild holding the pipe cannot hold the attempt.

## Turn reports: an exit-0-but-incomplete turn is persisted

Every direct writer prints exactly ONE terminal JSON report on stdout, last, and since 2026-09-18 exits 0
when at least one day wrote while reporting `outcome=incomplete` with `days_unwritten` and
`unwritten=[{day, outcome, detail}]`. stdout is therefore piped and teed exactly like stderr
(`CommandOutputTail`, `_default_stdout_sink`), so the Railway stream is unchanged, and
`parse_terminal_report` takes the last JSON-object line of the bounded tail (`COMMAND_STDOUT_TAIL_BYTES`).
`summarize_turn_report` bounds it (`TURN_REPORT_UNWRITTEN_MAX` entries, `TURN_REPORT_DETAIL_CHARS` per
detail, nested per-product `results[].unwritten` folded in) and `run_scheduled_command` writes it to the
completed checkpoint cursor as `turn_report` and to metrics as `days_unwritten`, whatever the exit
status. The streak `consecutive_incomplete_buckets` is process-held (`_LANE_TURN_REPORTS`, keyed by the
OWNING lane so a repair turn counts toward it) and says so; the tick lifts every incomplete lane to
`ExecutorTickSummary.incomplete_lanes`, one `plantgeo_job_executor_lane_incomplete` WARNING per tick
that ran it, and the healthy/unhealthy tick line. A day stuck at `status=conflict` is now visible on the
checkpoint row and in the tick without log archaeology; it is still the lane's own contract that decides
what to do about it. Each `detail` is `redact_text`ed here because the cursor path canonicalises but
never redacts. Two assumptions worth knowing: the tee forwards 4 KiB chunks, so an executor structlog
line can interleave INSIDE a child's long JSON line in the Railway stream (the ledger copy is intact);
and `parse_terminal_report` takes the last `{`-line, which assumes nothing but the writer's own report
is JSON on stdout -- JSON-rendered structlog on stdout (not the case today) would be mis-parsed.

## Operator action surface

A lane held behind a recorded-supersession requirement is still reported on every tick, but now in
three places rather than buried in a `blockers` string: `LaneTickResult.operator_action` (typed), the
tick summary's top-level `operator_actions`, and one `plantgeo_job_executor_operator_action_required`
ERROR event per held run per process (`announce_operator_actions`), with a matching `_cleared` event
once the run is superseded. The 30-second `plantgeo_job_executor_tick_unhealthy` line also names the
commands. This is deliberately NOT an alerting system. The durable surface this state belongs on is an
OPEN `agri.job_incident` row keyed `plantgeo.executor.operator-supersession-required:<run id>`, resolved
by `jobs-supersede-run`; that needs two runtime SQL files under `sql/execution/` (an upsert and a
resolve), which this directory does not own. Until they exist the log events are the surface.

## Bounded gap repair

RUNBOOK directive: write governed Parquet, read only Parquet, no PostgreSQL environmental readers or
writers, an unadmitted product stays unavailable. The generic `parquet-*` gap-fill and drain lanes were
deleted 2026-09-12 as the last PostgreSQL reactivation surface, which left a measured gap with no owner
(audit finding F4). Repair is re-registered WITHOUT a generic exporter:

- `gap_repair_contract.py` (leaf) binds each census layer to the executor lane whose source-direct
  writer owns it (`REPAIR_BINDINGS`), with that writer's own `--max-days` cap and backlog reach; the
  layers with no bounded knob or no time axis are listed in `REPAIR_EXCLUSIONS` with the reason.
  `select_repair_candidates` turns coverage rows into one verdict per lane; only `repair_authorized`
  carries a `RepairRequest`, and a request is refused at construction when it exceeds the writer's cap.
- `gap_repair.py` is the operator verb `jobs-plan-gap-repair`. It reads coverage under the
  `availability` authority only (one pointer GET and one bounded generation GET per lane, never a
  listing) and with `--apply` opens one run per authorized lane under `repair_lane_spec(spec)` -- a
  SEPARATE definition `<lane>:gap-repair`, because `select_latest_run.sql` reads the forward
  definition's newest terminal run as its cadence checkpoint and a repair filed there would settle a
  bucket the forward writer never ran. Backlog class, so `fair_due_order` never hands it the
  incremental turn. One logical run per definition per UTC day; the shard key is the layer and gap span.
- `run_scheduled_command` accepts `EXECUTOR_REPAIR_WORK_ITEM_KIND` and rebuilds the argv from the
  registered spec plus `RepairRequest.command_arguments()`: the lane's own command, `--product` where
  the writer fans out, `--max-days` bounded. A stored payload can never smuggle an argument the
  writer's contract does not expose. The WRITER selects the days (newest unfilled settled day first,
  within its own scan window); the repair only grants it a bounded turn.
- `_plan_repair_runs` in the tick drives open repair runs for ACTIVE lanes and nothing else: it never
  authors, never registers a definition that does not exist, never lists an object, and defers a
  repair whose owning lane's forward bucket is due in the same tick.

- **Nobody is at the keyboard after a stall**, so the leader authors the same work itself:
  `RepairAuthoringClock` (`PLANTGEO_JOB_EXECUTOR_REPAIR_INTERVAL_SECONDS`, default 6 h, `0` disables)
  makes `run_executor_tick` call `_author_due_repairs` once per interval -- one coverage read under the
  availability authority, one plan, one committed applied pass -- and a failed pass advances the clock
  too, so a broken store is never probed every 30 s. The verb remains for a human who wants a turn now.
- **A stalled lane with NO measured gap is still authorized** (`behind_provider` verdict,
  `gap_repair_contract._behind_provider`): availability rows close against the publisher's own ceiling,
  so the shortwave shape has `gap_ranges == ()` by construction and a gap-only rule never fires. The
  span handed to the writer runs from its newest recorded day to the horizon its registered lag
  predicts; the writer's own newest-first selection decides which of those days are settled.
- **Rotation**: `RepairAuthoringClock.recently_authored` (24 h window, process-held) excludes layers
  the previous passes authored (`deferred_by_rotation`), so two persistently unfillable layers cannot
  take the two-lane pass budget every interval and starve the rest.
- **OPT-IN: a new DEPLOYMENT releases a breaker-held lane once** (`ProcessStartRelease`, enabled only
  by `PLANTGEO_JOB_EXECUTOR_PROCESS_START_RELEASES_BREAKER=1`; OFF by default, owner 2026-09-18: the
  sensors release is an explicit CLI supersession until its fix has proven itself). A hold means "this
  code failed three buckets; look at it"; a deploy IS someone having looked. The release is a REAL
  supersession -- `job_run_supersession.supersede_failed_run` with operator `executor:process-start` --
  guarded by a durable one-row-per-(deployment, lane) marker written through the same incident insert
  (`claim_process_start_release`, `ON CONFLICT DO NOTHING`), so a container restart under the same
  deployment re-releases nothing, and only a run whose BUCKET lies strictly before the process's start
  bucket qualifies, so a bucket this process opened is never its own release. Every refusal and every
  ledger fault rolls back and returns the lane to its hold without aborting the planning tick.

What remains (R3): the source-direct historical verbs for the lanes whose gaps lie beyond their
writers' reach (`unreachable_by_forward_writer`), for fire-detections and water-gauges, and for
burn-severity cohorts. Adding a `--repair-from/--through` target to a writer is that writer's contract
change (`tests/direct/test_direct_writer_contract.py`), not this directory's.

## The once-per-UTC-day cadence report was a log-reading artefact

Refuted 2026-09-18 against the ledger and two captures (`.omc/research/runbook-20260915-shortwave/prod-logs`,
the 2026-09-18 01:02-07:20Z stream): every active hourly lane opened and ran a NEW bucket every hour
(`state: "ran"`, `succeeded`, ~79 s for climate). The child's stdout report is a JSON line, which Railway
parses into structured fields with an EMPTY `message`, so a text grep on `message` matched only the
reports long enough to be chunked as raw text (the 00:43Z turns). `tests/execution/test_lane_cadence.py`
pins the hourly behaviour. Daily lanes advancing exactly one settled day per day is the provider lag,
not the scheduler.

## Vegetation NDVI partition registration (2026-09-19): the corpus digest is gone

`vegetation_ndvi_plane.register_governed_partition_plane` registers ONE Parquet day partition and
reads no source table. It replaces `register_governed_plane` / `register_governed_forward_plane`,
which fingerprinted and materialised the whole `geo.features` NDVI corpus. That corpus was frozen
when the `postgres-vegetation` lane was retired (owner call 2026-09-04), so `_corpus_digest` found a
NULL checksum and raised `ValueError: no vegetation observations exist at or before <day>` on every
scheduled turn — the raise that rolled the promotion lane back twice on 2026-09-19 (evidence:
`conductor/tracks/gapless_parquet_publication_20260901/evidence/ndvi-promotion-activation-20260919.md`,
§"Re-activation after c922509d"). W8-F had already re-pointed DAY SELECTION at the availability
index; the register verb underneath it was the layer still holding Postgres.

**Why the digest was deleted rather than re-sourced.** Its six fields had exactly two consumers:
`_register_source_release` (release identity + observed window + `quality_summary` shape counts) and
the `GovernedPlane` the summary carries. Both are satisfied by the partition itself, and the owner's
settled design (memory `plantgeo-owner-decisions-2026-09-18`, backlog P4) already keys promotion by
per-day-partition content SHA. Re-sourcing a WHOLE-CORPUS digest from Parquet would have meant
reading every day partition on every single-day turn to answer a question no consumer asks. The two
`_corpus_digest` call sites were the only ones in the service (`register_governed_plane` itself had
had no caller since it was written), so the helper and `sql/execution/corpus_digest.sql` went with
them, together with the other statements that could only ever read the frozen tables:
`load_observations.sql`, `load_observations_for_days.sql`, `insert_spatial_cells.sql`,
`select_candidate_cell_keys.sql`. No live statement in this module binds a `layer_name` any more,
which is what makes the frozen tables UNREACHABLE rather than merely unused.

**What the confirmation re-read protected, and why there is no replacement.** The second
`_corpus_digest` call compared the corpus before and after materialisation: against a live,
concurrently written Postgres source it caught "the rows I registered a checksum for are not the
rows I loaded". On the partition path the digest and the INSERT are computed from ONE immutable
in-memory tuple, so that divergence is not expressible. The Parquet-side analogue — the partition's
generation advancing mid-turn — is detected upstream, where the authority lives: the promoter's
single pointer re-read and `AvailabilityPartitionConflictError` (`layer-lanes.md` §4a).
`CorpusChangedDuringRegistrationError` was deleted with the read it belonged to.

**Identity.** The registered `payload_checksum` IS the partition's content SHA:
`partition_payload_checksum` is byte-identical to
`vegetation_partition_promotion.day_partition_content_sha256` (same `foundation.canonical` routine,
same sorted `[[cell_key, value], …]` document), pinned by
`tests/execution/test_vegetation_partition_registration.py`. The promoter imports this module, so
the equality is held by a test rather than by a shared import. One consequence worth knowing: the
release-set logical key is always the payload-versioned form, so `load_governed_plane(cutoff_day)`,
which looks up the UNVERSIONED `release_set_logical_key`, does not find partition registrations. It
did not find forward registrations before this change either.

**What it refuses, in its own vocabulary.** Every refusal is a `PartitionRegistrationError`
subclass, never a bare `ValueError`, because the scheduled lane must be able to render a failure as
a turn report: `EmptyPartitionRegistrationError`, `DuplicatePartitionCellError`,
`NonFinitePartitionValueError`, `UnregisteredPartitionCellsError`, `PartitionSourceNotSuppliedError`,
plus the pre-existing `EmptyGovernedReleaseError` and `ReleaseSetManifestConflictError`, now rooted
in the same base.

**What this verb will not do: mint a lattice cell.** `agri.spatial_cell` needs a polygon and a
resolution; a `(cell_id, metric_value)` partition row carries neither. The observation insert joins
that dimension, so an unregistered cell would silently not land and the turn would report a smaller
promotion than it performed. `select_unregistered_spatial_cells.sql` therefore asks FIRST and
`UnregisteredPartitionCellsError` names the missing keys. If a partition ever publishes a genuinely
new cell, widen `vegetation_partition_promotion.read_day_partition_cell_values` to carry the base
rung's `cell_longitude`/`cell_latitude` (both NOT NULL there) and register the cell from its own
position — do not resurrect a geometry read against `geo.features`.

**Known gap, deliberately left.** `data_available_at` on each governed row is the registration
instant, not the partition's own `data_available_at` column, because the promoter's reader does not
carry it yet. This is conservative for leakage (never earlier than the truth) and is fixed by the
same widening. `OBSERVATION_CHECKSUM_PREFIX` is deliberately a NEW prefix: the retired loader hashed
scene ids, cloud cover and sample counts that a day partition does not carry.

## Quality receipt

Changes in this directory affect the Python quality fingerprint. Regenerate
`services/agri-data-service/QUALITY_RECEIPT.json` only after the final code and test edits are settled.
