---
type: evidence
track: plantgeo_ml_service_20260918
slice: p2a-parquet-io
recorded_on: 2026-09-19
---

# Phase 2A monitor sweep

Run from `services/plantgeo-ml-service`. All edits confined to that directory; no git commands run.

## Commands and results

1. `uv sync --locked --all-extras` -- PASS (`Resolved 62 packages`, `Audited 60 packages`; lockfile
   was already current, `--locked` did not fail, no `uv lock` needed).
2. `uv run --no-sync ruff format --check src tests scripts` -- BEFORE: 5 files would reformat.
   AFTER: `87 files already formatted`.
3. `uv run --no-sync ruff check src tests scripts` -- BEFORE: 43 errors (8 auto-fixable). AFTER:
   `All checks passed!`.
4. `uv run --no-sync mypy src scripts` -- BEFORE: 43 errors in 9 files. AFTER:
   `Success: no issues found in 45 source files`.
5. `uv run --no-sync pytest -q` -- BEFORE fixes not run (author rule). FIRST sweep run: 5 failed,
   338 passed. AFTER the batch: **4 failed, 339 passed**, 2 warnings (unrelated pre-existing
   `RuntimeWarning`s in `test_seasonal_evaluation.py`, phase 1, not touched by this slice).

## Fixes applied (one batch, one re-run)

### `ruff format` (mechanical reflow, no `--fix`, just format)
- `src/plantgeo_ml_service/foundation/parquet_paths.py:187` -- wrapped the `ZoomTierError` message.
- `src/plantgeo_ml_service/pipeline/object_store.py:465` (pre-format line) -- wrapped
  `offending = tuple(...)`.
- `tests/parity_parquet_cases.py`, `tests/test_object_store.py`, `tests/test_parquet_paths.py` --
  wrapped four over-length literals/comprehensions. Matches prediction #1.

### Broken `type: ignore` syntax (mypy `[syntax]` errors, prediction was silent on this)
Every `# type: ignore[code] - reason` in the slice used a bare ` - ` before the rationale, which
mypy does not accept as a valid ignore-comment continuation (only `# type: ignore[code]  # reason`
is). This made every one of the ignores inert, so the *real* error underneath (`import-untyped`,
`attr-defined`, `return-value`, `arg-type`) also fired. Fixed by turning ` - reason` into `  # reason`
(second `#`) in nine files, one line each:
- `src/plantgeo_ml_service/pipeline/availability_publisher.py:15,16,122`
- `src/plantgeo_ml_service/pipeline/duckdb_session.py:19`
- `src/plantgeo_ml_service/pipeline/expert_labels.py:14,15,182`
- `src/plantgeo_ml_service/pipeline/object_store.py:16,17,218`
- `src/plantgeo_ml_service/pipeline/observed_reader.py:107`
- `src/plantgeo_ml_service/warehouse/availability.py:15`
- `src/plantgeo_ml_service/warehouse/streams.py:13`
- `tests/parity_parquet_adapters.py:121` (now renumbered)
- `tests/test_parquet_parity.py:59`

### `_outcome`'s parameter type (mypy "Cannot infer type of lambda", not predicted)
- `tests/parity_parquet_cases.py:433` -- `_outcome(call: Callable[[], object])` forced mypy to
  bidirectionally infer the `lambda case=case: ...` default-argument idiom against a `Callable`
  protocol, which mypy cannot do inside a comprehension; matched phase 1's proven pattern instead:
  `def _outcome(call: Any) -> str:`. Confirmed by diffing against `tests/parity_cases.py:79`, which
  already uses `Any` and already passes.

### `PathsAdapter`/`AvailabilityAdapter` real type mismatches (mypy `arg-type`, prediction #4)
- `tests/parity_parquet_adapters.py:57-59` -- `paths.availability_lane_root` takes
  `(str, PartitionKind)`, narrower than the adapter field's `(str, str)`; wrapped in a lambda that
  does `cast("PartitionKind", kind)`.
