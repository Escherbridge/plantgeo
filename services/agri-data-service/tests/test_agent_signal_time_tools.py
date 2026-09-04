"""The selected-day agent contract: exact day, temporal neighbours, spatial neighbours, surfaces.

The whole file exists to hold one line of the layer-lane standard, section 11: a proximity answer
that arrives without its distance and without the observation's own date is indistinguishable from
an exact answer, and is the same class of bug as a lane reporting success having written nothing.

REPOINTED 2026-09-04. The eight PostgreSQL statements these tools used to issue were deleted with
`geo.mv_signal_cell_daily` and `agri.spatial_cell`; the tools now read the day-partitioned Parquet
warehouse. What that changed for this suite is the SEAM, not the contract: `FakeAgentWarehouse`
holds an in-memory object layout that real day classification walks and scripts the row reads by
their line-one marker, and `RecordingSession` still stands in for the two PostgreSQL statements that
remain -- the ingest lane's absence ledger and the governed ML forecast plane, neither of which is
environmental data.

The statement-level parity evidence lives in `test_agent_parquet_reads.py`, which runs the real
DuckDB statements over real Parquet; the four-state and refusal behaviour lives in
`test_agent_parquet_tools.py`. This file keeps the day contract itself.
"""

# ruff: noqa: PLR2004 - the literals here are fixture values, and naming each one hides the assertion.

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.agent import tools as agent_tools
from agri_data_service.agent.graph import AgentRequest
from agri_data_service.agent.prompts import build_location_context
from agri_data_service.routes.agent_analysis import AgentAnalyzeRequest
from tests.agent_fakes import FakeAgentWarehouse, published_lane

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

PLANE_MARKER = "agent_materialized_plane_populated"
AUDIT_MARKER = "agent_signal_coverage_on_day"
FEATURE_MARKER = "agent_feature_value_near_point"

VALUE_MARKER = "agent_signal_day_values"
CELLS_MARKER = "agent_signal_cell_day_counts"
ADMITTED_MARKER = "agent_signal_admitted_cells"
NEIGHBORS_MARKER = "agent_signal_time_neighbors"
POINT_LANE_MARKER = "agent_point_lane_rows"

BOISE_LONGITUDE = -116.2
BOISE_LATITUDE = 43.6
# A day inside every contracted lane's horizon (execution/coverage_contract.py, verified 2026-08-11).
SELECTED_DAY = "2026-03-14"
SELECTED_DATE = date(2026, 3, 14)

SIGNAL_LANE = "signal"
NEAR_CELL = "aaaaaaaa-0000-0000-0000-000000000001"


class FakeResult:
    """The narrow slice of SQLAlchemy's Result the PostgreSQL half of a tool uses."""

    def __init__(self, rows: Sequence[Mapping[str, object]]) -> None:
        self._rows = list(rows)

    def mappings(self) -> FakeResult:
        """Return self, because the fake already yields mappings."""
        return self

    def all(self) -> list[Mapping[str, object]]:
        """Every row the statement returned."""
        return list(self._rows)


class RecordingSession:
    """An AsyncSession stand-in that records every PostgreSQL read and answers it by its marker."""

    def __init__(self) -> None:
        """Start with no scripted answers; an unscripted statement returns no rows."""
        self.statements: list[tuple[str, dict[str, object]]] = []
        self._answers: dict[str, list[Mapping[str, object]]] = {}

    def answer(self, marker: str, rows: Sequence[Mapping[str, object]]) -> None:
        """Answer every statement carrying this marker with these rows."""
        self._answers[marker] = list(rows)

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> FakeResult:
        """Record the statement and answer it from the script."""
        sql = str(statement)
        bound = dict(parameters or {})
        self.statements.append((sql, bound))
        marker = self.marker_of(sql) or ""
        if marker == PLANE_MARKER and marker not in self._answers:
            # Default the plane probe to "every named relation is built", so a test that is about
            # something else does not have to script it.
            requested = bound.get("relation_names")
            names = list(requested) if isinstance(requested, list) else []
            return FakeResult(
                [
                    {"relation_name": name, "relation_exists": True, "relation_kind": "m", "is_populated": True}
                    for name in names
                ]
            )
        return FakeResult(self._answers.get(marker, []))

    @staticmethod
    def marker_of(sql: str) -> str | None:
        """The bare `-- <name>` marker a statement opens with."""
        first_line = sql.lstrip().splitlines()[0] if sql.strip() else ""
        return first_line.removeprefix("-- ").strip() if first_line.startswith("-- ") else None

    def markers(self) -> list[str]:
        """Every statement's marker, in execution order."""
        return [marker for sql, _ in self.statements if (marker := self.marker_of(sql)) is not None]

    def sql_for(self, marker: str) -> str:
        """The text of the first statement carrying this marker."""
        for sql, _ in self.statements:
            if self.marker_of(sql) == marker:
                return sql
        raise AssertionError(f"no statement carrying marker {marker!r} was executed")

    def parameters_for(self, marker: str) -> dict[str, object]:
        """The bound parameters of the first statement carrying this marker."""
        for sql, parameters in self.statements:
            if self.marker_of(sql) == marker:
                return parameters
        raise AssertionError(f"no statement carrying marker {marker!r} was executed")


