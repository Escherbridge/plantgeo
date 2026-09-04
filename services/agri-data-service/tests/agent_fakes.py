"""An in-memory Parquet warehouse for the agent tools: real day resolution, scripted DuckDB rows.

`FakeListing` from `tests/parquet_ops/fakes.py` is reused deliberately rather than re-written, so
the agent's day classification runs through the SAME `day_status_sets` the map's `/day` route does
and a test can produce the states real data cannot make on demand -- a conflict, a half-written
export, a lane that never wrote anything.

Only the DuckDB half is scripted. A statement is recognised by the bare `-- <name>` marker on its
first line, the same dispatch protocol `test_ingest_reconcile.py` and `test_jobs_lease.py` use for
PostgreSQL, so every assertion is made against the real statement text and its real bound
parameters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.agent.surfaces import SURFACE_PARQUET_LANES
from agri_data_service.agent.warehouse import DayEvidence, LaneEvidence, reset_source, set_source
from agri_data_service.warehouse.parquet.schema import StreamSchemaError, get_stream_schema
from tests.parquet_ops.fakes import FakeListing

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

_MARKER = re.compile(r"^--\s+(\w+)\s*$", re.MULTILINE)

#: The bucket the fake session pretends to address. Never reached; only rendered into a URI so a
#: test can assert WHICH part files a read addressed.
FAKE_BUCKET_URI = "s3://fake-bucket"


def marker_of(statement: str) -> str | None:
    """The bare `-- <name>` marker a DuckDB statement opens with."""
    found = _MARKER.search(statement)
    return None if found is None else found.group(1)


class FakeCursor:
    """The narrow slice of a DuckDB cursor `warehouse.scan_all` actually uses."""

    def __init__(self, rows: Sequence[dict[str, Any]]) -> None:
        self._rows = [dict(row) for row in rows]
        self._columns = list(self._rows[0]) if self._rows else []

    @property
    def description(self) -> list[tuple[str, str]]:
        """Column names in the shape DuckDB reports them."""
        return [(name, "UNKNOWN") for name in self._columns]

    def fetchall(self) -> list[tuple[Any, ...]]:
        """Every scripted row, as positional tuples in column order."""
        return [tuple(row[name] for name in self._columns) for row in self._rows]


def registered_columns() -> frozenset[str]:
    """Every column the lanes the agent reads declare, DERIVED from their registered Arrow schemas.

    The column probe in `warehouse.scan_all` asks the objects what they carry; a fake with no
    objects has to answer something, and answering the registered promise is the right default --
    it makes the probe invisible to a test about anything else, and lets a test that IS about a
    lane owing a re-export narrow the set and watch the refusal fire.
    """
    lanes = ("signal", "drought", *sorted({lane for lanes in SURFACE_PARQUET_LANES.values() for lane in lanes}))
    columns: set[str] = set()
    for lane in lanes:
        try:
            columns.update(get_stream_schema(lane, "observed").column_names)
        except StreamSchemaError:  # pragma: no cover - a lane without a schema cannot be read at all
            continue
    return frozenset(columns)


@dataclass
class FakeConnection:
    """Records every statement and its bound parameters, and answers it from the script."""

    answers: dict[str, list[dict[str, Any]]]
    columns: frozenset[str] = field(default_factory=registered_columns)
    executed: list[tuple[str, list[Any]]] = field(default_factory=list)

    def execute(self, statement: str, parameters: list[Any]) -> FakeCursor:
        """Record the read and answer it by the statement's line-one marker."""
        if "parquet_schema(" in statement:
            return FakeCursor([{"name": name} for name in sorted(self.columns)])
        self.executed.append((statement, list(parameters)))
        return FakeCursor(self.answers.get(marker_of(statement) or "", []))


@dataclass
class FakeServingSession:
    """A `ServingSession` stand-in: the same `object_uri` contract, no bucket behind it."""

    connection: FakeConnection
    bucket_uri: str = FAKE_BUCKET_URI

    def object_uri(self, relative_key: str) -> str:
        """Render one relative key the way the real session would."""
        return f"{self.bucket_uri}/{relative_key}"


