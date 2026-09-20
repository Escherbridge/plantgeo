"""The agent tools over the Parquet warehouse: the four states, the refusals, and what still reads PostgreSQL.

The whole file exists to hold one rule the move must not have softened: a tool that cannot answer
says so, and never returns an empty list the model can read as an absence. Against PostgreSQL there
were two states -- a matview was built or it was not. Against Parquet there are four, and three of
them are things a model must never collapse: a governed absence carries the upstream's own reason, a
day nobody wrote supports no conclusion at all, and a lane that never wrote anything is the old
"unbuilt plane" under a new name.

No object store, no DuckDB, no database. `FakeAgentWarehouse` holds an in-memory object layout that
real day classification walks, and scripts the row reads by their line-one marker.
"""

# ruff: noqa: PLR2004 - the literals here are fixture values, and naming each one hides the assertion.

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from agri_data_service.agent import tools as agent_tools
from agri_data_service.agent.surfaces import (
    AGENT_SURFACE_NAMES,
    SURFACE_PARQUET_LANES,
)
from tests.agent_fakes import FakeAgentWarehouse, absent_lane, published_lane

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

SELECTED_DAY = date(2026, 3, 14)
BOISE_LONGITUDE = -116.2
BOISE_LATITUDE = 43.6


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
    """An AsyncSession stand-in used to prove environmental tools do not open the database."""

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
        marker = self.marker_of(sql)
        return FakeResult(self._answers.get(marker or "", []))

    @staticmethod
    def marker_of(sql: str) -> str | None:
        """The bare `-- <name>` marker a statement opens with."""
        first_line = sql.lstrip().splitlines()[0] if sql.strip() else ""
        return first_line.removeprefix("-- ").strip() if first_line.startswith("-- ") else None

    def markers(self) -> list[str]:
        """Every PostgreSQL statement's marker, in execution order."""
        return [marker for sql, _ in self.statements if (marker := self.marker_of(sql)) is not None]


def session_provider(session: RecordingSession) -> Any:
    """Bind one recording session so environmental PostgreSQL access is observable."""

    @asynccontextmanager
    async def provider() -> AsyncIterator[RecordingSession]:
        yield session

    return provider


async def call(tool: Any, source: FakeAgentWarehouse, session: RecordingSession | None = None) -> dict[str, Any]:
    """Run one tool inside a bound run context and decode its payload."""
    async with agent_tools.run_context(
        session_provider=session_provider(session or RecordingSession()),
        warehouse_source=source,
    ):
        return json.loads(await tool())


# --- Scan budgets are stated, never silent -----------------------------------------


async def test_a_window_wider_than_the_partition_budget_reports_the_span_it_actually_read() -> None:
    """Answering two years from four months of it, silently, is the fabricated-absence bug in costume.

    The partition budget is the guard that binds where a day cap cannot: the fire lanes are asked
    across years, and every written day of them is at least one object-store GET.
    """
    budget = agent_tools.warehouse.MAX_SCANNED_DAY_PARTITIONS
    source = FakeAgentWarehouse()
    for offset in range(budget + 40):
        source.listing_store.write_day("fire-detections", "observed", 13, SELECTED_DAY - timedelta(days=offset))
    source.listing_store.write_day("burn-severity", "observed", 13, SELECTED_DAY)

    payload = await call(
        lambda: agent_tools.query_fire_history_near_point(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
            years_back=2,
            as_of=datetime(2026, 3, 14, tzinfo=UTC),
        ),
        source,
    )

    span = payload["applied_bounds"]["scanned_spans"]["fire-detections"]
    assert span["window_narrowed_by_scan_budget"] is True
    assert span["scanned_day_count"] == budget
    assert span["scanned_from"] > span["requested_from"]
    assert span["scanned_through"] == SELECTED_DAY.isoformat(), "the NEWEST days are the ones kept"


async def test_the_forecast_tool_refuses_a_never_written_lane_rather_than_an_empty_cell() -> None:
    """An unavailable forecast lane is a typed refusal, never a database probe."""
    source = FakeAgentWarehouse()
    session = RecordingSession()

    payload = await call(
        lambda: agent_tools.query_forecast_summary_for_cell(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, as_of=datetime(2026, 3, 14, tzinfo=UTC)
        ),
        source,
        session,
    )

    assert payload["error"] == "forecast_parquet_lane_not_published"
    assert session.markers() == []
    assert source.markers() == []


async def test_an_unadmitted_community_layer_is_refused_without_a_postgresql_fallback() -> None:
    """An unregistered lane is a typed Parquet refusal, never a database read."""
    source = FakeAgentWarehouse()
    session = RecordingSession()

    payload = await call(
        lambda: agent_tools.query_feature_value_near_point(
            surface_name="interventions",
            day=SELECTED_DAY.isoformat(),
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
        ),
        source,
        session,
    )

    assert payload["error"] == "surface_not_served_from_parquet"
    assert session.markers() == []
    assert source.markers() == []
    assert "PostgreSQL is not queried as a fallback" in payload["note"]


