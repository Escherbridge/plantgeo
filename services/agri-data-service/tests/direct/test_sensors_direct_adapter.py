"""The direct sensors merge/forward adapter: whole-block replace, stale-discard, and the status branches.

Ported from `test_weather_observations_adapter.py`'s shape (status branches, governed-absence
refusal), but the merge tests themselves are new: this lane's merge unit is a whole
(sensor_id, observed_day) BLOCK, not one grain, so "append an unseen grain" / "refresh a repeat
grain" do not apply here the way they do for weather-observations. See `adapter.py`'s module
docstring for why.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.pipeline.direct.sensors.adapter import (
    DirectSensorsError,
    DirectSensorsForwardAdapter,
    merge_sensors_day,
)
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.sensors import SENSORS_SCHEMA, SENSORS_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

DAY = date(2026, 9, 3)
FIRST_INSTANT = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
SECOND_INSTANT = FIRST_INSTANT + timedelta(hours=6)


class _SessionDouble:
    async def rollback(self) -> None:
        return None


def _row(
    *,
    sensor_id: str = "KBOI",
    observed_at: datetime = FIRST_INSTANT,
    measurement_name: str = "temperature",
    value: float = 20.0,
) -> dict[str, object]:
    return {
        "sensor_id": sensor_id,
        "station_name": "Boise Air Terminal",
        "network": "ASOS",
        "observed_day": DAY,
        "observed_at": observed_at,
        "measurement_name": measurement_name,
        "value": value,
        "unit_code": "wmoUnit:degC",
        "quality_control": None,
        "feature_id": f"direct:{sensor_id}:{observed_at.isoformat()}",
        "data_available_at": None,
        "station_longitude": -116.2228,
        "station_latitude": 43.5644,
    }


def _table(rows: list[dict[str, object]]) -> pa.Table:
    return pa.Table.from_pylist(rows, schema=SENSORS_SCHEMA.arrow_schema)


class TestMergeSensorsDay:
    def test_adds_an_unseen_stations_block_and_preserves_the_existing_block_verbatim(self) -> None:
        existing = _table([_row(sensor_id="KBOI", observed_at=FIRST_INSTANT)])
        incoming = _table([_row(sensor_id="KPDX", observed_at=FIRST_INSTANT)])

        merged = merge_sensors_day(existing, incoming, day=DAY)

        sensor_ids = {row["sensor_id"] for row in merged.table.to_pylist()}
        assert sensor_ids == {"KBOI", "KPDX"}
        assert merged.existing_rows == 1
        assert merged.added_blocks == 1
        assert merged.added_rows == 1
        assert merged.replaced_blocks == 0
        assert merged.stale_blocks == 0

    def test_a_newer_report_replaces_every_row_of_the_published_block(self) -> None:
        """A station that stopped reporting `windGust` must lose that stale row, not just gain a new one."""
        existing = _table(
            [
                _row(observed_at=FIRST_INSTANT, measurement_name="temperature", value=18.0),
                _row(observed_at=FIRST_INSTANT, measurement_name="windGust", value=5.0),
            ]
        )
        incoming = _table([_row(observed_at=SECOND_INSTANT, measurement_name="temperature", value=21.0)])

        merged = merge_sensors_day(existing, incoming, day=DAY)

        rows = merged.table.to_pylist()
        assert len(rows) == 1
        assert rows[0]["measurement_name"] == "temperature"
        assert rows[0]["value"] == pytest.approx(21.0)
        assert rows[0]["observed_at"] == SECOND_INSTANT
        assert merged.replaced_blocks == 1
        assert merged.updated_rows == 1
        assert merged.stale_blocks == 0

    def test_an_older_incoming_report_is_discarded_and_the_published_block_is_kept(self) -> None:
        existing = _table([_row(observed_at=SECOND_INSTANT, value=21.0)])
        incoming = _table([_row(observed_at=FIRST_INSTANT, value=18.0)])

        merged = merge_sensors_day(existing, incoming, day=DAY)

        rows = merged.table.to_pylist()
        assert len(rows) == 1
        assert rows[0]["observed_at"] == SECOND_INSTANT
        assert rows[0]["value"] == pytest.approx(21.0)
        assert merged.stale_blocks == 1
        assert merged.stale_rows == 1
        assert merged.replaced_blocks == 0

    def test_a_repeat_report_at_the_identical_instant_refreshes_the_block(self) -> None:
        """The rolling window can resurface the SAME instant on a later poll; treat it as a refresh, not a skip."""
        existing = _table([_row(observed_at=FIRST_INSTANT, value=18.0)])
        incoming = _table([_row(observed_at=FIRST_INSTANT, value=18.0, measurement_name="temperature")])

        merged = merge_sensors_day(existing, incoming, day=DAY)

        assert merged.replaced_blocks == 1
        assert merged.stale_blocks == 0

    def test_refuses_an_empty_incoming_poll(self) -> None:
        with pytest.raises(DirectSensorsError, match="empty poll"):
            merge_sensors_day(None, _table([]), day=DAY)

    def test_refuses_a_row_named_for_the_wrong_day(self) -> None:
        wrong_day_row = _row()
        wrong_day_row["observed_day"] = DAY + timedelta(days=1)

        with pytest.raises(DirectSensorsError, match="observed_day"):
            merge_sensors_day(None, _table([wrong_day_row]), day=DAY)

    def test_refuses_an_incoming_block_whose_rows_disagree_on_observed_at(self) -> None:
        """Two rows for one (sensor_id, day) with different observed_at means two reports were merged
        into one block upstream -- a `rows.py` invariant violation this adapter must not merge past."""
        mismatched = _table(
            [
                _row(observed_at=FIRST_INSTANT, measurement_name="temperature"),
                _row(observed_at=SECOND_INSTANT, measurement_name="windGust"),
            ]
        )

        with pytest.raises(DirectSensorsError, match="distinct observed_at"):
            merge_sensors_day(None, mismatched, day=DAY)

    def test_a_missing_published_day_treats_every_incoming_block_as_added(self) -> None:
        incoming = _table([_row(sensor_id="KBOI"), _row(sensor_id="KPDX")])

        merged = merge_sensors_day(None, incoming, day=DAY)

        assert merged.existing_rows == 0
        assert merged.added_blocks == 2
        assert merged.added_rows == 2


class TestDirectSensorsForwardAdapter:
    def test_a_missing_day_is_written_whole(self) -> None:
        store = ObjectStore(RecordingBackend())
        incoming = _table([_row(sensor_id="KBOI")])
        adapter = DirectSensorsForwardAdapter(incoming)

        result = asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="test"))

        assert result.row_count == 1
        assert store.partition_exists(SENSORS_STREAM, "observed", LANE_BASE_ZOOM_TIER, DAY) is True

    def test_a_second_poll_the_same_day_merges_a_newer_report_in(self) -> None:
        store = ObjectStore(RecordingBackend())
        first_poll = _table([_row(sensor_id="KBOI", observed_at=FIRST_INSTANT, value=18.0)])
        second_poll = _table([_row(sensor_id="KBOI", observed_at=SECOND_INSTANT, value=21.0)])
        asyncio.run(DirectSensorsForwardAdapter(first_poll)(_SessionDouble(), store, day=DAY, run_id="a"))

        second_result = asyncio.run(
            DirectSensorsForwardAdapter(second_poll)(_SessionDouble(), store, day=DAY, run_id="b")
        )

        assert second_result.row_count == 1
        published = store.read_partition(SENSORS_STREAM, "observed", LANE_BASE_ZOOM_TIER, DAY)
        row = published.to_pylist()[0]
        assert row["value"] == pytest.approx(21.0)

    def test_a_stale_poll_the_same_day_does_not_regress_the_published_block(self) -> None:
        store = ObjectStore(RecordingBackend())
        first_poll = _table([_row(sensor_id="KBOI", observed_at=SECOND_INSTANT, value=21.0)])
        stale_poll = _table([_row(sensor_id="KBOI", observed_at=FIRST_INSTANT, value=18.0)])
        asyncio.run(DirectSensorsForwardAdapter(first_poll)(_SessionDouble(), store, day=DAY, run_id="a"))

        asyncio.run(DirectSensorsForwardAdapter(stale_poll)(_SessionDouble(), store, day=DAY, run_id="b"))

        published = store.read_partition(SENSORS_STREAM, "observed", LANE_BASE_ZOOM_TIER, DAY)
        row = published.to_pylist()[0]
        assert row["value"] == pytest.approx(21.0)

    def test_refuses_to_merge_over_a_governed_absence(self) -> None:
        store = ObjectStore(RecordingBackend())
        store.write_absence(
            GovernedAbsence(
                reason="no station published a reading this day",
                upstream_response="{}",
                recorded_at=datetime(2026, 9, 3, tzinfo=UTC),
                run_id="historical",
            ),
            layer=SENSORS_STREAM,
            kind="observed",
            zoom=LANE_BASE_ZOOM_TIER,
            day=DAY,
        )
        adapter = DirectSensorsForwardAdapter(_table([_row(sensor_id="KBOI")]))

        with pytest.raises(DirectSensorsError, match="status=absent"):
            asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="test"))
