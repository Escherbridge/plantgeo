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

1. **Legibility & review** — the complete programmable-object surface and all
   69 tables are readable and diffable as real `.sql`, not Python heredocs.
2. **A round-trip guarantee** — an automated test proves a fresh build from the
   migration head re-emits this tree byte for byte. Since the 2026-08-25
   collapse the head *is* this tree, so that is a round trip and not an
   independent derivation (see *Parity guarantee*).
3. **Forward-load** — the mechanism for the *next* change, so files and applied
   schema can never drift again (see *Forward-load workflow*), under the
   idempotence rule in *Layering a revision on the greenfield baseline*.

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
  `forecast_quantiles_valid`. Until `20260803_0018` the same edge also came from
  `GENERATED` columns calling checksum functions; both are gone — one left with
  the hindcast plane, one became plain storage.
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
  `AGRI_TEST_DATABASE_URL` to a head-migrated disposable database, see
  `tests/conftest.py`) — `pg_dump`s that database, re-splits it, and asserts
  **byte-identical** to the committed tree.

### What parity proves since 2026-08-25, and what it no longer proves

**It is a round trip, not a second derivation.** The migration head is the
greenfield baseline `20260825_0000`, and that revision builds the schema by
executing `manifest.sql` — this very tree. So the assertion is
`dump(replay(T)) == T`, which holds for *any* round-trippable `T`. Demonstrated
during review: delete a `CHECK` from a tree file, re-run `regenerate.py`, and
parity is green. **A corrupted tree now passes.** Before the collapse the head
was 26 hand-written revisions, so the same comparison really did check the tree
against an independent source; that property is gone and no amount of
re-running the test brings it back.

**What it still catches**, and these are worth the run:

- DDL the server does not re-emit verbatim after a parse — exactly how the
  `= ANY ((ARRAY[...])::text[])` → `= ANY (ARRAY[(...)::text])` renormalisation
  was found. The tree is a **fixed point**: applying it and re-dumping must
  reproduce it byte for byte, so a diff on an unchanged tree is the fixed point
  breaking, not noise.
- A hand-edit to a tree file that is never regenerated, a manifest that
  references a missing or duplicated object, and any drift between the committed
  tree and what a fresh build actually produces.
- Cross-major dump-dialect drift, via `test_cross_major_tree_matches_migrations`.

**What it no longer catches**: the tree having drifted from the *intent* of the
26 archived revisions, because they are no longer on the migration path and
nothing on that path re-derives the schema from them.

