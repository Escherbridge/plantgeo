---
type: Implementation Plan
title: PlantGeo ML service — extraction, Parquet in/out, Mojo kernels, platform wiring
tags: [plantgeo_ml_service_20260918]
resource: ./spec.md
---

# Implementation Plan: PlantGeo ML service

## Overview

Four phases, one push each at minimum (owner 2026-09-04: push small and often). Authors never run the
suite (owner 2026-08-25); they predict what will fail and a monitor sweeps the combined tree once per
phase, then an independent reviewer judges. Every review loads `conductor-okf:code-styleguides` and
cites `python.md` / `engineering-principles.md` rules. Training against production data is **not**
in any phase.

| phase | ships | push | review lane |
|---|---|---|---|
| 0 | this track, superseded old track, RUNBOOK row, research findings, memory | with phase 1 | `oh-my-claudecode:critic` on spec + plan |
| 1 | service skeleton + hard cut of ML/Monte Carlo out of agri-data-service | one push, five services build | `/code-review high` on the cut; `quality-reviewer` on the skeleton |
| 2 | Parquet readers, forecast writer, fire-risk + KNN daily lanes, artifacts, API, cron service | two pushes (readers+writer, then lanes+API) | `/code-review high`; `/security-review` on the API (user input) |
| 3 | Mojo kernels behind parity harnesses, pixi project, Docker Mojo stage | one push | `/code-review high`; benchmark receipt in evidence |
| 4 | platform wiring: agent forecast tool, slider forecast days, web env, fire-risk track activation | one push | `/code-review`; `quality-reviewer` on the web side |

Decision source: `spec.md` §1 (D1–D8). Nothing below reopens them. File ownership per slice:
`metadata.json` → `partitions`. The authoritative file lists for phase 1 are in
`evidence/extraction-inventory-20260918.md`.

---

## Phase 0: Bookkeeping (this session, inline)

- [x] Task: Write `spec.md`, `plan.md`, `metadata.json`, `evidence/extraction-inventory-20260918.md`.
- [x] Task: Mark `ml_mojo_conversion_20260823` superseded; re-point `fire_risk_zone_forecast_20260823.depends_on`.
- [x] Task: `conductor/tracks.md` rows; one Outstanding-work row in `conductor/RUNBOOK.md`.
- [x] Task: Persist `.omc/research/mojo-ml-service-20260918/FINDINGS.md`; memory file for the eight decisions.
- [x] Task: `oh-my-claudecode:critic` review of spec + plan; verdict recorded in `metadata.json.reviews.phase0` (CHANGES-REQUIRED, 12 findings, all folded in 2026-09-18).

## Phase 1: Skeleton and hard cut — one push (LANDED b79101c6, pushed 5c8c34c4, deployed PASS 2026-09-19; hotfix b1f02f95 for the sanic-ext annotation NameError; plantgeo-ml live)

Goal: `services/plantgeo-ml-service/` exists, holds every pure module, builds on Railway, answers
`/ready`; agri-data-service no longer contains ML or Monte Carlo and its receipt is refreshed.

### 1A — New service skeleton (slice `p1a-service-skeleton`)

- [x] Task: `pyproject.toml` (hatchling, `plantgeo-ml-service` 0.1.0, python >=3.12, deps: sanic,
      sanic-ext, pydantic, pydantic-settings, structlog, click, numpy, polars, pyarrow, duckdb, boto3,
      scikit-learn; dev: pytest, pytest-asyncio, pytest-sanic, ruff, mypy), `uv.lock`, `ruff.toml`,
      `mypy.ini` copied from the sibling and trimmed; console script `plantgeo-ml`.
- [x] Task: package layout `src/plantgeo_ml_service/{foundation,method/{ml,monte_carlo,kernels},warehouse,pipeline,serving,interface}` each with `__init__.py` and a one-line `AGENTS.md` stub naming its layer.
- [x] Task: `config.py` — pydantic-settings `Settings` with `object_store_*` (same names as the
      sibling), `object_store_prefix`, `ml_prefix = "ml"`, `kernels: Literal["python","mojo"] = "python"`,
      `sanic_*`, `cors_origins`. A validator **refuses** any env var matching `*DATABASE_URL*`
      with a message naming D5. Test pins the refusal.
