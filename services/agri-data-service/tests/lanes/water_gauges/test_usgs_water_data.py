"""`water-gauges-daily` through real runner turns: the shipped TOML, the real provider and client, the S14 strategy.

Only the process edges are faked: the USGS API (`usgs_world.py`, behind `httpx.MockTransport`), the bucket
(`tests/runner/fakes.py::MemoryLaneStore`) and the clock. Spec §7a; plan Phase 3.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

import httpx
import pytest

from agri_data_service.execution.lane_catalogue import LANE_DISABLED_REASON, build_lane_catalogue
from agri_data_service.execution.lane_specs import LANE_SPECS
from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.region import load_region
from agri_data_service.ingest.usgs_water_data import (
    UsgsWaterDataPayloadError,
    parse_daily_values,
    publisher_named_day,
    tile_boxes,
)
from agri_data_service.pipeline.lanes.water_gauges.usgs_water_data import MAX_DAYS_PER_REQUEST
from agri_data_service.pipeline.parquet.lane_registry import (
    _MEASURED_COMPLETE_HISTORY_FLOORS,
    CALENDAR_HISTORY_FLOOR,
    LANE_REGISTRY,
)
from agri_data_service.pipeline.runner.binding import ConfigProviderClient
from agri_data_service.pipeline.runner.checkpoints import TurnCheckpoints
from agri_data_service.pipeline.runner.completeness import SourceCompleteness
from agri_data_service.pipeline.runner.exits import TurnConfigurationError
from agri_data_service.pipeline.runner.receipts import DayReceipt
from agri_data_service.pipeline.runner.resolve import resolve_strategy
from agri_data_service.pipeline.runner.windows import revision_rotation_days
from agri_data_service.pipeline.validation import water_gauges as legacy_validation
from tests.lane_config.builders import REAL_LANES_DIRECTORY
from tests.lanes.water_gauges.usgs_world import BOISE, DALLES, MERIDIAN, Gauge, Reading, UsgsWaterDataWorld
from tests.parquet.availability_documents import MemoryAvailabilityStorage
from tests.runner.fakes import NOW, ManualClock, MemoryLaneStore, days_between, ports_for, run, spec_for

if TYPE_CHECKING:
    from agri_data_service.foundation.lane_config import LaneConfig
    from agri_data_service.pipeline.runner.contract import TurnMode

LANE_ID: Final = "water-gauges-daily"
STREAM: Final = "water-gauges-daily"
#: Lag 2 and a 14-day recheck window from the shipped TOML, against the runner fakes' TODAY (2026-09-20).
WINDOW: Final = days_between(date(2026, 9, 5), date(2026, 9, 18))
PROBED_DAY: Final = date(2026, 9, 10)
TILES: Final = tuple(tile.bbox for tile in tile_boxes(load_region("pnw").default_camera_envelope))
SUPPORT_IDS: Final = frozenset(TILES) | frozenset(f"stream-subtypes:{tile}" for tile in TILES)
#: The Dalles' tile, and Boise's.
WESTERN_TILE: Final = "-125,42,-121,46"
BOISE_TILE: Final = "-117,42,-113,46"
#: The western tile that also serves the gauge on the -121 meridian.
SECOND_WESTERN_TILE: Final = "-121,42,-117,46"
SENTINEL_GAUGE: Final = Gauge("USGS-13200000", "SENTINEL GAUGE", -118.5, 44.5, "PST")
SILENT_GAUGE: Final = Gauge("USGS-12340500", "CLARK FORK ABOVE MISSOULA MT", -113.9, 46.87, "MST")
#: A second gauge in the Dalles' tile, a site whose record names daylight time, and one with no site record.
WILLAMETTE: Final = Gauge("USGS-14211720", "WILLAMETTE RIVER AT PORTLAND, OR", -122.6687, 45.5176, "PST", "ST-TS")
DAYLIGHT_GAUGE: Final = Gauge("USGS-14144700", "COLUMBIA RIVER AT VANCOUVER, WA", -122.6664, 45.6242, "PDT")
UNNAMED_GAUGE: Final = Gauge("USGS-14128870", "COLUMBIA RIVER BELOW BONNEVILLE DAM", -121.9406, 45.6285, "PST")
#: A 366-day gap-fill turn: 12 runs of at most 31 days x 8 tiles, plus one names unit per tile.
GAP_FILL_TURN_SENDS: Final = 12 * 8 + 8


def _shipped_lane() -> LaneConfig:
    return load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw")).lanes[LANE_ID]


def _enabled(*, gap_fill: bool = False) -> LaneConfig:
    """The shipped lane as G3 will flip it: enabled, and its gap-fill enabled at that gate when asked."""
    lane = _shipped_lane()
    schedule = lane.schedule.model_copy(
        update={"gap_fill_enabled": gap_fill, "gap_fill_enabled_at_gate": "G3" if gap_fill else None}
    )
    return lane.model_copy(update={"enabled": True, "schedule": schedule})


def _daily_flow(gauge: Gauge, day: date) -> Reading | None:
    """Every gauge reports every day; the Dalles and Boise values encode the day so a moved row shows."""
    if gauge == DALLES:
        return Reading(str(day.day * 1000))
    if gauge == BOISE:
        return Reading(str(day.day * 10))
    if gauge == SENTINEL_GAUGE:
        return Reading("-999999")
    if gauge == SILENT_GAUGE:
        return Reading(None, qualifier=["EQUIP"])
    return Reading("55.5")


async def _turn(
    lane: LaneConfig,
    world: UsgsWaterDataWorld,
    store: MemoryLaneStore,
    *,
    mode: TurnMode | Literal["compare"] = "forward",
    now: datetime = NOW,
) -> tuple[int, dict[str, object]]:
    """One turn with the real `ConfigProviderClient` sending to the fake API through the real provider file."""
    compare = mode == "compare"
    spec = spec_for(lane, mode="forward" if mode == "compare" else mode, compare=compare)
    clock = ManualClock(now)
    async with httpx.AsyncClient(transport=httpx.MockTransport(world.handler)) as http:
        ports = ports_for(resolve_strategy(lane), store, clock=clock, compare=compare)
        assert spec.provider is not None
        ports.client = ConfigProviderClient(provider=spec.provider, http=http, clock=clock)
        return await run(spec, ports)


def _rows(store: MemoryLaneStore, day: date) -> list[dict[str, object]]:
    return store.tables[(STREAM, day)].to_pylist()


def _gauges_on(store: MemoryLaneStore, day: date) -> set[object]:
    return {row["monitoring_location_id"] for row in _rows(store, day)}


def _unwritten(payload: dict[str, object]) -> list[dict[str, object]]:
    entries = payload["unwritten"]
    assert isinstance(entries, list)
    return entries


def _unwritten_reasons(payload: dict[str, object]) -> dict[str, str]:
    return {str(entry["day"]): str(entry["reason"]) for entry in _unwritten(payload)}


# --- forward --------------------------------------------------------------------------------------


async def test_a_forward_turn_writes_every_named_day_from_all_eight_tiles() -> None:
    """8 daily-values units and 8 monitoring-locations units; an edge gauge once; the sentinel and a null dropped."""
    world = UsgsWaterDataWorld(gauges=[DALLES, BOISE, MERIDIAN, SENTINEL_GAUGE, SILENT_GAUGE], reading_for=_daily_flow)
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_enabled(), world, store)

    assert exit_code == 0, payload
    daily = world.daily_requests()
    assert sorted(request.url.params["bbox"] for request in daily) == sorted(TILES)
    assert {request.url.params["time"] for request in daily} == {"2026-09-05/2026-09-18"}
    assert len(world.requests) == 2 * len(TILES)
    assert sorted(day for (stream, day) in store.tables if stream == STREAM) == WINDOW
    rows = {row["monitoring_location_id"]: row for row in _rows(store, PROBED_DAY)}
    assert sorted(rows) == sorted(gauge.monitoring_location_id for gauge in (BOISE, DALLES, MERIDIAN))
    dalles, boise = rows[DALLES.monitoring_location_id], rows[BOISE.monitoring_location_id]
    assert (dalles["site_number"], dalles["site_name"], dalles["flow_cfs"]) == ("14105700", DALLES.name, 10_000.0)
    assert (boise["flow_cfs"], boise["approval_status"], boise["source_time"]) == (100.0, "Provisional", "2026-09-10")
    # The named day stamped at each site's STANDARD midnight: PST -08:00, MST -07:00.
    assert dalles["observed_at"] == datetime(2026, 9, 10, 8, tzinfo=UTC)
    assert boise["observed_at"] == datetime(2026, 9, 10, 7, tzinfo=UTC)
    assert {row["observed_day"] for row in rows.values()} == {PROBED_DAY}
    receipt = store.receipts[(STREAM, PROBED_DAY)]
    assert (receipt.present_units, receipt.expected_units) == (len(SUPPORT_IDS), len(SUPPORT_IDS))


@pytest.mark.parametrize("site_type", ["ST-TS", "ST-CA", "ST-DCH"])
async def test_stream_subtypes_keep_their_values_and_names_while_nonstreams_are_excluded(site_type: str) -> None:
    subtype = replace(WILLAMETTE, site_type=site_type)
    nonstream = replace(BOISE, site_type="LK")
    world = UsgsWaterDataWorld(gauges=[DALLES, subtype, nonstream], reading_for=_daily_flow)
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_enabled(), world, store)

    assert exit_code == 0, payload
    assert _gauges_on(store, PROBED_DAY) == {DALLES.monitoring_location_id, subtype.monitoring_location_id}
    [row] = [row for row in _rows(store, PROBED_DAY) if row["monitoring_location_id"] == subtype.monitoring_location_id]
    assert (row["site_name"], row["flow_cfs"]) == (subtype.name, 55.5)
    assert all("site_type_code" not in request.url.params for request in world.requests)
    assert all(
        request.url.params["filter"] == "site_type_code = 'ST' OR site_type_code LIKE 'ST-%'"
        for request in world.requests
    )


def test_stream_family_queries_cannot_restore_an_exact_stream_checkpoint() -> None:
    lane = _enabled()
    spec = spec_for(lane)
    assert spec.provider is not None
    checkpoints = TurnCheckpoints(None, LANE_ID, spec.provider)
    for request in resolve_strategy(lane).plan_requests([PROBED_DAY], lane, load_region("pnw")):
        exact = replace(
            request,
            parameters=(
                *((key, value) for key, value in request.parameters if key != "filter"),
                ("site_type_code", "ST"),
            ),
        )
        assert checkpoints.identity(request) != checkpoints.identity(exact)


@pytest.mark.parametrize("publication_state", ["complete", "pending"])
@pytest.mark.parametrize("add_subtype", [False, True])
async def test_exact_stream_proofs_require_a_monotonic_full_family_repair(
    publication_state: Literal["complete", "pending"], add_subtype: bool
) -> None:
    world = UsgsWaterDataWorld(gauges=[DALLES, BOISE], reading_for=_daily_flow)
    store = MemoryLaneStore()
    first_exit, first = await _turn(_enabled(), world, store)
    assert first_exit == 0, first
    store.receipts = {
        key: replace(
            receipt,
            expected_units=len(TILES),
            present_units=len(TILES),
            expected_unit_ids=frozenset(TILES),
            present_unit_ids=frozenset(TILES),
            publication_state=publication_state,
        )
        for key, receipt in store.receipts.items()
    }
    original = store.receipts[(STREAM, PROBED_DAY)]
    storage = MemoryAvailabilityStorage()
    SourceCompleteness(storage).confirm(replace(original, publication_state="complete"))
    assert SourceCompleteness(storage).complete_days(STREAM, PROBED_DAY, PROBED_DAY, frozenset(TILES)) == frozenset(
        {PROBED_DAY}
    )
    assert SourceCompleteness(storage).complete_days(STREAM, PROBED_DAY, PROBED_DAY, SUPPORT_IDS) == frozenset()
    if add_subtype:
        world.gauges.append(WILLAMETTE)
    world.failing_tiles = {BOISE_TILE}

    partial_exit, partial = await _turn(_enabled(), world, store)

    assert partial_exit == 0, partial
    refusal = "coverage_unproven" if publication_state == "pending" else "coverage_lost"
    assert partial["rewrite_reasons"] == {refusal: len(WINDOW)}
    assert store.receipts[(STREAM, PROBED_DAY)] == original
    assert _gauges_on(store, PROBED_DAY) == {DALLES.monitoring_location_id, BOISE.monitoring_location_id}
    world.failing_tiles.clear()

    full_exit, full = await _turn(_enabled(), world, store)

    assert full_exit == 0, full
    assert full["rewrite_reasons"] == {"source_completed": len(WINDOW)}
    repaired = store.receipts[(STREAM, PROBED_DAY)]
    assert repaired.expected_unit_ids == repaired.present_unit_ids == SUPPORT_IDS
    assert repaired.publication_state == "complete"
    assert (repaired.source_digest != original.source_digest) is add_subtype
    SourceCompleteness(storage).confirm(repaired)
    assert SourceCompleteness(storage).complete_days(STREAM, PROBED_DAY, PROBED_DAY, SUPPORT_IDS) == frozenset(
        {PROBED_DAY}
    )


async def test_the_named_day_is_the_served_time_prefix_never_a_utc_conversion() -> None:
    """A late-evening value with an offset stays on its named day; converting to UTC would move it to the next."""
    world = UsgsWaterDataWorld(gauges=[DALLES], reading_for=_daily_flow)
    world.readings[(DALLES.monitoring_location_id, PROBED_DAY)] = Reading("4321", time="2026-09-10T23:30:00-07:00")
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_enabled(), world, store)

    assert exit_code == 0, payload
    [named] = _rows(store, PROBED_DAY)
    [next_day] = _rows(store, PROBED_DAY + timedelta(days=1))
    next_day_reading = _daily_flow(DALLES, PROBED_DAY + timedelta(days=1))
    assert next_day_reading is not None
    assert next_day_reading.value is not None
    assert (named["flow_cfs"], named["source_time"]) == (4321.0, "2026-09-10T23:30:00-07:00")
    assert next_day["flow_cfs"] == float(next_day_reading.value)


@pytest.mark.parametrize(
    "served",
    ["2026-09-10", "2026-09-10T23:30:00-07:00", "2026-09-10T00:30:00+05:00", "1990-09-30T23:59:59Z"],
)
def test_the_named_day_rule_equals_the_legacy_validator(served: str) -> None:
    """Spec §7a: the strategy's rule is `pipeline/validation/water_gauges.py::publisher_named_day` verbatim."""
    assert publisher_named_day(served) == legacy_validation.publisher_named_day(served)
    assert publisher_named_day(served) == date.fromisoformat(served[:10])


