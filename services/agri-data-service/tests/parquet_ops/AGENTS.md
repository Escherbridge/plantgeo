---
type: module-notes
---

# `tests/parquet_ops/` — proving the reusable Parquet core against the freeze

Covers `src/agri_data_service/parquet_ops/`. Nothing here reaches a bucket, a database or a
network: the two ports in `warehouse_reader.py` are implemented over dictionaries in `fakes.py`, and
the DuckDB tests point a real guarded session at a `tmp_path` directory instead of the store.

## Where each expectation comes from

**Never from a hand-written shape.** `tests/contract/fixtures/*.json` are the golden payloads, and
`src/__tests__/services/parquet-plane-client.test.ts` reads the same bytes through the real zod
schemas. Three levels, weakest last:

1. `test_parquet_envelopes.py` resolves each state through the REAL resolver against a fake
   warehouse built to match a fixture, and asserts `to_wire() == fixture`. Seven of the nine goldens
   are reproduced end to end this way, plus `window.json`.
2. `coverage.json` is reproduced by the BUILDER at each physical zoom rung
   (`test_the_census_builder_reproduces_the_frozen_payload_from_a_warehouse_seeded_to_its_facts`):
   a `FakeListing` is seeded with exactly the facts the golden states — each lane's span, its
   absences, its gaps, written at that lane's registered cadence — and `build_coverage(...).to_wire()`
   is asserted equal to the file. The renderer test beside it stays, but it proves field names and
   `from`/`to` aliasing ONLY: it constructs `LaneCoverage` objects from the golden's own values, so
   it cannot fail on anything the builder decides. That gap was real — measured 2026-08-25, the
   builder disagreed with the golden on THREE of five lanes, all the same defect: the day after
   `latest_day` at the live edge was accounted for by no range at all. The golden was wrong and the
   builder was right; `coverage.json`'s gap ranges moved, its shape did not.
3. `test_wire_agreement.py` compares the serving side's own spelling of every route, parameter,
   state, withholding reason and coverage schema version against `wire_contract.py`. The contract
   suite already compares that table to the TypeScript `WIRE` block, so the chain runs client ->
   contract -> server with no hopeful copy in it. The vocabulary assertions live HERE and nowhere
   else: a second copy in a behaviour test is a copy that can be edited without the freeze noticing.

`test_availability_coverage.py` holds the no-LIST tripwire. Its `ExplodingListing` raises on every
`WarehouseListing` method, so a coverage regression that reaches for an object prefix fails there
rather than on a production bill.

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
- `test_work_never_starts_when_no_admission_slot_is_available` and its cancellation companion —
  admission belongs to the core, and a timed-out caller cannot release the slot before its worker.
- `POSITIONED_SIGNAL_ROW` casts to `DOUBLE` on purpose. A bare `-116.2` is a DECIMAL to DuckDB, no
  registered schema carries one, and `wire.render_scalar` now fails closed on it — so an uncast
  fixture tests a shape the warehouse cannot produce.
- `test_no_day_bearing_field_of_any_answer_ever_carries_a_timezone` — walks every `*_day` in every
  route's body. A `T` or a `Z` in one is how 6,279 of 16,743 water-gauge rows once moved a day.
- `test_the_serving_session_caps_memory_threads_and_disables_spilling` — asserts the guard by
  reading `duckdb_settings()` back, so a future edit that "tunes" `max_temp_directory_size` off zero
  fails here rather than on the host.
- `test_a_cold_census_lists_each_registered_lane_tier_once` — proves the cold walk still covers the
  frozen thirteen-by-four ladder exactly once; the timeout fix changes scheduling, not scope.
- `test_a_cold_census_bounds_parallel_r2_listings_without_a_clock` — the first three lane listings
  rendezvous at a barrier, proving the cold path is concurrent and capped at three without asserting
  wall-clock timing.
