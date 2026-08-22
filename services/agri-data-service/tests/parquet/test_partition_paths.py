"""The frozen partition layout: exact rendering, round-tripping, and listing-only gap detection."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agri_data_service.foundation.parquet.paths import (
    MAX_GAP_WINDOW_DAYS,
    MAX_PART_INDEX,
    PARTITION_KINDS,
    PartitionKind,
    PartitionPathError,
    day_prefix,
    layer_prefix,
    missing_partition_days,
    month_prefix,
    parse_partition_path,
    partition_path,
    stream_prefix,
    try_parse_partition_path,
    validate_layer_slug,
    validate_partition_kind,
    year_prefix,
)

FIRST_JULY = date(2026, 7, 1)
LEAP_DAY = date(2024, 2, 29)


def test_partition_path_renders_the_frozen_layout_exactly() -> None:
    """Sixteen streams write against this string; it is pinned character for character."""
    assert (
        partition_path("fire-detections", "observed", date(2026, 7, 4))
        == "layer=fire-detections/kind=observed/year=2026/month=07/day=04/part-0.parquet"
    )
    assert (
        partition_path("signal", "forecast", date(2026, 12, 31), part_index=3)
        == "layer=signal/kind=forecast/year=2026/month=12/day=31/part-3.parquet"
    )


@pytest.mark.parametrize("kind", PARTITION_KINDS)
@pytest.mark.parametrize("day", [FIRST_JULY, LEAP_DAY, date(2022, 4, 30), date(1999, 1, 1)])
@pytest.mark.parametrize("part_index", [0, 1, MAX_PART_INDEX])
def test_partition_path_round_trips_through_its_parser(kind: PartitionKind, day: date, part_index: int) -> None:
    """Gap detection depends on the inverse, so build-parse-build must be a fixed point."""
    rendered = partition_path("vegetation", kind, day, part_index)
    parsed = parse_partition_path(rendered)

    assert parsed.layer == "vegetation"
    assert parsed.kind == kind
    assert parsed.day == day
    assert parsed.part_index == part_index
    assert parsed.key == rendered


def test_prefixes_compose_into_the_partition_path() -> None:
    """Each listing scope must be a literal prefix of the key, or narrowing a listing loses files."""
    full = partition_path("water-gauges", "observed", FIRST_JULY)

    assert full.startswith(layer_prefix("water-gauges"))
    assert full.startswith(stream_prefix("water-gauges", "observed"))
    assert full.startswith(year_prefix("water-gauges", "observed", 2026))
    assert full.startswith(month_prefix("water-gauges", "observed", 2026, 7))
    assert full.startswith(day_prefix("water-gauges", "observed", FIRST_JULY))
    assert day_prefix("water-gauges", "observed", FIRST_JULY).endswith("/")


def test_observed_and_forecast_are_disjoint_prefixes() -> None:
    """`kind` is a partition, not a column branch: no listing can blend the two streams."""
    observed = stream_prefix("sensors", "observed")
    forecast = stream_prefix("sensors", "forecast")

    assert observed != forecast
    assert not observed.startswith(forecast)
    assert not forecast.startswith(observed)


@pytest.mark.parametrize(
    "slug",
    ["Sensors", "fire_detections", "fire--detections", "-sensors", "sensors-", "", "a/b", "layer=x", ".."],
)
def test_layer_slug_validation_rejects_anything_that_could_escape_its_prefix(slug: str) -> None:
    """A lane writing outside its own prefix corrupts another agent's output and fails no other test."""
    with pytest.raises(PartitionPathError):
        validate_layer_slug(slug)


def test_layer_slug_validation_accepts_every_real_lane() -> None:
    lanes = (
        "soil-survey",
        "fire-detections",
        "vegetation",
        "burn-severity",
        "evacuation-zones",
        "interventions",
        "fire-perimeters",
        "water-gauges",
        "sensors",
        "weather-observations",
        "watersheds",
        "signal",
    )
    assert [validate_layer_slug(lane) for lane in lanes] == list(lanes)


def test_partition_kind_validation_is_closed() -> None:
    assert validate_partition_kind("observed") == "observed"
    assert validate_partition_kind("forecast") == "forecast"
    with pytest.raises(PartitionPathError):
        validate_partition_kind("actual")