- [x] Task: `app.py` — Sanic factory `create_app`, blueprint group `/api/v1/ml` (empty in phase 1),
      `/health` (200 always) and `/ready` (200 when bucket credentials resolve and a bounded
      `HEAD` on the bucket succeeds; 503 with a typed reason otherwise).
- [x] Task: `tests/test_layer_import_contract.py` — port the AST walker; rules:
      `foundation` imports nothing internal; `method` may not import `warehouse|pipeline|serving|interface|polars|pyarrow|duckdb|boto3|sanic`;
      `method/ml` ↔ `method/monte_carlo` forbidden both ways; `method/kernels` may import only `foundation`;
      `warehouse` may not import `pipeline|serving|interface`; `serving` may not import `interface`.
      Plus `test_layer_packages_actually_import`.
- [x] Task: Move inventory §A (`method/ml`, `method/monte_carlo`, `method/AGENTS.md`, the three
      canonical helpers, `strategy_selection`, `strategy_label_mapping`, the CLI verbs, the ten
      pure tests). Rewrite imports. `execution/covariate_wind_model.py` is NOT pure (it imports
      sqlalchemy and loads `target_signal_series.sql` at module scope): **extract** its numeric core
      (fit/split/score and their dataclasses) into `method/ml/covariate_wind_model.py` and leave the
      query surface to be deleted with §B; list every function left behind in the predictions file.
      Use `cp` + `rm`, never `git mv` (two agents share the index); rename detection at commit
      time keeps history.
- [x] Task: Parity fixtures (FR-3 pattern): `tests/fixtures/parity/<name>.json` golden outputs,
      tests assert against the fixture always and against the sibling's source only when it is on
      disk; `scripts/regenerate_parity_fixtures.py`.
- [x] Task: `interface/cli.py` — click root `plantgeo-ml` with `strategy-train`,
      `strategy-label-map-preflight`, `serve` (runs the Sanic factory), and a `predict-daily` stub
      that exits 2 with "not implemented until phase 2" (a bounded turn must still exit cleanly).
- [x] Task: `Dockerfile` — same shape as the sibling: `python:3.12.12-slim-bookworm` pinned digest,
      uv 0.11.29, quality-receipt stage (`scripts/quality_receipt.py`, `scripts/verify_quality_receipt.py`,
      `scripts/check.py` copied and re-rooted), runtime stage, non-root user, `EXPOSE 8000`, sanic CMD.
      No Mojo stage yet (phase 3). `railway.json` with `/ready` healthcheck.
- [x] Task: `AGENTS.md` (service root) — layers, the zero-Postgres rule, the parity-not-import rule,
      where rationale lives. `RUNBOOK.md` — Directive, Outstanding work table, Environment (WSL2 +
      pixi not yet required), Continuation plan.
- [x] Task: predict-and-record: list the tests the author expects to fail in the sweep and why, in
      `conductor/tracks/plantgeo_ml_service_20260918/evidence/phase1-predictions.md`.

### 1B — Hard cut in agri-data-service (slice `p1b-agri-cut`)

- [x] Task: Delete inventory §B (execution lane, route, the 40 mechanically derived SQL files,
      tests, `cli/ml.py`, the two verbs and `_write_atomic`, the `ml` group registration). Before
      deleting each `*_postgresql` test, read it: keep any that only exercises a table still present
      in `db/agri_baseline.sql`. `tests/test_sql_tree_conventions.py::test_loaded_exactly_once` fails
      on any orphaned `.sql`; re-derive the list from `load_query_sql(...)` call sites after deleting.
- [x] Task: Split the forecaster sections out of `tests/parquet/test_signal_serving.py` (imports
      at :24-38, section from :290) and `tests/parquet/test_vegetation_serving.py` (imports :27-37,
      section from :475) into `services/plantgeo-ml-service/tests/test_signal_forecast.py` and
      `tests/test_vegetation_forecast.py` with rewritten imports; the observed-side sections stay
      untouched. (p1a has finished by the time p1b runs, so creating these two files is safe.)
- [x] Task: `tests/test_layer_import_contract.py:480-482` pins `interface/cli/commands.py:146`;
      deleting the verbs above it moves the line. Regenerate `CLI_ADAPTER_VIOLATIONS` from the
      sweep's assertion message (the docstring at :479 says so); record it as a predicted failure.
