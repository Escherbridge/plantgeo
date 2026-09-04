"""The direct weather-observations row builder: the day-key substring rule and the feature_id namespace.

`rows.py` is the one module in this lane where a subtle mistake could silently disagree with
PostgreSQL forever (the day key) or misrepresent a row's provenance (feature_id), so both get pinned
here rather than only exercised indirectly through the adapter.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from agri_data_service.pipeline.direct.weather_observations.rows import (
    DirectWeatherObservationsRowError,
    _feature_id,
    _observation_day,
    direct_weather_observation_tables,
)
from agri_data_service.pipeline.direct.weather_observations.source import WeatherPointObservation
from agri_data_service.warehouse.schemas.weather_observations import WEATHER_OBSERVATIONS_SCHEMA

INGESTED_AT = datetime(2026, 9, 3, 18, 0, tzinfo=UTC)


def _observation(
    *,
    latitude: float = 46.5,
    longitude: float = -117.25,
    observed_at_text: str = "2026-09-03T17:30:00.000Z",
    temperature: float = 21.5,
) -> WeatherPointObservation:
    return WeatherPointObservation(
        latitude=latitude,
        longitude=longitude,
        observation={
            "observedAt": observed_at_text,
            "temperature": temperature,
            "humidity": 40.0,
            "windSpeed": 3.2,
            "windDirection": 270.0,
            "precipitation": 0.0,
        },
    )


class TestObservationDay:
    def test_takes_the_first_ten_characters_of_observed_at_never_a_timestamp_cast(self) -> None:
        """Matches `geo.feature_observation_day`'s `substring(observedAt, 1, 10)`, not `.date()`."""
        assert _observation_day("2026-09-03T23:58:00.000Z") == date(2026, 9, 3)

    def test_refuses_a_string_too_short_to_name_a_day(self) -> None:
        with pytest.raises(DirectWeatherObservationsRowError, match="too short"):
            _observation_day("2026-09")

    def test_refuses_a_non_canonical_day_prefix(self) -> None:
        """An ISO week-date parses under Python 3.11+'s `date.fromisoformat` but round-trips to a
        different string ("2026-W36-3" -> "2026-09-02"), reaching the canonical-mismatch branch --
        unlike "2026-9-03", whose first ten characters ("2026-9-03T") are not parseable at all and so
        trip the earlier not-YYYY-MM-DD branch instead."""
        with pytest.raises(DirectWeatherObservationsRowError, match="not canonical"):
            _observation_day("2026-W36-3T00:00:00.000Z")

    def test_refuses_a_day_prefix_fromisoformat_cannot_parse_at_all(self) -> None:
        """The "2026-9-03T..." case the test above names but does not itself trip: its first ten
        characters ("2026-9-03T") are not a parseable `date.fromisoformat` string at all -- unlike
        the canonical-mismatch case, which parses successfully and then fails the round-trip check."""
        with pytest.raises(DirectWeatherObservationsRowError, match="not YYYY-MM-DD"):
            _observation_day("2026-9-03T00:00:00.000Z")


class TestFeatureId:
    def test_is_namespaced_with_direct_and_never_collides_with_a_real_uuid(self) -> None:
        """A `geo.features.id` is a bare UUID and never contains a colon, so this prefix can never collide."""
        feature_id = _feature_id("46.5000:-117.2500:2026-09-03T17:30:00.000Z")
        assert feature_id == "direct:46.5000:-117.2500:2026-09-03T17:30:00.000Z"
        assert feature_id.startswith("direct:")

    def test_is_deterministic_from_external_id_alone(self) -> None:
        assert _feature_id("same-external-id") == _feature_id("same-external-id")


class TestDirectWeatherObservationTables:
    def test_builds_one_table_per_named_day_conforming_to_the_registered_schema(self) -> None:
        tables = direct_weather_observation_tables([_observation()], ingested_at=INGESTED_AT)

        assert list(tables) == [date(2026, 9, 3)]
        table = tables[date(2026, 9, 3)]
        assert table.schema.equals(WEATHER_OBSERVATIONS_SCHEMA.arrow_schema)
        assert table.num_rows == 1

    def test_a_poll_spanning_the_utc_midnight_boundary_buckets_into_two_days(self) -> None:
        tables = direct_weather_observation_tables(
            [
                _observation(latitude=46.0, observed_at_text="2026-09-02T23:58:00.000Z"),
                _observation(latitude=47.0, observed_at_text="2026-09-03T00:02:00.000Z"),
            ],
            ingested_at=INGESTED_AT,
        )

        assert sorted(tables) == [date(2026, 9, 2), date(2026, 9, 3)]

    def test_every_base_row_carries_a_non_null_external_id_feature_id_and_source(self) -> None:
        table = direct_weather_observation_tables([_observation()], ingested_at=INGESTED_AT)[date(2026, 9, 3)]
        row = table.to_pylist()[0]

        assert row["external_id"] == "46.5000:-117.2500:2026-09-03T17:30:00.000Z"
        assert row["feature_id"] == "direct:46.5000:-117.2500:2026-09-03T17:30:00.000Z"
        assert row["source"] == "Open-Meteo"
        assert row["observed_at"] == datetime(2026, 9, 3, 17, 30, tzinfo=UTC)
        assert row["ingested_at"] == INGESTED_AT

    def test_refuses_an_observation_with_no_observed_at(self) -> None:
        broken = WeatherPointObservation(latitude=1.0, longitude=2.0, observation={"temperature": 5.0})

        with pytest.raises(DirectWeatherObservationsRowError):
            direct_weather_observation_tables([broken], ingested_at=INGESTED_AT)

    def test_refuses_a_naive_ingested_at(self) -> None:
        with pytest.raises(DirectWeatherObservationsRowError, match="timezone"):
            direct_weather_observation_tables(
                [_observation()],
                ingested_at=datetime(2026, 9, 3, 18, 0),  # noqa: DTZ001 - the naive input under test, not a defect
            )
