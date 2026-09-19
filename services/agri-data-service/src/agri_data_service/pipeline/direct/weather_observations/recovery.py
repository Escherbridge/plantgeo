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

A THIRD OBJECT, THE SUPPORT WITNESS. Every checkpoint key is bound to the digest of the COMPLETE
sample grid, and that grid is derived from environment facts read at call time, so moving
`INGEST_BBOX` or the sample spacing silently relocates every retained body. `record_support_witness`
therefore writes one grid-independent object per day beside the bodies, and `read_support_witness`
is what lets a recovery say "searched the wrong key space" instead of "the bucket is lost"
(style review W10, S5/S6).
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
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
    from datetime import date, datetime

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
#:
#: FOUR since 2026-09-19. `probe_budget_exhausted` is the fourth, and it is the difference between
#: "this grid was searched and nothing was retained" and "the turn ran out of the budget
#: `forward.py::RECOVERY_PROBE_BUDGET_SHARE` allows a probe before the searching finished". Only the
#: first is evidence of loss; reporting the second as `no_retained_capture` would be the manufactured
#: gap `layer-lanes.md` section 4 forbids. It can only arise when a `deadline` is passed --
#: `forward.py::_run_recovery_turn` passes none, because reading the retained grid IS that turn.
#:
#: FIVE since 2026-09-19 (style review W10, S5). `foreign_support_grid` is the second thing that is
#: not a loss: every checkpoint key is bound to `weather_support_sha256(points)`, and both inputs to
#: that digest -- `INGEST_BBOX` and the sample spacing -- are environment facts read at call time
#: (`support.py::weather_sample_points`), so an operator may move the grid without a deploy. Every
#: retained body then sits under a key this turn will never construct. Saying `no_retained_capture`
#: about that is a false claim of permanent loss made in the exact moment an operator is deciding
#: whether to panic. The discriminator is the support witness below, not a guess.
WeatherRecoveryState = Literal[
    "complete_capture",
    "partial_capture",
    "no_retained_capture",
    "probe_budget_exhausted",
    "foreign_support_grid",
]


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
    #: Support points the probe budget never reached. Kept apart from `missing_or_rejected_points`
    #: because that count is evidence about the bucket and this one is evidence about the turn.
    unprobed_points: int = 0
    #: WHICH IDENTITY WAS SEARCHED, on every report. Style review W10, S5: the report carried
    #: `support_points` -- a count -- and nothing that names the key space the count was taken in, so
    #: a grid change and a genuine loss produced byte-identical reports.
    support_sha256: str = ""
    #: The grid question's answer, read once per recovered day. `None` only where it was not asked.
    witness: WeatherSupportWitness | None = None

    def to_event(self) -> dict[str, object]:
        """Render the verdict for the forward writer's progress stream; the observations stay out of it."""
        event: dict[str, object] = {
            "day": self.day.isoformat(),
            "state": self.state,
            "support_points": self.support_points,
            "support_sha256": self.support_sha256,
            "recovered_points": self.recovered_points,
            "missing_or_rejected_points": self.missing_or_rejected_points,
            "unprobed_points": self.unprobed_points,
        }
        if self.witness is not None:
            event.update(self.witness.to_event())
        return event


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


#: Identity namespace for the per-day record of WHICH support grids this lane has polled a day under.
#: Its own provider string, so a witness can never be mistaken for a retained provider response by
#: `recover_weather_day` -- the two live in the same key space and are told apart by this field.
WEATHER_SUPPORT_WITNESS_PROVIDER: Final = "open-meteo-current-conditions-support-witness-v1"

#: The witness identity must be findable WITHOUT knowing the support digest -- that is the entire
#: point of it -- so the grid slot of its identity carries this constant instead. Not a grid digest
#: and not mistakable for one: `weather_support_sha256` only ever returns 64 hex characters.
WEATHER_SUPPORT_WITNESS_GRID: Final = "any-support-grid"

#: A stable, non-network identity for the witness request slot; nothing fetches it.
WEATHER_SUPPORT_WITNESS_REQUEST: Final = "urn:plantgeo:weather-observations:support-witness"

#: How many distinct grids one day's witness remembers. A day is polled under one grid in the
#: ordinary case and two across a change; eight bounds a pathological flapping of the environment
#: without ever growing the object past the checkpoint envelope bound.
WEATHER_SUPPORT_WITNESS_LIMIT: Final = 8


