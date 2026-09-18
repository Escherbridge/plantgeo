"""The direct sensors merge/forward adapter: whole-block replace, stale-discard, and the status branches.

Ported from `test_weather_observations_adapter.py`'s shape (status branches), but the merge tests
themselves are new: this lane's merge unit is a whole (sensor_id, observed_day) BLOCK, not one grain,
so "append an unseen grain" / "refresh a repeat grain" do not apply here the way they do for
weather-observations. See `adapter.py`'s module docstring for why.

The governed-absence tests DIVERGE from weather-observations on purpose. That lane still refuses a
`status=absent` day; this one reconciles it when the poll carries rows, because the retired
`pipeline/lanes/sensors.py` governed days absent that NWS later answered with readings and the
refusal held `sensors-direct-forward`'s breaker for a week (2026-09-06..13). The inverse -- a marker
with NO polled rows -- must stay untouched, and the tests below pin both directions.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.paths import absence_marker_path
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.sensors.adapter import (
    SENSORS_DIRECT_KIND,
    DirectSensorsError,
    DirectSensorsForwardAdapter,
    merge_sensors_day,
)
from agri_data_service.pipeline.parquet.derivation import govern_day_absent
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.sensors import SENSORS_SCHEMA, SENSORS_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import Callable

DAY = date(2026, 9, 3)
FIRST_INSTANT = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
SECOND_INSTANT = FIRST_INSTANT + timedelta(hours=6)
#: The producer that governed the stranded days absent; it was deleted 2026-09-07, so the marker is
#: the only surviving record of its reasoning and the retraction must carry it forward verbatim.
RETIRED_PRODUCER_RUN_ID = "pipeline/lanes/sensors.py:export_sensors_day:20260906T012000Z"


class _SessionDouble:
    async def rollback(self) -> None:
        return None


class _DeleteRefusedOnceBackend(RecordingBackend):
    """`RecordingBackend.refuses_delete_of` refuses forever; a storage blip refuses ONCE and then succeeds."""

    def __init__(self, *, store_key: Callable[[ObjectStore], str]) -> None:
        super().__init__()
        self._store_key = store_key
        self._refuse_once: str | None = None

    def arm(self, store: ObjectStore) -> None:
        self._refuse_once = self._store_key(store)

    def delete(self, key: str) -> None:
        if key == self._refuse_once:
            self._refuse_once = None
            raise OSError(f"bucket refused to delete {key} once")
        super().delete(key)


def _retired_producer_absence() -> GovernedAbsence:
    """The shape of marker the retired Postgres-reading adapter left on 2026-09-05/06."""
    return GovernedAbsence(
        reason="the sensors export for this day produced zero rows from geo.features",
        upstream_response='{"producer": "pipeline/lanes/sensors.py", "rows": 0}',
        recorded_at=datetime(2026, 9, 6, 1, 20, tzinfo=UTC),
        run_id=RETIRED_PRODUCER_RUN_ID,
    )


def _govern_absent_at_every_tier(store: ObjectStore, absence: GovernedAbsence) -> None:
    """Seed the whole absence ladder the way the retired producer's finalizer did, coarse rungs first."""
    govern_day_absent(store, absence, layer=SENSORS_STREAM, kind=SENSORS_DIRECT_KIND, day=DAY)


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

    def test_a_replayed_incomplete_attempt_rewrites_the_checkpointed_merge_verbatim(self) -> None:
        """Crash recovery: the second attempt must republish what the first INTENDED, not re-merge the bucket."""
        store = ObjectStore(RecordingBackend())
        adapter = DirectSensorsForwardAdapter(_table([_row(sensor_id="KBOI", value=18.0)]))
        asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="a"))  # a part with no completion marker
        checkpointed = adapter.merge
        assert checkpointed is not None
        # Another hand rewrites the still-`incomplete` day underneath the retry.
        store.write_partition(
            _table([_row(sensor_id="KPDX", value=30.0)]),
            layer=SENSORS_STREAM,
            kind=SENSORS_DIRECT_KIND,
            zoom=LANE_BASE_ZOOM_TIER,
            day=DAY,
        )

        asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="a"))

        published = store.read_partition(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY)
        assert published.to_pylist() == checkpointed.table.to_pylist()
        assert adapter.merge is checkpointed
        assert adapter.absence_overturned is None


