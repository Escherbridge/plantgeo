# Execution modules

`source_ingestion.py` is the phase-one operational vertical slice for a governed, locally captured current-observation release: a bounded GeoJSON payload is structurally validated, checkpointed locally, then persisted idempotently as a source release, content-addressed artifact, and validated release set. It is intentionally not an upstream fetcher, generic data loader, forecast, trainer, or public prediction publisher. See `docs/data-ingestion-and-serving-contract.md` for the server/local ownership boundary.

Source-ingestion checkpoint v2 binds both the complete reviewed plan and the release-set content checksum. New release sets must be populated while `draft` and transition to `validated` only after their membership is flushed, because the warehouse trigger freezes membership after validation.

`source-ingest` connects with `LOCAL_SOURCE_LOADER_DATABASE_URL` when it is set and `DATABASE_URL` otherwise; the two may be the same string. **No identity or target enforcement remains** — the 2026-08-08 owner ruling (recorded in `20260808_0019`) retired the role family and then the DSN validators with it, so no login, host, port, or database name is asserted and there is no dedicated-loader requirement to satisfy. Choosing the right database is the operator's responsibility now, not the config layer's. What the command still enforces is payload custody: before any checksum, checkpoint, or artifact write it performs a bounded whole-document GeoJSON custody scan (50,000 JSON nodes, depth 32) and rejects canonicalized credential field names/suffixes plus Bearer/Basic authorization strings. It does not silently redact an immutable source artifact.

`promotion.py` is an offline semantic lineage bundle contract for already validated phase-one release sets. It re-applies the same bounded GeoJSON custody validation to embedded source artifacts, verifies hashes and supersession closure, and creates only a trigger-safe draft → membership → validated restore plan. It is not a general `pg_restore` wrapper, database exporter, restore CLI, or Railway job; those remain a separately reviewed private-control-plane integration.

`historical_backfill.py` owns deterministic, bounded NASA POWER daily request and response contracts for the initial four-year meteorology baseline. It validates the exact four-calendar-year window, canonical sampling-point plan, per-source query, response payload size, UTC observation timestamps, missing values, coverage accounting, a checksum-bound complete local receipt checkpoint, and raw response cache. The cache is written only after complete validation and before a warehouse transaction, so retried writes never re-request a successful source response. A later NASA finalization can only rebind a complete source replay to an advanced release-set as-of time; it never refetches or rewrites source receipts. It never carries credentials, opens a database connection, selects an ingestion geography, or publishes to Railway.

`_require_cds_credentials` (`historical_era5.py`) resolves `CDSAPI_URL`/`CDSAPI_KEY` **environment-first, then `Settings`/`.env`**, and reads both at call time rather than import time. Until 2026-08-08 it read `os.environ` only while `Settings` loaded `.env` through pydantic-settings — which populates the settings object and never `os.environ` — so a `.env` entry was silently inert, every operator had to run `set -a; . ./.env; set +a` first, and forgetting it produced a refusal that reads like a licence problem. `Settings.cdsapi_key` is a `SecretStr`; the pair goes straight to `cdsapi.Client` and is never logged, checkpointed, persisted, or named in the refusal, which lists only the two variable names. A blank or whitespace-only export is treated as unset rather than shadowing `.env`, so a stale empty shell variable cannot re-create the original bug. Accepting the dataset licence is still a browser action for the account behind the key; no resolution order changes that.

`NASA_POWER_SIGNAL_SPECIFICATIONS` carries the three POWER soil-wetness parameters (`GWETTOP`, `GWETROOT`, `GWETPROF`) alongside the meteorology baseline because POWER is keyless, whereas the ERA5-Land soil path in `historical_era5.py` is gated on a Copernicus dataset licence that only the account holder can accept in a browser. The two soil streams are complementary, not interchangeable, and must never be unit-mixed: POWER reports a MERRA-2 **degree of saturation** in `fraction_of_saturation` (0 = dry, 1 = saturated), while ERA5 `soil_water_content_layer_1` reports a **volumetric** water content in `m^3/m^3`. Depth support is named in the signal (`soil_wetness_surface` = top 5 cm, `soil_wetness_root_zone` = top 100 cm, `soil_wetness_profile` = the full modelled column), matching the existing ERA5 `soil_temperature_level_1` convention. `support_key` is not a depth discriminator: the NASA, CDS and USDM lanes all write `surface`, and the one lane that writes something else (`historical_open_meteo.py`, `era5-land-0.1deg`) uses it to distinguish *spatial* support, not depth. Adding a signal name needs no migration: `agri.signal_observation.signal_name` is a plain `varchar(150)` with no enum or check constraint. Extending the ML covariate vector is a separate, reviewed change — `agri.covariate_feature_schema` pins its signal list to the immutable `agri_covariates_v1` version, so new signals are deliberately invisible to training until a new schema version is authored.

`historical_parquet.py` converts only a complete local NASA raw-receipt set into an immutable, compressed daily Hive-partitioned Parquet dataset. It stages one bounded source-cell file at a time, caps DuckDB to one thread and 1 GB with a build-local spill directory, and atomically publishes a manifest-bound dataset. An interrupted conversion reuses its single target-bound build directory only after each staged cell's row count, key, and payload checksum are revalidated against the raw receipt; ambiguous or mismatched staging fails closed. Successful publication removes staging. It is intentionally a local cold-history store; it never requests an upstream API, writes PostgreSQL, or promotes a full history to Railway.

`historical_era5.py` owns cache-first CDS capture for the governed ERA5-Land plan. It treats each calendar month as one immutable ZIP artifact, validates every planned point/variable/day before advancing the durable checkpoint, and requires local CDS credentials only for a missing cache entry. Its requested one-degree points remain point samples; they never claim the product's native 0.1-degree grid or acre-scale precision.

**Superseded for soil state on 2026-08-06, deliberately not deleted.** `historical_open_meteo.py` now serves soil moisture and soil temperature from the same ERA5-Land product at native 0.1 degrees, keyless, and finished in an afternoon while this lane reached 2 of 49 periods against repeated 502s and SSL errors. No data was dropped because this lane never persisted a warehouse row -- `agri.data_source` holds no `era5-land` key, and the only residue is a git-ignored `.agri-local-runs/historical-era5/` cache whose two checkpoints never reached `validated`. The module stays because it is the one working CDS integration template, and the genuinely CDS-only products (AgERA5 `sis-agrometeorological-indicators`, CEMS fire danger `cems-fire-historical-v1`, seasonal forecasts) reuse its shape -- Open-Meteo redistributes none of them. Two traps carry over to those products: the four-calendar-year window validator plus exact day-for-day period coverage make time-splitting a plan impossible, and cell-splitting buys no wall clock because retrievals are per-period, so intra-plan period concurrency is the only real lever. See `conductor/tracks/cds_only_products_20260808/`.

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

