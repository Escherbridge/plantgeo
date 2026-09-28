"""The sensors lane asks NWS only for what can still change a published station-day block.

Every test drives `poll_recent_sensor_readings` -- the lane's public fetch seam -- against a fake
api.weather.gov that holds real-shaped hourly reports with an "available at" instant (NWS publishes
a report some minutes after it was observed), and publishes each poll through the real adapter plus
the z13 completion marker the shared finalizer writes. The fake records the `start` of every
observation request, so the assertions are the request shape and the published winners.
See `pipeline/direct/sensors/AGENTS.md`, "Fetch only what can still change a published block".
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest

from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.foundation.parquet.paths import completion_marker_path
from agri_data_service.ingest.sensors import NWS_OBSERVATION_RETENTION
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.sensors.adapter import SENSORS_DIRECT_KIND, DirectSensorsForwardAdapter
from agri_data_service.pipeline.direct.sensors.rows import direct_sensor_tables
from agri_data_service.pipeline.direct.sensors.source import poll_recent_sensor_readings
from agri_data_service.pipeline.direct.sensors.watermark import SENSORS_WATERMARK_OVERLAP
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.sensors import SENSORS_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from agri_data_service.pipeline.direct.sensors.source import SensorsPollResult

BBOX = "-125,42,-111,49"
PUBLISH_LAG = timedelta(minutes=5)
#: Real Idaho ASOS stations, so the roster's bbox and network filters run on production-shaped input.
STATION_POINTS: dict[str, tuple[float, float]] = {"KBOI": (-116.2228, 43.5644), "KIDA": (-112.0707, 43.5146)}
UNSET_CLOCK = datetime(2026, 9, 1, tzinfo=UTC)


@dataclass
class FakeNws:
    """api.weather.gov as this lane sees it: one roster page, and per-station reports it has published."""

    roster: list[str]
    reports: dict[str, list[tuple[datetime, datetime]]] = field(default_factory=dict)
    clock: datetime = UNSET_CLOCK
    requested_starts: dict[str, datetime] = field(default_factory=dict)
    features_served: dict[str, int] = field(default_factory=dict)

    def report_hourly(self, station: str, *, first: datetime, last: datetime) -> None:
        """Publish one report at :53 of every hour, visible `PUBLISH_LAG` after it was observed."""
        instant = first.replace(minute=53, second=0, microsecond=0)
        while instant <= last:
            self.reports.setdefault(station, []).append((instant, instant + PUBLISH_LAG))
            instant += timedelta(hours=1)

    def report_once(self, station: str, *, observed_at: datetime, available_at: datetime) -> None:
        """Publish one report that reaches NWS at a chosen instant -- a late or special report."""
        self.reports.setdefault(station, []).append((observed_at, available_at))

    def handle(self, request: httpx.Request) -> httpx.Response:
        segments = request.url.path.strip("/").split("/")
        if segments == ["stations"]:
            return httpx.Response(200, json={"features": [_station_feature(station) for station in self.roster]})
        station = segments[1]
        start = datetime.fromisoformat(request.url.params["start"])
        end = datetime.fromisoformat(request.url.params["end"])
        self.requested_starts[station] = start
        visible = sorted(
            observed_at
            for observed_at, available_at in self.reports.get(station, [])
            if start <= observed_at < end and available_at <= self.clock
        )
        self.features_served[station] = len(visible)
        return httpx.Response(
            200, json={"type": "FeatureCollection", "features": [_observation(station, at) for at in visible]}
        )


def _station_feature(station: str) -> dict[str, object]:
    longitude, latitude = STATION_POINTS[station]
    return {
        "properties": {"stationIdentifier": station, "name": f"{station} ASOS", "provider": "ASOS"},
        "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
    }


def _observation(station: str, observed_at: datetime) -> dict[str, object]:
    return {
        "properties": {
            "stationId": station,
            "timestamp": observed_at.isoformat(),
            "temperature": {"value": 18.5, "unitCode": "wmoUnit:degC", "qualityControl": "V"},
            "windSpeed": {"value": 11.2, "unitCode": "wmoUnit:km_h-1", "qualityControl": "V"},
        }
    }


def _poll(fake: FakeNws, store: ObjectStore, *, now: datetime) -> SensorsPollResult:
    """Run the lane's fetch seam once at `now` against the fake upstream."""
    fake.clock = now
    fake.requested_starts.clear()
    fake.features_served.clear()

    async def poll() -> SensorsPollResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as client:
            return await poll_recent_sensor_readings(client, BBOX, now=now, store=store)

    return asyncio.run(poll())


