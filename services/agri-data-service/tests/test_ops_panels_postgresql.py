"""The ops health panels' failure isolation, proved against a real PostgreSQL transaction.

`tests/test_ops_routes.py` stubs AsyncSession, so it can prove shape, derivation and rendering but
NOT that a savepoint does anything: its stub raises a plain exception that poisons nothing, and its
`_Savepoint.__aexit__` only returns False. Delete the `async with session.begin_nested():` line from
`_optional_rows` and every assertion in that file still passes -- while production would blank every
panel below the first failure. This file is where that claim is actually tested.

It needs no fixture data. The disposable governance database is migrated to the agri Alembic head
and therefore has no `public.users` at all, which is exactly the deployment `_optional_rows` names
as its reason to exist -- so the real platform statement fails here for the real reason, with the
real driver, inside the real session.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from agri_data_service.routes import ops as ops_route

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# What the live asyncpg stack produces for a missing relation, once `_panel_error_summary` has
# unwrapped the dialect's own wrapper off `orig.__cause__`. Asserted exactly, on purpose: the pair
# read "ProgrammingError: ProgrammingError" before that unwrapping, and a real-database test that
# does not pin the shape it observes is no use for catching driver drift -- which is precisely what
# this constant exists to catch on the next SQLAlchemy or asyncpg upgrade.
_LIVE_MISSING_RELATION_SUMMARY = "ProgrammingError: UndefinedTableError"
# Anything near this length is a message, and a message carries the statement.
_PANEL_ERROR_SANE_LENGTH = 120


def _panel_query(cache_key: str, statement: Any) -> ops_route._ScanQuery:
    """One panel read with caching effectively disabled, so every call really hits the database."""
    return ops_route._ScanQuery(cache_key=cache_key, statement=statement, parameters={}, ttl_seconds=0)


@pytest.fixture(autouse=True)
def _clear_query_cache() -> None:
    """The result cache is a module global that outlives a request, and would mask a failed read."""
    ops_route._QUERY_CACHE.clear()


@pytest.fixture
async def ops_session(agri_db_async_dsn: str) -> AsyncIterator[AsyncSession]:
    """A real asyncpg-backed session with the route's own statement timeout already pinned.

    Pinned first, exactly as `_load_snapshot` does, because part of the claim under test is that a
    SET LOCAL made before a savepoint survives that savepoint being rolled back.
    """
    engine = create_async_engine(agri_db_async_dsn)
    try:
        async with AsyncSession(engine) as session:
            await session.execute(ops_route._SET_STATEMENT_TIMEOUT)
            yield session
            # Read-only throughout; roll back rather than commit so nothing can be left behind.
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_failing_panel_leaves_the_transaction_usable_for_every_panel_after_it(
    ops_session: AsyncSession,
) -> None:
    """The savepoint claim, end to end: one panel fails, the two that follow still return rows."""
    panel_errors: dict[str, str] = {}
    now = datetime.now(UTC)

    # This database has no `public.users`, so the real statement fails for the real reason.
    platform = await ops_route._optional_rows(
        ops_session,
        _panel_query(ops_route._PLATFORM_CACHE_KEY, ops_route._PLATFORM_ACTIVITY_SQL),
        now,
        panel_errors,
    )
    # Both of these run AFTER the failure, in the same transaction. Without the savepoint they
    # would raise InFailedSqlTransaction instead of returning anything -- see the control below.
    forecast = await ops_route._optional_rows(
        ops_session,
        _panel_query(ops_route._FORECAST_CACHE_KEY, ops_route._FORECAST_STATE_SQL),
        now,
        panel_errors,
    )
    sources = await ops_route._optional_rows(
        ops_session,
        _panel_query(ops_route._SOURCES_CACHE_KEY, ops_route._UNARMED_SOURCES_SQL),
        now,
        panel_errors,
    )

    assert platform == []
    assert ops_route._PLATFORM_CACHE_KEY in panel_errors
    # The transaction survived: these are real reads that returned real rows afterwards.
    assert forecast, "the forecast panel read after a failed panel and came back empty"
    assert ops_route._FORECAST_CACHE_KEY not in panel_errors
    assert ops_route._SOURCES_CACHE_KEY not in panel_errors
    # A freshly migrated database has no catalogued sources, so `sources` is legitimately empty --
    # what matters is that it was READ rather than refused.
    assert isinstance(sources, list)
    # All four planes came back, each stage carrying a real count. Deliberately not asserting the
    # counts are zero: this database is shared with the rest of the governance suite and what is
    # being proved here is that the read HAPPENED, not what happens to be in it.
    assert {str(stage["plane"]) for stage in forecast} == {"evidence", "iteration", "governed", "recommendation"}
    assert all(stage["row_count"] is not None for stage in forecast)


@pytest.mark.asyncio
async def test_without_a_savepoint_the_same_failure_poisons_every_later_read(
    ops_session: AsyncSession,
) -> None:
    """The control that makes the test above mean something.

    Same session, same failing statement, same following read -- only the savepoint removed. This
    is what `/ops/backfill` would do if `_optional_rows` caught the exception without wrapping the
    read in `begin_nested()`: the page loses every panel below the first failure, not just the one
    that failed.
    """
    with pytest.raises(SQLAlchemyError):
        await ops_session.execute(ops_route._PLATFORM_ACTIVITY_SQL)

    with pytest.raises(SQLAlchemyError) as poisoned:
        await ops_session.execute(ops_route._FORECAST_STATE_SQL)

    # The second statement is perfectly valid and fails anyway, purely because the first one failed.
    assert poisoned.value is not None


@pytest.mark.asyncio
async def test_the_published_panel_error_carries_no_statement_text(
    ops_session: AsyncSession,
) -> None:
    """`/ops/backfill` is unauthenticated, and a real StatementError carries the whole statement.

    The unit-test version of this asserts against a hand-built error. This one asserts against
    whatever the live driver actually raises, which is the thing that will change under us when
    SQLAlchemy or asyncpg is upgraded.
    """
    panel_errors: dict[str, str] = {}

    await ops_route._optional_rows(
        ops_session,
        _panel_query(ops_route._PLATFORM_CACHE_KEY, ops_route._PLATFORM_ACTIVITY_SQL),
        datetime.now(UTC),
        panel_errors,
    )
    published = panel_errors[ops_route._PLATFORM_CACHE_KEY]

    assert len(published) <= _PANEL_ERROR_SANE_LENGTH
    assert "[SQL:" not in published
    # Neither the statement nor the header comments that describe where identity data lives.
    for leaked in ("select", "public.users", "email", "slug", "session_token", "lat/lon"):
        assert leaked not in published.lower()
    # It names the REAL postgres condition rather than the dialect wrapper that hides it. This is
    # the assertion that fails loudly if the driver's exception shape moves under us.
    assert published == _LIVE_MISSING_RELATION_SUMMARY
