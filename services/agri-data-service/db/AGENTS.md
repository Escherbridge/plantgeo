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
  `AGRI_TEST_DATABASE_URL` to a head-migrated disposable database, see
  `tests/conftest.py`) — `pg_dump`s that database, re-splits it, and asserts
  **byte-identical** to the committed tree.

Proven separately at build time: applying `manifest.sql` to an empty database
rebuilds an object inventory identical to the migration-built database; the
parity test derives the current per-object counts instead of pinning a stale
inventory in this guide.

> Note: rebuilding from the tree and re-dumping shows cosmetic differences in a
> few `CHECK` expressions (`= ANY((ARRAY[...])::text[])` vs
> `= ANY(ARRAY[(...)::text])`). PostgreSQL re-normalises casted-`ANY`-array
> expressions on reparse; the schemas are semantically identical. The tree is
> generated from the migration output, so the parity test (which compares
> against the migration dump, not a rebuild) is exact.

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

## Hindcast knowledge pin and quality gates

Revision `20260801_0014` stores the actuals/knowledge horizon on
`forecast_hindcast_run.actual_knowledge_as_of` at first finalization and pins
every later read to it, so a finalized receipt re-verifies identically no matter
when the audit runs. `hindcast_v3` redefines `coverage_fraction` as horizon
completeness (ideal horizon steps with an actual at that pinned horizon, over
`horizon_steps`) and wires
`forecast_quality_policy.min_interval_coverage_fraction` into the pass decision;
`hindcast_v1`/`hindcast_v2` receipts keep their exact preimages and their old
gate. Two reusable predicates,
`agri.strategy_selection_cutoff_violation` and
`agri.strategy_selection_quality_evidence`, are the single definitions of the
corrected as-of rule and of the "backing hindcast passed its policy"
requirement; the finalizer, the audit flagging pass, and the tests all call
them. See `../alembic/AGENTS.md` for the full rationale.

## Strategy-selection evidence

Revision `20260725_0013` introduces an append-only treatment/control label and
selection-receipt plane. Raw intervention facts remain in
`intervention_evidence_input`; a `strategy_label_episode` may call rows a
baseline and outcome only by binding them to an approved outcome definition,
explicit arm, subject, availability cutoff, and finalized label-release
checksum. Selection candidates are immutable before receipt finalization.
Only the finalizers may move a label release from `staging` to `validated` or a
selection receipt from `staging` to `finalized`.

`feasibility_candidate` and `effect_candidate` are distinct database states,
but revision `0013` deliberately refuses every `effect_candidate`
finalization. A later migration may open that path only after it persists and
verifies cluster-bootstrap uncertainty, placebo and negative-control tests,
and a strictly positive held-out lower confidence bound for
`best - second_best`. Until then, evaluation and feasibility receipts may be
finalized; an effect request must remain staging or be replaced by a durable
abstention.

Validated labels are reproducible trainer inputs, not merely normalized
targets. The release pins the ordered feature-name schema and the outcome's
smallest meaningful effect. Every episode pins its cohort, assignment time,
assignment-time covariate vector and checksum, covariate availability, raw
baseline/outcome evidence, and data availability. The private
`export_strategy_label_bundle` function emits exactly `strategy_labels_v1`,
ordered by episode key, only after the release and server-computed outcome
checksum validate. The export includes the finalized label-release checksum;
`strategy_label_bundle_checksum` hashes the exact JSONB export text without a
self-referential field. The trainer hashes the exact UTF-8 JSON text after
removing only surrounding file whitespace. Strategy model output metadata
must repeat both checksums, and the training row repeats the release checksum.

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
