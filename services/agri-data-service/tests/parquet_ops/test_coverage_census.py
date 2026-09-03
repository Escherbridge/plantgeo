"""The whole-warehouse census: exact per rung, closed at the live edge, honest about `null`.

The golden `coverage.json` is reproduced by the BUILDER, from a warehouse seeded with exactly the
facts it states -- a renderer fed the golden's own values back cannot fail on anything the builder
decides, and did not. See `tests/parquet_ops/AGENTS.md`.
"""

from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest

from agri_data_service.foundation.parquet.lane_contract import LANE_NATURES
from agri_data_service.foundation.parquet.paths import validate_layer_slug
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS, validate_zoom_tier
from agri_data_service.parquet_ops import coverage as coverage_module
from agri_data_service.parquet_ops.coverage import (
    CENSUS_LIST_WORKERS,
    DEDICATED_SLIDER_PRODUCT_LAYERS,
    NON_SLIDER_REGISTERED_LAYERS,
    CensusLane,
    CoverageCache,
    build_coverage,
    build_lane_coverage,
    registered_census_lanes,
)
from agri_data_service.parquet_ops.faults import ServingRefusalError
from agri_data_service.parquet_ops.request_params import ReadScope
from agri_data_service.parquet_ops.serving import resolve_release
from agri_data_service.parquet_ops.snapshot_products import PRODUCT_BY_LAYER
from agri_data_service.parquet_ops.warehouse_reader import ObjectStoreListing
from agri_data_service.parquet_ops.wire import DayRange, LaneCoverage, WarehouseCoverage
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ListedObject
from agri_data_service.warehouse.parquet.schema import get_stream_schema
from tests.contract.wire_contract import WireCoverage
from tests.parquet_ops.fakes import FakeListing, FakeRowReader, instant

if TYPE_CHECKING:
    from collections.abc import Iterator

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

FIXTURE = Path(__file__).resolve().parents[1] / "contract" / "fixtures" / "coverage.json"

SIGNAL_LANE = CensusLane(layer="signal", nature="daily_series", kind="observed")
SOIL_LANE = CensusLane(layer="soil-survey", nature="static_lookup", kind="observed")
DROUGHT_LANE = CensusLane(
    layer="drought", nature="release_series", kind="observed", cadence_days=7, publication_lag_days=4
)

#: One whole-stream listing per physical lane, per census.
LISTINGS_PER_CENSUS = 1
LISTINGS_AFTER_A_REBUILD = 2

#: The tier used by focused single-rung tests.
SEEDED_TIER: ZoomTier = 13

#: Concurrent cold page loads the single-flight test fires at one empty cache.
CONCURRENT_COLD_LOADS = 4

#: The fixed R2 listing ceiling the cold-path regression protects.
EXPECTED_CENSUS_LIST_WORKERS: Final = 3

#: Direct and dedicated physical lanes included in one production census.
EXPECTED_REGISTERED_CENSUS_LANES: Final = 16

#: Every registered physical lane must report all four serving rungs.
EXPECTED_CENSUS_RUNG_ROWS: Final = EXPECTED_REGISTERED_CENSUS_LANES * len(ZOOM_TIERS)


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
            zoom=validate_zoom_tier(int(lane["zoom"])),
            earliest_day=None if lane["earliest_day"] is None else date.fromisoformat(str(lane["earliest_day"])),
            latest_day=None if lane["latest_day"] is None else date.fromisoformat(str(lane["latest_day"])),
            published_ranges=tuple(_range(entry) for entry in lane["published_ranges"]),
            gap_ranges=tuple(_range(entry) for entry in lane["gap_ranges"]),
            governed_absence_ranges=tuple(_range(entry) for entry in lane["governed_absence_ranges"]),
            coverage_authority=lane["coverage_authority"],
            availability_generation_sha256=lane["availability_generation_sha256"],
            availability_pointer_key=lane["availability_pointer_key"],
            source_ceiling_day=(
                None if lane["source_ceiling_day"] is None else date.fromisoformat(str(lane["source_ceiling_day"]))
            ),
            required_rungs=tuple(lane["required_rungs"]),
            withheld_reason=lane["withheld_reason"],
        )
        for lane in payload["lanes"]
    )
    census = WarehouseCoverage(
        generated_at=instant(str(payload["generated_at"])),
        evaluated_through_day=date.fromisoformat(str(payload["evaluated_through_day"])),
        lanes=lanes,
    )

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
    lanes_by_layer = {str(entry["layer"]): _census_lane(entry) for entry in payload["lanes"]}
    lanes = tuple(lanes_by_layer.values())
    written = {
        (str(entry["layer"]), int(entry["zoom"])): _seed_lane(
            listing,
            lane=lanes_by_layer[str(entry["layer"])],
            entry=entry,
        )
        for entry in payload["lanes"]
    }

    census = build_coverage(listing, lanes=lanes, generated_at=instant(str(payload["generated_at"])))

    assert census.to_wire() == payload
    assert all(written[("signal", tier)] > 0 for tier in ZOOM_TIERS)