async def test_a_tile_that_stays_down_is_unwritten_for_its_gauges_and_never_erases_a_written_day() -> None:
    """Spec §7a per-tile independence: seven whole tiles write a short day, the eighth's return adds its gauges,
    and a later outage of another tile leaves the full day as written (S11 `fewer_units`)."""
    world = UsgsWaterDataWorld(gauges=[DALLES, BOISE], reading_for=_daily_flow, failing_tiles={BOISE_TILE})
    store = MemoryLaneStore()

    first_exit, first = await _turn(_enabled(), world, store)

    assert first_exit == 0, first
    assert _gauges_on(store, PROBED_DAY) == {DALLES.monitoring_location_id}
    receipt = store.receipts[(STREAM, PROBED_DAY)]
    assert (receipt.present_units, receipt.expected_units) == (len(SUPPORT_IDS) - 2, len(SUPPORT_IDS))
    assert set(_unwritten_reasons(first).items()) == {(day.isoformat(), "upstream_unavailable") for day in WINDOW}
    assert all(BOISE_TILE in str(entry["detail"]) for entry in _unwritten(first))

    world.failing_tiles.clear()
    second_exit, second = await _turn(_enabled(), world, store)

    assert second_exit == 0, second
    assert second["rewrite_reasons"] == {"more_units": len(WINDOW)}
    assert _gauges_on(store, PROBED_DAY) == {DALLES.monitoring_location_id, BOISE.monitoring_location_id}
    assert _unwritten(second) == []

    world.failing_tiles.add(WESTERN_TILE)
    third_exit, third = await _turn(_enabled(), world, store)

    assert third_exit == 0, third
    assert third["rewrite_reasons"] == {"fewer_units": len(WINDOW)}
    assert _gauges_on(store, PROBED_DAY) == {DALLES.monitoring_location_id, BOISE.monitoring_location_id}
    assert all(WESTERN_TILE in str(entry["detail"]) for entry in _unwritten(third))


