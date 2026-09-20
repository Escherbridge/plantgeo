"""Selected-day context, surface coverage, and spatial evidence contracts."""

# ruff: noqa: PLR2004 - the literals here are fixture values, and naming each one hides the assertion.

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.agent import tools as agent_tools
from agri_data_service.agent.graph import AgentRequest
from agri_data_service.agent.prompts import build_location_context
from agri_data_service.routes.agent_analysis import AgentAnalyzeRequest
from tests.agent_fakes import FakeAgentWarehouse, published_lane

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

POINT_LANE_MARKER = "agent_point_lane_rows"

BOISE_LONGITUDE = -116.2
BOISE_LATITUDE = 43.6
# A day inside every contracted lane's horizon (execution/coverage_contract.py, verified 2026-08-11).
SELECTED_DAY = "2026-03-14"
SELECTED_DATE = date(2026, 3, 14)


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
    assert "Use this exact selected day for environmental analysis." in context
    assert "signal tool" not in context


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


async def test_forecast_refusal_does_not_probe_postgresql() -> None:
    """Forecast serving is unavailable until a governed Parquet lane is published."""
    source = FakeAgentWarehouse()
    session = RecordingSession()

    async with agent_tools.run_context(session_provider=_session_provider(session), warehouse_source=source):
        raw = await agent_tools.query_forecast_summary_for_cell(longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE)

    assert session.markers() == []
    payload = json.loads(raw)
    assert payload["error"] == "forecast_parquet_lane_not_published"


# --- The selected-day surface catalogue -------------------------------------------


def test_the_agent_catalogue_is_the_map_catalogue_hand_spelled() -> None:
    """Names are spelled out so a surface silently dropped from the map is caught, not copied.

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
        "fire-risk",
        "weather-forecast",
        "land-context",
        "demand-heatmap",
        "strategy-recommendations",
        "soil-phh2o",
        "soil-soc",
        "soil-nitrogen",
        "soil-bdod",
        "soil-cec",
        "soil-ocd",
        "botanical-occurrences",
        "botanical-richness",
        "botanical-collection-effort",
        "gbif-occurrences",
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
        "surface_evidence_for_selection": agent_tools.surface_evidence_for_selection,
    }
    for name, tool in published.items():
        schema = tool.to_dict()["input_schema"]
        assert schema["properties"]["surface_name"]["type"] == "string", name
        assert schema["properties"]["day"]["type"] == "string", name
        assert {"surface_name", "day"} <= set(schema["required"]), name
        assert tool in agent_tools.WAREHOUSE_TOOLS, name
