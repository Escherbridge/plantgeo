"""Pinned warehouse identity strings and observation timestamps for every ingest producer."""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import pytest

from agri_data_service.ingest.identity import (
    PRODUCER_BY_LAYER_NAME,
    FeatureIdentity,
    MissingNativeKeyError,
    build_burn_severity_identity,
    build_drought_area_identity,
    build_fire_perimeter_identity,
    build_firms_identity,
    build_streamflow_gauge_identity,
    build_weather_observation_identity,
    format_coordinate,
    format_javascript_fixed,
    format_javascript_timestamp,
)

FIRMS_PADDED_PROPERTIES = {
    "satellite": "N",
    "acqDate": "2026-08-03",
    "acqTime": "0142",
    "brightness": 312.4,
    "confidence": "n",
    "frp": 12.3,
}
FIRMS_PADDED_COORDINATES = [-119.15625, 45.15625]

FIRMS_UNPADDED_PROPERTIES = {
    "satellite": "N",
    "acqDate": "2026-08-03",
    "acqTime": "36",
    "brightness": 298.1,
    "confidence": "l",
    "frp": 4.2,
}
FIRMS_UNPADDED_COORDINATES = [-95.5, 29.75]

FIRMS_NIGHT_PROPERTIES = {
    "satellite": "1",
    "acqDate": "2026-07-30",
    "acqTime": "2311",
    "brightness": 327.9,
    "confidence": "h",
    "frp": 55.7,
}
FIRMS_NIGHT_COORDINATES = [-122.6764815, 45.1234565]

COLUMBIA_GAUGE = {
    "siteNo": "14105700",
    "siteName": "COLUMBIA RIVER AT PORTLAND, OR",
    "lat": 45.5372,
    "lon": -122.6768,
    "flowCfs": 125000.5,
    "percentile": None,
    "condition": "normal",
    "trend": "stable",
    "updatedAt": "2026-08-03T09:15:00.000-07:00",
}
MISSISSIPPI_GAUGE = {
    "siteNo": "05331000",
    "siteName": "MISSISSIPPI RIVER AT ST. PAUL, MN",
    "lat": 44.9428,
    "lon": -93.0958,
    "flowCfs": 8420.2,
    "percentile": None,
    "condition": "below_normal",
    "trend": "declining",
    "updatedAt": "2026-08-02T23:45:00.000-05:00",
}

TIE_SAMPLE_OBSERVATION = {
    "observedAt": "2024-08-02T00:00:00.000Z",
    "temperature": 22.5,
    "humidity": 48,
    "windSpeed": 3.2,
    "windDirection": 210,
    "precipitation": 0,
}
NEGATIVE_ZERO_SAMPLE_OBSERVATION = {
    "observedAt": "2026-08-04T06:00:00.000Z",
    "temperature": -2.1,
    "humidity": 85,
    "windSpeed": 6.7,
    "windDirection": 45,
    "precipitation": 1.2,
}

DATED_FIRE_PERIMETER = {
    "uniqueFireIdentifier": "2026-ORTNF-000123",
    "irwinId": "abc123-def456",
    "incidentName": "Test Fire",
    "fireDiscoveryDateTime": "2026-07-30T14:22:00.000Z",
    "polygonDateTime": "2026-08-02T18:05:00.000Z",
    "gisAcres": 4521.3,
    "fireCause": "Lightning",
    "incidentTypeCategory": "WF",
    "pooState": "US-OR",
    "percentContained": 42,
}
UNDATED_FIRE_PERIMETER = {
    "uniqueFireIdentifier": "2026-CAANF-000456",
    "irwinId": "def789-ghi012",
    "incidentName": "Second Fire",
    "fireDiscoveryDateTime": "2026-08-01T09:10:00.000Z",
    "polygonDateTime": None,
    "gisAcres": 112,
    "fireCause": None,
    "incidentTypeCategory": "WF",
    "pooState": "US-CA",
    "percentContained": None,
}

DIXIE_BURN_SEVERITY = {
    "Fire_ID": "ID4315711583020210714",
    "Fire_Name": "DIXIE",
    "Ig_Date": "2021-07-14",
    "BurnBndAc": 963309,
    "Severity": 3,
}
SECOND_BURN_SEVERITY = {
    "Fire_ID": "ID4890211796020200815",
    "Fire_Name": "TEST",
    "Ig_Date": "2020-08-15",
    "BurnBndAc": 1500,
    "Severity": 4,
}