### Shortwave radiation is a SECOND upstream for a signal NASA POWER already writes

`shortwave_radiation_sum` -> `surface_shortwave_radiation`, `MJ/m^2/day`, `[0.0, 60.0]`. It is the
first entry in this table whose `signal_name` is one an **existing, running lane already produces**:
`historical_backfill.py:49` maps NASA POWER's `ALLSKY_SFC_SW_DWN` to exactly that name and unit. That
collision is the entire point. NASA's radiation lane
(`plans/nasa-power-western-na-weather-radiation-20220531-20260531.json`) is COMPLETE at 397/397 cells
and permanently capped at **2026-05-31**, because `ALLSKY_SFC_SW_DWN` carries a hard ~2-month
publication lag that no amount of re-running fixes. Open-Meteo republishes the same daily quantity
with roughly six days of lag, so a second producer on the same `signal_name` is how the ceiling
moves. It republishes it from **ERA5, not ERA5-Land**: ERA5-Land publishes no radiation flux through
this endpoint at all, which is measured, dated and explained in §"The archive model decides which
variables have values at all" below, and is why this one plan runs on `models=era5`.

**No conversion is applied, and applying one would be the bug.** Both quantities are a daily *sum of
megajoules per square metre*: Open-Meteo's daily-variable table publishes `shortwave_radiation_sum`
in `MJ/m2`, and `historical_backfill.py` applies no scaling to `ALLSKY_SFC_SW_DWN`. Confirmed against
production 2026-08-08: 1,166,676 stored NASA rows, min 0.41, **mean 16.65**, max 35.10 — a
kWh/m^2/day series would sit near a mean of 4.6. `original_unit` and `normalized_unit` are therefore
both the NASA spelling `MJ/m^2/day` rather than the provider's `MJ/m2`, because `normalized_unit` is a
**join key**: `geo.climate_field_observation` gates radiation on
`('surface_shortwave_radiation', 'shortwave-radiation', 'MJ/m^2/day')`, so a re-spelled unit matches
nothing and is silently invisible instead of wrongly served.

**This plan rides the NASA lattice, not this lane's usual one.**
`plans/open-meteo-era5-nasa-power-lattice-radiation-20220802-20260802.json` declares
`grid_name = "nasa-power-0.5-degree"` and carries the NASA plan's 397 `na-sample:1deg:*` cells
verbatim. Nothing in this lane hardcodes the NDVI lattice — `grid_name`/`grid_resolution_m` are plain
plan fields and `_require_open_meteo_spatial_cells` compares against `plan.grid_name` — so the
retarget is a plan change with no code change. Keeping the signal on the lattice NASA established for
it is what keeps one signal's coverage definition single-valued, and it is also what lets
`getPublishedClimateField`'s `cell.grid_name = 'nasa-power-0.5-degree'` predicate still address the
rows. The 1-degree cell spacing clears `require_governed_lattice`'s "cells must not share a native
grid point" rule with an order of magnitude to spare.

**`support_key` is `era5-0.25deg` on this plan, and that still blocks serving.** It is no longer a
pinned `Literal`: it is a pattern-checked `str` whose value is re-derived per model from
`OPEN_METEO_ARCHIVE_PRODUCTS` and re-checked by `require_governed_lattice`, so ERA5-Land plans carry
`era5-land-0.1deg` and this ERA5 one carries `era5-0.25deg`. Both are honest for the same reason: the
field records *spatial* support, and it is what lets a reader tell a 0.25-degree ERA5 sample from a
0.1-degree ERA5-Land one and from the 0.5-degree NASA POWER sample of the same cell-day. Writing
`surface` here to make the rows serve would erase that distinction for every variable this lane
carries. The consequence, stated plainly so nobody discovers it by seeing an unmoved map: **three
serving-side predicates exclude these rows today**, and each is a deliberate gate somebody wrote, not
an oversight —

| Reader | Predicate | Effect |
|---|---|---|
| `db/agri/functions/covariate_daily_features.sql` | `signal.support_key = 'surface'` | ML covariate layer never sees them |
| `drizzle/0020_climate_field.sql` (`geo.climate_field_observation`) | `source.key = 'nasa-power-daily'` | view excludes them |
| `environmental-read-model.ts` `getPublishedClimateField` | `reading.support_key = 'surface'` | reader excludes them |

