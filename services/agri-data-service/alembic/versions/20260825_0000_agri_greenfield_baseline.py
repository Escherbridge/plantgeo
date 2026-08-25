"""Build the whole agri schema from the declarative tree in one forward-only baseline revision.

Revision ID: 20260825_0000
Revises: (none -- this is the root)

WHAT THIS REPLACES. Revisions ``20260719_0001`` through ``20260825_0026`` are applied history and
now live, unedited, in ``alembic/archive/``. They are no longer on the migration path. This
revision is the only revision ``alembic/versions/`` contains, and applying it to an empty database
that has the reviewed extensions produces the same ``agri`` schema the 26-revision chain produced.

WHY A COLLAPSE WAS FORCED. ``20260719_0001``'s foundation preflight requires the ``timescaledb``
extension to be INSTALLED before it will create the ``agri`` schema at all, and its text is
immutable applied history that cannot be edited without breaking its own content checksum.
TimescaleDB was dropped from production by hand on 2026-08-25 and removed from the reviewed
extension gate (``infra/local-warehouse/enable-extensions.sql``) in the same change, so a build
from revision zero needed an operator to install an extension purely so ``20260825_0026`` could
drop it again three seconds later. That deadlock is why the chain is collapsed rather than
extended: this baseline never asks for ``timescaledb``, so a fresh build converges on production's
extension set without ever installing it. Nothing here un-hypertables anything -- the only
hypertable the cluster ever had was ``tracking.positions`` (Drizzle-owned, always empty, plain
since the extension drop), and this schema never had one.

WHAT IT EXECUTES, AND WHY THAT IS THE HONEST SOURCE. ``db/manifest.sql`` and the ``db/agri/**``
tree it orders are generated from the migration head by ``db/tools/regenerate.py`` and guarded
byte-for-byte by ``tests/test_declarative_schema_parity.py``. That tree IS the schema the chain
produced, in pg_dump dependency order, so replaying it is a faithful rebuild rather than a
hand-transcribed one. This revision reads the manifest at apply time and executes each referenced
object file, so the baseline can never drift from the reviewed tree: there is one definition of
every object, not two.

WHAT THE TREE CANNOT CARRY, AND HOW THIS REVISION SUPPLIES IT. ``db/tools/dump_schema.py`` dumps
with ``--no-owner --no-privileges``, so the tree holds no ACLs. The archived chain's privilege
layer reduces to three surviving facts, reproduced verbatim below:

* ``REVOKE ... FROM PUBLIC`` across the schema. ``20260722_0008`` already applied
  ``REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA agri FROM PUBLIC`` and every later revision
  hand-listed the routines it added; the schema-wide form here is that same idiom applied once,
  after every object exists, and additionally covers PROCEDURES (which ``ALL FUNCTIONS`` does not).
* ``REVOKE CREATE ON SCHEMA agri FROM PUBLIC`` (``20260725_0012``).
* The conditional operator-role grant from ``20260723_0010``: read-only ``SELECT`` on
  ``agri.forecast_input_recorded_at`` for whichever of five operator-provisioned roles exist on the
  target, and no write privilege. Those roles are created outside Alembic, so the ``IF EXISTS``
  guard is load-bearing, not defensive.

MEASURED, NOT ASSUMED. Both builds were produced on a disposable PostgreSQL 16.14 database on
2026-08-25 -- the archived chain replayed from ``alembic/archive/`` into one database, this revision
applied to another -- and compared object by object. Same 235 object files, same 90 relations, same
59 routines, every object owned by the migrating role in both, identical extension sets
(``pgcrypto``, ``plpgsql``, ``postgis``, ``vector``), identical schema ACL. The DDL differs on 74
lines across 41 files, every one of them a PostgreSQL reparse normalisation with identical string
literals: ``= ANY ((ARRAY[...])::text[])`` re-emitted as ``= ANY (ARRAY[(...)::text])`` (66 lines),
the same for ``<> ALL`` (6), and ``((A AND B) AND C)`` flattened to ``(A AND B AND C)`` (2).

The privilege layer differs in exactly one measured way, and it goes the safe direction. The chain
leaves three routines still executable by ``PUBLIC`` -- ``forecast_candidate_evaluation_receipt_checksum``,
``forecast_derived_signal_value_checksum`` and ``forecast_quality_policy_contract_v2``, added by
``20260814_0021`` and ``20260801_0014``, neither of which revoked them. The schema-wide
``ALL ROUTINES`` form below covers them, so a baseline-built database has zero PUBLIC-executable
routines against production's three. Neither database grants ``PUBLIC`` ``USAGE`` on schema ``agri``,
so those three were never reachable and this closes a gap in defence-in-depth rather than a live
hole. The blanket revoke also materialises an explicit owner ACL on 22 relations where the chain
left ``relacl`` NULL; NULL means *owner has everything, nobody else has anything*, which is exactly
what ``{plantgeo_owner=arwdDxt/plantgeo_owner}`` spells out, so no privilege moves.

No roles are created. Every role Alembic ever created -- ``plantgeo_intervention_guard_owner``,
``plantgeo_forecast_input_recorder_owner``, ``plantgeo_forecast_mv_refresh_owner``,
``plantgeo_release_lineage_guard_owner`` -- was retired by ``20260803_0018`` and
``20260808_0019``, which reassigned their objects to the migrating role first. At head every
``agri`` object is owned by whoever applies the migration, which is exactly what replaying the
tree produces. This matches the 2026-08-03 owner ruling (*no custom DB roles*).

WHAT A CHECK CONSTRAINT COSTS A NON-OWNER WRITER, AND WHY THE GRANT BLOCK IS NOT OPTIONAL. Four
``agri`` routines are invoked from ``CHECK`` constraints: ``forecast_quantiles_valid``
(``forecast_receipt``, ``forecast_quality_policy``), ``expert_label_envelope_valid``,
``forecast_derived_signal_value_checksum`` and
``forecast_candidate_evaluation_receipt_checksum``. A CHECK is evaluated with the **writer's**
privileges, not the table owner's, so after the schema-wide ``REVOKE EXECUTE ON ALL ROUTINES`` a
non-owner role holding ``USAGE`` + ``INSERT`` gets ``ERROR: permission denied for function ...`` on
a perfectly valid row. Measured on a baseline-built database 2026-08-25. The archived chain has the
same hole for two of the four (``20260722_0008``'s ``ALL FUNCTIONS`` revoke caught
``forecast_quantiles_valid``; ``20260814_0022:394`` hand-revoked
``expert_label_envelope_valid`` in the revision that added it); the other two survived only
because no revision ever revoked them.
``_CHECK_CONSTRAINT_EXECUTE_GRANTS_SQL`` below closes it for all of them by reading
``pg_constraint``/``pg_depend`` rather than a hand-list, so a CHECK added by a future revision is
covered on the next fresh build without anyone remembering this paragraph.

FORWARD-ONLY. There is no ``downgrade()``. Rolling back a baseline means dropping the schema,
which is a restore-from-backup decision, not a migration.

THE RULE EVERY REVISION LAYERED ON THIS ONE MUST OBEY. This revision executes the CURRENT
``db/agri/**`` tree, and ``db/tools/regenerate.py`` rebuilds that tree *from the migration head*.
So the moment a follow-on revision creates an object and the tree is regenerated, the tree contains
that object too -- and the NEXT build from empty runs this baseline (which creates it from the
tree) and then the follow-on revision (which creates it again). Every revision that comes after
this one must therefore be **idempotent against a tree that already contains its own changes**:
``CREATE TABLE IF NOT EXISTS``, ``ADD COLUMN IF NOT EXISTS``, ``CREATE INDEX IF NOT EXISTS``, a
``NOT EXISTS`` probe around ``ADD CONSTRAINT``, and ``load_object_sql(..., or_replace=True)`` or
drop-then-create for programmable objects. ``db/AGENTS.md`` (*Layering a revision on the greenfield
baseline*) is the full statement of the rule;
``tests/test_alembic_baseline_forward_rehearsal.py`` lints it without a database and proves it on
one.
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa

from agri_data_service.db.extensions import REQUIRED_EXTENSIONS
from agri_data_service.db.sql_objects import SCHEMA_ROOT, load_object_sql
from alembic import op

revision: str = "20260825_0000"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "agri"
MANIFEST_PATH = SCHEMA_ROOT.parent / "manifest.sql"

# Matches a psql `\i agri/<kind>/<name>.sql` include line in db/manifest.sql.
_MANIFEST_INCLUDE = re.compile(r"^\\i\s+(agri/\S+\.sql)\s*$", re.MULTILINE)

# Rendered from agri_data_service.db.extensions.REQUIRED_EXTENSIONS -- the one definition /ready,
# this preflight and db/tools/verify_stamp_target.py all read. Order is the tuple's, and the query
# sorts the missing names anyway, so the rendered SQL is deterministic.
_REQUIRED_EXTENSION_VALUES_SQL = ",\n".join(f"            ('{name}'::text)" for name in REQUIRED_EXTENSIONS)

_REQUIRED_EXTENSION_PREFLIGHT_SQL = f"""
DO $baseline_extension_preflight$
DECLARE
    missing_extensions text;
