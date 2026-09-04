# Source-direct Parquet producers

Modules here fetch upstream products and publish registered Parquet schemas without staging
ingested rows in PostgreSQL. PostgreSQL may still supply the shared session-scoped lane-day
advisory lock during the transition; it is coordination, not a data sink.

## Water gauges

water_gauges.py partitions NWIS instantaneous values by the publisher-named day: the first ten
characters of updatedAt, before converting the timestamp to UTC. Records whose parser had to
substitute the wall clock are not source observations and must be dropped before this transformer.

The nominal base grain is (site_number, observed_at), but four reconciled historical days contain
duplicate physical rows at that grain in both PostgreSQL and Parquet. A completed partition keeps
every such row and its provenance. A repeated source grain refreshes source fields only when it maps
to exactly one existing row; a match to multiple historical rows is ambiguous and fails without
changing or dropping either. Unmatched published rows are retained byte-for-byte and unseen grains
are appended. New direct rows truthfully use geometry_linked=false, a null availability time, and
the direct fetch instant as ingested_at.

Publication goes through gap_fill.fill_one_lane_day: z13 is written and pruned, z9/z5/z0 are
derived and marked, and the z13 completion marker lands last. An object-store failure may therefore
leave z13 incomplete. The same process replays its pre-mutation intended table; a later process
reads every physically present row, adds the current fetch only when every matching grain is
unambiguous, rewrites one complete z13 part, and lets the shared finalizer prune the residue. It
never grain-deduplicates an incomplete partition: that would make an interrupted generation
indistinguishable from the legitimate duplicate source rows already proven by reconciliation.

Every successful tick re-reads z13, proves the complete duplicate-preserving table, and checks
every incoming source field at every incoming grain. Completion-marker status alone is not forward
writer evidence.

## Fire detections

`fire_detections.py` refreshes settled NASA FIRMS days in the dedicated
`layer=fire-detections/kind=observed/zoom=...` namespace. It intentionally leaves
`ingest/firms.py`, `ingest/commands.py`, and `ingest/runner.py` unchanged, so the existing
PostgreSQL FIRMS ingestion path remains available while the direct writer is proven.

The writer is bounded in four independent dimensions:

- one exact UTC day per FIRMS request, over a maximum five-day lookback;
- a fail-closed 50,000-record ceiling per day;
- a maximum number of days per process that cannot be smaller than the lookback window; and
- finite exponential retries for source and object-store failures.

Every lane-day uses the same session-scoped advisory-lock identity and the same base-write,
coarse-tier derivation, prune, and completion-marker finalizer as the Parquet gap-fill/drain.
The lock is acquired before HTTP and held across every bounded publish retry; contention performs
no fetch, while each actual retry refetches once so stale source cannot overwrite a newer release.
The adapter rolls back the statement-timeout transaction before HTTP because the session lock
survives rollback. Retry waits are finite and capped (60 s base, 300 s max, 3,600 s contention).

Normal hourly runs consult FIRMS' live availability table and revisit the bounded settled window
from every applicable product even when completion markers already exist, but never before the
shared `FIRE_DETECTIONS_DIRECT_WRITER_START_DAY` ownership boundary (2026-08-25).
`FIRE_FORWARD_START_DAY` and `--forward-start-day` may repeat that value but cannot move it.
SP records supersede NRT
records with the same native identity, matching the historical ingest contract.
That is the forward-refresh contract: a late NRT revision inside the window must not be hidden by an
earlier successful write. A complete zero-row constellation response records a governed z13 absence;
it never writes an empty Parquet file.

FIRMS may later revise an initially empty direct-owned day to contain detections. The adapter is
already running inside that day’s shared advisory lock, so a complete non-empty response explicitly
retracts the z13 absence immediately before the first base write. The inverse transition remains
fail-closed: a later empty response never removes published data or governs it absent automatically.

The Railway entry point is `python -m agri_data_service.pipeline.direct.fire_detections`, configured
by `services/agri-data-service/railway.fire-detections-forward.json`. Required runtime variables are
the ordinary object-store settings, `LOCAL_SOURCE_LOADER_DATABASE_URL` (or its existing fallback),
`INGEST_BBOX`, and `NASA_FIRMS_KEY`. Optional `FIRE_FORWARD_*` variables tune the bounded lookback,
day count, record cap, retry series, and contention timeout. `--force-day YYYY-MM-DD` intentionally
re-publishes one already-settled day inside that same bounded NRT window for a one-day operational proof.

## An all-null day is a refusal until the mirror is proven past it

Both API-direct writers used to turn "every support cell answered with no value" straight into a
GOVERNED ABSENCE. That claim is permanent and it is about the SOURCE — *the source published nothing
for this day* — while the thing actually observed is usually the opposite: the mirror has not reached
the day yet. ERA5-Land lands a day's cells as the reanalysis is produced; POWER fills a cell whose
inputs have not arrived. At the settled edge, which is exactly where a forward writer works, an
all-null answer is the ordinary shape of a day that is still coming.

So the rule is now: **an all-null (soil) or all-fill (climate) day is a `SoilSourceUnsettledError` /
`ClimateSourceUnsettledError` — a refusal, retried next turn — UNLESS the archive is proven to have
mirrored past it.**

The proof is the simplest honest one available and costs nothing: **the next settled day of the same
product, in the same scan window, is already published with values.** If a later day answered with
rows, the source has moved past this day and the null is its verdict rather than its backlog.
`_mirrored_past_day` reads it out of the tier-status listing the turn already paid for — no extra
request and no upstream call — and `_mirrored_past_proof` renders the sentence that is stored inside
the absence marker's `upstream_response` beside the receipt, so the marker carries WHY it was allowed
to be written and not only what was fetched.

Consequences worth knowing:

- The **newest** owed day can never satisfy the proof, by construction: nothing is published after it.
  So the leading edge refuses rather than governs, which is correct.
- The refusal is reported as `source_unsettled` on that day and does **not** fail the turn or consume
  the retry series. Refetching inside the same turn asks the same question of the same mirror; the
  next turn is the soonest the answer can change. `DirectSoilFieldAdapter.unsettled_refusal` /
  `DirectClimateFieldAdapter.unsettled_refusal` records the refusal as well as raising it, because
  `gap_fill._export_one_day` turns every adapter exception into `raised` and the walk has to tell
  "not settled yet" from a real failure.
- `mirrored_past_proof` defaults to `no_mirrored_past_proof`, which returns `None`. Fail-closed: any
  caller that has not supplied the proof gets the refusal, never a fabricated absence.

## A governed absence is re-examined, or it is permanent

`_retract_disproven_absence` exists precisely because the archive backfills a day it first answered
null for. It runs inside the lane-day lock, immediately before the first base write, at EVERY rung —
and it can only run on a day the walk SELECTS. `_pending_days` skipped every day whose base rung was
`absent`, so that retraction was unreachable and an absence, once written, was forever.

`_pending_days` now returns two lists concatenated: the days that owe real work (newest first), then
the days governed as absent within the newest `SOIL_ABSENCE_RECHECK_DAYS` / `CLIMATE_ABSENCE_RECHECK_DAYS`
(14) of the scan window. Rechecks come **last**, so a day with no data at all always outranks a day
that already has an answer, and the window is bounded because re-fetching absences from years ago
would spend every turn on days that settled long since.

A recheck of a day the source still answers all-null is cheap and safe: the proof that justified the
original absence is still standing (the later day is still published), so the marker is simply
rewritten. A recheck of a day the source has backfilled retracts the marker at all four rungs and
publishes the rows.

## One distinct day per turn, across all eight products

The per-turn request budget is sized in DAYS, not in product-days: one archive request carries every
variable for its locations, so eight products asking for the SAME day cost one day's worth of
requests. `SoilSourceCache` / `ClimateSourceCache` hold the responses per `(chunk|cell, day)` and the
budget is `chunks_per_day x --max-days` (soil) or `cells x --max-days x distinct publication clocks`
(climate).

The consequence to plan around: **the budget covers one distinct day across the eight products.** When
the products agree on which day they owe — the normal case, since they share a publication clock —
one turn advances all eight. When they diverge, each divergent pending day costs a whole tick of its
own, because the second day finds an exhausted budget and reports `request_budget_exhausted`. Eight
products stranded on eight different days therefore take eight turns to converge, not one.