@pytest.mark.parametrize(
    "path",
    [
        "layer=sensors/kind=observed/year=2026/month=7/day=04/part-0.parquet",
        "layer=sensors/kind=observed/year=26/month=07/day=04/part-0.parquet",
        "layer=sensors/year=2026/month=07/day=04/part-0.parquet",
        "layer=sensors/kind=actual/year=2026/month=07/day=04/part-0.parquet",
        "layer=Sensors/kind=observed/year=2026/month=07/day=04/part-0.parquet",
        "layer=sensors/kind=observed/year=2026/month=07/day=04/part-0.csv",
        "layer=sensors/kind=observed/year=2026/month=07/day=04/manifest.json",
        "layer=sensors/kind=observed/year=2026/month=13/day=04/part-0.parquet",
        "layer=sensors/kind=observed/year=2026/month=02/day=30/part-0.parquet",
        "warehouse/layer=sensors/kind=observed/year=2026/month=07/day=04/part-0.parquet",
        "",
    ],
)
def test_parser_rejects_keys_outside_the_layout(path: str) -> None:
    """A tolerant parser would let a malformed key read as a present day and hide a gap."""
    assert try_parse_partition_path(path) is None
    with pytest.raises(PartitionPathError):
        parse_partition_path(path)


def test_parser_normalises_windows_separators() -> None:
    """A local staging mirror on Windows is a real producer path; a backslash key must still parse."""
    windows_key = "layer=sensors\\kind=observed\\year=2026\\month=07\\day=04\\part-0.parquet"

    assert parse_partition_path(windows_key).day == date(2026, 7, 4)


def test_part_index_is_bounded() -> None:
    with pytest.raises(PartitionPathError):
        partition_path("sensors", "observed", FIRST_JULY, part_index=-1)
    with pytest.raises(PartitionPathError):
        partition_path("sensors", "observed", FIRST_JULY, part_index=MAX_PART_INDEX + 1)


def test_year_and_month_components_are_bounded() -> None:
    """`year=0999` renders happily and sorts wrong; the bound is what stops it."""
    with pytest.raises(PartitionPathError):
        year_prefix("sensors", "observed", 999)
    with pytest.raises(PartitionPathError):
        year_prefix("sensors", "observed", 10_000)
    with pytest.raises(PartitionPathError):
        month_prefix("sensors", "observed", 2026, 0)
    with pytest.raises(PartitionPathError):
        month_prefix("sensors", "observed", 2026, 13)


def test_missing_partition_days_reports_only_the_absent_days() -> None:
    keys = [
        partition_path("vegetation", "observed", date(2026, 7, 1)),
        partition_path("vegetation", "observed", date(2026, 7, 3)),
    ]

    missing = missing_partition_days(
        layer="vegetation",
        kind="observed",
        first_day=date(2026, 7, 1),
        last_day=date(2026, 7, 4),
        keys=keys,
    )

    assert missing == (date(2026, 7, 2), date(2026, 7, 4))


def test_missing_partition_days_ignores_other_layers_kinds_and_non_part_files() -> None:
    """A neighbouring lane's objects must never make this lane's gap disappear."""
    keys = [
        partition_path("sensors", "observed", date(2026, 7, 2)),
        partition_path("vegetation", "forecast", date(2026, 7, 2)),
        "layer=vegetation/kind=observed/year=2026/month=07/day=02/manifest.json",
    ]

    missing = missing_partition_days(
        layer="vegetation",
        kind="observed",
        first_day=date(2026, 7, 1),
        last_day=date(2026, 7, 2),
        keys=keys,
    )

    assert missing == (date(2026, 7, 1), date(2026, 7, 2))


def test_missing_partition_days_counts_a_multi_part_day_as_present() -> None:
    keys = [partition_path("signal", "observed", FIRST_JULY, part_index=index) for index in (1, 2)]

    missing = missing_partition_days(
        layer="signal",
        kind="observed",
        first_day=FIRST_JULY,
        last_day=FIRST_JULY,
        keys=keys,
    )

    assert missing == ()


def test_missing_partition_days_returns_the_whole_window_when_nothing_was_written() -> None:
    missing = missing_partition_days(
        layer="interventions",
        kind="observed",
        first_day=date(2026, 7, 1),
        last_day=date(2026, 7, 3),
        keys=(),
    )

    assert missing == (date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3))


def test_missing_partition_days_refuses_a_backwards_window() -> None:
    """A backwards window would return an empty tuple, which reads as 'fully covered'."""
    with pytest.raises(PartitionPathError):
        missing_partition_days(
            layer="signal",
            kind="observed",
            first_day=date(2026, 7, 4),
            last_day=date(2026, 7, 1),
            keys=(),
        )


def test_missing_partition_days_refuses_an_unbounded_window() -> None:
    with pytest.raises(PartitionPathError):
        missing_partition_days(
            layer="signal",
            kind="observed",
            first_day=date(1900, 1, 1),
            last_day=date(1900, 1, 1) + timedelta(days=MAX_GAP_WINDOW_DAYS),
            keys=(),
        )