def weather_support_witness_identity(day: date) -> SourceCheckpointIdentity:
    """Name the grid-independent witness object for one day; the only identity here without a grid."""
    return SourceCheckpointIdentity(
        WEATHER_SUPPORT_WITNESS_PROVIDER,
        WEATHER_SUPPORT_WITNESS_GRID,
        day.isoformat(),
        WEATHER_SUPPORT_WITNESS_REQUEST,
    )


@dataclass(frozen=True, slots=True)
class WeatherSupportWitness:
    """Which support grids a day was polled under, against the one a recovery searched.

    THE ONE THING THAT CAN TELL A MISSING BODY APART FROM A MOVED GRID. A checkpoint key hashes the
    whole identity, support digest included, so nothing can enumerate "every body retained for this
    day under any grid" -- `AvailabilityStorage` (`pipeline/parquet/availability_storage.py:61-75`)
    offers `read`, `put_immutable` and `compare_and_swap` and no listing at all. The witness is
    therefore written forward, by the poll that knows its own grid, rather than discovered backwards.

    It states which grid the day was POLLED under, not that a body survives: retention is per point
    and already counted elsewhere (`WeatherCheckpointReport`). So `grid_changed` never means "your
    data is safe" -- it means "this turn searched a key space the day was never written into, and
    the search proves nothing about loss".
    """

    day: date
    #: The digest the recovery searched under, i.e. the grid this process is configured for now.
    searched_sha256: str
    #: Grids this day is known to have been polled under, oldest first; empty when none is readable.
    witnessed_sha256: tuple[str, ...] = ()
    #: Was a witness object readable at all? `False` makes every verdict below an honest "unknown".
    recorded: bool = False

    @property
    def verdict(self) -> Literal["grid_matches", "grid_changed", "no_witness"]:
        """Which of the three this is; `no_witness` is a stated absence of evidence, not evidence."""
        if not self.recorded or not self.witnessed_sha256:
            return "no_witness"
        return "grid_matches" if self.searched_sha256 in self.witnessed_sha256 else "grid_changed"

    def to_event(self) -> dict[str, object]:
        """Render the grid question for a progress stream: what was searched, and what was witnessed."""
        return {
            "support_grid_verdict": self.verdict,
            "searched_support_sha256": self.searched_sha256,
            "witnessed_support_sha256": list(self.witnessed_sha256),
        }


def _witnessed_grids(checkpoint: SourceCheckpoint | None) -> tuple[str, ...]:
    """Decode a witness body into its digests, treating anything unreadable as no witness at all."""
    if checkpoint is None:
        return ()
    try:
        decoded = json.loads(checkpoint.body)
    except ValueError:
        return ()
    if not isinstance(decoded, dict):
        return ()
    held = decoded.get("support_sha256")
    if not isinstance(held, list):
        return ()
    return tuple(entry for entry in held if isinstance(entry, str))


def record_support_witness(
    day: date, support_sha256: str, checkpoints: SourceResponseCheckpoints, *, now: datetime
) -> tuple[str, ...]:
    """Remember that this day was polled under this grid, unioned with whatever is already witnessed.

    Rewritten on every poll rather than only when the set changes, so the witness ages out of the
    `CHECKPOINT_MAX_AGE` window at the same rate as the bodies it describes -- a witness that expired
    first would turn a knowable answer back into `no_witness`.

    Cannot raise and cannot cost a poll: `SourceResponseCheckpoints.write`
    (`pipeline/parquet/source_checkpoint.py:80-122`) reports its own faults and returns. A witness
    that fails to write costs a future recovery its discriminator, never this turn its retention.

    One shape this inherits rather than chooses: a prior witness carrying an equal or NEWER instant
    is left alone (`pipeline/parquet/source_checkpoint.py:105-106`), so a grid change is recorded by
    the first poll whose `fetched_at` is strictly later than the last one's -- which is every real
    poll, since `fetched_at` is the turn's own clock, but not two calls sharing an instant.
    """
    identity = weather_support_witness_identity(day)
    held = _witnessed_grids(checkpoints.read(identity, now=now))
    merged = held if support_sha256 in held else (*held, support_sha256)[-WEATHER_SUPPORT_WITNESS_LIMIT:]
    body = canonical_json({"support_sha256": list(merged)}).encode()
    checkpoints.write(identity, SourceCheckpoint(body=body, retrieved_at=now), response_sha256=sha256_digest(body))
    return merged


