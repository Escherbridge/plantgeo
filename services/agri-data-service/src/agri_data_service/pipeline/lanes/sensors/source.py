"""Sweep NWS's rolling roster for one day, bypassing the governed `ProviderClient` (Wave O).

THE ROSTER IS UNKNOWN AT `plan_requests` TIME, exactly the tension `pipeline/lanes/watersheds/
source.py` and `pipeline/lanes/watersheds/nhdplus.py` name for NHDPlus_HR's two-phase walk: which
stations exist is itself an upstream answer (`GET /stations`), and `plan_requests` is synchronous
(the Protocol forbids I/O there), so it cannot pre-plan one `SourceRequest` per station. `nws.py`
therefore plans exactly ONE unit per day and this module fetches it whole, through its OWN
`upstream_client`-backed walk -- the allowed alternative Wave O names ("fetches only through the
provider client OR the existing source module's upstream_client"). `nws.toml` needs no per-station
`path_template` endpoint as a result; see `pipeline/lanes/sensors/AGENTS.md`.

ONE REPORT PER STATION-DAY REQUEST (`limit=1`), RESTATED FROM THE LEGACY WRITER. NWS answers a
bounded `start`/`end` window newest-first, so `limit=1` is the day's winner. Measured 2026-10-03 on
KBOI: one 5-minute station-day without `limit` is 312 features and 1.2 MB, past `ingest/sensors.py::
OBSERVATION_BOUNDS`' 1 MiB cap; with `limit=1` it is 5.4 KB. `_limited_observation_url` and
`SENSORS_OBSERVATION_BOUNDS` (64 KiB) restate `pipeline/direct/sensors/source.py`'s identically
named pair, never import it (`pipeline/direct/**` is forbidden to this package).

FAILURE ISOLATION IS PER STATION; A DEGRADED ROSTER REFUSES THE WHOLE SWEEP. `_poll_station` below
isolates a per-STATION observation failure (any `UpstreamError`) the way
`pipeline/direct/sensors/source.py::_sweep_day` does: that station is reported unanswered, never
fatal to the day. A per-STATE roster failure is different: `ingest.sensors.fetch_station_roster`
reports it as `unavailable_states` rather than raising, and a sweep over that roster would be
missing whole states from BOTH `expected_unit_ids` and `present_unit_ids` -- a day that reads
complete while it is not. `_sweep` therefore raises on `roster.degraded` (the signal legacy reads at
`pipeline/direct/sensors/source.py::poll_recent_sensor_readings`). That raise, the roster call
itself raising, or a code fault in this module escapes `fetch_sensors_day_snapshot` and becomes that
unit's `strategy_error` (`pipeline/runner/AGENTS.md` "A strategy fault in settle/rows/derive is that
day's strategy_error"; `pipeline/runner/fetch.py` extends the same classification to a `fetch()`
raise). No new exception type is minted: the degraded roster raises the base `UpstreamError`.

THE WINNING-REPORT REDUCTION IS RESTATED, NEVER IMPORTED, FROM `pipeline/direct/sensors/rows.py`.
`_day_winner` is the same `max()` over (timestamp text, producer_local_id), both DESC, as that
module's `_winning_write` -- the literal Python form of `sql/pipeline/direct/sensors/
postgres_day_counts.sql`'s `ORDER BY observedAt DESC, feature.id DESC` `DISTINCT ON` reduction.
With `limit=1` it normally sees one record; it still reduces, so an upstream that ignores `limit`
cannot pick an older report. `feature_id` carries the SAME `direct:` prefix that module mints
(never a new one), so no entry is owed to `tests/lanes/test_direct_feature_id_parity.py`.

NO ROW-LEVEL MERGE WITH THE PREVIOUSLY PUBLISHED DAY HAPPENS HERE, UNLIKE
`pipeline/lanes/sensors/merge.py`. A strategy cannot read back the previous partition's rows, so
`rows()` always returns this turn's own complete sweep. The anti-regression property legacy's
block-merge gave -- a later poll cannot silently erase a station's measurements -- is recovered
generically instead: `present_station_ids` is the set of stations that ANSWERED (not the set with
rows), so a failed poll makes the day partial and the runner's S11 rules refuse it, while a fully
answered sweep reaches the digest rule and a newer report rewrites the day. See
`pipeline/lanes/sensors/AGENTS.md`, "Coverage is stations that answered".
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.ingest.http import (
    UpstreamBounds,
    UpstreamError,
    UpstreamPayloadError,
    fetch_bounded_json,
    upstream_client,
)
from agri_data_service.ingest.identity import MissingNativeKeyError
from agri_data_service.ingest.sensors import (
    MAX_CONCURRENT_STATION_REQUESTS,
    NWS_OBSERVATION_RETENTION,
    build_sensor_reading_identity,
    fetch_station_roster,
    nws_request_headers,
    observation_url,
    parse_observation,
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
_ISO_DAY_LENGTH: Final = len("YYYY-MM-DD")

#: Reports asked for per request; NWS answers newest-first, so one is the day's winner (module docstring).
SENSORS_REPORTS_PER_REQUEST: Final = 1

#: Per-request cap: a `limit=1` answer is about 5.4 KB, a whole un-limited day 1.2 MB (module docstring).
SENSORS_OBSERVATION_BOUNDS: Final = UpstreamBounds(max_bytes=64 * 1024, timeout_seconds=15.0)


def _day_start(day: date) -> datetime:
    """Midnight UTC opening one calendar day."""
    return datetime.combine(day, time.min, tzinfo=UTC)


def _day_end(day: date) -> datetime:
    """Midnight UTC closing one calendar day (exclusive)."""
    return _day_start(day) + _ONE_DAY


def _limited_observation_url(station_identifier: str, window: HistoryWindow) -> str:
    """`observation_url` for a bounded window, asking only for the newest `SENSORS_REPORTS_PER_REQUEST`."""
    return f"{observation_url(station_identifier, window)}&limit={SENSORS_REPORTS_PER_REQUEST}"


def _collection_features(payload: object) -> list[object]:
    """A history FeatureCollection's features, refusing a body that is not one."""
    features = payload.get("features") if isinstance(payload, dict) else None
    if not isinstance(features, list):
        raise UpstreamPayloadError("NOAA NWS returned an invalid observation collection")
    return features


