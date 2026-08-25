---
type: reference
---

# `alembic/archive/` — applied history, off the migration path

## What this directory is

The 26 revisions `20260719_0001` … `20260825_0026` that built the `agri` schema between
2026-07-19 and 2026-08-25. They are **unedited**: byte-for-byte what was applied to production.
They are **not** revisions any more — Alembic only scans `alembic/versions/`, and this directory is
a sibling of it, not a child, so nothing here is ever discovered, imported, or executed.

`alembic/versions/` now holds exactly one revision: `20260825_0000_agri_greenfield_baseline.py`,
with `down_revision = None`.

## Why the chain was collapsed rather than extended

`20260719_0001`'s foundation preflight refuses to create schema `agri` unless the `timescaledb`
extension is already installed. That text is applied history and cannot be edited without breaking
its own content checksum. TimescaleDB was dropped from production by hand on 2026-08-25 and removed
from the reviewed extension gate (`infra/local-warehouse/enable-extensions.sql`) in the same
change, and `20260825_0026` was written to drop it inside the chain. The result was a deadlock: a
build from revision zero required an operator to install `timescaledb` purely so revision 26 could
drop it again, and `tests/test_migration_runtime_contract.py` simultaneously asserted the gate no
longer creates it. A fresh build could not be produced from the tree as it stood.

The baseline resolves that by never asking for `timescaledb` at all. A greenfield build now
converges on production's extension set (`btree_gist`, `hypopg`, `pg_buffercache`, `pgcrypto`,
`plpgsql`, `postgis`, `vector`) without the extension ever being installed. `tracking.positions`
— the only hypertable the cluster ever had, Drizzle-owned and always empty — is unaffected;
nothing in the `agri` schema was ever a hypertable.

## Why they are kept rather than deleted

Three reasons, in order of how often they bite:

1. **They are the only record of *why* each object looks the way it does.** Every revision carries a
   long docstring with the measurement, the owner ruling, or the incident that produced it. The
   declarative tree in `db/agri/**` carries the *what* and nothing else, because it is generated.
2. **Nine test modules assert against their text.** `tests/test_forecasting_migration_contract.py`,
   `tests/test_forecast_iteration_migration_contract.py`,
   `tests/test_strategy_selection_migration_contract.py`,
   `tests/test_gate_hardening_migration_contract.py`,
   `tests/test_geospatial_evidence_migration_contract.py`,
   `tests/test_signal_evaluation_migration_contract.py`,
   `tests/test_security_definer_lockdown_migration_contract.py` and
   `tests/test_migration_runtime_contract.py` read files here and assert on their contents. Those
   are contracts on *history* — they check that a revision that has been applied everywhere said
   what it was reviewed as saying — so they keep working unchanged against the archive.
3. **A database somewhere may still be mid-chain.** Stamping is the operator's tool for a database
   already at `20260817_0025` or `20260825_0026`; a database at an *earlier* revision has to be
   walked forward, and walking it forward needs these files back on a version path.

## Rules

- **Never edit a file in this directory.** They are checksummed applied history. A behaviour change
  goes in a new revision under `alembic/versions/`, authored against `db/agri/**`.
- **Never move a file back into `alembic/versions/` casually.** Doing so re-introduces a second
  Alembic head and `alembic upgrade head` stops being well-defined;
  `tests/test_alembic_baseline_contract.py` fails loudly if it happens.
- **To replay the chain on a disposable database** (the only supported reason to point Alembic at
  this directory), write a throwaway `alembic.ini` with
  `version_locations = <service root>/alembic/archive` and `version_path_separator = space`, and
  point `DATABASE_URL_SYNC` at that database. It needs `timescaledb` installed first, for the
  deadlock reason above. Never do this against production.

## Where the schema now comes from

`alembic/versions/20260825_0000_agri_greenfield_baseline.py` reads `db/manifest.sql` at apply time
and executes every object file it references, then applies the surviving `REVOKE ... FROM PUBLIC`
lockdown and the one conditional operator-role grant. See that revision's docstring for what the
`--no-owner --no-privileges` tree cannot carry and how each missing piece is supplied, and
`db/AGENTS.md` for the regeneration and parity contract.