## The ordinal has nowhere else to go in the lane shape

`_lane_row` writes `selected_observation_id = <response/support ordinal>`, and the column name says
`observation_id`. That is a genuine mismatch, and it stays, because the contract admits no
alternative — reported here rather than left to be rediscovered:

- The lane shape (`SOIL_TEMPERATURE_FIELDS`, used by `register_soil_wetness_product` and
  `register_soil_temperature_product`) has **no ordinal column**. `selected_source_row_ordinal` exists
  only in the 33-column `SNAPSHOT_LINEAGE_FIELDS`, which is a different row shape, not a superset.
- Writing NULL is not available either: `selected_observation_id` is listed in
  `SOIL_TEMPERATURE_BASE_NON_NULL_COLUMNS`, so `objectstore._refuse_null_base_columns` rejects the
  base rung outright and the lane could not publish at all. The same is true of
  `selected_source_row_id` in the lineage shape, which is in `SNAPSHOT_LINEAGE_BASE_NON_NULL_COLUMNS`.
- Both shapes were frozen by `scripts/soil_wetness_snapshot_breakdown.py` and
  `scripts/soil_temperature_snapshot_breakdown.py`, and the immutable snapshot history is already
  written in them. Adding a column is a re-export of that history, not an edit.

What makes it readable rather than a trap is the DISCRIMINATOR: `selected_source_release_id` carries
`direct:<response sha256>`. A reader joining `selected_observation_id` to `agri.signal_observation.id`
without checking that prefix is reading the wrong namespace — see "Direct lineage namespace".

## NASA POWER climate fields and soil wetness

`climate/` publishes ELEVEN streams the browser draws under seven toggles: the eight
`climate-field-*` streams -- air temperature (three streams: mean, max, min), dew point,
precipitation, relative humidity, shortwave radiation and wind speed -- and the three
`soil-wetness-*` depths (surface, root zone, profile). It exists because nothing produced a forward
POWER day.

**Why soil wetness lives in the climate writer and not beside the ERA5-Land one.** Its name groups
it with soil, its SOURCE does not: `GWETTOP`/`GWETROOT`/`GWETPROF` are NASA POWER parameters on the
397-cell POWER lattice at the meteorology lag of 5, and one point request already returns every
parameter the URL asked for. Publishing them from a second writer would mean a second 397-request
fan-out over the same lattice on the same day for values the first fan-out could have carried for
free. They report a DEGREE OF SATURATION, not a volumetric water content, and name their depth in
the `signal_name` rather than in `support_key`
(`execution/weather_observations/nasa_power.py` NASA_POWER_SIGNAL_SPECIFICATIONS) -- which is what
keeps them distinct from the ERA5-Land `soil_water_content_layer_N` series below.

**The eleven-parameter body has no real capture.** `.omc/research/nasa-power-point-response-2026-09-02.json`
was taken with an eight-parameter request months before the three depths joined the table, and POWER
returns only what the URL lists. The real capture is still parsed, against the eight parameters it
was really asked for, and the eleven-parameter shape is covered by
`tests/direct/climate/fixtures/nasa-power-point-response-soil-wetness-synthetic.json` -- the same
body with three series added, carrying a `_fixture_provenance` block that says which values are
invented. Take a live eleven-parameter capture and this fixture can be retired. The only NASA
POWER ingestion in the tree is `execution/weather_observations/nasa_power.py`, a retired local
backfill verb with no scheduler owner writing into PostgreSQL `agri.signal_observation`, and the
Parquet days these streams hold were built ONCE from the immutable canonical snapshot by
`scripts/build_*_from_canonical_snapshot.py`. The production assessment of 2026-09-01 measured the
result: a 27-day tail on five products and a 94-day tail on shortwave radiation.

### The layout question is already answered, and the answer is not uniform

`scripts/build_shortwave_radiation_from_canonical_snapshot.py` and its precipitation twin write
their DATA and COMPLETION MARKERS through `partition_path(...)`/`completion_marker_path(...)` -- the
generic lane layout, `layer=<slug>/kind=observed/zoom=NN/year=/month=/day=/`. Only their manifests
live under `layer=<slug>/_breakdown/snapshot=<id>/`. Forward days written by `fill_one_lane_day`
therefore land in the SAME namespace as the history for those two streams, and `parquet_ops`
already censuses them through `DEDICATED_SLIDER_PRODUCT_LAYERS`.

The other six are different. `air_temperature_snapshot_breakdown.py`,
`dew_point_snapshot_breakdown.py`, `breakdown_wind_speed_snapshot.py` and
`build_relative_humidity_from_canonical_snapshot.py` write under
`layer=<slug>/snapshot=prod-20260826-full-signal-v1/kind=observed/zoom=NN/...`, and
`parquet_ops/snapshot_products.py` serves them by listing that immutable root and binding every part
to a manifest plus `_COMPLETE`. That root is closed: nothing may be added to it, and the reader
matches only `key.startswith(f"{product.data_root}/")`.

So this writer publishes every product in the GENERIC layout, which is correct-and-visible for two
streams and correct-but-not-yet-visible for six. Making the other six visible is a reader change in
`snapshot_products.py`, not a writer change: the reader must union its closed snapshot evidence with
the generic prefix for days after the product's own last snapshot day. Writing forward days into the
snapshot root instead would break the manifest binding that makes the history trustworthy.

### Three row shapes, and two of them have a lineage that does not apply

Five streams (air temperature mean/max/min, dew point, wind speed) use the frozen twelve-column
signal plane. Three (precipitation, relative humidity, shortwave radiation) use the thirty-three
column snapshot-breakdown contract, whose extra twenty-one columns describe how one canonical
PostgreSQL row was SELECTED out of a multi-release population. A direct fetch has no such
population, and `TierDerivation.base_non_null_columns` forbids nulling sixteen of those columns at
the base rung, so the row cannot simply omit them.

The three soil-wetness depths use a THIRD contract, nineteen columns, frozen by
`scripts/soil_wetness_snapshot_breakdown.py` LANE_SCHEMA and registered as
`warehouse/parquet/snapshot_signal_product.py::register_soil_wetness_product`. It is not a subset of
the thirty-three: it has no `source_snapshot_id` at all and names its selection
`selected_observation_id` / `selected_canonical_row_sha256`. Three shapes is why `rows.py` dispatches
through a table keyed on `row_shape` rather than through a ternary -- a fourth shape is one entry,
and a wrong shape is a write that fails against the registered Arrow schema rather than a silent
column drift.

**On the nineteen-column shape the discriminator moves.** `source_snapshot_id` does not exist there,
so the `direct:` token rides `selected_source_release_id` -- the only column in that shape a
namespace can be read off -- and `selected_observation_id` is a RESPONSE ORDINAL, never an
`agri.signal_observation.id`. A reader that checks only `source_snapshot_id` will read those rows as
canonical selections; it must check the release id too.

**Direct lineage namespace.** On a direct row `source_snapshot_id` is `direct:<sha256 over the
day's responses>`, and THAT IS THE DISCRIMINATOR. Every lineage column on such a row is scoped to
that day's responses rather than to `agri.signal_observation`: the "release" is the day's retrieval,
the "part" is the ONE POINT REQUEST that cell's value was read out of (`selected_source_part_key` is
that cell's own URL and `selected_source_part_sha256` that cell's own response digest), the "row" is
the parameter series inside it, and `selected_source_row_id` therefore equals
`selected_source_row_ordinal`, which is the cell's index in support order. A reader that joins
`selected_source_row_id` to `agri.signal_observation.id` without first checking the `direct:` prefix
is reading the wrong namespace. Nothing fabricates a PostgreSQL identity for a row that never had
one, which is the rule that made this shape necessary rather than convenient.

### Support cells come from the dimension the history was built from

`support.py` reads `agri.spatial_cell WHERE grid_name = 'nasa-power-0.5-degree'` through the session
already open for the lane-day lock, and refuses anything other than 397 cells. That table is where
the historical rows' `cell_id`, centroid and `coverage_fraction` came from, so identity is
bit-identical by construction rather than by re-derivation from coordinates. The count is pinned
against three independent measurements: `parquet_ops/snapshot_products.py`'s
`coverage_cells_per_day`, the breakdown builders' `EXPECTED_CELLS_PER_DAY`, and `agent/tools.py`.

