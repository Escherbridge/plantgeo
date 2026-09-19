---
type: evidence
slice: p2c-api
phase: 2C
written_on: 2026-09-19
---

# Phase 2C sweep: monitor pass over the FR-8 routes, `predict-daily`, and the cron image

Ran from `services/plantgeo-ml-service/`. `uv sync --locked --all-extras` succeeded with no
changes (63 packages resolved, 61 audited) — no `uv lock` needed.

## Commands and starting counts

- `uv run --no-sync ruff format --check src tests scripts` — 6 files would reformat
  (`pipeline/predict_daily.py`, `planes/analog_reads.py`, `planes/artifact_reads.py`,
  `planes/forecast_reads.py`, `planes/partition_reads.py`, `tests/test_routes_fire_risk.py`).
- `uv run --no-sync ruff check src tests scripts` — 13 errors: 3x `E501`, 2x `I001`
  (`forecast_reads.py`), 1x `TC004` (`partition_reads.py:23`, `date` imported under
  `TYPE_CHECKING` but used at runtime for `isinstance`), `PLR0913` x2, `UP047`
  (`routes.py::_parsed`), `PLC0415` (`serving_harness.py`), `PERF401` (`predict_daily.py`).
  The author's predicted `TC001` on `PositionColumns`/`ClaimProvenance` did **not** occur — ruff
  reported `TC004` on an unrelated import instead.
- `uv run --no-sync mypy src scripts` — 15 errors in 3 files: `fire_risk_reads.py` (4 sites) and
  `analog_reads.py` (4 sites) all mis-tagged `# type: ignore[arg-type]` where `int(row[...])`
  actually needs `[call-overload]` (mypy reports both "unused-ignore" and the real error on the
  same line); one `no-any-return` in `availability_reads.py:175`.
- `uv run --no-sync pytest -q` — **15 failed, 627 passed**. All 15 failures were in the three new
  route test files (`test_routes_analogs.py`, `test_routes_fire_risk.py`,
  `test_routes_forecast.py`), every one reporting `availability_unpublished` instead of the
  fixture's real data.

## Root cause of all 15 pytest failures (not predicted by the author)

`tests/serving_harness.py::mount()` patched `routes.open_store`/`open_serving_session` but never
wired the fixture's `InMemoryPointerStore` into the mirrored `ObjectStore` backend.
`planes/availability_reads.py::read_lane_availability` reads the pointer via
`store.read_object(pointer_key)`, not via a `PointerStore`. In production this works because
`BotoPointerStore` (`interface/cli.py:161`) shares the same S3 client/bucket as the backend; the
test harness used two genuinely separate stores (a disk-mirrored dict vs. an in-memory dict), so
every fixture-published pointer was invisible to the route under test — a pre-existing gap in the
test harness that this phase's route tests were the first to exercise the pointer-read path
through. Fixed in `tests/serving_harness.py:68-83`: `mount()` now mirrors every
`harness.pointers.pointers` entry into `harness.store.backend` before returning, reproducing the
production coupling.

## A second bug this surfaced, once availability actually resolved

With the pointer visible, four route tests then hit `AttributeError:
'pyarrow.lib.RecordBatchReader' object has no attribute 'to_pylist'` from
`planes/partition_reads.py`. DuckDB 1.5.5's `.execute(...).arrow()` returns a
`pyarrow.RecordBatchReader`, not a `Table` (verified directly: `con.execute("SELECT 1").arrow()`
→ `RecordBatchReader`). All four call sites in `partition_reads.py`
(`nearest_cell_position:144`, `rows_at_cell_position:177`, `newest_valid_day:185`, and one more at
201) chained `.to_pylist()` straight onto `.arrow()`, which only a `Table` has. The one pre-existing
`.arrow()` caller (`duckdb_session.py::read_parquet_keys:153`) never hit this because its caller
(`observed_reader.py:106`) hands the result to `pl.from_arrow()`, which accepts a
`RecordBatchReader` directly. Fixed by inserting `.read_all()` before `.to_pylist()` at all four
sites in `partition_reads.py`.

