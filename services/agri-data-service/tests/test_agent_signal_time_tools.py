"""The three selected-day agent tools: exact day, temporal neighbours, spatial neighbours.

The whole file exists to hold one line of the layer-lane standard, section 11: a proximity answer
that arrives without its distance and without the observation's own date is indistinguishable from
an exact answer, and is the same class of bug as a lane reporting success having written nothing.

No database. `RecordingSession` answers `AsyncSession.execute` by the bare `-- <marker>` comment
each statement opens with, the same seam `test_ingest_reconcile.py` and `test_jobs_lease.py` use,
so every statement is asserted on its real text and its real bound parameters.
"""

# ruff: noqa: PLR2004 - the literals here are fixture values, and naming each one hides the assertion.

from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.agent import tools as agent_tools
from agri_data_service.agent.graph import AgentRequest
from agri_data_service.agent.prompts import build_location_context
from agri_data_service.routes.agent_analysis import AgentAnalyzeRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

_MARKER = re.compile(r"^--\s+(\w+)\s*$", re.MULTILINE)

VALUE_MARKER = "agent_signal_value_on_day"
COVERAGE_MARKER = "agent_signal_coverage_on_day"
NEIGHBORS_MARKER = "agent_signal_neighbors_in_time"
CELLS_MARKER = "agent_nearest_signal_cells"

BOISE_LONGITUDE = -116.2
BOISE_LATITUDE = 43.6
# A day inside every contracted lane's horizon (execution/coverage_contract.py, verified 2026-08-11).
SELECTED_DAY = "2026-03-14"


class FakeResult:
    """The narrow slice of SQLAlchemy's Result the agent tools actually use."""

    def __init__(self, rows: Sequence[Mapping[str, object]]) -> None:
        self._rows = list(rows)

    def mappings(self) -> FakeResult:
        """Return self, because the fake already yields mappings."""
        return self

    def all(self) -> list[Mapping[str, object]]:
        """Every row the statement returned."""
        return list(self._rows)


class RecordingSession:
    """An AsyncSession stand-in that records every statement and answers it by its marker comment."""

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
        self.statements.append((sql, dict(parameters or {})))
        return FakeResult(self._answers.get(self.marker_of(sql) or "", []))

    @staticmethod
    def marker_of(sql: str) -> str | None:
        """The bare `-- <name>` marker a statement opens with."""
        found = _MARKER.search(sql)
        return None if found is None else found.group(1)

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


def _value_row(**overrides: object) -> dict[str, object]:
    """One row shaped like signal_value_on_day.sql returns."""
    row: dict[str, object] = {
        "signal_name": "air_temperature_mean",
        "source_parameter": "T2M",
        "support_key": "surface",
        "normalized_unit": "degC",
        "observation_count": 3,
        "cell_count": 3,
        "nearest_cell_distance_m": 4210.5,
        "nearest_cell_key": "nasa-power-0.5-degree/43.5/-116.5",
        "nearest_cell_grid_name": "nasa-power-0.5-degree",
        "nearest_cell_value": 11.4,
        "nearest_cell_observed_at": datetime(2026, 3, 14, tzinfo=UTC),
        "minimum_value": 10.1,
        "maximum_value": 12.9,
        "mean_value": 11.5,
        "first_observed_at": datetime(2026, 3, 14, tzinfo=UTC),
        "last_observed_at": datetime(2026, 3, 14, tzinfo=UTC),
    }
    row.update(overrides)
    return row


def _neighbor_row(side: str, observed_day: date, offset: int, **overrides: object) -> dict[str, object]:
    """One row shaped like signal_neighbors_in_time.sql returns."""
    row: dict[str, object] = {
        "side": side,
        "signal_name": "soil_wetness_root_zone",
        "source_parameter": "GWETROOT",
        "support_key": "surface",
        "normalized_unit": "fraction",
        "observed_day": observed_day,
        "nearest_cell_observed_at": datetime(observed_day.year, observed_day.month, observed_day.day, tzinfo=UTC),
        "day_offset": offset,
        "distance_days": abs(offset),
        "nearest_cell_value": 0.42,
        "nearest_cell_key": "nasa-power-0.5-degree/43.5/-116.5",
        "nearest_cell_grid_name": "nasa-power-0.5-degree",
        "nearest_cell_distance_m": 4210.5,
    }
    row.update(overrides)
    return row


# --- 1. Value at the caller's day --------------------------------------------------


