---
type: evidence
slice: p2c-api
phase: 2C
written_on: 2026-09-19
sweep_owed: true
---

# Phase 2C predictions: the FR-8 routes, `predict-daily`, and the cron image

Author ran NO pytest, ruff or mypy (owner rule 2026-08-25). Smoke imports only. This file lists
what was written, what was touched outside the slice's ownership, and what the author predicts the
sweep will find. A monitor sweeps the combined tree and judges.

## Files created

All paths relative to `services/plantgeo-ml-service/`.

| file | what it owns |
|---|---|
| `src/plantgeo_ml_service/planes/refusals.py` | one stable code per reason a read states nothing |
| `src/plantgeo_ml_service/planes/wire.py` | the claim block, the serving-path vocabulary, the cell renderer |
| `src/plantgeo_ml_service/planes/query_models.py` | every query parameter, pydantic, `extra="forbid"` |
| `src/plantgeo_ml_service/planes/partition_reads.py` | the bounded day read: explicit keys, a box, a row cap |
| `src/plantgeo_ml_service/planes/availability_reads.py` | the pointer and its generation; the MEASURED horizon |
| `src/plantgeo_ml_service/planes/fire_risk_reads.py` | `read_fire_risk_point` |
| `src/plantgeo_ml_service/planes/analog_reads.py` | `read_analogs` |
| `src/plantgeo_ml_service/planes/forecast_reads.py` | `read_forecast_summary`, both serving paths |
| `src/plantgeo_ml_service/planes/artifact_reads.py` | `list_artifacts` |
| `src/plantgeo_ml_service/planes/routes.py` | the blueprint, the serving pool, the status per code |
| `src/plantgeo_ml_service/pipeline/predict_daily.py` | `run_predict_daily` and the turn receipt |
| `infra/cron/Dockerfile` | the `plantgeo-ml-cron` image, `CMD plantgeo-ml predict-daily` |
| `railway.cron.json` | DOCUMENTATION of the cron service settings; Railway reads nothing |
| `tests/serving_harness.py` | the mirrored bucket and fake request the route tests share |
| `tests/test_routes_fire_risk.py`, `test_routes_forecast.py`, `test_routes_analogs.py`, `test_routes_artifacts.py` | the four routes |
| `tests/test_predict_daily.py`, `tests/test_cli.py` | the turn and its exit codes |

## Files modified

| file | change |
|---|---|
| `src/plantgeo_ml_service/app.py` | dropped the 501 artifacts placeholder; mounts `planes.routes.machine_learning_bp` |
| `src/plantgeo_ml_service/interface/cli.py` | real `predict-daily`; `PredictRuntime`/`open_runtime`; dropped the not-implemented constants |
| `src/plantgeo_ml_service/planes/AGENTS.md` | the rationale for everything above |
| `RUNBOOK.md` | 2B/2C rows, and four new immediately-owed items (extensions, cron creation, dry run) |

## Requests outside this slice's ownership

1. **`tests/test_app.py` was edited** (one test, one constant, one import). It asserted the
   artifacts route answers 501, which this slice deliberately replaces. The replacement asserts the
   four FR-8 URIs are mounted; behaviour moved to `tests/test_routes_*.py`. No other test file was
   touched. Flagging it because `tests/` is not in the slice's ownership list.
2. **The service `Dockerfile` still does not install the DuckDB extensions.** `infra/cron/Dockerfile`
   does. Until the same `install_extensions` step lands in the service image, every deployed
   `/api/v1/ml` partition read refuses with `DuckDbExtensionError` rendered as `serving_fault`. That
   file is not in this slice's ownership; RUNBOOK immediately-owed item 5 records it.
3. **`p2d-fire-risk-registration` is still owed on the agri side.** `fire-risk` has no lane contract
   there, so nothing outside this service can read the lane these routes serve.

## Coordinator additions, and where they landed

- `published_horizon_days` is MEASURED from the current availability generation
  (`planes/availability_reads.py::_newest_published_day`), never `source_ceiling - issue_day`. Only
  rows in the `published` terminal state count, so a governed absence does not inflate it.
  `tests/test_routes_forecast.py::test_a_partly_published_run_reports_the_horizon_it_reached_not_the_one_it_declared`
  publishes 12 of 30 declared days and asserts the route answers 12.
