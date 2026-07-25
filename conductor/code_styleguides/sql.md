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
