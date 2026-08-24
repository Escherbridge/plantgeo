"""The conformed calendar dimension: the field derivation, the coverage rule, and one real version.

The generator is pure stdlib, so most of this is arithmetic pinned against days whose answers are
independently known (a leap day, an ISO week that belongs to the previous year, a month end). The
write path runs against the in-memory `RecordingBackend`, so the whole lane is exercised with no
network, no credentials and no database -- which is the point: this stream has no source system.
"""

from __future__ import annotations

import io
import math
from datetime import UTC, date, datetime, timedelta

import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.calendar import (
    CALENDAR_REQUIRED_FORWARD_DAYS,
    CALENDAR_STREAM,
    CALENDAR_VERSION_FORWARD_DAYS,
    MAX_CALENDAR_DAYS,
    CalendarDay,
    CalendarError,
    calendar_day_for,
    calendar_days,
    calendar_version_covers,
    calendar_version_span,
    days_in_year,
    required_calendar_version_day,
)
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.lanes.calendar import build_calendar_table, export_calendar_version
from agri_data_service.pipeline.parquet.lane_registry import CALENDAR_HISTORY_FLOOR, LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.calendar import CALENDAR_SCHEMA
from tests.parquet.test_objectstore_writer import RecordingBackend

TODAY = date(2026, 8, 22)
LEAP_DAY = date(2028, 2, 29)

# 2027-01-01 is a Friday that ISO 8601 assigns to week 53 of 2026. It is the canonical proof that
# `iso_year` is not `year`, which is why both columns exist rather than one.
ISO_YEAR_STRADDLE = date(2027, 1, 1)
ISO_STRADDLE_YEAR = 2026
ISO_STRADDLE_WEEK = 53
FRIDAY = 5
LEAP_DAY_OF_YEAR = 60
FIRST_QUARTER = 1


def test_a_leap_day_decomposes_without_a_special_case() -> None:
    entry = calendar_day_for(LEAP_DAY)

    assert entry.calendar_day == LEAP_DAY
    assert entry.day_of_year == LEAP_DAY_OF_YEAR
    assert entry.quarter == FIRST_QUARTER
    assert entry.is_month_end, "29 February is a month end in a leap year and the flag must say so"
    assert not entry.is_month_start


def test_iso_year_is_not_the_civil_year_at_a_week_straddle() -> None:
    """Splitting these two across a join is the classic week-boundary defect this dimension prevents."""
    entry = calendar_day_for(ISO_YEAR_STRADDLE)

    assert entry.year == ISO_YEAR_STRADDLE.year
    assert entry.iso_year == ISO_STRADDLE_YEAR
    assert entry.iso_week == ISO_STRADDLE_WEEK
    assert entry.iso_day_of_week == FRIDAY


def test_month_start_and_end_flags_are_computed_not_assumed() -> None:
    assert calendar_day_for(date(2026, 2, 1)).is_month_start
    assert calendar_day_for(date(2026, 2, 28)).is_month_end
    assert not calendar_day_for(date(2026, 2, 27)).is_month_end
    # 2026 is not a leap year, so February has 28 days; a hardcoded 30/31 table would fail here.
    assert calendar_day_for(date(2026, 4, 30)).is_month_end


def _circle_distance(left: CalendarDay, right: CalendarDay) -> float:
    """Euclidean distance between two days on the day-of-year unit circle."""
    return math.hypot(left.day_of_year_sin - right.day_of_year_sin, left.day_of_year_cos - right.day_of_year_cos)


def test_the_meteorological_season_is_a_fixed_month_grouping() -> None:
    """WMO three-month blocks -- no solar event, no equinox lookup. December belongs to DJF."""
    assert calendar_day_for(date(2026, 12, 1)).meteorological_season == "DJF"
    assert calendar_day_for(date(2026, 1, 31)).meteorological_season == "DJF"
    assert calendar_day_for(date(2026, 3, 1)).meteorological_season == "MAM"
    assert calendar_day_for(date(2026, 6, 30)).meteorological_season == "JJA"
    assert calendar_day_for(date(2026, 9, 15)).meteorological_season == "SON"