BEGIN
    SELECT string_agg(required.extname, ', ' ORDER BY required.extname)
    INTO missing_extensions
    FROM (
        VALUES
{_REQUIRED_EXTENSION_VALUES_SQL}
    ) AS required(extname)
    LEFT JOIN pg_extension installed ON installed.extname = required.extname
    WHERE installed.extname IS NULL;

    IF missing_extensions IS NOT NULL THEN
        RAISE EXCEPTION USING
            ERRCODE = '55000',
            MESSAGE = format(
                'Agri baseline preflight failed: missing installed PostgreSQL extension(s): %s.',
                missing_extensions
            ),
            HINT = 'An operator must first confirm package availability and run the reviewed extension gate '
                '(infra/local-warehouse/enable-extensions.sql). This migration never creates extensions.';
    END IF;
END
$baseline_extension_preflight$;
"""

# pg_dump's own preamble, replicated so the tree loads under the settings it was captured with.
# check_function_bodies is the load-bearing one: manifest order creates some routines before the
# relations their bodies read, exactly as a pg_restore would. These four are the same four
# db/tools/split_schema.py writes into db/manifest.sql's preamble, and
# test_alembic_baseline_contract.py::test_the_capture_settings_are_the_manifests_own_preamble
# parses that generated file and compares -- so the two copies cannot drift silently.
_CAPTURE_SETTINGS_SQL = (
    "SET check_function_bodies = false",
    "SET default_tablespace = ''",
    "SET default_table_access_method = heap",
    "SELECT pg_catalog.set_config('search_path', '', false)",
)

# The archived chain's whole surviving PUBLIC lockdown, applied once after every object exists.
# ALL ROUTINES rather than ALL FUNCTIONS: PostgreSQL treats procedures as a separate class here and
# 20260723_0010 had to revoke its two procedures by name for that reason.
_REVOKE_FROM_PUBLIC_SQL = (
    "REVOKE CREATE ON SCHEMA agri FROM PUBLIC",
    "REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA agri FROM PUBLIC",
    "REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA agri FROM PUBLIC",
    "REVOKE EXECUTE ON ALL ROUTINES IN SCHEMA agri FROM PUBLIC",
)

# Verbatim from 20260723_0010: operator-provisioned roles get SELECT and nothing else on the
# forecast input ledger. None of these roles is created by Alembic, so each grant is conditional.
_OPERATOR_ROLE_GRANTS_SQL = """
DO $baseline_operator_role_grants$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'plantgeo_local_developer',
        'plantgeo_loader',
        'plantgeo_forecast_mv_refresher',
        'plantgeo_forecast_refresh_operator',
        'plantgeo_local_viewer'
    ]
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format(
                'REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON '
                'agri.forecast_input_recorded_at FROM %I',
                role_name
            );
            EXECUTE format(
                'GRANT SELECT ON agri.forecast_input_recorded_at TO %I',
                role_name
            );
        END IF;
    END LOOP;
