---
type: Track Spec
title: Config-driven ingestion — one TOML per lane, thin strategies, one runner, cron runtime, LayerService serving
description: Cap legacy soil first (G0, before the paid Open-Meteo key); then replace per-lane forward modules and scattered lane facts with typed lane TOML, a strategy Protocol and a shared runner; unify forward and gap-fill; re-source climate meteorology to Open-Meteo ERA5 + IFS; move water gauges early to USGS daily values; cut over the remaining lanes by a reviewed swarm, then delete the old path.
tags: [chore, config_driven_ingestion_20260926, pending]
timestamp: 2026-09-26
resource: ./metadata.json
---

# Config-driven ingestion

Chartered 2026-09-26 after the ingestion grill (`.omc/research/ingestion-grill-20260926/`:
`ROUNDS.md`, `executor.md`, `climate.md`, `water-gauges.md`, `soil.md`, `other-lanes.md`).

- **Revised 2026-09-26** after adversarial review loop 1 (`critic-track-review-1.md`,
  CHANGES-REQUIRED) and the owner's round-2 answers. §3.3 records the answers and §14 disposes of
  every finding.
- **Revised again** after review loop 2 (`critic-track-review-2.md`, CHANGES-REQUIRED, 4 new HIGH).
  §3.4 records the loop-2 decisions and §15 disposes of every loop-2 finding.