async def test_value_on_day_asks_for_the_caller_day_and_never_the_live_edge() -> None:
    """The bound window is the selected day's own midnights, not a lookback from now."""
    session = RecordingSession()
    session.answer(VALUE_MARKER, [_value_row()])
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
            day=SELECTED_DAY,
        )

    bound = session.parameters_for(VALUE_MARKER)
    assert bound["day_start"] == datetime(2026, 3, 14, tzinfo=UTC)
    assert bound["day_end"] == datetime(2026, 3, 15, tzinfo=UTC)
    payload = json.loads(raw)
    assert payload["requested_day"] == SELECTED_DAY
    assert payload["applied_bounds"]["requested_day"] == SELECTED_DAY
    # The row's own recorded time survives to the model, so the answer is not merely the day asked for.
    assert payload["signals_on_day"][0]["nearest_cell_observed_at"].startswith(SELECTED_DAY)
    assert payload["signals_on_day"][0]["nearest_cell_distance_m"] == 4210.5


async def test_value_on_day_reads_the_existing_coverage_audit_for_the_same_day() -> None:
    """An empty day is explained from agri.signal_coverage_audit rather than left ambiguous."""
    session = RecordingSession()
    session.answer(
        COVERAGE_MARKER,
        [
            {
                "signal_name": "vapor_pressure_deficit",
                "source_parameter": "vpd",
                "support_key": "era5-land-0.1deg",
                "status": "no_data",
                "audit_row_count": 4,
                "cell_count": 4,
                "expected_observation_count": 4,
                "received_observation_count": 0,
                "earliest_window_start": datetime(2026, 3, 1, tzinfo=UTC),
                "latest_window_end": datetime(2026, 3, 31, 23, 59, 59, tzinfo=UTC),
                "nearest_cell_distance_m": 4210.5,
            }
        ],
    )
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
            day=SELECTED_DAY,
        )

    assert session.markers() == [VALUE_MARKER, COVERAGE_MARKER]
    assert "agri.signal_coverage_audit" in session.sql_for(COVERAGE_MARKER)
    payload = json.loads(raw)
    assert payload["signals_on_day"] == []
    governed = payload["coverage_audit_on_day"][0]
    assert governed["status"] == "no_data"
    assert governed["received_observation_count"] == 0
    assert "no_data" in payload["note"]


async def test_value_on_day_reads_the_audit_over_exactly_the_cells_the_value_came_from() -> None:
    """A wider audit scope would let an absence recorded elsewhere appear to explain this point."""
    session = RecordingSession()
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        await agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
            day=SELECTED_DAY,
            radius_meters=25_000.0,
            signal_names=["precipitation"],
        )

    value_bound = session.parameters_for(VALUE_MARKER)
    audit_bound = session.parameters_for(COVERAGE_MARKER)
    for shared in ("longitude", "latitude", "radius_meters", "cell_limit", "day_start", "day_end", "signal_names"):
        assert value_bound[shared] == audit_bound[shared], f"{shared} must be scoped identically"


async def test_value_on_day_refuses_an_unparseable_day_without_querying() -> None:
    """A day we cannot read is refused, never replaced with today -- that is a fabricated date."""
    session = RecordingSession()
    async with agent_tools.run_context(session_provider=_session_provider(session)) as ledger:
        raw = await agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
            day="last tuesday",
        )

    assert not session.statements
    payload = json.loads(raw)
    assert payload["received_day"] == "last tuesday"
    assert "ISO calendar day" in payload["error"]
    assert ledger == [{"tool": "signal_value_on_day", "row_count": 0, "error": "invalid_day"}]


# --- 2. Temporal proximity ---------------------------------------------------------


async def test_temporal_neighbors_carry_their_own_date_and_their_real_gap() -> None:
    """A neighbour without its distance and its date is indistinguishable from an exact answer."""
    session = RecordingSession()
    session.answer(
        NEIGHBORS_MARKER,
        [
            _neighbor_row("before", date(2026, 3, 8), -6),
            _neighbor_row("after", date(2026, 3, 19), 5),
        ],
    )
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_signal_neighbors_in_time(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
            day=SELECTED_DAY,
        )

    payload = json.loads(raw)
    before, after = payload["temporal_neighbors"]
    assert (before["side"], before["observed_day"], before["distance_days"], before["day_offset"]) == (
        "before",
        "2026-03-08",
        6,
        -6,
    )
    assert (after["side"], after["observed_day"], after["distance_days"], after["day_offset"]) == (
        "after",
        "2026-03-19",
        5,
        5,
    )
    for row in payload["temporal_neighbors"]:
        assert row["nearest_cell_distance_m"] == 4210.5
        assert row["observed_day"] != payload["requested_day"]


