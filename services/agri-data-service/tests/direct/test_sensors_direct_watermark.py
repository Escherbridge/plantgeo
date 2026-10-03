"""Real sensors forward turns against a fake api.weather.gov: what each turn asks, what it costs, what lands.

Every test drives `forward.run`, the lane's entry point, through the real source, adapter, shared
finalizer and in-memory bucket. Only the process edge is faked: NWS (an `httpx.MockTransport` that
honours `start`/`end`/`limit`, answers newest-first and pads each feature to NWS's real ~5 KB), the
Postgres lock and session, and the availability store. The fake counts every request and every byte
it served, so the cost assertions do not trust the writer's own report.
See `pipeline/direct/sensors/AGENTS.md`, "Ask once per day, after the day has ended".
"""

# ruff: noqa: PLR2004 - the small literal counts ARE the assertion; naming each one hides it.

from __future__ import annotations

import asyncio
import functools
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from agri_data_service.config import Settings
from agri_data_service.foundation.parquet.paths import completion_marker_path
from agri_data_service.ingest.http import upstream_client
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.sensors import forward
from agri_data_service.pipeline.direct.sensors.adapter import SENSORS_DIRECT_KIND
from agri_data_service.pipeline.direct.sensors.progress import SENSORS_STATION_ATTEMPT_CAP, SWEEP_PROGRESS_ROOT
from agri_data_service.pipeline.direct.sensors.source import SensorsFetchBudget
from agri_data_service.pipeline.parquet import gap_fill
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.objectstore import BotoObjectStoreBackend, ObjectStore
from agri_data_service.warehouse.schemas.sensors import SENSORS_STREAM
from tests.parquet.test_objectstore_writer import RecordingBackend

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

BBOX = "-125,42,-111,49"
PUBLISH_LAG = timedelta(minutes=5)
#: Real NWS observation features run 3.9-5.8 KB (measured on KBOI, 2026-10-03); the fake pads to match.
FEATURE_PADDING = "M" * 4_500
#: Real Idaho stations: KBOI reports every 5 minutes (ASOS high-frequency), KIDA hourly.
STATION_POINTS: dict[str, tuple[float, float]] = {"KBOI": (-116.2228, 43.5644), "KIDA": (-112.0707, 43.5146)}
UNSET_CLOCK = datetime(2026, 9, 1, tzinfo=UTC)


@dataclass(frozen=True)
class ObservationRequest:
    station: str
    start: datetime
    end: datetime
    limit: int | None


