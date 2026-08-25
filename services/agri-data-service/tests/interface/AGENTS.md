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
2. `coverage.json` is reproduced by the BUILDER
   (`test_the_census_builder_reproduces_the_frozen_payload_from_a_warehouse_seeded_to_its_facts`):
   a `FakeListing` is seeded with exactly the facts the golden states — each lane's span, its
   absences, its gaps, written at that lane's registered cadence — and `build_coverage(...).to_wire()`
   is asserted equal to the file. The renderer test beside it stays, but it proves field names and
   `from`/`to` aliasing ONLY: it constructs `LaneCoverage` objects from the golden's own values, so
   it cannot fail on anything the builder decides. That gap was real — measured 2026-08-25, the
   builder disagreed with the golden on THREE of five lanes, all the same defect: the day after
   `latest_day` at the live edge was accounted for by no range at all. The golden was wrong and the
   builder was right; `coverage.json`'s gap ranges moved, its shape did not.
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
- `test_a_mixed_key_set_is_refused_rather_than_dropping_the_days_that_lack_the_columns` — the same
  lane MID re-export, which is the state the warehouse is actually in. It drives a mixed key set
  through the real `DuckDbRowReader` and `serving.resolve_window`, and asserts in passing that the
  union probe it replaced still sees the column and passes — the defect, stated in the test.
  Its control beside it proves a uniform key set is still served.
- `test_a_clip_that_collapses_to_a_lower_dimension_is_dropped_rather_than_served` — an edge-touching
  polygon clipped to a LINESTRING and was served under a schema promising a Polygon.
- `test_an_over_budget_read_is_a_coded_refusal_the_client_will_not_retry` and its siblings — every
  fault that used to reach Sanic as a generic 500, which `upstream-fault.ts` reads as transient and
  retries against a process already at its ceiling.
- `POSITIONED_SIGNAL_ROW` casts to `DOUBLE` on purpose. A bare `-116.2` is a DECIMAL to DuckDB, no
  registered schema carries one, and `wire.render_scalar` now fails closed on it — so an uncast
  fixture tests a shape the warehouse cannot produce.
- `test_no_day_bearing_field_of_any_answer_ever_carries_a_timezone` — walks every `*_day` in every
  route's body. A `T` or a `Z` in one is how 6,279 of 16,743 water-gauge rows once moved a day.
- `test_the_serving_session_caps_memory_threads_and_disables_spilling` — asserts the guard by
  reading `duckdb_settings()` back, so a future edit that "tunes" `max_temp_directory_size` off zero
  fails here rather than on the host.
