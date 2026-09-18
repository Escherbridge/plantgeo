"""The direct weather-observations merge/forward adapter: append, refresh, and the status branches.

Ported from `tests/parquet/test_direct_writers.py`'s water-gauges merge tests, adjusted for the
three-column `(latitude, longitude, observed_at)` grain and the fact this lane never needs an
"ambiguous refresh" refusal (see `adapter.py`'s module docstring for why not).

The governed-absence tests mirror `test_sensors_direct_adapter.py::TestGovernedAbsenceReconciliation`
on purpose. Until 2026-09-15 this lane REFUSED a `status=absent` day outright -- the construct that
held `sensors-direct-forward`'s breaker for a week once a retired Postgres adapter had governed days
absent that the live poll later answered. Rows now disprove the marker; a marker with NO polled rows
must stay untouched, and the tests below pin both directions.
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.paths import absence_marker_path
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.weather_observations.adapter import (
    WEATHER_OBSERVATIONS_DIRECT_KIND,
    DirectWeatherObservationsError,
    DirectWeatherObservationsForwardAdapter,
    merge_weather_observations_day,
)
from agri_data_service.pipeline.parquet.derivation import govern_day_absent
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.weather_observations import (
    WEATHER_OBSERVATIONS_SCHEMA,
    WEATHER_OBSERVATIONS_STREAM,
)
from tests.parquet.test_objectstore_writer import RecordingBackend

LAYER = WEATHER_OBSERVATIONS_STREAM
KIND = WEATHER_OBSERVATIONS_DIRECT_KIND
DAY = date(2026, 9, 3)
FIRST_INSTANT = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
SECOND_INSTANT = FIRST_INSTANT + timedelta(hours=1)
#: The producer that could have governed a day absent; it was retired 2026-09-07, so the marker is
#: the only surviving record of its reasoning and the retraction must carry it forward verbatim.
RETIRED_PRODUCER_RUN_ID = "pipeline/lanes/weather_observations.py:export_weather_observations_day:20260905T020000Z"


class _SessionDouble:
    async def rollback(self) -> None:
        return None


class _RefusesOneDelete(RecordingBackend):
    """A bucket that refuses exactly one named delete, then behaves -- the transient the retry exists for."""

    def __init__(self, refused_key: str) -> None:
        super().__init__()
        self.refuse_once: str | None = refused_key

    def delete(self, key: str) -> None:
        if key == self.refuse_once:
            self.refuse_once = None
            raise OSError(f"bucket refused to delete {key}")
        super().delete(key)


def _retired_producer_absence() -> GovernedAbsence:
    """The shape of marker the retired Postgres-reading adapter left on a zero-row `geo.features` day."""
    return GovernedAbsence(
        reason="the ingest cron never ran this day",
        upstream_response='{"producer": "pipeline/lanes/weather_observations.py", "rows": 0}',
        recorded_at=datetime(2026, 9, 5, 2, 0, tzinfo=UTC),
        run_id=RETIRED_PRODUCER_RUN_ID,
    )


def _govern_absent_at_every_tier(store: ObjectStore, absence: GovernedAbsence) -> None:
    """Seed the whole absence ladder the way the retired producer's finalizer did, coarse rungs first."""
    govern_day_absent(store, absence, layer=WEATHER_OBSERVATIONS_STREAM, kind=WEATHER_OBSERVATIONS_DIRECT_KIND, day=DAY)


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


def _poll(*, latitude: float = 46.0, temperature_c: float = 20.0, observed_at: datetime = FIRST_INSTANT) -> pa.Table:
    return _table(
        [_row(latitude=latitude, observed_at=observed_at, ingested_at=observed_at, temperature_c=temperature_c)]
    )


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
        adapter = DirectWeatherObservationsForwardAdapter(_poll())

        result = asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="test"))

        assert result.row_count == 1
        assert store.partition_exists(WEATHER_OBSERVATIONS_STREAM, "observed", LANE_BASE_ZOOM_TIER, DAY) is True
        assert adapter.absence_overturned is None

    def test_a_second_poll_the_same_day_merges_into_the_first(self) -> None:
        store = ObjectStore(RecordingBackend())
        second_poll = _poll(latitude=47.0, temperature_c=21.0, observed_at=SECOND_INSTANT)
        asyncio.run(DirectWeatherObservationsForwardAdapter(_poll())(_SessionDouble(), store, day=DAY, run_id="a"))

        second_result = asyncio.run(
            DirectWeatherObservationsForwardAdapter(second_poll)(_SessionDouble(), store, day=DAY, run_id="b")
        )

        assert second_result.row_count == 2
        published = store.read_partition(WEATHER_OBSERVATIONS_STREAM, "observed", LANE_BASE_ZOOM_TIER, DAY)
        assert published.num_rows == 2

    def test_a_replayed_incomplete_attempt_rewrites_the_checkpointed_merge_verbatim(self) -> None:
        """Crash recovery: the second attempt must republish what the first INTENDED, not re-merge the bucket."""
        store = ObjectStore(RecordingBackend())
        adapter = DirectWeatherObservationsForwardAdapter(_poll(temperature_c=18.0))
        asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="a"))  # a part with no completion marker
        checkpointed = adapter.merge
        assert checkpointed is not None
        # Another hand rewrites the still-`incomplete` day underneath the retry.
        store.write_partition(
            _poll(latitude=47.0, temperature_c=30.0),
            layer=WEATHER_OBSERVATIONS_STREAM,
            kind=WEATHER_OBSERVATIONS_DIRECT_KIND,
            zoom=LANE_BASE_ZOOM_TIER,
            day=DAY,
        )

        asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="a"))

        published = store.read_partition(LAYER, KIND, LANE_BASE_ZOOM_TIER, DAY)
        assert published.to_pylist() == checkpointed.table.to_pylist()
        assert adapter.merge is checkpointed
        assert adapter.absence_overturned is None


