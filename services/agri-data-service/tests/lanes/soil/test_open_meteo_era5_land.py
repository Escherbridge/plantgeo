"""`soil-era5-land-direct-forward` through real runner turns: the shipped TOML, the real S14 strategy.

The shipped lane is dark (`executor = "legacy"`), so every turn flips it the way G6 will
(`model_copy(update={"executor": "config"})`). Only the process edges are faked: the Open-Meteo
archive (`SoilWorld`, answering the provider's real multi-location body shape at the native 0.1-degree
nodes) and the bucket (`MemoryLaneStore`). The cell identities are a uuid5 stand-in parsed through
the strategy's real identity parser, because the real `agri.spatial_cell` ids are not in the tree
(`pipeline/lanes/soil/AGENTS.md` "Cell identities").
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cache
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.region import load_region
from agri_data_service.pipeline.lanes.soil.archive import NATIVE_GRID_DEGREES
from agri_data_service.pipeline.lanes.soil.open_meteo_era5_land import (
    STRATEGY,
    VALUE_CELL_COUNT,
    OpenMeteoEra5LandStrategy,
)
from agri_data_service.pipeline.lanes.soil.support import (
    SUPPORT_IDENTITY_SCHEMA,
    parse_support_identity,
    probe_points,
    quantize,
    support_cells,
)
from agri_data_service.pipeline.parquet.lane_registry import config_stream_mirror_violations
from agri_data_service.pipeline.runner.contract import SourceThrottledError, SourceUnavailableError
from agri_data_service.pipeline.runner.reader import registered_stream_contract
from agri_data_service.pipeline.runner.resolve import resolve_strategy
from agri_data_service.warehouse.field_products.soil import SOIL_FIELD_PRODUCTS, SOIL_SOURCE_PARAMETERS
from tests.lane_config.builders import REAL_LANES_DIRECTORY
from tests.runner.fakes import (
    NOW,
    TODAY,
    MemoryLaneStore,
    ScriptedUpstream,
    days_between,
    ports_for,
    run,
    spec_for,
    unwritten_by_day,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from agri_data_service.foundation.lane_config.models import LaneConfig, LaneConfigSet
    from agri_data_service.pipeline.lanes.soil.archive import Point

LANE_ID: Final = "soil-era5-land-direct-forward"
STREAMS: Final = tuple(product.stream for product in SOIL_FIELD_PRODUCTS)
#: `TODAY - publication_lag_days`: the forward window is `[EDGE - 13, EDGE]`, the probe window the same 14 days.
EDGE: Final = TODAY - timedelta(days=5)
LATTICE: Final = load_region("pnw").analysis_lattices["analysis-0p25"]
CELLS: Final = support_cells(LATTICE)
#: A count-faithful stand-in for the 98 ocean cells (the first 98 in key order: none is a probe cell).
MASKED: Final = frozenset((quantize(cell.latitude), quantize(cell.longitude)) for cell in CELLS[: len(CELLS) - 1470])
PROBE: Final = frozenset((quantize(lat), quantize(lon)) for lat, lon in probe_points(LATTICE))
VALUE: Final = 0.5
#: One day's fan-out (31 x 50 + 18 locations) plus the 2-location, 14-day probe.
DAY_WEIGHT: Final = 1568
PROBE_WEIGHT: Final = 2
_ID_NAMESPACE: Final = uuid.UUID("00000000-0000-4000-8000-000000000000")


def _key(point: Point) -> tuple[object, object]:
    return (quantize(point[0]), quantize(point[1]))


@cache
def _identity_document() -> dict[str, object]:
    """The shipped document's shape over the real lattice, with uuid5 ids in place of `agri.spatial_cell`'s."""
    return {
        "schema": SUPPORT_IDENTITY_SCHEMA,
        "cells": [
            {
                "cell_key": cell.cell_key,
                "cell_id": str(uuid.uuid5(_ID_NAMESPACE, cell.cell_key)),
                "cell_latitude": cell.latitude,
                "cell_longitude": cell.longitude,
                "coverage_fraction": 1.0,
                "support_ordinal": ordinal,
            }
            for ordinal, cell in enumerate(CELLS)
        ],
    }


SOIL: Final = OpenMeteoEra5LandStrategy(
    identity=lambda: parse_support_identity(_identity_document()), region=lambda: load_region("pnw")
)


@dataclass
class SoilWorld:
    """The archive: a published day values every land cell, minus `nulls`; `poison` breaks one value."""

    valued: set[date] = field(default_factory=set)
    #: Per day, the (parameter, point) pairs the archive still answers null for.
    nulls: dict[date, set[tuple[str, tuple[object, object]]]] = field(default_factory=dict)
    #: Per day, one (parameter, point) answered with this out-of-range value.
    poison: dict[date, tuple[str, tuple[object, object], float]] = field(default_factory=dict)

    def value(self, day: date, point: Point, parameter: str) -> float | None:
        key = _key(point)
        if day not in self.valued or key in MASKED or (parameter, key) in self.nulls.get(day, set()):
            return None
        poisoned = self.poison.get(day)
        if poisoned is not None and poisoned[:2] == (parameter, key):
            return poisoned[2]
        return VALUE

    def answer(self, endpoint: str, parameters: dict[str, str], probe: bool) -> bytes:  # noqa: ARG002
        """The provider's array body: native-node coordinates, GMT, `location_id` from the second entry."""
        latitudes = [float(text) for text in parameters["latitude"].split(",")]
        longitudes = [float(text) for text in parameters["longitude"].split(",")]
        days = days_between(date.fromisoformat(parameters["start_date"]), date.fromisoformat(parameters["end_date"]))
        body: list[dict[str, object]] = []
        for index, point in enumerate(zip(latitudes, longitudes, strict=True)):
            location: dict[str, object] = {
                "latitude": round(round(point[0] / NATIVE_GRID_DEGREES) * NATIVE_GRID_DEGREES, 6),
                "longitude": round(round(point[1] / NATIVE_GRID_DEGREES) * NATIVE_GRID_DEGREES, 6),
                "elevation": 385.68,
                "generationtime_ms": 0.123,
                "utc_offset_seconds": 0,
                "timezone": "GMT",
                "timezone_abbreviation": "GMT",
                "daily_units": {"time": "iso8601", **dict.fromkeys(SOIL_SOURCE_PARAMETERS, "m³/m³")},
                "daily": {
                    "time": [day.isoformat() for day in days],
                    **{
                        parameter: [self.value(day, point, parameter) for day in days]
                        for parameter in SOIL_SOURCE_PARAMETERS
                    },
                },
            }
            if index:
                location["location_id"] = index
            body.append(location)
        return json.dumps(body).encode("utf-8")


