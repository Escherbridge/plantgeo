# PlantGeo data-lane execution ownership — 2026-08-28

**Superseded by [`docs/reports/data-lane-execution-ownership-2026-09-02.md`](./data-lane-execution-ownership-2026-09-02.md)** — the executor described here as a not-yet-merged proposal is now the sole production scheduler; see that report's "What the 2026-08-28 report said that is no longer true" for itemized corrections.

## Executive conclusion

PlantGeo does not yet have one durable owner for the complete path from publisher to PostgreSQL to
live Parquet. Production currently combines four ownership models:

1. the hourly `plantgeo-ingest-cron`, which sequentially runs eight forward source ingesters,
   vegetation catch-up, `jobs-pulse`, and a generic Parquet gap fill;
2. healthy source-specific legacy services for direct fire, MTBS, and the finite SoilGrids cache
   warm;
3. a direct water writer whose first database-reference repair rendered empty during a real cron run;
   its loader DSN now references the proven ingest-cron credential owner, but a successful scheduled
   write after that second repair is not yet proven; and
4. offline or plan-based climate/soil workflows whose dedicated products are immutable snapshots,
   not durable forward lanes.

The repository already has the right durability substrate in `agri.job_*`: persisted job
definitions and runs, fenced work-item leases, bounded attempts/backoff, checkpoints, dead letters,
and per-definition pause state. The missing layer is one continuously running, self-configured
execution service that applies source-specific cadence and publication lag, chooses fair
newest-first incremental work plus historical backlog, and owns every active lane only after an
explicit legacy-writer handoff.

The safely deployable transition is therefore a **shadow-first dedicated executor**. The implementation
on this branch inventories every lane and predicts its current cadence bucket without registering ledger
definitions or reading/writing source state while it is in shadow mode. A write-capable lane remains
inactive until its named legacy owner is disabled, no run is in flight, and the exact operator handoff
acknowledgements are supplied. Those acknowledgements are a fail-closed gate, not machine-verified
Railway or source-watermark parity. PostgreSQL remains the ingestion and transition bridge. No row,
service, writer, or source may be deleted as a shortcut.

The most important ownership gaps are upstream of Parquet:

- `signal` has a scheduled generic Parquet projection but no durable recurring NASA POWER or
  Open-Meteo ERA5-Land forward owner;
- `soil-survey` has a scheduled Parquet projection but only lazy/manual SSURGO population;
- `watersheds` has a scheduled Parquet projection but no scheduled WBD source refresh;
- WFIGS fire-perimeter history is declared but not wired; and
- 15 registered climate/soil product streams plus three extra soil-wetness breakdowns are bound to
  one frozen canonical snapshot rather than to incremental source receipts.

## Production evidence

Evidence below was read from Railway production without installing or using the Railway CLI.

- Project: `6faaf3ea-ac46-4c8b-bbfe-1351dbb9d990`
- Environment: `b7cfa813-8a5c-4fcd-80f2-cab736d840a7`
- Repository baseline inspected: `9f1cd8613e39e15e55f6f2d30576e0074401067d`

