# Execution modules

`source_ingestion.py` is the phase-one operational vertical slice for a governed, locally captured current-observation release: a bounded GeoJSON payload is structurally validated, checkpointed locally, then persisted idempotently as a source release, content-addressed artifact, and validated release set. It is intentionally not an upstream fetcher, generic data loader, forecast, trainer, or public prediction publisher. See `docs/data-ingestion-and-serving-contract.md` for the server/local ownership boundary.

Source-ingestion checkpoint v2 binds both the complete reviewed plan and the release-set content checksum. New release sets must be populated while `draft` and transition to `validated` only after their membership is flushed, because the warehouse trigger freezes membership after validation.

`source-ingest` requires `LOCAL_SOURCE_LOADER_DATABASE_URL` to name `plantgeo_loader` on the dedicated local Compose target at `127.0.0.1:5442/plantgeo`; it rejects the `plantgeo_owner` bootstrap role and never falls back to the service `DATABASE_URL`. Before any checksum, checkpoint, or artifact write, it performs a bounded whole-document GeoJSON custody scan (50,000 JSON nodes, depth 32) and rejects canonicalized credential field names/suffixes plus Bearer/Basic authorization strings. It does not silently redact an immutable source artifact.

`promotion.py` is an offline semantic lineage bundle contract for already validated phase-one release sets. It re-applies the same bounded GeoJSON custody validation to embedded source artifacts, verifies hashes and supersession closure, and creates only a trigger-safe draft → membership → validated restore plan. It is not a general `pg_restore` wrapper, database exporter, restore CLI, or Railway job; those remain a separately reviewed private-control-plane integration.

`historical_backfill.py` owns deterministic, bounded NASA POWER daily request and response contracts for the initial four-year meteorology baseline. It validates the exact four-calendar-year window, canonical sampling-point plan, per-source query, response payload size, UTC observation timestamps, missing values, coverage accounting, a checksum-bound complete local receipt checkpoint, and raw response cache. The cache is written only after complete validation and before a warehouse transaction, so retried writes never re-request a successful source response. A later NASA finalization can only rebind a complete source replay to an advanced release-set as-of time; it never refetches or rewrites source receipts. It never carries credentials, opens a database connection, selects an ingestion geography, or publishes to Railway.

`NASA_POWER_SIGNAL_SPECIFICATIONS` carries the three POWER soil-wetness parameters (`GWETTOP`, `GWETROOT`, `GWETPROF`) alongside the meteorology baseline because POWER is keyless, whereas the ERA5-Land soil path in `historical_era5.py` is gated on a Copernicus dataset licence that only the account holder can accept in a browser. The two soil streams are complementary, not interchangeable, and must never be unit-mixed: POWER reports a MERRA-2 **degree of saturation** in `fraction_of_saturation` (0 = dry, 1 = saturated), while ERA5 `soil_water_content_layer_1` reports a **volumetric** water content in `m^3/m^3`. Depth support is named in the signal (`soil_wetness_surface` = top 5 cm, `soil_wetness_root_zone` = top 100 cm, `soil_wetness_profile` = the full modelled column), matching the existing ERA5 `soil_temperature_level_1` convention. `support_key` is not a depth discriminator: the NASA, CDS and USDM lanes all write `surface`, and the one lane that writes something else (`historical_open_meteo.py`, `era5-land-0.1deg`) uses it to distinguish *spatial* support, not depth. Adding a signal name needs no migration: `agri.signal_observation.signal_name` is a plain `varchar(150)` with no enum or check constraint. Extending the ML covariate vector is a separate, reviewed change — `agri.covariate_feature_schema` pins its signal list to the immutable `agri_covariates_v1` version, so new signals are deliberately invisible to training until a new schema version is authored.

`historical_parquet.py` converts only a complete local NASA raw-receipt set into an immutable, compressed daily Hive-partitioned Parquet dataset. It stages one bounded source-cell file at a time, caps DuckDB to one thread and 1 GB with a build-local spill directory, and atomically publishes a manifest-bound dataset. An interrupted conversion reuses its single target-bound build directory only after each staged cell's row count, key, and payload checksum are revalidated against the raw receipt; ambiguous or mismatched staging fails closed. Successful publication removes staging. It is intentionally a local cold-history store; it never requests an upstream API, writes PostgreSQL, or promotes a full history to Railway.

`historical_era5.py` owns cache-first CDS capture for the governed ERA5-Land plan. It treats each calendar month as one immutable ZIP artifact, validates every planned point/variable/day before advancing the durable checkpoint, and requires local CDS credentials only for a missing cache entry. Its requested one-degree points remain point samples; they never claim the product's native 0.1-degree grid or acre-scale precision.

## `historical_open_meteo.py` -- the Open-Meteo ERA5-Land archive lane

A read of the **same ERA5-Land product** as `historical_era5.py`, at its **native 0.1 degrees**, over
the 1,568-cell `sentinel2-ndvi-0p25deg` analysis lattice. Keyless by default; an optional
`OPEN_METEO_API_KEY` buys quota, not access (see "Paid access is environment, not plan" below).

### Why this lane exists at all

`HistoricalEra5LandBackfillPlan.require_governed_monthly_coverage` rejects any cell whose centroid
is not a multiple of the reviewed 1.0-degree output grid. Every NDVI lattice centroid sits on
`.125`/`.375`. The CDS contract therefore **structurally cannot** address these cells; the lane that
covers them has to be a different one. Those cells already exist in `agri.spatial_cell` with zero
signal rows, which is why the ML covariate layer returns all-NULL there. This lane mints no spatial
cells: `_require_open_meteo_spatial_cells` fails closed if a reviewed `cell_key` is absent or sits
on a different `grid_name`.

