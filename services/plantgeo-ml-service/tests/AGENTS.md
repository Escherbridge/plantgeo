# tests

No test in this service touches a database, a network or a live bucket. That is not a convention
here, it is the service's premise: decision D5 makes `plantgeo-ml-service` zero-Postgres, and
`config.Settings` refuses to construct while any `*DATABASE_URL*` variable is in the environment.

## The lattice test is the load-bearing one

`test_layer_import_contract.py` enforces `foundation -> method -> warehouse -> pipeline -> planes ->
interface`, plus two rules the layer lattice cannot express because both sides share a layer:
`method/ml` and `method/monte_carlo` never import each other, and `method/kernels` may import
`foundation` only. `method` may not import `polars`, `pyarrow`, `duckdb`, `boto3` or `sanic` at all:
a storage client in an estimator is what would make the phase-3 Mojo port impossible and the unit
tests need a bucket. `test_layer_packages_actually_import` is the second half, because the AST walk
only parses files and a typo'd re-export parses fine.

## Parity fixtures, not parity imports

The service never imports `agri_data_service` (spec section 4): the two deploy independently. The
helpers it copied instead (`foundation/canonical.py`, `foundation/contracts.py`) are held honest by
`tests/fixtures/parity/*.json`, golden outputs over the fixed case set in `parity_cases.py`.

Each parity test asserts twice on purpose:

1. **This service reproduces the fixture.** Runs everywhere, including inside the Docker image where
   the sibling's source is absent.
2. **The sibling still reproduces the fixture.** Skipped when `services/agri-data-service/src` is not
   on disk; in a repository checkout this is what notices the sibling moving. A fixture that only
   ever compared against itself would pass forever while the two services diverged.

When assertion 2 fails, decide which side is correct and port the change, then regenerate with
`uv run --no-sync python scripts/regenerate_parity_fixtures.py`. Regenerating first turns a caught
drift into an accepted one.

## What was dropped on the way in

`tests/test_covariates_v2_schema.py` did not come across: it is a live-Postgres test
(`psycopg2`, `AGRI_TEST_DATABASE_URL`) whose seven registry cases query
`agri.covariate_feature_schema`. Its three pure cases were not re-homed either; the registry
expectations belong with the phase-2 Parquet export of that plane. Recorded in
`conductor/tracks/plantgeo_ml_service_20260918/evidence/phase1-predictions.md`.

## The phase-2A Parquet fixtures, and the one asymmetry in them

`parity_parquet_cases.py` holds the fixed case set and the evaluators;
`parity_parquet_adapters.py` holds both sides of each. Five subjects cross the boundary:
`parquet_paths`, `parquet_markers`, `streams`, `lanes` and `availability`.
`test_parquet_parity.py` runs the same two assertions per subject that the phase-1 tests run, and
adds one that proves the harness itself can fail.

**Why an ADAPTER and not a module.** The two services spell the same knowledge across different
module boundaries: agri-data-service splits the object-key grammar over `foundation/parquet/paths.py`,
`foundation/parquet/zoom.py` and `pipeline/parquet/objectstore.py`, while this service keeps all of
it in `foundation/parquet_paths.py`. The adapter absorbs that, so one fixed case set judges both
layouts and no shipped function is widened to make a test convenient. Every signature shim lives in
the adapter file and nowhere else.

**Why the sibling is IMPORTED here and LOADED BY PATH in phase 1.** `foundation/canonical.py` and
`execution/contracts.py` import nothing, so a file load works. These modules import their own
package, so the sibling's `src` goes on `sys.path` and the real dotted name is imported.
`sibling_module()` does that once.

**The one thing that does not cross: the generation metadata VALUES.** The sibling's formatter is
`availability_index._metadata`, and that module imports SQLAlchemy through
`pipeline/parquet/publication_barrier.py`. A zero-Postgres service does not take an ORM into its
lockfile to read one pure function. What crosses instead is the public
`AVAILABILITY_METADATA_KEYS` set, inside `availability.json` -- so a key added, removed or renamed on
either side still fails this sweep. The VALUE rendering is pinned by `availability_metadata.json`,
which this service alone produces, and a second test asserts its key set equals the parity-checked
one so the two fixtures cannot drift apart. Read against the sibling's `_metadata` at authoring time
(2026-09-19); if that function's rendering changes, only a reviewer reading both will catch it.

**The one case that compares BYTES, not renderings.** `evaluate_streams` conforms a fixed,
deliberately out-of-grain-order `fire-detections` table through each side's own
`conform_to_stream_schema` and serializer and pins the sha256 of the result in `streams.json`. Every
other stream case compares a schema RENDERING, which a dropped sort passes. This one does not: it is
what proves the two services write comparable files. It reaches the sibling's private
`_serialize_parquet` on purpose, because the bytes that function writes ARE the contract and
anything more public would compare something else. A pyarrow upgrade that changes the emitted bytes
will fail it, and that is the correct signal: our files changed.