- `tests/parity_parquet_adapters.py:121` -- `generation_key_for`'s second argument is
  `Literal["observed", "forecast"]`; added the same `cast("PartitionKind", kind)`.
- `tests/parity_parquet_adapters.py:127` -- `availability_pointer_path`, same cast (its existing
  `type: ignore[arg-type]` comment is removed now that the cast makes it unnecessary).

### `ruff check --fix` (8 auto-fixed: F401, RUF022, RUF100 x3, SIM300, I001 x2)
- `scripts/regenerate_parity_fixtures.py:19` -- removed unused `Callable` import.
- `src/plantgeo_ml_service/pipeline/availability_publisher.py:347` -- sorted `__all__`.
- `src/plantgeo_ml_service/pipeline/duckdb_session.py:152`,
  `src/plantgeo_ml_service/pipeline/object_store.py:465`,
  `tests/parity_parquet_adapters.py` (sibling section) -- removed three `noqa` comments whose codes
  (`S608`, `PD011`, `SLF001`) are not in `ruff.toml`'s `select` list, so they never suppressed
  anything; ruff's own `RUF100` fix. (`SLF001` on `documents._generation_receipt_sha256` was
  predicted (#5); the other two were not.)
- `tests/test_parquet_paths.py:168` -- `ZOOM_TIERS[-1] == BASE_PARTITION_ZOOM` (Yoda condition).
- `tests/test_observed_reader.py`, `tests/test_streams.py` -- import-block reordering (I001).

### Manual mechanical lint fixes (not `--fix`-able, one line each)
- `src/plantgeo_ml_service/pipeline/expert_labels.py:138` -- merged
  `release == "." or release == ".."` into `release in {".", ".."}` (PLR1714).
- `src/plantgeo_ml_service/pipeline/object_store.py:36` -- moved `ParquetStreamSchema` import into
  the existing `TYPE_CHECKING` block (TC001; annotation-only use, confirmed by grep).
- `tests/test_duckdb_session.py:6` -- moved `pathlib.Path` into a new `TYPE_CHECKING` block (TC003;
  annotation-only use on `tmp_path: Path` fixture parameters).
- `tests/parity_parquet_cases.py` -- renamed the ambiguous `l` lambda-default parameter to `ly` at
  4 call sites (E741); inlined 4 wrapper lambdas that only forwarded their args unchanged into
  `_grid(adapter.day_prefix)` etc. (PLW0108), since the adapter attribute's `Callable[..., str]`
  already matches `_grid`'s expected signature exactly.
- `src/plantgeo_ml_service/pipeline/observed_reader.py` + `tests/test_observed_reader.py` -- renamed
  `ObservedReadRefusal` to `ObservedReadRefusalError` (N818), 13 occurrences across the 2 files that
  own it; no other file in the service referenced the old name.
- `# noqa: PLR0913 - <reason>` added, matching the established house convention already used
  throughout `src/plantgeo_ml_service/method/ml/*.py`:
  - `src/plantgeo_ml_service/pipeline/object_store.py:318` (`write_partition`, 6 args).
  - `tests/parity_parquet_adapters.py:94` (`metadata` closure, 8 args).
- `# noqa: PLC0415` added, matching the same file's own existing convention for lazy imports (used
  to avoid importing both sides' `plantgeo_ml_service` submodules until the adapter that needs them
  is actually called):
  - `tests/parity_parquet_adapters.py:43,63,74,85,92,118` (six lazy imports inside the `ml_*_adapter`
    functions and `pointer_key`).
  - `tests/test_streams.py:129` (`LaneContract` imported only for one negative-case test).
- `# noqa: ARG002` added on `tests/test_availability_publisher.py:186`
  (`_AlwaysLoses.compare_and_set`, a test double that must match the `PointerStore` protocol shape
  but never reads its own arguments).