END
$baseline_operator_role_grants$;
"""

# A CHECK constraint is evaluated with the WRITER's privileges, so the schema-wide
# REVOKE EXECUTE above locks a non-owner writer out of any table whose CHECK calls an agri routine.
# Discovered from pg_constraint/pg_depend rather than hand-listed: a CHECK added by a later
# revision is covered the next time this runs, and a routine that stops being CHECK-invoked stops
# being granted. Same conditional operator-role list, same reason the IF EXISTS is load-bearing.
_CHECK_CONSTRAINT_EXECUTE_GRANTS_SQL = """
DO $baseline_check_constraint_execute_grants$
DECLARE
    role_name text;
    routine_signature text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'plantgeo_local_developer',
        'plantgeo_loader',
        'plantgeo_forecast_mv_refresher',
        'plantgeo_forecast_refresh_operator',
        'plantgeo_local_viewer'
    ]
    LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            FOR routine_signature IN
                SELECT DISTINCT
                    format('%I.%I(%s)', 'agri', routine.proname, pg_get_function_identity_arguments(routine.oid))
                FROM pg_constraint constraint_row
                JOIN pg_depend dependency
                    ON dependency.objid = constraint_row.oid
                    AND dependency.classid = 'pg_constraint'::regclass
                JOIN pg_proc routine
                    ON routine.oid = dependency.refobjid
                    AND dependency.refclassid = 'pg_proc'::regclass
                WHERE constraint_row.contype = 'c'
                  AND constraint_row.connamespace = 'agri'::regnamespace
                  AND routine.pronamespace = 'agri'::regnamespace
                ORDER BY 1
            LOOP
                EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO %I', routine_signature, role_name);
            END LOOP;
        END IF;
    END LOOP;