| Service | Service ID | Deployment evidence | Operational evidence |
| --- | --- | --- | --- |
| `plantgeo-ingest-cron` | `3ae3cc37-c398-43fe-b74c-83e4da130423` | `4a132f12-8d84-4935-b5ec-9ace61192ce1`, `SUCCESS`, built at the exact inspected main SHA | Run began `2026-08-28T18:05:05Z`; forward ingestion and geometry repair progressed, but `jobs-pulse` ended red at `18:30:44Z`, detailed below |
| `plantgeo-fire-detections-forward` | `f4ad61fe-e71a-4776-b9d5-0b153c9ee5b7` | `5e3ebe9f-5a26-449b-85d1-344c32a44c2a`, `SUCCESS` | Scheduled run succeeded `2026-08-28T17:16:02.231Z`; selected settled days `2026-08-26` and `2026-08-25`, published z13 and z9/z5/z0 ladders with 2,274 and 1,574 base rows, and reported no remaining selected-window backlog |
| `plantgeo-water-gauges-forward` | `40cb252b-e21c-4140-8d94-5db77eb2398d` | Previous deployment `45942b88-451c-4598-921d-34cbe9bebfd3` was `CRASHED`. The first transition deployment `b4e6a1fc-1c64-4fed-9a9d-666f3eca575c` also crashed on its `2026-08-28T23:19Z` cron run. Second-reference deployment `1313429e-2750-48df-942f-912af90d0552` reached `SUCCESS` at `23:22:51Z` | The original failure fetched 883 writable records across 18 sentinel sites, then raised `set LOCAL_SOURCE_LOADER_DATABASE_URL or DATABASE_URL`. The first attempted reference `${{Plantgeo.DATABASE_URL}}` rendered empty: its real scheduled run fetched 882 writable records across 19 sites and raised the same error. At `23:22:14Z`, **only this service** was changed again so `LOCAL_SOURCE_LOADER_DATABASE_URL` references the already-working `plantgeo-ingest-cron.LOCAL_SOURCE_LOADER_DATABASE_URL`. Deployment success proves configuration delivery only; the next normal `15 * * * *` run must prove the DSN resolves and the write completes |
| `plantgeo-cron-mtbs` | `a683cc83-2b49-4276-a136-941e1b2cbe24` | `0740d237-3afa-419c-b9eb-02be2892238e`, `SUCCESS` | Healthy weekly source-specific release refresh |
| `plantgeo-cron-soilgrids` | `0960aa81-4499-4cb1-9daa-3350eed4d654` | `28468b13-66f5-41c4-8e6f-3ccc531f1771`, `SUCCESS` | Healthy finite cache warm; logs showed the cached target population complete/no pending cells. This is ISRIC `public.soil_grid_cache`, not SSURGO `soil-survey` ownership |
| `plantgeo-soil-moisture-parquet-load` | `4a1413f1-5f96-44ea-853c-6a379c7673c4` | `29c54089-79b1-45a4-8b9e-471369e2ce93`, `SUCCESS` | Completed a large finite snapshot load. Success is evidence for the immutable backfill, not a recurring forward schedule |
| `plantgeo-parquet-api` | `33aed861-af76-4fdd-a95e-784bdcc95e55` | `480139cd-9629-4cd3-91bc-afc104657f17`, `SUCCESS` | Serving service healthy at evidence capture |

### Exact `jobs-pulse` failure

Deployment `4a132f12-8d84-4935-b5ec-9ace61192ce1` emitted this terminal record at
`2026-08-28T18:30:44Z`:

```text
jobs_pulse_tick_failed failing_lane_count=2 failing_lanes=[
  {
    'lane':'matview-refresh',
    'kind':'dispatchable',
    'outcome':'dead_lettered',
    'dead_lettered':1,
    'standing_dead_letters':139,
    'detail':'dead-lettered 1 of 3 claimed (succeeded 0, retried 2, run_status running); 139 work item(s) standing dead-lettered for this definition, first matview-refresh:20260816T023533998619Z; the lane is not idle, its work is buried -- requeue those items, or cancel them deliberately, and the next tick goes green'
  },
  {
    'lane':'validate-streams',
    'kind':'maintenance',
    'outcome':'invalid',
    'dead_lettered':0,
    'standing_dead_letters':0,
    'detail':'complete=4, incomplete=5, invalid=3'
  }
] lane_count=9
```

At about `18:26:46Z`, `matview-refresh` had refreshed `geo.mv_feature_observation_day` in
90.98 seconds, logged missing `geo.mv_feature_observation_day_axis` and
`geo.mv_signal_cell_daily`, dead-lettered one work item on attempt three, and placed two in
`retry_wait`. This was not an idle/no-work failure: 139 standing dead letters meant work was buried,
and the validation pass independently found three invalid streams.

The ingest image deliberately runs its four verbs unconditionally and combines their exit codes at
the end (`infra/cron-ingest/Dockerfile`). A failed `jobs-pulse` therefore does not skip the generic
Parquet gap-fill command that follows it, although it correctly makes the overall cron run red.

No rows or services were deleted, and no legacy service was disabled during this evidence-gathering
or water-configuration repair.

## The three inventories

These inventories answer different questions and must not be merged into one count:

- `agent/tools.py:132-164` declares **24 user-facing surfaces**: 11 `geo.features` surfaces and 13
  stream-backed names.
- `pipeline/parquet/lane_registry.py:711-941` registers **13 live Parquet storage streams**: 12
  database-backed streams plus the computed calendar.
- `tests/parquet/test_snapshot_signal_product_schemas.py:25-55` asserts **15 additional registered
  snapshot-product schemas**. Three more immutable soil-wetness breakdowns are not live registered
  schemas.

## Registered live Parquet streams

`parquet-gap-fill` currently treats incremental and historical projection as one job: it inventories
the contracted window, selects missing days newest-first, and visits lanes round-robin until its
wall-clock budget is spent (`pipeline/parquet/gap_fill.py:1294-1369`). Each lane-day uses a shared
PostgreSQL advisory lock, but the command itself has no `agri.job_*` lease
(`pipeline/parquet/gap_fill.py:1230-1245`). Its resume state is inferred from immutable objects and
completion markers.

| Parquet stream / served surface | Source → PostgreSQL ingestion | Source forward ownership | Source history ownership | Parquet incremental and backfill ownership | Cadence, lag, held horizon | Current disposition and blocker |
| --- | --- | --- | --- | --- | --- | --- |
| `burn-severity` | MTBS ArcGIS release cohorts → `geo.features` (`docs/lanes/burn-severity.md:18-23`) | Healthy weekly `plantgeo-cron-mtbs`; `ingest-mtbs` is intentionally excluded from `ingest-all` | Same command re-reads established dated fire-year cohorts; no arbitrary calendar-window history lane | Generic gap fill exports release days and governed non-release absences | Irregular release series; floor `2020-11-24`; lag 7; no honest fixed cadence (`lane_registry.py:711-729`) | Consolidate as a weekly source-specific handler after parity. Preserve its refusal when a cohort lacks an established release date |
| `drought` → `drought-areas` | US Drought Monitor → `geo.drought_areas` | Current release is checked inside hourly `ingest-all`, although the publisher is weekly | `ingest-drought-history` walks release Tuesdays, but has no scheduled durable owner (`ingest/commands.py:462-493`) | Generic gap fill from PostgreSQL | Weekly release series; floor `2022-08-09`; lag 4; cadence 7 (`lane_registry.py:731-749`) | Forward can consolidate now. Add durable release-week work items for history |
| `evacuation-zones` | Oregon OEM current-state ArcGIS view → `geo.features` | Hourly `ingest-all` | Explicitly unsupported; prior evacuation levels cannot be reconstructed (`ingest/evacuation_zones.py:108-124`) | Generic source-watermark snapshot projection | Static lookup; inert floor `2025-04-14`; lag 0 (`lane_registry.py:751-765`) | Consolidate forward polling; report history as unsupported rather than authoring phantom gaps |
| `fire-detections` | NASA FIRMS forward/archive → `geo.features`; the direct forward writer fetches FIRMS itself and uses PostgreSQL for coordination | PostgreSQL bridge remains in `ingest-all`; healthy direct source→Parquet legacy service owns current days | Durable `firms-archive` lane, floor `2000-11-01`, newest-first five-day windows (`ingest/lanes.py:177-199`) through `jobs-pulse` | Generic history owns through `2026-08-24`; direct writer owns `2026-08-25+` (`pipeline/lanes/fire_detections.py:26`, `lane_registry.py:767-781`) | Daily; floor `2000-11-01`; lag 2 | Healthy legacy writer; migrate last. Existing writer ceiling is the correct explicit non-overlap boundary |
| `fire-perimeters` | NIFC WFIGS current-incidents view → `geo.features` | Hourly `ingest-all` | Code declares history from `2020-01-01`, but the historical endpoint is not wired into the backfillable source registry (`ingest/wfigs.py:105-112`; `docs/lanes/fire-perimeters.md:82-100`) | Generic gap fill projects accumulated current snapshots | Daily; actual-held floor `2025-07-28`; lag 1 (`lane_registry.py:783-798`) | Forward can consolidate. Backfill needs a reviewed historical WFIGS adapter plus identity/version semantics |
| `sensors` | NOAA/NWS `api.weather.gov` → `geo.features` | Hourly `ingest-all` | Manual `ingest-backfill` reaches only the source's moving ~6-day retention; no durable lane (`docs/lanes/sensors.md:80-91`) | Generic gap fill from accumulated PostgreSQL rows | Daily storage; floor `2026-07-29`; lag 1 (`lane_registry.py:800-813`) | P0 forward lane: missed schedules become unrecoverable after retention rolls. Measure and declare the actual source cadence |
| `signal` → 12 climate/soil field surfaces | NASA POWER daily and Open-Meteo ERA5-Land archive → `agri.signal_observation` (`docs/lanes/weather-observations.md:18-133`) | **No durable recurring Railway forward owner** | Plan/checkpoint workflows and `durable-backfill.sh` exist as a separate historical mechanism explicitly “still to unify” (`docs/runbooks/durable-backfill-lanes.md:585-603`) | Generic `signal` gap fill projects only what PostgreSQL already holds | Daily; floor `2022-04-30`; lag 9, chosen for slower ERA5-Land (`lane_registry.py:815-828`) | Projection-only ownership. Add daily provider jobs, provider-specific lags, source receipts, and incremental product derivation |
| `soil-survey` | USDA NRCS SSURGO/SDA → `geo.features` and conformed geometry through lazy reads/manual bulk driver | **No scheduled SSURGO source owner**; population is partial by construction (`docs/lanes/soil-survey.md:56-74,132-134,269-272`) | Survey-area vintages can be retained, but no durable survey-area/version backfill exists | Generic static-watermark projection from the partial PostgreSQL population | Static lookup; inert floor `2025-08-26`; lag 0 (`lane_registry.py:830-844`) | Projection-only ownership. Build a bounded, paged SSURGO worker. SoilGrids health is unrelated |
| `vegetation` | Sentinel-2 L2A → raw `geo.features` → governed NDVI observations | Hourly `ingest-all`; its `on_persisted` callback publishes affected Parquet days (`ingest/runner.py:47-55`) | Source supports history to `2015-06-27`, exposed only through manual `ingest-backfill` (`ingest/vegetation.py:78,1042`) | In-process forward writer, vegetation catch-up, and generic gap fill share a publication barrier | Daily storage; floor `2022-08-05`; lag 7 (`lane_registry.py:846-862`); upstream nominal revisit 5 days | Consolidate carefully. Preserve the persisted-source callback and barrier; add durable source-history ownership |
| `water-gauges` | USGS NWIS instantaneous feed → `geo.features`; daily-values endpoint supplies archive | PostgreSQL bridge works in `ingest-all`; direct writer's missing DSN reference was repaired, but its first post-fix scheduled write is unproven | Durable `streamflow-archive`, floor `2022-08-05`, newest-first 30-day windows/10-day chunks (`ingest/lanes.py:201-215`) | Generic gap fill owns the whole declared window; direct writer targets the same current namespace and shares the lane-day lock | Daily; held floor `2026-05-24`; lag 2 (`lane_registry.py:864-878`) | Activation blocker: unlike fire, water has no writer ceiling. A lock prevents simultaneous mutation, not alternating ownership. Establish an explicit handoff before enabling replacement writes |
| `watersheds` | USGS WBD HUC12 ArcGIS → `geo.features` | `ingest-watersheds` exists but is absent from `ingest-all` and has no cron (`docs/lanes/watersheds.md:96-109`) | No prior-revision archive is fetched | Generic static-watermark projection can run only after PostgreSQL changes | Static lookup; inert floor `2026-08-07`; lag 0 (`lane_registry.py:880-893`) | Projection-only ownership. Add a low-cadence current-version check/reload; do not invent prior revisions |
| `weather-observations` | Open-Meteo current-conditions forecast endpoint → `geo.features`; distinct from historical `signal` despite the overloaded name | Hourly `ingest-all` | Endpoint advertises a moving 92-day `past_days` window, but it is not wired into the backfillable registry (`ingest/open_meteo.py:71-80,152-155`) | Generic gap fill from `geo.features` | Daily; fallback floor `2026-08-01`; lag 2 (`lane_registry.py:895-917`) | Forward can consolidate. Author and measure the current-conditions contract before claiming historical parity |
| `calendar` | No publisher and no PostgreSQL source rows; computed from registered floors and object coverage | Generic gap fill computes a new version when required forward coverage moves | Same writer regenerates the complete dimension | Generic static adapter | Derived floor `2000-11-01`; lag 0 (`lane_registry.py:919-941`) | Safe first active scheduler candidate; still give it persisted due-state and a lease |