- [x] Task: Delete `method/ml/**` and `method/monte_carlo/**` **after** `p1a` confirms the copies
      landed (wave order in `metadata.json`). If `method/` is then empty, delete it, its layer
      entry in `LAYER_FORBIDDEN_IMPORTS`, and `warehouse`/`pipeline`'s `agri_data_service.method`
      forbids (a rule about a package that no longer exists is noise).
- [x] Task: `__init__.py` re-exports; `test_layer_import_contract.py` rules
      (`SUBPACKAGE_FORBIDDEN_IMPORTS`, `SIBLING_MODULE_DIRECTORIES`); `lane_registry.py:149-153`
      comment; `pyproject.toml` `scikit-learn` (grep `src/` first; `analysis/` is outside).
- [x] Task: Doc pointers — `execution/AGENTS.md`, `src/agri_data_service/AGENTS.md`,
      `conductor/code_styleguides/layer-lanes.md` §5 (one paragraph: ML and Monte Carlo now live in
      `services/plantgeo-ml-service`; the forecast_module stem names a module there), each
      `docs/lanes/<slug>.md` §7 for the five forecastable lanes (one line each).
- [x] Task: predict-and-record in `evidence/phase1-predictions.md` (append).

### 1C — Sweep, review, push (coordinator + monitor)

- [x] Task: Monitor runs agri-data-service `python scripts/check.py --write-receipt` and the ML
      service's `uv run pytest && ruff && mypy` once; reports every failure, not the first.
- [x] Task: `/code-review high` on the cut; `quality-reviewer` on the skeleton; verdicts to
      `metadata.json.reviews.phase1`. Fixes batch; one more sweep.
- [x] Task: Push. Confirm all four existing services and `plantgeo-ml` build the same commit
      (`plantgeo-main` image runs `check:data-boundary`; a bare URL in a `src/**` comment fails it).
      Creating the `plantgeo-ml` Railway service and its config-as-code root is an owner action;
      the push is not blocked on it, the `/ready` proof is.

## Phase 2: Parquet in, Parquet out — two pushes

Goal: the service reads governed observed partitions, writes governed forecast partitions and the
fire-risk lane, and serves them. Real bucket writes wait for an owner go; everything is proven on a
scratch prefix first.

### 2A — Readers, writer, contract parity (slice `p2a-parquet-io`) — LANDED 0290a1e6, deployed 095ed2a3

- [x] Task: `foundation/parquet_paths.py` — the object-key grammar (`layer=`, `kind=`, `zoom=`,
      `year=/month=/day=`, `part-NNNN.parquet`, `_complete.json`, `absent.json`), `PartitionKind`,
      `ZoomTier`, `validate_layer_slug`. Cross-service parity test: import agri-data-service's
      `foundation/parquet/paths.py` by file path from the monorepo and assert identical strings for
      a fixed grid of (layer, kind, zoom, day, part).
- [x] Task: `warehouse/streams.py` — the observed schemas the service consumes (`signal`,
      `fire-detections`, `vegetation`, `drought`, `burn-severity`, `weather-observations`) as pinned
      Arrow schemas with a parity test against the sibling's `warehouse/schemas/*.py` exports; plus
      `FORECAST_PROVENANCE_COLUMNS` (six) and the `fire-risk` schema (FR-5).
- [x] Task: `pipeline/object_store.py` — boto3 backend, bounded listing, `read_day`, `write_partition`
      (atomic part write + completion marker with sha256 receipt), `write_absence`. DuckDB session
      with `max_temp_directory_size='0GiB'` and pre-installed httpfs/spatial like the sibling.
- [x] Task: `pipeline/availability_publisher.py` (FR-4a) — after every rung of a forecast day is
      complete, build the availability generation (`AVAILABILITY_REQUIRED_RUNGS` = all rungs, rows
      and provenance shaped exactly as `pipeline/parquet/availability_documents.py` admits), write
      `availability/generation=<sha>/availability.parquet`, compare-and-set `_LATEST.json`, retry
      once on a lost race, refuse with a receipt otherwise. Parity fixtures for the generation and
      pointer documents; a test that `availability_lane_root("vegetation","forecast")` differs from
      the observed root the other session's promotion lane writes.