## Fixes applied (mechanical batch)

- `ruff format` + `ruff check --fix` — the 6 reformats and the 2 `I001` import-order fixes.
- `partition_reads.py:23` — moved `from datetime import date` out of `TYPE_CHECKING` (TC004; used
  at runtime for `isinstance`).
- `partition_reads.py:109`, `tests/serving_harness.py:98` — `# noqa: PLR0913 - ...` (matches the
  existing convention in `method/ml/*.py`).
- `routes.py:282` — `_parsed` converted to PEP 695 generic syntax
  (`def _parsed[QueryModelT: BaseModel](...)`); removed the now-unused module-level
  `QueryModelT = TypeVar(...)` and its `TypeVar` import.
- `tests/serving_harness.py:78` — `# noqa: PLC0415 - lazy import keeps this a test-only seam`.
- `pipeline/predict_daily.py:161-172` — `outcomes.append(...)` inside a `for` loop replaced with
  `outcomes.extend(<genexpr> for layer in ...)` (PERF401), same items, same order.
- `fire_risk_reads.py:133-135,149` and `analog_reads.py:172,183,196` — `# type: ignore[arg-type]`
  → `# type: ignore[call-overload]` on the seven `int(row[...])` sites (mypy: these overloads of
  `int()` are matched by `call-overload`, not `arg-type`); `analog_reads.py:184`
  (`float(row["quantile"])`) correctly kept `[arg-type]` — that one IS an `arg-type` error, and my
  first blanket `sed` wrongly touched it too; caught and reverted by re-running mypy.
  Comment text (the "pinned schema" rationale) left untouched.
- `availability_reads.py:168-175` — replaced the list-comprehension `published = [row["day"] for
  row in ... if ... isinstance(row.get("day"), date)]` (mypy can't narrow `row["day"]` from an
  `isinstance` check on `row.get("day")`) with an explicit loop that narrows the same `day`
  variable it checks, fixing `no-any-return` with no change to which rows count as published.
- `partition_reads.py` (4 sites) — `.arrow().to_pylist()` → `.arrow().read_all().to_pylist()`.
- `tests/serving_harness.py::mount()` — mirrors published pointers into the store backend (see
  above); this is the one non-mechanical fix, needed to make the fixtures reach the route code at
  all.

No assertion was weakened or deleted; no parity fixture was regenerated.

## Re-run results (final)

- `ruff format --check src tests scripts` — 141 files already formatted.
- `ruff check src tests scripts` — All checks passed!
- `mypy src scripts` — Success: no issues found in 78 source files.
- `pytest -q` — **642 passed**, 2 pre-existing `RuntimeWarning`s in
  `tests/test_seasonal_evaluation.py` (unrelated divide-by-zero in `seasonal_evaluation.py:468,475`,
  not touched by this phase).
- `pytest -q tests/test_route_annotations.py tests/test_layer_import_contract.py` — 8 passed.
  `Request`/`HTTPResponse` in `app.py` and `planes/routes.py` remain runtime imports with the
  required `# noqa: TC002` comment; untouched by this sweep.

## `tests/test_app.py` check (requested explicitly)

`test_the_factory_mounts_health_readiness_and_the_four_versioned_reads`
(`tests/test_app.py:118-128`) asserts only that `/api/v1/ml/artifacts*` is mounted in the router;
its docstring states behaviour moved to `tests/test_routes_*.py`. Confirmed
`tests/test_routes_artifacts.py` is not vacuous: it asserts `response.status == HTTP_OK` with a
real body (`count`, `artifacts`, `artifact_absent_reason`) on the empty-bucket case, and
`response.status != HTTP_OK` with a typed `error.code` (`ARTIFACT_UNREADABLE`,
`ARTIFACT_KIND_UNKNOWN`, `INVALID_REQUEST`) on the refusal cases. No further action needed.

## Standing failures

None. All four gates are green after the fixes above.