async def test_an_approval_change_rewrites_its_day_and_a_database_refresh_does_not() -> None:
    """S11: Provisional -> Approved at the same value is a digest change; fresh feature UUIDs are not."""
    world = UsgsWaterDataWorld(gauges=[DALLES, BOISE], reading_for=_daily_flow)
    store = MemoryLaneStore()
    await _turn(_enabled(), world, store)
    written_before = len(store.writes())
    digests_before = {day: store.receipts[(STREAM, day)].source_digest for day in WINDOW}

    world.readings[(DALLES.monitoring_location_id, PROBED_DAY)] = Reading("10000", approval_status="Approved")
    exit_code, payload = await _turn(_enabled(), world, store)

    assert exit_code == 0, payload
    assert store.writes()[written_before:] == [f"write:{STREAM}:{PROBED_DAY.isoformat()}:availability=True"]
    approvals = {row["monitoring_location_id"]: row["approval_status"] for row in _rows(store, PROBED_DAY)}
    assert approvals == {DALLES.monitoring_location_id: "Approved", BOISE.monitoring_location_id: "Provisional"}
    changed = {day for day in WINDOW if store.receipts[(STREAM, day)].source_digest != digests_before[day]}
    assert changed == {PROBED_DAY}


async def test_an_unchanged_complete_forward_day_repairs_its_missing_coarse_rung() -> None:
    """A digest-equal recheck still schedules ladder repair inside the current forward window."""
    world = UsgsWaterDataWorld(gauges=[DALLES, BOISE], reading_for=_daily_flow)
    store = MemoryLaneStore()
    first_exit, first = await _turn(_enabled(), world, store)
    assert first_exit == 0, first
    store.ladder_incomplete.add((STREAM, PROBED_DAY))
    receipt = store.receipts[(STREAM, PROBED_DAY)]
    writes = len(store.writes())

    exit_code, payload = await _turn(_enabled(), world, store)

    assert exit_code == 0, payload
    assert payload["days_source_owed"] == 0
    assert payload["days_ladder_owed"] == 1
    assert payload["ladder_repairs"] == 1
    assert len(store.writes()) == writes
    assert store.receipts[(STREAM, PROBED_DAY)] == receipt
    assert store.status(STREAM, PROBED_DAY) == "data"
    assert f"repair:{STREAM}:{PROBED_DAY.isoformat()}" in store.journal
    assert _unwritten(payload) == []


