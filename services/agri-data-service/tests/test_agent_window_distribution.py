"""Window distribution at a point (owner decision 2026-10-04) through the tool function and the HTTP bridge.

Real DuckDB over small Parquet fixtures (`LocalWarehouse`) and the map's own window day classification.
See agent/AGENTS.md, "Window distribution (2026-10-04)".
"""

# ruff: noqa: PLR2004 - fixture coordinates, days and measured values are the assertions.

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
import structlog

from agri_data_service.agent import tools, window_distribution
from agri_data_service.agent.day_tolerance import day_tolerance
from agri_data_service.agent.selection_reads import SPARSE_AREA_LANES, SelectionReader
from agri_data_service.agent.surfaces import APP_SURFACE_NAMES, SURFACE_PARQUET_LANES
from agri_data_service.parquet_ops.warehouse_reader import GeometrySupport, spatial_support
from agri_data_service.routes import agent_tools as route
from tests.test_agent_closest_datapoint import BOISE, GORGE, _enum_arrays, drought_row, ndvi_row, weather_station_row
from tests.test_agent_selection_evidence import LocalWarehouse, climate_row

if TYPE_CHECKING:
    from pathlib import Path

#: The window's last day; "today" sits well after it unless a test is about the clamp.
END = date(2026, 6, 15)
START = END - timedelta(days=29)
TODAY = date(2026, 9, 1)
DEW_POINT = "climate-field-dew-point"


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(window_distribution, "utc_today", lambda: TODAY)


