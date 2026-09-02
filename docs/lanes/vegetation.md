---
type: lane-contract
---

# `vegetation` lane

Scope, stated up front: this lane is **Sentinel-2 L2A NDVI** (`agri.data_source.key =
"sentinel2-ndvi-l2a"`) on the `sentinel2-ndvi-0p25deg` grid, per the RUNBOOK's
[§0.24.2](../../conductor/RUNBOOK.md) classification. It is written for the agent that will build
`warehouse/schemas/vegetation.py`, `pipeline/lanes/vegetation.py`, `pipeline/validation/vegetation.py`,
`planes/vegetation.py`, and **bring `method/monte_carlo/vegetation_ndvi_forecast.py` into
conformance** per [`conductor/code_styleguides/layer-lanes.md`](../../conductor/code_styleguides/layer-lanes.md)
§1 and §3 — that file already exists and must not be duplicated a third time (it already has one
undeclared duplicate; see §5.2). No Parquet path, filename or column list is asserted here — that
contract is being written concurrently by another agent this session. Every claim below is cited to
the file/line that establishes it; `UNVERIFIED` marks what the repo does not establish and names what
would confirm it.

## 1. Source system

- **Publisher / imagery producer**: European Space Agency (ESA) / Copernicus — Sentinel-2 L2A
  surface reflectance
  (`services/agri-data-service/src/agri_data_service/execution/vegetation_ndvi_plane.py:313-314`:
  `"name": "Sentinel-2 L2A NDVI (Copernicus)"`, `"owner": "European Space Agency / Copernicus"`).
- **Distribution path (two-tier, not first-party ESA infrastructure)**: the service reads Sentinel-2
  L2A Cloud-Optimized GeoTIFFs through **Earth Search**, a STAC API operated by Element84 over AWS's
  public `sentinel-cogs` mirror bucket — `base_url = "https://earth-search.aws.element84.com/v1"`,
  `collection = "sentinel-2-l2a"` (`vegetation_ndvi_plane.py:319`,
  `src/agri_data_service/ingest/vegetation.py:76`, `SOURCE_VERSION = "sentinel2-l2a-earth-search-v1"`
  at `vegetation_ndvi_plane.py:41`). Earth Search/AWS is a mirror, not the licensor — the recorded
  license is the original Copernicus notice, not an Element84 or AWS term.