async def test_a_late_approval_behind_the_forward_window_is_rewritten_by_the_rolling_revision() -> None:
    """S11 for USGS approvals: a day written Provisional months ago is re-asked by a forward turn's revision
    block and rewritten Approved within one rotation (every block once per `revision_rotation_days` turns)."""
    lane = _enabled()
    days = lane.days
    assert days is not None
    rotation = revision_rotation_days(days)
    assert rotation is not None
    world = UsgsWaterDataWorld(gauges=[DALLES], reading_for=_daily_flow)
    store = MemoryLaneStore()
    late = date(2026, 6, 1)
    await _turn(lane, world, store, now=datetime(2026, 6, 5, 12, tzinfo=UTC))
    [written] = _rows(store, late)
    assert written["approval_status"] == "Provisional"
    world.readings[(DALLES.monitoring_location_id, late)] = Reading(str(late.day * 1000), approval_status="Approved")

    for elapsed in range(rotation):
        exit_code, payload = await _turn(lane, world, store, now=NOW + timedelta(days=elapsed))
        assert exit_code == 0, payload
        assert payload["requests"] <= lane.budget.forward_max_weighted_calls
        [row] = _rows(store, late)
        if row["approval_status"] == "Approved":
            break
    else:
        pytest.fail(f"{late} was not re-asked in {rotation} forward turns")

    revision = (date.fromisoformat(str(payload["revision_first"])), date.fromisoformat(str(payload["revision_last"])))
    assert revision[0] <= late <= revision[1]
    assert payload["rewrite_reasons"]["digest_changed"] == 1  # type: ignore[index]
    assert _unwritten(payload) == []