@dataclass(frozen=True, slots=True)
class SensorsDaySnapshot:
    """One day's roster sweep: whether NWS could still serve it, and every station's winning rows.

    `roster_station_ids` is the roster this turn asked; `present_station_ids` is the part of it that
    answered (with or without a report for the day) -- the coverage `nws.py::settle` hands the runner.
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


@dataclass(frozen=True, slots=True)
class _DaySweep:
    """What one day's sweep asks: the day, the coverage box, its clamped poll window, and the turn's clock."""

    day: date
    bbox: str
    window: HistoryWindow
    fetched_at: datetime


def _day_winner(records: Sequence[dict[str, object]], *, day: date) -> tuple[dict[str, object], FeatureIdentity] | None:
    """The day's winning report: `max` over (raw timestamp text, producer_local_id), both DESC.

    A record whose own timestamp prefix names a DIFFERENT day than `day` is dropped -- only this
    day's own reports may win it (`pipeline/direct/sensors/rows.py`, "THE DAY KEY IS AN ISO-STRING
    PREFIX").
    """
    candidates: list[tuple[str, str, dict[str, object], FeatureIdentity]] = []
    for record in records:
        timestamp = record.get("timestamp")
        if not isinstance(timestamp, str) or len(timestamp) < _ISO_DAY_LENGTH:
            continue
        try:
            if date.fromisoformat(timestamp[:_ISO_DAY_LENGTH]) != day:
                continue
            identity = build_sensor_reading_identity(record)
        except (MissingNativeKeyError, ValueError):
            continue
        candidates.append((timestamp, identity.producer_local_id, record, identity))
    if not candidates:
        return None
    _timestamp, _producer_local_id, record, identity = max(candidates, key=lambda item: (item[0], item[1]))
    return record, identity


def _measurement_rows(record: dict[str, object], identity: FeatureIdentity, *, day: date) -> list[dict[str, object]]:
    """Fan out one winning report's already-validated readings into base rows at the registered grain."""
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
                # Same `direct:` prefix as `pipeline/direct/sensors/rows.py::_feature_id`.
                "feature_id": f"direct:{identity.producer_local_id}",
                "data_available_at": None,
                "station_longitude": record.get("longitude"),
                "station_latitude": record.get("latitude"),
            }
        )
    return rows