def test_a_missing_release_reports_its_full_uncovered_carry_interval() -> None:
    """A skipped Tuesday leaves every day unreadable until the next release, not only Tuesday."""
    listing = FakeListing()
    for day in (date(2026, 7, 7), date(2026, 7, 14), date(2026, 7, 28)):
        listing.write_day("drought", "observed", SEEDED_TIER, day)

    censused = build_lane_coverage(listing, lane=DROUGHT_LANE, tier=SEEDED_TIER, today=date(2026, 8, 1))

    assert censused.published_ranges == (
        DayRange(first_day=date(2026, 7, 7), last_day=date(2026, 7, 20)),
        DayRange(first_day=date(2026, 7, 28), last_day=date(2026, 8, 1)),
    )
    assert censused.gap_ranges == (DayRange(first_day=date(2026, 7, 21), last_day=date(2026, 7, 27)),), (
        "the complete interval that bounded release carry cannot serve"
    )


def test_a_healthy_release_carries_through_the_current_sunday() -> None:
    listing = FakeListing()
    listing.write_day("drought", "observed", SEEDED_TIER, date(2026, 8, 18))

    censused = build_lane_coverage(listing, lane=DROUGHT_LANE, tier=SEEDED_TIER, today=date(2026, 8, 23))

    assert censused.earliest_day == date(2026, 8, 18)
    assert censused.latest_day == date(2026, 8, 23)
    assert censused.published_ranges == (DayRange(first_day=date(2026, 8, 18), last_day=date(2026, 8, 23)),)
    assert censused.gap_ranges == ()


def test_the_latest_governed_release_absence_uses_live_carry_then_becomes_historical() -> None:
    listing = FakeListing()
    listing.write_day("drought", "observed", SEEDED_TIER, date(2026, 8, 11))
    listing.write_absence(
        "drought",
        "observed",
        SEEDED_TIER,
        date(2026, 8, 18),
        reason="upstream published no weekly release",
        upstream_response="HTTP 200, release absent",
        recorded_at=instant("2026-08-22T00:00:00Z"),
        run_id="drought-gap-fill:2026-08-18",
    )

    live = build_lane_coverage(listing, lane=DROUGHT_LANE, tier=SEEDED_TIER, today=date(2026, 8, 30))

    assert live.published_ranges == (DayRange(first_day=date(2026, 8, 11), last_day=date(2026, 8, 17)),)
    assert live.governed_absence_ranges == (DayRange(first_day=date(2026, 8, 18), last_day=date(2026, 8, 30)),)
    assert live.gap_ranges == ()

    listing.write_day("drought", "observed", SEEDED_TIER, date(2026, 9, 8))
    historical = build_lane_coverage(listing, lane=DROUGHT_LANE, tier=SEEDED_TIER, today=date(2026, 9, 8))

    assert historical.governed_absence_ranges == (DayRange(first_day=date(2026, 8, 18), last_day=date(2026, 8, 24)),)
    assert historical.gap_ranges == (DayRange(first_day=date(2026, 8, 25), last_day=date(2026, 9, 7)),)


