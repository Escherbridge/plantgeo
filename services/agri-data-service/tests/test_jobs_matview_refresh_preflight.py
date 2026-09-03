"""The preflight both matview lanes share: refuse immediately when `agri.matview_refresh_state` is
missing, rather than burning a full retry budget on a step certain to raise.

Pure-unit coverage with no database, on the same seam `test_jobs_lease.py` and `test_jobs_worker.py`
use: `RecordingSession` answers each statement by the `-- <name>` marker it opens with. This is a NEW
test file (not one of the two named in this package's test ownership) written specifically to pin the
production incident this preflight exists to prevent: `agri.matview_refresh_state` missing dead-lettered
ten `matview-refresh` shards and parked thirteen `strategy-mv-refresh` ones, one fresh doomed work item
per tick, before the underlying migration was applied.

SINCE 2026-09-02 THERE ARE TWO PREFLIGHTS AND THIS FILE OWNS THE BOUNDARY BETWEEN THEM. This one is
definition-level: it refuses the whole lane, before a run is opened, over relations with no graceful
path. `matview_refresh.py::_absent_relations` is per-spec, runs inside the handler, and turns an
absent VIEW into the governed `relation_absent` outcome instead -- covered in
`tests/test_matview_refresh.py`. A view must never migrate into this file's tuple; the two tests at
the bottom are what stop it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from agri_data_service.jobs.matview_refresh import (
    MATVIEW_REFRESH_DEFINITION_NAME,
    MATVIEW_REFRESH_QUALIFIED_NAMES,
    MATVIEW_REFRESH_REQUIRED_RELATIONS,
    MATVIEW_REFRESH_STATE_RELATION,
    trigger_matview_refresh,
)
from agri_data_service.jobs.strategy_mv_refresh import (
    STRATEGY_MV_REFRESH_DEFINITION_NAME,
    trigger_strategy_mv_refresh,
)
from agri_data_service.jobs.worker import PREFLIGHT_MISSING_RELATIONS_STOP_REASON

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_MARKER = re.compile(r"^--\s+(\w+)\s*$", re.MULTILINE)


class FakeResult:
    """The narrow slice of SQLAlchemy's Result the runtime actually uses."""

    def __init__(self, rows: Sequence[Mapping[str, object]]) -> None:
        self._rows = list(rows)

    def mappings(self) -> FakeResult:
        """Return self, because the fake already yields mappings."""
        return self

    def first(self) -> Mapping[str, object] | None:
        """The first row, or None when the statement matched nothing."""
        return self._rows[0] if self._rows else None

    def all(self) -> list[Mapping[str, object]]:
        """Every row the statement returned."""
        return list(self._rows)


class RecordingSession:
    """An AsyncSession stand-in that records every statement and answers it by its `-- marker` comment."""

    def __init__(self) -> None:
        """Start with no scripted answers; an unscripted statement returns no rows."""
        self.statements: list[tuple[str, dict[str, object]]] = []
        self.commits = 0
        self.rollbacks = 0
        self._answers: dict[str, list[Mapping[str, object]]] = {}

    def answer(self, marker: str, rows: Sequence[Mapping[str, object]]) -> None:
        """Answer every statement carrying `-- marker` with this row set."""
        self._answers[marker] = list(rows)

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> FakeResult:
        """Record the statement and answer it from the script."""
        sql = str(statement)
        self.statements.append((sql, dict(parameters or {})))
        marker = self.marker_of(sql)
        return FakeResult(self._answers.get(marker or "", []))

    async def commit(self) -> None:
        """Count a commit."""
        self.commits += 1

    async def rollback(self) -> None:
        """Count a rollback."""
        self.rollbacks += 1

    @staticmethod
    def marker_of(sql: str) -> str | None:
        """The `-- <name>` marker a statement opens with."""
        found = _MARKER.search(sql)
        return None if found is None else found.group(1)

    def markers(self) -> list[str]:
        """Every statement's marker, in the order they were executed."""
        return [marker for sql, _ in self.statements if (marker := self.marker_of(sql)) is not None]

    def emitted(self, marker: str) -> bool:
        """True when at least one statement carrying this marker was executed."""
        return marker in self.markers()


def _missing_relation_session() -> RecordingSession:
    """A session that reports `agri.matview_refresh_state` absent and nothing else scripted."""
    session = RecordingSession()
    session.answer(
        "check_relations_exist",
        [{"qualified_name": MATVIEW_REFRESH_STATE_RELATION, "relation_exists": False}],
    )
    return session


async def test_matview_refresh_refuses_before_it_ever_upserts_the_definition() -> None:
    session = _missing_relation_session()

    summary = await trigger_matview_refresh(session, requested_by="test")

    assert summary.definition_name == MATVIEW_REFRESH_DEFINITION_NAME
    assert summary.stop_reason == PREFLIGHT_MISSING_RELATIONS_STOP_REASON
    assert summary.preflight_missing_relations == (MATVIEW_REFRESH_STATE_RELATION,)
    assert summary.job_run_id is None
    # No definition upserted, no run opened, no shard minted, no attempt claimed -- exactly the
    # burn-the-retry-budget-forever shape this preflight exists to stop before it starts.
    assert session.emitted("upsert_job_definition") is False
    assert session.emitted("insert_job_run") is False
    assert session.emitted("insert_job_work_items") is False
    assert session.emitted("claim_work_item") is False


def test_the_definition_preflight_declares_the_ledger_and_never_a_matview() -> None:
    """TWO preflights exist and they must not be confused; this pins the boundary between them.

    The DEFINITION-level one here refuses the whole lane before it opens a run, and it may only ever
    name relations with no graceful path -- the shared ledger, whose absence makes every read and
    write raise. The PER-SPEC one (`matview_refresh.py::_absent_relations`, inside the handler)
    handles an absent VIEW, and its answer is the governed `relation_absent` outcome, not a refusal.

    Promoting a view into this tuple would turn "nine of ten views are ready, refresh them" into
    "refuse the tick", which is a strictly worse answer than the bug being fixed: a lane that refuses
    every tick over a relation the Parquet pivot deliberately dropped never refreshes anything again.
    """
    assert MATVIEW_REFRESH_REQUIRED_RELATIONS == (MATVIEW_REFRESH_STATE_RELATION,)
    assert set(MATVIEW_REFRESH_REQUIRED_RELATIONS).isdisjoint(MATVIEW_REFRESH_QUALIFIED_NAMES)


async def test_the_definition_preflight_asks_the_catalog_about_the_ledger_alone() -> None:
    """The declaration above is what the statement actually binds, not merely what the constant says."""
    session = _missing_relation_session()

    await trigger_matview_refresh(session, requested_by="test")

    asked = [params for sql, params in session.statements if RecordingSession.marker_of(sql) == "check_relations_exist"]
    assert asked == [{"qualified_names": [MATVIEW_REFRESH_STATE_RELATION]}]


async def test_strategy_mv_refresh_refuses_on_the_same_shared_relation() -> None:
    session = _missing_relation_session()

    summary = await trigger_strategy_mv_refresh(session, requested_by="test")

    assert summary.definition_name == STRATEGY_MV_REFRESH_DEFINITION_NAME
    assert summary.stop_reason == PREFLIGHT_MISSING_RELATIONS_STOP_REASON
    assert summary.preflight_missing_relations == (MATVIEW_REFRESH_STATE_RELATION,)
    assert session.emitted("upsert_job_definition") is False
    assert session.emitted("insert_job_run") is False
    assert session.emitted("claim_work_item") is False
