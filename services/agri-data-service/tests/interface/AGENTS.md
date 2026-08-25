---
type: module-notes
---

# `tests/interface/` — proving the `/api/v1/parquet` plane against the freeze

Covers `src/agri_data_service/interface/http/`. Nothing here reaches a bucket, a database or a
network: the two ports in `warehouse_reader.py` are implemented over dictionaries in `fakes.py`, and
the DuckDB tests point a real guarded session at a `tmp_path` directory instead of the store.

## Where each expectation comes from

**Never from a hand-written shape.** `tests/contract/fixtures/*.json` are the golden payloads, and
`src/__tests__/services/parquet-plane-client.test.ts` reads the same bytes through the real zod
schemas. Three levels, weakest last:

1. `test_parquet_envelopes.py` resolves each state through the REAL resolver against a fake
   warehouse built to match a fixture, and asserts `to_wire() == fixture`. Seven of the nine goldens
   are reproduced end to end this way, plus `window.json`.
2. `coverage.json` is reproduced by the RENDERER only (`test_coverage_census.py`), because it is an
   illustrative census rather than a self-consistent one — its `signal` lane leaves 2026-08-07
   unaccounted for by either range, which no principled census can emit. The census BUILDER is
   proven against `WireCoverage`, the pydantic table, which forbids unknown fields.
3. `test_wire_agreement.py` compares the serving side's own spelling of every route and parameter
   against `wire_contract.py`. The contract suite already compares that table to the TypeScript
   `WIRE` block, so the chain runs client -> contract -> server with no hopeful copy in it.

## Why the routes are called directly

`sanic-testing` is not a dependency and this slice does not add one. `test_ops_routes.py` set the
precedent: hand the handler a `SimpleNamespace(args=...)`, which is the whole of `Request` these
routes touch. Blueprint wiring is proven separately by inspecting `app.router.routes`, the way
`test_service_profiles.py` does — so a route that moved off `/api/v1/parquet/<segment>` still fails.

## The tests that exist because of a specific failure

- `test_hive_columns_never_reach_a_served_row` — DuckDB appends `layer`, `kind`, `zoom`, `year`,
  `month` and `day` from the path unless `hive_partitioning=false`. Measured on this bucket
  2026-08-25: a plain `read_parquet` of a signal part returns sixteen columns, not ten.
- `test_a_viewport_is_refused_when_the_written_objects_lack_the_position_columns` — the `signal`
  base rung declared `cell_longitude`/`cell_latitude` in its registered schema and the written
  objects did not carry them. Serving the whole world to that viewport is both a lie and the read
  that consumed the host.
- `test_no_day_bearing_field_of_any_answer_ever_carries_a_timezone` — walks every `*_day` in every
  route's body. A `T` or a `Z` in one is how 6,279 of 16,743 water-gauge rows once moved a day.
- `test_the_serving_session_caps_memory_threads_and_disables_spilling` — asserts the guard by
  reading `duckdb_settings()` back, so a future edit that "tunes" `max_temp_directory_size` off zero
  fails here rather than on the host.
