"""The whole-warehouse census: tier-agnostic, closed against the live edge, honest about `null`.

The golden `coverage.json` is one illustrative census, so it proves the RENDERER byte for byte while
the pydantic contract model proves every census the builder produces. See `tests/interface/AGENTS.md`.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from agri_data_service.foundation.parquet.lane_contract import LANE_NATURES
from agri_data_service.foundation.parquet.paths import validate_layer_slug
from agri_data_service.interface.http.coverage import (
    CensusLane,
    CoverageCache,
    build_coverage,
    build_lane_coverage,
    registered_census_lanes,
)
from agri_data_service.interface.http.wire import DayRange, LaneCoverage, WarehouseCoverage
from tests.contract.wire_contract import WireCoverage
from tests.interface.fakes import FakeListing, instant

FIXTURE = Path(__file__).resolve().parents[1] / "contract" / "fixtures" / "coverage.json"

SIGNAL_LANE = CensusLane(layer="signal", nature="daily_series", kind="observed")
SOIL_LANE = CensusLane(layer="soil-survey", nature="static_lookup", kind="observed")

#: One whole-tier listing per rung of the published ladder, per lane, per census.
LISTINGS_PER_CENSUS = 4
LISTINGS_AFTER_A_REBUILD = 8


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


def _range(entry: object) -> DayRange:
    assert isinstance(entry, dict)
    return DayRange(first_day=date.fromisoformat(entry["from"]), last_day=date.fromisoformat(entry["to"]))


class _CountingListing:
    """Wraps a `FakeListing` and records how many listing walks a census actually pays for."""

    def __init__(self) -> None:
        self.inner = FakeListing()
        self.calls = 0

    def list_keys(
        self,
        layer: str,
        kind: str,
        tier: int,
        *,
        year: int | None = None,
        month: int | None = None,
    ) -> tuple[str, ...]:
        """Count the walk, then answer as the fake normally would."""
        self.calls += 1
        return self.inner.list_keys(layer, kind, tier, year=year, month=month)

    def read_object(self, relative_key: str) -> bytes | None:
        """Delegate to the wrapped fake."""
        return self.inner.read_object(relative_key)