Only the fourth predicate, `cell.grid_name = 'nasa-power-0.5-degree'`, is already satisfied — by the
lattice choice above. Widening the other three is a **separate reviewed change** with a real design
question attached (0.5-degree and 0.25-degree support drawn on one ramp is exactly what
`0020_climate_field.sql`'s own header argues against), so the producer landed first and alone. The
`DISTINCT ON (signal_name, day) ORDER BY data_available_at DESC` precedence those two readers use is
sound and needs nothing; it is the support/source gates in front of it that decide whether the merge
is reachable at all. Note that the precedence only *applies* to rows the gates admit: for this lane
neither reader reaches these rows today, so nothing about a duplicate or a second lineage in it is
resolved by that ordering yet.

The plan carries **its own `source` block**, not the sibling plans': `source.key` is
`open-meteo-era5-archive` and its `purpose` names ERA5, this lattice and this parameter. The same
rule forces both facts — `_ensure_data_source` in `historical_writer/_shared.py` pins `model` and the
native grid into `configuration` and raises "already governed by different metadata" on any
disagreement, so a second reanalysis needs a second key rather than a reworded one. On the ERA5-Land
side that same rule *freezes* the shared `purpose` ("...soil-state covariates...", naming the
Sentinel-2 NDVI lattice) across all three sibling plans even though it no longer describes every
parameter they carry; correcting it there is a coordinated edit across every sibling plan file's
`source` block, not a one-plan change.

`transform_version` is the shared `open-meteo-era5-land-archive-daily-mean-normalization-v1` on this
ERA5 plan too, and the `era5-land` inside that string is a misnomer that is **deliberately kept**. It
names this lane's passthrough normalization, not the provider's reduction (Open-Meteo performs the
daily reduction upstream and this lane applies none), which is also why it already carries a non-mean
aggregation in VPD's daily max. More decisively, it is *identity* rather than description: it is one
of the four columns of `uq_source_release_identity`, it is projected as provenance by
`agri.v_signal_timeseries_contract`, and it is inside `plan_checksum`. Renaming it would fork the
identity of the 8 chunks already persisted under it and orphan the plan's checkpoint and its whole
`raw/` cache, buying accuracy in a label at the price of a re-fetch of a quota-bound dataset.

### Two checksums, on purpose

The wire response carries `generationtime_ms`, a per-request server timing metric. Left in, every
refetch would produce a new `payload_checksum` and therefore a second source release for identical
content. The shared `open_meteo_lane.canonical_location_document` removes exactly that key -- named
once in `NONDETERMINISTIC_RESPONSE_FIELDS` -- and canonicalizes the rest; the
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
  `RATE_LIMIT_BACKOFF_SECONDS`. Both live in `open_meteo_lane.py` and are obeyed by every lane; this
  one no longer keeps its own copy.
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

### The archive model decides which variables have values at all

One endpoint, two reanalyses. `models=era5_land` is the 0.1-degree product whose soil layers match
the CDS variable definitions; `models=era5` is its 0.25-degree parent, which carries strictly more
variables -- notably every radiation flux. **ERA5-Land publishes no radiation through this endpoint,
and says so by returning nulls rather than an error.** Measured live on 2026-08-09 at 31N/104W:
`models=era5_land&daily=shortwave_radiation_sum` answered HTTP 200 with the key present, the
`daily_units` block correct (`MJ/m²`), the time axis exactly the reviewed window, and every one of
the 1,462 values `null`. `models=era5` answered 30.76, 31.01, 30.21 MJ/m² for the same days at the
same returned grid point.

Every guard in this lane passed that payload, correctly: the byte cap, the location ordering, the
grid-attribution check, the time-axis check, the `provider_unit` assertion and the range bounds all
describe a *well-formed* response, and that response was well-formed. `_coverage_status` then read
zero observed values as `no_data`, `require_accounted_open_meteo_result` agreed that zero rows is
exactly what a `no_data` series should produce, and the checkpoint reached `validated` with
397/397 series empty. The first authoring of the radiation plan therefore fetched 8 chunks,
validated them, persisted them, finalized a release set, and wrote a `.done` marker over zero rows.

Two changes make that unrepresentable rather than merely unlikely:

1. **`OpenMeteoArchiveSignal.model` is required and first.** The signal table names the reanalysis
   that publishes each variable, and `require_governed_lattice` refuses a plan whose parameters are
   not all published by its own `model`. This is the only place the mistake can be caught *before* a
   quota-bound fetch, because the provider answers with 200 either way.
2. **A variable empty in every reviewed cell blocks finalization.**
   `unanswered_open_meteo_parameters` is whole-plan, not per-chunk: one cell or one chunk may
   honestly hold no data, but a variable with zero observed values across every reviewed cell is a
   mapping or model-availability failure wearing a coverage failure's clothes. See the next section
   for why it exits non-zero instead of waiting.

`OPEN_METEO_ARCHIVE_PRODUCTS` is what the model fixes: native grid name, spacing, resolution,
`support_key`, `data_source` key, `source_kind`, upstream product string, and `artifact_kind`. A plan
may not drift from any of them. The two models get **separate `data_source` keys**
(`open-meteo-era5-land-archive`, `open-meteo-era5-archive`) because `_ensure_data_source` pins
`model` and the native grid into `configuration` and re-verifies them on every replay -- one key
cannot honestly describe both products, and a shared key would have made the ERA5 plan fail at
persist time with a confusing conflict message instead of registering its own source. Widening the
model cost the radiation signal resolution, not honesty: `support_key` is `era5-0.25deg`, so a
reader can still tell a 0.25-degree ERA5 sample from ERA5-Land's 0.1-degree one and from NASA
POWER's 0.5-degree one of the same cell.

**`artifact_kind` is per-model and was the last field to be threaded.** `agri.artifact.kind` names the
product the bytes came from, and `historical_export.py` copies it into every export manifest as
`source_artifact_kind`, so one literal cannot describe two reanalyses. ERA5-Land keeps
`source_open_meteo_era5_land_archive_daily_json` byte-for-byte (artifacts are immutable and its rows
are already persisted); ERA5 gets `source_open_meteo_era5_archive_daily_json`. **Known residual:** the
8 radiation chunks persisted on 2026-08-09, before this was threaded, carry the ERA5-Land kind on
ERA5 bytes. That is not retroactively rewritten — an artifact row is immutable by design and rewriting
one to improve a label would be worse than the label. Everything else on those rows (source key,
`source_kind`, `upstream_product`, `support_key`, native grid, artifact URI namespace) already
discriminates correctly, so the mislabel is recoverable by joining to the release's data source.

`schema_version` stays `open-meteo-era5-land-archive-daily-v1` for both models. It names the plan
*document schema*, which is unchanged, and it is the walk-identity prefix `ops_historical_walks.sql`
parses; re-versioning it would rename every already-persisted walk to say nothing new. It is also
what `plan_continuation._plan_lane` routes on, which is correct for the same reason: both models
share one document contract, and every *other* lane pins a different `schema_version` value.

### Two different finalization refusals, only one of which is a wait

`historical-open-meteo-persist` reports `finalization_blocked_by_incomplete_coverage`,
`finalization_blocked_by_stale_release_set_as_of` and `finalization_blocked_by_unanswered_parameters`
separately, and **exits non-zero** on the last two.
Incomplete coverage is a wait-and-resume state. A `release_set_as_of` that precedes a persisted
receipt is not: coverage is complete, there is nothing left to fetch, and the plan itself has to be
re-authored. Reporting it as missing coverage sends an operator after chunks that do not exist. The
sibling finalizers all raise `ValueError("release_set_as_of must not precede a persisted source
receipt")` for this condition; the CLI now reports it faithfully instead of skipping past the raise.

A parameter that came back empty in every reviewed cell is the same shape of failure and gets the
same treatment: there is nothing to resume, because the fetch already succeeded. Waiting would be
worse than useless -- `durable-backfill.sh` only writes its `.done` marker when persist exits 0
*and* reports no incompleteness, so a lane that silently finalized over zero rows retired itself and
made every future wake a no-op. The non-zero exit keeps the lane open and puts
`unanswered_parameters` in front of an operator.

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
payload checksum, transform version), which every lookup in `historical_writer/open_meteo.py` binds. Folding
the chunk size into the label would rename already-persisted releases and orphan a finalized
release set, so the label stays and the docstring says what it is.

`historical_era5_parquet.py` turns only a complete ERA5 receipt set into an atomic Zstandard-compressed daily Hive lake. It re-parses the locally cached monthly ZIPs without a provider or database call, emits a bounded daily row set, and ties the manifest to both the exact plan and receipt manifest. It is the compact cold-history representation and does not promote history to Railway.

