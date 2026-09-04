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
    POSTGRESQL_ONLY_SURFACE_NAMES,
    SURFACE_PARQUET_LANES,
)
from agri_data_service.parquet_ops import faults
from tests.agent_fakes import FakeAgentWarehouse, absent_lane, published_lane

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

SELECTED_DAY = date(2026, 3, 14)
BOISE_LONGITUDE = -116.2
BOISE_LATITUDE = 43.6

SIGNAL_LANE = "signal"
NEAR_CELL = "aaaaaaaa-0000-0000-0000-000000000001"
SECOND_CELL = "bbbbbbbb-0000-0000-0000-000000000002"


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
        self.plane_is_populated = True

    def answer(self, marker: str, rows: Sequence[Mapping[str, object]]) -> None:
        """Answer every statement carrying this marker with these rows."""
        self._answers[marker] = list(rows)

    async def execute(self, statement: object, parameters: Mapping[str, object] | None = None) -> FakeResult:
        """Record the statement and answer it from the script."""
        sql = str(statement)
        bound = dict(parameters or {})
        self.statements.append((sql, bound))
        marker = self.marker_of(sql)
        if marker == "agent_materialized_plane_populated":
            requested = bound.get("relation_names")
            names = list(requested) if isinstance(requested, list) else []
            return FakeResult(
                [
                    {
                        "relation_name": name,
                        "relation_exists": True,
                        "relation_kind": "m",
                        "is_populated": self.plane_is_populated,
                    }
                    for name in names
                ]
            )
        return FakeResult(self._answers.get(marker or "", []))

    @staticmethod
    def marker_of(sql: str) -> str | None:
        """The bare `-- <name>` marker a statement opens with."""
        first_line = sql.lstrip().splitlines()[0] if sql.strip() else ""
        return first_line.removeprefix("-- ").strip() if first_line.startswith("-- ") else None

    def markers(self) -> list[str]:
        """Every PostgreSQL statement's marker, in execution order."""
        return [marker for sql, _ in self.statements if (marker := self.marker_of(sql)) is not None]

    def parameters_for(self, marker: str) -> dict[str, object]:
        """The bound parameters of the first statement carrying this marker."""
        for sql, parameters in self.statements:
            if self.marker_of(sql) == marker:
                return parameters
        raise AssertionError(f"no PostgreSQL statement carrying marker {marker!r} was executed")


def session_provider(session: RecordingSession) -> Any:
    """Bind one recording session as the run's PostgreSQL provider."""

    @asynccontextmanager
    async def provider() -> AsyncIterator[RecordingSession]:
        yield session

    return provider


def signal_warehouse(*, published: Sequence[date] = (SELECTED_DAY,)) -> FakeAgentWarehouse:
    """A warehouse whose signal lane published the named days and holds the standard scripted answers."""
    source = FakeAgentWarehouse()
    for day in published:
        source.listing_store.write_day(SIGNAL_LANE, "observed", 13, day)
    source.answer(
        "agent_signal_day_values",
        [
            {
                "signal_name": "air_temperature",
                "support_key": "surface",
                "normalized_unit": "degC",
                "observed_day": SELECTED_DAY,
                "observation_count": 5,
                "cell_count": 2,
                "nearest_cell_distance_m": 4607.7,
                "nearest_cell_id": NEAR_CELL,
                "nearest_cell_value": 4.14,
                "nearest_cell_observed_at": datetime(2026, 3, 14, 12, tzinfo=UTC),
                "nearest_cell_coverage_fraction": 0.9,
                "nearest_cell_allowed_client_exposure": True,
                "minimum_value": 4.14,
                "maximum_value": 5.14,
                "mean_value": 4.74,
                "last_observed_at": datetime(2026, 3, 14, 12, tzinfo=UTC),
            }
        ],
    )
    source.answer(
        "agent_signal_admitted_cells",
        [
            {"cell_id": NEAR_CELL, "distance_meters": 4607.7},
            {"cell_id": SECOND_CELL, "distance_meters": 16857.9},
        ],
    )
    return source


async def call(tool: Any, source: FakeAgentWarehouse, session: RecordingSession | None = None) -> dict[str, Any]:
    """Run one tool inside a bound run context and decode its payload."""
    async with agent_tools.run_context(
        session_provider=session_provider(session or RecordingSession()),
        warehouse_source=source,
    ):
        return json.loads(await tool())