def _session_provider(session: RecordingSession) -> Any:
    @asynccontextmanager
    async def provider() -> AsyncIterator[RecordingSession]:
        yield session

    return provider


def _signal_warehouse(*, published: Sequence[date] = (SELECTED_DATE,)) -> FakeAgentWarehouse:
    """A warehouse whose signal lane published the named days, with nothing scripted yet."""
    source = FakeAgentWarehouse()
    for day in published:
        source.listing_store.write_day(SIGNAL_LANE, "observed", 13, day)
    return source


def _value_row(**overrides: object) -> dict[str, object]:
    """One row shaped like `parquet_reads.SIGNAL_DAY_VALUES` returns.

    No `source_parameter` and no `nearest_cell_grid_name`. The Parquet signal plane is grained by
    (support key, signal, unit, cell, day) and carries neither an upstream parameter column nor the
    grid a cell belongs to -- `agri.spatial_cell` held that and has been retired -- so reporting
    either would be inventing it. See agent/AGENTS.md, "Reading the Parquet warehouse".
    """
    row: dict[str, object] = {
        "signal_name": "air_temperature_mean",
        "support_key": "surface",
        "normalized_unit": "degC",
        "observed_day": SELECTED_DATE,
        "observation_count": 3,
        "cell_count": 3,
        "nearest_cell_distance_m": 4210.5,
        "nearest_cell_id": NEAR_CELL,
        "nearest_cell_value": 11.4,
        "nearest_cell_observed_at": datetime(2026, 3, 14, tzinfo=UTC),
        "nearest_cell_coverage_fraction": 1.0,
        "nearest_cell_allowed_client_exposure": True,
        "minimum_value": 10.1,
        "maximum_value": 12.9,
        "mean_value": 11.5,
        "last_observed_at": datetime(2026, 3, 14, tzinfo=UTC),
    }
    row.update(overrides)
    return row


def _neighbor_row(side: str, observed_day: date, offset: int, **overrides: object) -> dict[str, object]:
    """One row shaped like `parquet_reads.SIGNAL_TIME_NEIGHBORS` returns."""
    row: dict[str, object] = {
        "side": side,
        "signal_name": "soil_wetness_root_zone",
        "support_key": "surface",
        "normalized_unit": "fraction",
        "observed_day": observed_day,
        "nearest_cell_observed_at": datetime(observed_day.year, observed_day.month, observed_day.day, tzinfo=UTC),
        "day_offset": offset,
        "distance_days": abs(offset),
        "nearest_cell_value": 0.42,
        "nearest_cell_id": NEAR_CELL,
        "nearest_cell_distance_m": 4210.5,
    }
    row.update(overrides)
    return row


# --- 1. Value at the caller's day --------------------------------------------------


async def test_value_on_day_reads_the_caller_day_partition_and_never_the_live_edge() -> None:
    """The day is the PART FILE, not a lookback from now and not a predicate over a timestamp."""
    source = _signal_warehouse(published=[SELECTED_DATE, SELECTED_DATE - timedelta(days=1)])
    source.answer(VALUE_MARKER, [_value_row()])
    session = RecordingSession()
    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )

    addressed = source.part_uris_for(VALUE_MARKER)
    assert len(addressed) == 1
    assert "day=14" in addressed[0]
    assert "month=03" in addressed[0]
    assert "year=2026" in addressed[0]
    payload = json.loads(raw)
    assert payload["requested_day"] == SELECTED_DAY
    assert payload["signals_on_day"][0]["observed_day"] == SELECTED_DAY


