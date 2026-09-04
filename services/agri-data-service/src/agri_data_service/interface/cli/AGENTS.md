# `interface/cli`

## Responsibility

Thin Click adapters for the `agri-service` console script. The root exposes exactly four verb groups:
`forecast`, `ml`, `data`, and `ops`.

## Dependency rules

- Command bodies delegate to the retained execution, ingest, pipeline, method, warehouse, and shared
  `parquet_ops` modules; this package does not own business rules.
- Parquet reads acquire bounded core admission through `parquet_ops`; no CLI adapter opens DuckDB directly.
- A leaf command is registered once beneath one family. The removed flat command surface is not an alias source.

## Hard-cutover invariant

- The only console script is `agri-service = "agri_data_service.interface.cli:cli"`.
- Do not restore the retired binary name, the retired top-level CLI module, flat leaf aliases, or compatibility forwarding.
- Every deployment command moves in the same change as a leaf path; a stale Railway dashboard override is a
  deployment mismatch, not permission to retain an alias.

## Availability operator inputs

`data availability-bootstrap` and `data availability-publish` validate an externally SHA-pinned
local JSON document offline by default. Only explicit `--apply` constructs a bucket client, verifies
every referenced object digest and attempts conditional publication. The commands do not discover
history, activate schedulers or grant production authorization; bootstrap inputs must already name
the exact verified manifest/checkpoint receipts.

## Executor operator inputs

`ops jobs-supersede-run` records an operator's evidence that the failed or partial checkpoint run holding
an executor lane may be superseded, so the scheduler resumes the lane at the current bucket. It is a dry
run without `--apply`, writes one resolved `agri.job_incident` row and nothing else, refuses a lane the
clock will release by itself (a `coalesce_latest` lane below its three-failure breaker), and prints one
JSON receipt that names the ledger it wrote. The body lives in `execution/job_run_supersession.py`; see
`execution/AGENTS.md`, "Failed checkpoints are superseded by the clock or by an operator".