- `# noqa: PLR2004` added on the bare-int assertions ruff flagged, matching the bare-`# noqa: PLR2004`
  convention already used across `tests/test_recommendation_models.py` etc.:
  `tests/test_availability_publisher.py:214`, `tests/test_expert_labels.py:86`,
  `tests/test_object_store.py:63,141,207,233`.

### Test fix: wrong fixture dates (not a wrong path/fixture name, but the same class of bug)
- `tests/test_observed_reader.py::test_a_window_below_the_lanes_history_floor_is_refused` --
  `date(floor.year - 1, 1, 1)..CEILING` is a ~27-year span against `fire-detections`' 2000-11-01
  floor, which trips `_validated_window`'s `window_too_wide` check (`MAX_WINDOW_DAYS = 4_000`,
  `src/plantgeo_ml_service/pipeline/observed_reader.py:119`) BEFORE the `below_history_floor` check
  it runs next (`observed_reader.py:127`) ever gets reached -- the checks are ordered
  `window_inverted` -> `window_too_wide` -> `below_history_floor`, by design, and the test's own
  dates hit two refusal conditions at once. Narrowed the window's `last_day` to
  `date(floor.year - 1, 1, 2)` (a 2-day span entirely before the floor) so only
  `below_history_floor` can fire. No assertion was touched or weakened; the test now proves what its
  name claims.

## Standing failures (not fixed -- real findings)

**4 of 4 remaining `pytest` failures are one root cause, confined to `tests/test_expert_labels.py`,
and are a Windows-toolchain environment gap, not a defect in the ten new modules:**

```
tests/test_expert_labels.py::test_an_exported_release_reads_back_with_its_envelope_decoded
tests/test_expert_labels.py::test_an_outcome_that_does_not_belong_to_its_kind_is_refused
tests/test_expert_labels.py::test_an_unknown_confidence_band_is_refused_because_it_has_no_sample_weight
tests/test_expert_labels.py::test_an_undecodable_envelope_is_refused
```

Every one raises, at `read_expert_labels` -> `table.to_pylist()` -> pyarrow's
`TimestampScalar.as_py()` -> `pyarrow.lib.string_to_tzinfo` -> `zoneinfo._common.load_tzdata`:

```
zoneinfo._common.ZoneInfoNotFoundError: 'No time zone found with key UTC'
ModuleNotFoundError: No module named 'tzdata'
```

Reproduced directly: `uv run --no-sync python -c "import zoneinfo; zoneinfo.ZoneInfo('UTC')"` fails
the same way inside this project's uv-managed interpreter
(`AppData\Roaming\uv\python\cpython-3.12.9-windows-x86_64-none`), while the machine's separate system
Python succeeds -- Windows carries no OS-level IANA tz database, and CPython's `zoneinfo` falls back
to the pure-Python `tzdata` PyPI package on Windows only; this project's `pyproject.toml`/`uv.lock`
declare no `tzdata` dependency. `expert_labels.py` is the first module in the tree whose schema
carries a `pa.timestamp(..., tz="UTC")` column that gets decoded with `.to_pylist()` (`grep` across
`tests/*.py` found no other file calling `pa.timestamp` with a timezone). `Dockerfile:1` bases on
`python:3.12.12-slim-bookworm`, a Debian image that ships system tzdata by default, so this almost
certainly passes in the actual build/CI image and is Windows-host-only -- the same class of gap
recorded in `plantgeo-linux-receipt-catches-windows-timing` memory. Not fixed here: adding a
`tzdata` dependency is a `pyproject.toml`/`uv.lock` change outside "mechanical," and per the sweep
rule ("never fix by ... regenerating a parity fixture" analog: never paper over an environment gap by
guessing at a dependency add) it is left for the reviewer to confirm whether `tzdata` should be
pinned for Windows dev parity or whether this is accepted as CI-only coverage.

## Gate summary