# --- The four states ---------------------------------------------------------------


async def test_a_published_day_reports_its_state_beside_its_rows() -> None:
    """`day_state` must be readable BEFORE the rows, or an empty list has no interpretation."""
    source = signal_warehouse()
    payload = await call(
        lambda: agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY.isoformat()
        ),
        source,
    )

    assert payload["day_state"]["state"] == "published"
    assert payload["day_state"]["lane"] == SIGNAL_LANE
    assert payload["day_state"]["zoom_tier"] == 13
    assert payload["signals_on_day"][0]["signal_name"] == "air_temperature"


async def test_a_governed_absence_carries_the_upstreams_own_reason_and_no_rows() -> None:
    """The lane looked and the source had nothing. That is a MEASUREMENT, and its evidence rides with it."""
    source = FakeAgentWarehouse()
    source.listing_store.write_absence(
        SIGNAL_LANE,
        "observed",
        13,
        SELECTED_DAY,
        reason="upstream_published_nothing",
        upstream_response="HTTP 200, zero features",
        recorded_at=datetime(2026, 3, 15, 4, tzinfo=UTC),
        run_id="run-2026-03-15",
    )
    payload = await call(
        lambda: agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY.isoformat()
        ),
        source,
    )

    assert payload["day_state"]["state"] == "governed_absence"
    assert payload["day_state"]["absence"]["reason"] == "upstream_published_nothing"
    assert payload["day_state"]["absence"]["upstream_response"] == "HTTP 200, zero features"
    assert payload["day_state"]["absence"]["run_id"] == "run-2026-03-15"
    assert payload["signals_on_day"] == []
    # No row read was attempted at all: the day is settled, and reading it would find nothing to read.
    assert source.markers() == []


async def test_a_day_nobody_wrote_is_named_rather_than_answered_as_empty() -> None:
    """`day_not_written` is a gap. Nothing follows from it, and the payload says which state it is."""
    source = signal_warehouse(published=[SELECTED_DAY - timedelta(days=3)])
    payload = await call(
        lambda: agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY.isoformat()
        ),
        source,
    )

    assert payload["day_state"]["state"] == "day_not_written"
    assert payload["signals_on_day"] == []
    assert source.markers() == []


async def test_a_lane_that_never_wrote_anything_is_a_typed_refusal() -> None:
    """The Parquet spelling of the old unbuilt-matview refusal, and it must still name the lane."""
    source = FakeAgentWarehouse()
    payload = await call(
        lambda: agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY.isoformat()
        ),
        source,
    )

    assert payload["error"] == "parquet_lane_never_written"
    assert payload["unwritten_lanes"] == [SIGNAL_LANE]
    assert "REFUSAL, not an absence" in payload["note"]
    assert "do not report the subject as absent" in payload["note"]


async def test_a_half_written_day_is_refused_rather_than_served_or_called_a_gap() -> None:
    """Parts with no completion marker: serving them puts a prefix of a release in front of the model."""
    source = FakeAgentWarehouse()
    source.listing_store.write_day(SIGNAL_LANE, "observed", 13, SELECTED_DAY, complete=False)
    payload = await call(
        lambda: agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY.isoformat()
        ),
        source,
    )

    assert payload["error"] == "parquet_serving_refused"
    assert payload["refusal_code"] == "partition_day_incomplete"
    assert "REFUSAL, not an absence" in payload["note"]


async def test_a_lane_whose_published_objects_lack_the_promised_columns_refuses_by_name() -> None:
    """The live state of the signal lane on 2026-09-04, pinned.

    `warehouse/parquet/schema.py` declares `cell_longitude` and `cell_latitude` non-nullable, and
    the newest published z13 part -- `year=2026/month=08/day=06/part-0.parquet` -- carries eleven
    columns and neither of them. Without this refusal every signal tool answers a
    `duckdb.BinderException`, which reaches the model as "the tool broke" and supports no
    conclusion at all. With it, the tool says which columns are missing and that the lane owes a
    re-export -- a fact about the published objects, never about the data.
    """
    source = signal_warehouse()
    source.columns = frozenset(agent_tools.SIGNAL_PLANE_COLUMNS) - {"cell_longitude", "cell_latitude"}

    payload = await call(
        lambda: agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY.isoformat()
        ),
        source,
    )

    assert payload["error"] == "parquet_serving_refused"
    assert payload["refusal_code"] == "lane_columns_absent"
    assert "cell_latitude, cell_longitude" in payload["refusal_detail"]
    assert "owes a re-export" in payload["refusal_detail"]
    assert "says nothing about whether the data exists" in payload["refusal_detail"]


