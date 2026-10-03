"""Ask NWS, once per ended day, for each station's newest report of that day -- and nothing else.

THE FLOOR IS ROLLING. `ingest/sensors.py::observation_url` accepts a bounded `start`/`end`, but
api.weather.gov only answers inside its own rolling retention (`NWS_OBSERVATION_RETENTION`, six days).
`_rolling_window` builds `[now - retention, now)` and proves it is inside the source's declared
`HistoryCapability`; every request below is clamped inside it. This writer never manufactures an
absence: a day NWS no longer holds is simply never asked for.

THE LANE KEEPS ONE REPORT PER STATION-DAY, THE DAY'S NEWEST (`rows.py`), so the turn asks for exactly
that: `?start=<day start>&end=<day end>&limit=1`. NWS returns newest-first, so one feature is the
winner. Measured 2026-10-03 on KBOI: one 5-minute station-day without `limit` is 312 features and
1.2 MB, past `OBSERVATION_BOUNDS`' 1 MiB cap; with `limit=1` it is 5.4 KB.

WHEN TO ASK is `watermark.py`'s day plan: only days that ended at least the late-report allowance ago
and are not yet done. A turn with nothing due makes no NWS request at all, not even the roster.

THE TURN HAS TWO PHASES, under one hard budget (`SensorsFetchBudget`):
- Sweep: every due day, oldest first, every roster station, one request each. A day is publishable
  only when every station was asked; a day the budget cut short is dropped from this turn and stays
  due, so a partial sweep is never marked done.
- Gap walk: stations this lane holds nothing for since some earlier day (a new roster member, a
  station whose request failed, or one whose day was too large before `limit` existed) walk backwards
  over the done days: newest report in `[gap start, end)`, then the same span ending at that report's
  day, until NWS answers empty. One request per day that has data, plus one.

See this package's `AGENTS.md`, "Ask once per day, after the day has ended", for the evidence and
what this does not cover.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.ingest.http import (
    UpstreamBounds,
    UpstreamPayloadError,
    UpstreamPayloadTooLargeError,
    fetch_bounded_json_sized,
)
from agri_data_service.ingest.policy import resolve_bounded_bbox
from agri_data_service.ingest.sensors import (
    MAX_CONCURRENT_STATION_REQUESTS,
    NWS_OBSERVATION_RETENTION,
    NWS_SENSOR_SOURCE,
    fetch_station_roster,
    nws_request_headers,
    nws_sensor_source,
    observation_url,
    parse_observation,
)
from agri_data_service.ingest.source import FetchRequest, HistoryWindow, select_writes
from agri_data_service.pipeline.direct.sensors.progress import DaySweepProgress, HeldProgress, roster_sha256
from agri_data_service.pipeline.direct.sensors.watermark import (
    StationFrontiers,
    day_end,
    day_start,
    owed_day_plan,
    read_sensors_day_plan,
    read_station_frontiers,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx

    from agri_data_service.ingest.records import FeatureWrite
    from agri_data_service.ingest.sensors import SensorStation
    from agri_data_service.pipeline.direct.sensors.progress import SweepProgressStore
    from agri_data_service.pipeline.direct.sensors.watermark import SensorsDayPlan
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

#: The ceiling every request is clamped inside: NWS's whole rolling retention.
SENSORS_ROLLING_WINDOW: Final = NWS_OBSERVATION_RETENTION

#: Reports asked for per request. One is the day's winner, because NWS answers newest-first.
SENSORS_REPORTS_PER_REQUEST: Final = 1

#: Per-request cap. A `limit=1` answer is about 5.4 KB; 64 KiB leaves room for a long METAR and
#: refuses fast if NWS ever ignores `limit` (a whole 5-minute day is 1.2 MB).
SENSORS_OBSERVATION_BOUNDS: Final = UpstreamBounds(max_bytes=64 * 1024, timeout_seconds=15.0)

#: Hard per-turn request ceiling across both phases. One day's sweep is one request per station (582
#: in production, `DEFAULT_MAX_STATIONS` 750), so this always fits at least three days; a six-day
#: catch-up finishes on the next hourly turn because the unswept days stay due.
SENSORS_MAX_REQUESTS_PER_RUN: Final = 2_400

#: Hard per-turn observation-byte ceiling. 2,400 requests at about 5.4 KB is about 13 MB.
SENSORS_MAX_BYTES_PER_RUN: Final = 32 * 1024 * 1024

#: Share of `--time-budget-seconds` the fetch may use before it stops starting requests; the rest is
#: left for publishing what it fetched.
SENSORS_FETCH_TIME_SHARE: Final = 0.5

#: Unchanged `--max-records` bounds. A request yields at most `SENSORS_REPORTS_PER_REQUEST` records,
#: so the request budget is capped at `--max-records` and `select_writes` never has to drop one.
SENSORS_DEFAULT_MAX_RECORDS: Final = 200_000
SENSORS_MIN_MAX_RECORDS: Final = 1_000
SENSORS_MAX_MAX_RECORDS: Final = 1_000_000

#: Budget words, reported on `sensors_forward_fetch.budget_exhausted`.
BUDGET_REQUESTS: Final = "requests"
BUDGET_BYTES: Final = "bytes"
BUDGET_TIME: Final = "time"


class SensorsSourceError(ValueError):
    """Raised when no bbox is configured to poll the NWS roster from."""


@dataclass(slots=True)
class SensorsFetchBudget:
    """One turn's hard ceiling on observation requests, bytes and wall clock. Checked before every request."""

    max_requests: int = SENSORS_MAX_REQUESTS_PER_RUN
    max_bytes: int = SENSORS_MAX_BYTES_PER_RUN
    #: `time.monotonic()` after which no new request starts; None means no clock bound.
    deadline: float | None = None
    requests: int = 0
    bytes_in: int = 0
    #: Which ceiling stopped the turn (`BUDGET_*`), or None.
    exhausted: str | None = None

    def try_spend(self) -> bool:
        """Reserve one request, or record which ceiling refused it."""
        if self.exhausted is None:
            if self.requests >= self.max_requests:
                self.exhausted = BUDGET_REQUESTS
            elif self.bytes_in >= self.max_bytes:
                self.exhausted = BUDGET_BYTES
            elif self.deadline is not None and time.monotonic() >= self.deadline:
                self.exhausted = BUDGET_TIME
        if self.exhausted is not None:
            return False
        self.requests += 1
        return True