The archive endpoint's `models` parameter defaults to `era5` at 0.25 degrees. `models=era5_land` is
mandatory and is pinned in the contract as a `Literal`, not a default an operator can drift.
`cell_selection=nearest` is likewise pinned so a coastal request can never be silently relocated to
a land cell the lattice does not name.

### Naming: one physical quantity, one name

The moisture layers carry the **same `signal_name`** as the CDS lane
(`soil_water_content_layer_1/2/3`) and the same unit `m^3/m^3`. A third name for the same variable
would invite a model to treat one feature as two independent ones. What differs is modelled by the
schema already:

| Axis | CDS lane | This lane |
|---|---|---|
| `data_source.key` | `era5-land` | `open-meteo-era5-land-archive` |
| `support_key` | `surface` | `era5-land-0.1deg` |
| licence snapshot | CC-BY (Copernicus) | CC-BY 4.0 (Open-Meteo) over Copernicus/ECMWF |
| provenance strength | first-party CDS receipt | **intermediary redistribution** |

`support_key` here carries **spatial** support, not depth: every other historical lane writes
`surface`, so `era5-land-0.1deg` is what lets a reader tell a 0.1-degree ERA5-Land sample from a
0.5-degree NASA POWER sample of the same cell.

Open-Meteo is an intermediary. The source registration says so in its citation and in
`data_source.configuration.provider_role = intermediary_redistributor`, and every source release
repeats it in `quality_summary`. This is deliberately not dressed up as an ECMWF receipt.

None of this may ever be blended with NASA POWER's `soil_wetness_{surface,root_zone,profile}`, which
is a MERRA-2 **degree of saturation** (`fraction_of_saturation`), a different physical quantity.
Distinct `support_key` values are what make the two safely coexist on one cell-day.

### Every value is bounded, and an out-of-range value fails the chunk

`OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS` carries an inclusive `[minimum, maximum]` per variable --
`[0.0, 1.0]` m^3/m^3 for volumetric water content, `[-100.0, 70.0]` C for temperature -- and
`_archive_values` rejects anything outside it. This follows the current-weather precedent in
`ingest/open_meteo.py` (`CURRENT_VALUE_BOUNDS` / `_bounded_value`).

The finite check alone is not enough: `-999` and the netCDF `_FillValue` `9.969e36` are both finite
floats. Without a range, a provider that emitted a sentinel instead of `null` for an out-of-domain
cell would write 1,462 rows per cell with `is_observed=true`, `quality_flag='accepted'`,
`coverage_fraction=1`, a `complete` coverage audit, a valid checksum and a finalizable release set.
Every structural guard would pass.

An out-of-range value **fails the chunk** rather than being downgraded to `no_data`. `no_data` is a
positive claim -- "the provider modelled nothing here" -- earned by an honest `null`; a sentinel is
evidence the provider malfunctioned, and recording it as a modelled gap would assert something no
one measured. A failed chunk gets no receipt, so it stays pending and resumable, which is the same
failure mode this lane already uses for a truncated response.

### Soil temperature is deliberately excluded -- measured, not assumed

`OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS` defines `soil_temperature_0_to_7cm_mean`, but no reviewed
plan requests it. Measured 2026-08-06 against a first-party CDS `derived-era5-land-daily-statistics`
retrieval on the product's native 0.1-degree grid, over the 16 Boise probe cells:

| Quantity | Provider output precision | Rounding half-width | Measured MAE | Measured max abs error |
|---|---|---|---|---|
| `soil_moisture_0_to_7cm_mean` | 3 dp | 0.0005 | **0.00024 m^3/m^3** | **0.00050 m^3/m^3** |
| `soil_temperature_0_to_7cm_mean` | 1 dp | 0.05 | **0.45 C** | **1.30 C** |

Moisture's entire residual is display rounding: the max error is below the rounding half-width, so
the underlying values are identical to Open-Meteo's output precision. Temperature's error is 9x the
rounding bound at the mean and 26x at the maximum, so it is a real systematic difference -- Open-Meteo
applies its own elevation handling, which moves a temperature and cannot move a dimensionless
volumetric water content. Until that difference is explained and bounded, temperature from this lane
would be a different variable wearing the CDS lane's `soil_temperature_level_1` name. It is not
ingested.

### VPD is an atmospheric covariate riding the same lane as the soil-state ones

`vapour_pressure_deficit_max` (kPa, `[0.0, 15.0]`) is the first `OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS`
entry that is not soil state. It has no native ERA5-Land/CDS variable to align its `signal_name`
to -- Open-Meteo derives it from the same `era5_land` hourly fields, it is not a first-party
reanalysis output -- so the warehouse name drops only the daily-aggregation suffix, matching the
`soil_temperature_level_N` precedent: `vapour_pressure_deficit_max` -> `vapor_pressure_deficit`.
No unit conversion applies (`kPa` in, `kPa` out), same as every other entry in this table.

`plans/open-meteo-era5-land-pnw-vpd-20220430-20260430.json` mirrors the soil-temperature lattice
plan cell-for-cell (`source`, `model`, `native_grid_*`, `support_key`, `window` are byte-identical)
and is unrun: `release_set_as_of` is the same far-future placeholder convention as the soil-temperature
and moisture lattices, not a completion forecast. See plans/AGENTS.md.

