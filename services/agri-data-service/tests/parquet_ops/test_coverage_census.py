"""The whole-warehouse census: tier-agnostic, closed against the live edge, honest about `null`.

The golden `coverage.json` is reproduced by the BUILDER, from a warehouse seeded with exactly the
facts it states -- a renderer fed the golden's own values back cannot fail on anything the builder
decides, and did not. See `tests/parquet_ops/AGENTS.md`.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from agri_data_service.foundation.parquet.lane_contract import LANE_NATURES
from agri_data_service.foundation.parquet.paths import validate_layer_slug
from agri_data_service.parquet_ops import coverage as coverage_module
from agri_data_service.parquet_ops.coverage import (
    CensusLane,
    CoverageCache,
    build_coverage,
    build_lane_coverage,
    registered_census_lanes,
)
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.parquet_ops.wire import DayRange, LaneCoverage, WarehouseCoverage
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from tests.contract.wire_contract import WireCoverage
from tests.parquet_ops.fakes import FakeListing, instant

if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

FIXTURE = Path(__file__).resolve().parents[1] / "contract" / "fixtures" / "coverage.json"

SIGNAL_LANE = CensusLane(layer="signal", nature="daily_series", kind="observed")
SOIL_LANE = CensusLane(layer="soil-survey", nature="static_lookup", kind="observed")
DROUGHT_LANE = CensusLane(
    layer="drought", nature="release_series", kind="observed", cadence_days=7, publication_lag_days=4
)

#: One whole-tier listing per rung of the published ladder, per lane, per census.
LISTINGS_PER_CENSUS = 4
LISTINGS_AFTER_A_REBUILD = 8

#: The tier the seeded fixtures write at. The census is tier-agnostic, so one rung proves it.
SEEDED_TIER: ZoomTier = 13

#: USDM releases the golden's drought span holds at a seven-day cadence, and the count that produced
#: 138 gap ranges under the rule this suite replaced.
DROUGHT_RELEASES_IN_THE_GOLDEN = 138

#: Concurrent cold page loads the single-flight test fires at one empty cache.
CONCURRENT_COLD_LOADS = 4


def golden() -> dict[str, object]:
    """The frozen census payload."""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_the_census_renderer_reproduces_the_frozen_payload_byte_for_byte() -> None:
    """Field names, `from`/`to` aliasing and `null` bounds, asserted against the golden file itself."""
    payload = golden()
    lanes = tuple(
        LaneCoverage(
            layer=str(lane["layer"]),
            nature=lane["nature"],
            kind=lane["kind"],
            earliest_day=None if lane["earliest_day"] is None else date.fromisoformat(str(lane["earliest_day"])),
            latest_day=None if lane["latest_day"] is None else date.fromisoformat(str(lane["latest_day"])),
            gap_ranges=tuple(_range(entry) for entry in lane["gap_ranges"]),
            governed_absence_ranges=tuple(_range(entry) for entry in lane["governed_absence_ranges"]),
        )
        for lane in payload["lanes"]
    )
    census = WarehouseCoverage(generated_at=instant(str(payload["generated_at"])), lanes=lanes)

    assert census.to_wire() == payload


def test_the_census_builder_reproduces_the_frozen_payload_from_a_warehouse_seeded_to_its_facts() -> None:
    """The golden must be REACHABLE, not merely well-shaped.

    The renderer test above proves field names and `from`/`to` aliasing by feeding the golden its own
    values back, which cannot fail on anything the builder decides. This one seeds a warehouse with
    exactly the facts the golden states -- its spans, its absences, its gaps, each lane at its
    registered cadence -- and asserts the CENSUS of that warehouse is the golden byte for byte.
    """
    payload = golden()
    listing = FakeListing()
    lanes = tuple(_census_lane(entry) for entry in payload["lanes"])
    written = {
        lane.layer: _seed_lane(listing, lane=lane, entry=entry)
        for lane, entry in zip(lanes, payload["lanes"], strict=True)
    }

    census = build_coverage(listing, lanes=lanes, generated_at=instant(str(payload["generated_at"])))

    assert census.to_wire() == payload
    assert written["drought"] == DROUGHT_RELEASES_IN_THE_GOLDEN, (
        "the drought span holds 138 weekly releases -- the count that reported 138 gap ranges before a "
        "Wednesday stopped counting as a missed publication"
    )


def test_a_release_series_reports_a_gap_only_where_a_release_was_owed() -> None:
    """A Wednesday was never owed a USDM map. A skipped Tuesday was, and is the only thing reported."""
    listing = FakeListing()
    for day in (date(2026, 7, 7), date(2026, 7, 14), date(2026, 7, 28)):
        listing.write_day("drought", "observed", SEEDED_TIER, day)

    censused = build_lane_coverage(listing, lane=DROUGHT_LANE, today=date(2026, 8, 1))

    assert censused.gap_ranges == (DayRange(first_day=date(2026, 7, 21), last_day=date(2026, 7, 21)),), (
        "the missed release, and none of the eighteen days between publications"
    )


def test_a_release_series_is_not_late_at_the_live_edge_until_its_publication_lag_runs_out() -> None:
    """USDM's Tuesday map is not missing on the Tuesday; four days later it is."""
    listing = FakeListing()
    listing.write_day("drought", "observed", SEEDED_TIER, date(2026, 8, 18))

    on_time = build_lane_coverage(listing, lane=DROUGHT_LANE, today=date(2026, 8, 25))
    overdue = build_lane_coverage(listing, lane=DROUGHT_LANE, today=date(2026, 8, 30))

    assert on_time.gap_ranges == ()
    assert overdue.gap_ranges == (DayRange(first_day=date(2026, 8, 25), last_day=date(2026, 8, 25)),)


