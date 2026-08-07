---
type: track-spec
track: upstream_dataset_expansion_20260806
status: planned
---

# Upstream dataset expansion — specification

Everything below was measured against the working tree on 2026-08-06 and
[`docs/unused-upstream-datasets.md`](../../../docs/unused-upstream-datasets.md) (survey
date 2026-08-06). File:line citations are evidence. Where this document proposes rather
than measures, it says so.

## Goal

The ERA5-Land soil-state lane has exactly one credible upstream route, and it is the
keyless one. Four upstream capabilities the platform has no source for today —
fire-weather ignition stress, river discharge history, wildfire smoke, and forecast
uncertainty — land through the two ingestion standards this repo already runs in
production: the checkpointed `historical-*` **durable** backfill, and the scheduled
`ingest-*` **cron** verb. Nothing here invents a third pattern.

## Why this track exists

[`docs/unused-upstream-datasets.md`](../../../docs/unused-upstream-datasets.md) is a
survey. It correctly separates verified variables from unverified candidates and records
that the CDS lane is being retired, but a survey does not unregister a scheduled task,
does not add a whitelist entry, and does not decide how four new upstream capabilities
map onto two existing ingestion patterns. That is this track.

There is also a live correctness problem the survey only names. The CDS ERA5-Land soil
lane is not merely slower than its Open-Meteo replacement — it is actively driven by a
Windows scheduled task, `PlantGeo-ERA5-PNW-backfill`, that keeps retrying against a
provider queue that resolved 2 of 49 periods in a day against repeated 502s and dropped
connections, at a coarser 1.0-degree grid, for the same reanalysis Open-Meteo already
serves keylessly at 0.1 degrees. Every day that task stays registered it spends wall
clock and CDS quota on a lane this track is retiring.

## Settled findings

Measured. Implement against these; do not re-derive them.