## Snapshot-only climate and soil products

All registered snapshot products below are asserted in
`tests/parquet/test_snapshot_signal_product_schemas.py:25-55`. Their builders are resumable and
receipt-checked, but they read the immutable raw canonical snapshot
`prod-20260826-full-signal-v1`, pinned to source-manifest SHA-256
`465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f`. They are finite migration
tools, not recurring forward writers.

### Fifteen registered snapshot product streams

| Product stream(s) | Frozen source parameter(s) | Builder | Ownership verdict |
| --- | --- | --- | --- |
| `climate-field-air-temperature-mean`, `-max`, `-min` | NASA POWER `T2M`, `T2M_MAX`, `T2M_MIN` | `scripts/air_temperature_snapshot_breakdown.py:56-74` | Snapshot-only; no product-level direct-source forward owner |
| `climate-field-wind-speed` | NASA POWER `WS2M` | `scripts/breakdown_wind_speed_snapshot.py` | Snapshot-only; scalar speed only, no wind-direction ingestion |
| `climate-field-relative-humidity` | NASA POWER `RH2M` | `scripts/build_relative_humidity_from_canonical_snapshot.py:46-57` | Snapshot-only |
| `climate-field-shortwave-radiation` | NASA POWER `ALLSKY_SFC_SW_DWN` | `scripts/build_shortwave_radiation_from_canonical_snapshot.py:49-61` | Snapshot-only; source has a separately measured ~2-month lag, not the blanket five-day NASA lag |
| `climate-field-precipitation` | NASA POWER `PRECTOTCORR` | `scripts/build_precipitation_from_canonical_snapshot.py:46-58` | Snapshot-only |
| `soil-field-vpd` | Open-Meteo ERA5-Land `vapour_pressure_deficit_max` | `scripts/vpd_snapshot_breakdown.py:73-79` | Snapshot-only |
| `soil-field-moisture-0-7cm`, `-7-28cm`, `-28-100cm` | Open-Meteo ERA5-Land depth products | `scripts/build_soil_moisture_from_canonical_snapshot.py:87-109` | Snapshot-only; successful production load proves only this finite build |
| `soil-temperature-0-to-7cm`, `-7-to-28cm`, `-28-to-100cm`, `-100-to-255cm` | Open-Meteo ERA5-Land depth products | `scripts/soil_temperature_snapshot_breakdown.py:193-220` | Snapshot-only |