def _landed() -> LaneConfigSet:
    return load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw"), stream_contract=registered_stream_contract)


def _config_lane(*, gap_fill: bool = False) -> LaneConfig:
    """The shipped lane as G6 flips it; `gap_fill` also flips its S12 switch."""
    lane = _landed().lanes[LANE_ID]
    update: dict[str, object] = {"executor": "config"}
    if gap_fill:
        update["schedule"] = lane.schedule.model_copy(update={"gap_fill_enabled": True})
    return lane.model_copy(update=update)


def _publish(store: MemoryLaneStore, days: Iterable[date]) -> None:
    """Seed already-served days for all eight streams (content is irrelevant: a served day is not re-asked)."""
    placeholder = pa.table({"cell_id": pa.array(["seeded"], pa.string())})
    for day in days:
        for stream in STREAMS:
            store.publish(stream, day, placeholder)


def _window_except(*owed: date) -> list[date]:
    """The forward window minus `owed`. The archive values every served day too, or the probe reads `invalid`."""
    return [day for day in days_between(EDGE - timedelta(days=13), EDGE) if day not in owed]


async def _forward(
    store: MemoryLaneStore, answer: Callable[[str, dict[str, str], bool], bytes]
) -> tuple[int, dict[str, object], ScriptedUpstream]:
    upstream = ScriptedUpstream(answer)
    exit_code, report = await run(spec_for(_config_lane()), ports_for(SOIL, store, upstream=upstream))
    return exit_code, report, upstream


# --- the shipped lane --------------------------------------------------------------------------


