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
