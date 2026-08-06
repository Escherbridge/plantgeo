---
type: code-styleguide
---

# PlantGeo SQL, schema, and migration standard

The required standard for the `agri` warehouse: Alembic migrations
(`services/agri-data-service/alembic/versions/*.py`), the canonical declarative
tree (`services/agri-data-service/db/agri/**`), and every view, function,
procedure, and trigger. PostgreSQL 16 + PostGIS + TimescaleDB. It inherits
`engineering-principles.md`.

## Baseline

- Schema-qualify every object (`agri.<name>`); the rebuild runs with an empty
  `search_path`. Use explicit column lists, explicit types, and explicit casts —
  no `SELECT *` in a view or function that other code depends on.
- Prefer set-based statements over row-by-row PL/pgSQL loops. Reach for a loop
  only when a set expression genuinely cannot express the work.
- Deterministic by construction: pin UTC and format GUCs
  (`IntervalStyle`, date/float rendering) inside any function whose output is
  checksummed; give any evaluated/aggregated query a stable `ORDER BY`.
- Name for intent: `v_` views, `mv_` materialized views, `ck_`/`fk_`/`ix_`
  constraints and indexes, `guard_`/`enforce_`/`verify_` trigger functions.
  Keep rationale in `db/AGENTS.md` and `alembic/AGENTS.md`, not inline essays.

## The declarative schema project (source of truth)

- `db/agri/**` is the single canonical definition of every object — tables,
  sequences, functions, procedures, views, the materialized view, constraints,
  indexes, triggers, partitions — one reviewable file each. It is generated from
  the migration head and **provably matches** it (`test_declarative_schema_parity`).
- Do not hand-edit generated files to change the live schema. To change an
  object: edit its `db/agri/**` file, add a migration that loads it via
  `load_object_sql(...)` (drop-then-create for a signature/column change;
  `or_replace=True` for a body-only change), then run `db/tools/regenerate.py`.
- `manifest.sql` rebuilds the schema in dependency order: schema → sequences →
  core objects interleaved by pg_dump emission order (tables and functions depend
  on each other bidirectionally) → foreign keys → partition attaches → triggers.
  Preserve these waves when the tree changes.

## Migrations

- Migrations are **immutable and forward-only** once shipped: never edit an
  applied revision's body (it breaks its content checksum and the immutability
  contract). Roll back by restoring a verified backup, not by mutating history.
- Deterministic and explicit: never call `metadata.create_all()` from a
  historical revision. The foundation revision does not enable extensions — it
  preflights that the reviewed manual gate already installed them.
- Membership/finalization invariants are enforced at the database level
  (serialize-on-write, freeze-after-validation). New revisions must preserve
  those guarantees, not weaken them.

## Immutability, provenance, and evaluation gates

- Finalized receipts, forecast/iteration values, publications, and geospatial
  evidence are immutable: guard triggers reject update/delete. Application code
  never fights these guards.
- Checksums bind identity and lineage; a revised threshold or formula gets a
  **new** identity/version (e.g. `hindcast_v2`), never a backfilled digest on an
  old receipt.
- The evaluation-only iteration plane has no publication foreign key and no
  promotion function. Hindcasts and iterations are evaluation/ML-feature evidence
  and must never join the operational serving view or bypass publication gates.
- No leakage in SQL either: `RETURNS TABLE` signal/forecast functions expose
  forecast-versus-actual series only after their server-recorded availability
  time; calibration horizons may not cross the simulated cutoff.

## Query & algorithmic excellence

- Every serving query is bounded (release-set-pinned, time-windowed, row-capped)
  and backed by an appropriate index; verify the plan for hot paths with
  `EXPLAIN (ANALYZE, BUFFERS)`. Use GiST for geometry, btree for lookups/ordering.
- Materialized serving aggregates are created `WITH NO DATA` and refreshed only
  through the reviewed refresher role — never auto-refreshed on write.
- Apply a transaction-local `statement_timeout` (120 s) to direct SQL runbooks,
  matching the CLI/procedure convention.

## Runtime query SQL lives in dedicated files, not Python strings

This is a **different tree from the declarative schema project above**. `db/agri/**`
is DDL — the source of truth for objects the database itself holds. This section is
about DML/query SQL a service issues at request or job time (`SELECT`/`INSERT`/`UPDATE`
statements built as `sqlalchemy.text(...)`). Never file a runtime query under
`db/agri/**`; that tree is regenerated from the migration head and a stray `SELECT`
there breaks the parity test's assumption that every file is an object definition.

