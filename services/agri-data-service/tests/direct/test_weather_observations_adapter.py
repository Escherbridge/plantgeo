"""The direct weather-observations merge/forward adapter: append, refresh, and the status branches.

Ported from `tests/parquet/test_direct_writers.py`'s water-gauges merge tests, adjusted for the
three-column `(latitude, longitude, observed_at)` grain and the fact this lane never needs an
"ambiguous refresh" refusal (see `adapter.py`'s module docstring for why not).
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.pipeline.direct.weather_observations.adapter import (
    DirectWeatherObservationsError,
    DirectWeatherObservationsForwardAdapter,
    merge_weather_observations_day,
)
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.weather_observations import (
    WEATHER_OBSERVATIONS_SCHEMA,
    WEATHER_OBSERVATIONS_STREAM,
)
from tests.parquet.test_objectstore_writer import RecordingBackend

DAY = date(2026, 9, 3)
FIRST_INSTANT = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
SECOND_INSTANT = FIRST_INSTANT + timedelta(hours=1)


class _SessionDouble:
    async def rollback(self) -> None:
        return None


def _row(*, latitude: float, observed_at: datetime, ingested_at: datetime, temperature_c: float) -> dict[str, object]:
    return {
        "latitude": latitude,
        "longitude": -117.25,
        "observed_at": observed_at,
        "observed_day": DAY,
        "external_id": f"{latitude:.4f}:-117.2500:{observed_at.isoformat()}",
        "temperature_c": temperature_c,
        "relative_humidity_pct": 40.0,
        "wind_speed_ms": 3.0,
        "wind_direction_deg": 180.0,
        "precipitation_mm": 0.0,
        "source": "Open-Meteo",
        "feature_id": f"direct:{latitude:.4f}:-117.2500:{observed_at.isoformat()}",
        "ingested_at": ingested_at,
    }


def _table(rows: list[dict[str, object]]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=WEATHER_OBSERVATIONS_SCHEMA.arrow_schema)


class TestMergeWeatherObservationsDay:
    def test_appends_an_unseen_grain_and_preserves_the_existing_rows_verbatim(self) -> None:
        existing = _table(
            [_row(latitude=46.0, observed_at=FIRST_INSTANT, ingested_at=FIRST_INSTANT, temperature_c=20.0)]
        )
        incoming = _table(
            [_row(latitude=47.0, observed_at=FIRST_INSTANT, ingested_at=FIRST_INSTANT, temperature_c=18.0)]
        )

        merged = merge_weather_observations_day(existing, incoming, day=DAY)

        rows = merged.table.to_pylist()
        assert rows[0] == existing.to_pylist()[0]
        assert len(rows) == 2
        assert merged.existing_rows == 1
        assert merged.added_rows == 1
        assert merged.updated_rows == 0

    def test_a_repeat_point_instant_refreshes_source_fields_but_keeps_first_ingested_at(self) -> None:
        """A later poll re-answering the same instant is a self-heal, not a new reading."""
        existing = _table(
            [_row(latitude=46.0, observed_at=FIRST_INSTANT, ingested_at=FIRST_INSTANT, temperature_c=20.0)]
        )
        incoming = _table(
            [_row(latitude=46.0, observed_at=FIRST_INSTANT, ingested_at=SECOND_INSTANT, temperature_c=99.0)]
        )

        merged = merge_weather_observations_day(existing, incoming, day=DAY)

        row = merged.table.to_pylist()[0]
        assert row["temperature_c"] == pytest.approx(99.0)
        assert row["ingested_at"] == FIRST_INSTANT, "ingested_at is provenance, never refreshed by a repeat poll"
        assert merged.added_rows == 0
        assert merged.updated_rows == 1

    def test_refuses_an_empty_incoming_poll(self) -> None:
        with pytest.raises(DirectWeatherObservationsError, match="empty poll"):
            merge_weather_observations_day(None, _table([]), day=DAY)

    def test_refuses_a_duplicate_grain_within_one_incoming_poll(self) -> None:
        duplicated = _row(latitude=46.0, observed_at=FIRST_INSTANT, ingested_at=FIRST_INSTANT, temperature_c=20.0)
        incoming = _table([duplicated, dict(duplicated)])

        with pytest.raises(DirectWeatherObservationsError, match="duplicate grain"):
            merge_weather_observations_day(None, incoming, day=DAY)

    def test_refuses_a_row_named_for_the_wrong_day(self) -> None:
        wrong_day_row = _row(latitude=46.0, observed_at=FIRST_INSTANT, ingested_at=FIRST_INSTANT, temperature_c=20.0)
        wrong_day_row["observed_day"] = DAY + timedelta(days=1)

        with pytest.raises(DirectWeatherObservationsError, match="observed_day"):
            merge_weather_observations_day(None, _table([wrong_day_row]), day=DAY)


class TestDirectWeatherObservationsForwardAdapter:
    def test_a_missing_day_is_written_whole(self) -> None:
        store = ObjectStore(RecordingBackend())
        incoming = _table(
            [_row(latitude=46.0, observed_at=FIRST_INSTANT, ingested_at=FIRST_INSTANT, temperature_c=20.0)]
        )
        adapter = DirectWeatherObservationsForwardAdapter(incoming)

        result = asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="test"))

        assert result.row_count == 1
        assert store.partition_exists(WEATHER_OBSERVATIONS_STREAM, "observed", LANE_BASE_ZOOM_TIER, DAY) is True

    def test_a_second_poll_the_same_day_merges_into_the_first(self) -> None:
        store = ObjectStore(RecordingBackend())
        first_poll = _table(
            [_row(latitude=46.0, observed_at=FIRST_INSTANT, ingested_at=FIRST_INSTANT, temperature_c=20.0)]
        )
        second_poll = _table(
            [_row(latitude=47.0, observed_at=SECOND_INSTANT, ingested_at=SECOND_INSTANT, temperature_c=21.0)]
        )
        asyncio.run(DirectWeatherObservationsForwardAdapter(first_poll)(_SessionDouble(), store, day=DAY, run_id="a"))

        second_result = asyncio.run(
            DirectWeatherObservationsForwardAdapter(second_poll)(_SessionDouble(), store, day=DAY, run_id="b")
        )

        assert second_result.row_count == 2
        published = store.read_partition(WEATHER_OBSERVATIONS_STREAM, "observed", LANE_BASE_ZOOM_TIER, DAY)
        assert published.num_rows == 2

    def test_refuses_to_merge_over_a_governed_absence(self) -> None:
        store = ObjectStore(RecordingBackend())
        store.write_absence(
            GovernedAbsence(
                reason="the ingest cron never ran this day",
                upstream_response="{}",
                recorded_at=datetime(2026, 9, 3, tzinfo=UTC),
                run_id="historical",
            ),
            layer=WEATHER_OBSERVATIONS_STREAM,
            kind="observed",
            zoom=LANE_BASE_ZOOM_TIER,
            day=DAY,
        )
        incoming = _table(
            [_row(latitude=46.0, observed_at=FIRST_INSTANT, ingested_at=FIRST_INSTANT, temperature_c=20.0)]
        )
        adapter = DirectWeatherObservationsForwardAdapter(incoming)

        with pytest.raises(DirectWeatherObservationsError, match="status=absent"):
            asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="test"))
