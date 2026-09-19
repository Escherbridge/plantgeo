"""Behaviour of the object-key grammar: what it builds, what it refuses, and how a day is classified."""

from __future__ import annotations

from datetime import date

import pytest

from plantgeo_ml_service.foundation.parquet_paths import (
    BASE_PARTITION_ZOOM,
    ZOOM_TIERS,
    PartitionPathError,
    ZoomTierError,
    absence_marker_path,
    availability_generation_path,
    availability_lane_root,
    availability_pointer_path,
    classify_partition_day,
    completion_marker_path,
    derived_empty_completion_marker_path,
    parse_partition_path,
    partition_path,
    promotion_receipt_path,
    serving_zoom_tier,
    tier_day_objects,
    try_parse_partition_path,
    validate_layer_slug,
    validate_zoom_tier,
)

DAY = date(2026, 9, 19)
SHA = "a" * 64


def test_a_partition_key_carries_every_axis_in_the_frozen_order() -> None:
    key = partition_path("signal", "forecast", 13, DAY, 7)

    assert key == "layer=signal/kind=forecast/zoom=13/year=2026/month=09/day=19/part-7.parquet"


def test_a_key_round_trips_through_its_parser() -> None:
    parsed = parse_partition_path(partition_path("fire-detections", "observed", 5, DAY, 12))

    assert (parsed.layer, parsed.kind, parsed.zoom, parsed.day, parsed.part_index) == (
        "fire-detections",
        "observed",
        5,
        DAY,
        12,
    )
    assert parsed.key == partition_path("fire-detections", "observed", 5, DAY, 12)


@pytest.mark.parametrize("slug", ["Signal", "fire_detections", "fire--detections", "-signal", "signal-", ""])
def test_a_slug_outside_the_convention_is_refused(slug: str) -> None:
    with pytest.raises(PartitionPathError):
        validate_layer_slug(slug)


@pytest.mark.parametrize("zoom", [1, 7, 12, 14, -1])
def test_a_rung_off_the_ladder_is_refused_rather_than_written(zoom: int) -> None:
    with pytest.raises(ZoomTierError):
        validate_zoom_tier(zoom)


def test_a_part_index_past_the_ceiling_is_refused() -> None:
    with pytest.raises(PartitionPathError):
        partition_path("signal", "observed", 13, DAY, 10_000)


@pytest.mark.parametrize(("requested", "expected"), [(0, 0), (4, 0), (5, 5), (8, 5), (9, 9), (12, 9), (22, 13)])
def test_a_request_between_rungs_is_served_by_the_rung_below_it(requested: int, expected: int) -> None:
    assert serving_zoom_tier(requested) == expected


@pytest.mark.parametrize("requested", [-1, 23])
def test_a_zoom_off_the_web_map_scale_is_refused(requested: int) -> None:
    with pytest.raises(ZoomTierError):
        serving_zoom_tier(requested)


def test_a_key_of_another_shape_parses_as_none_rather_than_raising() -> None:
    assert try_parse_partition_path("layer=signal/kind=observed/zoom=13/year=2026/month=09/day=19/absent.json") is None
    assert try_parse_partition_path("not-a-key") is None


def test_an_impossible_calendar_day_does_not_parse() -> None:
    assert (
        try_parse_partition_path("layer=signal/kind=observed/zoom=13/year=2025/month=02/day=30/part-0.parquet") is None
    )


def test_the_two_kinds_of_one_layer_have_separate_availability_roots() -> None:
    """FR-4a: the observed promotion lane and this service's forecast publisher share no pointer."""
    forecast_root = availability_lane_root("vegetation", "forecast")
    observed_root = availability_lane_root("vegetation", "observed")

    assert forecast_root != observed_root
    assert availability_pointer_path("vegetation", "forecast") != availability_pointer_path("vegetation", "observed")
    assert availability_generation_path("vegetation", "forecast", SHA).startswith(forecast_root)


def test_a_generation_key_refuses_anything_that_is_not_a_content_digest() -> None:
    with pytest.raises(PartitionPathError):
        availability_generation_path("vegetation", "forecast", "not-a-digest")


def test_the_promotion_receipt_is_zoom_independent() -> None:
    """It gates a governed-plane register verb, not one rendered rung, so it carries no `zoom=`."""
    key = promotion_receipt_path("vegetation", "observed", DAY)

    assert "zoom=" not in key
    assert key.endswith("/promotion-receipt.json")


def _day_objects(keys: list[str], *, zoom: int) -> object:
    return tier_day_objects(keys, layer="signal", kind="observed", zoom=zoom)  # type: ignore[arg-type]


def test_a_day_with_parts_and_a_marker_is_data() -> None:
    keys = [
        partition_path("signal", "observed", 13, DAY, 0),
        completion_marker_path("signal", "observed", 13, DAY),
    ]

    assert classify_partition_day(DAY, _day_objects(keys, zoom=13), zoom=13) == "data"  # type: ignore[arg-type]


def test_a_day_with_parts_but_no_marker_is_incomplete_not_data() -> None:
    """A run killed part way leaves a prefix of the parts; completion is asserted, never inferred."""
    keys = [partition_path("signal", "observed", 13, DAY, 0)]

    assert classify_partition_day(DAY, _day_objects(keys, zoom=13), zoom=13) == "incomplete"  # type: ignore[arg-type]


def test_a_day_holding_both_a_release_and_an_absence_is_a_conflict() -> None:
    keys = [
        partition_path("signal", "observed", 13, DAY, 0),
        completion_marker_path("signal", "observed", 13, DAY),
        absence_marker_path("signal", "observed", 13, DAY),
    ]

    assert classify_partition_day(DAY, _day_objects(keys, zoom=13), zoom=13) == "conflict"  # type: ignore[arg-type]


def test_a_derived_empty_rung_is_data_above_the_base_and_incomplete_at_it() -> None:
    """Only a rung DERIVED from a non-empty base can honestly say its rows all generalised away."""
    coarse_keys = [derived_empty_completion_marker_path("signal", "observed", 5, DAY)]
    base_keys = [derived_empty_completion_marker_path("signal", "observed", BASE_PARTITION_ZOOM, DAY)]

    assert classify_partition_day(DAY, _day_objects(coarse_keys, zoom=5), zoom=5) == "data"  # type: ignore[arg-type]
    assert (
        classify_partition_day(DAY, _day_objects(base_keys, zoom=BASE_PARTITION_ZOOM), zoom=BASE_PARTITION_ZOOM)  # type: ignore[arg-type]
        == "incomplete"
    )


def test_a_day_nothing_mentions_is_missing() -> None:
    assert classify_partition_day(DAY, _day_objects([], zoom=13), zoom=13) == "missing"  # type: ignore[arg-type]


def test_keys_of_another_tier_are_ignored_rather_than_counted() -> None:
    keys = [partition_path("signal", "observed", 9, DAY, 0), completion_marker_path("signal", "observed", 9, DAY)]

    assert classify_partition_day(DAY, _day_objects(keys, zoom=13), zoom=13) == "missing"  # type: ignore[arg-type]


def test_the_ladder_is_the_four_published_tiers() -> None:
    assert ZOOM_TIERS == (0, 5, 9, 13)
    assert ZOOM_TIERS[-1] == BASE_PARTITION_ZOOM
