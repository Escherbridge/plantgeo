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

Only a day in which EVERY support cell reports a fill is a governed absence, and its marker carries
the receipt: the sha256 over the day's concatenated response digests, the sha256 over its request
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

- **Zero values** is a governed absence with the day's receipt. That is the honest state of a day the
  archive has not mirrored yet, which is also why the lag is nine and not five.
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
