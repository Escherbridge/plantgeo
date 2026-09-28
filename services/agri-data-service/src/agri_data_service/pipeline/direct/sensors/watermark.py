"""Per-station NWS fetch starts, read back from what this lane already published to z13.

See this package's `AGENTS.md`, "Fetch only what can still change a published block", for the
evidence (447 MB/day from api.weather.gov) and the correctness argument this module implements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.ingest.source import HistoryWindow
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.sensors.adapter import SENSORS_DIRECT_KIND
from agri_data_service.warehouse.schemas.sensors import SENSORS_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

#: Covers NWS's own ingest lag plus the gap between a run's fetch and its z13 completion marker
#: (time budget <= 900 s plus the per-day retry tail). See `AGENTS.md` before shrinking it. This is
#: the per-station re-fetch floor (`window_for`); it is NOT the day-settle bound -- see
#: `SENSORS_SETTLE_OVERSCAN` below for why those two had to be split.
SENSORS_WATERMARK_OVERLAP: Final = timedelta(hours=3)

#: How long after a day's UTC midnight-to-midnight span ends a run still re-fetches it, even once a
#: completion marker exists. Deliberately much larger than `SENSORS_WATERMARK_OVERLAP`: that overlap
#: only has to cover NWS's in-order publication lag for a report that hasn't landed yet, but settling
#: a day also has to cover (a) a correction or quality-flag update NWS applies to a report that has
#: ALREADY landed and already won its day, and (b) `rows.py`'s own admission that NWS's `observedAt`
#: "IS NOT PROVABLY A UTC DATE" -- `_day_start`/`_window_days` here assume UTC midnight, so a day
#: could in principle end as much as one UTC offset early or late. 12 h comfortably covers both: it
#: is larger than any corrections window this lane has observed and larger than the largest US UTC
#: offset (UTC-10, Aleutian). See `AGENTS.md`, "What this does not cover".
SENSORS_SETTLE_OVERSCAN: Final = timedelta(hours=12)


@dataclass(frozen=True, slots=True)
class SensorsFetchPlan:
    """The rolling window, the lane-wide frontier inside it, and each published station's newest report."""

    window: HistoryWindow
    lane_frontier: datetime
    station_newest: Mapping[str, datetime] = field(default_factory=dict)
    #: Window days whose z13 read raised (corrupt part, transient bucket error that outlived retries).
    #: Each one is folded in as reopened rather than aborting the whole plan -- see
    #: `read_sensors_fetch_plan`.
    days_unreadable: int = 0

    def window_for(self, station_identifier: str) -> HistoryWindow:
        """Return one station's request window: the full rolling window unless this lane already holds it."""
        newest = self.station_newest.get(station_identifier)
        if newest is None:
            return self.window
        latest_start = self.window.end - SENSORS_WATERMARK_OVERLAP
        start = min(self.lane_frontier, newest - SENSORS_WATERMARK_OVERLAP, latest_start)
        return HistoryWindow(start=max(self.window.start, start), end=self.window.end)


def unwatermarked_plan(window: HistoryWindow) -> SensorsFetchPlan:
    """A plan that asks every station for the whole rolling window -- the pre-2026-09-28 request shape."""
    return SensorsFetchPlan(window=window, lane_frontier=window.start)


def _window_days(window: HistoryWindow) -> list[date]:
    """Every UTC calendar day the half-open rolling window touches, oldest first."""
    first, last = window.start.astimezone(UTC).date(), window.end.astimezone(UTC).date()
    return [first + timedelta(days=offset) for offset in range((last - first).days + 1)]


def _day_start(day: date) -> datetime:
    """Midnight UTC opening one calendar day."""
    return datetime.combine(day, time.min, tzinfo=UTC)


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


