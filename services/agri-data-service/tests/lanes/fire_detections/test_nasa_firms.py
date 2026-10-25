"""`fire-detections-direct-forward` through real runner turns: the shipped TOML, the real S14 strategy.

Mirrors `tests/lanes/watersheds/test_nhdplus.py`'s shape: load the REAL TOML through the REAL loader,
flip `executor` to `"config"` so a non-compare turn may run, and fake only the upstream
(`tests.runner.fakes.ScriptedUpstream`) and the bucket (`MemoryLaneStore`).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING

import pytest

from agri_data_service.foundation.lane_config import load_lane_configs
from agri_data_service.foundation.region import load_region
from agri_data_service.ingest.firms import FIRMS_VIIRS_SOURCES
from agri_data_service.ingest.policy import PACIFIC_NORTHWEST_COVERAGE_BBOX
from agri_data_service.pipeline.lanes.fire_detections.nasa_firms import (
    AVAILABILITY_ENDPOINT,
    DAY_UNIT_SOURCE,
    FIRMS_RESPONSE_RECORD_CAP,
    FORWARD_ENDPOINT,
    STRATEGY,
)
from agri_data_service.pipeline.runner.contract import SourceUnavailableError
from agri_data_service.pipeline.runner.exits import EXIT_UPSTREAM_UNAVAILABLE
from agri_data_service.pipeline.runner.resolve import resolve_strategy
from tests.lane_config.builders import REAL_LANES_DIRECTORY
from tests.runner.fakes import (
    NOW,
    TODAY,
    ManualClock,
    MemoryLaneStore,
    ScriptedUpstream,
    ports_for,
    run,
    spec_for,
    unwritten_by_day,
)

if TYPE_CHECKING:
    from agri_data_service.foundation.lane_config.models import LaneConfig
    from agri_data_service.pipeline.runner.contract import TurnMode

LANE_ID = "fire-detections-direct-forward"
STREAM = "fire-detections"
BBOX = PACIFIC_NORTHWEST_COVERAGE_BBOX
HEADER = "latitude,longitude,bright_ti4,confidence,frp,satellite,acq_date,acq_time"
#: publication_lag_days=2 off TODAY (2026-09-20); absence_recheck_days=5 -> [EDGE-4, EDGE].
EDGE = TODAY - timedelta(days=2)
WINDOW_FIRST = EDGE - timedelta(days=4)


def _row(
    *,
    lat: float = 45.1,
    lon: float = -120.2,
    satellite: str = "N20",
    acq_date: str = "2026-09-18",
    acq_time: str = "0512",
) -> str:
    return f"{lat},{lon},330.5,n,12.3,{satellite},{acq_date},{acq_time}"


def _csv(rows: list[str]) -> bytes:
    return "\n".join([HEADER, *rows]).encode("utf-8")


#: Today's live NRT trio, each covering the whole forward window (the default availability answer).
NRT_COVERAGE: dict[str, tuple[date, date]] = {product: (date(2026, 1, 1), TODAY) for product in FIRMS_VIIRS_SOURCES}


class FirmsWorld:
    """FIRMS as the lane sees it: a `data_availability` table, and what each (day, product) answers.

    Products default to a header-only (zero-row) CSV; `availability` defaults to the live NRT trio.
    Asking a product for a day outside its availability fails the test: the strategy must never do it.
    """

    def __init__(self, availability: dict[str, tuple[date, date]] | None = None) -> None:
        self.availability = dict(NRT_COVERAGE if availability is None else availability)
        self.rows: dict[tuple[str, str], list[str]] = {}
        self.asked: list[tuple[str, str]] = []
        self.availability_reads = 0
        self.availability_down = False

    def publish(self, day: date, product: str, rows: list[str]) -> None:
        self.rows[(day.isoformat(), product)] = rows

    def answer(self, endpoint: str, parameters: dict[str, str], probe: bool) -> bytes:  # noqa: ARG002 - the ScriptedUpstream shape
        if endpoint == AVAILABILITY_ENDPOINT:
            assert parameters == {}
            self.availability_reads += 1
            if self.availability_down:
                raise SourceUnavailableError("data_availability 503")
            table = [f"{product},{first},{last}" for product, (first, last) in self.availability.items()]
            return "\n".join(["data_id,min_date,max_date", *table]).encode("utf-8")
        assert endpoint == FORWARD_ENDPOINT
        key = (parameters["start_date"], parameters["source"])
        first, last = self.availability.get(key[1], (date.max, date.min))
        assert first <= date.fromisoformat(key[0]) <= last, f"asked {key} outside its availability"
        self.asked.append(key)
        return _csv(self.rows.get(key, []))

    def asked_products(self, day: date) -> set[str]:
        return {product for asked_day, product in self.asked if asked_day == day.isoformat()}


def _shipped_lane() -> LaneConfig:
    return load_lane_configs(REAL_LANES_DIRECTORY, load_region("pnw")).lanes[LANE_ID]


def _config_lane() -> LaneConfig:
    """The shipped lane as a future cut-over will flip it: `executor = "config"`."""
    return _shipped_lane().model_copy(update={"executor": "config"})


async def _turn(
    lane: LaneConfig,
    store: MemoryLaneStore,
    world: FirmsWorld,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: TurnMode = "forward",
) -> tuple[int, dict[str, object]]:
    monkeypatch.setenv("INGEST_BBOX", BBOX)
    spec = spec_for(lane, mode=mode)
    ports = ports_for(STRATEGY, store, upstream=ScriptedUpstream(answer=world.answer), clock=ManualClock(NOW))
    return await run(spec, ports)


def _rows(store: MemoryLaneStore, day: date) -> list[dict[str, object]]:
    return store.tables[(STREAM, day)].to_pylist()


# --- shipped config shape --------------------------------------------------------------------


def test_the_shipped_lane_is_a_refuse_daily_series_with_a_five_day_recheck_window() -> None:
    lane = _shipped_lane()

    assert (lane.nature, lane.kind, lane.executor) == ("daily_series", "ingest", "legacy")
    assert lane.days is not None
    assert (lane.days.partial_day, lane.days.publication_lag_days, lane.days.absence_recheck_days) == (
        "refuse",
        2,
        5,
    )
    assert lane.source is not None
    assert (lane.source.provider, lane.source.endpoint, lane.source.lifecycle) == (
        "nasa-firms",
        "area-csv-dated",
        "active",
    )
    assert lane.schedule.gap_fill_enabled is False
    assert [stream.slug for stream in lane.streams] == [STREAM]
    assert lane.streams[0].volatile_columns == ()


def test_the_shipped_strategy_key_resolves_to_the_nasa_firms_strategy() -> None:
    assert resolve_strategy(_shipped_lane()) is STRATEGY


# --- plan_requests -----------------------------------------------------------------------------


def test_plan_requests_plans_one_unit_per_day_whose_products_fetch_reads_from_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INGEST_BBOX", BBOX)
    lane, region = _shipped_lane(), load_region("pnw")
    days = [date(2026, 9, 17), date(2026, 9, 18)]

    requests = STRATEGY.plan_requests(days, lane, region)

    assert [(request.unit, request.endpoint, request.days) for request in requests] == [
        ("2026-09-17", FORWARD_ENDPOINT, (days[0],)),
        ("2026-09-18", FORWARD_ENDPOINT, (days[1],)),
    ]
    assert dict(requests[1].parameters) == {
        "source": DAY_UNIT_SOURCE,
        "area": BBOX,
        "day_range": "1",
        "start_date": "2026-09-18",
    }


def test_plan_requests_plans_nothing_when_ingest_bbox_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INGEST_BBOX", raising=False)

    requests = STRATEGY.plan_requests([date(2026, 9, 18)], _shipped_lane(), load_region("pnw"))

    assert requests == []


# --- full turns, through run_turn ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_normal_detections_day_writes_one_row_per_populated_cell(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two detections in the same 0.005-degree cell on one day: one written row, counts folded."""
    store, world = MemoryLaneStore(), FirmsWorld()
    world.publish(
        EDGE,
        "VIIRS_SNPP_NRT",
        [_row(lat=45.101, lon=-120.201, satellite="SNPP", acq_date="2026-09-18", acq_time="0100")],
    )
    world.publish(
        EDGE,
        "VIIRS_SNPP_NRT",
        [
            *world.rows[(EDGE.isoformat(), "VIIRS_SNPP_NRT")],
            _row(lat=45.102, lon=-120.202, satellite="SNPP", acq_date="2026-09-18", acq_time="0130"),
        ],
    )

    exit_code, report = await _turn(_config_lane(), store, world, monkeypatch)

    assert exit_code == 0, report
    rows = _rows(store, EDGE)
    assert len(rows) == 1
    assert (rows[0]["cell_longitude"], rows[0]["cell_latitude"], rows[0]["detection_count"]) == (-120.205, 45.1, 2)
    # Exactly one day carries detections; the other four days in the window are zero-row absences.
    assert (report["days_written"], report["days_absent"]) == (1, 4)