| Gate | Before | After |
|---|---|---|
| `ruff format --check` | 5 files would reformat | 87/87 formatted |
| `ruff check` | 43 errors | 0 errors |
| `mypy src scripts` | 43 errors, 9 files | 0 errors, 45 files clean |
| `pytest -q` | 5 failed, 338 passed | 4 failed, 339 passed |

## p2a-fix-batch

The phase-2A review (`metadata.json` → `reviews.phase2a`, CHANGES-REQUIRED) returned 3 blockers,
5 majors and 4 minors. All are applied below, plus the `tzdata` call the monitor sweep above left to
the reviewer. Every edit is inside `services/plantgeo-ml-service/` and this file; the conventions of
the sweep above are kept, including `# type: ignore[code]  # reason`.

### B1 — `conform_to_stream_schema` never sorted, so bytes depended on caller row order

- `src/plantgeo_ml_service/pipeline/object_store.py:551-565` — select, cast AND
  `conformed.sort_by([(column, "ascending") for column in stream.sort_columns])`, the sibling's
  `pipeline/parquet/objectstore.py:1133` line.
- `tests/test_object_store.py:287-304` — two row orders of one table serialize to the same sha256,
  and the conformed table is asserted to be in grain order.
- **Parity fixture extended with a serialized-BYTES case** (a deliberate new case; see
  "Fixture regeneration"): `tests/parity_parquet_cases.py:78-99,342-354,544-573` adds a fixed,
  out-of-grain-order `fire-detections` table and makes `evaluate_streams` emit `serialized_row_order`
  and `serialized_sha256`. `tests/parity_parquet_adapters.py:96-103,221-229` wires each side's own
  conform + serialize (the sibling's private `_serialize_parquet`, deliberately: the bytes that
  function writes ARE the contract). Measured — both services emit
  `efd08edc2eedb5c96dc80edd0c3d03b4ec7c84baad2fe25d9362e0189049783d` for the same input, so the
  harness does support the case.

### B2 — the two retraction guards

- `object_store.py:338-411` — `write_partition` refuses over a governed absence
  (`GovernedAbsenceConflictError`, new at `object_store.py:102-108`) and, at `part_index == 0`,
  clears the day's completion marker before uploading: THE RETRACTION POINT, sibling
  `objectstore.py:586-601`.
- `object_store.py:431-465` — `write_absence_marker` refuses when data parts exist and clears a
  residual completion marker, sibling `objectstore.py:617-640`.
- New supporting methods `absence_exists`, `part_blocking_absence`, `clear_completion_marker` at
  `object_store.py:467-487`.
- Tests `tests/test_object_store.py:307-371`: absence-then-write refused, write-then-absence refused,
  part-0 clears the marker, part-1 deliberately does NOT, absence retracts a residual marker.

### B3 — the pointer could advance a lane the generation never touched

- `src/plantgeo_ml_service/pipeline/availability_publisher.py:314` — the pointer key is now
  `store.absolute_key(availability_pointer_path(layer, kind))`: the same prefixed,
  traversal-guarded resolution the generation object goes through.
- `availability_publisher.py:138-152` — `BotoPointerStore` lost its own `prefix` field, so no store
  can add a second root under the one the `ObjectStore` resolved.
- Tests `tests/test_availability_publisher.py:253-273`: with `prefix="ml/scratch/x/"` the only
  pointer key written starts with that prefix, and a bare-prefix escape raises `ObjectKeyError`.

### M4 — the lost-race retry re-put a stale payload

- `availability_publisher.py:292-339` and `341-367` — the head is re-read on every attempt and the
  pointer payload is REBUILT through `pointer_for(..., prior=...)` (`:258-291`) with the newly read
  generation as `prior_generation_key`. `_prior_binding` refuses with a lost-race receipt for a body
  that is not a pointer, a pointer for another lane, or a head whose digest is already this
  generation (re-pointing would name it its own predecessor).
- Test `tests/test_availability_publisher.py:275-301` uses an in-memory store that flips the etag
  once and asserts the landed head names the PEER as `prior_generation_sha256`;
  `:303-312` covers the already-the-head refusal.

