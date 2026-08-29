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
- Row operations use `run_serving_read`. Merged mutable/snapshot coverage is metadata-only and runs
  in a worker outside the DuckDB pool; no adapter opens a DuckDB connection or owns a pool/semaphore.
- Snapshot day/window routes resolve and single-flight manifest/checkpoint evidence in a worker
  before `run_serving_read`; only the evidence-bound row query may occupy a DuckDB slot. Cold
  waiters therefore cannot starve unrelated mutable reads.
- Coverage's async payload single-flight happens before its metadata worker, so concurrent cold
  callers share one bounded census and never acquire DuckDB slots. Product-local snapshot refusals are
  logged and omitted from the frozen coverage wire while healthy products remain available.
- Immutable daily-series products reuse `/day` with exact `day` semantics. They never carry the
  latest value from an earlier day; monthly files are filtered to the requested `observed_day`.
- The private origin and route spelling remain frozen by `tests/contract/wire_contract.py` and the
  TypeScript client contract.
- Timeouts stay below the caller budgets so the adapter can return the typed reason.