- **Location.** `src/agri_data_service/sql/<package>/<name>.sql`, where `<package>`
  is the existing top-level package that owns the call site (`execution`, `ingest`,
  `routes`, `db`) — mirroring that package split the same way `db/agri/**` mirrors
  object kind. This keeps the file inside `src/agri_data_service`, so it packages
  into the wheel automatically (`[tool.hatch.build.targets.wheel] packages =
  ["src/agri_data_service"]`); `db/agri/**` gets no such treatment because it sits
  above `src/` and is read only from a dev checkout during a migration, never from
  the installed `agri-cli` console script.
- **Naming.** One statement per file, named after the Python constant that will hold
  it, lowercased and without its leading underscore — `_INSERT_FEATURES` becomes
  `insert_features.sql`, `_SELECT_EXISTING_EXTERNAL_IDS` becomes
  `select_existing_external_ids.sql`.
- **Loading.** `agri_data_service.db.sql_queries.load_query_sql(relative_path)` reads
  and returns the file's text, resolved against
  `src/agri_data_service/sql`, the sibling of `db/sql_objects.py`'s `load_object_sql`
  and deliberately shaped like it: same escape guard, same loud
  `FileNotFoundError` on a missing file. Assign its result at module import time,
  wrapped in `text(...)`, exactly where an inline literal sits today:

  ```python
  from sqlalchemy import text
  from agri_data_service.db.sql_queries import load_query_sql

  _INSERT_FEATURES = text(load_query_sql("ingest/insert_features.sql"))
  ```

  Reading at import time means the file is read once per process and a missing or
  unreadable file fails at service startup, not at first query. The string literal
  in the call site is the file's path, so `grep -r insert_features.sql` finds every
  loader and every `.sql` file in one pass — that is the traceability the layout
  buys over an inline string.
- **Documentation header.** Every extracted `.sql` file opens with a `--` comment
  block stating: a one-line purpose, the module that loads it (import path, not a
  free-text description), and the bound parameters it expects with their meaning
  (name, and type if it is not obvious from the name). Example:

  ```sql
  -- Purpose: page the published forecast serving view by series and time window.
  -- Loaded by: agri_data_service.routes.forecasts
  -- Params: series_key (text), valid_from/valid_to (timestamptz, nullable),
  --         spatial_mode (text: 'none'|'centroid'|'geojson'), fetch_limit (int),
  --         offset (int)
  SELECT ...
  ```

- **When inline SQL is still correct.** A one-line lookup or existence check
  (roughly: fits on one line, one `WHERE` clause, no CTE) stays inline as a
  `text("...")` literal next to its call site — extracting it buys traceability
  nothing, since the whole statement is already visible at the call site. SQL
  assembled dynamically from optional predicates, or built through SQLAlchemy
  Core/ORM expression construction (`select(...)`, `.where(...)`), also stays in
  Python: a `.sql` file cannot represent a runtime-conditional shape, and forcing
  one to would just move the real logic into string concatenation around a file
  read. Extract everything else — anything with a CTE, a `JOIN`, or more than one
  clause, and certainly anything already living as a multi-line `text("""...""")`
  literal today (`routes/forecasts.py`'s `_FORECAST_SERVING_SQL`,
  `ingest/writer.py`'s `_INSERT_FEATURES`/`_REFRESH_FEATURE`, and their kin).
- **Parameter binding is unconditional.** Every extracted file keeps using bound
  parameters (`:name`, executed with a params dict) — never f-string or `%`
  interpolation of a value into the SQL text, extracted or not. This is the same
  rule that keeps a bigint parameter from silently resolving to the wrong type on
  the TypeScript side of this codebase; on the Python side, a string-interpolated
  value is a SQL-injection surface as well as a type-coercion one. A file may
  interpolate a Python module constant into its own text at load time (e.g. a byte
  cap baked into a `CASE` branch) only when that constant is never user input and
  is itself defined in Python, not read off a request.

## Privileges & safety

- Grant least privilege per role; the migration creates no unreviewed grants.
  Loader/refresher/iteration/reader roles receive only the tables and verbs they
  need, and never publication or recommendation surfaces.
- All local credentials live in ignored env files, never in tracked SQL or docs.

## Review checklist

1. Is the object defined once in `db/agri/**`, applied via a forward-loading
   migration, with the parity test regenerated and green?
2. Is the migration forward-only and free of `create_all`, and does it preserve
   database-level immutability/finalization invariants?
3. Are checksummed outputs deterministic (pinned GUCs, stable order)?
4. Are evaluation-only artifacts kept out of serving/publication?
5. Is every serving query release-pinned, time-honest, bounded, and indexed?
6. Do grants stay least-privilege, with no publication/recommendation exposure?
7. Is every non-trivial runtime query in its own `sql/<package>/*.sql` file, loaded
   through `load_query_sql` with bound parameters, and does its header name a
   purpose, its loading module, and its parameters?