`source.key = "open-meteo-era5-land-archive"` is shared identity across every plan in this lane, and
`_ensure_data_source` in `historical_writer.py` raises "already governed by different metadata" if a
plan's `source` block disagrees with the `data_source` row an earlier plan already persisted. The
registered `purpose` text ("...soil-state covariates...") therefore stays byte-identical here even
though it no longer describes every parameter this lane carries; correcting it is a coordinated edit
across every sibling plan file's `source` block, not a one-plan change.

### Two checksums, on purpose

The wire response carries `generationtime_ms`, a per-request server timing metric. Left in, every
refetch would produce a new `payload_checksum` and therefore a second source release for identical
content. `_canonical_archive_document` removes exactly that key and canonicalizes the rest; the
result is the artifact's bytes and `source_release.payload_checksum`. The **exact wire bytes'**
digest and length are preserved in `quality_summary.wire_payload_checksum`, in the artifact's
`metadata_json`, and in the local raw-cache receipt, so custody of what actually arrived is not lost.
Artifacts are `database_inline` (~360 KB per 16-cell chunk of JSON, TOAST-compressed) rather than the
ERA5 lane's `local_raw_cache`, because a keyless source's document is small enough to keep durably.

### Accounting, not completeness

`require_accounted_open_meteo_result` is the guard against a chunk that dropped records reporting
success. It does **not** demand completeness -- ocean and out-of-domain cells legitimately have no
data -- it demands that every requested `(cell, parameter)` series is explained by exactly one
coverage row spanning the whole window, and that the fact rows match those rows exactly.

- A series with **no** value on any day writes **zero** observation rows and one
  `signal_coverage_audit` row with `status='no_data'`. That is one honest gap record, not 1,462
  `is_observed=false` rows, and matches the CDS lane's own precedent for out-of-domain cells.
- A series with **some** missing days writes an explicit `is_observed=false`,
  `quality_flag='source_missing'`, `normalized_value=NULL` row for each and
  `status='partial'`. Partial stays partial.
- Nulls are never coerced to zero. A verified ocean point returns 1,462 nulls, and a zero volumetric
  water content would be a physically meaningful (bone-dry) claim.

Observations are bucketed by the **ISO date prefix the publisher named** (`date.fromisoformat` on the
first ten characters), never by recasting an instant, and stored as that day at 00:00Z.

### Rate limiting is the binding constraint

Open-Meteo's free tier weights a request by locations x variables x timesteps, so chunk size changes
request count but **not** total quota cost. A refusal is classified from the 429 body:

- `minute` -> retried up to `MAX_FETCH_ATTEMPTS` with a 70 s wait. It is the only scope in
  `RATE_LIMIT_BACKOFF_SECONDS`.
- `hour` / `day` / **unrecognised** -> not retried. Sleeping through an hourly wall turns a quota
  refusal into an unexplained hang; the chunk fails immediately carrying the provider's own wording.

`unknown` used to sleep 3 x 120 s. It no longer does. The lesson of the daily-wall bug is that an
unretryable wall must not be slept through, and an unrecognised body is far likelier to be a
reworded wall than a transient blip -- against a keyless quota, guessing wrong burns four requests
and six minutes for nothing. It fails closed instead, and `_rate_limit_scope` classifies
least-retryable-first so an ambiguous body ("Daily ... try again in 60 minutes") resolves to `day`.

A failed chunk gets **no receipt**, so it stays pending in the checkpoint and
`historical-open-meteo-backfill` exits non-zero with the failed chunk keys listed. The checkpoint is
the resume point: `historical-open-meteo-status` reports what is still outstanding, and re-running
the backfill fetches only that.

Chunk boundaries are part of the plan checksum, so changing `chunk_cell_count` is a new plan rather
than a resume that straddles two chunk shapes.

### Paid access is environment, not plan

A Professional subscription lifts the quota wall above. `OPEN_METEO_API_KEY` is read from
`os.environ` at fetch time and appears in **no** plan field, so it does not enter `plan_checksum`.

That was the deciding argument. A key is a credential and an access path, not a property of the data
requested: the same cells, window, model and variables come back either way. Putting it -- or a host
field -- in the plan would change `plan_checksum`, which orphans
`historical-open-meteo/<checksum>.json` and its entire `raw/` cache, forcing a re-fetch of a
quota-bound dataset. Paying for quota must not cost a re-fetch, and it must not silently invalidate
the already-validated 16-cell probe. Absent stays fully supported: the free host is the default and
the published repo has no key.

Provenance is served **per release** instead, where it costs no checksum. Each retrieval carries the
host that answered it (`OpenMeteoArchiveCapture.request_base_url` ->
`HistoricalOpenMeteoRawCacheReceipt` -> `OpenMeteoArchiveChunkResult`), and
`_ensure_open_meteo_source_release` records `open_meteo_archive_chunk_url(plan, chunk,
base_url=result.request_base_url)` in `source_release.query_parameters.request_url`. The key is
never part of that URL.

The host is threaded through the cache receipt rather than re-resolved at persist time because
`historical-open-meteo-persist` **replays the local cache**. Re-resolving would let a keyless
retrieval be written up as a paid one the moment an operator exported a key -- a mixed-host crawl is
the normal case when a run starts free, walls, and resumes keyed. `request_base_url` is additive
inside `schema_version: 1` on the raw cache receipt: bumping the version would invalidate the probe's
existing 718 KB cache, and a receipt written without the field provably predates the paid host, so
the free host is derived rather than defaulted. `require_archive_base_url` admits only the two
reviewed hosts, since a cache receipt is a file and therefore untrusted input.