- `issue_day` and `source_ceiling` ride beside it on both paths.
- `serving_path` is stated on the wire: `forecast_kind`, or `release_series` for `weather-forecast`,
  whose future lives inside the `kind=observed` issue file as `valid_time` rows. The release path
  also returns `generation_key`, and the absence of a `kind=forecast` partition is not a refusal
  there. Both paths are pinned by their own route test.

## Convention question the brief asked to be answered

The sibling uses TWO shapes: `parquet_ops/faults.py` codes rendered by
`interface/http/parquet_routes.py::_refusal_body` as `{"error": {"code", "message"}}` at a transport
status, and the four warehouse STATES as 200 carrying `state`. `routes/agent_tools.py` uses a third,
flatter shape. This slice matched **`faults.py` + `_refusal_body`**: a `MachineLearningRefusalError`
carries `(code, message)` and renders as `{"error": {"code", "message"}}`. Per the brief, the
content-absence codes answer **200** with that envelope (`CONTENT_REFUSAL_CODES`), which is where
this deliberately differs from the sibling's `state` vocabulary; serving faults keep a transport
status from `REFUSAL_HTTP_STATUS`, and nothing answers 500.

## Predicted failures

Ordered by how likely the author thinks each is.

1. **`ruff E501` (line length 120).** Several assembled statements are close to the limit; the most
   likely offenders are `artifact_reads.py::_summary`'s `read_over_budget` call, the
   `forecast_reads.py::_read_release_series` `_nearest(...)` call, and `predict_daily.py`'s
   `forecast_run_id(...)` call. One already carries an explicit `# noqa: E501`
   (`partition_reads.py::newest_valid_day`), which may itself be reported as unnecessary.
2. **`ruff TC001` on runtime imports used only in annotations.** `PositionColumns` in
   `forecast_reads.py` and `ClaimProvenance` in `routes.py` are imported at runtime and appear only
   in annotations, so ruff will likely want them in the `TYPE_CHECKING` block. They must NOT be
   moved where they are a handler annotation (`tests/test_route_annotations.py`), but neither of
   these two is; the fix is to move them and leave `Request`/`HTTPResponse` exactly as they are.
3. **`mypy` unused-ignore under `strict`.** `duckdb`'s `.arrow().to_pylist()` returns `Any`, so the
   `# type: ignore[arg-type]` comments in `fire_risk_reads.py` and `analog_reads.py` may be reported
   as unnecessary. If so, delete the comment, not the narrowing.
4. **`mypy` on `ServingContext.session`.** `DuckDbSession | None` with a `require_session()` guard
   should check clean, but `routes.py::_run`'s `session = open_serving_session() if needs_session
   else None` may need an explicit annotation.
5. **`pytest`: the release-series position columns.** `test_routes_forecast.py` queries the weather
   lane at the CELL's coordinates. `weather_forecast_rows.py` publishes the lattice cell's
   `latitude`/`longitude`, not the provider's snapped sample, so the 0.25-degree search box should
   find it; if the base rung floors the coordinate onto a lattice origin, the assertion still holds
   but the returned `cell_longitude` will be the floored value.
6. **`pytest`: `write_forecast_day` on a hand-built fire-risk frame.** `test_routes_fire_risk.py`
   writes two cells at one day through the real ladder. If `FORECAST_TIER_DERIVATIONS` expects a
   column this fixture omits, the fixture fails rather than the reader.
7. **`pytest`: `CliRunner().invoke(...).stdout`.** The infrastructure case writes to stderr; if this
   click version mixes the streams, the JSON assertions in the other CLI cases could see both.
8. **Not a failure, a gap:** no test opens two concurrent reads, so `MAX_CONCURRENT_READS` and the
   `serving_at_capacity` refusal are unexercised. Deliberate: a test that races a semaphore is a
   flake generator, and the refusal path is three lines.

## What the sweep should check first

- `uv run --no-sync python scripts/check.py`, then batch every fix and sweep once.
- `tests/test_route_annotations.py` must still pass: it is the only thing standing between this
  surface and deployment `c125e2c2`'s `NameError: 'Request' is not defined`.
- `tests/test_layer_import_contract.py`: `planes/` imports `foundation`, `method`, `warehouse` and
  `pipeline` only, and `pipeline/predict_daily.py` imports no `planes` module.
- The quality receipt must be re-written after the fixes, over a staged tree, and never by hand.
