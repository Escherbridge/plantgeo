---
type: Track Spec
title: Config-driven ingestion — one TOML per lane, thin strategies, one runner, cron runtime, LayerService serving
description: Cap legacy soil first (G0, before the paid Open-Meteo key); then ship an observability and soft-failure wave (Wave O, GL-1–GL-5) so every lane logs one redacted contract, every upstream send is metered, and no failure breaks a lane permanently or reaches another lane; then replace per-lane forward modules and scattered lane facts with typed lane TOML, a strategy Protocol and a shared runner; unify forward and gap-fill; re-source climate meteorology to Open-Meteo ERA5 + IFS; move water gauges early to USGS daily values; cut over the remaining lanes by a reviewed swarm, then retire the old path through a dedicated supersession cleanup lane that fires only on verified readiness and deletes code, never data.
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
- **Revised a fourth time 2026-09-27** for the owner request of 2026-09-26, a dedicated cleanup
  lane: §4.8 defines "verified ready to supersede" and the lane, FR-26–FR-29, NFR-8, A21–A22 and
  R4–R8 are new, FR-20 is restructured, and §18 records the request, the design rounds and the
  owner questions CQ-1–CQ-12. Design record:
  `.omc/research/ingestion-grill-20260926/cleanup-lane-design.md` (revision 2).
- **Revised a fifth time 2026-09-27** for the owner's second request of 2026-09-26, an
  observability and soft-failure wave (Wave O): §3.6 records the owner answers WQ-1–WQ-7 and the
  publication-debt alignment, §4.9 defines the wave, FR-8 and §4.4 change (holds re-attempt by
  themselves), FR-30–FR-38, NFR-9–NFR-10, A23–A26 and R9–R12 are new, and §19 records the request
  and the design rounds. Design record: `.omc/research/ingestion-grill-20260926/observability-wave-design.md`
  (revision 2, not yet re-reviewed as a whole).
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

**Second, before the framework: Wave O (§4.9).** Five small pushes (GL-1–GL-5) give every lane one
redacted JSON logging contract, meter every upstream send and attribute it to a turn, publish a
read-only source-usage report, and record every failure on an incident row so that no failure fails
a tick, stops another lane, or leaves a lane without a defined way out. Self-probing holds, the paid
Open-Meteo cap and a monthly usage receipt follow at G1 (owner request 2026-09-26, §19; answers
§3.6).

Two source changes ride on the framework:

- **Climate re-source:** meteorology moves to Open-Meteo, with ERA5 as the settled data and ECMWF
  IFS as a provisional tail ending **yesterday UTC**, on the 0.25° analysis lattice. Solar stays on
  NASA POWER UTC, re-gridded onto the lattice, with an IFS tail. POWER soil wetness is retired.
- **Early water-gauges move:** the lane moves to USGS modern Water Data **daily values**. It serves a
  daily mean with a real history re-pull to 1990-09-30, ahead of the batch cut-over.

**Last: a dedicated supersession cleanup lane (§4.8).** Each migrated lane, and finally the whole
legacy path, is retired only once it is **verified ready to supersede** its legacy counterpart
(READY(L), §4.8.2). The lane quarantines, observes, then deletes; closing the rollback window is its
own owner go; it removes code, tests, config, env vars, crons, docs, skills and memory residue, and
deletes no data (owner request 2026-09-26, §18).

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

### 3.6 Owner answers, observability wave (SETTLED 2026-09-27; WQ = the design record's Q)

| # | answer | where it lands |
|---|---|---|
| WQ-1 | **YES: holds re-attempt by themselves on a bounded ladder.** Code, hang and config holds probe at 6 h, 12 h, 24 h, then daily; upstream and infra holds keep FR-8's 1, 2, 4, 8, 16, 24 h, then daily. A probe never fires more often than the lane's cadence and is one attempt. Probation is bounded, episodes chain, and a flapping alarm fires. `SOFT_FAILURE=off` or `CODE_PROBE_HOURS=""` restores today's behaviour. | FR-8, §4.4, §4.9.3, FR-36; plan 1D |
| WQ-2 | **YES (default, not asked):** the evidence classes R1–R4 drive the upstream ladder for legacy non-zero exits; anything ambiguous is `code`. | §4.9.3, FR-8, FR-36 |
| WQ-3 | **GL-1–GL-5 ship as small pushes after G0's 24 h observation and before Phase-1 authoring.** GL-6 (the ladder) is **folded into `f1-executor`** and ships at G1. Launch waits for a quiescent shared tree. | §4.9, plan Phase 0W, 1D; metadata partitions |
| WQ-4 | **Enforce only the paid monthly Open-Meteo cap**: 5,000,000 a month; forward stops at 95 % of charged spend; gap-fill is admitted below 60 %. The pools-and-reserves proposal (the `open-meteo-free`, `firms` and `usgs-water-data` pools and reserves, and the interim free-pool brake) is **declined**. Usage metering and the report stay. | §4.9.2, FR-37, §13 |
| WQ-5 | **Default:** User-Agent `plantgeo-agri-data-service/<version>+<sha7>`, no contact unless `PLANTGEO_UPSTREAM_CONTACT` is set; NWS and MTBS keep their own required formats. | §4.9.2, FR-32 |
| WQ-6 | **YES at G1:** a durable monthly `receipts/source-usage/<YYYY-MM>.json` in the existing object store. | §4.9.2, FR-34 |
| WQ-7 | **NO (default):** no deploy-time probe of held lanes; the 2026-09-18 opt-in deploy release stands. | §4.9.3 |
| PD | **Publication debt merges now** (owner 2026-09-27; a merge is in flight): `execution/job_executor_service.py::TurnReport.incomplete` also counts `publication_debt`. Wave O reads such a turn as `ok`/`incomplete` (reason `publication_debt`), keeps the merged metric, and launches only after the merge lands. | §4.9.3, A25, A26 |

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
  `f1-executor` file in Phase 1 (review H6). **The allow-list half is delivered early, at GL-5**
  (`o2b-incidents`/`o5b-soft-failure`, §4.9.3: unknown and non-executable ids are quarantined and
  `active_lanes` excludes them); `f1-executor` gives the kill-switch the same rule.
- **Cron** (`execution/cron_schedule.py`) applies to config lanes only. Legacy lanes keep
  cadence/phase until they flip.
  - Due = the latest fire ≤ now with no settled run for it.
  - `coalesce_latest` and `replay_oldest` keep their meaning.
  - A never-run lane waits for its next fire.
  - A gap-fill fire is its own definition, `<lane>:gap-fill`.
- **Work queue** (S15) with runtime switches (S16).
- **Breaker split** on S4, revised for WQ-1 and WQ-2 (§4.9.3). GL-5 records every hold on one
  incident row per episode; `f1-executor` adds native exits and the ladder at G1:
  - Upstream class (`75`, and a legacy non-zero exit whose report carries R1–R4 upstream or infra
    evidence): single-attempt half-open probes at 1 h, 2 h, 4 h, 8 h, 16 h, 24 h, then daily, and
    auto-release through probation. One `agri.job_incident` row per hold episode.
  - Code class (`70`, `78`, a timeout, and a legacy non-zero exit without evidence): a hold and one
    open `agri.job_incident` row shown in `/admin/jobs`, **re-attempted by single-attempt probes at
    6 h, 12 h, 24 h, then daily** (`PLANTGEO_JOB_EXECUTOR_CODE_PROBE_HOURS`). **No webhook, no
    email (O4).** No per-tick repeated error line.
  - No probe fires more often than the lane's cadence. Probation ends after 2 conclusive clean
    buckets or 48 h; a re-open within 7 days chains the episode; ≥ 3 episodes in 7 days log
    `hold_flapping` once; a lost probe never advances the rung. No deploy-time probe (WQ-7).
  - `SOFT_FAILURE=off` restores today's operator-only holds; `CODE_PROBE_HOURS=""` keeps code holds
    operator-only; `BREAKER_MODE=legacy` subsumes both.
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
- **Rollback of Wave O (§4.9):** an owner-set switch (`PLANTGEO_JOB_EXECUTOR_SOFT_FAILURE`,
  `PLANTGEO_JOB_EXECUTOR_CODE_PROBE_HOURS`, `PLANTGEO_UPSTREAM_TELEMETRY`, `PLANTGEO_LOG_ROUTING`) or
  a revert commit (sweep, receipt, go). Triggers: any secret hit, a lost terminal report, a router
  regression, a probe storm, `meter_errors > 0`, or a lane stuck in a state without a transition.
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
  this rollback window is finite. Whether the cleanup lane may end this promise before Q1 2027 is
  CQ-6, answered at 7R before water joins any cohort (§4.8.6, §18.5).
- **Window end (CA16).** Every rollback above holds from G0/G3/G6 **until that lane's 7Q-n push**
  (§4.8). After 7Q-n the lane rolls back by §4.8.6 rule 3 (revert 7Q-n before any executor flip).
  After its G9D-n go the default is fix forward. Plan §Rollback is marked HISTORICAL at G9S.

### 4.8 Supersession cleanup lane (owner request 2026-09-26; §18)

Phase 7 is a dedicated **cleanup lane**. It fires for a lane only once that lane is **verified
ready to supersede** its legacy counterpart (READY(L), §4.8.2), and for the whole legacy path only
once WHOLE-PATH-READY holds (§4.8.5). It removes what the legacy path **and this track's own
coexistence and rollback scaffolding** leave behind: code, tests, config, env vars, ledger state
(soft-retired, never deleted), crons, docs, skills and memory. It keeps what the owner decisions
keep: legacy `climate-field-*`, `soil-wetness-*` and `water-gauges` history stays readable, soil
wetness stays retired, and **the lane deletes no data of any kind** (tripwire c7-1; every purge is
G9X, never scheduled).

The spine is **risk-first-staged** (judged 38 vs 36 vs 32; §18.1). Three kinds of step stay apart:

| step | what it does | what it can break | gate |
|---|---|---|---|
| quarantine (7Q-n) | takes shared entries out of the executor: the repair subsystem first (7Q-1a), then a cohort's specs and ids (7Q-1b, 7Q-2) | dispatch | G9Q-1a, G9Q-1b, G9Q-2 |
| delete (7D-n) | removes code nothing imports any more | only the sweep | G9D-n |
| close the rollback window | ends §4.7 rollback for the named lanes | the right to revert | G9D-n, its own owner go |

- **Cohort 1:** the 10 class-A lanes, water gauges (only with CQ-6 acknowledged at 7R),
  `mtbs-forward` and the CQ-10 names. Static_lookup lanes without a config-path write (§4.8.3), and
  burn-severity under a CQ-9 carve-out, move to a later cohort. **Cohort 2:** climate. Any
  non-default cut at 7R is a partition revision.
- **Seven pushes:** G9.0, G9Q-1a, G9Q-1b, G9D-1, G9Q-2, G9D-2, G9S, each with one monitor sweep.
- Plan Phase 7 holds the flow, the per-slice tasks, the greps and the production enumeration;
  `metadata.json` → `partitions` holds the `c7-*` slices and tripwires c7-1–c7-16.
- **Id namespaces.** Cleanup amendments are `CA1`–`CA20` (the design record's A1–A20, renamed so
  they never read as the §9 assumptions A1–A20). Cleanup owner questions are `CQ-1`–`CQ-12` (the
  design record's OQ-n; §16.2's OQ-3 is a different, answered question). Greps are `G-1`–`G-14`
  (hyphenated; not gates). Slice-local task ids (V, R, P, B, RR, Q, D, S, F, C) live in plan Phase 7
  and are written with their slice outside it.

#### 4.8.1 Lane classes and when the clock starts

| class | lanes | successor | clock start T0(L) |
|---|---|---|---|
| A: migrated in place | the 10 `*-direct-forward` batch lanes | the same id on the runner | G6 deploy |
| B: replaced | `water-gauges-direct-forward` | `water-gauges-daily` | G4 deploy |
| C: re-sourced | `climate-nasa-power-direct-forward` (soil wetness is retired) | the five climate definitions from the landed TOMLs | G8 deploy, after the browser check |
| D: deleted | `mtbs-forward` | none, or the CA7 capture successor | burn-severity's T0 |
| E: shadow | crop-cover, land-context-blm-*, ndvi-promotion | none in this track | never (Q-21) |
| F: unprefixed legacy-path definitions | names the T0 census classifies `legacy-path-to-retire` (e.g. the archive walks) | none | the T0 census (CQ-10) |

#### 4.8.2 Verified ready to supersede: READY(L) = RC-1 ∧ … ∧ RC-8

Rows go in `evidence/phase7.md` §R. `i-coordinator` writes RC-1–RC-5 from the owner-run or delegated
census; `c7-readiness` returns RC-6–RC-8. **READY(L) is evidence only; acting on it still needs the
G9 gates (plan "Owner gates").**

| row | criterion | measured by | Q |
|---|---|---|---|
| RC-1 | The Phase-6 QA row passes at L's cadence-class minimum (§4.8.3). The class is computed from the landed TOML `forward_cron`. | QA row id | Q-03..Q-07 |
| RC-2 | The Phase-6 validation row passes, checked by a different reviewer, plus the §4.8.3 specifics. | validation row id | Q-11, Q-14, Q-15, Q-19, Q-20 |
| RC-3 | ≥ 14 calendar days since T0(L). | gate log | Q-30 |
| RC-4 | **The whole window is trigger-free.** Triggers: the plan §Rollback item-6 list; a breaker hold or open non-info incident on L; any `L:gap-repair` run after T0; **an unaudited `enabled` change on a braked or pinned name** (`jobs-lane-census` `unaudited_enabled_changes`); **a burn-severity capture object whose LastModified falls outside every CA1-marked burn-severity run window**. Legacy soil runs are judged against the bounds committed in `pipeline/direct/soil/AGENTS.md`. A trigger restarts the clock when it is resolved. | `jobs-lane-census`, `prefix-census` | Q-29 |
| RC-5 | **Dormancy, config path and claims.** (a) No `:gap-repair` runs since T0. (b) Every work item since T0 under `L` and `L:gap-fill` carries the CA1 marker. (c) **Pinned names:** no non-terminal runs, items (any kind) or live leases. **Class-A cohort names:** no non-terminal item without the CA1 marker and no `gap-repair-command` item; config-marked in-flight work is allowed. A pre-T0 unmarked item still non-terminal makes L NOT READY; the owner then chooses a carve-out, or a named-item pin addition under a go. (d) **Per CA13:** the stored `job_definition` row keeps its T0 schedule and parameters digest (nothing re-registered or upserted over it), and the census's catalogue-derived runtime schedule equals the TOML `forward_cron`. (e) **No `availability/pending/` claim, owed or `.quarantined.json`, older than one fire interval under L's roots**; frozen roots hold none at all (§4.8.4). | `jobs-lane-census`, `prefix-census` | Q-12, Q-16, Q-27 |
| RC-6 | **Consumers closed.** Every G-7 hit (a **bare-token** search) is classified: (i) a UI layer key; (ii) rebound to the successor; (iii) a frozen-history read covered by the owner decision; (iv) a named request to its owning session. Scope: web `src/**`, `agri_data_service/**` outside the deletion set (`agent/**` at HEAD and in the tree), and `services/plantgeo-ml-service/src/**`. An unclassified or live-expecting read makes L NOT READY; for climate it also blocks the G9Q-2 brake. `agent/selection_scope.py::support_lattice` is covered by CA11 (p6); climate is NOT READY until its row is classified. | G-7, G-8 | Q-10 |
| RC-7 | **Importers and salvage baseline.** G-1 and G-1b find L's legacy package only in its own deletion set and the allow-list. The cross-track grep finds no other track importing it. **Every branch or worktree in the `c7-readiness` R6 census whose diff touches this cohort's deletion set has an owner disposition** (salvage-merged, abandoned, or rebase owed). | greps, R6 | Q-22 |
| RC-8 | **Amendments landed.** CA1–CA3, CA8 (authoring **and** driving), CA12, CA13 and CA20 for all lanes; CA4 at G1; CA5, CA11 (water) and CA18 for water; CA6 and CA11 (climate) for climate; CA7 for burn-severity; CA14 for shimmed lanes; CA17 for static_lookup lanes; CA19 at G6. **If CA2 or CA3 is absent, 7R stops, the partition is revised and an f1 follow-up is raised; there is no c7 fallback.** | grep + tests | D-01..D-04, C-25 |

#### 4.8.3 Cadence classes and lane specifics

| class (computed) | expected lanes | minimum inside the window | specifics that are part of RC-2 |
|---|---|---|---|
| hourly | sensors, weather-observations, fire-detections (if hourly) | ≥ 72 consecutive fires, each exit 0 with a correct S5 | sensors: `retention_exceeded` is reported. weather-observations: the `customer-*` host is named and metered. fire-detections: the 10,000 cap surfaces as `unwritten`. |
| sub-daily | soil (`50 */6 * * *`) | ≥ 72 h **and** ≥ 12 fires | No G0 shortcut. The G6 hole count holds ≤ 1,600 per turn. No RC-4 bound breach. |
| daily | vegetation, the burn-severity daily path, `water-gauges-daily`, the climate ingest lanes | ≥ 3 fires | vegetation: 2026-09-01..05 are held. **burn-severity:** ≥ 2 Friday captures; for each, the LastModified of the capture's `snapshot=` prefix falls inside `[started_at, completed_at]` of a CA1-marked run of `plantgeo.executor.burn-severity-direct-forward` (`jobs-lane-census` × `prefix-census`). Counting starts at max(T0, the `mtbs-forward` brake, census proof that it is inactive). The go records that `scripts/prepare_mtbs_current_snapshot.py` and `scripts/stage_mtbs_current_snapshot.py` were not run in the window. If CA7 returned "no successor", the CQ-9 answer governs. |
| weekly / release | drought | ≥ 2 valid-Tuesday releases | A 404 is `unsettled`, never absent. |
| **static_lookup** | evacuation-zones, fire-perimeters, watersheds | ≥ 3 fires, each exit 0 | The compare-equality row, **plus one real config-path write in production**: a watermark change written by the config lane, or one owner-approved CA17 `--republish-current` showing digest equality with the served snapshot and a new availability generation. Without either, the lane is a **carve-out** to a later cohort (CQ-8). |
| transform | `meteorology`, `shortwave` | ≥ 3 input-driven rebuilds and ≥ 1 provisional→settled prune | Q-09..Q-11 |

#### 4.8.4 RETIRE-SAFE rows