async def test_value_on_day_reads_the_existing_coverage_audit_for_the_same_day() -> None:
    """The audit is a PostgreSQL governance ledger and is read over the day's two UTC midnights."""
    source = _signal_warehouse()
    source.answer(VALUE_MARKER, [_value_row()])
    source.answer(ADMITTED_MARKER, [{"cell_id": NEAR_CELL, "distance_meters": 4210.5}])
    session = RecordingSession()
    session.answer(AUDIT_MARKER, [{"signal_name": "air_temperature_mean", "status": "no_data"}])

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )

    bound = session.parameters_for(AUDIT_MARKER)
    assert bound["day_start"] == datetime(2026, 3, 14, tzinfo=UTC)
    assert bound["day_end"] == datetime(2026, 3, 15, tzinfo=UTC)
    assert json.loads(raw)["coverage_audit_on_day"][0]["status"] == "no_data"


async def test_value_on_day_reads_the_audit_over_exactly_the_cells_the_value_came_from() -> None:
    """An absence recorded somewhere else must not be able to explain this point."""
    source = _signal_warehouse()
    source.answer(VALUE_MARKER, [_value_row()])
    source.answer(
        ADMITTED_MARKER,
        [
            {"cell_id": NEAR_CELL, "distance_meters": 4210.5},
            {"cell_id": "bbbbbbbb-0000-0000-0000-000000000002", "distance_meters": 16857.9},
        ],
    )
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        await agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )

    bound = session.parameters_for(AUDIT_MARKER)
    assert bound["cell_ids"] == [NEAR_CELL, "bbbbbbbb-0000-0000-0000-000000000002"]
    assert bound["cell_distances"] == [4210.5, 16857.9]
    # The value read and the cell read share one admitted session, so they cannot disagree about
    # which cells were in range.
    assert source.markers() == [VALUE_MARKER, ADMITTED_MARKER]


async def test_value_on_day_refuses_an_unparseable_day_without_querying() -> None:
    """A day we cannot read is refused, never replaced with today -- that is a fabricated date."""
    source = _signal_warehouse()
    session = RecordingSession()
    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source) as ledger:
        raw = await agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
            day="last tuesday",
        )

    assert not session.statements
    assert source.markers() == []
    payload = json.loads(raw)
    assert payload["received_day"] == "last tuesday"
    assert "ISO calendar day" in payload["error"]
    assert ledger == [{"tool": "signal_value_on_day", "row_count": 0, "error": "invalid_day"}]


# --- 2. Temporal proximity ---------------------------------------------------------


async def test_temporal_neighbors_carry_their_own_date_and_their_real_gap() -> None:
    """A neighbour handed back without its gap is indistinguishable from an exact match."""
    source = _signal_warehouse(
        published=[SELECTED_DATE - timedelta(days=6), SELECTED_DATE, SELECTED_DATE + timedelta(days=2)]
    )
    source.answer(
        NEIGHBORS_MARKER,
        [
            _neighbor_row("before", SELECTED_DATE - timedelta(days=6), -6),
            _neighbor_row("after", SELECTED_DATE + timedelta(days=2), 2),
        ],
    )
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_signal_neighbors_in_time(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )

    payload = json.loads(raw)
    rows = {row["side"]: row for row in payload["temporal_neighbors"]}
    assert rows["before"]["observed_day"] == "2026-03-08"
    assert rows["before"]["distance_days"] == 6
    assert rows["before"]["day_offset"] == -6
    assert rows["after"]["day_offset"] == 2
    assert all(row["observed_day"] != payload["requested_day"] for row in payload["temporal_neighbors"])
    assert "never quote one of those as this day's value" not in payload["note"]
    assert "Never report one of these as the value on requested_day" in payload["note"]


async def test_temporal_neighbors_bind_the_selected_day_and_a_bounded_search_span() -> None:
    """The day travels four times because DuckDB counts positional parameters by appearance."""
    source = _signal_warehouse(published=[SELECTED_DATE - timedelta(days=1)])
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_signal_neighbors_in_time(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY, neighbor_days=20
        )

    bound = source.parameters_for(NEIGHBORS_MARKER)
    assert bound[-4:] == [SELECTED_DATE, SELECTED_DATE, SELECTED_DATE, SELECTED_DATE]
    bounds = json.loads(raw)["applied_bounds"]
    assert bounds["searched_from"] == "2026-02-22"
    assert bounds["searched_through"] == "2026-04-03"