async def test_a_serving_fault_is_a_refusal_and_never_a_claim_about_content() -> None:
    """Every slot busy, a read past its memory ceiling: facts about this process, not about the data."""
    source = signal_warehouse()
    source.run_raises = faults.serving_at_capacity(operation="agent_signal_value_on_day", concurrent_reads=3)
    payload = await call(
        lambda: agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY.isoformat()
        ),
        source,
    )

    assert payload["error"] == "parquet_serving_refused"
    assert payload["refusal_code"] == "serving_at_capacity"


# --- Scan budgets are stated, never silent -----------------------------------------


async def test_an_over_wide_signal_window_is_clamped_and_the_clamp_is_reported_back() -> None:
    """A model asking for a decade gets the depth cap, and is TOLD it got it, never a silent decade.

    `MAX_DAYS_BACK` now sits AT the partition budget, so a fully-published window one day cap allows
    (the span is inclusive of today, so `days_back` days is `days_back + 1` calendar days) is one day
    over `MAX_SCANNED_DAY_PARTITIONS` -- `narrow_to_budget` is what supplies the final "120", not the
    day-cap arithmetic, and it reports itself back the same way the fire lanes' narrowing already does.
    """
    published = [SELECTED_DAY - timedelta(days=offset) for offset in range(200)]
    source = signal_warehouse(published=published)
    source.answer("agent_signal_window_summary", [])
    budget = agent_tools.warehouse.MAX_SCANNED_DAY_PARTITIONS

    payload = await call(
        lambda: agent_tools.query_signals_near_point(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
            days_back=3650,
            as_of=datetime(2026, 3, 14, tzinfo=UTC),
        ),
        source,
    )

    bounds = payload["applied_bounds"]
    assert bounds["days_back"] == agent_tools.MAX_DAYS_BACK
    assert bounds["requested_from"] == (SELECTED_DAY - timedelta(days=agent_tools.MAX_DAYS_BACK)).isoformat()
    assert bounds["window_narrowed_by_scan_budget"] is True
    assert bounds["scanned_day_count"] == budget
    assert len(source.part_uris_for("agent_signal_window_summary")) == budget
    assert "SCAN BUDGET, not the depth of the record" in payload["note"]


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


async def test_an_empty_window_explains_itself_with_a_state_census() -> None:
    """ "No rows" and "no days" are different claims, and the payload has to separate them."""
    source = signal_warehouse(published=[])
    source.listing_store.write_absence(
        SIGNAL_LANE,
        "observed",
        13,
        SELECTED_DAY,
        reason="upstream_published_nothing",
        upstream_response="HTTP 200, zero features",
        recorded_at=datetime(2026, 3, 15, 4, tzinfo=UTC),
        run_id="run-2026-03-15",
    )

    payload = await call(
        lambda: agent_tools.query_signals_near_point(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
            days_back=7,
            as_of=datetime(2026, 3, 14, tzinfo=UTC),
        ),
        source,
    )

    states = payload["window_day_states"]
    assert payload["signal_summaries"] == []
    assert states["governed_absence"] == 1
    assert states["day_not_written"] == 7
    assert states["published"] == 0


# --- The filters that no longer exist ----------------------------------------------


async def test_a_grid_filter_is_refused_rather_than_ignored() -> None:
    """The retired cell registry carried the grid name; answering unfiltered would widen the question."""
    source = signal_warehouse()
    payload = await call(
        lambda: agent_tools.query_nearest_signal_cells(
            longitude=BOISE_LONGITUDE,
            latitude=BOISE_LATITUDE,
            day=SELECTED_DAY.isoformat(),
            grid_names=["nasa-power-0.5-degree"],
        ),
        source,
    )

    assert payload["error"] == "grid_filter_unavailable"
    assert payload["received_grid_names"] == ["nasa-power-0.5-degree"]
    assert source.markers() == [], "a refused filter must not reach the warehouse"