**The replacement for the lost leg** is `tests/test_alembic_archive_replay_parity.py`
— marked `agri_db_migration_rehearsal`, opt-in via
`AGRI_ARCHIVE_REPLAY_ADMIN_DSN`, needs a server that can install `timescaledb`
(`20260719_0001`'s preflight demands it). It replays `alembic/archive/` into one
disposable database, builds another from the baseline, and compares them.
Catalogue inventories — relations, columns, routine bodies by `md5(prosrc)`,
index definitions, triggers, FKs, PK/UNIQUE — must match **exactly**. `CHECK`
definitions are scored by the reviewed reparse rule (identical string literals
in the same order, same *set* of identifiers), which is deliberately weaker than
byte equality and says so in its own docstring. Run it after any change that
claims the tree still means what the chain meant.

`tests/test_alembic_baseline_parity.py` covers the two things a text dump cannot
see at all: whether every object the manifest declares was **built**, and the
**privilege layer** `--no-privileges` discards.

## Layering a revision on the greenfield baseline

**Read this before writing the next migration.** It is the one rule the collapse
imposes, and getting it wrong breaks the disposable-database recipe below rather
than the migration that introduces it.

The baseline executes the **current** `db/agri/**` tree. `regenerate.py` rebuilds
that tree by running `alembic upgrade head`. Play those two facts forward:

1. `20260826_0027` does `op.create_table("foo")`. `alembic upgrade head` works —
   the tree has no `foo` yet.
2. You regenerate. The tree now contains `agri/tables/foo.sql`.
3. The **next** build from empty runs the baseline (which creates `foo` from the
   tree) and then `0027` (which creates `foo` again) → `ERROR: relation "foo"
   already exists`.

So: **every revision layered on the baseline must be idempotent against a tree
that already contains its own changes.**

| change | the shape that survives regeneration |
|---|---|
| new table | `CREATE TABLE IF NOT EXISTS` (or `op.create_table(..., if_not_exists=True)` where the Alembic version supports it) |
| new column | `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` |
| new index | `CREATE INDEX IF NOT EXISTS` (`CONCURRENTLY` still allowed) |
| new constraint | a `NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = ...)` probe — PostgreSQL has **no** `ADD CONSTRAINT IF NOT EXISTS` |
| function / procedure / view | `load_object_sql(..., or_replace=True)`, or `DROP ... IF EXISTS` then `load_object_sql(...)` |
| new sequence / schema | `CREATE ... IF NOT EXISTS` |
| a drop | already idempotent with `IF EXISTS`; keep naming the object explicitly, never `CASCADE` |

Programmable objects were never at risk — drop-then-create and `or_replace` are
idempotent by construction. What breaks is new **tables, columns, indexes and
constraints**.

Two gates enforce it, and they are deliberately different in strength
(`tests/test_alembic_baseline_forward_rehearsal.py`):

- `test_no_revision_layered_on_the_baseline_uses_non_idempotent_ddl` — no
  database, always runs, a lint over the shapes that are certainly not
  re-appliable. It cannot prove idempotence, only catch the obvious loss of it.
- The rehearsal — builds a database from empty with `AGRI_MIGRATION_REHEARSAL_ADMIN_DSN`
  and is authoritative. It carries its own negative control: one case injects a
  revision that re-applies a tree file the baseline already loaded and asserts
  the build **fails**, another injects the guarded form and asserts it succeeds.

**Order of operations stays the same**: write the revision against `db/agri/**`,
apply it, regenerate, commit both. Only the *shape* of the DDL changes.

## Provisioning the disposable database

Four separate gates stand between a fresh container and a green `agri_db` run,
and three of them fail in ways that read as something else. The whole recipe,
from an empty machine:

```powershell
podman run -d --name agri-sweep-db --user 0:0 --env-file <env> `
  -p 127.0.0.1:5442:5432 localhost/plantgeo-spatiotemporal:pg16
podman exec agri-sweep-db psql -U plantgeo_owner -d postgres `
  -c "CREATE DATABASE agri_sweep OWNER plantgeo_owner"
podman cp ../../infra/local-warehouse/enable-extensions.sql agri-sweep-db:/tmp/
podman exec agri-sweep-db psql -U plantgeo_owner -d agri_sweep -f /tmp/enable-extensions.sql

$env:DATABASE_URL_SYNC = "postgresql://plantgeo_owner:<pw>@127.0.0.1:5442/agri_sweep"
uv run alembic upgrade head

$env:AGRI_TEST_DATABASE_URL = "postgresql://plantgeo_owner:<pw>@127.0.0.1:5442/agri_sweep"
$env:PGBIN = "C:\Program Files\PostgreSQL\16\bin"
uv run pytest tests/ -q
```

The env file carries `POSTGRES_DB`/`POSTGRES_USER`/`POSTGRES_PASSWORD`/`PGDATA`
exactly as `infra/local-warehouse/compose.yaml` sets them; that compose is the
same recipe with a persistent named volume, so use it when you want the warehouse
rather than a throwaway.

Why each step, and what its absence looks like:

- **The extension gate is manual on purpose.** The greenfield baseline
  `20260825_0000` raises `55000` listing the missing extensions and states
  *"this migration never creates extensions"* — the same fail-closed shape the
  archived `20260719_0001` had, minus `timescaledb`. (This guide previously cited
  `20260716_0001`, an id that never existed in either directory.) The required
  list has exactly one definition, `agri_data_service.db.extensions.REQUIRED_EXTENSIONS`,
  which the preflight, `/ready` and `db/tools/verify_stamp_target.py` all read.
  `infra/local-warehouse/enable-extensions.sql` is that operator
  step, and the image's Dockerfile deliberately deletes the upstream
  `docker-entrypoint-initdb.d` scripts that would otherwise install TimescaleDB
  behind your back. Run it against **each** database; extensions are per-database.
- **`AGRI_TEST_DATABASE_URL` is a libpq DSN, not a SQLAlchemy URL.**
  `conftest._assert_head_and_safe` hands it straight to `psycopg2.connect`, so a
  `postgresql+asyncpg://` prefix fails with `invalid dsn: missing "="` — which
  reads like a malformed string rather than a wrong dialect.
- **A database named `plantgeo` is refused.** `PROTECTED_DATABASE_NAME` guards
  the persistent warehouse, so the disposable one needs its own name. The failure
  is a `pytest.fail` at fixture setup, which surfaces as ~40 ERRORs rather than
  one clear message.
- **`PGBIN` is only needed by the parity test**, and only it fails without one
  (`pg_dump not found`) while the other DB-backed tests pass. Point it at a
  `pg_dump` whose major is **≥** the server's; a 16.9 client dumps a 16.14 server
  fine, since the constraint is major-to-major.

Unset `AGRI_TEST_DATABASE_URL` and every one of these tests **skips silently**.
`conftest` prints an `AGRI_DB SWEEP NOTICE` naming what it let skip, and enforces
a no-skip gate once the variable *is* set — so a green sweep with the variable
unset proves considerably less than it appears to. Measured 2026-08-11: 2494
passed with it unset, 2536 passed with it set. The 3 remaining skips are
structural, plus `agri_db_cross_major`, which by design needs a second server on
a different major.

## Toolchain and major-version awareness

`pg_dump` output is stable only *within* a major version, and `pg_dump` cannot
dump a server newer than itself. The tree therefore has one **canonical major**,
`dump_schema.CANONICAL_SERVER_MAJOR` (currently 16, matching the local
warehouse). Two paths exist and they are deliberately not equal in strength:

- **Canonical major (PostgreSQL 16) -- byte-exact, unchanged.**
  `test_declarative_tree_matches_migrations` dumps, re-splits, and compares
  byte-for-byte. It applies **no** normalisation; it asserts the raw dump needs
  none. `regenerate.py` refuses to run against any other major, so a foreign
  dump dialect can never be baked into the reviewed source of truth.
- **Any other major -- normalised, opt-in.**
  `test_cross_major_tree_matches_migrations` (`AGRI_CROSS_MAJOR_DATABASE_URL`,
  marker `agri_db_cross_major`) applies exactly two enumerated rules and then
  requires the same byte-exact match.

The two rules, and why each is narrow enough to trust:

1. **`\restrict` / `\unrestrict` markers (pg18+).** psql meta-commands, not
   DDL, carrying a randomly generated token that differs every run, so they can
   never be compared. Stripped in `dump_schema.dump_agri` and again in
   `split_schema.parse_blocks` (the closing marker sits after the final object
   header and would otherwise be swept into the last object's file).
2. **Inline NOT NULL constraint names (pg18+).** PostgreSQL 18 stores NOT NULL
   as real `pg_constraint` rows (722 of them in `agri`; PostgreSQL 16 stores
   none), and its `pg_dump` prints an explicit `CONSTRAINT <name>` whenever the
   stored name is not the one a restore would derive
   (`<table>_<column>_not_null`) -- here, 14 cases: 6 names truncated at 63
   characters and 8 inherited by the `job_event_default` partition from its
   parent. The regex is anchored to a column-level `NOT NULL` and deletes only a
   *name*, so a NOT NULL appearing or disappearing, a type change, or a
   table-level constraint still has to match byte-for-byte afterwards.

   **The anchoring alone is not what makes this safe, and must not be relied on
   as if it were.** The regex is not string-literal aware: a column whose
   DEFAULT contains text resembling a constraint clause (verified with
   `DEFAULT ' CONSTRAINT evil NOT NULL'::text`) *is* rewritten by it. What
   actually holds the guarantee is the **catalogue cross-check** each caller
   makes. The canonical pg16 path asserts the removed set is *empty*; the
   cross-major path asserts it *equals* the set of non-derivable
   `contype = 'n'` rows read independently from that server's catalogue. The
   injected-literal case above fails both legs loudly (`removed-not-cat=['evil']`
   plus a tree byte-identity failure). Any future caller of
   `normalize_named_not_null` **must** pair it with one of those assertions;
   invoking it bare would silently corrupt a DEFAULT.

   Note also that a pg18 NOT NULL constraint *rename* between two non-derivable
   names is invisible to this check, and inherently so: pg16 cannot express
   these names, so the canonical tree holds nothing to drift from. Nothing
   verifiable is lost, but parity does not cover it.

**What the cross-major check does not prove.** It shows two servers produce the
same *schema*; it says nothing about data migration, restore, planner behaviour,
or extension runtime behaviour. See
`../plans/postgresql-18-migration-rehearsal-2026-08-02.md` for the measured
evidence and its limits.

When no local client is new enough to dump the foreign server, set
`AGRI_CROSS_MAJOR_PG_DUMP_ARGV` to a JSON argv that launches the client shipped
with that server (e.g. `["podman","exec","-i","<container>","pg_dump"]`) and
`AGRI_CROSS_MAJOR_DUMP_DSN` to the DSN *that* client can reach.

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

## Governance: checksums are records, not enforcement

The head is `20260825_0000`; the revision this section is *about* is
`20260803_0018`, now in `alembic/archive/`. It retires the database-level enforcement
layer built on top of the checksums — 48 triggers, 34 routines, 10
status-to-checksum evidence CHECKs and 3 owner roles — plus the whole hindcast
plane (`forecast_hindcast_run`, `forecast_hindcast_value`,
`v_forecast_hindcast_outcome`, their checksum/finalize/signal functions, and
`strategy_selection_quality_evidence`, which INNER JOINed that view). All 61
checksum columns and their 29 format CHECKs stay.

Why: `../plans/checksum-layer-audit-2026-08-03.md` §3.1. Tamper-evidence needs
the digest to be beyond the reach of the party who might tamper, and here the
researcher, the DBA and the hypothetical adversary are **one person with one
credential**. The digest lives in the same row it protects, in the same
database, defended only by triggers the table owner can disable — the repo
ships two copy-pasteable bypasses (`…0014:169`,
`tests/test_forecasting_v1_upgrade_postgresql.py:209`). What the layer actually
bought was accident prevention, and §3.2 shows that comes from the checksum
columns plus `materialize_forecast_iteration`'s idempotency block, both kept.

So, reading this tree after `0018`:

- **Checksums are a reproducibility record.** They state what the inputs were.
  They do not assert that nobody changed them.
- **There are no database-enforced state machines.** `staging → finalized`,
  `draft → validated`, `→ published` are no longer gated by `verify_*` triggers
  or evidence CHECKs, and release-set membership is no longer frozen after
  draft. Nothing in the database stops a hand-written UPDATE.
- **The CLI owns preconditions.** Every invariant the guards used to hold — a
  validated run before a receipt, a manifest before publishing, lineage before
  an actual, membership discipline on a release set — is the caller's job to
  establish before it writes.

Two weakenings that no DDL signature reveals: dropping
`ck_forecast_publication_published_evidence` and
`ck_forecast_training_validated_evidence` lets
`agri/views/v_forecast_series_serving.sql` emit rows with NULL
`manifest_checksum`/`published_at` (`:12-13`) and NULL training checksums
(`:48-49`). No column changed nullability; only the served contract weakened.

## What survives `0018`, and why each survivor is load-bearing

- **`ck_forecast_receipt_finalized_evidence`**
  (`agri/tables/forecast_receipt.sql:24`) — the only status-to-checksum evidence
  CHECK retained. With the guards gone it is the **only** thing preventing
  `status = 'finalized'` on a receipt carrying no `receipt_checksum`, and
  `agri/views/v_forecast_series_serving.sql:61` filters the ML serving lane on
  exactly that status. `receipt_checksum` has no SQL function behind it, so the
  digest must arrive from Python; this CHECK is what keeps the two moving
  together.
- **The seven `record_*` writers and their 16 triggers** — they populate
  `agri.forecast_input_recorded_at`, which
  `agri/views/v_forecast_timeseries_contract.sql:45-46` INNER JOINs and on which
  `agri/functions/forecast_daily_bootstrap.sql:67-72` does
  `IF NOT FOUND THEN RAISE EXCEPTION`. Dropping them hard-RAISEs for any series
  or data source registered after `0018`. They stay `SECURITY DEFINER` (the
  constrained-loader path needs it) but now run as the migrating role rather
  than the retired `plantgeo_forecast_input_recorder_owner` — a real privilege
  widening, accepted deliberately, not a no-op.
- **`guard_strategy_review_change`** (2 triggers) — sole caller of
  `strategy_outcome_definition_checksum` and
  `strategy_selection_policy_checksum`
  (`agri/functions/guard_strategy_review_change.sql:35-36,53-54`), which it
  assigns into `NEW`. Dropping it leaves `definition_checksum` and
  `policy_checksum` permanently NULL.
- **`plantgeo_forecast_mv_refresh_owner`** — the one owner role `0018` kept, and
  **retired in turn by `20260808_0019`.** It owned
  `agri.mv_forecast_ml_daily_serving` and
  `agri.refresh_forecast_ml_daily_serving()`; non-concurrent `REFRESH` requires
  matview ownership and the refresher is `SECURITY DEFINER`, so **owner and
  definer must remain the same role** — which is why `0019` retires it with
  `REASSIGN OWNED BY … TO CURRENT_USER` first. A bare `DROP ROLE` errors `2BP01`
  and a `DROP OWNED BY` would have deleted the ML matview. Both objects now
  belong to the owner credential; the `GRANT EXECUTE` to
  `plantgeo_forecast_mv_refresher` left with that role, and the CLI no longer
  assumes it. See `../alembic/AGENTS.md` § `20260808_0019`.
- **`guard_forecast_immutable_rows`** (6 triggers) — the one immutability rule
  kept, on the append-only reference tables.
- **The `require_*` family** (3 functions, 10 triggers) — outside every dropped
  family, left in place deliberately rather than swept up by prefix analogy.
- **`publish_forecast_publication`, `validate_forecast_run`,
  `validate_forecast_feature_snapshot`, `validate_forecast_training_run`** —
  also outside every dropped family, so two of the serving view's three state
  predicates still have an in-database mover. Only receipt finalization loses
  its writer.

The strategy-selection plane itself (`20260725_0013`) still stands as storage:
label episodes, candidates, releases, receipts and
`export_strategy_label_bundle`/`strategy_label_bundle_checksum` are unchanged.
What is gone is the machinery that *enforced* the contract — the finalizers, the
parent-state insert triggers and the change guards. The `strategy_labels_v1`
export is still exact and still the trainer's input; producing it is now a CLI
obligation, and the refusal of `effect_candidate` finalization is a CLI rule
rather than a database one.

## `value_checksum` is now computed in the procedure

`forecast_iteration_value.value_checksum` was
`GENERATED ALWAYS AS (agri.forecast_iteration_value_checksum(...)) STORED`;
`0018` converts it to a plain nullable column, and forward-loads
`agri/procedures/materialize_forecast_iteration.sql` so its INSERT computes the
checksum **explicitly**.

That amendment is not cosmetic. Had the INSERT kept relying on the dropped
expression, every new row would carry NULL;
`agri/functions/forecast_iteration_receipt_checksum.sql:44` does
`string_agg(value.value_checksum, '|' ORDER BY horizon_step)`, which returns
NULL over all-NULL input, and `concat_ws` **silently omits** NULL arguments — so
the receipt digest would stop covering the forecast values while still emitting
a well-formed 64-hex string that passes every retained format CHECK. A
valid-looking digest covering nothing.

The column is left nullable (a NOT NULL would need a full-table validation pass,
outside the agreed scope). The amended procedure is now the only thing that
populates it.

## `forecast_receipt.receipt_checksum` has no writer

`agri.finalize_forecast_receipt` was the **only** object in the schema that
wrote `forecast_receipt.receipt_checksum` and set `status = 'finalized'`, and
`0018` drops it. There is no `forecast_receipt_checksum()` function to fall back
on. So **the ML serving view can never gain a new row** until a Python publisher
reproduces that digest byte-exactly: the preimage that lived at
`finalize_forecast_receipt.sql:153-168`, rendered under the `20260803_0017`
determinism pins (`TimeZone`, `DateStyle`, `IntervalStyle`,
`extra_float_digits`). Anything less produces a digest that passes the format
CHECK and matches nothing. The obligation is pre-existing — the ML lane has
never produced a row — but `0018` puts it on the critical path.

## Drop conventions, and what parity does not see

- **No `CASCADE`, ever.** Every drop names its object explicitly, function drops
  carry full argument-type signatures so a name collision cannot take out the
  wrong overload, and the statements are ordered by dependency (triggers before
  their functions, a view before its tables, a composite-returning function
  before the table whose row type it returns). A missed dependency must fail the
  migration loudly rather than quietly widen its blast radius.
- **SQL and PL/pgSQL bodies given as string literals are not tracked
  dependencies.** `DROP VIEW` succeeds against a function that INNER JOINs that
  view and leaves a runtime landmine which raises only when called — which is
  why `strategy_selection_quality_evidence` had to be retired alongside
  `v_forecast_hindcast_outcome`.
- **Roles, ownership and grants are invisible to the parity test.**
  `tools/dump_schema.py`'s `DUMP_ARGS` passes `--no-owner --no-privileges`, so no
  role, no `ALTER ... OWNER TO` and no `GRANT`/`REVOKE` appears anywhere under
  `agri/**`. Dropping a role produces **no tree diff**, and a forgotten `REVOKE`
  is not caught by `test_declarative_tree_matches_migrations`. Privilege changes
  are reviewed in the migration and asserted by behavioural tests
  (`routes/health/` and `sql/routes/health_*.sql`, the role contract tests) —
  never by regenerating.

## Assignment-time covariate layer

Revision `20260802_0016` adds the `covariate_*` function family: a pinned,
ordered 40-name covariate schema (`agri_covariates_v1`), an explicit
declared-gap registry (ERA5-Land, credential-gated), a per-(cell, UTC day,
feature) read over the house day spine, and a checksummed window manifest.
Every meteorology/drought covariate is strictly lagged, so a feature row for a
day can never contain that day's own observation; availability is always gated
on server-recorded `data_available_at`, never a simulated cutoff. See
`../alembic/AGENTS.md` for the full rationale, including why the same revision
rewrites `drought_class_daily_series`' body for a ~1000x speedup, and what that
rewrite got wrong on its first pass.

## Restore-safe `search_path` and checksum determinism pins

Revision `20260803_0017` adds a `SET search_path TO 'public', 'pg_catalog'` line
to the 21 routines that called a `public`-schema extension function unqualified,
and completes the rendering pins on `strategy_label_bundle_checksum`,
`export_strategy_label_bundle` and `forecast_hindcast_value_checksum` (the last
of which left with the hindcast plane in `20260803_0018`). No body changed, so
every `md5(prosrc)` is unchanged.

The `search_path` pins are why the schema can be restored from its own `pg_dump`
output. `forecast_iteration_value.value_checksum` *was* a stored generated
column, so `pg_restore` **recomputed** it during `COPY` under `search_path = ''`
and an unqualified `digest()` could not resolve; `20260803_0018` converts that
column to plain storage, which retires the restore-time recompute but not the
pins — every routine that still renders a checksum at write time depends on
them. See `../alembic/AGENTS.md` (`20260803_0018`) for the full derivation, and
note the shape of the fix: `ALTER ROUTINE ... SET`, never `CREATE OR REPLACE`,
because a replace that omitted the existing `SET` clauses would silently drop the
determinism pins.

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
new tables, columns, indexes and constraints, author the migration **in the
idempotent shape** required by *Layering a revision on the greenfield baseline*
above, then regenerate — the tree follows the migration, never the reverse.
"Author it normally, then regenerate" was this guide's wording until 2026-08-25
and it is the exact instruction that produces a build which double-applies its
own DDL.

## `forecast_signal_lineage_audit.ancestor_count` counts paths, not ancestors

`agri/functions/forecast_signal_lineage_audit.sql`'s recursive CTE (`20260814_0021`) enumerates
**every simple path** back from the root value, not every distinct ancestor node: a value reachable
through two different lineage edges contributes two rows to `walk`, one per path, and
`ancestor_count` is `count(*)` over those rows. Two properties follow, and neither is a bug:

- **The walk is exponential in DAG width**, bounded only by the hard `traversal_depth < 64` cutoff
  (`forecast_signal_lineage_audit.sql:41`). A signal whose recipe fans in from many parents at each
  hop can enumerate a combinatorial number of paths before the depth bound stops it: this is a
  correctness/verification function run per-audit, not a hot serving path, and the bound exists
  precisely so a pathological DAG terminates rather than hangs.
- **`ancestor_count` is a path count, not a distinct-ancestor count.** A diamond-shaped lineage
  (root depending on the same grandparent through two different parents) reports `ancestor_count =
  2` for that one grandparent, not 1. Treat it as "how many lineage paths this audit walked," never
  as "how many distinct signal values this root depends on" -- the two only coincide when every
  value in the DAG has exactly one path back to the root.