async def test_temporal_neighbors_report_a_one_sided_result_without_inventing_the_other() -> None:
    """A missing side is a statement about the days scanned, and the state census says which."""
    source = _signal_warehouse(published=[SELECTED_DATE - timedelta(days=6)])
    source.answer(NEIGHBORS_MARKER, [_neighbor_row("before", SELECTED_DATE - timedelta(days=6), -6)])
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_signal_neighbors_in_time(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )

    payload = json.loads(raw)
    assert [row["side"] for row in payload["temporal_neighbors"]] == ["before"]
    assert payload["window_day_states"]["published"] == 1
    assert payload["window_day_states"]["day_not_written"] > 0


async def test_temporal_neighbors_clamp_an_over_wide_search_and_report_the_clamp() -> None:
    source = _signal_warehouse()
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_signal_neighbors_in_time(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY, neighbor_days=99_999
        )

    bounds = json.loads(raw)["applied_bounds"]
    assert bounds["neighbor_days"] == agent_tools.MAX_NEIGHBOR_DAYS


# --- 3. Spatial proximity ----------------------------------------------------------


async def test_nearest_cells_carry_their_real_distance_and_list_empty_cells_too() -> None:
    """An INNER join would make "the nearest cells" quietly mean "the ones that had data"."""
    source = _signal_warehouse(published=[SELECTED_DATE])
    source.answer(
        CELLS_MARKER,
        [
            {
                "cell_id": NEAR_CELL,
                "centroid_longitude": -116.25,
                "centroid_latitude": 43.62,
                "distance_meters": 4210.5,
                "observation_count_on_day": 4,
                "signal_count_on_day": 2,
                "last_observed_at": datetime(2026, 3, 14, tzinfo=UTC),
            },
            {
                "cell_id": "bbbbbbbb-0000-0000-0000-000000000002",
                "centroid_longitude": -116.05,
                "centroid_latitude": 43.55,
                "distance_meters": 16857.9,
                "observation_count_on_day": 0,
                "signal_count_on_day": 0,
                "last_observed_at": None,
            },
        ],
    )
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_nearest_signal_cells(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )

    cells = json.loads(raw)["nearest_cells"]
    assert [cell["observation_count_on_day"] for cell in cells] == [4, 0]
    assert cells[0]["distance_meters"] < cells[1]["distance_meters"]
    assert all("cell_id" in cell for cell in cells)
    # The grid name and resolution the retired registry carried are omitted, never nulled.
    assert all("grid_name" not in cell and "resolution_m" not in cell for cell in cells)


async def test_nearest_cells_bind_the_day_and_clamp_the_requested_count() -> None:
    source = _signal_warehouse(published=[SELECTED_DATE])
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_nearest_signal_cells(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY, cell_count=9_999
        )

    bound = source.parameters_for(CELLS_MARKER)
    assert bound[-2] == SELECTED_DATE
    assert bound[-1] == agent_tools.MAX_NEAREST_CELLS
    assert json.loads(raw)["applied_bounds"]["cell_count"] == agent_tools.MAX_NEAREST_CELLS


async def test_nearest_cells_returns_an_empty_list_rather_than_widening_the_radius() -> None:
    """No cell inside the radius is an answer about the radius, and the day state explains it."""
    source = _signal_warehouse(published=[SELECTED_DATE])
    source.answer(CELLS_MARKER, [])
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_nearest_signal_cells(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY, radius_meters=100.0
        )

    payload = json.loads(raw)
    assert payload["nearest_cells"] == []
    assert payload["day_state"]["state"] == "published"
    assert payload["applied_bounds"]["radius_meters"] == agent_tools.MIN_RADIUS_METERS


# --- Shared guarantees -------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name",
    ["signal_value_on_day", "signal_neighbors_in_time", "nearest_signal_cells"],
)
async def test_selected_day_tools_reject_a_bad_coordinate_without_querying(tool_name: str) -> None:
    """A bad coordinate must never reach the warehouse, on any of the three."""
    source = _signal_warehouse()
    session = RecordingSession()
    call = getattr(agent_tools, f"query_{tool_name}")
    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source) as ledger:
        raw = await call(longitude=999.0, latitude=BOISE_LATITUDE, day=SELECTED_DAY)

    assert not session.statements
    assert source.markers() == []
    assert "error" in json.loads(raw)
    assert ledger == [{"tool": tool_name, "row_count": 0, "error": "invalid_coordinate"}]


