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

Current MTBS reads inject the existing availability object-store adapter lazily into the common listing. The common resolver owns snapshot evidence and optional `mtbs_snapshot` wire metadata, including empty viewports; the HTTP adapter has no independent release authority.

All day, window, and release routes now enter that common resolver through
`parquet_ops.authorized_serving`. Time-bearing reads therefore use the freshly fetched availability
pointer and receipt-bound generation rather than an S3 history listing. Availability authority
faults are HTTP 503 transport/serving refusals and never HTTP 200 content states. Static lookups keep
their existing listing path, and no GET performs a receipt repair or any other object-store write.

## Transitional botanical authoring lookup

`botanical_species_information.py` is a deliberately temporary nonspatial reader. It accepts only
the exact canonical UUID of an existing `agri.species` row and is mounted only by the read-capable
`combined_local` and `published_reader` profiles. It never joins by name, reads GIS observations,
or substitutes for the future immutable botanical profile Parquet product. Populated legacy fields
remain `unverified_authoring`, including Boolean defaults; `reviewed_authoring_database` names the
governed surface and does not approve each field. Missing fields are `unknown/not_reported`. Only companion rows whose
modeled review state is `approved` are returned, with their existing evidence and reviewer fields.
