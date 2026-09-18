"""The expected horizon is measured from the registration and the calendar, never from the pointer.

A stalled publisher's pointer ceiling agrees with its own newest day, so `latest_recorded_day ==
source_ceiling_day` says nothing. These tests hold the second horizon to being independent of it, and
hold the frozen coverage wire to NOT carrying the new fields until the contract is bumped.
"""

# ruff: noqa: PLR2004 - assertion literals are the measured facts under test

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Final

from agri_data_service.parquet_ops.availability_coverage import (
    AVAILABILITY_STALE_GRACE_DAYS,
    lane_coverage_from_index,
    withheld_lane_coverage,
)
from agri_data_service.parquet_ops.coverage import CensusLane, build_lane_coverage
from agri_data_service.parquet_ops.freshness import (
    EXPECTED_HORIZON_BASIS,
    FRESHNESS_SCHEMA_VERSION,
    PUBLICATION_GRACE_DAYS,
    horizon_gap_days,
    lanes_behind_provider,
    measure_lane_freshness,
    render_freshness_report,
    render_lane_freshness,
)
from agri_data_service.parquet_ops.wire import WarehouseCoverage
from tests.parquet_ops.fakes import FakeListing
from tests.parquet_ops.test_availability_coverage import (
    DROUGHT_LANE,
    SIGNAL_LANE,
    SOIL_LANE,
    golden,
    whole_ladder,
)

#: The audit's shortwave shape, frozen as a FIXTURE: a writer that stopped and a pointer that agrees with it.
#: The 75-day lag below is the policy the lane ran under when the stall was measured and is deliberately
#: NOT the registered value (`CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS`, corrected to 6 by lane A2);
#: this test is about the shape of the yardstick, and a self-contained lane keeps it independent of that number.
FIXTURE_LAG_DAYS: Final = 75
SHORTWAVE_LANE: Final = CensusLane(
    layer="climate-field-shortwave-radiation",
    nature="daily_series",
    kind="observed",
    publication_lag_days=FIXTURE_LAG_DAYS,
)
TODAY: Final = date(2026, 9, 15)
NOW: Final = datetime(2026, 9, 15, 6, tzinfo=UTC)
SHORTWAVE_EXPECTED: Final = TODAY - timedelta(days=FIXTURE_LAG_DAYS)
#: The measured 107-day-stale newest day: 32 days behind the horizon the fixture lag predicts.
SHORTWAVE_NEWEST: Final = TODAY - timedelta(days=107)
SHORTWAVE_STALENESS: Final = 107 - FIXTURE_LAG_DAYS


def test_a_daily_lane_published_through_its_horizon_is_not_behind() -> None:
    expected = TODAY - timedelta(days=SIGNAL_LANE.publication_lag_days)
    verdict = measure_lane_freshness(SIGNAL_LANE, latest_recorded_day=expected, today=TODAY)
    assert verdict.expected_horizon_day == expected
    assert verdict.staleness_days == 0
    assert verdict.behind_provider is False
    assert verdict.tolerance_days == 1 + PUBLICATION_GRACE_DAYS


def test_a_daily_lane_a_month_behind_its_horizon_is_behind_its_provider() -> None:
    expected = TODAY - timedelta(days=SIGNAL_LANE.publication_lag_days)
    verdict = measure_lane_freshness(SIGNAL_LANE, latest_recorded_day=expected - timedelta(days=30), today=TODAY)
    assert verdict.staleness_days == 30
    assert verdict.behind_provider is True


def test_a_release_lane_inside_one_cadence_period_is_not_behind() -> None:
    """Drought publishes weekly with a four-day lag; six days after a release it is quiet, not stalled."""
    expected = TODAY - timedelta(days=DROUGHT_LANE.publication_lag_days)
    quiet = measure_lane_freshness(DROUGHT_LANE, latest_recorded_day=expected - timedelta(days=6), today=TODAY)
    assert quiet.tolerance_days == DROUGHT_LANE.cadence_days + PUBLICATION_GRACE_DAYS
    assert quiet.behind_provider is False
    stopped = measure_lane_freshness(DROUGHT_LANE, latest_recorded_day=expected - timedelta(days=11), today=TODAY)
    assert stopped.behind_provider is True


def test_an_irregular_release_cohort_states_staleness_but_is_never_flagged_behind() -> None:
    """burn-severity: MTBS cohorts years apart under a registered cadence of 1; a permanent flag is noise."""
    cohort = CensusLane(layer="burn-severity", nature="release_series", kind="observed", publication_lag_days=7)
    verdict = measure_lane_freshness(cohort, latest_recorded_day=date(2024, 8, 22), today=TODAY)
    assert verdict.staleness_days is not None
    assert verdict.staleness_days > 700
    assert verdict.behind_provider is None


def test_a_static_lookup_has_no_horizon_to_be_behind() -> None:
    verdict = measure_lane_freshness(SOIL_LANE, latest_recorded_day=date(2026, 1, 1), today=TODAY)
    assert verdict.expected_horizon_day is None
    assert verdict.staleness_days is None
    assert verdict.behind_provider is None


def test_nothing_recorded_still_states_the_horizon_the_provider_reached() -> None:
    verdict = measure_lane_freshness(SIGNAL_LANE, latest_recorded_day=None, today=TODAY)
    assert verdict.expected_horizon_day == TODAY - timedelta(days=SIGNAL_LANE.publication_lag_days)
    assert verdict.staleness_days is None
    assert verdict.behind_provider is None