async def test_selected_day_statements_are_read_only_and_named_by_a_line_one_marker() -> None:
    """Every statement the three tools issue, in EITHER dialect, is a SELECT and carries a marker."""
    source = _signal_warehouse(published=[SELECTED_DATE - timedelta(days=1), SELECTED_DATE])
    source.answer(VALUE_MARKER, [_value_row()])
    source.answer(ADMITTED_MARKER, [{"cell_id": NEAR_CELL, "distance_meters": 4210.5}])
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        await agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )
        await agent_tools.query_signal_neighbors_in_time(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )
        await agent_tools.query_nearest_signal_cells(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )

    assert source.markers() == [VALUE_MARKER, ADMITTED_MARKER, NEIGHBORS_MARKER, CELLS_MARKER]
    # The only PostgreSQL statement any of the three still issues is the absence ledger.
    assert session.markers() == [AUDIT_MARKER]
    for statement, _ in [*source.executed, *session.statements]:
        assert statement.splitlines()[0].strip().startswith("-- agent_")
        # The beginner-doc headers are prose and legitimately contain English words that collide
        # with SQL verbs ("drops the rest"); only executable lines are scanned.
        executable = "\n".join(line for line in statement.splitlines() if not line.lstrip().startswith("--")).upper()
        for verb in ("INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE", "CREATE ", "DROP", "ALTER"):
            assert verb not in executable, f"{verb} must not appear in an agent tool statement"


def test_selected_day_tools_publish_a_required_day_argument() -> None:
    """The day is required in the schema: a defaulted day is how a tool drifts back to 'latest'."""
    published = {
        "signal_value_on_day": agent_tools.signal_value_on_day,
        "signal_neighbors_in_time": agent_tools.signal_neighbors_in_time,
        "nearest_signal_cells": agent_tools.nearest_signal_cells,
    }
    for name, tool in published.items():
        definition = tool.to_dict()
        schema = definition["input_schema"]
        assert schema["properties"]["day"]["type"] == "string", name
        assert set(schema["required"]) == {"longitude", "latitude", "day"}, name
        assert tool in agent_tools.WAREHOUSE_TOOLS, name


# --- The day the model is told to use ----------------------------------------------


def test_location_context_states_the_selected_day() -> None:
    """The map's day reaches the model as a fact, so it has something exact to pass to the tools."""
    context = build_location_context(
        longitude=BOISE_LONGITUDE,
        latitude=BOISE_LATITUDE,
        precision="exact",
        as_of=datetime(2026, 8, 11, 12, 0, tzinfo=UTC),
        question=None,
        selected_day=SELECTED_DATE,
    )
    assert "## Selected day (the day the map is showing)" in context
    assert "2026-03-14" in context
    assert "Pass this exact day to every signal tool." in context


def test_location_context_says_so_when_no_day_was_supplied() -> None:
    """Standing in today's date is stated outright, never passed off as the caller's selection."""
    context = build_location_context(
        longitude=BOISE_LONGITUDE,
        latitude=BOISE_LATITUDE,
        precision="exact",
        as_of=datetime(2026, 8, 11, 12, 0, tzinfo=UTC),
        question=None,
    )
    assert "did not carry the map's selected day" in context
    assert "2026-08-11" in context


def test_the_analyze_route_accepts_the_selected_day_and_carries_it_into_the_request() -> None:
    """Without this the tools are always asked about today and answer nothing on every real call.

    The ingress model forbids extra fields, so an un-declared `selected_day` is a 400 rather than a
    silent drop -- and today is past the live edge of every lane, so the whole selected-day surface
    would return empty results for locations holding four years of data.
    """
    payload = AgentAnalyzeRequest.model_validate(
        {"longitude": BOISE_LONGITUDE, "latitude": BOISE_LATITUDE, "selected_day": SELECTED_DAY}
    )

    assert payload.selected_day == SELECTED_DATE
    assert (
        AgentAnalyzeRequest.model_validate({"longitude": BOISE_LONGITUDE, "latitude": BOISE_LATITUDE}).selected_day
        is None
    )
    assert "selected_day" in AgentRequest.__dataclass_fields__


# --- Row caps are stated, never silently applied -----------------------------------


