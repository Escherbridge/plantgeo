---
type: evidence
track: plantgeo_ml_service_20260918
slice: p2a-parquet-io
recorded_on: 2026-09-19
---

# Phase 2A predictions: Parquet in, Parquet out

Authored by the `p2a-parquet-io` slice. The author did not run `pytest`, `ruff` or `mypy` (owner
rule 2026-08-25): this file predicts what a sweep will find so the monitor can tell an expected
finding from a surprise. What the author DID run: `py_compile` over every file, a real import of
every shipped module and every test module, and
`uv run --no-sync python scripts/regenerate_parity_fixtures.py`.

## Files created

Under `services/plantgeo-ml-service/src/plantgeo_ml_service/`:

| file | layer | what it owns |
|---|---|---|
| `foundation/parquet_paths.py` | L0 | the object-key grammar, the zoom ladder, the three parsers, the day classifier, the availability roots |
| `foundation/parquet_markers.py` | L0 | the completion and governed-absence payload bodies |
| `warehouse/streams.py` | L2 | six copied observed schemas, `forecast_schema_for`, the new `fire-risk` schema |
| `warehouse/lanes.py` | L2 | the copied lane clocks and `settled_through` |
| `warehouse/availability.py` | L2 | the generation schema, metadata keys, terminal row and pointer documents |
| `pipeline/object_store.py` | L3 | the backend protocol, boto3 and in-memory backends, the receipted writer and reader |
| `pipeline/duckdb_session.py` | L3 | the bounded spill-free session and `read_parquet_keys` |
| `pipeline/observed_reader.py` | L3 | `read_lane_window` and its five named refusals |
| `pipeline/availability_publisher.py` | L3 | `build_generation`, `pointer_for`, `publish_generation` and the compare-and-set |
| `pipeline/expert_labels.py` | L3 | the pinned label schema and its reader |

Tests: `tests/parity_parquet_cases.py`, `tests/parity_parquet_adapters.py`,
`tests/test_parquet_parity.py`, `tests/test_parquet_paths.py`, `tests/test_streams.py`,
`tests/test_object_store.py`, `tests/test_duckdb_session.py`, `tests/test_observed_reader.py`,
`tests/test_availability_publisher.py`, `tests/test_expert_labels.py`.

Edited: `src/plantgeo_ml_service/config.py` (three `duckdb_*` settings and two validators),
`scripts/regenerate_parity_fixtures.py`, `AGENTS.md`, `RUNBOOK.md`, `src/.../foundation/AGENTS.md`,
`src/.../warehouse/AGENTS.md`, `src/.../pipeline/AGENTS.md`, `tests/AGENTS.md`.

Nothing outside `services/plantgeo-ml-service/` was touched except this evidence file.

## Fixtures regenerated

`tests/fixtures/parity/` now holds eight files. Five are new and were generated FROM
agri-data-service's own modules, then verified to be reproduced byte for byte by this service:

- `parquet_paths.json` -- 3 layers x 2 kinds x 4 rungs x 3 days x 3 part indices for
  `partition_path`, the same grid for four prefix and marker builders, `promotion_receipt_path`,
  `availability_lane_root`, nine slug cases, ten serving-zoom cases, thirteen parse cases across all
  three parsers, and a parse-then-rebuild round trip over the same thirteen.
- `parquet_markers.json` -- the exact serialized bodies of a version-1 completion marker, a
  version-2 one carrying per-part digests, a derived-empty marker and a governed absence.
- `streams.json` -- every field name, Arrow type and nullability of all six observed schemas and
  their `forecast_schema_for` results, plus each stream's codec, grain and base-non-null columns.
- `lanes.json` -- the six copied lanes' floor, lag, cadence, nature and forecaster stem.
- `availability.json` -- the index schema, the required rungs, the pointer and generation keys, four
  terminal rows' wire form, the generation receipt digest, the metadata KEY set, and the pointer's
  wire form.

`availability_metadata.json` is the one fixture with no sibling side, and this is the slice's one
deliberate weakening of FR-3: the sibling's value formatter is
`pipeline/parquet/availability_index._metadata`, and importing that module drags SQLAlchemy in
through `publication_barrier.py`. A zero-Postgres service does not add an ORM to its lockfile to
read one pure function. The metadata KEY set still crosses (it is public, in
`warehouse/schemas/availability_index.py`), so an added, removed or renamed key fails the sweep; only
the VALUE rendering is pinned by this service alone, read against the sibling's `_metadata` at
authoring time. Recorded in `tests/AGENTS.md`. A reviewer should confirm this is an acceptable
trade or ask for the alternative (a `dev` extra carrying `sqlalchemy` purely for parity).

## Predicted sweep findings

### Likely to fail, with the reason

1. **`ruff check` line length in `tests/parity_parquet_cases.py`.** Two dict-comprehension lines in
   `evaluate_parquet_paths` (`validate_layer_slug` and `serving_zoom_tier`) run past 120 characters.
   Mechanical; reformat, do not restructure.