async def test_two_values_under_one_identity_are_dropped_counted_and_the_rest_of_the_day_is_written() -> None:
    """A revision between two tiles' sends: the edge gauge cannot be attributed, so it is not written, and the
    report counts it (review M1)."""
    world = UsgsWaterDataWorld(gauges=[DALLES, MERIDIAN], reading_for=_daily_flow)
    world.tile_overrides[(SECOND_WESTERN_TILE, MERIDIAN.monitoring_location_id)] = "999"
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_enabled(), world, store)

    assert exit_code == 0, payload
    assert [row["monitoring_location_id"] for row in _rows(store, PROBED_DAY)] == [DALLES.monitoring_location_id]
    assert payload["rows_dropped_by_reason"] == {"identity_conflict": len(WINDOW)}


async def test_one_unreadable_feature_is_dropped_and_counted_while_its_tile_still_writes() -> None:
    """Review H1: a feature in another unit is refused alone; the tile's other gauges and days are written whole."""
    world = UsgsWaterDataWorld(gauges=[DALLES, WILLAMETTE, BOISE], reading_for=_daily_flow)
    world.readings[(DALLES.monitoring_location_id, PROBED_DAY)] = Reading("283", unit_of_measure="m^3/s")
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_enabled(), world, store)

    assert exit_code == 0, payload
    assert _gauges_on(store, PROBED_DAY) == {WILLAMETTE.monitoring_location_id, BOISE.monitoring_location_id}
    assert _gauges_on(store, PROBED_DAY + timedelta(days=1)) == {
        gauge.monitoring_location_id for gauge in (DALLES, WILLAMETTE, BOISE)
    }
    receipt = store.receipts[(STREAM, PROBED_DAY)]
    assert (receipt.present_units, receipt.expected_units) == (len(SUPPORT_IDS), len(SUPPORT_IDS))
    assert payload["rows_dropped_by_reason"] == {"rejected_feature": 1}