- [x] Task: `pipeline/observed_reader.py` — `read_lane_window(layer, zoom, first_day, last_day)`
      via DuckDB `read_parquet` over listed keys; respects publication lag per lane
      (`PUBLICATION_LAG_DAYS` copied from the sibling's registry with a parity test).
- [~] Task (reader done 2026-09-19, agri export verb still owed): `pipeline/expert_labels.py` — reads `ml/labels/expert/<release>/part-0000.parquet`.
      The one-time export from Postgres is a **sibling-service CLI verb** `agri-service ops
      export-expert-labels --release <id> --prefix ml/labels/expert` (owner go before running on
      prod; the write is a production mutation). Owned by `p2a` on the agri side, file list in
      `metadata.json`.

### 2B — Daily lanes, artifacts, API, cron (slices `p2b-fire-risk`, `p2b-knn`, `p2c-api`)

- [ ] Task (`p2b-fire-risk`): `pipeline/fire_risk_features.py` — port `analysis/fire_risk_index.py`'s
      feature plane to a leakage-gated builder: for `issued_on`, every feature comes from days
      ≤ `issued_on − lag(producer)`; cyclical day-of-year, photoperiod (computed, not read),
      stratum from vegetation NDVI class; `refused_reason` for out-of-stratum cells.
- [ ] Task (`p2b-fire-risk`): `method/ml/fire_risk_model.py` — logistic + calibrated probability over
      the feature vector, artifact = canonical JSON (coefficients, moments, stratum table, feature
      names, `trained_on` window, checksum). `pipeline/fire_risk_daily.py` writes the `fire-risk`
      lane for horizons 1–14 with a prediction receipt. An untrained artifact is a typed refusal,
      never a zero score. `pipeline/fire_risk_backtest.py` — walk-forward, per-stratum PR-AUC and
      Brier against VPD-only and climatology; the daily writer refuses a real prefix unless the
      artifact carries a backtest receipt clearing the declared lift (FR-5 gate). Training itself
      is not run in this track.
- [ ] Task (`p2d-fire-risk-registration`, agri side, sequenced after the other session's
      `p1d-registration` lands on `lane_registry.py`): `LaneRegistration` for `fire-risk`
      (`daily_series`, floor basis, lag, cadence, ladder, `forecast_module=None` because the ML
      service writes it directly), `warehouse/schemas/fire_risk.py`, `docs/lanes/fire-risk.md`,
      `tests/parquet/test_lane_contract.py` expectations.
- [ ] Task (`p2b-knn`): `pipeline/analog_ensemble_daily.py` — covariate vectors from the `signal`
      lane's observed partitions (the sibling's `select_covariate_vectors.sql` semantics re-expressed
      in DuckDB), temporal exclusion window, `method/ml/analog_ensemble.py` unchanged, p10/p50/p90 for
      horizons 1–30, written as `layer=signal/kind=forecast` with the six provenance columns and a
      `forecast_run_id` derived from (artifact sha, issued_on, seed).
- [ ] Task (`p2b-knn`): Monte Carlo dispatch — `pipeline/monte_carlo_daily.py` maps each registry
      forecast stem to its `method/monte_carlo` module and writes `kind=forecast` for
      `fire-detections`, `sensors`, `signal` (where AnEn has no artifact), `vegetation`,
      `water-gauges`; each refuses on insufficient history exactly as the module already does.
- [ ] Task (`p2c-api`): `serving/` readers (bounded, typed refusals) and the four routes in FR-8;
      every response carries `artifact_sha256`, `issued_on`, `claim_tier: evaluation_only`.
      `/security-review` on the query parsing.
- [ ] Task (`p2c-api`): `interface/cli.py predict-daily [--issued-on] [--prefix-override] [--dry-run]`
      — runs fire-risk then Monte Carlo then AnEn, exits 0 on a bounded turn, writes one receipt.
      `railway.cron.json` + `infra/cron/Dockerfile` for `plantgeo-ml-cron` (owner creates the
      service; no schedule armed without a go).
- [ ] Task: sweep, `/code-review high`, `/security-review`, push; dry-run against the production
      bucket to `ml/scratch/<date>/` and record the listing in `evidence/phase2-dry-run.json`.

## Phase 3: Mojo kernels — one push