The support query is one table, one predicate, no CTE and no join, so `code_styleguides/sql.md`
keeps it inline beside its caller rather than in `sql/pipeline/`.

### The `grid_name` misnomer, and the lattice it does not describe

**`grid_name = 'nasa-power-0.5-degree'` names the POWER PRODUCT's resolution, not the spacing of the
sample.** The 397 rows behind that label are the NASA plan's `na-sample:1deg:*` cells: a ONE-DEGREE
integer lattice over western North America, 31N to 51N and 125W to 104W, read verbatim from
`plans/nasa-power-western-na-weather-fast-20220806-20260806.json` (`nasa.cells`) and described the
same way in `execution/AGENTS.md`, "This plan rides the NASA lattice, not this lane's usual one".
22 longitudes by 21 latitudes is 462 positions and the plan samples 397 of them, so **the lattice is
a subset of its own bounding box** and nothing may enumerate it from the extent.

The database value is NOT renamed. It is the join key three serving predicates and every historical
row already carry, and a rename would be a data migration in exchange for a nicer label.
`support.py` states the misnomer at the constant, pins the step and the extent as
`NASA_POWER_SUPPORT_STEP_DEGREES`/`_WEST`/`_EAST`/`_SOUTH`/`_NORTH`, and `require_pinned_lattice_cell`
refuses any cell off that step, outside that extent, or without the `na-sample:1deg:` key prefix --
so a re-keyed dimension cannot pass itself off as this support.

**This misnomer is what produced the blocker this section replaces.** A writer that read "0.5-degree"
as a fact about the sample asked POWER's regional endpoint for a bbox and demanded an exact bijection
between the 0.5-degree grid points it returned and the 397 support cells. No bbox can satisfy that:
the regional grid is twice as dense and the requested extent (`INGEST_BBOX`, `-125,42,-111,49`)
covers only 109 of the 397 cells. Every product-day raised, the lane failed every tick, and it did so
while hammering a public API.

### One point request per support cell-day

**The lane uses the same POINT endpoint the immutable history was built from**, which is what makes a
forward day reproduce the semantics of the days it extends. One request is one support cell for one
day and carries EVERY product's parameter: measured against the live service on 2026-09-02, a request
for all eight parameters at 46N/119W for 2026-08-20 answered 1,189 bytes containing
`properties.parameter.<PARAM>.<YYYYMMDD>` for each of them
(`.omc/research/nasa-power-point-response-2026-09-02.json`, headed by
`nasa-power-point-response-2026-09-02.md`). `parse_climate_point_body` is bound to that capture.

**The support bijection is by construction, not by search.** The request is built FROM a support
cell, so the answer is bound back to that cell. There is no nearest-neighbour lookup and no tolerance
window. `_require_echoed_point` additionally checks the echoed `geometry.coordinates` against the
cell's centroid by exact quantized equality and refuses a mismatch: every support cell sits on an
integer degree, which is exactly on POWER's 0.5-degree product grid, so the service snaps nothing and
equality is the honest comparison rather than an optimistic one.

**One cache per turn, keyed by cell and day.** `ClimateSourceCache` holds each completed cell-day
response, so `--product all` pays 397 requests for a day and reads eight lane-days out of them rather
than paying 8 x 397. A FAILED request is never held, so a retry re-asks only for the cells that
failed; a held response is reused across retries, which also makes the day's receipt stable across
attempts instead of minting a new `source_snapshot_id` each time. The fan-out runs behind a
semaphore of `NASA_POWER_POINT_CONCURRENCY = 4` against a public, key-free API.

**Refusal is per DAY, never per cell.** If any cell's request fails after `ingest/http.py`'s transport
retries, the whole product-day is `source_unsettled` and stays owed. A partial day published once is a
day nothing revisits.

### The request budget

`ClimateForwardConfig.request_budget` is `397 x --max-days x CLIMATE_DISTINCT_PUBLICATION_CLOCKS`,
where the clock count (2) is derived from the distinct `publication_lag_days` across the eight
products -- the meteorology lag of 5 and the solar lag of 75 -- because that is how many distinct
settled edges a turn can select days at. At the defaults that is 794 requests, roughly 1 MB, and about
two minutes at concurrency 4. `_publish_product` checks `cache.can_afford(support, day)` BEFORE it
starts a day and reports `request_budget_exhausted` for that day rather than beginning a fan-out it
cannot finish; `fill_cell_day_cache` refuses as a backstop if it is reached anyway.

### Fill cells, absence and refusal

A cell whose value for the day is at or below POWER's `-999` fill ceiling
(`nasa_power_observed_value`) contributes NO ROW and DOES NOT refuse the day. **A mix of real values
and fill values is real data**: POWER publishes a fill for a cell whose inputs have not landed, and
refusing the day would hold the whole lane behind one cell for as long as that stays true. What keeps
the shrunk support visible instead is `fill_cell_count` on the receipt, carried into every progress
record and into the absence marker. This deliberately reverses the earlier rule that refused any mixed
day; that rule was written for a single regional response, where a mix genuinely was ambiguous.

A day in which EVERY support cell reports a fill is a REFUSAL (`source_unsettled`) unless a later
settled day of the product is already published with values -- see "An all-null day is a refusal
until the mirror is proven past it". Only then is it a governed absence, and its marker then carries
that proof alongside the receipt: the sha256 over the day's concatenated response digests, the sha256 over its request
URLs, the request count, the total bytes, the newest retrieval instant, the cell count and the fill
cell count. Everything else short of a complete day is a REFUSAL: a transport failure, a non-2xx
status, an oversized body, a body that echoes a different point, a missing parameter or day key, an
unusable value, or a support cell with no held response.

POWER revises a fill-value day into real values once its inputs land, so a non-empty response
explicitly retracts an earlier absence immediately before the first base write, inside the same
lane-day lock, at EVERY rung. The inverse stays fail-closed: a later all-fill response never removes
published data.

### The turn deadline bounds every wait

`--time-budget-seconds` is a bound on the WHOLE turn, not a gap between days. The deadline is threaded
into `_publish_day_with_retries`, `_publish_locked_day` and `fetch_climate_day`, every `asyncio.sleep`
is clamped to what remains of it, and the lane-day contention wait is `min(contention_deadline,
deadline)`. A bound that is reached returns the typed day outcome `time_budget_exhausted` rather than
raising: the day stays owed and the next tick takes it.

`ClimateTimeBudgetExhaustedError` is deliberately NOT a `ClimateSourceError`. A source error is a
statement about POWER and the adapter wraps it into a lane failure; this is a statement about the
turn, so it travels through the adapter untouched and `_publish_locked_day` catches it explicitly.