@dataclass(frozen=True, slots=True)
class SweepInProgress:
    """One due day a turn advanced but could not finish; its progress is saved for the next due turn."""

    day: date
    stations_asked: int
    stations_total: int

    def as_event(self) -> dict[str, object]:
        return {
            "day": self.day.isoformat(),
            "stations_asked": self.stations_asked,
            "stations_remaining": self.stations_total - self.stations_asked,
        }


@dataclass(frozen=True, slots=True)
class SensorsPollResult:
    """What one turn asked NWS and what it may publish, already freshness-filtered."""

    fetched_at: datetime
    plan: SensorsDayPlan
    stations_polled: int = 0
    records_seen: int = 0
    writes: tuple[FeatureWrite, ...] = ()
    rejected: int = 0
    dropped: int = 0
    #: Due days every station was asked for (this turn or across turns); publishable.
    days_swept: tuple[date, ...] = ()
    #: Due days the budget cut short; not publishable this turn, still due next turn.
    days_unswept: tuple[date, ...] = ()
    #: Due days past `max_days`, oldest-first order; not asked this turn, still due next turn.
    days_deferred: tuple[date, ...] = ()
    #: Due days whose sweep continued from scratch a previous turn saved.
    days_resumed: tuple[date, ...] = ()
    #: Unswept days whose partial sweep this turn advanced AND saved: healthy progress, not a stall.
    sweep_in_progress: tuple[SweepInProgress, ...] = ()
    #: (day, reason) for scratch dropped instead of resumed (`progress.DISCARD_*`).
    progress_discarded: tuple[tuple[date, str], ...] = ()
    #: Done days a gap walk added reports to; publishable only where the merge changes them.
    days_backfilled: tuple[date, ...] = ()
    stations_gap_walked: int = 0
    #: Stations whose gap walk the budget refused or cut short. They walk again next turn.
    stations_deferred: int = 0
    #: Station requests that raised; counted, never fatal, as `collect_sensor_records` does.
    stations_unavailable: int = 0
    requests: int = 0
    bytes_in: int = 0
    budget_exhausted: str | None = None
    #: Window days whose z13 read raised, from the day plan and the frontier read together.
    days_unreadable: int = 0
    frontiers: StationFrontiers = field(default_factory=StationFrontiers)
    #: Configured states this turn's own roster page request failed for (`ingest/sensors.py::StationRoster`).
    #: Non-empty means this turn's roster is smaller than the full configured one, which must not be
    #: mistaken for a deliberate `SENSOR_STATION_STATES` change (`progress.py::SweepProgressStore.resume`).
    roster_states_unavailable: tuple[str, ...] = ()

    @property
    def publishable_days(self) -> frozenset[date]:
        """Days this turn may write: fully swept due days and done days a gap walk reached."""
        return frozenset(self.days_swept) | frozenset(self.days_backfilled)