async def test_the_cell_list_says_it_is_observed_rather_than_declared() -> None:
    """A cell silent longer than the universe window is missing; the note must not let that read as a grid."""
    source = signal_warehouse(published=[SELECTED_DAY])
    source.answer(
        "agent_signal_cell_day_counts",
        [
            {
                "cell_id": NEAR_CELL,
                "centroid_longitude": -116.25,
                "centroid_latitude": 43.62,
                "distance_meters": 4607.7,
                "observation_count_on_day": 4,
                "signal_count_on_day": 2,
                "last_observed_at": datetime(2026, 3, 14, 12, tzinfo=UTC),
            }
        ],
    )

    payload = await call(
        lambda: agent_tools.query_nearest_signal_cells(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY.isoformat()
        ),
        source,
    )

    assert payload["nearest_cells"][0]["cell_id"] == NEAR_CELL
    assert "grid_name" not in payload["nearest_cells"][0], "a column with no source is omitted, not nulled"
    assert "resolution_m" not in payload["nearest_cells"][0]
    assert "OBSERVED, NOT DECLARED" in payload["note"]
    assert payload["applied_bounds"]["cell_universe_days"] == agent_tools.CELL_UNIVERSE_DAYS


# --- What still reads PostgreSQL, and why ------------------------------------------


async def test_the_coverage_audit_is_read_over_exactly_the_cells_the_value_came_from() -> None:
    """The cells now arrive as two positionally-paired arrays; `agri.spatial_cell` is gone."""
    source = signal_warehouse()
    session = RecordingSession()
    session.answer(
        "agent_signal_coverage_on_day",
        [{"signal_name": "air_temperature", "status": "no_data", "audit_row_count": 1}],
    )

    payload = await call(
        lambda: agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY.isoformat()
        ),
        source,
        session,
    )

    bound = session.parameters_for("agent_signal_coverage_on_day")
    assert bound["cell_ids"] == [NEAR_CELL, SECOND_CELL]
    assert bound["cell_distances"] == [4607.7, 16857.9]
    assert len(bound["cell_ids"]) == len(bound["cell_distances"]), "the two arrays are walked in step"
    assert payload["coverage_audit_on_day"][0]["status"] == "no_data"
    assert payload["cells_in_radius"] == 2


async def test_the_audit_is_not_read_at_all_when_no_cell_is_in_range() -> None:
    """An audit over cells the value never came from would explain the wrong point."""
    source = signal_warehouse()
    source.answer("agent_signal_admitted_cells", [])
    session = RecordingSession()

    await call(
        lambda: agent_tools.query_signal_value_on_day(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, day=SELECTED_DAY.isoformat()
        ),
        source,
        session,
    )

    assert "agent_signal_coverage_on_day" not in session.markers()


async def test_the_forecast_tool_resolves_its_cell_from_parquet_and_reads_the_ml_plane_in_postgresql() -> None:
    """The ML serving plane is not environmental data and stays; only the retired cell lookup moved."""
    source = signal_warehouse(published=[SELECTED_DAY])
    session = RecordingSession()
    session.answer("agent_forecast_summary_for_cell", [{"metric_name": "ndvi", "valid_day": "2026-03-15"}])

    payload = await call(
        lambda: agent_tools.query_forecast_summary_for_cell(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, as_of=datetime(2026, 3, 14, tzinfo=UTC)
        ),
        source,
        session,
    )

    assert session.markers() == ["agent_materialized_plane_populated", "agent_forecast_summary_for_cell"]
    bound = session.parameters_for("agent_forecast_summary_for_cell")
    assert bound["cell_id"] == NEAR_CELL
    assert bound["cell_distance_m"] == 4607.7
    assert payload["resolved_cell"]["cell_id"] == NEAR_CELL
    assert payload["forecast_values"][0]["metric_name"] == "ndvi"


async def test_the_forecast_tool_still_refuses_an_unpopulated_matview_by_name() -> None:
    """Its matview shipped with relispopulated false; a silent fallback would hide that forever."""
    source = signal_warehouse()
    session = RecordingSession()
    session.plane_is_populated = False

    payload = await call(
        lambda: agent_tools.query_forecast_summary_for_cell(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, as_of=datetime(2026, 3, 14, tzinfo=UTC)
        ),
        source,
        session,
    )

    assert payload["error"] == "pre_aggregated_plane_unbuilt"
    assert payload["unbuilt_relations"] == [agent_tools.FORECAST_DAILY_RELATION]
    assert session.markers() == ["agent_materialized_plane_populated"]
    assert source.markers() == [], "the plane probe fails before any warehouse read is attempted"


