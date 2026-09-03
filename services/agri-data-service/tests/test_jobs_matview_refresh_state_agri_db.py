"""Real-asyncpg proof that both matview lanes' shared statements PARSE, not just that they read right.

Gated on ``AGRI_TEST_DATABASE_URL`` through ``tests/conftest.py``'s ``agri_db_async_dsn`` fixture,
which pins the Alembic head and refuses the persistent ``plantgeo`` warehouse.

WHY THIS FILE EXISTS. ``tests/test_jobs_matview_refresh_preflight.py`` and
``tests/test_matview_refresh.py`` cover this same code through a ``FakeSession``/``RecordingSession``
stand-in that answers a statement by the ``-- marker`` it opens with and never sends it to a server.
That seam cannot see a PARSE-time fault, and a PARSE-time fault is exactly what took both lanes down
in production on 2026-08-17: alembic ``20260817_0025`` added a ``consecutive_failures`` CASE to
``upsert_matview_refresh_state.sql`` that reads ``outcome`` a SECOND time, SQLAlchemy renders a
repeated named parameter as one placeholder, and PostgreSQL deduced two types for it -- 42P08,
``inconsistent types deduced for parameter $6``. Every ``matview-refresh`` and ``strategy-mv-refresh``
tick raised it before writing a single row, and the whole mocked suite stayed green.

So these tests deliberately assert almost nothing about VALUES and everything about EXECUTION: the
statement reaching a real server and coming back is the assertion. They are cheap -- one tiny row per
test, no matview is refreshed -- and they run wherever the sweep's disposable database runs.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from agri_data_service.jobs.lease import find_missing_relations
from agri_data_service.jobs.matview_refresh import (
    MATVIEW_REFRESH_STATE_RELATION,
    UPSERT_MATVIEW_REFRESH_STATE,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from sqlalchemy.ext.asyncio import AsyncEngine

_DELETE_STATE_ROW: Final = text("DELETE FROM agri.matview_refresh_state WHERE view_name = :view_name")

# `ck_matview_refresh_state_schema_qualified_view` requires `schema.relation`, lower snake case only,
# so the per-test unique suffix is uuid4's hex form rather than its dashed one.
_PROBE_SCHEMA: Final = "geo"

# Two consecutive `failed` attempts: the smallest count that proves the ON CONFLICT branch INCREMENTS
# rather than re-deriving 1 from the parameter the way the INSERT branch does.
_AFTER_TWO_FAILURES: Final = 2


@pytest.fixture
async def engine(agri_db_async_dsn: str) -> AsyncIterator[AsyncEngine]:
    created = create_async_engine(agri_db_async_dsn)
    try:
        yield created
    finally:
        await created.dispose()


@pytest.fixture
async def probe_view_name(engine: AsyncEngine) -> AsyncIterator[str]:
    """A view name no other test or production tick will collide on, deleted on teardown.

    The name never names a real matview: nothing in these tests issues a REFRESH. It exists only so
    the upsert has a primary key to conflict on, which is what makes the ON CONFLICT branch -- the
    branch carrying the CASE that broke -- reachable from a test at all.
    """
    name = f"{_PROBE_SCHEMA}.zz_probe_{uuid.uuid4().hex}"
    try:
        yield name
    finally:
        async with AsyncSession(engine) as session:
            await session.execute(_DELETE_STATE_ROW, {"view_name": name})
            await session.commit()


async def _upsert(engine: AsyncEngine, view_name: str, *, outcome: str, refreshed_at: object) -> Mapping[str, object]:
    """Run the real shared statement against the real server and return the row it hands back."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        result = await session.execute(
            UPSERT_MATVIEW_REFRESH_STATE,
            {
                "view_name": view_name,
                "source_watermark": "{}",
                "refreshed_at": refreshed_at,
                "duration_ms": 1,
                "row_count": None,
                "outcome": outcome,
            },
        )
        row = result.mappings().first()
        await session.commit()
    assert row is not None
    return row


