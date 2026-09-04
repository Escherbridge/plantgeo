---
type: lane-contract
---

# `weather-observations` lane

Scope of this document, stated up front because the name is ambiguous in the repo (see §5): this
lane is **the governed historical weather archive backing `agri.signal_observation`** — NASA POWER
daily point data plus the Open-Meteo ERA5-Land archive — per the RUNBOOK's
[§0.24.2](../../conductor/RUNBOOK.md) classification and this session's measurements. It is written
for the agent that will build `warehouse/schemas/weather-observations.py`,
`pipeline/lanes/weather-observations.py`, `pipeline/validation/weather-observations.py`,
`method/monte_carlo/weather-observations.py` and `planes/weather-observations.py` per
[`conductor/code_styleguides/layer-lanes.md`](../../conductor/code_styleguides/layer-lanes.md) §1. No
Parquet path, filename or column list is asserted here — that contract is being written concurrently
by another agent this session.

## 1. Source system

Two independent upstreams currently land rows in `agri.signal_observation` for this lane, plus one
side lane that is registered but effectively unserved. All facts below are cited to the file/line
that establishes them.

### NASA POWER Daily Point API

- **Publisher**: NASA POWER (Prediction Of Worldwide Energy Resources), a NASA/LaRC service.
- **Endpoint**: `https://power.larc.nasa.gov/api/temporal/daily/point`
  (`services/agri-data-service/src/agri_data_service/execution/historical_backfill.py:24`).