@dataclass
class FakeAgentWarehouse:
    """An `AgentWarehouseSource` over an in-memory listing, a statement script and folded evidence."""

    listing_store: FakeListing = field(default_factory=FakeListing)
    answers: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    evidence: dict[str, LaneEvidence] = field(default_factory=dict)
    #: What the PUBLISHED objects carry, which is not always what the registered schema promises.
    #: Narrow it to prove a lane that owes a re-export refuses instead of raising a binder error.
    columns: frozenset[str] = field(default_factory=registered_columns)
    #: Set to raise from `run`, so a serving refusal can be exercised without a warehouse.
    run_raises: Exception | None = None
    executed: list[tuple[str, list[Any]]] = field(default_factory=list)
    operations: list[str] = field(default_factory=list)

    def listing(self) -> FakeListing:
        """The in-memory object layout the day resolution walks."""
        return self.listing_store

    async def run(self, work: Callable[[Any], Any], *, operation: str) -> Any:
        """Run the read against a scripted session, recording which operation asked."""
        self.operations.append(operation)
        if self.run_raises is not None:
            raise self.run_raises
        connection = FakeConnection(answers=self.answers, columns=self.columns)
        try:
            answered = work(FakeServingSession(connection=connection))
        finally:
            self.executed.extend(connection.executed)
        return answered

    def availability_evidence(self, layers: Sequence[str], now: datetime) -> tuple[LaneEvidence, ...]:
        """Return the scripted evidence, defaulting an unscripted lane to an unpublished index."""
        del now
        return tuple(
            self.evidence.get(layer, LaneEvidence(layer=layer, withheld_reason="availability_unpublished"))
            for layer in layers
        )

    # --- Scripting helpers ---------------------------------------------------------

    def answer(self, marker: str, rows: Sequence[dict[str, Any]]) -> None:
        """Answer every statement carrying this marker with these rows."""
        self.answers[marker] = [dict(row) for row in rows]

    def statement_for(self, marker: str) -> str:
        """The text of the first executed statement carrying this marker."""
        for statement, _ in self.executed:
            if marker_of(statement) == marker:
                return statement
        raise AssertionError(f"no DuckDB statement carrying marker {marker!r} was executed")

    def parameters_for(self, marker: str) -> list[Any]:
        """The bound parameters of the first executed statement carrying this marker."""
        for statement, parameters in self.executed:
            if marker_of(statement) == marker:
                return parameters
        raise AssertionError(f"no DuckDB statement carrying marker {marker!r} was executed")

    def part_uris_for(self, marker: str) -> list[str]:
        """The part-file URIs one read addressed; always the first bound parameter."""
        first = self.parameters_for(marker)[0]
        assert isinstance(first, list), "every agent statement binds its part-file list first"
        return first

    def arguments_for(self, marker: str) -> list[Any]:
        """One read's parameters WITHOUT the part-file list, so an index matches the documented order."""
        return self.parameters_for(marker)[1:]

    def markers(self) -> list[str]:
        """Every executed statement's marker, in execution order."""
        return [marker for statement, _ in self.executed if (marker := marker_of(statement)) is not None]


class RefusingWarehouse:
    """A source that fails loudly the moment a test forgets to bind a real fake.

    THE PRODUCTION DEFAULT IS A LIVE BUCKET. Before this existed, an agent test that bound only a
    database session fell through to `_ObjectStoreSource`, opened a boto client against R2 and
    listed production objects -- seventeen seconds per test, and a live read from a suite that is
    supposed to touch nothing. Bound autouse for the whole module, this turns that into an error
    naming the test.
    """

    def listing(self) -> FakeListing:
        """Refuse: no test may read the real object store."""
        raise AssertionError(_UNBOUND_MESSAGE)

    async def run(self, work: Callable[[Any], Any], *, operation: str) -> Any:
        """Refuse: no test may open a serving session against the real bucket."""
        del work, operation
        raise AssertionError(_UNBOUND_MESSAGE)

    def availability_evidence(self, layers: Sequence[str], now: datetime) -> tuple[LaneEvidence, ...]:
        """Refuse: no test may read a real availability index."""
        del layers, now
        raise AssertionError(_UNBOUND_MESSAGE)


_UNBOUND_MESSAGE = (
    "this test reached the agent's warehouse without binding one: pass "
    "`warehouse_source=FakeAgentWarehouse(...)` to `tools.run_context(...)`. The unbound default is "
    "the PRODUCTION object store."
)


@pytest.fixture(autouse=True)
def _refuse_the_production_warehouse() -> Iterator[None]:
    """Bind the refusing source for the whole module, so an unbound read is an error and not a read."""
    token = set_source(RefusingWarehouse())
    try:
        yield
    finally:
        reset_source(token)


def published_lane(
    layer: str,
    days: Sequence[date],
    *,
    nature: str = "daily_series",
    row_count: int = 12,
    source_ceiling_day: date | None = None,
) -> LaneEvidence:
    """One lane that proved itself and published rows on every named day."""
    return LaneEvidence(
        layer=layer,
        nature=nature,
        days={
            day: DayEvidence(
                state="published",
                row_count=row_count,
                published_at=datetime(day.year, day.month, day.day, 6, tzinfo=UTC),
                absence_reason=None,
            )
            for day in days
        },
        source_ceiling_day=source_ceiling_day or (max(days) if days else None),
    )


def absent_lane(
    layer: str,
    days: Sequence[date],
    *,
    reason: str = "upstream_published_nothing",
    nature: str = "daily_series",
) -> LaneEvidence:
    """One lane that proved itself and recorded a governed absence on every named day."""
    return LaneEvidence(
        layer=layer,
        nature=nature,
        days={
            day: DayEvidence(
                state="governed_absence",
                row_count=0,
                published_at=datetime(day.year, day.month, day.day, 6, tzinfo=UTC),
                absence_reason=reason,
            )
            for day in days
        },
        source_ceiling_day=max(days) if days else None,
    )
