---
type: track-evidence
track: plantgeo_ml_service_20260918
slice: p2d-fire-risk-registration
branch: ml/p2d-agri-registration
---

# Phase 2D sweep — 2026-09-19

Monitor pass over the author's predictions (`phase2d-predictions.md`). AGRI_TEST_DATABASE_URL confirmed
genuinely unset (`echo "[${AGRI_TEST_DATABASE_URL}]"` printed `[]`, not a set empty string).

## Commands run

- `uv sync --locked --all-extras` — resolved clean, no lock drift, no `uv lock` needed.
- `uv run --no-sync python scripts/check.py` — first pass, then re-run after fixes.
- `uv run --no-sync pytest tests/parquet/test_lane_contract.py -v` — confirms the `forecast_module`
  binding test runs, not skips.
- `uv run --no-sync ruff format .`, `ruff check --fix tests/parquet/test_stream_schema_registry.py`.
- Web: no `node_modules` in worktree; created a directory junction
  (`cmd /c mklink /J node_modules C:\Users\atooz\Programming\plantgeo\node_modules`) rather than
  `npm install`, since the main checkout's `node_modules` is current for this branch's deps.
- `npx vitest run src/__tests__/region src/lib/region src/lib/map --reporter=dot`
- `npx tsc --noEmit -p .`
- `npm run check:data-boundary`
- `npx vitest run --reporter=dot` (full suite, bounded)

## Gate counts

| gate | first pass | after fixes |
| --- | --- | --- |
| agri format | PASS | PASS |
| agri lint | FAIL (3 errors) | PASS |
| agri mypy | PASS | PASS |
| agri pytest | 4 failed, 4578 passed, 89 skipped, 1 xfailed | PASS: 4582 passed, 89 skipped, 1 xfailed |
| `test_lane_contract.py` (3 new + full file) | — | 26 passed, all 3 new tests RAN (not skipped) |
| vitest targeted (region/map) | 10 files / 78 tests | PASS (unchanged) |
| tsc --noEmit | PASS | PASS |
| check:data-boundary | PASS | PASS |
| vitest full suite | not run first pass | 220 files / 2906 tests passed, 0 failed, 88.69s |

## Fixes applied (one batch)

1. `src/agri_data_service/execution/expert_label_export.py:180` — added
   `# noqa: PLR0913 - one argument per exported release's own identifying fields` on
   `write_expert_label_export` (6 args), matching the repo's existing `noqa: PLR0913` convention
   (`agent/botanical_species_profiles.py:82`, `agent/warehouse.py:432`, etc).