def _publish(store: ObjectStore, poll: SensorsPollResult, *, completed_at: datetime, max_days: int = 7) -> None:
    """Publish a poll's newest `max_days` buckets the way the forward does: merge, write z13, mark complete."""
    tables = direct_sensor_tables(poll.writes)
    for day in sorted(tables, reverse=True)[:max_days]:
        adapter = DirectSensorsForwardAdapter(tables[day])
        asyncio.run(adapter(cast("Any", None), store, day=day, run_id="sensors-direct-forward-test"))
        assert adapter.merge is not None
        store.write_completion_marker(
            PartitionCompletion(
                part_count=1,
                row_count=adapter.merge.table.num_rows,
                completed_at=completed_at,
                run_id="sensors-direct-forward-test",
            ),
            layer=SENSORS_STREAM,
            kind=SENSORS_DIRECT_KIND,
            zoom=LANE_BASE_ZOOM_TIER,
            day=day,
        )


def _published_winner(store: ObjectStore, station: str, day: date) -> datetime:
    """The one report z13 holds for a station-day."""
    table = store.read_partition(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day)
    instants = {row["observed_at"] for row in table.to_pylist() if row["sensor_id"] == station}
    assert len(instants) == 1, f"{station} {day} should hold exactly one winning report, got {instants}"
    winner = instants.pop()
    assert isinstance(winner, datetime)
    return winner