# DERIVED FROM THE TYPESCRIPT, NOT CAPTURED FROM A DATABASE. The lane brief's §4.2 preferred route
# -- reading `properties->>'id'` back out of production `geo.features` -- was unavailable:
# `PLANTGEO_READONLY_URL` is unset and no populated database is reachable. These tables therefore
# use §4.2's FALLBACK route. Every `typescript_feature_id` below was produced by EXECUTING a
# character-for-character transcription of `firmsObservationId` (`ingestion-jobs.ts:100-115`),
# `ingestion-jobs.ts:189`, `:294`, `:334` and `environmental-time.ts:6-50` under Node v24.13.0, and
# was cross-checked against `Number.prototype.toFixed(4)` / `Date.prototype.toISOString()` directly.
#
# Size your trust accordingly: this pins the port against the CURRENT TypeScript's logic, not
# against stored history. The upstream string SHAPES are assumed here rather than observed -- the
# NWIS `updatedAt` millisecond-and-offset spelling below, and the `boundedSamplePoints` grid
# coordinates -- and a wrong assumption there still passes every assertion in this file. A real
# §4.2 capture is still outstanding (see `ingest/AGENTS.md`); until it runs, a downstream key
# mismatch means stored history is the authority and these literals are what to re-derive.
#
# Rows 1 and 2 are exact ties at the fifth decimal that round in OPPOSITE directions
# (-113.26495 toward zero, -113.26685 away): any implementation that treats the decimal literal
# rather than the double's exact binary value as the tie gets one of them wrong.
TYPESCRIPT_FIRMS_FEATURES = [
    (
        {"satellite": "N", "acqDate": "2026-08-02", "acqTime": "1106"},
        [-113.26495, 47.83797],
        "N:2026-08-02:1106:47.8380:-113.2649",
        "firms:N:2026-08-02:1106:47.8380:-113.2649",
        datetime(2026, 8, 2, 11, 6, tzinfo=UTC),
    ),
    (
        {"satellite": "N", "acqDate": "2026-08-02", "acqTime": "1106"},
        [-113.26685, 47.84259],
        "N:2026-08-02:1106:47.8426:-113.2669",
        "firms:N:2026-08-02:1106:47.8426:-113.2669",
        datetime(2026, 8, 2, 11, 6, tzinfo=UTC),
    ),
    (
        {"satellite": "N", "acqDate": "2026-08-02", "acqTime": "1106"},
        [-113.21275, 47.84199],
        "N:2026-08-02:1106:47.8420:-113.2127",
        "firms:N:2026-08-02:1106:47.8420:-113.2127",
        datetime(2026, 8, 2, 11, 6, tzinfo=UTC),
    ),
    (
        {"satellite": "N", "acqDate": "2026-08-02", "acqTime": "1106"},
        [-116.1519, 48.25135],
        "N:2026-08-02:1106:48.2514:-116.1519",
        "firms:N:2026-08-02:1106:48.2514:-116.1519",
        datetime(2026, 8, 2, 11, 6, tzinfo=UTC),
    ),
    (
        {"satellite": "N", "acqDate": "2026-08-02", "acqTime": "1106"},
        [-114.83283, 48.48005],
        "N:2026-08-02:1106:48.4800:-114.8328",
        "firms:N:2026-08-02:1106:48.4800:-114.8328",
        datetime(2026, 8, 2, 11, 6, tzinfo=UTC),
    ),
    (
        {"satellite": "N", "acqDate": "2026-08-02", "acqTime": "1108"},
        [-116.88065, 42.39418],
        "N:2026-08-02:1108:42.3942:-116.8807",
        "firms:N:2026-08-02:1108:42.3942:-116.8807",
        datetime(2026, 8, 2, 11, 8, tzinfo=UTC),
    ),
    # Trap T3: a three-digit `acqTime`. `firmsObservationId` interpolates it with no normalisation
    # so it enters the key raw (`926`), while `parseFirmsObservationTime` pads before parsing, so
    # the timestamp is `09:26Z`.
    (
        {"satellite": "N", "acqDate": "2026-08-02", "acqTime": "926"},
        [-114.3224, 46.94507],
        "N:2026-08-02:926:46.9451:-114.3224",
        "firms:N:2026-08-02:926:46.9451:-114.3224",
        datetime(2026, 8, 2, 9, 26, tzinfo=UTC),
    ),
]

TYPESCRIPT_STREAMFLOW_GAUGES = [
    (
        {"siteNo": "05014500", "updatedAt": "2026-08-02T18:30:00.000-06:00"},
        "05014500:2026-08-02T18:30:00.000-06:00",
        "usgs-nwis:05014500:2026-08-02T18:30:00.000-06:00",
        datetime(2026, 8, 3, 0, 30, tzinfo=UTC),
    ),
    (
        {"siteNo": "05014500", "updatedAt": "2026-08-03T03:30:00.000-06:00"},
        "05014500:2026-08-03T03:30:00.000-06:00",
        "usgs-nwis:05014500:2026-08-03T03:30:00.000-06:00",
        datetime(2026, 8, 3, 9, 30, tzinfo=UTC),
    ),
]