def _rolling_window(now: datetime) -> HistoryWindow:
    """Build `[now - retention, now)` and prove it is inside the source's own declared capability.

    The proof never fails at run time; it exists so a change to this constant or to
    `NWS_OBSERVATION_RETENTION` cannot drift the two apart silently.
    """
    window = HistoryWindow(start=now - SENSORS_ROLLING_WINDOW, end=now)
    nws_sensor_source(now).history.require(NWS_SENSOR_SOURCE, window)
    return window


def _limited_observation_url(station_identifier: str, window: HistoryWindow) -> str:
    """`observation_url` for a bounded window, asking only for the newest `SENSORS_REPORTS_PER_REQUEST`."""
    return f"{observation_url(station_identifier, window)}&limit={SENSORS_REPORTS_PER_REQUEST}"


def _collection_features(payload: object) -> list[object]:
    """Return a history FeatureCollection's features, refusing a body that is not one."""
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        raise UpstreamPayloadError("NOAA NWS returned an invalid observation collection")
    return features


async def _newest_reports(
    client: httpx.AsyncClient, station: SensorStation, window: HistoryWindow, budget: SensorsFetchBudget
) -> list[dict[str, object]] | None:
    """One budgeted request for a station's newest report(s) in `window`; None when the budget refused it."""
    if not budget.try_spend():
        return None
    try:
        sized = await fetch_bounded_json_sized(
            client,
            _limited_observation_url(station.station_identifier, window),
            SENSORS_OBSERVATION_BOUNDS,
            nws_request_headers(),
        )
    except UpstreamPayloadTooLargeError as error:
        budget.bytes_in += error.transferred_bytes
        raise
    budget.bytes_in += sized.byte_count
    records = (parse_observation(feature, station) for feature in _collection_features(sized.payload))
    return [record for record in records if record is not None]


def _report_day(record: dict[str, object]) -> date | None:
    """The day `rows.py` will file a report under: the first ten characters of its raw timestamp."""
    timestamp = record.get("timestamp")
    if not isinstance(timestamp, str):
        return None
    try:
        return date.fromisoformat(timestamp[:10])
    except ValueError:
        return None


@dataclass(slots=True)
class _Sweep:
    """What one turn's slice of a due day's sweep returned, per station."""

    answered: dict[str, list[dict[str, object]]] = field(default_factory=dict)
    #: Stations whose request raised THIS turn; merged into `DaySweepProgress.unavailable`'s attempt
    #: counts by the caller, one attempt per station per turn it is asked.
    unavailable: set[str] = field(default_factory=set)
    #: Stations the budget refused; they stay unasked and the day stays unpublished.
    refused: int = 0


