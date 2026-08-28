---
type: module-notes
---

# `tests/interface/` — proving the thin HTTP adapter

These tests call Sanic handlers directly with a minimal request object and patch `_run_row_read` to
use the fakes from `tests/parquet_ops/`. They verify blueprint mounting, request-to-core delegation,
HTTP status ownership, and error-envelope behavior. Core resolution, DuckDB, coverage, and wire
tests belong in `tests/parquet_ops/`.