Re-persisting the same bytes under a different host raises "already governed by different metadata"
from the existing `query_parameters` comparison. That is correct: the provenance really did change,
and it can only happen if someone deletes the cache, re-fetches, and re-persists.

**Where the key may and may not appear.** May: the process environment, and the wire URL built by
`archive_daily_request`. May not: a plan, a checkpoint, a raw cache receipt, an artifact, a log line,
a test fixture, or any warehouse column. `reject_sensitive_fields` runs over `query_parameters`
immediately before the release INSERT -- the export path (`_validate_metadata` in
`historical_promotion.py`) already refuses a credentialed URL, but it runs long after the row is
permanent, so the same check is applied at the moment the value would become durable.

### Checkpoint `state` is re-derived on load, never trusted

`rederive_historical_open_meteo_checkpoint_state` recomputes `state` from receipt completeness
(none -> `initialized`, all -> `validated`, otherwise `running`) every time a CLI command loads a
checkpoint. A `blocked` checkpoint whose chunks are all receipted would otherwise be unrecoverable:
nothing is outstanding, so no run can move it off `blocked` and persistence would refuse to finalize
forever, needing a hand-edited JSON file. `reason` is preserved -- it is the evidence of the last
stop, and only `state` gates a resume. Nothing is lost by re-deriving, because finalization
independently re-validates the manifest, the cached documents, and the persisted releases.

### Two different finalization refusals, only one of which is a wait

`historical-open-meteo-persist` reports `finalization_blocked_by_incomplete_coverage` and
`finalization_blocked_by_stale_release_set_as_of` separately, and **exits non-zero** on the second.
Incomplete coverage is a wait-and-resume state. A `release_set_as_of` that precedes a persisted
receipt is not: coverage is complete, there is nothing left to fetch, and the plan itself has to be
re-authored. Reporting it as missing coverage sends an operator after chunks that do not exist. The
sibling finalizers all raise `ValueError("release_set_as_of must not precede a persisted source
receipt")` for this condition; the CLI now reports it faithfully instead of skipping past the raise.

Re-authoring a plan is expensive on purpose: `release_set_as_of` is inside the plan checksum, so a
new as-of orphans `historical-open-meteo/<checksum>.json` **and** its whole `raw/` cache, forcing a
re-fetch of a quota-bound dataset. Set the as-of far past any plausible completion rather than
forecasting one; an as-of after the last receipt is the safe direction, since an as-of before one
blocks finalization and never leaks.

### What is not stored twice

`signal_observation.metadata_json` is written empty for this lane. It used to carry
`source_parameter` (already a first-class column on the same row) and `native_grid_name` (already in
`source_release.query_parameters`, in `cell_source_crosswalk.metadata_json`, and in the plan). At
~104 inline bytes that never reach TOAST, the duplicate would have cost roughly 690 MB across the
6.8 M-row lattice. The NASA and CDS lanes still write `{"source_parameter": ...}`; aligning them is
a separate reviewed change, not a silent edit to an already-persisted shape.

`_open_meteo_source_version` is a window/grid/chunk-ordinal **label**, not an identity: it omits
`chunk_cell_count`, so a 50-cell plan and an 8-cell plan both emit `...:cells-0000` for disjoint
cell sets. Identity is the `uq_source_release_identity` composite (data source, source version,
payload checksum, transform version), which every lookup in `historical_writer.py` binds. Folding
the chunk size into the label would rename already-persisted releases and orphan a finalized
release set, so the label stays and the docstring says what it is.

`historical_era5_parquet.py` turns only a complete ERA5 receipt set into an atomic Zstandard-compressed daily Hive lake. It re-parses the locally cached monthly ZIPs without a provider or database call, emits a bounded daily row set, and ties the manifest to both the exact plan and receipt manifest. It is the compact cold-history representation and does not promote history to Railway.

`historical_usdm.py` owns bounded U.S. Drought Monitor medium-resolution ZIP capture. It accepts only reviewed Tuesday releases in the four-year plan, verifies the exact WGS84 shapefile package/schema, preserves only native D0–D4 polygons without inferring absent classes or normal conditions, and writes checksum-bound weekly checkpoints. It is not an analysis-grid interpolation or local-condition source.

`historical_writer.py` persists only complete, checkpointed NASA POWER source cells, ERA5-Land monthly point samples, and USDM weekly vectors through the dedicated local loader session. It owns lineage, raw receipts, crosswalks, normalized observations, complete coverage audits, and release-set finalization, but commits nothing itself. ERA5 artifacts retain a checksum-bound local-cache pointer rather than inlining large ZIPs; its 9-km source resolution is context metadata and its response remains a requested point sample. USDM keeps the raw canonical geometry checksum while its reviewed transform may use PostGIS `MakeValid` to store a valid serving multipolygon; that behavior must be reflected in the immutable transform version. The caller owns transaction boundaries and advances a checkpoint only after commit. It is not a Railway receiver or a scheduler.

`historical_promotion.py` carries only typed, content-addressed historical lineage across the local-to-Railway boundary. Its 8 MB chunk and raw-artifact limits are deliberately aligned with the reviewed USDM acquisition ceiling; a 50-million-record, 20,000-chunk root remains bounded but must be streamed/spooled rather than materialized in memory. Grid crosswalks declare immutable `spatial_support_kind`; a caller must preserve that support and native resolution so regional cells cannot be represented as acre-scale observations.