def test_a_stalled_publisher_looks_healthy_by_its_ceiling_and_behind_by_its_horizon() -> None:
    """THE DEFECT, reproduced: the pointer agrees with the newest day, so only the second horizon sees the stall."""
    stalled = whole_ladder(
        SHORTWAVE_LANE,
        published=[SHORTWAVE_NEWEST - timedelta(days=1), SHORTWAVE_NEWEST],
        source_ceiling=SHORTWAVE_NEWEST,
    )

    rows = lane_coverage_from_index(stalled, lane=SHORTWAVE_LANE, now=NOW)

    for row in rows:
        # The yardstick written by the thing it measures: tautologically "current".
        assert row.latest_recorded_day == row.source_ceiling_day == SHORTWAVE_NEWEST
        assert row.gap_ranges == ()
        # The independent yardstick: a month behind the provider's own lag.
        assert row.expected_horizon_day == SHORTWAVE_EXPECTED
        assert row.staleness_days == SHORTWAVE_STALENESS
        assert row.behind_provider is True
        assert horizon_gap_days(row) == SHORTWAVE_STALENESS


def test_the_expected_horizon_does_not_move_when_the_pointer_does() -> None:
    """Two indexes, same days, different ceilings: the pointer changes, the horizon does not."""
    days = [SHORTWAVE_NEWEST]
    honest = lane_coverage_from_index(
        whole_ladder(SHORTWAVE_LANE, published=days, source_ceiling=SHORTWAVE_EXPECTED),
        lane=SHORTWAVE_LANE,
        now=NOW,
    )
    stalled = lane_coverage_from_index(
        whole_ladder(SHORTWAVE_LANE, published=days, source_ceiling=SHORTWAVE_NEWEST),
        lane=SHORTWAVE_LANE,
        now=NOW,
    )
    assert {row.expected_horizon_day for row in honest} == {row.expected_horizon_day for row in stalled}
    assert {row.staleness_days for row in honest} == {row.staleness_days for row in stalled} == {SHORTWAVE_STALENESS}
    # What DOES differ is the gap tail: an honest ceiling owes the days, a stalled one hides them.
    assert all(row.gap_ranges for row in honest)
    assert all(row.gap_ranges == () for row in stalled)
    assert {horizon_gap_days(row) for row in honest} == {0}


def test_a_withheld_lane_still_states_the_horizon_its_provider_reached() -> None:
    rows = withheld_lane_coverage(SIGNAL_LANE, reason="availability_unpublished", now=NOW)
    for row in rows:
        assert row.expected_horizon_day == TODAY - timedelta(days=SIGNAL_LANE.publication_lag_days)
        assert row.staleness_days is None
        assert row.behind_provider is None
        assert row.withheld_reason == "availability_unpublished"


def test_the_census_path_attaches_the_same_freshness() -> None:
    listing = FakeListing()
    newest = TODAY - timedelta(days=40)
    for tier in (0, 5, 9, 13):
        listing.write_day("signal", "observed", tier, newest)  # type: ignore[arg-type]

    row = build_lane_coverage(listing, lane=SIGNAL_LANE, tier=0, today=TODAY)

    assert row.coverage_authority == "census"
    assert row.source_ceiling_day is None
    assert row.expected_horizon_day == TODAY - timedelta(days=SIGNAL_LANE.publication_lag_days)
    assert row.staleness_days == 40 - SIGNAL_LANE.publication_lag_days
    assert row.behind_provider is True


def test_the_frozen_coverage_wire_does_not_carry_the_freshness_fields() -> None:
    """Schema version 3 is frozen and dual-tested; the new fields ride the sibling report until the bump."""
    rows = lane_coverage_from_index(
        whole_ladder(SHORTWAVE_LANE, published=[SHORTWAVE_NEWEST], source_ceiling=SHORTWAVE_NEWEST),
        lane=SHORTWAVE_LANE,
        now=NOW,
    )
    golden_keys = set(golden()["lanes"][0])  # type: ignore[index]
    for row in rows:
        assert row.behind_provider is True, "the row itself knows"
        assert set(row.to_wire()) == golden_keys, "the frozen wire does not"


def test_the_freshness_report_is_a_sibling_payload_with_its_own_version() -> None:
    rows = lane_coverage_from_index(
        whole_ladder(SHORTWAVE_LANE, published=[SHORTWAVE_NEWEST], source_ceiling=SHORTWAVE_NEWEST),
        lane=SHORTWAVE_LANE,
        now=NOW,
    )
    coverage = WarehouseCoverage(generated_at=NOW, evaluated_through_day=TODAY, lanes=rows)

    report = render_freshness_report(coverage)

    assert report["freshness_schema_version"] == FRESHNESS_SCHEMA_VERSION
    assert report["expected_horizon_basis"] == EXPECTED_HORIZON_BASIS
    assert report["evaluated_through_day"] == "2026-09-15"
    lanes = report["lanes"]
    assert isinstance(lanes, list)
    assert lanes[0] == render_lane_freshness(rows[0])
    assert lanes[0]["expected_horizon_day"] == SHORTWAVE_EXPECTED.isoformat()
    assert lanes[0]["staleness_days"] == SHORTWAVE_STALENESS
    assert lanes[0]["behind_provider"] is True
    assert lanes_behind_provider(rows) == rows


def test_the_pointer_grace_and_the_publication_grace_are_one_literal() -> None:
    """Two questions, one slack: a change to either must be a change to both."""
    assert AVAILABILITY_STALE_GRACE_DAYS == PUBLICATION_GRACE_DAYS