| subject | RETIRE-SAFE requires |
|---|---|
| `plantgeo.executor.water-gauges-direct-forward` | `water-gauges-daily` is READY; every §7a row passes as a conjunction; no runs since G4; the legacy stream is still registered, with coverage equal to its terminal-day receipt; **no owed or quarantined claim under `layer=water-gauges/`** (CA18 drained them at G4; otherwise they are recovered at 7R with `agri-service data availability-reconcile-physical` under a go, and the terminal-day receipt is recorded only after that); RC-6 green for water; **the CQ-6 acknowledgement recorded at 7R, before water entered cohort 1**. Without it, water never enters cohort 1. |
| `plantgeo.executor.climate-nasa-power-direct-forward` | All five climate successors READY; RC-6 green before the G9Q-2 brake; **no open claim under any of the 8 `climate-field-*` and 3 `soil-wetness-*` roots at brake + one fire interval**, including `climate-field-shortwave-radiation` (frozen since G1; its claims are recovered at 7R); no new partitions across 7O-2; a terminal-day receipt per stream, written only after its pending prefix is empty; the frozen schema modules still serve one pre-freeze day per stream. |
| `plantgeo.executor.mtbs-forward` | Its token is out and it has no runs in 30 days, or it has been braked since the G6 go; burn-severity is READY; the importer grep is clean. |
| sensor absence correction (`pipeline/parquet/sensor_absence_correction.py`, `scripts/correct_sensor_absences.py`) | SC-1: the retirement condition is recorded; SC-2: `prefix-census` finds no open `availability/repairs/` journal; SC-3: no operator workflow cites it. On failure, sensors is a carve-out for this path. |
| class-F unprefixed names (CQ-10) | The T0 census classifies each one `legacy-path-to-retire` with a reason; no code path still dispatches it (`jobs/dispatch.py::LANE_DISPATCH` and executor specs); it has no non-terminal work; a critic verdict on the classification is recorded. |

#### 4.8.5 The whole legacy path: WHOLE-PATH-READY (gates G9S)

- **WP-1.** Every cohort's G9D has pushed and been followed by 7 clean days. A laggard blocks G9S
  unless it has an explicit carve-out.
- **WP-2.** The repair subsystem is gone; `ACTIVE_LANES` ⊆ the remaining legacy `LANE_SPECS` keys;
  the CA12 kill-switch exists, is tested and is set to "run"; **`REPAIR_INTERVAL` reads `0`**.
- **WP-3.** No Railway variable remains whose only *reachable* reader was deleted (`c7-readiness`
  R5). Declaration-only files are residue, not readers. `REPAIR_INTERVAL` is exempt: it is held at 0.
- **WP-4.** The `prefix-census`, **top level included**, shows: kept legacy streams equal their
  receipts; **no owed or quarantined claim under a frozen root**; the `source-response-checkpoints/v1/`
  object count ≥ its T0 count; migrated streams advancing; no `unknown` prefix.
- **WP-5.** Docs, skills, **conductor styleguides**, memory (**mechanical G-13**), RUNBOOK and plan
  checks are clean; **`conductor/tracks.md` is flipped and the retrospective row is written**.
- **WP-6.** History row counts are ≥ their T0 values; no run of a retired name exists after its G9Q;
  no unaudited `enabled` change.
- **WP-7.** A disposition row exists for every track switch, every artifact this lane created, and
  every piece of local state (the 7S dispositions table, plan `c7-scaffold` S5).
- **WP-8.** Deny-list closure: no MIGRATED legacy package remains (c7-14), never an allow-list of
  survivors.
- **WP-9.** The R6 branch and worktree census, re-run since the last G9D, shows no legacy-path hunk
  merged back into main.

#### 4.8.6 Rollback-window closure rule

1. **Opens** at T0(L).
2. **Up to G9V-1b / G9V-2,** plan §Rollback applies; it ends at "that lane's 7Q-n push" (CA16).
3. **From G9V-n through 7O-n,** the rollback is:
   1. brake `L` and `L:gap-fill`;
   2. re-set the recorded `ACTIVE_LANES` value;
   3. if 7Q-n was pushed, revert it (`REPAIR_INTERVAL` is 0, so legacy repair cannot re-arm);
   4. flip `executor = "legacy"` (sweep + receipt + go);
   5. **for climate:** re-enable the braked legacy writer with `jobs-set-lane-enabled` (the spec is
      back after the revert); **if the census shows its latest checkpoint operator-held, run
      `agri-service ops jobs-supersede-run --lane climate-nasa-power-direct-forward --run-id <id>`**.
      A drained run is `cancelled` and never counts toward the failure streak
      (`sql/execution/select_latest_run.sql` counts only `failed`/`partial`).
4. **Closes only by a G9D-n go that names the lanes.** Water's acknowledgement was recorded at 7R.
5. **After G9D-n,** the default is to fix forward; a revert needs a new go.
6. **Dashboard rollback.** `REPAIR_INTERVAL` stays `0` permanently, so a rollback to any pre-7Q-1a
   image cannot re-arm repair. Before a rollback to a pre-7Q-1b image, the owner re-sets the recorded
   `ACTIVE_LANES`.

#### 4.8.7 Out of scope (enforced by tripwires and the FORBIDDEN lists)

- Shadow lanes; `execution/weather_observations/**`; other `pipeline/direct` lanes, including
  `vegetation_type/**`; the frozen schema modules; `warehouse/field_products/**`;
  `warehouse/mtbs_snapshots.py`; `parquet_ops/mtbs_snapshot_catalog.py`; every `layer=*/` object;
  calendar versions; credentials; `src/lib/server/services/usgs-water.ts`; the manifest-pinned
  canonical builders (re-pointed, never deleted); `scripts/purge_parquet_layout.py`;
  `forecast_module` and the ML service; served `*_basis` strings; edits to `agent/**`;
  `FIRES_LAYER_ID`.
- `jobs/lease.py`, `sql/jobs/refresh_job_run_rollup.sql` and `jobs/worker.py` behaviour (read-only
  for `c7-verbs`).
- Editing `src/lib/server/trpc/routers/jobs.ts::toggleLane` (a follow-up).
- The archive-walk history lines in `routes/AGENTS.md` and the
  `sql/routes/ops_lane_landed_evidence.sql` header (allow-listed as history).
- Docs that cite the dead `ingest-backfill` verb and predate this track, such as
  `docs/rebuilding-the-dataset.md` and `docs/unused-upstream-datasets.md` (a follow-up).
- Deleting any branch, worktree, `.agri-local-runs/` or `plans/` content, unless a CQ-7 answer
  names it.

#### 4.8.8 New ops verbs (all authored by `c7-verbs`; no schema change; no DELETE/DROP/TRUNCATE)

**Shared rules (`execution/definition_retirement.py`).**

- **Pin.** `RETIRABLE_DEFINITIONS: Final[frozenset[str]]` holds full names: the three forward names
  (water, climate, `mtbs-forward`), the six `:gap-repair` names (tripwire c7-2), and the CQ-10
  names, added by `c7-quarantine-1`. It is a literal, pinned by a test.
- **Liveness predicate `live_dispatch_reasons(name)`.** It reads runtime state, **never code
  registration alone**, and runs inside the executor service so it sees that service's env.
  Reasons:
  1. `legacy_spec_active`: the name is an executable `LANE_SPECS` definition **and** its id is in the
     process's `ACTIVE_LANES`;
  2. `repair_driven`: a `:gap-repair` name while `REPAIR_INTERVAL != "0"`, or while its owner id is
     in `REPAIR_LANE_IDS` and the owner lane is not `executor = "config"` (`c7-repair-retire` deletes
     this branch);
  3. `catalogue_enabled`: a catalogue lane, including `:gap-fill`, whose TOML says `enabled = true`;
  4. `dispatch_registered`: registered in `jobs/dispatch.py::LANE_DISPATCH`.

  **The ledger `enabled` flag is not a reason**, because both mutating verbs disable versions
  themselves. Both verbs refuse when the tuple is non-empty.
- **Incident-match rule** (`sql/execution/select_definition_incidents.sql`, loaded once and shared by
  census and retire). An incident matches a definition when its `job_run_id` is in the definition's
  runs or its `job_work_item_id` is in their items; or it is run-less and its fingerprint matches
  `process_start_release_fingerprint(*, lane_id)` (`<prefix>%:<lane_id>`), or its type is
  `executor_lane_control` or `executor_definition_{drained,retired,restored}` and its `detail` names
  the definition (the key is re-read from `execution/job_lane_control.py` at authoring), or it
  matches the `f1-executor` breaker-hold fingerprint recorded at 7R. Pinned by a unit test and by
  the DB-gated test.
- **Dry-run protocol.** A dry run executes the full apply transaction under `SET LOCAL lock_timeout`
  and `statement_timeout`, prints before/after counts, the prior per-version state and a plan digest
  (sha256 over the sorted item ids, run ids, prior versions and incident ids), then **ROLLS BACK**.
  `--apply --expected-plan-sha256 <d>` recomputes the digest inside its own transaction and aborts on
  a mismatch (the `data availability-reconcile-physical --expected-sha256` precedent).

**`agri-service ops jobs-lane-census`** (read-only):
`[--definition NAME ...] [--prefix P | --all] [--since ISO] [--json]`. A `READ ONLY` transaction
with a statement timeout. Per name: `classification` (live-config, shadow, legacy-path-to-retire,
unrelated, unknown); `live_dispatch_reasons`; `versions[{version, enabled, schedule,
parameters_sha256, updated_at}]`; `registered_in`; `runs_total`, `runs_since{by_status}`;
`attempts_since{by_failure_class}`; `nonterminal{runs, work_items_by_kind, unmarked_items}`,
`live_leases`; `config_path_items`; **`unaudited_enabled_changes`** (an `updated_at` newer than the
newest matching audit incident); `open_incidents` (shared rule); `latest_run`.

**`agri-service ops jobs-drain-definition`** (guarded):
`--definition NAME --operator WHO --reason TEXT [--apply --expected-plan-sha256 D]`.

- **Refuses** a name outside the pin, a non-empty liveness tuple, and any **live** lease
  (`lease_expires_at > now()`), printing when it expires.
- **`--apply`, one transaction:** (1) `disable_definition_versions.sql` returns the prior
  per-version state; (2) `cancel_definition_nonterminal_work.sql` moves `queued`, `retry_wait` and
  `deferred` items, **and `leased`/`running` items whose lease has expired**, to `cancelled`, sets
  `completed_at`, clears both lease columns together and `next_attempt_at`, and closes each dangling
  `job_attempt` the way `jobs/lease.py::reclaim_expired_leases` does; (3) `close_drained_runs.sql`
  sets every `queued`/`running` run with no remaining non-terminal item to **`status = 'cancelled'`**,
  `completed_at` and `cancellation_reason`, recomputing the three counters inline in **one
  statement** (the IMMEDIATE count constraint), and **never calls the rollup**
  (`sql/jobs/refresh_job_run_rollup.sql` counts `cancelled` items as `failed`); (4)
  `insert_definition_retirement_incident.sql` writes one `executor_definition_drained` incident
  (`info`/`resolved`) with counts, prior versions and the plan digest. Idempotent.

**`agri-service ops jobs-retire-definition`** (soft; never deletes):
`--definition NAME (--successor NAME ... | --successor none) --operator WHO --reason TEXT
[--resolve-open-incidents] [--restore] [--apply --expected-plan-sha256 D]`.

- Successors are full names, each with a succeeded run within 14 days; `none` is allowed for
  `mtbs-forward`, the `:gap-repair` names and the CQ-10 names.
- **Refuses** (exit 2, named reason): a pin miss; a non-empty liveness tuple; non-terminal work or a
  live lease (it prints the drain line); a missing or stale successor.
- **`--apply`:** disable the versions and record their prior state; with `--resolve-open-incidents`,
  resolve the listed incidents with `detail.resolution = "definition_retired"`; write one
  `executor_definition_retired` audit carrying `prior_versions`. A second apply is a no-op.
- **`--restore`** re-applies the `prior_versions` of the latest retired audit
  (`restore_definition_versions.sql`) and writes an `executor_definition_restored` audit. **It never
  enables a version that was disabled before the retire, and never reopens incidents.**

**`agri-service data parquet prefix-census`** (read-only):
`[--top-level] [--layer SLUG] [--counts] [--json]`. List and GET only.

- `--top-level` lists the common prefixes under `object_store_prefix`, classified `known-infra`,
  `layer-root` or `unknown`. The pinned known-infra list is `ml/`, `schema-baselines/`,
  `source-response-checkpoints/v1/`, `ops-archive/` and the calendar export prefix (re-grepped at
  7R); it records the `source-response-checkpoints/v1/` count.
- Per layer root it covers the `snapshot=`, `_breakdown/`, `availability/repairs/<sha>/` and
  **`availability/pending/`** sub-prefixes: counts and bytes (bounded), min/max day, newest
  LastModified (per `snapshot=` prefix) and the pointer generation.
- Classes: `registered-live`, `frozen-history`, **`open-claim`** (an owed or `.quarantined.json`
  claim, keyed by `pipeline/parquet/objectstore.py::availability_retry_path`), `open-journal`,
  `known-infra`, `unknown`.

#### 4.8.9 Amendments to earlier slices (CA1–CA20)

Each amendment is checked first at its own gate's review; RC-8 at 7R only re-checks.

| id | slice (gate) | amendment | why |
|---|---|---|---|
| CA1 | f1-executor (G1) | A ledger-visible config-path marker on every work item a config lane dispatches. | RC-5(b) |
| CA2 | f1-executor (G1) | Brake and supersede resolve through the catalogue, including `<lane>:gap-fill`. **Config-lane definitions register only through `sql/execution/insert_definition.sql` (`ON CONFLICT (name, version) DO NOTHING`) with `read_lane_pause_state` honoured, never through `jobs/worker.py::ensure_job_definition`** (whose upsert sets `enabled = EXCLUDED.enabled` and un-pauses a lane). Test: a braked config lane and its `:gap-fill` are not dispatched on the next tick. | K4, K33 |
| CA3 | f1-executor (G1) | Config dispatch and supersede gate only on TOML `enabled` + catalogue + CA12. The test removes both the lane's `LaneExecutionSpec` and its `ACTIVE_LANES` token (`job_executor_service.py::run_scheduled_command` today fails `unknown_executor_lane` / `ownership_activation_removed`). | K3 |
| CA4 | f1-config (G1 blocker) | `infra/job-executor/Dockerfile` copies `lanes/` into **both** its quality-receipt stage and its runtime stage (`scripts/quality_receipt.py::digest_input_paths` silently skips a missing directory). | K11 |
| CA5 | w3-water-gauges (G4) | `src/lib/map/layer-registry.ts::PLATFORM_LAYERS["water-gauges"].warehouseLayerName` goes through the stream constant. | D-02 |
| CA6 | p6-climate-rebind (G8) | The soil-wetness entries of `layer-registry.ts::CLIMATE_SIGNAL_ICONS` and the two web tests (`sync-index.test.ts`, `environmental-metric-dispatch.test.ts`). | D-01, D-03, D-04 |
| CA7 | s-burn-severity (G5); p5-cutover (G6 go) | Implement the Friday capture / current_snapshot / stage successor inside `pipeline/lanes/burn_severity/**` and return a row naming its path; if the frozen contract cannot carry it, return a "no successor" row that `i-coordinator` routes to the owner (CQ-9) before G6. No receipt or descriptor change; readiness uses time correlation (§4.8.3). The G6 go brakes `mtbs-forward` when a successor exists and the census shows it enabled. | C-25, K7 |
| CA8 | f1-executor (G1) | Legacy repair **authoring and driving** (`_plan_repair_runs`, and the repair-kind path of `run_scheduled_command`) skip `executor = "config"` lanes. Tested with an open `:gap-repair` run on a config lane. | K34 |
| CA9 | f1-providers (G1) | A `docs/env-vars.md` paragraph for the second `OPEN_METEO_API_KEY` consumer (the config provider client). | D-07 |
| CA10 | — | Withdrawn in design round 1. | — |
| CA11 | w3-water-gauges (G4), p6-climate-rebind (G8) | `agent/surfaces.py::SURFACE_PARQUET_LANES` values; `foundation/region/layer_availability.py` soil-wetness surfaces; `tests/test_agent_parquet_tools.py` catalogue rows; **`agent/selection_scope.py::support_lattice`** (`startswith("climate-field-")`, p6). `git status` first (another session edits `agent/**`). | K22, K38 |
| CA12 | f1-executor (G1) | One named, tested env kill-switch distinct from `ACTIVE_LANES`. | K23 |
| CA13 | f1-executor (G1) | **Decided:** the config path keeps the definition name and `EXECUTOR_DEFINITION_VERSION` (no version bump; `insert_definition.sql` starts every later version disabled). If a bump is unavoidable, the G6 go carries an explicit lane-wide resume and RC-5(d) compares the new version's digest to the TOML instead. | K19 |
| CA14 | p4-extract (G5) | Every shim created is listed in `DEPRECATED_ALIASES.md` with removal condition "deleted at 7D-n of the supersession cleanup lane". | K26 |
| CA15 | — | Withdrawn: the 1,584 soil bound is already applied at `d37c202f`. Verify-only. | K1 |
| CA16 | i-coordinator (landing) | Plan working rule 2 (each Phase-7 push gate counts as a phase); tripwire indexes 6 and 12; the plan §Rollback heading ("until that lane's 7Q-n push") and its "after 7Q-n" line. **Applied in this revision.** | — |
| CA17 | f1-runner (G1) | A static_lookup `--republish-current` forward option: refetch, assert digest equality with the served snapshot, rewrite through the normal writer + availability generation + pointer path; refuse on mismatch. Each use needs an owner go. | CQ-8 |
| CA18 | w3-water-gauges (G4 go text) | Pause the legacy water lane only after a legacy turn whose report shows its owed-claim retry drained; otherwise record the open claims for 7R recovery. | K37 |
| CA19 | p5-cutover (G6 go text) | One applied brake drill after the flip on watersheds and `watersheds:gap-fill` (brake, one tick with no dispatch, resume). | K33 |
| CA20 | f1-runner (G1) | A config forward turn retries the lane's owed `availability/pending/` claims first (the legacy `_retry_owed_availability` contract), pinned by a conformance test. | K37 |

#### 4.8.10 Coverage of the cleanup inventory

The inventory (81 items: 62 legacy, 12 track-bridge, 2 G0 stopgap, 5 other; 31 readiness criteria;
21 gaps in the former Phase 7) is in
`.omc/research/ingestion-grill-20260926/cleanup-lane-design.result.json`. Keys: DEL, EDIT, RET
(soft-retire), UNSET, VER (verify-only), KEEP. Slices: `rr` = c7-repair-retire, `q1`/`q2` =
c7-quarantine-1/-2, `d1`/`d2` = c7-delete-1/-2, `sc` = c7-scaffold, `df` = c7-docs-final, `db` =
c7-docs-banners, `ic` = i-coordinator, `v` = c7-verbs; a task id follows its slice (plan Phase 7).