## Requests outside this slice's ownership (carried forward, not evaluated by this sweep)

Unchanged from `phase2c-predictions.md`: the service `Dockerfile` still lacks the DuckDB
extensions install step that `infra/cron/Dockerfile` has, and `p2d-fire-risk-registration` is
still owed on the agri side. Neither is in `services/plantgeo-ml-service/`'s sweep scope beyond
what's already flagged.
## p2c-fix-batch

Every blocker, major and minor from `metadata.json` -> `reviews.phase2c`, applied in one batch under
`services/plantgeo-ml-service/` only. Gates run ONCE at the end, then one mechanical pass.

### B1 -- the ceiling is the thing that runs the read

- `planes/routes.py:172-260` -- `ServingPool` (a `ThreadPoolExecutor(max_workers=MAX_CONCURRENT_READS)`
  with an explicit claim/release) replaces the `asyncio.Semaphore`. The claim is taken before submit
  and released by the WORKER, so a caller that timed out no longer hands its slot back while its
  query runs.
- `planes/routes.py:262-296` -- `ReadCancellation` binds the open session and survives the race where
  the timeout fires before the worker opened one.
- `planes/routes.py:330-357` -- `bounded_read` (was `_answered`): stop waiting, `cancellation.cancel()`,
  THEN answer `read_timed_out`.
- `pipeline/duckdb_session.py:74-81` -- `DuckDbSession.interrupt()`.
- Test `tests/test_serving_pool.py` -- a hung read is interrupted, its session closes, the pool
  returns to `in_flight == 0`, and `MAX_CONCURRENT_READS + 1` further reads all answer 200; a second
  case proves an offer past the ceiling is refused `serving_at_capacity` rather than queued.

### M2 -- size is settled by `head`, before any body is fetched

- `pipeline/object_store.py:117-122` -- `ObjectTooLargeError`; `:626-647` -- `_read_bounded` does
  `head` -> compare -> `get`, and re-checks the fetched length in case the object was replaced
  mid-read.
- `pipeline/object_store.py:341` / `:496` -- `read_object(..., max_bytes=...)` and `object_size()` on
  both the store and the facade.
- `planes/availability_reads.py:133-163` -- the generation is bounded by its MEASURED size; the
  pointer's self-declared `generation_bytes` is no longer read at all (`_whole_number` deleted).
- Tests `tests/test_read_only_store.py::test_an_oversize_object_is_refused_before_its_body_is_ever_fetched`
  (asserts `backend.get` was never called) and `::test_an_object_inside_its_ceiling_is_read_whole`.

### M3 -- the artifact listing is bounded in count, in bytes, and is newest-first

- `planes/artifact_reads.py:29-52` -- `MAX_LISTED_ARTIFACTS` 200 -> **25**, plus `MAX_LISTING_BYTES`
  (8 MB per request) and `MAX_WALKED_ARTIFACT_KEYS`.
- `planes/artifact_reads.py:83-112` -- newest-first by `ListedObject.last_modified` with the key as a
  stable tie-break; `listing_truncated: true` is a typed field on the wire; `_claim` now names
  `artifacts[0]`, since the newest row moved to the front.
- Test `tests/test_read_only_store.py::test_an_artifact_listing_answers_the_newest_first_and_says_when_it_was_cut_short`
  and `::test_a_listing_whose_artifacts_pass_the_request_budget_is_refused_by_name`.

### M4 -- a read-only facade and a sealed session

- `pipeline/object_store.py:283-326` -- `ReadOnlyObjectStore` Protocol and `ReadOnlyObjectStoreView`,
  built from a backend and a prefix and deliberately NOT wrapping an `ObjectStore`, so `put` and
  `delete` are unreachable rather than merely unused. `planes/**` now type their store parameter as
  `ReadOnlyObjectStore`; `planes/routes.py:126-150` builds and holds the facade.