async def test_a_list_that_hit_its_row_cap_says_so_rather_than_reading_as_an_absence() -> None:
    """A cap silently reached turns "not in the list" into a fabricated absence for the model.

    Both notes tell the model that a missing signal or a missing side is a statement about the data.
    That is only true while the list is complete, so the completeness has to travel with it.
    """
    source = _signal_warehouse(published=[SELECTED_DATE - timedelta(days=6)])
    source.answer(
        NEIGHBORS_MARKER,
        [
            _neighbor_row("before", SELECTED_DATE - timedelta(days=6), -6, signal_name=f"signal_{index:03d}")
            for index in range(agent_tools.MAX_TEMPORAL_NEIGHBOR_ROWS + 5)
        ],
    )
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_signal_neighbors_in_time(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )

    payload = json.loads(raw)
    assert len(payload["temporal_neighbors"]) == agent_tools.MAX_TEMPORAL_NEIGHBOR_ROWS
    assert payload["temporal_neighbors_truncated"] is True
    assert "temporal_neighbors_truncated is true" in payload["note"]


async def test_an_untruncated_answer_states_its_completeness_too() -> None:
    source = _signal_warehouse()
    source.answer(VALUE_MARKER, [_value_row()])
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )

    payload = json.loads(raw)
    assert payload["signals_on_day_truncated"] is False
    assert payload["coverage_audit_on_day_truncated"] is False


# --- 4. An unbuilt plane is refused, never answered as an absence -------------------


async def test_an_unwritten_signal_lane_is_refused_by_name_rather_than_answered_empty() -> None:
    """The bug class this guard exists for, in its Parquet spelling.

    Against PostgreSQL it was a matview created WITH NO DATA and never refreshed. Against Parquet it
    is a lane that has written nothing at the rung the agent reads. The two available failures are
    still "the tool errored" and "the tool returned nothing", and the second is still far worse,
    because "no signal here" and "the lane was never published" become one answer to the model.
    """
    source = FakeAgentWarehouse()
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source) as ledger:
        raw = await agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )

    assert source.markers() == [], "an unwritten lane is never read from"
    payload = json.loads(raw)
    assert payload["error"] == "parquet_lane_never_written"
    assert payload["unwritten_lanes"] == [SIGNAL_LANE]
    assert "REFUSAL, not an absence" in payload["note"]
    assert ledger[0]["error"] == "parquet_lane_never_written"


async def test_a_relation_the_probe_never_mentions_counts_as_unbuilt() -> None:
    """Silence about a plane is not evidence that the plane is fine -- fail closed, not open.

    The probe now guards ONE relation, the governed ML forecast plane, because every environmental
    plane moved to Parquet where "never built" is a state the warehouse reports rather than a
    catalog fact to be inferred.
    """
    source = _signal_warehouse()
    session = RecordingSession()
    session.answer(PLANE_MARKER, [])

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_forecast_summary_for_cell(longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE)

    assert session.markers() == [PLANE_MARKER]
    payload = json.loads(raw)
    assert payload["error"] == "pre_aggregated_plane_unbuilt"
    assert payload["unbuilt_relations"] == [agent_tools.FORECAST_DAILY_RELATION]


# --- 5. The generic surface triad, for the layers that are not signal grids ---------


def test_the_agent_catalogue_is_the_map_catalogue_hand_spelled() -> None:
    """24 names, spelled out here so a surface silently dropped from the map is caught, not copied.

    Deliberately NOT derived from geo.layers, from the lane registry or from the TypeScript
    constants. A generated list drifts with the thing it is meant to check: a layer that vanished
    would vanish from the agent's vocabulary too, and the agent would say "I do not know that
    surface" instead of "that surface stopped being served". Same reasoning as the hand-spelled
    assertion docs/layer-lane-standard.md section 9 requires of the slider capability catalogue.
    """
    expected = {
        # The 11 feature-backed surfaces (drizzle 0001, 0011, 0013, 0017).
        "burn-severity",
        "evacuation-zones",
        "fire-detections",
        "fire-perimeters",
        "interventions",
        "sensors",
        "soil-survey",
        "vegetation",
        "watersheds",
        "water-gauges",
        "weather-observations",
        # The 4 SLIDER_STREAM_LAYER_NAMES entries (src/types/time-slider.ts).
        "drought-areas",
        "soil-field-moisture",
        "soil-field-temperature",
        "soil-field-vpd",
        # The 9 climate-field streams (CLIMATE_FIELD_SIGNAL_IDS, src/lib/environmental/climate-field.ts).
        "climate-field-air-temperature",
        "climate-field-dew-point",
        "climate-field-precipitation",
        "climate-field-relative-humidity",
        "climate-field-shortwave-radiation",
        "climate-field-soil-wetness-profile",
        "climate-field-soil-wetness-root-zone",
        "climate-field-soil-wetness-surface",
        "climate-field-wind-speed",
    }
    assert set(agent_tools.AGENT_SURFACE_NAMES) == expected
    assert len(agent_tools.AGENT_SURFACE_NAMES) == len(expected)
    # The feature-backed subset is exactly the eleven, and disjoint from the streams.
    assert set(agent_tools.FEATURE_SURFACE_NAMES) < set(agent_tools.AGENT_SURFACE_NAMES)
    assert not set(agent_tools.FEATURE_SURFACE_NAMES) & set(agent_tools.STREAM_SURFACE_NAMES)


