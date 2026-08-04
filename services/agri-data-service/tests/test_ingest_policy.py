"""The bounded-ingestion policy: bbox parsing, JavaScript number parity, and observation freshness."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agri_data_service.ingest.policy import (
    PACIFIC_NORTHWEST_COVERAGE_BBOX,
    BoundedIngestionPolicyError,
    format_javascript_number,
    is_fresh_observation,
    javascript_number,
    javascript_parse_float,
    parse_bbox,
    resolve_bounded_bbox,
    resolve_max_source_records,
    resolve_weather_sample_spacing_degrees,
)

NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)

# Pinned literally rather than imported from policy.py: these bounds are the ported contract, so the
# test must fail if the module's own constants drift.
EXPECTED_DEFAULT_MAX_SOURCE_RECORDS = 10_000
EXPECTED_MIN_MAX_SOURCE_RECORDS = 1_000
EXPECTED_MAX_MAX_SOURCE_RECORDS = 50_000
EXPECTED_MIN_WEATHER_SAMPLE_SPACING_DEGREES = 0.25
EXPECTED_MAX_WEATHER_SAMPLE_SPACING_DEGREES = 5.0


@pytest.fixture(autouse=True)
def _clear_ingest_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every policy read is a call-time environment read, so each test starts from an unset environment."""
    for variable in ("INGEST_BBOX", "INGEST_MAX_SOURCE_RECORDS", "WEATHER_SAMPLE_SPACING_DEGREES"):
        monkeypatch.delenv(variable, raising=False)


def test_pacific_northwest_coverage_bbox_is_the_constant_the_deleted_typescript_test_held() -> None:
    assert PACIFIC_NORTHWEST_COVERAGE_BBOX == "-125,42,-111,49"


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", 0.0), ("   ", 0.0), ("42", 42.0), ("+1", 1.0), (".5", 0.5), ("1e3", 1000.0), ("-0.25", -0.25)],
)
def test_javascript_number_matches_the_values_number_accepts(text: str, expected: float) -> None:
    assert javascript_number(text) == expected


@pytest.mark.parametrize("text", ["abc", "1_0", "12abc", "0x10"])
def test_javascript_number_returns_nan_where_javascript_returns_nan(text: str) -> None:
    assert javascript_number(text) != javascript_number(text)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(-125.0, "-125"), (42.0, "42"), (0.0, "0"), (-0.0, "0"), (42.5, "42.5"), (-111.25, "-111.25")],
)
def test_format_javascript_number_drops_the_trailing_zero_python_would_print(value: float, expected: str) -> None:
    assert format_javascript_number(value) == expected


def test_format_javascript_number_refuses_a_non_finite_value() -> None:
    with pytest.raises(ValueError, match="finite"):
        format_javascript_number(float("inf"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [("12.5", 12.5), ("12abc", 12.0), ("  7 ", 7.0), ("-3.5e2", -350.0)],
)
def test_javascript_parse_float_takes_the_leading_numeric_prefix(text: str, expected: float) -> None:
    assert javascript_parse_float(text) == expected


@pytest.mark.parametrize("text", ["abc", "", "  "])
def test_javascript_parse_float_returns_none_where_parse_float_returns_nan(text: str) -> None:
    assert javascript_parse_float(text) is None


def test_an_unset_bbox_resolves_to_none_rather_than_raising() -> None:
    assert resolve_bounded_bbox() is None


def test_the_bbox_comes_from_the_environment_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_BBOX", PACIFIC_NORTHWEST_COVERAGE_BBOX)
    assert resolve_bounded_bbox() == PACIFIC_NORTHWEST_COVERAGE_BBOX


def test_an_override_wins_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_BBOX", PACIFIC_NORTHWEST_COVERAGE_BBOX)
    assert resolve_bounded_bbox("-120,44,-118,46") == "-120,44,-118,46"


def test_a_blank_override_falls_through_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INGEST_BBOX", PACIFIC_NORTHWEST_COVERAGE_BBOX)
    assert resolve_bounded_bbox("   ") == PACIFIC_NORTHWEST_COVERAGE_BBOX


def test_the_resolved_bbox_is_rejoined_the_way_javascript_joins_it() -> None:
    # Python's str(float) would emit "-125.0"; the upstream URLs must receive "-125".
    assert resolve_bounded_bbox("-125.0,42.00,-111,49") == "-125,42,-111,49"


@pytest.mark.parametrize(
    "bbox",
    [
        "-125,42,-111",
        "-125,42,-111,49,50",
        "-125,42,abc,49",
        "-190,42,-111,49",
        "-125,42,-111,95",
        "-111,42,-125,49",
        "-125,49,-111,42",
        "-125,42,-90,49",
        "-125,20,-111,49",
    ],
)
def test_a_malformed_or_oversized_bbox_raises(bbox: str) -> None:
    with pytest.raises(BoundedIngestionPolicyError):
        resolve_bounded_bbox(bbox)


def test_parse_bbox_splits_a_checked_box_into_ordinates() -> None:
    assert parse_bbox(PACIFIC_NORTHWEST_COVERAGE_BBOX) == (-125.0, 42.0, -111.0, 49.0)


def test_max_source_records_defaults_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_max_source_records() == EXPECTED_DEFAULT_MAX_SOURCE_RECORDS
    monkeypatch.setenv("INGEST_MAX_SOURCE_RECORDS", "10")
    assert resolve_max_source_records() == EXPECTED_MIN_MAX_SOURCE_RECORDS
    monkeypatch.setenv("INGEST_MAX_SOURCE_RECORDS", "9999999")
    assert resolve_max_source_records() == EXPECTED_MAX_MAX_SOURCE_RECORDS
    monkeypatch.setenv("INGEST_MAX_SOURCE_RECORDS", "not-a-number")
    assert resolve_max_source_records() == EXPECTED_DEFAULT_MAX_SOURCE_RECORDS


def test_weather_sample_spacing_defaults_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    assert resolve_weather_sample_spacing_degrees() == 1.0
    monkeypatch.setenv("WEATHER_SAMPLE_SPACING_DEGREES", "0.01")
    assert resolve_weather_sample_spacing_degrees() == EXPECTED_MIN_WEATHER_SAMPLE_SPACING_DEGREES
    monkeypatch.setenv("WEATHER_SAMPLE_SPACING_DEGREES", "50")
    assert resolve_weather_sample_spacing_degrees() == EXPECTED_MAX_WEATHER_SAMPLE_SPACING_DEGREES
    monkeypatch.setenv("WEATHER_SAMPLE_SPACING_DEGREES", "garbage")
    assert resolve_weather_sample_spacing_degrees() == 1.0


def test_a_fresh_observation_is_accepted() -> None:
    assert is_fresh_observation(NOW - timedelta(hours=1), timedelta(days=2), NOW)


def test_a_stale_observation_is_rejected() -> None:
    assert not is_fresh_observation(NOW - timedelta(days=3), timedelta(days=2), NOW)


def test_a_future_observation_is_rejected_beyond_five_minutes_of_skew() -> None:
    assert is_fresh_observation(NOW + timedelta(minutes=4), timedelta(days=2), NOW)
    assert not is_fresh_observation(NOW + timedelta(minutes=6), timedelta(days=2), NOW)


def test_a_naive_observation_is_refused_rather_than_assumed_utc() -> None:
    with pytest.raises(ValueError, match="timezone"):
        is_fresh_observation(datetime(2026, 8, 3, 12, 0), timedelta(days=2), NOW)  # noqa: DTZ001