TYPESCRIPT_WEATHER_OBSERVATIONS = [
    (
        (42.5, -111.5),
        {"observedAt": "2026-08-03T01:15:00.000Z"},
        "42.5000:-111.5000:2026-08-03T01:15:00.000Z",
        "open-meteo:42.5000:-111.5000:2026-08-03T01:15:00.000Z",
        datetime(2026, 8, 3, 1, 15, tzinfo=UTC),
    ),
    (
        (42.5, -111.5),
        {"observedAt": "2026-08-03T14:00:00.000Z"},
        "42.5000:-111.5000:2026-08-03T14:00:00.000Z",
        "open-meteo:42.5000:-111.5000:2026-08-03T14:00:00.000Z",
        datetime(2026, 8, 3, 14, 0, tzinfo=UTC),
    ),
]

# A perimeter carrying no `polygonDateTime` falls back to `fireDiscoveryDateTime`, matching
# scripts/backfill-geometry.sql; only a perimeter carrying neither dates to `-infinity`.
TYPESCRIPT_FIRE_PERIMETERS = [
    (
        {"uniqueFireIdentifier": "2026-ID1AX-000618", "polygonDateTime": "2026-07-15T03:56:00.000Z"},
        "2026-ID1AX-000618",
        "wfigs:2026-ID1AX-000618",
        datetime(2026, 7, 15, 3, 56, tzinfo=UTC),
    ),
    (
        {"uniqueFireIdentifier": "2026-IDNCF-000283", "polygonDateTime": "2026-07-21T23:36:23.000Z"},
        "2026-IDNCF-000283",
        "wfigs:2026-IDNCF-000283",
        datetime(2026, 7, 21, 23, 36, 23, tzinfo=UTC),
    ),
    (
        {"uniqueFireIdentifier": "2026-IDBOD-265460", "polygonDateTime": None},
        "2026-IDBOD-265460",
        "wfigs:2026-IDBOD-265460",
        None,
    ),
    (
        {
            "uniqueFireIdentifier": "2026-ORBUD-002693",
            "polygonDateTime": None,
            "fireDiscoveryDateTime": "2026-07-28T00:00:41.000Z",
        },
        "2026-ORBUD-002693",
        "wfigs:2026-ORBUD-002693",
        datetime(2026, 7, 28, 0, 0, 41, tzinfo=UTC),
    ),
]


def _instant_from_epoch_milliseconds(epoch_milliseconds: int) -> datetime:
    """Build the exact instant a JavaScript `new Date(ms)` represents, without float rounding."""
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=epoch_milliseconds)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0.15625, "0.1563"),
        (-0.15625, "-0.1563"),
        (45.15625, "45.1563"),
        (-119.15625, "-119.1563"),
        # A five-decimal FIRMS latitude that an f-string spells "46.7812".
        (46.78125, "46.7813"),
        # Shortest-repr traps. These are the values that separate rounding the double's EXACT
        # binary value (correct) from rounding Decimal(repr(value)) (wrong): repr() re-rounds an
        # already-rounded 5-decimal string, turns it into an exact tie, and pushes it away from
        # zero. Each expectation below was checked against Node v24 `toFixed(4)` directly.
        (-113.26495, "-113.2649"),
        (-113.21275, "-113.2127"),
        (-116.90605, "-116.9060"),
        (45.63565, "45.6356"),
        (48.48005, "48.4800"),
        (22.07915, "22.0791"),
        (-15.24715, "-15.2471"),
        (1.00005, "1.0001"),
        (-0.0, "0.0000"),
        (0.09375, "0.0938"),
        (-0.09375, "-0.0938"),
        (12.34375, "12.3438"),
        (-12.34375, "-12.3438"),
        (100.00005, "100.0001"),
        (-100.00005, "-100.0001"),
        (0.00005, "0.0001"),
        (-0.00005, "-0.0001"),
        (45.1234565, "45.1235"),
        (-122.6764815, "-122.6765"),
        (0.0, "0.0000"),
        (90.0, "90.0000"),
        (-90.0, "-90.0000"),
        (180.0, "180.0000"),
        (-180.0, "-180.0000"),
    ],
)
def test_format_coordinate_rounds_ties_away_from_zero_like_javascript_to_fixed(value: float, expected: str) -> None:
    assert format_coordinate(value) == expected


def test_format_coordinate_is_not_an_f_string() -> None:
    """An f-string rounds ties half-to-even on the exact double; JS rounds them away from zero."""
    assert f"{46.78125:.4f}" == "46.7812"
    assert format_coordinate(46.78125) == "46.7813"
    assert f"{-0.0:.4f}" == "-0.0000"
    assert format_coordinate(-0.0) == "0.0000"