class TestGovernedAbsenceReconciliation:
    """Rows disprove a marker; no rows leave it alone; a conflict and a corrupt marker stay an admin's call."""

    def test_an_absence_disproven_by_polled_rows_is_retracted_at_every_tier_and_the_rows_written(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """INVERTED 2026-09-15: this exact setup used to assert `match="status=absent"`; the rows now win."""
        store = ObjectStore(RecordingBackend())
        original = _retired_producer_absence()
        _govern_absent_at_every_tier(store, original)
        adapter = DirectWeatherObservationsForwardAdapter(
            _table(
                [
                    _row(latitude=46.0, observed_at=FIRST_INSTANT, ingested_at=FIRST_INSTANT, temperature_c=20.0),
                    _row(latitude=47.0, observed_at=FIRST_INSTANT, ingested_at=FIRST_INSTANT, temperature_c=18.0),
                ]
            )
        )

        run_id = "weather-observations-direct-forward-test"
        result = asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id=run_id))

        assert result.row_count == 2
        assert result.absence_recorded is False
        assert store.partition_exists(LAYER, KIND, LANE_BASE_ZOOM_TIER, DAY)
        for tier in ZOOM_TIERS:
            assert store.absence_exists(LAYER, KIND, tier, DAY) is False, tier
        overturned = adapter.absence_overturned
        assert overturned is not None, "the forward reads this to put the retraction on the day's checkpoint"
        assert overturned.day == DAY
        assert overturned.tiers == ZOOM_TIERS
        assert overturned.incoming_rows == 2
        assert overturned.overturned_by_run_id == "weather-observations-direct-forward-test"
        for marker in overturned.markers:
            assert marker.absence == original, f"z{marker.tier} provenance must survive the retraction verbatim"
        event = json.loads(capsys.readouterr().err.strip())
        assert event["event"] == "weather_observations_forward_absence_retracted"
        assert event["layer"] == WEATHER_OBSERVATIONS_STREAM
        assert event["run_id"] == "weather-observations-direct-forward-test"
        assert event["overturned_by"] == "observed_rows"
        assert event["tiers"] == list(ZOOM_TIERS)
        assert {marker["run_id"] for marker in event["markers"]} == {RETIRED_PRODUCER_RUN_ID}
        assert event["markers"][-1] == {
            "tier": LANE_BASE_ZOOM_TIER,
            "reason": original.reason,
            "upstream_response": original.upstream_response,
            "recorded_at": "2026-09-05T02:00:00+00:00",
            "run_id": RETIRED_PRODUCER_RUN_ID,
        }

    def test_an_absence_with_no_polled_rows_is_respected_and_no_marker_is_touched(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """DO NOT WEAKEN. A poll that says nothing about a day may never manufacture data over its absence."""
        store = ObjectStore(RecordingBackend())
        _govern_absent_at_every_tier(store, _retired_producer_absence())
        adapter = DirectWeatherObservationsForwardAdapter(_table([]))

        with pytest.raises(DirectWeatherObservationsError, match="empty poll"):
            asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="test"))

        for tier in ZOOM_TIERS:
            assert store.absence_exists(LAYER, KIND, tier, DAY) is True, tier
        assert store.partition_exists(LAYER, KIND, LANE_BASE_ZOOM_TIER, DAY) is False
        assert adapter.absence_overturned is None
        assert capsys.readouterr().err == ""

    def test_a_malformed_poll_never_reaches_the_marker(self) -> None:
        """The merge's own validation runs BEFORE any retraction, so a bad poll cannot cost a marker."""
        store = ObjectStore(RecordingBackend())
        _govern_absent_at_every_tier(store, _retired_producer_absence())
        wrong_day_row = _row(latitude=46.0, observed_at=FIRST_INSTANT, ingested_at=FIRST_INSTANT, temperature_c=20.0)
        wrong_day_row["observed_day"] = DAY + timedelta(days=1)

        with pytest.raises(DirectWeatherObservationsError, match="observed_day"):
            asyncio.run(
                DirectWeatherObservationsForwardAdapter(_table([wrong_day_row]))(
                    _SessionDouble(), store, day=DAY, run_id="t"
                )
            )

        for tier in ZOOM_TIERS:
            assert store.absence_exists(LAYER, KIND, tier, DAY) is True, tier

    def test_a_day_holding_both_parts_and_a_marker_is_still_refused_for_an_admin(self) -> None:
        """`conflict` is two contradictory claims already published; a poll may not pick between them."""
        backend = RecordingBackend()
        store = ObjectStore(backend)
        asyncio.run(DirectWeatherObservationsForwardAdapter(_poll())(_SessionDouble(), store, day=DAY, run_id="a"))
        backend.put(
            store.key_for(absence_marker_path(LAYER, KIND, LANE_BASE_ZOOM_TIER, DAY)),
            _retired_producer_absence().to_json_bytes(),
            content_type="application/json",
        )
        adapter = DirectWeatherObservationsForwardAdapter(_poll(observed_at=SECOND_INSTANT, temperature_c=25.0))

        with pytest.raises(DirectWeatherObservationsError, match="status=conflict"):
            asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="b"))

        assert store.absence_exists(LAYER, KIND, LANE_BASE_ZOOM_TIER, DAY)
        published = store.read_partition(LAYER, KIND, LANE_BASE_ZOOM_TIER, DAY)
        assert published.to_pylist()[0]["temperature_c"] == pytest.approx(20.0)
        assert adapter.absence_overturned is None

    def test_a_clear_refused_part_way_is_finished_by_the_retry_and_the_union_of_tiers_is_reported(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The bucket refuses ONE delete mid-ladder; the bounded retry re-reads the survivors and clears them."""
        original = _retired_producer_absence()
        store = ObjectStore(RecordingBackend())
        backend = _RefusesOneDelete(store.key_for(absence_marker_path(LAYER, KIND, 9, DAY)))
        store = ObjectStore(backend)
        _govern_absent_at_every_tier(store, original)
        adapter = DirectWeatherObservationsForwardAdapter(_poll())

        with pytest.raises(OSError, match="refused to delete"):
            asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="t"))

        assert [store.absence_exists(LAYER, KIND, tier, DAY) for tier in ZOOM_TIERS] == [False, False, True, True]
        assert store.partition_exists(LAYER, KIND, LANE_BASE_ZOOM_TIER, DAY) is False
        first = adapter.absence_overturned
        assert first is not None, "the removals before the refusal must be on record"
        assert first.tiers == (0, 5), "the removals before the refusal must be on record"

        result = asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="t"))

        assert result.row_count == 1
        for tier in ZOOM_TIERS:
            assert store.absence_exists(LAYER, KIND, tier, DAY) is False, tier
        union = adapter.absence_overturned
        assert union is not None
        assert union.tiers == ZOOM_TIERS
        assert all(marker.absence == original for marker in union.markers)
        events = [json.loads(line) for line in capsys.readouterr().err.strip().splitlines()]
        assert [event["tiers"] for event in events] == [[0, 5], [0, 5, 9, 13]], (
            "each attempt announces the union so far; the last event per (run_id, day) is the truth"
        )

    def test_an_undecodable_marker_is_refused_rather_than_retracted_without_its_provenance(self) -> None:
        """A marker whose reasoning cannot be read cannot be carried forward, so it is not deleted blind."""
        backend = RecordingBackend()
        store = ObjectStore(backend)
        backend.put(
            store.key_for(absence_marker_path(LAYER, KIND, LANE_BASE_ZOOM_TIER, DAY)),
            b"not a governed absence",
            content_type="application/json",
        )
        adapter = DirectWeatherObservationsForwardAdapter(_poll())

        with pytest.raises(DirectWeatherObservationsError, match="cannot decode"):
            asyncio.run(adapter(_SessionDouble(), store, day=DAY, run_id="t"))

        assert store.absence_exists(LAYER, KIND, LANE_BASE_ZOOM_TIER, DAY)
        assert store.partition_exists(LAYER, KIND, LANE_BASE_ZOOM_TIER, DAY) is False
        assert adapter.absence_overturned is None
