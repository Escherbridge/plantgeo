---
type: track-spec
track: cds_only_products_20260808
status: planned
---

# CDS-only products — specification

Everything in "Settled findings" and "The three products" was verified this session by an
adversarial workflow (every verifier returned not-refuted); it is evidence, not a proposal, and
this document does not re-derive it. Where this document does add its own measurement, it is
against the working tree on 2026-08-08 and cites `file:line`.

## Goal

The CDS ERA5-Land soil lane is retired — Open-Meteo now serves soil moisture and soil
temperature keylessly, finer, and faster (`conductor/tracks/upstream_dataset_expansion_20260806/`).
That retirement was never "stop using CDS"; it was "stop using CDS for a product Open-Meteo
already redistributes." Three products remain that CDS is the *only* route to. This track backs
them in, in priority order, reusing the CDS integration this repo already built and paid the
credential and licence cost for, rather than treating the retirement as license to abandon the
client.

## Why this track exists

`historical_era5.py`'s own module docstring already names this track and points at it:

> "SUPERSEDED FOR SOIL STATE (2026-08-06), NOT RETIRED. ... Do not re-run the soil plans. Do keep
> this module: it is the working CDS integration template — `_require_cds_credentials`, monthly
> batching, checkpointing, raw-cache reuse — that the genuinely CDS-only products reuse, since
> Open-Meteo redistributes none of them. See `conductor/tracks/cds_only_products_20260808/` and
> `docs/unused-upstream-datasets.md`."
> — [`execution/historical_era5.py:1-14`](../../../services/agri-data-service/src/agri_data_service/execution/historical_era5.py#L1-L14)

`docs/unused-upstream-datasets.md`'s own Copernicus CDS section closes the same way: "CDS remains
the only route to genuinely CDS-only products — AgERA5 agrometeorological indicators, CEMS fire
danger indices, and seasonal forecasts — none of which Open-Meteo redistributes. Reach for it only
for those." ([`docs/unused-upstream-datasets.md:113-120`](../../../docs/unused-upstream-datasets.md#L113-L120))

Both citations predate this track's existence and were written by the same session that retired
the soil lane. This track is not proposing new work; it is picking up a pointer the codebase
already carries.

## Settled findings

Measured. Implement against these; do not re-derive them.

**1. The CDS integration template is real code, not a description.**
[`_require_cds_credentials`](../../../services/agri-data-service/src/agri_data_service/execution/historical_era5.py#L698-L716)
reads `CDSAPI_URL`/`CDSAPI_KEY` from the process environment first, falling back to
`settings.cdsapi_url`/`settings.cdsapi_key`
([`config.py:130-133`](../../../services/agri-data-service/src/agri_data_service/config.py#L130-L133)).
Monthly batching and coverage validation are a real Pydantic contract, not a convention:
`Era5LandPeriod.require_consistent_month`
([`historical_era5.py:123-138`](../../../services/agri-data-service/src/agri_data_service/execution/historical_era5.py#L123-L138))
forces one deterministic calendar-month artifact per period, and
`HistoricalEra5LandBackfillPlan.require_governed_monthly_coverage`
([`historical_era5.py:190-212`](../../../services/agri-data-service/src/agri_data_service/execution/historical_era5.py#L190-L212))
asserts `covered_dates == expected_dates` — the periods must reconstruct the window day-for-day,
with neither a gap nor an overlap, or the plan fails validation before it is ever checksummed.
This is also why time-splitting a plan to parallelize it is impossible: the coverage check is a
whole-window equality, not a per-period bound.

**2. `HistoricalBackfillWindow` forces exactly four calendar years, unconditionally.**
[`historical_backfill.py:69-77`](../../../services/agri-data-service/src/agri_data_service/execution/historical_backfill.py#L69-L77):
`require_exact_four_calendar_years` raises unless `start_date` is exactly four calendar years
before `end_date`. Combined with finding 1, a plan cannot be split by time and gain wall clock —
every split plan still walks all of its own periods sequentially against the same CDS queue. The
only real parallelization lever available without a schema change is intra-plan period concurrency
(`asyncio.gather` over periods, replacing whatever sequential loop drives period fetches today) —
that is a code change to the historical replay loop, not a plan-authoring choice, and it is out of
scope unless a phase below explicitly takes it on.

**3. These two contract classes are dataset-specific, not generic.**
`Era5LandPeriod` and `HistoricalEra5LandBackfillPlan`
([`historical_era5.py:105-163`](../../../services/agri-data-service/src/agri_data_service/execution/historical_era5.py#L105-L163))
hardcode `dataset: Literal["derived-era5-land-daily-statistics"]` and a `daily_statistic`/
`frequency`/`requested_grid_degrees` shape built around that one CDS dataset's ZIP-bundle request.
AgERA5 (`sis-agrometeorological-indicators`) and CEMS (`cems-fire-historical-v1`) are different CDS
and EWDS datasets with different request shapes (finding 5, finding 7) — the *reuse* named in the
module docstring is the client, the checkpointing, and the raw-cache-keyed-on-plan-checksum shape,
not these two literal classes. Each new lane needs its own sibling contract classes, the same way
`historical_open_meteo.py` did not reuse `Era5LandPeriod` either.

**4. `durable-backfill.sh` dispatches by string interpolation, so it needs no code change to add a
lane — but its exit semantics are a per-lane contract, not a script guarantee.**
[`durable-backfill.sh:50-51`](../../../services/agri-data-service/durable-backfill.sh#L50-L51)
calls `historical-${LANE}-backfill` / `historical-${LANE}-persist` — any lane token whose CLI verbs
exist works without editing the script. But
[`durable-backfill.sh:53-57`](../../../services/agri-data-service/durable-backfill.sh#L53-L57)
records, in the script's own comment, that "the era5 lane exits non-zero on an incomplete
checkpoint, but the open-meteo lane persists whatever chunks it has and still exits 0, reporting
incompleteness only as a field in its JSON output" — and the script greps the JSON payload for
`"finalization_blocked_by_incomplete_coverage"` / `"finalization_blocked_by_stale_release_set_as_of"`
to normalize that difference. AgERA5's and CEMS's `historical-agera5-persist` /
`historical-cems-persist` verbs must each deliberately choose and document which shape they emit;
neither may assume the other lane's behavior without confirming it against this grep.

**5. `durable-archive-backfill.sh` is the wrong runner for these two lanes.**
[`durable-archive-backfill.sh:1-16`](../../../services/agri-data-service/durable-archive-backfill.sh#L1-L16)
states its own scope: it drives the checkpoint-table-less generic `ingest-backfill` verb (currently
hardcoded to `nasa-firms-archive` / `usgs-streamflow-archive`), where "the same window always
yields the same grid and an operator resumes by naming a boundary" via a cursor file — a different
durability contract from the plan-checksum-and-checkpoint shape `historical_era5.py` uses. GloFAS
and CAMS were routed to this script for that reason. AgERA5 and CEMS are, by finding 3, plan- and
checkpoint-based lanes in the `historical_era5.py` shape, not `ingest-backfill` sources — they
belong on `durable-backfill.sh`, with new lane tokens `agera5` and `cems`, not on
`durable-archive-backfill.sh`.

**6. `agri.covariate_feature_schema` is a function with a hardcoded signal list, not an open
table.**
[`db/agri/functions/covariate_feature_schema.sql:7-16`](../../../services/agri-data-service/db/agri/functions/covariate_feature_schema.sql#L7-L16)
raises unless `p_schema_version = 'agri_covariates_v1'`, and that version's `signal_spec` CTE is a
fixed seven-row `VALUES` list (`air_temperature_max/mean/min`, `dew_point_temperature`,
`precipitation`, `relative_humidity`, `wind_speed`). Any AgERA5 or CEMS `signal_name` this track
lands in `agri.signal_observation` is invisible to model training until a new schema version
function is authored — this track records that caveat per phase, the same way the sibling track's
Phase 1 recorded it for `vapour_pressure_deficit_max`; it does not solve it.

**7. CDS prices `derived-era5-land-daily-statistics` by time, not area — and that number does not
transfer to AgERA5.**
[`plans/AGENTS.md:50-76`](../../../services/agri-data-service/plans/AGENTS.md#L50-L76) measured
`cost = 2 x variables x days` (limit 400) against that one dataset's costing endpoint, with area
and grid free. AgERA5's `sis-agrometeorational-indicators` is a structurally different request —
its published examples retrieve one variable and one statistic per call, not a bundled multi-
variable ZIP — so this measured figure is evidence about a sibling dataset's cost shape, not a
number this track may reuse. Phase 1 below re-measures cardinality against AgERA5's own costing
endpoint before any plan is authored.

**8. Plan-naming and plan-authoring precedent exists and should be extended, not reinvented.**
`services/agri-data-service/plans/` holds one generator script,
`author_pnw_soil_moisture_plans.py`, and every plan file follows a
`<source>-<region>-<purpose>-<start>-<end>.json` naming convention (e.g.
`era5-land-pnw-soil-20220430-20260430.json`, `open-meteo-era5-land-pnw-vpd-20220430-20260430.json`).
A hand-typed plan JSON is the same trap the sibling track named for a wrong `nasa_lattice_plan_checksum` —
it looks valid forever while pointing at nothing. AgERA5 and CEMS plans should be authored by a
generator script in this same shape, not hand-typed.

## The three products

| # | Product | Host | Status |
|---|---|---|---|
| 1 | AgERA5 agrometeorological indicators | `cds.climate.copernicus.eu` (existing credentials) | ready to scope — no new credential plumbing |
| 2 | CEMS fire danger indices | `ewds.climate.copernicus.eu` (new host, new credentials) | blocked on a second, separately-registered EWDS account |
| 3 | Seasonal forecasts (SEAS5 / C3S) | unconfirmed | scoping only — dataset ids not verified to the same standard |

### 1. AgERA5 agrometeorological indicators — rank 1, start here

- CDS dataset id: `sis-agrometeorological-indicators`, **version pinned to `2_0`** (published
  2025-05-15, supersedes `1_1` — the version must be an explicit field in the request, not left to
  default).
- **Evaluate the sibling timeseries product first.** `sis-agrometeorological-indicators-timeseries`
  is a precomputed point/region time-series product over the same variables. PlantGeo needs its
  existing 0.1-degree cell lattice, not full grids, so the timeseries product could avoid gridded
  NetCDF handling entirely — this is an early task, not an afterthought, because it changes which
  contract classes and parsing code get written at all.
- Host: `cds.climate.copernicus.eu` — the same classic store the retired soil lane already has
  working credentials and accepted licences for (finding 1). This is the reason this product ranks
  first: zero new credential plumbing.
- Resolution: 0.1 x 0.1 degree global — already matches PlantGeo's 0.1-degree lattice exactly, so
  no regrid, unlike the retired lane's 1.0-degree output grid (finding 7 / `plans/AGENTS.md`'s
  "Extent is free but grid is frozen" section).
- Temporal: daily, 1979-01-01 to present, updated daily. Licence CC-BY, already accepted under the
  existing CDS terms acceptance.
- **The cost trap is structural, not a queue-latency repeat of the retired lane's problem.** Unlike
  `derived-era5-land-daily-statistics`, which bundles a variable list into one ZIP per month,
  AgERA5's published examples retrieve one variable and one statistic per call. N variables times
  statistics multiplies per-period request count directly. Finding 7 states this cannot be sized
  from the retired lane's measured costing figure — it must be measured fresh against AgERA5's own
  request form before a variable set is chosen.
- Variables on offer include `2m_temperature` (with a required statistic —
  `24_hour_maximum`/`24_hour_mean`/`24_hour_minimum`/`day_time_maximum`/`night_time_minimum`, and
  others), precipitation flux, solar radiation flux, vapour pressure, wind speed, and relative
  humidity. A task in this phase identifies which of these are **not** already available from NASA
  POWER (the platform's existing 9-signal, 397-cell, complete PNW/Idaho covariate lattice) or from
  the Open-Meteo archive lane — this phase must not re-ingest a signal this platform already has
  from a keyless or already-credentialed source.

### 2. CEMS fire danger indices — rank 2, closes a named product gap

- CDS/EWDS dataset ids: `cems-fire-historical-v1` (historical reanalysis) and `cems-fire-seasonal`
  (seasonal forecast).
- **The activation-blocking fact: this is a different data store, not just a different dataset id.**
  Both datasets live on `ewds.climate.copernicus.eu`, the Copernicus Early Warning Data Store, a
  2024+ split-off from the classic CDS. The existing `CDSAPI_URL`/`CDSAPI_KEY`
  ([`config.py:130-133`](../../../services/agri-data-service/src/agri_data_service/config.py#L130-L133))
  are scoped to the classic host only and will not authenticate against EWDS. This lane needs a
  **second**, separately-registered EWDS account and API key, its own terms/licence click-through,
  and a second URL/key pair threaded through `Settings` and `.env` — new plumbing this repo has
  never carried, not an extra dataset entry on the existing credential pair. The known failure mode
  is a `"dataset cems-fire-historical-v1 not found"` error, which is really the classic client
  pointed at the wrong host. This is why the metadata activation gate calls out a working non-404
  request as the explicit precondition, so the failure surfaces at activation rather than inside a
  running `retrieve()` call mid-backfill.
- Why it matters: PlantGeo already serves fire-detections, fire-perimeters and burn-severity layers
  but has no fire *danger* index. This closes a real capability gap, not a resolution upgrade over
  an existing signal.
- Content: the ECMWF GEFF model driven by ERA5, bundling three fire-danger systems in one dataset —
  Canadian FWI (`fire_weather_index`, `fine_fuel_moisture_code`, `duff_moisture_code`,
  `drought_code`, `initial_spread_index`, `build_up_index`, `fire_daily_severity_index`), US NFDRS
  (`energy_release_component`, `burning_index`, `spread_component`, `ignition_component`,
  `keetch_byram_drought_index`), and Australian McArthur (`fire_danger_index`, `drought_factor`).
- History: 1940 to present, daily. Request shape: gridded NetCDF/GRIB2, `product_type: reanalysis`,
  a `system_version` (seen as `4_1`), a variable list, year/month/day, and `grid`.
- **Resolution is unresolved and must be confirmed on the live request form before checksumming.**
  The EWDS landing page states 0.25 degree deterministic and 0.5 degree ensemble; the Confluence
  API guide's sample `grid` parameter shows 0.5/0.5. This is recorded as an open question below,
  not guessed at.

### 3. Seasonal forecasts — rank 3, scoping only

SEAS5 / C3S seasonal originating-centre datasets, long-horizon. Lowest priority. Its dataset ids
were not verified to the same standard as products 1 and 2 in this session's adversarial workflow —
this track does not treat any seasonal dataset id as settled, and Phase 3 below is scope and
open-question capture, not implementation.

## Non-goals

- **Re-litigating or reversing the CDS soil-state retirement.** Soil moisture and soil temperature
  stay on Open-Meteo. Nothing here re-registers `era5-land-pnw-soil-20220430-20260430.json` or its
  western-NA sibling, and nothing here re-chunks the retired lane for throughput — that measured
  problem was queue latency, not chunk size (`plans/AGENTS.md`'s CDS costing section).
- **GloFAS river discharge, CAMS air quality, and the Ensemble API.** Those are
  `conductor/tracks/upstream_dataset_expansion_20260806/`'s scope, already built there (with
  persistence blocked, per that track's own follow-up items) and unrelated to CDS — Open-Meteo
  redistributes all three.
- **Implementing seasonal forecast ingestion.** Scoped as Phase 3 open questions only in this
  track; a follow-on track picks it up once dataset ids are verified to the same standard as
  products 1 and 2.
- **Any new map layer, tile function, or UI for AgERA5 or CEMS.** This track ingests; serving is a
  separate decision, matching the sibling track's precedent — see open question 4.
- **A new `agri.covariate_feature_schema` version.** Every new `signal_name` this track lands is
  recorded as ML-invisible until that version is authored (finding 6); authoring it is a follow-on,
  not a task of this track.
- **The `asyncio.gather`-over-periods concurrency change named in finding 2.** Recorded as the only
  real parallelization lever available, not adopted here — it is a behavioral change to shared
  replay code, out of scope unless a phase explicitly takes it on.
- **Deleting, refactoring, or moving `historical_era5.py` or its credential contract.** It is the
  template this whole track reuses; it stays exactly where it is.

## Open questions — owner input required

1. **AgERA5 request cardinality.** Once list-cardinality is confirmed on the live request form
   (settled finding 7), does the variable set from the "genuinely not already obtainable" list
   (product 1) still fit inside an acceptable per-period request count, or does it need to be
   trimmed — and if trimmed, which variables are cut first?
2. **AgERA5 timeseries vs. gridded.** Does `sis-agrometeorological-indicators-timeseries` cover
   PlantGeo's lattice well enough to skip `sis-agrometeorational-indicators`'s gridded NetCDF
   path entirely, or does the timeseries product's spatial/temporal coverage fall short in a way
   that forces the gridded product regardless of its higher per-request cost?
3. **CEMS resolution.** Is the live grid 0.25 degree, 0.5 degree, or does it depend on
   `product_type` (deterministic vs. ensemble) — and once confirmed, does 0.5 degree (a 5x
   downsample of the platform's 0.1-degree lattice) need the same "representative of the cell, not
   any point in it" caveat the retired 1-degree ERA5-Land soil lane carried?
4. **Serving decision for fire danger indices.** Should CEMS output become a new map layer (a
   genuine capability gap next to the existing fire-detections/perimeters/burn-severity layers,
   per product 2), an ML covariate feature (gated behind open question 1's schema-version follow-
   on), both, or neither until a consumer is named? This is a design decision, not an ingestion
   detail, and gates nothing in Phase 1 or Phase 2 below, but should be answered before either
   phase's output sits unused the way GloFAS's and CAMS's currently do.
