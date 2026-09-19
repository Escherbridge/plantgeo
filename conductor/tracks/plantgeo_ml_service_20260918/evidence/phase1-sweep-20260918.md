---
type: evidence
track: plantgeo_ml_service_20260918
slice: phase1-monitor-sweep
---

# Phase 1C monitor sweep — 2026-09-18

Run against the combined tree left by p1a (`services/plantgeo-ml-service` skeleton) and p1b (hard
cut of ML/Monte Carlo out of `services/agri-data-service`). Predictions read from
`conductor/tracks/plantgeo_ml_service_20260918/evidence/phase1-predictions.md` and
`services/plantgeo-ml-service/RUNBOOK.md` before running anything.

**Mid-sweep plan changes from the coordinator, both honoured:**
1. Skip STEP 4 (receipts) entirely — no `git add`, no `--write-receipt`, on either service. No
   `git add` was run at any point in this sweep, so there is nothing to restage/restore.
2. No edits at all under `services/agri-data-service` — a concurrent session's verifier is
   digesting that tree. Confirmed via `git status --porcelain services/agri-data-service` that
   every changed path there is already staged (`M `/`D ` in column 1) by that other session before
   I ever ran a command against it. I only ran read-only commands (`uv sync`, `scripts/check.py`,
   `pytest`) there; zero bytes changed.

## STEP 1 — services/plantgeo-ml-service (fully green, fixed then re-verified)

Commands, in order:
```
uv sync --locked --extra dev
uv run --no-sync ruff format --check src tests scripts   # 64 files already formatted
uv run --no-sync ruff check src tests scripts             # 16 errors, first pass
uv run --no-sync ruff check --fix src tests scripts        # 2 auto-fixed (I001 import order)
# 14 manual mechanical fixes (below)
uv run --no-sync ruff check src tests scripts              # All checks passed!
uv run --no-sync ruff format --check src tests scripts     # 64 files already formatted (unchanged)
uv run --no-sync mypy src scripts                           # Success: no issues found in 35 source files
uv run --no-sync pytest -q                                  # 176 passed, 2 warnings, 12.81s
```

Final pass/fail/skip: format PASS, lint PASS (0 errors), mypy PASS (0 errors), pytest 176 passed /
0 failed / 0 skipped.

None of the near-certain predictions in phase1-predictions.md §6 items 1-3 materialized as
predicted (format was already clean; the parity-fixture import-order noqa was fine; mypy did not
flag `parity_cases`). What actually fired was a different, equally mechanical set:

### Fixes applied (all mechanical, no test skipped/deleted/weakened)

- `scripts/check.py:19` — TC003: moved `from pathlib import Path` out of the runtime import block
  into the existing `if TYPE_CHECKING:` block (all 9 uses of `Path` in the file are annotations
  only; `from __future__ import annotations` is already present).
  `if TYPE_CHECKING:\n    from collections.abc import Callable, Mapping, Sequence\n    from pathlib import Path`
- `scripts/check.py:414` — ISC004: wrapped the two-line implicit string concatenation in an
  explicit tuple-of-parens: `lines = [(\"Refusing to write a receipt: ...\" \"disagrees with its index.\")]`.
- `tests/parity_cases.py:12` — TC003: same pattern as check.py; `ModuleType` is annotation-only
  (3 call sites), moved under a new `if TYPE_CHECKING:\n    from types import ModuleType` block.
- `src/plantgeo_ml_service/app.py:81-82` — PLC0415: added scoped `# noqa: PLC0415 - ...` on both
  lazy imports (`boto3`, `botocore.config.Config`), matching the sibling's own convention
  (`grep -rn "noqa: PLC0415" services/agri-data-service/src` shows the same idiom repo-wide). This
  was prediction §6 item 5, resolved exactly as it recommended: a scoped noqa naming the reason,
  not moving the import.
- `src/plantgeo_ml_service/interface/cli.py:72-73` — same PLC0415 pattern for the lazy `create_app`/
  `get_settings` imports inside `serve()`.
- `src/plantgeo_ml_service/method/ml/analog_ensemble.py:65,122` — PLR0917 (too-many-positional-
  arguments, a distinct Pylint check from the existing `PLR0913` the functions already carried a
  noqa for): extended both noqa comments to `# noqa: PLR0913, PLR0917 - ...`, same rationale as the
  existing PLR0913 justification (one parameter per knob/input is the contract).
