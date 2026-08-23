"""The lane-nature vocabulary and the static-lookup watermark rule, exercised as pure functions.

Everything here is stdlib-pure: no store, no session, no clock. The driver's use of these rules is
pinned in `test_gap_fill.py`; what is pinned here is the rules themselves, because they are what
decides whether a reference set is "current" or owes a snapshot.

The sub-day cases below are named for `evacuation-zones` on purpose: that lane is the one where the
day-resolution answer was a life-safety defect rather than a staleness annoyance.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from agri_data_service.foundation.parquet.lane_contract import (
    LANE_NATURES,
    LaneContractError,
    SourceWatermark,
    nature_has_time_axis,
    nature_permits_cadence,
    nature_permits_forecast,
    newest_covered_day,
    newest_data_day,
    newest_marker_day,
    resolve_static_lane,
    validate_lane_nature,
)
from agri_data_service.foundation.parquet.paths import absence_marker_path, partition_path

if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.zoom import ZoomTier

TODAY = date(2026, 8, 22)
CHANGED_ON = date(2026, 8, 7)
EXPECTED_NATURE_COUNT = 3

# The tier a lane's own export writes, and the coarsest rung derived from it. Named by minimum zoom,
# as the ladder is: every "newest day" question below is asked of ONE of them.
BASE_TIER: ZoomTier = 13
WHOLE_WORLD_TIER: ZoomTier = 0

# Two instants on ONE UTC day: an Oregon OEM zone that was Be Ready in the morning and Go Now that
# afternoon. The day cannot tell them apart, which is exactly why the watermark carries the instant.
BE_READY_AT = datetime(2026, 8, 7, 9, 15, tzinfo=UTC)
GO_NOW_AT = datetime(2026, 8, 7, 16, 40, tzinfo=UTC)
EVACUATION_BASIS = "evacuation-zones: GREATEST(feature_updated_at) over 412 published rows"


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


def test_a_version_after_the_watermark_is_current_on_the_day_alone() -> None:
    """A LATER version day supersedes the watermark outright; no instant is consulted to say so."""
    verdict = resolve_static_lane(
        watermark=SourceWatermark(day=CHANGED_ON, basis="max(updated_at)"),
        newest_data_day=TODAY,
        newest_data_instant=None,
        newest_marker_day=None,
        today=TODAY,
    )

    assert verdict.state == "current"
    assert verdict.version_day is None
    # WHICH resolution answered, not merely that the answer was `current`. This case never reaches
    # `_resolve_watermark_day`, so a day-resolution caveat here would mean the branching had moved.
    assert "later than the source watermark" in verdict.detail
    assert "DAY-RESOLUTION" not in verdict.detail


def test_a_version_on_the_watermark_day_with_no_instant_is_current_only_at_day_resolution() -> None:
    """Equal days cannot decide themselves, so this is the fallback -- and it must admit that it is.

    Before the watermark carried an instant this case was indistinguishable from the one above. It
    now answers through `_resolve_watermark_day`, and asserting only `current` would let the whole
    sub-day comparison be deleted without a test noticing.
    """
    verdict = resolve_static_lane(
        watermark=SourceWatermark(day=CHANGED_ON, basis="max(updated_at)"),
        newest_data_day=CHANGED_ON,
        newest_data_instant=None,
        newest_marker_day=None,
        today=TODAY,
    )

    assert verdict.state == "current"
    assert verdict.version_day is None
    assert "DAY-RESOLUTION" in verdict.detail
    assert "the source reported no change instant" in verdict.detail


def test_a_version_older_than_the_watermark_owes_one_snapshot_dated_at_the_watermark() -> None:
    """Dated at the WATERMARK, never at the run date -- that is what makes the day meaningful."""
    verdict = resolve_static_lane(
        watermark=SourceWatermark(day=CHANGED_ON, basis="max(updated_at)"),
        newest_data_day=date(2026, 7, 1),
        newest_data_instant=None,
        newest_marker_day=None,
        today=TODAY,
    )

    assert verdict.state == "stale"
    assert verdict.version_day == CHANGED_ON


def test_a_lane_that_has_never_written_owes_its_first_version() -> None:
    verdict = resolve_static_lane(
        watermark=SourceWatermark(day=CHANGED_ON, basis="max(updated_at)"),
        newest_data_day=None,
        newest_data_instant=None,
        newest_marker_day=None,
        today=TODAY,
    )

    assert verdict.state == "stale"
    assert verdict.version_day == CHANGED_ON


def test_an_empty_source_is_reported_as_empty_rather_than_as_a_day_owed() -> None:
    verdict = resolve_static_lane(
        watermark=SourceWatermark(day=None, basis="max(updated_at)=null"),
        newest_data_day=None,
        newest_data_instant=None,
        newest_marker_day=None,
        today=TODAY,
    )

    assert verdict.state == "source_empty"
    assert verdict.version_day is None


def test_not_reading_the_watermark_is_its_own_state_not_zero_gaps() -> None:
    """ "Nobody looked" and "the source says we are current" both show no missing days. They differ."""
    verdict = resolve_static_lane(
        watermark=None,
        newest_data_day=CHANGED_ON,
        newest_data_instant=None,
        newest_marker_day=None,
        today=TODAY,
    )

    assert verdict.state == "watermark_unread"
    assert verdict.version_day is None
    assert "UNKNOWN" in verdict.detail


def test_a_watermark_after_today_is_a_clock_disagreement_and_is_refused() -> None:
    """Writing an observed partition dated in the future is never right, whatever the source claims."""
    with pytest.raises(LaneContractError, match="later than"):
        resolve_static_lane(
            watermark=SourceWatermark(day=date(2026, 8, 23), basis="max(updated_at)"),
            newest_data_day=None,
            newest_data_instant=None,
            newest_marker_day=None,
            today=TODAY,
        )


def test_a_source_change_later_the_same_day_makes_the_held_version_stale() -> None:
    """EVACUATION ZONES: an Oregon OEM level moves Be Ready -> Go Now within hours of one UTC day.

    A snapshot exported at 09:15 cannot hold a change the source made at 16:40, however current its
    version stamp looks. Comparing days alone answered `current` here and served a stale evacuation
    level until UTC midnight -- the defect this rule closes.
    """
    verdict = resolve_static_lane(
        watermark=SourceWatermark(day=CHANGED_ON, instant=GO_NOW_AT, basis=EVACUATION_BASIS),
        newest_data_day=CHANGED_ON,
        newest_data_instant=BE_READY_AT,
        newest_marker_day=None,
        today=TODAY,
    )

    assert verdict.state == "stale"
    # THE SAME DAY, re-exported. A static lane re-exports its whole population, so correcting a
    # version means overwriting it -- the partition layout does not grow a second axis.
    assert verdict.version_day == CHANGED_ON
    assert "BEFORE" in verdict.detail


def test_an_export_at_or_after_the_source_change_captured_it_and_is_current() -> None:
    """An export READS the source at write time, so writing after the change means holding it."""
    for exported_at in (GO_NOW_AT, GO_NOW_AT + timedelta(minutes=1)):
        verdict = resolve_static_lane(
            watermark=SourceWatermark(day=CHANGED_ON, instant=GO_NOW_AT, basis=EVACUATION_BASIS),
            newest_data_day=CHANGED_ON,
            newest_data_instant=exported_at,
            newest_marker_day=None,
            today=TODAY,
        )

        assert verdict.state == "current"
        assert verdict.version_day is None


def test_an_unknown_instant_falls_back_to_day_resolution_and_says_so_in_the_verdict() -> None:
    """A lane that re-exported on every tick forever would be a worse bug than the one being fixed.

    The calendar dimension is pure computation and has no source instant to offer; a backend may
    equally be unable to report an export instant. Both settle at the day, and BOTH say in `detail`
    that the answer was day-resolution, because reporting certainty nobody has is this module's one
    unforgivable move.
    """
    no_source_instant = resolve_static_lane(
        watermark=SourceWatermark(day=CHANGED_ON, basis="calendar: pure computation, no source system"),
        newest_data_day=CHANGED_ON,
        newest_data_instant=BE_READY_AT,
        newest_marker_day=None,
        today=TODAY,
    )
    no_export_instant = resolve_static_lane(
        watermark=SourceWatermark(day=CHANGED_ON, instant=GO_NOW_AT, basis=EVACUATION_BASIS),
        newest_data_day=CHANGED_ON,
        newest_data_instant=None,
        newest_marker_day=None,
        today=TODAY,
    )

    for verdict in (no_source_instant, no_export_instant):
        assert verdict.state == "current"
        assert verdict.version_day is None
        assert "DAY-RESOLUTION" in verdict.detail
    assert "the source reported no change instant" in no_source_instant.detail
    assert "no export instant" in no_export_instant.detail


def test_a_later_version_day_is_current_without_consulting_either_instant() -> None:
    """The day IS the version stamp, so a later version supersedes whatever the two clocks report."""
    verdict = resolve_static_lane(
        watermark=SourceWatermark(day=CHANGED_ON, instant=GO_NOW_AT, basis=EVACUATION_BASIS),
        newest_data_day=date(2026, 8, 20),
        newest_data_instant=BE_READY_AT,
        newest_marker_day=None,
        today=TODAY,
    )

    assert verdict.state == "current"
    assert "later than the source watermark" in verdict.detail


def test_a_timezone_naive_instant_is_refused_on_both_sides_of_the_comparison() -> None:
    """Assuming a zone for a version stamp shifts it by up to a day, which is the error being fixed."""
    with pytest.raises(LaneContractError, match="timezone-naive"):
        SourceWatermark(day=CHANGED_ON, instant=datetime(2026, 8, 7, 16, 40), basis=EVACUATION_BASIS)  # noqa: DTZ001
    with pytest.raises(LaneContractError, match="timezone-naive"):
        resolve_static_lane(
            watermark=SourceWatermark(day=CHANGED_ON, instant=GO_NOW_AT, basis=EVACUATION_BASIS),
            newest_data_day=CHANGED_ON,
            newest_data_instant=datetime(2026, 8, 7, 9, 15),  # noqa: DTZ001
            newest_marker_day=None,
            today=TODAY,
        )


def test_an_empty_source_cannot_carry_a_change_instant() -> None:
    """No published rows means nothing changed; a day-less watermark holding an instant is incoherent."""
    with pytest.raises(LaneContractError, match="nothing that changed"):
        SourceWatermark(day=None, instant=GO_NOW_AT, basis="max(updated_at)=null")


def test_a_governed_absence_at_the_watermark_is_a_failed_read_and_never_latches_to_current() -> None:
    """THE ABSENCE LATCH: one zero-row export used to freeze a static lane at `current` forever."""
    keys = [absence_marker_path("watersheds", "observed", BASE_TIER, CHANGED_ON)]
    watermark = SourceWatermark(day=CHANGED_ON, basis="watersheds: GREATEST(feature_updated_at) over 9396 rows")

    verdict = resolve_static_lane(
        watermark=watermark,
        newest_data_day=newest_data_day(layer="watersheds", kind="observed", zoom=BASE_TIER, keys=keys),
        newest_data_instant=None,
        newest_marker_day=newest_marker_day(layer="watersheds", kind="observed", zoom=BASE_TIER, keys=keys),
        today=TODAY,
    )

    # A static watermark is meant to sit still, so `current` here would never be revisited.
    assert verdict.state == "stale"
    assert verdict.version_day == CHANGED_ON
    assert "failed read" in verdict.detail
    # The merged view is precisely the trap: it cannot tell the two claims apart.
    assert newest_covered_day(layer="watersheds", kind="observed", zoom=BASE_TIER, keys=keys) == CHANGED_ON


def test_a_marker_beside_an_up_to_date_part_file_still_reads_as_current() -> None:
    """Refusing markers as coverage must not make a genuinely current reference set look stale."""
    keys = [
        absence_marker_path("watersheds", "observed", BASE_TIER, date(2026, 7, 1)),
        partition_path("watersheds", "observed", BASE_TIER, CHANGED_ON),
    ]

    verdict = resolve_static_lane(
        watermark=SourceWatermark(day=CHANGED_ON, basis="max(updated_at)"),
        newest_data_day=newest_data_day(layer="watersheds", kind="observed", zoom=BASE_TIER, keys=keys),
        newest_data_instant=None,
        newest_marker_day=newest_marker_day(layer="watersheds", kind="observed", zoom=BASE_TIER, keys=keys),
        today=TODAY,
    )

    assert verdict.state == "current"
    assert verdict.version_day is None
    # No instant on either side, so this is the day-resolution arm; naming it keeps the test from
    # passing for a reason it never meant to assert.
    assert "DAY-RESOLUTION" in verdict.detail

    # And the same asymmetry at INSTANT resolution: the marker still must not drag a captured export
    # to stale, now decided by the two clocks rather than by the fallback.
    at_instant_resolution = resolve_static_lane(
        watermark=SourceWatermark(day=CHANGED_ON, instant=GO_NOW_AT, basis=EVACUATION_BASIS),
        newest_data_day=newest_data_day(layer="watersheds", kind="observed", zoom=BASE_TIER, keys=keys),
        newest_data_instant=GO_NOW_AT,
        newest_marker_day=newest_marker_day(layer="watersheds", kind="observed", zoom=BASE_TIER, keys=keys),
        today=TODAY,
    )

    assert at_instant_resolution.state == "current"
    assert "DAY-RESOLUTION" not in at_instant_resolution.detail


def test_an_empty_source_stays_empty_rather_than_stale_when_a_marker_covers_it() -> None:
    """Marker-only coverage may read as settled ONLY where the watermark itself has no day."""
    verdict = resolve_static_lane(
        watermark=SourceWatermark(day=None, basis="max(updated_at)=null"),
        newest_data_day=None,
        newest_data_instant=None,
        newest_marker_day=CHANGED_ON,
        today=TODAY,
    )

    assert verdict.state == "source_empty"
    assert verdict.version_day is None


def test_the_two_coverage_kinds_are_reported_apart_from_the_same_listing() -> None:
    """A part file and a marker make opposite claims about a version stamp, so they scan apart."""
    keys = [
        partition_path("watersheds", "observed", BASE_TIER, date(2026, 8, 1)),
        absence_marker_path("watersheds", "observed", BASE_TIER, CHANGED_ON),
    ]

    assert newest_data_day(layer="watersheds", kind="observed", zoom=BASE_TIER, keys=keys) == date(2026, 8, 1)
    assert newest_marker_day(layer="watersheds", kind="observed", zoom=BASE_TIER, keys=keys) == CHANGED_ON
    assert newest_data_day(layer="watersheds", kind="observed", zoom=BASE_TIER, keys=[]) is None
    assert newest_marker_day(layer="watersheds", kind="observed", zoom=BASE_TIER, keys=[]) is None


def test_the_newest_covered_day_counts_part_files_and_absence_markers_alike() -> None:
    """A governed absence at a version day means the source was asked at that version; that is coverage."""
    keys = [
        partition_path("watersheds", "observed", BASE_TIER, date(2026, 8, 1)),
        partition_path("watersheds", "observed", BASE_TIER, date(2026, 8, 1), part_index=3),
        absence_marker_path("watersheds", "observed", BASE_TIER, CHANGED_ON),
        # Neither this lane, nor this stream, nor this TIER: all three must be ignored, or one lane's
        # coverage would be answered from another's objects.
        partition_path("soil-survey", "observed", BASE_TIER, TODAY),
        partition_path("watersheds", "forecast", BASE_TIER, TODAY),
        partition_path("watersheds", "observed", WHOLE_WORLD_TIER, TODAY),
        "layer=watersheds/kind=observed/not-a-partition.txt",
    ]

    assert newest_covered_day(layer="watersheds", kind="observed", zoom=BASE_TIER, keys=keys) == CHANGED_ON
    assert newest_covered_day(layer="watersheds", kind="observed", zoom=BASE_TIER, keys=[]) is None


def test_a_coarse_tier_snapshot_never_answers_for_the_tier_below_it() -> None:
    """THE BLENDING TRAP, at its smallest: a z0 version says nothing about whether z13 was written.

    Every key here is the same lane, the same stream and the same version day -- only the tier
    differs. Tier-blind, all three questions would answer `CHANGED_ON` for the base tier and a lane
    whose most detailed rung has never been written would be reported current, which is precisely the
    reading that lets a real gap go unfilled forever.
    """
    keys = [
        partition_path("watersheds", "observed", WHOLE_WORLD_TIER, CHANGED_ON),
        absence_marker_path("watersheds", "observed", WHOLE_WORLD_TIER, CHANGED_ON),
    ]

    assert newest_data_day(layer="watersheds", kind="observed", zoom=BASE_TIER, keys=keys) is None
    assert newest_marker_day(layer="watersheds", kind="observed", zoom=BASE_TIER, keys=keys) is None
    assert newest_covered_day(layer="watersheds", kind="observed", zoom=BASE_TIER, keys=keys) is None
    # And the coarse tier still answers for ITSELF, so this is scoping rather than blanket refusal.
    assert newest_covered_day(layer="watersheds", kind="observed", zoom=WHOLE_WORLD_TIER, keys=keys) == CHANGED_ON