`historical_usdm.py` owns bounded U.S. Drought Monitor medium-resolution ZIP capture. It accepts only reviewed Tuesday releases in the four-year plan, verifies the exact WGS84 shapefile package/schema, preserves only native D0–D4 polygons without inferring absent classes or normal conditions, and writes checksum-bound weekly checkpoints. It is not an analysis-grid interpolation or local-condition source.

`historical_writer/` persists only complete, checkpointed NASA POWER source cells, ERA5-Land monthly point samples, and USDM weekly vectors through the dedicated local loader session. It owns lineage, raw receipts, crosswalks, normalized observations, complete coverage audits, and release-set finalization, but commits nothing itself. ERA5 artifacts retain a checksum-bound local-cache pointer rather than inlining large ZIPs; its 9-km source resolution is context metadata and its response remains a requested point sample. USDM keeps the raw canonical geometry checksum while its reviewed transform may use PostGIS `MakeValid` to store a valid serving multipolygon; that behavior must be reflected in the immutable transform version. The caller owns transaction boundaries and advances a checkpoint only after commit. It is not a Railway receiver or a scheduler.

**Three performance properties in this lane are load-bearing, not incidental.** (1) `_INSERT_USDM_POLYGONS` is the one raw-SQL statement in `historical_writer/` (it lives in `historical_writer/usdm.py`) — its text now lives in `sql/execution/insert_usdm_polygons.sql`, loaded at import time — and it is raw *because* of `WITH candidate AS MATERIALIZED`: the repair chain `ST_GeomFromGeoJSON → ST_MakeValid → ST_CollectionExtract → ST_Multi` is by far the most expensive operation in the USDM lane, `MATERIALIZED` is what guarantees Postgres evaluates it exactly once per polygon rather than once per predicate that reads the result, and SQLAlchemy Core has no way to emit that keyword. The statement replaced a per-polygon validation SELECT plus a per-polygon INSERT that embedded the identical expression a second time — two round trips and two `ST_MakeValid` evaluations per polygon. It still fails closed: `accepted` counts the polygons that passed the validity predicates, and a batch where that count is short of the batch size raises the same `ValueError` the per-polygon check raised. (2) The artifact idempotency checks in `_ensure_artifact` (nasa.py), `_ensure_usdm_artifact` and `_ensure_open_meteo_artifact` **defer `content_bytes` and do not compare it** — they pass `defer_content_bytes=True` to `provenance.ensure_artifact`. The `uri`/`checksum_sha256` predicate plus the `inline_artifact_checksum_matches` and `inline_artifact_size_matches` CHECKs already prove content identity, so re-reading a 64 MB blob to re-prove it was pure waste on exactly the resume path these lanes are built around. `_ensure_era5_artifact` is deliberately *not* deferred (`defer_content_bytes=False`): its check is `content_bytes IS NOT NULL`, an assertion that the ERA5 artifact stores no inline blob at all, and that column is NULL there by construction. (3) `fetch_era5_land_monthly` offloads **both** halves — download and parse — with `asyncio.to_thread`, and `historical_usdm._fetch_with_client` does the same for `parse_usdm_shapefile_zip`. Offloading only the download, as the ERA5 lane originally did, left the larger blocking half (unzip, HDF5 decompression, one xarray point selection per reviewed cell) on the event loop. The per-cell `.sel` in `_era5_values_by_cell_and_date` is deliberately *not* vectorised: each iteration also enforces the reviewed-cell distance guard, the post-selection dimensionality check and the per-cell time-coordinate check, and each raises a cell-named error — a pointwise vectorised `.sel` would change which failure surfaces first and what it says.

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
window, then calibration window, then the held-out origin -- so no target day
ever appears in the window that produced the model scoring it.

Its scores prove the framework runs end to end; they are not an operational or
life-safety forecast. Interval coverage is an empirical residual band, not a
calibrated confidence bound. The comparison baseline is the existing
`daily_increment_bootstrap_v1` iteration read through
`agri.forecast_iteration_evaluation`, at exactly the same origin and horizon
steps.

`covariate_wind_persist.py` is the receipt writer and, as of 2026-08-08, the
**first production writer of `agri.forecast_training_run` and
`agri.forecast_backtest_metric`** -- both tables previously had no writer outside
the test suite, which made the whole ML receipt chain structurally empty. It is
reached through `agri-cli forecast-train-wind --persist`, and `--persist` is OFF
by default: without it the verb reads, scores and prints one JSON line exactly as
the module always did, and rolls the session back. `covariate_wind_lane.py` runs
the same work as a durable lane on the `agri.job_*` ledger.

### What a persisted run writes, and what it deliberately does not

One transaction, in lineage order, because every validator inspects rows that must
already exist and already be in the state it demands: a `job_definition` (upserted
once), a `job_run` inserted already `succeeded` and pinned to a governed release
set, an `artifact` holding the canonical model JSON inline (so the database's own
CHECK recomputes the digest), a `forecast_model` (`ml` / `metric_forecast`), a
`job_output` for the model, a `forecast_feature_snapshot` promoted by
`agri.validate_forecast_feature_snapshot`, the `forecast_training_run` promoted by
`agri.validate_forecast_training_run`, a second `job_output` for the backtest, a
`forecast_run`, and one `forecast_backtest_metric` per rolling origin.

It writes **no `forecast_receipt`, no `forecast_value` and no
`forecast_publication`**, and it never calls `agri.validate_forecast_run`. The
forecast run therefore stays `staged` forever. That is the structural guarantee
that evaluation evidence cannot reach a serving surface:
`mv_forecast_ml_daily_serving` is built over published receipts and values, and
this lane produces neither -- the guarantee is the absence of a row, not a WHERE
clause someone could forget.

The lane binds an **existing** validated release set and an **existing** active
`forecast_quality_policy` (named by `--quality-policy-key`, required with
`--persist`). It mints neither. A release set certifies which governed inputs a
model saw and a quality policy encodes the thresholds a forecast must clear;
a training lane that created its own would be certifying itself. When either is
absent the run fails loudly and names what is missing.

### Rolling origins, and the sample size the headline number needs

