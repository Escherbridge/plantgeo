---
type: track-spec
slug: plantgeo_ml_service_20260918
status: active
supersedes: ml_mojo_conversion_20260823
---

# PlantGeo ML service — extract ML and Monte Carlo into a dedicated Parquet-fed service

Chartered 2026-09-18. Owner: *"start moving the ml work into a dedicated service. It will still ingest
from the parquet but it will get its own api serving lane and will be focused on training. We will also
be using Mojo and the ML libraries that they have to create performant ml models focused on strategy
selection, prediction, as well as spatial KNN where applicable ... pull the monte_carlo and ml areas in
the agri_data_service api out into this new plantgeo_ml_service lane."*

Supersedes [`ml_mojo_conversion_20260823`](../ml_mojo_conversion_20260823/spec.md), which gave the ML
freeze an address but never answered its own four questions (where Mojo runs, what crosses the
boundary, how equivalence is proven, what the rollback is). This track answers them.

**Out of scope, deliberately:** training runs against production data. The owner is finalising the
data sets in a separate session. This track ships code and plans; every model it defines is trained
by a later, owner-triggered run. Nothing here promotes a model.

## 1. Decisions (grilled 2026-09-18, two rounds, eight answers — settled, do not re-ask)

| # | decision | owner's answer |
|---|---|---|
| D1 | placement | monorepo sibling `services/plantgeo-ml-service/`, own Dockerfile + `railway.json`, new Railway service `plantgeo-ml` in the Aevani project |
| D2 | who writes `kind=forecast` partitions | **the ML service.** Monte Carlo forecasters move with the ML code; the service reads observed Parquet and writes forecast partitions to the same bucket with the six provenance columns. agri-data-service becomes observed-only |
| D3 | Mojo shape | **Python host, Mojo kernels.** Python owns API, Parquet I/O (polars/pyarrow/DuckDB), orchestration, tests. Numeric cores are Mojo modules imported from Python, each behind a golden-output parity harness |
| D4 | first vertical slice | **fire-risk occurrence AND spatial KNN (Analog Ensemble), both written out daily to Parquet** |
| D5 | Postgres | **zero.** No `DATABASE_URL`. Features, labels, artifacts (canonical JSON, never pickle), receipts and predictions all live in the bucket under an `ml/` prefix or a lane's `kind=forecast` stream. The 28-row expert label plane is exported to Parquet once |
| D6 | cutover | **hard cut in one push.** The moved and deleted modules leave agri-data-service in the same push that lands the service skeleton; git history is the archive |
| D7 | dev environment | **WSL2 + pixi locally**; Mojo also compiles in the Railway Docker build; Python tests on Windows run against a pure-Python reference of every kernel |
| D8 | bookkeeping | this track supersedes `ml_mojo_conversion_20260823`; `fire_risk_zone_forecast_20260823` re-points its dependency here; the service owns its own `RUNBOOK.md`; the main RUNBOOK carries one Outstanding-work row |

Toolchain facts the decisions rest on (`.omc/research/mojo-ml-service-20260918/FINDINGS.md`): Mojo 1.0
shipped 2026-08-11 under Apache-2.0; Windows needs WSL2; Python-importing-Mojo caps a function at six
arguments; there is no first-party Parquet reader; Mojmelo (KNN, GBDT, logistic) and NuMojo exist but
are small community projects that may lag the 1.0 API.

## 2. What exists today, measured 2026-09-18 at HEAD `8451ebcf`

- `method/ml/` — 9 modules, 4,046 lines: Analog Ensemble k-NN, split-conformal calibration, ridge
  wind model, covariates v2, expert label plane, recommendation models (species fit + strategy
  selection), seasonal candidates/evaluation/lineage graph. numpy + scikit-learn.
- `method/monte_carlo/` — 5 forecasters, 2,656 lines: fire-detections hurdle bootstrap, sensors,
  signal (19 series), NDVI seasonal anomaly, water-gauge AR(1). numpy PCG64, seeded.
- `execution/` ML lane — 16 modules, ~7,400 lines, every one reading or writing Postgres
  (`analog_ensemble_*`, `recommendation_*`, `seasonal_*`, `conformal_recalibration`,
  `covariate_wind_*`, `forecast_receipt_writer`, `strategy_selection`, `strategy_label_mapping`).