def test_the_latest_release_uses_live_carry_then_becomes_historical_when_a_later_release_lands() -> None:
    listing = FakeListing()
    listing.write_day("drought", "observed", SEEDED_TIER, date(2026, 8, 18))

    live = build_lane_coverage(listing, lane=DROUGHT_LANE, tier=SEEDED_TIER, today=date(2026, 8, 30))

    assert live.published_ranges == (DayRange(first_day=date(2026, 8, 18), last_day=date(2026, 8, 30)),)
    assert live.gap_ranges == ()

    listing.write_day("drought", "observed", SEEDED_TIER, date(2026, 9, 8))
    historical = build_lane_coverage(listing, lane=DROUGHT_LANE, tier=SEEDED_TIER, today=date(2026, 9, 8))

    assert historical.published_ranges == (
        DayRange(first_day=date(2026, 8, 18), last_day=date(2026, 8, 24)),
        DayRange(first_day=date(2026, 9, 8), last_day=date(2026, 9, 8)),
    )
    assert historical.gap_ranges == (DayRange(first_day=date(2026, 8, 25), last_day=date(2026, 9, 7)),)


def test_a_conflicting_release_is_a_gap_for_every_day_the_release_reader_refuses() -> None:
    listing = FakeListing()
    listing.write_day("drought", "observed", SEEDED_TIER, date(2026, 8, 18))
    listing.write_day("drought", "observed", SEEDED_TIER, date(2026, 8, 25))
    listing.write_absence(
        "drought",
        "observed",
        SEEDED_TIER,
        date(2026, 8, 25),
        reason="conflicting publication",
        upstream_response="part and absence both exist",
        recorded_at=instant("2026-08-26T00:00:00Z"),
        run_id="conflict",
    )
    scope = ReadScope(layer="drought", kind="observed", tier=SEEDED_TIER, bbox=None)

    with pytest.raises(ServingRefusalError) as raised:
        resolve_release(listing, FakeRowReader(), scope=scope, as_of=date(2026, 8, 30))
    conflicted = build_lane_coverage(listing, lane=DROUGHT_LANE, tier=SEEDED_TIER, today=date(2026, 8, 30))

    assert raised.value.code == "partition_day_conflict"
    assert conflicted.published_ranges == (DayRange(first_day=date(2026, 8, 18), last_day=date(2026, 8, 24)),)
    assert conflicted.gap_ranges == (DayRange(first_day=date(2026, 8, 25), last_day=date(2026, 8, 30)),)

    listing.write_day("drought", "observed", SEEDED_TIER, date(2026, 9, 8))
    superseded = build_lane_coverage(listing, lane=DROUGHT_LANE, tier=SEEDED_TIER, today=date(2026, 9, 8))

    assert superseded.gap_ranges == (DayRange(first_day=date(2026, 8, 25), last_day=date(2026, 9, 7)),)


def test_a_daily_series_closes_its_gaps_against_today_and_not_against_its_publication_lag() -> None:
    """Every day up to today was owed an OBSERVATION; the lag says when the driver gets to it, not whether."""
    listing = FakeListing()
    listing.write_day("signal", "observed", SEEDED_TIER, date(2026, 8, 1))
    lagged = CensusLane(layer="signal", nature="daily_series", kind="observed", publication_lag_days=9)

    censused = build_lane_coverage(listing, lane=lagged, tier=SEEDED_TIER, today=date(2026, 8, 6))

    assert censused.gap_ranges == (DayRange(first_day=date(2026, 8, 2), last_day=date(2026, 8, 6)),)


def test_a_lane_that_has_never_been_written_reports_null_bounds_rather_than_a_guessed_day() -> None:
    """`soil-survey` has 238,986 source rows and 0 written objects; the census must say so."""
    lane = build_lane_coverage(FakeListing(), lane=SOIL_LANE, tier=SEEDED_TIER, today=date(2026, 8, 25))

    assert lane.earliest_day is None
    assert lane.latest_day is None
    assert lane.published_ranges == ()
    assert lane.gap_ranges == ()
    assert lane.governed_absence_ranges == ()