### M5 — `IfMatch` honoured was assumed

- `availability_publisher.py:62-86` (the process latch), `:325-331` and `:369-382`
  (`_pointer_landed_exactly`) — after an advance the pointer is read back and compared to the bytes
  written and to the etag that was expected; a mismatch returns
  `outcome="conditional_put_unsupported"` and latches the process, because a per-call refusal would
  let the very next publish overwrite a peer anyway.
- Test `tests/test_availability_publisher.py:314-336` with a fake store that answers 200 and
  persists only the first write: the outcome is reported and the next publish raises.

### M6 — `_require_rows_published_by` and the nature check

- `availability_publisher.py:435-467` — `_validated_rows` now takes `created_at` (the build order is
  swapped at `:216-218`) and refuses `row.published_at > created_at` (sibling
  `availability_documents.py:654-657`) and any row redeclaring the lane's `nature` (sibling
  `_parse_rows`, `availability_documents.py:673-680`).
- Tests `tests/test_availability_publisher.py:348-361`; the fixture `_row` helper gained a `nature`
  parameter.
- **Owed by the lanes slice, NOT written by p2a:** a `kind=forecast` lane root's bootstrap marker and
  receipt (`<lane_root>/availability/bootstrap/_BOOTSTRAPPED.json`, sibling
  `availability_index.bootstrap_availability`). `AvailabilityConfig` requires the receipt and every
  pointer carries it, but nothing in this slice creates a lane's generation-zero history. Until it
  exists, `parquet_ops/availability_coverage.py` has no bootstrapped lane to answer `selectable_days`
  through, so **acceptance 5.2 needs the lanes slice as well as p2a.** Recorded in
  `src/plantgeo_ml_service/pipeline/AGENTS.md`, "What p2a does NOT write, and who owes it".

### M7 — the schema was a caller argument

- `object_store.py:338-369` — `write_partition(table, *, layer, kind, zoom, day, part_index,
  stream=None)` looks the contract up through `warehouse.streams.stream_schema(layer, kind)`, the
  sibling's `get_stream_schema(layer, kind)` shape. `stream=` survives for tests and is ASSERTED
  equal to the registry's, raising `ParquetSchemaMismatchError` otherwise.
- Tests `tests/test_object_store.py:374-405`: a caller's own schema is refused, a forecast partition
  takes the forecast schema without being told, and observed rows are refused on the forecast side
  because the six provenance columns are mandatory. Call sites updated across
  `tests/test_object_store.py` (12) and `tests/test_observed_reader.py:131,154,163`.

### M8 — retry and bootstrap keys, and the pending marker

- `src/plantgeo_ml_service/foundation/parquet_paths.py:66-72,302-355,514-527` — the retry segment,
  day prefix, suffix and quarantine suffix, the bootstrap segment and file name, plus
  `availability_retry_prefix`, `availability_retry_path`, `availability_retry_quarantine_path`,
  `try_parse_availability_retry_path`, `try_parse_availability_retry_quarantine_path`,
  `availability_bootstrap_marker_key` and `require_lane_root` (sibling `objectstore.py:145-184` and
  `availability_documents.py:415-418`). Neither retry parser normalises backslashes, because the
  sibling's does not and a copy accepting one more shape is a drift.
- Parity fixture: five new `parquet_paths.json` sections over `RETRY_PARSE_CASES`
  (`parity_parquet_cases.py:78-90,261-289`) and `bootstrap_marker_key` in `availability.json`
  (`parity_parquet_cases.py:428`); adapters at `parity_parquet_adapters.py:69-76,157,193-198,253`.
  Both sides MATCH on every new case.
- `object_store.py:489-520` — `put_immutable` and `write_availability_retry` (bounded at 8 MiB like
  the sibling); `availability_publisher.py:384-401` writes one claim per terminal day on a lost race.
