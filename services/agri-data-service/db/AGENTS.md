# Declarative `agri` schema project

This directory is the canonical, human-readable source of truth for **every**
object in the `agri` warehouse schema — tables, sequences, functions,
procedures, views, the materialized view, constraints, indexes, triggers, and
the one partition attach. Each object is its own reviewable `.sql` file instead
of being buried inside a Python migration string.

It is the PostgreSQL-idiomatic equivalent of an SSDT `.sqlproj`: a per-object
declarative tree plus a `manifest.sql` that rebuilds the whole schema in
dependency order.

## Relationship to Alembic (read this first)

Alembic is still the **applier of record**. It owns schema state, ordering, and
the immutable/forward-only governance described in `../alembic/AGENTS.md`. This
tree does **not** replace migrations and the existing shipped migrations are
left byte-for-byte untouched (rewriting them would break their content
checksums and the immutability contract).

What this tree adds:

1. **Legibility & review** — the SQL surface (144 programmable objects + 63
   tables) is readable and diffable as real `.sql`, not Python heredocs.
2. **A parity guarantee** — an automated test proves the tree is exactly what
   the migrations produce (see *Parity guarantee*).
3. **Forward-load** — the mechanism for the *next* change, so files and applied
   schema can never drift again (see *Forward-load workflow*).

## Layout

```
db/
  manifest.sql               # ordered rebuild of the whole schema
  agri/
    schema/schema.sql        # CREATE SCHEMA agri
    sequences/*.sql          # standalone CREATE SEQUENCE (serial-style) only
    functions/*.sql          # one file per function (incl. trigger functions)
    procedures/*.sql         # one file per procedure
    tables/*.sql             # self-contained: CREATE TABLE + identity + column
                             #   defaults + PK/UNIQUE/CHECK + indexes
    views/*.sql              # one file per view
    materialized_views/*.sql # matview + its indexes
    foreign_keys/<table>.sql # FK constraints, grouped by owning table
    triggers/<table>.sql     # CREATE TRIGGER bindings, grouped by owning table
    partitions/attach.sql    # ATTACH PARTITION statements (job_event)
  tools/
    split_schema.py          # pg_dump -> per-object tree + manifest (pure, deterministic)
    dump_schema.py           # canonical `pg_dump` flags + banner normalisation
    regenerate.py            # one-command: create disposable db -> migrate -> dump -> split
```

Identity columns (`GENERATED ... AS IDENTITY`) live inside their table file, not
under `sequences/`, because pg_dump emits them as `ALTER TABLE` that needs the
table to exist.

## Manifest wave ordering (why it is not just "by type")

Tables and functions depend on each other **bidirectionally**, so no coarse
"all functions then all tables" split is valid:

- tables → functions: `forecast_receipt`/`forecast_quality_policy` `CHECK`s call
  `forecast_quantiles_valid`; `forecast_hindcast_value`/`forecast_iteration_value`
  have `GENERATED` columns calling checksum functions.
- functions → tables: several functions `RETURNS agri.<table>` row types.

`manifest.sql` therefore applies:

1. `schema`
2. all `sequences` (a `CREATE SEQUENCE` depends on nothing; column defaults
   reference them, so they are front-loaded)
3. **core objects interleaved by pg_dump's own dependency-sorted emission
   order** — functions, procedures, tables, views, materialized view
4. `foreign_keys` (deferred: reference other tables)
5. `partitions` attach (deferred: needs parent+child tables and their indexes)
6. `triggers` (deferred: need both their functions and their tables)

`SET check_function_bodies = false` in the preamble lets function bodies
reference not-yet-created objects, matching pg_dump.

## Parity guarantee

`tests/test_declarative_schema_parity.py`:

- `test_manifest_matches_tree` (no database) — `manifest.sql` references exactly
  the files on disk and every file carries the generated banner.
- `test_declarative_tree_matches_migrations` (set
  `SCHEMA_PARITY_DATABASE_URL` to a head-migrated disposable database) —
  `pg_dump`s that database, re-splits it, and asserts **byte-identical** to the
  committed tree.

Proven separately at build time: applying `manifest.sql` to an empty database
rebuilds an object inventory identical to the migration-built database
(63 tables · 4 views · 1 matview · 66 functions · 2 procedures · 70 triggers ·
485 constraints · 181 indexes) with zero dependency errors.

> Note: rebuilding from the tree and re-dumping shows cosmetic differences in a
> few `CHECK` expressions (`= ANY((ARRAY[...])::text[])` vs
> `= ANY(ARRAY[(...)::text])`). PostgreSQL re-normalises casted-`ANY`-array
> expressions on reparse; the schemas are semantically identical. The tree is
> generated from the migration output, so the parity test (which compares
> against the migration dump, not a rebuild) is exact.

## Regenerating the tree

Run after any migration that changes schema. Requires the local warehouse (or
any Postgres with the reviewed extensions) and a pg16 `pg_dump`/`psql`:

```powershell
$env:PGBIN = 'C:\Program Files\PostgreSQL\16\bin'
uv run python db/tools/regenerate.py `
  --admin-dsn 'postgresql://plantgeo_owner:<pw>@127.0.0.1:5442/plantgeo'
```

It creates a disposable database, enables extensions, applies `alembic upgrade
head`, captures the DDL, rewrites `db/agri/**` + `manifest.sql`, and drops the
disposable database. `pg_dump` output is stable within a major version; the
parity test pins the comparison to the same toolchain and normalises only the
version banner.

## Forward-load workflow

When a future migration changes a programmable object, **edit the canonical file
here and load it from the migration** — do not paste DDL back into a Python
string:

```python
from agri_data_service.db.sql_objects import load_object_sql

def upgrade() -> None:
    # body-only change to a function/procedure/view
    op.execute(load_object_sql("functions/forecast_daily_bootstrap.sql", or_replace=True))

    # signature or view-column change -> drop first, then load canonical text
    op.execute("DROP FUNCTION IF EXISTS agri.forecast_percentile(...);")
    op.execute(load_object_sql("functions/forecast_percentile.sql"))
```

Then run `regenerate.py` so the rest of the tree (and any dependent object
pg_dump reorders) reflects the new head, and the parity test stays green. For
new tables/constraints, author the migration normally, then regenerate — the
tree follows the migration, never the reverse.
