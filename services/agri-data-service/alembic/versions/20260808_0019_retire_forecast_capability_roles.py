"""Retire the forecast capability-role family; applications connect with the single owner credential.

Revision ID: 20260808_0019
Revises: 20260803_0018

Owner ruling, 2026-08-08: "I don't want to maintain any role management code, or
the least amount possible." The forecast capability-role family goes -- not
widened, not replaced, retired -- and every application connects with the one
owner credential.

**Lineage of the ruling.** 2026-08-03 recorded "no custom DB roles" as settled,
and revision ``20260803_0018`` acted on it for the three owner roles it could
reach. On the morning of 2026-08-08,
``docs/reports/migration-decision-packet-2026-08-08.md`` put a training role to
the owner as its recommended Option A; the answer was *no per-lane trainer
role*, which turned into an Option B revision that widened
``plantgeo_forecast_writer`` instead. This revision supersedes that file
entirely: the owner's follow-up ruling is that the family itself goes, so
widening a member of it was solving the wrong problem.

**The evidence that these roles governed nobody.** Verified on a head-migrated
database before this revision was written:

* All four capability roles (``writer``, ``publisher``, ``reader``,
  ``mv_refresher``) are ``NOLOGIN`` bundles with **zero members**. No login
  anywhere inherits or can ``SET ROLE`` to any of them.
* No DSN in this repository or in any deployment authenticates as one of them.
* All four **lack ``USAGE`` on schema ``agri``** at head: they reached the schema
  only through owner roles that ``20260803_0018`` retired, so every grant they
  still hold has been unreachable since that revision. A privilege you cannot
  reach is not a control; it is a catalogue entry that makes a readiness probe
  look meaningful.

Separation of duties needs two parties. Here the researcher, the operator and
the DBA are one person holding one credential -- the same finding
``plans/checksum-layer-audit-2026-08-03.md`` §3.1 made about the tamper-evidence
triggers, reaching the same conclusion about the roles built on the same
assumption.

**What this revision does.** For each of the five roles, in this order:

1. ``REASSIGN OWNED BY <role> TO CURRENT_USER``. Load-bearing for exactly one of
   them: ``plantgeo_forecast_mv_refresh_owner`` owns
   ``agri.mv_forecast_ml_daily_serving``, its unique index
   ``uq_mv_forecast_ml_daily_serving_identity``, and
   ``agri.refresh_forecast_ml_daily_serving()``. A bare ``DROP ROLE`` would fail
   ``2BP01`` and a bare ``DROP OWNED BY`` would **delete the ML matview**, which
   is precisely why ``20260803_0018`` left this role standing. Reassignment is
   the same handover that revision used for the seven ``record_*`` writers. For
   the other four the statement is a no-op, and it is applied uniformly anyway so
   that an object owned by a capability role in some database this revision has
   not seen is handed over rather than dropped.
2. ``DROP OWNED BY <role>``. After the reassignment this removes privileges, not
   objects: every grant the role holds in *this* database, including the
   0014-0016-era grants, without touching a single object.
3. ``DROP ROLE <role>``.

Roles are cluster-wide while ``REASSIGN OWNED`` and ``DROP OWNED`` are
database-local, so -- exactly as in ``20260803_0018`` -- each teardown traps
``dependent_objects_still_exist`` and raises a NOTICE. The database-local half
always completes; the role disappears once the last database on the cluster has
replayed this revision. Any other error still aborts.

**What deliberately survives.**

*``plantgeo_loader``.* Untouched, and it is not in the list. It is a real
``LOGIN`` role that the deployed Railway cron ingest and the in-flight local
archive walks **authenticate as right now**; dropping it would break running
production jobs. It survives as a plain login until those DSNs are rotated to
the owner credential. No code in this repository manages, provisions or requires
it any more -- the role-name assertion in
``Settings.require_local_source_loader_database_url`` and the
``infra/local-warehouse/create-loader-role.sql`` bootstrap script are removed in
the same commit -- so ``LOCAL_SOURCE_LOADER_DATABASE_URL`` keeps working
unchanged with that login and will keep working with any other.

*The entire ML data plane.* No table, function, view, index or materialized view
is dropped or altered here. ``agri.mv_forecast_ml_daily_serving`` and
``agri.refresh_forecast_ml_daily_serving()`` change owner and nothing else. The
refresher stays ``SECURITY DEFINER``: after the reassignment its definer is the
migrating owner credential, which is also the caller, so the bit is now inert --
and a non-concurrent ``REFRESH`` still requires matview ownership, which the
handover preserves. ``agri_data_service.cli`` drops its
``SET LOCAL ROLE plantgeo_forecast_mv_refresher`` in the same commit because
that role no longer exists.

*The ``20260802_0016`` ``REVOKE EXECUTE ... FROM PUBLIC``.* Deliberately not
re-granted. Under a single owner credential the caller is the function owner, so
the covariate functions remain owner-callable, which is everyone who connects.
Re-granting to ``PUBLIC`` would widen the surface for no operational gain, and
the readiness probe that used to assert the absence of ``PUBLIC`` EXECUTE is
retired with the rest of the role contract.

**No ``downgrade()``.** Recreating five ``NOLOGIN`` roles is easy; recreating the
grant matrix they held across revisions 0014, 0015, 0016 and 0018 is a
reconstruction, and the build that consumed that matrix -- the readiness probe in
``routes/health/`` -- no longer exists in this tree. Roll back by deploying the
previous build, not by re-inventing a privilege matrix nothing reads.
"""