| id | disposition | where | gate |
|---|---|---|---|
| C-01 | DEL the 12 cohort constants and `MTBS_FORWARD_LANE_ID`; KEEP the 5 shadow ids | q1 Q4, q2 | G9Q-1b / G9Q-2 |
| C-02 | DEL cohort specs and imports; re-point the vegetation start-day import; KEEP ndvi-promotion | q1 Q3, q2 | G9Q-1b / G9Q-2 |
| C-03 | KEEP `_REFERENCE_DATA_SPECS` | — | — |
| C-04 | mechanism KEEP; tokens emptied at G9V-n; parse per CQ-2 | sc S3 | G9S |
| C-05 | DEL the whole file (`execution/gap_repair_contract.py`) | rr | G9Q-1a |
| C-06 | DEL, with the `interface/cli/ops.py` registration | rr | G9Q-1a |
| C-07 | DEL (`RepairAuthoringClock`, `_author_due_repairs`, `_plan_repair_runs`) | rr | G9Q-1a |
| C-08 | imports VER at 7Q; refusal text EDIT at 7D; `*_basis` KEEP | q1 Q6; d1 D7, d2 | G9Q / G9D |
| C-09 | KEEP both `pipeline/constants.py` start-day constants | — | — |
| C-10 | DEL after the time-correlated capture row (CA7 / CQ-9) | d1 D2 | G9D-1 |
| C-11 | DEL | d2 | G9D-2 |
| C-12..C-20 | DEL per lane (sensors subject to its RETIRE-SAFE row; static_lookup lanes subject to the §4.8.3 config-write row) | d1 D2 | G9D-1 |
| C-21 | DEL (CQ-6 acknowledged at 7R, otherwise not in cohort 1) | d1 D3 | G9D-1 |
| C-22 | spec at 7Q-1b; drain at G9Q-1b; retire at G9R-1; package at 7D-1 | q1, v V2/V3, d1 | — |
| C-23, C-24 | DEL by explicit name | d1 D2 | G9D-1 |
| C-25 | CA7; CQ-9 | c7-readiness, ic P2 | 7R |
| C-26, C-27 | DEL (with water) | d1 D3 | G9D-1 |
| C-28 | DEL the symbol and its cases | d1 D3 | G9D-1 |
| C-29 | **KEEP both canonical builders, re-pointed** (era5 at d1, nasa_power at d2) | d1 D4, d2 | G9D-n |
| C-30 | EDIT (re-point) or DEL per CA7 / CQ-9 | d1 D4 | G9D-1 |
| C-31 | DEL only when the sensor RETIRE-SAFE row passes | d1 D4 | G9D-1 |
| C-32, C-33 | EDIT cohort cases | d1, d2 | G9D-n |
| B-01 | guard at 7Q; field per CQ-2 | q1 Q7, sc S3 | — |
| B-02 | per CQ-3 | sc S2 | G9S |
| B-03 | KEEP `--compare` | sc S5 | — |
| B-04 | KEEP the S18 mirror; lazy `LANE_REGISTRY` is a follow-up | ic C6 | — |
| B-05 | DEL with soil | d1 | G9D-1 |
| B-06 | DEL | rr | G9Q-1a |
| B-07 | DEL | q2 | G9Q-2 |
| B-08 | KEEP | — | — |
| B-09 | spec at 7Q-1b; drain; retire | q1, v V2/V3 | — |
| B-10 | RET historical; heading amended at landing (CA16) | ic C4 | landing, G9S |
| B-11 | KEEP (CA12) | sc S5 | — |
| B-12 | authoring DEL at 7Q-1a; parse per CQ-2 | rr, sc S3 | — |
| B-13 | VER (the probe knobs are `Final` constants; nothing reads them from the environment) | ic P3, df F2 | — |
| B-14 | DEL shims; crop-cover and land-context KEEP | d1, d2 | G9D-n |
| R-01 | brake while resolvable; drain (which disables); RET | G9Q-n, G9R-n | — |
| R-02 | KEEP history | c7-1 | — |
| R-03 | RET with `--resolve-open-incidents` listed in the go; not undone by `--restore` | G9R-n | — |
| R-04 | tokens out at G9V-1b / G9V-2; variable KEEP (CQ-2) | G9V-n | — |
| R-05 | **set to 0 at G9V-1a; KEEP at 0 permanently** | G9V-1a | — |
| R-06 | UNSET `FIRE_FORWARD_*` after the reachable-reader proof | G9R-1 | — |
| R-07 | KEEP; enumerate with `agri-service data parquet prefix-census --layer <slug> --json` | v V4 | — |
| R-08 | VER (the data stays; no pointer verb) | ic P3 | — |
| R-09..R-11 | VER | ic P3, census | — |
| R-12, R-13 | KEEP | c7-11 | — |
| R-14 | KEEP | ic P3 | — |
| D-01, D-03, D-04 | CA6 at G8; VER by G-8 | — | G8 |
| D-02 | CA5 at G4 | — | G4 |
| D-05, D-06 | VER / EDIT | df F3 | G9S |
| D-07 | CA9; d1/d2 rows; df F2 | — | — |
| D-08, D-09 | banner, then rewrite | db B1, df F1 | — |
| D-10 | sweep | df F4 | G9S |
| D-11 | KEEP | — | — |
| D-12 | closed from evidence; banner "superseded, never executed"; 7R confirmation | db B1, ic P3 | — |
| D-13 | banner, then rewrite | db B1, df F5 | — |
| D-14 | RET | ic C4 | G9S |
| D-15 | EDIT at landing (RUNBOOK cited by heading) | ic C4 | now |
| D-16 | EDIT memory | ic C3 | G9S |
| D-17, D-18 | EDIT at G4/G8; VER at 7S | ic C3 | — |
| D-19 | KEEP | ic C6 | — |
| D-20 | executor image: CA4 at G1 | — | G1 |

**New items found by the design rounds** (re-grepped at 7R). N-01..N-47 are the round-1 rows,
carried by id in the design record with these round-2 changes: N-06 (CA3 + q1 Q1, now with the
in-flight item); N-07 (drain at G9V-1a, retire at G9R-1); N-08 (`REPAIR_INTERVAL` permanently 0);
N-13 (UNSET at G9S); N-19 (VER; CQ-7 covers three tasks); N-29 (includes the two re-pointed
builders); N-32 (other worktrees exist → N-52); N-38 (sc S7 after the caller census); N-41 (the
drain verb, redesigned).

| id | finding | disposition |
|---|---|---|
| N-48 | Owed and quarantined availability claims under frozen and cohort roots | RC-5(e); CA18; CA20; ic P6 recovery; `prefix-census` `open-claim`; WP-4; c7-10 |
| N-49 | Wrong CLI paths (`data parquet`, no `--layer` on coverage) | plan §7.5 rewritten; v V4 `--layer`; CliRunner pin |
| N-50 | Launcher callers (local task, docs, SQL header, memory) | c7-readiness R7; sc S7 precondition; G-14; CQ-7 covers three tasks |
| N-51 | Dead code left behind by deletions; env readers reachable only from dead code | c7-readiness R5 orphan census; d1 CONDITIONAL owns; G-6 reachable-reader rule |
| N-52 | Unmerged branches and worktrees on the legacy path | c7-readiness R6; RC-7; WP-9; c7-15; RUNBOOK rule; CQ-11 |
| N-53 | Unprefixed legacy-path `job_definition` names; the preserve-set tarball | ic P1 `--all`; class F; CQ-10; sc S5 row |
| N-54 | Conductor styleguides | db B1 banner; df F10 rewrite; G-9 covers `conductor/**` excluding `tracks/**` |
| N-55 | Memory and scrt palaces | mechanical G-13; sc S5 `.mpg` row; ic C3 |
| N-56 | Web comments and AGENTS.md; the `ingest/AGENTS.md` verb table; two ingest tests | df owns them; G-9/G-12/G-3 widened; web sweep at G9S |
| N-57 | `tracks.md` status and the retrospective row | ic C1, C4 |
| N-58 | Stale `cadence_basis` strings and the `ingest/firms.py` archive note | sc S6 scope by grep; df F6 |
| N-59 | Local run state and plan files | sc S5 rows; CQ-7 |
| N-60 | Top-level bucket prefixes | v V4 `--top-level`; WP-4 |
| N-61 | The drain deadlock (code-registration refusal) | §4.8.8 runtime liveness predicate |
| N-62 | The rollup rewrites `cancelled` as `failed` | `close_drained_runs.sql`, with no rollup call |
| N-63 | SQL loaded-once rule (`tests/test_sql_tree_conventions.py` rule d) | c7-verbs owns its own SQL files |
| N-64 | Stranded expired-lease items | the drain cancels them and closes their attempts |
| N-65 | G-7 blind to template, prefix and bare keys | bare tokens; CA11 `selection_scope.py` |
| N-66 | A brake can be undone by an upsert | CA2 registration pin; applied drill (G9V-1a, CA19) |
| N-67 | Repair driving on config lanes | CA8 extended |
| N-68 | The `toggleLane` bypass; `--restore` state | census flag; per-version restore; sc S5 row |
| N-69 | Static_lookup lanes never write on the config path | CA17; §4.8.3 row; CQ-8 |
| N-70 | Unregistering destroys the only task definition | XML export (c7-16) |
| N-71 | Undefined incident-to-definition rule | §4.8.8 shared fragment |
| N-72 | Canonical builders that re-export frozen history | KEEP, re-pointed (C-29) |

**Where each readiness criterion Q-n is used:**

| Q | used in | Q | used in |
|---|---|---|---|
| Q-01 | WP-7, sc S5 | Q-17 | RETIRE-SAFE water |
| Q-02 | this lane | Q-18 | RETIRE-SAFE water; §4.8.6 |
| Q-03..Q-07 | RC-1, §4.8.3 | Q-19, Q-20 | §4.8.3 soil |
| Q-08 | §4.8.3 (a normal QA row) | Q-21 | class E, §4.8.7 |
| Q-09..Q-11 | §4.8.3, RETIRE-SAFE climate | Q-22 | RC-7 |
| Q-12 | RETIRE-SAFE climate, RC-5 | Q-23 | WP-3 |
| Q-13 | RETIRE-SAFE climate | Q-24 | WP-4, v V4 |
| Q-14, Q-15 | RETIRE-SAFE water | Q-25, Q-26 | WP-5, df F*, ic C3, G-13 |
| Q-16 | RETIRE-SAFE water, RC-5 | Q-27 | R-02, WP-6 |
| Q-28 | WP-1 | Q-29 | RC-4 |
| Q-30 | RC-3 | Q-31 | q1 Q3 |

### 4.9 Observability and soft failure (Wave O; owner request 2026-09-26; §19)

Wave O gives every lane one logging contract, meters every upstream send, records every failure on
an incident row, and keeps one lane's failure from stopping that lane permanently or reaching
another lane. **Alerting is deferred** (the owner: "another time"); incident rows and their
escalation events are its hook points. The design record is
`.omc/research/ingestion-grill-20260926/observability-wave-design.md` (revision 2); the answers are
§3.6. Plan Phase 0W holds the tasks, the test names, the sweep proofs, the observation rows and the
queries; `metadata.json` → `partitions` holds the `o*` slices and tripwires wave-o-1–wave-o-11.

| push | slices (model) | ships | reviews | observation |
|---|---|---|---|---|
| GL-1 | `o1-logging-core` (sonnet) | `foundation/observability/` (logging, redaction, events, usage, router module, vocabulary, bootstrap); the CLI root and `app.py` on it; `describe_error` at `routes/ops.py` | `/code-review high`, `/security-review` | smoke read ≤ 1 h |
| GL-2 | `o3-ingest-meter` (sonnet) | the meter and User-Agent in `ingest/http.py`'s two factories; USGS per-tile retry; the raw-client guard | `/code-review high`, `/security-review` | smoke read |
| GL-3 | `o2a-exit-classes` → `o5a-executor-observability` (sonnet) | the router tee, turn ids, the usage fold into `job_attempt.metrics`, an observational `exit_class` | `/code-review high`, `/security-review` | 24 h |
| GL-4 | `o4-usage-report` (sonnet) | two SQL files, `execution/usage_report.py::month_to_date`, `agri-service ops jobs-usage-report` | `/code-review high` | smoke read |
| GL-5 | `o2b-incidents` → `o5b-soft-failure` (opus) | quarantine, switch rules, savepoints, per-lane isolation, incident lifecycles, recorded holds, the repair breaker | `/code-review high`, critic | 24 h |
| G1 | `f1-executor` (GL-6 folded, WQ-3), `f1-config`, `f1-runner`, `f1-providers` | the hold ladder and probes, native exits, the paid Open-Meteo cap, the monthly receipt, the runner's S5 usage fields, one retry ladder | Phase-1/2 reviews | G1 observation |

**Launch preconditions.** G0's 24 h observation is closed;
`git status --porcelain -- services/agri-data-service` is empty **at launch**, not only at the push
(at `d37c202f` another session holds uncommitted `agent/**` edits); the publication-debt merge (PD)
has landed; the partitions are re-grepped (stamps `85c4b8f4`); and, before GL-1, the coordinator has
recorded Railway's stderr `level` mapping, log-rate limit and line-size limit (A23). The design's
estimate is about 3–4 days of Phase-1 delay with GL-6 folded.

#### 4.9.1 Logging contract

- **One configure.** `foundation/observability/logging.py::configure_logging(profile, *, long_running=False, console=False)`
  ports `app.py::create_app`'s structlog chain, pinned in order: `merge_contextvars` →
  `add_log_level` → Railway level rename → UTC `TimeStamper` → turn context → `format_exc_info` →
  leaf normaliser → redaction → 16 KiB clamp → `JSONRenderer(default=redacting_fallback)`.
  `dict_tracebacks`, `ExceptionDictTransformer` and `show_locals=True` are forbidden;
  `cache_logger_on_first_use=False`. GL-1 also moves `app.py` onto it, so the web process is
  redacted from GL-1.
- **Envelope.** One flat JSON object per line (≤ 16 KiB own; ≤ 64 KiB forwarded child JSON):
  `event`, `level` ∈ {debug, info, warn, error}, `timestamp`, `service`, `deploy` always; `lane`,
  `mode`, `executor`, `turn_id`, `attempt`, `shard_key`, `probe`, `run_origin` on lane-scoped lines;
  `host`, `provider`, `pool` on `plantgeo_source_*` lines (**the host only, never a URL**).
- **Profiles.** `service` (the executor with `long_running=True`, its armed children, the Sanic app,
  the runner from G1): debug, info and warn to stdout, error to stderr. `tool` (the
  `interface/cli/root.py::cli` callback for every group **except `agent`**, manual
  `python -m agri_data_service.*` runs and service `scripts/*.py`): JSON with `level`, every level to
  stderr; stdout stays the data channel. **The `agent` group and `agent/**` are exempt** (another
  session; `interface/cli/agent.py::reserved_stdout`).
- **Sinks and loggers.** Sinks resolve `sys.stdout`/`sys.stderr` when they write. The stdlib root
  stays at WARNING with pinned floors (httpx, httpcore, sqlalchemy, asyncpg, botocore, boto3,
  s3transfer, urllib3, rasterio, asyncio, sanic); `PLANTGEO_LOG_LEVEL` applies to first-party
  loggers only; `logging.captureWarnings(True)`. `jobs/worker.py`'s module logger becomes a deferred
  `get_logger(name)` that decides on every call.
- **Arming** (`foundation/observability/bootstrap.py::arm_from_environment`, one guarded call in the
  package root, fail-open). An executor child (`PLANTGEO_TURN_ID` set) configures `service`, writes
  `plantgeo_turn_usage_open{pid}`, and registers an `atexit` usage writer that flushes stdout and
  stderr, then `os.write(1, b"\n" + line + b"\n")`, always, even with zero hosts. **No signal
  handler** is installed; SIGTERM keeps its default. A manual `-m` or script run (read from
  `sys.orig_argv`) configures `tool`; any process without a turn id that metered a host writes one
  `plantgeo_source_usage` operator summary to stderr at exit (skipped for long-running processes and
  under pytest).
- **Turn context.** `execution/job_executor_service.py::run_scheduled_command` makes a uuid4
  `turn_id` its first act, passes `PLANTGEO_TURN_{ID,MODE,BUCKET,PROBE}`, `PLANTGEO_LANE_ID` and
  `PLANTGEO_ATTEMPT` to the child, and stamps `metrics.turn_id` and `metrics.spawned` (false on every
  return before `create_subprocess_exec`, true right after).
- **Child log router** (`foundation/observability/router.py::ChildLogRouter`, pure). Input is bounded
  before any regex (64 KiB reassembly, 16 KiB per non-JSON line, 8 KiB per JSON leaf, truncation that
  drops a trailing partial token; a longer line becomes a `plantgeo_child_output` stub). JSON lines
  gain missing context and a `level` only when absent (suffix rule plus `LEGACY_LEVEL_OVERRIDES`),
  then are redacted. Non-JSON lines are redacted; `*Warning:` prefixes route as warn. A rate bucket
  (50 lines/s, burst 200, per attempt) and per-attempt ceilings (1,000 non-error, 200 error, 500
  debug, or 1 MiB) never drop error lines, the report or `lane_turn`. A fault never drops redaction.
  `PLANTGEO_LOG_ROUTING=off` disables re-levelling only. The raw tails, `parse_terminal_report` and
  `_command_failure_reason` still get raw bytes. The report parser takes the last
  `plantgeo_lane_turn_report` line, else the last JSON object with no `level` key.
- **Events.** New names follow `plantgeo_<component>_<noun>_<verb>`; existing names are kept.
  `plantgeo_source_request_failed`/`_retry` (warn; 20 per host per process, then counted);
  `plantgeo_source_usage` (the audit line); `plantgeo_job_executor_lane_turn` (exactly one per
  terminal handler outcome, pre-spawn included: info `ok`; warn `incomplete`, `upstream`, `infra`,
  `report_missing`, `lease_lost`, `interrupted`; error `code`, `hang`, `config`); the hold, incident
  and escalation events of §4.9.3. `tick_started`, `leader_*` and `tick_healthy` drop to debug; the
  tick summary prints on a (lane, state, run) change and hourly, and the hourly heartbeat lists held
  and plan-failed lanes.
