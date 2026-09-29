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
- `lane_catalogue.py` (config-driven ingestion, Phase 1) — the catalogue bridge: which definitions run on
  the legacy path and which on the config path, and the CA12 kill-switch over both. See "Lane catalogue".
- `cron_schedule.py` (config-driven ingestion, Phase 1) — the next-fire clock over
  `foundation/lane_config/cron.py::parse_cron`. See "Cron schedule".
- `provider_budget.py` (Wave O at G1) — WQ-4's paid Open-Meteo admission, before `fair_due_order`. See
  "Budget admission".
- `usage_receipt.py` (Wave O at G1) — the monthly `receipts/source-usage/<YYYY-MM>.json`. See "Daily upkeep".

The line counts above are the 2026-09-18 split's; the files have grown since and are not re-counted here.

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
all lanes in shadow mode. A selected identifier that is not in `LANE_SPECS`, not executable, or on the
losing side of a declared active-lane conflict is QUARANTINED (`ActivationConfig.quarantined`, GL-5),
never a startup exit: every other lane runs (see "Soft failure"). Removed services do not participate in runtime validation
and must not be represented by service IDs, owner constants, or acknowledgement variables.

`PLANTGEO_JOB_EXECUTOR_STOPPED_LANES` is the ONE env kill-switch (CA12), distinct from the allow-list: a lane
named there never dispatches on either path, and naming `<lane>` also stops its `:gap-fill` and
`:gap-repair` definitions. It follows the allow-list's rule (S8, H6/FR-11): an id no path knows is
quarantined with one warning and one `lane_quarantined` incident (`detail.variable` names the variable),
never an exit, and every other named lane is still stopped. See "Lane catalogue".

Lane cadence, phase offset, command, timeout, catch-up policy, and publication contract live in
`LANE_SPECS` (see `lane_specs.py`). Keep each current source-direct lane as a separate failure domain.
New recurring work must be registered there instead of adding a Railway cron -- or, for a lane on the
config path, in its lane TOML (`lanes/<id>.toml`), which `lane_catalogue.py` turns into definitions.

**Legacy soil runs six-hourly since G1** (O6/FR-21): `cadence_seconds` 21,600 with `phase_offset_seconds`
3,000 unchanged, so its buckets fall at 00:50, 06:50, 12:50 and 18:50 UTC and its decorative schedule
string is `"50 */6 * * *"`. This sits ON TOP of G0's per-run cap (`pipeline/direct/soil/forward.py`: one
day and 33 logical requests per run), so the paid Open-Meteo spend is at most four capped runs a day.
Both pins move together: `tests/direct/soil/test_lane_registrations.py::EXPECTED_SCHEDULE` and
`tests/test_job_executor_service.py::EXPECTED_SCHEDULES`; `tests/execution/test_cron_schedule.py` proves
the string names the buckets the cadence opens.

`VEGETATION_NDVI_PROMOTION_LANE_ID` is registered and deliberately NOT in the deployed allow-list.
Activating it is a production mutation an owner makes by adding the identifier to that variable on
the job-executor service (the lane's own command carries no `--day`, so `newest_servable_day` picks
the ceiling from the vegetation lane's own Parquet AVAILABILITY INDEX -- the newest day it states
SERVABLE at or before today -- `default_promotion_days` promotes `--max-days` backwards from there,
and `promotion_ceiling` measures that same day for freshness, one predicate deciding both so the day
promoted and the day measured can never diverge. It never reads Postgres `agri.vegetation`: that table is frozen since 2026-09-04, and the
lane's first activated tick raised exactly because its ceiling then came from
`pipeline/direct/vegetation/forward.py::settled_through`, which queries it. An index with no
servable day at all yields an empty day list, which the turn reports as `no_days_promoted`
rather than raising).

**Servable, not base-rung-published — for EVERY day in the window, not only the ceiling.**
`layer-lanes.md` §4a: "a selectable day is their intersection, not the observed union." The promoter
READS the base rung -- that is where the bytes it content-addresses live -- but
`VegetationDayPartitionKey` is zoom-independent, so promoting a day registers it on the governed
plane as a whole day, at every rung. `availability_days_at_base_rung` therefore carries
`servable_days`, the days published at EVERY `required_rungs` row of the generation it read, and
`is_servable` is on the `LaneAvailability` PROTOCOL rather than on the concrete index alone, because
`run_vegetation_promotion` asks it of every day it evaluates.

`AvailabilityIndexDays.servable_days` is REQUIRED and has no default (corrected 2026-09-19,
STYLE-REVIEW-W10 S7). While it defaulted to `None`, `is_servable` fell back to the base-rung verdict
-- a SECOND definition of servability that the only production constructor could never reach, and
the one every fabricated-availability test exercised. A test double now states its own intersection,
so the predicate the tests prove is the predicate the lane runs. The ordering in
`_non_promotable_entry` (absence first, servability last, so an indexed absence keeps the index's own
`absence_reason` instead of being relabelled `not_servable`) is proved by
`test_a_day_that_is_both_an_indexed_absence_and_unservable_keeps_the_index_reason`, not by reading.

Gating only the ceiling was not enough and was corrected on 2026-09-19 (STYLE-REVIEW-W9 B2, which is
STYLE-REVIEW-W8 S3 still open): `default_promotion_days` returns `max_days` trailing CALENDAR days
below its ceiling, and every one of those used to be classified by `indexed_day`, the base-rung
verdict. A `--max-days 7` catch-up after an outage thus registered any sub-ceiling day the finer
rungs do not publish. Such a day is now its own day status, `not_servable`, with reason
`availability_index_does_not_publish_this_day_at_every_required_rung` and its own top-level
`not_servable_days` list. It is filtered in the LOOP and not out of `default_promotion_days` on
purpose: dropping it from the window would make it invisible to the report, which is the silent skip
§1a forbids. It is neutral for the exit code for the same reason `not_yet_indexed` is -- the writer
is mid-publication, nothing about the day is defective, and the register verb was never offered it.

**The staleness bound measures against the lane's DECLARED publication lag. That is a registry
constant, not a fact about the provider** (corrected 2026-09-19, STYLE-REVIEW-W10 S1; the field was
called `frontier_day` and the prose called it "the provider frontier", and neither was true of the
code). A ceiling compared against itself is always current, so a lane whose forward writer died
re-confirms the same ancient day every turn, reports it `unchanged` -- which counts as progress --
and exits 0 forever. But `today - ceiling_day` cannot answer "has the writer stopped" either, and
the first version of this bound did exactly that (STYLE-REVIEW-W9 B1). `lane_registry.py`'s
vegetation `floor_basis` records `publication_lag_days=7` as a MEASURED MEDIAN gap between usable
Sentinel-2 days, widened past the nominal 5-day revisit by cloud screening -- so a PERFECTLY HEALTHY
lane's newest servable day already sits about seven days behind today, and a 14-day bound measured
from today bought one median gap of slack against a distribution the same registry calls
heavy-tailed. A routine Oct-Mar PNW overcast fortnight tripped it.

**The bound is a LITERAL DAY COUNT and the declared lag is reported beside it, never multiplied by
it** (corrected 2026-09-19, STYLE-REVIEW-W11 S1). `stale_ceiling` is a newest servable day
`VEGETATION_PROMOTION_STALE_CEILING_DAYS` (21) or more days behind today, one comparison in one
unit: `PromotionCeiling.is_stale(stale_after_age_days=...)` is `age_days >= stale_after_age_days` and
touches nothing else (`vegetation_partition_promotion.py`, `PromotionCeiling.is_stale`).

It was `..._LAG_ALLOWANCES = 2` multiplied back by the registered lag, and that shape had two
defects. The constant a reader met said `2` while the bound the code enforced was `3 x lag`, because
counting past the declared-lag day spends one whole lag before the counter starts. And
`publication_lag_days` is documented in `lane_registry.py`'s vegetation `floor_basis` as a MEASURED
MEDIAN -- a number this repo expects to re-measure -- so re-measuring it to 10 would have moved this
SAFETY bound from 21 days to 30 with a green suite, the boundary tests being written as
`lag * (allowances + 1)` themselves. The test compared the code to itself, which is the freshness
yardstick again (`.omc` memory `plantgeo-freshness-yardstick-is-tautological`).

**A safety bound is not a cadence fact.** The standing rule that a cadence fact comes from the
registry and never from a literal in the lane still holds and is still enforced here -- the lag is
read at call time through `lane_specs.vegetation_promotion_declared_lag_days()`. But "how far behind
is DEAD" is not a property of the provider at all: it is this lane's own tolerance, and sourcing it
from a re-measurable median is what let an unrelated measurement move it. So the two questions are
separate constants with separate owners. "How far behind is NORMAL" is `publication_lag_days`, owned
by the registry. "How far behind is DEAD" is `VEGETATION_PROMOTION_STALE_CEILING_DAYS`, owned by this
lane, moved only by editing that line, and PINNED against the literal `21` in
`tests/execution/test_vegetation_partition_promotion.py::test_the_stale_ceiling_bound_is_a_day_count_this_lane_owns`
together with the 20-not-stale / 21-stale boundary. That test, and
`test_re_measuring_the_declared_lag_cannot_move_the_staleness_verdict` (same ceiling, three different
registered lags, one verdict), are the half that ENFORCES this paragraph.

The one surviving link between the two numbers runs one way and can only refuse:
`lane_specs.stale_ceiling_days_clearing_declared_lag` returns its `stale_ceiling_days` argument
unchanged or raises, so no re-measurement can ever compute a bound. Its floor is
`(1 + VEGETATION_PROMOTION_STALE_CEILING_MINIMUM_SLACK_LAGS) * declared_lag_days` -- the `1 +` is the
lag a healthy lane already sits behind today, written out rather than folded into the multiplier,
since folding it in is exactly how `= 2` came to mean `3 x lag`. At the registered lag of 7 the floor
is 21 and the bound is 21, so this lane currently sits exactly ON its floor: re-measuring the lag
DOWN always passes and merely widens the reported `ceiling_declared_lag_slack_days`, while
re-measuring it UP to 8 or more raises, surfacing as this lane's own `failed` report naming the
conflict. A cloudy 16-day gap is inside 21 and reports normally; a stopped writer keeps aging and
cannot escape.

