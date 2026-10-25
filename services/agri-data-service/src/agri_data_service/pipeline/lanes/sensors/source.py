"""Sweep NWS's rolling roster for one day, bypassing the governed `ProviderClient` (Wave O).

THE ROSTER IS UNKNOWN AT `plan_requests` TIME, exactly the tension `pipeline/lanes/watersheds/
source.py` and `pipeline/lanes/watersheds/nhdplus.py` name for NHDPlus_HR's two-phase walk: which
stations exist is itself an upstream answer (`GET /stations`), and `plan_requests` is synchronous
(the Protocol forbids I/O there), so it cannot pre-plan one `SourceRequest` per station. `nws.py`
therefore plans exactly ONE unit per day and this module fetches it whole, through its OWN
`upstream_client`-backed walk -- the allowed alternative Wave O names ("fetches only through the
provider client OR the existing source module's upstream_client"). `nws.toml` needs no per-station
`path_template` endpoint as a result; see `pipeline/lanes/sensors/AGENTS.md`.

FAILURE ISOLATION MIRRORS THE LEGACY WRITER, AT TWO LAYERS, NOT ONE. `ingest.sensors.
fetch_station_roster` already isolates a per-STATE roster-page failure (`unavailable_states`,
never a raise); `_poll_station` below isolates a per-STATION observation-request failure the same
way `pipeline/direct/sensors/source.py::_sweep_day` does (`sweep.unavailable`, never fatal to the
day). Only a fault that defeats BOTH layers -- the roster call itself raising, or a code fault in
this module -- escapes `fetch_sensors_day_snapshot` uncaught, and becomes that unit's
`strategy_error` (`pipeline/runner/AGENTS.md` "A strategy fault in settle/rows/derive is that day's
strategy_error"; `pipeline/runner/fetch.py` extends the same classification to a `fetch()` raise).
Per `pipeline/direct/sensors/AGENTS.md`, "a specialized type is reserved for a tested semantic
branch rather than a lane-specific wrapper name" -- no new exception type is minted here; whatever
`ingest.sensors`/`ingest.http` raises (e.g. `UpstreamPayloadError` for a roster page that is not a
readable FeatureCollection) propagates verbatim.

THE WINNING-REPORT REDUCTION IS RESTATED, NEVER IMPORTED, FROM `pipeline/direct/sensors/rows.py`
(`pipeline/direct/**` is forbidden to this package). `_day_winner` is the same `max()` over
(timestamp text, producer_local_id), both DESC, as that module's `_winning_write` -- the literal
Python form of `sql/pipeline/direct/sensors/postgres_day_counts.sql`'s
`ORDER BY observedAt DESC, feature.id DESC` `DISTINCT ON` reduction. `feature_id` carries the SAME
`direct:` prefix that module mints (never a new one), so no entry is owed to
`tests/lanes/test_direct_feature_id_parity.py`.

NO ROW-LEVEL MERGE WITH THE PREVIOUSLY PUBLISHED DAY HAPPENS HERE, UNLIKE
`pipeline/lanes/sensors/merge.py`. A strategy cannot read back the previous partition's rows (the
runner only ever hands it day lists and digests, `pipeline/runner/contract.py::DayContext`), so
`rows()` always returns this turn's own complete sweep. The anti-regression property legacy's
block-merge gave -- a later poll cannot silently erase a station's measurements -- is recovered
generically instead, through `Written.expected_unit_ids`/`present_unit_ids` keyed by station
identifier (`nws.py::settle`) and the runner's own S11 "fewer units never written" rule
(`pipeline/runner/AGENTS.md` "Writer"). See `nws.py`'s module docstring for the one way this
genuinely differs from a static tile partition: the roster itself can change turn to turn.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.identity import MissingNativeKeyError
from agri_data_service.ingest.sensors import (
    MAX_CONCURRENT_STATION_REQUESTS,
    NWS_OBSERVATION_RETENTION,
    OBSERVATION_BOUNDS,
    build_sensor_reading_identity,
    fetch_station_observations,
    fetch_station_roster,
)
from agri_data_service.ingest.source import HistoryWindow

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx

    from agri_data_service.ingest.identity import FeatureIdentity
    from agri_data_service.ingest.sensors import SensorStation

#: Mirrors `pipeline/direct/sensors/rows.py`'s own exclusion: free text, not a measurement value.
_EXCLUDED_MEASUREMENT_KEY: Final = "textDescription"
_ONE_DAY: Final = timedelta(days=1)


def _day_start(day: date) -> datetime:
    """Midnight UTC opening one calendar day."""
    return datetime.combine(day, time.min, tzinfo=UTC)


def _day_end(day: date) -> datetime:
    """Midnight UTC closing one calendar day (exclusive)."""
    return _day_start(day) + _ONE_DAY


@dataclass(frozen=True, slots=True)
class SensorsDaySnapshot:
    """One day's roster sweep: whether NWS could still serve it, and every station's winning rows.

    `roster_station_ids`/`present_station_ids` are this turn's own answer to "what did the roster
    look like, and which of it answered" -- see this module's docstring, "no row-level merge", for
    why these two sets (not a merged table) are how this strategy protects previously published
    stations from a station that merely failed to answer THIS turn.
    """

    day: date
    fetched_at: datetime
    in_retention: bool
    roster_size: int
    stations_answered: int
    stations_unavailable: int
    rows: tuple[dict[str, object], ...] = ()
    roster_station_ids: frozenset[str] = frozenset()
    present_station_ids: frozenset[str] = frozenset()


def _day_winner(
    records: Sequence[dict[str, object]], *, day: date
) -> tuple[dict[str, object], FeatureIdentity] | None:
    """The day's winning report: `max` over (raw timestamp text, producer_local_id), both DESC.

    Restated from `pipeline/direct/sensors/rows.py::_winning_write`; see this module's docstring.
    A record whose own timestamp prefix names a DIFFERENT day than `day` is dropped here -- the
    bounded poll window can straddle a neighbour day at its edges, and only this day's own reports
    may win it (`pipeline/direct/sensors/rows.py`'s module docstring, "THE DAY KEY IS AN ISO-STRING
    PREFIX").
    """
    candidates: list[tuple[str, str, dict[str, object], object]] = []
    for record in records:
        timestamp = record.get("timestamp")
        if not isinstance(timestamp, str) or len(timestamp) < 10:  # noqa: PLR2004 - len("YYYY-MM-DD")
            continue
        try:
            if date.fromisoformat(timestamp[:10]) != day:
                continue
        except ValueError:
            continue
        try:
            identity = build_sensor_reading_identity(record)
        except (MissingNativeKeyError, ValueError):
            continue
        candidates.append((timestamp, identity.producer_local_id, record, identity))
    if not candidates:
        return None
    _timestamp, _producer_local_id, record, identity = max(candidates, key=lambda item: (item[0], item[1]))
    return record, identity


def _measurement_rows(record: dict[str, object], identity: FeatureIdentity, *, day: date) -> list[dict[str, object]]:
    """Fan out one winning report's already-validated readings into base rows at the registered grain.

    `readings` entries are already shape-checked by `ingest.sensors.observation_readings`/
    `_measurement` (finite numeric value; `unitCode`/`qualityControl` present only when NWS sent
    them) -- restated here is only the fan-out, not the per-measurement validation.
    """
    readings = record.get("readings")
    if not isinstance(readings, dict):
        return []
    rows: list[dict[str, object]] = []
    for measurement_name, measurement in readings.items():
        if measurement_name == _EXCLUDED_MEASUREMENT_KEY or not isinstance(measurement, dict):
            continue
        rows.append(
            {
                "sensor_id": identity.entity_local_id,
                "station_name": record.get("stationName"),
                "network": record.get("network"),
                "observed_day": day,
                "observed_at": identity.observed_at,
                "measurement_name": measurement_name,
                "value": measurement["value"],
                "unit_code": measurement.get("unitCode"),
                "quality_control": measurement.get("qualityControl"),
                # Same `direct:` prefix `pipeline/direct/sensors/rows.py::_feature_id` mints; never a
                # new one (module docstring).
                "feature_id": f"direct:{identity.producer_local_id}",
                "data_available_at": None,
                "station_longitude": record.get("longitude"),
                "station_latitude": record.get("latitude"),
            }
        )
    return rows


async def _poll_station(
    client: httpx.AsyncClient, station: SensorStation, window: HistoryWindow, *, day: date
) -> tuple[list[dict[str, object]], bool]:
    """One station's bounded poll: `(rows, answered)`. `answered=False` means the request raised -- isolated."""
    try:
        records = await fetch_station_observations(client, station, window)
    except Exception:  # noqa: BLE001 - isolated per station, never fatal to the day (module docstring)
        return [], False
    winner = _day_winner(records, day=day)
    if winner is None:
        return [], True
    record, identity = winner
    return _measurement_rows(record, identity, day=day), True


async def _sweep(
    client: httpx.AsyncClient, *, day: date, bbox: str, start: datetime, end: datetime, fetched_at: datetime
) -> SensorsDaySnapshot:
    """Drain the roster, then poll every station for `day`'s bounded window, isolating per-station faults."""
    roster = await fetch_station_roster(client, bbox)
    stations = roster.stations
    if not stations:
        return SensorsDaySnapshot(
            day=day, fetched_at=fetched_at, in_retention=True, roster_size=0, stations_answered=0, stations_unavailable=0
        )
    window = HistoryWindow(start=start, end=end)
    concurrency = asyncio.Semaphore(MAX_CONCURRENT_STATION_REQUESTS)

    async def poll(station: SensorStation) -> tuple[list[dict[str, object]], bool]:
        async with concurrency:
            return await _poll_station(client, station, window, day=day)

    results = await asyncio.gather(*(poll(station) for station in stations))
    rows: list[dict[str, object]] = []
    present: set[str] = set()
    answered = unavailable = 0
    for station, (station_rows, ok) in zip(stations, results, strict=True):
        if ok:
            answered += 1
        else:
            unavailable += 1
        if station_rows:
            present.add(station.station_identifier)
            rows.extend(station_rows)
    return SensorsDaySnapshot(
        day=day,
        fetched_at=fetched_at,
        in_retention=True,
        roster_size=len(stations),
        stations_answered=answered,
        stations_unavailable=unavailable,
        rows=tuple(rows),
        roster_station_ids=frozenset(station.station_identifier for station in stations),
        present_station_ids=frozenset(present),
    )


async def fetch_sensors_day_snapshot(
    day: date, bbox: str, *, client: httpx.AsyncClient | None = None, now: datetime | None = None
) -> SensorsDaySnapshot:
    """Sweep one day's whole roster, or answer `in_retention=False` with zero NWS requests when it has rolled out.

    Mirrors `pipeline/direct/sensors/source.py::_rolling_window`: the floor is `now - 6 days`,
    resolved at call time because it rolls forward every day, never a frozen constant. `now`
    defaults to the real clock; tests pass a fixed one (`pipeline/lanes/sensors/nws.py`'s fetch
    calls this with no `now`, so the runner's own turn clock is NOT threaded through here today --
    see `AGENTS.md` for why that is an open item, not a correctness bug, at this sketch's scope).
    """
    fetched_at = now if now is not None else datetime.now(UTC)
    window_start, window_end = fetched_at - NWS_OBSERVATION_RETENTION, fetched_at
    day_s, day_e = _day_start(day), _day_end(day)
    in_retention = day_e > window_start and day_s < window_end
    if not in_retention:
        return SensorsDaySnapshot(
            day=day, fetched_at=fetched_at, in_retention=False, roster_size=0, stations_answered=0, stations_unavailable=0
        )
    start, end = max(day_s, window_start), min(day_e, window_end)
    if client is not None:
        return await _sweep(client, day=day, bbox=bbox, start=start, end=end, fetched_at=fetched_at)
    async with upstream_client(OBSERVATION_BOUNDS) as owned_client:
        return await _sweep(owned_client, day=day, bbox=bbox, start=start, end=end, fetched_at=fetched_at)


__all__ = [
    "SensorsDaySnapshot",
    "fetch_sensors_day_snapshot",
]