def test_each_published_tier_reports_only_the_history_that_exact_rung_can_read() -> None:
    listing = FakeListing()
    listing.write_day("signal", "observed", 13, date(2026, 8, 1))
    listing.write_day("signal", "observed", 9, date(2026, 8, 2))

    z13 = build_lane_coverage(listing, lane=SIGNAL_LANE, tier=13, today=date(2026, 8, 2))
    z9 = build_lane_coverage(listing, lane=SIGNAL_LANE, tier=9, today=date(2026, 8, 2))

    assert (z13.zoom, z13.earliest_day, z13.latest_day) == (13, date(2026, 8, 1), date(2026, 8, 1))
    assert z13.published_ranges == (DayRange(first_day=date(2026, 8, 1), last_day=date(2026, 8, 1)),)
    assert z13.gap_ranges == (DayRange(first_day=date(2026, 8, 2), last_day=date(2026, 8, 2)),)
    assert (z9.zoom, z9.earliest_day, z9.latest_day) == (9, date(2026, 8, 2), date(2026, 8, 2))


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

    lane = build_lane_coverage(listing, lane=SIGNAL_LANE, tier=13, today=date(2026, 8, 6))

    assert lane.latest_day == date(2026, 8, 1)
    assert lane.governed_absence_ranges == (DayRange(first_day=date(2026, 8, 2), last_day=date(2026, 8, 3)),)
    assert lane.gap_ranges == (DayRange(first_day=date(2026, 8, 4), last_day=date(2026, 8, 6)),)


def test_a_half_written_day_is_a_gap_in_the_census_because_nothing_of_it_is_servable() -> None:
    listing = FakeListing()
    listing.write_day("signal", "observed", 13, date(2026, 8, 1))
    listing.write_day("signal", "observed", 13, date(2026, 8, 2), complete=False)

    lane = build_lane_coverage(listing, lane=SIGNAL_LANE, tier=13, today=date(2026, 8, 2))

    assert lane.latest_day == date(2026, 8, 1)
    assert lane.published_ranges == (DayRange(first_day=date(2026, 8, 1), last_day=date(2026, 8, 1)),)
    assert lane.gap_ranges == (DayRange(first_day=date(2026, 8, 2), last_day=date(2026, 8, 2)),)


def test_a_conflicting_day_does_not_prove_that_rung_readable() -> None:
    listing = FakeListing()
    listing.write_day("signal", "observed", 13, date(2026, 8, 1))
    listing.write_day("signal", "observed", 13, date(2026, 8, 2))
    listing.write_absence(
        "signal",
        "observed",
        13,
        date(2026, 8, 2),
        reason="conflicting publication",
        upstream_response="HTTP 200, features: []",
        recorded_at=instant("2026-08-03T00:00:00Z"),
        run_id="run",
    )

    lane = build_lane_coverage(listing, lane=SIGNAL_LANE, tier=13, today=date(2026, 8, 2))

    assert lane.latest_day == date(2026, 8, 1)
    assert lane.published_ranges == (DayRange(first_day=date(2026, 8, 1), last_day=date(2026, 8, 1)),)
    assert lane.gap_ranges == (DayRange(first_day=date(2026, 8, 2), last_day=date(2026, 8, 2)),)


def test_a_static_lookup_reports_its_version_stamp_and_never_a_gap_since_it() -> None:
    """A version stamp is not a day anyone observed, so no day between two versions can be missing.

    Caught against the real warehouse 2026-08-25: `watersheds` (one load day, 2026-08-07) reported a
    gap running to today, which would gray out a slider that has no axis to scrub in the first place.
    """
    listing = FakeListing()
    listing.write_day("watersheds", "observed", 13, date(2026, 8, 7))
    lane = CensusLane(layer="watersheds", nature="static_lookup", kind="observed")

    censused = build_lane_coverage(listing, lane=lane, tier=13, today=date(2026, 8, 25))

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
    assert parsed.evaluated_through_day == "2026-08-25"
    assert [(lane.layer, lane.zoom) for lane in parsed.lanes] == [
        (layer, tier) for layer in ("signal", "soil-survey") for tier in ZOOM_TIERS
    ]
    assert all(lane.earliest_day is None for lane in parsed.lanes if lane.layer == "soil-survey")