from collections.abc import Sequence

from alembic import op

revision = "20260808_0019"
down_revision = "20260803_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The five forecast roles, hardcoded rather than discovered: a revision must apply
# identically to every database that replays it, so it may not branch on the catalogue
# it finds. `plantgeo_forecast_mv_refresh_owner` leads because it is the only one that
# owns anything, so the ownership handover is the first thing this revision does.
# `plantgeo_loader` is absent on purpose; see the docstring.
FORECAST_ROLES_TO_RETIRE: tuple[str, ...] = (
    "plantgeo_forecast_mv_refresh_owner",
    "plantgeo_forecast_writer",
    "plantgeo_forecast_publisher",
    "plantgeo_forecast_reader",
    "plantgeo_forecast_mv_refresher",
)

# REASSIGN before DROP OWNED, because `plantgeo_forecast_mv_refresh_owner` owns the ML
# matview, its unique index and the refresher function -- DROP OWNED without the
# reassignment would delete them. Roles are cluster-wide but both statements are
# database-local, so a role still holding objects or grants in a database that has not
# replayed this revision survives the DROP with a NOTICE instead of aborting it.
_RETIRE_FORECAST_ROLE = r"""
DO $retire_forecast_role$
DECLARE
    target_role CONSTANT name := '{role}';
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = target_role
    ) THEN
        RETURN;
    END IF;

    EXECUTE format('REASSIGN OWNED BY %I TO CURRENT_USER', target_role);
    EXECUTE format('DROP OWNED BY %I', target_role);

    BEGIN
        EXECUTE format('DROP ROLE %I', target_role);
    EXCEPTION WHEN dependent_objects_still_exist THEN
        RAISE NOTICE
            '% still owns objects or holds grants in another database on this '
            'cluster; it is retired here and disappears once every database has '
            'replayed 20260808_0019',
            target_role;
    END;
EXCEPTION WHEN insufficient_privilege THEN
    -- Attribution, not recovery: alembic wraps the upgrade in one transaction, so
    -- nothing is half-retired -- but a bare 42501 from a five-role loop names
    -- neither the role nor the requirement. REASSIGN OWNED needs membership in
    -- the target role; DROP ROLE needs superuser, or CREATEROLE with ADMIN
    -- OPTION on it (PostgreSQL 16+). The bootstrap owner and the migration
    -- runner satisfy both in every environment this line ships to; anything
    -- else should fail with its name on the error.
    RAISE EXCEPTION
        'retiring % as % failed: %. REASSIGN OWNED requires membership in the '
        'target role, and DROP ROLE requires superuser or CREATEROLE with '
        'ADMIN OPTION on it; rerun 20260808_0019 as a role that has them.',
        target_role, current_user, SQLERRM;
END
$retire_forecast_role$;
"""


def upgrade() -> None:
    for role in FORECAST_ROLES_TO_RETIRE:
        op.execute(_RETIRE_FORECAST_ROLE.format(role=role))


def downgrade() -> None:
    raise NotImplementedError(
        "Forward-only, like every revision in this line. Recreating five NOLOGIN "
        "roles is trivial; reconstructing the grant matrix they accumulated across "
        "0014, 0015, 0016 and 0018 is not, and the readiness contract that consumed "
        "that matrix is deleted in the same commit. Roll back by deploying the "
        "previous build."
    )
