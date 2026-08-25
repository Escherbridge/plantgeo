"""Freeze the readiness contract: the extensions and the migration revision this build requires.

Both names here are governance assertions, not defaults. ``tests/test_health_readiness.py``
reads them back and compares them to the live database, and ``queries.py`` renders
``REQUIRED_EXTENSIONS`` into the SQL row-literal the probe statement compares against -- so an
edit here changes what /ready refuses to serve on. See ``routes/AGENTS.md`` for why the probe
SQL lives in ``sql/routes/health_*.sql``, and for what revision ``20260808_0019`` removed from
this file: the whole forecast-role and calling-login privilege matrix, which governed roles that
had no members and no DSN.

``REQUIRED_EXTENSIONS`` is re-exported, not redefined: the canonical tuple is
``agri_data_service.db.extensions``, which the greenfield baseline's preflight and
``db/tools/verify_stamp_target.py`` also read. There were three hand-kept copies before 2026-08-25.
"""

from agri_data_service.db.extensions import REQUIRED_EXTENSIONS

# Bumped past BOTH 20260816_0024 and 20260817_0025 at once: neither revision bumped it, and
# health_migration.sql demands exact equality, so /ready reported migration=false against a
# correctly-migrated production database. Only the agri_db-gated
# test_expected_alembic_revision_matches_migrated_head_database compares this to a live head, and
# that gate had been running dark. Every new revision bumps this line.
#
# 2026-08-25: now the greenfield baseline, after the 26-revision chain collapsed into it. THIS PIN
# MOVING IS WHAT MAKES THE PRODUCTION STAMP MANDATORY: health_migration.sql demands exact equality,
# so a deploy carrying this build against a database still reading `20260817_0025` reports
# migration=false and /ready refuses to come up. Stamp first, deploy second.
EXPECTED_ALEMBIC_REVISION = "20260825_0000"

__all__ = ["EXPECTED_ALEMBIC_REVISION", "REQUIRED_EXTENSIONS"]