2. **`ruff` `B023` / loop-variable binding** on the `lambda case=case:` default-argument idiom used
   throughout the evaluators. Phase 1's `parity_cases.py` uses the same idiom and passes, so this
   may already be configured away; if it fires, the default-argument binding is the fix already
   applied and the rule needs a scoped `noqa`.
3. **`mypy --strict` on `pl.from_arrow` in `observed_reader.py`.** It is typed as returning
   `DataFrame | Series`. Two `type: ignore[return-value]` comments are already in place; if the
   installed polars stubs disagree about the code, the ignore code will need changing, not removing.
4. **`mypy --strict` on the parity adapters.** `PathsAdapter` and friends type their members as
   `Callable[..., str]` and `Any`, which is what lets one dataclass hold two services' differently
   shaped functions. Expect `no-any-return` or `type-arg` complaints in `tests/`; note that
   `mypy` runs over `src scripts` only (`python.md`, Baseline), so `tests/` may not be checked at
   all -- but `scripts/regenerate_parity_fixtures.py` IS, and it imports those adapters. If mypy
   follows the import into `tests/`, expect findings there.
5. **`ruff` `SLF001`** on `documents._generation_receipt_sha256` in `parity_parquet_adapters.py`. A
   scoped `noqa` with the reason is already on that line; confirm the rule code is right.
6. **`test_duckdb_session.py::test_a_guarded_connection_refuses_to_open_without_its_extensions` and
   `test_a_missing_extension_is_a_named_refusal_not_a_silent_download`** assume DuckDB will not
   auto-install `httpfs` from an empty extension directory. `autoinstall_known_extensions=false` is
   set first, so this should hold; if the CI image has httpfs pre-installed into the DEFAULT home
   and `extension_directory` does not override the lookup, these two will pass trivially instead of
   proving the refusal. Treat a PASS here as weaker evidence than it looks.

### Expected to pass, and why they are worth watching anyway

7. **`test_layer_import_contract.py`.** Every new module respects the lattice:
   `foundation/*` is stdlib only (no numpy, polars, pyarrow, duckdb, boto3, sanic);
   `warehouse/*` imports only `foundation` and pyarrow; `pipeline/*` imports `foundation`,
   `method`, `warehouse`, and never `planes` or `interface`. `boto3` is imported lazily INSIDE
   `BotoObjectStoreBackend.from_credentials`, which the AST walker still sees as a `pipeline`
   import -- that is allowed. `test_layer_packages_actually_import` will import all ten new modules
   for real; that was smoke-tested and passes.
8. **`test_config.py`.** Three settings were added to `Settings`. If that test enumerates the field
   set rather than sampling it, it will fail on the three new names. Additive and expected.
9. **The QUALITY_RECEIPT digest is now stale** for the whole service tree. Re-run
   `scripts/check.py --write-receipt` on a green, staged tree; never hand-edit it. A CRLF tree
   writes a different digest.

### Not a failure, but a reviewer question

10. **`pipeline/expert_labels.py` defines its own `ExpertLabel` frozen dataclass.** The brief said to
    read into "the `ExpertLabel` dataclasses `method/ml/expert_label_plane.py` already defines".
    That module defines no such class: its label model is the pydantic `HarvestLabel` (the
    pre-persistence harvest shape, with a nested `source` and `citation_check`), while the EXPORTED
    plane is the flattened `agri.expert_label` row. The new dataclass mirrors that table one column
    for one, reuses `OUTCOMES_BY_KIND` and `CONFIDENCE_WEIGHTS` from the method module for its
    validation, and does not duplicate any checksum logic. Flag if the intent was otherwise.
11. **`warehouse/availability.py` and `warehouse/lanes.py` were not named in the brief.** The brief
    put the availability documents and the `PUBLICATION_LAG_DAYS` table inside their pipeline
    consumers. They were split out because the documents are L2 knowledge (an Arrow schema and its
    wire forms) that `warehouse` may own and `pipeline` may not re-export, and because the lane
    clock is shared by the reader today and by the 2B writers tomorrow. Both keep their consumers
    under the ~600-line soft ceiling.
12. **`foundation/parquet_markers.py` was not named either.** The marker BODIES are stdlib JSON and
    belong beside the marker KEYS; keeping them in `object_store.py` would have pushed that file
    past the ceiling and made the payload untestable without a bucket abstraction.
13. **`classify_partition_day` / `tier_day_objects` were added to `parquet_paths.py`** beyond the
    brief's list. `observed_reader.py` needs a day status, the sibling defines it in the same module
    as the grammar, and defining it anywhere else would have created a second definition a census
    and a reader could disagree about.

## What was NOT built, deliberately

- The agri-side `export-expert-labels` verb (out of this slice's ownership; another session is
  pushing to `services/agri-data-service`).
- The `fire-risk` `LaneRegistration`, `docs/lanes/fire-risk.md` and the sibling's
  `warehouse/schemas/fire_risk.py` (`p2d`, sequenced after the other session's `p1d-registration`).
- Any feature builder, estimator, daily lane, artifact writer, serving reader or route (2B/2C).
- Any real bucket write. Nothing in this slice has touched the production prefix.
- Any Dockerfile change, including the `install_extensions` build step the DuckDB session needs.