The lag itself is still read from `LANE_REGISTRY` at call time, never copied, and that helper refuses
a non-positive lag. It reads `publication_lag_days` and NOT `cadence_days`: vegetation is
`daily_series`, whose cadence `layer-lanes.md` (96831d8b) §1a pins at 1 -- "only a `release_series`
may declare a cadence above one day" -- so the helper is named for the lag it reads and no longer
spends the word "window" on a cadence the registry does not hold.

**What the bound does NOT know, stated plainly.** Nothing in this turn consults the source. No
availability query is issued (§1b lists "the availability query" among what a layer's source
`Protocol` owns; vegetation's `pipeline/direct/vegetation/source.py` exposes only
`fetch_vegetation_day`, a per-day scene fetch), no source watermark is read, and the whole change
from the first 14-day bound amounts to moving the refusal from `today - 14` to `today - 21` and
reporting the eight `ceiling_*` fields listed below. The word "frontier" is already spent in this directory on the measured thing:
`plan_continuation.py:288 probe_provider_frontier` HTTP-probes the provider per cell, and
`ProviderFrontier` carries `mode: "declared" | "measured"` plus a `measured_at`
(`plan_continuation.py:147-160, 277-286`). Promotion's constant had neither a mode nor a
measurement, so it may not carry that name (`engineering-principles.md` §1, one canonical definition
per concept).

**Is a genuine provider-derived frontier available cheaply here? No -- and it is owed to the source
module, not to this one.** Sentinel-2 would answer it with an Earth Search item search
(`ingest/vegetation.py:562 scene_search_url(bbox, start, end)`) over a trailing window, taking the
newest scene datetime. That costs a network round trip (paged) in a verb whose entire design is
"the availability index is the authority and no socket is opened" (module docstring,
`vegetation_partition_promotion.py`), an `httpx` client and bounds this module does not carry, and a
bbox that under §1b must come from the region manifest rather than a module constant. It would also
answer a DIFFERENT question: a scene existing at the provider is not a day our ingest could keep --
cloud screening removes scenes, which is the very reason the declared lag is 7 and not 5 -- so a
provider frontier would call a healthy lane stale whenever screening rejected what the provider
published, restoring W9 B1's false positive from the other side. The honest construction is §1b's:
the vegetation source `Protocol` gains an availability query, the forward writer records its verdict
in the availability index, and this promoter keeps reading only the index. **Owed, not taken here**
-- `pipeline/` is outside this lane's files, and until it lands the bound is this lane's own declared
tolerance and is named as one.

**A stale turn still promotes its ceiling day.** The verdict is applied AFTER the window runs, not
before it. `DEFAULT_MAX_DAYS` is 1 and no scheduled turn ever revisits a day below its ceiling, so a
refusal taken before evaluation consumed the very day it refused for: when the clouds cleared the
ceiling jumped past it and nothing promoted it again -- the gate manufacturing the permanent hole it
exists to detect. The report therefore carries the promoted days, BOTH halves of the turn's own
verdict (`promotion_status` and `promotion_reason` -- keeping only the status lost the difference
between `all_days_absent` and `no_indexed_day_promoted`, STYLE-REVIEW-W10 S3), status
`stale_ceiling`, reason
`the_newest_servable_day_is_more_days_behind_today_than_this_lanes_stale_ceiling_bound_allows`, and
a NON-ZERO exit. The stderr `vegetation_promotion_stale_ceiling` event carries the ceiling fields and
the verdict only, never the day payload it would otherwise duplicate from stdout (W10 N1).

Every default-window report `main()` prints -- green, refused, stale and `failed` alike -- carries
`ceiling_day`, `ceiling_age_days`, `ceiling_declared_lag_day`,
`ceiling_age_beyond_declared_lag_days`, `ceiling_declared_lag_days`,
`ceiling_declared_lag_slack_days`, `ceiling_stale_after_age_days` and
`ceiling_is_stale`, so the verdict is re-derivable from the report alone and is still readable when
another status wins. The half that ENFORCES it: `main()` merges `ceiling_fields(...)` once, after
the `try/except`, for every default-window turn
(`vegetation_partition_promotion.py`, the `if is_default_window_turn:` block), and `ceiling_fields`
is TOTAL on a `None` ceiling, so a turn that died before reading the availability index renders the
same eight keys as `None` rather than dropping them (STYLE-REVIEW-W10 S2). An operator naming `--day`
explicitly is a bounded repair, is never gated on freshness, carries no `ceiling_*` keys -- and a day
named twice is evaluated once (W10 N2).

Operationally this means a genuinely dead lane exits 1 every hour, burns the work item's five
attempts and dead-letters, while still re-promoting its (unchanged, therefore no-op) ceiling day.
That is the intended loudness: the days are safe, the ledger names the lane, and the refusal costs
one idempotent receipt comparison per turn.

The turn's per-day outcome is decided by the vegetation lane's AVAILABILITY INDEX
(`layer-lanes.md` §4a), which the promoter reads and never writes, before it opens any object:

- index says `governed_absence` -> reported `status: "absent"` carrying the INDEX'S OWN
  `absence_reason`, no object read attempted, the remaining days still promote;
- index has no row for the day -> `status: "not_yet_indexed"`, skipped, neutral for the exit code;
- index states the day at the base rung and not at every `required_rungs` row -> `status:
  "not_servable"`, skipped, neutral for the exit code, no object read attempted;
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
- a day that was written and is empty still fails, naming the lane and the day;
- the register verb refuses the day by name (any `PartitionRegistrationError`) -> `status:
  "registration_refused"` carrying the refusal's `error_class` and message, its transaction rolled
  back so the next day opens a clean one, and the remaining days still run. Every other exception
  still propagates: a turn cannot honestly report a failure nobody has named.

### One outcome, one status: reconciling the ceiling and the refusal vocabularies (2026-09-19)

The staleness bound (W9-C) and the registration-refusal vocabulary (W9-F) were written in separate
worktrees and met here. They do NOT overlap, and the precedence below is what makes that true —
`vegetation_partition_promotion.SUCCESSFUL_TURN_STATUSES` and `FAILING_TURN_STATUSES` are kept as
two sets precisely so a test can prove they PARTITION `TERMINAL_STATUSES`, rather than leaving
`exit_code_for`'s fail-closed default to absorb a status nobody defined.

A turn ends on exactly one of SIX statuses, decided in this order (revised 2026-09-19 by
STYLE-REVIEW-W9 B1 and S3; `stale_ceiling` moved from first to third and `failed` joined the
vocabulary it had always been printed beside):

1. `failed` -- an exception nobody named escaped the turn, rendered by `failed_report` in the turn's
   own shape: empty day lists, a `reason`
   (`an_exception_escaped_the_turn_and_its_per_day_outcomes_were_not_rendered`), `error: "<Class>:
   <message>"`, and -- on a default-window turn -- the same eight `ceiling_*` keys every other
   status carries. **Exit 1**, through `exit_code_for` like every other status rather than a
   hand-written `return 1`. It was the only terminal status with no `reason` and no ceiling fields,
   which is shape drift for any log consumer keying on either (STYLE-REVIEW-W10 S2/N3, fixed
   2026-09-19). Still the shape that rolled this lane back twice; STYLE-REVIEW-W9 S2 (render the days
   already committed before the exception on this path) remains OPEN, and the reason string says so
   rather than implying the turn had no days.
2. `registration_refused` -- at least one evaluated day reached the register verb and was refused.
   **Exit 1**, and it DOMINATES every other in-turn outcome: a refusal is a defect in the day it
   names (an unregistered lattice cell, an empty or duplicated partition, a non-finite value), not a
   governed outcome the way an absence is. A mixed turn that promoted one day and was refused on
   another therefore may not report `completed` and exit 0 — nor may it be folded into
   `no_days_promoted`, whose name would then be false for exactly that turn. `registration_refused_days`
   names every refused day at the top level, beside `absent_days`, `not_yet_indexed_days` and
   `not_servable_days`. It also dominates `stale_ceiling`: a refusal names a specific defect in a
   specific day an operator must fix, while staleness is a property of the lane that
   `ceiling_is_stale` reports on the same line whichever status won.
3. `stale_ceiling` -- decided in `main()` AFTER the window ran, on a default-window turn only. The
   newest servable day is `VEGETATION_PROMOTION_STALE_CEILING_DAYS` (21) or more days behind today, so the window is not progress even though its days were promoted.
   **Exit 1**, with the turn's own outcome preserved as BOTH `promotion_status` and
   `promotion_reason`. It does not re-state a `failed` turn: that would bury the `error` behind a
   freshness verdict measured from a ceiling the turn may never have read.
4. `completed` -- at least one day promoted or confirmed unchanged, and none was refused. Exit 0,
   with `reason: "at_least_one_day_was_promoted_or_confirmed_unchanged"` -- stated rather than
   omitted so `reason` is a key of every terminal report and not only of the red ones.
5. `waiting_for_writer` -- EVERY evaluated day is one the writer has not finished: `not_yet_indexed`
   (`reason: "forward_writer_has_indexed_none_of_these_days"`) or `not_servable` (`reason:
   "forward_writer_has_published_none_of_these_days_at_every_required_rung"`). Exit 0, logged once
   per turn. This is the steady state of the lane as configured (writer not started, `--max-days`
   1), and exiting non-zero for it would page every turn, indefinitely, for a lane behaving exactly
   as intended. It is deliberately not `completed`: nothing was promoted, and the report says so.
6. `no_days_promoted` -- anything else with no progress and no refusal: `reason: "all_days_absent"`
   when every requested day was a governed absence, otherwise `no_indexed_day_promoted` — which is
   also the EMPTY-WINDOW outcome, since an index with no servable day yields no days to evaluate.
   **Exit 1**, so a scheduled lane cannot succeed vacuously against days that should have been there.

