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
still the registered `LANE_REGISTRY['sensors'].adapter`) already captured and that this producer can
never re-fetch. So this module never asks for one named day: it fetches the WHOLE currently-available
window in one poll, `[now - NWS_OBSERVATION_RETENTION, now)`, and lets `rows.py` bucket whatever days
that window actually touched. `_rolling_window` proves the window it built is provably inside the
source's own declared capability by calling `HistoryCapability.require` on it -- a defensive,
self-checking assertion, not a request the source could ever refuse given how the window is derived.

THE TRADE THIS MAKES: MORE BYTES PER RUN, IN EXCHANGE FOR NEVER DEPENDING ON RUN CADENCE. Every poll
asks for the SAME request count regardless of window size (`observation_url` issues one request per
station whether `window` is `None` or set), but a six-day window's response body is far larger than
a single latest-reading response -- an hourly-reporting station over six days is roughly 144 readings
instead of one. That is accepted deliberately: unlike the retired `_run_sensor_job`, which polled
`window=None` ("current" only) and relied on running on a steady cadence forever to never miss an
hour, this writer's whole reason to exist is that the steady cadence STOPPED. A `window=None` design
would only ever capture readings from the moment this writer starts running forward, permanently
losing everything between the old cron's last run and this one's first -- exactly the kind of loss a
6-day-bounded source cannot undo later. Asking for the full window every run makes the writer
self-healing across any gap up to six days, including a missed run, at the cost of re-transferring
data that mostly has not changed. If that bandwidth cost proves too high once this lane is measured
running, the response is to run it LESS often (NWS's own ASOS cadence is hourly, so there is no
freshness reason to poll faster than that), never to shrink the window below the full retention --
a shorter window reopens exactly the permanent-loss risk this paragraph describes.

FRESHNESS AND TRUNCATION ARE REUSED, NOT REIMPLEMENTED. `select_writes` applies the identical
`FreshnessRule` (`nws_sensor_source(now).freshness`, keyed to the SAME `NWS_OBSERVATION_RETENTION`)
and the identical newest-survives truncation ranking the Postgres ingest job's `_run_sensor_job`
already applies (`ingest/sensors.py:822-828`) -- so a direct-written accept/reject/truncate decision
on one poll is byte-identical to what the retired ingest cron would have decided on the same
response, matching the discipline `weather_observations/source.py` states for its own reuse of
`get_current_weather`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.ingest.policy import resolve_bounded_bbox
from agri_data_service.ingest.sensors import (
    NWS_OBSERVATION_RETENTION,
    NWS_SENSOR_SOURCE,
    collect_sensor_records,
    fetch_station_roster,
    nws_sensor_source,
)
from agri_data_service.ingest.source import FetchRequest, HistoryWindow, select_writes

if TYPE_CHECKING:
    import httpx

    from agri_data_service.ingest.writer import FeatureWrite

#: The window this module always asks for: the full currently-available rolling retention, so one
#: poll recovers as much of it as NWS still holds rather than guessing at a shorter, tunable span.
#: A day that ages out of this window before the next poll runs is gone from the source forever
#: (`ingest/sensors.py:94-96`), so there is no "budget" argument for asking for less of it.
SENSORS_ROLLING_WINDOW: Final = NWS_OBSERVATION_RETENTION

#: Deliberately NOT `ingest.policy.resolve_max_source_records` (default 10,000, operator ceiling
#: 50,000). That knob was sized around a "current" poll -- `window=None` returns at most one reading
#: per station, so the default roster of 750 (`ingest/sensors.py::DEFAULT_MAX_STATIONS`) never comes
#: close to it. A full six-day window multiplies that by up to roughly 24 hourly readings a station a
#: day: 750 x 6 x 24 is 108,000, already past even the shared knob's own maximum. Worse than merely
#: dropping the oldest readings (which is what `select_writes`'s truncation ranking is designed for):
#: `collect_sensor_records` stops FETCHING once its ceiling is reached, mid-roster, so a too-low
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
) -> SensorsPollResult:
    """Fetch every in-box station's readings inside the rolling retention window, roster then history.

    Reuses `fetch_station_roster` and `collect_sensor_records` verbatim -- the same pagination,
    coverage-box filter, network filter, determinism-then-cap ordering and bounded-concurrency
    batching `run_sensor_ingestion_job` applies -- so the station population and the readings polled
    for it are exactly what the retired Postgres ingest job would have polled for the same bbox and
    window. `select_writes` then applies the source's freshness rule and truncation ranking, so what
    survives here is exactly what would have been written to `geo.features`.
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
    records = await collect_sensor_records(client, stations, window, ceiling)
    request = FetchRequest(bbox=resolved_bbox, max_records=ceiling, now=fetched_at, client=client)
    selection = select_writes(nws_sensor_source(fetched_at), records, request)
    return SensorsPollResult(
        fetched_at=fetched_at,
        stations_polled=len(stations),
        records_seen=len(records),
        writes=tuple(selection.writes),
        rejected=selection.rejected,
        dropped=selection.dropped,
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