async def test_temporal_neighbors_bind_the_selected_day_and_a_bounded_search_span() -> None:
    """The day drives the split; the span is what makes 'no neighbour' a claim about the window."""
    session = RecordingSession()
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_signal_neighbors_in_time(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
            day=SELECTED_DAY,
            neighbor_days=10,
        )

    bound = session.parameters_for(NEIGHBORS_MARKER)
    assert bound["day"] == date(2026, 3, 14)
    assert bound["search_start"] == datetime(2026, 3, 4, tzinfo=UTC)
    assert bound["search_end"] == datetime(2026, 3, 25, tzinfo=UTC)
    bounds = json.loads(raw)["applied_bounds"]
    assert bounds["neighbor_days"] == 10
    assert bounds["searched_from"] == "2026-03-04"
    assert bounds["searched_through"] == "2026-03-24"


async def test_temporal_neighbors_report_a_one_sided_result_without_inventing_the_other() -> None:
    """A signal with no reading after the day gets no 'after' row, and the note says what that means."""
    session = RecordingSession()
    session.answer(NEIGHBORS_MARKER, [_neighbor_row("before", date(2026, 3, 13), -1)])
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_signal_neighbors_in_time(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
            day=SELECTED_DAY,
        )

    payload = json.loads(raw)
    assert [row["side"] for row in payload["temporal_neighbors"]] == ["before"]
    assert "not about all of history" in payload["note"]


async def test_temporal_neighbors_clamp_an_over_wide_search_and_report_the_clamp() -> None:
    """An over-large window is clamped by the service, and the clamp is echoed back."""
    session = RecordingSession()
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_signal_neighbors_in_time(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
            day=SELECTED_DAY,
            radius_meters=10_000_000.0,
            neighbor_days=99_999,
        )

    bounds = json.loads(raw)["applied_bounds"]
    assert bounds["neighbor_days"] == agent_tools.MAX_NEIGHBOR_DAYS
    assert bounds["radius_meters"] == agent_tools.MAX_RADIUS_METERS
    assert session.parameters_for(NEIGHBORS_MARKER)["radius_meters"] == agent_tools.MAX_RADIUS_METERS


# --- 3. Spatial proximity ----------------------------------------------------------


async def test_nearest_cells_carry_their_real_distance_and_list_empty_cells_too() -> None:
    """A nearer cell holding nothing must stay visible, or 'nearest' silently means 'nearest with data'."""
    session = RecordingSession()
    session.answer(
        CELLS_MARKER,
        [
            {
                "cell_key": "nasa-power-0.5-degree/43.5/-116.5",
                "grid_name": "nasa-power-0.5-degree",
                "resolution_m": 55_000,
                "centroid_longitude": -116.5,
                "centroid_latitude": 43.5,
                "distance_meters": 4210.5,
                "observation_count_on_day": 0,
                "signal_count_on_day": 0,
                "first_observed_at": None,
                "last_observed_at": None,
            },
            {
                "cell_key": "nasa-power-0.5-degree/44.0/-116.5",
                "grid_name": "nasa-power-0.5-degree",
                "resolution_m": 55_000,
                "centroid_longitude": -116.5,
                "centroid_latitude": 44.0,
                "distance_meters": 44_800.0,
                "observation_count_on_day": 11,
                "signal_count_on_day": 11,
                "first_observed_at": datetime(2026, 3, 14, tzinfo=UTC),
                "last_observed_at": datetime(2026, 3, 14, tzinfo=UTC),
            },
        ],
    )
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_nearest_signal_cells(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
            day=SELECTED_DAY,
        )

    payload = json.loads(raw)
    nearest, populated = payload["nearest_cells"]
    assert nearest["observation_count_on_day"] == 0
    assert nearest["distance_meters"] < populated["distance_meters"]
    assert populated["observation_count_on_day"] == 11
    # The count is a census over ONE write plane. Saying "0 is an answer" without naming the plane
    # is the failure the layer lane standard calls out: NDVI lands on the forecast plane and is not
    # counted here, so an unqualified zero reads to the model as "this cell holds nothing".
    assert "SIGNAL plane" in payload["note"]
    assert "not a claim that the cell is empty" in payload["note"]
    # LEFT JOIN is what keeps the empty cell in the answer; an INNER join would drop it.
    assert "LEFT JOIN day_observations" in session.sql_for(CELLS_MARKER)