@pytest.mark.asyncio
async def test_a_day_where_every_live_product_reports_zero_detections_is_a_governed_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, world = MemoryLaneStore(), FirmsWorld()
    # Every (day, product) defaults to a header-only, zero-row CSV -- nothing to publish().

    exit_code, report = await _turn(_config_lane(), store, world, monkeypatch)

    assert exit_code == 0, report
    for day_offset in range(5):
        day = WINDOW_FIRST + timedelta(days=day_offset)
        assert store.status(STREAM, day) == "absent"
    assert report["days_absent"] == 5


@pytest.mark.asyncio
async def test_an_initially_absent_day_is_re_asked_and_its_absence_retracted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 5-day refetch window re-asks a GOVERNED ABSENCE every turn (`census.absent_days()`), so a day that
    starts with zero fires and later gets one retracts its absence and becomes written -- the legacy adapter's
    own "a complete non-empty response retracts that day's absence marker" (docs/lanes/fire-detections.md
    section 2), expressed through the generic `refuse` nature's recheck rather than a hand-rolled retraction."""
    store, world = MemoryLaneStore(), FirmsWorld()
    retracted_day, steady_absent_day = EDGE, EDGE - timedelta(days=1)
    # Every (day, product) defaults to zero rows; nothing published yet anywhere in the window.
    lane = _config_lane()

    first_exit, first_report = await _turn(lane, store, world, monkeypatch)
    assert first_exit == 0, first_report
    assert (store.status(STREAM, retracted_day), store.status(STREAM, steady_absent_day)) == ("absent", "absent")
    assert first_report["days_absent"] == 5

    # FIRMS starts reporting one fire on `retracted_day`; `steady_absent_day` stays genuinely quiet.
    world.publish(retracted_day, "VIIRS_SNPP_NRT", [_row(lat=45.1, lon=-120.2, satellite="SNPP")])
    second_exit, second_report = await _turn(lane, store, world, monkeypatch)

    assert second_exit == 0, second_report
    assert store.status(STREAM, retracted_day) == "data"  # the absence was retracted, never left standing
    assert len(_rows(store, retracted_day)) == 1
    assert store.status(STREAM, steady_absent_day) == "absent"  # still genuinely quiet; not disturbed
    assert second_report["days_written"] == 1