- **Auth**: none. POWER is keyless
  (`execution/AGENTS.md:15`: "POWER is keyless, whereas the ERA5-Land soil path ... is gated on a
  Copernicus dataset licence").
- **License**: recorded in every plan's `source` block as `"NASA POWER data policy"`, license URL
  `https://power.larc.nasa.gov/docs/services/api/temporal/daily/`
  (`plans/nasa-power-western-na-soil-wetness-20220806-20260806.json:2011-2013`,
  `plans/author_pnw_soil_moisture_plans.py:303-311`). No redistribution restriction is recorded in
  the repo beyond that policy reference — **UNVERIFIED** whether it imposes attribution or
  redistribution conditions beyond what the linked policy page states; confirm by reading that page
  directly if redistribution terms matter for a public-facing feature.
- **Parameters requested** (11, all keyless, one HTTP request per grid cell per 4-year window):
  `ALLSKY_SFC_SW_DWN`, `GWETPROF`, `GWETROOT`, `GWETTOP`, `PRECTOTCORR`, `RH2M`, `T2M`, `T2M_MAX`,
  `T2M_MIN`, `T2MDEW`, `WS2M`
  (`execution/historical_backfill.py:48-60`, `NASA_POWER_SIGNAL_SPECIFICATIONS`).
- **Grid**: `nasa-power-0.5-degree`, 397 cells, cell half-span 0.25° (`historical_backfill.py:31-33`).
- Response cap 5,000,000 bytes, 30 s timeout, 4 retry attempts (`historical_backfill.py:26-29`).

### Open-Meteo ERA5-Land Archive API

- **Publisher**: Open-Meteo, acting as an **intermediary redistributor** of ECMWF ERA5-Land
  reanalysis via the Copernicus Climate Change Service — not a first-party CDS receipt
  (`execution/AGENTS.md:23-27,62-64`: "Open-Meteo is an intermediary... deliberately not dressed up
  as an ECMWF receipt").
- **Endpoint (free tier)**: `https://archive-api.open-meteo.com/v1/archive`
  (`ingest/open_meteo.py:101`).
- **Endpoint (paid tier)**: `https://customer-archive-api.open-meteo.com/v1/archive`, selected
  automatically when `OPEN_METEO_API_KEY` is set (`ingest/open_meteo.py:107-108,172-174`).
- **Auth**: none required. `OPEN_METEO_API_KEY` is optional and **buys quota, not access**
  (`execution/AGENTS.md:27,260-270`) — same cells/window/model/variables come back either way; the
  key is read from the environment at fetch time and deliberately excluded from the plan checksum so
  paying for quota never forces a re-fetch.
- **License**: `"CC-BY 4.0 (Open-Meteo) over Copernicus/ECMWF ERA5-Land"`, license URL
  `https://open-meteo.com/en/license`
  (`tests/test_historical_open_meteo.py:87-95`, `plans/author_pnw_soil_moisture_plans.py:167-175`).
  **CC-BY implies attribution is required on redistribution** — carry the citation string
  ("Open-Meteo is an intermediary redistributor of ERA5-Land") forward into any public-facing surface
  built on this data.
- **Model parameter**: `models=era5_land` is **mandatory and pinned as a `Literal`** — the endpoint's
  undeclared default is `era5` (0.25°, a different, coarser reanalysis) and silently answers with the
  wrong product if `models` is omitted (`ingest/open_meteo.py:117-119`, `execution/AGENTS.md:39-40`).
- **Cell selection**: `cell_selection=nearest`, pinned so a coastal request cannot be silently
  relocated to a land cell the analysis lattice does not name (`ingest/open_meteo.py:130-132`,
  `execution/AGENTS.md:41-42`).
- **Native grid**: `era5-land-0.1-degree`, 0.1°, ~9,000 m resolution
  (`historical_open_meteo.py:77-79`), sampled at nearest-point against the 1,568-cell
  `sentinel2-ndvi-0p25deg` analysis lattice (only 1,470 of those cells ever carry a value — see §5).
- Response cap 64 MiB, 300 s timeout (`ingest/open_meteo.py:102`).

### A second, largely unserved radiation producer

`shortwave_radiation_sum` (Open-Meteo, `models=era5` — **not** `era5_land`, because ERA5-Land
publishes no radiation flux through this endpoint at all, measured live 2026-08-09 as an all-null
series — `execution/historical_open_meteo.py:212-214`, `execution/AGENTS.md:132-134`) is registered
under its own `data_source.key = "open-meteo-era5-archive"` and rides the **NASA lattice**
(`grid_name="nasa-power-0.5-degree"`, `execution/AGENTS.md:146-155`) rather than this lane's usual
one, specifically to duplicate NASA POWER's own `surface_shortwave_radiation` signal past NASA's
publication ceiling (see §2). **It writes `support_key='era5-0.25deg'`, and three separate serving
predicates exclude rows at that support key today** (`covariate_daily_features.sql`,
`0020_climate_field.sql`, `environmental-read-model.ts`'s `getPublishedClimateField` —
`execution/AGENTS.md:168-172`), so this producer is registered and has persisted at least 8 chunks
(dated 2026-08-09, `execution/AGENTS.md:357`) but is **not** part of what any current reader actually
serves, and its extent was not part of this session's 46,068,872-row measurement (which named only
`nasa-power-daily` and `open-meteo-era5-land-archive` as contributing sources). Treat its row count
and freshness as **UNVERIFIED** — confirm with a direct count against `data_source.key =
'open-meteo-era5-archive'` before assuming it contributes meaningfully.

### The CDS-direct path exists but is dormant — do not resurrect it

`historical_era5.py` is a first-party Copernicus CDS integration (`cdsapi`, credentials
`CDSAPI_URL`/`CDSAPI_KEY`) that predates the Open-Meteo lane above. It is explicitly **superseded for
soil state (2026-08-06), not deleted**: it "reached 2 of 49 periods against repeated 502s and SSL
errors" and **never persisted a single warehouse row** — `agri.data_source` has no `era5-land` key at
all (`historical_era5.py:1-14`, `execution/AGENTS.md:19-21`). It survives only as a working CDS
integration template for genuinely CDS-only products (AgERA5, CEMS fire danger) that Open-Meteo does
not redistribute. **Do not re-run its plans and do not treat it as a producer for this lane.**

## 2. Cadence

Both upstreams publish daily. The measured **publication lag** (how far behind real-world time the
newest available day sits) is declared, not derived, and is enforced as a hard constant:

```
PUBLICATION_LAG_DAYS = {
    "nasa-power-daily": 5,
    "open-meteo-era5-land-archive": 9,
}
```
(`execution/coverage_census.py:56-59`). Measured against production 2026-08-11: NASA POWER's newest
day was 2026-08-06 (5 days behind) and Open-Meteo ERA5-Land's was 2026-08-02 (9 days behind)
(`execution/AGENTS.md:1283-1293`). A lane with no measured lag (e.g. the radiation-only
`open-meteo-era5-archive` side lane in §1) falls back to a deliberately generous
`UNMEASURED_PUBLICATION_LAG_DAYS = 14` (`coverage_census.py:65`) — over-reporting completeness for a
fortnight is the safe failure direction; under-reporting sends a fetch after days that do not exist
yet.

**Radiation specifically has a much longer, separately-measured lag that the 5-day NASA constant
above does not capture.** `ALLSKY_SFC_SW_DWN` "carries a hard ~2-month publication lag that no amount
of re-running fixes" — NASA's radiation plan is complete at 397/397 cells and **permanently capped at
2026-05-31**, while NASA's other seven surface signals reach 2026-08-06
(`execution/AGENTS.md:122-131`). Open-Meteo's `open-meteo-era5-archive` side lane exists specifically
to move that ceiling forward with roughly six days of lag, but per §1 its rows are not currently
served. **Do not apply the blanket 5-day NASA lag to `surface_shortwave_radiation` — measure it
separately before declaring a contract.**

`cadence_basis`: `Cadence.DAILY` for every contracted signal in this lane
(`execution/coverage_contract.py:287,306,325`).

## 3. Historical horizon

**Earliest actually obtainable upstream — UNVERIFIED for both providers at the specificity this lane
needs.** The repo records only one loosely-related figure: a comment in `ingest/open_meteo.py:75-78`
states "the ERA5 archive... goes to 1940" for the Open-Meteo archive host in general, and flags itself
as "documentation-sourced, NOT live-probed" — it does not distinguish the `era5` model from the
`era5_land` model this lane actually pins, and ERA5-Land's own public start date is commonly later
than ERA5's. **Confirm by issuing one live `models=era5_land` request for a date far in the past and
reading the response**, not by trusting this comment for ERA5-Land specifically. NASA POWER's true
earliest offering has no citation anywhere in this codebase — no `HistoryCapability` is declared for
the historical (non-current-conditions) NASA lane. Confirm with a live probe against
`power.larc.nasa.gov` before declaring a horizon deeper than what is already held.

**Earliest we actually hold**, measured and declared, differs by producer and even by signal group
within one producer:

| producer / signal group | declared contract start | measured reality | cells |
|---|---|---|---|
| NASA POWER, 8 surface signals (temp/dew/precip/humidity/radiation/wind) | **2022-08-06** (conservative) | full 397-cell lattice from **2022-04-30** — 98 days deeper than the declared claim | 397 |
| NASA POWER, 3 soil-wetness signals | **2022-08-06** | genuinely widens here: 4 cells only for 2022-04-30..2022-08-05, then full 397 from 2022-08-06 | 397 (after widening) |
| Open-Meteo ERA5-Land, 8 signals | **2022-04-30** | matches declared start | 1,470 of 1,568 |

(`execution/coverage_contract.py:271-334`, `execution/AGENTS.md:1377-1385`.) The NASA surface
contract's 98-day gap between measured and declared coverage is a known, deliberate conservatism —
**"raising it is an owner call"**, recorded as open follow-up A in `execution/AGENTS.md:1479-1480`. Do
not silently "fix" it by widening the contract; that decision belongs to the owner, not to a wave-2
implementer.

Overall measured extent across the whole plane (both producers, all 19 signals): **2022-04-30 →
2026-08-06, 1,560 distinct days** (this session's measurement, matches the table above).

## 4. Grain

**`(support_key, signal_name, normalized_unit, cell_id, observed_day)`** — this is `SIGNAL_PLANE_GRAIN`,
declared beside `SIGNAL_PLANE_STREAM` in
`services/agri-data-service/src/agri_data_service/warehouse/parquet/schema.py:169-175`. It used to be
confirmed identically across four independent SQL statements (`signal_value_on_day.sql`,
`signal_neighbors_in_time.sql`, `signals_near_point.sql`, `nearest_signal_cells.sql`); this wave
deleted all four when the agent moved off PostgreSQL onto the Parquet schema above (`drizzle/0034_…sql`
still names them, an immutable historical record, left as-is). The dropped rollup's own `GROUP
BY`/unique constraint (`drizzle/0029:533`) agreed with the same grain. ~24.5M rows at this grain
across the whole 46,068,872-row plane.

**Every row is a point sample, never an areal average — even though it renders as a filled cell.**
Both writers record `spatial_support_kind = "point_sample"` explicitly
(`execution/historical_writer/nasa.py:284`, `execution/historical_writer/era5.py:262`), and the NASA
writer's own doc-comment warns: "requested one-degree points remain point samples; they never claim
the product's native... grid or acre-scale precision" (`historical_era5.py:1-8`, applies to the
lattice-sampling pattern generally). A re-implementer who treats a cell's value as a spatial average
over its polygon is asserting something no observation supports.

**`support_key` distinguishes spatial support, not depth**, and the two producers never collide on
one cell-day because they carry different values here:

| `support_key` | producer | lattice | cells |
|---|---|---|---|
| `surface` | NASA POWER | `nasa-power-0.5-degree` (0.5°, ~55,660 m) | 397 |
| `era5-land-0.1deg` | Open-Meteo ERA5-Land | sampled at native 0.1° (~9,000 m), reported against `sentinel2-ndvi-0p25deg` cells | 1,470 of 1,568 |

**19 contracted `signal_name` values** land in this plane (11 NASA POWER + 9 Open-Meteo ERA5-Land,
minus the one name they deliberately share — `surface_shortwave_radiation`):

| `signal_name` | producer | `normalized_unit` | source parameter |
|---|---|---|---|
| `air_temperature_mean` | NASA POWER | `C` | `T2M` |
| `air_temperature_max` | NASA POWER | `C` | `T2M_MAX` |
| `air_temperature_min` | NASA POWER | `C` | `T2M_MIN` |
| `dew_point_temperature` | NASA POWER | `C` | `T2MDEW` |
| `precipitation` | NASA POWER | `mm/day` | `PRECTOTCORR` |
| `relative_humidity` | NASA POWER | `%` | `RH2M` |
| `wind_speed` | NASA POWER | `m/s` | `WS2M` (scalar only — see §5) |
| `surface_shortwave_radiation` | NASA POWER (served) / Open-Meteo ERA5 (unserved, `era5-0.25deg`) | `MJ/m^2/day` | `ALLSKY_SFC_SW_DWN` / `shortwave_radiation_sum` |
| `soil_wetness_surface` | NASA POWER | `fraction_of_saturation` | `GWETTOP` |
| `soil_wetness_root_zone` | NASA POWER | `fraction_of_saturation` | `GWETROOT` |
| `soil_wetness_profile` | NASA POWER | `fraction_of_saturation` | `GWETPROF` |
| `soil_water_content_layer_1` | Open-Meteo ERA5-Land | `m^3/m^3` | `soil_moisture_0_to_7cm_mean` |
| `soil_water_content_layer_2` | Open-Meteo ERA5-Land | `m^3/m^3` | `soil_moisture_7_to_28cm_mean` |
| `soil_water_content_layer_3` | Open-Meteo ERA5-Land | `m^3/m^3` | `soil_moisture_28_to_100cm_mean` |
| `soil_temperature_level_1` | Open-Meteo ERA5-Land | `C` | `soil_temperature_0_to_7cm_mean` |
| `soil_temperature_level_2` | Open-Meteo ERA5-Land | `C` | `soil_temperature_7_to_28cm_mean` |
| `soil_temperature_level_3` | Open-Meteo ERA5-Land | `C` | `soil_temperature_28_to_100cm_mean` |
| `soil_temperature_level_4` | Open-Meteo ERA5-Land | `C` | `soil_temperature_100_to_255cm_mean` |
| `vapor_pressure_deficit` | Open-Meteo ERA5-Land | `kPa` | `vapour_pressure_deficit_max` |

(`execution/historical_backfill.py:48-60`, `execution/historical_open_meteo.py:184-215`,
`execution/coverage_contract.py:271-334`.) **NASA POWER's soil-wetness signals and Open-Meteo's
soil-water-content signals are different physical quantities and must never be blended**: POWER
reports a MERRA-2 **degree of saturation** (0=dry, 1=saturated), ERA5-Land reports **volumetric water
content** in m³/m³ (`execution/AGENTS.md:66-68,15`). They already carry distinct `signal_name`s, so
this is a naming discipline to preserve, not a bug to fix.

**`observation_count` is a source-release republication count, not a measurement count** — do not
weight anything by it. Mean 1.001 on current (2026-07) data; the historical 1.81–1.88× duplication
this column once suggested is a backfill-era artifact of overlapping archive releases, not an ongoing
property (verified facts, this session). **`coverage_fraction` is a constant `1.0`** and
**`allowed_client_exposure` is a constant `False`** across every governed row of this plane — the
latter is unresolved (misread column semantics vs. an unset default) despite the map painting this
data; do not build an exposure gate on it without settling that first
(RUNBOOK `conductor/RUNBOOK.md` §0.23.9). **The governed quality gate (`is_observed AND
quality_flag='accepted'`) currently removes zero rows** — keep it as a guard, not as evidence the data
has been filtered.

## 5. Known gaps and traps

1. **`surface_shortwave_radiation` has ZERO rows for NASA POWER in July 2026, while every sibling
   NASA-POWER signal has exactly 12,307** (397 cells × 31 days). Governed, under contract, and
   **unexplained** — this is a live lane gap, not a governed absence, and it is a ready-made first
   validation test case (§6). (RUNBOOK §0.22.8/§0.23.9.)

2. **Wind direction is not ingested.** NASA POWER requests eight surface parameters and `WD2M` is not
   among them (`historical_backfill.py:48-60`). `wind_speed` is a scalar; wind barbs/direction
   rendering are impossible from this lane without adding a new upstream parameter
   (`docs/layer-lane-standard.md` §14).

3. **The name `weather-observations` is overloaded in this repo and covers two unrelated
   producers on two different planes.** This document describes the **historical governed archive**
   (NASA POWER + Open-Meteo ERA5-Land) that lands in `agri.signal_observation` — the RUNBOOK's
   `weather-observations` lane. But `ingest/open_meteo.py` **also** defines a `WEATHER_LAYER` bound to
   the literal name `"weather-observations"` (`ingest/open_meteo.py:62-70`) that ingests **current
   conditions** from a **different endpoint** (`https://api.open-meteo.com/v1/forecast`, a 92-day
   rolling `past_days` window, `ingest/open_meteo.py:71-80`) and writes `FeatureWrite` rows —
   i.e. **`geo.features`, not `agri.signal_observation`** (`ingest/open_meteo.py:410`, cf.
   `docs/layer-lane-standard.md` §3's plane table). Confirm which of these two producers a given piece
   of wave-2 work is actually meant to migrate before writing code against either one — they share a
   layer/channel name but not a source, a plane, or a cadence.

4. **Radiation has two producers that structurally cannot be merged into one row.** NASA POWER writes
   `surface_shortwave_radiation` at `support_key='surface'`; Open-Meteo's ERA5 (not ERA5-Land) side
   lane writes the same `signal_name` at `support_key='era5-0.25deg'` — deliberately, so a reader can
   still tell which reanalysis and resolution produced a value (`execution/AGENTS.md:157-166`). Do not
   average or coalesce these; per §1 the second producer is not currently served by any reader anyway.
   If a Parquet lane forecasts or serves this signal, it should use the NASA POWER (`surface`) series,
   the one actually reaching production readers today.

5. **The ERA5-Land lattice is 1,470 of 1,568 cells, permanently** — the missing 98 sit on the Pacific
   edge (longitude -124.88..-122.38) where ERA5-Land publishes no data over water; this is not a gap
   to be filled (`execution/coverage_contract.py:326-333`). The lane's completeness floor is already
   set to `minimum_cell_fraction=0.9375` (exactly 1,470/1,568) to reflect this — **do not reset it to
   1.0**, which would classify every single day as permanently THIN and send a filler after cells no
   upstream will ever serve (this was a real, measured production bug, now fixed in
   `coverage_contract.py`, described at `execution/AGENTS.md:1481-1485` as "open follow-up B"; the
   note is stale relative to the code and should not be treated as still-open).

6. **NASA POWER's declared horizon is intentionally 98 days narrower than what production actually
   holds** for its 8 surface signals (declared 2022-08-06, measured 2022-04-30) — a conservative
   claim, not a bug, and raising it needs an owner decision (§3, `execution/AGENTS.md:1479-1480`).

7. **Duplicate/overlapping-release inflation (`observation_count` > 1) is concentrated in the
   backfilled years, not an ongoing property** — current-month duplication runs ~0.076% of rows.
   Don't design storage or validation around the historical 1.8× collapse; it will not recur going
   forward at that rate (verified facts, this session).

8. **The CDS-direct path (`historical_era5.py`) never persisted a warehouse row and is superseded —
   do not resurrect it or its plans** (§1). It remains only as an integration template for other,
   genuinely CDS-only products.

9. **`allowed_client_exposure` reads `False` on every governed row of this plane while the map already
   serves this data.** Unresolved in the repo — treat any exposure gate built on this column as
   provisional until that discrepancy is explained (RUNBOOK §0.23.9).

## 6. Validation approach

The repo already has a working pattern for reconciling what was written against what the source
system holds — reuse its shape rather than inventing a new one:

1. **Per-release coverage accounting already exists and should be the model for a Parquet
   validator.** Every source release records `expected_observation_count` vs
   `received_observation_count` in `agri.signal_coverage_audit`
   (`execution/historical_writer/nasa.py:333-356`, `era5.py:317-345`), and
   `require_accounted_open_meteo_result` enforces that every requested `(cell, parameter)` series is
   explained by exactly one coverage row spanning the whole window before a release can finalize
   (`execution/AGENTS.md:216-230`). A day with **no** value writes **zero** observation rows plus one
   `status='no_data'` audit row — never `is_observed=false` padding for every missing day. A
   Parquet-era validator should reproduce this expected/received accounting per day rather than
   inferring gaps from row absence alone.

2. **The existing gap-probe mechanism (`execution/coverage_fill.py`) is a live-source diff, already
   built.** It re-requests the upstream for the exact span of a suspected hole (one request per probed
   cell, not per day) and classifies the result as `SERVED` (some data came back, and the walk itself
   records the per-parameter `no_data` audit for the empty ones) or `EMPTY` (nothing came back for
   anything asked) — `execution/AGENTS.md:1313-1347`. This is the concrete "list objects, don't scan
   them" validation layer-lanes.md §4 asks a Parquet lane to have; adapt its request/diff shape rather
   than re-deriving one.

3. **A concrete, ready-made first test case**: request NASA POWER's `ALLSKY_SFC_SW_DWN` for the
   397-cell lattice across July 2026 (the exact window and parameter already known to be short) and
   compare the row count returned against the 12,307 every sibling NASA signal carries for that month
   (§5, item 1). This resolves whether the gap is real upstream-side (governed absence, record it) or
   an artifact of this repo's own write path (a defect to fix). It is explicitly unresolved today
   (RUNBOOK §0.23.9) and is the single most concrete validation exercise available for this lane.

4. **Failures must name the day, the lane and the source response** — "N rows mismatched" is not
   actionable (`conductor/code_styleguides/layer-lanes.md` §4). The existing coverage-audit rows
   already carry this shape (`window_start`, `window_end`, `status`, `details` with the source
   parameter) — preserve it.

5. **Do not conflate a real gap with a governed absence.** `agri.signal_coverage_audit.status` already
   distinguishes `complete | partial | no_data | failed`
   (`docs/layer-lane-standard.md` §7); a day recorded `no_data` for a genuinely out-of-domain cell (the
   98 Pacific-edge ERA5-Land cells in §5) must not be re-walked forever, while a day with no audit row
   at all is a hole a validator should flag, not silently accept.

## 7. Forecast recommendation

**`horizon: 30d`.** The RUNBOOK explicitly classifies `weather-observations` as **"yes — the core
forecast lane"**, calling it out ahead of every other of the eleven lanes
(`conductor/RUNBOOK.md` §0.24.2). Per the lane contract, this means a `method/monte_carlo/
weather-observations.py` module is required, not optional, and an absent one "reads as unfinished
work rather than a settled property"
(`conductor/code_styleguides/layer-lanes.md` §2).

**What gets projected**: this lane carries 19 physically distinct signals (§4) — temperature,
humidity, wind speed, precipitation, radiation, dew point, three soil-wetness depths, four soil-
temperature depths, and vapor pressure deficit. **Recommend forecasting each `signal_name`
independently, per cell**, not one blended "weather" quantity: they have different units, different
distributional shapes, and (for radiation) different canonical producers (§5, item 4). Do not attempt
a single joint model across all 19 as a first cut.

**Existing precedent to build from**: the only Monte Carlo forecaster in the repo today,
`method/monte_carlo/vegetation_ndvi_forecast.py`, uses a seeded (`numpy.random.PCG64`) seasonal-anomaly
bootstrap — day-of-year climatology plus a resampled anomaly pool plus a short-lag persistence anchor,
emitting low/median/high quantiles. This is the shape `conductor/code_styleguides/layer-lanes.md` §3's
provenance columns (`forecast_run_id`, `random_seed`, `ensemble_size`, `horizon_days`, `issued_on`,
`quantile`/`draw_index`) are built around, and is the reasonable starting template for each weather
signal's forecaster.

**What drives uncertainty, and where the NDVI template will NOT transfer cleanly:**

- **Temperature, dew point, humidity, wind speed, soil temperature**: strong day-of-year seasonality
  plus short-lag day-to-day persistence — the same structure NDVI's anomaly-bootstrap already assumes.
  Reasonable to reuse the existing template's shape directly.
- **Precipitation**: zero-inflated and heavily right-skewed, not the bounded, near-continuous
  distribution NDVI's anomaly pool assumes. A direct copy of the NDVI method would likely
  misrepresent precipitation's uncertainty (e.g. a symmetric anomaly bootstrap can produce physically
  impossible negative precipitation draws). This is flagged here as an **open design question**, not a
  settled recommendation — whoever builds this forecaster should treat precipitation as its own
  design problem, not a parameter change on the NDVI template.
- **Surface shortwave radiation**: forecast only the NASA POWER (`support_key='surface'`) series — the
  one actually served today (§5, item 4) — not an average across the two disagreeing producers.
- **Soil wetness vs. soil water content**: already distinct `signal_name`s reporting different
  physical quantities (§4); treat and forecast as fully independent series, never merged.
- **The `issued_on` anchor must respect each signal's own producer lag** (§2): NASA-POWER-backed
  signals should anchor their forecast issue day at `today − 5d`, Open-Meteo-ERA5-Land-backed signals
  at `today − 9d`, and `surface_shortwave_radiation` specifically needs its own measured lag rather
  than inheriting NASA's blanket 5-day constant, given its documented ~2-month ceiling (§2). Issuing
  every signal's forecast from the same anchor day would silently forecast over several days of data
  that has not actually landed yet, for the slower-publishing producer.