@dataclass
class FakeNws:
    """api.weather.gov as this lane sees it: one roster page, and per-station reports it has published."""

    roster: list[str]
    reports: dict[str, list[tuple[datetime, datetime]]] = field(default_factory=dict)
    clock: datetime = UNSET_CLOCK
    roster_requests: int = 0
    observation_requests: list[ObservationRequest] = field(default_factory=list)
    observation_bytes: int = 0
    #: Station -> remaining requests to answer with a 503 instead of a report (fix 1: a station's own
    #: failed attempts, retried and capped by `progress.SENSORS_STATION_ATTEMPT_CAP`).
    failing_stations: dict[str, int] = field(default_factory=dict)
    #: Remaining roster-page requests to answer with a 503 (fix 4: a transient state-roster outage).
    failing_roster_requests: int = 0

    def report_every(self, station: str, *, minutes: int, first: datetime, last: datetime) -> None:
        """Publish one report every `minutes`, at :53 for hourly stations, visible `PUBLISH_LAG` later."""
        instant = first.replace(minute=53 if minutes == 60 else 0, second=0, microsecond=0)
        while instant <= last:
            self.reports.setdefault(station, []).append((instant, instant + PUBLISH_LAG))
            instant += timedelta(minutes=minutes)

    def report_once(self, station: str, *, observed_at: datetime, available_at: datetime) -> None:
        """Publish one report that reaches NWS at a chosen instant -- a late report."""
        self.reports.setdefault(station, []).append((observed_at, available_at))

    def fail_station(self, station: str, *, times: int) -> None:
        """The next `times` observation requests for `station` (any day) answer with a 503."""
        self.failing_stations[station] = self.failing_stations.get(station, 0) + times

    def fail_roster(self, *, times: int = 1) -> None:
        """The next `times` roster-page requests answer with a 503."""
        self.failing_roster_requests += times

    def reset_counters(self, clock: datetime) -> None:
        self.clock = clock
        self.roster_requests = 0
        self.observation_requests.clear()
        self.observation_bytes = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        segments = request.url.path.strip("/").split("/")
        if segments == ["stations"]:
            self.roster_requests += 1
            if self.failing_roster_requests > 0:
                self.failing_roster_requests -= 1
                return httpx.Response(503, content=b"")
            return httpx.Response(200, json={"features": [_station_feature(station) for station in self.roster]})
        station = segments[1]
        start = datetime.fromisoformat(request.url.params["start"])
        end = datetime.fromisoformat(request.url.params["end"])
        limit = int(request.url.params["limit"]) if "limit" in request.url.params else None
        self.observation_requests.append(ObservationRequest(station, start, end, limit))
        if self.failing_stations.get(station, 0) > 0:
            self.failing_stations[station] -= 1
            return httpx.Response(503, content=b"")
        visible = sorted(
            (
                observed_at
                for observed_at, available_at in self.reports.get(station, [])
                # `end` EXCLUSIVE, as probed live on KBOI 2026-10-03 (`sensors/AGENTS.md`): the next day's
                # 00:00:00 report is outside `[day start, day end)`, which the steady-day test leans on.
                if start <= observed_at < end and available_at <= self.clock
            ),
            reverse=True,  # NWS answers newest-first, which is what makes `limit=1` the day's winner
        )
        body = json.dumps(
            {"type": "FeatureCollection", "features": [_observation(station, at) for at in visible[:limit]]}
        ).encode()
        self.observation_bytes += len(body)
        return httpx.Response(200, content=body, headers={"content-type": "application/geo+json"})


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
            "rawMessage": FEATURE_PADDING,
            "temperature": {"value": 18.5, "unitCode": "wmoUnit:degC", "qualityControl": "V"},
            "windSpeed": {"value": 11.2, "unitCode": "wmoUnit:km_h-1", "qualityControl": "V"},
        }
    }


class SessionDouble:
    """The lane-day session the shared finalizer pins a timeout on and rolls back; no SQL runs."""

    async def execute(self, statement: Any, params: dict[str, Any] | None = None) -> None:
        del statement, params

    async def rollback(self) -> None:
        return None


@asynccontextmanager
async def _always_granted(*_args: object, **_kwargs: object) -> AsyncIterator[bool]:
    yield True


@asynccontextmanager
async def _session(_url: str) -> AsyncIterator[SessionDouble]:
    yield SessionDouble()


@dataclass
class Turn:
    exit_code: int
    fetch: dict[str, Any]
    complete: dict[str, Any]


@dataclass
class Lane:
    """One fake NWS and one bucket, run through `forward.run` turn by turn."""

    fake: FakeNws
    monkeypatch: pytest.MonkeyPatch
    capsys: pytest.CaptureFixture[str]
    backend: RecordingBackend = field(default_factory=RecordingBackend)

    @property
    def store(self) -> ObjectStore:
        return ObjectStore(self.backend)

    def turn(self, now: datetime, *extra_args: str) -> Turn:
        self.fake.reset_counters(now)
        self.capsys.readouterr()
        args = forward.parse_args(["--bbox", BBOX, *extra_args])
        exit_code = asyncio.run(forward.run(args, clock=lambda: now))
        events = [json.loads(line) for line in self.capsys.readouterr().out.splitlines() if line.startswith("{")]
        by_name = {event["event"]: event for event in events}
        return Turn(exit_code, by_name["sensors_forward_fetch"], by_name["sensors_forward_complete"])

    def winner(self, station: str, day: date) -> datetime:
        """The one report z13 holds for a station-day."""
        table = self.store.read_partition(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day)
        instants = {row["observed_at"] for row in table.to_pylist() if row["sensor_id"] == station}
        assert len(instants) == 1, f"{station} {day} should hold exactly one winning report, got {instants}"
        winner = instants.pop()
        assert isinstance(winner, datetime)
        return winner

    def completed_at(self, day: date) -> datetime:
        marker = self.store.read_completion_marker(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day)
        assert marker is not None
        return marker.completed_at

    def published(self, day: date) -> bool:
        return self.store.partition_exists(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day)

    def stations_on(self, day: date) -> set[str]:
        table = self.store.read_partition(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day)
        return {str(row["sensor_id"]) for row in table.to_pylist()}

    def scratch_keys(self) -> list[str]:
        """Sweep-progress scratch still in the bucket; empty once every swept day published."""
        return [key for key in self.backend.objects if key.startswith(SWEEP_PROGRESS_ROOT)]

    def asked_for(self, day: date) -> list[str]:
        """Stations this turn asked NWS about for one day's sweep."""
        opens = _day_start(day)
        return [
            request.station
            for request in self.fake.observation_requests
            if opens <= request.start < opens + timedelta(days=1)
        ]