def test_the_shipped_lane_is_dark_probes_its_edge_and_mirrors_the_eight_soil_registrations() -> None:
    """Legacy executor (the live cron keeps running), S6 probe required, S18 mirror clean, volatile columns valid."""
    landed = _landed()
    lane = landed.lanes[LANE_ID]

    assert LANE_ID not in landed.quarantined
    assert (lane.executor, lane.enabled, lane.schedule.gap_fill_enabled) == ("legacy", True, False)
    assert [stream.slug for stream in lane.streams] == list(STREAMS)
    assert lane.days is not None
    assert lane.days.expected_value_units == VALUE_CELL_COUNT
    assert landed.requires_probe_edge(lane)
    assert resolve_strategy(lane) is STRATEGY
    assert [violation for violation in config_stream_mirror_violations(landed) if LANE_ID in violation] == []


# --- forward turns -----------------------------------------------------------------------------


async def test_a_settled_day_writes_1470_cells_to_every_stream_for_one_day_of_budget() -> None:
    """32 chunk requests + the probe = 1,570 weighted calls; the edge day the probe shows null waits."""
    day = EDGE - timedelta(days=1)
    store, world = MemoryLaneStore(), SoilWorld(valued={day, *_window_except(day, EDGE)})
    _publish(store, _window_except(day, EDGE))

    exit_code, report, upstream = await _forward(store, world.answer)

    assert exit_code == 0
    assert {stream: store.tables[(stream, day)].num_rows for stream in STREAMS} == dict.fromkeys(STREAMS, 1470)
    assert len(upstream.fan_out_sends) == 32
    assert report["weighted_calls"] == DAY_WEIGHT + PROBE_WEIGHT
    held = unwritten_by_day(report)[EDGE.isoformat()]
    assert (held["reason"], held["detail"], held["behind_edge"]) == ("unsettled", "newer_than_probed_edge", False)
    moisture = store.tables[("soil-field-moisture-0-7cm", day)].to_pylist()[0]
    assert moisture["source_snapshot_id"].startswith("direct:")
    assert moisture["selected_source_part_key"].startswith("https://upstream.test/archive?latitude=")
    assert moisture["selected_source_part_key"].endswith("&timezone=GMT&cell_selection=nearest")


async def test_the_declared_volatile_columns_are_exactly_the_columns_rows_stamps_with_the_retrieval_instant() -> None:
    """G6 compare drops these; a column stamped from `retrieved_at` but undeclared would read as drift."""
    day = EDGE - timedelta(days=1)
    store, world = MemoryLaneStore(), SoilWorld(valued={day, *_window_except(day, EDGE)})
    _publish(store, _window_except(day, EDGE))

    await _forward(store, world.answer)

    for stream in _config_lane().streams:
        table = store.tables[(stream.slug, day)]
        stamped = {
            name
            for name in table.column_names
            if pa.types.is_timestamp(table.schema.field(name).type) and set(table.column(name).to_pylist()) == {NOW}
        }
        assert stamped == set(stream.volatile_columns), stream.slug


def _every_land_cell_but_the_probe_pair(parameter: str) -> set[tuple[str, tuple[object, object]]]:
    return {(parameter, _key((cell.latitude, cell.longitude))) for cell in CELLS} - {(parameter, key) for key in PROBE}


@pytest.mark.parametrize(
    ("damage", "reason"),
    [
        pytest.param(
            {"nulls": {(SOIL_SOURCE_PARAMETERS[0], _key((CELLS[-1].latitude, CELLS[-1].longitude)))}},
            "refused_partial",
            id="one-cell-short-on-one-variable",
        ),
        pytest.param(
            {"nulls": _every_land_cell_but_the_probe_pair("vapour_pressure_deficit_max")},
            "refused_partial",
            id="one-variable-unsettled-while-seven-are-whole",
        ),
        pytest.param(
            {"poison": ("vapour_pressure_deficit_max", _key((CELLS[-1].latitude, CELLS[-1].longitude)), 99.0)},
            "strategy_error",
            id="a-value-outside-its-reviewed-range",
        ),
    ],
)
async def test_a_day_that_is_not_whole_is_refused_and_nothing_is_written(
    damage: dict[str, object], reason: str
) -> None:
    """Settled lane: a thin or malformed day is held for the next fire, never published thin (S19)."""
    day = EDGE - timedelta(days=1)
    world = SoilWorld(valued={day, *_window_except(day, EDGE)})
    if "nulls" in damage:
        world.nulls[day] = damage["nulls"]  # type: ignore[assignment]
    if "poison" in damage:
        world.poison[day] = damage["poison"]  # type: ignore[assignment]
    store = MemoryLaneStore()
    _publish(store, _window_except(day, EDGE))

    exit_code, report, _ = await _forward(store, world.answer)

    assert exit_code == 0
    assert unwritten_by_day(report)[day.isoformat()]["reason"] == reason
    assert store.writes() == []