`geospatial_capture.py` is a local, database-free custody boundary for reviewed
public geospatial requests. Its frozen plan allowlists exact HTTPS hosts,
expected feature identities, byte checksums, source support, licence status,
and inference ceilings; it publishes a cache only after the complete receipt
set validates. A checksum change is a new provider release requiring review,
not an automatic refresh. The active pilot plan contains only explicitly open
sources; no blocked source may enter a plan. Consumers use the exact byte
buffers returned by cache revalidation rather than reopening raw files. The
pilot's WUI request is a pinned property-bounding-box AOI query, not a
hand-selected object list.

`geospatial_pilot.py` consumes only a fully revalidated all-open capture and
writes one immutable local release set. The Hillside to Hollow subject is a
named OSM property with neighborhood support, never a cadastral parcel. Outputs
are facts, PostGIS-derived context, and known evidence gaps; the module has no
publication, forecast, strategy, selection, or recommendation path. Its
analysis receipt hashes the exact executed SQL and bind parameters, input
feature checksums, PostGIS version, output schema, disclosed rounding rules,
and the conservative year-end convention used for the WUI vintage minimum-age
lower bound.

`strategy_selection.py` is a database-free, evaluation-only causal benchmark
for a strict external intervention-label bundle. It compares matched
difference-in-differences, cross-fitted AIPW, a doubly robust ridge learner,
and an arm-specific ridge sensitivity model on expanding-time,
held-out-spatial-block folds. The canonical JSON artifact contains
coefficients, diagnostics, and both the finalized label-release checksum and
exact trimmed UTF-8 bundle-text checksum rather than executable pickle bytes.
PostgreSQL recomputes the digest from the authoritative JSONB export before
training validation. Hard support, availability, overlap, balance, agreement,
and conservative-effect
gates produce an explicit abstention; this module never publishes a forecast
or recommendation.

`strategy_label_mapping.py` is the database-free custody preflight before any
external intervention rows may be normalized. Its versioned manifest permits
only direct source-field references and requires the named source release,
outcome definition, treatment/control risk set, subject and assignment
windows, spatial block, raw evidence lineage, and time-honest covariates. An
incomplete manifest reports every missing path and has no checksum; only a
complete canonical mapping receives a SHA-256 digest. The module deliberately
has no row transform or database path, and rejects Boise forecast actuals
because forecast-error labels cannot establish intervention effects.

`covariate_wind_model.py` is a database-free-at-the-core, evaluation-only direct
multi-horizon ridge forecaster over the `0016` covariate layer. It reads the
pinned covariate vector and the WS2M target through their own availability-gated
SQL functions, fits one standardized closed-form ridge per horizon step, and
calibrates a p10-p90 band from residuals on a held-out calibration window that
ends strictly before the forecast origin. The split is temporal only -- fit
window, then calibration window, then a single held-out origin -- so no target
day ever appears in the window that produced the model scoring it. It never
writes to the warehouse, never joins a serving or publication surface, and never
produces a receipt: its output is a JSON report labelled `evaluation_only`.

Its scores prove the framework runs end to end; they are not an operational or
life-safety forecast. Interval coverage is an empirical residual band, not a
calibrated confidence bound. The comparison baseline is the existing
`daily_increment_bootstrap_v1` iteration read through
`agri.forecast_iteration_evaluation`, at exactly the same origin and horizon
steps.

## Vegetation NDVI Monte Carlo (`vegetation_ndvi_forecast.py`, `vegetation_ndvi_plane.py`)

`vegetation_ndvi_forecast.py` is the pure, database-free method
`ndvi_seasonal_anomaly_bootstrap_v1`: a deterministic Monte Carlo for the **sparse,
strongly seasonal** daily NDVI series of one 0.25-degree grid cell. It exists because the
shipped `agri.forecast_daily_bootstrap` (`daily_increment_bootstrap_v1`) cannot serve this
stream at all — that function resamples **consecutive-calendar-day first differences** and
demands at least two of them, while the measured Sentinel-2 corpus has a median 7-day gap
between observation days and **1,411 of 1,568 cells have zero consecutive-day pairs**. Under
`gap_policy = 'locf'` the single-day carry-forward would manufacture increments of exactly
zero and therefore a zero-width band, which is worse than refusing.

The method, per cell, from that cell's own governed history only:

1. **Seasonal level** — a circular day-of-year climatology over a +/-15-day window
   (`SEASONAL_WINDOW_DAYS`), requiring at least `MIN_CLIMATOLOGY_SAMPLES` real observations in
   the window. No interpolation and no carry-forward anywhere.
2. **Anomaly pool** — `observed - climatology(day_of_year)`. An observation whose *own*
   seasonal window is unsupported (typical of isolated PNW Nov-Feb scenes that survive the
   20 % cloud screen) is **dropped from the pool**, never referenced to a fabricated level.
3. **Persistence** — a daily anomaly decay `phi` derived from that cell's own lag-1 anomaly
   autocorrelation across consecutive *observations* (gap <= 30 days, at least
   `MIN_AUTOCORRELATION_PAIRS` pairs), rescaled by the mean gap and clamped to
   `[0, MAX_DAILY_PERSISTENCE]`.
4. **Simulation** — `climatology(t) + phi**(gap+h) * anchor_anomaly
   + sqrt(1 - phi**(2*(gap+h))) * innovation`, where `innovation` is resampled with
   replacement from the **seasonally matched** anomaly pool for the target day, and the path
   is clipped to the physical NDVI range `[-1, 1]`. `p10/p50/p90` are `numpy.percentile`
   (linear, matching `percentile_cont`) over the simulated values at each horizon step.