def read_support_witness(
    day: date, support_sha256: str, checkpoints: SourceResponseCheckpoints, *, now: datetime
) -> WeatherSupportWitness:
    """Ask, in ONE object read, whether this day was ever polled under the grid about to be searched."""
    held = _witnessed_grids(checkpoints.read(weather_support_witness_identity(day), now=now))
    return WeatherSupportWitness(
        day=day,
        searched_sha256=support_sha256,
        witnessed_sha256=held,
        recorded=bool(held),
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

    ALSO WITNESSES THE GRID for every day it named, because this is the only place that knows both
    the day and the support digest at the moment of writing. A later recovery reads the witness to
    tell "nothing was retained" apart from "the grid moved" (`read_support_witness`); without a
    forward record nothing can, since checkpoint keys hash the grid and the store offers no listing.
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
    for day in by_day:
        record_support_witness(day, support_sha256, checkpoints, now=poll.fetched_at)
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
    deadline: float | None = None,
) -> WeatherRecoveryReport:
    """Reparse one day from whatever of its support grid was retained, and SAY which of the four it is.

    A PARTIAL CAPTURE IS STILL WORTH MERGING, which is why this no longer refuses one. The writer
    this feeds publishes by MERGING at the `(latitude, longitude, observed_at)` grain
    (`adapter.py::merge_weather_observations_day`) rather than asserting a day's extent, so half a
    grid recovered is half a grid of readings that would otherwise be gone for good. Refusing it
    bought nothing: the alternative to a partial republication is not a whole one, it is none.
    It is also the ordinary shape at a UTC-midnight straddle, where one poll legitimately retains
    part of the grid under each of two days.

    This returns observations; it does not publish them. Republication stays with the forward
    writer's own lane-day lock and finalizer, so a recovery can never take a shortcut past them.

    `deadline` is an optional `time.monotonic()` stamp, checked BEFORE each point's read, that
    stops the probe from spending a whole turn on one GET-per-point walk of the grid; the points it
    never reached are reported as `unprobed_points` and, when nothing was recovered, as
    `probe_budget_exhausted` rather than as an absence. A caller that passes `None` -- the operator's
    `--recover-day` turn -- walks the whole grid, because reading it is the entire turn.

    BOUNDED AGAINST THE GRID, NOT ONLY AGAINST THE CLOCK (style review W10, S6). The witness is read
    FIRST, in one object read, and a day witnessed only under other grids returns
    `foreign_support_grid` without walking a single point: the walk would issue one GET per support
    point against keys that cannot exist, and then report the emptiness it manufactured as a loss.
    A day with no witness is walked, because absence of the witness is not evidence of a move.
    """
    support_sha256 = weather_support_sha256(points)
    witness = read_support_witness(day, support_sha256, checkpoints, now=now)
    if witness.verdict == "grid_changed":
        return WeatherRecoveryReport(
            day=day,
            state="foreign_support_grid",
            support_points=len(points),
            recovered_points=0,
            missing_or_rejected_points=0,
            observations=(),
            support_sha256=support_sha256,
            witness=witness,
        )
    recovered: list[WeatherPointObservation] = []
    missing_or_rejected = 0
    unprobed = 0
    for point in points:
        if deadline is not None and time.monotonic() >= deadline:
            unprobed += 1
            continue
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
        # An unfinished search is not an empty bucket: `no_retained_capture` is the word the forward
        # writer prints as "lost, not owed", and it is only true of a grid that was actually walked.
        state: WeatherRecoveryState = "probe_budget_exhausted" if unprobed else "no_retained_capture"
    elif missing_or_rejected or unprobed:
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
        unprobed_points=unprobed,
        support_sha256=support_sha256,
        witness=witness,
    )


__all__ = [
    "WEATHER_CURRENT_CHECKPOINT_PROVIDER",
    "WEATHER_SUPPORT_WITNESS_PROVIDER",
    "WeatherCheckpointReport",
    "WeatherDayRetention",
    "WeatherRecoveryReport",
    "WeatherRecoveryState",
    "WeatherSupportWitness",
    "checkpoint_current_poll",
    "read_support_witness",
    "record_support_witness",
    "recover_weather_day",
    "weather_checkpoint_identity",
    "weather_support_sha256",
    "weather_support_witness_identity",
]
