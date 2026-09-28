"""Poll NOAA NWS ground stations for one rolling window, reusing the ingest job's own fetchers.

THE ACQUISITION MODEL HAS AN ARCHIVE ENDPOINT, BUT A ROLLING ONE. Unlike `weather_observations/`'s
Open-Meteo `current` endpoint (no `start`/`end` at all), `ingest/sensors.py::observation_url` DOES
accept a bounded `start`/`end` window
(`{NWS_API_BASE_URL}/stations/{id}/observations?start=..&end=..`) -- but api.weather.gov only
ever answers inside its own rolling retention, measured at `NWS_OBSERVATION_RETENTION = timedelta(days=6)`
(`ingest/sensors.py:94-96`). `nws_sensor_source(now)` states that fact in typed terms:
`HistoryCapability(supported=True, earliest=moment - NWS_OBSERVATION_RETENTION)`
(`ingest/sensors.py:596-614`), computed fresh from the `now` PASSED IN, never a module constant.

THIS IS WHY THE FLOOR HERE IS ROLLING, NOT FIXED. A writer that asked NWS for one exact past day by
date, the way `climate`/`soil` ask their archives, would get real data for six days and then start
receiving nothing for every older day forever -- and if that "nothing" were read as a governed
absence (the `climate`/`soil` convention for a settled source that answered empty), it would
permanently overwrite the record of days the OLD Postgres-reading adapter (`pipeline/lanes/sensors.py`,
the registered `LANE_REGISTRY['sensors'].adapter` until 2026-09-07 and deleted with that swap) already
captured and that this producer can never re-fetch. Those published days are still in the bucket and
nothing rewrites them now, which is exactly why this module must never manufacture an absence over
one. So it never asks for one named day: the window it computes is `[now - NWS_OBSERVATION_RETENTION,
now)`, and it lets `rows.py` bucket whatever days that window actually touched -- that window is still
the CEILING every station's request is clamped inside (see below for why it is no longer, since
2026-09-28, the request every station actually sends). `_rolling_window` proves the window it built is
provably inside the
source's own declared capability by calling `HistoryCapability.require` on it -- a defensive,
self-checking assertion, not a request the source could ever refuse given how the window is derived.

THE WINDOW IS THE CEILING, NOT THE REQUEST (since 2026-09-28). Asking every station for the whole six
days on every run was measured at 601 requests and 447 MB a day from api.weather.gov -- roughly 0.74 MB
a station, almost all of it reports this lane already holds. `watermark.py` now reads, from the lane's
own z13, each station's newest published report and the lane-wide frontier of days not yet settled,
and each station is asked only for `[min(frontier, newest - overlap), now)`, clamped inside this
rolling window. A station with no published report inside the window (the first run, a station new
to the roster) still gets the whole window, so the self-healing across a missed run -- and across a
missed STATION or a failed day write -- that the full window bought is kept; only the re-transfer of
settled days is dropped. See this package's `AGENTS.md`, "Fetch only what can still change a
published block", for the argument and the one compound failure it does not cover.

FRESHNESS AND TRUNCATION ARE REUSED, NOT REIMPLEMENTED. `select_writes` applies the identical
`FreshnessRule` (`nws_sensor_source(now).freshness`, keyed to the SAME `NWS_OBSERVATION_RETENTION`)
and the identical newest-survives truncation ranking the Postgres ingest job's `_run_sensor_job`
already applies (`ingest/sensors.py:822-828`) -- so a direct-written accept/reject/truncate decision
on one poll is byte-identical to what the retired ingest cron would have decided on the same
response, matching the discipline `weather_observations/source.py` states for its own reuse of
`get_current_weather`.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.ingest.policy import resolve_bounded_bbox
from agri_data_service.ingest.sensors import (
    MAX_CONCURRENT_STATION_REQUESTS,
    NWS_OBSERVATION_RETENTION,
    NWS_SENSOR_SOURCE,
    STATION_POLL_BATCH_SIZE,
    fetch_station_observations,
    fetch_station_roster,
    nws_sensor_source,
)
from agri_data_service.ingest.source import FetchRequest, HistoryWindow, select_writes
from agri_data_service.pipeline.direct.sensors.watermark import read_sensors_fetch_plan, unwatermarked_plan

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx

    from agri_data_service.ingest.records import FeatureWrite
    from agri_data_service.ingest.sensors import SensorStation
    from agri_data_service.pipeline.direct.sensors.watermark import SensorsFetchPlan
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

#: The CEILING every station's request is clamped inside: the full currently-available rolling
#: retention, so a station with no published report yet (first run, new roster member) still recovers
#: as much of it as NWS holds. A day that ages out of this window before the next poll runs is gone
#: from the source forever (`ingest/sensors.py:94-96`), so this ceiling itself is never shortened.
#: Since 2026-09-28 it is no longer what most requests actually ask for -- `watermark.py` narrows a
#: held station's own request to what can still change a published day; see that module and
#: `AGENTS.md`, "Fetch only what can still change a published block".
SENSORS_ROLLING_WINDOW: Final = NWS_OBSERVATION_RETENTION

#: Deliberately NOT `ingest.policy.resolve_max_source_records` (default 10,000, operator ceiling
#: 50,000). That knob was sized around a "current" poll -- `window=None` returns at most one reading
#: per station, so the default roster of 750 (`ingest/sensors.py::DEFAULT_MAX_STATIONS`) never comes
#: close to it. A full six-day window multiplies that by up to roughly 24 hourly readings a station a
#: day: 750 x 6 x 24 is 108,000, already past even the shared knob's own maximum. Worse than merely
#: dropping the oldest readings (which is what `select_writes`'s truncation ranking is designed for):
#: `_collect_planned_records` stops FETCHING once its ceiling is reached, mid-roster, so a too-low
#: ceiling here would silently and permanently starve every station later in the alphabetically
#: sorted roster (`ingest/sensors.py::fetch_station_roster`) of ANY history, run after run -- not a
#: bounded, rotating truncation but a structural blind spot. 200,000 comfortably covers the default
#: roster with headroom for stations that report more often than hourly (METAR SPECIs).
SENSORS_DEFAULT_MAX_RECORDS: Final = 200_000
SENSORS_MIN_MAX_RECORDS: Final = 1_000
SENSORS_MAX_MAX_RECORDS: Final = 1_000_000


class SensorsSourceError(ValueError):
    """Raised when no bbox is configured to poll the NWS roster from."""


@dataclass(frozen=True, slots=True)
class SensorsPollResult:
    """Everything one rolling-window poll of the NWS roster produced, already freshness-filtered."""

    fetched_at: datetime
    stations_polled: int
    records_seen: int
    writes: tuple[FeatureWrite, ...]
    rejected: int
    dropped: int
    #: Stations asked for less than the whole rolling window because the lane already held them.
    stations_watermarked: int = 0
    #: Stations whose request raised; counted, never fatal, exactly as `collect_sensor_records` does.
    stations_unavailable: int = 0
    #: Window days whose z13 watermark read raised and were folded in as reopened instead of aborting
    #: the plan; see `watermark.py::read_sensors_fetch_plan`. Zero when `store` is None.
    days_unreadable: int = 0


@dataclass(frozen=True, slots=True)
class _PlannedCollection:
    """What one planned roster sweep returned, with the counts the fetch event reports."""

    records: list[dict[str, object]]
    watermarked: int
    unavailable: int


async def _collect_planned_records(
    client: httpx.AsyncClient,
    stations: Sequence[SensorStation],
    plan: SensorsFetchPlan,
    ceiling: int,
) -> _PlannedCollection:
    """`ingest/sensors.py::collect_sensor_records` with one window PER STATION taken from `plan`.

    Same batch size, same concurrency ceiling, same per-station failure tolerance and same stop at
    `ceiling`; the only difference is that each station's request carries its own `start`.
    """
    concurrency = asyncio.Semaphore(MAX_CONCURRENT_STATION_REQUESTS)

    async def poll(station: SensorStation) -> list[dict[str, object]]:
        async with concurrency:
            return await fetch_station_observations(client, station, plan.window_for(station.station_identifier))

    records: list[dict[str, object]] = []
    watermarked = unavailable = 0
    for offset in range(0, len(stations), STATION_POLL_BATCH_SIZE):
        batch = stations[offset : offset + STATION_POLL_BATCH_SIZE]
        watermarked += sum(
            1 for station in batch if plan.window_for(station.station_identifier).start > plan.window.start
        )
        for collection in await asyncio.gather(*(poll(station) for station in batch), return_exceptions=True):
            if isinstance(collection, BaseException):
                unavailable += 1
                continue
            records.extend(collection)
        if len(records) >= ceiling:
            break
    return _PlannedCollection(records=records, watermarked=watermarked, unavailable=unavailable)


def _rolling_window(now: datetime) -> HistoryWindow:
    """Build `[now - retention, now)` and prove it is inside the source's own declared capability.

    The proof is not a live check against NWS -- it can never fail, because the window is derived
    FROM `nws_sensor_source(now).history.earliest` in the first place. It exists so a future change
    to either this constant or `NWS_OBSERVATION_RETENTION` cannot silently drift the two apart:
    `HistoryCapability.require` raises `HistoryUnavailableError` the moment they disagree, rather
    than this module quietly asking for a window the source has, in typed terms, already refused.
    """
    window = HistoryWindow(start=now - SENSORS_ROLLING_WINDOW, end=now)
    nws_sensor_source(now).history.require(NWS_SENSOR_SOURCE, window)
    return window


async def poll_recent_sensor_readings(
    client: httpx.AsyncClient,
    bbox: str | None = None,
    *,
    now: datetime | None = None,
    max_records: int | None = None,
    store: ObjectStore | None = None,
) -> SensorsPollResult:
    """Fetch every in-box station's readings the lane does not already hold, roster then history.

    Reuses `fetch_station_roster` verbatim -- the same pagination, coverage-box filter, network
    filter and determinism-then-cap ordering `run_sensor_ingestion_job` applies. With `store`, each
    station's window is narrowed by `watermark.py` from what z13 already holds; without it every
    station gets the whole rolling window, the pre-2026-09-28 request shape. `select_writes` then
    applies the source's freshness rule and truncation ranking unchanged.
    """
    resolved_bbox = resolve_bounded_bbox(bbox)
    if resolved_bbox is None:
        raise SensorsSourceError("no bbox configured: pass --bbox or set INGEST_BBOX")
    fetched_at = now if now is not None else datetime.now(UTC)
    window = _rolling_window(fetched_at)
    ceiling = SENSORS_DEFAULT_MAX_RECORDS if max_records is None else max_records

    stations = await fetch_station_roster(client, resolved_bbox)
    if not stations:
        return SensorsPollResult(
            fetched_at=fetched_at, stations_polled=0, records_seen=0, writes=(), rejected=0, dropped=0
        )
    plan = unwatermarked_plan(window) if store is None else read_sensors_fetch_plan(store, window, now=fetched_at)
    collected = await _collect_planned_records(client, stations, plan, ceiling)
    request = FetchRequest(bbox=resolved_bbox, max_records=ceiling, now=fetched_at, client=client)
    selection = select_writes(nws_sensor_source(fetched_at), collected.records, request)
    return SensorsPollResult(
        fetched_at=fetched_at,
        stations_polled=len(stations),
        records_seen=len(collected.records),
        writes=tuple(selection.writes),
        rejected=selection.rejected,
        dropped=selection.dropped,
        stations_watermarked=collected.watermarked,
        stations_unavailable=collected.unavailable,
        days_unreadable=plan.days_unreadable,
    )


__all__ = [
    "SENSORS_DEFAULT_MAX_RECORDS",
    "SENSORS_MAX_MAX_RECORDS",
    "SENSORS_MIN_MAX_RECORDS",
    "SENSORS_ROLLING_WINDOW",
    "SensorsPollResult",
    "SensorsSourceError",
    "poll_recent_sensor_readings",
]