def test_a_census_row_says_it_was_proven_by_a_listing_and_cites_no_availability_evidence() -> None:
    """A listing binds no cross-rung contract and no ceiling, so it must claim neither."""
    listing = FakeListing()
    listing.write_day("signal", "observed", 13, date(2026, 8, 1))

    census = build_coverage(
        listing,
        lanes=(SIGNAL_LANE,),
        generated_at=datetime(2026, 8, 25, 4, 0, tzinfo=UTC),
    )

    for lane in census.lanes:
        assert lane.coverage_authority == "census"
        assert lane.availability_generation_sha256 is None
        assert lane.availability_pointer_key is None
        assert lane.source_ceiling_day is None
        assert lane.required_rungs == ()
        assert lane.withheld_reason is None


def test_the_memo_is_rebuilt_when_a_bootstrapped_lane_leaves_the_census() -> None:
    """Reusing a wider memo would emit a lane twice: once from its index, once from a stale listing."""
    listing = _CountingListing()
    cache = CoverageCache(ttl_seconds=600)
    now = datetime(2026, 8, 25, 4, 0, tzinfo=UTC)

    both = cache.get(listing, lanes=(SIGNAL_LANE, SOIL_LANE), now=now)
    narrowed = cache.get(listing, lanes=(SOIL_LANE,), now=now)

    assert {lane.layer for lane in both.lanes} == {"signal", "soil-survey"}
    assert {lane.layer for lane in narrowed.lanes} == {"soil-survey"}
    assert listing.calls > LISTINGS_PER_CENSUS, "a different lane set is a different census"


def test_the_census_covers_all_direct_lanes_and_every_schema_backed_slider_product() -> None:
    lanes = registered_census_lanes()

    assert len(lanes) >= 1
    assert len({lane.layer for lane in lanes}) == len(lanes), "one census row per lane, or a lookup is ambiguous"
    for lane in lanes:
        assert validate_layer_slug(lane.layer) == lane.layer
        assert lane.nature in LANE_NATURES
        assert lane.kind == "observed"

    product_lanes = {lane.layer: lane for lane in lanes if lane.layer in DEDICATED_SLIDER_PRODUCT_LAYERS}
    assert set(product_lanes) == set(DEDICATED_SLIDER_PRODUCT_LAYERS)
    assert all(lane.nature == "daily_series" for lane in product_lanes.values())
    # The two exclusion sets are IMPORTED, never respelt: an immutable product is registered as a
    # lane so it has a floor and a schedule, and censused through `build_snapshot_coverage` instead,
    # so a hand-written list here goes stale the day a product gains or loses its snapshot.
    assert {lane.layer for lane in lanes if lane.layer in LANE_REGISTRY} == (
        set(LANE_REGISTRY) - NON_SLIDER_REGISTERED_LAYERS - set(PRODUCT_BY_LAYER)
    )
    for layer in DEDICATED_SLIDER_PRODUCT_LAYERS:
        assert get_stream_schema(layer, "observed").name == layer
    assert len(lanes) == EXPECTED_REGISTERED_CENSUS_LANES, (
        "every lane registration except calendar/signal and the immutable snapshot products, which "
        "are censused through build_snapshot_coverage instead; the five DEDICATED_SLIDER_PRODUCT_LAYERS "
        "are registrations themselves today, so the derived fallback adds no further row"
    )


def test_the_census_is_memoized_so_a_burst_of_page_loads_pays_one_listing_walk() -> None:
    listing = _CountingListing()
    cache = CoverageCache(ttl_seconds=600)
    first_call = datetime(2026, 8, 25, 4, 0, tzinfo=UTC)

    cache.get(listing, lanes=(SIGNAL_LANE,), now=first_call)
    cache.get(listing, lanes=(SIGNAL_LANE,), now=datetime(2026, 8, 25, 4, 1, tzinfo=UTC))
    walks_while_fresh = listing.calls
    cache.get(listing, lanes=(SIGNAL_LANE,), now=datetime(2026, 8, 25, 4, 20, tzinfo=UTC))

    assert walks_while_fresh == LISTINGS_PER_CENSUS, "one whole-stream listing, once"
    assert listing.calls == LISTINGS_AFTER_A_REBUILD, "an expired census is rebuilt rather than served stale"