- `pipeline/duckdb_session.py:150-162` -- `seal_serving_configuration`:
  `SET disabled_filesystems='LocalFileSystem'` then `SET lock_configuration=true`, in that order
  because the lock is one-way. `open_session(..., serving=True)` applies it after
  `apply_object_store`; `routes.open_serving_session` passes `serving=True`.
- Tests `::test_the_facade_a_plane_receives_has_no_way_to_write_or_delete` (asserts no `put`/`delete`)
  and `::test_a_sealed_session_refuses_to_read_the_local_filesystem` (the same `read_csv` answers
  BEFORE the seal and raises after, and `SET disabled_filesystems=''` is refused).

### M5 -- the pointer decides, before any listing

- `planes/fire_risk_reads.py:96-119` -- `read_lane_availability` + `covers()` ahead of
  `day_part_keys`; an uncovered day is `availability_day_not_covered`, an unpublished lane is
  `availability_unpublished`.
- `planes/analog_reads.py:122-148` -- each window day is judged against the pointer; an uncovered
  horizon joins `unreadable_days` rather than refusing the horizons that WERE published.
- `tests/serving_harness.py::publish_lane` -- new: advances a pointer onto a day already written, for
  a lane (`fire-risk`) that partitions by `valid_day` and so cannot use
  `write_and_publish_forecast_rows`. `tests/test_routes_fire_risk.py` fixture now publishes, and a
  new case proves a WRITTEN-but-unpublished lane still refuses every day.

### M6 -- weather-forecast is never silently skipped

- `config.py:76-86` -- `forecast_cells_key`, aliased to `PLANTGEO_ML_FORECAST_CELLS_KEY`, naming a
  bucket object rather than carrying the list itself.
- `pipeline/weather_forecast_daily.py:213-249` -- `read_forecast_cells`, bounded by
  `MAX_FORECAST_CELL_INVENTORY_BYTES`, with its own `ForecastCellInventoryError`.
- `pipeline/predict_daily.py:56-62, 349-372` -- `NO_FORECAST_CELLS`/`skipped` becomes
  `FORECAST_CELLS_UNCONFIGURED` with status **`refused`**; a broken inventory refuses under its own
  code. Wired through `interface/cli.py:85-108`.
- Tests: `test_predict_daily.py::test_a_deployment_with_no_cell_inventory_refuses_the_weather_lane_and_never_calls_it_skipped`
  plus three inventory cases in `test_weather_forecast_daily.py`.

### M7 -- conflicting digests are their own reason

- `planes/wire.py:56-60` -- `ARTIFACT_ABSENT_PROVENANCE_CONFLICTED`.
- `planes/forecast_reads.py:262-288` -- `_claim_for` now separates three absences: no digest column
  (`lane_is_not_model_backed`), the sentinel (`no_artifact_published`), and **two** digests in one
  cell (`artifact_provenance_conflicted`).
- Tests in `test_routes_forecast.py`.

### Minors

- No object keys, variable names or raw exception text in public bodies: `refusals.py` --
  `object_store_unconfigured()` takes no detail, `artifact_unreadable` drops `key`,
  `availability_no_published_day` drops `generation_key`, and the `read_over_budget` details in
  `artifact_reads`/`partition_reads` no longer quote a prefix. Every dropped fact is LOGGED
  (`routes.py:395`, `artifact_reads.py:184`). The answered payload still carries `pointer_key` and
  `generation_key` -- deliberately: those are the provenance the web contract asks for and
  `test_routes_forecast.py:182` asserts, and removing them would have weakened an assertion.
- Nearest cell through `foundation/lattice.py`: `planes/partition_reads.py:118-186` -- SQL supplies a
  bounded `DISTINCT` candidate set (capped at `MAX_NEAREST_CANDIDATES`), and the choice is made in
  whole micro-degrees with a deterministic tie-break. The float `ORDER BY` is gone.
- `planes/availability_reads.py:66-70` -- `published_horizon_days` docstring now says MAX published
  day minus issue day, and states that interior gaps do not shorten it.
- Dead code deleted: `availability_reads.refused_availability`, `wire.refusal`, `app.API_PREFIX`
  (and the now-unused `BASE_PATH` import), `availability_reads._whole_number`.