def test_a_daily_series_closes_its_gaps_against_today_and_not_against_its_publication_lag() -> None:
    """Every day up to today was owed an OBSERVATION; the lag says when the driver gets to it, not whether."""
    listing = FakeListing()
    listing.write_day("signal", "observed", SEEDED_TIER, date(2026, 8, 1))
    lagged = CensusLane(layer="signal", nature="daily_series", kind="observed", publication_lag_days=9)

    censused = build_lane_coverage(listing, lane=lagged, today=date(2026, 8, 6))

    assert censused.gap_ranges == (DayRange(first_day=date(2026, 8, 2), last_day=date(2026, 8, 6)),)


def test_a_lane_that_has_never_been_written_reports_null_bounds_rather_than_a_guessed_day() -> None:
    """`soil-survey` has 238,986 source rows and 0 written objects; the census must say so."""
    lane = build_lane_coverage(FakeListing(), lane=SOIL_LANE, today=date(2026, 8, 25))

    assert lane.earliest_day is None
    assert lane.latest_day is None
    assert lane.gap_ranges == ()
    assert lane.governed_absence_ranges == ()


def test_a_day_counts_as_covered_when_any_published_tier_holds_it() -> None:
    """Tier-agnostic by contract: per-tier coverage would multiply the census to answer nothing."""
    listing = FakeListing()
    listing.write_day("signal", "observed", 13, date(2026, 8, 1))
    listing.write_day("signal", "observed", 9, date(2026, 8, 2))

    lane = build_lane_coverage(listing, lane=SIGNAL_LANE, today=date(2026, 8, 2))

    assert lane.earliest_day == date(2026, 8, 1)
    assert lane.latest_day == date(2026, 8, 2)
    assert lane.gap_ranges == ()


def test_gaps_and_governed_absences_are_disjoint_runs_closed_against_today() -> None:
    """The days since a lane last published are a gap a reader can act on, not an unknown."""
    listing = FakeListing()
    listing.write_day("signal", "observed", 13, date(2026, 8, 1))
    for day in (date(2026, 8, 2), date(2026, 8, 3)):
        listing.write_absence(
            "signal",
            "observed",
            13,
            day,
            reason="upstream published no scenes",
            upstream_response="HTTP 200, features: []",
            recorded_at=instant("2026-08-04T00:00:00Z"),
            run_id="run",
        )

    lane = build_lane_coverage(listing, lane=SIGNAL_LANE, today=date(2026, 8, 6))

    assert lane.latest_day == date(2026, 8, 1)
    assert lane.governed_absence_ranges == (DayRange(first_day=date(2026, 8, 2), last_day=date(2026, 8, 3)),)
    assert lane.gap_ranges == (DayRange(first_day=date(2026, 8, 4), last_day=date(2026, 8, 6)),)


def test_a_half_written_day_is_a_gap_in_the_census_because_nothing_of_it_is_servable() -> None:
    listing = FakeListing()
    listing.write_day("signal", "observed", 13, date(2026, 8, 1))
    listing.write_day("signal", "observed", 13, date(2026, 8, 2), complete=False)

    lane = build_lane_coverage(listing, lane=SIGNAL_LANE, today=date(2026, 8, 2))

    assert lane.latest_day == date(2026, 8, 1)
    assert lane.gap_ranges == (DayRange(first_day=date(2026, 8, 2), last_day=date(2026, 8, 2)),)