- `src/plantgeo_ml_service/method/ml/recommendation_models.py:900-919` — ISC004 x5: each multi-line
  implicit-string-concatenation tuple element (in `deviations=(...)` and `caveats=(...)`) wrapped in
  an explicit `(...)` grouping. No wording changed, only added parens.
- `scripts/check.py` and `tests/test_signal_forecast.py` / `tests/test_strategy_label_mapping.py`
  — I001 import-order: fixed automatically by `ruff check --fix` (safe autofix, no manual edit).

No behavioural test failures anywhere in this service; the "Expected to pass" predictions in
§6 held.

## STEP 2 — services/agri-data-service (fully green, read-only, no edits made)

```
uv sync --locked --extra dev
uv run --no-sync python scripts/check.py
```
`scripts/check.py` summary: format PASS (0.10s), lint PASS (0.08s), mypy PASS (2.16s), pytest PASS
(204.69s). Exit code 0.

Confirmed detail with a direct `pytest -q` run (also read-only): **4475 passed, 88 skipped,
1 xfailed**, 0 failed, in 182.05s. `AGRI_TEST_DATABASE_URL` unset on purpose — the 88 skips are all
`*_postgresql` tests, expected. The 1 xfail is
`tests/test_layer_import_contract.py::test_cli_is_a_thin_click_adapter`, confirmed strict and
correctly *not* XPASS-ing:
```
XFAIL tests/test_layer_import_contract.py::test_cli_is_a_thin_click_adapter - 1 known violations
await the wave-C2 extraction; the count is pinned by test_cli_adapter_violations_stay_pinned
```
This confirms p1b's recomputed `CLI_ADAPTER_VIOLATIONS` pin (`interface/cli/commands.py:93`, down
from old line 146) is correct — no line-number drift from formatting, contrary to predicted-failure
§8 item 3's "likely" risk.

No failures anywhere in agri-data-service. No fix was needed and, per the coordinator's
mid-sweep instruction, none would have been applied there regardless — any failure found would have
been reported verbatim, not touched.

## STEP 3 — batching note

All ML-service fixes above were applied in one batch, then the four gates were re-run exactly once
(ruff check, ruff format --check, mypy, pytest), matching the "one sweep" rule. Agri-data-service
required zero fixes, so its gates were run once via `scripts/check.py` and confirmed once more via a
direct `pytest -q` for exact counts (both read-only, no code changed between the two runs).

## STEP 4 — SKIPPED per coordinator instruction

No `git add` was run on either service directory. No `--write-receipt` was run on either service.
`QUALITY_RECEIPT.json` in `services/plantgeo-ml-service` remains in its original `pending` state
from p1a (see RUNBOOK.md "Immediately owed" item 1) — still owed, not done in this sweep by
explicit instruction. Nothing to restore: I never ran `git add` at any point in this session.

## git status --porcelain services/ | wc -l