- **Redaction** (`foundation/observability/redaction.py`). `redact_strict` keeps the ledger pattern
  verbatim (`jobs/lease.py::redact_text` and `ingest/results.py::redact_secrets` become re-exports).
  `redact_for_log`: an exact-value scrub of secret values ≥ 8 characters (environment names ending
  `_API_KEY`, `_KEY`, `_SECRET`, `_SECRET_ACCESS_KEY`, `_ACCESS_KEY_ID`, `_TOKEN`, `_PASSWORD`,
  `DATABASE_URL`, `DATABASE_URL_SYNC`, the named provider keys, the same rule over `./.env`, DSN
  passwords, and `register_secret_values`) → an SQL-block cut from `[SQL: ` → DSN userinfo → secret
  query parameters → `Bearer`/`Authorization`. `redact_value` walks mappings with a casefolded key
  set (the bare `key` is excluded, so burn-severity's `manifest.key` survives) and an 8-level depth
  stub. `describe_error` gives only the class name for `sqlalchemy.*` and replaces the three
  `routes/ops.py` `error=str(error)` sites. **Never logged:** headers, bodies, URLs, SQL, parameters,
  locals. Metrics hold numbers and closed-vocabulary labels only.

#### 4.9.2 Source-usage audit

- **Counted per host** by request and response hooks in `ingest/http.py::upstream_client` and a new
  `upstream_sync_client()` (both gain a test-only `transport=` keyword; G0's hook coexists):
  `http_requests` (every send, incl. transport re-sends and redirects), `http_2xx`/`3xx`/`4xx`/`429`/`5xx`,
  `transport_failures`, `last_send_outcome` + `last_send_at`, `bytes_in` (through `fetch_bounded`),
  `backoff_seconds`, `weighted_calls_metered` and `meter_errors`. Per turn the lane reports its own
  `requests`, `weighted_calls` (logical), `fetch_attempts` and `probe_status`; per process
  `rss_peak_kib` and `cpu_seconds`.
- **Open-Meteo weight.** `foundation/observability/usage.py::open_meteo_weight(url)` =
  locations × max(1, days/14) × max(1, variables/10) × models (the O10 rule), parsed per URL shape and
  pinned equal to G0's `pipeline/direct/soil/source.py::open_meteo_request_weight` on soil's builder;
  the ensemble host is `weight_rule=unverified` until P5.
- **Fail-open.** Every hook, the User-Agent resolver, arming and the usage writers catch everything
  and count `meter_errors`. `PLANTGEO_UPSTREAM_TELEMETRY=off` (or any unrecognised value) restores
  today's clients byte for byte.
- **Identification (WQ-5).** `User-Agent: plantgeo-agri-data-service/<version>+<RAILWAY_GIT_COMMIT_SHA[:7]>`,
  plus `(+<contact>)` only when `PLANTGEO_UPSTREAM_CONTACT` is set; a caller's own header wins (NWS,
  MTBS).
- **One lane edit and a guard.** `pipeline/direct/burn_severity/capture.py::_capture_snapshot` swaps
  its raw `httpx.Client` for `upstream_sync_client(...)`. `tests/test_no_raw_http_clients.py` fails on
  a raw `httpx.Client(`, `httpx.AsyncClient(`, `cdsapi.Client(` or `urllib.request` under `src/` or
  `scripts/` outside `ingest/http.py` and a reasoned allow-list (`agent/**`; internal endpoints;
  operator-only tools; crop-cover, which `s-crop-cover` moves; dormant modules without a live
  entrypoint). An entry whose file is gone or matches nothing never fails the guard, so a deletion
  needs no edit.
- **Durable record.** `agri.job_attempt.metrics` (no migration), stamped by `o5a` with `turn_id`,
  `spawned`, `exit_class`, `turn_outcome`, `report_present`, `usage_reported`, `usage_complete`,
  `unwritten_known`, `probe`, `probe_status`, the stdout, volume and resource keys, the request
  counters, `usage.hosts{…}` (≤ 16 hosts), `usage.charged`, `usage.suspect` and `usage.charged_basis`;
  the merged `publication_debt` key stays. Legacy names map `requests_spent` → `requests`, `rows` →
  `rows_written`, `bytes`/`written_bytes` → `bytes_written`. Logs are for reading, never the monthly
  record. **From G1 the executor also writes `receipts/source-usage/<YYYY-MM>.json`** to the existing
  object store after each UTC month closes (WQ-6); `c7-verbs` lists `receipts/` as known-infra.
- **Charging** (a basis per attempt):

  | basis | condition | charged (counts toward the lines and the stop) | suspect (gap-fill line only) |
  |---|---|---|---|
  | `metered` | a usage line is present | `CHARGE_BASIS=metered`: max(metered, reported logical); `logical`: the reported figure | 0 |
  | `reported` | spawned, no usage line, the report states `weighted_calls` | the reported logical figure | 0 |
  | `suspect` | spawned with neither, or `lost` on a weighted-pool definition after the epoch | 0 | the lane's logical cap (`vocabulary.LANE_LOGICAL_CAPS`; soil 1,602) |
  | `not_spawned` | `spawned=false` | 0 | 0 |
  | excluded | running or deferred attempts; attempts before the metering epoch | — | — |

  The metering epoch is `min(started_at)` over attempts carrying `metrics ? 'spawned'`, computed in
  SQL, so history is never re-priced. The wire worst case (76,896 weighted at the default; 192,240 at
  `--retry-attempts 10`) is documented, never charged. `CHARGE_BASIS=metered` until P5 answers.
- **Report (GL-4).** `sql/execution/select_provider_usage.sql` and
  `sql/execution/select_provider_month_to_date.sql` (CTEs `epoch`, `metered`, `lost`; a
  `:logical_caps` bind); `execution/usage_report.py::month_to_date(session, *, pool, now)` is the only
  loader of the second file. `agri-service ops jobs-usage-report [--days N | --since D --until D] [--lane ID …] [--pool P] [--by pool|lane|host|day] [--format table|json]`
  runs in a read-only transaction with `statement_timeout = 30s` and prints three fault-isolated
  sections: (1) month to date per pool (charged, suspect, basis split, the 60 % and 95 % lines, the
  epoch); (2) per lane × host (send, 429, 5xx and transport rates, bytes, backoff share, retry
  amplification, elapsed p50/p95, `start_lag_seconds`, RSS, bytes per row, outcome and class counts,
  flag columns including `publication_debt`, operator spend as log-only); (3) open incidents of every
  kind.
- **Quota enforcement (WQ-4; `f1-executor` at G1): only the paid monthly Open-Meteo cap.**
  `lanes/_providers/open-meteo.toml` declares 5,000,000 a month with `ceiling_fraction = 0.60` and
  `stop_fraction = 0.95`. `execution/provider_budget.py` admits gap-fill while
  charged + suspect + the turn cap ≤ 0.60 × 5,000,000 and stops forward only when
  charged ≥ 0.95 × 5,000,000; suspect never stops forward, and suspect > 10 % of charged month to date
  opens `budget_basis_suspect:open-meteo-paid` and refuses gap-fill until it resolves. Admission runs
  per lane inside planning **before `execution/lane_scheduling.py::fair_due_order`**: a refused lane is
  a `deferred_budget` result, never enters `due`, opens no run and never uses
  `JobHandlerOutcome.deferred`; it logs one warn per lane per UTC day and bumps
  `budget_deferred:open-meteo-paid`. Legacy soil and its repair charge the paid pool only while
  `OPEN_METEO_API_KEY` is set. A provider try-lock sits on `db/engine.py::executor_lane_pool`. Manual
  runs stay outside admission (each prints an operator usage line).
- **Not adopted (WQ-4):** windowed pools and reserves for `open-meteo-free`, `firms` and
  `usgs-water-data`; the GL-5 `pool_saturated` brake; `POOL_BULK_LANES`. Pool names stay as metering
  and report labels only.

#### 4.9.3 Soft-failure contract

**Exit classes** (vocabulary in `foundation/observability/vocabulary.py`; stamped by `o5a` through
`execution/exit_classes.py::classify_exit`; observational at GL-3; the ladder acts from G1):

| child result | `exit_class` / `turn_outcome` | ladder (G1) | incident | level |
|---|---|---|---|---|
| exit 0, report complete | `ok` / `completed` | resets the streak | may resolve incomplete, report-missing and probation | info |
| exit 0 with unwritten days, **publication debt** (PD), or soil `probe_status ≠ ok` | `ok` / `incomplete` | none | `lane_incomplete` (reason `unwritten`, `publication_debt`, `probe_gated` or `inconclusive`) | warn |
| exit 0, no report | `report_missing` | legacy none; config code | `lane_report_missing` | warn; error at 3 |
| 75; legacy non-zero with upstream evidence | `upstream` / `upstream_unavailable` | upstream | `lane_hold` | warn; error at a 72 h chain |
| legacy non-zero with infra evidence | `infra` / `infra_unavailable` | upstream | `lane_hold` (+ `fleet`) | warn; error at a 72 h chain |
| 70; legacy non-zero without evidence; a missing or unknown class on a held run | `code` / `code_error` | code | `lane_hold` | error |
| timeout (monitor kill) | `hang` / `timeout` | code | `lane_hold` | error |
| 78; 2; a pre-spawn failure | `config` / `config_error` | code | `lane_hold` | error |
| shutdown yield; no command budget | `interrupted` | not an attempt charge | none | warn |
| fence lost | `lease_lost` | retried; a probe item parks (inconclusive) | `executor_lease_lost` after 3 | warn; error once |
| attempt closed `lost` | read as `code`; a probe item is inconclusive (≤ 3), then failed | — | — | — |
| repair turns | the same classes | never `lane_hold` | `lane_repair_failing` | as above |
| admission refusal (G1) | not an attempt | none | `budget_deferred` | warn |

**Evidence rules** (`execution/exit_classes.py`; they read only the parsed terminal report, and the
usage line for R3; WQ-2):

| rule | matches | lanes |
|---|---|---|
| R1 message | `upstream request failed with status (429\|5\d\d)` (unique to `ingest/http.py::UpstreamHttpError`) or a transient token (`UpstreamTimeoutError`, `UpstreamTransportError`, `OpenMeteoRateLimitError`) in the report's `error` or `detail` | climate, vegetation, watersheds |
| R2 pair | one JSON object whose `error_type` is transient and, for `UpstreamHttpError`, whose `detail` holds the 429/5xx message | water gauges, sensors, weather observations (`<lane>_forward_failed` on stdout) |
| R3 wrapper + meter | the lane's registered wrapper (`WRAPPER_EVIDENCE[lane]`) and a usage-line `last_send_outcome` ∈ {429, 5xx, transport} | drought, fire-perimeters, evacuation-zones, burn-severity, fire-detections |
| R4 infra | an infra token (`EndpointConnectionError`, `ConnectTimeoutError`, `SlowDown`, `ServiceUnavailable`, `InternalError`, `OperationalError`, `ConnectionDoesNotExistError`, `CannotConnectNowError`, `ConnectionResetError`, `could not connect to server`) | R2/DB census failures whose token survives the wrap |

A conflict (a non-allow-listed exception class, or a wrapper after a 2xx/3xx/4xx last send) is
`code`. With no report, only the stderr tail's last `…Error: `/`…Exception: ` line is read; without a
usage line R3 cannot match. Soil's probe unavailability already exits 0 (G0).

**Holds.**

- **GL-5 records and reconciles** (release stays operator-only until G1). Fingerprint
  `lane_hold:<lane>` (renamed `…:resolved:<id>` on resolve); `detail` holds the state (held, probing,
  probation, paused), `exit_class`, `class_source`, rung, `chain_first_seen_at`, `episodes_7d`, the
  last 10 probes, clean buckets and announcements. One read per tick
  (`sql/execution/select_lane_incidents.sql`: open incidents plus holds resolved in the last 7 days).
  A hold opens when `execution/lane_scheduling.py::judge_failed_checkpoint` returns
  `release="operator"`; its class comes from `sql/execution/select_run_final_attempt.sql` (missing,
  unknown or `lost` = `code`). `execution/lane_incidents.py::reconcile` (pure, every tick) resolves a
  hold whose verdict is no longer held (`released_by=operator` when `latest.superseded_by_operator`
  and the run is not a probe's; otherwise `reconciled`), pauses it while the definition is disabled
  or the lane inactive or quarantined, and returns it to `held` with its rung on re-enable. Repairs
  are withheld while the hold is open and the verdict is held (or the state is probing). A chain
  older than 72 h raises the severity to error, logs `hold_chronic` once and lists the lane in every
  hourly heartbeat.
- **G1 ladder** (`f1-executor`; WQ-1, WQ-2, WQ-3). Upstream and infra: 1, 2, 4, 8, 16, 24 h, then
  daily. Code, hang and config: `PLANTGEO_JOB_EXECUTOR_CODE_PROBE_HOURS` (default `6,12,24`, then
  daily; empty or unparseable = operator-only). A probe fires only when `verdict.newer_bucket_exists`.
  Mechanics follow `execution/job_run_supersession.py::_release_by_process_start` inside a savepoint:
  append the probe (with `superseded_run_id`), `supersede_failed_run(operator="executor:probe")`,
  commit, then `_open_scheduled_run(…, max_attempts=1)` through a new `jobs/worker.py::open_job_run`
  keyword. A failed probe advances the rung on the same fingerprint. A `lost`, `executor_lease_lost`
  or `interrupted` probe is inconclusive (same rung, sticky class; the fourth counts as failed,
  `lost_repeatedly`). An exit-0 probe starts probation: single-attempt buckets, repairs resume, and
  the hold resolves (`released_by=probe`) after 2 conclusive clean buckets (no unwritten day and no
  publication debt; conclusive per `PROGRESS_EVIDENCE`) or, after 48 h without a failed bucket, as
  `probation_expired` plus `lane_incomplete` (reason `inconclusive`). For 24 h after a resolve,
  buckets open with `max_attempts=2` (1 when the chain has ≥ 2 episodes in 7 days); a re-open within
  7 days inherits the rung and the chain; ≥ 3 episodes in 7 days log `hold_flapping` at error once.
  No deploy-time probe (WQ-7). USGS probe turns use `probe_attempts=1`.

**Isolation (a failure never reaches another lane).**

- Every Wave O statement on the leader session runs in `session.begin_nested()`. A `SQLAlchemyError`
  rolls back to the savepoint, logs `incident_write_failed` once per transition, and that lane
  degrades to HEAD planning (operator-only hold, the definition's `max_attempts`, repairs as today).
  Only `_pinned_connection_invalidated(session)` keeps today's tick-level re-raise. Upserts write
  `last_seen_at = GREATEST(last_seen_at, :now)`.
- A non-SQL exception in one lane's planning, repair planning included, reports that lane
  `plan_failed`, bumps `lane_plan_failed:<lane>` and continues with the next lane; `tick_partial`
  logs before any re-raise; `_drain_both` gathers with `return_exceptions=True`.
- **Repair breaker:** 2 consecutive `code`, `hang` or `config` repair failures (never
  `invalid_repair_request`) withhold the lane from `plan_gap_repairs` for 1, 2, 4, then 7 days, then
  allow one run; a success resolves it. An authoring pass that raises bumps
  `executor_repair_authoring` (error at 2 intervals).
- **Quarantine:** `ActivationConfig.active_lanes` is the parsed ids minus a new, defaulted
  `quarantined` field, so `plan_gap_repairs` and `resolve_executor_lane` skip quarantined ids
  unedited; unknown and non-executable ids are quarantined, never an exit; on a conflict the
  incumbent legacy lane is kept (no lane declares `conflicts_with` at HEAD). The remaining startup
  exits are a missing DSN and a `Settings()` validation error (§4.9.6).
- Concurrency stays 1 until G6. A hanging lane costs up to 3 buckets × 5 attempts × its timeout
  before it holds (as at HEAD), then one timeout per probe.

**Incidents** (every streak lives on a row; severities map through
`execution/lane_incidents.py::INCIDENT_SEVERITY`, which lives in `execution` because `foundation` may
not import `models`):

| fingerprint | opens | escalates | resolves |
|---|---|---|---|
| `lane_hold:<lane>` | the verdict is operator-held | 72 h chain → error + `hold_chronic`; ≥ 3 episodes in 7 d → `hold_flapping` | probation end; operator supersession; reconciliation |
| `lane_incomplete:<lane>` | the first incomplete turn | 6 h warn; 24 h and 72 h error | the first complete turn |
| `lane_report_missing:<lane>` | exit 0 without a report | error at 3 | the next report |
| `lane_blocked:<lane>` | a blocked open run or a prior-version run | none | the state clears |
| `lane_quarantined:<lane>`, `executor_config:<VAR>` | startup | none | the first tick of a deployment without the fault |
| `lane_plan_failed:<lane>` | planning raised or degraded | error at 3 ticks | a clean planning pass |
| `lane_repair_failing:<lane>` | a repair run settles failed | 2 code-class runs → error + the repair breaker | a repair success |
| `executor_repair_authoring` | an authoring pass raised | error at 2 intervals | the next good pass |
| `executor_lease_lost:<lane>` / `:fleet` | a `lease_lost` attempt / every dispatched lane lost in one tick | error at 3 | a non-lost attempt |
| `fleet:<exit_class>` | ≥ 3 holds of one class within 1 h | error once | every member resolves |
| `budget_deferred:open-meteo-paid`, `budget_basis_suspect:open-meteo-paid` (G1) | an admission refusal; suspect > 10 % of charged | none | the first admitted turn; the ratio falls below 10 % or the month rolls over |

The only silencers at GL are `jobs-set-lane-enabled --disabled` (paused) and removing the lane from
the allow-list (paused, inactive); from G1, `acknowledged` in `/admin/jobs` suppresses escalation.
`agri.job_event` is the heartbeat channel, never the audit record; `job-logs-maintain` runs daily
from G1 (`f1-executor`).

**Switches** (synonyms: on {1, true, yes, on, enabled}, off {0, false, no, off, disabled, none};
trimmed, case-insensitive):

| variable | unset | unrecognised or garbled |
|---|---|---|
| `PLANTGEO_JOB_EXECUTOR_SOFT_FAILURE` | on | off: HEAD planning (operator-only holds, no repair breaker or withholding, the definition's `max_attempts`); incidents are still written |
| `PLANTGEO_JOB_EXECUTOR_CODE_PROBE_HOURS` (G1) | `6,12,24` | operator-only code holds |
| `PLANTGEO_UPSTREAM_TELEMETRY` | on | off (the legacy clients) |
| `PLANTGEO_LOG_ROUTING` | on | off (redaction stays on) |
| numeric tunables (poll, max lanes, repair interval, ceilings, sample rate) | their defaults | the default + one `config_fallback` warn + `executor_config:<VAR>` |
| `PLANTGEO_LOG_LEVEL` | info | info |

`BREAKER_MODE=legacy` (S16) subsumes `SOFT_FAILURE=off`. No variable is required and no secret is
set. Optional owner-set variables: `PLANTGEO_UPSTREAM_CONTACT`, `PLANTGEO_LOG_LEVEL=debug` and the
switches above; `PLANTGEO_JOB_EXECUTOR_PROCESS_START_RELEASES_BREAKER` stays off (WQ-7).

#### 4.9.4 Coverage (the design record's 73 inventory ids)

| element (design §4.1) | step | slices | inventory ids |
|---|---|---|---|
| LOG-1 profiles, pinned chain, arming, `agent` exempt, deferred worker logger, `app.py` | GL-1 (runner at G1) | `o1`, `o5a`, `f1-runner` | L-02, L-08, S-14, T-02, T-09 |
| LOG-2 event vocabulary; `PLANTGEO_LOG_LEVEL` | GL-1 | `o1` | L-09, E-15, E-16 |
| LOG-3 redaction; `describe_error`; dead code deleted | GL-1 | `o1`, `o5a`, `f1-providers` (`KeyedRequestUrl`) | L-05, L-06, L-14, L-15 |
| LOG-4/5/6 router, volume bounds, report discrimination | GL-1 module, GL-3 wiring | `o1`, `o5a`, `f1-runner` | L-10, L-11, S-02, S-13, E-17, E-20 |
| USE-1 meter, USE-5 User-Agent, SOFT-7 USGS tiles | GL-2 | `o3` | L-03, L-04, F-06, S-04, S-05, S-06, S-12, T-03, E-07, E-08, E-09, E-21 |
| USE-2 usage line, turn id, charging | GL-3 | `o5a` | L-07, L-16, F-08, E-03, E-10, E-12 |
| SOFT-3 evidence rules (observational) | GL-3 | `o2a`, `o5a` | F-01 |
| USE-3 report | GL-4 | `o4` | E-02, E-11, E-19 |
| SOFT-1 quarantine and the environment rule | GL-1, GL-5 | `o1`, `o2b`, `o5b` | L-01, T-05 |
| SOFT-2/5/6/9 hold record, isolation, incidents, repair breaker | GL-5 | `o2b`, `o5b` | F-03, F-04, F-07 |
| SOFT-2/3 ladder acting on R1–R4 (GL-6 folded, WQ-1–WQ-3) | G1 | `f1-executor` | L-13, T-01, F-05 |
| USE-4 paid cap only (WQ-4) | G1 | `f1-config`, `f1-executor` | S-08, F-09, E-13 |
| SOFT-4 queue, locks, `lease_lost` exclusion, S16 dual read | G1 | `f1-executor` | F-02, F-10, S-15, T-06, E-14 |
| SOFT-8 one retry ladder with Retry-After | G1 | `f1-providers` | S-01, S-07, S-11, T-04, E-06 |
| USE-6 runner S5 fields | G1 | `f1-runner` | S-03, E-01, E-04, E-05, E-22 |
| coordination | every push | `i-coordinator` | L-12, T-07, T-08, E-18 |
| out of scope | — | — | S-09 (crop-cover allow-listed; `s-crop-cover` moves it), S-10 (land-context shadow), S-16 (no SoilGrids producer) |

SOFT-10 (the pool brake) is **not adopted** (WQ-4). Sibling track `observability_log_capture_20260903`:
its W1 is delivered at GL-1 and W4 is extended at GL-3; W2, W3 and W5–W7 stay blocked on D2–D7.

#### 4.9.5 Retirement with the cleanup lane (Phase 7)

Wave O's legacy-lane parts die with the c7 deletions; the design record's `d7-*` targets map as
§18.6 says.

| Wave O artifact | removed by | when |
|---|---|---|
| the `upstream_sync_client` swap in `pipeline/direct/burn_severity/capture.py` | `c7-delete-1`, with the package (a later cohort under a CQ-9 carve-out) | 7D-1 |
| the USGS tile retry and policy in `ingest/usgs_nwis.py` and its tests | `c7-delete-1`, with the water chain (only with CQ-6) | 7D-1 |
| repair withholding, the repair breaker and the `executor_repair_authoring` wiring (`job_executor_service.py` repair symbols, `lane_incidents.py` repair rules, `tests/execution/test_repair_withholding.py`) | `c7-repair-retire`, with the repair subsystem | 7Q-1a |
| cohort-1 legacy rows: `WRAPPER_EVIDENCE` and the R2 lane rows in `exit_classes.py` and their fixture cases, cohort-1 `PROGRESS_EVIDENCE` entries, the soil legacy map in `execution/provider_budget.py` | `c7-quarantine-1` (Q13) | 7Q-1b |
| the climate `PROGRESS_EVIDENCE` entry and climate fixture cases | `c7-quarantine-2` | 7Q-2 |
| the generic legacy-exit path (R1/R2/R4 over legacy prints), `LEGACY_LEVEL_OVERRIDES` in `router.py`, the unmarked-report fallback in `turn_reports.py` | `c7-scaffold` (S8), **only under the CQ-2 alternative**; under the CQ-2 default they stay with the shadow lanes' legacy arm | 7S |
| the four Wave O switches | KEEP (operational kill switches); a row in the 7S dispositions table | 7S |
| the monthly receipts under `receipts/source-usage/` | never (data; c7-1); `c7-verbs` lists `receipts/` as known-infra | G9.0 |
| the dead `jobs_supersede_run` except-branch (`execution/job_run_supersession.py`) | an `i-coordinator` C6 follow-up (no c7 slice owns that file) | — |

#### 4.9.6 Stated residuals

1. Startup exits on a missing DSN or a `Settings()` validation error; Railway's ON_FAILURE ceiling
   of 10.
2. Manual runs get the `tool` profile and one stderr operator usage line, but no ledger row, no
   admission and no lock.
3. `plantgeo-ml-service` is outside the ledger, the tee and the report.
4. `/admin/jobs`: incidents and acknowledgement arrive at G1; `toggleLane` is unaudited (K35);
   `triggerLane` is dead.
5. Blind lanes are judged conclusive on "report present, not failed".
6. Uncounted: backoff in the 14 private retry ladders; vegetation's direct bytes; grandchild sends
   after an `os._exit` kill (flagged `usage_complete=false`).
7. Suspect turns are charged at the logical cap; if P5 shows wire billing, a killed turn could have
   spent up to the wire worst case.
8. Tool-profile tagging depends on Railway reading `level` from stderr JSON (A23).
9. Infra failures wrapped without a token class as `code`.
10. `job_event` retention is unscheduled until G1.

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
| FR-8 | Breaker + incident (revised 2026-09-27 for WQ-1 and WQ-2; §4.4, §4.9.3) | Every hold is one `agri.job_incident` row per episode, recorded and reconciled from GL-5 and shown in `/admin/jobs`, with no webhook or email (O4). Exit 75, and a legacy non-zero exit with R1–R4 upstream or infra evidence: single-attempt half-open probes at 1, 2, 4, 8, 16, 24 h, then daily, and auto-release through probation. Exit 70/78, a timeout, and a legacy non-zero exit without evidence: a hold re-attempted by single-attempt probes at 6, 12, 24 h, then daily (`CODE_PROBE_HOURS`). No probe fires more often than the lane's cadence; probation ends after 2 conclusive clean buckets or 48 h; a re-open within 7 days chains the episode; ≥ 3 episodes in 7 days log `hold_flapping` once; a lost probe never advances the rung. One failure after a release does not re-hold. No per-tick repeated error line. `SOFT_FAILURE=off` restores operator-only holds, `CODE_PROBE_HOURS=""` keeps code holds operator-only, and `BREAKER_MODE=legacy` restores today. | P0 |
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
| FR-20 | Deprecation (restructured 2026-09-27 into the §4.8 cleanup lane; FR-26–FR-29) | Only after READY(L) (FR-26) and the G9 gates: the legacy repair subsystem leaves first (7Q-1a, `c7-repair-retire`), then each cohort's entries, ids and literals leave the shared executor/registry files (7Q-n, `c7-quarantine-n`; the former `d7-legacy-shared`), then, after the 7O-n observation, its legacy modules, tests and extraction shims are deleted (7D-n, `c7-delete-n`; the former `d7-legacy-lane-modules`). Review N8 ordering holds both ways (tripwire c7-3). The allow-list parse and `ACTIVE_LANES` stay per CQ-2 (default KEEP, pinned to the shadow ids); the CA12 kill-switch stays. `mtbs-forward` is deleted (O7). | P1 |
| FR-21 | Legacy bridge (G1) | Shortwave is dropped from the legacy writer's turn (registration and serving intact); all 11 legacy climate products and all 8 soil products are excluded from legacy repair; the legacy climate lag is `CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS`; legacy soil runs 6-hourly (`"50 */6 * * *"`) **on top of G0's per-run cap**. Tests pin the legacy product iteration, the repair exclusions (incl. soil → `no_repair_binding`), the climate lag, and the soil cadence and schedule string. | P0 |
| FR-22 | Analysis lattice | §4.1 manifest entry in `pnw.json` and `pnw.ts` (optional field, so `kenya_highlands.*` stay valid), plus the 1,568-cell parity test. | P0 |
| FR-23 | Extraction | Before the swarm, everything outside `pipeline/direct/**` that imports a Phase-7-deleted module, **and the land-context and crop-cover source protocols the shadow strategies need** (`pipeline/source_bindings.py`), imports from a stable home. **Every module those moved modules import from `pipeline/direct/**` moves with them** (review P6: `burn_severity/products.py`; `drought/products.py`; `evacuation_zones/{products,rows,source,support}.py`; `watersheds/source.py`; `crop_cover/{source,products}.py`; `land_context/{source,products}.py`), so no `pipeline/lanes/**` module imports `pipeline/direct/**` and `d7-legacy-lane-modules` finds no surviving importer. Legacy files become re-export shims with unchanged behaviour. Full list in `metadata.json` → `p4-extract`. | P0 |
| FR-24 | **G0 legacy soil cap** | §4.4 G0 bullet and its status table. No soil process spends more than **33 logical requests (≤ 1,602 weighted calls hard, 1,570 on a clean fan-out — re-check R4), ≤ 132 fetch attempts (≤ 1,584 HTTP requests incl. transport retries and redirect hops)** (33 × `MAX_FETCH_ATTEMPTS` 4), whatever its flags, and the report states `requests_spent`, `weighted_calls`, `fetch_attempts` and `http_requests`. An owed day inside the probe window whose probe is null costs no request and no slot, so **no probe-null day is ever fanned out**. A probe that cannot answer is `unavailable`; a 429 is `deferred`; in both the window is gated, older owed days are walked, and the run exits 0 (O-R3-1). A probe null on a `data` day is `invalid`: the walk is ungated and still capped. A day the probe shows valued but whose fan-out is thin (≠ 1,470 values) raises `DirectSoilFieldError` after in-run retries that re-ask nothing, and the run exits 1 — unchanged legacy behaviour, the A15 episode (review P2). Tests: plan G0 brief. | P0 |
| FR-25 | **Stream registration mirror** | S18. Every stream a lane TOML declares has exactly one `LaneRegistration`; `water-gauges-daily` and the climate streams are servable and censused; a parity test pins every mirror row to its TOML row; `CALENDAR_HISTORY_FLOOR` includes the mirror rows (the calendar's next version carries the earlier days, A19) and its `floor_basis` text names the lane that sets the minimum. | P0 |
| FR-26 | **Verified ready to supersede** (§4.8.1–§4.8.5) | Every class A–D and F subject has a READY(L) row (RC-1–RC-8) or a RETIRE-SAFE row in `evidence/phase7.md` §R, with a cohort and visible carve-outs; RC-3 ≥ 14 days from T0(L); RC-4 counts the whole window, not the final snapshot; RC-8 is a hard stop when CA2 or CA3 is absent. WHOLE-PATH-READY (WP-1–WP-9) gates G9S. A dossier critic verdict is recorded before any cohort is cut, and CQ-6 and CQ-10 are answered first. READY is evidence only; every action waits for its G9 gate. | P1 |
| FR-27 | **Cleanup ops verbs** (§4.8.8) | `agri-service ops jobs-lane-census`, `jobs-drain-definition`, `jobs-retire-definition` (with `--restore`) and `agri-service data parquet prefix-census` exist. Tests: the census SQL is read-only; the liveness predicate refuses on each reason and accepts a braked, token-removed definition whose spec still exists and a `:gap-repair` name whose owner is still bound (owner on `executor = "config"`, `REPAIR_INTERVAL` "0"); the drain cancels an expired-lease item and closes its attempt; drained runs end `status = 'cancelled'` with consistent counters; a dry run changes no row; `--apply` refuses a stale digest; `--restore` reproduces the per-version state; the incident-match rule is covered; `prefix-census` makes no write call; `tests/interface/test_cleanup_command_lines.py` parses every plan §7.5 command line through CliRunner `--help`. No schema change; no DELETE/DROP/TRUNCATE; each SQL file loaded once (`tests/test_sql_tree_conventions.py` rule d); `CLI_ADAPTER_VIOLATIONS` unchanged. | P1 |
| FR-28 | **Staged supersession** (§4.8) | Per cohort: token removal is its own owner step plus a soak (c7-7); the 7Q-n push lands only when the census meets tripwire c7-4 and `tests/execution/test_config_dispatch_without_legacy_spec.py` (spec removed, token removed, a config-marked in-flight item) is green; 7D-n lands only after 7O-n and a G9D-n go naming the lanes; greps G-1–G-14 meet plan §7.3; the deny-list closure (WP-8) holds; no data row or object is deleted (c7-1); `REPAIR_INTERVAL` is 0 from G9V-1a, permanently (c7-5). | P1 |
| FR-29 | **Scaffolding and residue closure** (§4.8.5, plan 7S) | A dispositions table in `lanes/AGENTS.md` covers every track switch (executor arm, S16, `--compare`, S18 mirror, CA12, `REPAIR_INTERVAL`, `_unwritten_entries` legacy keys), this lane's own artifacts and local state; a variable is unset only after a reachable-reader proof (G-6); docs, skills, styleguides, AGENTS.md (Python and web), README, infra docs and comments cite only live paths (G-9, G-12, G-14); memory passes the mechanical G-13 sweep; plan §Rollback is HISTORICAL; `conductor/tracks.md` is flipped and the retrospective row is written; the T5 census, 7 days after G9S, meets plan §7.5. | P1 |
| FR-30 | **Logging contract** (§4.9.1; GL-1, GL-3) | One `configure_logging`; every first-party line is one flat JSON object with `event`, `level`, `timestamp` and `service`; `service` routes debug/info/warn to stdout and error to stderr; `tool` sends every level to stderr; the `agent` group is not reconfigured; sinks resolve streams at write time; the executor tee routes child lines through `ChildLogRouter` within the §4.9.1 bounds; exactly one `plantgeo_job_executor_lane_turn` per terminal outcome, pre-spawn included; an idle executor writes about 24 heartbeats a day plus state changes. Tests: plan 0W.1 and 0W.3; `tests/test_layer_import_contract.py` and `tests/interface/test_availability_cli.py` pass unedited. | P0 |
| FR-31 | **Secret redaction** (§4.9.1; GL-1) | No secret value, URL, header, body, SQL block, parameter or local reaches a log line from the executor, its children, the CLI or the web process. Tests: the `test_redaction.py` suite, `test_sqlalchemy_traceback_drops_sql_and_parameters`, `test_non_string_leaf_repr_is_redacted`, `test_truncation_never_leaves_a_secret_prefix`, `tests/interface/test_ops_panel_log_redaction.py::test_every_ops_error_site_logs_class_name_only`, `tests/test_app_logging.py::test_web_process_lines_are_redacted_json`; observation row 3 is clean. | P0 |
| FR-32 | **Source metering and identification** (§4.9.2; GL-2) | Every send through `upstream_client` and `upstream_sync_client` is counted per host with its status class, `last_send_outcome`, bytes and backoff; the Open-Meteo weight equals G0's on soil's builder; a raising meter never fails a send; `PLANTGEO_UPSTREAM_TELEMETRY=off` is byte-for-byte legacy; providers see the WQ-5 User-Agent; the raw-client guard passes; USGS tiles retry independently (3 attempts, 45 s ceiling, `probe_attempts=1`, the failed tile named) while evacuation zones keep 6 attempts under a probe. | P0 |
| FR-33 | **Turn attribution and durable usage** (§4.9.2; GL-3) | Every attempt carries `turn_id` and `spawned`; the usage line survives an unflushed `python -m` report; `metrics.usage.hosts` is populated; the charging basis follows the §4.9.2 table; `exit_class` is stamped from `classify_exit` and acts on nothing at GL-3; a turn with publication debt is `incomplete`; the end-to-end child test passes (plan 0W.3). | P0 |
| FR-34 | **Usage report and monthly receipt** (§4.9.2; GL-4, G1) | `agri-service ops jobs-usage-report` prints its three sections from a read-only transaction with a 30 s timeout; `month_to_date` is the only loader of its SQL; pre-epoch and unspawned attempts are never charged; running attempts are excluded; suspect stays a separate column; a lost attempt after the epoch is suspect at the logical cap. From G1, `receipts/source-usage/<YYYY-MM>.json` is written once per closed UTC month (WQ-6). | P0 |
| FR-35 | **Soft-failure isolation and incidents** (§4.9.3; GL-5) | A raising incident read or write leaves every lane dispatching as at HEAD; a planning fault in one lane leaves the others running; the executor starts with every malformed variable and with an unknown allow-list id (FR-11's legacy half, delivered early); every incident kind has a defined exit; the hold is recorded and reconciled (release stays operator-only until G1); repairs are withheld for a ledger-held lane and the repair breaker trips after 2 code-class failures; `SOFT_FAILURE=off` restores HEAD planning. The fault-injection sweep proves a second lane completes unaffected (plan 0W.5). | P0 |
| FR-36 | **Self-healing holds** (§4.9.3; G1, `f1-executor`; WQ-1–WQ-3) | FR-8 as revised. Tests: `tests/execution/test_hold_ladder.py`, `tests/execution/test_hold_probes.py` (incl. `test_daily_lane_never_probes_more_often_than_its_cadence`, `test_lost_probe_does_not_advance_the_rung_or_flip_the_ladder`, `test_three_episodes_in_7_days_log_hold_flapping_once`, `test_deploy_probe_is_off_by_default_and_folds_when_on`) and the probe cases of `test_soft_failure_fault_injection.py`. | P0 |
| FR-37 | **Paid Open-Meteo cap** (§4.9.2; G1; WQ-4) | Admission runs per lane before `fair_due_order`; gap-fill is refused at 60 % of 5,000,000 (charged + suspect + turn cap) and forward only at 95 % of charged spend; suspect never stops forward. Tests: `test_refused_lanes_never_take_a_selection_slot`, `test_suspect_charges_never_stop_forward`, `test_admission_imports_month_to_date`, `test_gap_fill_is_refused_at_sixty_percent_and_forward_at_ninety_five_percent`. No free-tier, FIRMS or USGS pool, reserve or brake exists. | P0 |
| FR-38 | **Config-lane observability conformance** (§4.9.1–§4.9.2; G1, Phase 4) | The runner configures `service`; `report.py` emits the S5 usage fields (USE-6), refuses to serialise without `unwritten`, prints from `finally` and carries no `level`; ≤ 200 info lines per turn. Strategies and the runner never `print`, log through `foundation/observability`, fetch only through the provider client (one retry ladder with a Retry-After clamp, `KeyedRequestUrl`), and add no raw-client allow-list entry. Checked in every swarm review. | P0 |

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
| burn-severity-direct-forward | MTBS EDW `MapServer/63`; Friday current capture | release archive + current snapshot | refuse | Release/snapshot identity; cron `55 8 * * *` | Envelope from the Region (federation offender in `pipeline/direct/burn_severity/{capture,current_snapshot,stage}.py`). Its source protocol, `mtbs.py` and `products.py` move in `p4-extract`. The Friday capture/current_snapshot/stage successor lands inside `pipeline/lanes/burn_severity/**` (CA7), or the owner decides a carve-out (CQ-9); readiness is proved by capture-time correlation (§4.8.3). |
| climate-nasa-power-direct-forward | POWER daily point, UTC, 397 × 1° | range requests | (legacy) | **Replaced** (§6) | Shortwave dropped from its turn and all its products removed from legacy repair at G1 (M5, N6). Cleanup cohort 2: braked at G9Q-2 once RC-6 is green, drained, retired at G9R-2, package deleted at 7D-2 (§4.8). |
| drought-direct-forward | USDM weekly GeoJSON | archive by release date | refuse | `release_series`: 404 = `unsettled`, never absent; valid Tuesday | The cron is narrowed to the release window. Protocol, `usdm.py` and `products.py` move in `p4-extract`. |
| evacuation-zones-direct-forward | Oregon OEM `Fire_Evacuation_Areas_Public/FeatureServer/0` | none | refuse | `static_lookup`, watermark `dataLastEditDate` | `coverage` regional US-OR. Its watermark reader and the `products`, `rows`, `source` and `support` modules it imports move in `p4-extract`. |
| fire-detections-direct-forward | FIRMS area API (`NASA_FIRMS_KEY`) | FIRMS archive (unwired today) | write_and_recheck | Rolling re-fetch reconcile | `refetch_window_days` split from the edge; the 10,000-record cap surfaces as `unwritten`. |
| fire-perimeters-direct-forward | WFIGS `…Perimeters_Current/FeatureServer/0` | none | refuse | `static_lookup`, watermark `editingInfo.lastEditDate` | — |
| sensors-direct-forward | NWS station observations | retention 6 d | write_and_recheck | Rolling 7-day window; emits `unwritten` | Past-retention days are `retention_exceeded`. `merge_sensors_day` moves in `p4-extract`. |
| soil-era5-land-direct-forward | Open-Meteo archive `era5_land`, 1,568 / 1,470 cells | archive to 1950 | refuse (0 or exactly 1,470) | Mirrored-past proof; 14-day absence recheck; one fetch serves 8 streams | **Capped at G0** (33 requests/run, probe-gated; FR-24), 6-hourly and repair-excluded at G1. The config lane's `probe_edge` keeps it gated (S19). The grid is `analysis-0p25`. |
| vegetation-sentinel2-ndvi-direct-forward | Element84 STAC `sentinel-2-l2a` | STAC archive | author derives it from the legacy forward | Writer floor 2026-09-06; lag 7 = measured median | The TOML floor includes 2026-09-01..05, so **G6's gap-fill enable fills those 5 days** (named in G6). The STAC probe is the edge. |
| water-gauges-direct-forward | legacy NWIS IV | — | — | — | **Replaced early** by `water-gauges-daily` (§7a). Paused at G4 (CA18). Cleanup cohort 1 only with the CQ-6 acknowledgement recorded at 7R: drained at G9Q-1b, retired at G9R-1, deleted at 7D-1 (§4.8). |
| watersheds-direct-forward | USGS NHDPlus HR | n/a | refuse | `static_lookup`, watermark `max(loaddate)` | `lifecycle = "discontinued"`. The manifest binds `hydrosheds` while the lane reads NHDPlus HR, so the TOML records the real source. Its watermark reader and `source.py` move in `p4-extract`. |
| weather-observations-direct-forward | Open-Meteo forecast `current=` | `past_days` ≤ 92 | write_and_recheck | Rows per tick; emits `unwritten` | Lag and floor are cited or marked `basis = "uncited"`. It moves to the customer host at cut-over (W). Exempt from the `probe_edge` rule (write_and_recheck). |
| crop-cover-usda-maintain (shadow) | USDA NASS CDL | annual editions | refuse | Edition identity | `enabled = false`; runner scratch dir. Its source protocol (`CropCoverSource`, `USDA_CROP_COVER_SOURCE`) and the `source.py`/`products.py` it imports move in `p4-extract`. |
| land-context-blm-* (shadow) | BLM ArcGIS | per service | refuse | Watermark per service | `enabled = false`. The reconcile mapping is decided at activation (O7). Its source protocol (`LandContextSource`, `BLM_LAND_CONTEXT_SOURCE`) and the `source.py`/`products.py` it imports move in `p4-extract`. |
| mtbs-forward (shadow) | — | — | — | — | **Deleted** in Phase 7 (O7; executor F7): braked at G6 when CA7 names a successor, drained at G9Q-1b, retired at G9R-1 with `--successor none` or the successor; its written snapshots stay (burn-severity reads them). |
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
- **NFR-8 Irreversibility (cleanup lane, §4.8).**
  - The cleanup deletes no data row or object (c7-1); ledger names are soft-retired, never deleted,
    and `--restore` is the way back.
  - Every ledger dry run, `--apply` and variable change is named in its go with the exact command
    line; dry runs execute and roll back; `--apply` is pinned to the dry-run digest (c7-13).
  - The owner runs every variable change; `c7-*` sub-agents never touch production.
  - `PLANTGEO_JOB_EXECUTOR_REPAIR_INTERVAL_SECONDS` stays `0` permanently from G9V-1a, so every
    pre-cleanup image stays safe to redeploy (c7-5).
- **NFR-9 Soft failure (Wave O, §4.9.3).**
  - A Wave O fault never fails a tick (savepoints; fail-open telemetry and arming).
  - A failing lane never stops or delays another lane's planning or dispatch.
  - No lane stays in a state without a defined transition; every hold, paused and inactive ones
    included, has an exit.
  - The process exits on configuration only for a missing DSN or a `Settings()` validation error
    (stated residuals).
- **NFR-10 Log volume and auditability (Wave O, §4.9.1–§4.9.2).**
  - An idle executor writes about 24 heartbeats a day plus state changes and one `lane_turn` per
    terminal outcome; no own line exceeds 16 KiB; child lines per attempt stay within the router
    bounds; successful requests are never logged at info.
  - No secret, URL, header, body, SQL block, parameter or local reaches a log line.
  - `meter_errors` is 0 in steady state.
  - Every upstream send is attributable to a turn and a host from `job_attempt.metrics` alone, and
    every closed month has a durable usage receipt from G1.

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
| A21 | **Class-A lanes keep their ledger identity across the flip** (CA13): the config path keeps the definition name and `EXECUTOR_DEFINITION_VERSION`, and `sql/execution/insert_definition.sql` (`ON CONFLICT (name, version) DO NOTHING`) never re-registers over the stored row. | RC-5(d) compares the stored digest with its T0 value. | Medium: an unavoidable bump needs an explicit lane-wide resume in the G6 go. |
| A22 | **The class-F set is whatever the T0 `--all` census finds** (e.g. the archive walks `routes/AGENTS.md` calls "abandoned"). | CQ-10: retire at G9R-1 only names whose sole driver this lane deletes; a critic reviews the classification. | Low: an unretired name stays enabled but undriven. |
| R4 | **Quarantine breaks dispatch** (`run_scheduled_command` fails an id missing from `LANE_SPECS` or `ACTIVE_LANES`). | CA3; token removal as its own step plus a soak (c7-7); the c7-4 census precondition; `test_config_dispatch_without_legacy_spec.py`; 7Q-1a and 7Q-1b split. | §4.8.6 rule 3. |
| R5 | **A rollback to a pre-cleanup image re-arms legacy repair** (`RepairAuthoringClock.from_environment` reads unset/empty as the 6 h default). | `REPAIR_INTERVAL` = 0 permanently from G9V-1a (c7-5, c7-11). | None. |
| R6 | **Salvage from an unmerged branch or worktree resurrects deleted legacy modules** (22 of 51 unmerged branches touch the legacy path at `d37c202f`). | R6 census; RC-7; WP-9; c7-15; CQ-11. | Low if the rule holds. |
| R7 | **A brake is silently undone** (an upsert through `ensure_job_definition`, or the unaudited `/admin/jobs` `toggleLane`). | CA2 registration pin; applied brake drills (G9V-1a, CA19); `unaudited_enabled_changes` is an RC-4 trigger. | Low. |
| R8 | **Owed availability claims are stranded under frozen roots** (only the legacy forward writers retry them, e.g. `pipeline/direct/climate/forward.py::_retry_owed_availability`). | RC-5(e); CA18; CA20; 7R recovery with `data availability-reconcile-physical`; c7-10. | Low: recovery writes availability evidence only. |
| A23 | **Railway reads the stderr JSON `level` field**, and its per-replica log-rate and line-size limits admit the §4.9.1 bounds. | The coordinator reads Railway's docs before GL-1 and records the facts in `foundation/observability/AGENTS.md`; if stderr is tagged by stream, one-shot verbs stay mis-tagged (a stated residual). | Low. |
| A24 | **Open-Meteo bills logical requests**, not every wire send, and not failed or 429 sends. | `CHARGE_BASIS=metered` (the larger figure) until P5 reads the portal; suspect turns are charged at the lane's logical cap in their own column. | Low: only the admission lines move. |
| A25 | **The service tree becomes quiescent soon after G0's observation**: the publication-debt merge and the other session's `agent/**` edits land. | Launch waits (wave-o-1); the coordinator asks the owner if quiescence has not come within 48 h of G0's observation closing. | Low: each day of waiting delays Phase 1 by a day. |
| A26 | **A standing publication-debt gauge is real debt, not noise.** Once publication debt counts, a writer that prints a standing `availability_quarantined_standing` every bucket stays `incomplete`, so its `lane_incomplete` (reason `publication_debt`) reaches error at 24 h. | The GL-5 critic and observation row 11 check it against the lane's pending claims; a genuine quarantined claim is the same debt RC-5(e) tracks. | Low. |
| R9 | **A self-probing hold spends quota on a broken lane.** | One attempt per probe; never more often than the cadence; ladders 6/12/24 h then daily (code) and 1–24 h then daily (upstream); the fourth inconclusive probe counts as failed; the flapping alarm; soil relapse bounded (probation 3 × 1 × 1,602 = 4,806; watch 9,612; outside the watch 24,030, as at HEAD); `CODE_PROBE_HOURS=""` or `SOFT_FAILURE=off`. | None (a variable). |
| R10 | **A redaction miss puts a secret in Railway's logs.** | The exact-value scrub runs first; the SQL-block cut; `/security-review` on GL-1–GL-3; observation row 3's secret probe; any hit is a rollback trigger. | Medium: the owner must rotate a leaked key. |
| R11 | **Wave O's executor edits collide with Phase 1.** | Sequenced co-owners (`o5a` → `o5b` → `f1-executor`); `f1-config` depends on `o5b-soft-failure`; `f1-executor` extends the GL machinery and never rebuilds it. | Low. |
| R12 | **Log volume exceeds Railway's rate limit**, or a child floods. | The router's rate bucket and per-attempt ceilings; tick demotion; observation row 5. | Low. |

(A7 is withdrawn: pruning is consistent with `layer-lanes.md` §2; see §4.6.)

## 10. Open questions (owner)

None for Phases 0–6. Round 1 (§3.1), round 2 (§3.3), the loop-2 decisions (§3.4) and the round-3
decisions (§3.5) were answered 2026-09-26. Probe-dependent facts (P1–P5, weather-observations W) are
measurements with stated defaults, not owner decisions.

The cleanup lane (§4.8) carries **CQ-1–CQ-12** (§18.5), each with a recommended default. **CQ-6 and
CQ-10 block the 7R cohort cut; CQ-8 is needed at G1 (for CA17) and CQ-9 before G6.**

Wave O's WQ-1–WQ-7 were answered 2026-09-27 (§3.6). Revision 2 of its design has not been
re-reviewed as a whole (§19); whether to run one more critic pass on it before GL-1 is the only open
point, and the default is no separate pass (§19.4).

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
- **Owner: a failure heals or waits, never spreads.** As the owner, I want a failing lane to retry
  itself on a bounded schedule and never stop another lane.
  - *Given* a legacy lane exits 1 with a 503 from its source, *then* its hold is `upstream`, it is
    probed once at 1 h, 2 h, 4 h …, and it resolves after two clean buckets.
  - *Given* a code error, *then* it is probed once at 6 h, 12 h, 24 h, then daily, and
    `/admin/jobs` shows one incident row per episode.
  - *Given* one lane's planning raises, *then* every other lane still runs that tick.
- **Operator: are we abusing a source?** As an operator, I want to see what each lane asks of each
  provider.
  - *Given* GL-4, *when* I run `agri-service ops jobs-usage-report --by host`, *then* I see sends,
    429/5xx rates, retry amplification and backoff share per host, and every send carries the
    WQ-5 User-Agent.
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
- **Owner: supersede only when verified.** As the owner, I want the old path removed only when the
  new one is proven, and never my data.
  - *Given* a lane's READY(L) row has every RC green, *when* I give the G9Q and G9D gos, *then* its
    legacy code is gone, its history still serves, and `jobs-lane-census` shows its retired name
    disabled with an audit row.
  - *Given* a trigger fires inside the window, *then* the lane's clock restarts and it is not cut.
- **Next-region deployer.** As the deployer of the next region, I want to bind sources by writing
  a TOML, a strategy and a manifest lattice entry. A source whose `coverage` excludes the envelope
  is refused at startup with a named reason.

## 13. Out of scope

- The watersheds move to 3DHP.
- Activating shadow lanes.
- ML retraining on the new wind and precipitation semantics (a follow-up).
- Deleting legacy climate or water partitions.
- Any data purge by the cleanup lane: ledger rows, `job_checkpoint` rows, partitions, availability
  generations and pending claims, calendar versions, `source-response-checkpoints/v1/`,
  `schema-baselines/`; deleting a Railway service, branch, worktree, `.agri-local-runs/` or `plans/`
  content (G9X, never scheduled; §4.8.7).
- Renaming legacy ids, or renaming UI layer keys (`climate-field-*`, `water-gauges`).
- Recovering the sensors retention gaps.
- Running PlantGeo locally.
- Making `LANE_REGISTRY` lazily loaded (a follow-up that would let S18's mirror become synthesis).
- Changing legacy soil's thin-day behaviour (it stays a raise; the config runner's `refused_partial`
  replaces it at G6).
- Any production mutation without an explicit owner go (plan gate list).
- Alerting on Wave O incidents (webhook, email, paging): "another time" (the owner); incident rows
  and escalation events are the hook points.
- Quota pools and reserves for the free Open-Meteo tier, FIRMS and USGS Water Data, and the interim
  free-pool brake (declined, WQ-4).
- Reconfiguring the `agent` CLI group or `agent/**` logging, and `plantgeo-ml-service` logging
  (outside Wave O).

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

## 18. Owner request 2026-09-26: dedicated cleanup lane

**Request (verbatim):** "this lane should include a dedicated clean up lane for when we are verified
ready to supersede"

**Meaning recorded.** The track gets a dedicated cleanup lane that fires only once a migrated lane
(and finally the whole legacy path) is **verified ready to supersede** its legacy counterpart, and
removes everything the legacy path **and** the track's own temporary coexistence/rollback scaffolding
leave behind (code, tests, config, env vars, ledger state, object-store residue, crons, docs, skills,
memory), while keeping what the owner decisions say must stay (legacy `climate-field-*`,
`soil-wetness-*` and `water-gauges` history readable; soil wetness retired; production mutations
only on an explicit owner go).

**Where it landed (2026-09-27, HEAD `d37c202f`).** §4.8 (definition, verbs, amendments, coverage),
FR-20 restructured, FR-26–FR-29, NFR-8, A21–A22, R4–R8; plan Phase 7 (the lane), the G9 gate family
and the cleanup tripwires; `metadata.json` → `owner_requests`, `owner_go_steps` G9.0–G9X and
`partitions` (`c7-*` slices replace `d7-legacy-shared` and `d7-legacy-lane-modules`; tripwires
c7-1–c7-16). The partition stamps stay at `85c4b8f4`; the `c7-*` owns lists were verified at
`d37c202f`, whose diff from `0df6ac50` touches only coordination files. `i-coordinator` recomputes
`computed_at_commit` and re-greps every `c7-*` owns list before the first `c7-*` launch (tripwire
index 0).

**Design process.** Workflow `design-supersession-cleanup-lane` (`wf_36b80a59-49e`); record
`.omc/research/ingestion-grill-20260926/cleanup-lane-design.md` (+ `.result.json`).

1. **Inventory:** 81 items (62 legacy, 12 track-bridge, 2 G0 stopgap, 5 other), 21 gaps in the
   former Phase 7, 31 readiness criteria (Q-01–Q-31).
2. **Three designs, one judge** (§18.1).
3. **Critic round 1:** 45 findings, all disposed (§18.3) → revision 1.
4. **Critic round 2:** 43 findings, all disposed (§18.4) → revision 2, written here. Revision 2
   itself has had no third round; its next adversarial passes are the 7R dossier critic and the
   per-push reviews (plan §7.4).

### 18.1 Score table (round-0 judgement, unchanged by both rounds)

| criterion | rolling-per-lane | batch-after-all | risk-first-staged |
|---|---|---|---|
| (a) safety and irreversibility | 7: per-lane token closure, the `REPAIR_INTERVAL` order trap, the brake fix; but it schedules an irreversible checkpoint prune that `infra/job-executor/AGENTS.md` forbids, and misses K3 | 7: deletes no data, strong retire verb; but every runtime risk lands in one push and the only rollback is a whole-batch revert | **8**: quarantine, observe, delete; drains work, proves config dispatch, deletes no data, fences off G9X (its round-0 brake gap is fixed by rounds 1–2) |
| (b) completeness | 9 | 8 | 9 |
| (c) buildability | 5 | 8 | 7 |
| (d) fit with owner decisions and push-small | 7 | 6 | 8 |
| (e) cost and latency | 4 (~15 pushes) | 8 (~3 pushes) | 6 (7 pushes) |
| **total** | 32 | 36 | **38 (chosen spine)** |

### 18.2 Baseline facts (verified read-only at `d37c202f`)

| # | fact | evidence | effect |
|---|---|---|---|
| K1 | HEAD `d37c202f` touches only coordination files since `0df6ac50` (G0); the 1,584 soil bound is already in plan, spec and metadata (tripwire index 2). | `git diff --stat 0df6ac50 d37c202f` | CA15 withdrawn (verify-only); a later commit on a c7 owns path forces an R1 re-grep. |
| K2 | `execution/lane_ids.py` is imported by `lane_specs.py`, `gap_repair_contract.py` and tests; `vegetation_partition_promotion.py` names it only in a docstring. | grep | Constants go at 7Q; `pipeline/constants.py` is KEEP. |
| K3 | `job_executor_service.py::run_scheduled_command` fails a lane missing from `LANE_SPECS` (`unknown_executor_lane`) or `ACTIVE_LANES` (`ownership_activation_removed`). | file | CA3 decouples both. |
| K4 | `interface/cli/ops.py` registers nine verbs (`seed`, `db-status`, `db-upgrade`, `job-logs-maintain`, `jobs-executor`, `jobs-supersede-run`, `jobs-plan-gap-repair`, `jobs-set-lane-enabled`, `export-expert-labels`); none cancels work. `job_lane_control.py::resolve_definition` matches only executable `LANE_SPECS` names; `job_run_supersession.py::resolve_executor_lane` needs a spec and an active token. | files | New drain verb (§4.8.8); CA2. |
| K5 | `RepairAuthoringClock.from_environment`: unset or empty = the 6 h default (repair on); `0` = off. | `job_executor_service.py::RepairAuthoringClock` | c7-5: held at 0 permanently. |
| K6 | Lane-control audit incidents are written `info`/`resolved`, with no run and no item. | `sql/execution/insert_executor_lane_control_incident.sql` | Not residue. |
| K7 | `mtbs-forward` runs `pipeline.direct.burn_severity` weekly into `layer=burn-severity/kind=observed`; the snapshot catalogue records no producer. | `lane_specs.py`; `warehouse/mtbs_snapshots.py`; `parquet_ops/mtbs_snapshot_catalog.py` | Producer evidence by capture-time correlation (§4.8.3). |
| K8 | `SOIL_MAX_DAYS: Final = 1`; the probe knobs are `Final` constants with no env reader. | `pipeline/direct/soil/forward.py`, `source.py` | B-05 and B-13 have no env-var half. |
| K9 | `lane_specs.py` imports `VEGETATION_DIRECT_WRITER_START_DAY`; the kept `vegetation-ndvi-governed-plane-promotion` spec's `writer_floor` reads it. | grep | Re-pointed at 7Q-1b, never deleted. |
| K10 | `pipeline/parquet/source_checkpoint.py` is imported only by legacy climate, soil and weather_observations. | grep | Module deleted at 7S; the objects are KEEP. |
| K11 | The executor image's quality-receipt stage copies `src tests scripts alembic db`; `quality_receipt.py::digest_input_paths` silently skips a missing directory. | `infra/job-executor/Dockerfile`; `scripts/quality_receipt.py` | CA4 at G1 (blocker). |
| K12 | The infra rule reconciles `LANE_SPECS`, the Dockerfile COPYs and `ACTIVE_LANES` as one change. | `infra/job-executor/AGENTS.md` | G9V-n and G9Q-n do it deliberately in two steps. |
| K13 | The shadow lanes keep their `_REFERENCE_DATA_SPECS` and are absent from `ACTIVE_LANES` on purpose. | `lane_specs.py` | The final "prior ∩ catalogue" is in practice empty. |
| K14 | §7a: "After G4, re-point the web back and resume the legacy lane until the Q1 2027 legacy decommission." | §7a, §4.7 | CQ-6 is answered at 7R, before water joins any cohort. |
| K15–K18 | History is KEEP; Q-08 is a normal QA row; the D-10/D-05 facts; the canonical builders are manifest-pinned history tooling. | design round 1 | — |
| K19 | `sql/execution/insert_definition.sql` uses `ON CONFLICT (name, version) DO NOTHING`, and every later version starts disabled. | SQL header | CA13 decided: no version bump. |
| K20 | Scheduled payloads are `{"lane_id","scheduled_for"}`; the argv is rebuilt at pickup. | `_resolve_command` | RC-5(c) inspects no payloads. |
| K21–K24 | Filesystem pins; Python, agent and ML consumers; `parse_activation` raises on unknown tokens; `geo.features` is gone. | design round 1 | — |
| K25 | The G0 worktree and branch are gone (`g0-final.patch` is in HEAD), but `git worktree list` shows 16 other worktrees, and 22 of 51 unmerged local branches touch `pipeline/direct/**`, `lane_specs.py`, `gap_repair*.py` or `job_executor_service.py` (largest: `freshness/integrated-candidate-20260914`, 28 files). RUNBOOK "Session 22 wave 9": stale `.tmp` worktrees are "assessed and salvaged, not deleted". | `git worktree list`; merge-base diffs | R6 census, RC-7, c7-15, CQ-11. |
| K26–K30 | `DEPRECATED_ALIASES.md`; served `*_basis` strings; the pending `vegetation_type` track; the `:(glob)` rule; `ingest-backfill` has no registration. | design round 1 | — |
| K31 | `sql/jobs/refresh_job_run_rollup.sql` counts `cancelled` items as `failed`; `sql/execution/select_latest_run.sql` counts only `failed`/`partial` runs toward `consecutive_failures`. | files | The drain writes `cancelled` itself and never calls the rollup. |
| K32 | `jobs/lease.py::reclaim_expired_leases` runs only inside a driven slice, scoped to `queued`/`running` runs. | `jobs/lease.py`, `jobs/worker.py` | The drain reaps expired leases itself. |
| K33 | `_load_or_register_definition` inserts with `enabled = not pause_state.registered` and returns `None` when paused; `jobs/worker.py::ensure_job_definition` upserts `enabled = EXCLUDED.enabled` (the `jobs/dispatch.py` docstring warns it un-pauses a lane). | files | CA2 pins the registration path. |
| K34 | `_plan_repair_runs` loops over `REPAIR_LANE_IDS`, skipping a lane only if its spec is missing or its token inactive; after G1, `REPAIR_BINDINGS` holds vegetation, drought, weather-observations and sensors. | file; tripwire index 27 | CA8 also covers driving; `:gap-repair` names are drainable. |
| K35 | `src/lib/server/trpc/routers/jobs.ts::toggleLane` updates `enabled` on any name, across all versions, with no audit. | file (`f1-executor` owns it) | Known bypass; census flag; follow-up. |
| K36 | The CLI root registers only `data`, `ops` and `agent` (`parquet` exists only as `data parquet`); `data parquet coverage` takes no options (29 s timeout); `data parquet day` needs `--layer --zoom --day`; `data availability-reconcile-physical --apply` is pinned by `--expected-sha256 --expected-head-generation`. | `interface/cli/{root,data,parquet,availability}.py` | Every plan §7.5 command rewritten; the digest-pinned apply precedent reused. |
| K37 | Pending claims live under `layer=<slug>/kind=<k>/availability/pending/day=*.json` (`.quarantined.json` when parked; `pipeline/parquet/objectstore.py::_AVAILABILITY_RETRY_SEGMENT`); only the legacy forward writers drain them (`_retry_owed_availability` in climate, soil, vegetation and `water_gauges_forward`), plus `run_gap_fill` and `drain.py`. | files | RC-5(e), CA18, CA20, c7-10. |
| K38 | Bare `climate-field` hits the old suffix regex missed: `agent/selection_scope.py::support_lattice`, `src/lib/environmental/climate-field.ts`, `src/lib/map/climate-field-layer-ids.ts`, `src/lib/server/services/parquet-climate-field.ts` (`LANE_BASE_LATTICES["climate-field"]`), `RegionalIntelligencePanel.tsx`. | rg | G-7 uses bare tokens. |
| K39 | `tests/test_sql_tree_conventions.py` rule (d): every SQL file is loaded by exactly one `load_query_sql(...)` call. | file | `c7-verbs` owns its own SQL files. |
| K40 | `JobIncident` has no `job_definition_id` (it links through nullable `job_run_id`/`job_work_item_id`); fingerprints `supersession_fingerprint(run_id)` and `process_start_release_fingerprint(deployment, lane_id)`; `JobRun.cancellation_reason`; a terminal status needs `completed_at`; the counter constraint is IMMEDIATE; `JobWorkItem` CHECKs pair the two lease columns and require `completed_at` on terminal items. | `models/jobs.py` | §4.8.8 incident rule and drain SQL shape. |
| K41 | Ten `PlantGeo*` scheduled tasks, all Disabled; three reach deleted launchers or a retired source: `PlantGeoStreamflowArchiveBackfill`, `PlantGeo-FIRMS-archive-backfill`, `PlantGeo-NASA-SoilWetness-continuation`. | `Get-ScheduledTask` (local, read-only) | CQ-7 covers all three. |
| K42 | Orphan candidates: `ingest/wfigs.py` (imported only by `pipeline/direct/fire_perimeters`), `ingest/mtbs.py` (only by `pipeline/direct/burn_severity`), `ingest/policy.py::resolve_weather_sample_spacing_degrees` (called by `pipeline/direct/weather_observations/support.py`), the `ingest/sensors.py` station resolvers (only by `pipeline/direct/sensors`). | rg | R5 orphan census on landed code; d1 CONDITIONAL owns. |
| K43 | `conductor/code_styleguides/python.md` says "An active `pipeline/direct` forward writer is a package"; `federation.md` lists the burn_severity capture modules as offenders; `conductor/layer-sessions/*.md` has zero hits. | rg | Banner at 7Q-1a, rewrite at 7S. |
| K44 | `scripts/build_nasa_power_from_canonical_snapshot.py` re-exports frozen climate history (sibling of the era5 builder); its only legacy tie is a collision-guard constant. | docstring | KEEP both builders, re-pointed. |
| K45 | `partitions.tripwires` is a 0-based list of 34 entries: index 2 = the soil bound (already 1,584), 6 = the `pipeline/lanes` rule, 12 = authors run no tests. | metadata | Replacements keyed by 0-based index + asserted `old_prefix`. |
| K46 | Git-ignored local state: `.agri-local-runs/` (`nasa-firms-archive-walk`, `historical-nasa`, `mtbs`, `soil-wetness-root-zone-parquet-cutover`, `burn-severity-cutover-backup`, `locks`, …), `plans/`, `.mpg/*.json` palaces. | ls | 7S dispositions rows. |
| K47 | `routes/AGENTS.md` says the ledger of the two archive walks is "abandoned"; those walk definitions sit outside `plantgeo.executor.*`. | file | T0 census with no prefix (class F). |
| K48 | s-* slices own `docs/lanes/<layer>.md`; w3 and p6 co-own `parquet-trpc-readers.test.ts`; `p4-extract` owns the `sensors/adapter.py` shim and `parquet_ops/coverage.py`. | metadata | Co-owner entries. |

### 18.3 Review round 1 disposition (as refined by round 2)

| id | verdict | what changed |
|---|---|---|
| COM1-01, SAF1-03 | accepted | `tests/test_layer_import_contract.py` gets one d1 edit under critic review; tripwire index 6 reworded |
| COM1-02 | accepted | `tests/foundation/test_geography_bounding_box.py` goes to d1; G-1b |
| COM1-03, SAF1-02 | accepted | CA11; G-7 widened (now bare tokens) |
| COM1-04, BUI1-04 | accepted | vegetation start-day import re-pointed |
| COM1-05 | accepted | the soil bound is read from `pipeline/direct/soil/AGENTS.md`; CA15 later withdrawn as already applied |
| COM1-06 | partly rebutted | builders are history tooling; sub-prefixes are classified |
| COM1-07 | accepted | CA14; README in df F7 |
| COM1-08 | accepted | the 7S dispositions table covers this lane's artifacts |
| COM1-09 | partly rebutted | the G0 worktree is gone (other worktrees handled in round 2) |
| COM1-10 | accepted | per-version digests; CA13 (decided in round 2) |
| COM1-11 | accepted | G-12 and G-13 (G-13 made mechanical in round 2) |
| COM1-12 | accepted | full co-owner lists (reworked in round 2) |
| COM1-13 | accepted | sensor library joins d1 conditionally |
| COM1-14 | accepted | root `.env.example`; the `usgs_nwis` binding KEEP |
| COM1-15 | accepted | infra docs df F8; watch-path read |
| COM1-16 | accepted | launchers sc S7 (caller census added in round 2) |
| COM1-17 | accepted | X-ids replaced; the sentinel-runbook question closed |
| SAF1-01 | accepted | CA3 and CA12; G9V step |
| SAF1-04 | accepted | sensor RETIRE-SAFE row |
| SAF1-05 | accepted | RC-5(c) rewritten (again in round 2) |
| SAF1-06, BUI1-01 | accepted | drain verb (redesigned in round 2) |
| SAF1-07 | accepted | `REPAIR_INTERVAL` 0 (now permanent) |
| SAF1-08 | accepted | `mtbs-forward` producer evidence (now by time correlation) |
| SAF1-09 | accepted | deny-list; cross-track grep |
| SAF1-10 | accepted | "who runs" column; go contents |
| SAF1-11 | accepted | water acknowledgement (moved to 7R in round 2) |
| SAF1-12 | accepted | `:gap-fill` braked and probed |
| SAF1-13, BUI1-11 | accepted | `MAX_LANES_PER_TICK` unset at G9S |
| SAF1-14 | accepted | `forecast_module` untouched |
| SAF1-15 | accepted | concrete docs list |
| BUI1-02 | accepted | explicit replace and add lists |
| BUI1-03 | accepted | spec-lookup tests in q1/q2 |
| BUI1-05 | accepted | amendments as `amend_slices` (here: CA1–CA20 folded into each slice's task) |
| BUI1-06 | accepted | `c7-verbs` after G8 |
| BUI1-07 | accepted | a non-default cohort is a partition revision |
| BUI1-08 | accepted | pin by full name; repeatable successors |
| BUI1-09 | accepted | `c7-readiness` is grep-only |
| BUI1-10 | accepted | AGENTS.md ownership |
| BUI1-12 | accepted | baseline re-verification (now `d37c202f`) |
| BUI1-13 | accepted | sub-gates count as phases |

### 18.4 Review round 2 disposition

| id | verdict | what changed |
|---|---|---|
| COM2-01 | accepted | RC-5(e); water and climate RETIRE-SAFE claim rows (incl. shortwave, frozen since G1); `prefix-census` `open-claim` class; WP-4; c7-10 (terminal receipt only after the pending prefix is empty); ic P6 recovery by `data availability-reconcile-physical` under a go; CA18 and CA20 (K37). |
| COM2-02 | accepted | Every command rewritten to `agri-service data parquet ...` (K36); the coverage row runs the whole census with a retry-then-`prefix-census` rule; `prefix-census --layer`; the `test_cleanup_command_lines.py` CliRunner pin; R-07 enumeration fixed. |
| COM2-03 | accepted | R7 caller census (task `.Actions`, repo, memory) as the sc S7 precondition; CQ-7 covers three tasks (K41); G-14 with a history allow-list. |
| COM2-04 | accepted with an evidence correction | R5 orphan census at 7R and after each 7D; d1 CONDITIONAL owns for `ingest/wfigs.py`, `ingest/mtbs.py` and the policy and sensors symbols; G-6 "reader" means a reachable caller. Correction: `resolve_weather_sample_spacing_degrees` is called today, and `ingest/mtbs.py` is text-cited but not imported by `planes/burn_severity.py` and `lane_registry.py` (K42). |
| COM2-05 | accepted (merged with SAF2-09) | R6 census, RC-7, WP-9, c7-15, a RUNBOOK standing rule, a checklist row; no branch or worktree is deleted (G9X). |
| COM2-06 | accepted | T0 census `--all` with per-name classification; class F; CQ-10; pin extended by `c7-quarantine-1` after critic review; `dispatch_registered` liveness reason; the preserve-set row (re-retire after any restore). |
| COM2-07 | partly accepted, partly rebutted | Styleguides bannered at 7Q-1a and rewritten at 7S (df F10); G-9 covers `conductor/**` excluding `tracks/**`. Rebutted for `conductor/layer-sessions/*.md`: zero citations at HEAD (K43). |
| COM2-08 | accepted | G-13 built mechanically from the deletion, retire and unset sets; the `.mpg` row; the FIRMS-cap memory line updated at ic C3. |
| COM2-09 | accepted (merged with BUI2-08) | `c7-docs-final` owns `ingest/AGENTS.md`, the two web AGENTS.md files, three web comment files and two ingest tests; G-12 covers `src/**/*.{ts,tsx}`, G-9 web AGENTS.md, G-3 `src/**/AGENTS.md`; web sweep at G9S. |
| COM2-10 | accepted | K1 rebased to `d37c202f`; CA15 withdrawn; RUNBOOK cited by heading. |
| COM2-11 | accepted | ic C1 retrospective row; ic C4 `tracks.md` flip; WP-5 and checklist rows. |
| COM2-12 | accepted | sc S6 scope by grep; the `ingest/firms.py` `jobs-firms-archive` note in df F6. |
| COM2-13 | accepted | Dispositions rows for `.agri-local-runs/` and legacy `plans/` files: KEEP unless the CQ-7 answer names them. |
| COM2-14 | accepted | `prefix-census --top-level` with a pinned known-infra list; T0/T5 counts for `source-response-checkpoints/v1/`; WP-4. |
| SAF2-01 | accepted | Runtime liveness predicate (§4.8.8); the drain disables versions itself, cancels expired-lease items, writes `cancelled` runs without the rollup (K31); §4.8.6 climate rollback adds supersede-if-held; class-A leftovers via RC-5(c) plus a carve-out or a named pin. |
| SAF2-02 | accepted | G-7 bare tokens (K38); CA11 adds `selection_scope.py` to p6; climate NOT READY until every hit is classified. |
| SAF2-03 | accepted | CA2 registration pin with a no-dispatch test; applied brake drill at G9V-1a, and CA19 at G6. |
| SAF2-04 | accepted (merged with BUI2-06) | CQ-6 answered at 7R before the cohort cut; on a decline, water never enters cohort 1 (token, spec and chain kept). |
| SAF2-05 | partly accepted, partly rebutted | Accepted: CA8 covers driving, tested at G1 (K34). Rebutted: a G6 disable step — the only way to disable a `:gap-repair` row at G6 is the unaudited `toggleLane`, and the tested guard makes it unnecessary; the G6 go records open `:gap-repair` runs read-only; the drain happens at G9V-1a. |
| SAF2-06 | accepted | `REPAIR_INTERVAL` 0 permanently: R-05, c7-5, c7-11, WP-2, WP-3, the dispositions table, §4.8.6 rule 6. |
| SAF2-07 | accepted | CA16 amends the plan §Rollback heading and adds the "after 7Q-n" line (applied in this revision). |
| SAF2-08 | accepted (merged with BUI2-12) | Tripwire replacements keyed by 0-based index + asserted `old_prefix`; index 2 left alone; CA15 dropped (K45). |
| SAF2-09 | accepted | See COM2-05; K25 corrected (16 worktrees, 22 legacy-touching branches). |
| SAF2-10 | accepted | No waiver: dry runs execute and roll back against production; `--apply` pinned to the dry-run digest (c7-13); the DB-gated test is supplementary; CQ-12 asks consent. |
| SAF2-11 | accepted | `unaudited_enabled_changes` (an RC-4 trigger); `--restore` re-applies the recorded per-version state; `toggleLane` dispositions row plus a follow-up (K35). |
| SAF2-12 | accepted | G9V-1b and G9V-2 soak until every cohort lane and each `<L>:gap-fill` has fired once after the change, before the 7Q push. |
| SAF2-13 | accepted | §4.8.3 static_lookup row requires a config-path write (a watermark change or CA17 `--republish-current`); otherwise a carve-out; CQ-8. |
| SAF2-14 | accepted | Both canonical builders re-pointed and KEPT (C-29, N-72, K44). |
| SAF2-15 | accepted | CA13 decided (same version, `DO NOTHING`); RC-5(d) compares the stored digest with its T0 value and the runtime schedule with the TOML. |
| SAF2-16 | accepted | c7-16: export the task XML to `ops-archive/local-tasks/`, keys in the go (ic P7). |
| BUI2-01 | accepted | See SAF2-01; V5 covers a braked, token-removed definition whose spec still exists, and a `:gap-repair` name whose owner is still bound. |
| BUI2-02 | accepted | `close_drained_runs.sql` writes `cancelled` plus inline counters in one statement, with no rollup call; the DB-gated test pins it. |
| BUI2-03 | accepted | `c7-verbs` owns eight SQL files and loads each once (K39). |
| BUI2-04 | accepted | The drain cancels expired-lease `leased`/`running` items and closes their attempts; DB-gated case added. |
| BUI2-05 | accepted | Attribution plumbing dropped (no descriptor or receipt change); evidence is run windows × capture LastModified; CA7 revised; "no successor" routed through `i-coordinator` (CQ-9). |
| BUI2-06 | accepted | See SAF2-04. |
| BUI2-07 | accepted | `lanes/*.toml` replaced with explicit owners; the prose co-owner entries expanded; `docs/lanes/*.md` lists w3 and the s-* slices; the first-owner-per-file map is only a note; merged check: 0 cycles, 0 unordered pairs, 0 undeclared overlaps. |
| BUI2-08 | accepted | See COM2-09; `usgs_nwis\.py` added to G-4. |
| BUI2-09 | accepted | RC-5(c) and c7-4 restated (unmarked items and `gap-repair-command` only for class-A names); q1 Q1 covers a config-marked in-flight item. |
| BUI2-10 | accepted | §4.8.8 incident-match rule in one shared SQL fragment, pinned in tests. |
| BUI2-11 | accepted | The CA2/CA3 fallback is removed from `c7-quarantine-1`; a missing CA2/CA3 is a hard RC-8 stop at 7R. |
| BUI2-12 | accepted | See SAF2-08; K4 corrected (nine registrations). |
| BUI2-13 | accepted | `c7-docs-banners` launches with `c7-repair-retire` and lands in G9Q-1a (i-coordinator enforces); thin CLI delegates; q1 split into `c7-repair-retire` (G9Q-1a) and `c7-quarantine-1` (G9Q-1b); a 7D diff over 500 lines splits by lane group. |

### 18.5 Owner questions (CQ = design-record OQ)

Each has a recommended default. **CQ-6 and CQ-10 block the 7R cohort cut.**

| # | question | recommended default | when needed | cost if reversed |
|---|---|---|---|---|
| CQ-1 | **Timing:** the RC-3 floor, the 7O length, the cohort-1 start, the G9V soak. | 14 days from T0; 7O = max(3 days, one fire of every cohort lane); cohort 1 after G8; soak = one post-change fire of every cohort lane and `<L>:gap-fill` (up to about a week because of weekly drought). | 7R | Shorter floors miss weekly effects. |
| CQ-2 | **The shadow lanes' legacy path.** | KEEP the `executor = "legacy"` arm, the catalogue branch, the allow-list parse and `ACTIVE_LANES`, pinned to the shadow ids; unset the variable only once CA12 exists and is tested. | 7S | Shadow strategies would go live unvalidated. |
| CQ-3 | **The S16 switches** (`DISPATCH`, `BREAKER_MODE`). | Retire them at 7S after ≥ 30 days unused; `MAX_LANES_PER_TICK` is unset with them. | 7S | A code change to re-add. |
| CQ-4 | *(closed)* Legacy repair after G6. | CA8 covers authoring and driving; `REPAIR_INTERVAL` = 0 permanently from G9V-1a. | — | — |
| CQ-5 | *(closed)* The sentinel runbook. | Closed from evidence (K24): bannered "superseded, never executed". | — | — |
| CQ-6 | **Water's rollback promise.** §7a promises "resume the legacy lane until the Q1 2027 legacy decommission". | **Acknowledge at 7R**, before cohort 1 is cut. On a decline, water stays out of cohort 1 entirely (token, spec and chain kept) until a later cohort or Q1 2027. | **7R (blocking)** | One more cohort pair; dead code kept in the sweep. |
| CQ-7 | **Local scheduled tasks and local state** (`PlantGeoStreamflowArchiveBackfill`, `PlantGeo-FIRMS-archive-backfill`, `PlantGeo-NASA-SoilWetness-continuation`). | Export each task's XML to `ops-archive/local-tasks/`, then unregister the three at G9S; KEEP `.agri-local-runs/` and `plans/` unless you name directories; the other seven tasks stay. | G9S | Re-register from the exported XML. |
| CQ-8 | **Static_lookup lanes with no upstream change** (watersheds is discontinued). | Add CA17 `--republish-current` at G1; you approve one republish per lane before its 7D. | G1 (CA17), 7R | Without it these lanes stay carve-outs indefinitely. |
| CQ-9 | **Burn-severity Friday capture**, if `s-burn-severity` returns "no successor". | **Carve-out:** the legacy capture path stays until a successor lands; dropping the capture is your explicit call. | before G6 | Dropping loses current-snapshot publishing. |
| CQ-10 | **Unprefixed legacy-path `job_definition` names** from the T0 `--all` census (e.g. the archive walks). | Retire at G9R-1 through the pin `c7-quarantine-1` extends, only names whose sole driver this lane deletes; leave others untouched. | **7R (blocking for class F)** | They stay enabled but undriven. |
| CQ-11 | **Salvage rule for the 22 legacy-touching branches and 16 worktrees** (and the `archive/*` tags the 2026-09-27 worktree triage leaves). | Adopt the RUNBOOK standing rule: after G9D-n, salvaged hunks on the deleted legacy path are dropped, never re-applied; every branch gets a per-cohort disposition at 7R; this lane deletes none. | 7R | A later salvage could resurrect deleted modules. |
| CQ-12 | **Production rolled-back dry runs** of the two ledger verbs (they briefly lock rows on braked definitions). | Yes, under each gate's go, with lock and statement timeouts; this replaces the waivable local real-DB proof. | G9V-1a | Otherwise ledger UPDATEs ship proven only against fakes and a PG16 recipe. |

### 18.6 Former Phase-7 slices

References to `d7-legacy-shared` and `d7-legacy-lane-modules` in §14–§17 are historical. Their work
now lives in: `d7-legacy-shared` → `c7-repair-retire` (7Q-1a) + `c7-quarantine-1` (7Q-1b) +
`c7-quarantine-2` (7Q-2); `d7-legacy-lane-modules` → `c7-delete-1` (7D-1) + `c7-delete-2` (7D-2).
The N8 chain `g0-soil-cap` → `f1-legacy-bridge` → `d7-legacy-shared` for `pipeline/direct/AGENTS.md`
(§16.2 P9(ii)) now ends at `c7-delete-1` (cohort sections) and `c7-delete-2` (climate section).

## 19. Owner request 2026-09-26: observability + soft-failure wave

**Request (verbatim):** "we may also want to port and extend a robust logging set up that gets
applied in all lanes and make sure we have graceful soft failure error handling. we can set up
alerting specifically another time, but for now we dont want runs to stop in a way that they break
permanently or break other lanes. we want to be able to look at logs for efficiency and general
auditability to make sure we are not abusing our sources. may be good to spawn a dedicated wave for
this."

**Meaning recorded.** A dedicated wave (Wave O) ports the web app's structlog chain into one
redacted logging contract for every lane and the executor; meters and attributes every upstream send
per host and per turn; publishes a read-only source-usage report for efficiency and source-abuse
auditing; and makes failures soft: recorded on incident rows, never failing a tick, never stopping
another lane, never leaving a lane without a defined exit, and, from G1, healing through bounded
self-probing holds. Alerting is deferred.

**Where it landed (2026-09-27, HEAD `d37c202f`).** §3.6 (owner answers), §4.9 (the wave), §4.4 and
FR-8 revised (WQ-1, WQ-2), §4.7 (Wave O rollback), FR-30–FR-38, NFR-9–NFR-10, A23–A26, R9–R12, two
user stories and three out-of-scope rows; plan Phase 0W (GL-1–GL-5 with the design's §6 test names,
sweep proofs, observation rows and §7 queries), GL-6 folded into 1D, Wave O tasks in 1A, 1B, 1C, 1G,
Phase 2 and Phase 4, the c7 references in plan 7.2, the GL owner gates and tripwires;
`metadata.json` → `owner_requests`, `owner_decisions_20260927_observability`, `owner_go_steps`
GL-1–GL-5, the seven `o*` slices, sequenced co-owners, shared writes, tripwires
wave-o-1–wave-o-11 and `reviews.observability_wave_design`. The partition stamps stay at
`85c4b8f4`; every Wave O owns list is re-grepped at launch (wave-o-9).

**Design process.** Workflow `design-observability-soft-failure-wave` (`wf_fdeebf54-80d`); record
`.omc/research/ingestion-grill-20260926/observability-wave-design.md` (+ `.result.json`).

1. **Inventory:** 73 ids (L-01–L-16, F-01–F-10, S-01–S-16, T-01–T-09, E-01–E-22), mapped in §4.9.4.
2. **Three designs, one judge** (§19.1).
3. **Critic round 1:** 54 findings, all disposed → revision 1.
4. **Critic round 2:** 40 findings (1 CRITICAL, 9 HIGH, 18 MEDIUM, 12 LOW), all disposed (§19.2) →
   revision 2, written here with the owner answers of 2026-09-27 (§3.6).
5. **Revision 2 has not been re-reviewed** as a whole. Its next adversarial passes are the per-push
   reviews (`reviews.phase0_gl1`–`phase0_gl5`) and, for the folded ladder, the Phase-1/2 reviews of
   `f1-executor` (`reviews.observability_wave_design`: "revision 2 unreviewed").

### 19.1 Score table (round 0; not re-scored)

| criterion | logs-first | ledger-first | minimal-envelope |
|---|---|---|---|
| (a) soft-failure safety | **9** | 7 | 8 |
| (b) source-audit strength | **9** | **9** | 8 |
| (c) coverage of every inventory id | 9 | 9 | 9 |
| (d) buildable, fits partitions and settled decisions, low ceremony | **8** | 5 | 7 |
| (e) secret redaction and log-volume safety | 8 | **9** | **9** |
| **total** | **43 (chosen spine)** | 39 | 41 |

The logs-first spine kept its two choke points (the `ingest/http.py` factories and the executor tee),
a counts-only `job_attempt.metrics` and incident-row escalation; it borrowed redaction by value,
URL-free source events, level-aware report parsing and all-or-nothing water gauges from
minimal-envelope, and `turn_id`, pinned stdlib loggers, `KeyedRequestUrl`, `unwritten_known` and the
monthly receipt from ledger-first.

### 19.2 Review round 2 disposition

Round 1's 54 rows stand as recorded in revision 1, except the 13 that round 2 supersedes (COV1-06,
COV1-10, COV1-11, COV1-13, BUI1-10, BUI1-12, BUI1-15, BUI1-16, BUI1-18, SOF1-03, SOF1-07, SOF1-10,
SOF1-16). The design record holds the full resolution text.

| id | sev | verdict | lands in |
|---|---|---|---|
| COV2-01 | HIGH | accepted: R1–R4 evidence rules, one fixture per lane from its real `main()` print | §4.9.3 |
| COV2-02 | HIGH | accepted in revision 2; **its pools, reserves and brake then declined by WQ-4**; metering and the report kept | §4.9.2, §3.6 |
| COV2-03 | HIGH | accepted: the epoch in SQL, `spawned=false` never charged, running rows excluded | §4.9.2 |
| COV2-04 | MED | accepted: the `infra` class on the upstream ladder; `fleet:<class>` | §4.9.3 |
| COV2-05 | MED | accepted: the `executor_repair_authoring` incident | §4.9.3 |
| COV2-06 | MED | accepted: savepoints; per-lane repair-plan isolation; a raising incident read in fault injection | §4.9.3 |
| COV2-07 | MED | accepted: `app.py` at GL-1; `describe_error` at `routes/ops.py` | §4.9.1 |
| COV2-08 | MED | accepted: `sys.orig_argv` arming; the exit-time operator summary | §4.9.1 |
| COV2-09 | LOW | accepted: pid pairing; `usage_complete=false` | §4.9.2 |
| COV2-10 | LOW | accepted: the raw-client guard with a reasoned allow-list | §4.9.2 |
| COV2-11 | LOW | accepted: a lifecycle per incident kind; the silencers stated | §4.9.3 |
| COV2-12 | LOW | accepted: launch waits for quiescence; the `agent` group exempt | §4.9, §4.9.1 |
| COV2-13 | LOW | accepted: `captureWarnings`; warning prefixes routed as warn | §4.9.1 |
| COV2-14 | LOW | accepted: `job_event` is the heartbeat channel; its retention goes to `f1-executor` | §4.9.3 |
| COV2-15 | LOW | accepted: the weight parse table; equality with G0 | §4.9.2 |
| SOF2-01 | CRIT | accepted: four charging bases; only charged spend stops forward | §4.9.2 |
| SOF2-02 | HIGH | accepted: refusal inside planning before `fair_due_order` (its brake half moot under WQ-4) | §4.9.2 |
| SOF2-03 | HIGH | accepted, rebutted in part (`INCIDENT_SEVERITY` lives in `execution`) | §4.9.3 |
| SOF2-04 | HIGH | accepted: episode chaining, the flapping alarm, the watch | §4.9.3 |
| SOF2-05 | HIGH | accepted: per-tick reconciliation; ledger-gated repair withholding | §4.9.3 |
| SOF2-06 | MED | accepted: inconclusive probes; no SIGTERM handler | §4.9.1, §4.9.3 |
| SOF2-07 | MED | accepted: a per-policy `probe_attempts` (USGS only) | §4.9.2, §4.9.3 |
| SOF2-08 | MED | accepted: `select_run_final_attempt.sql`; the R2 pair | §4.9.3 |
| SOF2-09 | MED | accepted: bounded probation | §4.9.3 |
| SOF2-10 | MED | accepted: a garbled switch resolves to the legacy state | §4.9.3 |
| SOF2-11 | MED | accepted: the repair breaker | §4.9.3 |
| SOF2-12 | LOW | accepted: `active_lanes` minus quarantined; the legacy incumbent kept | §4.9.3 |
| SOF2-13 | LOW | accepted, as COV2-09 | §4.9.2 |
| BUI2-01 | HIGH | accepted: the flush-first usage writer | §4.9.1 |
| BUI2-02 | HIGH | accepted, as SOF2-01 | §4.9.2 |
| BUI2-03 | MED | accepted: the fact corrected; launch waits for quiescence | §4.9 |
| BUI2-04 | MED | accepted: o2b's final-attempt SQL; the operator discriminator; a single loader | §4.9.2, §4.9.3 |
| BUI2-05 | MED | accepted: the leaf normaliser, the redacting default, the SQL-block cut | §4.9.1 |
| BUI2-06 | MED | accepted: the deferred worker logger | §4.9.1 |
| BUI2-07 | MED | accepted: the operator decision made at exit | §4.9.1 |
| BUI2-08 | MED | accepted: sinks resolve at write time | §4.9.1 |
| BUI2-09 | MED | accepted, rebutted in part; the six pushes became five plus the G1 fold (WQ-3) | §4.9 |
| BUI2-10 | LOW | accepted: `.env` and DSN collection, casefolded keys, the depth stub | §4.9.1 |
| BUI2-11 | LOW | accepted: the router rate bucket; Railway figures read before GL-1 | §4.9.1, A23 |
| BUI2-12 | LOW | accepted: the `transport=` seam; the wire worst case documented; the f1-executor task rewritten | §4.9.2, plan 1D |

### 19.3 Deviations from the design record at integration

- **WQ-4 declined the pools.** The GL-5 `pool_saturated` brake, `POOL_BULK_LANES`, the windowed
  pools and reserves, their incident kind and event, and their tests
  (`test_pool_brake_membership_follows_key_presence`, `test_pool_brake_withholds_soil_never_weather_observations`,
  `test_brake_never_takes_a_selection_slot`, `test_windowed_pool_reserve_protects_the_realtime_lane`)
  are not built. The gap-fill line drops the design's "− forward reserve" term: the owner set it at
  "below 60 %". One test is added to pin the owner's lines:
  `test_gap_fill_is_refused_at_sixty_percent_and_forward_at_ninety_five_percent`; another pins WQ-6:
  `test_monthly_usage_receipt_is_written_once_per_closed_month`.
- **WQ-3 folded GL-6.** `o2c-hold-ladder` and `o5c-holds-and-probes` are not created; their owns and
  tests are `f1-executor` entries, so the FR-8 revision ships at G1 under Phase 1–2 review.
- **Publication debt (PD).** The design predates the merge. `ok`/`incomplete` now includes
  publication debt, `lane_incomplete` gains the reason `publication_debt`, a debt bucket is never a
  clean probation bucket, the merged `publication_debt` metric is kept, fault injection adds an
  "exit 0 with publication debt" case, and launch waits for the merge (A25, A26).
- **The design's `d7-*` targets map to the c7 slices** (§4.9.5): `c7-repair-retire`,
  `c7-quarantine-1`/`-2` and `c7-scaffold` gain owns entries for Wave O's legacy parts only.
- **The raw-client guard tolerates** a missing or zero-match allow-list entry, so no c7 slice owns
  it; `p4-extract` re-points the crop-cover entry when it moves `crop_cover/source.py`, and
  `s-crop-cover` removes it.
- **The `o*` chain is linear** (o1 → o3 → o2a → o5a → o4 → o2b → o5b), matching the push order, so
  every co-owner proof is a `depends_on` chain; the design had GL-4 and GL-5 both depending on GL-3.
- **The dead `jobs_supersede_run` except-branch** lives in `execution/job_run_supersession.py`, which
  no c7 slice owns; it becomes an `i-coordinator` C6 follow-up instead of a d7 deletion.
- **`receipts/`** joins `c7-verbs`' pinned known-infra list (a Wave O reference into §4.8.8, checked
  at the G9.0 review).
- **The design's stale "396" correction is moot:** the spec already carries G0's 1,584 bound.

### 19.4 Remaining owner points

- **Optional:** a fresh critic pass on revision 2 as integrated, before GL-1. The default is no
  separate pass: the GL-1–GL-3 `/security-review`s, the GL-5 critic and the Phase-2 critic cover it.
- WQ-4's three confirmations (the key's tier, whether the plan resets on a billing anniversary,
  whether failed or 429 sends bill) remain P5 measurements; the tier precondition is already in plan
  G0 §9.