def test_a_static_lookup_reports_its_version_stamp_and_never_a_gap_since_it() -> None:
    """A version stamp is not a day anyone observed, so no day between two versions can be missing.

    Caught against the real warehouse 2026-08-25: `watersheds` (one load day, 2026-08-07) reported a
    gap running to today, which would gray out a slider that has no axis to scrub in the first place.
    """
    listing = FakeListing()
    listing.write_day("watersheds", "observed", 13, date(2026, 8, 7))
    lane = CensusLane(layer="watersheds", nature="static_lookup", kind="observed")

    censused = build_lane_coverage(listing, lane=lane, today=date(2026, 8, 25))

    assert censused.earliest_day == date(2026, 8, 7)
    assert censused.latest_day == date(2026, 8, 7)
    assert censused.gap_ranges == ()
    assert censused.governed_absence_ranges == ()


def test_a_built_census_satisfies_the_frozen_contract_model() -> None:
    """`extra="forbid"`: a census field nobody announced fails here rather than in the browser."""
    listing = FakeListing()
    listing.write_day("signal", "observed", 13, date(2026, 8, 1))

    census = build_coverage(
        listing,
        lanes=(SIGNAL_LANE, SOIL_LANE),
        generated_at=datetime(2026, 8, 25, 4, 0, tzinfo=UTC),
    )
    parsed = WireCoverage.model_validate(census.to_wire())

    assert parsed.generated_at == "2026-08-25T04:00:00Z"
    assert [lane.layer for lane in parsed.lanes] == ["signal", "soil-survey"]
    assert parsed.lanes[1].earliest_day is None


def test_the_census_covers_every_registered_lane_and_names_a_real_nature_for_each() -> None:
    lanes = registered_census_lanes()

    assert len(lanes) >= 1
    assert len({lane.layer for lane in lanes}) == len(lanes), "one census row per lane, or a lookup is ambiguous"
    for lane in lanes:
        assert validate_layer_slug(lane.layer) == lane.layer
        assert lane.nature in LANE_NATURES
        assert lane.kind == "observed"


def test_the_census_is_memoized_so_a_burst_of_page_loads_pays_one_listing_walk() -> None:
    listing = _CountingListing()
    cache = CoverageCache(ttl_seconds=600)
    first_call = datetime(2026, 8, 25, 4, 0, tzinfo=UTC)

    cache.get(listing, lanes=(SIGNAL_LANE,), now=first_call)
    cache.get(listing, lanes=(SIGNAL_LANE,), now=datetime(2026, 8, 25, 4, 1, tzinfo=UTC))
    walks_while_fresh = listing.calls
    cache.get(listing, lanes=(SIGNAL_LANE,), now=datetime(2026, 8, 25, 4, 20, tzinfo=UTC))

    assert walks_while_fresh == LISTINGS_PER_CENSUS, "one whole-tier listing per published rung, once"
    assert listing.calls == LISTINGS_AFTER_A_REBUILD, "an expired census is rebuilt rather than served stale"


def test_a_burst_of_cold_page_loads_pays_one_census_walk_and_not_one_each() -> None:
    """The memo alone is not single-flight: with no lock, N cold loads each start a full 52-listing walk."""
    listing = _CountingListing(delay_seconds=0.05)
    cache = CoverageCache(ttl_seconds=600)
    now = datetime(2026, 8, 25, 4, 0, tzinfo=UTC)

    with ThreadPoolExecutor(max_workers=CONCURRENT_COLD_LOADS) as pool:
        answered = [
            future.result()
            for future in [
                pool.submit(cache.get, listing, lanes=(SIGNAL_LANE,), now=now) for _ in range(CONCURRENT_COLD_LOADS)
            ]
        ]

    assert listing.calls == LISTINGS_PER_CENSUS, "one walk for the burst, not one per caller"
    assert all(census is answered[0] for census in answered)


def test_a_failed_refresh_serves_the_previous_census_rather_than_claiming_an_empty_warehouse() -> None:
    """A census the warehouse earned must not become a whole-map absence claim because a listing failed."""
    listing = _CountingListing()
    cache = CoverageCache(ttl_seconds=1)
    first = cache.get(listing, lanes=(SIGNAL_LANE,), now=datetime(2026, 8, 25, 4, 0, tzinfo=UTC))
    listing.fault = ConnectionError("the object store did not answer")

    served = cache.get(listing, lanes=(SIGNAL_LANE,), now=datetime(2026, 8, 25, 4, 5, tzinfo=UTC))

    assert served is first, "the answer carries its own `generated_at`, so its age is stated rather than hidden"