async def test_a_parquet_feature_layer_carries_its_lanes_own_columns_under_properties() -> None:
    """The Parquet lanes publish typed columns, so the JSON allow-list the old statement needed is gone."""
    source = FakeAgentWarehouse()
    source.listing_store.write_day("vegetation", "observed", 13, SELECTED_DAY)
    source.answer(
        "agent_point_lane_rows",
        [
            {
                "cell_id": "veg-1",
                "grid_name": "sentinel2-ndvi-0p25deg",
                "metric_name": "ndvi",
                "metric_value": 0.62,
                "observed_day": SELECTED_DAY,
                "cell_longitude": -116.25,
                "cell_latitude": 43.62,
                "distance_meters": 4607.7,
            }
        ],
    )

    payload = await call(
        lambda: agent_tools.query_feature_value_near_point(
            surface_name="vegetation",
            day=SELECTED_DAY.isoformat(),
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
        ),
        source,
    )

    only = payload["features"][0]
    assert only["distance_meters"] == 4607.7
    assert only["distance_basis"] == "point"
    assert only["centroid_longitude"] == -116.25
    assert only["properties"]["metric_name"] == "ndvi"
    assert only["properties"]["metric_value"] == 0.62
    assert payload["applied_bounds"]["parquet_lane"] == "vegetation"
    assert "metric_value" in payload["applied_bounds"]["projected_columns"]


# --- Coverage from the availability index ------------------------------------------


async def test_a_surface_is_covered_only_on_days_every_one_of_its_lanes_published() -> None:
    """Three air-temperature lanes; a day one of them is missing is a day the map cannot draw."""
    source = FakeAgentWarehouse()
    lanes = SURFACE_PARQUET_LANES["climate-field-air-temperature"]
    source.evidence[lanes[0]] = published_lane(lanes[0], [SELECTED_DAY, SELECTED_DAY - timedelta(days=1)])
    source.evidence[lanes[1]] = published_lane(lanes[1], [SELECTED_DAY, SELECTED_DAY - timedelta(days=1)])
    source.evidence[lanes[2]] = published_lane(lanes[2], [SELECTED_DAY - timedelta(days=1)])

    covered_payload = await call(
        lambda: agent_tools.query_observation_coverage_on_day(
            surface_name="climate-field-air-temperature", day=(SELECTED_DAY - timedelta(days=1)).isoformat()
        ),
        source,
    )
    thin_payload = await call(
        lambda: agent_tools.query_observation_coverage_on_day(
            surface_name="climate-field-air-temperature", day=SELECTED_DAY.isoformat()
        ),
        source,
    )

    assert covered_payload["coverage"]["is_covered"] is True
    assert thin_payload["coverage"]["is_covered"] is False
    # The thin day still reports what EACH lane said, so the missing one can be named.
    states = {row["lane"]: row["state"] for row in thin_payload["coverage"]["lane_states"]}
    assert states[lanes[0]] == "published"
    assert states[lanes[2]] == "not_published"
    assert thin_payload["coverage"]["observed_day_count"] == 1


async def test_a_lane_that_cannot_prove_its_coverage_is_refused_and_never_reported_empty() -> None:
    """The alternative evidence is a whole-stream LIST, which this tool will not pay on a request path."""
    source = FakeAgentWarehouse()
    payload = await call(
        lambda: agent_tools.query_observation_coverage_on_day(surface_name="vegetation", day=SELECTED_DAY.isoformat()),
        source,
    )

    assert payload["error"] == "parquet_availability_withheld"
    assert payload["unproven_lanes"] == [{"lane": "vegetation", "reason": "availability_unpublished"}]
    assert "do NOT say the day is empty" in payload["note"]


async def test_a_governed_absence_day_is_uncovered_and_carries_the_recorded_reason() -> None:
    """`is_covered` false has kinds, and the recorded reason is the one that closes the question."""
    source = FakeAgentWarehouse()
    source.evidence["vegetation"] = absent_lane("vegetation", [SELECTED_DAY])

    payload = await call(
        lambda: agent_tools.query_observation_coverage_on_day(surface_name="vegetation", day=SELECTED_DAY.isoformat()),
        source,
    )

    coverage = payload["coverage"]
    assert coverage["is_covered"] is False
    assert coverage["lane_states"][0]["state"] == "governed_absence"
    assert coverage["lane_states"][0]["absence_reason"] == "upstream_published_nothing"