**1. Every Open-Meteo archive plan is pinned to one model for its whole lifetime, which
is exactly why `et0_fao_evapotranspiration` is a trap and exactly how to close it.**
[`historical_open_meteo.py:153`](../../../services/agri-data-service/src/agri_data_service/execution/historical_open_meteo.py#L153)
declares `model: Literal["era5_land"]` on the plan itself — not per parameter. There is no
field anywhere in this lane that could carry `model=era5` for one variable while the rest
of the plan stays `era5_land`. So `et0_fao_evapotranspiration` cannot be safely whitelisted
in
[`OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS`](../../../services/agri-data-service/src/agri_data_service/execution/historical_open_meteo.py#L91)
as it stands today — doing so would accept the parameter, silently request it under
`era5_land`, and receive nulls the lane's own bounds check would wave through, because
`None` is a legitimate no-data result, not an out-of-range value. The guard has to be
structural (the parameter stays out of the whitelist, provably, under test) rather than a
comment, because the whole point of a trap is that it does not error.

**2. Registering a brand-new upstream source needs no Alembic revision.**
[`models/provenance.py:69`](../../../services/agri-data-service/src/agri_data_service/models/provenance.py#L69)
declares `DataSource.key` as `String(100), unique=True` — free text, not an enum or a
check-constrained set. A new `data_source` row is DML written by the ingestion code at
first run (the same "ensure" pattern every existing lane already uses), not a migration.
`ReleaseSet.logical_key` and `ReleaseSet.manifest_checksum`
([`models/provenance.py:193-195`](../../../services/agri-data-service/src/agri_data_service/models/provenance.py#L193))
are both separately unique, so each new plan needs its own release-set key, but that key
is also just a string a plan declares.

**3. Changing a plan's `parameters` changes `plan_checksum` and orphans the checkpoint and
raw cache.** This is why every addition below is scoped as a new plan file, a new
`release_set.logical_key`, and a fresh quota-bound fetch — confirmed by the same rule
already governing the soil-temperature bands whitelisted 2026-08-06 and the standing
Open-Meteo lattice plans in
[`plans/AGENTS.md`](../../../services/agri-data-service/plans/AGENTS.md).

**4. The CRON standard is a `railway.json` only, against one shared image.**
[`infra/cron-weather/railway.json`](../../../infra/cron-weather/railway.json) and
[`infra/cron-streamflow/railway.json`](../../../infra/cron-streamflow/railway.json) each
carry no `Dockerfile` of their own — both point `dockerfilePath` at
[`infra/cron-ingest/Dockerfile`](../../../infra/cron-ingest/Dockerfile), the same image
every cron service builds, and differ only in `cronSchedule` and `startCommand`. A new
scheduled source is therefore one new `infra/cron-<name>/railway.json`, one new
`ingest-<name>` command registered through
[`register_ingest_commands`](../../../services/agri-data-service/src/agri_data_service/ingest/commands.py),
and the Railway dashboard wiring `plantgeo-ingest-cron` already needed (Root Directory
`/`, config-as-code path pointed at the new `railway.json`). No source is required to join
`ingest-all` — `ingest-mtbs` is deliberately excluded from it today because it publishes
quarterly rather than hourly, and no `infra/cron-mtbs/` exists at all. A new source can
pick its own cadence and its own membership.

**5. `signal_observation`'s uniqueness already carries a free-text discriminator beyond
signal and time.**
[`models/historical.py:116-125`](../../../services/agri-data-service/src/agri_data_service/models/historical.py#L116)
uniques on `(source_release_id, cell_id, signal_name, source_parameter, support_key,
observed_at)`. `support_key` already carries non-depth meaning in production —
`era5-land-0.1deg` distinguishes the Open-Meteo archive lane from the CDS lane's
`surface` — so it is available, without DDL, to distinguish any other same-signal,
same-timestamp variants a new source might need to carry.

**6. The forecast plane already has a quantile representation built for exactly
ensemble-shaped output, and it is not hardcoded to the in-house Monte Carlo.**
`ForecastReceipt.quantile_levels`
([`models/forecasting.py:678`](../../../services/agri-data-service/src/agri_data_service/models/forecasting.py#L678))
is an `ARRAY(Float)` checked by `agri.forecast_quantiles_valid(...) AND 0.5 = ANY(...)`,
and `ForecastValue`
([`models/forecasting.py:719-723`](../../../services/agri-data-service/src/agri_data_service/models/forecasting.py#L719))
carries `point_value`, `p10_value`, `p50_value`, `p90_value`, plus a generic
`quantile_values` JSONB for any quantile beyond those three. `ForecastSeries.data_source_id`
([`models/forecasting.py:39`](../../../services/agri-data-service/src/agri_data_service/models/forecasting.py#L39))
is a foreign key to any `data_source`, not a constant — the schema does not assume the
forecast came from this codebase's own model. This is load-bearing for the Ensemble API
open question below.

**7. Variable-name verification is a hard discipline here, enforced by what getting it
wrong costs.** `docs/unused-upstream-datasets.md` separates "Verified available" from
"Unverified candidates — probe before planning" for exactly one reason stated at the top
of that file: an unverified name "goes straight into checksummed plan files, so an
unverified name costs a full re-fetch." `vapour_pressure_deficit_max` is in the verified
column (3.32 kPa, live). None of the three new API surfaces below have been probed this
way yet — their field names are inferred from Open-Meteo's published documentation, not
confirmed against the live endpoint, which is the same status the doc gives
`precipitation_sum` and `wind_gusts_10m_max` today.

**8. The CDS retirement is a measured throughput and reliability decision, not a
preference.** Per `docs/unused-upstream-datasets.md` and the session that produced it: the
keyless Open-Meteo lane moved 6,447,420 rows in an afternoon at 0.1 degrees; the CDS lane
resolved 2 of 49 monthly periods in a day against repeated 502s and dropped connections, at
a coarser 1.0-degree output grid. `plans/AGENTS.md`'s CDS costing section shows this is not
a cost-model problem to tune — area is free and only time-span-per-request is a cost
input, so a quarterly-period rechunking would help throughput but does nothing about
provider-side queue latency, which is the actual failure mode observed. Retiring the lane
for soil state, rather than re-chunking it, is the correct response to the measurement.

## The four datasets

| # | Dataset | Standard | Status |
|---|---|---|---|
| 1 | `vapour_pressure_deficit_max` | durable only | verified, ready the moment Phase 0 merges |
| 2 | Flood API (GloFAS discharge) | cron | needs a live-endpoint probe |
| 3 | Air Quality API (CAMS) | cron | needs a live-endpoint probe |
| 4 | Ensemble API | cron, schema-gated | needs a probe and an owner schema decision |

### 1. `vapour_pressure_deficit_max` — durable, verified

The standard fire-weather ignition covariate, and it is absent from every source this
platform currently ingests — NASA POWER's daily bundle does not carry it, and neither
does the current-conditions Open-Meteo forecast path. Verified 2026-08-06 against the
live archive endpoint at 3.32 kPa. This is a single new entry in
`OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS`, on the same `era5_land`-pinned lane the soil
bands already use, with its own bounds (Open-Meteo documents VPD as non-negative; a
reviewed upper bound belongs in the entry, not left at the type's default). It needs no
new cron service — like soil moisture and soil temperature, this is a historical/ML
covariate, not a live map layer.

### 2. Open-Meteo Flood API — GloFAS river discharge

Gives the water layer river-discharge *history*, where USGS NWIS today gives point
observations. This is a different capability from streamflow, not a replacement for it:
NWIS is first-party US gauge telemetry; GloFAS is a global reanalysis/forecast model, so
the two would coexist the way the CDS and Open-Meteo ERA5-Land lanes coexist — different
provenance strength, same physical quantity, both worth keeping. Primary standard is cron
(`ingest-flood-discharge`, paired in cadence with `ingest-streamflow`'s `*/30 * * * *`
schedule, since both feed the same live water-layer use case). A durable historical
archive lane is a natural second phase once the cron lane has proven the source, but is
not required for this phase to ship — see open question 2.

### 3. Open-Meteo Air Quality API — CAMS PM2.5 / PM10 / dust

Wildfire smoke coverage. No ingested source today describes downwind smoke impact; FIRMS
(`ingest-firms`, `30 */3 * * *`) and MTBS describe the fire and the burn scar, not the
plume. Primary standard is cron (`ingest-air-quality`), cadenced to pair usefully with
FIRMS rather than necessarily matching its schedule exactly — dust and smoke concentration
change faster than a 3-hourly fire-detection sweep needs.

### 4. Open-Meteo Ensemble API — probabilistic forecast members

The platform's only realistic upstream source of genuine forecast uncertainty; today,
every quantile band in `ForecastValue` is produced by this codebase's own Monte Carlo
(`method`/`execution` layer), never by an upstream provider. Primary standard is cron
(`ingest-ensemble-forecast`), because ensemble members are a forecast product issued
against a clock, not a static historical archive to replay. Landing the output is a real
open question — see below — but is answerable inside the existing `forecast_receipt` /
`forecast_value` schema per finding 6, without DDL, if the reduction to quantiles at
ingest time is acceptable.

## Retiring the CDS soil lane

Concrete, in scope for Phase 0:

- Unregister the `PlantGeo-ERA5-PNW-backfill` Windows scheduled task. This is the thing
  actually spending wall clock and CDS quota against a queue that is not answering; it is
  outside this repository and outside any agent's authority to touch directly, but the
  plan must name it as a required action, not an assumption someone else will notice it.
- Stop scheduling and stop authoring new plans against
  `era5-land-pnw-soil-20220430-20260430.json` and
  `era5-land-western-na-soil-20220430-20260430.json` (both present, uncommitted, in
  `services/agri-data-service/plans/`). Retiring the *lane for soil state* does not mean
  deleting `historical_era5.py` or the CDS client — per
  `docs/unused-upstream-datasets.md`, CDS remains the only route to AgERA5
  agrometeorological indicators, CEMS fire danger indices, and seasonal forecasts, none of
  which Open-Meteo redistributes. The code stays; the soil-state invocation of it stops.
- `open-meteo-era5-land-pnw-soiltemp-20220430-20260430.json` already exists in the same
  directory — the Open-Meteo replacement plan for soil temperature has already been
  authored. Its presence is evidence the retirement is already underway in intent, not
  merely proposed here.

## The et0 trap, closed structurally

Not "add a comment" — the finding is that a comment would not have prevented this, because
the failure mode is a silent accept. The closure is a test that asserts
`et0_fao_evapotranspiration` is absent from `OPEN_METEO_ARCHIVE_SIGNAL_SPECIFICATIONS` for
as long as the plan schema hardcodes a single `model` per plan (finding 1), so a future
addition that reintroduces it fails the suite instead of landing nulls with a valid
checksum. If a future track wants `et0_fao_evapotranspiration` for real, the correct fix is
a second, `era5`-pinned plan type at 0.25 degrees — a materially different, coarser lane —
not a whitelist entry on this one.

## Non-goals

- **The unverified candidates table in `docs/unused-upstream-datasets.md`**
  (`precipitation_sum`, `rain_sum`, `snowfall_sum`, `shortwave_radiation_sum`,
  `direct_radiation`, `diffuse_radiation`, `wind_gusts_10m_max`, `cloud_cover`,
  `growing_degree_days_base_0_limit_50`). Each needs its own probe before it is a plan; none
  is in this track's four.
- **The Climate Change API (CMIP6), Satellite Radiation, Marine, and Elevation APIs.** The
  survey doc notes lower relevance to the current layers; out of scope here.
- **Any new map layer or UI for the three cron datasets.** This track ingests; serving is a
  separate decision, matching precedent — vegetation has been ingested with no reader for
  some time, and that gap belongs to a different track.
- **Deleting or refactoring `historical_era5.py` or the CDS client.** CDS is retired for
  soil state only; the code and its credential contract stay for AgERA5, CEMS, and seasonal
  forecasts.
- **Resolving any other track's open question**, including the community-submission ML
  label bridge.
- **Building the durable historical-archive lane for Flood or Air Quality in this track.**
  Scoped as a possible follow-on, not a requirement — see open question 2.

## Open questions — owner input required

1. **Ensemble member storage.** Reduce Open-Meteo's raw ensemble members to quantiles at
   ingest time and land them in the existing `forecast_receipt.quantile_levels` /
   `forecast_value.quantile_values` (no DDL, matches finding 6, but discards the raw
   per-member correlation structure an ML consumer might eventually want) — or persist raw
   per-member draws, which needs a new storage decision and likely a migration. The
   no-DDL path is available today; the raw path is not scoped in this track unless chosen.
2. **Do Flood API and Air Quality API get a durable historical-archive lane in this
   track, or does the cron lane alone satisfy the ask, with the archive scoped as a fifth
   capability later?** The literal ask was four new datasets; this track can ship all four
   as cron-only and still be complete, or the owner may want the archive lane bundled now
   while the endpoint is already being probed.
3. **Does GloFAS discharge coexist with USGS NWIS on the map, or does one supersede the
   other for the water layer?** This is a serving decision outside this Python-only track,
   but the cron cadence and whether the source joins `ingest-all` depend on the answer.
4. **Do the three new cron sources join `ingest-all`, or stay excluded like `ingest-mtbs`
   until each has run cleanly for a few cycles?** A new source failing inside `ingest-all`
   fails every other source's run with it.
