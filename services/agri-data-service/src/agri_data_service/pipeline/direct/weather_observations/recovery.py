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

THE WITNESS IS EVIDENCE, NEVER A GATE (style review W11, B1). It is written through a call that
reports its own faults and returns (`pipeline/parquet/source_checkpoint.py:118-122`), so a failed
write leaves the PREVIOUS grid's digests standing while this poll's bodies sit under the new one --
the witness is then WRONG, not absent, and no concurrency is needed to get there. So no reading of
it may refuse a search. `recover_weather_day` walks the configured grid in every case, and consults
the witness only to name which empty answer an exhausted walk earned: a claim that the grid moved
needs a witnessed OTHER grid (positive evidence) AND a completed walk of this one that found
nothing (`recover_weather_day`, the state block after the walk).
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
#: boundary poll, which legitimately splits ONE grid across two day namespaces
#: (`forward.py::_newest_day_buckets`), report it for both halves. A partial capture is worth
#: merging; only `no_retained_capture` means there is nothing here to repair with.
#:
#: FOUR since 2026-09-19. `probe_budget_exhausted` is the fourth, and it is the difference between
#: "this grid was searched and nothing was retained" and "the turn ran out of the budget
#: `forward.py::RECOVERY_PROBE_BUDGET_SHARE` allows a probe before the searching finished". Only the
#: first is evidence of loss; reporting the second as `no_retained_capture` would be the manufactured
#: gap `layer-lanes.md` section 4 forbids. It can only arise when a `deadline` is passed --
#: `forward.py::_run_recovery_turn` passes none, because reading the retained grid IS that turn.
#:
#: FIVE since 2026-09-19 (style review W10, S5). `foreign_checkpoint_identity` is the second thing that is
#: not a loss: every checkpoint key is bound to `weather_support_sha256(points)`, and both inputs to
#: that digest -- `INGEST_BBOX` and the sample spacing -- are environment facts read at call time
#: (`support.py::weather_sample_points`), so an operator may move the grid without a deploy. Every
#: retained body then sits under a key this turn will never construct. Saying `no_retained_capture`
#: about that is a false claim of permanent loss made in the exact moment an operator is deciding
#: whether to panic. The discriminator is the support witness below, not a guess.
#:
#: IT IS EARNED, NOT ASSUMED (style review W11, B1). `foreign_checkpoint_identity` is reachable only from
#: an EXHAUSTED walk that recovered nothing AND a witness that names a different identity -- both
#: halves, because the witness set is best-effort and absence is not evidence against an identity.
#: It was once returned before the first read, which let one swallowed witness write refuse a
#: recovery whose bodies were sitting under the searched key all along.
WeatherRecoveryState = Literal[
    "complete_capture",
    "partial_capture",
    "no_retained_capture",
    "probe_budget_exhausted",
    "foreign_checkpoint_identity",
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


def weather_checkpoint_set_sha256(points: Sequence[tuple[float, float]], *, day: date, support_sha256: str) -> str:
    """Fingerprint the complete ordered checkpoint keyspace searched for one day."""
    identities = [
        weather_checkpoint_identity(point, day=day, support_sha256=support_sha256).as_dict() for point in points
    ]
    return sha256_digest(canonical_json(identities))


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
#: without ever growing the object past the checkpoint envelope bound. The ninth grid DESTROYS the
#: first digest, which is why nothing may read a complete negative out of this set
#: (`WeatherSupportWitness.verdict`) and why the reading of it is reported as `witness_truncated`
#: once the bound is reached (style review W11, N3).
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

    THE ONLY EVIDENCE, ALONGSIDE A COMPLETED WALK, THAT TELLS A MISSING BODY FROM A MOVED GRID. A
    walk that finds nothing says where the bodies are NOT; this says where they were once written.
    Neither half is the answer alone, and `recover_weather_day` requires both. A checkpoint key hashes the
    whole identity, support digest included, so nothing can enumerate "every body retained for this
    day under any grid" -- `AvailabilityStorage` (`pipeline/parquet/availability_storage.py:61-75`)
    offers `read`, `put_immutable` and `compare_and_swap` and no listing at all. The witness is
    therefore written forward, by the poll that knows its own grid, rather than discovered backwards.

    It states which grid the day was POLLED under, not that a body survives: retention is per point
    and already counted elsewhere (`WeatherCheckpointReport`).

    EVERY VERDICT HERE IS ONE-SIDED, AND THAT IS THE POINT (style review W11, B1). The set is a
    lower bound on the grids a day was polled under and never an upper one, in three ways that need
    no concurrency between them: a swallowed witness write leaves the previous grid's digests
    standing (`record_support_witness`), eviction at `WEATHER_SUPPORT_WITNESS_LIMIT` drops the
    oldest, and a lost compare-and-swap writes nothing at all
    (`pipeline/parquet/source_checkpoint.py:118`). So a PRESENT digest is evidence -- the day really
    was polled under that identity -- while an ABSENT one is evidence of nothing. `other_identities_witnessed`
    therefore says only "a different identity is on the record", never "this one is not"; what it is
    allowed to decide is fixed at the one place that reads it (`recover_weather_day`), which walks
    the configured grid first in every case and asks this verdict only about an empty result.
    """

    day: date
    #: The digest the recovery searched under, i.e. the grid this process is configured for now.
    searched_sha256: str
    #: The full ordered checkpoint identity set this recovery constructs.
    searched_checkpoint_identity_sha256: str = ""
    #: Modern witnesses as `(support digest, complete identity-set digest)`, oldest first.
    witnessed_identities: tuple[tuple[str, str], ...] = ()
    #: Grid-only entries decoded from the legacy witness schema; diagnostic, never conclusive.
    legacy_witnessed_sha256: tuple[str, ...] = ()

    @property
    def witnessed_sha256(self) -> tuple[str, ...]:
        """List all witnessed grids for diagnostics, including legacy grid-only entries."""
        modern = tuple(support for support, _identity in self.witnessed_identities)
        return tuple(dict.fromkeys((*modern, *self.legacy_witnessed_sha256)))

    @property
    def truncated(self) -> bool:
        """Is the set at its eviction bound, so an older digest may have been dropped from it?

        Reported rather than reasoned about: nothing here refuses on a witness, so truncation can
        only widen an already one-sided reading. It travels on `to_event` so an operator reading a
        `foreign_checkpoint_identity` refusal knows the named identities may not be all of them.
        """
        witnessed = len(self.witnessed_identities) + len(self.legacy_witnessed_sha256)
        return witnessed >= WEATHER_SUPPORT_WITNESS_LIMIT

    @property
    def verdict(self) -> Literal["identity_matches", "other_identities_witnessed", "no_witness"]:
        """Which keyspace is evidenced; legacy grid-only entries remain unknown.

        Named for what is on the record, not for what is inferred from it: the previous name for the
        third case was `grid_changed`, which asserted a move from the mere absence of the searched
        digest and cost a recovery its walk (style review W11, B1).
        """
        if self.searched_checkpoint_identity_sha256 in {
            identity_sha256 for _support_sha256, identity_sha256 in self.witnessed_identities
        }:
            return "identity_matches"
        if self.witnessed_identities:
            return "other_identities_witnessed"
        return "no_witness"

    def to_event(self) -> dict[str, object]:
        """Render the grid question for a progress stream: what was searched, and what was witnessed."""
        return {
            "checkpoint_identity_verdict": self.verdict,
            "searched_support_sha256": self.searched_sha256,
            "witnessed_support_sha256": list(self.witnessed_sha256),
            "searched_checkpoint_identity_sha256": self.searched_checkpoint_identity_sha256,
            "witnessed_checkpoint_identity_sha256": [
                identity_sha256 for _support_sha256, identity_sha256 in self.witnessed_identities
            ],
            "witness_truncated": self.truncated,
        }


def _witnessed_identities(
    checkpoint: SourceCheckpoint | None,
) -> tuple[tuple[tuple[str, str], ...], tuple[str, ...]]:
    """Decode modern identity witnesses and retain legacy grid-only entries as diagnostics."""
    if checkpoint is None:
        return (), ()
    try:
        decoded = json.loads(checkpoint.body)
    except ValueError:
        return (), ()
    if not isinstance(decoded, dict):
        return (), ()
    entries = decoded.get("checkpoint_identities")
    modern: list[tuple[str, str]] = []
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            support_sha256 = entry.get("support_sha256")
            identity_sha256 = entry.get("checkpoint_identity_sha256")
            if isinstance(support_sha256, str) and isinstance(identity_sha256, str):
                modern.append((support_sha256, identity_sha256))
    legacy = decoded.get("legacy_support_sha256", decoded.get("support_sha256", []))
    modern_entries = tuple(modern[-WEATHER_SUPPORT_WITNESS_LIMIT:])
    legacy_entries = (
        tuple(entry for entry in legacy if isinstance(entry, str))[-WEATHER_SUPPORT_WITNESS_LIMIT:]
        if isinstance(legacy, list)
        else ()
    )
    legacy_slots = WEATHER_SUPPORT_WITNESS_LIMIT - len(modern_entries)
    bounded_legacy = legacy_entries[-legacy_slots:] if legacy_slots > 0 else ()
    return modern_entries, bounded_legacy


def record_support_witness(
    day: date,
    support_sha256: str,
    checkpoint_identity_sha256: str,
    checkpoints: SourceResponseCheckpoints,
    *,
    now: datetime,
) -> tuple[tuple[str, str], ...]:
    """Remember the exact checkpoint keyspace used for this day.

    Rewritten on every poll rather than only when the set changes, so the witness ages out of the
    `CHECKPOINT_MAX_AGE` window at the same rate as the bodies it describes -- a witness that expired
    first would turn a knowable answer back into `no_witness`.

    Cannot raise and cannot cost a poll: `SourceResponseCheckpoints.write`
    (`pipeline/parquet/source_checkpoint.py:80-122`) reports its own faults and returns.

    WHAT A FAILED WRITE ACTUALLY COSTS (style review W11, B1). This said the cost was "a future
    recovery its discriminator, never this turn its retention", and the first half was false: the
    write leaves whatever was already there, so after a grid change the witness still names the
    PREVIOUS grid while the bodies land under the new one. The failure produces a WRONG
    discriminator, not an absent one -- a lost compare-and-swap (`source_checkpoint.py:118`) writes
    nothing and says so only to the log. Retention is untouched either way, which is the half that
    was true. The wrongness is made harmless where it is read rather than here: `recover_weather_day`
    walks the configured grid before consulting any verdict, so the worst this costs is the naming
    of an empty answer, never the search that could have filled it.

    One shape this inherits rather than chooses: a prior witness carrying an equal or NEWER instant
    is left alone (`pipeline/parquet/source_checkpoint.py:105-106`), so a grid change is recorded by
    the first poll whose `fetched_at` is strictly later than the last one's -- which is every real
    poll, since `fetched_at` is the turn's own clock, but not two calls sharing an instant.
    """
    identity = weather_support_witness_identity(day)
    held, legacy = _witnessed_identities(checkpoints.read(identity, now=now))
    entry = (support_sha256, checkpoint_identity_sha256)
    candidates = held if entry in held else (*held, entry)
    merged = candidates[-WEATHER_SUPPORT_WITNESS_LIMIT:]
    legacy_slots = WEATHER_SUPPORT_WITNESS_LIMIT - len(merged)
    bounded_legacy = legacy[-legacy_slots:] if legacy_slots > 0 else ()
    body = canonical_json(
        {
            "checkpoint_identities": [
                {
                    "support_sha256": witnessed_support,
                    "checkpoint_identity_sha256": witnessed_identity,
                }
                for witnessed_support, witnessed_identity in merged
            ],
            "legacy_support_sha256": list(bounded_legacy),
        }
    ).encode()
    checkpoints.write(identity, SourceCheckpoint(body=body, retrieved_at=now), response_sha256=sha256_digest(body))
    return merged


def read_support_witness(
    day: date,
    support_sha256: str,
    checkpoint_identity_sha256: str,
    checkpoints: SourceResponseCheckpoints,
    *,
    now: datetime,
) -> WeatherSupportWitness:
    """Ask, in one object read, which complete checkpoint keyspaces were witnessed."""
    held, legacy = _witnessed_identities(checkpoints.read(weather_support_witness_identity(day), now=now))
    return WeatherSupportWitness(
        day=day,
        searched_sha256=support_sha256,
        searched_checkpoint_identity_sha256=checkpoint_identity_sha256,
        witnessed_identities=held,
        legacy_witnessed_sha256=legacy,
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
        record_support_witness(
            day,
            support_sha256,
            weather_checkpoint_set_sha256(accepted, day=day, support_sha256=support_sha256),
            checkpoints,
            now=poll.fetched_at,
        )
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
    """Reparse one day from whatever of its support grid was retained, and SAY which of the five it is.

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

    THE GRID QUESTION IS ANSWERED AFTER THE WALK, NOT INSTEAD OF IT (style review W11, B1). The
    witness is read first, in one object read, but nothing is refused on it: EVERY day is walked,
    and the verdict only names which empty answer an exhausted walk earned. `foreign_checkpoint_identity`
    needs both halves -- a walk of this grid that recovered nothing, and a witness naming a
    different one -- because the witness set is a lower bound on the grids a day was polled under
    (`WeatherSupportWitness`), so the absence of the searched digest from it is not evidence that
    the day was never written here. A walk is the only thing that can produce that evidence.

    Between 2026-09-19 and this change the verdict was a gate: one swallowed witness write
    (`record_support_witness`) left the previous grid's digests standing, and a recovery of bodies
    lying under the searched key refused with zero probes and told the operator to restore a bbox
    that would move the configured grid away from them.

    WHAT THE WALK COSTS WHEN THE GRID REALLY DID MOVE: one checkpoint read per support point,
    against keys that cannot exist, once per refused day. The operator turn
    (`forward.py::_run_recovery_turn`) passes no deadline and spends that walk exactly once for the
    day it was asked about; the in-poll caller passes one, so the same walk is bounded by
    `forward.py::RECOVERY_PROBE_BUDGET_SHARE` and reports `probe_budget_exhausted` rather than any
    grid claim if it runs out. That is the price of never refusing a readable day, and it is paid
    only in the case that used to be refused for free and sometimes wrongly.
    """
    support_sha256 = weather_support_sha256(points)
    checkpoint_identity_sha256 = weather_checkpoint_set_sha256(points, day=day, support_sha256=support_sha256)
    witness = read_support_witness(day, support_sha256, checkpoint_identity_sha256, checkpoints, now=now)
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
    state: WeatherRecoveryState
    if not recovered:
        # Three empty answers, ordered by how much of the search actually happened. An unfinished
        # search is not an empty bucket: `no_retained_capture` means the checkpoint identity this
        # build constructs was walked to the end. A completed empty walk plus a witnessed OTHER
        # identity is the only combination that may claim a move -- the walk supplies the negative
        # about this keyspace, the witness the positive about another.
        if unprobed:
            state = "probe_budget_exhausted"
        elif witness.verdict == "other_identities_witnessed":
            state = "foreign_checkpoint_identity"
        else:
            state = "no_retained_capture"
    elif missing_or_rejected or unprobed:
        # Recovering anything at all under a grid the witness does not name is the witness being
        # incomplete, not the grid being foreign; the bodies decide, so the capture states stand.
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
    "weather_checkpoint_set_sha256",
    "weather_support_sha256",
    "weather_support_witness_identity",
]
