"""Drop timescaledb and timescaledb_toolkit so a fresh build converges on production.

Revision ID: 20260825_0026
Revises: 20260817_0025

WHAT THIS CLOSES. Production dropped both `timescaledb` and `timescaledb_toolkit` from the live
database on 2026-08-25, by hand, ahead of this revision. `tracking.positions` -- the ONLY
hypertable either extension ever produced, and ALWAYS empty (0 rows, 0 chunks, 40 kB, every
measurement since it was created) -- survived that drop as a plain table, because TimescaleDB's
own extension-drop hook un-hypertables its dependants rather than dropping them; no data was lost
because there was never any data to lose. No continuous aggregate was ever built on this database,
and none ever could have been: a continuous aggregate can only exist on a hypertable, and the one
hypertable this database ever had never held a signal-plane row for one to aggregate.
`agri.signal_observation` was never converted and stays exactly the plain table
`20260816_0024`'s own docstring already measured it as (`timescaledb_information.hypertables` held
exactly one row, and it was not this table). Production extensions are now exactly: `btree_gist`,
`hypopg`, `pg_buffercache`, `pgcrypto`, `plpgsql`, `postgis`, `vector`.

WHY A MIGRATION, NOT JUST A PRODUCTION FACT. `20260719_0001`'s foundation preflight -- applied
history, and not edited by this revision -- still requires `timescaledb` to be INSTALLED before it
will create the `agri` schema at all: `('timescaledb'::text)` is one of its four required rows, and
the preflight raises rather than proceeding if that row's `pg_extension` join comes back empty. A
fresh database build therefore still needs an operator to install `timescaledb` through the
reviewed manual extension gate (outside this service, and not this subtree's to change) before
`0001` will run at all -- `0001`'s text is immutable and cannot be edited to relax that requirement
without breaking its content checksum. This revision is what a fresh build needs to run
immediately AFTER `0001` succeeds: it removes the extension `0001` demanded, so a database built
from revision zero today ends up with the same extension set production actually runs, rather than
permanently carrying one more installed extension than production has forever after.

ORDER AND CASCADE, DELIBERATELY ASYMMETRIC. `timescaledb_toolkit` is dropped first, and WITHOUT
CASCADE: it depends on `timescaledb`, nothing depends on it, and letting it fail loudly if it
cannot drop cleanly on its own is a cheap assertion that no unexpected object was ever built on top
of it. `timescaledb` is dropped second, and CASCADE is required there because the extension owns
catalog objects (the `_timescaledb_catalog`/`_timescaledb_internal` schemas and everything
`timescaledb_information.hypertables` reads) that a plain `DROP EXTENSION` cannot leave behind --
the same CASCADE production's own manual drop used, and what converts `tracking.positions` back to
a plain table rather than dropping it.

IDEMPOTENT AND FORWARD-ONLY. Both statements carry `IF EXISTS`, so re-running this revision against
a database that never had either extension installed, or one that already went through this exact
drop by hand as production did, is a no-op rather than an error. There is no `downgrade()`:
dropping these extensions is exactly the operational decision production already made outside any
migration, `DROP EXTENSION ... CASCADE` retains no catalog state to reconstruct a hypertable from
even for the one that was always empty, and reinstalling the extension is a package-availability
question for the target environment, not a mechanical reverse of two DDL statements. Roll back by
restoring a verified backup and deploying the prior application version, matching the forward-only
posture `alembic/AGENTS.md` already documents for the schema `0001` creates.
"""

from collections.abc import Sequence

from alembic import op

revision = "20260825_0026"
down_revision = "20260817_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# No CASCADE: nothing in this database depends on timescaledb_toolkit, and a bare DROP failing
# would mean something unexpected was built on it -- worth surfacing, not silently overridden.
_DROP_TIMESCALEDB_TOOLKIT = "DROP EXTENSION IF EXISTS timescaledb_toolkit"

# CASCADE here mirrors production's own manual drop on 2026-08-25: the extension owns catalog
# objects a plain DROP cannot leave behind, and its drop hook is what un-hypertables
# tracking.positions rather than dropping it.
_DROP_TIMESCALEDB = "DROP EXTENSION IF EXISTS timescaledb CASCADE"


def upgrade() -> None:
    op.execute(_DROP_TIMESCALEDB_TOOLKIT)
    op.execute(_DROP_TIMESCALEDB)


def downgrade() -> None:
    raise NotImplementedError(
        "Reinstalling timescaledb/timescaledb_toolkit is not a mechanical reverse of two DROP "
        "EXTENSION statements: CASCADE retains no catalog state to rebuild a hypertable from, and "
        "package availability on the target environment is an operator question this migration "
        "cannot answer. Restore a verified backup and deploy the prior application version instead."
    )