### Three additional immutable, unregistered breakdowns

`scripts/soil_wetness_snapshot_breakdown.py:177-181` produces these NASA POWER products under
`derived-canonical/`, not as live registered `layer=.../kind=observed` streams:

- `soil-wetness-surface` from `GWETTOP`;
- `soil-wetness-root-zone` from `GWETROOT`; and
- `soil-wetness-profile` from `GWETPROF`.

The exact total is therefore **15 registered snapshot product streams plus 3 extra immutable
breakdowns = 18 snapshot-only outputs without durable direct-source forward ownership**.
`climate-field-dew-point` is an additional gap: it is served through the generic signal read model,
but no dedicated physical product schema or builder exists.

### Unregistered served field surfaces

These are user/agent-facing logical names, not independent `LANE_REGISTRY` storage streams. Their
daily coverage is derived from PostgreSQL signal views and the generic `signal` Parquet stream:

- `climate-field-air-temperature`
- `climate-field-dew-point`
- `climate-field-precipitation`
- `climate-field-relative-humidity`
- `climate-field-shortwave-radiation`
- `climate-field-wind-speed`
- `climate-field-soil-wetness-surface`
- `climate-field-soil-wetness-root-zone`
- `climate-field-soil-wetness-profile`
- `soil-field-moisture`
- `soil-field-temperature`
- `soil-field-vpd`

