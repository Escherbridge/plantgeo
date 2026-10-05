---
type: module-notes
---

# `interface/http/` — thin Sanic adapter for Parquet operations

This directory owns only the Sanic blueprint and HTTP transport policy. All parsing, state
resolution, coverage, DuckDB sessions, admission control, object reads, typed refusals, and wire
rendering live in top-level `agri_data_service.parquet_ops`.

## Invariants

- `parquet_routes.py` is the sole HTTP adapter. No second HTTP-facing implementation or alias lives
  beside it.
- Every resolved four-state envelope leaves as HTTP 200. A refusal is serving/transport state, never
  warehouse content.
- Core `ServingRefusalError` carries only `code` and `message`; `_REFUSAL_HTTP_STATUS` owns the
  complete code-to-status mapping. A core module must never import an HTTP constant.
- Row operations use `run_serving_read`. Registered-lane coverage is metadata-only and runs
  in a worker outside the DuckDB pool; no adapter opens a DuckDB connection or owns a pool/semaphore.
- Coverage's async payload single-flight happens before its metadata worker, so concurrent cold
  callers share one bounded census and never acquire DuckDB slots.
- All public day/window reads use the registered live layout, including graduated temperature
  history. Frozen manifest readers remain explicit recovery APIs, not HTTP fallbacks.
- The private origin and route spelling remain frozen by `tests/contract/wire_contract.py` and the
  TypeScript client contract.
- Timeouts stay below the caller budgets so the adapter can return the typed reason.
- Handler annotations must resolve at runtime, because sanic-ext evaluates them while workers start.
  - Import `HTTPResponse` (and every other annotated type) at module level, with
    `# noqa: TC002 - sanic-ext evaluates handler annotations at runtime.`, never under
    `TYPE_CHECKING`.
  - Route tests call handlers directly and cannot catch this.
    `tests/interface/test_route_annotations_resolve.py` resolves every registered route's hints.
  - On 2026-09-28, soil-survey S3 (`f3253639`) broke this rule. Every parquet-api worker died at boot
    and the deploy failed its healthcheck.

Current MTBS reads inject the existing availability object-store adapter lazily into the common listing. The common resolver owns snapshot evidence and optional `mtbs_snapshot` wire metadata, including empty viewports; the HTTP adapter has no independent release authority.

All day, window, and release routes now enter that common resolver through
`parquet_ops.authorized_serving`. Time-bearing reads therefore use the freshly fetched availability
pointer and receipt-bound generation rather than an S3 history listing. Availability authority
faults are HTTP 503 transport/serving refusals and never HTTP 200 content states. Static lookups keep
their existing listing path, and no GET performs a receipt repair or any other object-store write.

## SSURGO native-geometry route (soil-survey port, S3)

`soil_survey.py` is its own dedicated blueprint (`/api/v1/soil-survey/{query,point,status}`), not a case
inside `parquet_routes.py`: it reads `foundation.soil_survey.release`'s sharded `Release` index
rather than the day-partitioned Parquet layout every other route in this directory serves, so it
carries its own admission pin (`config.py::ssurgo_admitted_release_sha256`) and its own gates.

**Gate order, cheapest and most storage-free first**; the first three run before object storage is
ever touched:

1. Request shape (`_parse`) -- malformed bbox/point/zoom is 400 `soil_survey_invalid_request`.
2. Region binding (`foundation.region.is_layer_bound(load_region(), "soil-survey")`) -- an unbound
   region answers 200 `unavailable`/`no_source_bound_in_region`.
3. The admission pin -- unset, 200 `unavailable`/`soil_survey_release_not_admitted` (dark by
   default: this is the state a fresh deploy of push P3 answers with, per plan Go-A3).
4. The zoom gate -- below z13 (`SoilSurveyViewport.at_native_rung`) a `/point` request is 200
   `unavailable`/`soil_survey_zoom_in` with no I/O. A `/query` request answers from the admitted
   release's overview (`pipeline/direct/soil_survey/AGENTS.md`, "Overview below z13") when one is
   published at `overview_key(<pin>)`; with none published, or on ANY read fault, it is the same
   200 `soil_survey_zoom_in` -- the overview is an enhancement, so its fault never becomes a 503 at
   zooms that answered 200 before it existed. `_overview_cache` holds one entry per process (the
   pin's release index and overview, both immutable per SHA) and re-checks a miss after 300 s, so a
   freshly published overview lights up without a restart. An UNUSABLE object (mis-stamped,
   truncated, oversized, undecodable: `_OVERVIEW_CONTENT_FAULTS`) is cached as a miss too, so a bad
   publish costs one download per 5 min rather than one per pan holding a `_GATHER_SLOT` the z13
   path also needs; transport faults (boto) are not cached and the next pan retries.

Only once all four pass does the z13 route open object storage, and even then the object-store reads
(release index, shard manifests, part bytes) run OFF the event loop (`asyncio.to_thread`) and
OUTSIDE `parquet_ops.duckdb_session.run_serving_read`'s bounded slot -- see `planes/AGENTS.md`,
"Admitted release read path" (F10). A read fault at any of those stages (`SoilSurveyError`, a
digest mismatch, a bounded-cap refusal, `ClientError`/`BotoCoreError`, a DuckDB error, or the read
timing out) is uniformly HTTP 503 `soil_survey_read_refused` -- transport/serving state, never a
claim about what the release holds, exactly like every other route's refusal in this file.

The metadata-only `/status` route accepts no query parameters. It follows the binding and
admission gates, then loads the exact same bounded, checksummed release index as a viewport
request, checks its region, and renders only static publication metadata. It uses the existing
gather semaphore and fourteen-second deadline without acquiring a DuckDB slot, walking shards,
or checking geometry. Every answer uses `Cache-Control: no-store`; pin removal, a missing or
corrupt object, and transport faults must not be concealed by a prior positive response.
No pin is a content state (`soil_survey_release_not_admitted`); failed index verification is
HTTP 503. Its only declared rung is 13; viewport `/query` keeps its separate low-zoom gate.

## Transitional botanical authoring lookup

`botanical_species_information.py` is a deliberately temporary nonspatial reader. It accepts only
the exact canonical UUID of an existing `agri.species` row and is mounted only by the read-capable
`combined_local` and `published_reader` profiles. It never joins by name, reads GIS observations,
or substitutes for the future immutable botanical profile Parquet product. Populated legacy fields
remain `unverified_authoring`, including Boolean defaults; `reviewed_authoring_database` names the
governed surface and does not approve each field. Missing fields are `unknown/not_reported`. Only companion rows whose
modeled review state is `approved` are returned, with their existing evidence and reviewer fields.
