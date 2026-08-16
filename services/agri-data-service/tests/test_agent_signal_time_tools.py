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
PLANE_MARKER = "agent_materialized_plane_populated"
SURFACE_COVERAGE_MARKER = "agent_observation_coverage_on_day"
SURFACE_NEIGHBORS_MARKER = "agent_observation_temporal_neighbors"
FEATURE_NEAR_MARKER = "agent_feature_value_near_point"

BOISE_LONGITUDE = -116.2
BOISE_LATITUDE = 43.6
# A day inside every contracted lane's horizon (execution/coverage_contract.py, verified 2026-08-11).
SELECTED_DAY = "2026-03-14"


def executable_sql(statement: str) -> str:
    """`statement` with every `--` comment line blanked out.

    Every `.sql` file in this service opens with a walkthrough that NAMES the relation the query
    was repointed away from, because "reads X, not the 26 GB Y" is the single most useful thing a
    reader can learn from the header. A bare `"Y" not in statement` therefore fails on the
    explanation rather than on the query. Assertions about what a statement READS are made against
    the executable text; assertions about what it SAYS can still use the raw string.
    """
    return "\n".join("" if line.lstrip().startswith("--") else line for line in statement.splitlines())


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
        bound = dict(parameters or {})
        self.statements.append((sql, bound))
        marker = self.marker_of(sql) or ""
        if marker == PLANE_MARKER and marker not in self._answers:
            # Default the plane probe to "every named relation is built", so a test that is about
            # something else does not have to script it. A test that IS about an unbuilt plane
            # calls answer(PLANE_MARKER, ...) and overrides this.
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
    """One row shaped like signal_value_on_day.sql returns.

    No `source_parameter`. geo.mv_signal_cell_daily is grained by
    (support key, signal, unit, cell, day) and carries no upstream parameter column, so the
    statement no longer groups by one -- reporting a parameter the rollup cannot distinguish
    would be inventing it. See agent/AGENTS.md, "Reading the pre-aggregated planes".
    """
    row: dict[str, object] = {
        "signal_name": "air_temperature_mean",
        "support_key": "surface",
        "normalized_unit": "degC",
        "observed_day": date(2026, 3, 14),
        "observation_count": 3,
        "cell_count": 3,
        "nearest_cell_distance_m": 4210.5,
        "nearest_cell_key": "nasa-power-0.5-degree/43.5/-116.5",
        "nearest_cell_grid_name": "nasa-power-0.5-degree",
        "nearest_cell_value": 11.4,
        "nearest_cell_observed_at": datetime(2026, 3, 14, tzinfo=UTC),
        "nearest_cell_coverage_fraction": 1.0,
        "nearest_cell_allowed_client_exposure": "public",
        "minimum_value": 10.1,
        "maximum_value": 12.9,
        "mean_value": 11.5,
        "last_observed_at": datetime(2026, 3, 14, tzinfo=UTC),
    }
    row.update(overrides)
    return row


def _neighbor_row(side: str, observed_day: date, offset: int, **overrides: object) -> dict[str, object]:
    """One row shaped like signal_neighbors_in_time.sql returns."""
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
        "nearest_cell_key": "nasa-power-0.5-degree/43.5/-116.5",
        "nearest_cell_grid_name": "nasa-power-0.5-degree",
        "nearest_cell_distance_m": 4210.5,
    }
    row.update(overrides)
    return row


# --- 1. Value at the caller's day --------------------------------------------------