What this prevents is concrete: `contention_timeout_seconds` alone permits a 3600-second wait and the
retry ladder permits `retry_attempts x (fetch + retry_max_seconds)` on top of it, against an executor
command timeout of 1200 s (`execution/AGENTS.md`, "`command_timeout_seconds` is derived from the CLI
default"). A `SIGKILL` at that ceiling would land while the lane holds a session advisory lock.

### Lags, floors, and the one number that is not measured

Meteorology lag is 5, NASA POWER's measured value in `execution/coverage_census.py`. Shortwave
radiation's is 75 and is CONSERVATIVE RATHER THAN MEASURED: 5 plus the 67-day difference between the
canonical snapshot's meteorology last day (2026-08-06) and its `ALLSKY_SFC_SW_DWN` last day
(2026-05-31) in the same build, plus three days of slack. Measure POWER's own live solar edge and
replace it. Over-waiting delays a real day by one tick; under-waiting sends a fetch after a day
POWER has not produced and turns it into a governed absence that is simply wrong.

Floors follow the same asymmetry and are per-product, not global: the day after THIS product's own
immutable history. Seven products floor at 2026-08-07; shortwave radiation floors at 2026-06-01,
because the snapshot's source ledger only reached 2026-05-31 for that parameter. A single
2026-08-07 floor would have left the nine weeks from 2026-06-01 owned by nobody, which is most of
the 94-day tail this lane was built to close.

### Ownership, and why the registered adapter refuses

`pipeline/parquet/lane_registry.py` registers all eight streams with `_refuse_source_direct_export`.
There is no PostgreSQL producer to register and there never was: the only NASA POWER ingestion in the
tree is `execution/weather_observations/nasa_power.py`, a retired local backfill verb with no
scheduler owner, and the Parquet days these streams hold today were built ONCE from the immutable
canonical snapshot by `scripts/build_*_from_canonical_snapshot.py`. Nothing produced a new climate
day, which is why the production browser showed a 27-day tail on five products and a 94-day tail on
shortwave radiation (RUNBOOK, "Production timeline evidence"). A lane with no registration is a
stream nothing schedules and nothing censuses, which is the failure `test_lane_registry.py` exists to
make loud. The real writer substitutes its own adapter with
`replace(lane, adapter=...)`, exactly as the fire and water direct writers do. `writer_ceiling` stays
`None` because a ceiling divides a calendar between two writers and here there is only one; what
bounds the lane is its floor, enforced by `refuse_immutable_day` ON THE ADAPTER so the bound holds no
matter which driver reaches it.

`plantgeo-ingest-cron` is deliberately NOT named as the legacy owner of the eight generic
`parquet-climate-field-*` specs. It never produced one of these days, and naming it would put them
in its atomic cutover group -- so activating the real ingest cutover would drag along eight lanes
whose adapter refuses by design.

The direct lane and those eight generic specs DO declare each other in `conflicts_with`, from both
sides, so the executor refuses to run two owners over one calendar. See `execution/AGENTS.md`,
"It conflicts with its own generic specs, from both sides".

### Entry point

`python -m agri_data_service.pipeline.direct.climate`, with `--product` naming one of the seven
browser toggles or `all`, `--max-days` (default 1, max 5) days per product per turn,
`--time-budget-seconds`, `--run-id`, and the bounded retry and contention knobs. Days are taken
newest-settled-first and one at a time under the lane-day lock; a day already complete at every rung
is an idempotent no-op. Required runtime variables are the ordinary object-store settings and
`LOCAL_SOURCE_LOADER_DATABASE_URL` (or its existing fallback). NASA POWER needs no key.

**There is no `--bbox` and there is no `INGEST_BBOX` dependency.** The extent of this lane is the
pinned support read from `agri.spatial_cell`, which is a stronger bound than a policy bbox and the
only one that keeps a forward day comparable with the history it extends. A bbox knob here was not
merely redundant, it was the blocker: see "The `grid_name` misnomer" above.

**The lane extends the availability index.** `fill_one_lane_day` is called with
`availability_storage=BotoAvailabilityStorage.from_settings()`, constructed beside
`ObjectStore.from_settings()` so an unwired bucket fails in exactly the same way and nothing opens a
socket at import. Without it the extension step is silently inert, and under
`PARQUET_COVERAGE_AUTHORITY=availability` every day this writer publishes would be withheld from
readers while every rung it wrote looked healthy.

The executor lane is `climate-nasa-power-direct-forward`, hourly at :40 -- distinct from the direct
fire and water writers at :15 and the SoilGrids warmer at :25. IT SHIPS IN SHADOW: it is in no
active lane list, and activation stays an explicit operator act through the executor's allow-list
variable.

## ERA5-Land soil fields

`soil/` publishes the EIGHT streams the browser draws under three soil toggles: three moisture
depths (`soil-field-moisture-0-7cm`, `-7-28cm`, `-28-100cm`), four temperature bands
(`soil-temperature-0-to-7cm`, `-7-to-28cm`, `-28-to-100cm`, `-100-to-255cm`) and one VPD stream
(`soil-field-vpd`). The production assessment of 2026-09-01 measured 31-day tails on all three
toggles: every one of those streams stops at 2026-08-02 and nothing in the tree was going to move it.

### The upstream is Open-Meteo, and why it is not the CDS

The obvious writer to build was one over `execution/historical_era5.py`, the Copernicus CDS lane. It
would have been wrong on all three products, and the evidence is in the artifacts that wrote the
history:

- **Source identity.** Every historical row of all eight streams carries `data_source_key`
  `open-meteo-era5-land-archive` and `support_key` `era5-land-0.1deg`
  (`scripts/build_soil_moisture_from_canonical_snapshot.py` SOURCE_KEY/SUPPORT_KEY,
  `scripts/soil_temperature_snapshot_breakdown.py` EXPECTED_SOURCE_KEY/EXPECTED_SUPPORT_KEY,
  `scripts/vpd_snapshot_breakdown.py` ProductContract). The CDS lane never persisted a warehouse row
  and `agri.data_source` has no `era5-land` key at all -- its own module docstring says so.
- **Support.** The CDS plan requests a 1.0-degree OUTPUT grid (`ERA5_LAND_REQUESTED_GRID_DEGREES`);
  the history sits on the 1,568-cell 0.25-degree `sentinel2-ndvi-0p25deg` analysis lattice sampled at
  ERA5-Land's native 0.1 degrees. A day on the coarser grid is not comparable with the days it would
  claim to extend.
- **Coverage.** The CDS lane carries `volumetric_soil_water_layer_1` only -- one of the three moisture
  depths -- and has no VPD variable at all. VPD is not derived here: Open-Meteo publishes
  `vapour_pressure_deficit_max` as a daily variable of the same `era5_land` model, and that published
  series IS the immutable history (kPa in, kPa out). A forward writer that recomputed VPD from
  temperature and dew point would be a second, different estimator writing under the first one's
  `signal_name`.
- **Credentials.** `CDSAPI_*` lives only on the inert `plantgeo-ingest-cron` service, so a CDS writer
  could not run in production at all. The archive host is keyless; `OPEN_METEO_API_KEY` only lifts
  the quota wall and is read from the environment at fetch time.

`execution/AGENTS.md` section "Soil temperature is deliberately excluded" predates the reviewed
soil-temperature plan and describes a decision that was reversed; the plans and the written rows are
the newer evidence.

### The lattice and the support are two facts

`grid_name` is `sentinel2-ndvi-0p25deg` -- where the cells are -- and `support_key` is
`era5-land-0.1deg` -- what resolution the source models at. Both are recorded, on every row, exactly
as the history records them. The lattice is a COMPLETE box: 56 longitudes by 28 latitudes over
(-125, 42) to (-111, 49), on centroids a half step off the integer degree (42.125, 42.375, ...), and
all three reviewed plans carry exactly 1,568 cells. This is the opposite of the NASA POWER lattice,
which is a 397-cell SUBSET of its own bounding box, so the guard here checks the half-step offset an
integer-degree guard borrowed from the climate writer would reject outright.

### One archive request per support chunk-day

The archive endpoint is multi-location: one request carries fifty cells and every one of the eight
variables for one day, so a settled day costs `ceil(1568 / 50) = 32` requests TOTAL -- shared by all
eight products, because the cache is keyed `(chunk_key, day)` and not by product. Fifty is the
`chunk_cell_count` of all three reviewed plans; the endpoint's own ceiling is 200
(`ingest/open_meteo.py` MAX_ARCHIVE_LOCATIONS_PER_REQUEST). A larger chunk buys fewer round trips at
NO quota saving -- Open-Meteo weights a request by locations x variables x timesteps, not by count --
and costs a four-times larger body to lose on one transport error. One settled day is therefore
1,568 x 8 x 1 = 12,544 weighted units and roughly one to two megabytes of response, once per turn,
whatever `--product` selects.

### What is reused, and the one thing that is not

Everything under the historical plan is reused and is public: `archive_daily_request` (the
credentialed URL), `archive_daily_url` (the keyless one that is RECORDED into every row's
`selected_source_part_key`), `fetch_archive_daily` (byte and rate-limit bound), `fetch_lane_capture`
(retry and 429 policy), `canonical_location_document`, `ordered_locations`, `validated_grid_point`,
`max_grid_offset_degrees`, `nearest_native_grid_point`, `bounded_numeric_series`, and
`OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS` for units and acceptance ranges.

What is NOT reused is `parse_open_meteo_archive_payload` itself, and the reason is structural: it is
reachable only through a `HistoricalOpenMeteoArchivePlan`, whose window is a
`HistoricalBackfillWindow`, which REFUSES any span that is not exactly four calendar years
(`execution/backfill_types.py::require_exact_four_calendar_years`). A forward writer asks for one
day. Re-declaring the window rule to get around it would be a second definition of the historical
contract; the three checks that module keeps private -- its `daily` block, its named-day axis and its
provider-unit assertion -- are restated in `source.py`, each beside a comment naming its sibling.

The support's native-grid uniqueness check is likewise the plan validator's own
(`require_governed_lattice`), restated in `support_chunks` because two cells rounding to one
0.1-degree box would receive one another's value under `cell_selection=nearest`.

### Null cells, absence and refusal

98 of the 1,568 cells are ocean or out of domain, where ERA5-Land models nothing and the archive
answers `null` rather than zero. Exactly 1,470 carry a value on every one of the 1,556 immutable
days -- `scripts/vpd_snapshot_breakdown.py` and
`scripts/build_soil_moisture_from_canonical_snapshot.py` both pin EXPECTED_CELLS_PER_DAY = 1,470 and
both refuse a day holding any other number. So:

- **Zero values** is a REFUSAL (`source_unsettled`) unless a later settled day of the product is
  already published with values; only then is it a governed absence, and the marker carries that
  proof beside the day's receipt. Corrected 2026-09-02: calling an unmirrored day a governed absence
  states that the SOURCE had nothing, permanently, about a day that had simply not arrived. See
  "An all-null day is a refusal until the mirror is proven past it".
- **Exactly 1,470** publishes.
- **Anything between** is REFUSED, not published thin. A different count is a different land-sea mask,
  and a thin day would merge invisibly with 1,556 days that are not thin. The count is MEASURED for
  moisture and VPD and INHERITED for temperature, which rides the same lattice and the same model but
  was never counted independently -- so the first live temperature day proves or refutes the
  inheritance loudly. A persistent refusal means the mask really changed: re-measure
  `ERA5_LAND_VALUE_CELL_COUNT` and re-base the products, do not relax the check.
- **A failed chunk** refuses the whole day. 31 of 32 chunks would publish a day silently missing fifty
  cells.

### The turn deadline bounds every wait

`fetch_lane_capture` sleeps 70 s on a minutely quota refusal and 15/30/45 s on transport errors.
Left to `asyncio.sleep` those waits are unbounded by the turn, so one walled chunk could hold the
whole budget and the executor's SIGKILL would land on a writer holding a session lock.
`deadline_bounded_sleep` is passed as its `sleep`, and a backoff that would outlast the turn raises
`SoilTimeBudgetExhaustedError` -- deliberately NOT a `SoilSourceError`, so the driver reports
`time_budget_exhausted` for the day instead of turning a clock into a source failure.

### Entry point

`python -m agri_data_service.pipeline.direct.soil`, with `--product` naming one of `moisture`,
`temperature`, `vpd` or `all`, `--max-days` (default 1, max 5) days per product per turn,
`--time-budget-seconds`, `--run-id`, and the bounded retry and contention knobs. Days are taken
newest-settled-first and one at a time under the lane-day lock; a day already complete at every rung
is an idempotent no-op. Required runtime variables are the ordinary object-store settings and
`LOCAL_SOURCE_LOADER_DATABASE_URL` (or its existing fallback). The archive needs no key.

The lane extends the availability index the same way the climate writer does, and drains its own owed
retry claims per product -- activating it deactivates the eight generic
`parquet-soil-field-*`/`parquet-soil-temperature-*` lanes through `conflicts_with`, so nothing else
would ever come back for a claim it leaves behind.

The executor lane is `soil-era5-land-direct-forward`, hourly at :50 -- distinct from the climate
writer at :40, the fire and water writers at :15 and the SoilGrids warmer at :25. IT SHIPS IN
SHADOW: it is in no active lane list, and activation stays an explicit operator act through the
executor's allow-list variable.

## Drought

`drought/` is the reference geometry-lane direct writer: the first of the eight
`environmental_postgres_retirement_20260904` layers whose base rung needs a DuckDB spatial repair, not
only Polars. `postgres-drought`/`ingest-drought` (`_fill_drought` in `pipeline/parquet/lane_registry.py`,
reading `geo.drought_areas` through `pipeline/lanes/drought.py::export_drought_release`) are both
stopped (owner decision 2026-09-04) and never resume; this package replaces them for every day it
owns, `forward.py` for the newest settled release and `backfill.py` for the full 2022-08-09..settled
history, and PostgreSQL is never written by either.

### Two DuckDB spatial sessions, never merged into one

Every published day opens DuckDB spatial TWICE, guarded identically both times via
`foundation/parquet/duckdb_extensions.py::extension_directory_setting()` before the first `LOAD spatial`
-- the fix `152feca` shipped for the 2026-09-02 z9 outage
(`warehouse/parquet/tiers.py::_load_spatial`, copied verbatim into `support.py::_load_spatial` since
`tiers.py` is a read-only L1 module this L3 package may not import into):

1. `support.py::drought_geometry_session` repairs the BASE rung before it is ever written --
   `ST_Multi(ST_CollectionExtract(ST_MakeValid(ST_GeomFromGeoJSON(geojson)), 3))`, the exact chain
   `sql/ingest/store_drought_area.sql` runs in PostGIS, restated for DuckDB spatial because the direct
   fetch never passes through Postgres at all. A class that repairs to empty refuses the WHOLE release
   (`DroughtGeometryError`), matching the SQL file's own guard against storing a fabricated
   `MULTIPOLYGON EMPTY` coverage claim.
2. `warehouse/parquet/tiers.py::derivation_session`, opened by `fill_one_lane_day`'s default
   `derive_and_write_day_tiers`, simplifies the already-repaired base rung down to z9/z5/z0. This
   package never opens that session directly and never bypasses it -- the adapter (`adapter.py`) writes
   ONLY the z13 rung; `fill_one_lane_day` derives the rest, exactly as `fire_detections.py` does.

### The `provenance=` trap does not apply here, by construction

Neither writer ever constructs `TerminalEvidence` or passes `provenance=`. `data_receipts` and
`sha256`s come entirely from `gap_fill.py`'s own written-object ledger
(`_rung_objects_from_ledger`, `pipeline/parquet/gap_fill.py:2081-2082`:
`sorted(base.parts, key=lambda read: read.relative_path)`), which is populated by `store.write_partition`
inside `ObjectStore.recording_written_objects()` -- this package never lists or sorts parts itself for
a WRITE. `provenance` therefore defaults to `DIGESTED_PROVENANCE`
(`pipeline/parquet/availability_index.py:313`), never the bootstrap compiler's `manifest_trusted`. The
one place this package DOES list and sort parts is `parity.py`, and that is a READ, never a claim.

### The part sort in the one place this package builds one itself

`forward.py::_tier_status_for_weeks` and `backfill.py`'s reuse of it walk `store.list_partition_keys`
month by month and hand the raw key list straight to `foundation/parquet/paths.py::partition_day_statuses`,
which does its own parsing and ordering -- this package never re-derives a numeric part order from
`part-N` key text, so the unpadded `part-10` sorts-before-`part-9` trap
(`plantgeo-parquet-coarse-rungs-unbuilt`) has nothing here to reintroduce it into. A release is at
most five rows and one part in every observed case; the multi-part budget
(`pipeline/lanes/drought.py::_rows_per_part`, unchanged and unread by this package) is Postgres-side
machinery this writer does not need, since `rows.py` always builds one table per release and lets
`write_partition` split it into exactly one part-0 in the ordinary case.

### The `direct:` area_id, and why it is not a lineage column

`DROUGHT_SCHEMA.area_id` (`warehouse/schemas/drought.py:40-43`) is documented as "kept for provenance
back to the warehouse row; not part of the grain" -- but a direct-fetched row has no warehouse row
behind it. `rows.py::direct_area_id` builds `direct:<valid_date>:<dm_category>` instead of a Postgres
id: deterministic (idempotent republication produces byte-identical values), and namespaced so a
reader checking only the column's shape cannot mistake it for a real `geo.drought_areas.id` foreign
key -- the same discriminator discipline `climate`'s `source_snapshot_id`/`selected_source_release_id`
`direct:` prefix uses, restated here because `area_id` is a single opaque string with no
lineage-column sibling to carry the namespace instead.

### USDM's 404 is "not yet", never "never" -- the release_series mirrored-past pattern

USDM's archive answers a genuinely unpublished Tuesday with the SAME 404
(`ingest/usdm.py::fetch_drought_release` already turns it into `release=None`) whether the release is
merely late or was truly skipped. `adapter.py` applies the identical rule
`pipeline/direct/AGENTS.md` states above for climate/soil's all-fill days, at weekly grain instead of
daily: an unpublished Tuesday is `DroughtSourceUnsettledError` (a refusal, retried next tick) UNLESS a
LATER release Tuesday is already published, which proves USDM's cadence moved past it
(`forward.py::_mirrored_past_proof`, read off the same census snapshot the turn already paid for). In
practice this is a safety margin rather than a live path: `pipeline/parquet/lane_registry.py`'s
`DROUGHT_STREAM` registration measured 209/209 continuous releases from the 2022-08-09 floor through
2026-08-18, so a genuine historical gap has never been observed. The proof is read from a snapshot
taken at the TOP of the turn, so a release published earlier in the SAME turn does not yet count for a
later (older, if walking newest-first) week in that turn -- this under-proves rather than over-proves,
delaying a governed absence by at most one tick rather than ever fabricating one early.

### Backfill re-fetches USDM directly; it never reads `geo.drought_areas` for content

`backfill.py` reaches D2 parity by re-fetching every historical Tuesday from USDM's own dated-file
archive (`ingest/usdm.py::usdm_source_url`), the SAME source `ingest/usdm_history.py` walked to fill
`geo.drought_areas` in the first place -- not by reading Postgres. Three reasons, recorded so a future
reader does not "simplify" this into a Postgres read:

- **It is source-direct by construction.** This whole track's point is that Postgres is never read for
  content again; a backfill that read `geo.drought_areas` rows would be exactly the dependency D4
  retires.
- **The archive is not privileged information Postgres holds and USDM does not.** Every historical row
  in `geo.drought_areas` was itself fetched from this same per-date URL
  (`ingest/usdm.py::PostgresDroughtStore`); there is nothing in Postgres's copy that a direct refetch
  cannot reproduce.
- **Row-level parity is about presence and count, not byte-identical WKB.** `parity.py` compares day
  coverage and row COUNTS, never geometry bytes, so PostGIS's and DuckDB spatial's `ST_MakeValid`
  outputs are never required to agree bit-for-bit -- only their topology needs to (both are GEOS-backed).

`backfill.py::_owed_weeks_oldest_first` differs from `forward.py::_pending_weeks` in exactly one way:
a governed absence is ALWAYS re-examined on a backfill turn, never bounded to a recent recheck window,
because a full-history walk is already paying the census cost and an old wrongly-governed absence would
otherwise never be revisited by anything.

### `parity.py`: the one module in this package that reads Postgres, and only reads it

`parity.py::build_drought_parity_receipt` is the D1 parity receipt
(`conductor/tracks/environmental_postgres_retirement_20260904/spec.md`, "a counted comparison showing
the Parquet twin covers at least every day and row the PostgreSQL relation holds"): one inline,
one-table, no-join query (`SELECT valid_date, count(*) ... GROUP BY valid_date`, per
`code_styleguides/sql.md`'s convention for a query this small) against `geo.drought_areas`, compared
day-by-day and row-by-row against the written z13 rung's own completion markers. It is read-only --
`main()` explicitly rolls the Postgres session back rather than ever committing -- and it is the ONLY
module here `forward.py`/`backfill.py` never import from and never call: the write path must keep
working the day `geo.drought_areas` is dropped, and `parity.py` importing cleanly on its own is the
proof that dependency direction holds. A day with parts but no completion marker is reported as
`parquet_incomplete_days`, counted toward neither coverage nor a row total, matching
`pipeline/validation/drought.py::written_release_span`'s identical refusal to trust a half-finished
export as evidence.

### Entry points

- `python -m agri_data_service.pipeline.direct.drought` -- the forward writer, matching the climate/soil
  `__main__.py` contract exactly (`main`, `parser`, `parse_args`, one JSON report on stdout). No
  `--product`: this lane publishes exactly one stream. `--max-days` (default 1, max 5) is a release
  COUNT, not a day count -- each unit is one weekly USDM Tuesday, newest settled first.
- `python -m agri_data_service.pipeline.direct.drought.backfill` -- the oldest-first walker over the
  full floor-to-settled window, sharing every locked publish-and-verify function `forward.py` defines;
  it differs only in which weeks it selects and in which direction.
- `python -m agri_data_service.pipeline.direct.drought.parity` -- the read-only receipt; exits 1 when
  `parity_achieved` is false, so an operator or a CI gate can use the exit code directly.

Suggested executor lane slugs for the join agent (not registered here -- `pipeline/parquet/lane_registry.py`
and `execution/job_executor_service.py` are both this worker's forbidden files): `drought-direct-forward`
alongside the climate writer's :40 and the fire/water writers' :15 pattern, and a separate
`drought-direct-backfill` lane run at low frequency (backfill's own census walks ~48 months of listings
per turn -- `DROUGHT_BACKLOG_SCAN_WEEKS` bounds the FORWARD writer's scan to the newest 60 weeks
specifically so the hourly forward tick never pays that cost). Both should ship SHADOW, matching every
other direct writer in this directory, with `conflicts_with` declared against the retired `drought`
generic spec exactly as the NASA POWER writer declares it against its eight generic specs.

### Known simplifications, recorded rather than hidden

- **The turn deadline is a coarse per-week gate**, checked before each release's fetch/lock cycle
  (`time.monotonic() >= deadline`), not threaded into every `asyncio.sleep` the way
  `soil/source.py::deadline_bounded_sleep` bounds ERA5-Land's 70s quota waits. USDM's per-release
  fetch is one small JSON file with no comparable long single wait, so this is a deliberate
  proportionality call, not an oversight -- revisit if USDM ever adds a slow endpoint or aggressive
  rate limiting this lane must wait through.
- **`backfill.py`'s per-turn R2 census lists the FULL 209-week window every turn** (`release_weeks(lane.history_floor,
  settled_through)`), unlike `forward.py`'s `DROUGHT_BACKLOG_SCAN_WEEKS`-bounded scan. Acceptable for a
  low-frequency backfill utility; would need its own bounding if ever scheduled hourly.

## Weather observations

`weather_observations/` publishes the ONE `weather-observations` stream: Open-Meteo current-conditions
point readings, twelve columns, no per-product fan-out and no lineage columns at all -- the simplest
row shape of any lane in this directory. Built for owner decision D4
(`environmental_postgres_retirement_20260904`) after that track's second grill stopped the
`postgres-weather` executor lane, so the layer stopped advancing on the live map; this writer is what
un-freezes it.

### The name collision, again, and which producer this actually is

`weather-observations` is overloaded exactly as `warehouse/schemas/weather_observations.py`'s own
module docstring says: the governed NASA POWER / ERA5-Land archive behind `agri.signal_observation`
is a DIFFERENT plane, already exported by the `signal` stream and already given a direct writer above
(the climate section). This module exports the OTHER producer -- `ingest/open_meteo.py`'s
`WEATHER_LAYER`, which polls Open-Meteo's *current-conditions* forecast endpoint
(`https://api.open-meteo.com/v1/forecast`, never the archive host) and, on the retired Postgres path,
wrote into `geo.features` through `ingest/writer.py::ingest_features`. Confirmed the same way the
schema module confirms it: `build_weather_write` returns a `FeatureWrite`, never a signal-plane row.

### There is no archive endpoint, so there is no settled-day fetch

Every other writer in this directory (climate, soil, and both single-module ones) asks a source for
ONE PAST DAY and gets it back complete. `current_weather_url` (`ingest/open_meteo.py:333`) has no
`start_date`/`end_date`/`past_days` parameter at all -- it returns only the instant closest to now,
accepted solely when it is within `MAX_OBSERVATION_AGE` (three hours) of the poll. There is no day
this lane's writer can ask the source for by date; every "day" downstream is assembled by BUCKETING
whatever instants repeated polls, over time, happen to return. That is why `source.py` carries no
`--product` enumeration, no settled-day retry ladder and no `mirrored_past_proof` machinery: none of
climate/soil's "is this day settled yet" questions are askable of an endpoint that only ever answers
"now".

**Consequence for `--max-days`.** The forward CLI still exposes `--max-days`, matching the climate/soil
contract shape a reviewer expects, but it means something different and says so in `forward.py`'s
module docstring: it caps how many of the AT MOST TWO day-buckets one poll produced are actually
published (a poll can straddle the UTC midnight boundary and see readings dated on both sides of it),
never a backlog depth. `WEATHER_OBSERVATIONS_MAX_DAYS = 2` is a structural ceiling, not a tuned budget.

### The acquisition model is water-gauges' shape, not climate's -- borrow that adapter instead

Because a day is filled by many incremental polls rather than one complete fetch, `adapter.py` ports
`water_gauges.py::merge_water_gauges_day` / `DirectWaterGaugesForwardAdapter` almost directly: retain
every published row, refresh a repeated grain's source columns, append every unseen grain, replay a
pre-mutation merge on an `incomplete` status instead of re-deriving it. The one simplification: this
lane never needs water-gauges' "ambiguous refresh, refuse" branch. `bounded_sample_points` returns the
identical float coordinates on every call for one bbox and spacing, so a repeat grain match is always
the SAME point reporting the SAME instant again -- never an ambiguous historical duplicate the way two
NWIS releases at one timestamp can be. A match therefore always refreshes cleanly.

`WEATHER_OBSERVATIONS_PROVENANCE_COLUMNS = ("ingested_at",)` is never refreshed on a repeat grain
match, for the same reason water-gauges holds its provenance columns back: `ingested_at` is when THIS
writer first captured the reading, and overwriting it on a later poll of the same instant would erase
that fact for a reading that did not change.

### The day key is a substring, and here it is provably a UTC date too

Matches the project-wide rule (`pipeline/direct/AGENTS.md`, water-gauges section, and
`drizzle/0018_fire_discovery_observation_day.sql:46-48`): `geo.feature_observation_day` takes
`substring(properties ->> 'observedAt', 1, 10)`, never an instant cast, because an instant cast moved
6,279 of 16,743 water-gauge rows onto the wrong day. `rows.py::_observation_day` reproduces the exact
substring rather than calling `.date()` on the parsed instant.

Unlike NWIS's `updatedAt` -- which is not always UTC-rendered by its publisher, hence the trap -- this
lane's `observedAt` is ALWAYS produced by `format_javascript_timestamp`, which unconditionally converts
to UTC before rendering (`ingest/identity.py:126-136`). So the substring and a UTC-truncated instant
can never disagree FOR THIS PRODUCER specifically; the substring is still what is implemented, because
matching the rule the warehouse actually evaluates beats matching a proof about the rule.

### `feature_id` on a direct row: a `direct:` token, not a fabricated UUID

The schema documents `feature_id` as `features.id::text` -- a real `geo.features` UUID -- for every
Postgres-sourced row, and `TierDerivation.base_non_null_columns` forbids leaving it null at the base
rung. A direct write has no `geo.features` row to cite. `rows.py::_feature_id` carries
`f"direct:{external_id}"` instead, where `external_id` is already the row's full
`{lat4dp}:{lon4dp}:{observedAtISO}` identity, so no extra hash is needed and the value is deterministic
across retries. A `geo.features.id` is a bare UUID and never contains a colon, so this can never
collide with a real one; a reader must check the prefix before treating `feature_id` as a Postgres
foreign key, the same discipline "Direct lineage namespace" above states for the climate/soil shapes.

### The support is a computed grid, not a warehouse dimension

Unlike climate/soil's `agri.spatial_cell`-pinned lattices, this lane's sample points were never a
stored dimension: `ingest/open_meteo.py::bounded_sample_points` computes a grid from `INGEST_BBOX` and
a spacing AT CALL TIME, and every historical `geo.features` row for this layer was written by exactly
that function. `support.py::weather_sample_points` reuses it verbatim rather than inventing a
`spatial_cell` grid_name, so a direct-written day samples the identical points a Postgres-era ingest
tick would have.

### Backfill is the existing, untouched Postgres adapter -- nothing new was built for it

Same pattern as `fire_detections.py`: `pipeline/lanes/weather_observations.py` and its
`LaneRegistration` in `pipeline/parquet/lane_registry.py` are left exactly as they were. That adapter
already reads settled days out of `geo.features` and republishes them to Parquet
(`export_weather_observations_day`), which is precisely what D2's backfill bar needs -- it remains the
mechanism an operator runs (`parquet-drain --selection missing --layer weather-observations`, per
`conductor/layer-sessions/weather-observations.md` section 1) until the day this writer's forward
adapter is swapped in as the registered one, exactly as the climate/soil sections describe for their
own generic-spec conflicts. No new backfill code was written in this package because none was owed:
the gap is in what schedules the existing adapter, not in the adapter itself.

### The parity receipt

`parity.py::build_parity_receipt` is READ-ONLY on both sides -- it opens a PostgreSQL session and the
object store, counts, and writes to neither. Postgres is the ground list (D2 only requires Parquet to
cover what Postgres ALREADY holds; a Parquet-only day, such as any day this writer publishes after
Postgres ingestion stopped, is not under-coverage and is deliberately not computed, since doing so
would require a whole-stream `list_partition_keys()` -- the exact A4 tripwire this track's acceptance
criteria forbid, for a number D2 does not ask for). Its `_POSTGRES_DAY_COUNTS_SQL` mirrors
`sql/pipeline/weather_observations_day_export.sql`'s WHERE clause and key-presence guard exactly, so
the count matches precisely what the existing Postgres adapter would itself export. Run it with
`python -m agri_data_service.pipeline.direct.weather_observations.parity`; it exits 1 on
`under_coverage` so an operator's drop-packet script can gate on it directly.

### Entry point

`python -m agri_data_service.pipeline.direct.weather_observations`, with `--bbox` (defaults to
`INGEST_BBOX`), `--max-days` (default 2, the same as the structural ceiling -- a lower default silently drops the older bucket at the UTC-midnight straddle and those readings cannot be re-fetched), `--time-budget-seconds`,
`--run-id`, and the bounded retry and contention knobs, following the climate/soil `__main__.py`
contract shape. One poll per invocation; every named day the poll touched is merge-published under its
own lane-day lock, newest first. Required runtime variables are the ordinary object-store settings,
`LOCAL_SOURCE_LOADER_DATABASE_URL` (or its existing fallback), and `INGEST_BBOX` (or `--bbox`).
Open-Meteo's current-conditions endpoint needs no key.

**No `products.py`, deliberately.** Every other multi-column writer here (climate, soil) has one
because it enumerates several streams with divergent row shapes or source parameters. This lane
exports exactly one stream at one row shape, so a `products.py` would hold nothing but the constants
`rows.py`/`adapter.py` already carry -- the module was omitted rather than shipped empty.

**No governed-absence path in the forward writer, deliberately.** A live poll can only ever prove "the
source answered nothing THIS INSTANT", never "the source had nothing for this whole day" -- the
absence-with-mirrored-past-proof machinery climate/soil carry answers a question ("has the archive
moved past this day") that has no counterpart for an endpoint with no archive. A day the poller never
ran for (an outage, or a day before this writer's deployment) is not retroactively fetchable from this
source at all, so it surfaces through the ordinary gap census as `missing` -- which is exactly the
governed gap census D2 asks for, not a silent hole and not a failure this writer should paper over with
a fabricated absence marker.

**Proposed executor lane** (not wired -- `pipeline/parquet/lane_registry.py` and
`execution/job_executor_service.py` are outside this package's ownership): `weather-observations-direct-forward`,
on a schedule distinct from the climate writer at :40, the fire and water writers at :15, the SoilGrids
warmer at :25 and the soil writer at :50 -- :20 or :35 are both free. Should ship in shadow, activated
only through the executor's allow-list variable, matching every sibling in this file.

## Vegetation NDVI

`vegetation/` un-freezes the `postgres-vegetation` layer the 2026-09-04 lane stop left stranded
(`conductor/tracks/environmental_postgres_retirement_20260904/spec.md`, "Why this lane exists").
Unlike every other writer above, this lane already HAD a working Postgres-reading exporter
(`pipeline/lanes/vegetation.py::export_vegetation_day`, registered as `_fill_vegetation` in
`pipeline/parquet/lane_registry.py:538-552,867-882`) with 990 of 1,195 base days already published.
This package therefore ships TWO drivers with two different jobs, not one:

- `forward.py` -- a genuine source-direct writer (Earth Search STAC + windowed COG reads via
  `ingest/vegetation.py::collect_ndvi_grid_records`, reused byte-for-byte) that owns every day
  STRICTLY AFTER `products.py::VEGETATION_DIRECT_WRITER_START_DAY` and never touches Postgres.
- `backfill.py` -- reaches D2 parity for every day AT OR BEFORE that boundary by calling
  `fill_one_lane_day` with the EXISTING, UNCHANGED registered adapter (no `replace(lane, adapter=...)`),
  so historical days keep being computed by the already-reviewed Postgres path rather than being
  re-derived from raw Sentinel-2 a second time under the same grain.

### Same lattice `soil/` borrows, read a second time rather than imported

`support.py` reads `agri.spatial_cell WHERE grid_name = 'sentinel2-ndvi-0p25deg'` -- the IDENTICAL
1,568-cell lattice `pipeline/direct/soil/support.py` already reads for ERA5-Land ("Support cells come
from the dimension the history was built from", above). `vegetation/support.py` does not import
`pipeline.direct.soil`: two sibling direct-writer packages sharing one dimension each carry their own
copy of the count/step/offset guard rather than an import across a B-worker package boundary. The
0.25-degree centroids sit at ODD multiples of 0.125 (43.1250, not 43.0 or 43.25) -- this track's
brief names the trap by name, and `test_vegetation_support.py` pins it with a synthetic full 56x28
grid rather than trusting a handful of hand-picked points.

**The prefix join, not a coordinate re-derivation.** `ingest/vegetation.py::ndvi_grid_cells` mints an
UNPREFIXED raw `cellKey` (`"{lat}:{lon}"`, anchored on the global origin, never on a bbox), while
`agri.spatial_cell.cell_key` carries the `sentinel2-ndvi-0p25deg:` prefix. `VegetationSupport.resolve`
prepends the prefix and does an exact string-keyed lookup -- no nearest-neighbour, no re-quantization
of the fetch response's own echoed coordinates, because the raw record never carries any (unlike
climate's `_require_echoed_point`, which exists precisely because POWER echoes a point back).

### Sparse-by-construction: the base-rung acceptance rule inverts soil/climate's

`soil`/`climate` refuse a day unless EXACTLY the expected value-cell count is present (a "thin day" is
refused, not published). Sentinel-2 revisits the whole support box roughly every 5 days and cloud
screening widens that to a MEASURED median 7-day gap (`pipeline/parquet/lane_registry.py:880-881`), so
a settled day filling 40 of 1,568 cells is the ordinary shape of real data, not a partial fetch.
`source.py::fetch_vegetation_day` therefore accepts ANY count greater than zero; only a WHOLE-GRID-EMPTY
day is treated as possibly unsettled, through the same "mirrored past" proof `soil`/`climate` use for
an all-null/all-fill day -- restated here for "zero cells filled" rather than "every value null".

### The exporter's zero-row path is NOT a gap — corrected 2026-09-04

An earlier revision of this section claimed `pipeline/lanes/vegetation.py::export_vegetation_day` had a
defect: its docstring promises a source-empty day "is a governed absence... recorded with
`store.write_absence`", while its body only calls `store.write_partition`, which refuses a zero-row
table. Two review passes confirmed that code reading. **The conclusion drawn from it was still wrong,
and the workaround built on it has been deleted.**

The raise IS the signal. Traced end to end:

- `pipeline/lanes/vegetation.py:86` calls `store.write_partition(table, ...)`.
- `pipeline/parquet/objectstore.py:551` raises `EmptyPartitionError` on a zero-row table.
- `pipeline/parquet/gap_fill.py:1174` catches **exactly that class** and calls `_govern_absent_day`.
- `gap_fill.py:1198` marks all four rungs absent, coarse-first and base-last, rolling the ladder back
  as a unit if any rung refuses.
- `gap_fill.py:1704` extends the availability index with `terminal_state="governed_absence"`.

So the canonical path already produces a durable, indexed, four-rung governed absence carrying the
exporter's own zero-row result as its proof. The docstring is imprecise about *who* writes the absence
— the caller does, not the function — but the outcome it promises is exactly what happens. Nothing in
`pipeline/lanes/vegetation.py` needs fixing; only its docstring is loose.

`backfill.py::_postgres_has_rows` and `_DAY_HAS_GOVERNED_ROWS_SQL` were the workaround for this
imaginary gap, and they caused a real one: by intercepting a zero-row day *before* the mechanism that
would have settled it, they turned every Postgres-empty day into a `no_governed_rows` entry that wrote
nothing durable, so the walker re-selected the same oldest days every turn and could never advance.
Both are deleted. Every day now goes through `fill_one_lane_day`.

The rejected alternative, for the record: writing the absence inside `backfill.py` would have
desynchronised the marker from the index. `availability_index.py:2320` cross-checks them —
`if absence.reason != evidence.absence_reason: raise AvailabilityConflictError` — and
`gap_fill.py:1718` hardcodes `absence_reason=zero_row_absence_reason(lane.slug, day)`. A bespoke reason
would have failed that check; a copied one would have been a lie about what was measured.

**Lesson worth keeping:** a docstring that misattributes a responsibility reads exactly like a missing
implementation. Trace the caller before building a workaround.

### Entry points

Forward: `python -m agri_data_service.pipeline.direct.vegetation`, with `--max-days` (default 1, max
5), `--time-budget-seconds`, `--run-id`, and the bounded retry/contention knobs -- the identical
contract `climate`/`soil` expose, minus `--product` (this lane registers exactly one stream). Days are
taken newest-settled-first, one at a time under the lane-day lock, strictly after
`VEGETATION_DIRECT_WRITER_START_DAY`.

Backfill: `python -m agri_data_service.pipeline.direct.vegetation.backfill`, with `--max-days`
(default 5, max 30) and `--time-budget-seconds`. Days are taken OLDEST-FIRST across
`[registered history_floor, VEGETATION_DIRECT_WRITER_START_DAY)` -- a fixed historical debt with no
settled edge to chase, unlike the forward walk.

Parity: `python -m agri_data_service.pipeline.direct.vegetation.parity`, with an opt-in `--count-rows`
that also reads every published base-rung day (slow on a multi-year history; the default run only
lists partition keys). Reads Postgres and lists/reads Parquet; writes neither. This is the D1 parity
receipt and the operator's confirmation gate for `VEGETATION_DIRECT_WRITER_START_DAY` itself: if the
newest day Postgres's governed plane holds is LATER than that constant, the constant must be raised to
match before the two writers' domains are treated as non-overlapping.

Both direct-fetch entry points extend the availability index the same way `climate`/`soil` do
(`fill_one_lane_day` is passed `availability_storage=BotoAvailabilityStorage.from_settings()`), and
NEITHER passes `provenance=` anywhere: `adapter.py` hands `store.write_partition`/`store.write_absence`
a table or a `GovernedAbsence`, and the shared finalizer builds every `TerminalEvidence` from the real
written-object ledger, so provenance defaults to `digested` by construction -- see this track's brief,
"CRITICAL TRAP", and D3 above.

The forward lane is unregistered with the executor as of this writing (`execution/job_executor_service.py`
is out of this package's ownership); a join agent registers it, e.g. as
`vegetation-sentinel2-ndvi-direct-forward`, distinct from `climate`'s `:40`, `soil`'s `:50` and the
fire/water writers' `:15`. IT SHIPS IN SHADOW like its siblings: activation stays an explicit operator
act through the executor's allow-list variable, and only after `parity.py` confirms
`VEGETATION_DIRECT_WRITER_START_DAY` (2026-09-05) is correctly placed against the real handoff.