- `pipeline/predict_daily.py:255-265` -- `_fire_risk_outcome` catches `ObjectStoreError` as THIS
  lane's refusal; one lane's bad read no longer ends the turn.
- `pipeline/predict_daily.py:392-406` -- receipts carry `error_code_for(error)` (a snake_case code
  off the exception TYPE), never `f"{type}: {message}"`.

### Wire change against `bc08eca9`

`fire-risk` rows now write `quantile = "point"` (`layer-lanes.md` section 3, amended 2026-09-19).
`warehouse/streams.py:80-92` adds `POINT_QUANTILE` and `DETERMINISTIC_PROVENANCE_FIELDS`, which is
`FORECAST_PROVENANCE_FIELDS` with `quantile` carried as a **string**; `FIRE_RISK_SCHEMA` uses it,
while the drawn lanes keep the float fraction. `pipeline/fire_risk_daily.py:323` writes the label,
`planes/fire_risk_reads.py:140-152` (`_point_row`) selects on it, and `forecast_reads` renders
whatever the column holds. Noted in `planes/AGENTS.md`. **A client reading `quantile == 0.5` off a
fire-risk row now reads `"point"`.** Existing published fire-risk partitions carry the old float
type and will not conform to the new schema; the lane republishes on its next turn.

### RUNBOOK item 5 / Dockerfiles

`Dockerfile:50-69` now installs the DuckDB extensions into `/opt/duckdb-extensions`, `chown`s them
to `plantgeo`, and runs a build-time `open_guarded_connection()` probe AS the service user -- the
probe is the part the cron image was missing too, added at `infra/cron/Dockerfile:47-70` (where the
install also moved AFTER the `useradd` so the `chown` has a user to name). `RUNBOOK.md:50-57` item 5
struck through and recorded.

### Gates (run once, then one mechanical pass)

| gate | result |
|---|---|
| `uv sync --locked --all-extras` | 63 resolved, 61 audited, no lock change |
| `uv run --no-sync ruff format src tests scripts` | 4 files reformatted, 139 unchanged |
| `ruff format --check src tests scripts` | 143 files already formatted |
| `ruff check src tests scripts` | All checks passed (5 found, 4 auto-fixed, 1 `ARG001` fixed by hand) |
| `mypy src scripts` | Success: no issues found in 78 source files |
| `pytest -q` | **660 passed**, 2 pre-existing `RuntimeWarning`s in `test_seasonal_evaluation.py` |

Starting count was 642 passed; the batch adds 18 cases across `test_serving_pool.py` (new),
`test_read_only_store.py` (new), `test_routes_fire_risk.py`, `test_routes_forecast.py`,
`test_weather_forecast_daily.py`.

One existing assertion CHANGED rather than weakened:
`test_predict_daily.py::test_one_lane_refusing_does_not_stop_the_lanes_after_it:66` asserted the
weather lane was `"skipped"`, which M6 deliberately abolishes; it now asserts the lane reached its
own step and reported `FORECAST_CELLS_UNCONFIGURED`, which is the same isolation claim under the new
vocabulary. **No parity fixture was regenerated** (`git status --porcelain tests/fixtures/` is
empty); `tests/fixtures/parity/streams.json` pins the sibling-copied observed streams only and does
not cover the originated `fire-risk` schema.

### Receipt

- `git add services/plantgeo-ml-service`
- `uv run --no-sync python scripts/check.py --write-receipt` -- format PASS 0.83s, lint PASS 0.09s,
  mypy PASS 1.65s, pytest PASS 35.54s
- receipt sha: `sha256:3593115e0e0ba8d91ef3cf15adf860c0d8c7ba5c3b2711d6e3246674e2466dbb` over 162
  files, generated `2026-09-19T15:08:38.660715Z`
- `git add services/plantgeo-ml-service`
- `python scripts/verify_quality_receipt.py` -> **exit 0** ("quality receipt verified")

Nothing was committed.