@pytest.mark.asyncio
async def test_an_already_written_day_is_never_re_asked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `refuse` lane's forward fan-out is owed days plus absences only (`census.owed_days()`): once a day
    holds data, no later turn asks FIRMS for it again -- late NRT revisions are deliberately out of scope."""
    store, world = MemoryLaneStore(), FirmsWorld()
    written_day = EDGE
    world.publish(written_day, "VIIRS_SNPP_NRT", [_row(lat=45.1, lon=-120.2, satellite="SNPP")])
    lane = _config_lane()

    first_exit, _first_report = await _turn(lane, store, world, monkeypatch)
    assert first_exit == 0
    assert store.status(STREAM, written_day) == "data"
    asked_before = [key for key in world.asked if key[0] == written_day.isoformat()]
    assert asked_before, "the first turn must actually have asked for the day it wrote"

    second_exit, _second_report = await _turn(lane, store, world, monkeypatch)

    assert second_exit == 0
    asked_after = [key for key in world.asked if key[0] == written_day.isoformat()]
    assert asked_after == asked_before  # not one additional request for the written day


@pytest.mark.asyncio
async def test_a_response_at_the_10000_record_cap_is_never_written_as_if_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single product-day response landing AT the cap cannot be proven complete: `unsettled`, nothing written."""
    store, world = MemoryLaneStore(), FirmsWorld()
    capped_product = FIRMS_VIIRS_SOURCES[0]
    world.publish(EDGE, capped_product, [_row(satellite=capped_product)] * FIRMS_RESPONSE_RECORD_CAP)

    exit_code, report = await _turn(_config_lane(), store, world, monkeypatch)

    assert exit_code == 0, report
    assert store.status(STREAM, EDGE) == "missing"
    unwritten = unwritten_by_day(report)
    assert unwritten[EDGE.isoformat()]["reason"] == "unsettled"
    assert capped_product in unwritten[EDGE.isoformat()]["detail"]