- **None of it runs.** `routes/recommendations.py` is not mounted in `app.py`; the five execution
  lane entry points are registered under no CLI verb; the only live verbs are `ml strategy-train` and
  `ml strategy-label-map-preflight`, which operate on local files. Seven of the ten `agri.*` tables
  the lane's SQL names are absent from `db/agri_baseline.sql`; only `expert_label`,
  `expert_label_release`, `expert_label_source` survive.
- Four of five Monte Carlo forecasters have **no production caller**; their only callers are tests
  (`tests/parquet/test_signal_serving.py` §"the 30-day forecast" and
  `tests/parquet/test_vegetation_serving.py` §"forecast-row provenance" import the `method` copies
  directly). Those test sections move with the forecasters. The NDVI one also runs through a
  duplicate copy at `execution/vegetation_ndvi_forecast.py`, which `execution/vegetation_ndvi_plane.py`
  and the other session's `vegetation_partition_promotion.py` import. **That copy stays.**
- `lane_registry.py` names a `forecast_module` for five lanes and `layer-lanes.md` §2-3 make
  "ships a forecaster" and "claims a horizon" the same fact. No `kind=forecast` partition has ever
  been written; the agent tool `forecast_summary_for_cell` refuses until one is.
- The fire-risk feature plane is built and scored in `analysis/fire_risk_index.py` (AUC 0.725
  in-sample, VPD alone 0.697, skill stratified — closed forest 0.586). See
  `fire_risk_zone_forecast_20260823/metadata.json`.

## 3. Functional requirements

- **FR-1 Service skeleton.** `services/plantgeo-ml-service/` with package `plantgeo_ml_service`,
  Sanic app on `/api/v1/ml`, `/health` and `/ready`, pydantic-settings config that refuses any
  database variable, uv-locked Python 3.12, the same quality-receipt Docker gate agri-data-service
  uses, `railway.json`, `AGENTS.md`, `RUNBOOK.md`.
- **FR-2 Lattice.** The service keeps the sibling's enforced lattice, same layer names, and its AST
  import test: `foundation → method → warehouse → pipeline → planes → interface`; `method/` may not
  import polars, pyarrow, duckdb, boto3, sanic, sqlalchemy or httpx. `method/monte_carlo` and
  `method/ml` remain siblings that never import each other. A new `method/kernels/` holds the Python
  reference implementations and the Mojo-backed dispatch and may import only `foundation`.
- **FR-3 Parquet contract parity.** The object-key grammar, partition kinds, zoom rungs,
  `forecast_schema_for()` (observed schema + `FORECAST_PROVENANCE_FIELDS`, sibling
  `warehouse/parquet/schema.py:81-109`), and the completion/absence marker names are reproduced
  exactly. Parity is proven by **checked-in golden fixtures** (`tests/fixtures/parity/*.json`): every
  test asserts the ML module reproduces the fixture, and when the sibling's source is present on disk
  (repo checkout, never the Docker image) it also regenerates the fixture from the sibling and fails
  on drift. `scripts/regenerate_parity_fixtures.py` is the one way fixtures change.
- **FR-4 Forecast partitions.** For each lane whose registry names a forecaster, the service writes
  `layer=<slug>/kind=forecast/...` partitions at **every rung of the lane's ladder** with
  `forecast_schema_for(observed)`, propagating `cell_id` (and every observed key column) verbatim
  from the observed rows the forecast was issued from, plus a completion marker per rung, through a
  writer whose receipts match agri-data-service's.
- **FR-4a Availability publication.** Writing a partition does not publish it. The ML service is an
  **availability-index publisher** for `kind=forecast`: after a day's rungs are complete it writes
  `availability/generation=<sha>/availability.parquet` under `layer=<slug>/kind=forecast/` with the
  exact `AVAILABILITY_REQUIRED_RUNGS` ladder and conditionally advances `_LATEST.json` (compare-and-set
  on the pointer's checksummed identity; on a lost race it re-reads and retries once, then refuses
  with a receipt). Acceptance reads back through the sibling's `parquet_ops/availability_coverage.py`,
  never a raw listing. The `vegetation` lane root is also written by the other session's
  `vegetation_partition_promotion.py` (observed side, `kind=observed`); the two kinds have separate
  roots, so there is no shared pointer, and the plan asserts that with a test on
  `availability_lane_root(layer, kind)`.