async def test_temporal_neighbours_carry_the_real_gap_each_side() -> None:
    """These are neighbours, never answers, so the signed and unsigned gaps both travel."""
    source = FakeAgentWarehouse()
    source.evidence["vegetation"] = published_lane(
        "vegetation",
        [SELECTED_DAY - timedelta(days=6), SELECTED_DAY + timedelta(days=3)],
        source_ceiling_day=SELECTED_DAY + timedelta(days=3),
    )

    payload = await call(
        lambda: agent_tools.query_observation_temporal_neighbors(
            surface_name="vegetation", day=SELECTED_DAY.isoformat()
        ),
        source,
    )

    by_side = {row["side"]: row for row in payload["temporal_neighbors"]}
    assert by_side["before"]["day_offset"] == -6
    assert by_side["before"]["distance_days"] == 6
    assert by_side["after"]["day_offset"] == 3
    assert by_side["after"]["distance_days"] == 3
    assert by_side["after"]["observation_count"] == 12


async def test_an_unadmitted_surface_is_refused_by_the_coverage_tools() -> None:
    """It has no Parquet lane; saying "uncovered" would be a claim the evidence cannot support."""
    source = FakeAgentWarehouse()
    payload = await call(
        lambda: agent_tools.query_observation_coverage_on_day(
            surface_name="interventions", day=SELECTED_DAY.isoformat()
        ),
        source,
    )

    assert payload["error"] == "surface_not_served_from_parquet"


# --- The fire summary --------------------------------------------------------------


async def test_the_fire_summary_reports_whole_lane_history_as_a_discriminated_shape() -> None:
    """A withheld index and a lane with no days would render identically as nulls; they must not."""
    source = FakeAgentWarehouse()
    source.listing_store.write_day("fire-detections", "observed", 13, SELECTED_DAY)
    source.listing_store.write_day("burn-severity", "observed", 13, SELECTED_DAY)
    source.evidence["fire-detections"] = published_lane("fire-detections", [SELECTED_DAY])
    source.answer(
        "agent_point_lane_rows",
        [
            {
                "cell_longitude": -116.25,
                "cell_latitude": 43.62,
                "observed_day": SELECTED_DAY,
                "detection_count": 7,
                "distance_meters": 4607.7,
            }
        ],
    )

    payload = await call(
        lambda: agent_tools.query_fire_history_near_point(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, as_of=datetime(2026, 3, 14, tzinfo=UTC)
        ),
        source,
    )

    summaries = {row["layer_name"]: row for row in payload["layer_summaries"]}
    detections = summaries["fire-detections"]
    assert detections["feature_count"] == 7, "a cell-day row is worth its detection_count, not one feature"
    assert detections["row_count"] == 1
    assert detections["distance_basis"] == "point"
    assert detections["layer_history"]["state"] == "available"
    assert detections["layer_history"]["latest_day"] == SELECTED_DAY.isoformat()
    # The second lane has no scripted index, so its history is WITHHELD rather than silently empty.
    assert summaries["burn-severity"]["layer_history"] == {
        "state": "withheld",
        "reason": "availability_unpublished",
    }


# --- Catalogue and removal tripwires -----------------------------------------------


def test_every_catalogue_surface_is_mapped_or_explicitly_refused() -> None:
    """Every surface declares a Parquet lane, an app reader, a botanical reader, or explicit absence."""
    mapped = set(SURFACE_PARQUET_LANES)
    assert mapped <= set(AGENT_SURFACE_NAMES)
    assert set(AGENT_SURFACE_NAMES) - mapped == {
        "land-context",
        "interventions",
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
    }


@pytest.mark.parametrize(
    "relation",
    [
        "geo.mv_signal_cell_daily",
        "geo.mv_signal_observation_day",
        "geo.mv_feature_observation_day",
        "geo.mv_drought_observation_day",
        "geo.mv_drought_release_index",
        "geo.v_observation_day_census",
        "geo.drought_areas",
        "agri.spatial_cell",
        "agri.signal_observation",
        "agri.drought_polygon_snapshot",
    ],
)
def test_no_surviving_agent_statement_reads_a_retired_environmental_relation(relation: str) -> None:
    """The c2-style removal proof, executable.

    No SQL files survive under `sql/agent/` and none may name an environmental relation
    the retirement track is dropping. `geo.mv_signal_cell_daily` and `agri.spatial_cell` are already
    gone from production, so a surviving reference would be a hard error rather than a stale read;
    the rest are still there and must have no agent reader before their drop packet can close.
    """
    root = Path(__file__).resolve().parents[1] / "src" / "agri_data_service" / "sql" / "agent"
    offenders: list[str] = []
    for statement in sorted(root.glob("*.sql")):
        executable = "\n".join(
            line for line in statement.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("--")
        )
        if relation in executable:
            offenders.append(statement.name)
    assert not offenders, f"{relation} is still read by {offenders}"


def test_the_agent_sql_tree_is_empty_after_the_postgresql_fallback_removal() -> None:
    """A file left behind after its caller moved is the next reader's trap, so the set is empty."""
    root = Path(__file__).resolve().parents[1] / "src" / "agri_data_service" / "sql" / "agent"
    assert sorted(path.name for path in root.glob("*.sql")) == []