`--origins N` refits the WHOLE model at each of N origins, walking back from
`--origin-date` by `--origin-stride-days` (default: the horizon count, so two
origins' target spans never overlap). Each origin recomputes its own fit and
calibration boundaries from its own cutoff, so an earlier origin never sees a
later one's days. Per-origin metrics land as `forecast_backtest_metric` rows keyed
by that origin's cutoff; the pooled aggregate and the per-horizon breakdown land
in `forecast_run.quality_summary` and `forecast_training_run.validation_metrics`.

The aggregate is deliberately **not** an extra metric row. That table is unique on
`(forecast_run_id, series_id, cutoff_time)`, so an "aggregate" row would need a
`cutoff_time` no origin actually had, and inventing that instant to satisfy a
unique index would be fabricating provenance.

**Sample size, honestly.** The horizons scored from one origin are consecutive
daily values of an autocorrelated variable: their effective sample size is closer
to 1-3 than to the horizon count. `origin_count` is the figure that grows the
sample; `evaluated_count` is not. Both travel in the receipt, and the caveat is
carried inside `validation_metrics.caveats` as well as here, because a number read
out of a JSONB column travels without the document that qualified it. Horizon 0 is
a nowcast, so N horizons are days 0..N-1 -- one day shorter in lead time than the
SQL baseline's steps, which start at 1. `horizon_origin_offset` records that.

### Feature-coverage accounting: the shrinkage is reported, not silent

The covariate completeness mask is whole-row: one short feature discards that day's
other thirty-nine present ones. That is the SQL function's contract and `db/agri`
is frozen, so the fix here is visibility rather than behaviour. Every run reports,
in `validation_metrics.feature_coverage`, the candidate day count from the spine,
how many days were feature-complete, how many were excluded, how many were
feature-complete but had no target, the usable count that actually trained, and
`blocking_features` -- which feature blocked how many days. A thin training set now
says why it is thin instead of just being small. A run whose usable count reaches
zero refuses to write a receipt rather than recording an empty training set.

### as_of_mode is `global`, and that is a known leakage

Every receipt carries `"as_of_mode": "global"`. The vocabulary across this plane --
`data_available_at`, `p_as_of_time`, "availability-gated" -- reads as point-in-time
correctness, and it is not that. It is **one** knowledge cutoff applied uniformly
to the whole history: a feature row for 2023-01-01 is assembled from whatever
version of that observation is visible at the run's single as-of instant, including
NASA POWER reprocessings and re-issued USDM polygons that did not exist in 2023.
For revised products this is revision leakage and it inflates apparent backtest
skill in a way no unit test can catch. The correct fix is a per-issue-date as-of --
`covariate_daily_features` deriving its gate per `observed_date` (e.g.
`spine.observed_date + interval '<n> days'`) rather than taking a scalar -- and
under the immutability rule at `covariate_feature_schema.sql` that is a new schema
version, `agri_covariates_v2`, not an edit. It was deliberately not attempted here;
recording it in the receipt is what stops the current numbers being read as
point-in-time honest.

### The durable lane

`covariate_wind_lane.py` registers `execution.covariate_wind_train` with
`@job_handler` at import time, the same mechanism `ingest/archive_walk.py` uses,
and `cli.py` importing the module is what performs the registration. **One work
item is one (cell, origin batch)** -- `shard_key` is `<cell_id>:<batch newest
origin>` -- so "which batches are still missing" is a `GROUP BY` over `shard_key`.
`agri-cli forecast-train-wind-plan` fans the shards out idempotently and
`agri-cli forecast-train-wind-run` drives one bounded slice.

It is **not** `jobs-run`, and that is custody rather than taste: `jobs-run` opens
`ingest_session()`, which is the source-loader DSN, and a governed forecast receipt
must not be written through the ingestion role. The session is bound through a
contextvar for the length of one slice, so a `jobs-run` pointed at this definition
raises `CovariateWindContextError` instead of writing through the wrong role.

A batch is indivisible -- fit, score and receipt are one transaction -- so the only
budget decision available is whether to START it, which the handler makes with
`has_budget_for` before it touches the session, and records the measured duration
in its cursor so the next tick estimates from evidence. A batch that cannot be
evaluated **fails** rather than completing: completing it would make a shard that
produced no receipt indistinguishable from one that produced a receipt, which is
the silence the ledger exists to prevent. The plan pins `as_of_time`, and it must:
every identity key a batch derives folds the as-of instant in through the parameter
checksum, so an unpinned instant would make a re-claimed shard write a second
receipt instead of resolving the first.

### Known gaps in this lane

- **~~No DSN exists for the least-privilege forecast roles.~~ Closed by the 2026-08-08
  teardown, not by a grant.** This bullet used to read that no single role could complete
  the `forecast-train-wind --persist` chain — the writer held INSERT but no validator
  EXECUTE, the publisher the reverse. Revision `20260808_0019` dropped the whole family
  (`plantgeo_forecast_writer`/`_publisher`/`_reader`/`_mv_refresher`/`_mv_refresh_owner`)
  after verifying it had zero members, no DSN, and no `USAGE` on schema `agri`, and the
  follow-up change stripped the DSN validators that asserted a login. The lane still runs
  on `FORECAST_ITERATION_DATABASE_URL` — now an optional override that falls back to
  `DATABASE_URL` — with the single owner credential, which can complete the chain because
  it owns the objects. Do not re-create a role to "restore separation of duties" here: see
  `docs/reports/migration-decision-packet-2026-08-08.md` § Resolution.
- **No ablation.** The teardown's "is the covariate layer earning its keep" question
  -- fit once with the target's own lags (features 31-35) and once without -- is not
  answered here. `leading_standardized_coefficients` in the receipt is a hint, not
  an answer.
- **The target is its own feature.** `wind_speed` is `signal_ordinal 7`, so features
  31-35 are its lags and rolling means. This is a legitimate autoregressive setup,
  and it is not what "a 40-feature covariate model" conveys. The target is also NASA
  POWER WS2M, a MERRA-2-derived reanalysis, so a good score means "we can reproduce
  MERRA-2's wind field from its own recent history", not "we can forecast wind".
  There is no ground-truth station series in this warehouse to validate against.

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

## `plan_continuation.py` -- why a finished plan stops moving, and what may be done about it

A fixed-window plan that `durable-backfill.sh` has marked `.done` is frozen forever: every later wake
exits at the `.done` check, so the lane ages one calendar day per calendar day with nothing pushing
it. That had already happened twice by hand before this module existed. `agri-cli
historical-plan-continue` authors the successor plan; `agri-cli historical-plan-staleness` is the half
that makes the freeze visible instead of silent, and is meant to be read, not to gate anything.

**Scheduling is deliberately not automated here.** Registering a Windows scheduled task needs the
owner's own hands, so the two new plans of 2026-08-08 were left unscheduled and so is this. The verb
is the mechanism; wiring it to a timer is a documented manual step.

### The window slides. It cannot extend, and that is the whole cost model

`HistoricalBackfillWindow.require_exact_four_calendar_years` rejects any window that is not exactly
four calendar years, and **both** lanes share that class. "Keep the original start and only push the
end forward" is therefore structurally impossible: `continuation_window` moves the end to the frontier
and the start follows it. A tail-only plan covering just the new days would need a contract change.

The consequence has to be stated in rows, because it is the deciding fact. `_source_version` in
`historical_writer/nasa.py` is `{schema_version}:{start}-{end}:{cell_key}` and
`_open_meteo_source_version` likewise folds the window in, so a slid window is a **new**
`source_release` for every cell. `uq_signal_observation_release_cell_signal_time` is scoped to the
release, so it does not dedupe the overlap: the ~4 years the two windows share are re-fetched and
re-persisted in full. Measured on `weather-fast` (397 cells x 7 parameters) at the 2026-08-09
frontier: **2,779 genuinely new rows against 4,060,119 duplicated ones**, roughly 2 GB at the ~529
bytes per `agri.signal_observation` row measured elsewhere in this tree.

This is the same shape as the hazard that got the redundant `era5-land-pnw-soil-*.json` plan skipped.
Whether the duplicates are *merged* rather than double-counted is **each reader's own property, and
it is lane-specific** -- which is why neither this module's generated plan description nor this
section names a reader as though it were a general guarantee:

- The NASA lane is merged. `agri.covariate_daily_features` takes `DISTINCT ON (signal_name, day)
  ORDER BY data_available_at DESC`, and `geo.climate_field_observation` does the same, so a second
  lineage over the same cell-days resolves to the newest release.
- **The Open-Meteo lanes are not reached by either reader at all.**
  `covariate_daily_features.sql` filters `signal.support_key = 'surface'` and this lane emits
  `era5-land-0.1deg` / `era5-0.25deg`; the climate-field view filters `source.key =
  'nasa-power-daily'`. Both gates are deliberate (see §"Shortwave radiation is a SECOND upstream"),
  and until one is widened, a duplicate lineage on an Open-Meteo plan is pure storage with no merge
  semantics in front of it either way.

Either way the cost is storage and throughput rather than a wrong number, it is paid in full on every
continuation regardless of how far the window moves, and the only real lever is **how rarely one is
authored**. `MINIMUM_CONTINUATION_ADVANCE_DAYS` (30) is that lever, and the decision reports both row
counts so an operator overriding it is doing so with the trade in front of them.

`superseding_sibling` refuses outright when a same-family plan already carries a later window. That is
exactly the redundant-ERA5 mistake, and the check is what stops the staleness sweep from recommending
it: the superseded 2022-04-30 plans read as 95-101 days behind and would otherwise look like the most
urgent lanes in the report.

**The candidate set is both directories, and that is load-bearing.**
`historical-plan-continue --output-directory` writes outside the source plan's own directory, so a
search of only `source.path.parent` cannot see the continuation the previous invocation just wrote --
and this check is the *single* guard against stacking lineages. Searching only one side made three
ordinary invocations author three overlapping continuations of one plan, ~3.94 M duplicate rows each.
`sibling_plan_candidates` globs the source's directory **and** the output directory, de-duplicated by
resolved path. **Residual, measured rather than assumed:** an operator who points every invocation at
a *different, empty* directory still escapes the check, because the earlier sibling is in neither
place the check can see. Closing that needs a registry of plan directories rather than two globs; the
practical rule is that `services/agri-data-service/plans/` is the canonical home and a continuation
belongs beside its family.

**Family identity is the filename stem minus its date pair, and that is a disclosed limitation.**
Two plans that cover the same cells and parameters under *different* stems are not siblings to this
check. That is true today, not hypothetically:
`plans/nasa-power-western-na-soil-lattice-20220430-20260430.json` (397 cells,
`ALLSKY_SFC_SW_DWN/PRECTOTCORR/RH2M/T2M/T2MDEW/T2M_MAX/T2M_MIN/WS2M`) is fully covered by
`weather-fast` ∪ `weather-radiation` over the same 397 cells, and the split of `soil-wetness`,
`weather-fast` and `weather-radiation` into three families is itself deliberate (they advance at
different provider frontiers). Filename keying cannot see either relationship. Detecting overlap
properly means comparing cell sets, parameter sets and windows across every plan in the tree, which is
a real design with a real false-positive question attached -- deliberately not smuggled in here. Until
then: **an operator adding a plan family that overlaps an existing one owns that check by hand**, and
`historical-plan-staleness` will list both as continuable.

### The end date is measured, never assumed

There is no lag constant anywhere in this module. `probe_provider_frontier` asks the provider, with
the plan's own parameters, at three cells spread across its own lattice, over a 240-day window:

- **Across cells it takes the maximum.** An ocean or out-of-domain cell publishes nothing, and reading
  that as provider lag would freeze a lane permanently.
- **Across parameters it takes the minimum**, and names the limiting ones. This is not conservatism:
  `require_complete_nasa_result` fails the *whole cell* -- every parameter -- when one parameter is
  short of the window, and the Open-Meteo `require_accounted_*` rule wants one coverage row per
  requested series. A plan can only reach the day its slowest parameter has reached.

That distinction is the entire reason `weather-fast` and the radiation plan are separate files, and
the probe reproduces the split from first principles. Measured 2026-08-09, three cells agreeing:
`PRECTOTCORR/RH2M/T2M/T2MDEW/T2M_MAX/T2M_MIN/WS2M` and the three `GWET*` parameters all reach
2026-08-07, while `ALLSKY_SFC_SW_DWN` reaches 2026-05-31. The Open-Meteo ERA5-Land archive reached
2026-08-03 for every variable this tree requests.

A fill value is not freshness: the NASA branch routes every value through
`nasa_power_observed_value`, so POWER's `-999` reads as missing, and the Open-Meteo branch treats a
`null` the same way. Both are the shared readers, not copies -- `_extract_parameter_values`,
`_source_numeric_value` and `_four_calendar_years_before` were made public for this
(`extract_nasa_power_parameter_values`, `nasa_power_observed_value`, `four_calendar_years_before`), and
`nasa_power_daily_point_url` was factored out of `nasa_power_daily_url` so both build one URL shape.
`--end-date` bypasses the probe for an offline run and records `mode: "declared"`, which is the
operator's claim rather than a measurement. Two bounds keep that claim from turning into work that
cannot succeed, and both are refusals rather than exceptions:

- **A frontier past today is refused** (`provider_frontier_end_date_is_in_the_future`). A probe
  cannot measure one, but `--end-date` accepts any calendar date, and days the provider has not
  published are not freshness -- fetching them spends a full lattice's quota on nothing.
- **A continuation may never leave an uncovered gap**
  (`continuation_window_would_leave_an_uncovered_gap`). Because the window slides rather than extends,
  a far enough jump moves the *start* past the day the source stopped: `--end-date 2031-01-01` against
  a plan ending 2026-08-06 proposed `2027-01-01 .. 2031-01-01` and a 147-day hole covered by neither
  lineage. The proposed start must be at most one day past the source's end.

### Lane routing is `schema_version`, never key shape

`_plan_lane` reads the `schema_version` each contract pins: the archive lanes' documents are
structurally alike, and four of them (`historical_open_meteo`, `historical_cams`,
`historical_glofas`, `ensemble_forecast`) carry `cells` and `chunk_cell_count` at the top level. Duck
typing on those keys routed a CAMS plan into the Open-Meteo archive contract, where
`model_validate` raised a bare `pydantic.ValidationError` -- which is a `ValueError`, not a
`PlanContinuationError`, so it escaped `scan_plan_staleness`'s `except PlanContinuationError:
continue` and aborted the whole sweep on one unrelated file. An unrecognised `schema_version` and a
recognised one whose document fails its contract both raise `PlanContinuationError` now, so the sweep
skips the file and keeps measuring the rest. The NASA contract carries its `schema_version` on the
nested `nasa` block, which is where it is read from.

### What a continuation inherits, and what it may change

Everything except the window, the two identifiers derived from it, and the description: cells,
parameters, grid, chunking, `transform_version` and the whole `source` block are carried verbatim,
because `_ensure_data_source` raises "already governed by different metadata" on any source
disagreement. The plan is built with `model_copy`, which skips validators, and then **re-parsed from
the emitted bytes through the same contract the backfill verb uses** -- a document that cannot survive
that round trip is never written. Output is `indent=2`, `sort_keys=True`, LF, one trailing newline,
matching the 2026-08-08 continuation plans already on disk.

The filename stem and `release_set_key` are retargeted *independently*. They are usually equal, but
not always: `open-meteo-era5-land-pnw-vpd-*.json` carries the key
`open-meteo-era5-land-pnw-ndvi-lattice-vpd-*`, so deriving one from the other would silently rename a
release set.

`release_set_as_of` is placed 30 days past today rather than at a forecast completion, per the rule in
§historical_open_meteo: an as-of before a persisted receipt blocks finalization and forces a re-author,
which orphans the checkpoint and the whole raw cache, while an as-of after it merely waits.

### Exit semantics

A refusal is **not** a fault. `historical-plan-continue` prints the decision as JSON and exits zero
for every `ContinuationRefusal`, matching `durable-backfill.sh`'s own "nothing to do right now"
convention, so a scheduled invocation that finds the frontier unmoved is silent success rather than a
red run. Only an unreadable plan, an unreachable provider or an unwritable path raises. `--write` is
off by default: without it the verb decides and reports but writes nothing.

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

`historical_open_meteo.py` converged onto this scaffold after the fact; `historical_era5.py` has not,
and remains the last independent copy of `_atomic_write` / `_require_aware_utc` / `_date_range`.

The archive lane's convergence was proven byte-identical before the private copies were deleted --
`canonical_location_document` was diffed against the deleted `_canonical_archive_document` on a real
payload and on every malformed shape, because a changed canonicalization moves `payload_checksum` and
would orphan every cached chunk receipt on disk and every persisted `source_release.payload_checksum`.
`tests/test_historical_open_meteo.py` pins that equality. Two messages did change, deliberately, so
one wording lives in one place: the bounded reader now says "does not align with its time axis"
rather than "its daily time axis", and the manifest guard says "required for a Open-Meteo archive
release manifest" (the shared string cannot pick an article per lane). Nothing else moved.

Two seams the archive lane still cannot share, both rooted in `ingest/open_meteo.py` predating
`OpenMeteoEndpoint`: it assembles its own `OPEN_METEO_ARCHIVE_ENDPOINT` from that module's host and
bounds constants, and it restates `archive_daily_request`'s two fields as an `OpenMeteoProductRequest`
before calling `fetch_lane_capture`. Moving the archive endpoint into `ingest/open_meteo.py` alongside
the air-quality and flood ones deletes both, and is the remaining follow-up.

What a lane may still keep privately is a genuine behavioural difference, not a copy:
`_validated_grid_point` here is a four-line wrapper around the shared guard that additionally reads
`elevation`, because only this lane persists one. Widening the shared function for it would push an
unused return value onto three lanes that do not record elevation.

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
`_ensure_*` helpers belong in `historical_writer/`, which this pass did not touch. Until they
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

## `historical_writer/` -- four lanes over one governed core

`execution/historical_writer.py` was a 1859-line module holding four near-identical per-source
persistence pipelines. It is now a package. Nothing about what reaches the warehouse changed; the
public surface (`persist_*`, `finalize_*`, the five result dataclasses, `ReleaseSetIdentity`) is
re-exported from `historical_writer/__init__.py`, so `cli.py` and the tests import exactly what they
imported before.

| Module | Holds |
|---|---|
| `__init__.py` | the re-export surface, including private `_insert_era5_observations` (imported by name in `tests/test_historical_era5.py`) |
| `_results.py` | `ReleaseSetIdentity` and the five write-result dataclasses |
| `_shared.py` | the three bounded batch inserts, `_require_spatial_cells`, the WKT builders, `_utc_now_or_value`, and `_ensure_data_source` (the one all four lanes call with only a different `configuration`) |
| `_release_sets.py` | `_finalize_historical_release_set` (the receipt guard + advisory lock + source lookup all four `finalize_*` wrappers copied) and `_finalize_release_set` |
| `nasa.py`, `usdm.py`, `era5.py`, `open_meteo.py` | one lane each: its `persist_*`, `finalize_*`, `_ensure_*`, `_insert_*`, `_verify_persisted_*`, `_required_*_source_releases`, and its source-version builder |

**The divergences that were NOT merged, and where each now lives.** These are the reasons the four
copies could not simply collapse; each is now a visible per-lane argument rather than an invisible
copy-paste difference.

- **`schema_version` source.** NASA reads `plan.nasa.schema_version`; USDM pins the lane constant
  `usdm-shapefile-v1` (`usdm.py:_SCHEMA_VERSION`, which also builds `_usdm_source_version`); ERA5 and
  Open-Meteo pin their module-level schema constants. Kept per-lane.
- **Artifact `storage_class` and blob policy.** ERA5 alone writes `local_raw_cache` /
  `content_bytes=None`, asserts `content_bytes IS NULL` on replay, and therefore must pass
  `defer_content_bytes=False`. The other three write `database_inline` and defer. Kept per-lane.
- **Error phrasing.** Every lane's conflict / unvalidated / artifact messages are byte-identical to
  what they raised before, held as module constants (`_RELEASE_CONFLICT_MESSAGE` and friends) so a
  grep for a production error string still lands on one lane.
- **Spatial cells.** NASA is the only lane that *mints* a `SpatialCell`
  (`nasa.py:_ensure_spatial_cell`, with its `ST_Equals` geometry re-check). ERA5 and Open-Meteo
  *require* the lattice (`_shared.py:_require_spatial_cells`); Open-Meteo adds a grid-name check on
  top. USDM has no cells at all.
- **Verification counts.** Open-Meteo counts all coverage rows; NASA and ERA5 count only
  `status == "complete"`; USDM counts polygons plus exactly one source-coverage row. Kept per-lane.
- **`_insert_observations`.** NASA slices a fully materialized list by
  `HISTORICAL_NASA_OBSERVATION_INSERT_BATCH_SIZE`; ERA5 and Open-Meteo accumulate into a bounded
  buffer and flush at `HISTORICAL_SIGNAL_INSERT_BATCH_SIZE`. The Open-Meteo lane also writes
  `metadata_json={}` on purpose (see its own comment). Kept per-lane.

`ingest/writer.py` and `historical_writer/` remain two disjoint warehouse planes and share no code.
`ingest/writer.py` writes the **serving** plane (`geo.features`, Type-2 geometry versions, the
realtime publish channel). `historical_writer/` writes the **governed provenance** plane in `agri.*`
(`SourceRelease`, `Artifact`, `ReleaseSet`, `SpatialCell`, `SignalObservation`,
`SignalCoverageAudit`, `DroughtPolygonSnapshot`) and touches neither `geo.features` nor the realtime
channel. Their only shared vocabulary is the word "writer". Do not fuse them: the `db/agri/**` vs
`geo.*` separation exists to keep the plane that governs evidence away from the plane that serves it.
The new module names (`_release_sets.py`, `_shared.py`, per-source lanes) are deliberate, so the
distinction reads as structure rather than as a naming accident.

## The governed provenance upsert

`execution/provenance.py` is the one implementation of "idempotently upsert DataSource ->
SourceRelease -> Artifact -> ReleaseSet, refusing a governed-identity mismatch". Its shape:

```
assert_contract_fields(record, expected, *, conflict_message)
advisory_lock(session, key)
find_data_source(session, source_key)
require_active_data_source(session, source_key, *, inactive_message)
ensure_data_source(session, candidate, *, expected, inactive_message, conflict_message)
ensure_source_release(session, candidate, *, expected, conflict_message)
require_validation_timestamp(release, *, message)
ensure_artifact(session, candidate, *, expected, defer_content_bytes, conflict_message)
find_release_set(session, *, logical_key, manifest_checksum, conflict_message)
release_set_member_ids(session, release_set_id)
```

Each `ensure_*` returns `(record, found)` -- `found` is False when this call created the row, which is
what every caller's `idempotent` flag is built from.

Two design rules make it safe to share across governed planes. **(a) The candidate carries the
identity.** Each `ensure_*` reads its WHERE clause off the ORM instance the caller is proposing --
`ensure_source_release` selects on the candidate's own `(data_source_id, source_version,
payload_checksum, transform_version)` -- so a caller cannot search under one identity and then insert
another. **(b) `expected` is a required argument, not a fixed field list.** The replay comparison is
genuinely different per caller and unifying it would silently loosen one refusal or tighten another:
the historical lanes pin `configuration` on the data source and do not compare `data_available_at` on
the release, while `source_ingestion` does the exact opposite; `source_ingestion` compares the
artifact `content_bytes` and the historical lanes deliberately do not. Nothing has a default, so an
omitted governance field is a type error rather than a weaker row.

**Who calls it.** The four historical lanes (via `historical_writer/_shared.py:_ensure_data_source`,
`_release_sets.py`, and each lane's `_ensure_*_source_release` / `_ensure_*_artifact`) and
`source_ingestion.publish_source_release`, whose `# noqa: PLR0912, PLR0915` disappeared with the
inlined copies.

**Who deliberately does not, and why.**

- `geospatial_pilot.py` -- FROZEN. Its release-set lifecycle is a different contract: it writes
  `ReleaseSetItem(source_role="evidence_input")` and validates in the same call, where the historical
  lanes carry no source role and refuse a non-DRAFT set. Its checksummed SQL and receipt shape are
  pinned by its own fixtures. There is a comment above `_get_or_create_data_source` saying so.
  Migrating it needs an explicit `source_role` parameter, a `validate_immediately` flag, and its own
  real-DB review.
- `routes/historical_promotion.py` -- its `_ensure_data_source` / `_ensure_source_release` /
  `_ensure_artifact_receipt` do not issue a SELECT at all. They read from the `_ChunkIndex` the
  performance lane prefetches once per chunk, and they abort with HTTP status codes rather than
  raising `ValueError`. Calling the shared select-per-row helpers would reintroduce exactly the N+1
  that the prefetch removed. Left alone on purpose.
- `vegetation_ndvi_plane.py` -- writes provenance through `sql/execution/insert_*.sql` with
  `ON CONFLICT DO NOTHING` and no field-by-field refusal at all. It is a different mechanism, not a
  copy; routing it through `ensure_*` would make it start raising on drift it currently tolerates.
  That is a behaviour change and needs its own decision, not a refactor.
- `execution/promotion.py` and `execution/geospatial_capture.py` were named as provenance copies in
  the split plan and are **not**. `promotion.py` has no `AsyncSession` anywhere: it plans
  `RestoreStep(kind=ENSURE_DATA_SOURCE | ENSURE_SOURCE_RELEASE | ENSURE_ARTIFACT)` over pydantic
  records, and `routes/historical_promotion.py` is what applies them. `geospatial_capture.py` is
  HTTP-capture-to-disk with no database access at all. Recorded so the next audit does not re-open it.

**Follow-up migrations,** in the order they become safe: `historical_open_meteo.py` /
`open_meteo_lane.py` once the lane-adoption pass lands (check them for provenance copies then);
`vegetation_ndvi_plane.py` behind an explicit decision about `ON CONFLICT DO NOTHING` vs governed
refusal; `geospatial_pilot.py` last, with the `source_role` / `validate_immediately` parameters.