async def test_a_site_with_no_known_zone_is_stamped_at_the_regional_standard_midnight() -> None:
    """Review M2: a daylight-time abbreviation names its zone's standard offset, and a site missing from the names
    answer takes the offset most sites name, never UTC midnight (the previous local evening)."""
    world = UsgsWaterDataWorld(
        gauges=[DALLES, BOISE, WILLAMETTE, DAYLIGHT_GAUGE, UNNAMED_GAUGE], reading_for=_daily_flow
    )
    world.unnamed.add(UNNAMED_GAUGE.monitoring_location_id)
    store = MemoryLaneStore()

    exit_code, payload = await _turn(_enabled(), world, store)

    assert exit_code == 0, payload
    rows = {row["monitoring_location_id"]: row for row in _rows(store, PROBED_DAY)}
    pacific_midnight = datetime(2026, 9, 10, 8, tzinfo=UTC)
    assert rows[DAYLIGHT_GAUGE.monitoring_location_id]["observed_at"] == pacific_midnight
    unnamed = rows[UNNAMED_GAUGE.monitoring_location_id]
    assert (unnamed["observed_at"], unnamed["site_name"]) == (pacific_midnight, None)
    assert rows[BOISE.monitoring_location_id]["observed_at"] == datetime(2026, 9, 10, 7, tzinfo=UTC)


# --- gap-fill: the DV re-pull to the floor --------------------------------------------------------


async def test_gap_fill_walks_up_from_the_floor_in_31_day_tile_units_inside_the_turn_cap() -> None:
    """The oldest 366 owed days from 1990-09-30, 12 chunks x 8 tiles + 8 names units = 104 sends <= the cap."""
    lane = _enabled(gap_fill=True)
    world = UsgsWaterDataWorld(gauges=[DALLES, BOISE], reading_for=_daily_flow)
    store = MemoryLaneStore()

    exit_code, payload = await _turn(lane, world, store, mode="gap-fill")

    assert exit_code == 0, payload
    floor = _MEASURED_COMPLETE_HISTORY_FLOORS["water-gauges"]
    assert len(world.requests) == GAP_FILL_TURN_SENDS <= lane.budget.gap_fill_max_weighted_calls
    intervals = [
        tuple(map(date.fromisoformat, request.url.params["time"].split("/"))) for request in world.daily_requests()
    ]
    assert min(first for first, _ in intervals) == floor
    assert max((last - first).days + 1 for first, last in intervals) == MAX_DAYS_PER_REQUEST
    written = sorted(day for (stream, day) in store.tables if stream == STREAM)
    assert (written[0], written[-1], len(written)) == (floor, floor + timedelta(days=365), 366)


async def test_gap_fill_recovers_a_historical_partial_day_after_a_process_restart() -> None:
    """A 1990 tile outage stays source-owed even when every zoom rung was published."""
    lane = _enabled(gap_fill=True)
    world = UsgsWaterDataWorld(gauges=[DALLES, BOISE], reading_for=_daily_flow, failing_tiles={BOISE_TILE})
    store = MemoryLaneStore()
    first_exit, first = await _turn(lane, world, store, mode="gap-fill")
    assert first_exit == 0, first
    floor = _MEASURED_COMPLETE_HISTORY_FLOORS["water-gauges"]
    assert _gauges_on(store, floor) == {DALLES.monitoring_location_id}
    restarted = MemoryLaneStore(
        statuses=dict(store.statuses),
        tables=dict(store.tables),
        receipts={key: DayReceipt.from_payload(receipt.to_payload()) for key, receipt in store.receipts.items()},
    )
    restarted.ladder_incomplete.add((STREAM, floor))
    before = restarted.census([STREAM], floor, floor, expected_unit_ids=SUPPORT_IDS)
    assert before.owed_days() == (floor,)
    assert before.source_owed_days() == frozenset({floor})
    world.failing_tiles.clear()
    world.requests.clear()

    second_exit, second = await _turn(lane, world, restarted, mode="gap-fill")

    assert second_exit == 0, second
    assert second["days_source_owed"] == len(store.tables)
    assert second["days_ladder_owed"] == 0
    assert _gauges_on(restarted, floor) == {DALLES.monitoring_location_id, BOISE.monitoring_location_id}
    assert any(request.url.params["time"].startswith(floor.isoformat()) for request in world.daily_requests())
    after = restarted.census([STREAM], floor, floor, expected_unit_ids=SUPPORT_IDS)
    assert after.owed_days() == ()
    assert after.source_owed_days() == frozenset()