- **FR-5 Fire-risk daily lane.** A `fire-risk` lane at the fire-detections 0.005° cell grain:
  `(cell_longitude, cell_latitude, valid_day)` → `probability`, `risk_score`, `stratum`,
  `refused_reason`, plus `model_artifact_sha256` and the six provenance columns. Cyclical
  seasonality and photoperiod are mandatory features; out-of-stratum cells are refused, never
  scored; every feature respects each producer's publication lag. Written daily for horizons 1–14.
  **Publication gate:** no `fire-risk` partition is written to a real prefix until a walk-forward
  backtest receipt in `evidence/` reports per-stratum PR-AUC and Brier on held-out years against
  the VPD-only and climatology baselines with a declared minimum lift; strata that do not clear it
  publish `refused_reason`, not a score. The only skill measured today (AUC 0.725) is in-sample.
- **FR-5a Fire-risk lane registration.** `fire-risk` is a new layer slug and needs what every lane
  has: a `LaneRegistration` (nature `daily_series`, history floor with a cited basis, publication lag,
  cadence, rung ladder), `docs/lanes/fire-risk.md`, a `warehouse/schemas/fire_risk.py` stream schema in
  agri-data-service so its readers and slider catalogue know it, and the ML service's matching pinned
  schema. The registry edit is sequenced after the concurrent session's `p1d-registration` slice on
  `lane_registry.py` lands.
- **FR-6 Spatial KNN daily lane.** The Analog Ensemble forecaster reads covariate vectors from the
  `signal` lane's observed partitions (not Postgres), finds analogs with a temporal exclusion window,
  and writes `layer=signal/kind=forecast` partitions daily for horizons 1–30 at p10/p50/p90.
- **FR-7 Artifacts and receipts.** Model artifacts are canonical JSON at
  `ml/artifacts/<model_kind>/<sha256>.json`; training and prediction receipts at
  `ml/receipts/<model_kind>/<issued_on>/...json`; the expert label plane at
  `ml/labels/expert/<release>/part-0000.parquet`. Every API response names the artifact digest.
- **FR-8 API.** `GET /api/v1/ml/fire-risk?lon&lat&day`, `GET /api/v1/ml/analogs?cell_id&origin`,
  `GET /api/v1/ml/forecast-summary?layer&lon&lat&day`, `GET /api/v1/ml/artifacts/<kind>`. All reads
  are bounded, typed refusals when the partition is absent, no fallback to any other store.
- **FR-9 Mojo kernels.** Three kernels ship with Python references and parity harnesses:
  weighted-L2 neighbour search with exclusion mask (KNN), the seasonal bootstrap ensemble (Monte
  Carlo) which **consumes a draw stream produced by numpy's seeded PCG64** and is asserted
  bit-identical on its outputs (the seed stays authoritative; no RNG is ported), and the
  cyclical/photoperiod feature kernel. `PLANTGEO_ML_KERNELS=python|mojo` selects; `mojo` refuses to
  start if the extension is missing. Mojo is an optimisation: no later phase depends on it.
- **FR-12 Weather-forecast (provider NWP) lane — added 2026-09-19.** Owner, relayed by the
  concurrent session: *"let ml take over any projections, they don't need to be in the lanes."*
  The Open-Meteo NWP product is this service's; agri-data-service deleted its inert schema and
  ingest packages (lift from the tree at `c922509d`: commits `e66dbc36` schema, `c9c5256c` ingest,
  `a1b3a497` fixes; plan `.omc/ultrapilot-20260918/W8-E-PLAN.md`). Shape, stated as an assumption
  with low reversal cost because the path grammar is unchanged: a distinct layer slug
  `weather-forecast`, nature `release_series`, written by this service under `kind=observed` with
  `day=` the provider issue date and future-ness in a `valid_time` column (the drought pattern).
  Provider runs are deterministic, so `kind=forecast`'s ensemble provenance (`random_seed`,
  `ensemble_size`) would be invented; `kind=forecast` under this slug stays reserved for an
  ML-corrected product. Two probe-settled conventions (`.omc/research/forecast-s3-probe-20260919/`):
  multi-location responses are a JSON array in request order whose pairing must be verified by the
  snapped coordinates, and the hourly precipitation timestamp labels the START of its accumulation
  hour (the provider's own daily sums prove it; Open-Meteo's docs say otherwise and are wrong), so
  the deleted code's one-hour shift is not ported. Style findings S6 and S7 in
  `.omc/ultrapilot-20260918/STYLE-REVIEW-W8.md` are fixed on lift. The service gains an
  httpx-capable `pipeline/sources/` layer for this; `method/` stays HTTP-free. Registration of the
  slug in agri-data-service's lane registry (`forecast_module=None`) is an agri-side task sequenced
  with the fire-risk registration (FR-5a). Retire or re-point
  `conductor/tracks/weather_forecast_parquet_lane_20260911/` and the RUNBOOK weather-forecast row
  when the lane lands.