**Assumptions and limits — do not overstate this method.** NDVI anomalies are assumed
approximately stationary inside a +/-15-day seasonal window with a single per-cell geometric
memory. Innovations are drawn **independently per horizon step**, so the product is
calibrated for **marginal per-horizon quantiles only** and supports no joint-path statistic.
The climatology rests on at most four seasonal cycles and is weakest Nov-Feb. There is no
trend term, so a multi-year greening or browning signal is absorbed into the anomaly pool and
biases the median toward climatology. No covariates (weather, drought, irrigation, fire) are
used. Band widening with horizon is a *consequence* of estimated persistence: where a cell's
anomalies are effectively white at its observation cadence, the band is climatological from
step 1 and does not widen. Measured holdout interval coverage is well below the nominal 80 %,
so the band is under-dispersed and must be presented as indicative, not as a calibrated
prediction interval.

`vegetation_ndvi_plane.py` registers the governed observation plane and writes the
**evaluation-only iteration plane**. `agri.forecast_observation` is the input adapter because
NDVI never reached `agri.signal_observation` — that table holds only NASA POWER. Publisher
day is `substring(properties->>'observedAt', 1, 10)::date`, the repo's ISO-prefix rule, never
a UTC recast; `observed_at` is stored as that day at 00:00Z so no downstream reader can
re-derive a different calendar day. `data_available_at` is the real warehouse arrival
(`max(geo.features.created_at)`), and `forecast_input_recorded_at` is maintained entirely by
the shipped triggers — there is no parallel provenance mechanism.

Two reinterpretations are deliberate and load-bearing. `forecast_iteration.increment_count`
and `forecast_iteration_value.increment_count` carry the **seasonal innovation pool size**
(the number of resampling units), not a count of daily increments, because this method has
none. `training_day_count` is the number of **seasonally referenced** observation days, while
`governed_day_count` in the in-memory state is every eligible day; the difference is the
dropped winter tail.