async def test_a_forecast_with_no_recent_cell_reports_that_rather_than_an_empty_plane() -> None:
    """ "No cell has reported near you" is a different claim from "no forecast exists"."""
    source = signal_warehouse()
    source.answer("agent_signal_admitted_cells", [])
    session = RecordingSession()

    payload = await call(
        lambda: agent_tools.query_forecast_summary_for_cell(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, as_of=datetime(2026, 3, 14, tzinfo=UTC)
        ),
        source,
        session,
    )

    assert payload["resolved_cell"] is None
    assert payload["forecast_values"] == []
    assert "agent_forecast_summary_for_cell" not in session.markers()


async def test_the_forecast_tool_refuses_a_never_written_lane_rather_than_an_empty_cell() -> None:
    """A lane that has never written anything is state 4 of 4, never "no cell reported nearby".

    Before this, `_admitted_signal_cells` answered `[]` for BOTH causes -- an unwritten lane and a
    written lane with no cell nearby -- and `resolved_cell: null` carried the same note either way,
    which asserted "no analysis cell reported inside the radius" about a lane that was never read at
    all.
    """
    source = FakeAgentWarehouse()
    session = RecordingSession()

    payload = await call(
        lambda: agent_tools.query_forecast_summary_for_cell(
            longitude=BOISE_LONGITUDE, latitude=BOISE_LATITUDE, as_of=datetime(2026, 3, 14, tzinfo=UTC)
        ),
        source,
        session,
    )

    assert payload["error"] == "parquet_lane_never_written"
    assert payload["unwritten_lanes"] == [SIGNAL_LANE]
    assert "REFUSAL, not an absence" in payload["note"]
    assert source.markers() == [], "the cell lookup must not run against an unwritten lane"


async def test_the_one_community_layer_is_served_from_postgresql_and_says_so() -> None:
    """RUNBOOK section 0.26.1 keeps `interventions` in PostgreSQL; inventing a Parquet lane would be a fiction."""
    source = FakeAgentWarehouse()
    session = RecordingSession()
    session.answer("agent_feature_value_near_point", [{"feature_id": "abc", "distance_meters": 120.0}])

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

    assert payload["applied_bounds"]["served_from"] == "postgresql"
    assert payload["features"][0]["feature_id"] == "abc"
    assert session.markers() == ["agent_feature_value_near_point"]
    assert source.markers() == [], "a PostgreSQL-resident layer never touches the Parquet warehouse"


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


async def test_the_postgresql_only_surface_is_refused_by_the_coverage_tools_with_its_reason() -> None:
    """It has no published day index; saying "uncovered" would be a claim the evidence cannot support."""
    source = FakeAgentWarehouse()
    payload = await call(
        lambda: agent_tools.query_observation_coverage_on_day(
            surface_name="interventions", day=SELECTED_DAY.isoformat()
        ),
        source,
    )

    assert payload["error"] == "surface_not_served_from_parquet"
    assert payload["postgresql_only_surface_names"] == list(POSTGRESQL_ONLY_SURFACE_NAMES)


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


def test_every_catalogue_surface_is_either_a_parquet_lane_or_named_as_postgresql_resident() -> None:
    """No surface may fall between the two: a missing mapping is how a layer silently stops answering."""
    mapped = set(SURFACE_PARQUET_LANES)
    postgresql_only = set(POSTGRESQL_ONLY_SURFACE_NAMES)
    assert mapped | postgresql_only == set(AGENT_SURFACE_NAMES)
    assert not mapped & postgresql_only


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

    Four `.sql` files survive under `sql/agent/` and none of them may name an environmental relation
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


def test_the_agent_sql_tree_holds_only_the_four_statements_that_stay() -> None:
    """A file left behind after its caller moved is the next reader's trap, so the set is asserted."""
    root = Path(__file__).resolve().parents[1] / "src" / "agri_data_service" / "sql" / "agent"
    assert sorted(path.name for path in root.glob("*.sql")) == [
        "feature_value_near_point.sql",
        "forecast_summary_for_cell.sql",
        "materialized_plane_populated.sql",
        "signal_coverage_on_day.sql",
    ]