@pytest.fixture(autouse=True)
def _idaho_roster(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENSOR_STATION_STATES", "ID")
    monkeypatch.delenv("SENSOR_STATION_NETWORKS", raising=False)
    monkeypatch.delenv("SENSOR_MAX_STATIONS", raising=False)


def test_a_daily_run_asks_a_held_station_only_for_its_last_day_and_a_new_station_for_the_whole_window() -> None:
    first_run = datetime(2026, 9, 27, 12, 20, tzinfo=UTC)
    second_run = first_run + timedelta(days=1)
    fake = FakeNws(roster=["KBOI"])
    fake.report_hourly("KBOI", first=second_run - timedelta(days=8), last=second_run)
    fake.report_hourly("KIDA", first=second_run - timedelta(days=8), last=second_run)
    store = ObjectStore(RecordingBackend())

    first = _poll(fake, store, now=first_run)
    assert fake.requested_starts == {"KBOI": first_run - NWS_OBSERVATION_RETENTION}, "an empty bucket: whole window"
    assert first.stations_watermarked == 0
    full_window_features = fake.features_served["KBOI"]
    _publish(store, first, completed_at=first_run + timedelta(minutes=1))

    fake.roster = ["KBOI", "KIDA"]  # KIDA joins the roster between runs
    second = _poll(fake, store, now=second_run)
    _publish(store, second, completed_at=second_run + timedelta(minutes=1))

    newest_held = datetime(2026, 9, 27, 11, 53, tzinfo=UTC)
    assert fake.requested_starts == {
        "KBOI": newest_held - SENSORS_WATERMARK_OVERLAP,
        "KIDA": second_run - NWS_OBSERVATION_RETENTION,
    }
    assert second.stations_watermarked == 1
    assert fake.features_served["KBOI"] <= full_window_features // 4, "the settled five days are not re-sent"
    assert _published_winner(store, "KBOI", date(2026, 9, 27)) == datetime(2026, 9, 27, 23, 53, tzinfo=UTC)
    assert _published_winner(store, "KBOI", date(2026, 9, 28)) == datetime(2026, 9, 28, 11, 53, tzinfo=UTC)
    assert _published_winner(store, "KIDA", date(2026, 9, 23)) == datetime(2026, 9, 23, 23, 53, tzinfo=UTC)


def test_a_report_that_reached_nws_after_the_last_poll_still_wins_the_day_before_midnight() -> None:
    first_run = datetime(2026, 9, 28, 0, 10, tzinfo=UTC)
    second_run = first_run + timedelta(hours=1)
    fake = FakeNws(roster=["KBOI"])
    fake.report_hourly("KBOI", first=first_run - timedelta(days=7), last=datetime(2026, 9, 27, 22, 53, tzinfo=UTC))
    late_final = datetime(2026, 9, 27, 23, 53, tzinfo=UTC)
    fake.report_once("KBOI", observed_at=late_final, available_at=datetime(2026, 9, 28, 0, 40, tzinfo=UTC))
    just_after_midnight = datetime(2026, 9, 28, 0, 5, tzinfo=UTC)
    fake.report_once("KBOI", observed_at=just_after_midnight, available_at=just_after_midnight + timedelta(minutes=2))
    store = ObjectStore(RecordingBackend())

    _publish(store, _poll(fake, store, now=first_run), completed_at=first_run + timedelta(minutes=1))
    assert _published_winner(store, "KBOI", date(2026, 9, 27)) == datetime(2026, 9, 27, 22, 53, tzinfo=UTC)

    second = _poll(fake, store, now=second_run)
    _publish(store, second, completed_at=second_run + timedelta(minutes=1))

    assert fake.requested_starts["KBOI"] < late_final, "the next day's report is newer, the overlap reaches back"
    assert fake.requested_starts["KBOI"] > second_run - NWS_OBSERVATION_RETENTION
    assert _published_winner(store, "KBOI", date(2026, 9, 27)) == late_final


def test_a_day_whose_write_failed_last_run_is_refetched_for_every_station_until_it_settles() -> None:
    seed_run = datetime(2026, 9, 26, 12, 20, tzinfo=UTC)
    failed_run = seed_run + timedelta(days=1)
    repair_run = failed_run + timedelta(days=1)
    fake = FakeNws(roster=["KBOI"])
    fake.report_hourly("KBOI", first=repair_run - timedelta(days=8), last=repair_run)
    store = ObjectStore(RecordingBackend())
    _publish(store, _poll(fake, store, now=seed_run), completed_at=seed_run + timedelta(minutes=1))

    # The failed run wrote its newest day but not 2026-09-26, which keeps its midday report.
    _publish(store, _poll(fake, store, now=failed_run), completed_at=failed_run + timedelta(minutes=1), max_days=1)
    assert _published_winner(store, "KBOI", date(2026, 9, 26)) == datetime(2026, 9, 26, 11, 53, tzinfo=UTC)

    _publish(store, _poll(fake, store, now=repair_run), completed_at=repair_run + timedelta(minutes=1))

    unsettled_since = seed_run + timedelta(minutes=1) - SENSORS_WATERMARK_OVERLAP
    assert fake.requested_starts == {"KBOI": unsettled_since}
    assert _published_winner(store, "KBOI", date(2026, 9, 26)) == datetime(2026, 9, 26, 23, 53, tzinfo=UTC)


def test_a_corrupt_completion_marker_reopens_its_day_instead_of_failing_the_whole_plan() -> None:
    first_run = datetime(2026, 9, 27, 12, 20, tzinfo=UTC)
    second_run = first_run + timedelta(days=1)
    fake = FakeNws(roster=["KBOI"])
    fake.report_hourly("KBOI", first=second_run - timedelta(days=8), last=second_run)
    backend = RecordingBackend()
    store = ObjectStore(backend)
    _publish(store, _poll(fake, store, now=first_run), completed_at=first_run + timedelta(minutes=1))

    # A day well inside the window and well settled -- corrupting only its marker isolates the read
    # failure from every other day's frontier, so a working plan proves the exception was contained.
    corrupted_day = date(2026, 9, 25)
    marker_key = store.key_for(
        completion_marker_path(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, corrupted_day)
    )
    assert marker_key in backend.objects, "the day must actually hold a marker to corrupt"
    backend.objects[marker_key] = b"not valid completion json"

    second = _poll(fake, store, now=second_run)

    assert second.days_unreadable == 1, "the corrupt marker must be counted, not silently absorbed"
    reopened_from = datetime(2026, 9, 24, 21, 0, tzinfo=UTC)  # corrupted_day's midnight - SENSORS_WATERMARK_OVERLAP
    assert fake.requested_starts["KBOI"] == reopened_from, (
        "a day whose marker cannot be read must widen the request back to its own midnight, never be treated as settled"
    )
    # The plan-building failure must not stop publication: this must not raise, and it must still
    # write a day.
    _publish(store, second, completed_at=second_run + timedelta(minutes=1))