- [ ] Task: `pixi.toml` / `mojoproject.toml` pinning Mojo 1.0.x; `kernels/` Mojo sources:
      `knn_search.mojo` (weighted L2, exclusion mask, top-k), `seasonal_bootstrap.mojo` (takes the
      draw stream numpy's seeded PCG64 produced as an input array; no RNG is ported; outputs
      asserted bit-identical to the Python reference), `seasonal_features.mojo` (sin/cos day-of-year,
      photoperiod).
- [ ] Task: `method/kernels/__init__.py` dispatch: `PLANTGEO_ML_KERNELS=python` uses the pure
      references; `mojo` imports the built extension and refuses to start if absent. Every kernel
      keeps ≤ 6 arguments (Python→Mojo limit) by passing one struct/array bundle.
- [ ] Task: `tests/kernels/test_parity_*.py` — golden outputs generated by the Python reference,
      asserted equal (exact for KNN and PCG64, `atol=1e-12` for features) under both dispatch modes;
      skipped with a named reason when Mojo is absent, never silently passing.
- [ ] Task: `Dockerfile` Mojo build stage (pixi install, `mojo build --emit shared-lib`), copied into
      runtime; `PLANTGEO_ML_KERNELS=mojo` in the Railway config only after the parity receipt is in
      `evidence/phase3-parity.json` from the Docker build.
- [ ] Task: benchmark receipt (`benchmark` stdlib vs scikit-learn brute force on the 1,568-cell
      pilot) in `evidence/phase3-benchmark.md`. Sweep, review, push.

## Phase 4: Platform wiring — one push (depends on phase 2, not on phase 3)

- [ ] Task: agri-data-service `agent/tools.py::forecast_summary_for_cell` reads
      `kind=forecast` partitions through the existing Parquet serving reader (no ML import); refusal
      remains for lanes without a published forecast day.
- [ ] Task: `src/lib/server/services/parquet-slider-capabilities.ts` — `forecastable` becomes true
      for lanes with forecast days; the slider's future dates read `kind=forecast`.
- [ ] Task: `PLANTGEO_ML_SERVICE_URL` in `src/lib/server/services/` client + `docs/env-vars.md`;
      the fire-risk tRPC read for `regional_fire_risk_surface_20260824`.
- [ ] Task: `fire_risk_zone_forecast_20260823` → `active`, runtime question closed; this track's
      status → `completed` once phase 4 is reviewed and deployed.

## Tripwires (carried into every brief)

- Never edit `execution/vegetation_*`, `execution/lane_specs.py`, `pipeline/**`, `planes/**`,
  `parquet_ops/**`, `warehouse/**` or `src/**` in phases 1–3 (other session's partitions).
- Never `git add conductor/` wholesale; the RUNBOOK is shared by concurrent sessions.
- Never `git mv` from two agents at once; copy + delete, and let rename detection do the rest.
- The receipt is refreshed by the sweep, never edited; a CRLF tree writes a different digest.
- Bash heredocs containing backticks write nothing in this harness; use Write/Edit.
- A Mojo function imported from Python takes at most six arguments.
- `random_seed` is recorded on every forecast row; an unseeded ensemble does not ship.
- `cell_id` is required non-null at the signal lane's base rung (`warehouse/parquet/schema.py:233`);
  ML-written forecast rows propagate `cell_id` and every observed key column verbatim from the
  observed rows they were issued from. Never reshape the grain; the schema is `forecast_schema_for()`.
- Writing a partition does not publish it: no forecast day is selectable until its availability
  generation and pointer are written (FR-4a). Acceptance reads through `availability_coverage.py`.
- Never edit `tests/parquet/test_signal_serving.py` or `test_vegetation_serving.py` beyond removing
  the forecaster sections p1b moves; the observed-side sections are the other session's coverage.
- Windows `round(lat / pitch)` integer binning, never IEEE division of the latitude.
- Staleness is measured from the lane's provider frontier (`settled_through`), never from today:
  a registered publication lag can be a measured median gap, so a healthy lane sits a full lag
  behind before anything is wrong; a forecast lane's source ceiling is frontier + horizon.
- Classify every day in a window by the full rung ladder; never gate the ceiling by the
  required-rung intersection while classifying other days by the base rung alone.