2. `tests/execution/test_expert_label_export.py:268` — `pytest.raises(ValueError, match="ml/|relative POSIX|traversing")`
   → `match=r"ml/|relative POSIX|traversing"` (RUF043, unescaped metacharacter pattern; author's
   predicted-lint risk #2 landed as this instead).
3. `tests/parquet/test_stream_schema_registry.py` — `ruff check --fix` resolved I001 unsorted import
   block (`FORECAST_ORIGINATED_STREAMS` inserted out of alphabetical order).
4. `ruff format .` reformatted `analysis/warehouse_session.py:51` (pre-existing long line, unrelated to
   this slice's edits, picked up incidentally by the whole-tree format pass) plus the new files.
5. **Predicted risk #5 confirmed**: `src/agri_data_service/execution/gap_repair_contract.py` —
   added `REPAIR_EXCLUSIONS` entries for `"fire-risk"` and `"weather-forecast"`
   ("forecast-originated: services/plantgeo-ml-service writes this lane, not an agri direct writer"),
   fixing `test_gap_repair.py::test_every_census_layer_is_either_bound_to_a_writer_or_excluded_with_a_reason`
   without editing the test.
6. `tests/parquet_ops/test_coverage_census.py:74` — `EXPECTED_REGISTERED_CENSUS_LANES: Final = 30` →
   `32` (the two new lanes are census lanes, exactly the count the author's slice adds; the parity
   with predicted risk #5). `EXPECTED_CENSUS_RUNG_ROWS` is derived
   (`= EXPECTED_REGISTERED_CENSUS_LANES * len(ZOOM_TIERS)`), so it auto-corrected to 128 with no
   separate edit; this fixed the third pytest failure (`test_a_cold_census_lists_each_registered...`,
   `test_a_cold_census_bounds_parallel_r2_listings_without_a_clock`) for free.

No assertion was weakened; every fix either matched an existing repo convention (noqa, raw regex,
import sort) or added exactly the two-lane classification/count the author's own design (section 2
of the predictions doc) already argued for.

## Standing failures

None. All four agri gates and all four web gates are green after the batch.

## Receipt

- `git add services/agri-data-service`, `uv run --no-sync python scripts/check.py --write-receipt`,
  `git add services/agri-data-service` again.
- `QUALITY_RECEIPT.json`: `tree_digest sha256:4545101b8b856749c75b07e25b14b6de98e874ee520ec33551b3cd7eb22ef556`
  over 862 files, generated `2026-09-19T14:39:09.184852Z`.
- `uv run --no-sync python scripts/verify_quality_receipt.py` → exit 0, printed
  "quality receipt verified: sha256:4545101b8b856749c75b07e25b14b6de98e874ee520ec33551b3cd7eb22ef556 over 862 files".

Staged (in addition to the agri tree): `src/lib/map/layer-region-binding.ts`,
`src/lib/region/kenya_highlands.ts`, `src/lib/region/pnw.ts`,
`conductor/tracks/plantgeo_ml_service_20260918/evidence/phase2d-predictions.md`,
`docs/lanes/fire-risk.md`, `docs/lanes/weather-forecast.md`.

`git status --porcelain | wc -l` → 27 (this evidence file not yet added when counted).

No commit, no push, per instructions.

## p2d-fix-batch

Applied 2026-09-19 against the phase 2D review (`metadata.json` `reviews.phase2d`). One batch, then
one gate sweep. The coordinator decision drove B3: **`fire-risk` and `weather-forecast` stay OUT of
the slider census and IN `REPAIR_EXCLUSIONS`** until a `kind=forecast` census exists and the ML
publisher is warm.

### B3 — the two ML lanes leave the census; the observed kind is refused

- `services/agri-data-service/src/agri_data_service/parquet_ops/coverage.py:67` — the exclusion,
  with the reason on it:
  `NON_SLIDER_REGISTERED_LAYERS: Final = frozenset({"calendar", "fire-risk", "signal", "weather-forecast"})`
  The comment above it states who writes the two lanes, that the census is observed-only and
  `fire-risk` has no `kind=observed` prefix to list, and the condition for re-entry.
- `tests/parquet_ops/test_coverage_census.py:77` — `EXPECTED_REGISTERED_CENSUS_LANES` **reverted
  32 -> 30**, the pre-slice value. Measured, not assumed: `len(registered_census_lanes())` is 30
  over 34 registrations (34 - 4 excluded), and `EXPECTED_CENSUS_RUNG_ROWS` is derived from it, so
  the rung pin followed to 120 with no second edit. B2 is therefore closed by the exclusion itself.
- `src/agri_data_service/warehouse/parquet/schema.py:44` — new typed
  `ForecastOnlyStreamError(StreamSchemaError)`; `:180-187` — `get_stream_schema` validates the kind
  FIRST and refuses the observed side of a `FORECAST_ORIGINATED_STREAMS` member with the writing
  service's own words: *"stream 'fire-risk' is forecast-only; it has no observed side to read"*
  (mirrors `plantgeo_ml_service/warehouse/streams.py::stream_schema`). It is a `StreamSchemaError`
  subclass so existing handlers still catch it. `observed_stream_schema` stays the raw registry
  lookup, which is what tier derivation and the registry tests need.
- **Callers grepped.** Every `get_stream_schema(..., "observed")` call site resolves its layer from
  the census, a dedicated product list or a hard-coded stream constant; none can now name a
  forecast-only slug. `warehouse/parquet/tiers.py:364,858` use `observed_stream_schema` and are
  unaffected, as is `tests/parquet/test_tier_derivation.py`.
- Tests: `tests/parquet/test_stream_schema_registry.py:279` (the refusal, both spellings, plus the
  registration still reachable) and `:299` (catchable as an ordinary `StreamSchemaError`);
  `tests/parquet_ops/test_coverage_census.py:504`
  `test_no_census_lane_asks_a_forecast_only_stream_for_a_contract_it_does_not_have` proves the
  census never makes the call — it asserts no census layer is forecast-originated, that
  `FORECAST_ORIGINATED_STREAMS <= NON_SLIDER_REGISTERED_LAYERS`, and that every census lane resolves
  its own kind (the one call the census, slider catalogue, coverage payload and serving reads share).
- `REPAIR_EXCLUSIONS` entries kept as the coordinator directed; the gap-repair test classifies
  census layers and tolerates an excluded layer that is not one.

### B4 — schema parity is a test now, not prose

- `services/agri-data-service/tests/parquet/test_ml_schema_parity.py` (new, 7 tests) — loads the
  sibling's three modules from their FILE PATHS (`_ML_SERVICE_SRC` on `sys.path` in a module-scoped
  fixture, removed again in `finally`; `spec_from_file_location` alone cannot work because the ML
  modules import each other by absolute dotted name) and asserts `arrow_schema.equals(...)`, sort
  columns, compression and name for `FIRE_RISK_SCHEMA` vs `warehouse/schemas/fire_risk.py` and
  `WEATHER_FORECAST_SCHEMA` vs `warehouse/schemas/weather_forecast.py`, plus metadata-free equality
  of the expert-label export schema against `pipeline/expert_labels.py::EXPERT_LABEL_SCHEMA` and the
  presence and non-nullability of its `label_key` sort column. Two extra per-column tests name WHICH
  field moved rather than answering one bool.
- **It RUNS here and SKIPS elsewhere, both proven.** In this worktree the six parity assertions
  execute (138 passed, 0 skipped across the six touched files). With `_ML_SERVICE_PACKAGE` pointed
  at a missing path the fixture raises `Skipped: no plantgeo-ml-service tree at <path>; schema
  parity between the two services is only checkable in a monorepo checkout, never inside the agri
  Docker image` — the `test_lane_contract.py::_require_ml_service_tree` pattern.
- `tests/execution/test_expert_label_export.py:45-49` — the comment claiming *"a parity test that
  imports the other service is impossible by design"* was falsified by the above and is corrected:
  the hand-spelled column list is the READABLE end of the pin, the equality lives in the new file.

### MAJOR — `weather_forecast.py` header no longer overclaims

`src/agri_data_service/warehouse/schemas/weather_forecast.py:14-31` — the equality claim is now
"same field names, types, nullability, order and sort columns", and a separate paragraph records the
base-non-null list as a HOUSING difference, matching `fire_risk.py`'s honest note: the ML copy
carries `base_non_null_columns=("cell_id",)` on the schema object (no derivation engine there), here
the identical list sits on the `TierDerivation` at `:133`, which is where this service's engine
reads it. The header also now points at the parity test instead of asking to be believed.

### MINORs

1. `docs/lanes/fire-risk.md:46-55` — the 14-day horizon is cited as a declared deviation from
   `layer-lanes.md` section 2's 30-day default, naming the 2026-09-19 amendment that added
   `fire-risk` to that section by name, and the FR-5 backtest gate as the evidence bound. Section 7
   (`:113-116`) cites the same amendment instead of asserting the requirement bare.
2. `src/__tests__/region/layer-region-binding.test.tsx:253-268` — the "one exception" comment now
   separates the two questions it was conflating: `land-context` remains the one platform layer with
   no toggle path (the set is unchanged, and correctly so), while the comment names all THREE
   currently UNBOUND platform layers — `land-context`, `fire-risk`, `weather-forecast` — with why
   each is unbound, and records that the two ML lanes ARE toggle-reachable through
   `REGION_LAYER_SLUG_BY_WAREHOUSE_NAME`, which is exactly what keeps them off `not_federated`.
3. `src/agri_data_service/execution/expert_label_export.py:85-102` — new `_export_key` refuses a
   `--prefix` outside `ml/` with the typed `ExpertLabelExportRefusal`, naming the flag, before the
   release is read and in a dry run too; the store's bare `ValueError` now only backs it up.
   `:319-336` — the CLI is **dry run by default**: `--dry-run` is gone and writing requires
   `--apply`, documented in the command docstring (the write is immutable, so the default must be
   the reversible one) and in `execution/AGENTS.md:419` (new section). Four new tests, including one
   that pins `--apply` as a flag defaulting to False with no `--dry-run` left to forget.

### Gates — one sweep, all green on the first pass

| gate | result |
| --- | --- |
| agri `scripts/check.py` format | PASS |
| agri lint | PASS |
| agri mypy | PASS |
| agri pytest | PASS — **4599 passed, 89 skipped, 1 xfailed** (was 4582/89/1; +17 from this batch) |
| six touched agri test files | 138 passed, 0 skipped (the parity tests RAN) |
| `npx vitest run src/__tests__/region --reporter=dot` | 9 files / 74 tests passed |
| `npx tsc --noEmit -p .` | PASS (exit 0) |
| `npm run check:data-boundary` | PASS — 12 URL rules, restricted-import, observation-fabrication |

No second pass was needed: nothing in the batch produced mechanical fallout.

`node_modules` remains the directory junction to the main checkout's; it was not replaced.

### Receipt

- `git add services/agri-data-service`; `uv run --no-sync python scripts/check.py --write-receipt`;
  `git add services/agri-data-service`.
- **CRLF trap hit and fixed before the receipt was accepted.** Two files written through Windows
  text APIs (`warehouse/parquet/__init__.py`, `tests/parquet/test_ml_schema_parity.py`) landed with
  CRLF and drew `git add`'s normalization warning. Both were rewritten byte-wise to LF and the
  receipt regenerated; a scan of every staged, modified and untracked file now reports no CRLF.
- `QUALITY_RECEIPT.json`: `tree_digest sha256:889dafd6e52a4be23b5d2bf71a551cd03b0c0d1f0b70ce8037538f1797fb9ff2`
  over **863 files** (was 862; the new parity test), generated `2026-09-19T15:05:36.259850Z`.
- `uv run --no-sync python scripts/verify_quality_receipt.py` -> **exit 0**, printed
  "quality receipt verified: sha256:889dafd6e52a4be23b5d2bf71a551cd03b0c0d1f0b70ce8037538f1797fb9ff2 over 863 files".

Staged beyond the agri tree: `src/__tests__/region/layer-region-binding.test.tsx`,
`src/lib/map/layer-region-binding.ts`, `src/lib/region/pnw.ts`, `src/lib/region/kenya_highlands.ts`,
`docs/lanes/fire-risk.md`, `docs/lanes/weather-forecast.md`, and both evidence files under
`conductor/tracks/plantgeo_ml_service_20260918/evidence/`.

No commit, no push, per instructions.

### One thing deliberately not touched

`services/plantgeo-ml-service/RUNBOOK.md:88` still says the agri export verb "does not exist yet"
and shows it without `--apply`. That file belongs to the ML-service lanes, not this slice. The stale
line is now harmless — the verb it names is a dry run without `--apply` — but it owes a correction
in the next ML sweep, together with the flag change.
