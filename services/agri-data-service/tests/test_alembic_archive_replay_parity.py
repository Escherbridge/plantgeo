"""Replay `alembic/archive/` and diff it against a baseline-built database. The check parity lost.

WHAT `test_declarative_schema_parity` STOPPED PROVING ON 2026-08-25. It dumps a head-migrated
database and compares the text to `db/agri/**`. Before the collapse the head was 26 hand-written
revisions, so that comparison was an independent second derivation. Now the head is
`20260825_0000`, which BUILDS the schema by executing `db/agri/**` -- so parity asserts
`dump(replay(T)) == T`, which holds for any round-trippable `T`. Demonstrated by the reviewer:
delete a CHECK from a tree file, re-run `db/tools/regenerate.py`, and parity is green. A corrupted
tree passes.

What parity still catches is real and worth keeping: DDL the server does not re-emit verbatim after
a parse (that is how the `= ANY ((ARRAY[...])::text[])` renormalisation was found), a manifest that
references a missing file, a hand-edit to a tree file that is never regenerated, and any drift
between the committed tree and what a fresh build actually produces. What it can no longer catch is
the tree having drifted from the INTENT of the 26 archived revisions -- because they are no longer
on the migration path and nothing re-derives the schema from them.

This module is that missing derivation, made runnable instead of described. It is marked and opt-in
because it is expensive and needs something the ordinary sweep must not have: `timescaledb`
installed, which `20260719_0001`'s preflight demands and which the whole collapse exists to avoid.

    AGRI_ARCHIVE_REPLAY_ADMIN_DSN=postgresql://...@127.0.0.1:5442/postgres uv run pytest \
        tests/test_alembic_archive_replay_parity.py -q

HOW THE COMPARISON IS SCORED, AND WHAT IT ADMITS. Two legs. The catalogue leg is EXACT: relations
and their kinds, every column's type/nullability/default, routine bodies by `md5(prosrc)`, index
definitions, trigger bindings and foreign keys must match identically -- no normalisation, no
allowance. The expression leg covers CHECK constraint definitions, where the two databases
genuinely store different node trees for the same predicate, because the baseline re-parses text
the chain's own parser emitted. Those are scored by `agri_data_service.db.schema_diff`
`reparse_equivalent` -- the same rule `db/tools/verify_stamp_target.py` gates a production stamp
on, stated once so the two cannot drift. It is deliberately weaker than byte equality and its own
docstring says exactly what it therefore misses.
`test_the_scoring_rule_rejects_a_difference_it_was_not_written_to_admit` below is its negative
control and needs no database.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import psycopg2
import pytest

from agri_data_service.db.schema_diff import reparse_equivalent
from tests.test_alembic_baseline_forward_rehearsal import (
    _alembic_upgrade_head,
    _enable_reviewed_extensions,
    _rehearsal_alembic_ini,
    _run_maintenance,
    _swap_database,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_ARCHIVE = _SERVICE_ROOT / "alembic" / "archive"

ADMIN_DSN_ENV = "AGRI_ARCHIVE_REPLAY_ADMIN_DSN"
_CHAIN_DATABASE = "agri_archive_replay_chain"
_BASELINE_DATABASE = "agri_archive_replay_baseline"

_INVENTORY_QUERIES = {
    "relations": (
        "SELECT relname, relkind, relrowsecurity, relispartition FROM pg_class "
        "WHERE relnamespace = 'agri'::regnamespace AND relkind IN ('r','p','v','m','S','i') ORDER BY 1, 2"
    ),
    "columns": (
        "SELECT cls.relname, att.attname, format_type(att.atttypid, att.atttypmod), att.attnotnull, "
        "att.attidentity, pg_get_expr(def.adbin, def.adrelid) "
        "FROM pg_attribute att JOIN pg_class cls ON cls.oid = att.attrelid "
        "LEFT JOIN pg_attrdef def ON def.adrelid = att.attrelid AND def.adnum = att.attnum "
        "WHERE cls.relnamespace = 'agri'::regnamespace AND att.attnum > 0 AND NOT att.attisdropped "
        "ORDER BY 1, 2"
    ),
    "routines": (
        "SELECT proname, pg_get_function_identity_arguments(oid), prokind, provolatile, prosecdef, "
        "proconfig::text, md5(prosrc) FROM pg_proc WHERE pronamespace = 'agri'::regnamespace ORDER BY 1, 2"
    ),
    "indexes": ("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'agri' ORDER BY 1"),
    "triggers": (
        "SELECT cls.relname, trg.tgname, pg_get_triggerdef(trg.oid) FROM pg_trigger trg "
        "JOIN pg_class cls ON cls.oid = trg.tgrelid WHERE cls.relnamespace = 'agri'::regnamespace "
        "AND NOT trg.tgisinternal ORDER BY 1, 2"
    ),
    "foreign_keys": (
        "SELECT conname, conrelid::regclass::text, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE connamespace = 'agri'::regnamespace AND contype = 'f' ORDER BY 1"
    ),
    "unique_and_primary_keys": (
        "SELECT conname, conrelid::regclass::text, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE connamespace = 'agri'::regnamespace AND contype IN ('p','u') ORDER BY 1"
    ),
}

_CHECK_CONSTRAINTS_QUERY = (
    "SELECT conname, conrelid::regclass::text, pg_get_constraintdef(oid) FROM pg_constraint "
    "WHERE connamespace = 'agri'::regnamespace AND contype = 'c' ORDER BY 1, 2"
)


def _require_admin_dsn() -> str:
    dsn = os.environ.get(ADMIN_DSN_ENV)
    if not dsn:
        pytest.skip(
            f"set {ADMIN_DSN_ENV} to a maintenance DSN on a disposable server whose image can "
            "install timescaledb (20260719_0001's preflight requires it). Never production."
        )
    return dsn


def _fetch(dsn: str, sql: str) -> list[tuple]:
    connection = psycopg2.connect(dsn)
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchall()
    finally:
        connection.close()


def _build(admin_dsn: str, database: str, alembic_ini: Path, *, install_timescaledb: bool) -> str:
    _run_maintenance(admin_dsn, f'DROP DATABASE IF EXISTS "{database}"')
    _run_maintenance(admin_dsn, f'CREATE DATABASE "{database}"')
    target = _swap_database(admin_dsn, database)
    _enable_reviewed_extensions(target)
    if install_timescaledb:
        # The deadlock the collapse exists to break, reproduced deliberately: 20260719_0001 refuses
        # to create schema agri without it, and 20260825_0026 drops it again three seconds later.
        _run_maintenance(target, "CREATE EXTENSION IF NOT EXISTS timescaledb")
    result = _alembic_upgrade_head(target, alembic_ini)
    assert result.returncode == 0, f"building {database} failed:\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}"
    return target


@pytest.fixture(scope="module")
def replayed_databases(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    """`(chain DSN, baseline DSN)`. Both dropped afterwards; neither survives the module."""
    admin_dsn = _require_admin_dsn()
    archive_ini = _rehearsal_alembic_ini(_ARCHIVE, tmp_path_factory.mktemp("archive_replay") / "alembic.ini")
    chain = _build(admin_dsn, _CHAIN_DATABASE, archive_ini, install_timescaledb=True)
    baseline = _build(admin_dsn, _BASELINE_DATABASE, _SERVICE_ROOT / "alembic.ini", install_timescaledb=False)
    try:
        yield chain, baseline
    finally:
        _run_maintenance(admin_dsn, f'DROP DATABASE IF EXISTS "{_CHAIN_DATABASE}"')
        _run_maintenance(admin_dsn, f'DROP DATABASE IF EXISTS "{_BASELINE_DATABASE}"')


@pytest.mark.agri_db_migration_rehearsal
@pytest.mark.parametrize("inventory", sorted(_INVENTORY_QUERIES))
def test_the_baseline_and_the_replayed_chain_agree_exactly_on_the_catalogue(
    replayed_databases: tuple[str, str], inventory: str
) -> None:
    """No normalisation, no allowance: these seven inventories must be identical row for row."""
    chain_dsn, baseline_dsn = replayed_databases
    sql = _INVENTORY_QUERIES[inventory]
    chain_rows = _fetch(chain_dsn, sql)
    baseline_rows = _fetch(baseline_dsn, sql)

    only_in_chain = [row for row in chain_rows if row not in baseline_rows]
    only_in_baseline = [row for row in baseline_rows if row not in chain_rows]
    assert not only_in_chain, (
        f"{inventory}: {len(only_in_chain)} row(s) only in the replayed chain: {only_in_chain[:5]}"
    )
    assert not only_in_baseline, (
        f"{inventory}: {len(only_in_baseline)} row(s) only in the baseline build: {only_in_baseline[:5]}"
    )


@pytest.mark.agri_db_migration_rehearsal
def test_every_check_constraint_matches_or_is_a_documented_reparse(
    replayed_databases: tuple[str, str],
) -> None:
    """The one leg that admits a difference, and the exact rule under which it admits it."""
    chain_dsn, baseline_dsn = replayed_databases
    chain = {(name, table): definition for name, table, definition in _fetch(chain_dsn, _CHECK_CONSTRAINTS_QUERY)}
    baseline = {(name, table): definition for name, table, definition in _fetch(baseline_dsn, _CHECK_CONSTRAINTS_QUERY)}

    assert set(chain) == set(baseline), (
        f"CHECK constraints only in the replayed chain: {sorted(set(chain) - set(baseline))}; "
        f"only in the baseline build: {sorted(set(baseline) - set(chain))}"
    )

    unexplained = [
        f"{table}.{name}\n  chain:    {chain[(name, table)]}\n  baseline: {baseline[(name, table)]}"
        for name, table in sorted(chain)
        if not reparse_equivalent(chain[(name, table)], baseline[(name, table)])
    ]
    assert not unexplained, "CHECK definitions differing beyond the reviewed reparse rule:\n" + "\n".join(
        unexplained[:10]
    )


@pytest.mark.agri_db_migration_rehearsal
def test_the_extension_sets_converge_despite_the_chain_having_needed_timescaledb(
    replayed_databases: tuple[str, str],
) -> None:
    """0026 drops what 0001 demanded, so both builds must land on the same four extensions."""
    chain_dsn, baseline_dsn = replayed_databases
    query = "SELECT extname FROM pg_extension ORDER BY 1"
    chain = [name for (name,) in _fetch(chain_dsn, query)]
    baseline = [name for (name,) in _fetch(baseline_dsn, query)]

    assert chain == baseline, f"replayed chain has {chain}, baseline build has {baseline}"
    assert "timescaledb" not in baseline


@pytest.mark.agri_db_migration_rehearsal
def test_the_baseline_closes_the_public_execute_gap_the_chain_left_open(
    replayed_databases: tuple[str, str],
) -> None:
    """The one measured privilege delta, asserted rather than asserted-about in a docstring."""
    chain_dsn, baseline_dsn = replayed_databases
    query = (
        "SELECT proname FROM pg_proc WHERE pronamespace = 'agri'::regnamespace "
        "AND has_function_privilege('public', oid, 'EXECUTE') ORDER BY 1"
    )
    chain_public = [name for (name,) in _fetch(chain_dsn, query)]
    baseline_public = [name for (name,) in _fetch(baseline_dsn, query)]

    assert not baseline_public, f"the baseline left {baseline_public} executable by PUBLIC"
    assert set(chain_public) <= {
        "forecast_candidate_evaluation_receipt_checksum",
        "forecast_derived_signal_value_checksum",
        "forecast_quality_policy_contract_v2",
    }, f"the replayed chain leaves unexpected routine(s) PUBLIC-executable: {chain_public}"


def test_the_scoring_rule_rejects_a_difference_it_was_not_written_to_admit() -> None:
    """No database. A negative control for `db/schema_diff.reparse_equivalent`, so the rule cannot rot."""
    chain_form = (
        "CHECK (((status)::text = ANY ((ARRAY['draft'::character varying, 'final'::character varying])::text[])))"
    )
    baseline_form = (
        "CHECK (((status)::text = ANY (ARRAY[('draft'::character varying)::text, ('final'::character varying)::text])))"
    )
    assert reparse_equivalent(chain_form, baseline_form)

    dropped_value = baseline_form.replace("('final'::character varying)::text", "")
    assert not reparse_equivalent(chain_form, dropped_value), "a dropped enum value must not score as a reparse"
    renamed_column = baseline_form.replace("status", "state")
    assert not reparse_equivalent(chain_form, renamed_column), "a renamed column must not score as a reparse"