def linear_quantile(values: list[float], fraction: float) -> float:
    """DuckDB `quantile_cont`: linear interpolation between the two closest ranks."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (position - low) * (ordered[high] - ordered[low])


def expected_stats(values: list[float]) -> dict[str, float]:
    return {
        "min": min(values),
        "p10": linear_quantile(values, 0.1),
        "median": linear_quantile(values, 0.5),
        "p90": linear_quantile(values, 0.9),
        "max": max(values),
        "mean": sum(values) / len(values),
    }


async def distribution(source: LocalWarehouse, surface: str, **overrides: Any) -> dict[str, Any]:
    arguments = {
        "surface_name": surface,
        **BOISE,
        "range_start": START.isoformat(),
        "range_end": END.isoformat(),
        "zoom": 13,
        **overrides,
    }
    async with tools.run_context(warehouse_source=source):
        return json.loads(await tools.query_distribution_at_point(**arguments))


def fire_cell_row(day: date, count: int) -> dict[str, Any]:
    """One fire-detections lattice cell whose 0.005-degree support is [-116.485,-116.48] x [43.495,43.5]."""
    return {
        "cell_longitude": -116.487,
        "cell_latitude": 43.493,
        "observed_day": day,
        "detection_count": count,
        "frp_sum": 1.0,
        "frp_observation_count": count,
        "high_confidence_detection_count": count,
        "newest_observed_at": datetime.combine(day, datetime.min.time(), UTC),
    }


#: Inside the fire cell above, with margin on every side (no floating-point edge case).
FIRE_PROBE = {"longitude": -116.483, "latitude": 43.497}


def write_absence(source: LocalWarehouse, lane: str, day: date) -> None:
    source.listing_store.write_absence(
        lane,
        "observed",
        13,
        day,
        reason="source_empty",
        upstream_response="zero records",
        recorded_at=datetime.combine(day, datetime.min.time(), UTC),
        run_id="fixture",
    )


# --- A daily lane over 30 days with gaps: exact stats and days_with_data ------------------------


@pytest.mark.parametrize(
    ("surface", "published", "absent", "values", "absence_as_value"),
    [
        # Dew point: five days with data, a repeated value on two days (one day each), a governed
        # absence that is a fill value -- excluded, not zero -- and 24 unwritten days.
        (DEW_POINT, {0: 5.0, 3: 1.0, 10: 5.0, 11: 2.0, 29: 9.0}, (5,), [5.0, 1.0, 5.0, 2.0, 9.0], None),
        # One day only: every statistic is that day's value.
        (DEW_POINT, {14: 3.5}, (), [3.5], None),
        # Fire detections: FIRMS answered "zero detections" on the absent days, a measured 0.
        ("fire-detections", {2: 3, 9: 5}, (4, 20), [3.0, 5.0, 0.0, 0.0], 0.0),
    ],
)
async def test_a_daily_lane_over_a_window_with_gaps_gives_exact_stats(  # noqa: PLR0913 - one table row per case
    tmp_path: Path,
    surface: str,
    published: dict[int, float],
    absent: tuple[int, ...],
    values: list[float],
    absence_as_value: float | None,
) -> None:
    source = LocalWarehouse()
    for offset, value in published.items():
        day = START + timedelta(days=offset)
        if surface == DEW_POINT:
            rows = [
                climate_row(day, longitude=-116, latitude=43, value=value),  # covers Boise
                climate_row(day, longitude=-115, latitude=44, value=100.0),  # another cell: never counted
            ]
        else:
            rows = [fire_cell_row(day, int(value))]
        source.write(tmp_path, surface, day, rows)
    for offset in absent:
        write_absence(source, surface, START + timedelta(days=offset))
    probe = FIRE_PROBE if surface == "fire-detections" else BOISE
    result = await distribution(source, surface, **probe)
    assert (result["surface"], result["range_start"], result["range_end"]) == (
        surface,
        START.isoformat(),
        END.isoformat(),
    )
    [lane] = result["lanes"]
    assert lane["state"] == "published"
    assert lane["days_in_window"] == 30
    assert lane["days_with_data"] == len(values)
    assert lane["stats"] == pytest.approx(expected_stats(values))
    assert (lane["spatial_relation"], lane["distance_km"]) == ("covers", 0.0)
    assert lane["governed_absence_as_value"] == absence_as_value
    assert lane["day_states"]["published"] == len(published)
    assert lane["day_states"].get("governed_absence", 0) == len(absent)


async def test_several_readings_on_one_day_collapse_to_that_days_mean_before_the_window_stats(
    tmp_path: Path,
) -> None:
    """Station readings: the day's value is the mean of its readings, so a busy day weighs one day."""
    source = LocalWarehouse()
    first, second = START, START + timedelta(days=1)
    source.write(
        tmp_path,
        "weather-observations",
        first,
        [
            weather_station_row(first, longitude=-116.3, latitude=43.49, temperature=10.0),
            weather_station_row(first, longitude=-116.3, latitude=43.49, temperature=20.0),
            weather_station_row(first, longitude=-116.3, latitude=43.49, temperature=30.0),
        ],
    )
    source.write(
        tmp_path,
        "weather-observations",
        second,
        [weather_station_row(second, longitude=-116.3, latitude=43.49, temperature=40.0)],
    )
    result = await distribution(source, "weather-observations")
    by_signal = {entry["signal_name"]: entry for entry in result["lanes"]}
    assert set(by_signal) == {"air_temperature", "relative_humidity", "wind_speed", "precipitation"}
    temperature = by_signal["air_temperature"]
    assert temperature["days_with_data"] == 2
    assert temperature["stats"] == pytest.approx(expected_stats([20.0, 40.0]))
    assert (temperature["spatial_relation"], temperature["distance_km_basis"]) == ("nearest_cell", "source_coordinate")
    assert temperature["distance_km"] == pytest.approx(15.3, abs=0.2)


# --- A covering cell vs a nearest cell ---------------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "relation", "distance_km", "nearest_searches"),
    [
        # Support [-122,-121.75] x [45.5,45.75] contains the Gorge probe: no search past it.
        ((-121.875, 45.625), "covers", 0.0, 0),
        # Support [-121.75,-121.5] x [45.75,46]: its corner is 17.37 km away, found by ONE search.
        ((-121.625, 45.875), "nearest_cell", 17.37, 1),
    ],
)
async def test_a_covering_cell_is_used_else_one_nearest_search_serves_the_whole_window(  # noqa: PLR0913 - table row
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cell: tuple[float, float],
    relation: str,
    distance_km: float,
    nearest_searches: int,
) -> None:
    searches: list[int] = []
    original = SelectionReader._nearest_cell

    def counted(self: SelectionReader, *args: Any, **kwargs: Any) -> Any:
        searches.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SelectionReader, "_nearest_cell", counted)
    source = LocalWarehouse()
    days = [START + timedelta(days=offset) for offset in (0, 7, 14, 21, 28)]
    values = [0.2, 0.4, 0.5, 0.7, 0.9]
    for day, value in zip(days, values, strict=True):
        longitude, latitude = cell
        rows = [
            ndvi_row(day, longitude=longitude, latitude=latitude, value=value),
            ndvi_row(day, longitude=-120.125, latitude=44.125, value=0.05),  # far away: never counted
        ]
        source.write(tmp_path, "vegetation", day, rows)
    [lane] = (await distribution(source, "vegetation", **GORGE))["lanes"]
    assert (lane["signal_name"], lane["unit"]) == ("ndvi", "unitless")
    assert lane["spatial_relation"] == relation
    assert lane["distance_km"] == pytest.approx(distance_km, abs=0.02)
    assert lane["days_with_data"] == 5
    assert lane["stats"] == pytest.approx(expected_stats(values))
    assert len(searches) == nearest_searches, "never one nearest search per day"


