"""Which ended days a sensors turn still owes NWS a question about, read back from this lane's own z13.

See this package's `AGENTS.md`, "Ask once per day, after the day has ended", for the production evidence
(2026-10-03: 14,400 requests and 4.39 GB a day) and the correctness argument this module implements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.sensors.adapter import SENSORS_DIRECT_KIND
from agri_data_service.warehouse.schemas.sensors import SENSORS_STREAM

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from agri_data_service.ingest.source import HistoryWindow
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

#: How long after a UTC day ends before the turn asks NWS for it. Covers a report observed before
#: midnight that reaches NWS late (the 23:53 case in the flow tests). A day is done once a write of it
#: completed at least this long after it ended. See `AGENTS.md` before shrinking it.
SENSORS_LATE_REPORT_ALLOWANCE: Final = timedelta(hours=3)

#: One UTC calendar day; named so the day arithmetic below reads as days, not as a bare literal.
_ONE_DAY: Final = timedelta(days=1)


def day_start(day: date) -> datetime:
    """Midnight UTC opening one calendar day."""
    return datetime.combine(day, time.min, tzinfo=UTC)


def day_end(day: date) -> datetime:
    """Midnight UTC closing one calendar day (exclusive)."""
    return day_start(day) + _ONE_DAY


def window_days(window: HistoryWindow) -> list[date]:
    """Every UTC calendar day the half-open rolling window touches, oldest first."""
    first, last = window.start.astimezone(UTC).date(), window.end.astimezone(UTC).date()
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


@dataclass(frozen=True, slots=True)
class SensorsDayPlan:
    """Every window day sorted by what a turn owes it. Built from z13 listings and markers alone."""

    window: HistoryWindow
    #: Ended, past the late-report allowance, and not done: the turn must ask NWS for these, oldest first.
    due_days: tuple[date, ...]
    #: Written by a run that finished at least the allowance after the day ended. Never re-asked.
    done_days: tuple[date, ...] = ()
    #: Parts AND an absence marker: the adapter refuses them, so asking NWS would only burn requests.
    blocked_days: tuple[date, ...] = ()
    #: Today, or a day that ended less than the allowance ago. Asked on a later turn.
    waiting_days: tuple[date, ...] = ()
    #: Every window day whose base rung holds a completed part, whatever its class above.
    published_days: tuple[date, ...] = ()
    #: Days whose completion marker read raised; each is counted and treated as owed, never as done.
    days_unreadable: int = 0

    @property
    def nothing_due(self) -> bool:
        """True when this turn has no question to ask NWS -- the hourly no-op."""
        return not self.due_days


def _z13_statuses(store: ObjectStore, days: list[date]) -> Mapping[date, str]:
    """Classify each window day at the base rung, listing each month the window touches once."""
    keys: list[str] = []
    for year, month in sorted({(day.year, day.month) for day in days}):
        keys.extend(
            store.list_partition_keys(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, year=year, month=month)
        )
    return partition_day_statuses(
        layer=SENSORS_STREAM,
        kind=SENSORS_DIRECT_KIND,
        zoom=LANE_BASE_ZOOM_TIER,
        first_day=days[0],
        last_day=days[-1],
        keys=keys,
    )


def _is_done(store: ObjectStore, day: date, *, now: datetime) -> bool:
    """True once a write of `day` completed at least the late-report allowance after the day ended.

    A marker stamped in the future (a fast writer clock) is clamped to `now` first, so it cannot make a
    day done early. A marker with no zone is not trusted.
    """
    completion = store.read_completion_marker(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day)
    if completion is None or completion.completed_at.utcoffset() is None:
        return False
    return min(completion.completed_at, now) >= day_end(day) + SENSORS_LATE_REPORT_ALLOWANCE


def read_sensors_day_plan(store: ObjectStore, window: HistoryWindow, *, now: datetime) -> SensorsDayPlan:
    """Sort every window day into due, done, blocked or waiting from z13 listings and markers.

    Reads no partition: the hourly no-op decision costs one listing per month and one marker per day.
    A marker read that raises is counted in `days_unreadable` and the day is owed, never done, so a bad
    object can only make the turn ask NWS, never skip a day.
    """
    days = window_days(window)
    statuses = _z13_statuses(store, days)
    due: list[date] = []
    done: list[date] = []
    blocked: list[date] = []
    waiting: list[date] = []
    published: list[date] = []
    unreadable = 0
    for day in days:
        if statuses[day] == "data":
            published.append(day)
        if now < day_end(day) + SENSORS_LATE_REPORT_ALLOWANCE:
            waiting.append(day)
            continue
        status = statuses[day]
        if status == "conflict":
            blocked.append(day)
            continue
        finished = False
        if status == "data":
            try:
                finished = _is_done(store, day, now=now)
            except Exception:  # a bad marker must widen the turn, never abort the plan
                unreadable += 1
        (done if finished else due).append(day)
    return SensorsDayPlan(
        window=window,
        due_days=tuple(due),
        done_days=tuple(done),
        blocked_days=tuple(blocked),
        waiting_days=tuple(waiting),
        published_days=tuple(published),
        days_unreadable=unreadable,
    )


def owed_day_plan(window: HistoryWindow, *, now: datetime) -> SensorsDayPlan:
    """With no bucket to read, every ended window day past the allowance is due."""
    days = window_days(window)
    due = tuple(day for day in days if now >= day_end(day) + SENSORS_LATE_REPORT_ALLOWANCE)
    return SensorsDayPlan(window=window, due_days=due, waiting_days=tuple(day for day in days if day not in due))


@dataclass(frozen=True, slots=True)
class StationFrontiers:
    """Each station's published winning report per day, read from z13 -- the per-station frontier."""

    by_day: Mapping[date, Mapping[str, datetime]] = field(default_factory=dict)
    #: Days whose partition read raised. Their stations simply look unpublished there (a wider ask).
    days_unreadable: int = 0

    def on_day(self, station_identifier: str, day: date) -> datetime | None:
        """The report this lane published for one station-day, or None."""
        return self.by_day.get(day, {}).get(station_identifier)

    def newest_day(self, station_identifier: str) -> date | None:
        """The newest day this lane holds any report for this station, or None for an unknown station."""
        held = [day for day, stations in self.by_day.items() if station_identifier in stations]
        return max(held, default=None)