- **Auth**: none. Keyless by default (`src/agri_data_service/execution/AGENTS.md:26`;
  `docs/rebuilding-the-dataset.md:42`: "Sentinel-2 / NDVI (`ingest-ndvi`) | No | — | Public Earth
  Search STAC API | Nothing").
- **License**: "Copernicus Sentinel Data Legal Notice",
  `https://sentinels.copernicus.eu/documents/247904/690755/Sentinel_Data_Legal_Notice`
  (`vegetation_ndvi_plane.py:43-44`). Required citation string, already pinned in code: *"Contains
  modified Copernicus Sentinel-2 L2A data (ESA); NDVI aggregated to 0.25-degree cell-day means by the
  PlantGeo agri-data-service."* (`vegetation_ndvi_plane.py:45-48`).
- **Redistribution constraint**: the registration SQL marks this source **openly exposable to
  clients** — `insert_data_source.sql` inserts the literal `true` for
  `allowed_client_exposure` on `agri.data_source`, with the comment *"Sentinel-2 NDVI is openly
  licensed, so it may be exposed to clients"*
  (`src/agri_data_service/sql/execution/insert_data_source.sql:32-34`). **Do not confuse this with**
  the *different, unrelated* `allowed_client_exposure` column the RUNBOOK flags as a constant `False`
  (`conductor/RUNBOOK.md:3295`) — that finding is about `agri.signal_observation`'s materialised
  rollup (the `weather-observations`/`sensors` signal plane, RUNBOOK §0.22), a different table
  entirely; it does not apply to this lane, which was checked directly.
  `UNVERIFIED`: whether the Copernicus legal notice imposes conditions beyond attribution (e.g. a
  redistribution cap, a required disclaimer) — confirm by reading the linked notice directly before
  building a public redistribution surface on it.

## 2. Cadence

- **Nominal upstream revisit**: declared in the registered `data_source.refresh_policy` as
  `{"cadence": "sentinel2_revisit", "nominalRevisitDays": 5}` (`vegetation_ndvi_plane.py:323`).
- **Raw-ingest owner**: executor lane `postgres-vegetation` runs
  `agri-service data ingest-ndvi` hourly. The source still revisits the Pacific Northwest about
  every 2-5 days, so most turns are expected no-ops; the cadence is an executor registry value, not
  a Railway `cronSchedule`.
- **Declared gap-detection cadence** for the raw plane (`store="features"`, i.e. `geo.features`):
  `publication_cadence_days=5`. Its historical `cadence_basis` still names the deleted
  `infra/cron-ndvi/railway.json`; treat that string as source-cadence provenance, not deployment
  configuration (`src/agri_data_service/ingest/validation/models.py:118-127`).
- **Measured observed lag is worse than the nominal 5 days**, because cloud screening removes usable
  scenes: the governed corpus has a **median 7-day gap between observation days**, and **1,411 of
  1,568 cells have zero consecutive-calendar-day observation pairs** — this is *why* the shipped
  generic bootstrap forecaster cannot serve this lane at all (see §7)
  (`src/agri_data_service/execution/AGENTS.md`, "Vegetation NDVI Monte Carlo" section). A 2026-08-07
  audit separately measured **268 missing days across 165 gaps, worst single gap 7 days**, against
  the ~5-day revisit, record density 1,197 of 1,465 calendar days, and logged this explicitly as
  upstream cadence, not a defect — *"do not re-investigate"*
  (`docs/reports/backfill-passoff-2026-08-07.md:113-119`). That measurement predates this session;
  re-verify the exact counts before treating them as current if precision matters.
- **The promotion cadence is currently "whenever an operator runs a CLI command by hand," i.e.
  effectively none.** See §5.1 — this is the most important cadence fact for this lane and is
  covered there rather than here because it is a gap, not a design property.

## 3. Historical horizon

- **Earliest obtainable from the upstream collection**: `2015-06-27T10:25:31.456Z` UTC — *"Declared
  by the collection itself at earth-search.aws.element84.com/v1/collections/sentinel-2-l2a"*
  (`src/agri_data_service/ingest/vegetation.py:77-78`,
  `SENTINEL2_L2A_EARLIEST_OBSERVATION`). This is what the raw-ingest source declares as its
  `HistoryCapability(supported=True, earliest=SENTINEL2_L2A_EARLIEST_OBSERVATION)`
  (`ingest/vegetation.py:1042`), which is what makes `agri-service data ingest-backfill --source
  sentinel2-ndvi --since … --until …` walkable that far back.
- **Earliest actually held in the governed, forecastable plane** (`agri.forecast_observation`):
  **2022-08-05**, through **2026-08-04** — **184,409 rows across 1,568 series**, verified against
  production 2026-08-09
  (`src/agri_data_service/sql/routes/ops_unarmed_sources.sql:33-36`). This exactly matches the
  184,409-row / 116 MB figure in `conductor/RUNBOOK.md` §0.23.2's four-relation table, and is roughly
  four years — **the deepest history of any of the eleven lanes**
  (`conductor/RUNBOOK.md:3341`: *"4 years of history, the deepest record"*).
- **The ~7-year gap between "obtainable" (2015) and "held" (2022) is unexplained in the repo.**
  `UNVERIFIED` whether 2022-08-05 was a deliberate backfill boundary (e.g. a data-quality or
  cost decision) or simply where the manual registration passes described in §5.1 happened to start.
  Confirm with whoever ran the original backfill, or by checking whether `ingest-backfill
  --source sentinel2-ndvi --since 2015-06-27` against `geo.features` (the raw plane, which does have
  the deeper `HistoryCapability`) already holds pre-2022 rows that were simply never promoted.
- Raw-ingest history and governed-plane history are **two different questions with two different
  answers** — do not report one when asked about the other.

## 4. Grain

- **Entity**: one cell of the `sentinel2-ndvi-0p25deg` grid — **1,568 cells total**, keyed
  `sentinel2-ndvi-0p25deg:{latitude:.4f}:{longitude:.4f}`
  (`services/agri-data-service/tests/test_historical_open_meteo.py:77`; example key
  `sentinel2-ndvi-0p25deg:43.1250:-116.3750`,
  `services/agri-data-service/plans/author_agera5_plans.py:51`). Measured lattice bounds from the
  first/last registered keys: latitude **42.1250 to 48.8750**, longitude **-124.8750 to -111.1250**
  (`services/agri-data-service/tests/test_pnw_soil_moisture_plans.py:209-210`) — the Pacific
  Northwest, matching the registered purpose text *"Pacific Northwest 0.25-degree vegetation lattice"*
  (`vegetation_ndvi_plane.py:316`).
- **Time unit**: one publisher-named UTC day, `DAY_BUCKET_RULE = "iso_date_prefix"`
  (`vegetation_ndvi_plane.py:53`). The stored `observed_at` is the scene instant, which is what makes
  the layer datable to the slider (`src/agri_data_service/ingest/AGENTS.md:289`).
- **What one row's value means**: a cell-day NDVI mean, `transform_version =
  "sentinel2-ndvi-daily-cell-mean-v1"` (`vegetation_ndvi_plane.py:40`), computed from a **5x5
  sub-pixel lattice inside the cell** (`CELL_SUBSAMPLE_SIDE = 5`, target resolution 160 m, requiring
  at least `MIN_VALID_SUBSAMPLES = 8` of the 25 sub-samples to be usable —
  `src/agri_data_service/ingest/vegetation.py:95-98`), using only pixels whose Sentinel-2 scene
  classification is vegetation, not-vegetated, water or unclassified (`SCL` classes `{4, 5, 6, 7}`) —
  cloud, cirrus, snow, shadow, saturated and no-data pixels are excluded before the mean is taken
  (`ingest/vegetation.py:100-101`).
- **Units**: NDVI index, dimensionless, physically bounded `[-1, 1]`
  (`METRIC_UNIT = "ndvi_index"`, `vegetation_ndvi_plane.py:49-50`; `NDVI_LOWER_BOUND`/`NDVI_UPPER_BOUND`
  in the method module).
- **A known reflectance trap already fixed once, worth preserving**: the `sentinel-cogs` bucket ships
  *already-harmonised* reflectance, so the `-0.1` offset the STAC item's `raster:bands.offset`
  declares must **not** be re-applied a second time — `SENTINEL2_L2A_REFLECTANCE_OFFSET` is pinned to
  `0.0`, with a measured justification in-code: re-applying the declared offset drives 88-97% of
  vegetation pixels outside the physical NDVI range, while leaving it off puts them at a textbook
  0.48-0.91 (`ingest/vegetation.py:111-120`). Relevant to any wave-2 code that re-touches the
  reflectance-reading path.
- **Two different planes hold what looks like "the same" NDVI layer, at two different grains — do not
  conflate them.** Raw per-scene samples land in `geo.features` (`shape="grid_cell"`, one row per
  sample landing on a shared raster cell — `ingest/AGENTS.md:289`); the governed, forecastable
  cell-day means described above land in `agri.forecast_observation` and **never** in
  `agri.signal_observation` (`docs/layer-lane-standard.md` §3: *"`agri.forecast_observation` |
  Sentinel-2 NDVI"*). An audit or reader that checks only `agri.signal_observation` will falsely
  report this lane dead — this has already happened once and is now pinned by a regression test
  (`services/agri-data-service/tests/test_ops_routes.py:1231-1240`,
  `sql/routes/ops_unarmed_sources.sql:19-41`).

## 5. Known gaps and traps

### 5.1 Production promotion and publication are executor-owned

The old `vegetation-register`-only description is historical. Production now has two explicit
executor duties: `postgres-vegetation` acquires Sentinel-2 observations, and
`vegetation-catch-up` runs `agri-service data parquet-catch-up-vegetation`. The catch-up lane
revalidates the 45-day fingerprint window and drains the durable pending-day queue behind the shared
publication barrier. Its schedule, lease, retry and dead-letter state live in the stateful executor;
there is no Railway cron service to arm. The original registration command remains an operator tool
for the governed forecast plane, not the production Parquet scheduler.

### 5.2 Two near-duplicate copies of the Monte Carlo method module exist — only one is wired in

`services/agri-data-service/src/agri_data_service/execution/vegetation_ndvi_forecast.py` (369 lines)
and `services/agri-data-service/src/agri_data_service/method/monte_carlo/vegetation_ndvi_forecast.py`
(370 lines) were diffed directly this session: **functionally identical**, differing only by a
3-line docstring update and one extra explanatory comment. But the two are **not equally wired in**:

- `interface/cli/commands.py:232-410`, `execution/vegetation_ndvi_plane.py:15-32` (the code that actually runs
  `agri-service forecast vegetation-register`/`vegetation-simulate`), and
  `tests/test_vegetation_ndvi_forecast.py:12` all import from **`execution.vegetation_ndvi_forecast`**
  — the pre-lattice copy.
- `src/agri_data_service/method/monte_carlo/__init__.py:3-9` re-exports from
  **`method.monte_carlo.vegetation_ndvi_forecast`**, and
  `src/agri_data_service/__init__.py:4` re-exports that at the package root — but **nothing that
  actually runs imports through this path**.

The `method/monte_carlo` copy is a dead, unwired duplicate today, despite `conductor/RUNBOOK.md`
§0.24.8 stating flatly that it "already exists" as if it were the live module. Bringing this lane
into conformance means **repointing the live imports** (`interface/cli/commands.py`, `vegetation_ndvi_plane.py`, the
test) to `method.monte_carlo.vegetation_ndvi_forecast` and **deleting**
`execution/vegetation_ndvi_forecast.py` — not maintaining both, and not creating a third copy under
`pipeline/` or `planes/`.

### 5.3 Sparse, cloud-gated series — a structural property the method already works around

See §2's median-7-day-gap and 1,411/1,568-zero-consecutive-pairs figures. This is *why* the shipped
generic `daily_increment_bootstrap_v1` (`agri.forecast_daily_bootstrap`) cannot serve this stream at
all — it resamples consecutive-calendar-day first differences and needs at least two, which this
series mostly does not have — and why a dedicated seasonal-anomaly method exists instead
(`execution/AGENTS.md`, "Vegetation NDVI Monte Carlo" section). Any wave-2 rebuild must preserve this
design constraint, not assume a dense daily series.

### 5.4 Winter-tail drop

An observation whose own ±15-day seasonal window has fewer than `MIN_CLIMATOLOGY_SAMPLES = 4`
neighbours — typical of isolated PNW Nov-Feb clear scenes that survive the 20% cloud screen — is
dropped from the anomaly pool rather than referenced to a fabricated climatology level
(`method/monte_carlo/vegetation_ndvi_forecast.py:112-130`). Winter forecast eligibility/quality is
therefore structurally weaker than summer's, by design, not by bug.

### 5.5 Revision leakage is not excluded, and the holdout does not measure operational latency

Because the NDVI corpus was backfilled in a single run, warehouse-availability time carries no real
hindcast information, so the holdout evaluation controls leakage only by **publisher-named day** — its
metrics measure method skill, not operational latency. Sentinel-2 L2A reprocessing (baseline changes)
is not tracked in this corpus, so revision leakage cannot be excluded
(`execution/AGENTS.md`, ~"Vegetation NDVI Monte Carlo" section, the `purpose`/holdout paragraph).

### 5.6 The `method.monte_carlo` / `method.ml` boundary is unenforced, and this is the one lane it already touches

`test_layer_import_contract.py` does not yet stop `method.monte_carlo` importing `method.ml` or vice
versa; adding that rule is a stated wave-2 prerequisite
(`conductor/code_styleguides/layer-lanes.md` §5, `conductor/RUNBOOK.md` §0.24.8). This applies to all
eleven lanes, not just this one — but `vegetation` is the one lane with a `method/monte_carlo/` module
already on disk today, so it is the first place a missing-rule regression would actually be
reachable.

## 6. Validation approach

No source-reconciling validator exists for this lane yet (`pipeline/validation/` is an `AGENTS.md`-only
stub — RUNBOOK §0.24.8). What exists is **internal-consistency checking**, and it answers a different
question than the contract requires:

1. **What's already built proves the write path agrees with itself, not that Postgres agrees with
   Earth Search.** `register_governed_plane`'s `_corpus_digest` step fingerprints what is already *in*
   Postgres (`sql/execution/corpus_digest.sql`, `sql/execution/load_observations.sql:105-168`), and
   `release_holds_claimed_corpus`/`all_requested_cells_materialised`
   (`execution/vegetation_ndvi_plane.py:250-266`) check that one registration pass's own writes match
   its own digest. That is a real and useful guard against a partial write silently reading as
   complete — but it is not a reconciliation against the source system, which
   `conductor/code_styleguides/layer-lanes.md` §4 requires: *"reconciles what the lane wrote against
   what the source system holds — not against the lane's own intermediate state, which only proves
   the code agrees with itself."*
2. **A real source-side validator would re-query Earth Search's STAC API** (collection
   `sentinel-2-l2a`, `https://earth-search.aws.element84.com/v1`) for the item/scene count and cloud
   cover intersecting a given cell/day window, and compare that against what's stored — reusing the
   scene-selection rule already implemented for ingest (max 20% cloud cover,
   `MAX_SCENE_CLOUD_COVER_PERCENT` in `vegetation_ndvi_plane.py:331`; valid `SCL` classes `{4,5,6,7}`,
   `ingest/vegetation.py:100-101`) rather than inventing a second selection rule that could disagree
   with the first.
3. **The raw-plane gap detector already exists and is cadence-aware, but it validates the wrong
   plane for this contract.** `find_observation_gaps` with `publication_cadence_days=5` for
   `stream="vegetation"` (`store="features"`, `ingest/validation/models.py:118-127`) answers "is a
   day missing from `geo.features`" by listing what's there against the declared cadence — the
   "gap is discoverable by listing objects, not scanning them" principle
   (`layer-lanes.md` §4) is already honoured here for the raw plane. A Parquet-era validator for this
   lane needs the equivalent check against `kind=observed` partitions specifically, not an assumption
   that the raw-plane check already covers the governed plane — it does not (§4's two-plane
   distinction).
4. **No STAC-item-count-vs-stored-row reconciliation exists anywhere in this repo for this lane.**
   This is the single most concrete missing piece and the natural first validation exercise to build.
5. **Failures must name the day, the cell/lattice region, and the source response** (item count,
   cloud cover) — *"N rows mismatched" is not actionable* (`layer-lanes.md` §4).
6. **A day the source cannot serve (heavy PNW cloud cover, no clear scene) is a governed absence, not
   a gap to fill** — §5.3/§2's cadence numbers already describe what that looks like at scale; do not
   interpolate a missing day into existence.

## 7. Forecast recommendation

**`horizon: 30d`.** `conductor/RUNBOOK.md` §0.24.2 classifies `vegetation` as **"yes — 4 years of
history, the deepest record"**, and it is the one lane that already has a Monte Carlo module on disk
— which makes it, in effect, the **reference implementation** other lanes are told to copy from
(see `docs/lanes/weather-observations.md` §7, which names this method as its own starting template).
Getting this lane's conformance right is higher-stakes than a typical lane for exactly that reason.

**Existing method** (`ndvi_seasonal_anomaly_bootstrap_v1`): circular day-of-year climatology (±15-day
window, `MIN_CLIMATOLOGY_SAMPLES=4`) → per-observation anomaly (dropped when its own seasonal window
is unsupported, §5.4) → a daily persistence decay `phi` from the cell's own lag-1 anomaly
autocorrelation (gap ≤30 days, ≥8 pairs) → simulation via `climatology(t) + phi**(gap+h) *
anchor_anomaly + sqrt(1 - phi**(2*(gap+h))) * innovation`, innovation resampled with replacement from
the seasonally-matched anomaly pool, clipped to `[-1, 1]`, with `p10/p50/p90` taken as
`numpy.percentile` (linear) over the simulated paths at each horizon step
(`method/monte_carlo/vegetation_ndvi_forecast.py:325-370`, `execution/AGENTS.md` "Vegetation NDVI
Monte Carlo" section). The RNG is seeded explicitly and deterministically: `PCG64(int(checksum,
16))` at `method/monte_carlo/vegetation_ndvi_forecast.py:348`, where `checksum` is a sha256 digest
over ~25 canonicalised fields including `request.seed`
(`canonical_parameter_text`, same file, lines 253-294).

**Gap against the six required provenance columns** (`layer-lanes.md` §3), checked against the actual
live schema (`agri.forecast_iteration`/`agri.forecast_iteration_value`,
`services/agri-data-service/db/agri/tables/forecast_iteration.sql`,
`.../forecast_iteration_value.sql`):

| contract column | current state | gap |
|---|---|---|
| `forecast_run_id` | No column of this name. `iteration_key` (`varchar(255)`, unique, built deterministically from method+series+cutoff+horizon+simulation_count+seed — `iteration_key_for`, `execution/vegetation_ndvi_plane.py:885-890`) plus the row's `id` (uuid) jointly serve this role. | Naming/mapping gap. Also a **semantic** one: `iteration_key` is content-addressed — identical inputs always produce the identical key (idempotent by design, guarding the semi-manual promotion step in §5.1) — which is a different notion of "run" than a fresh id per invocation. Decide explicitly which semantics the Parquet `forecast_run_id` should carry before mapping this through. |
| `random_seed` | `simulation_seed` (bigint) exists and passes `SimulationRequest.seed` straight through (`_write_iteration`, `execution/vegetation_ndvi_plane.py:842`). | **Real gap, not cosmetic**: the value actually fed to `PCG64` is `int(parameter_checksum, 16)`, not `request.seed` directly — the seed is only one of ~25 inputs hashed into that checksum. Recording `simulation_seed` alone does not let someone reproduce the draws without also replaying the full canonical-text construction. Either document this explicitly (the checksum *is* the reproducibility key) or restructure so one recorded value suffices, per the contract's *"an unseeded Monte Carlo is irreproducible"* requirement (`layer-lanes.md` §3). |
| `ensemble_size` | `simulation_count` (int, CHECK 100-10,000) is the direct equivalent. | Naming only. |
| `horizon_days` | Exists **twice with different meanings**: `agri.forecast_iteration.horizon_days` is the iteration's total requested horizon (1-366, default 30); each row's specific offset is `agri.forecast_iteration_value.horizon_step` (1..N). | The contract's per-row `horizon_days` ("1-30; how far out this row was projected") maps to `horizon_step`, not to the iteration-level field of the same name — resolve the naming collision explicitly when reshaping into per-row form. |
| `issued_on` | No column of this name. `agri.forecast_iteration.cutoff_time` (the publisher-named day the training history was cut off at) is the direct equivalent. | Naming/mapping only. |
| `quantile` / `draw_index` | **The real structural gap.** One `forecast_iteration_value` row = one horizon step, carrying three fixed quantiles as separate columns — `low_value`/`median_value`/`high_value` (p10/p50/p90, `LOW_QUANTILE=0.1`/`MEDIAN_QUANTILE=0.5`/`HIGH_QUANTILE=0.9`). There is no per-row quantile or draw identifier, and the method only ever retains three fixed quantiles, never individual draws. | This is a genuine reshape, not a rename: decide whether the forecast partition stores three summary-quantile rows (`quantile` ∈ {0.1, 0.5, 0.9}) per cell-day-horizon, or every retained draw (`draw_index` 0..`simulation_count`-1) — the contract text permits either, but the current wide layout produces neither directly. |

**Net assessment**: bringing this lane into conformance is not "add six columns." It requires (a)
resolving the two-module duplication (§5.2) so there is one live implementation, (b) deciding how the
RNG seed is exposed so a run is reproducible from *recorded* values alone, not from replaying a
25-field canonicalisation, (c) reshaping the wide p10/p50/p90-as-columns storage into a row-per-
quantile-or-draw shape, and (d) mapping the existing iteration-level vocabulary (`iteration_key`,
`cutoff_time`, iteration-level `horizon_days`, `simulation_count`) onto the contract's names. None of
this requires touching the simulation mathematics itself, and the existing holdout-evaluation
machinery (`summarize_holdout`, `_error_metrics`,
`execution/vegetation_ndvi_plane.py:1029-1094`) can keep validating the method unchanged through the
reshape.

**Projected quantity**: NDVI index (dimensionless, physically bounded `[-1, 1]`) per
`sentinel2-ndvi-0p25deg` grid cell (up to 1,568 cells, PNW extent — §4), per calendar day, 1 to 30
days ahead of a publisher-named cutoff day, as p10/median/p90 today — or, after the reshape in the
table above, as whichever quantile/draw layout wave 2 settles on.
