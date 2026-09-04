"""Fetch one current-conditions poll of the sample grid, reusing the ingest job's own point fetcher.

THE ACQUISITION MODEL IS NOT AN ARCHIVE FETCH. `climate/source.py` and `soil/source.py` ask an
archive endpoint for ONE SETTLED PAST DAY and get it back complete in one response. Open-Meteo's
`current` endpoint (`ingest/open_meteo.py::current_weather_url`) has no `start_date`/`end_date` and
answers only the instant closest to now, gated fresh by `MAX_OBSERVATION_AGE` (3 hours). There is no
day this lane's writer can ask the source for by date -- every "day" downstream of this module is
assembled by BUCKETING whatever instants many polls, over time, happen to return. See
`pipeline/direct/AGENTS.md`, "Weather observations", for the consequence that follows: no
`--product`-style enumeration and no settled-day retry ladder, because there is no day to retry.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agri_data_service.ingest.open_meteo import get_current_weather

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx


@dataclass(frozen=True, slots=True)
class WeatherPointObservation:
    """One sample point's validated current-conditions reading, exactly as `parse_current_weather` built it."""

    latitude: float
    longitude: float
    observation: dict[str, object]


@dataclass(frozen=True, slots=True)
class WeatherPollResult:
    """Everything one poll of the bounded sample grid produced, successes and failures counted."""

    fetched_at: datetime
    points_sampled: int
    observations: tuple[WeatherPointObservation, ...]
    unavailable_points: int


async def poll_current_conditions(
    client: httpx.AsyncClient,
    points: Sequence[tuple[float, float]],
    *,
    now: datetime | None = None,
) -> WeatherPollResult:
    """Fetch every sample point, keeping one point's failure or staleness from discarding the rest.

    Reuses `get_current_weather` verbatim -- the same bounds check, fetch, value-range validation and
    `MAX_OBSERVATION_AGE` freshness gate the ingest cron applied -- so a direct-written row and a
    Postgres-written row would have made an identical accept/reject decision on the same response.
    No concurrency limiter is added: `run_weather_ingestion_job` already fans out the full grid (at
    most `MAX_WEATHER_SAMPLE_POINTS` = 150 points) unbounded, and this reuses that proven shape rather
    than inventing a second one.
    """
    fetched_at = now if now is not None else datetime.now(UTC)
    results = await asyncio.gather(
        *(get_current_weather(client, latitude, longitude, fetched_at) for latitude, longitude in points),
        return_exceptions=True,
    )
    observations: list[WeatherPointObservation] = []
    unavailable_points = 0
    for (latitude, longitude), result in zip(points, results, strict=True):
        if isinstance(result, BaseException):
            unavailable_points += 1
            continue
        observations.append(WeatherPointObservation(latitude=latitude, longitude=longitude, observation=result))
    return WeatherPollResult(
        fetched_at=fetched_at,
        points_sampled=len(points),
        observations=tuple(observations),
        unavailable_points=unavailable_points,
    )


__all__ = ["WeatherPointObservation", "WeatherPollResult", "poll_current_conditions"]
