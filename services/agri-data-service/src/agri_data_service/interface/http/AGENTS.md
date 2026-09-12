---
type: module-notes
---

# `interface/http/` — thin Sanic adapter for Parquet operations

This directory owns only the Sanic blueprint and HTTP transport policy. All parsing, state
resolution, coverage, DuckDB sessions, admission control, object reads, typed refusals, and wire
rendering live in top-level `agri_data_service.parquet_ops`.

## Invariants

- `parquet_routes.py` is the sole environmental Parquet HTTP adapter. No environmental alias lives
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

## Botanical static lookup

`botanical_species_profiles.py` is the separate nonspatial reference adapter authorized by the
botanical profile track. `GET /api/v1/botanical-species-profiles/lookup` accepts exactly
`authority`, `authority_version`, `taxon_id`, `release_id`, optional `assertion_limit` (1–100), and
the returned `cursor`. It rejects duplicate parameters, names, floating releases, date and zoom
parameters. Both read profiles mount it; the write ingress does not.

The plane owns validation, read admission, integrity verification, evidence paging and refusal
payloads. The adapter maps published or unknown taxon results to HTTP 200, malformed requests to
400, unpublished releases or oversized responses to 409, and storage, integrity, capacity or timeout
failures to 503. Responses use `Cache-Control: no-store`, keeping incomplete/refused outcomes from
being cached as published profiles. Tests may inject immutable local storage through
`app.ctx.botanical_profile_storage`; an unset injection uses the lazy configured object store.