`purpose` discriminates the two products: `forward_simulation` (renderable, `cutoff_time` =
today's publisher day, `availability_mode = as_of_pinned_release`) and `holdout_evaluation`
(historical simulated cutoffs, `retrospective_pinned_release`). Because the NDVI corpus was
backfilled in a single run, warehouse-availability time carries no hindcast information, so
the holdout controls leakage by **publisher-named day** and its metrics measure method skill,
not operational latency. Revision leakage cannot be excluded: Sentinel-2 L2A reprocessing is
not tracked in this corpus.

`agri.forecast_backtest_metric` is deliberately **not** written. It is foreign-keyed to
`agri.forecast_run`, whose `ck_forecast_run_method` admits only `sql_linear` or `ml`; naming
a seasonal-anomaly Monte Carlo either way would be a false label, and that plane is the gated
publication path that evaluation evidence must not enter. Holdout evidence therefore lives in
`agri.forecast_iteration_actual` and `agri.v_forecast_iteration_outcome`.

Reproducing a recorded iteration requires passing its recorded `as_of_time`: the as-of
boundary is part of the parameter digest, matching `agri.forecast_daily_bootstrap`. A
consequence worth knowing before widening a plane: writing more governed inputs bumps
`forecast_input_recorded_at`, after which an earlier as-of no longer satisfies the governed
read, so an already recorded iteration can no longer be re-simulated from the warehouse. The
rows remain valid immutable evidence via their own history and receipt checksums. Moving the
as-of out of the parameter digest (keeping it in the receipt digest) would remove that
coupling and would require a new method version, not an edit to this one.

The method lives in Python rather than `db/agri/functions/**` because promoting it to a
reusable SQL function requires a new Alembic migration, which this track was scoped out of.
That is the recommended follow-up; the canonical parameter text and the hash-seeded sampler
were written to be portable to SQL without changing any digest.

## `open_meteo_lane.py` -- the scaffold the new Open-Meteo lanes share

Three lanes landed in one pass (`historical_glofas.py`, `historical_cams.py`,
`ensemble_forecast.py`), each about a thousand lines, each originally carrying its own byte-equivalent
copy of `_atomic_write`, `_require_aware_utc`, `_date_range`, `_nearest_native_grid_point`,
`_validated_grid_point`, `_required_float`, the canonical-document builder, the location-order guard,
the bounded numeric reader, the retry/backoff loop plus its `*FetchError`, the raw-cache write/verify
pair, the checkpoint-state rederivation, the receipt merge, and the release-manifest digest. Counting
`historical_open_meteo.py` and `historical_era5.py`, some of those existed five times.

`engineering-principles.md` §1 is explicit -- "a checksum/normalization rule lives in one function
that every caller shares. Duplicated truth is a defect" -- and the concrete failure is easy to state:
the canonicalizer strips `generationtime_ms` so `payload_checksum` is reproducible, so when
Open-Meteo adds a second nondeterministic field, every copy must change and missing one leaves that
lane's checksums silently non-reproducible while the others stay honest, with no test that catches
the divergence. `NONDETERMINISTIC_RESPONSE_FIELDS` is now named once.

**What a lane still owns:** its plan contract and validators, its per-variable specifications and
physical ranges, its chunking, its normalization into observations or staged receipts, its coverage
classification, and its `require_accounted_*` rule. **What it must not own:** anything in this
module. A lane declares one `OpenMeteoLane` -- a label, a cache directory name, an endpoint -- and
the scaffold's messages then name that lane, so a failure is still attributable without a traceback.

`fetch_lane_capture` takes the lane's own `fetch_text` and `error_factory` rather than resolving them
itself. That keeps two properties: each lane raises its own `*FetchError` subclass (so a caller can
catch one lane's failure), and a test can still monkeypatch that lane's module-level fetch function
to exercise its wiring. The loop itself -- only `minute` is slept through, an hour/day/unrecognised
wall breaks out immediately, transport backoff escalates linearly -- is covered scope by scope in
`tests/test_open_meteo_lane.py`; each lane's test file then proves only that it routes through it.

`historical_open_meteo.py` and `historical_era5.py` are NOT converged onto this scaffold. They were
mid-backfill when it was written, and converging a live lane's checksum path is not a refactor to do
under a running job. They remain the fourth and fifth copies of `_atomic_write` / `_require_aware_utc`
/ `_date_range`; converging them is the follow-up, and it is a behaviour-preserving one because the
extracted functions were lifted from those lanes unchanged.

## `historical_glofas.py` -- the Open-Meteo GloFAS river-discharge lane

A four-year daily replay of GloFAS reach discharge over an existing analysis lattice, written as
DATA in `agri.signal_observation`: new `signal_name` values, a new `support_key` per product, a new
spatial-cell grid, and a `data_source` row. **No new warehouse DDL and no Alembic migration** --
`signal_name` is a plain `varchar(150)` with no enum, so a new signal needs none.

New `support_key` values are minted deliberately distinct from `era5-land-0.1deg` and
`sentinel2-ndvi-0p25deg`: `glofas-v3-0.1deg` and `glofas-v4-0.05deg`. `geo.soil_field` and its
siblings aggregate by support key, so sharing one would let incomparable lattices be averaged
together.

New signal names: `river_discharge` plus six `river_discharge_ensemble_*` statistics. All stay
invisible to ML until a new `agri.covariate_feature_schema` version is authored.

**The model drags the lattice.** `GLOFAS_PRODUCTS` is a frozen bundle keyed by model carrying
`schema_version`, `native_grid_name`/`_degrees`/`_resolution_m`, `support_key` and
`supported_parameters`; the plan restates them and `require_governed_lattice` rejects any
disagreement. The reanalysis products publish a single deterministic reach value, so asking a
`consolidated_*` plan for ensemble statistics is a plan error, not a gap. Adding variable N+1 is one
dict entry plus one sorted string in the plan.

**Cells may not share a native grid point.** v4 is 0.05 degrees and v3 is 0.1, so a lattice authored
finer than that would duplicate one modelled value across several analysis cells. The plan validator
refuses it rather than letting the duplication reach the warehouse.

**It cannot yet write a row.** `persist_glofas_flood_chunk` / `finalize_glofas_release_set` and the
`_ensure_*` helpers belong in `historical_writer.py`, which this pass did not touch. Until they
exist, a completed fetch has no path to the warehouse.

**Do NOT add this lane to `durable-backfill.sh`.** That launcher calls `historical-<lane>-persist`
unconditionally and reads the Open-Meteo lane's `finalization_blocked_by_*` JSON fields for
completeness; this lane has neither. Lane exit semantics must not be generalized across lanes.

Each plan is its own JSON file, and any later variable addition is a NEW plan file plus a new release
set -- never an in-place edit, since `plan_checksum` keys both the checkpoint and the whole `raw/`
cache.

**There is deliberately no `infra/cron-flood/` or `infra/cron-air-quality/`.** Two Railway service
configs for these lanes were staged and then removed, because a repo file that cannot run is worse
than no file: it reads as a shipped capability. Every one of the following must land before either
service is created, and all of them were open when the configs were written:

* a `historical-<lane>-persist` verb -- without it a completed fetch has no path to the warehouse;
* the plan JSONs committed AND copied into the image -- `infra/cron-ingest/Dockerfile` copies only
  `pyproject.toml`, `uv.lock` and `src/`, so `--plan /app/plans/...` hits `click.Path(exists=True)`;
* `ENTRYPOINT []` in that service -- the image pins `ENTRYPOINT ["agri-cli", "ingest-all"]`, which a
  Railway `startCommand` does not clear;
* a Railway volume for `settings.local_execution_root` (default `.agri-local-runs`, relative to a
  root-owned `/app` under uid 10001) -- otherwise the checkpoint and raw cache are unwritable or
  ephemeral, and `--max-chunks N` re-downloads chunks 0..N-1 against the provider quota every night
  without ever advancing.

`restartPolicyType: NEVER` makes each of those a SILENT daily failure, which is why the bar is a
precondition list rather than a follow-up.

## `historical_cams.py` -- the Open-Meteo CAMS air-quality lane

Same shape as the GloFAS lane -- data, not DDL; a product bundle that drags its lattice; a plan that
restates and is checked -- with three differences that come from CAMS publishing **hourly**.

**Chunks are bounded on two axes.** 24 values per variable per cell per day means the cell block
alone cannot keep a response under the byte ceiling, so `chunk_day_count` exists and the chunk key
carries both (`cells-0000-days-0000`).

**A day is a declared reduction, not a passthrough.** Each variable names its daily statistic --
`mean` for a concentration, `maximum` for an index -- and the reduction is part of
`transform_version`. Hourly physical bounds are applied BEFORE the reduction, never after, so a
provider sentinel cannot survive into a mean. A day summarized from fewer than
`CAMS_MINIMUM_OBSERVED_HOURS_PER_DAY` (18) hours is not a daily statistic: it is written as an
explicitly unobserved row carrying the new `quality_flag` value `insufficient_hourly_coverage`
(`String(64)`, no CHECK). **The future CAMS writer must pass that flag through unchanged rather than
normalizing it to `source_missing`** -- the two mean different things.

**Every daily row carries `coverage_fraction`.** An 18-hour mean and a 24-hour mean are both
`accepted`, and the chunk receipt's `insufficient_hour_day_count` only counts the days that fell
below the floor -- it says nothing about a day that cleared it with 19 hours. `coverage_fraction` is
the per-row trace (`observed_hours / 24`), it maps onto `agri.signal_observation.coverage_fraction`
(CHECK 0..1, default 1), and it needs no migration. `HistoricalSignalObservation.coverage_fraction`
defaults to 1.0 so the daily-native lanes are unaffected. **The future CAMS writer must persist it
rather than hard-coding `coverage_fraction=1` the way the existing writers do** -- that hard-coded 1
is correct only for a lane whose provider publishes one value per day.

**Coverage status uses `failed`, not `partial`, for a series that never reached the hour floor.**
`agri.signal_coverage_audit`'s `status_matches_counts` CHECK permits `failed` with a zero received
count but forbids `partial` with one, and `no_data` is reserved for a series with zero hourly values
at all. So: nothing published at all is `no_data`; published but never enough hours is `failed`.

`cams_global` is a 0.4-degree lattice, so the plan validator refuses analysis cells that share a
native grid point -- CAMS lattices must be authored at >= 0.4 degrees (>= 0.1 for `cams_europe`).
The exact per-domain variable availability encoded in `CAMS_PRODUCTS.supported_parameters` is
unverified against the live upstream; each correction is one line.

Like the GloFAS lane it has no writer and no plan JSON yet, and it must not be added to
`durable-backfill.sh` for the same reason.

## `ensemble_forecast.py` -- the Open-Meteo ensemble lane

The only FORECAST lane here, and it is shaped by that. It takes `issue_date` + `horizon_days` (max
16) instead of a `HistoricalBackfillWindow`, and the horizon is an **hourly** axis
(`step_count = horizon_days * 24`) compared instant-by-instant against a GMT-pinned axis.
`chunk_cell_count` is capped at 25, far below the 200-location transport ceiling, because the body is
one member series per variable per cell: 25 cells x 5 variables x 384 hours x 51 members is already
~20 MB.

**`member_count` is the quantile denominator.** `EnsembleProduct.member_count` is inherited from the
product, and `_member_series` requires exactly `member01..memberNN` -- refusing both a missing and an
extra series. A provider changing its ensemble size must fail the chunk, never silently re-scale
every derived quantile.

**A step with any null member is dropped, never zero-filled.** `expected_value_count` stays the
declared horizon so the gap is visible, and a series below `MIN_COMPLETE_STEP_FRACTION` (0.9) is an
explicit `insufficient_member_coverage` failure rather than a thin receipt -- the same shape as the
CAMS hour floor.

### Issue time is plan-declared; availability is not

`issue_date` is whatever the plan says, and the provider always answers with its CURRENT model runs.
Nothing in the payload records when the content became available, so a receipt anchored only on the
plan would claim to have been issued at a moment its data did not exist -- `python.md`: "A
forecast/hindcast/iteration may use only data available at its as-of/issue/cutoff time. Simulated
cutoffs are never written as operational issue times." That matters here even though the lane is
fail-closed at the warehouse (`ENSEMBLE_WAREHOUSE_PERSISTENCE_STATE = blocked_forecast_method_check`),
because `staged_forecast_receipt_checksum` exists precisely so a later writer can finalize a receipt
without recomputing its content: a false issue time would be baked into an artifact a future writer
is told to trust.

Three rules close it, and `require_consistent_quantile_carriage` stays the authority for all of them:

1. **`require_issue_time_not_ahead_of_retrieval`** refuses a plan whose `issue_time` postdates the
   retrieval that answered it, on both the fetch path and the cache-reload path. The reverse -- an
   issue date in the past -- is not refused, because a same-day plan legitimately covers hours that
   elapsed between midnight and the fetch.
2. **`StagedForecastReceipt.data_available_at`** stages the retrieval instant on every receipt, as a
   fact distinct from `issue_time`. `require_accounted_ensemble_forecast_result` requires it to equal
   the chunk's `retrieved_at`.
3. **`StagedForecastValue.is_elapsed_at_retrieval`** flags each step whose hour had already passed at
   retrieval -- analysis carried on a forecast receipt. `quality_summary.elapsed_step_count`
   aggregates it. The flag defaults to `False` and is then checked against `data_available_at` by
   `require_consistent_quantile_carriage`, so a writer that omits or forges it fails at the checksum
   boundary rather than publishing a hindcast hour as a forecast one. A downstream evaluator must not
   score these steps as forecast skill.

### What blocks warehouse persistence

Not the quantile columns. `ForecastReceipt.quantile_levels` / `ForecastValue.quantile_values` accept
ensemble output with no migration; every receipt's required `forecast_run_id` does not, because
`ck_forecast_run_method` admits only `sql_linear` or `ml`. The next actionable step is a migration
widening `forecast_method` / `model_kind` to an ensemble method in four coordinated places (the DB
CHECK, `ForecastRun`, `ForecastModel`, and the `Literal` in `routes/forecasts.py:86`), after which the
staged document maps mechanically onto inserts. Do not record this dataset as "needs no migration"
without that qualifier.

New signal names introduced here (`air_temperature_2m`, `precipitation_total`,
`relative_humidity_2m`, `shortwave_radiation`, `wind_speed_10m`) need no migration but stay invisible
to ML until a new `agri.covariate_feature_schema` version.