# --- Sparse-area lanes: outside every polygon is an answer, not a value ------------------------


@pytest.mark.parametrize(
    ("probe", "relation", "has_stats"),
    [
        ({"longitude": -116.5, "latitude": 43.5}, "covers", True),  # inside the D1 square
        ({"longitude": -118.0, "latitude": 43.5}, "nearest_area_outside", False),  # 121 km west of it
    ],
)
async def test_a_sparse_area_lane_outside_every_polygon_gives_no_stats(
    tmp_path: Path, probe: dict[str, float], relation: str, *, has_stats: bool
) -> None:
    source = LocalWarehouse()
    releases = [START + timedelta(days=offset) for offset in (2, 9, 16, 23)]
    for release in releases:
        source.write(tmp_path, "drought", release, [drought_row(release)])
    [lane] = (await distribution(source, "drought-areas", **probe))["lanes"]
    assert lane["state"] == "published"
    assert lane["spatial_relation"] == relation
    if has_stats:
        assert lane["days_with_data"] == 4
        assert lane["stats"] == pytest.approx(expected_stats([1.0] * 4))
    else:
        assert (lane["stats"], lane["days_with_data"]) == (None, 0)
        assert lane["distance_km_basis"] == "geometry_centroid"
        assert lane["distance_km"] == pytest.approx(121.0, abs=1.0)


# --- Static lanes and release lanes ------------------------------------------------------------


@pytest.mark.parametrize(
    ("surface", "state", "refusal_code"),
    [
        ("soil-survey", "static_not_applicable", None),
        ("watersheds", "static_not_applicable", None),
        ("crop-cover", "refused", "release_lane_not_distributed"),
    ],
)
async def test_a_static_lane_is_not_applicable_and_reads_nothing(
    surface: str, state: str, refusal_code: str | None
) -> None:
    source = LocalWarehouse()
    [lane] = (await distribution(source, surface))["lanes"]
    assert lane["state"] == state
    assert lane["static"] is (state == "static_not_applicable")
    assert (lane["stats"], lane["days_with_data"], lane["days_in_window"]) == (None, 0, 30)
    assert lane.get("refusal_code") == refusal_code
    assert source.operations == [], "no warehouse read for a lane with nothing to distribute"


# --- The window: clamped to today, refused past 366 days ---------------------------------------


async def test_a_window_past_today_is_clamped_and_never_reads_a_later_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    today = START + timedelta(days=9)
    monkeypatch.setattr(window_distribution, "utc_today", lambda: today)
    source = LocalWarehouse()
    for offset, value in ((0, 2.0), (9, 4.0), (12, 50.0)):  # day 12 lies after "today"
        day = START + timedelta(days=offset)
        source.write(tmp_path, DEW_POINT, day, [climate_row(day, longitude=-116, latitude=43, value=value)])
    result = await distribution(source, DEW_POINT)
    assert (result["range_end"], result["requested_range_end"], result["range_end_clamped"]) == (
        today.isoformat(),
        END.isoformat(),
        True,
    )
    [lane] = result["lanes"]
    assert (lane["days_in_window"], lane["days_with_data"]) == (10, 2)
    assert lane["stats"]["max"] == 4.0