async def _sweep_day(  # noqa: PLR0913 - the day, its roster, its frontiers and the shared budget are separate inputs
    client: httpx.AsyncClient,
    stations: Sequence[SensorStation],
    day: date,
    window: HistoryWindow,
    frontiers: StationFrontiers,
    budget: SensorsFetchBudget,
) -> _Sweep:
    """Ask each given station for its newest report of one ended day, starting at what this lane already holds.

    `end` is the day's closing midnight as given: NWS treats `end` as EXCLUSIVE (probed 2026-10-03, see
    `AGENTS.md`), so the next day's 00:00:00 report is never this day's winner.
    """
    concurrency = asyncio.Semaphore(MAX_CONCURRENT_STATION_REQUESTS)
    end = min(day_end(day), window.end)

    async def ask(station: SensorStation) -> list[dict[str, object]] | None:
        held = frontiers.on_day(station.station_identifier, day)
        # Inclusive of the held winner, so an owed day that NWS has nothing newer for is re-written
        # (that write is what makes it done) rather than left owed forever.
        start = max(day_start(day), window.start, held or window.start)
        async with concurrency:
            return await _newest_reports(client, station, HistoryWindow(start=start, end=end), budget)

    sweep = _Sweep()
    answers = await asyncio.gather(*(ask(station) for station in stations), return_exceptions=True)
    for station, answer in zip(stations, answers, strict=True):
        if isinstance(answer, BaseException):
            sweep.unavailable.add(station.station_identifier)
        elif answer is None:
            sweep.refused += 1
        else:
            sweep.answered[station.station_identifier] = answer
    return sweep


@dataclass(slots=True)
class _Walk:
    """What one station's backwards gap walk returned."""

    records: list[dict[str, object]] = field(default_factory=list)
    deferred: bool = False
    failed: bool = False


async def _walk_station(
    client: httpx.AsyncClient, station: SensorStation, span: HistoryWindow, budget: SensorsFetchBudget
) -> _Walk:
    """Collect one station's newest report for every day in `span` that has one, newest day first."""
    walk = _Walk()
    end = span.end
    for _step in range((span.end - span.start).days + 2):
        try:
            reports = await _newest_reports(client, station, HistoryWindow(start=span.start, end=end), budget)
        except Exception:  # counted as unavailable; the days it did collect are still correct winners
            walk.failed = True
            return walk
        if reports is None:
            walk.deferred = True
            return walk
        if not reports:
            return walk
        walk.records.extend(reports)
        newest_day = max((day for record in reports if (day := _report_day(record)) is not None), default=None)
        if newest_day is None:
            return walk
        next_end = day_start(newest_day)
        if next_end <= span.start or next_end >= end:
            return walk
        end = next_end
    return walk


def _gap_span(
    station: SensorStation, frontiers: StationFrontiers, *, start: datetime, end: datetime
) -> HistoryWindow | None:
    """The done-day span a station holds nothing for: after its newest held day, or all of it if unknown."""
    newest = frontiers.newest_day(station.station_identifier)
    gap_start = start if newest is None else max(start, day_start(newest) + timedelta(days=1))
    return HistoryWindow(start=gap_start, end=end) if gap_start < end else None


async def _walk_gaps(
    client: httpx.AsyncClient,
    stations: Sequence[SensorStation],
    plan: SensorsDayPlan,
    frontiers: StationFrontiers,
    budget: SensorsFetchBudget,
) -> tuple[list[dict[str, object]], int, int, int]:
    """Gap-walk every station over the done days before the earliest due day: (records, walked, deferred, failed)."""
    end = day_start(plan.due_days[0])
    if end <= plan.window.start:
        return [], 0, 0, 0
    spans = [(station, _gap_span(station, frontiers, start=plan.window.start, end=end)) for station in stations]
    walking = [(station, span) for station, span in spans if span is not None]
    concurrency = asyncio.Semaphore(MAX_CONCURRENT_STATION_REQUESTS)

    async def walk(station: SensorStation, span: HistoryWindow) -> _Walk:
        async with concurrency:
            return await _walk_station(client, station, span, budget)

    records: list[dict[str, object]] = []
    deferred = failed = 0
    for result in await asyncio.gather(*(walk(station, span) for station, span in walking)):
        records.extend(result.records)
        deferred += result.deferred
        failed += result.failed
    return records, len(walking), deferred, failed


