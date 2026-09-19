"""Retain and reparse Open-Meteo current-conditions parser inputs; see this directory's AGENTS.md.

`weather-observations` is a ROLLING feed: the provider serves "now" and keeps no archive, so unlike
`climate` or `soil` there is no day this lane can ever re-fetch. If a poll's Parquet write fails,
that bucket is unrecoverable -- which is why the shared `pipeline/parquet/source_checkpoint.py`
machinery, already used by climate and soil, is extended to this lane here rather than left to a
lane-local invention. Retaining the body is the ONLY thing that turns a lost write into a retry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.ingest.http import UpstreamPayloadError
from agri_data_service.ingest.open_meteo import current_weather_url, parse_current_weather
from agri_data_service.pipeline.direct.weather_observations.rows import _observation_day
from agri_data_service.pipeline.direct.weather_observations.source import WeatherPointObservation
from agri_data_service.pipeline.parquet.source_checkpoint import (
    SourceCheckpoint,
    SourceCheckpointIdentity,
    report_checkpoint_rejection,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date, datetime

    from agri_data_service.pipeline.direct.weather_observations.source import WeatherPollResult
    from agri_data_service.pipeline.parquet.source_checkpoint import SourceResponseCheckpoints

#: Versioned separately from the serving stream: a parser change invalidates checkpoints without
#: touching a published partition.
WEATHER_CURRENT_CHECKPOINT_PROVIDER: Final = "open-meteo-current-conditions-v1"

WeatherRecoveryState = Literal["recoverable_capture", "source_retention_loss"]


@dataclass(frozen=True, slots=True)
class WeatherCheckpointReport:
    """How many accepted points this poll actually proved retained, read back rather than assumed."""

    attempted: int = 0
    retained: int = 0
    failed: int = 0

    def to_summary(self) -> dict[str, int]:
        """Report verified retention separately from attempted writes; a write is not a retention."""
        return {
            "source_checkpoints_attempted": self.attempted,
            "source_checkpoints_retained": self.retained,
            "source_checkpoints_failed": self.failed,
        }


@dataclass(frozen=True, slots=True)
class WeatherRecoveryReport:
    """One day's retained-source verdict, deliberately separate from that day's Parquet publication."""

    day: date
    state: WeatherRecoveryState
    support_points: int
    recovered_points: int
    missing_or_rejected_points: int
    observations: tuple[WeatherPointObservation, ...]


def weather_support_sha256(points: Sequence[tuple[float, float]]) -> str:
    """Bind retained responses to the COMPLETE ordered support grid they were polled for.

    A recovery that reparsed a subset of a different grid would publish a day whose extent is not
    this lane's, so the grid digest is part of every checkpoint identity.
    """
    if not points or len(points) != len(set(points)):
        raise ValueError("weather support must be nonempty and unique")
    support = [{"latitude": latitude, "longitude": longitude} for latitude, longitude in points]
    return sha256_digest(canonical_json(support))


def weather_checkpoint_identity(
    point: tuple[float, float], *, day: date, support_sha256: str
) -> SourceCheckpointIdentity:
    """Name one point/day's original request; the key namespace cannot collide with a serving partition."""
    latitude, longitude = point
    return SourceCheckpointIdentity(
        WEATHER_CURRENT_CHECKPOINT_PROVIDER,
        support_sha256,
        day.isoformat(),
        current_weather_url(latitude, longitude),
    )


def checkpoint_current_poll(
    poll: WeatherPollResult,
    points: Sequence[tuple[float, float]],
    checkpoints: SourceResponseCheckpoints,
) -> WeatherCheckpointReport:
    """Retain every accepted parser input, then READ EACH BACK before counting it retained."""
    support_sha256 = weather_support_sha256(points)
    accepted = tuple(points)
    retained = failed = 0
    for observed in poll.observations:
        observed_at = observed.observation.get("observedAt")
        if observed.response_body is None or not isinstance(observed_at, str):
            failed += 1
            continue
        if (observed.latitude, observed.longitude) not in accepted:
            failed += 1
            continue
        identity = weather_checkpoint_identity(
            (observed.latitude, observed.longitude), day=_observation_day(observed_at), support_sha256=support_sha256
        )
        checkpoints.write(
            identity,
            SourceCheckpoint(body=observed.response_body, retrieved_at=poll.fetched_at),
            response_sha256=sha256_digest(observed.response_body),
        )
        stored = checkpoints.read(identity, now=poll.fetched_at)
        if stored is not None and stored.body == observed.response_body:
            retained += 1
        else:
            failed += 1
    return WeatherCheckpointReport(attempted=len(poll.observations), retained=retained, failed=failed)


def recover_weather_day(
    day: date,
    points: Sequence[tuple[float, float]],
    checkpoints: SourceResponseCheckpoints,
    *,
    now: datetime,
) -> WeatherRecoveryReport:
    """Reparse a day from retained bodies; a partial support grid is a LOSS, never a publishable day.

    This returns observations; it does not publish them. Republication stays with the forward
    writer's own lane-day lock and finalizer, so a recovery can never take a shortcut past them.
    """
    support_sha256 = weather_support_sha256(points)
    recovered: list[WeatherPointObservation] = []
    missing_or_rejected = 0
    for point in points:
        latitude, longitude = point
        identity = weather_checkpoint_identity(point, day=day, support_sha256=support_sha256)
        checkpoint = checkpoints.read(identity, now=now)
        if checkpoint is None:
            missing_or_rejected += 1
            continue
        try:
            observation = parse_current_weather(json.loads(checkpoint.body), checkpoint.retrieved_at)
            if _observation_day(str(observation["observedAt"])) != day:
                raise ValueError("checkpoint response names a different observation day")
        except (UpstreamPayloadError, TypeError, ValueError) as error:
            report_checkpoint_rejection(identity, error)
            missing_or_rejected += 1
            continue
        recovered.append(
            WeatherPointObservation(
                latitude=latitude,
                longitude=longitude,
                observation=observation,
                response_body=checkpoint.body,
            )
        )
    state: WeatherRecoveryState = "source_retention_loss" if missing_or_rejected else "recoverable_capture"
    return WeatherRecoveryReport(
        day=day,
        state=state,
        support_points=len(points),
        recovered_points=len(recovered),
        missing_or_rejected_points=missing_or_rejected,
        observations=tuple(recovered),
    )


__all__ = [
    "WEATHER_CURRENT_CHECKPOINT_PROVIDER",
    "WeatherCheckpointReport",
    "WeatherRecoveryReport",
    "checkpoint_current_poll",
    "recover_weather_day",
    "weather_checkpoint_identity",
    "weather_support_sha256",
]