def _published_winners(store: ObjectStore, day: date) -> dict[str, datetime]:
    """Every station's published `observed_at` on one z13 day."""
    table = store.read_partition(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day)
    winners: dict[str, datetime] = {}
    for sensor_id, observed_at in zip(
        table.column("sensor_id").to_pylist(), table.column("observed_at").to_pylist(), strict=True
    ):
        if not isinstance(sensor_id, str) or not isinstance(observed_at, datetime) or observed_at.utcoffset() is None:
            continue
        current = winners.get(sensor_id)
        if current is None or observed_at > current:
            winners[sensor_id] = observed_at
    return winners


def read_station_frontiers(store: ObjectStore, days: Iterable[date]) -> StationFrontiers:
    """Read the published winners of every `data` day given. Only a turn that will ask NWS pays for this."""
    by_day: dict[date, dict[str, datetime]] = {}
    unreadable = 0
    for day in days:
        try:
            by_day[day] = _published_winners(store, day)
        except Exception:  # a bad day's stations look unpublished there: a wider ask, never a skip
            unreadable += 1
    return StationFrontiers(by_day=by_day, days_unreadable=unreadable)


__all__ = [
    "SENSORS_LATE_REPORT_ALLOWANCE",
    "SensorsDayPlan",
    "StationFrontiers",
    "day_end",
    "day_start",
    "owed_day_plan",
    "read_sensors_day_plan",
    "read_station_frontiers",
    "window_days",
]