def test_cyclical_day_of_year_has_no_new_year_discontinuity() -> None:
    """RUNBOOK §0.28.3: raw day-of-year puts 31 December 364 units from 1 January. This does not."""
    new_year = calendar_day_for(date(2027, 1, 1))
    new_years_eve = calendar_day_for(date(2026, 12, 31))
    midsummer = calendar_day_for(date(2026, 7, 2))

    assert new_year.day_of_year_sin == pytest.approx(0.0, abs=1e-12)
    assert new_year.day_of_year_cos == pytest.approx(1.0, abs=1e-12)
    # THE PROPERTY: crossing the year boundary is ONE step on the circle, the same size as any
    # other adjacent pair -- not the 364-unit jump a raw day-of-year column would report.
    across_new_year = _circle_distance(new_years_eve, new_year)
    mid_year_step = _circle_distance(midsummer, calendar_day_for(date(2026, 7, 3)))
    assert across_new_year == pytest.approx(mid_year_step, rel=0.01)
    # Half a year away is the opposite point on the circle, which is what makes it seasonal.
    assert midsummer.day_of_year_cos == pytest.approx(-1.0, abs=0.02)
    for entry in (new_year, new_years_eve, midsummer):
        assert entry.day_of_year_sin**2 + entry.day_of_year_cos**2 == pytest.approx(1.0)


def test_a_leap_year_does_not_shift_the_seasonal_phase() -> None:
    """Dividing by a fixed 365 would drift the cycle by a day every four years."""
    leap_year_days = 366
    common_year_days = 365

    assert days_in_year(2028) == leap_year_days
    assert days_in_year(2026) == common_year_days
    # 1 July is ~half way through both years, so the phase must agree closely despite the extra day.
    assert calendar_day_for(date(2028, 7, 1)).day_of_year_cos == pytest.approx(
        calendar_day_for(date(2026, 7, 1)).day_of_year_cos, abs=0.02
    )


def test_a_span_is_dense_chronological_and_unique() -> None:
    days = calendar_days(date(2026, 1, 1), date(2026, 12, 31))
    expected_days_in_2026 = 365

    assert len(days) == expected_days_in_2026
    assert [entry.calendar_day for entry in days] == sorted(entry.calendar_day for entry in days)
    assert len({entry.calendar_day for entry in days}) == len(days)


def test_a_backwards_or_absurd_span_is_refused() -> None:
    with pytest.raises(CalendarError, match="runs backwards"):
        calendar_days(date(2026, 1, 2), date(2026, 1, 1))
    with pytest.raises(CalendarError, match="exceeds the"):
        calendar_days(date(2026, 1, 1), date(2026, 1, 1) + timedelta(days=MAX_CALENDAR_DAYS))


def test_a_version_reaches_far_enough_past_today_for_a_thirty_day_horizon_from_any_as_of_date() -> None:
    """The product requirement is a 30-day horizon simulated from an as-of date; 400 is the slack."""
    _first, last = calendar_version_span(TODAY, floor=CALENDAR_HISTORY_FLOOR)

    assert last == TODAY + timedelta(days=CALENDAR_VERSION_FORWARD_DAYS)
    assert last >= TODAY + timedelta(days=CALENDAR_REQUIRED_FORWARD_DAYS)
    assert calendar_version_covers(TODAY, today=TODAY)


def test_the_dimension_regenerates_about_once_a_year_rather_than_once_a_day() -> None:
    """A version covering exactly the requirement would be stale tomorrow -- that is the churn removed."""
    slack_days = CALENDAR_VERSION_FORWARD_DAYS - CALENDAR_REQUIRED_FORWARD_DAYS
    last_still_covering = TODAY - timedelta(days=slack_days)
    first_too_old = last_still_covering - timedelta(days=1)

    assert required_calendar_version_day(today=TODAY, newest_version_day=last_still_covering) == (last_still_covering)
    assert required_calendar_version_day(today=TODAY, newest_version_day=first_too_old) == TODAY
    assert required_calendar_version_day(today=TODAY, newest_version_day=None) == TODAY


def test_a_future_dated_version_is_clamped_rather_than_reported_as_a_future_watermark() -> None:
    """`resolve_static_lane` refuses a watermark after today; a version that still covers is current."""
    assert required_calendar_version_day(today=TODAY, newest_version_day=TODAY + timedelta(days=5)) == TODAY