def test_a_cold_census_lists_each_registered_and_product_lane_tier_once() -> None:
    """The production cold path makes one exact prefix walk per physical lane and rung."""
    backend = _CountingObjectStoreBackend()
    lanes = registered_census_lanes()

    CoverageCache().get(
        ObjectStoreListing(backend=backend, prefix="warehouse/"),
        lanes=lanes,
        now=datetime(2026, 8, 25, 4, 0, tzinfo=UTC),
    )

    assert len(backend.prefixes) == EXPECTED_REGISTERED_CENSUS_LANES
    assert sorted(backend.prefixes) == sorted(f"warehouse/layer={lane.layer}/kind={lane.kind}/" for lane in lanes)


def test_a_cold_census_bounds_parallel_r2_listings_without_a_clock() -> None:
    """Three first-wave listings rendezvous; a serial or unbounded implementation fails structurally."""
    listing = _RendezvousListing()

    census = build_coverage(
        listing,
        lanes=registered_census_lanes(),
        generated_at=datetime(2026, 8, 25, 4, 0, tzinfo=UTC),
    )

    assert CENSUS_LIST_WORKERS == EXPECTED_CENSUS_LIST_WORKERS
    assert listing.calls == len(registered_census_lanes())
    assert listing.max_active == CENSUS_LIST_WORKERS
    assert len(census.lanes) == EXPECTED_CENSUS_RUNG_ROWS


def test_a_burst_of_cold_page_loads_pays_one_census_walk_and_not_one_each() -> None:
    """The memo alone is not single-flight: without the lock, every cold caller starts the full walk."""
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


def test_expired_concurrent_callers_block_for_one_fresh_rebuild() -> None:
    listing = _CountingListing(delay_seconds=0.05)
    cache = CoverageCache(ttl_seconds=1)
    previous = cache.get(listing, lanes=(SIGNAL_LANE,), now=datetime(2026, 8, 25, 4, 0, tzinfo=UTC))
    listing.inner.write_day("signal", "observed", 13, date(2026, 8, 25))
    expired_at = datetime(2026, 8, 25, 4, 5, tzinfo=UTC)

    with ThreadPoolExecutor(max_workers=CONCURRENT_COLD_LOADS) as pool:
        answered = [
            future.result()
            for future in [
                pool.submit(cache.get, listing, lanes=(SIGNAL_LANE,), now=expired_at)
                for _ in range(CONCURRENT_COLD_LOADS)
            ]
        ]

    assert listing.calls == LISTINGS_AFTER_A_REBUILD
    assert all(census is answered[0] and census is not previous for census in answered)
    assert answered[0].generated_at == expired_at


def test_a_failed_refresh_propagates_rather_than_serving_expired_readability_evidence() -> None:
    listing = _CountingListing()
    cache = CoverageCache(ttl_seconds=1)
    cache.get(listing, lanes=(SIGNAL_LANE,), now=datetime(2026, 8, 25, 4, 0, tzinfo=UTC))
    listing.fault = ConnectionError("the object store did not answer")

    with pytest.raises(ConnectionError, match="did not answer"):
        cache.get(listing, lanes=(SIGNAL_LANE,), now=datetime(2026, 8, 25, 4, 5, tzinfo=UTC))


def test_expired_concurrent_callers_share_one_failed_refresh_and_none_receive_stale_evidence() -> None:
    listing = _CountingListing(delay_seconds=0.05)
    cache = CoverageCache(ttl_seconds=1)
    cache.get(listing, lanes=(SIGNAL_LANE,), now=datetime(2026, 8, 25, 4, 0, tzinfo=UTC))
    listing.fault = ConnectionError("the object store did not answer")
    expired_at = datetime(2026, 8, 25, 4, 5, tzinfo=UTC)

    with ThreadPoolExecutor(max_workers=CONCURRENT_COLD_LOADS) as pool:
        futures = [
            pool.submit(cache.get, listing, lanes=(SIGNAL_LANE,), now=expired_at) for _ in range(CONCURRENT_COLD_LOADS)
        ]

    failures: list[ConnectionError] = []
    for future in futures:
        with pytest.raises(ConnectionError, match="did not answer") as raised:
            future.result()
        failures.append(raised.value)

    assert listing.calls == LISTINGS_AFTER_A_REBUILD, "one initial census and one shared failed refresh"
    assert all(failure is failures[0] for failure in failures)


