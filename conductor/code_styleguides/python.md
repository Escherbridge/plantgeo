---
type: code-styleguide
---

# PlantGeo Python and data-service standard

The required standard for the agri data/forecasting service
(`services/agri-data-service/**/*.py`) and any other PlantGeo Python. It is
specific to the governed warehouse: Alembic-owned PostGIS schema, immutable
forecast receipts, evaluation-only iteration plane, single-owner-credential DSN
custody, and Sanic/tRPC-adjacent services. It supplements `ruff` and `mypy --strict`;
it does not replace them. It inherits `engineering-principles.md`.

## Baseline

- `mypy` runs in `strict` mode (with the SQLAlchemy plugin) over **`src` and
  `scripts`**, and `ruff` over `src tests scripts`. A bare `type: ignore` is
  forbidden; a coded one (`# type: ignore[import-untyped]`) is allowed only where
  a third-party package genuinely ships no stubs, and the code must name the
  reason it exists.
- **`Any` is a boundary type, not a payload type.** Receive untyped values as
  `object`/`Unknown`, validate, then expose a named type. The one carve-out,
  reconciled against the tree on 2026-09-02 (747 pre-existing `dict[str, Any]` /
  `Mapping[str, Any]` occurrences: 218 in `src`, 529 in `scripts`): a document
  decoded from JSON — an object-store manifest, ledger, checkpoint or receipt —
  may be typed `Mapping[str, Any]` while it is being indexed by key and
  re-serialised, because a `TypedDict` cannot describe a document whose keys are
  mutated in place and whose bytes are SHA-256 pinned. Every value that then
  drives a decision is still narrowed with an explicit `isinstance` guard or a
  named helper that raises the module's own contract error. `Any` remains
  forbidden as the return type of domain logic, as a model field, and as a way to
  silence a type error rather than answer it.
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
- **DSN custody was retired on 2026-08-08** by owner ruling, recorded in Alembic
  revision `20260808_0019`. There is one credential: the owner. Do **not** add a
  least-privilege role, a host/port/database allowlist, a login assertion, or a
  "must not reuse `DATABASE_URL`" distinctness rule — reintroducing any of them
  is the violation now. `LOCAL_SOURCE_LOADER_DATABASE_URL`,
  `FORECAST_MV_REFRESH_DATABASE_URL`, and `FORECAST_ITERATION_DATABASE_URL` are
  optional *overrides* that fall back to `DATABASE_URL`; blank counts as unset.
  What a DSN validator may still do is check *shape* — one shared parser
  (`Settings._require_complete_database_url`) rejects anything that is not a
  complete `postgresql+asyncpg://` URL. Choosing the right database is an
  operator responsibility, not a config guarantee.
- The `receiver_writer`/`published_reader` **service profiles** survive and are
  still load-bearing: they are about which routes a process mounts and which pool
  it opens, not about privilege. A production profile must never carry
  `DATABASE_URL` or the other profile's DSN.
- Alembic is the **only** component that creates or alters the `agri` schema.
  Runtime/worker code must never call `create_all`, `drop_all`, or extension
  DDL. To change a programmable object, edit the canonical file in `db/agri/**`
  and load it via `agri_data_service.db.sql_objects.load_object_sql` from a new
  migration (see `db/AGENTS.md`).
- A non-trivial runtime query (a CTE, a join, or anything already a multi-line
  `text("""...""")` literal) is not typed inline: it lives in its own
  `src/agri_data_service/sql/<package>/<name>.sql` file and is loaded at module
  import time through `agri_data_service.db.sql_queries.load_query_sql`, bound with
  named parameters. See `code_styleguides/sql.md`, "Runtime query SQL lives in
  dedicated files, not Python strings", for the file layout, the loader, the
  required header, and where inline SQL still belongs.
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

## Readability and region portability

- Full-word identifiers: `bounding_box`, `request_timeout_seconds`,
  `ensemble_size`. No `bb`, `tmo`, `n_ens`. Field-standard terms (`ndvi`,
  `huc12`, `srid`) count as words. Name the algorithm at its implementation in
  one line (`# Kahan summation`) and link a reference for an esoteric one.
