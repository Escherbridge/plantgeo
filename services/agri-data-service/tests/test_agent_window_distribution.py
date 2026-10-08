"""Window distribution at a point (owner decision 2026-10-04) through the tool function and the HTTP bridge.

Real DuckDB over small Parquet fixtures (`LocalWarehouse`) and the map's own window day classification.
See agent/AGENTS.md, "Window distribution (2026-10-04)".
"""

# ruff: noqa: PLR2004 - fixture coordinates, days and measured values are the assertions.

from __future__ import annotations

import asyncio
import json
import math
import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import structlog

from agri_data_service.agent import tools, window_distribution
from agri_data_service.agent.day_tolerance import day_tolerance
from agri_data_service.agent.selection_reads import SPARSE_AREA_LANES, SelectionReader
from agri_data_service.agent.surfaces import APP_SURFACE_NAMES, SURFACE_PARQUET_LANES, surface_lanes
from agri_data_service.parquet_ops import faults
from agri_data_service.parquet_ops.duckdb_session import SERVING_MAX_CONCURRENT_READS, open_guarded_connection
from agri_data_service.parquet_ops.warehouse_reader import GeometrySupport, spatial_support
from agri_data_service.routes import agent_tools as route
from tests.test_agent_closest_datapoint import BOISE, GORGE, _enum_arrays, drought_row, ndvi_row, weather_station_row
from tests.test_agent_selection_evidence import LocalSession, LocalWarehouse, climate_row, read_surface

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


def fire_cell_row(day: date, count: int, *, longitude: float = -116.487, latitude: float = 43.493) -> dict[str, Any]:
    """One fire-detections lattice cell; the default's 0.005-degree support is [-116.485,-116.48] x [43.495,43.5]."""
    return {
        "cell_longitude": longitude,
        "cell_latitude": latitude,
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


def write_mixed_exposure_days(tmp_path: Path) -> LocalWarehouse:
    """Two older-export days (`allowed_client_exposure = false`) and one direct-writer day (`true`)."""
    source = LocalWarehouse()
    exported = {0: (2.0, False), 5: (4.0, False), 29: (9.0, True)}
    for offset, (value, exposed) in exported.items():
        day = START + timedelta(days=offset)
        row = {**climate_row(day, longitude=-116, latitude=43, value=value), "allowed_client_exposure": exposed}
        source.write(tmp_path, DEW_POINT, day, [row])
    return source


# Owner 2026-10-07: `allowed_client_exposure` is an export artifact, not a read gate. The map serves
# false-flagged rows, so both agent reads must too (agent/AGENTS.md, "Exposure is not a read gate").


async def test_the_window_distribution_counts_days_exported_with_exposure_false(tmp_path: Path) -> None:
    source = write_mixed_exposure_days(tmp_path)
    [lane] = (await distribution(source, DEW_POINT))["lanes"]
    assert (lane["state"], lane["days_with_data"]) == ("published", 3)
    assert lane["stats"] == pytest.approx(expected_stats([2.0, 4.0, 9.0]))


async def test_the_selection_read_serves_a_day_exported_with_exposure_false(tmp_path: Path) -> None:
    source = write_mixed_exposure_days(tmp_path)
    selection = await read_surface(
        source, DEW_POINT, day=START.isoformat(), range_start=START.isoformat(), range_end=END.isoformat()
    )
    selected = selection["lanes"][0]["selected"]
    assert (selected["state"], selected["served_day"]) == ("published", START.isoformat())
    [feature] = selected["features"]
    assert feature["covers_probe_point"] is True
    assert feature["properties"]["normalized_value"] == 2.0
    assert feature["properties"]["allowed_client_exposure"] is False


#: A detection cell one lattice step east of the probe's: in the same tile, never covering the probe.
FIRE_ELSEWHERE = {"longitude": -116.477, "latitude": 43.493}


@pytest.mark.parametrize(
    ("at_point", "elsewhere", "absent", "values"),
    [
        # Detections only elsewhere on day 3 and a governed absence on day 7: two measured zeros here.
        ({}, (3,), (7,), [0.0, 0.0]),
        # The point's own cell burned on day 1 (4 detections); day 3 elsewhere and day 7 absent are zeros.
        ({1: 4}, (3,), (7,), [4.0, 0.0, 0.0]),
    ],
)
async def test_fire_detections_count_every_answered_day_at_the_point_and_never_borrow_a_nearest_cell(  # noqa: PLR0913
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    at_point: dict[int, int],
    elsewhere: tuple[int, ...],
    absent: tuple[int, ...],
    values: list[float],
) -> None:
    """A published day with detections elsewhere is a 0 at the point, exactly like a governed absence."""
    searches: list[int] = []
    original = SelectionReader._nearest_cell

    def counted(self: SelectionReader, *args: Any, **kwargs: Any) -> Any:
        searches.append(1)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SelectionReader, "_nearest_cell", counted)
    source = LocalWarehouse()
    for offset in sorted({*at_point, *elsewhere}):
        day = START + timedelta(days=offset)
        rows = [fire_cell_row(day, 9, **FIRE_ELSEWHERE)] if offset in elsewhere else []
        if offset in at_point:
            rows.append(fire_cell_row(day, at_point[offset]))
        source.write(tmp_path, "fire-detections", day, rows)
    for offset in absent:
        write_absence(source, "fire-detections", START + timedelta(days=offset))
    [lane] = (await distribution(source, "fire-detections", **FIRE_PROBE))["lanes"]
    published_days = len({*at_point, *elsewhere})
    assert lane["day_states"]["published"] == published_days
    assert lane["days_with_data"] == published_days + len(absent) == len(values)
    assert lane["stats"] == pytest.approx(expected_stats(values))
    assert lane["stats"]["median"] == 0.0
    assert (lane["spatial_relation"], lane["distance_km"]) == ("covers", 0.0)
    assert searches == [], "a detection cell elsewhere is never the value at the point"


