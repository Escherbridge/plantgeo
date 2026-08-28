# Runtime query SQL

Every non-trivial query this service issues at request or job time lives here as
its own `.sql` file, not as a multi-line Python string. The standard is
`conductor/code_styleguides/sql.md`, section *Runtime query SQL lives in
dedicated files, not Python strings*; this file is the operating manual for the
tree and the binding rules for the extraction sweep that fills it.

This is **not** the declarative schema tree. `db/agri/**` is DDL regenerated from
the migration head and proved against it by `test_declarative_schema_parity`; a
stray `SELECT` filed there breaks that test's assumption that every file is an
object definition. DDL goes there and is loaded with
`agri_data_service.db.sql_objects.load_object_sql`. Queries go here.

## Layout and loading

One statement per file, at `sql/<package>/<name>.sql`, where `<package>` is the
top-level package that owns the call site — `execution`, `ingest`, `routes`,
`db`, `jobs`, `cli`. The name is the Python constant that holds it, lowercased
and stripped of its leading underscore: `_INSERT_FEATURES` becomes
`insert_features.sql`, `_SELECT_EXISTING_EXTERNAL_IDS` becomes
`select_existing_external_ids.sql`.

Load at module import time, wrapped in `text(...)`, exactly where the inline
literal sat:

```python
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql

_INSERT_FEATURES = text(load_query_sql("ingest/insert_features.sql"))
```

Import time, not call time: the file is read once per process, and a missing or
unreadable file fails at startup rather than at first query. Because the call
site's only literal is the file's path, `grep -r insert_features.sql` finds the
loader and the file in one pass — that traceability is the whole point of the
layout.

The tree sits under `src/agri_data_service`, so hatchling's
`packages = ["src/agri_data_service"]` ships it into the wheel and the installed
`agri-service` console script can read it. `tests/test_sql_queries_loader.py` asserts
that, building a wheel and looking for `sql/db/_loader_smoke.sql` inside it. The
`.gitkeep` in each package directory keeps the empty ones tracked; delete it once
the directory holds a real query.

## Documentation standard

Owner decision, and it is stricter than the styleguide's minimum: every `.sql`
file opens with the required header — **Purpose**, **Loaded by**, **Params** —
*and* a plain-English, clause-by-clause walkthrough written for someone who does
not know SQL. Say what each CTE contributes, what each join is for, and what each
filter excludes. The first time a load-bearing construct appears in a file —
`SKIP LOCKED`, `ON CONFLICT`, `LATERAL`, a window frame, `DISTINCT ON`, a cast
that exists to resolve a parameter type — explain what it does and why the query
needs it. A reader who has never written SQL should be able to say what this
statement returns and why it is safe. `sql/db/_loader_smoke.sql` is the smallest
conforming example; the following is the shape at full size.

```sql
-- Purpose: page the published forecast serving view by series and time window.
-- Loaded by: agri_data_service.routes.forecasts
-- Params: series_key (text), valid_from/valid_to (timestamptz, nullable),
--         spatial_mode (text: 'none'|'centroid'|'geojson'), fetch_limit (int),
--         row_offset (int)
--
-- Parameter names appear above WITHOUT a leading colon. See "Header/bind-param
-- trap" in sql/AGENTS.md: SQLAlchemy reads comments too.
--
-- How this query works, clause by clause:
--
--   WITH windowed AS (...)
--     A CTE ("common table expression") -- a named subquery defined up front and
--     referenced below like a table. This one narrows the serving view to the one
--     series and time window the request asked for, so everything downstream reads
--     a small set instead of the whole view.
--
--   LEFT JOIN LATERAL (SELECT ... LIMIT 1) AS latest ON TRUE
--     LATERAL lets the subquery on the right see each row on the left, so it can
--     be evaluated once per row -- an ordinary subquery cannot reference the row
--     it is being joined to. Used here to fetch each series' single most recent
--     publication. LEFT keeps rows that have none; ON TRUE means "no extra join
--     condition", because the correlation already lives inside the subquery.
--
--   avg(value) OVER (PARTITION BY series_key ORDER BY valid_at
--                    ROWS BETWEEN 6 PRECEDING AND CURRENT ROW)
--     A window function: it computes a value per row while still returning every
--     row (unlike GROUP BY, which collapses them). PARTITION BY restarts the
--     calculation per series; the ROWS ... frame is the sliding span it averages
--     over -- this row and the six before it, i.e. a 7-point trailing mean.
--
--   ORDER BY valid_at DESC, series_key
--     A total order. Paging without one can repeat or skip rows between pages,
--     because the database is otherwise free to return equal rows in any order.
--
--   LIMIT / OFFSET
--     The page: fetch_limit rows, starting row_offset rows in. Both are bound
--     parameters, never interpolated into the text. Note they are named here
--     without their colons even though the statement below writes them with one
--     -- a comment quoting SQL is still a comment text() will scan.
WITH windowed AS (
    ...
)
SELECT ...
```

