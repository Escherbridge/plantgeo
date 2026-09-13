---
type: track-evidence
track: botanical_occurrence_parquet_lane_20260911
status: implemented-pending-review
---

# Implementation notes, 2026-09-12

Author slice. No tests, ruff or lint were run here by design (owner rule: authors never verify their
own work). The predicted failures at the bottom are predictions, not results.

## What exists

| Area | Files |
| --- | --- |
| Schemas (7 streams) | `warehouse/schemas/botanical_occurrences.py` |
| Foundation | `foundation/botanical_occurrences/{__init__,limits,release_identity,terms,event_interval,coordinates}.py` + `AGENTS.md` |
| Lane | `pipeline/direct/botanical_occurrences/{__init__,__main__,forward,quarantine,archive_descriptor,fetch,rows,normalize,support,publish,identity}.py` + `AGENTS.md` |
| Serving | `planes/botanical_occurrences.py`, `interface/http/botanical_occurrences.py` |
| Agent | `agent/botanical_occurrences.py` (3 tools, not in `WAREHOUSE_TOOLS`) |
| Tests | `tests/direct/botanical_occurrences/*` (conftest + 6 modules), `tests/planes/test_botanical_occurrences.py`, `tests/agent/test_botanical_occurrences.py` (+ new `tests/agent/__init__.py`), `tests/interface/test_botanical_occurrence_routes.py` |

## Existing machinery built on (not re-implemented)

- `warehouse/parquet/schema.py::ParquetStreamSchema` / `register_stream_schema` — all seven streams.
- `pipeline/parquet/objectstore.py::conform_to_stream_schema` — every artifact is conformed and
  sorted to its declared grain before it is written.
- `pipeline/parquet/objectstore.py::polars_storage_options` and `BotoObjectStoreBackend` /
  `ObjectStoreBackend` — the bucket target reuses the same backend protocol and credential path
  `ObjectStore` uses; `config.settings.require_object_store()` is the only credential source.
- `planes/drought.py`'s reader shape — `s3://` root + `storage_options`, fail-closed `*ServingError`,
  synchronous by the same argument.
- `pipeline/direct/__init__.py` — `DirectWriterContract`, the outcome vocabulary (`published`,
  `idempotent_noop`, `no_window`, `time_budget_exhausted`, and `blocked` borrowed from
  `LANE_DAY_OUTCOMES` rather than minting a lane-private word), and the bbox-policy axis.

Not reused, with reasons in `pipeline/direct/botanical_occurrences/AGENTS.md`:
`ObjectStore.write_partition` (day layout; this lane's unit is a release set) and
`warehouse/parquet/tiers.py::TierDerivation` (the supports are an analytic lattice recomputed per
rung from exact sets, not simplified geometry rolled up from a base rung — summing a finer rung would
double-count richness).

## Deferred

1. **Shared registration** — `lane_registry.py`, `app.py`, `agent/tools.py`,
   `job_executor_service.py`, `interface/http/__init__.py` and the writer-contract table entry are
   all in `evidence/shared-registration.patch`, unapplied.
2. **Three executor duties** — specified in the patch, deliberately not registered (spec: only after
   implementation review).
3. **Outside-coverage cells** are not materialised and not synthesised by the reader.
4. **`historical_publication_unsupported` / `release_unknown`** are declared but unreachable; see
   the serving contract's deviations section.
5. **Pinned external taxonomy authority** — `source-names-v0` binds none; a pinned authority is a
   later recipe version.
6. **`DECLARED_ENVELOPE`** is the track's scoping placeholder (lon −125..−110, lat 41..50), not a
   measured coverage claim, and is flagged configurable in code and in `AGENTS.md`.
7. **No archive has been fetched.** `fetch.py` has no caller, and the admission decision remains
   `blocked` with both archive slots unused.

## Predicted test failures

These are the ones I would expect a sweep to surface, with what I think each is:

1. `tests/direct/test_direct_package_registration.py::test_every_direct_package_is_registered_without_a_postgres_fallback`
   — WILL FAIL. The new package directory exists and `LANE_REGISTRATIONS` names no adapter mentioning
   `pipeline.direct.botanical_occurrences`. Fixed by hunk 1 of the patch. This is the registration
   reminder working, not a defect.
2. `tests/direct/test_direct_writer_contract.py::test_every_direct_writer_is_in_this_table`
   — WILL FAIL for the same reason; fixed by hunk 2.
3. `tests/direct/test_direct_writer_contract.py::test_fire_detections_is_still_the_lone_identity_refuser_and_says_so`
   — LIKELY FAILS once hunk 2 lands: it asserts the `skip_and_count` set is exactly
   `{fire_perimeters, sensors, watersheds}` and this writer joins it. That is a real change to a
   declared set and belongs to the owner of `pipeline/direct/__init__.py`, not to this slice.
4. `tests/test_layer_import_contract.py` — POSSIBLE. It counts lanes under `pipeline/direct/` and
   polices cross-lane imports. This package imports only `pipeline/direct/__init__.py` and
   `foundation`, `warehouse`, `pipeline/parquet`, so I expect it to pass, but the lane-name set it
   builds may be asserted against a fixed list I did not read.
5. `tests/direct/botanical_occurrences/test_quarantine.py::test_each_hostile_archive_is_rejected_by_its_own_control[encrypted_member_archive]`
   — MOST LIKELY OF MY OWN TESTS TO FAIL. The fixture rewrites the central directory to set the
   encryption flag bit; `zipfile` may normalise `flag_bits` on `writestr`, in which case the bit is
   not observable and the archive is accepted. The control itself is a one-line check; the FIXTURE is
   the fragile part.
6. `...[ratio_bomb_archive]` — may fail if 8 MiB of zeros compresses to a ratio under the 200 guard
   in this zlib build (it should be far above), or if the member trips
   `member_unsupported_suffix`/CRC first and the asserted reason is not the one that fires first.
   Both reasons are collected, so the assertion checks membership rather than equality.
7. `tests/planes/test_botanical_occurrences.py` — the Polars `scan_parquet` over an empty artifact
   (`identifications` or `nonspatial` with zero rows) may raise rather than return an empty frame on
   this Polars version. If so the fix is a zero-row guard in `_read_detail`, not a schema change.
8. `tests/agent/test_botanical_occurrences.py` — `anthropic.beta_async_tool` may require a running
   tool context the species test provides via `agent_tools.run_context`; these tools have no such
   context, so a direct `await` may need the same wrapper.
9. `tests/planes/`, `tests/agent/` import `tests.direct.botanical_occurrences.conftest` — if pytest's
   configured import mode does not put the repository root on `sys.path` for those directories, the
   import fails and the helper needs moving to a shared fixture module.
10. `pa.Table.from_pylist(..., schema=...)` with a `pa.date32()` column fed `datetime.date` objects
    and a `pa.binary()` column fed `bytes` should both cast cleanly; if `from_pylist` rejects a
    missing key on a non-nullable column, `publish.py::_parquet_bytes` will raise for the empty
    artifacts. Every row dict is built with all keys present, so I expect this to hold.
11. `ruff` — not run. Likely findings: line length in a few long comment blocks, `PLR0913` on
    functions I annotated with `noqa` and may have missed, and the `S101` assert in `forward.py`.
