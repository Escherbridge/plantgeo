"""The partition layout: exact rendering, round-tripping, and listing-only gap detection per zoom tier."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import (
    MAX_GAP_WINDOW_DAYS,
    MAX_PART_INDEX,
    PARTITION_KINDS,
    PartitionKind,
    PartitionPathError,
    absence_marker_path,
    completion_marker_path,
    day_prefix,
    layer_prefix,
    missing_partition_days,
    month_prefix,
    parse_partition_path,
    partition_day_statuses,
    partition_path,
    stream_prefix,
    try_parse_absence_marker_path,
    try_parse_partition_path,
    validate_layer_slug,
    validate_partition_kind,
    year_prefix,
    zoom_prefix,
)
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS, ZoomTier, ZoomTierError

FIRST_JULY = date(2026, 7, 1)
LEAP_DAY = date(2024, 2, 29)

# Named by minimum zoom, as the ladder itself is: an adjective would stop being true if a rung moved.
TIER_Z0: ZoomTier = 0
TIER_Z5: ZoomTier = 5
TIER_Z9: ZoomTier = 9
TIER_Z13: ZoomTier = 13


def _completion_marker(layer: str, kind: str, zoom: ZoomTier, day: date) -> tuple[str, bytes]:
    """Build a completion marker key and payload for test fixtures that need to mark a day finished."""
    marker = PartitionCompletion(
        part_count=1, row_count=1, completed_at=datetime(2026, 8, 22, tzinfo=UTC), run_id="test"
    )
    return completion_marker_path(layer, kind, zoom, day), marker.to_json_bytes()


def test_partition_path_renders_the_layout_exactly() -> None:
    """Sixteen streams write against this string; it is pinned character for character."""
    assert (
        partition_path("soil-survey", "observed", 13, date(2026, 8, 23))
        == "layer=soil-survey/kind=observed/zoom=13/year=2026/month=08/day=23/part-0.parquet"
    )
    assert (
        partition_path("signal", "forecast", 5, date(2026, 12, 31), part_index=3)
        == "layer=signal/kind=forecast/zoom=05/year=2026/month=12/day=31/part-3.parquet"
    )


def test_absence_marker_renders_beside_the_part_file_of_the_same_tier() -> None:
    """The marker is the second and last object kind; it shares the day directory it governs."""
    assert (
        absence_marker_path("soil-survey", "observed", 13, date(2026, 8, 23))
        == "layer=soil-survey/kind=observed/zoom=13/year=2026/month=08/day=23/absent.json"
    )


def test_zoom_segment_is_zero_padded_so_a_listing_walks_the_ladder_in_order() -> None:
    """Unpadded, `zoom=13` sorts between `zoom=0` and `zoom=5` and a tier walk silently runs out of order."""
    rendered = [zoom_prefix("vegetation", "observed", tier) for tier in ZOOM_TIERS]

    assert rendered == sorted(rendered)
    assert rendered[0] == "layer=vegetation/kind=observed/zoom=00/"


@pytest.mark.parametrize("kind", PARTITION_KINDS)
@pytest.mark.parametrize("zoom", ZOOM_TIERS)
@pytest.mark.parametrize("day", [FIRST_JULY, LEAP_DAY, date(2022, 4, 30), date(1999, 1, 1)])
@pytest.mark.parametrize("part_index", [0, 1, MAX_PART_INDEX])
def test_partition_path_round_trips_through_its_parser(
    kind: PartitionKind,
    zoom: ZoomTier,
    day: date,
    part_index: int,
) -> None:
    """Gap detection depends on the inverse, so build-parse-build must be a fixed point."""
    rendered = partition_path("vegetation", kind, zoom, day, part_index)
    parsed = parse_partition_path(rendered)

    assert parsed.layer == "vegetation"
    assert parsed.kind == kind
    assert parsed.zoom == zoom
    assert parsed.day == day
    assert parsed.part_index == part_index
    assert parsed.key == rendered


@pytest.mark.parametrize("zoom", ZOOM_TIERS)
def test_absence_marker_path_round_trips_through_its_parser(zoom: ZoomTier) -> None:
    rendered = absence_marker_path("drought", "observed", zoom, FIRST_JULY)
    parsed = try_parse_absence_marker_path(rendered)

    assert parsed is not None
    assert parsed.zoom == zoom
    assert parsed.day == FIRST_JULY
    assert parsed.key == rendered


def test_prefixes_compose_into_the_partition_path() -> None:
    """Each listing scope must be a literal prefix of the key, or narrowing a listing loses files."""
    full = partition_path("water-gauges", "observed", TIER_Z13, FIRST_JULY)

    assert full.startswith(layer_prefix("water-gauges"))
    assert full.startswith(stream_prefix("water-gauges", "observed"))
    assert full.startswith(zoom_prefix("water-gauges", "observed", TIER_Z13))
    assert full.startswith(year_prefix("water-gauges", "observed", TIER_Z13, 2026))
    assert full.startswith(month_prefix("water-gauges", "observed", TIER_Z13, 2026, 7))
    assert full.startswith(day_prefix("water-gauges", "observed", TIER_Z13, FIRST_JULY))
    assert day_prefix("water-gauges", "observed", TIER_Z13, FIRST_JULY).endswith("/")


def test_zoom_sits_above_year_so_one_listing_covers_a_whole_tier() -> None:
    """Pruning a tier by directory is the entire reason the axis is above the date components."""
    tier_scope = zoom_prefix("signal", "observed", 9)

    assert tier_scope == "layer=signal/kind=observed/zoom=09/"
    assert year_prefix("signal", "observed", 9, 2026).startswith(tier_scope)
    assert year_prefix("signal", "observed", 9, 2019).startswith(tier_scope)
    assert not year_prefix("signal", "observed", 13, 2026).startswith(tier_scope)


def test_zoom_tiers_are_disjoint_prefixes() -> None:
    """No listing of one tier may reach another, or a serving read blends two resolutions."""
    scopes = [zoom_prefix("sensors", "observed", tier) for tier in ZOOM_TIERS]

    assert len(set(scopes)) == len(ZOOM_TIERS)
    assert not any(one.startswith(other) for one in scopes for other in scopes if one != other)


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
        "layer=sensors/kind=observed/year=2026/month=07/day=04/part-0.parquet",
        "layer=sensors/kind=observed/zoom=13/month=07/day=04/part-0.parquet",
        "layer=sensors/kind=observed/zoom=7/year=2026/month=07/day=04/part-0.parquet",
        "layer=sensors/kind=observed/zoom=07/year=2026/month=07/day=04/part-0.parquet",
        "layer=sensors/kind=observed/zoom=13/year=2026/month=7/day=04/part-0.parquet",
        "layer=sensors/kind=observed/zoom=13/year=26/month=07/day=04/part-0.parquet",
        "layer=sensors/zoom=13/year=2026/month=07/day=04/part-0.parquet",
        "layer=sensors/kind=actual/zoom=13/year=2026/month=07/day=04/part-0.parquet",
        "layer=Sensors/kind=observed/zoom=13/year=2026/month=07/day=04/part-0.parquet",
        "layer=sensors/kind=observed/zoom=13/year=2026/month=07/day=04/part-0.csv",
        "layer=sensors/kind=observed/zoom=13/year=2026/month=07/day=04/manifest.json",
        "layer=sensors/kind=observed/zoom=13/year=2026/month=13/day=04/part-0.parquet",
        "layer=sensors/kind=observed/zoom=13/year=2026/month=02/day=30/part-0.parquet",
        "layer=sensors/kind=observed/year=2026/zoom=13/month=07/day=04/part-0.parquet",
        "warehouse/layer=sensors/kind=observed/zoom=13/year=2026/month=07/day=04/part-0.parquet",
        "",
    ],
)
def test_parser_rejects_keys_outside_the_layout(path: str) -> None:
    """A tolerant parser would let a malformed key read as a present day and hide a gap."""
    assert try_parse_partition_path(path) is None
    with pytest.raises(PartitionPathError):
        parse_partition_path(path)


def test_a_key_written_before_the_zoom_axis_does_not_parse() -> None:
    """The pre-zoom objects are discarded, not migrated; parsing one would report a covered day."""
    legacy_part = "layer=soil-survey/kind=observed/year=2026/month=08/day=23/part-0.parquet"
    legacy_marker = "layer=soil-survey/kind=observed/year=2026/month=08/day=23/absent.json"

    assert try_parse_partition_path(legacy_part) is None
    assert try_parse_absence_marker_path(legacy_marker) is None


@pytest.mark.parametrize("segment", ["01", "04", "07", "12", "14", "22", "99"])
def test_parser_rejects_a_zoom_that_is_not_a_published_tier(segment: str) -> None:
    """An off-ladder key belongs to no tier; counting it would cover a gap in the tier that owns the day."""
    day_scope = f"layer=signal/kind=observed/zoom={segment}/year=2026/month=07/day=04"

    assert try_parse_partition_path(f"{day_scope}/part-0.parquet") is None
    assert try_parse_absence_marker_path(f"{day_scope}/absent.json") is None


def test_parser_normalises_windows_separators() -> None:
    """A local staging mirror on Windows is a real producer path; a backslash key must still parse."""
    windows_key = "layer=sensors\\kind=observed\\zoom=09\\year=2026\\month=07\\day=04\\part-0.parquet"
    parsed = parse_partition_path(windows_key)

    assert parsed.day == date(2026, 7, 4)
    assert parsed.zoom == TIER_Z9


def test_part_index_is_bounded() -> None:
    with pytest.raises(PartitionPathError):
        partition_path("sensors", "observed", TIER_Z13, FIRST_JULY, part_index=-1)
    with pytest.raises(PartitionPathError):
        partition_path("sensors", "observed", TIER_Z13, FIRST_JULY, part_index=MAX_PART_INDEX + 1)


def test_year_and_month_components_are_bounded() -> None:
    """`year=0999` renders happily and sorts wrong; the bound is what stops it."""
    with pytest.raises(PartitionPathError):
        year_prefix("sensors", "observed", TIER_Z13, 999)
    with pytest.raises(PartitionPathError):
        year_prefix("sensors", "observed", TIER_Z13, 10_000)
    with pytest.raises(PartitionPathError):
        month_prefix("sensors", "observed", TIER_Z13, 2026, 0)
    with pytest.raises(PartitionPathError):
        month_prefix("sensors", "observed", TIER_Z13, 2026, 13)


@pytest.mark.parametrize("zoom", [1, 4, 12, 14, -1, 22])
def test_every_builder_refuses_a_zoom_off_the_ladder(zoom: int) -> None:
    """A writer that invents a tier strands its rows under a prefix no reader resolves."""
    off_ladder: ZoomTier = zoom  # type: ignore[assignment]

    with pytest.raises(ZoomTierError):
        zoom_prefix("sensors", "observed", off_ladder)
    with pytest.raises(ZoomTierError):
        year_prefix("sensors", "observed", off_ladder, 2026)
    with pytest.raises(ZoomTierError):
        month_prefix("sensors", "observed", off_ladder, 2026, 7)
    with pytest.raises(ZoomTierError):
        day_prefix("sensors", "observed", off_ladder, FIRST_JULY)
    with pytest.raises(ZoomTierError):
        partition_path("sensors", "observed", off_ladder, FIRST_JULY)
    with pytest.raises(ZoomTierError):
        absence_marker_path("sensors", "observed", off_ladder, FIRST_JULY)


def test_missing_partition_days_reports_only_the_absent_days() -> None:
    """Completion markers make the days with parts count as covered; days without parts are missing."""
    marker_1, _ = _completion_marker("vegetation", "observed", TIER_Z13, date(2026, 7, 1))
    marker_3, _ = _completion_marker("vegetation", "observed", TIER_Z13, date(2026, 7, 3))
    keys = [
        partition_path("vegetation", "observed", TIER_Z13, date(2026, 7, 1)),
        marker_1,
        partition_path("vegetation", "observed", TIER_Z13, date(2026, 7, 3)),
        marker_3,
    ]

    missing = missing_partition_days(
        layer="vegetation",
        kind="observed",
        zoom=TIER_Z13,
        first_day=date(2026, 7, 1),
        last_day=date(2026, 7, 4),
        keys=keys,
    )

    assert missing == (date(2026, 7, 2), date(2026, 7, 4))


def test_missing_partition_days_ignores_other_layers_kinds_and_non_part_files() -> None:
    """A neighbouring lane's objects must never make this lane's gap disappear."""
    keys = [
        partition_path("sensors", "observed", TIER_Z13, date(2026, 7, 2)),
        partition_path("vegetation", "forecast", TIER_Z13, date(2026, 7, 2)),
        "layer=vegetation/kind=observed/zoom=13/year=2026/month=07/day=02/manifest.json",
    ]

    missing = missing_partition_days(
        layer="vegetation",
        kind="observed",
        zoom=TIER_Z13,
        first_day=date(2026, 7, 1),
        last_day=date(2026, 7, 2),
        keys=keys,
    )

    assert missing == (date(2026, 7, 1), date(2026, 7, 2))


def test_missing_partition_days_ignores_another_tier_of_the_same_stream() -> None:
    """A day published at z0 says nothing about z13; blending them reports coverage over a real gap."""
    keys = [partition_path("vegetation", "observed", TIER_Z0, day) for day in (date(2026, 7, 1), date(2026, 7, 2))]

    missing = missing_partition_days(
        layer="vegetation",
        kind="observed",
        zoom=TIER_Z13,
        first_day=date(2026, 7, 1),
        last_day=date(2026, 7, 2),
        keys=keys,
    )

    assert missing == (date(2026, 7, 1), date(2026, 7, 2))


def test_partition_day_statuses_classifies_each_tier_independently() -> None:
    """One day may be data at one tier, a governed absence at another, and a gap at a third."""
    marker_z0, _ = _completion_marker("drought", "observed", TIER_Z0, FIRST_JULY)
    keys = [
        partition_path("drought", "observed", TIER_Z0, FIRST_JULY),
        marker_z0,
        absence_marker_path("drought", "observed", TIER_Z5, FIRST_JULY),
    ]

    def status_at(zoom: ZoomTier) -> str:
        return partition_day_statuses(
            layer="drought",
            kind="observed",
            zoom=zoom,
            first_day=FIRST_JULY,
            last_day=FIRST_JULY,
            keys=keys,
        )[FIRST_JULY]

    assert status_at(TIER_Z0) == "data"
    assert status_at(TIER_Z5) == "absent"
    assert status_at(TIER_Z9) == "missing"


def test_partition_day_statuses_reports_a_conflict_only_within_one_tier() -> None:
    """Data and a marker at the same tier is the manual-admin case; across tiers it is two normal days."""
    keys = [
        partition_path("drought", "observed", TIER_Z9, FIRST_JULY),
        absence_marker_path("drought", "observed", TIER_Z9, FIRST_JULY),
    ]

    statuses = partition_day_statuses(
        layer="drought",
        kind="observed",
        zoom=TIER_Z9,
        first_day=FIRST_JULY,
        last_day=FIRST_JULY,
        keys=keys,
    )

    assert statuses[FIRST_JULY] == "conflict"


def test_missing_partition_days_counts_a_multi_part_day_as_present() -> None:
    """Multiple parts with a completion marker mean the day is finished; this tests multi-part counting."""
    marker, _ = _completion_marker("signal", "observed", TIER_Z13, FIRST_JULY)
    keys = [partition_path("signal", "observed", TIER_Z13, FIRST_JULY, part_index=index) for index in (1, 2)] + [marker]

    missing = missing_partition_days(
        layer="signal",
        kind="observed",
        zoom=TIER_Z13,
        first_day=FIRST_JULY,
        last_day=FIRST_JULY,
        keys=keys,
    )

    assert missing == ()


def test_missing_partition_days_returns_the_whole_window_when_nothing_was_written() -> None:
    missing = missing_partition_days(
        layer="interventions",
        kind="observed",
        zoom=TIER_Z13,
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
            zoom=TIER_Z13,
            first_day=date(2026, 7, 4),
            last_day=date(2026, 7, 1),
            keys=(),
        )


def test_missing_partition_days_refuses_an_unbounded_window() -> None:
    with pytest.raises(PartitionPathError):
        missing_partition_days(
            layer="signal",
            kind="observed",
            zoom=TIER_Z13,
            first_day=date(1900, 1, 1),
            last_day=date(1900, 1, 1) + timedelta(days=MAX_GAP_WINDOW_DAYS),
            keys=(),
        )


def test_missing_partition_days_refuses_a_zoom_off_the_ladder() -> None:
    """A gap census against a tier nobody publishes would report every day missing, forever."""
    with pytest.raises(ZoomTierError):
        missing_partition_days(
            layer="signal",
            kind="observed",
            zoom=11,  # type: ignore[arg-type]
            first_day=FIRST_JULY,
            last_day=FIRST_JULY,
            keys=(),
        )
