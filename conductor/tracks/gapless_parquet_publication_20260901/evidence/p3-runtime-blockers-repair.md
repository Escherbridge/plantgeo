---
type: track-evidence
track: gapless_parquet_publication_20260901
slice: p5-fix
status: authored_unverified
observed_at: 2026-09-02
---

# Runtime blockers repaired: matview refresh and the WFIGS response bound

## Verdict

The two pre-existing runtime failures the executor-only scheduler surfaced (RUNBOOK, "Current runtime
blockers are pre-existing failures surfaced by the new owner") are repaired **in code**. Nothing here
was executed, deployed, requeued or verified against production: no tests were run, no lint or type
check was run, no dead letter was touched, no lane was requeued, and no Railway or database call was
made. The single verification sweep belongs to the orchestrator at the join; the operator procedure
below belongs to a human after that sweep is green and the release is deployed.

**Both dead-letter populations remain standing on purpose.** The RUNBOOK's instruction — "do not erase
dead letters or force retries to make the scheduler appear green" — is honoured literally: this change
contains no requeue, no `next_attempt_at` write, no status mutation, and no `DELETE`.

## Blocker A — `jobs-matview-refresh`: 200 dead letters, `matview_refresh_failed` on the current tick

### What was actually wrong

`geo.mv_feature_observation_day_axis` and `geo.mv_signal_cell_daily` are absent from production. Three
individually-correct mechanisms compounded into a dead letter an hour, indefinitely:

1. An absent view can never succeed, so `upsert_matview_refresh_state.sql`'s `COALESCE` leaves
   `refreshed_at` NULL and `_eligibility` reads NULL as *never refreshed, try it* — eligible on every
   tick, ungated.
2. The consecutive-failure backoff could not engage. `consecutive_failures` increments only on outcome
   `failed`; a missing relation landed `skipped_missing`, which the SQL's `CASE` deliberately does not
   count, because a catalogue lookup issues no `REFRESH` and earns no backoff.
3. `MatviewRefreshReport.has_failures`' all-attempted-missing rule then failed the whole **tick** on
   any tick where the absent views were the only *eligible* ones — which, once every present view is
   watermark-fresh, is most of them. A failed tick retries, exhausts `max_attempts`, and dead-letters
   the shard the lane freshly minted for exactly that purpose.

### What changed

- **A per-spec preflight.** `matview_refresh.py::_absent_relations` runs one `check_relations_exist.sql`
  round trip (`to_regclass`, no lock, no error on a missing name) over the whole spec table at the top
  of the handler. An absent spec never reaches `_plan_refreshes`: no watermark query, no `REFRESH`, no
  failure, no retry.
- **A typed governed outcome, `relation_absent`.** Recorded in `agri.matview_refresh_state` (so
  `last_attempt_at` moves and an operator can see the lane considered it) and lifted to the **top
  level** of `job_attempt.metrics` as `relations_absent`. It sits in `_UNATTEMPTED_STATUSES`, so it can
  never fail a tick; it is deliberately NOT folded into `skipped_missing`, which now means only the
  mid-tick race (present at preflight, gone at `REFRESH`) and keeps its degraded all-missing signal.
- **No ledger migration was needed.** `agri.matview_refresh_state.outcome` is `character varying(64)`
  with no value `CHECK`, so the new literal is legal at the current Alembic head, and the existing
  `CASE` leaves `consecutive_failures` alone for it. Proven by an added assertion in the DB-gated
  `tests/test_jobs_matview_refresh_state_agri_db.py`, which runs only under `AGRI_TEST_DATABASE_URL`.
- **A `relation_absent` tick carries the LAST OBSERVED watermark forward**, never a fresh `{}`. Nothing
  was queried, so nothing new may be claimed to have been observed.

### Why the two specs were removed

Removing them is a **separate act** from the preflight and neither substitutes for the other. The
preflight stops an absence dead-lettering the lane; removal stops the lane standing an instruction to
rebuild a relation we chose not to have.

- **`geo.mv_signal_cell_daily` was dropped from production on 2026-08-18**, deliberately, under the
  Parquet/DuckDB pivot (owner call 2026-08-22: Postgres keeps only community features; every
  analytical plane becomes day-partitioned Parquet read by DuckDB). The serving contract above it is
  already the typed refusal: `agent/tools.py` probes the relation with `to_regclass` and returns
  `pre_aggregated_plane_unbuilt` rather than an empty result. A refresh lane rebuilding the relation
  underneath that refusal would contradict the pivot, not serve it.
- **`geo.mv_feature_observation_day_axis`** is drizzle/0031's split of the feature census. The honest
  statement is narrower: it was never applied against production, and under the same pivot it is not
  going to be. Its wide sibling `geo.mv_feature_observation_day` **is** present and stays on the lane,
  now carrying the census alone at its existing six-hourly cadence.

**What was deliberately not touched.** No agent SQL naming either relation was deleted
(`sql/agent/signals_near_point.sql` and its three siblings still read the rollup — that is a serving
contract on a different track). The definition-level preflight tuple
(`MATVIEW_REFRESH_REQUIRED_RELATIONS`) still names only `agri.matview_refresh_state`; a new test
asserts it stays disjoint from the view list, because "add the missing view to the preflight" is the
obvious wrong fix and is strictly worse than the bug — a lane that refuses every tick over a
deliberately-dropped relation never refreshes anything again.

The spec table is now **ten** views, hand-spelled in `tests/test_matview_refresh.py` against the
module rather than generated from it, with the two removed names asserted absent so a re-add has to
be deliberate.

## Blocker B — `postgres-fire-perimeters`: retry backoff on an oversized WFIGS response

### What was actually wrong

A 100-record page of large 2026 perimeters exceeds `WFIGS_BOUNDS.max_bytes` (16 MiB). Nothing about
that is transient: `upstream_retry.py::is_retryable_failure` correctly declines to retry an
oversized-body `UpstreamPayloadError`, so the lane failed, the job-level retry asked for **the same
page again**, and the lane walked into backoff — once an hour, forever. A retry cannot fix a request
whose *shape* is wrong.

### What changed

- **Adaptive paging, implemented generically in `ingest/arcgis.py`** so other ArcGIS sources can adopt
  it. `adaptive_page_offset_walk` halves the record count and re-asks at the **same offset**
  (100 → 50 → 25 → … → 1). Offsets advance by `len(page)` — records actually returned, never the size
  asked for — so shrinking mid-walk can neither skip nor double-count. A shrink **sticks** (MTBS's
  precedent: one oversized page is evidence about the whole feed); a single oversized **record** does
  not shrink the walk, because it is evidence about that record only.
- **A record that alone exceeds the bound is a typed governed source refusal**, not a fatality:
  `OversizedSourceRecord(offset, identity, declared_bytes, limit_bytes)`, logged as
  `arcgis_oversized_record_skipped` and surfaced in the run outcome. The walk steps over exactly one
  record and continues.
- **The object id comes from an identity probe**, not a guess: one extra request for that offset with
  `returnGeometry=false` (attributes only, kilobytes) reads `attr_UniqueFireIdentifier`. The answer is
  read for a name and **discarded** — never written. A probe that itself fails leaves the record
  anonymous rather than failing the run.
- **`geometryPrecision` was NOT touched.** Reducing it would degrade the geometry that gets *stored*,
  silently, on every record rather than on the one that overflowed. `ingest/AGENTS.md` records that as
  an owner decision about data quality, not a transfer tactic.
- **A per-run total-bytes budget**, `WFIGS_TOTAL_BYTE_BUDGET` = 128 MiB. The per-request cap bounded
  each page; nothing bounded a run of 200 of them (200 × 16 MiB = 3.2 GB), and the adaptive walk makes
  that *more* reachable precisely because it now keeps going where it used to raise. Hitting the budget
  reports `truncated=True`, exactly like the record ceiling. The record and page ceilings are unchanged.
- **`ingest/http.py` gained `UpstreamPayloadTooLargeError`** — a *subclass* of `UpstreamPayloadError`,
  so every existing `except` still catches it, `is_retryable_failure` still declines to retry it, and
  its message still opens with the exact string production logged. It carries
  `limit_bytes`/`declared_bytes`/`observed_bytes` so a caller can adapt instead of parsing prose.
  `fetch_bounded_json_sized` reports the byte count beside the payload; `fetch_bounded_json` is now a
  one-line, behaviour-identical delegate.
- **The two-tuple `fetch_fire_perimeters` contract is preserved**, because
  `pipeline/validation/fire_perimeters.py` unpacks it and is owned by another slice. It delegates to
  the new walk and discards everything but "were records left behind"; the walk still logs its own
  refusal line on that path.

A skipped record reports `truncated=True` and names itself in `IngestionJobResult.reason`, with counts
in `details` (`oversized_records`, `bytes_read`) because that mapping is `Mapping[str, int]`.

## Operator procedure

**Preconditions.** Do none of this until (a) the orchestrator's single sweep is green, and (b) the
release containing this change is deployed and `plantgeo-job-executor` is confirmed running it. A
requeue against the old code reproduces both failures exactly.

### 1. Deploy, then watch one unassisted tick of each lane

Do not requeue anything first. Both lanes mint fresh work on their own schedule, so the first green
tick arrives without operator action and is the cleanest possible evidence.

- `jobs-matview-refresh` (hourly). A green tick is `job_attempt.status = 'succeeded'` with
  `metrics->'relations_absent'` present. Expect it to be `[]` once the two specs are gone — that is the
  point of removing them. If any *remaining* relation is also absent, it appears there by name and the
  tick is **still** green; that is the governed outcome working, not a regression. Per-view detail is
  in `metrics->'views'`.
- `postgres-fire-perimeters` (hourly). A green tick is an `ingested` result. Read `details.bytes_read`
  and `details.oversized_records`; a non-zero `oversized_records` comes with `reason` naming each
  refused perimeter by `attr_UniqueFireIdentifier`, offset and declared size, and with
  `truncated = true`. That combination is a **successful** run that is honestly reporting a named loss,
  and the structured log carries one `arcgis_oversized_record_skipped` line per record.

### 2. Only then, requeue — and only what is safe to requeue

**The 200 `jobs-matview-refresh` dead letters stay standing.** They are not a backlog. Per
`jobs/AGENTS.md` ("Dead-lettered shards from before the fix are not re-armed, on purpose"), a matview
refresh shard owns no irreplaceable window: the lane mints a fresh uniquely-keyed shard into its one
persistent run on every trigger, and the next tick refreshes every view exactly as if the outage had
not happened. Each dead-lettered row is a truthful record that a specific tick failed. Erasing or
re-arming them changes no data and destroys the only record of the incident — and the RUNBOOK forbids
it explicitly.

`postgres-fire-perimeters` is the same shape: its retry backoff is a live feed's hourly refresh, not a
history window, so the next scheduled tick supersedes everything it missed. **Nothing needs
requeueing.** If a specific hour's perimeter snapshot is genuinely wanted, run the CLI by hand rather
than mutating the ledger:

```
agri-service data ingest-fire-perimeters
```

If an operator nonetheless decides a shard must be re-armed, that is a deliberate, separately-approved
act with its own evidence — not part of this repair.

### 3. What "green" must NOT be taken to mean

A green `jobs-matview-refresh` tick with a non-empty `relations_absent` means *the lane is healthy and
those relations do not exist*. It is not a statement that the relations were refreshed. Anything
downstream that reads a named-absent relation is answering from its own refusal path (for
`geo.mv_signal_cell_daily`, `agent/tools.py`'s typed `pre_aggregated_plane_unbuilt`), and that is the
contract — but it is a contract to check, not to assume, whenever a new name appears in that list.

## Rollback

Revert the commit and redeploy. There is no data migration, no schema change and no ledger mutation to
undo: `relation_absent` is a value in an unconstrained `character varying(64)` column, and rows already
carrying it are inert once the code that writes them is gone.

If a revert is not fast enough, disable the affected lane instead — `agri.job_definition.enabled = false`
for `matview-refresh` or the fire-perimeters lane — which stops the tick without touching history. That
returns the system to the pre-repair state: the lanes stop, and the relations stay absent.

## Follow-ups this repair deliberately does not do

- It does not decide what replaces `geo.mv_signal_cell_daily` for the map. That is the Parquet/DuckDB
  cutover's question, not this lane's.
- ~~It does not adopt the adaptive walk in `evacuation_zones.py`~~ — **done in fix pass B, 2026-09-02.**
  The reasoning was reversed on review: "has not yet hit the cap" is not a bound, and keeping two
  answers to one failure mode over the same ArcGIS transport is what guarantees the second one is found
  in production. See "Fix pass B" below.
- It does not enable `order_by_fields` or the `returnCountOnly` pre-check for WFIGS. Those remain the
  un-taken ArcGIS upgrades `ingest/AGENTS.md` describes, each needing its own live probe.

---

## Fix pass B (2026-09-02): what an adversarial review returned CHANGES-REQUIRED on

Authored, not verified. No tests, lint or type checks were run in this pass; no deployment, no ledger
mutation, no Railway or production call. One live GET was made against the public, key-free NASA POWER
point API to capture a real response as a parser fixture (`.omc/research/nasa-power-point-response-
2026-09-02.md` and its `.json`, copied byte-identically to
`services/agri-data-service/tests/direct/climate/fixtures/` because `.omc/research/` is gitignored).

### BLOCKER — the climate writer could not have published a single day

`pipeline/direct/climate/source.py` asked NASA POWER's **regional** endpoint for `INGEST_BBOX` and then
demanded an exact bijection between the 0.5-degree grid points it returned and the 397-cell support. No
bbox can satisfy that. The 397 cells behind `grid_name = 'nasa-power-0.5-degree'` are the NASA plan's
`na-sample:1deg:*` cells — a **one-degree** integer lattice spanning 31N–51N, 125W–104W — of which the
requested bbox (`-125,42,-111,49`) covers 109. Every product-day would have raised
`ClimateSourceUnsettledError`, the lane would have failed every tick, and it would have done so while
hammering a public API. The `conftest` fixture hid it by rendering the response *from* the support, so
the tests could not see the shape they were asserting.

**The repair is to drop the regional endpoint entirely and reuse the POINT path the immutable history
was built from**, which reproduces its semantics by construction rather than by argument. One request
per support cell per day, carrying every product's parameter; the answer is bound back to the cell the
request was built from, with the echoed `geometry.coordinates` checked against that cell's centroid by
exact equality (an integer degree is exactly on POWER's 0.5-degree product grid, so nothing snaps). A
per-turn `ClimateSourceCache` keyed by `(cell_key, day)` means `--product all` pays 397 requests for a
day and reads eight lane-days out of them; a failed request is never held, so a retry re-asks only for
what failed. `nasa_power_daily_regional_url` and `NASA_POWER_DAILY_REGIONAL_URL` are deleted; the point
URL builder and `extract_nasa_power_parameter_values` are reused rather than re-implemented.

`fetch_nasa_power_daily` itself could NOT be called: its `NasaPowerDailyPlan` pins a
`HistoricalBackfillWindow` validated to be exactly four calendar years, so a one-day window is refused
by contract. Reusing the URL builder, the payload extractor and the `nasa_power_observed_value` fill
rule is the same reuse without loosening an immutable historical contract.

**Decision — a fill cell is data, not a refusal.** A cell at or below POWER's `-999` fill ceiling
contributes no row and does not refuse the day; only an all-fill day is a governed absence. The earlier
rule refused any mixed day, which was defensible for one regional response and is not for 397
independent ones: POWER publishes a fill for a cell whose inputs have not landed, and refusing would
hold the lane behind that cell indefinitely. `fill_cell_count` on the receipt is what keeps the shrunk
support visible. The live capture is an instance of exactly this: on 2026-08-20 all seven meteorology
parameters carry real values and `ALLSKY_SFC_SW_DWN` is `-999.0`, which is the 75-day solar lag
observed directly.

The support is now built in tests from the real plan file, and `require_pinned_lattice_cell` refuses any
cell off the one-degree step, outside the measured extent, or without the `na-sample:1deg:` key prefix,
so the 0.5-degree assumption cannot be re-introduced silently. The database value `nasa-power-0.5-degree`
is **not** renamed — it is a join key three serving predicates already carry — and the misnomer is
recorded at the constant and in `pipeline/direct/AGENTS.md`.

### MAJOR — the turn deadline now bounds every retry and contention wait

`contention_timeout_seconds` alone permitted a 3600-second wait, and the retry ladder permitted
`retry_attempts × (fetch + retry_max_seconds)` on top of it, against an executor command timeout of
1800 s — so the lane would have been SIGKILLed while holding a session advisory lock. The deadline is
threaded into `_publish_day_with_retries`, `_publish_locked_day` and the source fan-out; every sleep and
the contention wait are clamped to what remains; a reached bound returns the typed day outcome
`time_budget_exhausted` rather than raising, so the day stays owed. `ClimateTimeBudgetExhaustedError` is
deliberately not a `ClimateSourceError`, so the adapter does not wrap a statement about the turn into a
lane failure. The executor spec's `command_timeout_seconds` is now derived —
`int(CLIMATE_DEFAULT_TIME_BUDGET_SECONDS) + COMMAND_CLEANUP_MARGIN_SECONDS` = 1200 — so the outer kill
is strictly greater than the inner wall clock by a stated 300-second grace and the two cannot drift.

### MAJOR — a governed oversized skip is truncation to a set-comparing caller

`wfigs.py::fetch_fire_perimeters` returned `outcome.truncated` alone, so a skipped record read as a
complete set to `pipeline/validation/fire_perimeters.py`, which compares perimeter SETS and would have
concluded the missing perimeters were retired upstream. It now returns
`outcome.truncated or bool(outcome.oversized)`, matching what `run_fire_perimeters_ingestion_job`
already did, and the docstring's claim that a skip does not change set membership is corrected.

### MINORS

- The direct climate lane and its eight generic `parquet-climate-field-*` specs now declare each other
  in `conflicts_with`, so `parse_activation` refuses the pairing whichever an operator names first.
- Both direct writers now pass `availability_storage=BotoAvailabilityStorage.from_settings()` into
  `fill_one_lane_day`, constructed beside `ObjectStore.from_settings()`. Without it the extension step
  is silently inert and, under `PARQUET_COVERAGE_AUTHORITY=availability`, every day they publish is
  withheld while every rung looks healthy.
- `test_layer_import_contract.py`'s lane-isolation walk now treats a SUBPACKAGE as one lane, and
  `pipeline/direct` is registered, so `climate/` cannot import `fire_detections` or `water_gauges`.
- Multi-paragraph source comments moved to the nearest AGENTS.md with a one-line pointer left behind:
  `lane_registry.py`, `matview_refresh.py` (×3), `arcgis.py`, `job_executor_service.py`.
- `evacuation_zones.py` switched to `adaptive_page_offset_walk` with a 64 MiB per-run byte budget and an
  identity probe. Its two-tuple return contract is unchanged, which is why this was a switch rather than
  a documented refusal.

### What is still owed

Everything in this section is authored and unverified. The single sweep (`pytest`, `ruff`, `mypy`) is
the orchestrator's at the join. Beyond that: the request volume is 794 point requests on a default
`--product all` turn (≈1 MB, ≈2 minutes at concurrency 4) and has never been exercised against the live
service at that scale — the concurrency cap is a politeness choice, not a measurement. The lane remains
in SHADOW; activation is `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` naming `climate-nasa-power-direct-forward`,
which is now mutually exclusive with any `parquet-climate-field-*` lane in the same list.