END
$baseline_check_constraint_execute_grants$;
"""


def manifest_object_paths() -> list[str]:
    """Every declarative object file the manifest includes, in manifest (dependency) order."""
    manifest = MANIFEST_PATH.read_text(encoding="utf-8")
    return [match.removeprefix("agri/") for match in _MANIFEST_INCLUDE.findall(manifest)]


def upgrade() -> None:
    # Online-only, like every DO-block revision in the archive: it reads the live catalogue for the
    # extension preflight and the operator-role probe, so `alembic upgrade --sql` cannot render it.
    connection = op.get_bind()
    original_search_path = connection.execute(sa.text("SHOW search_path")).scalar_one()

    # op.execute (i.e. sqlalchemy.text) rather than exec_driver_sql, which is what the archived
    # revisions used and what these exact files were loaded with before. It matters because the tree
    # is generated DDL carrying plpgsql bodies full of `%I`/`%s` format specifiers: the psycopg2
    # dialect doubles and re-collapses `%` correctly, whereas exec_driver_sql on SQLAlchemy 2.0.51
    # hands psycopg2 an empty parameter mapping and raises. The residual risk of text() -- a literal
    # `:identifier` being read as a bind parameter -- fails loudly with "a value is required for
    # bind parameter" rather than silently rewriting DDL, and the whole tree is loaded here on every
    # fresh build, so a file that ever acquired one could not reach production unnoticed.
    op.execute(_REQUIRED_EXTENSION_PREFLIGHT_SQL)

    for statement in _CAPTURE_SETTINGS_SQL:
        op.execute(statement)

    for relative_path in manifest_object_paths():
        op.execute(load_object_sql(relative_path))

    op.execute("SET check_function_bodies = true")
    connection.execute(
        sa.text("SELECT pg_catalog.set_config('search_path', :search_path, false)"),
        {"search_path": original_search_path},
    )

    for statement in _REVOKE_FROM_PUBLIC_SQL:
        op.execute(statement)
    op.execute(_OPERATOR_ROLE_GRANTS_SQL)
    # After the blanket revoke, never before it: this hands back exactly the EXECUTE a writer needs
    # to satisfy a CHECK constraint, and nothing else.
    op.execute(_CHECK_CONSTRAINT_EXECUTE_GRANTS_SQL)


def downgrade() -> None:
    raise NotImplementedError(
        "The greenfield baseline has no reverse: undoing it means dropping schema agri and every "
        "row in it. Restore a verified backup and deploy the prior application version instead."
    )