@pytest.mark.parametrize(
    ("probe_answer", "status", "reason"),
    [
        pytest.param(SourceUnavailableError("status 503"), "unavailable", "upstream_unavailable", id="5xx"),
        pytest.param(SourceThrottledError("status 429"), "deferred", "deferred_quota", id="429"),
        pytest.param(b"{}", "unavailable", "upstream_unavailable", id="malformed-body"),
    ],
)
async def test_a_probe_that_cannot_answer_gates_the_whole_window_and_the_turn_exits_zero(
    probe_answer: Exception | bytes, status: str, reason: str
) -> None:
    """O-R3-1: no fan-out request is sent for a window the probe could not read; the owed days say why."""
    day = EDGE - timedelta(days=1)
    world = SoilWorld(valued={day, *_window_except(day, EDGE)})
    store = MemoryLaneStore()
    _publish(store, _window_except(day, EDGE))

    def answer(endpoint: str, parameters: dict[str, str], probe: bool) -> bytes:
        if not probe:
            return world.answer(endpoint, parameters, probe)
        if isinstance(probe_answer, Exception):
            raise probe_answer
        return probe_answer

    exit_code, report, upstream = await _forward(store, answer)

    assert exit_code == 0
    assert report["probe_status"] == status
    assert upstream.fan_out_sends == []
    assert {unwritten_by_day(report)[owed.isoformat()]["reason"] for owed in (day, EDGE)} == {reason}
    assert store.writes() == []


async def test_the_1600_cap_buys_one_day_and_defers_the_next_whole() -> None:
    """Two valued owed days cost 2 x 1,568 + 2: the cap takes one day whole and defers the other whole."""
    older, newer = EDGE - timedelta(days=1), EDGE
    store, world = MemoryLaneStore(), SoilWorld(valued=set(_window_except()))
    _publish(store, _window_except(older, newer))

    exit_code, report, upstream = await _forward(store, world.answer)

    statuses = {day: store.status(STREAMS[0], day) for day in (older, newer)}
    assert exit_code == 0
    assert sorted(statuses.values()) == ["data", "missing"]
    deferred = next(day for day, status in statuses.items() if status == "missing")
    assert unwritten_by_day(report)[deferred.isoformat()]["reason"] == "deferred_budget"
    assert len(upstream.fan_out_sends) == 32
    assert report["weighted_calls"] == DAY_WEIGHT + PROBE_WEIGHT <= _config_lane().budget.forward_max_weighted_calls


# --- gap-fill: the all-null day ----------------------------------------------------------------


@pytest.mark.parametrize("later_day_published", [True, False], ids=["mirror-proven-past", "mirror-not-proven"])
async def test_an_all_null_day_is_absent_only_when_every_stream_publishes_a_later_day(
    later_day_published: bool,
) -> None:
    """The mirrored-past rule: all-null with a later served day is a governed absence; without one it waits."""
    floor = date(2026, 8, 3)
    hole = date(2026, 8, 20)
    gap_last = EDGE - timedelta(days=14)
    store = MemoryLaneStore()
    _publish(store, days_between(floor, hole - timedelta(days=1)))
    if later_day_published:
        _publish(store, days_between(hole + timedelta(days=1), gap_last))
    upstream = ScriptedUpstream(SoilWorld().answer)

    spec = spec_for(_config_lane(gap_fill=True), mode="gap-fill")
    exit_code, report = await run(spec, ports_for(SOIL, store, upstream=upstream))

    assert exit_code == 0
    assert len(upstream.fan_out_sends) == 32
    if later_day_published:
        assert {store.status(stream, hole) for stream in STREAMS} == {"absent"}
    else:
        assert {store.status(stream, hole) for stream in STREAMS} == {"missing"}
        assert unwritten_by_day(report)[hole.isoformat()]["reason"] == "unsettled"
    assert store.writes() == []