def test_format_coordinate_rounds_the_exact_double_not_its_shortest_repr() -> None:
    """Guards the other tempting rewrite: Decimal(repr(v)) re-rounds an already-rounded string.

    ECMA-262 picks the integer n minimising |n / 10^4 - x| against the double's EXACT value, so
    -113.26495 (whose double sits just below the midpoint) truncates toward zero. Rounding
    repr(v) instead turns it into a literal tie and pushes it away, which silently re-keys every
    five-decimal coordinate whose double sits just below its midpoint.
    """
    for value, expected in ((-113.26495, "-113.2649"), (22.07915, "22.0791"), (-15.24715, "-15.2471")):
        via_repr = str(Decimal(repr(value)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
        assert via_repr != expected
        assert format_coordinate(value) == expected


@pytest.mark.parametrize(
    ("value", "digits", "expected"),
    [(45.5, 0, "46"), (1.005, 2, "1.00"), (1.5, 6, "1.500000"), (46.78125, 4, "46.7813"), (-0.0, 2, "0.00")],
)
def test_format_javascript_fixed_honours_the_digit_count(value: float, digits: int, expected: str) -> None:
    assert format_javascript_fixed(value, digits) == expected


def test_format_javascript_fixed_defaults_to_the_four_coordinate_digits() -> None:
    assert format_javascript_fixed(46.78125) == format_coordinate(46.78125)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_format_javascript_fixed_rejects_a_non_finite_value(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        format_javascript_fixed(value)


@pytest.mark.parametrize(
    ("epoch_milliseconds", "expected"),
    [
        (0, "1970-01-01T00:00:00.000Z"),
        (1722556800000, "2024-08-02T00:00:00.000Z"),
        (1722556800005, "2024-08-02T00:00:00.005Z"),
        (1722556800450, "2024-08-02T00:00:00.450Z"),
        (1722556800999, "2024-08-02T00:00:00.999Z"),
        (1785823200000, "2026-08-04T06:00:00.000Z"),
    ],
)
def test_format_javascript_timestamp_always_emits_three_millisecond_digits_and_a_terminal_z(
    epoch_milliseconds: int,
    expected: str,
) -> None:
    assert format_javascript_timestamp(_instant_from_epoch_milliseconds(epoch_milliseconds)) == expected


def test_format_javascript_timestamp_normalises_an_offset_instant_and_rejects_a_naive_one() -> None:
    offset_instant = datetime(2026, 8, 3, 9, 15, tzinfo=timezone(timedelta(hours=-7)))

    assert format_javascript_timestamp(offset_instant) == "2026-08-03T16:15:00.000Z"

    with pytest.raises(ValueError, match="must include a timezone"):
        format_javascript_timestamp(datetime(2026, 8, 3, 9, 15))  # noqa: DTZ001


@pytest.mark.parametrize(
    ("properties", "coordinates", "typescript_feature_id", "expected_natural_key", "expected_observed_at"),
    TYPESCRIPT_FIRMS_FEATURES,
)
def test_firms_identity_reproduces_the_typescript_feature_id(
    properties: dict[str, object],
    coordinates: list[float],
    typescript_feature_id: str,
    expected_natural_key: str,
    expected_observed_at: datetime,
) -> None:
    identity = build_firms_identity(properties, coordinates)

    assert identity.producer_local_id == typescript_feature_id
    assert identity.natural_key == expected_natural_key
    assert identity.observed_at == expected_observed_at


@pytest.mark.parametrize(
    ("gauge", "typescript_feature_id", "expected_natural_key", "expected_observed_at"),
    TYPESCRIPT_STREAMFLOW_GAUGES,
)
def test_streamflow_gauge_identity_reproduces_the_typescript_feature_id(
    gauge: dict[str, object],
    typescript_feature_id: str,
    expected_natural_key: str,
    expected_observed_at: datetime,
) -> None:
    identity = build_streamflow_gauge_identity(gauge)

    assert identity.producer_local_id == typescript_feature_id
    assert identity.natural_key == expected_natural_key
    assert identity.observed_at == expected_observed_at


@pytest.mark.parametrize(
    ("sample_point", "observation", "typescript_feature_id", "expected_natural_key", "expected_observed_at"),
    TYPESCRIPT_WEATHER_OBSERVATIONS,
)
def test_weather_observation_identity_reproduces_the_typescript_feature_id(
    sample_point: tuple[float, float],
    observation: dict[str, object],
    typescript_feature_id: str,
    expected_natural_key: str,
    expected_observed_at: datetime,
) -> None:
    latitude, longitude = sample_point
    identity = build_weather_observation_identity(latitude, longitude, observation)

    assert identity.producer_local_id == typescript_feature_id
    assert identity.natural_key == expected_natural_key
    assert identity.observed_at == expected_observed_at


@pytest.mark.parametrize(
    ("perimeter", "typescript_feature_id", "expected_natural_key", "expected_observed_at"),
    TYPESCRIPT_FIRE_PERIMETERS,
)
def test_fire_perimeter_identity_reproduces_the_typescript_feature_id(
    perimeter: dict[str, object],
    typescript_feature_id: str,
    expected_natural_key: str,
    expected_observed_at: datetime | None,
) -> None:
    identity = build_fire_perimeter_identity(perimeter)

    assert identity.producer_local_id == typescript_feature_id
    assert identity.natural_key == expected_natural_key
    assert identity.observed_at == expected_observed_at


def test_firms_prefers_a_stored_observed_at_and_agrees_with_the_acquisition_fields_it_derives() -> None:
    properties, coordinates, _, expected_natural_key, expected_observed_at = TYPESCRIPT_FIRMS_FEATURES[0]
    stored = {**properties, "observedAt": "2026-08-02T11:06:00.000Z"}

    identity = build_firms_identity(stored, coordinates)

    assert identity.natural_key == expected_natural_key
    assert identity.observed_at == expected_observed_at

    # An `observedAt` that carries no zone is ignored rather than trusted, exactly as
    # `parseZonedObservationTime` returns null and `parseFirmsObservationTime` falls through.
    unzoned = {**properties, "observedAt": "2026-08-02 11:06:00"}
    assert build_firms_identity(unzoned, coordinates).observed_at == expected_observed_at


def test_firms_identity_pins_the_typescript_feature_id_and_the_utc_acquisition_instant() -> None:
    padded = build_firms_identity(FIRMS_PADDED_PROPERTIES, FIRMS_PADDED_COORDINATES)
    unpadded = build_firms_identity(FIRMS_UNPADDED_PROPERTIES, FIRMS_UNPADDED_COORDINATES)
    night = build_firms_identity(FIRMS_NIGHT_PROPERTIES, FIRMS_NIGHT_COORDINATES)

    assert padded.producer_local_id == "N:2026-08-03:0142:45.1563:-119.1563"
    assert padded.natural_key == "firms:N:2026-08-03:0142:45.1563:-119.1563"
    assert padded.observed_at == datetime(2026, 8, 3, 1, 42, tzinfo=UTC)

    assert unpadded.producer_local_id == "N:2026-08-03:36:29.7500:-95.5000"
    assert unpadded.natural_key == "firms:N:2026-08-03:36:29.7500:-95.5000"
    assert unpadded.observed_at == datetime(2026, 8, 3, 0, 36, tzinfo=UTC)

    assert night.producer_local_id == "1:2026-07-30:2311:45.1235:-122.6765"
    assert night.natural_key == "firms:1:2026-07-30:2311:45.1235:-122.6765"
    assert night.observed_at == datetime(2026, 7, 30, 23, 11, tzinfo=UTC)


def test_streamflow_gauge_identity_keeps_the_local_offset_string_and_dates_a_separate_utc_copy() -> None:
    columbia = build_streamflow_gauge_identity(COLUMBIA_GAUGE)
    mississippi = build_streamflow_gauge_identity(MISSISSIPPI_GAUGE)

    assert columbia.producer_local_id == "14105700:2026-08-03T09:15:00.000-07:00"
    assert columbia.natural_key == "usgs-nwis:14105700:2026-08-03T09:15:00.000-07:00"
    assert columbia.observed_at == datetime(2026, 8, 3, 16, 15, tzinfo=UTC)

    assert mississippi.producer_local_id == "05331000:2026-08-02T23:45:00.000-05:00"
    assert mississippi.natural_key == "usgs-nwis:05331000:2026-08-02T23:45:00.000-05:00"
    assert mississippi.observed_at == datetime(2026, 8, 3, 4, 45, tzinfo=UTC)


def test_weather_observation_identity_pins_sample_grid_ties_and_negative_zero() -> None:
    tie_sample = build_weather_observation_identity(0.15625, -0.15625, TIE_SAMPLE_OBSERVATION)
    negative_zero_sample = build_weather_observation_identity(1.00005, -0.0, NEGATIVE_ZERO_SAMPLE_OBSERVATION)

    assert tie_sample.producer_local_id == "0.1563:-0.1563:2024-08-02T00:00:00.000Z"
    assert tie_sample.natural_key == "open-meteo:0.1563:-0.1563:2024-08-02T00:00:00.000Z"
    assert tie_sample.observed_at == datetime(2024, 8, 2, tzinfo=UTC)

    assert negative_zero_sample.producer_local_id == "1.0001:0.0000:2026-08-04T06:00:00.000Z"
    assert negative_zero_sample.natural_key == "open-meteo:1.0001:0.0000:2026-08-04T06:00:00.000Z"
    assert negative_zero_sample.observed_at == datetime(2026, 8, 4, 6, 0, tzinfo=UTC)


def test_fire_perimeter_identity_is_the_bare_unique_fire_identifier() -> None:
    dated = build_fire_perimeter_identity(DATED_FIRE_PERIMETER)
    undated = build_fire_perimeter_identity(UNDATED_FIRE_PERIMETER)

    assert dated.producer_local_id == "2026-ORTNF-000123"
    assert dated.natural_key == "wfigs:2026-ORTNF-000123"
    assert dated.observed_at == datetime(2026, 8, 2, 18, 5, tzinfo=UTC)

    assert undated.producer_local_id == "2026-CAANF-000456"
    assert undated.natural_key == "wfigs:2026-CAANF-000456"


def test_drought_area_identity_pairs_the_release_date_with_the_drought_class_at_midnight_utc() -> None:
    early_release = build_drought_area_identity("2026-07-28", 2)
    later_release = build_drought_area_identity("2026-08-04", 4)

    assert early_release.producer_local_id == "2026-07-28:2"
    assert early_release.natural_key == "usdm:2026-07-28:2"
    assert early_release.observed_at == datetime(2026, 7, 28, tzinfo=UTC)

    assert later_release.producer_local_id == "2026-08-04:4"
    assert later_release.natural_key == "usdm:2026-08-04:4"
    assert later_release.observed_at == datetime(2026, 8, 4, tzinfo=UTC)


def test_drought_area_identity_accepts_a_stored_date_and_rejects_a_class_outside_the_d0_to_d4_scale() -> None:
    # `geo.drought_areas.valid_date` is a DATE column, so a consumer reading it back must not have
    # to re-derive the `YYYY-MM-DD` text that keys the row.
    from_release_text = build_drought_area_identity("2026-07-28", 2)
    from_stored_date = build_drought_area_identity(date(2026, 7, 28), 2)

    assert from_release_text.natural_key == from_stored_date.natural_key == "usdm:2026-07-28:2"
    assert from_release_text.observed_at == from_stored_date.observed_at

    # Upstream pins the class to `z.number().int().min(0).max(4)` (usdm-drought.ts:46).
    for outside_the_scale in (-1, 5):
        with pytest.raises(ValueError, match="drought class"):
            build_drought_area_identity("2026-07-28", outside_the_scale)

    with pytest.raises(MissingNativeKeyError):
        build_drought_area_identity("2026-07-28", True)

    with pytest.raises(MissingNativeKeyError):
        build_drought_area_identity("   ", 2)

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        build_drought_area_identity("07/28/2026", 2)


def test_burn_severity_identity_is_the_native_fire_id_with_no_release_date_yet() -> None:
    dixie = build_burn_severity_identity(DIXIE_BURN_SEVERITY)
    second = build_burn_severity_identity(SECOND_BURN_SEVERITY)

    assert dixie.producer_local_id == "ID4315711583020210714"
    assert dixie.natural_key == "mtbs:ID4315711583020210714"

    assert second.producer_local_id == "ID4890211796020200815"
    assert second.natural_key == "mtbs:ID4890211796020200815"


def test_observed_at_is_none_only_where_the_producer_supplies_no_observation_time() -> None:
    # A perimeter with neither field is the only WFIGS shape that supplies no observation time at all.
    assert build_fire_perimeter_identity({"uniqueFireIdentifier": "2026-CAANF-000456"}).observed_at is None
    assert build_burn_severity_identity(DIXIE_BURN_SEVERITY).observed_at is None
    assert build_burn_severity_identity(SECOND_BURN_SEVERITY).observed_at is None

    assert build_fire_perimeter_identity(UNDATED_FIRE_PERIMETER).observed_at is not None
    assert build_fire_perimeter_identity(DATED_FIRE_PERIMETER).observed_at is not None
    assert build_firms_identity(FIRMS_PADDED_PROPERTIES, FIRMS_PADDED_COORDINATES).observed_at is not None
    assert build_streamflow_gauge_identity(COLUMBIA_GAUGE).observed_at is not None
    assert build_weather_observation_identity(0.15625, -0.15625, TIE_SAMPLE_OBSERVATION).observed_at is not None
    assert build_drought_area_identity("2026-07-28", 2).observed_at is not None


def test_firms_rejects_a_missing_field_or_coordinate_instead_of_emitting_an_empty_segment() -> None:
    with pytest.raises(MissingNativeKeyError):
        build_firms_identity({"acqDate": "2026-08-03", "acqTime": "0142"}, FIRMS_PADDED_COORDINATES)

    with pytest.raises(MissingNativeKeyError):
        build_firms_identity({"satellite": "N", "acqTime": "0142"}, FIRMS_PADDED_COORDINATES)

    with pytest.raises(MissingNativeKeyError):
        build_firms_identity({"satellite": "N", "acqDate": "2026-08-03", "acqTime": "  "}, FIRMS_PADDED_COORDINATES)

    with pytest.raises(MissingNativeKeyError):
        build_firms_identity(FIRMS_PADDED_PROPERTIES, [-119.15625])

    with pytest.raises(MissingNativeKeyError):
        build_firms_identity(FIRMS_PADDED_PROPERTIES, None)


def test_streamflow_gauge_rejects_a_blank_site_number_or_an_upstream_reading_time_it_never_supplied() -> None:
    with pytest.raises(MissingNativeKeyError):
        build_streamflow_gauge_identity({"siteNo": "", "updatedAt": "2026-08-03T09:15:00.000-07:00"})

    with pytest.raises(MissingNativeKeyError):
        build_streamflow_gauge_identity({"siteNo": "14105700"})


def test_weather_observation_rejects_an_absent_upstream_instant() -> None:
    with pytest.raises(MissingNativeKeyError):
        build_weather_observation_identity(45.5, -122.5, {"temperature": 22.5})


def test_fire_perimeter_rejects_a_blank_unique_fire_identifier() -> None:
    with pytest.raises(MissingNativeKeyError):
        build_fire_perimeter_identity({"uniqueFireIdentifier": "   ", "polygonDateTime": None})

    with pytest.raises(MissingNativeKeyError):
        build_fire_perimeter_identity({"incidentName": "Nameless Fire"})


def test_burn_severity_rejects_an_absent_or_non_string_fire_id_instead_of_falling_back_to_empty() -> None:
    with pytest.raises(MissingNativeKeyError):
        build_burn_severity_identity({"Fire_Name": "DIXIE", "Ig_Date": "2021-07-14"})

    with pytest.raises(MissingNativeKeyError):
        build_burn_severity_identity({"Fire_ID": 4315711583020210714})


def test_two_producers_sharing_a_producer_local_id_never_share_a_natural_key() -> None:
    shared_local_id = "ID4315711583020210714"
    burn_severity = build_burn_severity_identity({"Fire_ID": shared_local_id})
    fire_perimeter = build_fire_perimeter_identity({"uniqueFireIdentifier": shared_local_id})

    assert burn_severity.producer_local_id == fire_perimeter.producer_local_id
    assert burn_severity.natural_key != fire_perimeter.natural_key
    assert burn_severity.natural_key == "mtbs:ID4315711583020210714"
    assert fire_perimeter.natural_key == "wfigs:ID4315711583020210714"


def test_identity_rejects_an_oversized_natural_key_and_a_malformed_producer_token() -> None:
    with pytest.raises(ValueError, match="255 characters"):
        FeatureIdentity(producer="firms", producer_local_id="x" * 250, observed_at=None)

    with pytest.raises(ValueError, match="producer must match"):
        FeatureIdentity(producer="Open Meteo", producer_local_id="0.1563:-0.1563", observed_at=None)

    with pytest.raises(ValueError, match="producer must match"):
        FeatureIdentity(producer="-firms", producer_local_id="0.1563:-0.1563", observed_at=None)

    with pytest.raises(ValueError, match="must include a timezone"):
        FeatureIdentity(producer="firms", producer_local_id="x", observed_at=datetime(2026, 8, 3))  # noqa: DTZ001


@pytest.mark.parametrize(
    ("layer_name", "producer"),
    [
        ("fire-detections", "firms"),
        ("water-gauges", "usgs-nwis"),
        ("weather-observations", "open-meteo"),
        ("fire-perimeters", "wfigs"),
    ],
)
def test_producer_by_layer_name_replaces_the_layer_namespace_in_the_backfill(layer_name: str, producer: str) -> None:
    assert PRODUCER_BY_LAYER_NAME[layer_name] == producer


# --- Entity identity: the enduring place, distinct from the observation taken there -----------------
# A gauge that never moves must not mint a new geo.geometry "place" on every reading. Measured against
# production before this split: 15,280 water-gauge rows for 899 real gauges (17x) and 2,885
# weather rows for 116 real sample points (25x), while every natural_key held exactly one version --
# an inert Type-2 dimension. See ingest/AGENTS.md.


def test_streamflow_entity_key_is_the_gauge_not_the_reading() -> None:
    first = build_streamflow_gauge_identity(COLUMBIA_GAUGE)
    later = build_streamflow_gauge_identity({**COLUMBIA_GAUGE, "updatedAt": "2026-08-03T11:15:00.000-07:00"})

    # Two readings from one gauge: two observations, ONE place.
    assert first.natural_key != later.natural_key
    assert first.entity_key == later.entity_key
    assert first.entity_key == f"usgs-nwis:{COLUMBIA_GAUGE['siteNo']}"
    assert not first.observation_is_its_own_entity


def test_weather_entity_key_is_the_sample_point_not_the_hour() -> None:
    first = build_weather_observation_identity(42.5, -111.5, TIE_SAMPLE_OBSERVATION)
    later = build_weather_observation_identity(
        42.5, -111.5, {**TIE_SAMPLE_OBSERVATION, "observedAt": "2026-08-03T02:15:00.000Z"}
    )

    assert first.natural_key != later.natural_key
    assert first.entity_key == later.entity_key
    assert first.entity_key == "open-meteo:42.5000:-111.5000"


def test_distinct_gauges_and_distinct_sample_points_never_share_an_entity() -> None:
    assert (
        build_streamflow_gauge_identity(COLUMBIA_GAUGE).entity_key
        != build_streamflow_gauge_identity(MISSISSIPPI_GAUGE).entity_key
    )
    assert (
        build_weather_observation_identity(42.5, -111.5, TIE_SAMPLE_OBSERVATION).entity_key
        != build_weather_observation_identity(45.0, -118.0, TIE_SAMPLE_OBSERVATION).entity_key
    )


def test_observation_is_its_own_entity_where_the_producer_emits_one_place_per_record() -> None:
    # FIRMS detections are discrete events (6,201 distinct pixels for 6,297 detections in production),
    # and wfigs/mtbs were already entity-keyed. For these the two keys coincide by design.
    firms = build_firms_identity(FIRMS_PADDED_PROPERTIES, FIRMS_PADDED_COORDINATES)
    perimeter = build_fire_perimeter_identity(DATED_FIRE_PERIMETER)
    burn = build_burn_severity_identity(DIXIE_BURN_SEVERITY)

    for identity in (firms, perimeter, burn):
        assert identity.observation_is_its_own_entity
        assert identity.entity_key == identity.natural_key


def test_entity_key_keeps_the_producer_namespace_so_two_producers_never_interleave() -> None:
    gauge = FeatureIdentity(producer="usgs-nwis", producer_local_id="X:t", observed_at=None, entity_local_id="X")
    weather = FeatureIdentity(producer="open-meteo", producer_local_id="X:t", observed_at=None, entity_local_id="X")
    assert gauge.entity_key != weather.entity_key


def test_entity_key_is_guarded_like_the_natural_key() -> None:
    with pytest.raises(ValueError, match="entity_key must not exceed"):
        FeatureIdentity(producer="firms", producer_local_id="a", observed_at=None, entity_local_id="e" * 250)

    with pytest.raises(MissingNativeKeyError):
        FeatureIdentity(producer="firms", producer_local_id="a", observed_at=None, entity_local_id="")


def test_the_observation_key_is_unchanged_by_the_entity_split() -> None:
    # The whole TypeScript-parity guarantee rides on producer_local_id staying byte-identical.
    assert build_streamflow_gauge_identity(COLUMBIA_GAUGE).producer_local_id == (
        f"{COLUMBIA_GAUGE['siteNo']}:{COLUMBIA_GAUGE['updatedAt']}"
    )
    assert build_weather_observation_identity(42.5, -111.5, TIE_SAMPLE_OBSERVATION).producer_local_id == (
        f"42.5000:-111.5000:{TIE_SAMPLE_OBSERVATION['observedAt']}"
    )


# --- WFIGS dates from its discovery time when the polygon carries none ------------------------------
# `scripts/backfill-geometry.sql` coalesces `polygonDateTime` to `fireDiscoveryDateTime` and says why:
# a JSON null polygon time would otherwise open v1 at `-infinity`, rendering the perimeter as active at
# every past slider position for the life of the warehouse. 13 of 112 production perimeters fit that
# shape. Now that the forward path mints versions from `observed_at`, the single minting site has to
# apply the same rule or it reintroduces exactly the harm the seed script guarded against.


def test_a_perimeter_with_no_polygon_time_dates_from_its_fire_discovery_time() -> None:
    identity = build_fire_perimeter_identity(UNDATED_FIRE_PERIMETER)

    assert identity.observed_at == datetime(2026, 8, 1, 9, 10, tzinfo=UTC)
    # The key is untouched: only the date the dimension versions by changes.
    assert identity.producer_local_id == UNDATED_FIRE_PERIMETER["uniqueFireIdentifier"]
    assert identity.natural_key == "wfigs:2026-CAANF-000456"


def test_a_perimeter_prefers_its_own_polygon_time_over_the_fires_discovery_time() -> None:
    identity = build_fire_perimeter_identity(DATED_FIRE_PERIMETER)

    assert identity.observed_at == datetime(2026, 8, 2, 18, 5, tzinfo=UTC)


def test_a_perimeter_with_neither_time_stays_undated_rather_than_inventing_one() -> None:
    identity = build_fire_perimeter_identity(
        {"uniqueFireIdentifier": "2026-IDBOD-265460", "polygonDateTime": None, "fireDiscoveryDateTime": None}
    )

    assert identity.observed_at is None


def test_an_unparseable_polygon_time_raises_rather_than_silently_using_the_discovery_time() -> None:
    with pytest.raises(ValueError, match="polygonDateTime"):
        build_fire_perimeter_identity(
            {
                "uniqueFireIdentifier": "2026-ORTNF-000123",
                "polygonDateTime": "not-a-timestamp",
                "fireDiscoveryDateTime": "2026-07-30T14:22:00.000Z",
            }
        )