The independent mappings are in `agent/tools.py:148-164`, `src/types/time-slider.ts:103-113`,
`src/lib/environmental/climate-field.ts:684-692`, and
`drizzle/0029_pre_aggregation_layer.sql:341-372`.

An incremental replacement for the snapshot products needs provider-owned source checkpoints,
per-product/day work items, immutable receipt reconciliation for new source parts, provider- and
product-specific publication lag, live-prefix promotion, and completion/absence markers at every
zoom tier. The frozen canonical snapshot must never become a silent forward fallback.

## PostgreSQL and non-Parquet boundaries

| Layer/product | Storage and writer | Boundary |
| --- | --- | --- |
| `interventions` | Human/partner-authored `geo.features` with moderation workflows | Explicitly PostgreSQL-only. `docs/lanes/interventions.md:368-432` says it does not migrate to Parquet. It is demand-driven and is not a scheduled ingestion lane |
| ISRIC SoilGrids point cache | `public.soil_grid_cache`, written by Node `scripts/warm-soilgrids.mjs` | Static finite warm, no `agri.job_*` row; resume is inferred by diffing spatial-cell centroids against cached points (`docs/runbooks/durable-backfill-lanes.md:541-560`). It is a source-specific migration input, not `soil-survey` |
| Six SoilGrids topsoil raster properties | R2 COG + PMTiles and append-only `geo.raster_release`; manual build → verify → publish scripts under `scripts/raster/` | Static non-Parquet release set: pH, organic carbon, nitrogen, bulk density, CEC, and OCD. No recurring upstream-version monitor |
| PostgreSQL source data generally | `geo.*`, `agri.*`, and supporting dimensions | Remains the transition bridge while Parquet ownership is proven. No row deletion or source-writer retirement is part of this migration |

## What can be consolidated now

The following already have executable Python entry points and can be registered in one dedicated
service once each lane's activation condition is satisfied:

- split the eight `ingest-all` source jobs into lane definitions with their real cadences and lags;
- generic Parquet projection as one durable outer definition per registered stream, each invoking a
  bounded `--layer <slug> --max-days-per-lane 1` turn; the exact selected day remains resumable in the
  underlying immutable-object/completion-marker contract rather than being claimed as an outer
  executor day checkpoint;
- existing durable `firms-archive` and `streamflow-archive` lanes;
- MTBS as a weekly source-specific handler;
- USDM current plus durable release-week history;
- calendar;
- vegetation forward/catch-up while preserving its publication barrier and persisted-source
  callback semantics; and
- direct fire and water adapters behind explicit, individually auditable activation gates.

The executor must persist a lane's next-due state and scheduled instant, take a singleton leadership
advisory lock, use fenced work-item leases for execution, bound retries/backoff, checkpoint completed
source and publication units, and alternate newest incremental work with historical backlog so one
deep lane cannot starve the rest.

## Source-specific blockers

- **SoilGrids Node warm:** the final one-service architecture needs either a Node-capable executor
  image/isolated subprocess adapter or a reviewed Python port, plus a lease. A cache diff alone is
  not durable job state.
- **SSURGO soil survey:** needs a new bounded survey-area/vintage worker; healthy SoilGrids telemetry
  provides no SSURGO evidence.
- **WFIGS history:** a capability declaration exists, but no runnable historical adapter does.
- **Watersheds:** this branch adds an independently activatable daily current-version fetch with no
  fabricated legacy owner. Production still has no scheduled WBD refresh until that replacement lane
  is deliberately activated and observed.
- **Open-Meteo current-weather history:** the moving 92-day source capability is unwired.
- **NASA POWER/Open-Meteo signal forward:** plan-based historical loaders exist, but no recurring
  direct-source owner exists.
- **Eighteen immutable climate/soil products:** require incremental source-part and per-day product
  writers before they can leave snapshot-only status.
- **SoilGrids raster publication:** remains an offline static release workflow unless explicit
  upstream-version monitoring is brought into scope.
- **`matview-refresh` dead letters and invalid streams:** the 139 standing dead letters and three
  invalid stream verdicts are real operational blockers, not a scheduler-idle state. They require
  deliberate requeue/cancel and data-contract remediation; the new executor must surface them
  without starving unrelated lanes.