async def test_surface_coverage_reads_the_index_the_slider_reads() -> None:
    """The agent and the slider must agree about which days exist, so they read one evidence source."""
    source = FakeAgentWarehouse()
    source.evidence["vegetation"] = published_lane(
        "vegetation",
        [date(2022, 8, 6), date(2026, 8, 2)],
        source_ceiling_day=date(2026, 8, 2),
    )
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_observation_coverage_on_day(surface_name="vegetation", day=SELECTED_DAY)

    payload = json.loads(raw)
    coverage = payload["coverage"]
    assert coverage["is_covered"] is False
    assert coverage["coverage_authority"] == "availability"
    # An uncovered day is not the end of the answer: the history bounds say WHICH kind of absence.
    assert coverage["earliest_observed_day"] == "2022-08-06"
    assert coverage["latest_observed_day"] == "2026-08-02"
    assert coverage["source_ceiling_day"] == "2026-08-02"
    assert "past what the source itself could have published" in payload["note"]
    assert session.statements == [], "coverage is a Parquet question and touches no database"


async def test_surface_temporal_neighbors_carry_their_real_gap() -> None:
    """A neighbour without its gap is indistinguishable from an exact answer, on every surface."""
    source = FakeAgentWarehouse()
    source.evidence["vegetation"] = published_lane("vegetation", [date(2026, 3, 3)], row_count=412)
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_observation_temporal_neighbors(
            surface_name="vegetation",
            day=SELECTED_DAY,
            neighbor_days=20,
        )

    payload = json.loads(raw)
    bounds = payload["applied_bounds"]
    assert bounds["searched_from"] == "2026-02-22"
    assert bounds["searched_through"] == "2026-04-03"
    only = payload["temporal_neighbors"][0]
    assert (only["side"], only["observed_day"], only["distance_days"], only["day_offset"]) == (
        "before",
        "2026-03-03",
        11,
        -11,
    )
    assert only["observation_count"] == 412
    assert only["observed_day"] != payload["requested_day"]
    assert "never quote one as the value on requested_day" in payload["note"]


async def test_surface_temporal_neighbors_clamp_an_over_wide_window_and_report_it() -> None:
    source = FakeAgentWarehouse()
    source.evidence["fire-detections"] = published_lane("fire-detections", [date(2026, 3, 3)])
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_observation_temporal_neighbors(
            surface_name="fire-detections",
            day=SELECTED_DAY,
            neighbor_days=99_999,
        )

    bounds = json.loads(raw)["applied_bounds"]
    assert bounds["neighbor_days"] == agent_tools.MAX_SURFACE_NEIGHBOR_DAYS


async def test_feature_value_near_point_dates_features_by_the_partition_the_map_asks_for() -> None:
    """One day rule, shared not re-derived, or the agent dates a feature differently from the tiles."""
    source = FakeAgentWarehouse()
    source.listing_store.write_day("water-gauges", "observed", 13, SELECTED_DATE)
    source.answer(
        POINT_LANE_MARKER,
        [
            {
                "site_number": "13206000",
                "site_name": "Boise River",
                "observed_day": SELECTED_DATE,
                "flow_cfs": 1420.0,
                "longitude": -116.19,
                "latitude": 43.61,
                "distance_meters": 812.4,
            }
        ],
    )
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_feature_value_near_point(
            surface_name="water-gauges",
            day=SELECTED_DAY,
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
        )

    addressed = source.part_uris_for(POINT_LANE_MARKER)
    assert len(addressed) == 1
    assert "layer=water-gauges" in addressed[0]
    assert "year=2026/month=03/day=14" in addressed[0]
    payload = json.loads(raw)
    nearest = payload["features"][0]
    assert nearest["distance_meters"] == 812.4
    assert nearest["distance_basis"] == "point"
    assert nearest["served_day"] == SELECTED_DAY
    assert nearest["properties"]["site_number"] == "13206000"
    assert session.statements == [], "a Parquet-served layer touches no database"


