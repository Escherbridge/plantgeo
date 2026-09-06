"""The direct sensors row builder: the day-key substring rule, the winning-report reduction, and
the feature_id namespace.

`rows.py` is where a subtle mistake could silently disagree with PostgreSQL forever (the day key,
shared with every other lane in this track) or misrepresent which report a station-day's rows came
from (the winning-report reduction, unique to this lane's block shape) -- both are pinned here
directly, mirroring `test_weather_observations_rows.py`'s discipline for its own lane.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from agri_data_service.ingest.identity import FeatureIdentity
from agri_data_service.ingest.sensors import NWS_API_PRODUCER, SENSORS_CHANNEL
from agri_data_service.ingest.writer import FeatureWrite
from agri_data_service.pipeline.direct.sensors.rows import (
    DirectSensorsRowError,
    _feature_id,
    _observation_day,
    direct_sensor_tables,
)
from agri_data_service.warehouse.schemas.sensors import SENSORS_SCHEMA

DEFAULT_READINGS: dict[str, object] = {
    "temperature": {"value": 21.5, "unitCode": "wmoUnit:degC"},
    "windGust": {"value": 5.0, "unitCode": "wmoUnit:km_h-1", "qualityControl": "V"},
}


def _write(  # noqa: PLR0913 - one keyword per field of the NWS document under test
    *,
    station_id: str = "KBOI",
    timestamp_text: str = "2026-09-03T17:00:00+00:00",
    readings: dict[str, object] | None = None,
    include_readings_key: bool = True,
    longitude: float = -116.2228,
    latitude: float = 43.5644,
    station_name: str | None = "Boise Air Terminal",
    network: str | None = "ASOS",
    include_geometry: bool = True,
) -> FeatureWrite:
    """Build a `FeatureWrite` shaped exactly like `ingest/sensors.py::build_sensor_reading_write`'s output.

    `readings=None` (the default) means "use `DEFAULT_READINGS`", never "no readings" -- pass
    `readings={}` explicitly for the empty case, matching a station whose winning report went
    entirely unmeasured.
    """
    identity = FeatureIdentity(
        producer=NWS_API_PRODUCER,
        producer_local_id=f"{station_id}:{timestamp_text}",
        observed_at=datetime.fromisoformat(timestamp_text),
        entity_local_id=station_id,
    )
    properties: dict[str, object] = {"sensor_id": station_id, "timestamp": timestamp_text, "observedAt": timestamp_text}
    if include_readings_key:
        properties["readings"] = dict(DEFAULT_READINGS) if readings is None else readings
    if include_geometry:
        properties["geometry"] = {"type": "Point", "coordinates": [longitude, latitude]}
    properties["source"] = "NOAA NWS"
    if station_name is not None:
        properties["station_name"] = station_name
    if network is not None:
        properties["network"] = network
    return FeatureWrite(layer_reference="sensors", identity=identity, properties=properties, channel=SENSORS_CHANNEL)


class TestObservationDay:
    def test_takes_the_first_ten_characters_of_observed_at_never_a_timestamp_cast(self) -> None:
        """Matches `geo.feature_observation_day`'s `substring(observedAt, 1, 10)`, not `.date()`."""
        assert _observation_day("2026-09-03T23:58:00+00:00") == date(2026, 9, 3)

    def test_refuses_a_string_too_short_to_name_a_day(self) -> None:
        with pytest.raises(DirectSensorsRowError, match="too short"):
            _observation_day("2026-09")

    def test_refuses_a_non_canonical_day_prefix(self) -> None:
        with pytest.raises(DirectSensorsRowError, match="not canonical"):
            _observation_day("2026-W36-3T00:00:00+00:00")

    def test_refuses_a_day_prefix_fromisoformat_cannot_parse_at_all(self) -> None:
        with pytest.raises(DirectSensorsRowError, match="not YYYY-MM-DD"):
            _observation_day("2026-9-03T00:00:00+00:00")


class TestFeatureId:
    def test_is_namespaced_with_direct_and_never_collides_with_a_real_uuid(self) -> None:
        """A `geo.features.id` is a bare UUID and never contains a colon, so this prefix can never collide."""
        feature_id = _feature_id("KBOI:2026-09-03T17:00:00+00:00")
        assert feature_id == "direct:KBOI:2026-09-03T17:00:00+00:00"
        assert feature_id.startswith("direct:")

    def test_is_deterministic_from_external_id_alone(self) -> None:
        assert _feature_id("same-external-id") == _feature_id("same-external-id")