async def test_a_shifted_seven_tile_answer_cannot_erase_a_previous_six_tile_answer() -> None:
    """A larger count proves no superset when a different tile fails on the next turn."""
    world = UsgsWaterDataWorld(
        gauges=[DALLES, BOISE],
        reading_for=_daily_flow,
        failing_tiles={BOISE_TILE, SECOND_WESTERN_TILE},
    )
    store = MemoryLaneStore()
    first_exit, first = await _turn(_enabled(), world, store)
    assert first_exit == 0, first
    original = store.receipts[(STREAM, PROBED_DAY)]
    assert original.present_units == len(SUPPORT_IDS) - 2 * len(world.failing_tiles)
    world.failing_tiles = {WESTERN_TILE}

    second_exit, second = await _turn(_enabled(), world, store)

    assert second_exit == 0, second
    assert second["rewrite_reasons"] == {"coverage_lost": len(WINDOW)}
    assert store.receipts[(STREAM, PROBED_DAY)] == original
    assert _gauges_on(store, PROBED_DAY) == {DALLES.monitoring_location_id}
    assert store.census([STREAM], PROBED_DAY, PROBED_DAY, expected_unit_ids=SUPPORT_IDS).owed_days() == (PROBED_DAY,)
    world.failing_tiles.clear()
    third_exit, third = await _turn(_enabled(), world, store)
    assert third_exit == 0, third
    assert _gauges_on(store, PROBED_DAY) == {DALLES.monitoring_location_id, BOISE.monitoring_location_id}


async def test_compare_reports_existing_source_debt_without_publishing_a_recovery() -> None:
    world = UsgsWaterDataWorld(gauges=[DALLES, BOISE], reading_for=_daily_flow, failing_tiles={BOISE_TILE})
    store = MemoryLaneStore()
    first_exit, first = await _turn(_enabled(), world, store)
    assert first_exit == 0, first
    receipts = dict(store.receipts)
    journal = list(store.journal)
    world.failing_tiles.clear()

    exit_code, payload = await _turn(_enabled(), world, store, mode="compare")

    assert exit_code == 0, payload
    assert payload["days_source_owed"] == len(WINDOW)
    assert store.receipts == receipts
    assert store.journal == journal
    assert _gauges_on(store, PROBED_DAY) == {DALLES.monitoring_location_id}


# --- G3 admission and the retained disabled-lane brake ----------------------------------------------


def test_the_prepared_g3_lane_admits_both_definitions_and_keeps_legacy_water() -> None:
    """The reviewed G3 diff enables ingestion beside the unchanged legacy serving lane."""
    configs = load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw"))
    catalogue = build_lane_catalogue(legacy_specs=LANE_SPECS, configs=configs, environment={})
    forward, gap_fill = catalogue.spec_named(LANE_ID), catalogue.spec_named(f"{LANE_ID}:gap-fill")
    assert forward is not None
    assert gap_fill is not None
    assert catalogue.config_gate(forward) is None
    assert catalogue.config_gate(gap_fill) is None
    assert configs.lanes[LANE_ID].schedule.gap_fill_enabled_at_gate == "G3"
    assert "water-gauges-direct-forward" in catalogue.legacy_specs


async def test_a_disabled_lane_still_refuses_both_definitions_and_any_writing_turn() -> None:
    configs = load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw"))
    disabled = _shipped_lane().model_copy(update={"enabled": False})
    configs = replace(configs, lanes={**configs.lanes, LANE_ID: disabled})
    catalogue = build_lane_catalogue(legacy_specs=LANE_SPECS, configs=configs, environment={})
    forward, gap_fill = catalogue.spec_named(LANE_ID), catalogue.spec_named(f"{LANE_ID}:gap-fill")
    assert forward is not None
    assert gap_fill is not None
    assert catalogue.config_gate(forward) == catalogue.config_gate(gap_fill) == LANE_DISABLED_REASON
    world = UsgsWaterDataWorld(gauges=[DALLES], reading_for=_daily_flow)
    with pytest.raises(TurnConfigurationError, match="enabled = false"):
        await _turn(disabled, world, MemoryLaneStore())

    assert world.requests == []