- Soft size ceiling, applied in review, not by `ruff`: modules ~600 lines,
  functions ~60, nesting ≤3. The 2026-09-18 readability pass split
  `pipeline/parquet/availability_index.py` (3,164 → 911 lines, six
  `availability_*` siblings), `parquet_ops/snapshot_products.py` (2,398 → 454,
  seven `snapshot_*` siblings), `pipeline/parquet/gap_fill.py` (2,386 → 321,
  `gap_fill_*` siblings plus `gap_census.py`) and
  `execution/job_executor_service.py` (2,552 → 1,784; `lane_specs.py`,
  `lane_scheduling.py`, `turn_reports.py`), each original re-exporting its
  public names. None may grow back; `job_executor_service.py` still owes a
  split of `run_scheduled_command` once
  `tests/execution/test_command_stderr_capture.py` stops monkeypatching its
  globals as one unit.
- **The region is a value, not a constant.** `foundation/region/` owns the
  frozen `Region` model and the one PNW manifest; lanes, planes and agent tools
  take a `Region` parameter. `PACIFIC_NORTHWEST_BBOX` (`ingest/mtbs.py`) and
  `SEED_ENVELOPE` (`foundation/botanical_occurrences/coordinates.py`) are the
  migration list. A new literal latitude, longitude, envelope or state code
  outside the manifest fails review (`federation.md` §1).
- **Sources implement a layer protocol and declare coverage.** A regional
  source (SSURGO, USDM, MTBS) lives at `<layer>/<source>.py` and implements the
  layer's `Protocol`; layer logic (schema, Monte Carlo, plane, agent tool) never
  branches on a source name. A region with the layer unbound gets a governed
  absence with a reason, never a fallback to the pilot's source
  (`federation.md` §2).
- Region-specific assumptions (fire-season months, growing-degree base, snowmelt
  window) are manifest fields or live inside a source implementation with the
  region named beside them, never in shared `method`/`planes` code.

## Tests and review gates

- Each behavior change ships focused `pytest` coverage of success **and** the
  failure/timeout/stale/partial path, boundary/leakage, and role/scope enforcement.
- Database invariants use the disposable-PostgreSQL contract tests, gated on the
  single `AGRI_TEST_DATABASE_URL` env var and its shared `tests/conftest.py`
  fixture (verifies Alembic head, refuses the persistent `plantgeo` warehouse);
  never point it at that warehouse. Mock external services at the boundary;
  tests must not call live APIs/Redis/Railway.
- After any migration that changes schema, regenerate the declarative tree
  (`db/tools/regenerate.py`) so the parity test stays green.
- Before ready: `uv run --no-sync python scripts/check.py`, which is the single
  authority for the four gates (`ruff format --check src tests scripts`,
  `ruff check src tests scripts`, `mypy src scripts`, `pytest -q`). **Never a bare
  `uv run`**: it re-resolves from the lock's default groups, drops the dev extra
  and takes pytest, ruff and mypy with it mid-sweep. In a multi-fix pass, apply
  all fixes first, then run the sweep once; then `scripts/check.py --write-receipt`
  so `QUALITY_RECEIPT.json` names the exact tree the green run judged. The image
  build runs `scripts/verify_quality_receipt.py` and refuses a tree whose digest
  has moved since that receipt.

## Review checklist

1. Is DDL Alembic-only, and is any object change forward-loaded from `db/agri/**`?
2. Does each component connect with the **single owner credential** and fail
   closed on a malformed DSN? DSN custody was retired on 2026-08-08 (Alembic
   `20260808_0019`); a review that asks for a per-component least-privilege role,
   a host/port/database allowlist or a login assertion is asking for the thing the
   migration drops. What a review may still require is complete-URL shape
   validation through `Settings._require_complete_database_url`, and that a
   `receiver_writer`/`published_reader` profile never carries `DATABASE_URL` or
   the other profile's DSN.
3. Is the path leakage-free, deterministic where checksummed, and provenance-carrying?
4. Are evaluation-only artifacts prevented from reaching publication/serving?
5. Is every input validated at ingress and every query/loop bounded?
6. Do tests cover the failure/partial path; did the full sweep pass once?
7. Does the change read the region from a `Region` value rather than a literal,
   put any new source behind its layer's `Protocol` with a `coverage` claim,
   and keep names full-word and the module under (or no further past) the soft
   size ceiling?
8. **Guide-consistency review.** If the change makes any rule in this guide false,
   the same change fixes the rule and names the ruling or measurement that
   supersedes it. A checklist item that has quietly outlived its decision is worse
   than a missing one: it fails review for the wrong reason and teaches the
   retired design. Items 2 and the `Any`-boundary carve-out under "Baseline" are
   both repairs of exactly that failure, made 2026-09-02.