class TestDirectSensorTables:
    def test_builds_one_table_per_named_day_with_one_row_per_reported_measurement(self) -> None:
        tables = direct_sensor_tables([_write()])

        assert list(tables) == [date(2026, 9, 3)]
        table = tables[date(2026, 9, 3)]
        assert table.schema.equals(SENSORS_SCHEMA.arrow_schema)
        assert table.num_rows == len(DEFAULT_READINGS)

    def test_excludes_textDescription_from_the_measurement_fan_out(self) -> None:  # noqa: N802 - names the NWS key verbatim
        write = _write(readings={"temperature": {"value": 20.0}, "textDescription": "Clear and dry"})

        table = direct_sensor_tables([write])[date(2026, 9, 3)]

        assert table.num_rows == 1
        assert table.to_pylist()[0]["measurement_name"] == "temperature"

    def test_a_station_day_with_no_surviving_measurement_contributes_no_table(self) -> None:
        """Matches the SQL export's own `CROSS JOIN LATERAL jsonb_each({})` yielding zero rows."""
        assert direct_sensor_tables([_write(readings={})]) == {}

    def test_picks_the_latest_report_as_the_stations_winner_for_the_day(self) -> None:
        """Two polls of the SAME station-day: only the later report's measurements survive, mirroring
        `sql/pipeline/sensors_day_export.sql`'s `DISTINCT ON ... ORDER BY observedAt DESC`."""
        earlier = _write(timestamp_text="2026-09-03T12:00:00+00:00", readings={"temperature": {"value": 18.0}})
        later = _write(timestamp_text="2026-09-03T18:00:00+00:00", readings={"temperature": {"value": 21.0}})

        rows = direct_sensor_tables([earlier, later])[date(2026, 9, 3)].to_pylist()

        assert len(rows) == 1
        assert rows[0]["value"] == pytest.approx(21.0)
        assert rows[0]["observed_at"] == datetime(2026, 9, 3, 18, 0, tzinfo=UTC)

    def test_an_older_report_never_wins_regardless_of_write_order(self) -> None:
        """The reduction is order-independent: feeding the later report first must not change the winner."""
        earlier = _write(timestamp_text="2026-09-03T12:00:00+00:00", readings={"temperature": {"value": 18.0}})
        later = _write(timestamp_text="2026-09-03T18:00:00+00:00", readings={"temperature": {"value": 21.0}})

        rows = direct_sensor_tables([later, earlier])[date(2026, 9, 3)].to_pylist()

        assert rows[0]["value"] == pytest.approx(21.0)

    def test_two_different_stations_the_same_day_both_survive(self) -> None:
        boise = _write(station_id="KBOI")
        portland = _write(station_id="KPDX", longitude=-122.5951, latitude=45.5898)

        table = direct_sensor_tables([boise, portland])[date(2026, 9, 3)]

        assert {row["sensor_id"] for row in table.to_pylist()} == {"KBOI", "KPDX"}

    def test_feature_id_coordinates_and_the_unmeasured_availability_column(self) -> None:
        row = direct_sensor_tables([_write()])[date(2026, 9, 3)].to_pylist()[0]

        assert row["feature_id"].startswith("direct:KBOI:")
        assert row["station_longitude"] == pytest.approx(-116.2228)
        assert row["station_latitude"] == pytest.approx(43.5644)
        assert row["data_available_at"] is None

    def test_a_station_with_no_geometry_gets_null_coordinates_not_a_fabricated_point(self) -> None:
        row = direct_sensor_tables([_write(include_geometry=False)])[date(2026, 9, 3)].to_pylist()[0]

        assert row["station_longitude"] is None
        assert row["station_latitude"] is None

    def test_refuses_a_write_with_no_readings_mapping_at_all(self) -> None:
        broken = _write(include_readings_key=False)

        with pytest.raises(DirectSensorsRowError, match="readings"):
            direct_sensor_tables([broken])

    def test_a_measurement_with_no_numeric_value_is_dropped_not_raised(self) -> None:
        write = _write(readings={"temperature": {"value": 20.0}, "windGust": {"unitCode": "wmoUnit:km_h-1"}})

        table = direct_sensor_tables([write])[date(2026, 9, 3)]

        assert table.num_rows == 1
        assert table.to_pylist()[0]["measurement_name"] == "temperature"
