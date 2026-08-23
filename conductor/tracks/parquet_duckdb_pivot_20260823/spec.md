---
type: track-spec
slug: parquet_duckdb_pivot_20260823
status: active
---

# Parquet/DuckDB pivot — the twelve-lane warehouse and its serving path

Chartered **retroactively on 2026-08-23**. Waves 1–3 already shipped without a track, so the
largest workstream in this repo was tracked only in RUNBOOK prose and `tracks.md`. This spec
records where it stands and what wave 4 is.

The owner call underneath it (RUNBOOK §0.28): Postgres keeps **only** community features; every
plane becomes day-partitioned Parquet on Railway object storage read by DuckDB + Polars; Martin
serves PMTiles. Map breakage during the transition is accepted.

## Already shipped — do not rebuild

| commit | what |
|---|---|
| `abf777f` | Twelve lanes: validation, serving, forecasters |
| `b794e98` | Lane **natures**, watermark-driven static lanes, conformed calendar dimension |
| `269d299` | Per-kind schema lookup — unblocked the five forecasters |
| `ecb559a` | ruff format sweep (36 files, behaviour-neutral) |
| `ef789f7` | Three of four adversarial-review defects |

**Live in production, measured 2026-08-23:** 1,240 objects (882 part files + 358 governed-absence
markers) across all twelve lanes. The cron writes; see §0.29.1 for the 86-minute latency trap.

## Wave 4, in priority order

### 1. Sub-day version fix — LIFE-SAFETY, do this first

`_read_source_watermark` truncates the source instant to a UTC **date** and the resolver compares
`>=`. After day D's first snapshot, every later same-day change reads `current` until UTC midnight.

On **evacuation-zones** that is wrong in a way that matters: Oregon OEM levels (1 Be Ready /
2 Be Set / 3 Go Now) change repeatedly during an active fire, and the lane can serve a stale
evacuation level for up to a day. The reader would gladly serve the new state and the cron would
gladly run it — **the resolver is the thing refusing**.

**Decided approach (owner, 2026-08-23): widen `ObjectStoreBackend` to surface `LastModified`.**
S3's `list_objects_v2` `Contents` entries already carry it and `_listed_keys` discards it, so this
costs a Protocol signature, one backend method and test fakes — **zero extra API calls and zero
on-disk change**. The rejected alternative was a sidecar object at the day prefix, which would add
a third object kind to a layout `paths.py` constrains to exactly two.

**Hard constraint: the partition day stays the version stamp. Do not change the path layout.**
A static lane's export is a full re-export of the whole population, so overwriting day D with the
newer state is exactly what "this version" means.

### 2. Pay down what the latch fix created

- **Auto-retract a governed absence when the retry returns rows.** Owner decision 2026-08-23. This
  deliberately **relaxes** the existing guarantee that "retracting it is a manual admin action" —
  recorded as a relaxation, not an oversight. Without it a latched lane raises
  `GovernedAbsenceConflictError` every tick forever.
- **Drive the absence latch through `_static_lane_census`.** It is unit-covered at
  `test_lane_contract.py` but never driven through the census, which is the exact shape of the bug.
- **Re-publish visibility.** `usda-soil.ts` now freezes `updated_at` on unchanged content while
  leaving `status='published'` unconditional, and `scripts/apply-pre-aggregation.mjs` declares
  `max(updated_at)` the watermark for every `geo.features`-backed matview. A re-publish of unchanged
  ground can now be missed. Either move the timestamp on the status transition or confirm the
  refresh driver keys on something else.
- **Doc surfaces.** `pipeline/parquet/AGENTS.md` and `README.md` still call offline-mode an opt-in
  flag; `ef789f7` inverted that.

### 3. Fire-risk feature plane

The chartered `fire_risk_zone_forecast_20260823` track's long pole. Cell-day covariates from fire
detections, burn severity, NDVI, soil moisture and **seasonality** (fires are heavily seasonal;
use the calendar dimension's cyclical `day_of_year_sin/cos`). Predicts a per-cell scalar, never a
polygon — contouring is a rendering step (§0.28.5). **Write no Python model**: training is blocked
on the Mojo runtime call (`ml_mojo_conversion_20260823`).

### 4. Wire every Open-Meteo product

Forward and historical; quota is not a constraint at the $99 tier. Ensemble and flood modules are
built but **persist-blocked** from the 2026-08-06 expansion wave — unblocks
`upstream_dataset_expansion_20260806`. Forecast layers appear in the timeslider **only** where a
genuine forecast source exists.

## Standing constraints

- **Measure, never guess a floor.** A wrong `history_floor` invents thousands of phantom gap-days;
  every floor in `lane_registry.py` carries a cited `floor_basis`.
- **Never run PlantGeo locally** (owner rule 2026-08-16) — test against prod and live Martin.
- **One sweep at the end**, never test→fix→test loops. Real-DB gate: `AGRI_TEST_DATABASE_URL` on
  local `agri_sweep` port 5442, plus `PGBIN`. Without it ~110 tests silently skip.
- **Any forecast must beat a climatology baseline** or it is not a forecast (§0.28.4).
- **Never self-approve.** `b794e98` shipped unreviewed and had four real defects.

## Owner decision 2026-08-23 (sixth session) — static lookups leave the lane registry

**`soil-survey` and `watersheds` are dropped from `lane_registry.py`** and move to a separate
config area for provisioning static lookups. `evacuation-zones` and `calendar` STAY.

The line this draws: a lane belongs in the gap-fill registry when something must *react* to it on a
cron cadence. HUC12 boundaries and SSURGO delineations change on multi-year timescales and want
deliberate provisioning; OEM evacuation levels move within hours of a fire (which is what wave 4's
sub-day fix exists for), and the calendar dimension computes itself. Two clocks, two mechanisms.

**This dissolves the soil-survey blocker rather than working around it.** Measured against
production 2026-08-23: the watermark join returns rows (so §0.29.1's `source_empty` reading is
wrong), and a bounded count returns 200,001 against `MAX_SOIL_SURVEY_POLYGON_KEYS = 200_000`
(`lane_registry.py:96`), so `_soil_survey_polygon_keys` (`lane_registry.py:288-293`, called from the
export at `:615`) raises every tick. An hourly driver can only refuse; a provisioning step can
**shard the release deliberately**, which is what the cap's own error message asks for. The cap
becomes a sharding parameter instead of a refusal.

### What must not be lost in the move

- **The partition layout does not change.** `watersheds` already holds 10 manual parts at
  `day=2026-08-07`; those objects stay exactly where they are, and `planes/watersheds.py` /
  `planes/soil_survey.py` keep reading them by path. This is a change to who WRITES, not to what is
  written or where.
- **The gap census is also the safety net.** Once these two leave the registry, nothing notices if
  their objects go missing or go stale — `build_gap_census` will no longer cover them. The new
  config area must carry its own coverage check, or the move trades a noisy failure for a silent
  one.
- **`ObjectStore.write_partition` still refuses a zero-row write and still refuses to overwrite a
  governed absence.** Provisioning is not exempt from those guarantees.