# --- signal_name: one lane of a multi-lane surface ---------------------------------------------


AIR_TEMPERATURE_LANES = {
    "climate-field-air-temperature-mean": ("air_temperature_mean", 10.0),
    "climate-field-air-temperature-max": ("air_temperature_max", 20.0),
    "climate-field-air-temperature-min": ("air_temperature_min", 0.0),
}


async def test_a_signal_name_reads_only_its_lane_and_an_unknown_one_is_refused_before_any_read(
    tmp_path: Path,
) -> None:
    source = LocalWarehouse()
    for lane, (signal, value) in AIR_TEMPERATURE_LANES.items():
        for offset in (0, 4):
            day = START + timedelta(days=offset)
            row = climate_row(day, longitude=-116, latitude=43, value=value + offset)
            source.write(tmp_path, lane, day, [{**row, "signal_name": signal}])

    whole = await distribution(source, "climate-field-air-temperature")
    assert [entry["parquet_lane"] for entry in whole["lanes"]] == list(AIR_TEMPERATURE_LANES)
    assert len(source.operations) == 3

    source.operations.clear()
    narrowed = await distribution(source, "climate-field-air-temperature", signal_name="air_temperature_max")
    [lane] = narrowed["lanes"]
    assert (lane["parquet_lane"], lane["signal_name"]) == ("climate-field-air-temperature-max", "air_temperature_max")
    assert lane["stats"] == pytest.approx(expected_stats([20.0, 24.0]))
    assert narrowed["signal_name"] == "air_temperature_max"
    assert len(source.operations) == 1, "one lane read, not three"

    source.operations.clear()
    refused = await distribution(source, "climate-field-air-temperature", signal_name="dew_point_temperature")
    assert (refused["state"], refused["refusal_code"], refused["lanes"]) == ("refused", "unknown_signal_name", [])
    assert "air_temperature_max" in refused["message"]
    assert source.operations == []


async def test_a_signal_name_narrows_a_multi_measure_lane_to_that_measure(tmp_path: Path) -> None:
    source = LocalWarehouse()
    source.write(
        tmp_path,
        "weather-observations",
        START,
        [weather_station_row(START, longitude=-116.3, latitude=43.49, temperature=12.0)],
    )
    [lane] = (await distribution(source, "weather-observations", signal_name="air_temperature"))["lanes"]
    assert (lane["signal_name"], lane["unit"], lane["days_with_data"]) == ("air_temperature", "C", 1)
    assert lane["stats"]["median"] == 12.0


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


# --- Latency shape: cost follows published parts and lanes overlap -----------------------------
# Production measurements and the budget they are held against: agent/AGENTS.md, "Window
# distribution", caveat 4.


class StatementLog:
    """A DuckDB connection that records, per statement, how many fixture part files it names."""

    def __init__(self, connection: Any, parts: set[str], named: list[int]) -> None:
        self._connection = connection
        self._parts = parts
        self._named = named

    def execute(self, statement: str, parameters: list[object] | None = None) -> Any:
        listed = {item for value in parameters or () if isinstance(value, list) for item in value}
        self._named.append(len(listed & self._parts))
        return self._connection.execute(statement, parameters)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