def test_the_stream_is_registered_at_the_legacy_complete_history_floor_and_moves_the_calendar() -> None:
    """N5 + A19: TOML floor, registration floors and the calendar floor are all the legacy lane's 1990-09-30."""
    floor = _MEASURED_COMPLETE_HISTORY_FLOORS["water-gauges"]
    registration = LANE_REGISTRY[STREAM]
    days = _shipped_lane().days

    assert days is not None
    assert days.floor == floor
    assert (registration.history_floor, registration.claimed_history_floor) == (floor, floor)
    assert floor == CALENDAR_HISTORY_FLOOR


# --- the page contract ------------------------------------------------------------------------------

#: One feature exactly as P4 received it (tile -125,42,-121,46, 2026-09-16), less `last_modified`.
_P4_FEATURE: Final[dict[str, object]] = {
    "type": "Feature",
    "properties": {
        "time_series_id": "f17f72684e3b4fe6b6ff61b9ab1d6b1b",
        "monitoring_location_id": "USGS-14197900",
        "parameter_code": "00060",
        "statistic_id": "00003",
        "time": "2026-09-16",
        "value": "7530",
        "unit_of_measure": "ft^3/s",
        "approval_status": "Provisional",
        "qualifier": None,
    },
    "id": "0011a0e4-bca7-4595-880a-7536ffbdcb13",
    "geometry": {"type": "Point", "coordinates": [-122.961489251109, 45.2845625124917]},
}


def _page(*, links: list[dict[str, str]] | None = None, **properties: object) -> bytes:
    feature = {**_P4_FEATURE, "properties": {**_P4_FEATURE["properties"], **properties}}  # type: ignore[dict-item]
    return json.dumps({"type": "FeatureCollection", "features": [feature], "links": links or []}).encode()


def test_the_p4_feature_shape_parses_to_its_identity_day_and_approval() -> None:
    """The recorded shape is the contract: identity, named day, flow and approval read verbatim."""
    page = parse_daily_values(_page())
    [value] = page.values

    assert page.rejected == ()
    assert (value.identity, value.named_day) == ("USGS-14197900:2026-09-16:00003", date(2026, 9, 16))
    assert (value.flow_cfs, value.approval_status, value.site_number) == (7530.0, "Provisional", "14197900")


@pytest.mark.parametrize(
    ("body", "refusal"),
    [
        pytest.param(
            _page(links=[{"rel": "next", "href": "https://api.waterdata.usgs.gov/next"}]), "next page", id="next-link"
        ),
        pytest.param(json.dumps({"type": "Feature"}).encode(), "not a FeatureCollection", id="not-a-collection"),
    ],
)
def test_a_page_level_break_refuses_the_whole_tile_answer(body: bytes, refusal: str) -> None:
    """A second page or a body that is no FeatureCollection cannot be read in part: the unit's `strategy_error`."""
    with pytest.raises(UsgsWaterDataPayloadError, match=refusal):
        parse_daily_values(body)


@pytest.mark.parametrize(
    ("page", "refusal", "named_day"),
    [
        pytest.param(_page(unit_of_measure="m^3/s"), "is in 'm", date(2026, 9, 16), id="another-unit"),
        pytest.param(_page(statistic_id="00001"), "not the daily mean", date(2026, 9, 16), id="another-statistic"),
        pytest.param(_page(value="about 12"), "not a number", date(2026, 9, 16), id="not-a-number"),
        pytest.param(_page(approval_status=None), "no approval_status", date(2026, 9, 16), id="valued-unapproved"),
        pytest.param(_page(qualifier=["EQUIP", None]), "qualifier", date(2026, 9, 16), id="null-in-qualifier"),
        pytest.param(_page(time="late"), "names no ISO day", None, id="no-named-day"),
    ],
)
def test_a_feature_level_break_drops_that_feature_alone_with_its_day(
    page: bytes, refusal: str, named_day: date | None
) -> None:
    """Another unit or statistic, a non-number, a valued row without approval: never written, counted on its day."""
    parsed = parse_daily_values(page)
    [rejected] = parsed.rejected

    assert parsed.values == ()
    assert refusal in rejected.reason
    assert rejected.named_day == named_day