@dataclass(slots=True)
class _DueDays:
    """Every due day's fate this turn, filled oldest first by `_sweep_due_days`."""

    records: list[dict[str, object]] = field(default_factory=list)
    swept: list[date] = field(default_factory=list)
    unswept: list[date] = field(default_factory=list)
    resumed: list[date] = field(default_factory=list)
    in_progress: list[SweepInProgress] = field(default_factory=list)
    discarded: list[tuple[date, str]] = field(default_factory=list)
    unavailable: int = 0


async def _sweep_due_days(  # noqa: PLR0913 - the days, roster, frontiers, budget and scratch are separate inputs
    client: httpx.AsyncClient,
    stations: Sequence[SensorStation],
    days: Sequence[date],
    window: HistoryWindow,
    frontiers: StationFrontiers,
    budget: SensorsFetchBudget,
    progress: SweepProgressStore | None,
    *,
    roster_degraded: bool = False,
) -> _DueDays:
    """Sweep each due day oldest first, resuming saved progress so no station is asked twice for a day.

    A day publishes only once every roster station was asked or has exhausted its attempt cap
    (`progress.SENSORS_STATION_ATTEMPT_CAP`). A day the budget cuts short saves what it asked; a fully
    swept day with reports also saves, so a failed publish retries without NWS. A fully swept day with
    nothing to publish clears its scratch, so the next turn asks again (the outage case).

    `roster_degraded` (this turn's own roster is missing a state -- `ingest/sensors.py::StationRoster`)
    turns a roster mismatch into an unpersisted, ephemeral sweep for the day (`HeldProgress.persist`),
    rather than a discard, so a transient state outage never costs a day's real saved progress.
    """
    roster = roster_sha256(stations)
    outcome = _DueDays()
    for day in days:
        held = HeldProgress(progress=DaySweepProgress(day=day, roster_sha256=roster), resumed=False)
        if progress is not None:
            held = progress.resume(day, roster=roster, allow_roster_discard=not roster_degraded)
        if held.discarded is not None:
            outcome.discarded.append((day, held.discarded))
        sweep_progress = held.progress
        if held.resumed:
            outcome.resumed.append(day)
        remaining = sweep_progress.remaining(stations)
        # Whether THIS TURN's own slice of the sweep asked anything -- never a before/after size
        # comparison on `stations_asked`, which counts DISTINCT stations and so cannot tell a station
        # re-asked (and failed again) from one this turn never touched at all: both leave the
        # `unavailable` dict's key set unchanged, even though a re-ask is real progress against its
        # attempt cap and must be saved.
        advanced = False
        if remaining and budget.exhausted is None:
            sweep = await _sweep_day(client, remaining, day, window, frontiers, budget)
            sweep_progress.answered.update(sweep.answered)
            for station_identifier in sweep.unavailable:
                sweep_progress.record_unavailable(station_identifier)
            outcome.unavailable += len(sweep.unavailable)
            advanced = bool(sweep.answered) or bool(sweep.unavailable)
        complete = not sweep_progress.remaining(stations)
        day_records = sweep_progress.records()
        if complete:
            outcome.swept.append(day)
            outcome.records.extend(day_records)
            if progress is not None and held.persist:
                if day_records:
                    if advanced:
                        progress.save(sweep_progress)
                else:
                    progress.clear(day)
            continue
        outcome.unswept.append(day)
        if held.persist and advanced and progress is not None and progress.save(sweep_progress):
            outcome.in_progress.append(
                SweepInProgress(day=day, stations_asked=sweep_progress.stations_asked, stations_total=len(stations))
            )
    return outcome


