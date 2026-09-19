"""Retain and reparse Open-Meteo current-conditions parser inputs; see this directory's AGENTS.md.

`weather-observations` is a ROLLING feed: the provider serves "now" and keeps no archive, so unlike
`climate` or `soil` there is no day this lane can ever re-fetch. If a poll's Parquet write fails,
that bucket is unrecoverable -- which is why the shared `pipeline/parquet/source_checkpoint.py`
machinery, already used by climate and soil, is extended to this lane here rather than left to a
lane-local invention. Retaining the body is the ONLY thing that turns a lost write into a retry.

BOTH HALVES HAVE CALLERS. `checkpoint_current_poll` runs on every turn before the first Parquet
write; `recover_weather_day` runs on every turn for each selected day that carries no complete
publication, and on demand for `forward.py --recover-day`. It was retained-but-unreachable code from
2026-09-19's salvage until the wave-9 review named it, and a safety net nothing pulls on is worse
than no net: it reads as covered.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Final, Literal

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.ingest.http import UpstreamPayloadError
from agri_data_service.ingest.open_meteo import current_weather_url, parse_current_weather
from agri_data_service.pipeline.direct.weather_observations.rows import (
    DirectWeatherObservationsRowError,
    observation_day,
)
from agri_data_service.pipeline.direct.weather_observations.source import WeatherPointObservation
from agri_data_service.pipeline.parquet.source_checkpoint import (
    SourceCheckpoint,
    SourceCheckpointIdentity,
    report_checkpoint_rejection,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from agri_data_service.pipeline.direct.weather_observations.source import WeatherPollResult
    from agri_data_service.pipeline.parquet.source_checkpoint import SourceResponseCheckpoints

#: Versioned separately from the serving stream: a parser change invalidates checkpoints without
#: touching a published partition.
WEATHER_CURRENT_CHECKPOINT_PROVIDER: Final = "open-meteo-current-conditions-v1"

#: THREE, not two. The original port collapsed "nothing was ever retained for this day" and "part of
#: the grid was retained" into one `source_retention_loss` verdict, which made the ordinary first
#: poll of a new UTC day report the same unpublishable word as a genuine half-loss -- and made a
#: boundary poll, which legitimately splits ONE grid across two day namespaces (`forward.py:108-110`),
#: report it for both halves. A partial capture is worth merging; only `no_retained_capture` means
#: there is nothing here to repair with.
WeatherRecoveryState = Literal["complete_capture", "partial_capture", "no_retained_capture"]


@dataclass(frozen=True, slots=True)
class WeatherDayRetention:
    """One named day's share of a poll's retention, kept per day because that is how recovery reads it."""

    retained: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class WeatherCheckpointReport:
    """How many accepted points this poll actually proved retained, read back rather than assumed."""

    attempted: int = 0
    retained: int = 0
    failed: int = 0
    #: Per day, because a poll straddling UTC midnight retains into two day namespaces and only a
    #: per-day answer can tell the writer whether THAT day's unwritten bucket is recoverable.
    days: Mapping[date, WeatherDayRetention] = field(default_factory=dict)

    def retained_whole_day(self, day: date) -> bool:
        """Did every accepted point for this day read back? The only honest basis for calling a loss permanent."""
        held = self.days.get(day)
        return held is not None and held.failed == 0 and held.retained > 0

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

    def to_event(self) -> dict[str, object]:
        """Render the verdict for the forward writer's progress stream; the observations stay out of it."""
        return {
            "day": self.day.isoformat(),
            "state": self.state,
            "support_points": self.support_points,
            "recovered_points": self.recovered_points,
            "missing_or_rejected_points": self.missing_or_rejected_points,
        }


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


def _named_day(observed_at: object) -> date | None:
    """Name one reading's day, or `None` when the field is absent or unparseable; never raise here.

    Retention runs BEFORE the first write, so a reading the row builder would have refused must not
    take the whole poll down from inside the safety net.
    """
    if not isinstance(observed_at, str):
        return None
    try:
        return observation_day(observed_at)
    except DirectWeatherObservationsRowError:
        return None


def checkpoint_current_poll(
    poll: WeatherPollResult,
    points: Sequence[tuple[float, float]],
    checkpoints: SourceResponseCheckpoints,
) -> WeatherCheckpointReport:
    """Retain every accepted parser input, then READ EACH BACK before counting it retained.

    Counted per day as well as in total: a point is keyed by the day ITS OWN reading lands on
    (`rows.py::observation_day`, the same call the row builder makes), so one poll across UTC
    midnight retains into two namespaces, and only a per-day count can answer the question the
    forward writer actually asks -- is THIS unwritten day's source still on disk?
    """
    support_sha256 = weather_support_sha256(points)
    accepted = tuple(points)
    retained = failed = 0
    by_day: dict[date, list[int]] = defaultdict(lambda: [0, 0])
    for observed in poll.observations:
        observed_at = observed.observation.get("observedAt")
        day = _named_day(observed_at)
        if observed.response_body is None or day is None or (observed.latitude, observed.longitude) not in accepted:
            # Charged to the day when the reading names one: a point whose body never arrived is
            # exactly the point that makes its day unrecoverable, so hiding it in the total alone
            # would let `retained_whole_day` call that day whole.
            failed += 1
            if day is not None:
                by_day[day][1] += 1
            continue
        identity = weather_checkpoint_identity(
            (observed.latitude, observed.longitude), day=day, support_sha256=support_sha256
        )
        checkpoints.write(
            identity,
            SourceCheckpoint(body=observed.response_body, retrieved_at=poll.fetched_at),
            response_sha256=sha256_digest(observed.response_body),
        )
        stored = checkpoints.read(identity, now=poll.fetched_at)
        if stored is not None and stored.body == observed.response_body:
            retained += 1
            by_day[day][0] += 1
        else:
            failed += 1
            by_day[day][1] += 1
    return WeatherCheckpointReport(
        attempted=len(poll.observations),
        retained=retained,
        failed=failed,
        days={day: WeatherDayRetention(retained=counts[0], failed=counts[1]) for day, counts in by_day.items()},
    )


def recover_weather_day(
    day: date,
    points: Sequence[tuple[float, float]],
    checkpoints: SourceResponseCheckpoints,
    *,
    now: datetime,
) -> WeatherRecoveryReport:
    """Reparse one day from whatever of its support grid was retained, and SAY which of the three it is.

    A PARTIAL CAPTURE IS STILL WORTH MERGING, which is why this no longer refuses one. The writer
    this feeds publishes by MERGING at the `(latitude, longitude, observed_at)` grain
    (`adapter.py::merge_weather_observations_day`) rather than asserting a day's extent, so half a
    grid recovered is half a grid of readings that would otherwise be gone for good. Refusing it
    bought nothing: the alternative to a partial republication is not a whole one, it is none.
    It is also the ordinary shape at a UTC-midnight straddle, where one poll legitimately retains
    part of the grid under each of two days.

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
            if observation_day(str(observation["observedAt"])) != day:
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
    if not recovered:
        state: WeatherRecoveryState = "no_retained_capture"
    elif missing_or_rejected:
        state = "partial_capture"
    else:
        state = "complete_capture"
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
    "WeatherDayRetention",
    "WeatherRecoveryReport",
    "WeatherRecoveryState",
    "checkpoint_current_poll",
    "recover_weather_day",
    "weather_checkpoint_identity",
    "weather_support_sha256",
]