@pytest.mark.parametrize(
    ("range_start", "range_end", "message"),
    [
        (START - timedelta(days=337), END, "at most 366"),  # 367 calendar days
        (END, START, "before range_start"),
        (TODAY + timedelta(days=1), TODAY + timedelta(days=5), "after today"),
        ("2026-02-30", END, "range_start"),
    ],
)
async def test_an_invalid_window_is_refused_before_any_read(
    range_start: date | str, range_end: date, message: str
) -> None:
    source = LocalWarehouse()
    result = await distribution(source, DEW_POINT, range_start=str(range_start), range_end=range_end.isoformat())
    assert (result["state"], result["refusal_code"], result["lanes"]) == ("refused", "invalid_window", [])
    assert message in result["message"]
    assert source.operations == []


async def test_366_days_is_the_longest_window_answered(tmp_path: Path) -> None:
    source = LocalWarehouse()
    first = END - timedelta(days=365)
    for day in (first, END):
        source.write(tmp_path, DEW_POINT, day, [climate_row(day, longitude=-116, latitude=43, value=1.0)])
    [lane] = (await distribution(source, DEW_POINT, range_start=first.isoformat()))["lanes"]
    assert (lane["days_in_window"], lane["days_with_data"]) == (366, 2)


# --- The HTTP bridge: same result, one structured log event ------------------------------------


async def test_the_bridge_answers_the_distribution_and_logs_one_agent_tool_call_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = LocalWarehouse()
    for offset, value in ((0, 2.0), (5, 6.0)):
        day = START + timedelta(days=offset)
        source.write(tmp_path, DEW_POINT, day, [climate_row(day, longitude=-116, latitude=43, value=value)])
    monkeypatch.setattr(route, "run_context", lambda: tools.run_context(warehouse_source=source))
    arguments = {
        "surface_name": DEW_POINT,
        **BOISE,
        "range_start": START.isoformat(),
        "range_end": END.isoformat(),
    }
    payload = {"name": "distribution_at_point", "arguments": arguments}
    with structlog.testing.capture_logs() as logs:
        response = await route.call_agent_tool(SimpleNamespace(json=payload, body=json.dumps(payload).encode()))
    assert response.status == 200
    [lane] = json.loads(response.body)["result"]["lanes"]
    assert (lane["parquet_lane"], lane["state"], lane["days_with_data"]) == (DEW_POINT, "published", 2)
    assert lane["stats"] == pytest.approx(expected_stats([2.0, 6.0]))
    events = [entry for entry in logs if entry["event"] == "agent_tool_call"]
    assert len(events) == 1
    event = events[0]
    assert {key: event[key] for key in ("tool", "surface", "lanes", "state", "range_start", "range_end")} == {
        "tool": "distribution_at_point",
        "surface": DEW_POINT,
        "lanes": [DEW_POINT],
        "state": "published",
        "range_start": START.isoformat(),
        "range_end": END.isoformat(),
    }
    assert (event["spatial_relation"], event["distance_km"], event["record_count"]) == ("covers", 0.0, 2)
    assert not {"stats", "result", "arguments", "longitude", "latitude"} & set(event)


# --- The catalogue -----------------------------------------------------------------------------


def test_every_dated_lane_has_a_window_measure_and_geometry_measures_are_sparse_areas() -> None:
    """A dated lane without a measure would be refused at runtime; pin the catalogue instead."""
    for surface, lanes in SURFACE_PARQUET_LANES.items():
        if surface in APP_SURFACE_NAMES:
            continue
        for lane in lanes:
            if day_tolerance(lane).mode == "nearest":
                assert lane in window_distribution.WINDOW_MEASURES, lane
    for lane in window_distribution.WINDOW_MEASURES:
        if isinstance(spatial_support(lane, "observed"), GeometrySupport):
            assert lane in SPARSE_AREA_LANES, f"{lane}: a tiling polygon lane's nearest polygon has no daily identity"


def test_the_published_tool_adds_no_enum_or_bound_to_the_gemini_budget() -> None:
    [schema] = [
        entry for entry in route.environmental_tool_schemas() if entry["function"]["name"] == "distribution_at_point"
    ]
    parameters = schema["function"]["parameters"]
    assert set(parameters["properties"]) == {
        "surface_name",
        "longitude",
        "latitude",
        "range_start",
        "range_end",
        "zoom",
    }
    assert set(parameters["required"]) == {"surface_name", "longitude", "latitude", "range_start", "range_end"}
    assert _enum_arrays(parameters) == []
    assert "enum" not in json.dumps(parameters)