**One shape across all six.** Every terminal report carries the same core keys --
`vegetation_partition_promotion.TERMINAL_REPORT_CORE_KEYS`: `status`, `reason`, `days`,
`absent_days`, `not_yet_indexed_days`, `not_servable_days`, `registration_refused_days`. Three
statuses ADD to that set and none omits from it: `failed` adds `error`, `stale_ceiling` adds
`promotion_status`/`promotion_reason`, and any default-window turn adds the eight `ceiling_*` keys.
The half that ENFORCES this is
`tests/execution/test_vegetation_partition_promotion.py::test_every_terminal_status_reports_the_same_core_keys`,
which builds one report per status and compares key sets; the sentence above is a summary of that
test, not a claim standing on its own (STYLE-REVIEW-W10's process rule, and W10 S2/N3 is what it
closes).

**One day, one transaction.** Nothing in this path used to commit, and `local_source_loader_session`
closes without committing — so an uncommitted turn rolled every governed release back at session
close while the object store kept a promotion receipt the NEXT turn reads as `unchanged`: green
forever against a plane holding nothing. The turn now commits after each promoted day and BEFORE its
receipt is written, and rolls back on a refusal. Both are required together: `advisory_lock` is
`pg_advisory_xact_lock`, so a refusal that left its transaction open would carry both the poisoned
transaction and the publication barrier into every remaining day of the turn.

## Lane catalogue

`lane_catalogue.py` (config-driven ingestion spec §4.4 "Catalogue bridge"; CA1-CA3, CA8, CA12, CA13) puts
every executor definition on exactly ONE path. `LaneCatalogue` is rebuilt from `LANE_SPECS` at call time
on every tick and every handler call (tests rebind `LANE_SPECS`), over lane TOMLs parsed once per process
(`load_config_lanes`, cached by directory and region: the directory is baked into the image, S13).

- **Config wins.** A lane TOML with `executor = "config"` takes its id off the legacy path; its legacy
  spec, if any, is skipped. `executor = "legacy"` leaves the lane where it was, and names a legacy spec or
  is quarantined. **No id is on both paths**:
  `tests/execution/test_lane_catalogue.py::test_every_real_lane_toml_puts_its_lane_on_exactly_one_path`
  pins the legacy id set LITERALLY and DERIVES the config id set from the real `lanes/`, so adding a
  TOML, disabled or not, needs no test edit.
- **A config lane's definitions** are built by `config_lane_spec`: the forward definition keeps the lane
  id, so its definition name and `EXECUTOR_DEFINITION_VERSION` are the legacy lane's own (CA13: a flip
  resumes the same ledger checkpoint and the same brake; no version bump). A `gap_fill_cron` adds
  `<lane>:gap-fill` (backlog class, coalesced), declared even while `gap_fill_enabled = false` so the
  brake can name it. Each runs the runner CLI, `python -m agri_data_service.pipeline.runner --lane <id>
  --mode forward|gap-fill|transform` (`RUNNER_COMMAND`), with the TOML's `turn_timeout_seconds` plus
  `COMMAND_CLEANUP_MARGIN_SECONDS` as its command timeout.
- **Config gates (CA3)**: the TOML's `enabled` (and `gap_fill_enabled`), the catalogue itself and the CA12
  kill-switch, never `ACTIVE_LANES` and never `LANE_SPECS`. A config lane that declares
  `conflicts_with` a lane that also dispatches waits (`config_conflict`): the incumbent keeps running.
- **Registration (CA2)**: a config definition registers through the tick's own
  `_load_or_register_definition` (`sql/execution/insert_definition.sql`, `ON CONFLICT DO NOTHING`, with
  `read_lane_pause_state` honoured), never `jobs/worker.py::ensure_job_definition`, whose upsert would
  un-pause a braked lane. `jobs-set-lane-enabled` (`job_lane_control.py::resolve_definition`) and
  `jobs-supersede-run` (`job_run_supersession.py::resolve_executor_lane`) resolve through the catalogue,
  so a config lane and its `:gap-fill` are braked and released like a legacy lane.
- **Shape sync (review M1).** Because the insert never updates, a same-version spec change (soil's
  six-hourly schedule; a CA13 cut-over) would never reach the stored row, and `/admin/jobs` computes
  `nextFireAt` from that row's `schedule`. `_sync_definition_shape`
  (`sql/execution/update_definition_shape.sql`) therefore rewrites a stored definition's `schedule`,
  `schedule_timezone` and `parameters` once per process per definition, only when they differ, and never
  names `enabled`. The runtime limits (`max_attempts`, `lease_seconds`, `time_budget_seconds`,
  `retry_policy`) are rewritten only for a config lane, whose TOML owns them; a legacy lane keeps its
  stored limits, since changing those has always meant a new definition version. The executor schedules
  from its spec, never from the stored `schedule`, so the sync changes what operators see, not when lanes run.