@dataclass
class StatementCountingWarehouse(LocalWarehouse):
    """Real DuckDB, with every statement's part-file count recorded in order."""

    named_parts: list[int] = field(default_factory=list)

    async def run(self, work: Any, *, operation: str) -> Any:
        self.operations.append(operation)
        connection = open_guarded_connection()
        try:
            counted = StatementLog(connection, set(self.files.values()), self.named_parts)
            return work(LocalSession(counted, self.files))
        finally:
            connection.close()


async def test_a_365_day_window_runs_the_same_statements_as_30_days_over_only_its_published_parts(
    tmp_path: Path,
) -> None:
    published = [END - timedelta(days=offset) for offset in (0, 11, 22)]  # inside both windows
    plans: dict[int, list[int]] = {}
    for window_days in (30, 365):
        source = StatementCountingWarehouse()
        for day in published:
            source.write(tmp_path, DEW_POINT, day, [climate_row(day, longitude=-116, latitude=43, value=1.0)])
        first = END - timedelta(days=window_days - 1)
        [lane] = (await distribution(source, DEW_POINT, range_start=first.isoformat()))["lanes"]
        assert (lane["days_in_window"], lane["days_with_data"]) == (window_days, 3)
        plans[window_days] = source.named_parts
    assert plans[365] == plans[30], "a longer window adds no statement: never one per calendar day"
    assert set(plans[365]) == {3}, "every statement reads the published parts, never a file per calendar day"


@dataclass
class SlotLimitedWarehouse(LocalWarehouse):
    """A process with `slots` serving slots, like `run_serving_read`'s admission gate.

    A read that finds every slot taken is refused `serving_at_capacity` after the (scaled-down) slot
    wait: a full process stays full because other callers take any slot that frees. Every read holds
    its slot for `hold_seconds`, long enough for a sibling read to start.
    """

    slots: int = SERVING_MAX_CONCURRENT_READS
    hold_seconds: float = 0.05
    slot_wait_seconds: float = 0.2
    in_flight: int = 0
    peak: int = 0
    refused: int = 0

    async def run(self, work: Any, *, operation: str) -> Any:
        if self.in_flight >= self.slots:
            await asyncio.sleep(self.slot_wait_seconds)
            self.refused += 1
            raise faults.serving_at_capacity(operation=operation, concurrent_reads=self.slots)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self.hold_seconds)
            return await super().run(work, operation=operation)
        finally:
            self.in_flight -= 1


MULTI_LANE_SURFACE = "climate-field-air-temperature"


def write_multi_lane_surface(source: LocalWarehouse, root: Path) -> tuple[str, ...]:
    """Two days on every lane of the three-lane surface; lane i's window mean is 14.5 + i."""
    lanes = surface_lanes(MULTI_LANE_SURFACE)
    root.mkdir(parents=True, exist_ok=True)
    for index, lane in enumerate(lanes):
        for offset in (0, 9):
            day = START + timedelta(days=offset)
            value = 10.0 + index + offset
            source.write(root, lane, day, [climate_row(day, longitude=-116, latitude=43, value=value)])
    return lanes


def lane_means(result: dict[str, Any]) -> list[tuple[str, float]]:
    return [(entry["parquet_lane"], entry["stats"]["mean"]) for entry in result["lanes"]]


EXPECTED_MEANS = [(lane, 14.5 + index) for index, lane in enumerate(surface_lanes(MULTI_LANE_SURFACE))]


async def test_one_multi_lane_call_reads_two_lanes_at_once_and_answers_in_catalogue_order(tmp_path: Path) -> None:
    source = SlotLimitedWarehouse()
    lanes = write_multi_lane_surface(source, tmp_path)
    assert len(lanes) > window_distribution.LANE_READ_CONCURRENCY
    assert lane_means(await distribution(source, MULTI_LANE_SURFACE)) == EXPECTED_MEANS
    assert (source.peak, source.refused) == (window_distribution.LANE_READ_CONCURRENCY, 0)


async def test_two_concurrent_multi_lane_calls_borrow_one_extra_slot_between_them_and_none_is_refused(
    tmp_path: Path,
) -> None:
    source = SlotLimitedWarehouse()
    write_multi_lane_surface(source, tmp_path)
    first, second = await asyncio.gather(
        distribution(source, MULTI_LANE_SURFACE), distribution(source, MULTI_LANE_SURFACE)
    )
    assert lane_means(first) == lane_means(second) == EXPECTED_MEANS
    # Each call's own slot plus ONE borrowed slot for the whole process: per-call borrowing would need 4.
    assert (source.peak, source.refused) == (SERVING_MAX_CONCURRENT_READS, 0)