async def test_nearest_cells_bind_the_day_and_clamp_the_requested_count() -> None:
    """The count is capped by the service and the cap is reported, matching the other tools."""
    session = RecordingSession()
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_nearest_signal_cells(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
            day=SELECTED_DAY,
            cell_count=5_000,
            grid_names=["nasa-power-0.5-degree", "nasa-power-0.5-degree", " "],
        )

    bound = session.parameters_for(CELLS_MARKER)
    assert bound["row_limit"] == agent_tools.MAX_NEAREST_CELLS
    assert bound["cell_limit"] == agent_tools.MAX_CELL_FANOUT
    assert bound["day_start"] == datetime(2026, 3, 14, tzinfo=UTC)
    assert bound["day_end"] == datetime(2026, 3, 15, tzinfo=UTC)
    # Duplicates and blanks are dropped, so the grid filter matches agri.spatial_cell.grid_name exactly.
    assert bound["grid_names"] == ["nasa-power-0.5-degree"]
    assert json.loads(raw)["applied_bounds"]["cell_count"] == agent_tools.MAX_NEAREST_CELLS


async def test_nearest_cells_returns_an_empty_list_rather_than_widening_the_radius() -> None:
    """No cell in range is an answer; a tool that quietly widened its radius would be lying."""
    session = RecordingSession()
    async with agent_tools.run_context(session_provider=_session_provider(session)) as ledger:
        raw = await agent_tools.query_nearest_signal_cells(
            longitude=0.0,
            latitude=0.0,
            day=SELECTED_DAY,
            radius_meters=100.0,
        )

    assert len(session.statements) == 1
    assert json.loads(raw)["nearest_cells"] == []
    assert ledger[0]["row_count"] == 0
    assert ledger[0]["radius_meters"] == agent_tools.MIN_RADIUS_METERS


# --- Shared guarantees -------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name",
    ["signal_value_on_day", "signal_neighbors_in_time", "nearest_signal_cells"],
)
async def test_selected_day_tools_reject_a_bad_coordinate_without_querying(tool_name: str) -> None:
    """A bad coordinate must never reach the database, on any of the three."""
    session = RecordingSession()
    call = getattr(agent_tools, f"query_{tool_name}")
    async with agent_tools.run_context(session_provider=_session_provider(session)) as ledger:
        raw = await call(longitude=999.0, latitude=BOISE_LATITUDE, day=SELECTED_DAY)

    assert not session.statements
    assert "error" in json.loads(raw)
    assert ledger == [{"tool": tool_name, "row_count": 0, "error": "invalid_coordinate"}]


async def test_selected_day_statements_are_read_only_and_named_by_a_line_one_marker() -> None:
    """Every new statement is a SELECT, and dispatchable by the marker this suite matches on."""
    session = RecordingSession()
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        await agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )
        await agent_tools.query_signal_neighbors_in_time(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )
        await agent_tools.query_nearest_signal_cells(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )

    assert session.markers() == [VALUE_MARKER, COVERAGE_MARKER, NEIGHBORS_MARKER, CELLS_MARKER]
    for sql, _ in session.statements:
        assert sql.splitlines()[0].strip().startswith("-- agent_")
        # The beginner-doc headers are prose and legitimately contain English words that collide
        # with SQL verbs ("drops the rest"); only executable lines are scanned.
        executable = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--")).upper()
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
        selected_day=date(2026, 3, 14),
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

    assert payload.selected_day == date(2026, 3, 14)
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
    session = RecordingSession()
    session.answer(
        NEIGHBORS_MARKER,
        [
            _neighbor_row("before", date(2026, 3, 8), -6, signal_name=f"signal_{index:03d}")
            for index in range(agent_tools.MAX_TEMPORAL_NEIGHBOR_ROWS)
        ],
    )
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_signal_neighbors_in_time(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )

    payload = json.loads(raw)
    assert len(payload["temporal_neighbors"]) == agent_tools.MAX_TEMPORAL_NEIGHBOR_ROWS
    assert payload["temporal_neighbors_truncated"] is True
    assert "temporal_neighbors_truncated is true" in payload["note"]


async def test_an_untruncated_answer_states_its_completeness_too() -> None:
    session = RecordingSession()
    session.answer(VALUE_MARKER, [_value_row()])
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )

    payload = json.loads(raw)
    assert payload["signals_on_day_truncated"] is False
    assert payload["coverage_audit_on_day_truncated"] is False