def test_the_table_conforms_to_the_registered_schema_and_covers_every_lane_floor() -> None:
    table = build_calendar_table(TODAY, floor=CALENDAR_HISTORY_FLOOR)

    assert table.schema.equals(CALENDAR_SCHEMA.arrow_schema)
    days = table.column("calendar_day").to_pylist()
    assert days[0] == CALENDAR_HISTORY_FLOOR
    assert days[-1] == TODAY + timedelta(days=CALENDAR_VERSION_FORWARD_DAYS)
    assert len(days) == len(set(days)), "one row per civil day, or two answers to the same question"
    assert days == sorted(days)


def test_every_lane_floor_resolves_inside_one_calendar_version() -> None:
    """A lane whose floor fell outside the dimension could not key to it, which is the join defect."""
    days = set(build_calendar_table(TODAY, floor=CALENDAR_HISTORY_FLOOR).column("calendar_day").to_pylist())

    for registration in LANE_REGISTRY.values():
        assert registration.history_floor in days, registration.slug
    for horizon_day in range(1, 31):
        assert TODAY + timedelta(days=horizon_day) in days


def test_writing_a_version_lands_under_the_calendar_prefix_and_reads_back_as_one_day() -> None:
    """A version spills across parts like every other lane, and still reads as ONE present version."""
    backend = RecordingBackend()

    receipts = export_calendar_version(ObjectStore(backend), day=TODAY, floor=CALENDAR_HISTORY_FLOOR)

    assert [receipt.relative_path for receipt in receipts] == list(backend.objects)
    assert all(key.startswith(f"layer={CALENDAR_STREAM}/") for key in backend.objects), (
        "a lane writes only under its own layer prefix"
    )
    read_back = [pq.read_table(io.BytesIO(backend.objects[receipt.relative_path])) for receipt in receipts]
    assert all(part.schema.equals(CALENDAR_SCHEMA.arrow_schema) for part in read_back)
    assert sum(part.num_rows for part in read_back) == sum(receipt.row_count for receipt in receipts)
    assert read_back[0].column("calendar_day").to_pylist()[0] == CALENDAR_HISTORY_FLOOR
    assert partition_day_statuses(
        layer=CALENDAR_STREAM,
        kind="observed",
        zoom=LANE_BASE_ZOOM_TIER,
        first_day=TODAY,
        last_day=TODAY,
        keys=list(backend.objects),
    ) == {TODAY: "incomplete"}
    # `incomplete`, not `data`: a lane export writes parts, and only the gap-fill driver marks a
    # day complete. Asserting `data` here would quietly require the writer to make a claim it is
    # deliberately not allowed to make.


@pytest.mark.asyncio
async def test_the_registered_adapter_writes_the_version_and_the_next_tick_finds_it_current() -> None:
    """End to end through the registry: write once, then report `current` rather than re-writing."""
    backend = RecordingBackend()
    store = ObjectStore(backend)
    registration = LANE_REGISTRY[CALENDAR_STREAM]
    assert registration.watermark is not None

    first = await registration.watermark(None, store, today=TODAY)  # type: ignore[arg-type]
    assert first.day == TODAY
    result = await registration.adapter(None, store, day=first.day, run_id="test")  # type: ignore[arg-type]
    assert result.row_count > 0
    # THE DRIVER MARKS, NOT THE LANE. Calling the adapter directly skips `_finalize_written_day`, so
    # the version is on disk as an unfinished upload -- and `newest_covered_day` correctly refuses to
    # count it. Standing in for the driver here is what makes this "the next tick" rather than
    # "the next tick, if the previous one happened to survive".
    store.write_completion_marker(
        PartitionCompletion(
            part_count=result.part_count,
            row_count=result.row_count,
            completed_at=datetime(2026, 8, 22, tzinfo=UTC),
            run_id="test",
        ),
        layer=CALENDAR_STREAM,
        kind="observed",
        zoom=LANE_BASE_ZOOM_TIER,
        day=first.day,
    )

    second = await registration.watermark(None, store, today=TODAY)  # type: ignore[arg-type]
    assert second.day == TODAY, "the held version now satisfies the coverage requirement"
    assert "Newest version held 2026-08-22" in second.basis