```
129
```
(Snapshot at end of sweep; almost entirely the other concurrent session's already-staged
agri-data-service changes plus the untracked `services/plantgeo-ml-service/` tree — none of this
sweep's edits are staged.)

## Standing failures

None. Both service trees pass format, lint, mypy and pytest as of this sweep. The only outstanding
item is the deliberately-skipped receipt write (STEP 4), which is a scope decision from the
coordinator, not a failure.

## p1-fix-batch

Applied 2026-09-18 against the quality review's CHANGES-REQUIRED verdict. Only
`services/plantgeo-ml-service/**` was edited (plus this evidence file). `services/agri-data-service`,
`src/` and the rest of `conductor/` were not touched.

### Fixes

1. **MAJOR -- divergent `canonical_json` in the wind model.** Deleted the local `canonical_json`
   (`ensure_ascii` default `True`, `allow_nan=False`) and its `canonical_digest` wrapper, which only
   wrapped it. `src/plantgeo_ml_service/method/ml/covariate_wind_model.py:19` now imports
   `canonical_json` and `sha256_digest` from `plantgeo_ml_service.foundation.canonical`; the one
   call site, `feature_code_checksum` at
   `src/plantgeo_ml_service/method/ml/covariate_wind_model.py:203`, is now
   `sha256_digest(canonical_json(document))`. `validate_finite` was NOT added: the digested document
   is three string/list fields with no float, so `allow_nan=False` was protecting nothing here.
   `foundation/canonical.py` is unchanged -- its output stays parity-identical with the sibling's.
   - Non-ASCII coverage added at `tests/parity_cases.py:32` (a document with non-ASCII KEYS in
     `CANONICAL_JSON_CASES`, which also flows into `canonical_json_bytes` in the contracts fixture)
     and `tests/parity_cases.py:45` (the canonical rendering of a non-ASCII key as a digest case).
     Both fixtures regenerated FROM THE SIBLING via
     `uv run --no-sync python scripts/regenerate_parity_fixtures.py`:
     `tests/fixtures/parity/canonical.json` and `tests/fixtures/parity/contracts.json`.
   - Unit test `tests/test_covariate_wind_model.py:317`
     (`test_the_feature_checksum_is_the_foundation_digest_of_the_foundation_rendering`) asserts the
     wind model's digest of a document equals `sha256_digest(canonical_json(document))`, written
     against the foundation helpers directly rather than a stored constant, and asserts the rendering
     is not unicode-escaped.

2. **MAJOR -- ungoverned package root, undeclared driver dependencies, narrow refusal marker.**
   - Root pseudo-layer: `ROOT_FORBIDDEN_IMPORTS` at `tests/test_layer_import_contract.py:76`
     (`sqlalchemy`, `asyncpg`, `psycopg`, `psycopg2`, `alembic`, `httpx`), walker `_root_violations`
     at `tests/test_layer_import_contract.py:160`, assertion
     `test_the_package_root_is_governed_too` at `tests/test_layer_import_contract.py:174` (which also
     asserts `app.py`, `config.py` and `__init__.py` are actually present, so the rule cannot pass
     vacuously), and a fires-for-real proof `test_the_root_rule_actually_fires` at
     `tests/test_layer_import_contract.py:184`.
   - New `tests/test_dependencies.py`: `test_no_database_driver_is_a_declared_dependency`
     (`tests/test_dependencies.py:76`) parses `pyproject.toml` with `tomllib` and refuses
     `sqlalchemy`, `asyncpg`, `psycopg`, `psycopg2`, `psycopg2-binary`, `alembic`, `pgvector`,
     `geoalchemy2` in `[project].dependencies` and every `optional-dependencies` extra; plus a
     group-non-empty guard and a would-actually-catch-one proof.
   - Refusal marker widened: `_DATABASE_VARIABLE_MARKER` became `DATABASE_VARIABLE_MARKERS` at
     `src/plantgeo_ml_service/config.py:19` (`DATABASE_URL`, `DATABASE_DSN`, `POSTGRES_DSN`,
     `PGHOST`, `PGDATABASE`) with a shared `names_a_database_variable()` helper at
     `src/plantgeo_ml_service/config.py:33`, consumed by the validator at
     `src/plantgeo_ml_service/config.py:130`. `DATABASE_REFUSAL_MESSAGE` (the D5 message) is
     unchanged. `tests/test_dependencies.py:100` parametrises the new spellings (`PGHOSTADDR` and
     `AGRI_POSTGRES_DSN` included, as substring matches); `tests/test_config.py:71` parametrises over
     the marker tuple itself, so a name declared but not enforced fails. The autouse
     clear-the-developer-shell fixtures at `tests/test_config.py:25`, `tests/test_app.py:42` and
     `tests/test_dependencies.py:22` now use the shared helper, so a shell carrying `PGHOST` cannot
     fail the positive cases for an unrelated reason.

3. **MAJOR -- `method/ml/covariates_v2.py` had zero tests.** New `tests/test_covariates_v2.py`
   re-homes the three pure cases from the sibling's deleted `tests/test_covariates_v2_schema.py`
   (read via `git show HEAD:...`, read-only): as-of mode per schema version
   (`tests/test_covariates_v2.py:27`), the ingress refusal of an unsupported version (`:33`), and
   Hargreaves-Samani reference ET -- summer > winter > 0, inverted temperatures refused (`:42`).
   Plus a supported-version-returned-unchanged case (`:38`). No database; the psycopg2-driven cases
   in that file died with the Postgres lane under D5 and are deliberately not re-homed.

4. **MINOR -- colliding CLI exit code.** `src/plantgeo_ml_service/interface/cli.py:18`:
   `NOT_IMPLEMENTED_EXIT_CODE` moved from `2` to `3`, and the comment now names the collision it
   avoids (click's own usage errors and `PREFLIGHT_NOT_READY_EXIT_CODE`).
   `PREFLIGHT_NOT_READY_EXIT_CODE` stays at `2`, mirroring the sibling.

5. **MINOR -- untested `/ready` timeout branch.** `tests/test_app.py:94`
   (`test_a_bucket_that_never_answers_reads_as_a_timeout_not_as_unreachable`) monkeypatches
   `app.BUCKET_PROBE_TIMEOUT_SECONDS` to 0.05 s and `app.head_bucket` to a 1 s sleep, then asserts
   both `bucket_readiness_reason() == "object_store_timeout"` and that `/ready` answers 503 with
   `{"status": "not_ready", "reason": "object_store_timeout"}` -- the typed reason, not
   `object_store_unreachable`.

6. **MINOR -- wrong parent count in a comment.** `tests/parity_cases.py:17` now reads "three parents
   up", matching `parents[3]` (tests/ -> service -> services/ -> repo).

7. **MINOR -- em-dashes.** Six prose em-dashes replaced with ` -- ` in
   `src/plantgeo_ml_service/method/AGENTS.md` (lines 23, 29, 30, 65, 89, 90). No code touched; zero
   em-dashes remain in that file.

8. **MINOR -- constants that read as dead.**
   `src/plantgeo_ml_service/method/ml/covariate_wind_model.py:24-25` carries a two-line pointer above
   `SCHEMA_VERSION` / `TARGET_SIGNAL_NAME` / `DEFAULT_ORIGIN_COUNT` naming phase 2A's Parquet
   covariate reader as the binder.

9. **RUNBOOK.** `services/plantgeo-ml-service/RUNBOOK.md:30` "Immediately owed" item 1 is struck
   through and marked done, and restated as the standing obligation to refresh the receipt after any
   change under the service path.

### Gates -- one sweep at the end, as required

Run from `services/plantgeo-ml-service`. `ruff format src tests scripts` reformatted 1 file
(`tests/test_dependencies.py`); `ruff check` then reported one mechanical `I001` (import order in
`tests/test_covariate_wind_model.py`, introduced by fix 1's new import), fixed with `--fix` and
re-formatted. Final state:

```
uv run --no-sync ruff format --check src tests scripts   66 files already formatted     PASS
uv run --no-sync ruff check src tests scripts            All checks passed!             PASS
uv run --no-sync mypy src scripts                        no issues in 35 source files   PASS
uv run --no-sync pytest -q                               202 passed, 2 warnings, 13.45s PASS
```

The two warnings are pre-existing `RuntimeWarning: invalid value encountered in divide` from
`src/plantgeo_ml_service/method/ml/seasonal_evaluation.py:468` and `:475`, untouched by this batch.
Test count rose from the 176 recorded earlier in this file to 202 (+26 from the new
`test_covariates_v2.py`, `test_dependencies.py`, the two root-contract cases, the `/ready` timeout
case, the wind-model digest case, and the marker-parametrised config cases).

`scripts/check.py --write-receipt` re-ran all four gates itself and reported PASS on each
(format 0.06s, lint 0.05s, mypy 0.54s, pytest 9.93s).

### Receipt

**Written: yes.** Sequence exactly as instructed; never a `git add -A`, never any other path:

```
git add services/plantgeo-ml-service            # 81 paths, ONLY this service
uv run --no-sync python scripts/check.py --write-receipt
  -> Wrote QUALITY_RECEIPT.json:
     sha256:0e54cace829fdbfa1c09fd00ca21fc10fa8a3b9c39c1a0e031c7acce8528bd73 over 72 files
git add services/plantgeo-ml-service/QUALITY_RECEIPT.json
git add services/plantgeo-ml-service            # staged bytes == digested bytes
python scripts/verify_quality_receipt.py
  -> quality receipt verified: sha256:0e54cace...8528bd73 over 72 files,
     generated 2026-09-19T00:44:47.134239Z
```

**Verifier exit code: 0.** `git diff --name-only services/plantgeo-ml-service` is empty, so the
working tree and the index agree on every digested file. Nothing was committed. The
agri-data-service paths visible in `git diff --cached` were staged by the concurrent session before
this batch began and were not touched here.

### Standing failures

None. The service tree passes format, lint, mypy and pytest, and the image build's receipt gate now
accepts it -- it previously refused by design on the `pending` receipt.
