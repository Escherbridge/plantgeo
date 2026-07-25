---
type: code-styleguide
---

# PlantGeo Python and data-service standard

The required standard for the agri data/forecasting service
(`services/agri-data-service/**/*.py`) and any other PlantGeo Python. It is
specific to the governed warehouse: Alembic-owned PostGIS/TimescaleDB schema,
immutable forecast receipts, evaluation-only iteration plane, least-privilege
DSNs, and Sanic/tRPC-adjacent services. It supplements `ruff` and `mypy --strict`;
it does not replace them. It inherits `engineering-principles.md`.

## Baseline

- `mypy` runs in `strict` mode (with the SQLAlchemy plugin). `Any`, bare
  `type: ignore`, and broad `dict[str, Any]` payloads are forbidden. Receive
  untyped values as `object`/`Unknown`, validate, then expose a named type.
- Model domain data with `pydantic` (settings, request/response, external
  payloads) or frozen `dataclass`es; prefer immutability (`frozen=True`,
  `Final`, `tuple` over `list` for fixed collections). Use `enum`/`Literal` for
  closed sets.
- Naming: `snake_case` functions/vars, `PascalCase` classes, `UPPER_SNAKE_CASE`
  for true process constants. Module files `snake_case.py`. Don't rename
  unrelated files to apply a rule.
- Terse one-line doc-comments only. Module rationale, contracts, and operational
  notes belong in the directory's `AGENTS.md` (e.g. `db/AGENTS.md`,
  `alembic/AGENTS.md`, `models/AGENTS.md`), not long docstrings.
- Async by default for I/O; never block the event loop with sync DB/HTTP calls.
  Use `asyncio.timeout`/explicit timeouts on every await that can hang.

## Boundaries, custody, and validation

- Every external value — HTTP/model response, CLI arg, env var, Redis/DB JSON,
  file — is untrusted. Validate once at ingress with a Pydantic model or guard;
  never let raw JSON flow into a query, receipt, or publication.
- **DSN custody is load-bearing.** Each capability has its own least-privilege
  role/DSN (`local_source_loader`, `forecast_iteration`,
  `forecast_mv_refresh`, `receiver_writer`, `published_reader`). A component uses
  only its DSN and fails closed on a wrong host/port/database/role or on
  `DATABASE_URL` where a scoped DSN is required. Never widen a role to make code
  work; add/justify a reviewed role.
- Alembic is the **only** component that creates or alters the `agri` schema.
  Runtime/worker code must never call `create_all`, `drop_all`, or extension
  DDL. To change a programmable object, edit the canonical file in `db/agri/**`
  and load it via `agri_data_service.db.sql_objects.load_object_sql` from a new
  migration (see `db/AGENTS.md`).
- Secrets, exact sensitive locations, and raw prompts are redacted from logs; log
  request IDs, source health, and safe diagnostics instead.

## Provenance, determinism, and forecasting integrity

- Carry provenance with every value and signal: source release, observed/published
  time, spatial support/resolution, license snapshot, checksum, and known-missing
  inputs. Partial stays partial — never fabricate a plausible default for a
  missing governed input.
- **No leakage.** A forecast/hindcast/iteration may use only data available at
  its as-of/issue/cutoff time. Simulated cutoffs are never written as operational
  issue times. Preserve time-honest evaluation.
- Determinism where it is checksummed: reproducible seeds, UTC everywhere, pinned
  rendering (dates/intervals/floats), and stable ordering. A checksum function's
  inputs and formatting must be pinned so the digest is reproducible.
- Immutability is enforced in the database (triggers/guards); application code
  must not attempt to mutate finalized receipts, values, publications, or
  evidence, and must treat those attempts as bugs, not retry candidates.
- Evaluation-only artifacts (`evaluation_only`, `publication_authorized=false`)
  never cross into receipt/publication/recommendation DML or the serving views.
  Publication requires the reviewed writer role, passing gates, immutable
  receipts, and a pointer — in that order.

## Algorithmic excellence

- Bound queries and computations at the boundary: date ranges, row limits,
  horizon days, simulation counts, page sizes. Paginate or stream anything
  unbounded; do not load a large dataset into memory to filter it — filter in
  PostGIS/SQL.
- Prefer set-based SQL and vectorized `numpy`/`pandas`/`polars` over Python loops
  on hot paths. Know a hot path's complexity and its supporting index/plan.
- State a method's assumptions and limits in code-adjacent docs; do not imply
  seasonality/autocorrelation modeling a bootstrap does not perform. Add a new
  immutable method version rather than silently changing a shipped one.
- Apply a transaction-local statement timeout on direct SQL callers, matching the
  CLI/procedure convention (120 s).

## Tests and review gates

- Each behavior change ships focused `pytest` coverage of success **and** the
  failure/timeout/stale/partial path, boundary/leakage, and role/scope enforcement.
- Database invariants use the disposable-PostgreSQL contract tests (env-gated,
  e.g. `FORECAST_ITERATION_TEST_DATABASE_URL`, `SCHEMA_PARITY_DATABASE_URL`);
  never point them at the persistent `plantgeo` warehouse. Mock external services
  at the boundary; tests must not call live APIs/Redis/Railway.
- After any migration that changes schema, regenerate the declarative tree
  (`db/tools/regenerate.py`) so the parity test stays green.
- Before ready: `uv run ruff format`, `ruff check src/ tests/`, `mypy src/`, and
  `pytest`. In a multi-fix pass, apply all fixes first, then run the sweep once.

## Review checklist

1. Is DDL Alembic-only, and is any object change forward-loaded from `db/agri/**`?
2. Does each component use its own least-privilege DSN and fail closed?
3. Is the path leakage-free, deterministic where checksummed, and provenance-carrying?
4. Are evaluation-only artifacts prevented from reaching publication/serving?
5. Is every input validated at ingress and every query/loop bounded?
6. Do tests cover the failure/partial path; did the full sweep pass once?