def test_a_first_census_that_fails_raises_rather_than_inventing_an_empty_one() -> None:
    listing = _CountingListing()
    listing.fault = ConnectionError("the object store did not answer")

    with pytest.raises(ConnectionError):
        CoverageCache().get(listing, lanes=(SIGNAL_LANE,), now=datetime(2026, 8, 25, 4, 0, tzinfo=UTC))


def test_the_census_refuses_when_its_aggregate_listing_budget_is_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-listing bound does not replace the aggregate budget across all census lanes."""
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

    for slug, registration in LANE_REGISTRY.items():
        if slug in NON_SLIDER_REGISTERED_LAYERS or slug in PRODUCT_BY_LAYER:
            continue
        lane = lanes[slug]
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
    tier = validate_zoom_tier(int(entry["zoom"]))
    absent = _days_in(entry["governed_absence_ranges"])
    unwritten = _days_in(entry["gap_ranges"]) | absent
    step = timedelta(days=lane.cadence_days)
    written = 0
    day = earliest
    while day <= latest:
        if day not in unwritten:
            listing.write_day(lane.layer, lane.kind, tier, day)
            written += 1
        day += step
    for day in sorted(absent):
        listing.write_absence(
            lane.layer,
            lane.kind,
            tier,
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
        self._lock = threading.Lock()

    def iter_tier_keys(self, layer: str, kind: PartitionKind, tier: ZoomTier) -> Iterator[str]:
        """Count the tier walk, optionally holding it open so cold callers overlap."""
        self._before_list()
        yield from self.inner.iter_tier_keys(layer, kind, tier)

    def iter_stream_keys(self, layer: str, kind: PartitionKind) -> Iterator[str]:
        """Count one whole-stream walk, optionally holding it open so cold callers overlap."""
        self._before_list()
        yield from self.inner.iter_stream_keys(layer, kind)

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
        self._before_list()
        return self.inner.list_keys(layer, kind, tier, year=year, month=month)

    def _before_list(self) -> None:
        """Record and optionally delay one listing operation."""
        with self._lock:
            self.calls += 1
            fault = self.fault
            delay_seconds = self.delay_seconds
        if delay_seconds:
            time.sleep(delay_seconds)
        if fault is not None:
            raise fault

    def read_object(self, relative_key: str) -> bytes | None:
        """Delegate to the wrapped fake."""
        return self.inner.read_object(relative_key)


class _CountingObjectStoreBackend:
    """An empty bucket that records the exact prefixes a cold census requests."""

    def __init__(self, keys: tuple[str, ...] = ()) -> None:
        self.keys = keys
        self.prefixes: list[str] = []

    def list_objects(self, prefix: str) -> Iterator[ListedObject]:
        self.prefixes.append(prefix)
        return (ListedObject(key=key, last_modified=None) for key in self.keys if key.startswith(prefix))

    def get(self, _key: str) -> bytes | None:
        raise AssertionError("coverage must not read object bodies")

    def size_of(self, _key: str) -> int | None:
        return None

    def put(self, _key: str, _payload: bytes, *, content_type: str) -> None:
        del content_type
        raise AssertionError("a census is read-only")

    def delete(self, _key: str) -> None:
        raise AssertionError("a census is read-only")


class _RendezvousListing(FakeListing):
    """An empty listing whose first wave can finish only when all bounded workers are active."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()
        self._first_wave = threading.Barrier(CENSUS_LIST_WORKERS)

    def iter_tier_keys(self, layer: str, kind: PartitionKind, tier: ZoomTier) -> Iterator[str]:
        del layer, kind, tier
        with self._lock:
            call = self.calls
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if call < CENSUS_LIST_WORKERS:
                self._first_wave.wait(timeout=2)
            yield from ()
        finally:
            with self._lock:
                self.active -= 1

    def iter_stream_keys(self, layer: str, kind: PartitionKind) -> Iterator[str]:
        del layer, kind
        with self._lock:
            call = self.calls
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if call < CENSUS_LIST_WORKERS:
                self._first_wave.wait(timeout=2)
            yield from ()
        finally:
            with self._lock:
                self.active -= 1