- **Revised a third time** after review loop 3 (`critic-track-review-3.md`, CHANGES-REQUIRED, "G0 NOT
  READY"). §3.5 records the round-3 owner decisions and §16 disposes of P1–P9. The next review is a
  **targeted re-check** of P1–P9 and the G0 section only, not a full loop 4 (O-R3-2).
- The owner's diagnosis (executor:Q1): **one lane's configuration is duplicated across several
  layers of abstraction, and each lane's forward module owns its whole turn.**

Plan: [`plan.md`](./plan.md). Context: [`../../product.md`](../../product.md),
[`../../tech-stack.md`](../../tech-stack.md). This track is bound by:

- [`federation.md`](../../code_styleguides/federation.md): one region manifest, a source Protocol
  per layer, soft ~600-line module / ~60-line function guidance.
- [`layer-lanes.md`](../../code_styleguides/layer-lanes.md): §1 lattice placement, §1a nature,
  §1b source binding, §2 observed vs forecast, §4a availability.
- [`python.md`](../../code_styleguides/python.md).

Citations use `path::symbol` relative to `services/agri-data-service/src/agri_data_service/` unless
they say otherwise. Line numbers are omitted because they go stale.

---

## 1. Overview

A lane becomes **data plus a small amount of behaviour**:

- **Data** lives in one typed TOML per lane under `services/agri-data-service/lanes/` (D1): schedules, floors, lags, the named analysis
  lattice, source endpoints, the *name* of the API-key variable, budgets, partial-day policy,
  `enabled`, the lane's stream declarations, and serving metadata.
- **Behaviour** lives in a thin **strategy** per source (`pipeline/lanes/<layer>/<source>.py`,
  `layer-lanes.md` §1b). The contract it implements lives in `pipeline/runner/contract.py` (S14,
  review N1), below the lanes, never beside them.
- **Everything else is in one runner:** windows, the probe gate, budget, concurrency, per-unit
  retries, 429 pauses, the neighbour-day census, writing, receipts, tiers, availability publication
  and the `unwritten` report.
- **Forward and gap-fill are one code path** over two different day windows.
- **Derived products are transform lanes**, input-driven and persisted.
- **The executor becomes cron-driven**, dispatching lanes from a work queue with a split breaker.
- **Serving is a keyed `LayerService` registry** over the existing `parquet_ops` read path,
  injected by sanic-ext.

**First, alone: G0.** Before any framework work, and before the owner sets the paid
`OPEN_METEO_API_KEY`, one small push caps the legacy soil writer at 33 logical requests per run,
stops it re-fanning the same unsettled day every hour, and makes it report its own requests,
weighted calls and HTTP attempts (FR-24, §4.4, O8, O-R3-1).

Two source changes ride on the framework:

- **Climate re-source:** meteorology moves to Open-Meteo, with ERA5 as the settled data and ECMWF
  IFS as a provisional tail ending **yesterday UTC**, on the 0.25° analysis lattice. Solar stays on
  NASA POWER UTC, re-gridded onto the lattice, with an IFS tail. POWER soil wetness is retired.
- **Early water-gauges move:** the lane moves to USGS modern Water Data **daily values**. It serves a
  daily mean with a real history re-pull to 1990-09-30, ahead of the batch cut-over.

## 2. Background: what exists at HEAD `85c4b8f4`

The executor deployment `d56306f5` *is* this commit.

### 2.1 One lane's facts live in five places

| fact | where it lives today |
|---|---|
| command, cadence, phase, timeout, catch-up, writer floor/ceiling, conflicts (15 kwargs) | `execution/lane_specs.py::_spec` |
| publication lag, floor basis, nature, forecast module | `pipeline/parquet/lane_registry.py` (`_registration`) |
| active or not | env `PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` via `execution/lane_specs.py::parse_activation`, read once at start; a typo exits the process |
| 19 lag/floor/skip constants | per-lane modules, e.g. `pipeline/direct/climate/products.py::CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS` |
| grid, attribution, source contract | web copies, e.g. `src/lib/environmental/climate-field.ts::CLIMATE_FIELD_GRID_NAME`. `src/lib/server/services/parquet-trpc-readers/climate-field.ts` **rejects** any `source_key` other than `nasa-power-daily` and any `precedence_contract` other than `nasa-power-point-per-support-cell-v1`. |

- **The crontab string is decoration.** Due-ness is
  `execution/lane_scheduling.py::scheduled_bucket` over `cadence_seconds` + `phase_offset_seconds`
  (executor.md F11).
- **Lattices.** The legacy climate support is **397 cells on a 1° step** (`na-sample:1deg:*`,
  `pipeline/direct/climate/support.py::NASA_POWER_SUPPORT_STEP_DEGREES`) over −125..−104, 31..51 N.
  The soil support is the **1,568-cell 0.25° half-step lattice**, read from Postgres
  `agri.spatial_cell` grid `sentinel2-ndvi-0p25deg` by
  `pipeline/direct/soil/support.py::load_era5_land_support`.
- **The region manifests do not declare the soil lattice.** `foundation/region/pnw.json`'s only
  lattice is `lattice_pitch_degrees: 0.01`, `floor_to_cell_origin`. A second manifest,
  `foundation/region/kenya_highlands.json` (+ `src/lib/region/kenya_highlands.ts`), also exists.

### 2.2 Each forward module owns its whole turn

`pipeline/direct/climate/forward.py` is 1,031 lines; the weather-observations forward is 1,342 and
the soil forward 917. Each re-implements windows, budget, census, retries, 429 handling and
reporting, and each got a different piece wrong:

| defect (evidence) | root cause the runner removes |
|---|---|
| **Shortwave livelock**, ~19k wasted POWER requests/day (climate F1/F2/F4) | A newest-first walk with `CLIMATE_UNSETTLED_FRONTIER_SKIPS = 1` and a two-fan-out budget. Lag 6 was measured on POWER **LST**, while the lane reads **UTC**, which is real only through 2026-06-30. |
| **Legacy soil re-fans the same unsettled frontier every run** (review N3) | `pipeline/direct/soil/forward.py::SoilForwardConfig.request_budget` = 32 chunks × (`max_days` 1 + `SOIL_UNSETTLED_FRONTIER_SKIPS` 4) = 160 requests ≤ 7,840 weighted calls per run, hourly; an all-null frontier chunk is never checkpointed (`soil/source.py::_checkpoint_eligible`), so each run re-asks it. Repair runs use `--max-days 5` (`execution/gap_repair_contract.py::REPAIR_BINDINGS` ← `SOIL_MAX_DAYS`): 288 requests. **G0 fixes this in legacy code** (FR-24); the runner's probe gate (S19) keeps it fixed. |
| **Gap repair keeps only the last candidate per pass** (executor F1) | `execution/gap_repair.py::author_gap_repairs` → `ensure_lane_definition` → `_load_or_register_definition` → `session.rollback()`. |
| **Water gauges: one tile's 503 discards all 8 tiles**; a 43 h hold (water-gauges F2–F4) | A bare `asyncio.gather` in `ingest/usgs_nwis.py::fetch_streamflow_gauges`; `ingest/http.py::fetch_bounded_json_sized` never retries a non-2xx; the DV archive source is unwired. |
| **The incomplete-turn alarm is blind** for all but three lanes (executor F3, soil F1) | `execution/job_executor_service.py::_unwritten_entries` reads a key only sensors, weather-observations and water-gauges emit. |
| **Quota refusals read as unpublished days** (soil F2) | Both surface as `source_unsettled`. |
| **The freshness horizon is calendar arithmetic** (other-lanes F4) | `pipeline/parquet/lane_ceiling.py::allowed_source_ceiling` = today − lag. |
| **The breaker cannot tell upstream from code**; ~2,880 identical lines/day; the printed command fails as printed; the admin error column is null (executor F2–F6) | Every non-zero exit is `scheduled_command_exit`. `lane_scheduling.py::supersession_command` omits required flags. Nothing writes `job_run.last_error_summary`. |
| **Head-of-line blocking** (executor Q2, F8) | `run_executor_tick` runs lanes serially on one pinned `AsyncSession` that holds the leader advisory lock. |

## 3. Decisions

### 3.1 Settled by the owner 2026-09-26, round 1 (implement; do not re-open)

| # | decision |
|---|---|
| D1 | **One TOML per lane** under `services/agri-data-service/lanes/`, type-validated at startup and by a contract test. `enabled` is in the file and replaces the env allow-list. An env kill-switch stays. Config holds DATA; strategies hold BEHAVIOUR. |
| D2 | **Thin strategy, fat runner.** The strategy provides `plan_requests(days)`, `fetch(request)`, `settle(day, responses, context) -> written \| absent \| unsettled` and `rows(day, responses)`. The runner supplies `context` (the neighbour-day census) and owns windows, budget, concurrency, per-unit retries, the 429 series, the `unwritten` report, writing, receipts and tiers. |
| D3 | **Forward and gap-fill are ONE code path** over different day windows. Sources declare a history capability or endpoint. |
| D4 | **Transforms are derived fact lanes** (`kind = "transform"`, `inputs = [...]`) with persisted output, rebuilt per day when an input publishes or revises that day. They may chain. |
| D5 | **Serving uses the .NET-style service pattern in one deployment:** a `LayerService` Protocol, a config-driven default plus specialised implementations, and a keyed `LayerServiceRegistry` injected with `app.ext.add_dependency`. The UI reads layer metadata from the API. |
| D6 | **Runtime:** parsed cron strings only (a forward and a gap-fill cron); bounded parallelism with per-lane locks; a split breaker; a printed command that is runnable as printed; child failure detail in the ledger. |
| D7 | `partial_day = refuse \| write_and_recheck` per strategy. |
| D8 | **Climate re-source** (§6). |
| D9 | **Water gauges** go to the USGS modern Water Data API, with 503 retries and per-tile independence. |
| D10 | **Migration shape:** framework → two adversarial review/fix loops → a batch cut-over by a parallel swarm, each lane with its own adversarial review → per-lane production QA + validation → deprecation. |

### 3.2 Decided by this spec (reversal costs in §9)

| # | decision | why |
|---|---|---|
| S1 | **Wind is served as 10 m wind speed**; the layer is relabelled; there is no FAO-56 at ingest. | ERA5 and IFS are natively 10 m (`wind_speed_2m` returns nothing). FAO-56 eq. 47 (`u2 ≈ 0.748·u10`) assumes reference grass, and a height conversion is a model, not a unit (`federation.md` §2). The five-layer history is re-pulled anyway. A `wind-speed-2m-fao56` transform can be added later as config plus one pure function. |
| S2 | **Existing lane ids are kept** for migrated lanes. New lanes get new ids, and so does water gauges (§7), because it runs **in parallel** with its legacy lane. | This gives ledger continuity and a one-field rollback. |
| S3 | **Provider facts live in `lanes/_providers/<provider>.toml`**. The authoritative file list is in `metadata.json`. | A shared quota cannot live in one lane's file. |
| S4 | **Runner exit codes:** `0` completed (unwritten days reported), `75` upstream unavailable, `70` internal error, `78` configuration error. | This is the breaker split as a signal. |
| S5 | **The `unwritten` reason enum:** `unsettled`, `deferred_quota`, `deferred_budget`, `upstream_unavailable`, `refused_partial`, `retention_exceeded`, `strategy_error`. The alarm counts only days **behind the provider edge**. | Separates quota from publication and makes every lane legible. |
| S6 | **Optional `probe_edge(client)`**; `today − lag` is the fallback; the edge source is recorded. A **settled** (`partial_day = refuse`) lane on a weighted provider whose forward cron fires more than once a day **must** implement it (FR-1). `write_and_recheck` lanes (IFS provisional, weather-observations) are exempt. | Fixes the lag tautology; S19 gates fan-out on it. |
| S7 | **One generic precedence transform** (`pipeline/lanes/transforms/precedence.py`, resolved by S14 like any strategy; no lane imports it). | Meteorology and solar share it. |
| S8 | **An invalid lane TOML quarantines its lane, never the process.** The kill-switch and the legacy allow-list warn on unknown ids and never exit. | executor F8b. |
| S9 | **An in-house 5-field UTC cron parser** with property tests. | Small; no dependency. |
| S10 | **An explicit ERA5 node per lattice cell.** Each cell requests the ERA5 0.25° grid point at its NE corner: centroid + (0.125°, 0.125°), computed in integer quarter-degree units from the cell index, sent with `cell_selection=nearest`. The mapping is a bijection at zero extra calls. IFS keeps D8's cell-centre sampling. | Half-step centroids are equidistant from four ERA5 nodes (review M2). Soil's `support_chunks` / `nearest_native_grid_point` guard false-collides at 0.25° and must not be reused. |
| S11 | **Rewrite rules.** Settled streams rewrite a written day when its **source receipt digest** changes (ERA5T revisions, USGS approval changes). Transforms rebuild when an **input digest** changes. "Strictly more units" applies only to partial `write_and_recheck` days. | Review H2. |
| S12 | **Gap-fill crons ship disabled.** Enabling one is an explicit TOML flip inside a named owner gate. Provisional pruning ships off and is enabled in the G6 commit. | Review H3: a gap-fill is a backfill. |
| S13 | **Lane TOMLs reach the image and the build gate.** `lanes/` is added to both `COPY` stages of `services/agri-data-service/Dockerfile` and to `scripts/quality_receipt.py::DIGEST_DIRECTORIES`. A static test pins both, `tests/scripts/test_quality_receipt.py` pins the new tuple, and `.dockerignore` points at `DIGEST_DIRECTORIES` instead of enumerating the set. Every TOML change, rollback flips included, therefore needs a monitor sweep and a receipt refresh before the image builds. | Review C1 (+ loop-2 residue). This keeps D1's path. |
| S14 | **Strategies resolve by import-path convention; the contract lives below the lanes.** Strategy key `<layer>.<source>` → module `agri_data_service.pipeline.lanes.<layer>.<source>`, attribute `STRATEGY`, resolved by `pipeline/runner/resolve.py` with `importlib`. The Protocols and value types (`IngestStrategy`, `TransformStrategy`, `SourceRequest`, `SourceResponse`, `Settlement`, `DayContext`, `ProviderEdge`, `Derivation`) live in `pipeline/runner/contract.py`. **Nothing shared lives in `pipeline/lanes/`**: `tests/test_layer_import_contract.py::_lane_names` treats every module and subpackage there as a lane, so `test_lanes_do_not_import_each_other` would fail the moment a strategy imported a shared module beside it. `pipeline/runner/` is not a sibling-policed directory and sits in the `pipeline` layer, which is forbidden only `planes` and `interface`. There is no central registry file to edit. | Review H6, N1: "shared needs move DOWN the lattice" (the test's own docstring); an exemption would weaken the test's default-deny rule. |
| S15 | **The executor becomes a work queue with a session per lane.** The leader tick holds the advisory lock on its own session and dispatches due lanes to at most `PLANTGEO_JOB_EXECUTOR_MAX_CONCURRENT_LANES` slots **without awaiting them**. Each lane task opens its own `AsyncSession`, takes a per-lane advisory lock and drives `jobs/worker.py::run_job_slice`. The default is **1 at G1**; raising it to 2 is part of G6 and needs the Phase-2 RSS evidence. `max_lanes_per_tick` is retired. | Review H5. |
| S16 | **Every shared runtime change has a switch.** `PLANTGEO_JOB_EXECUTOR_DISPATCH=queue\|serial` (serial restores today's in-tick await), `PLANTGEO_JOB_EXECUTOR_BREAKER_MODE=split\|legacy`, and the concurrency setting. `_unwritten_entries` reads the legacy keys **and** the S5 schema. The pure fixes (FR-10, `last_error_summary`) roll back by revert. | Review M6. |
| S17 | **Quiescent pushes, one shared checkout, no worktrees.** A commit that touches `services/agri-data-service/**` is made only when that tree's uncommitted changes are exactly the commit's files (`git status --porcelain -- services/agri-data-service` checked by the coordinator), because `scripts/check.py --write-receipt` digests the whole tree and refuses unstaged bytes. Every gated push is therefore made at a quiescent point. **No worktrees (O-R3-3):** the one slice that could have overlapped a gated phase, `p4-extract`, runs **sequentially after G4's push** in the shared checkout. During a multi-slice authoring window (Phase 1, the Phase-4 swarm), a lane rollback uses the ledger brake only; its TOML revert is committed at the next quiescent point. | Review N4; memory `plantgeo-shared-checkout-receipt-coordination`; owner O-R3-3. |
| S18 | **New streams get a checked registration mirror, not import-time TOML synthesis.** `f1-registry-bridge` creates `pipeline/parquet/config_stream_registrations.py`: data rows (`ConfigStreamRow`: slug, lane id, nature, `history_floor`, `complete_history_floor`, `publication_lag_days`, `floor_basis`) that `lane_registry.py` turns into `LaneRegistration`s (a refusal adapter naming the owning strategy module — the runner writes, the generic exporter refuses) and splices into `LANE_REGISTRATIONS`, `LANE_REGISTRY` and `CALENDAR_HISTORY_FLOOR`. A parity test proves every row equals its lane TOML's `[[streams]]` entry, so the TOML stays the one authored fact. Owners: `w3-water-gauges` appends `water-gauges-daily`; `p4-contract-freeze` appends the climate streams from the FROZEN contract. | Review N2. Synthesis at import would make `LANE_REGISTRY` read TOML at import — it is read at import by `execution/lane_specs.py` (`_registration(...)` in the lane table), `parquet_ops/authorized_serving.py::_LANES` and `CALENDAR_HISTORY_FLOOR` — which breaks the lazy-loading tripwire (memory `plantgeo-manifest-moves-must-be-lazy`). |
| S19 | **Probe-gated forward fan-out; caps per turn, per mode.** A forward fire of a lane with `probe_edge` spends the probe first (≤ 2 locations over ≤ 14 days: ≤ 2 weighted calls) and fans out the lattice window only when the probe shows a valued day the census still owes (a missing day, or a value on an absence-recheck day). Otherwise the fire spends only the probe and reports owed days newer than the edge as `unsettled`, so **a day the probe shows unsettled is never fanned out**. A probe that cannot answer gates the window and the turn completes (exit 0; O-R3-1). A valued day whose fan-out comes back partial is `Unsettled(refused_partial)` in the runner and is re-asked on later fires within the cap; **legacy soil (G0) differs**: a thin fan-out raises and the run exits 1 (FR-24). Every lane TOML carries `[budget] forward_max_weighted_calls` and `gap_fill_max_weighted_calls`; a turn never exceeds its mode's cap. | Reviews N3, P1, P2. |

### 3.3 Owner answers, round 2 (SETTLED 2026-09-26)

| # | answer | where it lands |
|---|---|---|
| O1 | **The provisional IFS tail ends yesterday UTC and never writes today.** This keeps agri observed-only (`conductor/RUNBOOK.md`: agri lanes stay observed-only; `layer-lanes.md` §2). | §6.1, FR-17 |
| O2 | **Water gauges flip EARLY**, ahead of the batch, to the USGS modern Water Data API. It serves a **daily mean** with a real daily-values history re-pull, so forward and gap-fill are the same quantity. It has its own gate. | §7a, FR-18, plan Phase 3 |
| O3 | **All five meteorology layers are pulled back to 1984**, at the same weighted cost as dew point alone. | §6.3 |
| O4 | **Code-error holds alert through an `agri.job_incident` row only**, visible in `/admin/jobs`. No webhook, no email. | §4.4, FR-8 |
| O5 | **The extent shrink to the 0.25° lattice (42.125–48.875 N) is ACCEPTED.** | §6.2 |
| O6 | **`OPEN_METEO_API_KEY` is set by the owner.** It is empty on the executor today. *Timing superseded by O8: the key is set only after G0's push and 24-h observation.* | §6.3, O8 |
| O7 | **Dropped:** the solar re-grid question (it is now mandatory); land-context reconcile (decided at activation); `mtbs-forward` (**deleted** under the 2026-08-03 clean-up-as-you-go rule); IFS cadence (a one-field change; default one turn a day). | §7, FR-17 |

### 3.4 Owner decisions after review loop 2 (SETTLED 2026-09-26)

| # | decision | where it lands |
|---|---|---|
| O8 | **G0 = the legacy soil cap, its own gated push, BEFORE G1 and BEFORE the key.** It has its own adversarial review and its own owner deploy go; only after its 24-h observation does the owner set `OPEN_METEO_API_KEY` (still EMPTY on the executor as of 2026-09-26, so no paid spend yet). There is no "early soil-cap push under G1's go". | §4.4, FR-24, §6.3, plan Phase 0 / G0 |
| O9 | **A full third adversarial loop reviews the loop-2 revision.** Done: loop 3 returned CHANGES-REQUIRED (§16). | §15, §16 |
| O10 | **Budget Open-Meteo conservatively at ≥ 1.0 call per location per request.** The pricing page states fractional counting only ABOVE 10 variables or 2 weeks per location; whether a smaller request costs less is unstated. P5 measures real usage and re-baselines. | §6.3, A2, A16 |

### 3.5 Owner decisions after review loop 3 (SETTLED 2026-09-26)

| # | decision | where it lands |
|---|---|---|
| O-R3-1 | **A probe that cannot answer never fails the run.** A transport failure after retries, a refused or non-array body, a `canonical_location_document` `ValueError`, or the time budget running out → `probe_status = "unavailable"`: gate the probe window, keep walking older owed days, exit 0. A 429 → `probe_status = "deferred"`: same gating, exit 0. Both are tested. | S19, §4.4 G0, FR-24, plan G0 |
| O-R3-2 | **The next review is a TARGETED re-check, not a full loop 4.** A fresh critic verifies only the P1–P9 fixes and the G0 section; the §16 table stays one row per id. | §16, plan Phase 0 |
| O-R3-3 | **A17 is REJECTED: no worktree for `p4-extract`.** It runs sequentially after G4 in the shared checkout; the quiescent-push rule stays. | S17, §4.7, §9 A17, §15 N4, plan Phase 4, metadata `p4-extract` |

## 4. Architecture

### 4.1 Lane TOML (data)

- **Schema.** A frozen Pydantic model in `foundation/lane_config/` (imports nothing internal).
- **Loader.** A function that takes the directory and the `Region` and returns per-lane results (S8).
- **Location.** `services/agri-data-service/lanes/*.toml` and `lanes/_providers/*.toml`, shipped
  by the Dockerfile and digested by the receipt (S13). The directory is resolved from one setting
  whose default matches both the repo layout and the image layout.
- **Invariants checked by the loader:**
  - ids are unique;
  - `inputs` exist and form a DAG;
  - `conflicts_with` is symmetric;
  - every source's `coverage` contains the envelope;
  - `absence_recheck_days > publication_lag_days`;
  - `grid` names a **named analysis lattice in the active region manifest** (C2), never a literal;
    a lane whose lattice the active region lacks is quarantined (S8);
  - gap-fill and pruning are off unless explicitly enabled (S12);
  - a settled (`refuse`) lane on a weighted provider whose forward cron fires more than once a day
    resolves a strategy with `probe_edge` (S6, S19);
  - every `[[streams]]` slug has exactly one registration (S18).

**The named analysis lattice (review C2, owned by `f1-config`).**

- `foundation/region/manifest.py::Region` gains `analysis_lattices`: an **optional** mapping (empty
  by default) of lattice key → `{pitch_degrees, origin_rule, envelope, cell_key_prefix}`. Optional so
  `foundation/region/kenya_highlands.json` and `src/lib/region/kenya_highlands.ts` stay valid unedited.
- `foundation/region/pnw.json` and `src/lib/region/pnw.ts` declare `analysis-0p25` with:
  - pitch 0.25;
  - origin rule `half_step`, so centroids sit at +0.125;
  - envelope = the default camera envelope −125..−111, 42..49;
  - `cell_key_prefix = "sentinel2-ndvi-0p25deg:"`, so cell keys stay identical to soil's.
- A test proves the manifest lattice reproduces soil's 1,568 cell keys (from the soil plan-cell
  fixture). The key is region-free (`federation.md` §1).

Illustrative lane (field names re-frozen from landed code at the end of Phase 2; values are
today's soil facts):

```toml
id = "soil-era5-land-direct-forward"          # S2: existing id kept
kind = "ingest"
nature = "daily_series"                        # layer-lanes.md §1a
enabled = true
executor = "legacy"                            # legacy | config — the cut-over switch
strategy = "soil.open_meteo_era5_land"         # S14 -> pipeline/lanes/soil/open_meteo_era5_land.py::STRATEGY
grid = "analysis-0p25"                         # a manifest analysis lattice (C2)

[source]
provider = "open-meteo"
endpoint = "archive"
model = "era5_land"
coverage = "global"
history = { capability = "archive", earliest = "1950-01-01" }
lifecycle = "active"

[schedule]
forward_cron = "50 * * * *"
gap_fill_cron = "20 3 * * *"
gap_fill_enabled = false                       # S12: an explicit flip inside a named gate
catch_up = "coalesce_latest"

[days]
floor = "…"
publication_lag_days = 5                       # fallback when probe_edge is absent
absence_recheck_days = 14
partial_day = "refuse"
expected_value_units = 1470

[budget]
forward_max_weighted_calls = 1600              # S19: one 14-day fan-out (1,568) + the probe
gap_fill_max_weighted_calls = 1600             # §6.3 soil gap-fill line
max_concurrency = 2
turn_timeout_seconds = 900

[[streams]]                                    # S18: one row per stream the lane writes
slug = "soil-field-vpd"
history_floor = "…"
floor_basis = "…"
```

### 4.2 Strategy Protocol (behaviour)

Phase 1 drafts the Protocol in `pipeline/runner/contract.py`; the **re-freeze at the end of Phase 2**
(M1) fixes it from landed code. The swarm implements the re-frozen text:

```python
class IngestStrategy(Protocol):
    """One source's irreducible behaviour for one lane; the runner owns everything else."""

    def plan_requests(self, days: Sequence[date], lane: LaneConfig, region: Region) -> Sequence[SourceRequest]: ...
    async def fetch(self, request: SourceRequest, client: ProviderClient) -> SourceResponse: ...
    def settle(self, day: date, responses: Sequence[SourceResponse], context: DayContext) -> Settlement: ...
    def rows(self, day: date, responses: Sequence[SourceResponse]) -> pa.Table: ...
    # optional (S6); required for settled weighted-provider lanes firing > 1/day:
    # async def probe_edge(self, client: ProviderClient) -> ProviderEdge: ...


class TransformStrategy(Protocol):
    def derive(self, day: date, inputs: Mapping[str, pa.Table | None], context: DayContext) -> Derivation: ...
```

- **`Settlement`** is `Written(expected_units, present_units)`, `Absent(reason, proof)` or
  `Unsettled(reason)`.
  - `Absent` needs a proof. Either `context.later_published_day(stream)` (the mirrored-past rule,
    `pipeline/direct/soil/forward.py::_mirrored_past_day`) or a declared retention bound.
  - Quota refusals and 5xx never reach `settle`; `fetch` raises typed errors.
- **`ProviderEdge`** carries the probed edge day, the days in the probe window that carry values,
  the probe status (`ok`, `invalid`, `blind`, `unavailable`, `deferred`, as in G0) and the edge
  source (`probe` | `lag_fallback`).
- **`DayContext`** is built by the runner from the tier census it already pays for. It carries:
  - neighbouring published days;
  - governed absences;
  - short-day unit counts;
  - the probed edge;
  - immutable-through;
  - retention;
  - the day's current receipt digest (S11).
- **A request unit** is the retry grain: a tile, a cell chunk, a release file. One failed unit never
  discards its siblings.
- **Module convention (S14).** A strategy module exports `STRATEGY`. Strategies import only from
  `foundation`, `warehouse` (incl. `field_products`), `ingest`, `pipeline/runner/contract.py`,
  `pipeline/parquet`, and **their own** `pipeline/lanes/<layer>/` package — never another layer's
  package (`test_lanes_do_not_import_each_other`) and **never `pipeline/direct/**`**, which Phase 7
  deletes (H6).

### 4.3 Runner

`python -m agri_data_service.pipeline.runner --lane <id> --mode forward|gap-fill|transform [--compare]`.
The last stdout line is the report. One turn goes like this:

1. Load the lane, the region and `STRATEGY` (S14).
2. **Choose the window:**
   - **forward** = `[edge − recheck_window, edge]`, where edge = `probe_edge` or `today − lag`.
     Nothing beyond the edge is fanned out, and for provisional lanes nothing after **yesterday
     UTC** (O1).
     - **Probe gate (S19).** When the strategy has `probe_edge`, the turn first spends the probe
       (≤ 2 locations, ≤ 14 days). It fans out the window only if the probe shows a valued day the
       census still owes (a missing day, or an absence-recheck day that now carries values). If
       not, the turn writes nothing, spends only the probe, and reports each owed day newer than
       the edge as `unsettled`. A probe that cannot answer (`unavailable`) or is throttled
       (`deferred`) gates the window and the turn exits 0 (O-R3-1).
   - **gap-fill** = the census hole list capped by `history.capability`, the turn budget and the
     provider month-to-date budget. It runs oldest-first unless `order` says otherwise. It is
     **disabled by default** (S12).
   - **transform** = the days whose input digests differ from the transform receipt.
3. **Budget** with the provider weight formula (§6.3), against the mode's per-turn cap
   (`forward_max_weighted_calls` or `gap_fill_max_weighted_calls`) and the provider month-to-date
   budget. A day that cannot be afforded is `deferred_budget`.
4. **Fetch** each unit with independent retries: exponential on 5xx and timeout, then
   `upstream_unavailable`. The 429 series is 20/40/80/160 s, then `deferred_quota`.
5. **Checkpoint** verified responses keyed by retrieval instant, with per-parameter eligibility.
6. **`settle` → `rows`.** Apply the **rewrite rules (S11)**: write when the day is absent in
   storage, when the source receipt digest changed (settled lanes), or when strictly more units
   are present (partial `write_and_recheck` days).
7. **Write** through existing primitives: retract a disproven absence, write the base rung,
   derive tiers, write receipts, and publish the availability generation + pointer. There is no
   availability extension for `static_lookup` (other-lanes F6).
8. **Report and exit:** the S5 report (including `requests`, `weighted_calls`, `fetch_attempts`, `http_requests` and
   `probe`), exit per S4, progress to stdout, failure detail to stderr.

`--compare` builds rows and diffs them against published partitions. It **cannot construct a
writer**.

### 4.4 Executor runtime

- **Catalogue bridge** (`execution/lane_catalogue.py`).
  - With `executor = "config"`, the TOML wins and the legacy spec is skipped.
  - Otherwise the legacy spec runs when the allow-list names it.
  - Invariant: no id is on both paths. The pinned test pins the legacy id set literally and derives
    the config id set from `lanes/`, so adding a disabled TOML needs no test edit.
- **Legacy allow-list and kill-switch.** `execution/lane_specs.py::parse_activation` stops
  raising on unknown ids: it warns and opens an incident (S8). This makes `lane_specs.py` an
  `f1-executor` file in Phase 1 (review H6).
- **Cron** (`execution/cron_schedule.py`) applies to config lanes only. Legacy lanes keep
  cadence/phase until they flip.
  - Due = the latest fire ≤ now with no settled run for it.
  - `coalesce_latest` and `replay_oldest` keep their meaning.
  - A never-run lane waits for its next fire.
  - A gap-fill fire is its own definition, `<lane>:gap-fill`.
- **Work queue** (S15) with runtime switches (S16).
- **Breaker split** on S4:
  - Upstream class (`75`): half-open probes at 1 h, 2 h, 4 h … 24 h and auto-release. One
    `agri.job_incident` row opens on hold and is resolved on release.
  - Code class (`70`, `78`, unclassified; every legacy non-zero exit is here): an operator hold
    and one open `agri.job_incident` row shown in `/admin/jobs`. **No webhook, no email (O4).**
    No per-tick repeated error line.
  - The streak counts only runs after the latest supersession marker (executor F4).
- **Operator command.** `supersession_command` prints complete dry-run and apply lines:
  `railway ssh --service plantgeo-job-executor --` prefix, `--evidence` pre-filled, `--operator`,
  `--apply`. A test parses them with the verb's click parser.
- **Ledger detail.** `sql/jobs/refresh_job_run_rollup.sql` writes `job_run.last_error_summary`.
  `src/lib/server/trpc/routers/jobs.ts` shows the cron, next fire, open incidents, failure text
  and provider month-to-date usage.
- **G0: the legacy soil cap (own push, first; FR-24, O8, O-R3-1).** In `pipeline/direct/soil/` only
  (the full executor brief is plan §"G0 task brief"):
  - `forward.py::SOIL_MAX_DAYS` 5 → **1**, so every invocation (hourly forward, a repair's
    `--max-days`, an operator run) fans out at most one day; `gap_repair_contract.py` reads it as
    soil's `max_days_cap`, so repair runs inherit the cap unedited.
  - `SoilForwardConfig.request_budget` → `chunks_per_day × max_days + SOIL_EDGE_PROBE_REQUESTS`
    = 32 × 1 + 1 = **33 logical requests per run** (was 160); at most `MAX_FETCH_ATTEMPTS` (4) HTTP
    attempts each, so **≤ 132 fetch attempts (≤ 1,584 HTTP requests incl. transport retries and redirect hops)**.
  - A per-run **edge probe**: one request for two inland support cells over the 14-day recheck
    window. Its status decides the walk:

    | `probe_status` | owed day in the window | absence recheck in the window | owed day older than the window | exit |
    |---|---|---|---|---|
    | `ok` | fanned out only if the probe shows it valued; otherwise `source_unsettled` (`newer_than_probed_edge`), 0 requests, no slot | fanned out only if valued; otherwise `idempotent_noop` (`absence_unchanged`) | walked as today | 0 |
    | `invalid` (null on a day the census holds as `data`) | gated (`source_unsettled`, `probe_invalid`) if newer than that product's newest census `data` day, else walked; still capped (G0 critic G2) | same rule | walked | 0 |
    | `blind` (nothing valued and nothing published in the window) | gated | gated | walked | 0 |
    | `unavailable` (transport failure after retries, refused or non-array body, `ValueError`, time budget out) | gated | gated | walked (if time remains) | 0 |
    | `deferred` (429) | gated | gated | the quota circuit refuses before any request: `source_unsettled`, walk stops | 0 |

  - The probe adds **one** exit-1 path: a probe cell that cannot be resolved (a changed support).
    Legacy exit-1 paths are unchanged (re-check R8: contention timeout, `blocked`, census conflict,
    a ladder that fails verification), among them a thin fan-out: `source.py::build_soil_day`
    refuses ≠ 1,470 values, `adapter.py::DirectSoilFieldAdapter.__call__` wraps it as
    `DirectSoilFieldError`, `_publish_locked_day` retries from the in-memory cache (0 requests) and
    raises (the A15 episode).
  - The report adds `weighted_calls`, `fetch_attempts`, `http_requests` and a `probe` block; each product adds
    `probe_status` and `probe_gated_days`.
  - Worst case per run: 1 probe (2 weighted) + 32 chunks (1,568 weighted) = **1,570 weighted** on a
    clean fan-out; **1,602 hard** (re-check R4: `SoilSourceCache.restore` restores only null-free
    chunks, so a restored 18-cell chunk plus an in-run re-ask can make all 32 requests 50-cell).
- **Legacy bridge fixes at G1** (`f1-legacy-bridge` + `f1-executor`):
  - FR-10: gap repair persists every candidate.
  - M5/N6: **shortwave is dropped from the legacy climate writer's turn** (its product iteration,
    URL parameters and distinct-clock budget), while its stream registration, census and serving
    stay intact. **All 11 legacy climate products** (`CLIMATE_FIELD_PRODUCTS`: 8 climate-field + 3
    soil-wetness) **and all 8 soil products are removed from legacy repair authoring**
    (`gap_repair_contract.py::REPAIR_BINDINGS`), so FR-10 starts no repair on streams the migration
    abandons (G8) or on the POWER quota G7's re-grid needs. The legacy climate `_spec` lag becomes
    `CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS` (5), not the shortwave lag (6).
  - O6: legacy soil's spec goes to a **6-hourly cadence** (`cadence_seconds` 21,600, phase offset
    3,000 s unchanged, so buckets at 00:50/06:50/12:50/18:50 UTC) on top of G0's per-run cap. Its
    decorative schedule string changes from `"50 * * * *"` to **`"50 */6 * * *"`**, and `f1-executor`
    updates both pins (`tests/direct/soil/test_lane_registrations.py::EXPECTED_SCHEDULE` and
    `tests/test_job_executor_service.py::EXPECTED_SCHEDULES`).

### 4.5 Serving (review H1)

- The live read path is `parquet_ops/authorized_serving.py`, `warehouse_reader.py`,
  `availability_coverage.py` and `coverage.py`, via `interface/http/parquet_routes.py::parquet_bp`.
  `planes/signal.py` and `planes/water_gauges.py` have only test importers, so they are **not**
  wrapped.
- `planes/layer_service.py` holds the `LayerService` Protocol and `ConfigLayerService`, which
  **delegates to `parquet_ops`**.
- `planes/layer_service_registry.py` holds the keyed registry. The key is the stream slug:
  **served slug == stream name**.
- A new stream is servable once it is registered (S18): `authorized_serving.py::_LANES` and
  `coverage.py::registered_census_lanes` both read `LANE_REGISTRY` / `LANE_REGISTRATIONS`, and an
  unregistered slug fails with "the lane is not registered".
- DI goes through `app.ext.add_dependency`. **The injected type stays a runtime import** (ML-track
  hotfix `b1f02f95`).
- New route: `GET /api/v1/parquet/layers` serves metadata (units, grid, attribution, lifecycle,
  edge and its source, precedence, coverage).
- **Rebind = web re-point, not a server indirection.** The new climate streams have new slugs
  (`meteorology-*`, `shortwave-*`). At G8, the stream reads (web readers, slider capability
  `parquetLanes`, the agent report, the manifest bindings) move to them; UI layer keys stay; every
  soil-wetness entry is removed from every web list and from both region manifests. The legacy
  `climate-field-*` streams stay readable history. That web set is owned by `p6-climate-rebind`.

### 4.6 Transforms and provisional pruning

- A transform writes under its own `layer=<slug>/` prefix. It reads its inputs' published partitions
  and never imports lane code.
- Its receipt records input digests per day, which drives the dirty-day window and gives the
  precedence audit.
- A provisional partition for day D is pruned after the transform has rebuilt D from the settled
  input. The pruned digest stays in the transform receipt.
- This is consistent with `layer-lanes.md` §2, which already requires deleting superseded
  partitions. The 2026-08-03 rule is about persisting versus a live proxy, not about keeping
  superseded values (review flag assessment; §11 F7 withdrawn).

### 4.7 Coexistence and rollback

- **Legacy keeps serving** each lane until that lane's Phase-6 verdicts. The only switch is
  `executor = "legacy" | "config"`.
- **Rollback of a lane:**
  1. Brake with no deploy: `agri-service ops jobs-set-lane-enabled --definition plantgeo.executor.<lane> --disabled …`.
  2. Revert: flip `executor = "legacy"`. **This needs a monitor sweep and a receipt refresh before
     the push** (S13), under an owner go, at a quiescent point (S17). During a multi-slice
     authoring window the brake holds until then. The same definition key resumes the legacy
     checkpoint.
- **Rollback of the shared runtime:** the S16 switches, or a revert commit.
- **Rollback of G0 (review P7).**
  1. **The owner clears `OPEN_METEO_API_KEY` first — mandatory before any G0 revert** once the key
     is set. Reverting G0 on the paid key would restore the ≤ 244,608 weighted/day pre-G0 ceiling
     against paid quota; on the free tier the 10,000 calls/day wall bounds it.
  2. Then a revert commit (sweep + receipt + owner go).
  3. **A probe fault is fixed forward, never by a revert:** a persistent `invalid`, `blind` or
     `unavailable` gets a probe-fix push under its own review and go. `deferred` on the free tier is
     expected, not a fault (A20).
- **Climate is additive:** new ids and new streams. Rollback before G8 changes nothing; after G8
  it is a web re-point back.
- **Water gauges runs in parallel** (§7a). Rollback before G4 changes nothing; after G4 it is a web
  re-point back plus resuming the legacy lane. The legacy host is decommissioned in Q1 2027, so
  this rollback window is finite.

## 5. Functional requirements

P0 = required to ship; P1 = required before deprecation.

| id | requirement | acceptance criteria | P |
|---|---|---|---|
| FR-1 | Lane config + image plumbing | Every `lanes/*.toml` parses. The §4.1 invariants hold. A contract test, run by the monitor sweep and **enforced at image build through the receipt digest** (S13), fails on invalid files, unresolved strategy keys (S14), cycles, one-sided conflicts, uncovered regions, recheck ≤ lag, footprint literals, key-shaped values, a settled weighted-provider lane firing > 1/day without `probe_edge`, and a `[[streams]]` slug without its registration. A static test pins the Dockerfile `COPY lanes/` lines (both stages), `DIGEST_DIRECTORIES` (incl. `tests/scripts/test_quality_receipt.py`), and that `.dockerignore` excludes no digest input. | P0 |
| FR-2 | Provider config | Provider files declare hosts (including `customer-*` and the historical-forecast host), `api_key_env` (a name), `api_key_required`, `weighted` + the weight formula, and the monthly budget. An empty required key disables the dependent lanes with a named config error and never degrades silently. The client builds on `ingest/open_meteo_endpoint.py`'s existing free/customer pattern and accepts a single-location body. | P0 |
| FR-3 | Protocol + resolution | §4.2 as re-frozen at the end of Phase 2, in `pipeline/runner/contract.py`; the S14 resolver in `pipeline/runner/resolve.py`. `tests/test_layer_import_contract.py` passes unedited. Conformance fixtures cover a grid `refuse` lane, a point `write_and_recheck` lane, a `release_series`, a `static_lookup` watermark and a precedence transform. | P0 |
| FR-4 | Runner | §4.3. Tests cover: independent unit retries; 429 then `deferred_quota`; budget refusal; **a turn never exceeds its mode's per-turn cap**; mirrored-past absence; **S11 rewrite rules** (a digest change rewrites a same-unit-count settled day; a partial recheck rewrites only on more units; an input change rebuilds a transform); immutable-through; retraction; the published pointer; S4 exits; stdout/stderr discipline; compare mode unable to write. | P0 |
| FR-5 | Unified windows + probe gate | Forward and gap-fill differ only in window. The shortwave regression: an edge far behind `today − lag` fans out nothing past the edge and drains an older backlog oldest-first. Provisional windows end at yesterday UTC. **S19:** `tests/runner/test_windows.py::test_a_forward_fire_fans_out_only_when_the_probe_shows_a_new_settled_day`, `::test_a_forward_fire_with_an_unmoved_edge_spends_only_the_probe` and `::test_an_unavailable_or_deferred_probe_gates_the_window_and_exits_zero`. | P0 |
| FR-6 | Cron | Every cron parses (contract test). Due-ness and catch-up are correct across downtime, first run and flips in both directions. | P0 |
| FR-7 | Work queue | Session per lane. The tick does not await lanes. A slot limit (default 1) is enforced. A lane never runs twice. Leader-loss cancels lane tasks. The S16 `serial` switch restores today's behaviour. Tests use fake children and fake sessions. | P0 |
| FR-8 | Breaker + incident | Exit 75: half-open probes and auto-release. Exit 70/78/legacy non-zero: hold + one open incident row, shown in `/admin/jobs`, with no webhook or email (O4). One failure after a release does not re-hold. No per-tick repeated error line. `BREAKER_MODE=legacy` restores today. | P0 |
| FR-9 | Command + ledger | The printed lines parse with the verb's parser. `last_error_summary` is populated. `/admin/jobs` shows it. | P0 |
| FR-10 | Gap-repair hotfix | Every authorized candidate persists. The test uses ≥2 candidates and a session fake that honours rollback, on bindings that survive G1 (`drought`, `vegetation`). | P0 |
| FR-11 | Quarantine | A bad TOML quarantines one lane. The kill-switch and the legacy allow-list warn on unknown ids (edits `lane_specs.py::parse_activation`). The process never exits on config. | P0 |
| FR-12 | Provider budget | Per-provider `weighted_calls` in reports; month-to-date SQL; turns refused past the monthly budget. This self-accounting is the meter of record if the provider portal shows no usage (A16); G0 starts it for legacy soil. | P0 |
| FR-13 | Serving registry | `ConfigLayerService` delegates to `parquet_ops`. Golden responses for every current route are unchanged. DI plus a runtime-import annotation test. | P0 |
| FR-14 | Layer metadata | `GET /layers`. A server-side web client, consumed at G8. `npm run check:data-boundary` green. | P1 |
| FR-15 | Transforms | Digest-driven rebuild, chaining, precedence with per-row `precedence_source`, prune-after-supersession (off until G6). | P0 |
| FR-16 | Swarm strategies | One strategy + TOML per §7 lane, preserving the listed settle semantics, each with its own adversarial verdict. | P0 |
| FR-17 | Climate re-source | §6 in full: 3 ingest + 2 transform lanes; S10 node mapping; five layers from 1984; **mandatory POWER solar re-grid**; provisional ending yesterday UTC; wind relabel; soil-wetness retirement at G8. | P0 |
| FR-18 | Water gauges early | §7a in full: modern API daily values, the named-day rule as recorded, a DV history re-pull to **1990-09-30** (`pipeline/parquet/lane_registry.py::_MEASURED_COMPLETE_HISTORY_FLOORS["water-gauges"]`), a parallel run, its own gate. **G4 requires the re-pull to have reached that floor** (or an A14 evidence row lowering it). | P0 |
| FR-19 | Compare mode | Read-only per lane before G6. **Not used for water gauges** (a different API and quantity); water gauges uses the §7a validation instead. | P0 |
| FR-20 | Deprecation | After both Phase-6 verdicts per lane: first remove every legacy import, entry and literal from the shared executor/registry files (`d7-legacy-shared`), then delete the legacy modules, tests and extraction shims (`d7-legacy-lane-modules`). `gap_repair*` authoring and the allow-list parse go once no legacy lane remains. `mtbs-forward` is deleted (O7). | P1 |
| FR-21 | Legacy bridge (G1) | Shortwave is dropped from the legacy writer's turn (registration and serving intact); all 11 legacy climate products and all 8 soil products are excluded from legacy repair; the legacy climate lag is `CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS`; legacy soil runs 6-hourly (`"50 */6 * * *"`) **on top of G0's per-run cap**. Tests pin the legacy product iteration, the repair exclusions (incl. soil → `no_repair_binding`), the climate lag, and the soil cadence and schedule string. | P0 |
| FR-22 | Analysis lattice | §4.1 manifest entry in `pnw.json` and `pnw.ts` (optional field, so `kenya_highlands.*` stay valid), plus the 1,568-cell parity test. | P0 |
| FR-23 | Extraction | Before the swarm, everything outside `pipeline/direct/**` that imports a Phase-7-deleted module, **and the land-context and crop-cover source protocols the shadow strategies need** (`pipeline/source_bindings.py`), imports from a stable home. **Every module those moved modules import from `pipeline/direct/**` moves with them** (review P6: `burn_severity/products.py`; `drought/products.py`; `evacuation_zones/{products,rows,source,support}.py`; `watersheds/source.py`; `crop_cover/{source,products}.py`; `land_context/{source,products}.py`), so no `pipeline/lanes/**` module imports `pipeline/direct/**` and `d7-legacy-lane-modules` finds no surviving importer. Legacy files become re-export shims with unchanged behaviour. Full list in `metadata.json` → `p4-extract`. | P0 |
| FR-24 | **G0 legacy soil cap** | §4.4 G0 bullet and its status table. No soil process spends more than **33 logical requests (≤ 1,602 weighted calls hard, 1,570 on a clean fan-out — re-check R4), ≤ 132 fetch attempts (≤ 1,584 HTTP requests incl. transport retries and redirect hops)** (33 × `MAX_FETCH_ATTEMPTS` 4), whatever its flags, and the report states `requests_spent`, `weighted_calls`, `fetch_attempts` and `http_requests`. An owed day inside the probe window whose probe is null costs no request and no slot, so **no probe-null day is ever fanned out**. A probe that cannot answer is `unavailable`; a 429 is `deferred`; in both the window is gated, older owed days are walked, and the run exits 0 (O-R3-1). A probe null on a `data` day is `invalid`: the walk is ungated and still capped. A day the probe shows valued but whose fan-out is thin (≠ 1,470 values) raises `DirectSoilFieldError` after in-run retries that re-ask nothing, and the run exits 1 — unchanged legacy behaviour, the A15 episode (review P2). Tests: plan G0 brief. | P0 |
| FR-25 | **Stream registration mirror** | S18. Every stream a lane TOML declares has exactly one `LaneRegistration`; `water-gauges-daily` and the climate streams are servable and censused; a parity test pins every mirror row to its TOML row; `CALENDAR_HISTORY_FLOOR` includes the mirror rows (the calendar's next version carries the earlier days, A19) and its `floor_basis` text names the lane that sets the minimum. | P0 |

## 6. Climate re-source (D8, O1, O3, O5, O7)

### 6.1 Lanes (ids proposed; re-frozen at the end of Phase 2)

| lane id | kind | source | window | writes |
|---|---|---|---|---|
| `meteorology-era5-settled` | ingest | Open-Meteo archive `models=era5`. Daily `temperature_2m_mean/max/min`, `dew_point_2m_mean`, `precipitation_sum`, `relative_humidity_2m_mean`, `wind_speed_10m_mean` at the S10 nodes. | forward: probed edge (≈ today−6), probe-gated (S19); gap-fill: 1984-01-01 → edge | `meteorology-era5-*` streams |
| `meteorology-ifs-provisional` | ingest | Open-Meteo forecast endpoint (forward) and **historical-forecast endpoint** (history capability). ECMWF IFS at the lattice centroids; the 7 variables + `shortwave_radiation_sum` in **one request** (8 ≤ 10 variables, no extra weight). | a 14-day request ending **yesterday UTC**, once a day; it writes days > the relevant settled edge | two streams: `meteorology-ifs-*` and `shortwave-ifs` |
| `shortwave-nasa-power-settled` | ingest | NASA POWER daily, `time-standard=UTC` pinned, `ALLSKY_SFC_SW_DWN`, sampled at the lattice centroids and de-duplicated by POWER's native 1° cell (≈98 cells). | forward: probed edge; gap-fill: 2022-04-30 → edge (**mandatory re-grid**, O7) | `shortwave-power` |
| `meteorology` | transform | settled ▷ provisional (S7) | digest-driven | `meteorology-air-temperature-mean/max/min`, `-dew-point`, `-precipitation`, `-relative-humidity`, `-wind-speed-10m` |
| `shortwave` | transform | settled ▷ provisional | digest-driven | `shortwave-radiation` |

- **Streams.** Each stream has one autoloaded schema module under `warehouse/schemas/`
  (`warehouse/parquet/schema.py::stream_schema_module`); there is no shared schema registry edit.
  Each stream also needs a registration: `p4-contract-freeze` appends the 24 climate stream rows to
  the S18 mirror from the FROZEN contract (7 ERA5 + 7 IFS + `shortwave-ifs` + `shortwave-power` + 7
  meteorology + `shortwave-radiation`). The new lanes never write the legacy `climate-field-*`
  streams.
- **Partial days.** Settled lanes: `refuse`. The provisional lane: `write_and_recheck`.
  `expected_value_units` for ERA5 is measured in Phase 0 (ERA5 has ocean values).
- **The legacy lane.** `climate-nasa-power-direct-forward` has no TOML. It keeps meteorology fresh
  (without shortwave, from G1) until G8, and is deleted in Phase 7.

### 6.2 Semantics the owner sees at the rebind

- **Extent shrink (O5, accepted).** The served climate area goes from the legacy 397 × 1° support
  over −125..−104, 31..51 N to the 1,568-cell lattice: centroids 42.125–48.875 N and
  −124.875..−111.125. Coverage south of 42 N and east of −111 is **no longer served** for any
  climate layer. The legacy streams remain readable history.
- **Wind:** 10 m (S1), about 1.34× the old 2 m values on flat grass. Consumers to update: the
  ML-service fire-risk features (a follow-up), `agent/report.py`, and the legend.
- **Precipitation:** POWER `PRECTOTCORR` was bias-corrected; ERA5 `precipitation_sum` is raw
  reanalysis; the IFS tail is forecast-model precipitation. The seam is visible per row as
  `precedence_source`.
- **Sample points.** Settled ERA5 values come from the NE-corner node (S10), about 14 km N and
  10 km E of the centroid. IFS values come from the centroid (D8). If validation shows a systematic
  step at the seam, sampling IFS at the node is a one-field TOML change.
- **Solar:** POWER's 1° SYN1deg value is shared by 16 lattice cells; the tail is ~9 km IFS.
- **ERA5T:** recent days are preliminary. Each probe-gated fan-out re-reads the whole 14-day window
  (the recheck is free at ≤ 14 days), and a monthly 90-day revision sweep picks up the rest (S11
  digest rewrite).
- **Provisional ends yesterday UTC (O1).** Today is never written by agri.

### 6.3 Open-Meteo budget (5M calls/month), recomputed conservatively (reviews H4, N3, P5, P8; O3, O8, O10)

**Weight rule (O10; conservative — P5 re-baselines it from measured usage):**

    weight = locations × models × max(1, days / 14) × max(1, variables / 10)

- Source: the pricing page captured at `.omc/research/open-meteo-pricing-20260926.html` ("one API
  call corresponds to one HTTP API request … more than 10 weather variables or … more than 2 weeks
  for a single location are considered multiple API calls … fractional counts are used"); its
  calculator also takes Locations and Models as inputs. **Below 2 weeks and 10 variables, 1.0 per
  location per request is the floor we assume**; the page never says a request costs less.
- A request spanning ≥ 14 days over the lattice costs **112 per grid-day** (1,568/14; stated
  fractional counting). A request of ≤ 14 days costs **1,568 flat**, so forward windows always ask
  for 14 days and get the recheck for free.
- The page also says "a usage dashboard is in development — until it is live, no hard cutoffs are
  enforced" (email alerts at 80/90/100%). Our caps are therefore the only brake (A16).
- **Weighted calls vs HTTP attempts.** Weighted calls are charged once per logical request started;
  `fetch_lane_capture` may make up to `MAX_FETCH_ATTEMPTS` (4) HTTP attempts per logical request
  (transport retries, minutely 429 waits). G0 reports both; P5 compares both with the portal.
- **Only legacy soil moves to the paid quota when the key is set.** Legacy code attaches the key to
  archive requests (`ingest/open_meteo.py::archive_daily_request`) and to `ingest/open_meteo_endpoint.py`
  hosts; legacy weather-observations uses the keyless forecast host
  (`ingest/open_meteo.py::get_current_weather_response` → `OPEN_METEO_BASE_URL`), and the endpoint-keyed
  CAMS / GloFAS / ensemble lanes are unscheduled (`execution/lane_specs.py::_DURABLE_JOB_SCHEDULES` is empty).

| item | arithmetic | weighted calls |
|---|---|---|
| **Pre-G0 legacy soil (today; keyless, so throttled by the free tier's 10,000/day)** | forward 24 runs × 5 fan-outs × 1,568 + repair ≤ 4 runs × 9 fan-outs × 1,568 | ≤ 188,160 + 56,448 = **≤ 244,608 / day** — why the key waits for G0 |
| Legacy soil, one run after G0 | probe 2 × 1 × 1 × 1 + fan-out 1,568 × 1 × 1 × 1 | **≤ 1,570 / run** (33 logical requests, ≤ 132 fetch attempts (≤ 1,584 HTTP requests incl. transport retries and redirect hops)) |
| **Interim** (key set after G0 → G1) | forward 24 × 1,570 + repair ≤ 4 × 1,570 (authoring every 6 h, `job_executor_service.py::DEFAULT_REPAIR_INTERVAL_SECONDS`; one persisted candidate per pass until FR-10) | **≤ 43,960 / day**. Realistic **1,616 / day** (24 × 2 + 1 × 1,568) **only once no owed day older than the probe window remains**; each such day costs one 1,570 fan-out per run until drained (≤ 37,680 / day hourly). The G0 hole-count row (taken before the key) states how many there are. |
| Legacy soil after G1 (6-hourly, no repair) | 4 × 1,570 | ≤ 6,280 / day; realistic 4 × 2 + 1,568 = 1,576 / day with no older backlog |
| Legacy-soil failure episode (A15) | 3 failed buckets before the hold × 5 attempts × 1,570 | ≤ 23,550 once |
| Compare runs at G6 (soil config forward; weather-observations one turn) | (1 + 1,568) + W₁ | 1,569 + W₁ |
| **Meteorology re-pull, all five layers, 1984-01-01 → 2026-09-20 (15,604 d; O3)** | 112 × 15,604 | **1,747,648** (+112 per day the edge advances before G7) |
| Provisional bootstrap via the historical-forecast endpoint, 2026-07-01 → yesterday at G7 (assumed ~110 d) | 112 × 110 | 12,320 |
| ERA5 settled forward, probe-gated (S19) | 30 × (24 probes × 1 + 1 fan-out × 1,568) | 47,760 / month |
| IFS provisional: one daily 14-day turn ending yesterday | 30 × 1,568 | 47,040 / month |
| Soil config lane forward, probe-gated (S19) | 30 × (24 × 1 + 1,568) | 47,760 / month |
| Revision sweeps, ERA5 + ERA5-Land (90 d, monthly) | 2 × 1,568 × 90 / 14 | 20,160 / month |
| **Soil gap-fill** (enabled at G6; cap 1,600 per turn, one daily fire) | ≤ 1,600 × 30 | ≤ 48,000 / month; expected = hole-runs × 1,568 (the G0 hole-count row, refreshed at G6) |
| weather-observations after its cut-over (customer forecast host) | measured in Phase 0 | W / month |
| Owner-run local scripts (`execution/historical_era5*`) | unscheduled | stated before each run |
| **Steady state after cut-over** | 47,760 + 47,040 + 47,760 + 20,160 | **162,720 + W / month (3.3% + W)**; ≤ 210,720 + W with soil gap-fill at its cap |
| **Worst single month** (every ceiling stacked: 14 interim days + 16 capped-soil days + one failure episode + compares + re-pull + bootstrap + soil gap-fill cap + a full steady month) | 14 × 43,960 + 16 × 6,280 + 23,550 + 1,569 + 1,747,648 + 12,320 + 48,000 + 162,720 = 615,440 + 100,480 + 23,550 + 1,569 + 1,747,648 + 12,320 + 48,000 + 162,720 | **≈ 2,711,727 + W₁ + W (≈ 54.2% + W₁ + W)** |
| An all-interim month (Phase 1–2 run long) | 30 × 43,960 | 1,318,800 (26.4%) |
| **Hard-bound correction (re-check R4)** | every legacy-soil row above scales by 1,602 / 1,570: interim 28 × 1,602; post-G1 4 × 1,602; failure episode 15 × 1,602 | interim **≤ 44,856 / day**, post-G1 **≤ 6,408 / day**, episode **≤ 24,030**; worst single month +12,544 + 2,048 + 480 = **+15,072 → ≈ 2,726,799 (≈ 54.5%) + W₁ + W**; all-interim month 30 × 44,856 = 1,345,680 (26.9%) |
| The realistic G7 month (after G6; legacy soil gone; no soil backlog) | 1,747,648 + 12,320 + 162,720 + 48,000 | 1,970,688 + W (39.4% + W) |

- **NFR-2 headroom.** 60% of 5M is 3,000,000; the stacked worst month leaves 288,273 for
  W₁ + W + the edge advance. Phase 0 measures W; if W₁ + W exceeds 250,000 / month, G7's re-pull is
  split across two months by lowering `gap_fill_max_weighted_calls` (a TOML field).
- **Re-pull schedule (G7).** The gap-fill cron runs hourly at 8,000 weighted calls/turn
  (≈192k/day), about 9.1 days in total.
- **Order is newest-first for this re-pull:**
  - 2022-04-30 onward (1,605 d × 112 = 179,760) lands in about 1 day;
  - 2018 onward (3,185 d × 112 = 356,720) lands in about 2 days;
  - the 1984–2017 span follows.
- A complete repeated re-pull still fits one month (A2).
- **P5 re-baseline.** After G0 and the key, Phase 0 reads the Open-Meteo customer portal for three
  full UTC days (if it shows per-key usage) and compares it with the soil runs' self-reported
  `weighted_calls`, `fetch_attempts` and `http_requests`; this table is re-issued from measured per-request costs
  before G1.

## 7. Per-lane migration table

| lane (id) | source + endpoint(s) | history | partial_day | settle semantics to preserve | notes |
|---|---|---|---|---|---|
| burn-severity-direct-forward | MTBS EDW `MapServer/63`; Friday current capture | release archive + current snapshot | refuse | Release/snapshot identity; cron `55 8 * * *` | Envelope from the Region (federation offender in `pipeline/direct/burn_severity/{capture,current_snapshot,stage}.py`). Its source protocol, `mtbs.py` and `products.py` move in `p4-extract`. |
| climate-nasa-power-direct-forward | POWER daily point, UTC, 397 × 1° | range requests | (legacy) | **Replaced** (§6) | Shortwave dropped from its turn and all its products removed from legacy repair at G1 (M5, N6). Deleted in Phase 7. |
| drought-direct-forward | USDM weekly GeoJSON | archive by release date | refuse | `release_series`: 404 = `unsettled`, never absent; valid Tuesday | The cron is narrowed to the release window. Protocol, `usdm.py` and `products.py` move in `p4-extract`. |
| evacuation-zones-direct-forward | Oregon OEM `Fire_Evacuation_Areas_Public/FeatureServer/0` | none | refuse | `static_lookup`, watermark `dataLastEditDate` | `coverage` regional US-OR. Its watermark reader and the `products`, `rows`, `source` and `support` modules it imports move in `p4-extract`. |
| fire-detections-direct-forward | FIRMS area API (`NASA_FIRMS_KEY`) | FIRMS archive (unwired today) | write_and_recheck | Rolling re-fetch reconcile | `refetch_window_days` split from the edge; the 10,000-record cap surfaces as `unwritten`. |
| fire-perimeters-direct-forward | WFIGS `…Perimeters_Current/FeatureServer/0` | none | refuse | `static_lookup`, watermark `editingInfo.lastEditDate` | — |
| sensors-direct-forward | NWS station observations | retention 6 d | write_and_recheck | Rolling 7-day window; emits `unwritten` | Past-retention days are `retention_exceeded`. `merge_sensors_day` moves in `p4-extract`. |
| soil-era5-land-direct-forward | Open-Meteo archive `era5_land`, 1,568 / 1,470 cells | archive to 1950 | refuse (0 or exactly 1,470) | Mirrored-past proof; 14-day absence recheck; one fetch serves 8 streams | **Capped at G0** (33 requests/run, probe-gated; FR-24), 6-hourly and repair-excluded at G1. The config lane's `probe_edge` keeps it gated (S19). The grid is `analysis-0p25`. |
| vegetation-sentinel2-ndvi-direct-forward | Element84 STAC `sentinel-2-l2a` | STAC archive | author derives it from the legacy forward | Writer floor 2026-09-06; lag 7 = measured median | The TOML floor includes 2026-09-01..05, so **G6's gap-fill enable fills those 5 days** (named in G6). The STAC probe is the edge. |
| water-gauges-direct-forward | legacy NWIS IV | — | — | — | **Replaced early** by `water-gauges-daily` (§7a). Paused at G4, deleted in Phase 7. |
| watersheds-direct-forward | USGS NHDPlus HR | n/a | refuse | `static_lookup`, watermark `max(loaddate)` | `lifecycle = "discontinued"`. The manifest binds `hydrosheds` while the lane reads NHDPlus HR, so the TOML records the real source. Its watermark reader and `source.py` move in `p4-extract`. |
| weather-observations-direct-forward | Open-Meteo forecast `current=` | `past_days` ≤ 92 | write_and_recheck | Rows per tick; emits `unwritten` | Lag and floor are cited or marked `basis = "uncited"`. It moves to the customer host at cut-over (W). Exempt from the `probe_edge` rule (write_and_recheck). |
| crop-cover-usda-maintain (shadow) | USDA NASS CDL | annual editions | refuse | Edition identity | `enabled = false`; runner scratch dir. Its source protocol (`CropCoverSource`, `USDA_CROP_COVER_SOURCE`) and the `source.py`/`products.py` it imports move in `p4-extract`. |
| land-context-blm-* (shadow) | BLM ArcGIS | per service | refuse | Watermark per service | `enabled = false`. The reconcile mapping is decided at activation (O7). Its source protocol (`LandContextSource`, `BLM_LAND_CONTEXT_SOURCE`) and the `source.py`/`products.py` it imports move in `p4-extract`. |
| mtbs-forward (shadow) | — | — | — | — | **Deleted** in Phase 7 (O7; executor F7). |
| vegetation-ndvi-governed-plane-promotion (shadow) | `execution/vegetation_partition_promotion.py` | — | — | Checksum per day-partition SHA | A disabled transform. The output target is confirmed by the author. |

### 7a. Water gauges, early lane (O2, D9, reviews M4, N5)

- **Lane.**
  - **Id:** `water-gauges-daily`, a new id (S2 exception, parallel run).
  - **Strategy:** `water_gauges.usgs_water_data` (`pipeline/lanes/water_gauges/usgs_water_data.py`)
    over a new `ingest/usgs_water_data.py`.
  - **Provider:** `lanes/_providers/usgs-water-data.toml`, key variable `USGS_WATER_DATA_API_KEY`
    if probe P4 shows one is needed.
- **Quantity.** The daily **mean** discharge: parameter `00060`, statistic `00003`, from the
  modern API's daily-values collection. Forward and gap-fill read the same collection over
  different windows, so they are the same quantity (D3). Approval status is a column, and a
  provisional→approved change is a digest rewrite (S11).
- **Units.** Per tile (the 8-tile layout derived from the region envelope). Each tile retries
  independently, and a tile that stays down is reported as unwritten for its gauges
  (`write_and_recheck`).
- **Named day, recorded against the ISO-prefix rule.**
  - The legacy rule is `pipeline/validation/water_gauges.py::publisher_named_day` =
    `date.fromisoformat(raw[:10])`: the first ten characters of the upstream string, never parsed
    and converted first. Legacy keys a reading by NWIS `updatedAt`, which carries the site's local
    offset (`ingest/identity.py::build_streamflow_gauge_identity`, `siteNo:updatedAt` verbatim).
  - For daily values, the named day is **the daily value's own `time` string as served**, passed
    through `publisher_named_day` unchanged. A date-only string is its own prefix. If probe P4
    shows a timestamp with an offset, the prefix of the served string is still the day.
  - **Never** derive the day from a UTC window. The legacy validator's
    `pipeline/validation/water_gauges.py::fetch_source_day_from_nwis` builds a half-open **UTC**
    window and must not be reused. **Never** call `datetime.fromisoformat(...)` and convert to UTC:
    that conversion moved 6,279 of 16,743 rows.
  - Identity is `monitoring_location_id:time:statistic_id` verbatim.
  - P4 records the served `time` format and whether USGS computes the daily value over the
    site-local standard day. Its acceptance rule is that the prefix day equals USGS's own day
    label for 10 gauges × 5 days.
- **History re-pull floor (N5): 1990-09-30.** That is the legacy lane's **complete-history floor**,
  `pipeline/parquet/lane_registry.py::_MEASURED_COMPLETE_HISTORY_FLOORS["water-gauges"]` ("corpus
  measurements/owner decisions", binding since 2026-09-19) — **not** the legacy registration's
  `history_floor` (2026-05-24, the dense-record/writer-ownership floor). The TOML sets
  `[days] floor = "1990-09-30"`; the S18 mirror row carries it as `history_floor` and
  `complete_history_floor`. The re-pull runs as the lane's gap-fill, enabled at G3 and paced by its
  TOML budget. The floor is lowered only by an A14 evidence row (P4's per-gauge DV start dates).
- **Registration and stream.** New stream `water-gauges-daily`, with schema
  `warehouse/schemas/water_gauges_daily.py`, the S18 mirror row (appended by `w3`), and manifest
  binding `usgs_water_data` (regional). Registering it moves `CALENDAR_HISTORY_FLOOR` to 1990-09-30;
  the calendar's next version carries the earlier days (A19). The legacy `water-gauges` stream stays
  as history.
- **Parallel run and gates.**
  - **G3:** the lane is enabled and its gap-fill is on. The legacy IV lane keeps serving.
  - **Validation**, replacing the row compare (review M4), is recorded as rows in the phase table:
    - spot-check 10 gauges × 5 days against the modern API's daily values, and against legacy
      NWIS `dv` while it exists;
    - forward/gap-fill equality on overlapping days;
    - the named-day check;
    - **history depth: the census holds no owed day in [1990-09-30 (or the A14-lowered floor), edge].**
  - **G4 (conditional on every validation row, including history depth):** the web water reader,
    the details panel, the map layer, `alert-engine.ts::checkStreamflowAlerts`, the slider capability
    `parquetLanes` entry and the attribution row (`parquet-trpc-readers/shared.ts`, "U.S. Geological
    Survey NWIS" → the Water Data daily-values source) move to `water-gauges-daily` behind one
    stream-name constant, and the legacy lane is paused. The UI layer key stays `water-gauges`. The
    alert is a **low-flow (drought-stress)** check, so a daily mean suits it.
- **Rollback.** Before G4 nothing user-visible changes. After G4, re-point the web back and resume
  the legacy lane until the Q1 2027 legacy decommission.

## 8. Non-functional requirements

- **NFR-1 Performance.** The runner adds ≤5% wall-clock versus legacy (measured in compare mode).
  Concurrency stays 1 until Phase-2 single-lane RSS × 2 fits the container with margin; raising it
  is part of G6.
- **NFR-2 Budget.**
  - Per-turn caps hold in every mode (S19; G0 for legacy soil).
  - Provider month-to-date usage is visible in `/admin/jobs`.
  - Open-Meteo usage stays ≤ 60% of 5M in any month, including the migration month (§6.3 stacked
    worst case ≈ 54.2% + W₁ + W; the §6.3 split rule keeps it under 60% if W is large).
- **NFR-3 Security.**
  - TOML holds variable names only; the contract test rejects key-shaped values.
  - The owner sets secrets.
  - `--evidence` never interpolates a DSN or key.
  - Route inputs come from closed vocabularies.
- **NFR-4 Observability.**
  - The S5 report comes from every lane.
  - One incident row per hold, with no repeated lines.
  - `level:error` only for failures.
- **NFR-5 Federation.**
  - No footprint literals; lattices come from the manifest (the G0 probe cells are derived from
    the legacy soil lattice's existing pinned constants, not a new literal).
  - `coverage` is declared and checked.
  - No region names in ids, prefixes or routes.
- **NFR-6 Readability.**
  - Modules under ~600 lines and functions under ~60 (soft).
  - Rationale lives in `AGENTS.md`.
  - Algorithms are named at their implementation.
- **NFR-7 Operability.** "Why didn't lane X write day D" is answerable from the ledger and
  `/admin/jobs` alone.

## 9. Risks and assumptions

| # | item | default | reversal cost |
|---|---|---|---|
| A1 | ERA5 node mapping (S10) is a bijection; Open-Meteo echoes the requested node with `cell_selection=nearest`. | Probe P1 verifies it before any re-pull. | Low before G7; high after (a re-pull is 1.75M). |
| A2 | The weight formula floors each factor at 1 (≥ 1.0 per location per request, O10) and is fractional above 14 days / 10 variables, multiplied by locations and models. | P5 re-baselines from measured usage. | Low: if real costs are lower, the ceilings only loosen. |
| A3 | POWER UTC solar trails ~3 months (an inference; climate F1). | `probe_edge`, never a fixed lag. | None. |
| A4 | The solar re-grid via POWER is mandatory (O7). Dedup by native cell gives ≈98 range requests, or the regional endpoint. | Probe P3. | — |
| A5 | POWER soil wetness is retired: GWET fractions are not ERA5-Land m³/m³. | No longer presented from G8; history stays readable. | Low. |
| A6 | Keeping ids gives ledger continuity across flips. | FR-6 tests both directions. | Low. |
| A8 | Concurrency 1 at G1 (S15). | Raised to 2 only inside G6, with evidence. | None (env). |
| A9 | Every TOML change is a sweep + receipt + push + redeploy (S13), at a quiescent point (S17). The ledger pause is the only instant brake. | — | — |
| A10 | In-house cron parser. | Property tests. | Low. |
| A11 | Customer hosts for the forecast and historical-forecast endpoints follow the `customer-*` pattern. | Probe P2; the provider file holds them. | None. |
| A12 | The USGS modern API may need a key. | Probe P4; the owner sets `USGS_WATER_DATA_API_KEY` under G2 if so. | None. |
| A13 | The interim legacy-soil ceiling between the key being set and G1 is **≤ 43,960 weighted/day** (G0's 1,570/run × 24 forward runs + ≤ 4 repair runs). Realistic ≈ 1,616/day **only with no owed day older than the probe window**; the G0 hole-count row, taken before the key, states the backlog, which drains at one 1,570 fan-out per run (review P8). | Accepted by O8's ordering: G0 is pushed and observed for 24 h before the key is set. | None. |
| A14 | The USGS daily-values history depth and rate limits allow the re-pull to 1990-09-30 within the TOML budget. | Probe P4 measures them; the floor is lowered only with an evidence row, and G4 waits for the (possibly lowered) floor. | Low. |
| A15 | **Retry amplification.** A run that exits non-zero — including a G0 thin-day raise (FR-24) — is retried up to 5 times in its bucket (`execution/lane_specs.py::LaneExecutionSpec.definition_spec`, `max_attempts=5`); the breaker holds a `coalesce_latest` lane after 3 failed buckets (`CLOCK_RELEASE_STREAK_LIMIT`), so a failure episode costs ≤ 15 × 1,570 = 23,550 once. An intermittent fail-4-then-succeed pattern in every bucket would not trip the breaker and could reach 5× the daily ceiling (≤ 219,800/day interim). A probe outage is **not** a failure (O-R3-1): it exits 0. | Detected by attempts per bucket in `/admin/jobs`, the G0 observation row and Open-Meteo's 80% email; stopped by the ledger brake. | Low. |
| A16 | **The provider meter may not exist yet.** The captured pricing page says a usage statistics portal is under development and monthly limits are not enforced. | P5 records what the portal shows; otherwise FR-12's self-accounting (G0's `weighted_calls`, `fetch_attempts` and `http_requests`) and the provider's 80/90/100% emails are the meter. | None. |
| A17 | ~~`p4-extract` in a git worktree~~ **REJECTED by the owner (O-R3-3).** `p4-extract` runs sequentially after G4's push in the shared checkout. | Phase 4 starts after G4 (the ~3-day cost the owner accepted). | — |
| A18 | The two G0 probe cells always carry ERA5-Land values. As (lon, lat): **(−118.125, 45.375)** and **(−117.875, 45.375)**, the support cells beside the pinned extent's centre (−118.0, 45.5), inland NE Oregon, outside the soil fixture's 98-cell mask. | One keyless read-only archive request before the G0 push (evidence row); at runtime `probe_status = "invalid"` flags a null probe on a published day. | Low (a derived constant). |
| A19 | **Calendar coverage of the new floors.** `lane_registry.py::_fill_calendar` exports each version with `floor=CALENDAR_HISTORY_FLOOR`, and versions regenerate on `_calendar_watermark` (about once a year), so the days before 2000-11-01 appear at the calendar's next version, not at G3/G5. | The `w3` and `p4-contract-freeze` reviewers confirm whether any reader joins the calendar for those days; if one does, a one-off calendar version export is added to that gate under the owner's go. | Low. |
| A20 | **Free-tier deferrals are routine before the key.** Until the key is set, the free tier's 10,000 calls/day wall (≈ 6 fan-outs, shared with other keyless traffic) makes a 429'd probe normal, so the G0 observation expects `probe_status = "deferred"` on some runs (review P7). Soil repair items authored before G0 carry `max_days: 5` and fail `gap_repair_contract.py::RepairRequest.__post_init__` as `invalid_repair_request` after the deploy — expected and harmless (review P9(iii)). | Neither is a rollback trigger. | None. |
| R1 | Batch cut-over blast radius. | Per-lane flag, compare equality, intact legacy, ledger brake. | — |
| R2 | Legacy misbehaviour during migration. | G0 ships the soil cap; G1 ships FR-10, the shortwave drop, the climate/soil repair exclusions and the soil cadence. | — |
| R3 | Legacy WaterServices blackouts. | Water gauges moves first (Phase 3). | — |

(A7 is withdrawn: pruning is consistent with `layer-lanes.md` §2; see §4.6.)

## 10. Open questions (owner)

None. Round 1 (§3.1), round 2 (§3.3), the loop-2 decisions (§3.4) and the round-3 decisions (§3.5)
were answered 2026-09-26. Probe-dependent facts (P1–P5, weather-observations W) are measurements
with stated defaults, not owner decisions.

## 11. Evidence flags against settled decisions (status after review loop 1)

| flag | status |
|---|---|
| F1 solar grid mix | **Resolved.** The re-grid is mandatory (O7); without it the precedence transform is undefined (`na-sample:1deg:*` vs `sentinel2-ndvi-0p25deg:*`). |
| F2 ERA5 alignment | **Resolved** by S10: explicit nodes, zero extra calls. |
| F3 "to today" vs observed-only | **Resolved** by O1: the provisional tail ends yesterday UTC. |
| F4 water-gauges quantity mix | **Resolved** by O2: daily mean for both windows, and a new stream. |
| F5 "SYN1deg trails ~3 months" | **Open (inference).** It is handled by `probe_edge`, not a constant. |
| F6 `enabled` needs a deploy | **Restated:** it needs a sweep, a receipt refresh and a deploy (S13). The ledger pause is the instant brake. |
| F7 pruning vs 2026-08-03 | **Withdrawn** (review flag assessment; §4.6). |

## 12. User stories

- **Owner: the paid key cannot be burned.** As the owner, I want the paid Open-Meteo key to be
  unable to burn more than a stated amount per day.
  - *Given* G0 is live and the frontier is unsettled, *when* soil runs hourly, *then* each run
    spends one probe request (2 weighted calls) and no fan-out, and no run ever reports
    `requests_spent` > 33, `fetch_attempts` > 132 or `http_requests` > 1,584.
  - *Given* the probe host is down, *then* the run reports `probe_status = "unavailable"`, walks
    older owed days, and exits 0; the lane is never held for it.
- **Operator: unwritten days.** As an operator, I want every turn to say which days it did not
  write and why.
  - *Given* soil is throttled, *then* the ledger shows `deferred_quota` for day D.
  - The alarm ignores frontier days.
  - `/admin/jobs` shows provider month-to-date usage.
- **Operator: a USGS blip.** As an operator, I want a USGS blip to heal itself.
  - *Given* three exit-75 buckets, *when* USGS recovers, *then* the lane auto-releases within
    ≤24 h of backoff and its incident row resolves.
  - *Given* exit 70, *then* the lane holds, one incident row appears in `/admin/jobs`, and the
    printed command runs as printed.
- **Owner: one file per lane.** As the owner, I want to change a lane by editing one reviewed file.
  - *Given* a typo, the contract test names the file and field, and an unswept TOML change fails
    the image build (S13).
  - *Given* it ships anyway, only that lane is quarantined.
- **Map user: provisional days.** As a map user, I want air temperature through **yesterday** on
  the soil cells, with each day's source stated.
  - *Given* day D is past the ERA5 edge, D reads "provisional (ECMWF IFS)" until ERA5 publishes it,
    then "ERA5".
- **Map user: water gauges.** As a map user, I want water gauges as daily mean discharge with real
  history.
  - *Given* G4, the gauge panel shows the daily mean and its approval status for each day back to
    1990-09-30 (or the evidence-lowered floor).
- **Next-region deployer.** As the deployer of the next region, I want to bind sources by writing
  a TOML, a strategy and a manifest lattice entry. A source whose `coverage` excludes the envelope
  is refused at startup with a named reason.

## 13. Out of scope

- The watersheds move to 3DHP.
- Activating shadow lanes.
- ML retraining on the new wind and precipitation semantics (a follow-up).
- Deleting legacy climate or water partitions.
- Renaming legacy ids, or renaming UI layer keys (`climate-field-*`, `water-gauges`).
- Recovering the sensors retention gaps.
- Running PlantGeo locally.
- Making `LANE_REGISTRY` lazily loaded (a follow-up that would let S18's mirror become synthesis).
- Changing legacy soil's thin-day behaviour (it stays a raise; the config runner's `refused_partial`
  replaces it at G6).
- Any production mutation without an explicit owner go (plan gate list).

## 14. Review loop 1 disposition

Review: `.omc/research/ingestion-grill-20260926/critic-track-review-1.md` (oh-my-claudecode:critic,
CHANGES-REQUIRED). The orchestrator re-verified C1, C2, H1 and F3.

| id | disposition | what changed |
|---|---|---|
| C1 | accepted (fix variant: COPY + digest, keeping D1's path) | spec S13, §4.1, §4.7, FR-1, A9, F6; plan Phase 1A, rollback; metadata `f1-config` owns `Dockerfile` + `scripts/quality_receipt.py` |
| C2 | accepted | spec §2.1, §4.1 named lattice, FR-22; metadata `f1-config` owns `manifest.py`, `pnw.json`, `pnw.ts`, `region.ts` |
| H1 | accepted | spec §4.5 (wrap `parquet_ops`; served slug == stream; rebind = web re-point), FR-13; metadata `f1-serving` owns `authorized_serving.py`/`warehouse_reader.py`, drops `signal.py`; `p6-climate-rebind` owns the 9 web/agent/manifest files |
| H2 | accepted | spec S11, §4.3 step 6, FR-4 |
| H3 | accepted | spec S12, §4.3, §4.6, §7 vegetation row; plan gates G6/G7 (old G9 and G10 folded into G6) |
| H4 | accepted; its "set the key in the G6 window" is superseded by O6 (key set now) | spec §6.3 recomputed (interim, capped legacy soil, compares, W, local scripts, incremental provisional, historical-forecast endpoint, span label fixed), A13; FR-21 |
| H5 | accepted | spec S15, S16, FR-7, A8, NFR-1; metadata `f1-executor` owns `jobs/worker.py`, `jobs/lease.py`; plan G1 at concurrency 1 |
| H6 | accepted | spec S14, §4.2, FR-11, FR-23; metadata `f1-executor` owns `lane_specs.py`; new `p4-extract` slice; no central registry file |
| M1 | accepted | spec §4.2, FR-3; plan Phase 0 = draft, Phase 2 end = re-freeze |
| M2 | accepted (IFS stays at centroids per D8; a node switch is one field) | spec S10, §6.1, §6.2, A1; the ~7M escalation removed |
| M3 | accepted (owner O5) | spec §6.2 |
| M4 | accepted (owner O2) | spec §7a, FR-18, FR-19, R3; plan Phase 3; metadata `w3-water-gauges` |
| M5 | accepted | spec §4.4, FR-21; metadata `f1-legacy-bridge` |
| M6 | accepted | spec S16, §4.7 |
| LOW-1 service lists | accepted | plan: the service set is re-listed from Railway at every gate (2026-09-19 set: plantgeo-main, -parquet-api, -job-executor, -martin, -ml) |
| LOW-2 provider names | accepted | `metadata.json` is the one authoritative list (S3); the plan points at it |
| LOW-3 lane counts | accepted | climate = 3 ingest + 2 transform lanes; one IFS lane writes two streams (§6.1) |
| LOW-4 ceremony | accepted in part (rebutted for review count) | plan: one evidence table per phase (`evidence/phase<N>.md`) replaces per-lane files and prediction files; gates consolidated to G1–G9 |
| FA-F3 | accepted (owner O1) | §11 |
| FA-F7 | accepted | §4.6, A7 withdrawn, §11 |
| FA-F1 | accepted (owner O7) | FR-17, A4, §11 |

**Rebuttal, LOW-4 (review count).** Per-lane adversarial reviews stay: one reviewer per swarm lane,
plus the water lane. D10 settled "each with its own adversarial review". The evidence that they
earn their cost is the project's own record: all five adversarial passes run on 2026-08-17 returned
CHANGES-REQUIRED (memory note `plantgeo-adversarial-review-earns-its-cost`). What is cut is the
paperwork. Each review's verdict is one row in its phase table, not a file.

**Note on C1's fix variant.** The review offered package data under `A/lanes/` or COPY + digest.
COPY + digest is chosen because D1 settled the directory as `services/agri-data-service/lanes/`.
The receipt digest makes an unswept TOML change fail the image build, which is the enforcement the
"CI test" wording lacked.

## 15. Review loop 2 disposition

Review: `.omc/research/ingestion-grill-20260926/critic-track-review-2.md` (oh-my-claudecode:critic,
opus, fresh context; CHANGES-REQUIRED, 4 new HIGH). Every id has one row. "Where" names the spec
section, the plan section and the `metadata.json` slice that carry the change. All evidence was
re-read at HEAD `85c4b8f4`. Rows superseded by loop 3 are marked and point at §16.

### 15.1 New findings

| id | sev | disposition | what changed | where |
|---|---|---|---|---|
| N1 | HIGH | **accepted — move, no exemption.** Confirmed: `tests/test_layer_import_contract.py::SIBLING_MODULE_DIRECTORIES` includes `pipeline/lanes`, and `_lane_names` makes `strategy.py`, `registry.py`, `transforms/` and each `<layer>/` a lane. | The Protocols and value types move to `pipeline/runner/contract.py` and the resolver to `pipeline/runner/resolve.py`; nothing shared stays in `pipeline/lanes/`. The precedence transform stays at `pipeline/lanes/transforms/precedence.py` because only `importlib` reaches it. Justification: the test's default-deny design and its docstring ("shared needs move DOWN the lattice"); `pipeline/runner/` is not sibling-policed and sits in the `pipeline` layer. | spec S14, §1, §4.2, FR-3; plan 1B; metadata `f1-runner` owns (drops `pipeline/lanes/{__init__,strategy,registry}.py`) |
| N2 | HIGH | **accepted — the critic's finding; the brief's preferred variant (import-time TOML synthesis) rebutted in favour of a checked mirror (§15.4).** Confirmed: `parquet_ops/authorized_serving.py` raises "the lane is not registered" for slugs outside `_LANES`; `parquet_ops/coverage.py::registered_census_lanes` iterates `LANE_REGISTRATIONS`. | S18: `f1-registry-bridge` creates `pipeline/parquet/config_stream_registrations.py` (data rows) + splicing in `lane_registry.py` (+ `CALENDAR_HISTORY_FLOOR`) + a TOML parity test. `w3` appends `water-gauges-daily`; `p4-contract-freeze` appends the 24 climate streams. The catalogue test pins the legacy set literally and derives the TOML set, so a disabled TOML needs no test edit; `w3` still owns `test_lane_catalogue.py`, `test_lane_contract.py` and `test_job_executor_service.py` edit-if-needed, on sequenced chains. | spec S18, FR-25, §4.4 catalogue, §4.5, §6.1, §7a; plan 1F, Phase 3, Phase 4; metadata `f1-registry-bridge`, `w3-water-gauges`, `p4-contract-freeze`, `sequenced_co_owners` |
| N3 | HIGH | **accepted** (loop 3 found it partial; completed by P1–P5, P8 in §16). Confirmed: `SoilForwardConfig.request_budget` = 32 × (1 + 4) = 160 requests; the lane spec passes no `--max-days`; ≤ 7,840 weighted per run. Also found: repair runs at `--max-days 5` = 288 requests (NEW-3). | G0 (FR-24): `SOIL_MAX_DAYS` 1, budget 33, per-run edge probe. S19: probe-gated forward fan-out + per-mode per-turn caps for config lanes, with FR-5 tests. §6.3 recomputed at ≥ 1.0 per location per request, with a soil gap-fill line (≤ 48,000/month), arithmetic shown, and P5 re-baselining. | spec S19, §4.3 step 2–3, §4.4 G0, FR-4, FR-5, FR-24, §6.3, A2, A13, A15, A16; plan Phase 0 (G0, P5); metadata `g0-soil-cap` |
| N4 | HIGH | **accepted — quiescent pushes.** Confirmed by memory `plantgeo-shared-checkout-receipt-coordination` (the receipt digests the whole tree and refuses unstaged bytes). **The worktree variant this row first adopted was rejected by the owner in round 3 (O-R3-3):** `p4-extract` runs sequentially after G4 in the shared checkout. | S17. G0 lands before Phase 1 starts (`f1-config` depends on `g0-soil-cap`), so the "early soil-cap push during Phase 1" collision disappears. `p4-extract` depends on `w3-water-gauges` and launches only after G4's push; `p4-contract-freeze` and the swarm follow it. Rollback reverts wait for a quiescent point; the ledger brake covers the gap. | spec S17, §3.5, §4.7, A17; plan working rule 9, Phase 4; metadata `p4-extract` `depends_on` + note, tripwires |
| N5 | MED | **accepted.** Confirmed: the registration's `history_floor` is 2026-05-24; `_MEASURED_COMPLETE_HISTORY_FLOORS["water-gauges"]` is 1990-09-30. | The re-pull floor is named: `_MEASURED_COMPLETE_HISTORY_FLOORS["water-gauges"]` = **1990-09-30**; TOML `[days] floor`; G4 is conditional on a history-depth row showing no owed day in [floor, edge]. | spec §7a, FR-18, A14, §12; plan Phase 3 G4; metadata `owner_go_steps.G4`, `w3-water-gauges` task |
| N6 | MED | **accepted** (loop 3: an unowned G0 test broke at G1; fixed by P4 in §16). Confirmed: `execution/gap_repair_contract.py::REPAIR_BINDINGS` binds every `CLIMATE_FIELD_PRODUCTS` stream; `lane_specs.py`'s climate `_spec` uses `CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS`. | `f1-legacy-bridge` removes all 11 legacy climate and all 8 soil bindings; `f1-executor` sets the climate `_spec` lag to `CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS` and pins it. | spec §4.4, FR-21, §7 climate row; plan 1D, 1E; metadata `f1-legacy-bridge`, `f1-executor` |
| N7 | MED | **accepted, widened.** Confirmed at the cited symbols. A grep of `src/` found 31 files naming `climate-field-`, and 4 sites that read the water *stream* (the reader, the slider capability `parquetLanes`, the alert source `warehouse:water-gauges`, the attribution row) plus the about-page source term. | `p6-climate-rebind` owns `src/lib/regional-intelligence.ts`, `src/lib/server/services/regional-analysis-workflow.ts`, `src/lib/map/layer-region-binding.ts`, both region manifests' soil-wetness entries and every other `climate-field-*` web file (listed in metadata). `w3` owns the water attribution in `parquet-trpc-readers/shared.ts` and the about-page source term, flipped with the stream constant at G4. | spec §4.5, §7a G4; plan Phase 3, Phase 6; metadata `w3-water-gauges`, `p6-climate-rebind` |
| N8 | MED | **accepted — swap.** Confirmed: `lane_specs.py` and `gap_repair_contract.py` import `pipeline/direct/*` modules that `d7-legacy-lane-modules` deletes. | `d7-legacy-shared` runs first (removes imports, entries, literals), then `d7-legacy-lane-modules` deletes the packages. | spec FR-20; plan Phase 7; metadata `d7-*` `depends_on` |
| ORCH-N3 | note | **accepted.** | O10 and §6.3's weight rule budget ≥ 1.0 per location per request; the pricing capture is cited; P5 measures real usage (with A16's caveat that the portal may not exist yet). | spec §3.4, §6.3, A2, A16; plan Phase 0 P5 |
| OQ | owner | **answered by the owner (O8).** | G0 before G1 and before the key; A13 restated at the true post-G0 ceiling. | spec §3.4, A13 |

### 15.2 LOW findings

| id | disposition | what changed | where |
|---|---|---|---|
| LOW-a §10 cites round 1 at §3.3 | accepted | §10 names round 1 (§3.1), round 2 (§3.3), loop 2 (§3.4) and round 3 (§3.5). | spec §10 |
| LOW-b land-context/crop-cover protocols stay in `pipeline/direct/**` | accepted (loop 3: partial; completed by P6) | `p4-extract` moves `CropCoverSource`/`USDA_CROP_COVER_SOURCE` and `LandContextSource`/`BLM_LAND_CONTEXT_SOURCE`, plus the `source.py`/`products.py` they import, to `pipeline/lanes/{crop_cover,land_context}/` (shims left behind; the shadow lanes' legacy modules stay) and re-points `pipeline/source_bindings.py`. | spec FR-23, §7 rows; plan Phase 4; metadata `p4-extract`, `s-land-context`, `s-crop-cover` |
| LOW-c the early soil-cap push reused G1's go | accepted | It is gone. G0 is its own gate row with its own review and go (O8). | spec §3.4, §4.4; plan gates; metadata `owner_go_steps.G0` |
| LOW-d `.dockerignore` comment enumerating the digest set will rot | accepted | `f1-config` owns `.dockerignore`: the enumeration becomes a pointer to `scripts/quality_receipt.py::DIGEST_DIRECTORIES`/`DIGEST_FILES`; FR-1's static test asserts no pattern excludes a digest input. | spec S13, FR-1; plan 1A; metadata `f1-config` |
| LOW-e co-ownership vs "a path in two slices voids the wave"; `shared_writes[].owner` held chain strings | accepted (loop 3: partial; completed by P9(i)) | Every `shared_writes[].owner` is a single slice id; `i-coordinator` is now a real slice object; the full order sits in `partitions.sequenced_co_owners`, one row per multi-owner path with its `depends_on` proof. | metadata `partitions` |
| LOW-f `tests/scripts/test_quality_receipt.py` unowned (C1 residue) | accepted | `f1-config` owns it; it pins the new `DIGEST_DIRECTORIES` tuple including `"lanes"`. | spec S13, FR-1; plan 1A; metadata `f1-config` |

### 15.3 Loop-1 partial dispositions, closed

| id | loop-2 status | closed by |
|---|---|---|
| C1 | resolved except one unowned test | LOW-f |
| H1 | partial (registration, web consumers) | N2 (S18, FR-25), N7 |
| H4 | partial (budget understated) | N3 (G0, S19, §6.3), then P1–P5, P8 (§16) |
| H6 | partial (strategy layout vs the import test) | N1 (S14), then P6 (§16) |
| M4 | mostly (floor ambiguous) | N5 |
| M5 | half-applied (10 non-shortwave streams still repairable; lag line unowned) | N6 |

### 15.4 Rebuttals, with evidence

- **N2, the brief's preferred variant (synthesise `LaneRegistration`s from TOML).** Not taken.
  `LANE_REGISTRY` is read at import time by `execution/lane_specs.py` (every `_registration("…")`
  call in the lane table), by `parquet_ops/authorized_serving.py::_LANES` (a module-level dict
  comprehension) and by `lane_registry.py::CALENDAR_HISTORY_FLOOR`. Synthesising from TOML there
  would read the lane directory at import, which the track's own tripwire forbids ("the TOML loader
  and region are read by a function at ingress, never a module-level snapshot"; memory
  `plantgeo-manifest-moves-must-be-lazy`). The mirror keeps the TOML as the authored fact and makes
  drift a test failure; converting to synthesis is a named follow-up (§13).
- **N3's "Phase 0 measures real usage from the Open-Meteo dashboard".** Amended, not dropped: the
  captured pricing page says "a usage statistics portal is under development. Until it is
  available, monthly limits are not enforced." P5 reads the portal if it shows usage; otherwise
  FR-12's self-accounting and the provider's 80/90/100% emails are the meter (A16).
- **N3's "one-location edge probe", for legacy soil only.** G0's probe asks for two locations, not
  one: a one-location Open-Meteo answer is a bare object and the legacy scaffold's
  `execution/open_meteo_lane.py::canonical_location_document` refuses any non-array body. The cost
  difference is 1 weighted call per run. The config runner's probe may use one location (FR-2's
  client accepts a bare object).
- **N4's worktree option.** *Superseded by O-R3-3:* the owner rejected the worktree; `p4-extract`
  runs sequentially after G4.

### 15.5 Found by the planner while fixing loop 2

| id | finding (evidence) | fix | where |
|---|---|---|---|
| NEW-1 | `tests/execution/test_gap_repair.py` (owned by `f1-executor`) uses `climate-field-shortwave-radiation` as its canonical repairable layer (`SHORTWAVE`, ~15 uses, incl. `REPAIR_BINDINGS[SHORTWAVE]`) and `soil-field-moisture-0-7cm` as `lane_inactive`; `f1-legacy-bridge` removes both bindings in the same wave. Already latent in loop 1's shortwave exclusion. | `f1-executor` re-bases those fixtures on `drought` and `vegetation`; the soil row expects `no_repair_binding`. | spec FR-10; plan 1D; metadata `f1-executor` task |
| NEW-2 | `foundation/region/kenya_highlands.json` and `src/lib/region/kenya_highlands.ts` exist; a required `analysis_lattices` field would break them, and they list `climate-field-soil-wetness-*`. | `analysis_lattices` is optional (FR-22); `p6-climate-rebind` removes kenya's soil-wetness entries at G8. | spec §2.1, §4.1, FR-22; metadata `f1-config` reads_only, `p6-climate-rebind` |
| NEW-3 | Legacy repair runs soil at `--max-days 5` (`REPAIR_BINDINGS` ← `SOIL_MAX_DAYS`), authored every 6 h (`DEFAULT_REPAIR_INTERVAL_SECONDS`): up to 56,448 weighted/day pre-G0, absent from loop 1's budget. | G0's `SOIL_MAX_DAYS = 1` caps repairs too; §6.3 counts ≤ 4 repair runs/day until G1 excludes soil. | spec §4.4, §6.3 |
| NEW-4 | A one-location Open-Meteo answer is a bare object, but `execution/open_meteo_lane.py::canonical_location_document` refuses any non-array body. | The G0 probe asks for **two** cells (2 weighted calls); the config provider client (FR-2) accepts a single-location body. | spec §4.4, FR-2, §15.4; plan G0 |
| NEW-5 | `lane_registry.py::CALENDAR_HISTORY_FLOOR` is `min(history_floor)` over the literal registrations and its `floor_basis` text names fire-detections; new streams with floors of 1990 and 1984 key to days the calendar lacks. | The mirror participates in the floor and the text names the lane that sets the minimum (FR-25); the calendar's next version carries the earlier days, and A19 covers any reader that needs them sooner. | spec S18, FR-25, §7a, A19; plan 1F, gates |
| NEW-6 | A rule "every weighted-provider lane firing > 1/day must probe" would wrongly bind weather-observations (`write_and_recheck`, rows per tick). | The rule binds only settled (`refuse`) lanes (S6, FR-1). | spec S6, §4.1, FR-1, §7 |

## 16. Review loop 3 disposition

Review: `.omc/research/ingestion-grill-20260926/critic-track-review-3.md` (oh-my-claudecode:critic,
opus, fresh context; CHANGES-REQUIRED, 2 HIGH + 5 MEDIUM, "G0 NOT READY"). One row per id; P9's five
parts have one row each. All evidence was re-read at HEAD `85c4b8f4`. The next review is a targeted
re-check of these rows and the G0 section only (O-R3-2).

### 16.1 Loop-2 dispositions the review marked partial

| id | loop-3 status | closed by |
|---|---|---|
| N3 | partial (cap design holds; §6.3 arithmetic verified) | P1, P2, P3, P4, P5, P8 |
| N6 | partial (a G0 test broke at G1) | P4 |
| LOW-b | partial (moved protocols' own imports stayed behind) | P6 |
| LOW-e | partial (`i-coordinator` not a slice object) | P9(i) |
| H4, H6 | partial via N3 / P6 | P1–P5, P6 |

### 16.2 New findings

| id | sev | disposition | what changed | where |
|---|---|---|---|---|
| P1 | HIGH | **accepted (owner O-R3-1).** Confirmed: `probe_soil_edge` runs in `run_soil_forward`, outside the adapter, so an escaping exception reaches `forward.py::main` → exit 1 → up to 5 in-bucket retries → a hold after 3 failed buckets. | The probe never raises for an upstream or body fault. `probe_status = "unavailable"` (transport failure after `MAX_FETCH_ATTEMPTS`, refused or non-array body, `ValueError` from `canonical_location_document`, `SoilTimeBudgetExhaustedError`) or `"deferred"` (429): the window is gated, older owed days are walked, exit 0. Only an unresolvable probe cell (a changed support) raises. Two tests: `test_an_unavailable_probe_gates_the_window_and_the_run_still_completes` (parametrised over the four causes) and `test_a_throttled_probe_is_deferred_and_the_run_exits_zero`. | spec §3.5, S19, §4.3, §4.4 status table, FR-5, FR-24, A15, §12; plan G0 brief §4, §7; metadata `g0-soil-cap` |
| P2 | MED | **accepted.** Confirmed: `source.py::build_soil_day` raises `SoilSourceUnsettledError` on ≠ 1,470 values; `adapter.py::DirectSoilFieldAdapter.__call__` wraps every `SoilSourceError` except `SoilProviderDeferredError` as `DirectSoilFieldError`; `_publish_locked_day` retries from the in-memory cache and raises. | FR-24 and S19 now say it: a thin fan-out raises `DirectSoilFieldError` and the run exits 1 (unchanged legacy behaviour, the A15 episode). The cap test's thin case is its own test, `test_a_thin_fanned_out_day_raises_after_at_most_thirty_three_requests`, which expects the raise with exactly 33 fake calls. | spec S19, §4.4, FR-24, A15, §13; plan G0 brief §7 |
| P3 | MED | **accepted; option chosen: `edge_probe=None` means ungated.** Confirmed: `frontier_turn` drives `_publish_product` with a stub that increments `requests_spent` itself, and no test drives `run_soil_forward`. | Run-level tests drive `run_soil_forward` across all eight products through the review's seam: monkeypatch `forward.settings.require_local_source_loader_database_url`, `forward.local_source_loader_session`, `forward.ObjectStore.from_settings`, `forward.BotoAvailabilityStorage.from_settings`, `forward.load_era5_land_support` (plus `forward._tier_status_window`, `forward.postgres_lane_day_lock`, `forward._retry_owed_availability`, `forward.SourceResponseCheckpoints` and `asyncio.sleep`), and count every HTTP attempt at `source.fetch_archive_daily`. `_publish_product` gains `edge_probe: SoilEdgeProbe \| None = None` (None = ungated, the walk `invalid` uses), so `frontier_turn` and the four frontier tests, **including `test_a_provider_deferral_is_not_treated_as_an_unsettled_frontier`, stay unchanged**. | plan G0 brief §5–§7 |
| P4 | MED | **accepted.** Confirmed: G0's `test_a_soil_repair_run_inherits_the_one_day_cap` would `KeyError` once `f1-legacy-bridge` drops soil from `REPAIR_BINDINGS`; `tests/direct/soil/test_lane_registrations.py::EXPECTED_SCHEDULE` and `tests/test_job_executor_service.py::EXPECTED_SCHEDULES` pin `"50 * * * *"`. | (i) The test is **dropped from G0**. G0's `test_max_days_is_one_so_every_invocation_fans_out_at_most_one_day` rejects `--max-days 2` at the parser, which is what bounds a repair's `--max-days`. After G1, `f1-legacy-bridge`'s `test_legacy_repair_exclusions.py` pins soil → `no_repair_binding`. (ii) **The schedule string changes** to `"50 */6 * * *"` (cadence 21,600 s, phase offset 3,000 s unchanged). `f1-executor` owns both pins, with co-owner rows `f1-executor` → `d7-legacy-lane-modules` for `test_lane_registrations.py` (g0 does not edit it; re-check P4 nit). | spec §4.4, FR-21; plan 1D, G0 brief §7; metadata `f1-executor` owns, `sequenced_co_owners` |
| P5 | MED | **accepted.** Confirmed: `requests_spent` counts the 2-location probe and the 18-cell chunk as 1 each; `fetch_lane_capture` makes up to 4 uncounted HTTP calls per request. | G0 adds `SoilSourceCache.weighted_calls` (charged per started request with `open_meteo_request_weight(locations, days, variables)`: 50 or 18 per chunk, 2 for the probe) and `SoilSourceCache.http_attempts` (a counting wrapper around `fetch_archive_daily`), both in the report. FR-24 reads "≤ 33 logical requests (≤ 1,570 weighted), ≤ 132 fetch attempts (≤ 1,584 HTTP requests incl. transport retries and redirect hops)". | spec §4.3 step 8, §4.4, FR-12, FR-24, §6.3, A16; plan G0 brief §3; metadata `g0-soil-cap` |
| P6 | HIGH | **accepted — move the transitive closure** (not a d7 exclusion). Confirmed by grep: `burn_severity/mtbs.py` → `burn_severity/products.py`; `drought/usdm.py` → `drought/products.py`; `evacuation_zones/watermark.py` → `products`, `rows` (→ `support`), `source`; `watersheds/watermark.py` → `source.py`; `crop_cover/source_protocol.py` → `source.py` → `products.py`; `land_context/source_protocol.py` → `source.py` → `products.py`. None of those leaf modules imports further `pipeline/direct/**` code. | `p4-extract` owns and moves the closure to `pipeline/lanes/<layer>/`, leaves shims, and re-points monkeypatch targets in the legacy tests that name a moved attribute (edit-if-needed; `d7-legacy-lane-modules` deletes those tests later). No `pipeline/lanes/**` module imports `pipeline/direct/**`, and d7's "grep importers first" finds none. | spec FR-23, §7 rows; plan Phase 4; metadata `p4-extract` owns, `sequenced_co_owners` |
| P7 | MED | **accepted.** | Clearing `OPEN_METEO_API_KEY` is **mandatory before any G0 revert**. A persistent `invalid`/`blind`/`unavailable` gets a probe fix, never a revert. The G0 observation accepts `deferred` on the free tier (A20). The old "`probe_status ≠ ok` for 24 h → revert" trigger is removed. | spec §4.7, A20; plan G0 brief §9, rollback |
| P8 | LOW | **accepted.** | The soil census hole count (owed days older than the probe window) is recorded as a G0 evidence row **before the key is set**, and refreshed at G6. Every "realistic" figure is qualified: 1,616/day (1,576 after G1) holds only once that backlog is drained, at one 1,570 fan-out per run. | spec §6.3, A13; plan G0 brief §9, Phase 5 G6 |
| P9(i) | LOW | **accepted.** | `i-coordinator` is now a slice object (owns only the four coordination files; `depends_on: []`; never launched as an author), and `integration_slice` names it. | metadata `partitions.slices`, `shared_writes` |
| P9(ii) | LOW | **accepted.** | G0 owns three soil passages of `pipeline/direct/AGENTS.md`: the whole soil `### Entry point` subsection, the soil budget sentence of "One distinct day per turn", and the weight paragraph of "One archive request per support chunk-day" (NEW-7). `f1-legacy-bridge` owns `pipeline/direct/climate/AGENTS.md` and the NASA POWER `### The request budget` subsection (794 → 397; NEW-9). Chain: `g0-soil-cap` → `f1-legacy-bridge` → `d7-legacy-shared`. | plan G0 brief §8, 1E; metadata `g0-soil-cap`, `f1-legacy-bridge`, `sequenced_co_owners` |
| P9(iii) | LOW | **accepted.** | Soil repair items authored before G0 carry `max_days: 5` and fail `RepairRequest.__post_init__` as `invalid_repair_request` after the deploy. This is expected in the G0 observation and is not a rollback trigger. | spec A20; plan G0 brief §9 |
| P9(iv) | LOW | **moot (O-R3-3).** | With no worktree there is only one `QUALITY_RECEIPT.json`, written by the monitor on `main`, so no rebase can make it pick a side. | spec S17 |
| P9(v) | LOW | **accepted.** | The G0 brief names `Era5LandSupport.resolve(longitude, latitude)` as **lon-first**, and warns that `archive_daily_request` takes points **lat-first** (`(cell.cell_latitude, cell.cell_longitude)`, as `source.py::_fetch_chunk_day` builds them) (NEW-8). | spec A18; plan G0 brief §2 |
| OQ-3 | owner | **answered (O-R3-1).** | Skip + exit 0, as recommended. | spec §3.5 |

### 16.3 Rebuttals

None. P3 offered two fixes and the plan takes the second (`edge_probe=None` ungated), because it
keeps `test_a_provider_deferral_is_not_treated_as_an_unsettled_frontier` and the three frontier walk
tests valid as the pinned behaviour of the `invalid` fallback. P6 offered two fixes and the plan
takes the first (move the closure): excluding modules from d7 would keep a `pipeline/direct/**`
import inside `pipeline/lanes/**`, which the tripwire forbids.

### 16.4 Found by the planner while fixing loop 3

| id | finding (evidence) | fix | where |
|---|---|---|---|
| NEW-7 | `pipeline/direct/AGENTS.md` "One archive request per support chunk-day" says Open-Meteo "weights a request by locations x variables x timesteps" and prices a soil day at "1,568 x 8 x 1 = 12,544 weighted units". This contradicts the captured pricing page, where ≤ 10 variables and ≤ 14 days cost 1.0 per location, i.e. 1,568. | G0 rewrites that paragraph to the O10 rule and cites the capture. | plan G0 brief §8; metadata `g0-soil-cap` |
| NEW-8 | Coordinate order differs between two calls the probe needs: `Era5LandSupport.resolve(longitude, latitude)` vs `ingest/open_meteo.py::archive_daily_request(coordinates=[(lat, lon), …])`. | Both orders are spelled out in the G0 brief, and a test asserts the probe URL's `latitude=45.375,45.375`. | plan G0 brief §2, §7 |
| NEW-9 | `pipeline/direct/AGENTS.md` NASA POWER `### The request budget` states `397 x --max-days x 2` = 794; after the G1 shortwave drop, the legacy writer has one clock. | `f1-legacy-bridge` updates that subsection. | plan 1E; metadata `f1-legacy-bridge` |
| NEW-10 | The soil `### Entry point` in `pipeline/direct/AGENTS.md` says the lane "SHIPS IN SHADOW" and "the archive needs no key"; `execution/lane_specs.py`'s soil description says it is ACTIVE in production, and the key moves it to the paid host. | G0 rewrites the whole subsection and states no cadence (it points at `execution/lane_specs.py`), so G1's cadence change needs no doc edit there. | plan G0 brief §8 |

## 17. Targeted re-check disposition (loop 3, owner O-R3-2)

Record: `.omc/research/ingestion-grill-20260926/critic-track-review-3-recheck.md`. P1–P9 verified or
partial there; the partials (P3, P5, P9(v)) close through R2, R4 and R3 below. Every fix is
brief text, applied by the coordinator on 2026-09-26; the reviewer ruled no further loop needed.

| id | sev | disposition | where |
|---|---|---|---|
| R1 | MED | **accepted.** Probe step 1b calls `require_time_remaining(deadline, day=window_last)` before charging; the deadline test case becomes `time_budget_seconds=10` + one `UpstreamError` (the 15 s backoff outlasts the budget in `deadline_bounded_sleep`). | plan G0 brief §4, §7 |
| R2 | MED | **accepted.** Settings seam patches `forward.settings.__class__` with `lambda _self: …`; both `from_settings` use `classmethod(lambda _cls, _source=None: …)`, per `tests/direct/climate/test_forward_command.py:727-737` (added to read-only). | plan G0 brief §1, §6 |
| R3 | MED | **accepted.** Probe-cell arithmetic in `Decimal`, `float(...)` passed to `Era5LandSupport.resolve`. | plan G0 brief §2 |
| R4 | LOW | **accepted.** 1,602 weighted is the hard per-run bound, 1,570 the clean fan-out; ceilings rescaled. | FR-24, §4.4, §6.3 correction row, plan G0 goal/§3/worst case, gate row G0, metadata |
| R5 | LOW | **accepted.** Catch `SoilProviderDeferredError` before `SoilSourceUnsettledError`. | plan G0 brief §4 |
| R6 | LOW | **accepted.** Exact null sets per harness case; probe `valued_days` ⊇ every census `data` day in the window; `_skipped` carries the new keys. | plan G0 brief §5, §6 |
| R7 | LOW | **accepted.** `source.py:81-83` comment and the `request_budget` docstring join G0's edits. | plan G0 brief §2 |
| R8 | LOW | **accepted.** "The probe adds one exit-1 path; legacy exit-1 paths are unchanged." | §4.4, plan G0 brief §4, metadata tripwire |
| P4 nit | — | **accepted.** §16 P4 row no longer names g0 as a co-owner of `test_lane_registrations.py`. | §16 |
| UNVERIFIED rollback | — | **accepted as a rule:** no executor build without G0 while the key is set, including a Railway dashboard rollback; clear the key first. | plan G0 brief §9 |
| Plan tier (flagged) | — | **owner precondition:** confirm the key's Open-Meteo tier covers the Historical/archive API before setting it. | plan G0 brief §9 |