Bound parameters are unconditional: `:name` executed with a params dict, never
f-string or `%` interpolation of a value into the text — that is a SQL-injection
surface and a type-coercion hazard at once. A file may bake in a Python module
constant at load time (a byte cap in a `CASE` branch, say) only when that
constant is never user input.

## Binding rules for the extraction sweep

These are verified findings, not style preferences. Each one has broken or would
break something.

**Header/bind-param trap.** SQLAlchemy's `text()` scans the entire string for
`:word` — comments included — so a header line reading
`-- Params: :series_key (text)` mints a phantom bind parameter that no caller
supplies, and execution fails on a missing bind. Write parameter names in headers
and prose comments **without** the leading colon. The same applies to any other
colon-prefixed word in a comment; `ingest/reconcile.py` already carries this
warning inline. Type annotations inside a comment (`text: 'none'|'centroid'`) are
safe only because the colon trails the word rather than leading it — keep it that
way.

**Marker protocol.** Some statements open with a `-- <marker>` comment line that
unit tests dispatch on: they answer `AsyncSession.execute` from a recording stub
and match the statement by that first line (`ingest/reconcile.py`,
`jobs/lease.py`, `ingest/validation.py` and kin). When a statement carries a
dispatch marker, the marker line stays the **first line of the `.sql` file**,
above the documentation header. Moving it below the header silently breaks the
test's dispatch, usually as a confusing "unexpected statement" rather than a
missing-marker error.

**Checksummed SQL is frozen.** `execution/geospatial_pilot.py` sha256s
`DERIVED_VALUES_SQL` into a provenance receipt (`"sql_sha256"`), and the receipt
is immutable once written. That constant must **never** be extracted, reflowed,
or reformatted — any byte change re-identifies every receipt derived from it. A
revised formula gets a new identity, never a rewritten digest. Leave it in
Python.

**`.bindparams(...)` typing stays in Python.** Where a call site attaches
explicit types (`bindparam("ids", type_=ARRAY(Text))` and friends), only the SQL
text moves into the file; the `.bindparams(...)` chain stays at the call site on
the `text(...)` object. Type information is Python-side and has no representation
in a `.sql` file.

**`alembic/versions/**` is immutable.** Applied revisions are forward-only and
content-checksummed; the sweep never touches them, regardless of how much inline
SQL they contain. They also sit outside `src/`, so the advisory
`tests/test_no_new_inline_sql.py` walker never sees them.

**Some SQL correctly stays inline.** A one-line lookup or existence check
(roughly: one line, one `WHERE`, no CTE) stays a `text("...")` literal next to its
call site — extracting it buys no traceability, since the whole statement is
already visible. SQL assembled from optional predicates, or built through
SQLAlchemy Core/ORM expression construction (`select(...)`, `.where(...)`), also
stays in Python: a `.sql` file cannot express a runtime-conditional shape, and
forcing it to would just move the logic into string concatenation around a file
read. Everything else — anything with a CTE, a join, or more than one clause, and
certainly anything already living as a multi-line `text("""...""")` literal —
gets extracted.

## The advisory ratchet

`tests/test_no_new_inline_sql.py` walks `src/agri_data_service/` with the `ast`
module, counts multi-line `text("""...""")` literals per module, and compares
against a checked-in baseline. It fails only when a module **exceeds** its
baseline — new inline SQL. Extracting a statement drops a module below its
number, which passes; ratchet the baseline down in the same commit so the ground
gained is held. It is advisory and ad-hoc (the owner removed build gates); it
runs under `scripts/check.py`, not as a deploy gate.

## Where these queries sit in the lane contract

The observed-day census, the coverage reconciliation and the agent proximity queries are all steps in
`docs/layer-lane-standard.md`. Two rules there are enforced by this directory's conventions and are easy to
breach silently: the census must filter `geometry_id IS NOT NULL` or it reports days the slider cannot reach,
and a colon immediately followed by a word character in a comment mints a phantom bind parameter.