## Activation sequence

1. **Deploy shadow-only.** Start the dedicated service with an empty active-lane allow-list and no
   handoff acknowledgements. Verify continuous process health, singleton leadership, the code-owned
   inventory, each executable lane's current cadence bucket, command, and handoff blockers. Shadow is
   schedule-only: it performs no ledger or layer writes and explicitly reports that source-watermark
   parity was not evaluated.
2. **Observe parity outside the activation gate.** Compare each shadow cadence prediction with the
   active legacy service, and separately record source watermark, selected incremental unit, historical
   frontier, last legacy run/deployment, and in-flight drain evidence. The executor does not infer or
   persist these facts in shadow mode; an acknowledgement must never be treated as proof that they were
   checked.
3. **Choose a genuinely unowned first writer.** `postgres-watersheds` has no current scheduled owner and
   is the only new source lane in this branch that can be activated without retiring a legacy service.
   `parquet-calendar` is not an independent first-writer candidate: the generic Parquet role still belongs
   to `plantgeo-ingest-cron`, so its replacement is part of that service's atomic handoff group.
4. **Handoff at the legacy-service boundary.** Independently deployed legacy services such as MTBS or
   direct fire can be retired and acknowledged separately after parity and in-flight drain checks. Every
   executable role owned by the monolithic `plantgeo-ingest-cron` must activate atomically because Railway
   cannot disable only one command inside that cron. The parser rejects a partial group. Never enable first
   and “let the lock sort it out.”
5. **Keep PostgreSQL writers.** Direct Parquet ownership does not authorize turning off source
   ingestion. PostgreSQL remains the transition and reconciliation bridge until a separately reviewed
   cutover proves it is no longer required.
6. **Fire last among healthy direct writers.** Its current service is healthy and already has a clean
   `2026-08-25` ownership boundary. Do not disturb it for an architectural demonstration.
7. **Water requires a complete two-owner handoff.** The repaired deployment must first prove a scheduled
   write. `parquet-water-gauges` cannot activate unless both `plantgeo-ingest-cron` and
   `plantgeo-water-gauges-forward` are acknowledged disabled with no run in flight. The source-specific
   direct-water migration input remains non-executable because no writer ceiling exists; a shared advisory
   lock is not ownership.
8. **Preserve vegetation's barrier.** Do not split its raw persistence and publication semantics
   across unsynchronised jobs.
9. **Incrementalize climate/soil before promotion.** Do not repoint a live reader to the frozen
   snapshot namespace and call it forward ownership.
10. **Retire only after evidence.** A legacy service may be disabled after parity and successful
    replacement runs, but should remain recoverable during the transition. No merge to `main` is part
    of this report or branch work.

## Repository evidence index

- Current consolidated production commands: `infra/cron-ingest/Dockerfile` and
  `infra/cron-ingest/railway.json`
- Eight forward source jobs: `services/agri-data-service/src/agri_data_service/ingest/runner.py:28-63`
- Manual backfillable source registry: `services/agri-data-service/src/agri_data_service/ingest/commands.py:330-353`
- Durable archive definitions: `services/agri-data-service/src/agri_data_service/ingest/lanes.py:177-226`
- Stateful job substrate and pulse: `services/agri-data-service/src/agri_data_service/jobs/` and
  `services/agri-data-service/src/agri_data_service/execution/jobs_pulse_command.py`
- Live Parquet registry: `services/agri-data-service/src/agri_data_service/pipeline/parquet/lane_registry.py:711-941`
- Generic gap-fill fairness and locking: `services/agri-data-service/src/agri_data_service/pipeline/parquet/gap_fill.py:1128-1369`
- Snapshot builder rationale: `services/agri-data-service/scripts/AGENTS.md`
- Serving-surface inventory: `services/agri-data-service/src/agri_data_service/agent/tools.py:132-164`
- Non-Parquet intervention boundary: `docs/lanes/interventions.md:368-432`
- SoilGrids/SSURGO distinction: `docs/lanes/soil-survey.md:208-235` and
  `docs/runbooks/durable-backfill-lanes.md:541-560`