- Tests `tests/test_object_store.py:429-441` and `tests/test_availability_publisher.py:363-377`.

### M9, M10, M11

- M9 — `availability_publisher.py:114-122,149-152,404-412`: `read_pointer` is bounded by
  `POINTER_MAX_BYTES` in both stores, and the boto one reads one byte past the ceiling rather than
  downloading the whole body to discover it is not a pointer. Test
  `tests/test_availability_publisher.py:338-346`.
- M10 — `object_store.py:489-506`: `put_immutable` adopts an exact replay and raises
  `ImmutableObjectConflictError` on different bytes under a content address, like
  `availability_storage.put_immutable`; `publish_generation` uses it for the generation object. Test
  `tests/test_object_store.py:408-417`.
- M11 — `tests/test_object_store.py:419-427` asserts `metadata.format_version == PARQUET_FORMAT_VERSION`
  ("2.6") on the bytes the writer actually emitted.
- M12 (metadata-value parity weakening) was accepted by the reviewer and is unchanged.

### TZ — the four Windows `test_expert_labels.py` failures

`pyproject.toml:24-28` adds `tzdata>=2024.1` to `[project].dependencies`, then `uv lock`
(`Resolved 63 packages`, `Added tzdata v2026.4`). Declared unconditionally rather than behind a
`sys_platform` marker, so every host decodes `pa.timestamp(tz="UTC")` against ONE tz database rather
than "whatever the base image ships, or nothing". The four named tests now pass:
`uv run --no-sync pytest -q tests/test_expert_labels.py` → `13 passed`.

### Fixture regeneration — deliberate, and why

`scripts/regenerate_parity_fixtures.py` was run ONCE, for the new cases B1 and M8 add
(`serialized_row_order`, `serialized_sha256`, the five retry sections, `bootstrap_marker_key`) —
never to make a failing assertion pass. Before regenerating, both adapters were evaluated against
each other directly and printed `paths MATCH / streams MATCH / avail MATCH` on every key including
the new ones, so the fixture records an agreement that already held. No pre-existing fixture value
changed.

### Gates (one sweep, plus the single permitted re-run for two mechanical failures of my own)

| Gate | First run | After |
|---|---|---|
| `uv sync --locked --all-extras` | n/a, lock re-cut for `tzdata` | PASS, 63 packages resolved |
| `ruff format --check src tests scripts` | 1 file would reformat | `87 files already formatted` |
| `ruff check src tests scripts` | 1 error (PLR0911 in `_prior_binding`) | `All checks passed!` |
| `mypy src scripts` | 1 error (untyped `pyarrow` import in the new parity case) | `Success: no issues found in 45 source files` |
| `pytest -q` | — | **365 passed**, 2 warnings (was 4 failed, 339 passed) |

Both "first run" failures were introduced by this batch and fixed in the one re-run: `_prior_binding`
was split into it and `_decoded_head` (`availability_publisher.py:341-367`), and the lazy
`import pyarrow as pa` at `tests/parity_parquet_cases.py:546` took the house
`# type: ignore[import-untyped]  # noqa: PLC0415  # …` form. The 2 warnings are the same pre-existing
`RuntimeWarning`s in `test_seasonal_evaluation.py` this slice does not touch. Net test count
`339 → 365` (+26).

### Receipt

`git add services/plantgeo-ml-service` → `uv run --no-sync python scripts/check.py --write-receipt`
(format PASS 0.05s, lint PASS 0.04s, mypy PASS 1.03s, pytest PASS 9.97s) →
`git add services/plantgeo-ml-service` → `python scripts/verify_quality_receipt.py`.

- receipt sha256: `5d37cf5e3091859cfe61cc29802d696bad40089a847b9826d11c675e6187ea08`, over 99 files,
  generated `2026-09-19T12:39:41.092676Z`
- verifier exit code: **0** (`quality receipt verified`)

Nothing was committed; the tree is staged only.