@pytest.fixture
def lane(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> Lane:
    monkeypatch.setenv("SENSOR_STATION_STATES", "ID")
    monkeypatch.delenv("SENSOR_STATION_NETWORKS", raising=False)
    monkeypatch.delenv("SENSOR_MAX_STATIONS", raising=False)
    built = Lane(fake=FakeNws(roster=["KBOI", "KIDA"]), monkeypatch=monkeypatch, capsys=capsys)
    monkeypatch.setattr(Settings, "require_object_store", lambda _self: object())
    monkeypatch.setattr(
        Settings, "require_local_source_loader_database_url", lambda _self: "postgresql+asyncpg://unused"
    )
    monkeypatch.setattr(
        BotoObjectStoreBackend, "from_credentials", classmethod(lambda _cls, _credentials: built.backend)
    )
    monkeypatch.setattr(BotoAvailabilityStorage, "from_settings", classmethod(lambda _cls, _source=None: None))
    monkeypatch.setattr(
        forward,
        "upstream_client",
        lambda bounds: upstream_client(bounds, transport=httpx.MockTransport(built.fake.handle)),
    )
    monkeypatch.setattr(forward, "local_source_loader_session", _session)
    monkeypatch.setattr(forward, "postgres_lane_day_lock", _always_granted)
    return built


def _utc(day: str, clock: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{clock}+00:00")


def _day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


#: Before every bootstrap turn's six-day window, so no window day is empty at the source.
FIRST_REPORT = datetime(2026, 9, 14, tzinfo=UTC)


def _seed_reports(fake: FakeNws, *, through: datetime) -> None:
    fake.report_every("KBOI", minutes=5, first=FIRST_REPORT, last=through)
    fake.report_every("KIDA", minutes=60, first=FIRST_REPORT, last=through)


def test_a_steady_day_asks_nws_once_per_station_and_every_other_hour_is_a_no_op(lane: Lane) -> None:
    """The 2026-10-03 regression: 600 requests and ~183 MB every hour. A day now costs one sweep."""
    _seed_reports(lane.fake, through=_utc("2026-09-30", "00:00"))
    lane.turn(_utc("2026-09-28", "03:20"))  # bootstrap: an empty bucket owes every ended window day
    settled_before = lane.completed_at(date(2026, 9, 27))

    hourly = [_utc("2026-09-28", "04:20") + timedelta(hours=hour) for hour in range(23)]
    for now in hourly:
        quiet = lane.turn(now)
        assert quiet.exit_code == 0
        assert quiet.complete["outcome"] == "idempotent_noop"
        assert lane.fake.roster_requests == 0, f"{now}: a no-op turn must not even page the roster"
        assert lane.fake.observation_requests == []

    steady = lane.turn(_utc("2026-09-29", "03:20"))

    assert steady.exit_code == 0
    assert steady.fetch["days_due"] == ["2026-09-28"]
    assert steady.complete["days_written"] == 1
    assert lane.fake.roster_requests == 1
    day_window = (_utc("2026-09-28", "00:00"), _utc("2026-09-29", "00:00"))
    assert sorted(
        (request.station, request.start, request.end, request.limit) for request in lane.fake.observation_requests
    ) == [
        ("KBOI", *day_window, 1),
        ("KIDA", *day_window, 1),
    ]
    assert lane.fake.observation_bytes < 2 * 6_000, "one ~5 KB winner per station, not a day of 5-minute reports"
    assert steady.fetch["requests"] == 2
    assert steady.fetch["bytes_in"] == lane.fake.observation_bytes, "the budget meters what crossed the wire"
    # KBOI also reported at 2026-09-29T00:00:00; with `end` exclusive that report never wins 09-28.
    assert lane.winner("KBOI", date(2026, 9, 28)) == _utc("2026-09-28", "23:55")
    assert lane.winner("KIDA", date(2026, 9, 28)) == _utc("2026-09-28", "23:53")
    assert lane.completed_at(date(2026, 9, 27)) == settled_before, "a done day is never re-published"


def test_a_catch_up_after_a_gap_asks_once_per_missed_day_and_lands_every_day(lane: Lane) -> None:
    _seed_reports(lane.fake, through=_utc("2026-10-02", "00:00"))
    lane.turn(_utc("2026-09-26", "03:20"))
    steady = lane.turn(_utc("2026-09-27", "03:20"))
    steady_requests, steady_bytes = len(lane.fake.observation_requests), lane.fake.observation_bytes

    catch_up = lane.turn(_utc("2026-10-01", "03:20"))  # the executor missed four days of turns

    missed = ["2026-09-27", "2026-09-28", "2026-09-29", "2026-09-30"]
    assert catch_up.exit_code == 0
    assert catch_up.fetch["days_due"] == missed
    assert catch_up.fetch["days_swept"] == missed
    assert catch_up.complete["days_written"] == 4
    assert lane.fake.roster_requests == 1
    assert len(lane.fake.observation_requests) == 4 * steady_requests == 8
    assert lane.fake.observation_bytes <= 4 * steady_bytes + 1_000
    assert steady.fetch["requests"] == 2
    for missed_day in missed:
        assert lane.winner("KBOI", date.fromisoformat(missed_day)) == _utc(missed_day, "23:55")
        assert lane.winner("KIDA", date.fromisoformat(missed_day)) == _utc(missed_day, "23:53")
    assert lane.turn(_utc("2026-10-01", "04:20")).complete["outcome"] == "idempotent_noop"


def test_a_report_that_reaches_nws_late_still_wins_its_day_because_the_turn_waits(lane: Lane) -> None:
    lane.fake.report_every("KIDA", minutes=60, first=_utc("2026-09-20", "00:00"), last=_utc("2026-09-28", "22:53"))
    lane.fake.report_every("KBOI", minutes=60, first=_utc("2026-09-20", "00:00"), last=_utc("2026-09-29", "06:00"))
    late_final = _utc("2026-09-28", "23:53")
    lane.fake.report_once("KIDA", observed_at=late_final, available_at=_utc("2026-09-29", "02:40"))
    lane.turn(_utc("2026-09-28", "03:20"))

    for early in ("00:20", "01:20", "02:20"):
        assert lane.turn(_utc("2026-09-29", early)).complete["outcome"] == "idempotent_noop"
    lane.turn(_utc("2026-09-29", "03:20"))

    assert lane.winner("KIDA", date(2026, 9, 28)) == late_final


def test_a_station_never_published_is_walked_back_over_the_done_days_one_request_per_day(lane: Lane) -> None:
    """Production 2026-10-03: ~106 five-minute stations never published, so they asked for 6 days every hour."""
    _seed_reports(lane.fake, through=_utc("2026-09-30", "00:00"))
    lane.fake.roster = ["KIDA"]  # KBOI absent while the bucket was built, like a station whose day never fit
    lane.turn(_utc("2026-09-28", "03:20"))
    lane.fake.roster = ["KBOI", "KIDA"]

    joined = lane.turn(_utc("2026-09-29", "03:20"))

    walked = [request for request in lane.fake.observation_requests if request.end <= _utc("2026-09-28", "00:00")]
    assert joined.fetch["stations_gap_walked"] == 1
    assert {request.station for request in walked} == {"KBOI"}
    assert len(walked) == 5, "one request per done day that has data (09-23..09-27), none per report"
    assert all(request.limit == 1 for request in lane.fake.observation_requests)
    assert lane.fake.observation_bytes < len(lane.fake.observation_requests) * 6_000
    assert joined.fetch["days_backfilled"] == ["2026-09-23", "2026-09-24", "2026-09-25", "2026-09-26", "2026-09-27"]
    for day in range(23, 29):
        assert lane.winner("KBOI", date(2026, 9, day)) == datetime(2026, 9, day, 23, 55, tzinfo=UTC)
    assert lane.winner("KIDA", date(2026, 9, 27)) == _utc("2026-09-27", "23:53"), "the held station is untouched"


def test_a_turn_out_of_request_budget_publishes_only_fully_swept_days_and_the_next_turn_finishes(
    lane: Lane,
) -> None:
    _seed_reports(lane.fake, through=_utc("2026-10-02", "00:00"))
    lane.turn(_utc("2026-09-26", "03:20"))
    lane.monkeypatch.setattr(forward, "SensorsFetchBudget", functools.partial(SensorsFetchBudget, max_requests=3))

    starved = lane.turn(_utc("2026-09-30", "03:20"))

    assert len(lane.fake.observation_requests) == 3, "the hard ceiling is never crossed"
    assert starved.exit_code == 0
    assert starved.fetch["budget_exhausted"] == "requests"
    assert starved.fetch["days_swept"] == ["2026-09-26"]
    assert starved.fetch["sweep_in_progress"] == [{"day": "2026-09-27", "stations_asked": 1, "stations_remaining": 1}]
    assert starved.complete["outcome"] == "incomplete"
    assert [entry["outcome"] for entry in starved.complete["unwritten"]] == ["request_budget_exhausted"] * 3
    for unswept in ("2026-09-27", "2026-09-28", "2026-09-29"):
        assert not lane.published(date.fromisoformat(unswept)), f"{unswept}: a half-swept day is never written"
    asked_on_the_27th = lane.asked_for(date(2026, 9, 27))

    lane.monkeypatch.setattr(forward, "SensorsFetchBudget", SensorsFetchBudget)
    resumed = lane.turn(_utc("2026-09-30", "04:20"))

    assert resumed.fetch["days_due"] == ["2026-09-27", "2026-09-28", "2026-09-29"]
    assert resumed.fetch["days_resumed"] == ["2026-09-27"]
    assert resumed.complete["outcome"] == "complete"
    assert sorted(asked_on_the_27th + lane.asked_for(date(2026, 9, 27))) == ["KBOI", "KIDA"], (
        "the resumed day asks only the station the starved turn did not reach"
    )
    assert len(lane.fake.observation_requests) == 1 + 2 + 2
    assert lane.winner("KBOI", date(2026, 9, 27)) == _utc("2026-09-27", "23:55")
    assert lane.winner("KIDA", date(2026, 9, 27)) == _utc("2026-09-27", "23:53")
    assert lane.scratch_keys() == [], "a published day retires its scratch"


def test_a_sweep_slower_than_one_turn_resumes_and_publishes_instead_of_tripping_the_breaker(lane: Lane) -> None:
    """Review 2026-10-03: at about 2 s a request NWS cannot answer the roster in one turn's fetch share.

    Without carry-over every turn re-asked the same first stations, discarded them, exited 1 and fed the
    breaker, and the day aged out of NWS. Here every turn can afford ONE request against a two-station roster.
    """
    _seed_reports(lane.fake, through=_utc("2026-09-30", "00:00"))
    lane.turn(_utc("2026-09-28", "03:20"))
    lane.monkeypatch.setattr(forward, "SensorsFetchBudget", functools.partial(SensorsFetchBudget, max_requests=1))
    day = date(2026, 9, 28)

    first = lane.turn(_utc("2026-09-29", "03:20"))

    assert first.exit_code == 0, "healthy partial progress must not feed the breaker"
    assert first.complete["days_written"] == 0
    assert first.complete["sweep_in_progress"] == [{"day": "2026-09-28", "stations_asked": 1, "stations_remaining": 1}]
    assert not lane.published(day)
    first_asked = lane.asked_for(day)

    second = lane.turn(_utc("2026-09-29", "04:20"))

    assert second.exit_code == 0
    assert second.fetch["days_resumed"] == ["2026-09-28"]
    assert second.complete["outcome"] == "complete"
    assert sorted(first_asked + lane.asked_for(day)) == ["KBOI", "KIDA"], "total requests for the day == roster size"
    assert lane.winner("KBOI", day) == _utc("2026-09-28", "23:55")
    assert lane.winner("KIDA", day) == _utc("2026-09-28", "23:53")
    assert lane.scratch_keys() == []
    assert lane.turn(_utc("2026-09-29", "05:20")).complete["outcome"] == "idempotent_noop"


def test_a_roster_change_between_turns_discards_the_partial_sweep_instead_of_publishing_a_mixed_day(
    lane: Lane,
) -> None:
    _seed_reports(lane.fake, through=_utc("2026-09-30", "00:00"))
    lane.turn(_utc("2026-09-28", "03:20"))
    lane.monkeypatch.setattr(forward, "SensorsFetchBudget", functools.partial(SensorsFetchBudget, max_requests=1))
    day = date(2026, 9, 28)
    lane.turn(_utc("2026-09-29", "03:20"))
    [asked_first] = lane.asked_for(day)
    lane.fake.roster = [station for station in ("KBOI", "KIDA") if station != asked_first]

    rebuilt = lane.turn(_utc("2026-09-29", "04:20"))

    assert rebuilt.fetch["progress_discarded"] == [{"day": "2026-09-28", "reason": "roster_changed"}]
    assert rebuilt.fetch["days_resumed"] == []
    assert rebuilt.complete["days_written"] == 1
    assert lane.stations_on(day) == set(lane.fake.roster), (
        f"{asked_first} left the roster; its saved report must not be published beside the new roster's"
    )
    assert lane.scratch_keys() == []


def test_every_due_day_a_turn_does_not_publish_is_named_in_its_report(lane: Lane) -> None:
    """A fully swept day NWS has nothing for, and a day `--max-days` defers, both stay owed and both are listed."""
    lane.fake.report_every("KBOI", minutes=5, first=FIRST_REPORT, last=_utc("2026-09-26", "23:00"))
    lane.fake.report_every("KIDA", minutes=60, first=FIRST_REPORT, last=_utc("2026-09-26", "23:00"))
    lane.turn(_utc("2026-09-26", "03:20"))  # bootstrap through 09-25

    quiet = lane.turn(_utc("2026-09-29", "03:20"), "--max-days", "2")

    unwritten = {entry["day"]: entry for entry in quiet.complete["unwritten"]}
    assert quiet.fetch["days_due"] == ["2026-09-26", "2026-09-27", "2026-09-28"]
    assert quiet.fetch["days_swept"] == ["2026-09-26", "2026-09-27"], "the cap sweeps the oldest due days"
    assert quiet.fetch["days_deferred"] == ["2026-09-28"]
    assert lane.asked_for(date(2026, 9, 28)) == [], "a deferred day costs no request"
    assert quiet.complete["days_written"] == 1
    assert unwritten["2026-09-27"]["outcome"] == "no_writable_observations"
    assert unwritten["2026-09-28"]["outcome"] == "request_budget_exhausted"
    assert "--max-days 2" in unwritten["2026-09-28"]["detail"]
    assert quiet.exit_code == 0
    assert lane.scratch_keys() == [], "a swept day with nothing to publish keeps no scratch, so it is asked again"
    assert lane.turn(_utc("2026-09-29", "04:20")).fetch["days_due"] == ["2026-09-27", "2026-09-28"]


def test_a_corrupt_completion_marker_reopens_its_day_for_one_turn_instead_of_failing_the_plan(lane: Lane) -> None:
    _seed_reports(lane.fake, through=_utc("2026-09-30", "00:00"))
    lane.turn(_utc("2026-09-28", "03:20"))
    corrupted = date(2026, 9, 25)
    key = lane.store.key_for(
        completion_marker_path(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, corrupted)
    )
    assert key in lane.backend.objects
    lane.backend.objects[key] = b"not valid completion json"

    reopened = lane.turn(_utc("2026-09-28", "04:20"))

    assert reopened.fetch["days_unreadable"] == 1
    assert reopened.fetch["days_due"] == ["2026-09-25"]
    assert reopened.complete["days_written"] == 1
    assert lane.turn(_utc("2026-09-28", "05:20")).complete["outcome"] == "idempotent_noop", "the rewrite repaired it"


def test_a_checksum_bad_scratch_is_discarded_and_the_day_sweeps_fresh_instead_of_resuming(lane: Lane) -> None:
    """Review 2026-10-03 fix 3a: a tampered checksum must read exactly like `DISCARD_UNREADABLE` -- a
    re-sweep, never a crash and never a silent trust of the corrupt bytes."""
    _seed_reports(lane.fake, through=_utc("2026-09-30", "00:00"))
    lane.turn(_utc("2026-09-28", "03:20"))
    lane.monkeypatch.setattr(forward, "SensorsFetchBudget", functools.partial(SensorsFetchBudget, max_requests=1))
    day = date(2026, 9, 28)
    lane.turn(_utc("2026-09-29", "03:20"))  # partial sweep saved: one station asked, one budget-refused
    [key] = lane.scratch_keys()
    envelope = json.loads(lane.backend.objects[key])
    envelope["sha256"] = "0" * 64
    lane.backend.objects[key] = json.dumps(envelope).encode()
    lane.monkeypatch.setattr(forward, "SensorsFetchBudget", SensorsFetchBudget)

    rebuilt = lane.turn(_utc("2026-09-29", "04:20"))

    assert rebuilt.fetch["progress_discarded"] == [{"day": "2026-09-28", "reason": "unreadable"}]
    assert rebuilt.fetch["days_resumed"] == []
    assert sorted(lane.asked_for(day)) == ["KBOI", "KIDA"], "a corrupt scratch is swept fresh, not partially"
    assert rebuilt.complete["days_written"] == 1
    assert lane.published(day)
    assert lane.scratch_keys() == []


def test_a_fully_swept_days_publish_fault_retries_on_the_next_turn_with_zero_nws_requests(
    lane: Lane,
) -> None:
    """Review 2026-10-03 fix 3b: the reason `source.py` saves scratch for a fully swept day with
    reports (`AGENTS.md`, "A sweep resumes across turns", Publish) -- a failed publish must retry from
    the saved sweep, never re-ask NWS for stations it already has winners for."""
    _seed_reports(lane.fake, through=_utc("2026-09-30", "00:00"))
    lane.turn(_utc("2026-09-28", "03:20"))
    day = date(2026, 9, 28)
    real_fill_one_lane_day = gap_fill.fill_one_lane_day
    faulted = {"spent": False}

    async def fault_once(*args: object, **kwargs: object) -> tuple[str, int, int, int, str | None]:
        if kwargs.get("day") == day and not faulted["spent"]:
            faulted["spent"] = True
            return ("raised", 0, 0, 0, "synthetic publish fault")
        return await real_fill_one_lane_day(*args, **kwargs)  # type: ignore[arg-type]

    lane.monkeypatch.setattr(forward, "fill_one_lane_day", fault_once)

    # retry-attempts=1 so the fault is not absorbed by `_publish_day`'s own in-turn retry loop: the
    # point under test is CROSS-turn recovery, not within-turn backoff.
    first = lane.turn(_utc("2026-09-29", "03:20"), "--retry-attempts", "1")

    assert not lane.published(day)
    assert [entry["outcome"] for entry in first.complete["unwritten"]] == ["raised"]
    assert lane.scratch_keys() != [], "the fully swept day's reports are saved before publish is even attempted"

    second = lane.turn(_utc("2026-09-29", "04:20"), "--retry-attempts", "1")

    assert second.fetch["days_due"] == ["2026-09-28"]
    assert second.fetch["requests"] == 0, "the retry must not re-ask NWS; the sweep is already fully saved"
    assert lane.fake.observation_requests == []
    assert second.complete["outcome"] == "complete"
    assert lane.published(day)
    assert lane.scratch_keys() == []
    assert lane.winner("KBOI", day) == _utc("2026-09-28", "23:55")


def test_scratch_for_a_day_no_longer_due_is_pruned_on_a_fetching_turn(lane: Lane) -> None:
    """Review 2026-10-03 fix 3c: a day that ages out of NWS's rolling retention entirely must not
    leave its scratch behind forever -- `AGENTS.md`'s "Housekeeping" bullet."""
    _seed_reports(lane.fake, through=_utc("2026-10-10", "00:00"))
    lane.turn(_utc("2026-09-28", "03:20"))
    lane.monkeypatch.setattr(forward, "SensorsFetchBudget", functools.partial(SensorsFetchBudget, max_requests=1))
    day = date(2026, 9, 28)
    lane.turn(_utc("2026-09-29", "03:20"))  # partial sweep saved for the due day
    assert lane.scratch_keys() != []
    lane.monkeypatch.setattr(forward, "SensorsFetchBudget", SensorsFetchBudget)

    # Far enough ahead that `day` (and its scratch) sit entirely outside the 6-day rolling window;
    # later days are still due, so this is a FETCHING turn, not the no-op that skips housekeeping.
    later = lane.turn(_utc("2026-10-06", "03:20"))

    assert day.isoformat() not in later.fetch["days_due"]
    assert lane.scratch_keys() == []


def test_a_station_that_fails_once_still_lands_its_day_once_it_answers(lane: Lane) -> None:
    """Review 2026-10-03 fix 1: before this, a station asked and refused once was never re-asked for
    THAT day -- the false claim was "the gap walk picks them up later", but the gap walk only walks
    days BEFORE the due window and only FORWARD from a station's newest held day, so a station failing
    on day D and succeeding on D+1 lost D forever."""
    _seed_reports(lane.fake, through=_utc("2026-09-30", "00:00"))
    lane.turn(_utc("2026-09-28", "03:20"))
    day = date(2026, 9, 28)
    lane.fake.fail_station("KBOI", times=1)

    first = lane.turn(_utc("2026-09-29", "03:20"))

    assert not lane.published(day)
    assert first.complete["outcome"] == "incomplete"
    assert first.exit_code == 0, "healthy progress (KIDA answered) must not feed the breaker"

    second = lane.turn(_utc("2026-09-29", "04:20"))

    assert lane.asked_for(day) == ["KBOI"], "only the station that failed is re-asked"
    assert second.complete["outcome"] == "complete"
    assert lane.published(day)
    assert lane.winner("KBOI", day) == _utc("2026-09-28", "23:55")
    assert lane.winner("KIDA", day) == _utc("2026-09-28", "23:53")


def test_a_station_that_exhausts_its_attempt_cap_still_lets_the_day_publish_without_it(lane: Lane) -> None:
    """Review 2026-10-03 fix 1: a station that never answers must not owe the day forever -- it is
    re-asked up to `SENSORS_STATION_ATTEMPT_CAP` times, then the day sweeps around it."""
    _seed_reports(lane.fake, through=_utc("2026-09-30", "00:00"))
    lane.turn(_utc("2026-09-28", "03:20"))
    day = date(2026, 9, 28)
    lane.fake.fail_station("KBOI", times=SENSORS_STATION_ATTEMPT_CAP)

    for attempt in range(SENSORS_STATION_ATTEMPT_CAP - 1):
        lane.turn(_utc("2026-09-29", "03:20") + timedelta(hours=attempt))
        assert not lane.published(day), f"attempt {attempt + 1}: the cap is not yet spent"

    last = lane.turn(_utc("2026-09-29", "03:20") + timedelta(hours=SENSORS_STATION_ATTEMPT_CAP - 1))

    assert last.complete["outcome"] == "complete"
    assert lane.published(day)
    assert lane.stations_on(day) == {"KIDA"}, "KBOI exhausted its cap and is swept around, not owed forever"
    assert lane.winner("KIDA", day) == _utc("2026-09-28", "23:53")


def test_a_degraded_roster_turn_does_not_discard_the_days_real_saved_progress(lane: Lane) -> None:
    """Review 2026-10-03 fix 4: a transient state-roster-page failure shrinks this turn's roster, but
    must never be mistaken for a deliberate `SENSOR_STATION_STATES` change -- that would wipe the
    day's real saved progress, costing a transient outage twice (losing it now, and again when the
    roster returns to normal and no longer matches what THIS turn would otherwise have saved)."""
    _seed_reports(lane.fake, through=_utc("2026-09-30", "00:00"))
    lane.turn(_utc("2026-09-28", "03:20"))
    lane.monkeypatch.setattr(forward, "SensorsFetchBudget", functools.partial(SensorsFetchBudget, max_requests=1))
    day = date(2026, 9, 28)
    lane.turn(_utc("2026-09-29", "03:20"))  # partial sweep saved: one station asked
    [key] = lane.scratch_keys()
    saved_payload = lane.backend.objects[key]
    lane.monkeypatch.setattr(forward, "SensorsFetchBudget", SensorsFetchBudget)
    lane.fake.fail_roster(times=1)  # the only configured state (ID) fails this turn: an empty roster

    degraded = lane.turn(_utc("2026-09-29", "04:20"))

    assert degraded.fetch["progress_discarded"] == [], "a degraded roster must not read as roster_changed"
    assert lane.backend.objects[key] == saved_payload, "the real saved progress must be left untouched"
    assert not lane.published(day)

    healed = lane.turn(_utc("2026-09-29", "05:20"))

    assert healed.fetch["days_resumed"] == [day.isoformat()], "the untouched progress resumes once the roster heals"
    assert healed.complete["outcome"] == "complete"
    assert lane.published(day)
    assert lane.winner("KBOI", day) == _utc("2026-09-28", "23:55")
    assert lane.winner("KIDA", day) == _utc("2026-09-28", "23:53")