@pytest.mark.asyncio
async def test_a_missing_constellation_member_refuses_the_whole_day_never_a_quiet_fire_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Availability names VIIRS_NOAA21_NRT as covering EDGE, but its fetch fails every retry: the day unit fails
    whole, so on `partial_day = "refuse"` the runner never reaches this strategy's `settle()` for that day
    (`turn.py::_settle_answered`) -- it is reported unwritten under the failure's own reason, and nothing is
    written for its two answering siblings either: never settled short as 2 of 3."""
    store, world = MemoryLaneStore(), FirmsWorld()
    world.publish(EDGE, "VIIRS_SNPP_NRT", [_row(satellite="SNPP")])
    down_product = "VIIRS_NOAA21_NRT"

    def flaky_answer(endpoint: str, parameters: dict[str, str], probe: bool) -> bytes:
        if parameters.get("start_date") == EDGE.isoformat() and parameters.get("source") == down_product:
            raise SourceUnavailableError("upstream 503")
        return world.answer(endpoint, parameters, probe)

    monkeypatch.setenv("INGEST_BBOX", BBOX)
    spec = spec_for(_config_lane(), mode="forward")
    ports = ports_for(STRATEGY, store, upstream=ScriptedUpstream(answer=flaky_answer), clock=ManualClock(NOW))
    exit_code, report = await run(spec, ports)

    assert exit_code == 0, report
    assert store.status(STREAM, EDGE) == "missing"  # never written, even though two of three products answered
    assert unwritten_by_day(report)[EDGE.isoformat()]["reason"] == "upstream_unavailable"


@pytest.mark.asyncio
async def test_a_malformed_detection_refuses_the_whole_day_as_a_strategy_error_never_a_partial_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A detection with coordinates but no parseable acqDate fails identity: the whole day refuses, nothing written."""
    store, world = MemoryLaneStore(), FirmsWorld()
    good = _row(lat=45.1, lon=-120.2, satellite="SNPP")
    malformed = "45.5,-120.5,330.5,n,12.3,SNPP,,0512"  # acq_date cell is empty -> unkeyable
    world.publish(EDGE, "VIIRS_SNPP_NRT", [good, malformed])

    exit_code, report = await _turn(_config_lane(), store, world, monkeypatch)

    assert exit_code == 0, report
    assert store.status(STREAM, EDGE) == "missing"  # never a partial write of just the good row
    assert unwritten_by_day(report)[EDGE.isoformat()]["reason"] == "strategy_error"


