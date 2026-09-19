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