def test_a_first_census_that_fails_raises_rather_than_inventing_an_empty_one() -> None:
    listing = _CountingListing()
    listing.fault = ConnectionError("the object store did not answer")

    with pytest.raises(ConnectionError):
        CoverageCache().get(listing, lanes=(SIGNAL_LANE,), now=datetime(2026, 8, 25, 4, 0, tzinfo=UTC))


def test_the_census_refuses_when_its_aggregate_listing_budget_is_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    """`MAX_LISTED_KEYS_PER_REQUEST` bounds ONE listing; a census makes fifty-two of them."""
    listing = FakeListing()
    for offset in range(3):
        listing.write_day("signal", "observed", SEEDED_TIER, date(2026, 8, 1) + timedelta(days=offset))
    monkeypatch.setattr(coverage_module, "MAX_CENSUS_LISTED_KEYS", 2)

    with pytest.raises(ServingRefusalError) as raised:
        build_coverage(listing, lanes=(SIGNAL_LANE,), generated_at=datetime(2026, 8, 25, 4, 0, tzinfo=UTC))

    assert raised.value.code == "census_budget_exhausted"


def test_every_registered_lane_carries_its_own_cadence_and_publication_lag_into_the_census() -> None:
    """The gap rule is the lane's own rhythm, so a census that guessed it would report false gaps."""
    lanes = {lane.layer: lane for lane in registered_census_lanes()}

    for slug, lane in lanes.items():
        registration = LANE_REGISTRY[slug]
        assert (lane.cadence_days, lane.publication_lag_days) == (
            registration.cadence_days,
            registration.publication_lag_days,
        )
    assert lanes["drought"].cadence_days > 1, "the release series this rule exists for"


def _census_lane(entry: object) -> CensusLane:
    """Build one census lane from the golden's row, taking its rhythm from the REGISTRY."""
    assert isinstance(entry, dict)
    registration = LANE_REGISTRY[str(entry["layer"])]
    assert registration.nature == entry["nature"], "the golden's nature must be the lane's registered one"
    kind: PartitionKind = "observed"
    assert entry["kind"] == kind
    return CensusLane(
        layer=registration.slug,
        nature=registration.nature,
        kind=kind,
        cadence_days=registration.cadence_days,
        publication_lag_days=registration.publication_lag_days,
    )


def _seed_lane(listing: FakeListing, *, lane: CensusLane, entry: object) -> int:
    """Write the warehouse the golden describes for one lane, and return how many days it holds."""
    assert isinstance(entry, dict)
    if entry["earliest_day"] is None:
        return 0
    earliest = date.fromisoformat(str(entry["earliest_day"]))
    latest = date.fromisoformat(str(entry["latest_day"]))
    absent = _days_in(entry["governed_absence_ranges"])
    unwritten = _days_in(entry["gap_ranges"]) | absent
    step = timedelta(days=lane.cadence_days)
    written = 0
    day = earliest
    while day <= latest:
        if day not in unwritten:
            listing.write_day(lane.layer, lane.kind, SEEDED_TIER, day)
            written += 1
        day += step
    for day in sorted(absent):
        listing.write_absence(
            lane.layer,
            lane.kind,
            SEEDED_TIER,
            day,
            reason="upstream published no scenes for this day",
            upstream_response="HTTP 200, features: []",
            recorded_at=instant("2026-08-17T03:02:11Z"),
            run_id="parquet-drain:1a7d9c22",
        )
    return written


def _days_in(ranges: object) -> set[date]:
    """Expand a golden's closed ranges into the days they claim."""
    assert isinstance(ranges, list)
    days: set[date] = set()
    for entry in ranges:
        span = _range(entry)
        day = span.first_day
        while day <= span.last_day:
            days.add(day)
            day += timedelta(days=1)
    return days


def _range(entry: object) -> DayRange:
    assert isinstance(entry, dict)
    return DayRange(first_day=date.fromisoformat(entry["from"]), last_day=date.fromisoformat(entry["to"]))


class _CountingListing:
    """Wraps a `FakeListing`, records how many walks a census pays for, and can be made to fail."""

    def __init__(self, delay_seconds: float = 0.0) -> None:
        self.inner = FakeListing()
        self.calls = 0
        self.delay_seconds = delay_seconds
        self.fault: Exception | None = None

    def list_keys(
        self,
        layer: str,
        kind: str,
        tier: int,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[str, ...]:
        """Count the walk, optionally hold it open long enough for a burst to overlap, then answer."""
        self.calls += 1
        if self.fault is not None:
            raise self.fault
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return self.inner.list_keys(layer, kind, tier, year=year, month=month)

    def read_object(self, relative_key: str) -> bytes | None:
        """Delegate to the wrapped fake."""
        return self.inner.read_object(relative_key)