def test_the_bounding_box_widens_with_latitude_rather_than_clipping() -> None:
    """A single metres-per-degree constant clips the east-west edges away from the equator."""
    at_equator = agent_tools._bbox_degrees(50_000.0, 0.0)
    at_boise = agent_tools._bbox_degrees(50_000.0, BOISE_LATITUDE)
    at_high_latitude = agent_tools._bbox_degrees(50_000.0, 70.0)
    assert at_equator < at_boise < at_high_latitude
    # Every box must still contain the radius it stands in for, north-south as well as east-west.
    assert at_equator >= 50_000.0 / 110_574.0


def test_the_per_axis_box_is_wider_east_west_than_north_south_away_from_the_equator() -> None:
    """The Parquet predicate compares each axis on its own, so each axis is sized on its own figure."""
    west, south, east, north = agent_tools._bbox_bounds(BOISE_LONGITUDE, BOISE_LATITUDE, 50_000.0)
    longitude_half_width = east - BOISE_LONGITUDE
    latitude_half_width = north - BOISE_LATITUDE
    assert longitude_half_width > latitude_half_width
    assert BOISE_LONGITUDE - west == pytest.approx(longitude_half_width)
    assert BOISE_LATITUDE - south == pytest.approx(latitude_half_width)
    # Both half-widths must still contain the radius they stand in for.
    assert latitude_half_width >= 50_000.0 / 110_574.0


async def test_feature_value_near_point_refuses_a_stream_surface_by_name() -> None:
    """A cell-grid stream has no features; answering it with an empty list would read as absence."""
    source = FakeAgentWarehouse()
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source) as ledger:
        raw = await agent_tools.query_feature_value_near_point(
            surface_name="climate-field-air-temperature",
            day=SELECTED_DAY,
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
        )

    assert not session.statements
    assert source.markers() == []
    payload = json.loads(raw)
    assert payload["received_surface_name"] == "climate-field-air-temperature"
    assert "refusal, not an absence" in payload["note"]
    assert ledger == [{"tool": "feature_value_near_point", "row_count": 0, "error": "unsupported_surface"}]


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("observation_coverage_on_day", {}),
        ("observation_temporal_neighbors", {}),
        (
            "feature_value_near_point",
            {"longitude": BOISE_LONGITUDE, "latitude": BOISE_LATITUDE},
        ),
    ],
)
async def test_surface_tools_refuse_a_name_outside_the_catalogue(tool_name: str, arguments: dict[str, object]) -> None:
    """An unknown surface is refused with the catalogue listed, never answered as empty."""
    source = FakeAgentWarehouse()
    session = RecordingSession()
    call = getattr(agent_tools, f"query_{tool_name}")
    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await call(surface_name="space-lasers", day=SELECTED_DAY, **arguments)

    assert not session.statements
    assert source.markers() == []
    payload = json.loads(raw)
    assert payload["received_surface_name"] == "space-lasers"
    assert "refusal, not an absence" in payload["note"]


def test_the_surface_triad_publishes_required_surface_and_day_arguments() -> None:
    """Neither may be defaulted: a defaulted surface or day is how a tool answers a different question."""
    published = {
        "observation_coverage_on_day": agent_tools.observation_coverage_on_day,
        "observation_temporal_neighbors": agent_tools.observation_temporal_neighbors,
        "feature_value_near_point": agent_tools.feature_value_near_point,
    }
    for name, tool in published.items():
        schema = tool.to_dict()["input_schema"]
        assert schema["properties"]["surface_name"]["type"] == "string", name
        assert schema["properties"]["day"]["type"] == "string", name
        assert {"surface_name", "day"} <= set(schema["required"]), name
        assert tool in agent_tools.WAREHOUSE_TOOLS, name