async def test_upsert_parses_against_a_real_server_on_the_insert_branch(
    engine: AsyncEngine, probe_view_name: str
) -> None:
    """The regression itself: the INSERT branch's `outcome` is read twice and must still parse.

    Pre-fix this raised `asyncpg.exceptions.AmbiguousParameterError` before touching a row, so the
    lane never got as far as recording that it had failed -- which is why `last_attempt_at` and
    `consecutive_failures` stayed empty in production while attempts piled up in `job_attempt`.
    """
    row = await _upsert(engine, probe_view_name, outcome="failed", refreshed_at=None)

    assert row["outcome"] == "failed"
    # Derived in SQL from the same parameter the CASE reads, so a wrong type would not merely raise
    # -- it would silently mis-derive. 1 proves the CASE saw the value the column stored.
    assert row["consecutive_failures"] == 1
    assert row["last_attempt_at"] is not None
    assert row["refreshed_at"] is None


async def test_upsert_parses_on_the_conflict_branch_and_the_counter_tracks_the_outcome(
    engine: AsyncEngine, probe_view_name: str
) -> None:
    """The ON CONFLICT branch carries its own copy of the CASE, so it needs its own execution."""
    await _upsert(engine, probe_view_name, outcome="failed", refreshed_at=None)
    second = await _upsert(engine, probe_view_name, outcome="failed", refreshed_at=None)
    assert second["consecutive_failures"] == _AFTER_TWO_FAILURES

    # `skipped_missing` issues no REFRESH, so it must leave the counter exactly where it was.
    skipped = await _upsert(engine, probe_view_name, outcome="skipped_missing", refreshed_at=None)
    assert skipped["consecutive_failures"] == _AFTER_TWO_FAILURES

    # `relation_absent` (2026-09-02) rides the same ELSE branch and needed NO migration to do it:
    # `outcome` is a plain `character varying(64)` with no value CHECK, so the new literal is legal
    # against a database at the current head. Proven here rather than asserted in prose, because the
    # whole point of this file is that a statement's behaviour against a real server is the only
    # evidence the mocked seam cannot fake. A counter that moved here would mean a governed absence
    # earns a backoff, which would silently withhold the refresh on the tick a relation reappears.
    absent = await _upsert(engine, probe_view_name, outcome="relation_absent", refreshed_at=None)
    assert absent["consecutive_failures"] == _AFTER_TWO_FAILURES
    assert absent["outcome"] == "relation_absent"


async def test_a_success_clears_the_backoff_counter(engine: AsyncEngine, probe_view_name: str) -> None:
    """A non-NULL refreshed_at means the attempt succeeded, which is the only thing that resets to 0."""
    await _upsert(engine, probe_view_name, outcome="failed", refreshed_at=None)
    succeeded = await _upsert(engine, probe_view_name, outcome="refreshed_concurrently", refreshed_at=datetime.now(UTC))

    assert succeeded["consecutive_failures"] == 0
    assert succeeded["refreshed_at"] is not None


async def test_find_missing_relations_round_trips_its_array_parameter(engine: AsyncEngine) -> None:
    """The preflight's `text[]` bind, proven against a real driver rather than a FakeSession.

    `check_relations_exist.sql` binds a native Python list into `unnest(CAST(:qualified_names AS
    text[]))`. Only a real asyncpg connection can show that PostgreSQL deduces that placeholder as
    `text[]` and that the list crosses the wire intact; the mocked seam would accept any shape.
    """
    async with AsyncSession(engine) as session:
        missing = await find_missing_relations(
            session,
            [MATVIEW_REFRESH_STATE_RELATION, "agri.zz_no_such_relation", "agri.job_work_item"],
        )

    assert missing == ("agri.zz_no_such_relation",)


async def test_find_missing_relations_asks_the_server_nothing_for_an_empty_list(engine: AsyncEngine) -> None:
    """The empty case short-circuits in Python; an empty array bind is never sent."""
    async with AsyncSession(engine) as session:
        assert await find_missing_relations(session, []) == ()
