"""Annual source editions do not create daily debt or silently fall back to an older year."""

from dataclasses import replace
from datetime import date

import pytest

from agri_data_service.parquet_ops.coverage import build_lane_coverage, census_lane_from_registration
from agri_data_service.parquet_ops.freshness import measure_lane_freshness
from agri_data_service.parquet_ops.request_params import ReadScope
from agri_data_service.parquet_ops.serving import resolve_release
from agri_data_service.parquet_ops.wire import DayNotWritten, DayRange, LaneNeverWritten, PublishedDay
from agri_data_service.pipeline.parquet.gap_census import build_lane_census
from agri_data_service.pipeline.parquet.gap_fill_contract import lane_window
from agri_data_service.pipeline.parquet.lane_ceiling import allowed_source_ceiling
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY, LaneRegistryError
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.crop_cover_releases import CDL_RELEASE_DATES
from tests.parquet.test_objectstore_writer import RecordingBackend
from tests.parquet_ops.fakes import FakeListing, FakeRowReader

CROP_LANE = LANE_REGISTRY["crop-cover"]
RELEASE_DAYS = tuple(CDL_RELEASE_DATES.values())
TODAY = date(2026, 9, 20)
SCOPE = ReadScope(layer="crop-cover", kind="observed", tier=13, bbox=None)


def test_empty_annual_lane_owes_only_admitted_editions() -> None:
    result = build_lane_census(CROP_LANE, ObjectStore(RecordingBackend()), today=TODAY)

    assert result.missing_days == tuple(reversed(RELEASE_DAYS))
    assert result.first_day == RELEASE_DAYS[0]
    assert result.last_day == RELEASE_DAYS[-1]
    assert result.absent_days == 0
    assert result.error is None


def test_annual_calendar_excludes_future_editions_and_prepublication_windows() -> None:
    before_first = date(2023, 1, 29)
    between_editions = date(2024, 1, 30)

    assert lane_window(CROP_LANE, today=before_first) is None
    result = build_lane_census(CROP_LANE, ObjectStore(RecordingBackend()), today=between_editions)
    assert result.missing_days == (RELEASE_DAYS[0],)
    assert allowed_source_ceiling(CROP_LANE, today=between_editions) == RELEASE_DAYS[0]
    assert allowed_source_ceiling(CROP_LANE, today=TODAY) == RELEASE_DAYS[-1]


def test_annual_coverage_keeps_real_editions_without_intervening_absences() -> None:
    listing = FakeListing()
    for day in RELEASE_DAYS:
        listing.write_day("crop-cover", "observed", 13, day)

    result = build_lane_coverage(listing, lane=census_lane_from_registration(CROP_LANE), tier=13, today=TODAY)

    assert result.published_ranges == tuple(DayRange(day, day) for day in RELEASE_DAYS)
    assert result.latest_day == RELEASE_DAYS[-1]
    assert result.latest_recorded_day == RELEASE_DAYS[-1]
    assert result.gap_ranges == ()
    assert result.governed_absence_ranges == ()
    assert result.behind_provider is False


def test_missing_annual_edition_is_a_gap_and_is_detected_by_freshness() -> None:
    listing = FakeListing()
    for day in RELEASE_DAYS[:-1]:
        listing.write_day("crop-cover", "observed", 13, day)
    lane = census_lane_from_registration(CROP_LANE)

    result = build_lane_coverage(listing, lane=lane, tier=13, today=TODAY)
    freshness = measure_lane_freshness(lane, latest_recorded_day=RELEASE_DAYS[-2], today=TODAY)

    assert result.gap_ranges == (DayRange(RELEASE_DAYS[-1], RELEASE_DAYS[-1]),)
    assert freshness.expected_horizon_day == RELEASE_DAYS[-1]
    assert freshness.behind_provider is True


def test_release_reader_does_not_substitute_an_old_crop_year_for_a_missing_new_edition() -> None:
    listing = FakeListing()
    part = listing.write_day("crop-cover", "observed", 13, RELEASE_DAYS[-2])
    reader = FakeRowReader(rows_by_key={part: ({"crop_year": 2024},)})

    result = resolve_release(listing, reader, scope=SCOPE, as_of=TODAY)

    assert result == DayNotWritten(requested_day=TODAY)
    assert reader.reads == []


def test_release_reader_selects_the_eligible_edition_and_ignores_stray_calendar_parts() -> None:
    listing = FakeListing()
    part = listing.write_day("crop-cover", "observed", 13, RELEASE_DAYS[0])
    listing.write_day("crop-cover", "observed", 13, date(2023, 8, 1))
    listing.write_day("crop-cover", "observed", 13, RELEASE_DAYS[1])
    reader = FakeRowReader(rows_by_key={part: ({"crop_year": 2022},)})
    selected_day = date(2023, 9, 1)

    result = resolve_release(listing, reader, scope=SCOPE, as_of=selected_day)

    assert isinstance(result, PublishedDay)
    assert result.served_day == RELEASE_DAYS[0]
    assert result.requested_day == selected_day
    assert result.rows == ({"crop_year": 2022},)


def test_empty_annual_lane_retains_never_written_state() -> None:
    assert resolve_release(FakeListing(), FakeRowReader(), scope=SCOPE, as_of=TODAY) == LaneNeverWritten(
        requested_day=TODAY
    )


@pytest.mark.parametrize(
    "days",
    [(), tuple(reversed(RELEASE_DAYS)), (RELEASE_DAYS[0], RELEASE_DAYS[0]), RELEASE_DAYS[1:]],
)
def test_release_calendar_refuses_ambiguous_or_incomplete_registration(days: tuple[date, ...]) -> None:
    with pytest.raises(LaneRegistryError, match="release"):
        replace(CROP_LANE, release_days=days)


def test_explicit_calendar_cannot_be_combined_with_daily_nature_or_cadence_arithmetic() -> None:
    with pytest.raises(LaneRegistryError, match="release"):
        replace(CROP_LANE, nature="daily_series")
    with pytest.raises(LaneRegistryError, match="release"):
        replace(CROP_LANE, cadence_days=365)


def test_daily_lane_source_ceiling_remains_lag_adjusted() -> None:
    lane = LANE_REGISTRY["signal"]
    assert lane.release_days is None
    assert allowed_source_ceiling(lane, today=TODAY) == date(2026, 9, 11)