- **FR-10 Daily schedule.** A Railway cron service `plantgeo-ml-cron` runs `plantgeo-ml predict-daily`
  once per day; a bounded turn exits 0 (owner rule 2026-09-04). No pulse without an explicit owner go.
- **FR-11 agri-data-service after the cut.** `method/ml`, `method/monte_carlo`, the eighteen
  execution modules, the unmounted recommendations route, the `ml` CLI family, the 40 SQL files whose
  only loaders were those modules (list derived mechanically, `evidence/extraction-inventory` §B), and
  their tests are gone; the forecaster sections of the two Parquet serving tests move with the
  forecasters; the import contract drops its ml/monte_carlo rules and regenerates its pinned
  `CLI_ADAPTER_VIOLATIONS` line; `lane_registry.forecast_module` documents that the stem now names a
  `plantgeo_ml_service.method.monte_carlo` module; the quality receipt is refreshed by one green sweep.
  **What survives, deliberately:** `models/forecasting.py`, `models/strategy_selection.py`,
  `tests/test_metadata_contract.py`'s expectations on the `strategy_selection_*` tables, and the
  `scripts/readiness.py:153` probe of `agri.strategy_selection_receipt`; they belong to the
  `models/` cleanup follow-up, not to this cut.

## 4. Non-functional requirements

- Determinism: same artifact + same inputs + same seed → byte-identical partition. Tests pin it.
- No secrets in code; bucket credentials arrive as `OBJECT_STORE_*` like the sibling service.
- Memory: a daily run fits in 2 GB; DuckDB runs with a temp-directory ceiling of zero.
- The service never imports `agri_data_service`. Shared knowledge is copied with a parity test, not
  imported, so the two deploy independently.
- Docs follow the directory-level rule: terse one-line doc-comments, rationale in `AGENTS.md`.

## 5. Acceptance

1. Phase 1 push builds all four existing Railway services plus `plantgeo-ml` from the same commit;
   agri-data-service's sweep is green with a refreshed receipt; `plantgeo-ml` answers `/ready`.
2. Phase 2: `predict-daily --dry-run` against the production bucket writes signal forecast
   partitions (and fire-risk partitions once FR-5's backtest gate is met) to a scratch prefix
   **with an availability generation and pointer**, and agri-data-service's
   `parquet_ops/availability_coverage.py` reports non-empty `selectable_days` for them. Real
   writes wait for an owner go.
3. Phase 3: Mojo parity harnesses pass in WSL2 and the Mojo extension builds inside the Docker
   image (the image runs the receipt digest gate, not pytest); the Mojo KNN kernel is at least as
   fast as scikit-learn's brute-force `NearestNeighbors` on the 1,568-cell pilot.
4. Phase 4: `forecast_summary_for_cell` stops refusing; the slider shows forecast days for
   `signal`; `fire_risk_zone_forecast_20260823` moves from `planned` to `active` with no runtime
   question left open.

## 6. Risks and rollback

- **Mojo underdelivers** → `PLANTGEO_ML_KERNELS=python` is the rollback and is the default until
  phase 3 parity passes. The service is complete without Mojo.
- **Ecosystem lag** (Mojmelo/NuMojo behind 1.0) → kernels are hand-written on the stdlib SIMD types;
  no third-party Mojo package is a dependency.
- **Monorepo drift** → the parity test in FR-3 fails the ML sweep the day agri-data-service changes
  its path grammar.
- **Concurrent session** → the ultrapilot run has ML declared out of scope and never touches
  `method/ml/**`; this track never touches `execution/vegetation_*`, `pipeline/**`, or `src/**` until
  phase 4, and edits `conductor/RUNBOOK.md` only in its own row.