async def test_value_on_day_asks_for_the_caller_day_and_never_the_live_edge() -> None:
    """The bound day is the selected one, not a lookback from now, and it reads the served rollup."""
    session = RecordingSession()
    session.answer(VALUE_MARKER, [_value_row()])
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
            day=SELECTED_DAY,
        )

    bound = session.parameters_for(VALUE_MARKER)
    assert bound["day"] == date(2026, 3, 14)
    # The agent and the map must read the same relation, or the agent can contradict the screen.
    statement = session.sql_for(VALUE_MARKER)
    assert "geo.mv_signal_cell_daily" in statement
    assert "agri.signal_observation" not in executable_sql(statement)
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

    assert session.markers() == [PLANE_MARKER, VALUE_MARKER, COVERAGE_MARKER]
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
    for shared in ("longitude", "latitude", "radius_meters", "cell_limit", "signal_names"):
        assert value_bound[shared] == audit_bound[shared], f"{shared} must be scoped identically"
    # The day is the one thing the two cannot spell alike: the rollup is grained by calendar day,
    # the audit by the window a lane fetched. Same day either way, and that is what is asserted.
    assert value_bound["day"] == date(2026, 3, 14)
    assert audit_bound["day_start"] == datetime(2026, 3, 14, tzinfo=UTC)
    assert audit_bound["day_end"] == datetime(2026, 3, 15, tzinfo=UTC)


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
    # Whole calendar days now, because the rollup's grain IS a calendar day; the old half-open
    # pair of timestamps existed only to bracket a raw observed_at column that is no longer read.
    assert bound["search_from"] == date(2026, 3, 4)
    assert bound["search_through"] == date(2026, 3, 24)
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
    assert "GOVERNED SIGNAL" in payload["note"]
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
    assert bound["day"] == date(2026, 3, 14)
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

    # The plane probe, then the read. Nothing widened, nothing retried.
    assert session.markers() == [PLANE_MARKER, CELLS_MARKER]
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

    # One plane probe for the whole run -- a matview cannot become unpopulated again once it has
    # been refreshed, so the answer is cached for the run rather than re-asked per tool.
    assert session.markers() == [PLANE_MARKER, VALUE_MARKER, COVERAGE_MARKER, NEIGHBORS_MARKER, CELLS_MARKER]
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


# --- 4. An unbuilt pre-aggregated plane is refused, never answered as an absence ----


async def test_an_unpopulated_rollup_is_refused_by_name_rather_than_answered_empty() -> None:
    """The bug class this guard exists for: a matview created WITH NO DATA and never refreshed.

    agri.mv_forecast_ml_daily_serving shipped in exactly that state. Reading an unpopulated
    matview raises rather than returning zero rows, so the two available failures were "the tool
    errored" and "the tool returned nothing" -- and the second is far worse, because "no drought
    here" and "the drought plane was never built" become the same answer to the model.
    """
    session = RecordingSession()
    session.answer(
        PLANE_MARKER,
        [
            {
                "relation_name": agent_tools.SIGNAL_ROLLUP_RELATION,
                "relation_exists": True,
                "relation_kind": "m",
                "is_populated": False,
            }
        ],
    )
    async with agent_tools.run_context(session_provider=_session_provider(session)) as ledger:
        raw = await agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )

    # The probe ran and nothing else did: an unbuilt plane is never read from.
    assert session.markers() == [PLANE_MARKER]
    payload = json.loads(raw)
    assert payload["error"] == "pre_aggregated_plane_unbuilt"
    assert payload["unbuilt_relations"] == [agent_tools.SIGNAL_ROLLUP_RELATION]
    assert "REFUSAL, not an absence" in payload["note"]
    assert ledger[0]["error"] == "plane_unbuilt"


async def test_a_relation_the_probe_never_mentions_counts_as_unbuilt() -> None:
    """Silence about a plane is not evidence that the plane is fine -- fail closed, not open."""
    session = RecordingSession()
    session.answer(PLANE_MARKER, [])
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_nearest_signal_cells(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY
        )

    assert session.markers() == [PLANE_MARKER]
    assert json.loads(raw)["error"] == "pre_aggregated_plane_unbuilt"


# --- 5. The generic surface triad, for the layers that are not signal grids ---------


