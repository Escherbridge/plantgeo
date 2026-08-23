"""The lane-nature vocabulary and the static-lookup watermark rule, exercised as pure functions.

Everything here is stdlib-pure: no store, no session, no clock. The driver's use of these rules is
pinned in `test_gap_fill.py`; what is pinned here is the rules themselves, because they are what
decides whether a reference set is "current" or owes a snapshot.
"""

from __future__ import annotations

from datetime import date

import pytest

from agri_data_service.foundation.parquet.lane_contract import (
    LANE_NATURES,
    LaneContractError,
    SourceWatermark,
    nature_has_time_axis,
    nature_permits_cadence,
    nature_permits_forecast,
    newest_covered_day,
    resolve_static_lane,
    validate_lane_nature,
)
from agri_data_service.foundation.parquet.paths import absence_marker_path, partition_path

TODAY = date(2026, 8, 22)
CHANGED_ON = date(2026, 8, 7)
EXPECTED_NATURE_COUNT = 3


def test_there_are_exactly_three_natures_and_each_validates() -> None:
    assert len(LANE_NATURES) == EXPECTED_NATURE_COUNT
    for nature in LANE_NATURES:
        assert validate_lane_nature(nature) == nature
    with pytest.raises(LaneContractError, match="must be one of"):
        validate_lane_nature("current_snapshot")


def test_only_a_static_lookup_lacks_a_time_axis_and_it_can_never_forecast() -> None:
    """The whole point of the three-way split: a version stamp is not an observation time."""
    assert nature_has_time_axis("daily_series")
    assert nature_has_time_axis("release_series")
    assert not nature_has_time_axis("static_lookup")
    assert nature_permits_forecast("daily_series")
    assert nature_permits_forecast("release_series")
    assert not nature_permits_forecast("static_lookup")


def test_only_a_release_series_has_a_cadence_to_step_over() -> None:
    assert nature_permits_cadence("release_series")
    assert not nature_permits_cadence("daily_series")
    assert not nature_permits_cadence("static_lookup")


def test_a_watermark_must_cite_what_produced_it() -> None:
    """An uncited version stamp reads as a measurement and cannot be re-derived."""
    with pytest.raises(LaneContractError, match="must cite the columns"):
        SourceWatermark(day=CHANGED_ON, basis="   ")


def test_a_version_at_or_after_the_watermark_is_current_and_owes_nothing() -> None:
    for held in (CHANGED_ON, TODAY):
        verdict = resolve_static_lane(
            watermark=SourceWatermark(day=CHANGED_ON, basis="max(updated_at)"),
            newest_covered_day=held,
            today=TODAY,
        )
        assert verdict.state == "current"
        assert verdict.version_day is None


def test_a_version_older_than_the_watermark_owes_one_snapshot_dated_at_the_watermark() -> None:
    """Dated at the WATERMARK, never at the run date -- that is what makes the day meaningful."""
    verdict = resolve_static_lane(
        watermark=SourceWatermark(day=CHANGED_ON, basis="max(updated_at)"),
        newest_covered_day=date(2026, 7, 1),
        today=TODAY,
    )

    assert verdict.state == "stale"
    assert verdict.version_day == CHANGED_ON


def test_a_lane_that_has_never_written_owes_its_first_version() -> None:
    verdict = resolve_static_lane(
        watermark=SourceWatermark(day=CHANGED_ON, basis="max(updated_at)"),
        newest_covered_day=None,
        today=TODAY,
    )

    assert verdict.state == "stale"
    assert verdict.version_day == CHANGED_ON


def test_an_empty_source_is_reported_as_empty_rather_than_as_a_day_owed() -> None:
    verdict = resolve_static_lane(
        watermark=SourceWatermark(day=None, basis="max(updated_at)=null"),
        newest_covered_day=None,
        today=TODAY,
    )

    assert verdict.state == "source_empty"
    assert verdict.version_day is None


def test_not_reading_the_watermark_is_its_own_state_not_zero_gaps() -> None:
    """"Nobody looked" and "the source says we are current" both show no missing days. They differ."""
    verdict = resolve_static_lane(watermark=None, newest_covered_day=CHANGED_ON, today=TODAY)

    assert verdict.state == "watermark_unread"
    assert verdict.version_day is None
    assert "UNKNOWN" in verdict.detail


def test_a_watermark_after_today_is_a_clock_disagreement_and_is_refused() -> None:
    """Writing an observed partition dated in the future is never right, whatever the source claims."""
    with pytest.raises(LaneContractError, match="later than"):
        resolve_static_lane(
            watermark=SourceWatermark(day=date(2026, 8, 23), basis="max(updated_at)"),
            newest_covered_day=None,
            today=TODAY,
        )


def test_the_newest_covered_day_counts_part_files_and_absence_markers_alike() -> None:
    """A governed absence at a version day means the source was asked at that version; that is coverage."""
    keys = [
        partition_path("watersheds", "observed", date(2026, 8, 1)),
        partition_path("watersheds", "observed", date(2026, 8, 1), part_index=3),
        absence_marker_path("watersheds", "observed", CHANGED_ON),
        # Neither this lane nor this stream: both must be ignored, or one lane's coverage would be
        # answered from another's objects.
        partition_path("soil-survey", "observed", TODAY),
        partition_path("watersheds", "forecast", TODAY),
        "layer=watersheds/kind=observed/not-a-partition.txt",
    ]

    assert newest_covered_day(layer="watersheds", kind="observed", keys=keys) == CHANGED_ON
    assert newest_covered_day(layer="watersheds", kind="observed", keys=[]) is None