class TestGovernedAbsenceReconciliation:
    """Rows disprove a marker; no rows leave it alone; a conflict and a corrupt marker stay an admin's call."""

    def test_an_absence_disproven_by_polled_rows_is_retracted_at_every_tier_and_the_rows_written(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The production defect: 2,377 polled rows for a day the retired producer had marked empty."""
        store = ObjectStore(RecordingBackend())
        original = _retired_producer_absence()
        _govern_absent_at_every_tier(store, original)
        adapter = DirectSensorsForwardAdapter(_table([_row(sensor_id="KBOI"), _row(sensor_id="KPDX")]))

        result = asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="sensors-direct-forward-test"))

        assert result.row_count == 2
        assert result.absence_recorded is False
        assert store.partition_exists(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY) is True
        for tier in ZOOM_TIERS:
            assert store.absence_exists(SENSORS_STREAM, SENSORS_DIRECT_KIND, tier, DAY) is False, tier
        overturned = adapter.absence_overturned
        assert overturned is not None, "the forward reads this to put the retraction on the day's checkpoint"
        assert overturned.day == DAY
        assert overturned.tiers == ZOOM_TIERS
        assert overturned.incoming_rows == 2
        assert overturned.overturned_by_run_id == "sensors-direct-forward-test"
        for marker in overturned.markers:
            assert marker.absence == original, f"z{marker.tier} provenance must survive the retraction intact"
        event = json.loads(capsys.readouterr().err.strip())
        assert event["event"] == "sensors_forward_absence_retracted"
        assert event["layer"] == SENSORS_STREAM
        assert event["run_id"] == "sensors-direct-forward-test"
        assert event["overturned_by"] == "observed_rows"
        assert event["tiers"] == list(ZOOM_TIERS)
        assert {marker["run_id"] for marker in event["markers"]} == {RETIRED_PRODUCER_RUN_ID}
        assert event["markers"][-1] == {
            "tier": LANE_BASE_ZOOM_TIER,
            "reason": original.reason,
            "upstream_response": original.upstream_response,
            "recorded_at": "2026-09-06T01:20:00+00:00",
            "run_id": RETIRED_PRODUCER_RUN_ID,
        }

    def test_an_absence_with_no_polled_rows_is_respected_and_no_marker_is_touched(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """DO NOT WEAKEN. A poll that says nothing about a day may never manufacture data over its absence."""
        store = ObjectStore(RecordingBackend())
        _govern_absent_at_every_tier(store, _retired_producer_absence())
        adapter = DirectSensorsForwardAdapter(_table([]))

        with pytest.raises(DirectSensorsError, match="empty poll"):
            asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="test"))

        for tier in ZOOM_TIERS:
            assert store.absence_exists(SENSORS_STREAM, SENSORS_DIRECT_KIND, tier, DAY) is True, tier
        assert store.partition_exists(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY) is False
        assert adapter.absence_overturned is None
        assert capsys.readouterr().err == ""

    def test_a_clear_that_fails_mid_ladder_is_completed_by_the_retry_and_every_tier_is_reported(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """z0/z5 cleared, z9 refused: the base rung still stands, so the retry re-selects the day and finishes."""
        backend = _DeleteRefusedOnceBackend(
            store_key=lambda store: store.key_for(absence_marker_path(SENSORS_STREAM, SENSORS_DIRECT_KIND, 9, DAY))
        )
        store = ObjectStore(backend)
        backend.arm(store)
        _govern_absent_at_every_tier(store, _retired_producer_absence())
        adapter = DirectSensorsForwardAdapter(_table([_row(sensor_id="KBOI")]))

        with pytest.raises(OSError, match="refused to delete"):
            asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="r"))

        assert [store.absence_exists(SENSORS_STREAM, SENSORS_DIRECT_KIND, t, DAY) for t in ZOOM_TIERS] == [
            False,
            False,
            True,
            True,
        ]
        assert store.partition_exists(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY) is False
        partial = adapter.absence_overturned
        assert partial is not None, "the two markers already gone must be on record before the retry"
        assert partial.tiers == (0, 5)
        first_event = json.loads(capsys.readouterr().err.strip())
        assert first_event["tiers"] == [0, 5]

        asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="r"))

        for tier in ZOOM_TIERS:
            assert store.absence_exists(SENSORS_STREAM, SENSORS_DIRECT_KIND, tier, DAY) is False, tier
        assert store.partition_exists(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY) is True
        completed = adapter.absence_overturned
        assert completed is not None
        assert completed.tiers == ZOOM_TIERS, "the union of both attempts, not only the survivors"
        assert {marker.absence.run_id for marker in completed.markers} == {RETIRED_PRODUCER_RUN_ID}
        second_event = json.loads(capsys.readouterr().err.strip())
        assert second_event["tiers"] == list(ZOOM_TIERS)

    def test_a_malformed_poll_never_reaches_the_marker(self) -> None:
        """The merge's own validation runs BEFORE any retraction, so a bad poll cannot cost a marker."""
        store = ObjectStore(RecordingBackend())
        _govern_absent_at_every_tier(store, _retired_producer_absence())
        wrong_day_row = _row()
        wrong_day_row["observed_day"] = DAY + timedelta(days=1)

        with pytest.raises(DirectSensorsError, match="observed_day"):
            asyncio.run(
                DirectSensorsForwardAdapter(_table([wrong_day_row]))(_SessionDouble(), store, day=DAY, run_id="t")
            )

        for tier in ZOOM_TIERS:
            assert store.absence_exists(SENSORS_STREAM, SENSORS_DIRECT_KIND, tier, DAY) is True, tier

    def test_a_day_holding_both_parts_and_a_marker_is_still_refused_for_an_admin(self) -> None:
        """`conflict` is two contradictory claims already published; a poll may not pick between them."""
        backend = RecordingBackend()
        store = ObjectStore(backend)
        asyncio.run(DirectSensorsForwardAdapter(_table([_row()]))(_SessionDouble(), store, day=DAY, run_id="a"))
        backend.put(
            store.key_for(absence_marker_path(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY)),
            _retired_producer_absence().to_json_bytes(),
            content_type="application/json",
        )
        adapter = DirectSensorsForwardAdapter(_table([_row(observed_at=SECOND_INSTANT, value=25.0)]))

        with pytest.raises(DirectSensorsError, match="status=conflict"):
            asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="b"))

        assert store.absence_exists(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY) is True
        published = store.read_partition(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY)
        assert published.to_pylist()[0]["value"] == pytest.approx(20.0)
        assert adapter.absence_overturned is None

    def test_an_undecodable_marker_is_refused_rather_than_retracted_without_its_provenance(self) -> None:
        """A marker whose reasoning cannot be read cannot be carried forward, so it is not deleted blind."""
        backend = RecordingBackend()
        store = ObjectStore(backend)
        backend.put(
            store.key_for(absence_marker_path(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY)),
            b"not a governed absence",
            content_type="application/json",
        )
        adapter = DirectSensorsForwardAdapter(_table([_row()]))

        with pytest.raises(DirectSensorsError, match="cannot decode"):
            asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="t"))

        assert store.absence_exists(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY) is True
        assert store.partition_exists(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, DAY) is False
        assert adapter.absence_overturned is None