- **The CA1 marker.** A config work item's payload is `{lane_id: <owning lane>, scheduled_for,
  executor: "config", mode}` and its run's `target_partitions` carry `executor: "config"`
  (`LaneExecutionSpec.work_item_payload`, `run_target_partitions`); a legacy row is byte-identical to
  before. `run_scheduled_command` routes a marked item, or one naming a config lane, to
  `_resolve_config_turn`; an unmarked item left open across a cut-over names its definition id and is
  resolved by it.
- **Quarantine (S8).** A lane TOML that fails its invariants runs on NEITHER path (we cannot know which
  executor it meant), and a lanes directory that cannot load at all leaves the legacy path running alone
  with one `plantgeo_job_executor_lane_catalogue_unloaded` error per process. **Except a cut-over lane**
  (review M2): `lane_catalogue.CUT_OVER_LANE_IDS` lists every legacy id a config TOML has taken over, and on
  a load failure those run on neither path, so a packaging fault never restarts a retired legacy writer
  beside its config successor. A CA13 cut-over appends its id in the same diff;
  `test_the_cut_over_list_names_every_real_config_lane_that_took_a_legacy_id` pins the list to `lanes/`.
  A static lookup or a transform that declares a `gap_fill_cron` is quarantined too: the runner has no
  gap-fill turn for it (review L4).
- **The kill-switch names repairs too (L8).** `<lane>:gap-repair` is a known id: naming it stops that
  lane's repair driving and leaves its forward running; naming the lane stops both.
- **Legacy repair stays legacy (CA8)**: see "Bounded gap repair".
- `--inventory` lists legacy rows, then config definitions with `not_dispatched_because`, then quarantined
  lane TOMLs; `activation_variables` names both the allow-list and the kill-switch.

A never-run config definition waits for its first cron fire after this process first planned it
(`_NEVER_RUN_FIRST_SEEN`), instead of opening the fire it was registered after (executor F8c). The map is
process-held: a restart only makes such a lane wait one more fire, the conservative direction.

## Cron schedule

`cron_schedule.py` (spec S9, D6) is the next-fire clock for config lanes only; legacy lanes keep their
cadence and phase until they flip. The GRAMMAR is `foundation/lane_config/cron.py::parse_cron`, the one
parser the loader validates a TOML with, so the loader and the executor can never read a cron
differently. Due = the latest fire at or before now (`latest_fire_at_or_before`); a replayed lane opens
the fire after its last bucket (`next_fire_after`); `coalesce_latest` and `replay_oldest` keep their
meaning through `lane_scheduling.py::next_scheduled_bucket`. The day rule is vixie cron's: when both day
fields are restricted either may match, and a field starting with `*` (even `*/2`) makes both required.
Every instant is UTC; a zoned instant is converted, a naive one refused. A grammatical cron that names no
real minute (`0 0 31 2 *`) raises `CronNeverFiresError` after an eight-year search (it covers the skipped
2100 leap day), which `lane_scheduling.py::_cron_fire` turns into that one lane's
`ExecutorConfigurationError`, so it is the lane's `plan_failed`, never a hang or a tick fault.

## Durable execution

The executor uses the `agri.job_*` tables for definitions, logical runs, work items, attempts,
checkpoints, events, incidents, and outbox records. PostgreSQL advisory locking elects one scheduler
leader. Logical cadence buckets remain stable across restarts; incremental lanes coalesce downtime
to the current bucket and backlog lanes replay their oldest owed bucket.

A failed or partial bucket remains held according to its catch-up policy. Operators release a held
run with `agri-service ops jobs-supersede-run`; the resulting incident is the durable audit record.
Since G1 the hold ladder also re-tries a held lane by itself (see "Holds and probes"). The `blockers`
field in tick output carries activation, executability, and operator-supersession requirements.

## Work queue

Spec S15/S16, FR-7 (config-driven ingestion Phase 1D, `f1-executor`). `LaneDispatcher` is the work queue:
the leader tick plans every lane on its own session exactly as before, opens each due lane's run there
(`_dispatch_due_lanes`), and hands the lane to the queue WITHOUT awaiting it. Each lane then runs as its
own asyncio task on its OWN `AsyncSession` (`_drive_dispatched_lane`), takes a session-level advisory lock
on its definition (`jobs/lease.py::try_definition_lock`, key `plantgeo:executor-definition:<name>`) and
drives `jobs/worker.py::run_job_slice`. The next tick `collect`s what finished and folds each verdict into
its incidents on the leader session, so every Wave O write still happens on the leader, inside its
savepoints.

- **A lane never runs twice.** A definition already in flight is reported `running` and not dispatched
  again; the per-definition lock keeps that true across executor processes, and the fenced lease on the
  work item is the last line underneath both.
- **Slots.** At most `PLANTGEO_JOB_EXECUTOR_MAX_CONCURRENT_LANES` lanes run at once (default **1** at G1;
  raising it is G6's, with the Phase-2 RSS evidence). A due lane with no free slot is
  `deferred_fairness` and waits for the next tick. Each slot owns one single-connection pool
  (`_lane_session_slots`), so N lanes hold N connections and never the leader's.
  `max_lanes_per_tick` is retired from the queue; it bounds only the serial dispatcher.
- **Backlog gets its turn (review PH1a).** One slot takes only the head of `fair_due_order`, so the queue
  alternates which class leads (`LaneDispatcher.next_lead`: the class NOT dispatched last). Without it every
  `:gap-repair` (backlog) waited while any forward was due; the serial dispatcher still leads with
  `incremental` and runs one of each class per tick. A forward still RUNNING counts as due for its lane's
  repair planning, so at two or more slots a lane's forward and its repair never run together (review PM3).
- **Daily upkeep waits for an idle queue (review PH1c).** `job-logs-maintain` holds `ACCESS EXCLUSIVE` on the
  default job-event partition; the day's pass runs on the first tick with no lane in flight, so a running
  lane's slice-end event write never queues behind it.
- **Only leader loss cancels lane tasks (review H4).** A tick that finds another leader, or whose leader lock
  could not be released (`ExecutorLeaderUnlockError`: a lost backend may still hold it), calls
  `LaneDispatcher.cancel_all`; the worker hands the shard in hand back
  (`jobs/worker.py::_release_after_cancellation`) and the child is stopped. Any OTHER tick fault, a planning
  `SQLAlchemyError` included (a single statement timeout re-raises from `_isolate_plan_fault`), leaves running
  lanes alone: each holds its own definition lock and fenced lease, and leadership is given up at the end of
  every tick anyway. Cancelling there would SIGTERM a paid fetch mid-turn and re-spend it.
  A stopping service drains in-flight lanes for `LANE_DRAIN_SECONDS`, then cancels the rest.
- **Deviation, stated.** The leader lock is still taken and released per tick on a fresh connection (as
  at HEAD); a lane task outlives the tick because its own definition lock and the fenced lease, not the
  leader lock, are what make its turn exclusive. `--once` is always one serial tick, so nothing it
  starts outlives the process.
- **S16 switches.** `PLANTGEO_JOB_EXECUTOR_DISPATCH=queue|serial` (unset `queue`; a garbled value is
  `serial`, HEAD's in-tick await, with one `config_fallback` warning). `run_executor_tick(dispatcher=None)`
  IS the serial path, byte for byte, so every pre-queue test still runs it.

Tests: `tests/execution/test_work_queue.py` (fake children through the real handler, fake lane sessions).

## Command lifecycle

Commands run as a direct child (`asyncio.create_subprocess_exec`, no `start_new_session`, so NOT in their
own process group: `_stop_process` terminates and then kills the child itself, and a grandchild that
outlives it is not signalled) with bounded timeouts, heartbeat updates, graceful termination, and forced
cleanup as the final fallback. Shutdown stops new launches and waits for the active command boundary
before releasing leadership. Never restore a deleted scheduler service
as rollback; remove a lane from the active allow-list or pause its durable definition instead.

## Command stderr reaches the ledger

`run_scheduled_command` pipes BOTH the child's stdout and stderr (`stdout=`/`stderr=asyncio.subprocess.PIPE`),
drains each concurrently through its own `CommandOutputTail` (`CommandStderrTail` is the same class, kept
under its original name), and keeps a bounded TAIL of each (`COMMAND_STDERR_TAIL_BYTES`,
`COMMAND_STDOUT_TAIL_BYTES`). Every failure reason -- non-zero exit, timeout, fence lost -- carries the
STDERR tail after the headline, so `agri.job_attempt.last_error_summary` states the child's actual
exception rather than only `command exited with status 1`. The sensors incident of 2026-09-12 needed log
archaeology for exactly that reason. The tail is content and therefore travels ONLY through `reason`,
which `jobs.lease.fail_work_item` redacts and clamps; `metrics` carries counts alone (`stderr_bytes`,
`stderr_truncated`, `stdout_bytes`, `stdout_truncated`) because metrics are stored unredacted. The drain
is bounded by `COMMAND_STDERR_DRAIN_SECONDS` after exit so a grandchild holding the pipe cannot hold the
attempt.

**The run row carries it too** (config-driven ingestion spec §4.4 "Ledger detail"; D6).
`sql/jobs/refresh_job_run_rollup.sql` writes `job_run.last_error_summary` in the same statement as the
run's status: the newest `last_error_summary` of a work item that has NOT succeeded, or NULL once every
errored item recovered. Nothing wrote that column before, so `/admin/jobs` read an always-empty field.
The text is the work item's, already redacted and clamped by `jobs.lease.fail_work_item`. Rolls back by
revert (S16: a pure fix).

**o5a (Wave O, GL-3): the `ChildLogRouter` tee sits IN FRONT OF the sinks, not behind them.** Each
`CommandOutputTail`'s sink is no longer a raw passthrough to this process's own stdout/stderr; it is a
closure that feeds a per-attempt `foundation.observability.router.ChildLogRouter` (`attempt_id=
str(turn_id)`), and the router's OWN sinks (`router.py::_default_stdout_sink`/`_default_stderr_sink`) are
what Railway now actually sees: reassembled, bounded, leveled and redacted JSON, per
`foundation/observability/AGENTS.md` "Child log router". This is a real, DELIBERATE change from "the
Railway stream is unchanged" (the pre-Wave-O claim above) -- the whole point of GL-1/GL-3 is that the
executor's log stream stops being raw child chatter and becomes one first-party JSON line per event
(FR-30). What protects the ledger from that change:
- **The raw tail is still raw, and it is fed FIRST.** `CommandOutputTail.feed` extends the bounded tail
  (read by `_command_failure_reason` and `parse_terminal_report`) before it calls the sink, so a router
  ceiling drop or a router fault can never cost the ledger the real exception or the report (see
  `tests/execution/test_command_stderr_capture.py::test_a_failing_command_s_real_exception_reaches_the_failure_reason`,
  whose `NOISE_LINES=200` is chosen to exactly fill the router's own `_MAX_ERROR_LINES_PER_ATTEMPT`
  ceiling and still leaves `outcome.reason` intact).
- **The tee is fail-open end to end** (Wave O GL-3 review H2; owner intent: no run stops permanently
  or breaks another lane). A sink that raises is counted (`CommandOutputTail.sink_failures`) and warned
  about once, never raised -- raising would kill `_drain_stream`, leave that pipe unread and block the
  child until the monitor misreports a `hang`. The router itself is `_TurnLogRouter`, a
  `ChildLogRouter` whose `feed`/`flush`/`usage_summary` are each guarded: a fault (a broken log stream
  reached through the router's unguarded 64 KiB stub path, say) is counted into
  `metrics.log_router_faults` and warned about once (`plantgeo_job_executor_log_router_failed`), and
  `usage_summary` falls back to "incomplete, no outcome". `_drain_both` gathers with
  `return_exceptions=True`, so one stream's reader fault never stops the other stream draining; each
  fault is warned about. The one `lane_turn` log call is guarded too: the audit line's own fault never
  fails the turn it describes. Pinned by
  `tests/execution/test_child_log_router_wiring.py::test_a_raising_router_leaves_the_raw_tail_and_report_intact`.
- After `_finish_drain`, `router.flush()` forces out any still-buffered partial line (no trailing `\n`)
  as a `plantgeo_child_output` stub before the attempt reads `router.log_lines_dropped`/
  `router.usage_summary()`.
- **The failure reason is redacted before it leaves the executor.** `CommandOutputTail.summary()`
  runs `redaction.redact_for_log` on every line before joining and front-cutting, so a cut can never
  strip the `Bearer`/`Authorization` word a pattern needs while leaving the credential behind, and a
  `[SQL: ` cut ends with its own line instead of swallowing the exception after it.
  `jobs.lease.fail_work_item`'s own `redact_strict` + clamp still runs afterwards; on its own it never
  caught a bearer token or a dict-repr'd header.

**The router is filled from THIS turn's context** (review M1). `ChildLogRouter._fill_turn_context`
reads `os.environ`, which in the executor is the EXECUTOR's environment: the turn keys exist only in
the CHILD's. `_TurnLogRouter` overrides it with the same values `_turn_context` hands the child, so a
JSON line the child printed without stamping itself (a `pipeline/direct/*` report, an R2
`emit("<lane>_forward_failed", ...)` line) still carries `turn_id`/`lane`/`mode`/`attempt`/`shard_key`.
`_ROUTER_TURN_FIELDS` duplicates `router.py::_TURN_ENV_TO_FIELD` under that module's own sibling-copy
rule. The permanent home is a `turn_context=` constructor argument on `ChildLogRouter` itself
(`foundation/observability/`, outside this directory); this override is the executor-side stand-in.

**Turn context reaches the child as environment, not argv** (design Sec 1.2): `run_scheduled_command`
makes a uuid4 `turn_id` its FIRST act -- before any validation, so even a refusal that never spawns a
process carries one -- and passes `PLANTGEO_TURN_ID`, `PLANTGEO_LANE_ID`, `PLANTGEO_TURN_MODE`
(`forward`/`repair`), `PLANTGEO_ATTEMPT` and `PLANTGEO_TURN_BUCKET` to the child
(`_turn_context`); `PLANTGEO_TURN_ID` alone is what arms `foundation.observability.bootstrap
.arm_from_environment` in the child. A spawn that raises `OSError` still logs its one `config`
`lane_turn` (`spawned=false`, `spawn_error`) and then re-raises, so the worker's own failure path and
failure class are unchanged. `metrics.spawned` is `False` on every return before
`create_subprocess_exec` and `True` immediately after -- a local variable, never inferred from a report,
because the worker's own merge (`{**metrics, **outcome.metrics}` in `jobs/worker.py::_invoke_handler`)
means only THIS call's own stamp is trustworthy.

**Exit classification is observational only at GL-3** (spec Sec 4.9.3, FR-33): every terminal return
(pre-spawn failures, a shutdown/no-budget yield, `fence_lost`, a timeout, and every post-spawn exit code)
calls `execution.exit_classes.classify_exit` (or, for the two yields, stamps `"interrupted"` directly --
`classify_exit` has no path to that class) and logs exactly one `plantgeo_job_executor_lane_turn` line
(`_emit_lane_turn`/`_finish_lane_turn`/`_pre_spawn_failure`/`_interrupted_outcome`). The bootstrap
`progressed` return (the very first call, which only advances the cursor to `state=ready`) is NOT
terminal and gets no `exit_class` or `lane_turn` line -- it has validated everything but attempted
nothing yet. `_LANE_EXIT_CLASSES` remembers the newest class per lane IN THIS PROCESS so
`announce_operator_actions` can name it on `plantgeo_job_executor_operator_action_required` without a
database read (o2b's `select_run_final_attempt.sql` is GL-5's job); nothing here holds a lane, skips a
repair, or opens an incident -- that starts at GL-5/GL-6.

**The `lane_turn` line is emitted through the CONFIGURED pipeline** (review H1). `_emit_lane_turn` looks
the structlog method up by NAME on `logger` at emit time (`_LANE_TURN_LOG_METHODS`), never through a
map of bound methods built at import: `agri-service ops jobs-executor` imports this module (via
`interface/cli/ops.py`) before the CLI root calls `configure_logging`, and a method read at import is
bound to structlog's unconfigured default -- console text, no `level` field Railway reads, no
`service`/`deploy` envelope, no redaction. Pinned by
`tests/execution/test_tick_volume.py::test_a_lane_turn_after_configure_logging_is_json_with_a_level`.
A non-`ok` post-spawn `lane_turn` also carries `stderr_tail`, the redacted tail summary bounded to
`LANE_TURN_STDERR_SUMMARY_CHARS` (review L1): the router's error ceiling can drop the final traceback
from the mirror and the ledger reason never reaches the log stream, so this is where the logs say WHY.

**Tick volume** (design Sec 1.4): `tick_started`/`leader_*`/`tick_healthy` log at debug; the JSON tick
summary echoes on a `(lane, state, run)` change or hourly (`_tick_signature`,
`TICK_SUMMARY_HEARTBEAT_SECONDS`); `tick_unhealthy` prints whenever the unhealthy SET -- failing lanes,
incomplete lanes, operator commands -- changes (`_UnhealthyEdge`, review M2), not only on the
healthy->unhealthy edge, so a new failure behind a standing hold is never hidden by the one before it.

**The usage fold** (design Sec 2.2, spec Sec 4.9.2) reads EVERY `plantgeo_turn_usage` line back off the
RAW stdout tail (`_fold_turn_usage` -- `ChildLogRouter.usage_summary()` only exposes its own pairing
concern, `usage_complete` and the winning `last_send_outcome`, never the per-host `hosts` breakdown),
keeps each pid's last line, and sums host counters across pids (review M3): a turn is not always one
process -- `pipeline/direct/burn_severity/daily.py` spawns a `multiprocessing` child that inherits
`PLANTGEO_TURN_ID` and does the fetching, and the parent's own empty-hosts line is written LAST, so
"last line wins" charged that turn nothing. `cpu_seconds`/`meter_errors` sum; `rss_peak_kib` is the
largest single process. The turn's self-reported counters come off the parsed terminal report
(`_report_usage_fields`, with the legacy `requests_spent`/`rows`/`bytes`/`written_bytes` name map);
`metrics` is stored unredacted, so only finite numbers and a `probe_status` from `_PROBE_STATUSES`
cross (review L4) -- an unrecognised status still makes the turn `incomplete`, it is just not copied.
A return that never spawned (pre-spawn refusal, no-budget yield) carries
`usage={charged_basis: not_spawned, charged: 0, suspect: 0}` too (review L3), so GL-4's rollup never
reads a NULL basis for a row that carries `spawned`. `start_lag_seconds` is WALL-clock
(`READY_AT_CURSOR_KEY` on the bootstrap cursor, review M4): the cursor is persisted and the spawn may
run in a later process or on another host, so a monotonic stamp compared across processes was
meaningless. A lag below `-START_LAG_CLOCK_SKEW_SECONDS` or above `START_LAG_MAX_SECONDS` reads as
`None`; a small negative (clock skew) reads as 0.
`_charging_basis` computes the PER-ATTEMPT half of the spec's charging table (`metered`/`reported`/
`suspect`/`not_spawned`) under `usage.charged`/`usage.suspect`/`usage.charged_basis`; the metering EPOCH
and the running/pre-epoch exclusions are read-time concerns o4's SQL rollup applies on top (this fold
cannot know them -- they depend on every OTHER attempt too). Under `CHARGE_BASIS=logical` a reported
`weighted_calls` of 0 is a real figure, never "absent" (review L2). **Stated residuals:** a process
that dies without running `atexit` (SIGKILL, `os._exit`, a `fork` child) writes no usage line, so its
sends are missing from `hosts` and `usage_complete` reads false for its pid; the fold reads only the
64 KiB stdout tail, so a usage line pushed out of it by later output is lost too; and `usage_reported`
is `True` only when a usage line was actually seen on this attempt's stdout.

## Turn reports: an exit-0-but-incomplete turn is persisted

Every direct writer prints exactly ONE terminal JSON report on stdout, last, and since 2026-09-18 exits 0
when at least one day wrote while reporting `outcome=incomplete` with `days_unwritten` and
`unwritten=[{day, outcome, detail}]`. stdout is therefore piped and teed exactly like stderr
(`CommandOutputTail`, routed through `ChildLogRouter` since o5a -- see "Command stderr reaches the
ledger" above), and `parse_terminal_report` reads the bounded RAW tail (`COMMAND_STDOUT_TAIL_BYTES`),
never the routed copy. `summarize_turn_report` bounds it (`TURN_REPORT_UNWRITTEN_MAX` entries,
`TURN_REPORT_DETAIL_CHARS` per detail, nested per-product `results[].unwritten` folded in) and
`run_scheduled_command` writes it to the completed checkpoint cursor as `turn_report` and to metrics as
`days_unwritten`, whatever the exit status. The streak `consecutive_incomplete_buckets` is process-held
(`_LANE_TURN_REPORTS`, keyed by the DEFINITION that ran, so a repair turn counts separately from its
owning lane and never taints the hourly lane's own streak) and says so; the tick lifts every incomplete
lane to `ExecutorTickSummary.incomplete_lanes`, one `plantgeo_job_executor_lane_incomplete` WARNING per
tick that ran it, and the healthy/unhealthy tick line. A day stuck at `status=conflict` is now visible on
the checkpoint row and in the tick without log archaeology; it is still the lane's own contract that
decides what to do about it. Each `detail` is `redact_text`ed here because the cursor path canonicalises
but never redacts.

**o5a's report-parse upgrade** (design Sec 1.6 point 8): `parse_terminal_report` now prefers the LAST
`plantgeo_lane_turn_report` line (the future runner's own event name, G1); absent that -- every
`pipeline/direct/*` writer today -- it falls back to the last JSON object carrying no `level` key at
all, exactly as before. This matters because the runner will ALSO print ordinary leveled log lines on
the same stdout (through `foundation.observability`), interleaved with its one report: a usage line
(`plantgeo_turn_usage[_open]`) always carries a `level`, so the old "last JSON object, full stop" rule
would have needed to change the moment ANY other JSON line reached stdout. Two residual assumptions:
the drain forwards 4 KiB chunks, so an executor's own routed line can interleave with a child's report
in whatever the router forwards (the RAW tail copy this parser reads is intact regardless); and a
legacy writer that ever emitted a SECOND no-`level` JSON object after its report would still be
misread as the newer one (last-wins), unchanged from before o5a.

**Two unwritten shapes, one reader (S16).** `_unwritten_entries` reads a direct writer's
`{day, outcome, detail}` AND the runner's S5 `{day, stream, reason, detail, behind_edge}`
(`pipeline/runner/report.py::UnwrittenEntry`): an S5 entry keeps its `reason` (and `stream`), and the
reason stands in for the missing `outcome`, so the tick, the checkpoint and the incident all read one
word. The runner states `days_unwritten` itself, counting only days behind the provider edge (S5); when a
report omits it, the fallback counts the entries not marked `behind_edge: false`, so a day newer than the
edge -- unsettled by design -- never raises the incomplete alarm. A legacy entry is kept exactly as
before. Pinned both ways by
`tests/execution/test_command_stderr_capture.py::test_the_legacy_and_the_s5_unwritten_shapes_are_both_read`.

### Publication debt is the second, quieter half of an incomplete turn

`days_unwritten` only ever sees a day the writer REFUSED. A day whose four rungs landed in R2 but
whose availability pointer never extended is a different failure with the same exit status: the
objects exist, the turn reports `outcome=completed`, and nothing serves them. `summarize_turn_report`
therefore also sums the owed-work counters every direct writer already prints from
`AvailabilityExtensionTally.to_summary()` -- named in `PUBLICATION_DEBT_COUNTERS`, folded over nested
per-product `results[]` exactly as `unwritten` is -- into `TurnReport.publication_debt`, with the
non-zero counters kept beside it so an operator reads WHICH duty is owed.

`TurnReport.incomplete` is true for either kind, so debt reaches `incomplete_lanes`, the
`plantgeo_job_executor_lane_incomplete` WARNING, the lane blocker line and
`consecutive_incomplete_buckets` without a second surface. The counter list is spelled out rather
than derived by subtracting the two settled counters (`availability_extended`,
`availability_skipped_unchanged`), so a NEW settled counter cannot silently read as debt. This is a
report reader: it does not open the object store, and a lane that prints no availability summary
simply carries zero debt rather than an assumed one.

**Owner decision 2026-09-27:** the debt gauges are meant to drive `TurnReport.incomplete` -- that is
the point of this whole subsection, not a side effect to walk back. The executor logs one
`plantgeo_job_executor_lane_incomplete` WARNING per incomplete lane per tick, and
`consecutive_incomplete_buckets` climbs for as long as the gauge stays non-zero, resetting only on a
clean turn or a process restart. This is reporting only: `ExecutorTickSummary.failed` (and so the
`--once` exit code) looks solely at `lane.state == "failed"`, never at `incomplete_lanes`; nothing here
feeds a breaker or `RepairAuthoringClock`, which schedules off measured coverage gaps, not off this
streak. In executor lanes the two counters that actually stand are `availability_quarantined_standing`
(a GAUGE restated whole by each retry sweep, `availability_extension.py`'s
`AvailabilityExtensionTally.quarantined_standing`) and `availability_not_bootstrapped` (owed per written
day until an operator bootstraps the index). `availability_reindex_owed` cannot fire here: the only
place that sets it is `pipeline/parquet/gap_fill_progress.py`, and generic `parquet-*` gap-fill and
drain schedules were removed from the executor catalog on 2026-09-12 (`execution/lane_specs.py`), so it
is inert for direct writers -- carried in `PUBLICATION_DEBT_COUNTERS` only in case a future lane wires
it back in.

## Operator action surface

**The printed commands run as printed** (D6, executor F5). `lane_scheduling.py::supersession_command`
prints two complete lines, the dry run (`LaneTickResult.operator_action`, `OperatorAction.command`) and the
recording (`operator_apply_action`, `apply_command`, the same line plus `--apply`):

    railway ssh --service plantgeo-job-executor -- agri-service ops jobs-supersede-run \
        --lane <lane> --run-id <run> --evidence reviewed-hold:lane=<lane>,run=<run> --operator "$(whoami)"

The `railway ssh` prefix runs the verb inside the executor, the one place its DSN, allow-list and lane
TOMLs line up (the verb refused from a laptop whose environment lacked the allow-list). `--evidence` is
pre-filled with one whitespace-free token, so a remote re-split cannot break it; `--operator` is a command
substitution the operator's own shell expands. Before this the line carried only `--lane` and `--run-id`
and failed with click's `Missing option '--evidence'`.
`tests/execution/test_operator_action_surface.py::test_the_printed_dry_run_and_apply_lines_parse_with_the_verb_s_own_parser`
parses both lines through the installed `agri-service` root. The verb resolves `--lane` through the lane
catalogue, so a config lane and its `<lane>:gap-fill` are released the same way (CA2), gated only by its
TOML and the kill-switch (CA3).

A lane held behind a recorded-supersession requirement is still reported on every tick, but now in
three places rather than buried in a `blockers` string: `LaneTickResult.operator_action` (typed), the
tick summary's top-level `operator_actions`, and one `plantgeo_job_executor_operator_action_required`
ERROR event per held run per process (`announce_operator_actions`), with a matching `_cleared` event
once the run is superseded. The `plantgeo_job_executor_tick_unhealthy` line (printed whenever the
unhealthy set changes, `_UnhealthyEdge`) also names the commands. This is deliberately NOT an alerting system. The durable surface this state belongs on is an
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
  Every S18 config stream (`pipeline/parquet/config_stream_registrations.py::CONFIG_STREAM_ROWS`) joins
  it by derivation (`CONFIG_STREAM_EXCLUSION`): its lane's own TOML gap-fill repairs it (CA8).
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
- **Every authorized candidate persists** (FR-10, executor F1). `author_gap_repairs` resolves EVERY
  repair definition first and only then opens the runs. `ensure_lane_definition` ends by rolling the
  planning transaction back (`_load_or_register_definition`), so resolving the next candidate's
  definition between two `open_job_run` calls discarded the previous run while its receipt still said
  `authored`: one logical key got two run ids in one pass (2026-09-26 11:08), and only the last
  candidate of every pass ever ran. Pinned by
  `tests/execution/test_gap_repair.py::test_an_applied_pass_persists_every_authorized_candidate`, which
  drives the REAL `ensure_lane_definition` against a session fake that honours rollback.
- **Legacy repair never touches a config lane** (CA8). Authoring (the tick's `_author_due_repairs` and
  the `jobs-plan-gap-repair` verb) reads `LaneCatalogue.legacy_activation`, so a config lane is
  `lane_inactive`; driving (`_plan_repair_runs`) skips an id outside the catalogue's legacy path even
  with an open `:gap-repair` run; and `run_scheduled_command` refuses a repair item for a config lane as
  `invalid_repair_request` (the item is wrong, not the lane, so the repair breaker never counts it).

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

## Soft failure

Wave O GL-5 (`o5b`; spec §4.9.3; plan `config_driven_ingestion_20260926` 0W.5). The owner's rule: "we
dont want runs to stop in a way that they break permenantly or break other lanes". At GL-5 the
executor RECORDS every failure streak on one `agri.job_incident` row and ACTS in exactly two ways
(repair withholding and the repair breaker). At GL-5 a hold was released only by an operator
(`jobs-supersede-run`, or `jobs-set-lane-enabled --disabled`); since G1 the ladder probes it too (see
"Holds and probes"). The row
helpers, `reconcile` and the breaker ladder are `lane_incidents.py` (`o2b`); the wiring is
`job_executor_service.py`.

**Nothing malformed stops the process.** `parse_activation` quarantines an unknown, non-executable or
conflict-losing allow-list id (`ActivationConfig.quarantined`); since Phase 1 a lane TOML that fails its
invariants and an unknown kill-switch id are quarantined by the same rule (`quarantine_sources`), each
incident naming its source in `detail.variable`. `ExecutorSettings.from_environment`
parses every numeric tunable (poll, lanes per tick, repair interval). A garbled value falls back to its
default with one `config_fallback` warning. A positive lanes-per-tick below the fairness floor clamps to
it. Each fallback opens `executor_config:<VAR>` on the first leader tick. `lane_quarantined:<lane>` opens
the same way. Both resolve on the first tick of a deployment without the fault. The remaining startup
exits are a missing DSN and a `Settings()` validation error (spec §4.9.6).

**The switch.** `PLANTGEO_JOB_EXECUTOR_SOFT_FAILURE` is on when unset or blank. An on-synonym keeps it
on; an off-synonym or a garbled value is OFF. OFF is HEAD planning byte for byte: no repair withholding
and no repair breaker. Incidents are still written. `run_executor_tick(soft_failure=None)` is also HEAD's
tick, and every pre-GL-5 test calls it that way.

**One read, savepoints, and who re-raises.** `_SoftFailureTick.open` reads `select_lane_incidents.sql`
once per tick. Every write is one `lane_incidents.py` helper, which opens its own
`session.begin_nested()`. `_SoftFailureTick._guard` catches the `SQLAlchemyError`. The savepoint has
already rolled back, so HEAD's work in the same transaction survives. `incident_write_failed` logs once
per transition (`SoftFailureState.failing_statements`). The lane it concerned is marked degraded and
plans as HEAD. Only `_pinned_connection_invalidated(session)` re-raises: that keeps today's tick-level
backoff. A failed READ degrades the whole tick to HEAD. Each lane's step ends in its own commit
(`_isolated`), because HEAD's planning helpers roll the transaction back between lanes.

**Isolation.** `_plan_active_lanes` plans each lane through `_plan_lane` inside its own `try`, and
`_plan_repair_runs` does the same through `_plan_repair_lane`. A non-SQL exception becomes that lane's
`failed` result with detail `plan_failed: <ErrorType>` (`_isolate_plan_fault`), and the loop moves on.
A `SQLAlchemyError` from HEAD's own statements still re-raises, as before. `lane_plan_failed:<lane>` is
bumped from those results and from degraded lanes; it escalates at 3 ticks and a clean pass resolves it.
`tick_partial` logs what the tick settled before any re-raise.

**Incidents** (a streak's escalation is the row's severity, so a restart never logs a crossing twice):

| fingerprint | opened by | escalates | resolved by |
|---|---|---|---|
| `lane_hold:<lane>` | `_LanePlan.operator_held` with no open row; class from `select_run_final_attempt.sql` | chain over 72 h: error, `hold_chronic` once, and the lane in `chronic_holds` on the hourly heartbeat | `reconcile`: `released_by=operator` or `reconciled` |
| `lane_report_missing:<lane>` | an exit-0 turn without a report | error at 3 | the next turn with a report, or `lane_inactive` |
| `lane_incomplete:<lane>` | an `ok`/`incomplete` turn (reason `unwritten`, `publication_debt`, `probe_gated`) | `detail.state` steps at 6 h, 24 h and 72 h | the first complete turn, or `lane_inactive` |
| `lane_blocked:<lane>` | a `failed` blocked-open-run or prior-version result | none | the state clears, or `lane_inactive` |
| `executor_lease_lost:<lane>` / `:fleet` | a `lease_lost` turn, or every dispatched lane lost in one tick | error at 3 | a turn that kept its lease; `:<lane>` also `lane_inactive` |
| `lane_repair_failing:<lane>` | a repair run that SETTLED failed (never `invalid_repair_request`) | the repair breaker | a repair that succeeds, `lane_inactive`, or `repair_quiet` |
| `executor_repair_authoring` | `_author_due_repairs` returned `None` | error at 2 intervals | the next good pass, or `authoring_disabled` (no repair clock) |
| `fleet:<exit_class>` | 3 or more lanes opening holds of one class within 1 h | one error | no open hold of that class remains |
| `budget_deferred:<pool>` (G1) | a lane's first admission refusal of the UTC day | none | a tick that admits a turn on the pool and refuses none |
| `budget_basis_suspect:<pool>` (G1) | suspect > 10 % of charged month to date | none | the share falls below 10 %, or the UTC month rolls over |

**No row is left without an exit** (`_SoftFailureTick._retirable_rows`, run in `after_planning`).
The lane-scoped kinds in `_INACTIVE_RETIRED_KINDS` clear only when their lane plans or runs a turn, so
once the lane leaves the allow-list or is quarantined they resolve as `lane_inactive` (a hold is paused
instead: it stays operator-only). A `lane_repair_failing` row with no settled repair failure for
`REPAIR_FAILING_QUIET_PERIOD` (7 days) past the point its breaker admits runs again resolves as
`repair_quiet`: the gaps closed and no repair will come to clear it. With no repair clock
(`PLANTGEO_JOB_EXECUTOR_REPAIR_INTERVAL_SECONDS` 0), an open `executor_repair_authoring` resolves as
`authoring_disabled`.

**The hold is recorded and reconciled, never released.** A paused hold (definition disabled, or lane
outside the allow-list or quarantined) is excluded from escalation. A re-enabled hold returns to
`held`. A hold whose run changed while nobody watched resolves as `reconciled` and reopens on the new
run. `detail` follows the design record's shape (`state`, `exit_class`, `class_source`, `rung` 0,
`chain_first_seen_at`, `episodes_7d`). `select_lane_incidents.sql` extracts only four of those keys, so
two facts ride elsewhere. The class is re-read from the run's final attempt (memoised per run in
`SoftFailureState.hold_classes`). The "hold_chronic already logged" marker is `CHRONIC_SUMMARY_SUFFIX`
on the summary. A hold's class is one of `lane_incidents.HOLD_EXIT_CLASSES` (`upstream`, `infra`,
`code`, `hang`, `config`); a missing, lost or non-failure stamp (`ok`, `interrupted`, `lease_lost`,
`report_missing`) reads as `code`, and `class_source` says which.

The rename on resolve is `<fingerprint>:resolved:<id>`, built in `resolve_lane_incident.sql` from three
separate literals. A backslash-escaped `\:resolved:` literal is NOT unescaped by SQLAlchemy when the
word is followed by a colon, so the backslash would land in the row and break the flapping count
(`test_compiled_statement_sends_no_backslash_escape` pins it).

**Announcements are de-duplicated on the row.** Opening a hold logs
`plantgeo_job_executor_operator_action_required` with the final attempt's class. A hold that opened
paused (definition disabled) announces on its `resume` transition instead. While the row stays
open its key is in `SoftFailureState.durable_announcements`, and `announce_operator_actions(durable=…)`
never repeats it, across restarts too. When the row cannot be written, the per-process
`SoftFailureState.announced` set is HEAD's fallback.

**Repair withholding and the breaker** act only by narrowing the activation the repair planners see
(`_without_lanes`). `plan_gap_repairs` and `_plan_repair_runs` both read `active_lanes`, so neither
changed. A lane is withheld while its hold row is open and its verdict is operator-held, or while its
breaker is cooling down. Each withheld repair lane shows as a `paused` result. Breaker state lives on
`lane_repair_failing`: `detail.state` is `counting:<n>` or `cooldown`, and `detail.rung` is the trip
count. The cooldown is `repair_breaker_cooldown_days(rung)` (1, 2, 4, then 7 days) after the tripping
upsert. Two consecutive code, hang or config settlements trip it. An upstream, infra or lease-lost
settlement breaks the run of failures but keeps the trip count. Once a cooldown lifts the lane gets ONE
run, and a code-class failure re-trips at the next rung.

**R3 lane keys.** `exit_classes.WRAPPER_EVIDENCE` is keyed by short layer names (`drought`), while the
executor passes lane ids (`drought-direct-forward`). Until GL-5 R3 could never match a production turn.
`run_scheduled_command` now maps the id through `_WRAPPER_EVIDENCE_KEYS` at the call site.

**No pool brake** (WQ-4): the paid Open-Meteo cap is G1's (`f1-config`, `f1-executor`); see "Budget admission".

**Acknowledged incidents stay quiet** (spec §4.9.3, from G1). An operator acknowledges an OPEN incident in
`/admin/jobs` (`src/lib/server/trpc/routers/jobs.ts::acknowledgeIncident`); the upsert never resets `status`, so
the acknowledgement survives every later bump. The executor then logs none of that row's escalation events
(`_acknowledged`: the streak escalation of `_bump_streak`, `hold_chronic`, the `lane_incomplete` step), while the
row's severity, the probes and the resolution rules carry on: acknowledging is not releasing.

Tests: `tests/execution/test_executor_resilience.py`, `test_hold_record.py`, `test_repair_withholding.py`,
and the GL-5 sweep proof `test_soft_failure_fault_injection.py`. All of them drive `run_executor_tick`
and the real handler with real child processes. They go through `tests/execution/soft_failure_fakes.py`,
an in-memory ledger that answers the four o2b statements with their SQL semantics.

## Holds and probes

Wave O GL-6, folded into `f1-executor` at G1 (spec §4.4 "Breaker split", §4.9.3 "G1 ladder"; WQ-1, WQ-2,
WQ-3, WQ-7; FR-8, FR-36). A held lane is re-tried by ONE single-attempt probe at a time, on a bounded
ladder, and released through probation. The pure rules are `lane_incidents.py` (`HoldLadders`,
`HoldProgress`, `probe_is_due`, `judge_probe`, `after_probe`, `judge_probation`, `watch_max_attempts`,
`PROGRESS_EVIDENCE`); the wiring is `_SoftFailureTick` (`ladder_gate`, `start_probe`, `shape_bucket`,
`_observe_ladder_hold`) plus `_plan_lane`'s `ladder` argument.

- **Ladders, by the hold's class.** `upstream`/`infra` (exit 75, or a legacy exit with R1-R4 evidence):
  1, 2, 4, 8, 16, 24 h, then daily. `code`/`hang`/`config` (70, 78, a timeout, a legacy exit without
  evidence): `PLANTGEO_JOB_EXECUTOR_CODE_PROBE_HOURS`, default `6,12,24`, then daily. Empty, or anything
  that is not a comma list of positive hours, is operator-only (a garbled value warns once and never
  probes faster than asked). The delay runs from the hold row's `last_seen_at`, the instant of its last
  transition; a probe needs `verdict.newer_bucket_exists` too, so a daily lane never probes more than
  daily. The chronic rewrite (once per chain, at 72 h) also bumps `last_seen_at`, so it can delay that
  rung's probe by up to one rung; stated, not fixed.
- **A probe** (`start_probe`, the `_release_by_process_start` pattern inside a savepoint): the hold row
  goes `probing` with the probe appended, the held run is superseded as `executor:probe`
  (`job_run_supersession.supersede_failed_run`), both commit together, and only then does the planner open
  the bucket with `max_attempts=1` (`DueLane.max_attempts` -> `_open_scheduled_run` ->
  `jobs/worker.py::open_job_run(max_attempts=...)`). A crash between the commit and the open re-opens the
  same probe next tick (`shape_bucket`: a `probing` hold whose target is superseded). The probe flag rides
  `DueLane.probe` -> `_execute_due_lane` -> the task's `_PROBE_TURN` context -> `_Turn.probe` ->
  `PLANTGEO_TURN_PROBE=1` in the child and `metrics.probe`. Any refusal or ledger fault rolls both writes
  back and the lane stays held (`plantgeo_job_executor_hold_probe_refused`).
- **Outcomes** are judged from the LEDGER on the next planning pass (`_observe_probe`), never from process
  memory, so a restart judges them the same: a succeeded probe run starts probation; a failed one moves the
  hold to the next rung on the SAME fingerprint and adopts the probe run's class (positive evidence); a run
  whose one attempt was lost, fenced out or interrupted is inconclusive: same rung, the class stays
  sticky (the row keeps its class run), and the fourth in a row counts as failed (`lost_repeatedly`). A
  probe that loses its fence parks (`yielded`) instead of failing.
- **Probation**: buckets run with `max_attempts=1` and repairs resume. Two conclusive clean buckets
  (turn `completed` and `PROGRESS_EVIDENCE` satisfied; `_observe_probation_turn`) resolve the hold
  `released_by=probe`. 48 h since the last change with no failed bucket resolves it as
  `probation_expired` and opens `lane_incomplete` with reason `inconclusive`, so a masked failure still
  escalates. A failed bucket restarts the count and the 48 h; the ledger holding the lane again re-holds
  it at the next rung. Probation is never chronic. Since G1 `lane_incomplete` clears only on a
  CONCLUSIVE complete turn (`_TurnVerdict.conclusive`), or the next unproven soil or climate turn would
  close the very `inconclusive` row expiry just opened; for every lane without a `PROGRESS_EVIDENCE` rule
  "conclusive" is "a report was present", so their clearing is unchanged.
- **Watch and chain.** For 24 h after a hold resolves, new buckets open with `max_attempts=2` (1 when the
  lane has 2 or more episodes in 7 days). A hold re-opened within 7 days inherits the prior episode's rung
  and `chain_first_seen_at`; the first episode that makes 3 in 7 days logs `hold_flapping` once (error).
- **Operator vs probe.** In `held` a superseded checkpoint is a person's release (resolve,
  `released_by=operator`); in `probing` it is the probe's own marker. The marker-aware streak
  (`select_latest_run.sql`, executor F4) tells them apart in the ledger too: a supersession whose owner
  starts with `executor:` never resets the failure streak, so a failed probe is operator-held again at
  once, while a PERSON's release earns a fresh streak (one failure after it does not re-hold).
- **Why the counters ride on `detail.state`.** `select_lane_incidents.sql` returns only `state`, `rung`,
  `chain_first_seen_at` and `episodes_7d` from `detail`, so a phase with a counter is written
  `<phase>:<n>` (`held:2` = two inconclusive probes, `probation:1` = one clean bucket), the repair
  breaker's `counting:<n>` idiom. The full design shape (`probes[]`, `clean_buckets`,
  `inconclusive_probes`, `next_probe_at`, `release`) is still written for `/admin/jobs`; `probes[]` is
  process-held and restarts empty after a restart.
- **No deploy probe (WQ-7).** `PLANTGEO_JOB_EXECUTOR_PROCESS_START_RELEASES_BREAKER` stays off. When an
  owner turns it on, the release folds into the ladder: recorded as a probe with `via: deploy`, run with one
  attempt, then probation.
- **Switches.** `PLANTGEO_JOB_EXECUTOR_BREAKER_MODE=split|legacy` (unset `split`; garbled `legacy`).
  `legacy`, `SOFT_FAILURE=off`, or a failed incident read this tick is GL-5's operator-only hold byte for
  byte (`SoftFailureState.ladders is None`); `legacy` also turns the marker-aware streak off. The two
  switches are independent (review PH2): `legacy` keeps GL-5's incident layer on, so it restores TODAY's
  production breaker; `SOFT_FAILURE=off` removes GL-5; both together are the pre-Wave-O executor. (Spec
  §4.9.3's "`BREAKER_MODE=legacy` subsumes `SOFT_FAILURE=off`" predates GL-5 going live; the coordinator
  records the amendment.)
  `SoftFailureState()` constructed bare keeps GL-5 behaviour; `SoftFailureState.for_process` reads the
  switches.
- **Native exits (S4).** A config lane's exit is read by `classify_exit(native=True)`: 75/70/78/2 as
  declared and any other non-zero code `code`, with no legacy evidence rules; a config turn that exits 0
  without its report fails as `report_missing` (a runner bug, so the code ladder).

Tests: `tests/execution/test_hold_ladder.py` (the pure rules), `test_hold_probes.py` (flows through
`run_executor_tick` with real children), `test_breaker_split.py`, and the probe cases of
`test_soft_failure_fault_injection.py`.

## Budget admission

Wave O at G1 (spec §4.9.2 "Quota enforcement", WQ-4, FR-37; `provider_budget.py`). ONE cap is enforced: the
paid Open-Meteo month, `lanes/_providers/open-meteo.toml [budget]` (5,000,000 weighted calls; gap-fill line
0.60, forward stop 0.95). Every other pool is metered and reported, never capped (WQ-4 declined the windowed
pools, reserves and the free-pool brake).

- **Where.** `run_executor_tick(budget=BudgetAdmissionState)` runs `admit_due_lanes` on every due definition
  AFTER planning and repair planning and BEFORE `fair_due_order`. A refused lane is a `deferred_budget`
  result that never enters `due`: it opens no run, never takes a `max_lanes_per_tick` selection slot or a
  queue slot, and never touches `JobHandlerOutcome.deferred` (so `MAX_CONSECUTIVE_PARKS` cannot fire).
  `budget=None` is HEAD's tick; the service loop always passes one (`--once` too).
- **The lines** (`judge_admission`, pure). Forward (a config lane's forward or transform turn, legacy soil's
  bucket) is refused only once CHARGED spend reaches the stop line; suspect spend NEVER stops forward, so
  G0's capped legacy soil keeps running at any suspect figure below 95 % charged. Gap-fill (a config lane's
  `:gap-fill`, legacy soil's `:gap-repair`) is admitted while charged + suspect + its own turn cap stays at
  or under the 60 % line, and is refused outright while suspect exceeds 10 % of charged
  (`SUSPECT_SHARE_LIMIT`, the `budget_basis_suspect` rule).
- **Who is charged** (`charge_for`). A config lane charges its source provider's `[budget]` at its TOML's
  per-mode cap (`[budget] forward_max_weighted_calls` / `gap_fill_max_weighted_calls`, S19). Legacy soil and
  its repair charge through `LEGACY_CHARGED_LANES` at G0's hard cap (1,602, `vocabulary.LANE_LOGICAL_CAPS`);
  `c7-quarantine-1` deletes that map with the lane. Either charges ONLY while the provider's key variable
  (`OPEN_METEO_API_KEY`) is set, the rule that picks the customer host; without it the turn hits the free
  host and is not admitted against anything.
- **Spend** is `usage_report.py::month_to_date`, imported, never re-loaded (its SQL has one loader), read once
  per pool per tick and only when a due lane charges that pool, inside its own savepoint. A failed read
  ADMITS (one `plantgeo_job_executor_budget_read_failed` error per pool until it reads again): a ledger fault
  must never stop a lane, and every turn is already bounded by its own cap. Only a pinned connection that
  lost its backend re-raises.
- **The numbers** come from the provider file through `budgeted_providers()`; when the lanes directory cannot
  load, `fallback_paid_budget()` enforces `usage_report.py`'s three constants, which
  `tests/lane_config/test_provider_hosts.py` pins equal to the file. The report's own budget lines read the
  same `budget_for_pool`, so the operator report and admission cannot disagree.
- **Incidents** (on the soft-failure tick's one read; `_SoftFailureTick.observe_budget`). `budget_deferred:<pool>`
  is bumped on a lane's FIRST refusal of the UTC day (the same gate as its one `plantgeo_job_executor_budget_deferred`
  warn), or when refusals continue with no open row, and resolves (`admitted_turn`) on a tick that admits a
  turn on the pool and refuses none -- stricter than "the first admitted turn", because an admitted forward
  beside a still-refused repair would otherwise close the row while refusals go on. `budget_basis_suspect:<pool>`
  opens (one warn) while suspect > 10 % of charged and resolves when the share falls
  (`suspect_share_below_limit`) or the UTC month rolls over (`month_rolled_over`, judged from the row's
  `first_seen_at`). Neither escalates. The two kinds are `provider_budget.BUDGET_INCIDENT_KINDS`, read back
  beside `vocabulary.INCIDENT_KINDS` until that vocabulary declares them.
- **The provider try-lock.** An admitted charging lane carries its pool on `DueLane.provider_pool`; on the work
  queue its lane session takes `jobs/lease.py::try_provider_lock` (session-level, key
  `plantgeo:executor-provider:<pool>`) after its definition lock, so one turn spends a capped pool at a time
  across every executor process. A busy pool never waits: the lane reports `deferred_fairness` and its open run
  is driven next tick. A lock that cannot be released drops the connection with it. The spec names
  `db/engine.py::executor_lane_pool`; no such pool exists, so the lock rides the per-slot lane session
  (`_lane_session_slots`). The serial dispatcher takes no provider lock: it runs one lane at a time under the
  leader lock.
- **Manual runs** stay outside admission (spec §4.9.6): each prints its own operator usage line.

Tests: `tests/execution/test_provider_budget.py` (the refused-lanes slot flow, suspect never stopping forward,
the incident lifecycle, the admission seam and every line at its boundary).

## Daily upkeep: job-event retention and the monthly usage receipt

`DailyMaintenance` runs once per UTC day in the repair-authoring slot of the tick (right after
`_author_due_repairs`), whatever the repair clock says; the day is process-held and a failed pass waits for the
next UTC day, never the next tick. `--once` never runs it.

- **`job-logs-maintain`** (COV2-14: `agri.job_event` is the heartbeat channel, never the audit record, and nothing
  scheduled its retention before G1): `db/maintenance.py::maintain_job_event_partitions` with the CLI's own
  defaults (30 days kept, 7 created ahead), on its OWN short-lived connection so its partition DDL and
  `ACCESS EXCLUSIVE` lock on `agri.job_event_default` never ride the leader's transaction; bounded by
  `JOB_LOGS_MAINTAIN_TIMEOUT_SECONDS`. A busy advisory lock (an operator running the verb) is an info line. The
  FIRST production pass drains every default-partition row accumulated since the table existed; expect it to
  take longer than later ones.
- **The monthly receipt** (WQ-6, FR-34; `usage_receipt.py`): `receipts/source-usage/<YYYY-MM>.json` in the existing
  object store (the Parquet bucket, under its prefix), written ONCE per closed UTC month and never rewritten
  (`size_of` first; an existing key is `already_present`). It waits `RECEIPT_SETTLE_DELAY` (one day) into the next
  month, so a turn still `running` at midnight -- which `month_to_date` excludes -- has settled first. It holds
  every pool's month-to-date figures for that month (`month_to_date(now=<month start>)`) and the per-lane rollup
  (`group_usage_rows(by="lane")` over the month's window), so a database rebuild never erases usage history.
  `c7-verbs` lists `receipts/` as known infrastructure; nothing prunes it. An unconfigured bucket logs once and
  turns receipts off for the process.

Tests: `tests/execution/test_usage_receipt.py::test_monthly_usage_receipt_is_written_once_per_closed_month`.

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
reads no source table. It replaced, and is now the only survivor of, `register_governed_plane` /
`register_governed_forward_plane`, which fingerprinted and materialised the whole `geo.features`
NDVI corpus. That corpus was frozen
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
`NonFinitePartitionValueError`, `UnregisteredPartitionCellsError`, plus the pre-existing
`EmptyGovernedReleaseError` and `ReleaseSetManifestConflictError`, now rooted in the same base. The
base class is the ONE exception type `run_vegetation_promotion` catches, and it renders as the
`registration_refused` day entry and terminal status above.

`register_governed_forward_plane` and its `PartitionSourceNotSuppliedError` were a one-commit
refusal shim, kept only so the promoter's import stayed green while the two halves of this change
lived on separate branches. The promoter now calls `register_governed_partition_plane` with the
`cell_values` it already held, so both are DELETED rather than left as a permanently-raising
signature a future caller could rediscover.

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

## Expert label export (`expert_label_export.py`): dry run by default, immutable once written

`agri-service ops export-expert-labels --release <id>` lifts one reviewed release out of the
Postgres expert-label plane into `ml/labels/expert/<release>/part-0000.parquet`, beside a
`receipt.json`, for `services/plantgeo-ml-service` to read. It is the ONE direction data flows from
agri to that service, and it runs once per release, under an owner go.

**It is a DRY RUN unless `--apply` is passed.** The default is the reversible one because the write
is not: re-exporting a release whose object holds different bytes is refused outright, since an ML
artifact may already pin its training set by that object's digest, and silently replacing it would
move the ground under a model that cited it. A dry run does everything but the two puts and prints
the sha256 and byte count the apply run would write, so digests can be compared before any bytes
are committed. Re-exporting IDENTICAL bytes is a no-op reported as `already_present`, not an error.

**`--prefix` is validated here, not only at write time.** A prefix outside `ml/` raises
`ExpertLabelExportRefusal` naming the flag, rather than the store's bare `ValueError` two layers
down — and it refuses in a dry run too, which would otherwise never reach the store at all.

## Quality receipt

Changes in this directory affect the Python quality fingerprint. Regenerate
`services/agri-data-service/QUALITY_RECEIPT.json` only after the final code and test edits are settled.
# PNW reference-data schedules

The BLM forward, reconcile and backfill definitions run daily at 09:00, 11:00 and 13:00 UTC.
Each command owns one complete current source snapshot and uses the same package lock. Backfill
means replay of admitted immutable capture evidence; no pre-admission history is invented.
The crop maintenance definition runs daily at 10:00 UTC. Its single command performs all three
duties: detect incomplete admitted editions, restore the oldest one, then check the newest source
monthly after history is complete. It writes at most one annual edition per invocation.

These definitions are initially shadow registrations. Add their four lane IDs to the production
active-lane allowlist only after the PR's code is deployed and the initial production readback
is verified. Existing deployed code cannot execute these new definitions. The crop working
directory is disposable; verified original source captures remain in immutable object storage.
