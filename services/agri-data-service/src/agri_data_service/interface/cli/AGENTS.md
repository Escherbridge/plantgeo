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