def _day_frontier(store: ObjectStore, day: date, status: str, *, now: datetime) -> datetime | None:
    """The earliest instant this day can still gain a newer report from, or None once it is settled.

    Settled means the z13 completion marker was written more than `SENSORS_SETTLE_OVERSCAN` after the
    day ended, so the run that last wrote it already saw the whole day plus room for a late correction
    or a UTC-offset misjudgment of where the day actually ends (see that constant's docstring).
    Anything else -- no parts, an absence, an incomplete write, a marker without a zone -- re-opens the
    day from its own midnight. A marker stamped in the future (clock skew on the writing host) is
    clamped to `now` before either comparison, so a fast clock cannot settle a day early.
    """
    reopened = _day_start(day) - SENSORS_WATERMARK_OVERLAP
    if status != "data":
        return reopened
    completion = store.read_completion_marker(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day)
    if completion is None or completion.completed_at.utcoffset() is None:
        return reopened
    completed_at = min(completion.completed_at, now)
    settled_at = _day_start(day) + timedelta(days=1) + SENSORS_SETTLE_OVERSCAN
    if completed_at >= settled_at:
        return None
    return completed_at - SENSORS_WATERMARK_OVERLAP


def _fold_station_newest(store: ObjectStore, day: date, newest: dict[str, datetime]) -> None:
    """Raise each station's newest published `observed_at` with the reports one published day holds."""
    table = store.read_partition(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day)
    for sensor_id, observed_at in zip(
        table.column("sensor_id").to_pylist(), table.column("observed_at").to_pylist(), strict=True
    ):
        if not isinstance(sensor_id, str) or not isinstance(observed_at, datetime):
            continue
        if observed_at.utcoffset() is None:
            continue
        current = newest.get(sensor_id)
        if current is None or observed_at > current:
            newest[sensor_id] = observed_at


def read_sensors_fetch_plan(
    store: ObjectStore, window: HistoryWindow, *, now: datetime | None = None
) -> SensorsFetchPlan:
    """Read the lane frontier and every station's newest published report from z13 inside `window`.

    An empty bucket holds no station watermark, so a first run (and any station new to the roster)
    asks for the whole window, exactly as before this module existed.

    A day whose z13 read raises (a corrupt part, a bucket error that outlived its own retries) is
    folded in as reopened from its own midnight rather than letting the exception escape: before this
    function existed, one bad day only failed that one day's publish in `forward.py::_publish_day`,
    and every other day still wrote; a plan-building exception here must not regress that to "the
    whole lane exits 1 for up to `NWS_OBSERVATION_RETENTION` until the bad day ages out of the
    window". Treating it as reopened -- never as settled -- means this can only widen a station's
    request, never cause a skip.
    """
    resolved_now = now if now is not None else datetime.now(UTC)
    days = _window_days(window)
    statuses = _z13_statuses(store, days)
    frontier_candidates: list[datetime] = []
    newest: dict[str, datetime] = {}
    days_unreadable = 0
    for day in days:
        status = statuses[day]
        try:
            frontier = _day_frontier(store, day, status, now=resolved_now)
        except Exception:  # a bad day must widen the request, never abort the plan
            days_unreadable += 1
            frontier_candidates.append(_day_start(day) - SENSORS_WATERMARK_OVERLAP)
            continue
        if frontier is not None:
            frontier_candidates.append(frontier)
        if status == "data":
            try:
                _fold_station_newest(store, day, newest)
            except Exception:  # a bad day's stations simply keep their older watermark
                days_unreadable += 1
    lane_frontier = min(frontier_candidates, default=window.end - SENSORS_WATERMARK_OVERLAP)
    return SensorsFetchPlan(
        window=window,
        lane_frontier=max(window.start, lane_frontier),
        station_newest=newest,
        days_unreadable=days_unreadable,
    )


__all__ = [
    "SENSORS_SETTLE_OVERSCAN",
    "SENSORS_WATERMARK_OVERLAP",
    "SensorsFetchPlan",
    "read_sensors_fetch_plan",
    "unwatermarked_plan",
]