async def _poll_station(
    client: httpx.AsyncClient, station: SensorStation, sweep: _DaySweep
) -> tuple[list[dict[str, object]], bool]:
    """One station's `limit=1` poll: `(rows, answered)`; an `UpstreamError` is isolated as unanswered."""
    try:
        payload = await fetch_bounded_json(
            client,
            _limited_observation_url(station.station_identifier, sweep.window),
            SENSORS_OBSERVATION_BOUNDS,
            nws_request_headers(),
        )
        features = _collection_features(payload)
    except UpstreamError:
        return [], False
    records = [record for feature in features if (record := parse_observation(feature, station)) is not None]
    winner = _day_winner(records, day=sweep.day)
    if winner is None:
        return [], True
    record, identity = winner
    return _measurement_rows(record, identity, day=sweep.day), True


async def _sweep(client: httpx.AsyncClient, sweep: _DaySweep) -> SensorsDaySnapshot:
    """Drain the roster (refusing a degraded one), then poll every station for the day's window."""
    roster = await fetch_station_roster(client, sweep.bbox)
    if roster.degraded:
        raise UpstreamError(
            f"NWS station roster is degraded: states {', '.join(roster.unavailable_states)} unavailable"
        )
    stations = roster.stations
    if not stations:
        return SensorsDaySnapshot(
            day=sweep.day,
            fetched_at=sweep.fetched_at,
            in_retention=True,
            roster_size=0,
            stations_answered=0,
            stations_unavailable=0,
        )
    concurrency = asyncio.Semaphore(MAX_CONCURRENT_STATION_REQUESTS)

    async def poll(station: SensorStation) -> tuple[list[dict[str, object]], bool]:
        async with concurrency:
            return await _poll_station(client, station, sweep)

    results = await asyncio.gather(*(poll(station) for station in stations))
    rows: list[dict[str, object]] = []
    answered: set[str] = set()
    for station, (station_rows, ok) in zip(stations, results, strict=True):
        if ok:
            answered.add(station.station_identifier)
        rows.extend(station_rows)
    return SensorsDaySnapshot(
        day=sweep.day,
        fetched_at=sweep.fetched_at,
        in_retention=True,
        roster_size=len(stations),
        stations_answered=len(answered),
        stations_unavailable=len(stations) - len(answered),
        rows=tuple(rows),
        roster_station_ids=frozenset(station.station_identifier for station in stations),
        present_station_ids=frozenset(answered),
    )


async def fetch_sensors_day_snapshot(
    day: date, bbox: str, *, client: httpx.AsyncClient | None = None, now: datetime | None = None
) -> SensorsDaySnapshot:
    """Sweep one day's whole roster, or answer `in_retention=False` with zero NWS requests when it has rolled out.

    Mirrors `pipeline/direct/sensors/source.py::_rolling_window`: the floor is `now - 6 days`,
    resolved at call time because it rolls. `now` defaults to the real clock; `nws.py::fetch` passes
    the strategy's injected clock.
    """
    fetched_at = now if now is not None else datetime.now(UTC)
    window_start, window_end = fetched_at - NWS_OBSERVATION_RETENTION, fetched_at
    day_s, day_e = _day_start(day), _day_end(day)
    if not (day_e > window_start and day_s < window_end):
        return SensorsDaySnapshot(
            day=day,
            fetched_at=fetched_at,
            in_retention=False,
            roster_size=0,
            stations_answered=0,
            stations_unavailable=0,
        )
    window = HistoryWindow(start=max(day_s, window_start), end=min(day_e, window_end))
    sweep = _DaySweep(day=day, bbox=bbox, window=window, fetched_at=fetched_at)
    if client is not None:
        return await _sweep(client, sweep)
    async with upstream_client(SENSORS_OBSERVATION_BOUNDS) as owned_client:
        return await _sweep(owned_client, sweep)


__all__ = [
    "SENSORS_OBSERVATION_BOUNDS",
    "SENSORS_REPORTS_PER_REQUEST",
    "SensorsDaySnapshot",
    "fetch_sensors_day_snapshot",
]