async def poll_recent_sensor_readings(  # noqa: PLR0913 - the turn's clock, ceilings, bucket, budget and scratch are separate dials
    client: httpx.AsyncClient,
    bbox: str | None = None,
    *,
    now: datetime | None = None,
    max_records: int | None = None,
    store: ObjectStore | None = None,
    budget: SensorsFetchBudget | None = None,
    progress: SweepProgressStore | None = None,
    max_days: int | None = None,
) -> SensorsPollResult:
    """Run one turn's NWS questions: nothing when no day is due, else the sweep and then the gap walk.

    With `store`, the day plan and station frontiers come from z13 (`watermark.py`); without it every
    ended day past the allowance is due and no station is known. With `progress`, a due day's sweep
    resumes across turns (`progress.py`). `max_days` caps how many due days the turn sweeps, oldest
    first: the oldest due day is the next to age out of NWS retention.
    """
    resolved_bbox = resolve_bounded_bbox(bbox)
    if resolved_bbox is None:
        raise SensorsSourceError("no bbox configured: pass --bbox or set INGEST_BBOX")
    fetched_at = now if now is not None else datetime.now(UTC)
    window = _rolling_window(fetched_at)
    ceiling = SENSORS_DEFAULT_MAX_RECORDS if max_records is None else max_records
    spend = budget if budget is not None else SensorsFetchBudget()
    spend.max_requests = min(spend.max_requests, ceiling // SENSORS_REPORTS_PER_REQUEST)

    plan = (
        owed_day_plan(window, now=fetched_at) if store is None else read_sensors_day_plan(store, window, now=fetched_at)
    )
    if plan.nothing_due:
        return SensorsPollResult(fetched_at=fetched_at, plan=plan, days_unreadable=plan.days_unreadable)

    if progress is not None:
        progress.prune(plan.due_days)
    reached = plan.due_days if max_days is None else plan.due_days[:max_days]
    frontiers = StationFrontiers() if store is None else read_station_frontiers(store, plan.published_days)
    roster = await fetch_station_roster(client, resolved_bbox)
    stations = roster.stations
    due = await _sweep_due_days(
        client, stations, reached, window, frontiers, spend, progress, roster_degraded=roster.degraded
    )
    records = due.records
    unavailable = due.unavailable

    gap_records, walked, deferred, failed = await _walk_gaps(client, stations, plan, frontiers, spend)
    unavailable += failed
    # Only done days take gap-walk reports: a due day is published by its own full sweep or not at all.
    done = frozenset(plan.done_days)
    landed: list[tuple[date, dict[str, object]]] = []
    for record in gap_records:
        landed_on = _report_day(record)
        if landed_on is not None and landed_on in done:
            landed.append((landed_on, record))
    backfilled = sorted({landed_on for landed_on, _record in landed})
    records.extend(record for _landed_on, record in landed)

    request = FetchRequest(bbox=resolved_bbox, max_records=ceiling, now=fetched_at, client=client)
    selection = select_writes(nws_sensor_source(fetched_at), records, request)
    return SensorsPollResult(
        fetched_at=fetched_at,
        plan=plan,
        stations_polled=len(stations),
        records_seen=len(records),
        writes=tuple(selection.writes),
        rejected=selection.rejected,
        dropped=selection.dropped,
        days_swept=tuple(due.swept),
        days_unswept=tuple(due.unswept),
        days_deferred=tuple(plan.due_days[len(reached) :]),
        days_resumed=tuple(due.resumed),
        sweep_in_progress=tuple(due.in_progress),
        progress_discarded=tuple(due.discarded),
        days_backfilled=tuple(backfilled),
        stations_gap_walked=walked,
        stations_deferred=deferred,
        stations_unavailable=unavailable,
        requests=spend.requests,
        bytes_in=spend.bytes_in,
        budget_exhausted=spend.exhausted,
        days_unreadable=plan.days_unreadable + frontiers.days_unreadable,
        frontiers=frontiers,
        roster_states_unavailable=roster.unavailable_states,
    )


__all__ = [
    "BUDGET_BYTES",
    "BUDGET_REQUESTS",
    "BUDGET_TIME",
    "SENSORS_DEFAULT_MAX_RECORDS",
    "SENSORS_FETCH_TIME_SHARE",
    "SENSORS_MAX_BYTES_PER_RUN",
    "SENSORS_MAX_MAX_RECORDS",
    "SENSORS_MAX_REQUESTS_PER_RUN",
    "SENSORS_MIN_MAX_RECORDS",
    "SENSORS_OBSERVATION_BOUNDS",
    "SENSORS_REPORTS_PER_REQUEST",
    "SENSORS_ROLLING_WINDOW",
    "SensorsFetchBudget",
    "SensorsPollResult",
    "SensorsSourceError",
    "SweepInProgress",
    "poll_recent_sensor_readings",
]