# --- availability-aware coverage ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_product_missing_from_availability_is_never_asked_and_the_day_settles_from_the_rest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The forward wedge, closed: NASA retires VIIRS_NOAA21_NRT, so it drops out of `data_availability`.
    The day is written from the two products that DO cover it (expected = present = 2), never held
    `upstream_unavailable` forever by a product that would fail every fetch."""
    retired = "VIIRS_NOAA21_NRT"
    world = FirmsWorld({product: span for product, span in NRT_COVERAGE.items() if product != retired})
    world.publish(EDGE, "VIIRS_SNPP_NRT", [_row(satellite="SNPP")])
    store = MemoryLaneStore()

    exit_code, report = await _turn(_config_lane(), store, world, monkeypatch)

    assert exit_code == 0, report
    assert store.status(STREAM, EDGE) == "data"
    receipt = store.receipts[(STREAM, EDGE)]
    assert (receipt.expected_units, receipt.present_units) == (2, 2)
    assert world.asked_products(EDGE) == {"VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT"}
    assert world.availability_reads == 1  # one read per turn, shared by all five day units


@pytest.mark.asyncio
async def test_an_availability_read_failure_holds_every_owed_day_and_never_guesses_a_constellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`data_availability` itself is down: no product is asked, nothing is written, every owed day is held
    `upstream_unavailable`, and the runner's own exit says every sent unit was upstream-unavailable (75)."""
    store, world = MemoryLaneStore(), FirmsWorld()
    world.availability_down = True

    exit_code, report = await _turn(_config_lane(), store, world, monkeypatch)

    assert exit_code == EXIT_UPSTREAM_UNAVAILABLE, report
    assert world.asked == []
    assert store.tables == {}
    unwritten = unwritten_by_day(report)
    for day_offset in range(5):
        day = WINDOW_FIRST + timedelta(days=day_offset)
        assert store.status(STREAM, day) == "missing"
        assert unwritten[day.isoformat()]["reason"] == "upstream_unavailable"


@pytest.mark.asyncio
async def test_a_day_no_product_covers_is_a_loud_refusal_never_a_quiet_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Availability ends the day before EDGE for every product (FIRMS has not published it yet): EDGE is
    reported unwritten naming the gap, never certified fire-free; the covered days still settle."""
    world = FirmsWorld({product: (date(2026, 1, 1), EDGE - timedelta(days=1)) for product in FIRMS_VIIRS_SOURCES})
    store = MemoryLaneStore()

    exit_code, report = await _turn(_config_lane(), store, world, monkeypatch)

    assert exit_code == 0, report
    assert store.status(STREAM, EDGE) == "missing"
    entry = unwritten_by_day(report)[EDGE.isoformat()]
    assert entry["reason"] == "unsettled"
    assert "names no product covering" in str(entry["detail"])
    assert world.asked_products(EDGE) == set()
    assert store.status(STREAM, EDGE - timedelta(days=1)) == "absent"


@pytest.mark.asyncio
async def test_an_old_day_owes_the_products_that_covered_it_then_not_todays_constellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gap-fill under-count, closed: on a 2000-era day only MODIS_SP is available, so a gap-fill turn asks
    MODIS_SP alone and writes the day with expected = present = 1 -- never today's NRT trio (which did not
    exist yet), and never a day written as complete short of the product that did cover it."""
    oldest = date(2000, 11, 1)
    world = FirmsWorld({**NRT_COVERAGE, "MODIS_SP": (oldest, date(2025, 12, 31))})
    world.publish(oldest, "MODIS_SP", [_row(satellite="Terra", acq_date="2000-11-01")])
    store = MemoryLaneStore()
    lane = _config_lane()
    lane = lane.model_copy(update={"schedule": lane.schedule.model_copy(update={"gap_fill_enabled": True})})

    exit_code, report = await _turn(lane, store, world, monkeypatch, mode="gap-fill")

    assert exit_code == 0, report
    assert store.status(STREAM, oldest) == "data"
    receipt = store.receipts[(STREAM, oldest)]
    assert (receipt.expected_units, receipt.present_units) == (1, 1)
    assert world.asked_products(oldest) == {"MODIS_SP"}
    assert len(_rows(store, oldest)) == 1
    assert world.availability_reads == 1