def test_the_agent_catalogue_is_the_map_catalogue_hand_spelled() -> None:
    """24 names, spelled out here so a surface silently dropped from the map is caught, not copied.

    Deliberately NOT derived from geo.layers or from the TypeScript constants. A generated list
    drifts with the thing it is meant to check: a layer that vanished would vanish from the agent's
    vocabulary too, and the agent would say "I do not know that surface" instead of "that surface
    stopped being served". Same reasoning as the hand-spelled assertion docs/layer-lane-standard.md
    section 9 requires of the slider capability catalogue.
    """
    expected = {
        # The 11 geo.layers rows (drizzle 0001, 0011, 0013, 0017).
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
    # The feature-backed subset is exactly the geo.layers half, and disjoint from the streams.
    assert set(agent_tools.FEATURE_SURFACE_NAMES) < set(agent_tools.AGENT_SURFACE_NAMES)
    assert not set(agent_tools.FEATURE_SURFACE_NAMES) & set(agent_tools.STREAM_SURFACE_NAMES)


async def test_surface_coverage_reads_the_census_the_slider_reads() -> None:
    """The agent and the slider must agree about which days exist, so they read one relation."""
    session = RecordingSession()
    session.answer(
        SURFACE_COVERAGE_MARKER,
        [
            {
                "surface_name": "vegetation",
                "surface_kind": "feature",
                "requested_day": date(2026, 3, 14),
                "is_covered": False,
                "observation_count": 0,
                "unlinked_count": 0,
                "distinct_key_count": 0,
                "day_newest_observed_at": None,
                "metric_counts": None,
                "earliest_observed_day": date(2022, 8, 6),
                "latest_observed_day": date(2026, 8, 2),
                "observed_day_count": 1_460,
                "total_observation_count": 44_000,
                "surface_newest_observed_at": datetime(2026, 8, 2, tzinfo=UTC),
            }
        ],
    )
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_observation_coverage_on_day(surface_name="vegetation", day=SELECTED_DAY)

    assert "geo.v_observation_day_census" in session.sql_for(SURFACE_COVERAGE_MARKER)
    bound = session.parameters_for(SURFACE_COVERAGE_MARKER)
    assert bound == {"surface_name": "vegetation", "day": date(2026, 3, 14)}
    payload = json.loads(raw)
    coverage = payload["coverage"]
    assert coverage["is_covered"] is False
    # An uncovered day is not the end of the answer: the history bounds say WHICH kind of absence.
    assert coverage["earliest_observed_day"] == "2022-08-06"
    assert coverage["latest_observed_day"] == "2026-08-02"
    assert "past its live edge" in payload["note"]


async def test_surface_coverage_probes_all_three_census_matviews_and_not_the_view() -> None:
    """A plain view over matviews reports itself populated even when they are not."""
    session = RecordingSession()
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        await agent_tools.query_observation_coverage_on_day(surface_name="drought-areas", day=SELECTED_DAY)

    probed = session.parameters_for(PLANE_MARKER)["relation_names"]
    assert probed == list(agent_tools.CENSUS_RELATIONS)
    assert "geo.v_observation_day_census" not in probed


async def test_surface_temporal_neighbors_carry_their_real_gap() -> None:
    """A neighbour without its gap is indistinguishable from an exact answer, on every surface."""
    session = RecordingSession()
    session.answer(
        SURFACE_NEIGHBORS_MARKER,
        [
            {
                "side": "before",
                "surface_name": "vegetation",
                "surface_kind": "feature",
                "observed_day": date(2026, 3, 3),
                "day_offset": -11,
                "distance_days": 11,
                "observation_count": 412,
                "distinct_key_count": 412,
                "newest_observed_at": datetime(2026, 3, 3, tzinfo=UTC),
            }
        ],
    )
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_observation_temporal_neighbors(
            surface_name="vegetation",
            day=SELECTED_DAY,
            neighbor_days=20,
        )

    bound = session.parameters_for(SURFACE_NEIGHBORS_MARKER)
    assert bound["day"] == date(2026, 3, 14)
    assert bound["search_from"] == date(2026, 2, 22)
    assert bound["search_through"] == date(2026, 4, 3)
    payload = json.loads(raw)
    only = payload["temporal_neighbors"][0]
    assert (only["side"], only["observed_day"], only["distance_days"], only["day_offset"]) == (
        "before",
        "2026-03-03",
        11,
        -11,
    )
    assert only["observed_day"] != payload["requested_day"]
    assert "never quote one as the value on requested_day" in payload["note"]


async def test_surface_temporal_neighbors_clamp_an_over_wide_window_and_report_it() -> None:
    session = RecordingSession()
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_observation_temporal_neighbors(
            surface_name="fire-detections",
            day=SELECTED_DAY,
            neighbor_days=99_999,
        )

    bounds = json.loads(raw)["applied_bounds"]
    assert bounds["neighbor_days"] == agent_tools.MAX_SURFACE_NEIGHBOR_DAYS


async def test_feature_value_near_point_dates_features_the_way_the_map_does() -> None:
    """One day rule, called not re-derived, or the agent dates a feature differently from the tiles."""
    session = RecordingSession()
    session.answer(
        FEATURE_NEAR_MARKER,
        [
            {
                "feature_id": "6f1f0a2e-0000-4000-8000-000000000001",
                "observed_day": date(2026, 3, 14),
                "distance_meters": 812.4,
                "centroid_longitude": -116.19,
                "centroid_latitude": 43.61,
                "has_geometry_link": True,
                "data_available_at": datetime(2026, 3, 15, tzinfo=UTC),
                "properties": {"id": "USGS-13206000", "observedAt": "2026-03-14T18:00:00Z"},
            }
        ],
    )
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await agent_tools.query_feature_value_near_point(
            surface_name="water-gauges",
            day=SELECTED_DAY,
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
        )

    statement = session.sql_for(FEATURE_NEAR_MARKER)
    assert "geo.feature_observation_day(feature.properties)" in statement
    # The bounding-box prefilter is what keeps the GiST index usable in front of the geography cast.
    assert "feature.geom && ST_Expand" in statement
    bound = session.parameters_for(FEATURE_NEAR_MARKER)
    assert bound["surface_name"] == "water-gauges"
    assert bound["day"] == date(2026, 3, 14)
    assert bound["property_keys"] == list(agent_tools.FEATURE_PROPERTY_KEYS)
    payload = json.loads(raw)
    nearest = payload["features"][0]
    assert nearest["distance_meters"] == 812.4
    assert nearest["observed_day"] == SELECTED_DAY


def test_the_bounding_box_widens_with_latitude_rather_than_clipping() -> None:
    """A single metres-per-degree constant clips the east-west edges away from the equator."""
    at_equator = agent_tools._bbox_degrees(50_000.0, 0.0)
    at_boise = agent_tools._bbox_degrees(50_000.0, BOISE_LATITUDE)
    at_high_latitude = agent_tools._bbox_degrees(50_000.0, 70.0)
    assert at_equator < at_boise < at_high_latitude
    # Every box must still contain the radius it stands in for, north-south as well as east-west.
    assert at_equator >= 50_000.0 / 110_574.0


async def test_feature_value_near_point_refuses_a_stream_surface_by_name() -> None:
    """A cell-grid stream has no features; answering it with an empty list would read as absence."""
    session = RecordingSession()
    async with agent_tools.run_context(session_provider=_session_provider(session)) as ledger:
        raw = await agent_tools.query_feature_value_near_point(
            surface_name="climate-field-air-temperature",
            day=SELECTED_DAY,
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
        )

    assert not session.statements
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
async def test_surface_tools_refuse_a_name_outside_the_catalogue(
    tool_name: str, arguments: dict[str, object]
) -> None:
    """An unknown surface is refused with the catalogue listed, never answered as empty."""
    session = RecordingSession()
    call = getattr(agent_tools, f"query_{tool_name}")
    async with agent_tools.run_context(session_provider=_session_provider(session)):
        raw = await call(surface_name="space-lasers", day=SELECTED_DAY, **arguments)

    assert not session.statements
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