async def test_a_borrowed_lane_refused_at_capacity_is_read_on_the_calls_own_slot_not_reported_refused(
    tmp_path: Path,
) -> None:
    # One slot left in the process: the borrowed read is refused after a wait longer than the call's
    # own slot takes over every other lane, so the handed-back lane is read after that loop ended.
    crowded = SlotLimitedWarehouse(slots=1, slot_wait_seconds=1.0)
    write_multi_lane_surface(crowded, tmp_path / "crowded")
    assert lane_means(await distribution(crowded, MULTI_LANE_SURFACE)) == EXPECTED_MEANS
    assert (crowded.peak, crowded.refused) == (1, 1)

    # The hand-back returned the process's borrowed slot: the next call reads two lanes at once again.
    free = SlotLimitedWarehouse()
    write_multi_lane_surface(free, tmp_path / "free")
    assert lane_means(await distribution(free, MULTI_LANE_SURFACE)) == EXPECTED_MEANS
    assert free.peak == window_distribution.LANE_READ_CONCURRENCY


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
        "signal_name",
    }
    assert set(parameters["required"]) == {"surface_name", "longitude", "latitude", "range_start", "range_end"}
    assert _enum_arrays(parameters) == []
    assert "enum" not in json.dumps(parameters)


# --- The web contract fixture ------------------------------------------------------------------

#: The web parses every case here with `distributionAtPointResultSchema` and checks what it renders.
WEB_DISTRIBUTION_CONTRACT = (
    Path(__file__).resolve().parents[3] / "src" / "__tests__" / "services" / "agri-distribution-contract.fixture.json"
)
#: Set to 1 to rewrite the web fixture from the real outputs below instead of checking it.
REWRITE_VARIABLE = "AGRI_WRITE_WEB_DISTRIBUTION_FIXTURE"


@dataclass
class CapacityRefusingWarehouse(LocalWarehouse):
    """Every read slot taken: the transient refusal a lane reports when serving is at capacity."""

    async def run(self, work: Any, *, operation: str) -> Any:  # noqa: ARG002 - refused before any work runs
        self.operations.append(operation)
        raise faults.serving_at_capacity(operation=operation, concurrent_reads=3)


async def _contract_cases(root: Path) -> dict[str, dict[str, Any]]:
    """One real `query_distribution_at_point` output per shape the web must parse."""
    published = LocalWarehouse()
    for offset, value in ((0, 2.0), (5, 6.0)):
        day = START + timedelta(days=offset)
        published.write(root, DEW_POINT, day, [climate_row(day, longitude=-116, latitude=43, value=value)])
    nearest = LocalWarehouse()
    for offset, value in ((0, 0.25), (14, 0.75)):
        day = START + timedelta(days=offset)
        nearest.write(root, "vegetation", day, [ndvi_row(day, longitude=-121.625, latitude=45.875, value=value)])
    fire = LocalWarehouse()
    fire.write(root, "fire-detections", START, [fire_cell_row(START, 9, **FIRE_ELSEWHERE)])
    write_absence(fire, "fire-detections", START + timedelta(days=1))
    return {
        "published": await distribution(published, DEW_POINT),
        "published_nearest_cell": await distribution(nearest, "vegetation", **GORGE),
        "published_fire_zero_at_point": await distribution(fire, "fire-detections", **FIRE_PROBE),
        "no_data_in_window": await distribution(LocalWarehouse(), DEW_POINT),
        "static_not_applicable": await distribution(LocalWarehouse(), "soil-survey"),
        "refused_release_lane": await distribution(LocalWarehouse(), "crop-cover"),
        "refused_at_capacity": await distribution(CapacityRefusingWarehouse(), DEW_POINT),
        "whole_call_refusal": await distribution(
            LocalWarehouse(), DEW_POINT, range_start=END.isoformat(), range_end=START.isoformat()
        ),
        "whole_call_unknown_signal": await distribution(LocalWarehouse(), DEW_POINT, signal_name="not_a_signal"),
    }


async def test_the_web_distribution_contract_fixture_is_what_the_tool_returns(tmp_path: Path) -> None:
    """Producer-checked like the catalogue fixture: regenerate with AGRI_WRITE_WEB_DISTRIBUTION_FIXTURE=1."""
    produced = json.loads(json.dumps(await _contract_cases(tmp_path)))
    assert produced["refused_at_capacity"]["lanes"][0]["refusal_code"] == "serving_at_capacity"
    assert produced["published_fire_zero_at_point"]["lanes"][0]["stats"]["median"] == 0.0
    if os.environ.get(REWRITE_VARIABLE) == "1":
        with WEB_DISTRIBUTION_CONTRACT.open("w", encoding="utf-8", newline="\n") as fixture:
            fixture.write(json.dumps(produced, indent=2) + "\n")
    assert json.loads(WEB_DISTRIBUTION_CONTRACT.read_text(encoding="utf-8")) == produced
