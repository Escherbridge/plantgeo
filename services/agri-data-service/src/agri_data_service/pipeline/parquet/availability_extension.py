"""Extend one lane's availability generation with the day the writer just made terminal.

Layer L2: may import `foundation`, `warehouse` and `db`; may NOT import method, planes, or interface.
See `AGENTS.md` in this directory, "`availability_extension.py` -- the terminal day joins the index".

THE RETRY CLAIM IS WRITTEN BEFORE THE HEAD IS READ, and that ordering is the whole recovery story.
`build_gap_census` never revisits a base-complete day, so ANY exit that leaves a terminal day out of
the index and out of the retry ledger loses it permanently -- silently, on a green tick. The claim
carries the day's own PHYSICAL receipts, which are ledger facts rather than head facts, so it can be
written before anything is known about the pointer and a later turn can rebuild the exact same
evidence from it without re-exporting a single row.
"""

from __future__ import annotations

import json
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final, Literal

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.foundation.parquet.zoom import ZoomTierError, validate_zoom_tier
from agri_data_service.pipeline.parquet.availability_index import (
    AvailabilityConfig,
    AvailabilityError,
    AvailabilityIdentity,
    AvailabilityIndex,
    AvailabilityRow,
    AvailabilityStorage,
    AvailabilityUnavailableError,
    EvidenceReceipt,
    PublicationRequest,
    PublicationResult,
    SourceEvidence,
    TerminalEvidence,
    TypedEvidenceArtifact,
    availability_row_from_terminal_evidence,
    build_source_evidence,
    build_terminal_evidence,
    publish_availability,
    read_latest_availability,
)
from agri_data_service.pipeline.parquet.objectstore import (
    MAX_AVAILABILITY_RETRY_BYTES,
    ObjectStore,
    WrittenObjectLedger,
    availability_lane_root,
)
from agri_data_service.pipeline.parquet.publication_barrier import postgres_lane_publication_barrier
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.pipeline.parquet.availability_index import (
        AvailabilityPublicationBarrier,
        TerminalState,
    )

JSON_CONTENT_TYPE: Final = "application/json"
LANE_EXPORT_SOURCE_SCHEMA_VERSION: Final = "availability-lane-export-source-v1"

#: v2 carries the day's PHYSICAL receipts rather than the typed-evidence keys v1 named. v1 could only
#: be written after the head read had already succeeded, because a typed evidence key is derived from
#: the lane identity the pointer carries -- which is exactly the failure this claim has to survive.
AVAILABILITY_RETRY_SCHEMA_VERSION: Final = "availability-retry-v2"

# The default origin of a `gap_fill` day: a day-scoped query over this warehouse's own tables. A
# direct writer that fetched an upstream API passes its own, because the two claim different things.
POSTGRES_DAY_EXPORT_ORIGIN: Final = "postgres-day-export"

#: The origin a LADDER REPAIR claims. It is not an export: no source was contacted and no base row
#: was written, so a claim wearing `POSTGRES_DAY_EXPORT_ORIGIN` would assert a query that never ran.
#: `_prepare_day` also keys the source-evidence REUSE off this value -- see `AGENTS.md`.
LADDER_REPAIR_ORIGIN: Final = "parquet-ladder-repair"

# How many owed days one lane may retry per tick. The retry re-verifies every physical part of a
# day, so an unbounded drain would spend a whole tick on a backlog that is by construction rare.
DEFAULT_MAX_RETRIES_PER_LANE: Final = 8

AvailabilityExtensionState = Literal[
    "extended",
    "skipped_unchanged",
    "not_bootstrapped",
    "ladder_incomplete",
    "retry_owed",
    "retry_claim_failed",
    "quarantined",
]

# How many parked claims one lane's sweep NAMES in its reason. Every one it finds is counted; the
# names are sampled, because a lane with a hundred parked days would otherwise render a hundred dates
# into a detail string that an operator reads as one line.
QUARANTINED_SAMPLE_SIZE: Final = 5

#: The gap kind for a rung that DERIVED TO NO ROWS and was therefore retracted. Named apart from a
#: generically broken ladder because it is not a fault: the base rung genuinely holds rows and every
#: one of them fell below this rung's floor. See `AGENTS.md`, "why an emptied rung strands its day".
DERIVED_TO_ZERO_ROWS: Final = "derived_to_zero_rows"


@dataclass(frozen=True, slots=True)
class LaneDaySource:
    """What the exporting process itself held for one lane-day at export time."""

    origin: str
    run_id: str
    row_count: int
    part_count: int
    exported_at: datetime
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class FinalizedLaneDay:
    """One terminal lane-day: what it physically wrote, and the source facts behind it."""

    terminal_state: TerminalState
    day: date
    written: WrittenObjectLedger
    source: LaneDaySource
    published_at: datetime
    #: The newest day this lane's SOURCE could have published, from `lane_ceiling.allowed_source_ceiling`.
    #: REQUIRED, with no default: a ceiling defaulted to the day itself makes the publisher ratchet the
    #: pointer to its newest published day, and coverage then closes every lane exactly at its own last
    #: row -- so no lane can ever report a gap tail, however far behind its source it has fallen.
    source_ceiling: date
    absence_reason: str | None = None

    def __post_init__(self) -> None:
        if self.day > self.source_ceiling:
            raise ValueError("a finalized lane-day cannot exceed the source ceiling it declares")


@dataclass(frozen=True, slots=True)
class AvailabilityExtensionOutcome:
    """What the availability step did with one terminal lane-day, and what it owes next."""

    state: AvailabilityExtensionState
    lane_root: str
    #: `None` only for a LANE-WIDE outcome that names no day, such as an unreadable retry ledger.
    day: date | None
    reason: str
    error_kind: str | None = None
    generation_key: str | None = None
    attempts: int = 0
    retry_marker: str | None = None
    #: How many lane-days this one outcome speaks for. Always 1 except for the QUARANTINE SWEEP,
    #: which is a lane-wide statement about a set of parked claims: one outcome per parked day would
    #: put a hundred sentences into a detail string to carry a number a tally already holds.
    counted_days: int = 1

    @property
    def note(self) -> str:
        """Render the one line a driver folds into its lane-day detail."""
        subject = self.lane_root if self.day is None else self.day.isoformat()
        return f"{subject}: availability {self.state}: {self.reason}"


@dataclass(slots=True)
class AvailabilityExtensionTally:
    """Every availability verdict one driver saw, counted so a summary can state them as numbers.

    `ladder_incomplete`, `retry_claim_failed`, `quarantined_standing` and `reindex_owed` are the four
    that MUST be visible: each leaves a terminal day out of the index, and a driver that reported
    them only inside a per-day detail string would present that loss as a green tick. See
    `AGENTS.md` in this directory, "The availability tally: four counters and two gauges".
    """

    extended: int = 0
    skipped_unchanged: int = 0
    not_bootstrapped: int = 0
    ladder_incomplete: int = 0
    retry_owed: int = 0
    retry_claim_failed: int = 0
    #: A GAUGE, NOT A COUNTER: how many claims are parked RIGHT NOW, restated whole by each lane's
    #: sweep rather than incremented per tick. Nothing retries them, so the same parked day is
    #: present again next tick, and a counter summed across ticks would report a rising loss where
    #: the truth is one standing one.
    quarantined_standing: int = 0
    #: A GAUGE: published days whose ladder this tick's census could not reach, so no repair will
    #: bring them back into the index until `drain --selection ladder` walks the whole bucket.
    reindex_owed: int = 0

    def record(self, outcome: AvailabilityExtensionOutcome) -> None:
        """Fold one outcome into the tally by its own state, and by how many days it speaks for."""
        name = _STATE_FIELDS[outcome.state]
        setattr(self, name, getattr(self, name) + outcome.counted_days)

    def add(self, other: AvailabilityExtensionTally) -> None:
        """Fold another tally into this one, field by field."""
        for field_name in _TALLY_FIELDS:
            setattr(self, field_name, getattr(self, field_name) + getattr(other, field_name))

    def to_summary(self) -> dict[str, int]:
        """Render the operator-facing counts, in the order a reader scans them."""
        return {f"availability_{name}": getattr(self, name) for name in _TALLY_FIELDS}


#: Which field each verdict lands in. Spelled out rather than derived from the state name, because
#: `quarantined` is a standing gauge and its field says so.
_STATE_FIELDS: Final[Mapping[AvailabilityExtensionState, str]] = {
    "extended": "extended",
    "skipped_unchanged": "skipped_unchanged",
    "not_bootstrapped": "not_bootstrapped",
    "ladder_incomplete": "ladder_incomplete",
    "retry_owed": "retry_owed",
    "retry_claim_failed": "retry_claim_failed",
    "quarantined": "quarantined_standing",
}

_TALLY_FIELDS: Final[tuple[str, ...]] = (
    "extended",
    "skipped_unchanged",
    "not_bootstrapped",
    "ladder_incomplete",
    "retry_owed",
    "retry_claim_failed",
    "quarantined_standing",
    "reindex_owed",
)


@dataclass(frozen=True, slots=True)
class _RungObjects:
    """Every physical object one rung of one terminal day wrote, as the run's own ledger recorded it."""

    rung: int
    row_count: int
    data_receipts: tuple[EvidenceReceipt, ...]
    completion_receipt: EvidenceReceipt | None
    absence_receipt: EvidenceReceipt | None


@dataclass(frozen=True, slots=True)
class _DayClaim:
    """One terminal lane-day stated WITHOUT its head: everything a later turn needs to index it.

    Every field is either a ledger fact or a lane constant, so this can be rendered and persisted
    before the pointer has been read -- and rebuilt from the persisted bytes afterwards.
    """

    lane_root: str
    day: date
    terminal_state: TerminalState
    absence_reason: str | None
    source: LaneDaySource
    source_ceiling: date
    published_at: datetime
    rungs: tuple[_RungObjects, ...]


@dataclass(frozen=True, slots=True)
class _PreparedDay:
    """One day's availability rows and every object that must exist before they may be published."""

    rows: tuple[AvailabilityRow, ...]
    #: `None` for a LADDER REPAIR of a day the generation already holds: its source object is already
    #: published and the rows cite it unchanged, so there is nothing new to write.
    source_object_key: str | None
    source_object_payload: bytes | None
    artifacts: tuple[TypedEvidenceArtifact, ...]
    #: The typed source-evidence digest every row of the day cites. Used as the publication's
    #: `input_sha256`, so that field binds the request to an object outside it rather than re-hashing
    #: the very rows it accompanies, which proved nothing.
    source_evidence_sha256: str


@dataclass(frozen=True, slots=True)
class RepairedBaseRung:
    """The base rung of a REPAIRED day, as the repair's own read-back proved it: keys plus digests.

    A repair writes nothing at the base rung, so its ledger holds nothing for it -- and a claim that
    named only the rungs it rewrote could never present the exact required-rungs ladder. These are
    the receipts of the very bytes the derivation read, so citing them costs no extra download.
    """

    rung: int
    data_receipts: tuple[EvidenceReceipt, ...]
    completion_receipt: EvidenceReceipt
    row_count: int
    part_count: int


@dataclass(frozen=True, slots=True)
class _LadderGap:
    """Why one terminal day cannot form the exact required-rungs ladder the contract demands."""

    reason: str
    kind: str = "ladder_incomplete"


async def extend_availability_for_lane_day(  # noqa: PLR0911, PLR0913 - one typed outcome per exit
    session: AsyncSession,
    store: ObjectStore,
    *,
    lane: str,
    kind: PartitionKind,
    day: date,
    outcome: FinalizedLaneDay,
    availability: AvailabilityStorage | None,
    now: Callable[[], datetime],
    publication_barrier: AvailabilityPublicationBarrier = postgres_lane_publication_barrier,
) -> AvailabilityExtensionOutcome:
    """Extend one lane's availability generation with a day that is ALREADY terminal, never before it."""
    lane_root = availability_lane_root(lane, kind)
    if availability is None:
        return AvailabilityExtensionOutcome(
            state="not_bootstrapped",
            lane_root=lane_root,
            day=day,
            reason="no availability storage is wired into this run, so the day is terminal but unindexed",
            error_kind="availability_not_wired",
        )
    claim = _claim_from_finalized(outcome, lane=lane, kind=kind, lane_root=lane_root, day=day)
    if isinstance(claim, _LadderGap):
        # Refused from the LEDGER ALONE, before any object is written: a retry would rebuild the
        # identical gap out of the identical receipts, so claiming one would only spin.
        return AvailabilityExtensionOutcome(
            state="ladder_incomplete",
            lane_root=lane_root,
            day=day,
            reason=claim.reason,
            error_kind=claim.kind,
        )
    claimed = _write_claim(store, claim, lane=lane, kind=kind, error_kind="publication_pending", recorded_at=now())
    if isinstance(claimed, AvailabilityExtensionOutcome):
        return claimed
    try:
        index = read_latest_availability(availability, lane_root=lane_root)
    except AvailabilityUnavailableError as unavailable:
        if unavailable.code == "availability_missing":
            # NOTHING is owed: the offline bootstrap builds generation zero from the objects this day
            # is already among, so a claim here would be satisfied by the bootstrap and never read.
            _clear_claim(store, lane=lane, kind=kind, day=day)
            return AvailabilityExtensionOutcome(
                state="not_bootstrapped",
                lane_root=lane_root,
                day=day,
                reason=f"{lane_root} has no availability generation yet, so this terminal day joins none",
                error_kind="availability_not_bootstrapped",
            )
        return _read_failure(lane_root, day, unavailable, retry_marker=claimed)
    except AvailabilityError as error:
        return _read_failure(lane_root, day, error, retry_marker=claimed)
    return await _index_claimed_day(
        session,
        store,
        availability,
        index=index,
        claim=claim,
        lane=lane,
        kind=kind,
        retry_marker=claimed,
        now=now,
        publication_barrier=publication_barrier,
    )


def claim_repaired_lane_day(  # noqa: PLR0913 - one coordinate of the repaired day per arg
    store: ObjectStore,
    *,
    lane: str,
    kind: PartitionKind,
    day: date,
    written: WrittenObjectLedger,
    base_rung: RepairedBaseRung,
    run_id: str,
    source_ceiling: date,
    published_at: datetime,
) -> AvailabilityExtensionOutcome:
    """Record that a REPAIRED day owes its availability step, so the next drain indexes it.

    A repair rewrites three of the day's four rungs, which changes their receipts -- and until this
    existed nothing told the index. The day stayed complete at every rung, was never re-selected, and
    was reported `repaired: 1` while remaining outside the generation for good. The claim carries the
    derived rungs from THIS run's ledger and the untouched base rung from the read the derivation
    already paid for, so no object is downloaded twice. See `AGENTS.md`, "A repaired day joins the
    index through a claim".
    """
    lane_root = availability_lane_root(lane, kind)
    claim = _claim_from_repair(
        lane=lane,
        kind=kind,
        lane_root=lane_root,
        day=day,
        written=written,
        base_rung=base_rung,
        run_id=run_id,
        source_ceiling=source_ceiling,
        published_at=published_at,
    )
    if isinstance(claim, _LadderGap):
        return AvailabilityExtensionOutcome(
            state="ladder_incomplete",
            lane_root=lane_root,
            day=day,
            reason=claim.reason,
            error_kind=claim.kind,
        )
    claimed = _write_claim(store, claim, lane=lane, kind=kind, error_kind="repair_pending", recorded_at=published_at)
    if isinstance(claimed, AvailabilityExtensionOutcome):
        return claimed
    return AvailabilityExtensionOutcome(
        state="retry_owed",
        lane_root=lane_root,
        day=day,
        reason=(
            "the coarse rungs were re-derived and their receipts changed, so this day owes a re-index; its "
            "claim names every physical receipt the next drain publishes from"
        ),
        error_kind="repair_pending",
        retry_marker=claimed,
    )


async def retry_pending_availability(  # noqa: PLR0913 - one lane coordinate or seam per arg
    session: AsyncSession,
    store: ObjectStore,
    *,
    lane: str,
    kind: PartitionKind,
    availability: AvailabilityStorage | None,
    now: Callable[[], datetime],
    max_days: int = DEFAULT_MAX_RETRIES_PER_LANE,
    publication_barrier: AvailabilityPublicationBarrier = postgres_lane_publication_barrier,
) -> tuple[AvailabilityExtensionOutcome, ...]:
    """Retry the AVAILABILITY STEP ALONE for every day one lane owes, never re-exporting the data.

    THE QUARANTINE SWEEP RIDES ON THIS PASS'S OWN LISTING. A parked claim
    (`day=<day>.quarantined.json`) is deliberately invisible to the retry walk -- that is what stops
    one unreadable day starving the eight retries a lane gets -- and until now nothing counted them
    either, so a lane writing claims it could not read back accumulated silent permanent losses. Both
    suffixes live under `availability/pending/`, so `list_availability_retry_claims` separates them
    out of the SAME walk: the count costs no extra request, and the cap it obeys is the key budget
    that walk already had.
    """
    if availability is None or max_days <= 0:
        return ()
    lane_root = availability_lane_root(lane, kind)
    try:
        claims = store.list_availability_retry_claims(lane, kind)
    except Exception as error:  # an unreadable retry ledger must never end a tick
        return (
            AvailabilityExtensionOutcome(
                state="retry_owed",
                lane_root=lane_root,
                day=None,
                reason=f"listing owed availability days failed: {type(error).__name__}: {error}",
                error_kind="retry_ledger_unreadable",
            ),
        )
    outcomes: list[AvailabilityExtensionOutcome] = []
    if claims.quarantined:
        outcomes.append(_quarantine_sweep(lane_root, claims.quarantined))
    for day in claims.owed[:max_days]:
        outcomes.append(  # noqa: PERF401 - each turn awaits, so no comprehension can build this
            await _retry_one_day(
                session,
                store,
                availability,
                lane=lane,
                kind=kind,
                day=day,
                lane_root=lane_root,
                now=now,
                publication_barrier=publication_barrier,
            )
        )
    return tuple(outcomes)


async def _retry_one_day(  # noqa: PLR0911, PLR0913 - one typed outcome per exit, one coordinate per arg
    session: AsyncSession,
    store: ObjectStore,
    availability: AvailabilityStorage,
    *,
    lane: str,
    kind: PartitionKind,
    day: date,
    lane_root: str,
    now: Callable[[], datetime],
    publication_barrier: AvailabilityPublicationBarrier,
) -> AvailabilityExtensionOutcome:
    """Rebuild one owed day's evidence from the claim it already wrote, publish it, and drop the claim."""
    marker = store.availability_retry_marker_path(lane, kind, day)
    try:
        payload = store.read_availability_retry(lane, kind, day)
    except Exception as error:
        return _retry_failure(lane_root, day, "retry_marker_unreadable", error)
    if payload is None:
        return AvailabilityExtensionOutcome(
            state="skipped_unchanged",
            lane_root=lane_root,
            day=day,
            reason="the retry claim was cleared before this turn reached it",
        )
    try:
        claim = _claim_from_marker(payload, lane_root=lane_root, day=day)
    except (ValueError, AvailabilityError) as error:
        return _quarantine_malformed_claim(
            store,
            payload,
            lane=lane,
            kind=kind,
            day=day,
            lane_root=lane_root,
            error=error,
        )
    try:
        index = read_latest_availability(availability, lane_root=lane_root)
    except AvailabilityUnavailableError as unavailable:
        if unavailable.code == "availability_missing":
            return AvailabilityExtensionOutcome(
                state="not_bootstrapped",
                lane_root=lane_root,
                day=day,
                reason=f"{lane_root} still has no availability generation, so the owed day stays owed",
                error_kind="availability_not_bootstrapped",
                retry_marker=marker,
            )
        return _read_failure(lane_root, day, unavailable, retry_marker=marker)
    except AvailabilityError as error:
        return _read_failure(lane_root, day, error, retry_marker=marker)
    return await _index_claimed_day(
        session,
        store,
        availability,
        index=index,
        claim=claim,
        lane=lane,
        kind=kind,
        retry_marker=marker,
        now=now,
        publication_barrier=publication_barrier,
    )


async def _index_claimed_day(  # noqa: PLR0913 - one lane-day coordinate or seam per arg
    session: AsyncSession,
    store: ObjectStore,
    availability: AvailabilityStorage,
    *,
    index: AvailabilityIndex,
    claim: _DayClaim,
    lane: str,
    kind: PartitionKind,
    retry_marker: str,
    now: Callable[[], datetime],
    publication_barrier: AvailabilityPublicationBarrier,
) -> AvailabilityExtensionOutcome:
    """Turn one claimed day into rows against a KNOWN head, publish them, and clear the claim on success."""
    lane_root = index.pointer.identity.lane_root
    day = claim.day
    try:
        prepared = _prepare_day(index, claim)
    except (ValueError, AvailabilityError) as refused:
        # The contract refused to describe this day at all -- a receipt key outside the layout, or
        # evidence past its byte ceiling. A retry would build the same document and be refused
        # identically, so the claim is dropped rather than left to spin.
        _clear_claim(store, lane=lane, kind=kind, day=day)
        return AvailabilityExtensionOutcome(
            state="ladder_incomplete",
            lane_root=lane_root,
            day=day,
            reason=(
                f"{lane} {day.isoformat()}: the day's evidence could not be built: {type(refused).__name__}: {refused}"
            ),
            error_kind="evidence_unbuildable",
        )
    if isinstance(prepared, _LadderGap):
        _clear_claim(store, lane=lane, kind=kind, day=day)
        return AvailabilityExtensionOutcome(
            state="ladder_incomplete",
            lane_root=lane_root,
            day=day,
            reason=prepared.reason,
            error_kind=prepared.kind,
        )
    if _already_indexed(index, prepared.rows):
        _clear_claim(store, lane=lane, kind=kind, day=day)
        return AvailabilityExtensionOutcome(
            state="skipped_unchanged",
            lane_root=lane_root,
            day=day,
            reason="the generation already carries this day's exact rungs and receipts",
            generation_key=index.pointer.generation_key,
        )
    try:
        if prepared.source_object_key is not None and prepared.source_object_payload is not None:
            availability.put_immutable(
                prepared.source_object_key,
                prepared.source_object_payload,
                content_type=JSON_CONTENT_TYPE,
            )
        for artifact in prepared.artifacts:
            availability.put_immutable(artifact.receipt.key, artifact.payload, content_type=JSON_CONTENT_TYPE)
    except AvailabilityError as error:
        return AvailabilityExtensionOutcome(
            state="retry_owed",
            lane_root=lane_root,
            day=day,
            reason=(
                f"the day is terminal and its availability evidence could not be written; its retry claim "
                f"already stands and names every physical receipt a later turn rebuilds from: "
                f"{type(error).__name__}: {error}"
            ),
            error_kind="evidence_write_failed",
            retry_marker=retry_marker,
        )
    result = await _publish_rows(
        session,
        availability,
        index=index,
        rows=prepared.rows,
        input_sha256=prepared.source_evidence_sha256,
        now=now,
        publication_barrier=publication_barrier,
    )
    if isinstance(result, AvailabilityError):
        return AvailabilityExtensionOutcome(
            state="retry_owed",
            lane_root=lane_root,
            day=day,
            reason=(
                f"the day stays terminal and the prior generation stays valid; only the availability step is "
                f"owed: {type(result).__name__}: {result}"
            ),
            error_kind="publication_failed",
            retry_marker=retry_marker,
        )
    _clear_claim(store, lane=lane, kind=kind, day=day)
    return AvailabilityExtensionOutcome(
        state="extended" if result.advanced else "skipped_unchanged",
        lane_root=lane_root,
        day=day,
        reason=f"the generation now covers {day.isoformat()} at every required rung",
        generation_key=result.pointer.generation_key,
        attempts=result.attempts,
    )


async def _publish_rows(  # noqa: PLR0913 - the publication contract's own coordinates, none foldable
    session: AsyncSession,
    availability: AvailabilityStorage,
    *,
    index: AvailabilityIndex,
    rows: tuple[AvailabilityRow, ...],
    input_sha256: str,
    now: Callable[[], datetime],
    publication_barrier: AvailabilityPublicationBarrier,
) -> PublicationResult | AvailabilityError:
    """Publish one day's rows, returning the result or the refusal rather than raising either."""
    if not rows:
        return AvailabilityError("an availability publication was assembled with no rows at all")
    ceiling = max(row.source_ceiling for row in rows)
    created_at = max(now(), *(row.published_at for row in rows))
    request = PublicationRequest(
        config=AvailabilityConfig(
            identity=index.pointer.identity,
            source_ceiling=ceiling,
            bootstrap_receipt=index.pointer.bootstrap_receipt,
        ),
        created_at=created_at,
        rows=rows,
        input_sha256=input_sha256,
    )
    try:
        return await publish_availability(
            session,
            availability,
            request,
            publication_barrier=publication_barrier,
        )
    except AvailabilityError as error:
        return error
    except ValueError as error:  # the contract's own validators refuse with plain ValueError
        return AvailabilityError(f"{type(error).__name__}: {error}")


def _claim_from_finalized(
    outcome: FinalizedLaneDay,
    *,
    lane: str,
    kind: PartitionKind,
    lane_root: str,
    day: date,
) -> _DayClaim | _LadderGap:
    """State one terminal day from the run's OWN LEDGER, with no reference to the availability head."""
    if outcome.day != day:
        return _LadderGap(reason=f"{lane} {day.isoformat()}: the finalized day describes {outcome.day.isoformat()}")
    if outcome.terminal_state == "governed_absence" and outcome.absence_reason is None:
        return _LadderGap(
            reason=f"{lane} {day.isoformat()}: a governed absence was finalized without the reason its markers carry"
        )
    rungs: list[_RungObjects] = []
    for rung in AVAILABILITY_REQUIRED_RUNGS:
        objects = _rung_objects(outcome, rung=rung, kind=kind, day=day)
        if isinstance(objects, _LadderGap):
            return _LadderGap(reason=f"{lane} {day.isoformat()}: {objects.reason}", kind=objects.kind)
        rungs.append(objects)
    return _DayClaim(
        lane_root=lane_root,
        day=day,
        terminal_state=outcome.terminal_state,
        absence_reason=outcome.absence_reason,
        source=outcome.source,
        source_ceiling=outcome.source_ceiling,
        published_at=outcome.published_at,
        rungs=tuple(rungs),
    )


def _claim_from_repair(  # noqa: PLR0913 - one coordinate of the repaired day per arg
    *,
    lane: str,
    kind: PartitionKind,
    lane_root: str,
    day: date,
    written: WrittenObjectLedger,
    base_rung: RepairedBaseRung,
    run_id: str,
    source_ceiling: date,
    published_at: datetime,
) -> _DayClaim | _LadderGap:
    """State one repaired day from this run's ledger plus the base rung the repair read but did not write."""
    if day > source_ceiling:
        return _LadderGap(reason=f"{lane} {day.isoformat()}: a repaired day cannot exceed its source ceiling")
    rungs: list[_RungObjects] = []
    for rung in AVAILABILITY_REQUIRED_RUNGS:
        if rung == base_rung.rung:
            rungs.append(
                _RungObjects(
                    rung=rung,
                    row_count=base_rung.row_count,
                    data_receipts=base_rung.data_receipts,
                    completion_receipt=base_rung.completion_receipt,
                    absence_receipt=None,
                )
            )
            continue
        objects = _rung_objects_from_ledger(written, rung=rung, kind=kind, day=day)
        if isinstance(objects, _LadderGap):
            return _LadderGap(reason=f"{lane} {day.isoformat()}: {objects.reason}", kind=objects.kind)
        rungs.append(objects)
    return _DayClaim(
        lane_root=lane_root,
        day=day,
        terminal_state="published",
        absence_reason=None,
        source=LaneDaySource(
            origin=LADDER_REPAIR_ORIGIN,
            run_id=run_id,
            row_count=base_rung.row_count,
            part_count=base_rung.part_count,
            exported_at=published_at,
            detail=f"{lane} coarse rungs re-derived from the published base rung",
        ),
        source_ceiling=source_ceiling,
        published_at=published_at,
        rungs=tuple(rungs),
    )


def _rung_objects(
    outcome: FinalizedLaneDay,
    *,
    rung: int,
    kind: PartitionKind,
    day: date,
) -> _RungObjects | _LadderGap:
    """Bind one rung of one terminal day to the objects this run actually wrote at that rung."""
    try:
        tier = validate_zoom_tier(rung)
    except ZoomTierError as error:
        return _LadderGap(reason=f"required rung z{rung} is not a published tier: {error}")
    ledger = outcome.written
    if outcome.terminal_state == "governed_absence":
        absence = ledger.absence_for(kind=kind, zoom=tier, day=day)
        if absence is None:
            return _LadderGap(
                reason=(
                    f"z{rung} carries no governed-absence marker from this run, so the day cannot be indexed "
                    f"as absent at every required rung"
                )
            )
        return _RungObjects(
            rung=rung,
            row_count=0,
            data_receipts=(),
            completion_receipt=None,
            absence_receipt=EvidenceReceipt(key=absence.relative_path, sha256=absence.sha256),
        )
    return _rung_objects_from_ledger(ledger, rung=rung, kind=kind, day=day)


def _rung_objects_from_ledger(  # noqa: PLR0911 - one named ladder gap per exit; folding them blurs the reasons
    ledger: WrittenObjectLedger,
    *,
    rung: int,
    kind: PartitionKind,
    day: date,
) -> _RungObjects | _LadderGap:
    """Bind one PUBLISHED rung to the parts and completion marker this run's ledger recorded for it."""
    try:
        tier = validate_zoom_tier(rung)
    except ZoomTierError as error:
        return _LadderGap(reason=f"required rung z{rung} is not a published tier: {error}")
    parts = ledger.parts_for(kind=kind, zoom=tier, day=day)
    completion = ledger.completion_for(kind=kind, zoom=tier, day=day)
    if not parts and completion is None:
        # THE UNMARKED EMPTIED RUNG, which is now only a LEGACY shape. `derivation._retract_tier`
        # writes a derived-empty completion marker as it empties a rung, so a run that reaches this
        # branch either predates that receipt or died between the prune and the mark. Either way the
        # day cannot form the exact required-rungs set the generation demands, and it is named so a
        # summary counts it apart from a genuinely broken export -- see `AGENTS.md`. A ladder repair
        # (`gap_fill.repair_one_lane_day`) re-derives the rung, closes it, and claims the day.
        return _LadderGap(
            reason=(
                f"z{rung} holds neither parts nor a completion marker: every base row was dropped at this rung "
                f"and it was retracted without the derived-empty receipt that would close it"
            ),
            kind=DERIVED_TO_ZERO_ROWS,
        )
    if not parts and completion is not None and completion.derived_empty:
        # THE EMPTIED RUNG, CLOSED. The base rung holds rows and every one of them fell below this
        # rung's floor, so the rung is published and empty -- a statement the day can make at every
        # rung without mixing terminal states, which a governed absence at one rung could not.
        return _RungObjects(
            rung=rung,
            row_count=0,
            data_receipts=(),
            completion_receipt=EvidenceReceipt(key=completion.relative_path, sha256=completion.sha256),
            absence_receipt=None,
        )
    if not parts or completion is None:
        return _LadderGap(
            reason=(
                f"z{rung} wrote {len(parts)} part(s) and "
                f"{'no' if completion is None else 'a'} completion marker in this run, so the required ladder "
                f"is not whole"
            )
        )
    if len(parts) != completion.part_count:
        return _LadderGap(
            reason=(
                f"z{rung} wrote {len(parts)} part(s) while its completion marker claims "
                f"{completion.part_count}, so no honest row can bind both"
            )
        )
    physical_rows = sum(part.row_count for part in parts)
    if physical_rows != completion.row_count:
        return _LadderGap(
            reason=(
                f"z{rung} wrote {physical_rows} physical row(s) while its completion marker claims "
                f"{completion.row_count}, so no honest row can bind both"
            )
        )
    return _RungObjects(
        rung=rung,
        row_count=completion.row_count,
        data_receipts=tuple(EvidenceReceipt(key=part.relative_path, sha256=part.sha256) for part in parts),
        completion_receipt=EvidenceReceipt(key=completion.relative_path, sha256=completion.sha256),
        absence_receipt=None,
    )


def _prepare_day(index: AvailabilityIndex, claim: _DayClaim) -> _PreparedDay | _LadderGap:
    """Build every row and evidence object one claimed day owes against the head that will carry it.

    A LADDER REPAIR REUSES THE DAY'S EXISTING SOURCE EVIDENCE when the generation already holds the
    day. Nothing was exported, so minting a second export-source document would state a fetch that
    never happened and churn the provenance of a day whose base rows are untouched. See `AGENTS.md`.
    """
    identity = index.pointer.identity
    if tuple(entry.rung for entry in claim.rungs) != index.pointer.required_rungs:
        return _LadderGap(
            reason=(
                f"{identity.lane} {claim.day.isoformat()}: the claim names rungs "
                f"{tuple(entry.rung for entry in claim.rungs)} and this lane's generation requires "
                f"{index.pointer.required_rungs}"
            )
        )
    held = _held_source(index, claim)
    if held is not None:
        return _prepared_from_held_source(index, claim, held=held)
    source_object_key, source_object_payload = _source_object(identity.lane_root, day=claim.day, source=claim.source)
    source_receipt = EvidenceReceipt(key=source_object_key, sha256=sha256_digest(source_object_payload))
    source_artifact = build_source_evidence(
        SourceEvidence(
            identity=identity,
            day=claim.day,
            source_ceiling=claim.source_ceiling,
            object_receipts=(source_receipt,),
        )
    )
    rows: list[AvailabilityRow] = []
    artifacts: list[TypedEvidenceArtifact] = [source_artifact]
    for objects in claim.rungs:
        evidence = _rung_evidence(
            identity,
            claim,
            objects=objects,
            source_receipt=source_artifact.receipt,
            source_ceiling=claim.source_ceiling,
        )
        artifact = build_terminal_evidence(evidence)
        artifacts.append(artifact)
        rows.append(availability_row_from_terminal_evidence(evidence, terminal_receipt=artifact.receipt))
    return _PreparedDay(
        rows=tuple(rows),
        source_object_key=source_object_key,
        source_object_payload=source_object_payload,
        artifacts=tuple(artifacts),
        source_evidence_sha256=source_artifact.receipt.sha256,
    )


@dataclass(frozen=True)
class _HeldSource:
    """The source evidence a generation already binds one day to, and the horizon that evidence states."""

    receipt: EvidenceReceipt
    source_ceiling: date


def _held_source(index: AvailabilityIndex, claim: _DayClaim) -> _HeldSource | None:
    """Return the source evidence the generation already binds this day to, when a REPAIR may reuse it.

    Only for a ladder repair, and only when every rung the generation holds for the day agrees on one
    source receipt and one source ceiling -- which `_validate_generation_day` already guarantees for
    any day it admitted.
    """
    if claim.source.origin != LADDER_REPAIR_ORIGIN:
        return None
    indexed = [row for row in index.rows if row.day == claim.day]
    receipts = {row.source_receipt for row in indexed}
    ceilings = {row.source_ceiling for row in indexed}
    if len(receipts) != 1 or len(ceilings) != 1:
        return None
    return _HeldSource(receipt=receipts.pop(), source_ceiling=ceilings.pop())


def _prepared_from_held_source(
    index: AvailabilityIndex,
    claim: _DayClaim,
    *,
    held: _HeldSource,
) -> _PreparedDay:
    """Build a repaired day's rows against source evidence already published, writing no new source object.

    THE REUSED DOCUMENT'S CEILING GOVERNS, not the one the repair recomputed. Verification refuses a
    row whose `source_ceiling` disagrees with the source evidence it binds, and a repair re-derives
    coarse rungs without observing the source at all, so it has nothing new to say about the horizon.
    Restating the lane's ceiling here published rows the verifier then refused: the correction stayed
    `retry_owed` on every tick while the bucket and the index diverged. See `AGENTS.md`, "A repaired
    day joins the index through a claim".
    """
    identity = index.pointer.identity
    rows: list[AvailabilityRow] = []
    artifacts: list[TypedEvidenceArtifact] = []
    for objects in claim.rungs:
        evidence = _rung_evidence(
            identity,
            claim,
            objects=objects,
            source_receipt=held.receipt,
            source_ceiling=held.source_ceiling,
        )
        artifact = build_terminal_evidence(evidence)
        artifacts.append(artifact)
        rows.append(availability_row_from_terminal_evidence(evidence, terminal_receipt=artifact.receipt))
    return _PreparedDay(
        rows=tuple(rows),
        source_object_key=None,
        source_object_payload=None,
        artifacts=tuple(artifacts),
        source_evidence_sha256=held.receipt.sha256,
    )


def _rung_evidence(
    identity: AvailabilityIdentity,
    claim: _DayClaim,
    *,
    objects: _RungObjects,
    source_receipt: EvidenceReceipt,
    source_ceiling: date,
) -> TerminalEvidence:
    """Render one rung's terminal evidence; the contract's own validators refuse anything dishonest."""
    return TerminalEvidence(
        identity=identity,
        day=claim.day,
        rung=objects.rung,
        terminal_state=claim.terminal_state,
        row_count=objects.row_count,
        source_ceiling=source_ceiling,
        published_at=claim.published_at,
        source_receipt=source_receipt,
        data_receipts=objects.data_receipts,
        completion_receipt=objects.completion_receipt,
        absence_receipt=objects.absence_receipt,
        absence_reason=claim.absence_reason,
    )


def _write_claim(  # noqa: PLR0913 - one lane-day coordinate per arg
    store: ObjectStore,
    claim: _DayClaim,
    *,
    lane: str,
    kind: PartitionKind,
    error_kind: str,
    recorded_at: datetime,
) -> str | AvailabilityExtensionOutcome:
    """Record that this terminal day owes its availability step; a failure here is its OWN outcome."""
    try:
        return store.write_availability_retry(
            _retry_marker_payload(claim, error_kind=error_kind, recorded_at=recorded_at),
            layer=lane,
            kind=kind,
            day=claim.day,
        )
    except Exception as error:  # the claim is the ONLY thing that brings a base-complete day back
        return AvailabilityExtensionOutcome(
            state="retry_claim_failed",
            lane_root=claim.lane_root,
            day=claim.day,
            reason=(
                f"the day is terminal and no retry claim could be recorded for it, so nothing will bring it "
                f"back: the base-tier census never revisits a completed day. "
                f"{type(error).__name__}: {error}"
            ),
            error_kind="retry_claim_unwritable",
        )


def _clear_claim(store: ObjectStore, *, lane: str, kind: PartitionKind, day: date) -> None:
    """Drop one day's retry claim once it is satisfied, or once it provably can never be."""
    store.clear_availability_retry(lane, kind, day)


def _source_object(lane_root: str, *, day: date, source: LaneDaySource) -> tuple[str, bytes]:
    """Render what the exporting process held for this day into one content-addressed object."""
    payload = canonical_json(_source_wire(lane_root, day=day, source=source)).encode("utf-8")
    digest = sha256_digest(payload)
    return f"{lane_root}/availability/source/day={day.isoformat()}/export={digest}.json", payload


def _source_wire(lane_root: str, *, day: date, source: LaneDaySource) -> dict[str, object]:
    """The canonical export-source document, spelled once so a claim and a replay agree byte for byte."""
    return {
        "day": day.isoformat(),
        "detail": source.detail,
        "exported_at": _format_datetime(source.exported_at),
        "lane_root": lane_root,
        "origin": source.origin,
        "part_count": source.part_count,
        "row_count": source.row_count,
        "run_id": source.run_id,
        "schema_version": LANE_EXPORT_SOURCE_SCHEMA_VERSION,
    }


def _retry_marker_payload(claim: _DayClaim, *, error_kind: str, recorded_at: datetime) -> bytes:
    """Render the claim that one terminal day still owes its availability step, and everything it owes."""
    payload = canonical_json(
        {
            "absence_reason": claim.absence_reason,
            "day": claim.day.isoformat(),
            "error_kind": error_kind,
            "lane_root": claim.lane_root,
            "published_at": _format_datetime(claim.published_at),
            "recorded_at": _format_datetime(recorded_at),
            "rungs": [_rung_wire(objects) for objects in claim.rungs],
            "schema_version": AVAILABILITY_RETRY_SCHEMA_VERSION,
            "source": _source_wire(claim.lane_root, day=claim.day, source=claim.source),
            "source_ceiling": claim.source_ceiling.isoformat(),
            "terminal_state": claim.terminal_state,
        }
    ).encode("utf-8")
    if len(payload) > MAX_AVAILABILITY_RETRY_BYTES:
        raise ValueError("availability retry claim exceeds its byte ceiling")
    return payload


def _rung_wire(objects: _RungObjects) -> dict[str, object]:
    return {
        "absence_receipt": None if objects.absence_receipt is None else objects.absence_receipt.to_wire(),
        "completion_receipt": (None if objects.completion_receipt is None else objects.completion_receipt.to_wire()),
        "data_receipts": [receipt.to_wire() for receipt in objects.data_receipts],
        "row_count": objects.row_count,
        "rung": objects.rung,
    }


_MARKER_FIELDS: Final = {
    "absence_reason",
    "day",
    "error_kind",
    "lane_root",
    "published_at",
    "recorded_at",
    "rungs",
    "schema_version",
    "source",
    "source_ceiling",
    "terminal_state",
}
_RUNG_FIELDS: Final = {"absence_receipt", "completion_receipt", "data_receipts", "row_count", "rung"}
_SOURCE_FIELDS: Final = {
    "day",
    "detail",
    "exported_at",
    "lane_root",
    "origin",
    "part_count",
    "row_count",
    "run_id",
    "schema_version",
}


def _claim_from_marker(payload: bytes, *, lane_root: str, day: date) -> _DayClaim:
    """Read one retry claim back, refusing anything that is not the exact claim this module writes."""
    value = _marker_object(payload)
    if set(value) != _MARKER_FIELDS:
        raise ValueError(f"availability retry claim fields must be exactly: {', '.join(sorted(_MARKER_FIELDS))}")
    if value["schema_version"] != AVAILABILITY_RETRY_SCHEMA_VERSION:
        raise ValueError(f"availability retry claim schema_version must be {AVAILABILITY_RETRY_SCHEMA_VERSION}")
    if value["lane_root"] != lane_root or value["day"] != day.isoformat():
        raise ValueError("availability retry claim does not describe the lane-day it is filed under")
    terminal_state = value["terminal_state"]
    if terminal_state not in ("published", "governed_absence"):
        raise ValueError("availability retry claim terminal_state must be published or governed_absence")
    listed = value["rungs"]
    if not isinstance(listed, list) or not listed:
        raise ValueError("availability retry claim must name at least one rung")
    return _DayClaim(
        lane_root=lane_root,
        day=day,
        terminal_state="published" if terminal_state == "published" else "governed_absence",
        absence_reason=_optional_text(value["absence_reason"], "absence_reason"),
        source=_source_from_wire(value["source"], lane_root=lane_root, day=day),
        source_ceiling=_marker_date(value["source_ceiling"], "source_ceiling"),
        published_at=_marker_datetime(value["published_at"], "published_at"),
        rungs=tuple(_rung_from_wire(item) for item in listed),
    )


def _marker_object(payload: bytes) -> Mapping[str, object]:
    decoded: object = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("an availability retry claim must be a JSON object")
    return decoded


def _rung_from_wire(value: object) -> _RungObjects:
    if not isinstance(value, dict) or set(value) != _RUNG_FIELDS:
        raise ValueError(f"each retry rung must be exactly: {', '.join(sorted(_RUNG_FIELDS))}")
    rung = value["rung"]
    row_count = value["row_count"]
    if isinstance(rung, bool) or not isinstance(rung, int):
        raise ValueError("a retry rung must carry an integer rung")
    if isinstance(row_count, bool) or not isinstance(row_count, int) or row_count < 0:
        raise ValueError("a retry rung must carry a non-negative row_count")
    data = value["data_receipts"]
    if not isinstance(data, list):
        raise ValueError("a retry rung's data_receipts must be a list")
    return _RungObjects(
        rung=rung,
        row_count=row_count,
        data_receipts=tuple(_receipt_from_wire(item) for item in data),
        completion_receipt=_optional_receipt_from_wire(value["completion_receipt"]),
        absence_receipt=_optional_receipt_from_wire(value["absence_receipt"]),
    )


def _receipt_from_wire(value: object) -> EvidenceReceipt:
    if not isinstance(value, dict) or set(value) != {"key", "sha256"}:
        raise ValueError("each retry receipt must be exactly key and sha256")
    key = value["key"]
    sha256 = value["sha256"]
    if not isinstance(key, str) or not isinstance(sha256, str):
        raise ValueError("a retry receipt must carry a string key and digest")
    return EvidenceReceipt(key=key, sha256=sha256)


def _optional_receipt_from_wire(value: object) -> EvidenceReceipt | None:
    return None if value is None else _receipt_from_wire(value)


def _source_from_wire(value: object, *, lane_root: str, day: date) -> LaneDaySource:
    if not isinstance(value, dict) or set(value) != _SOURCE_FIELDS:
        raise ValueError(f"a retry claim's source must be exactly: {', '.join(sorted(_SOURCE_FIELDS))}")
    if value["schema_version"] != LANE_EXPORT_SOURCE_SCHEMA_VERSION:
        raise ValueError(f"a retry claim's source schema_version must be {LANE_EXPORT_SOURCE_SCHEMA_VERSION}")
    if value["lane_root"] != lane_root or value["day"] != day.isoformat():
        raise ValueError("a retry claim's source does not describe the lane-day it is filed under")
    row_count = value["row_count"]
    part_count = value["part_count"]
    origin = value["origin"]
    run_id = value["run_id"]
    if isinstance(row_count, bool) or not isinstance(row_count, int):
        raise ValueError("a retry claim's source row_count must be an integer")
    if isinstance(part_count, bool) or not isinstance(part_count, int):
        raise ValueError("a retry claim's source part_count must be an integer")
    if not isinstance(origin, str) or not isinstance(run_id, str):
        raise ValueError("a retry claim's source origin and run_id must be strings")
    return LaneDaySource(
        origin=origin,
        run_id=run_id,
        row_count=row_count,
        part_count=part_count,
        exported_at=_marker_datetime(value["exported_at"], "exported_at"),
        detail=_optional_text(value["detail"], "source detail"),
    )


def _optional_text(value: object, label: str) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise ValueError(f"{label} must be a string or null")


def _marker_date(value: object, label: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO day string")
    return date.fromisoformat(value)


def _marker_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{label} must be a UTC instant ending in Z")
    return datetime.fromisoformat(value[:-1]).replace(tzinfo=UTC)


def _already_indexed(index: AvailabilityIndex, rows: Sequence[AvailabilityRow]) -> bool:
    """Return whether the current generation already carries exactly these rows."""
    held: Mapping[tuple[date, int], AvailabilityRow] = {row.grain: row for row in index.rows}
    return all(held.get(row.grain) == row for row in rows)


def _read_failure(
    lane_root: str,
    day: date,
    error: AvailabilityError,
    *,
    retry_marker: str,
) -> AvailabilityExtensionOutcome:
    """Report an unreadable availability head; the claim written before the read is what saves the day."""
    return AvailabilityExtensionOutcome(
        state="retry_owed",
        lane_root=lane_root,
        day=day,
        reason=(
            f"the day is terminal but this lane's availability head could not be read, so nothing was "
            f"published and its retry claim stands: {type(error).__name__}: {error}"
        ),
        error_kind="availability_unreadable",
        retry_marker=retry_marker,
    )


def _quarantine_malformed_claim(  # noqa: PLR0913 - one lane-day coordinate per arg, plus the refusal
    store: ObjectStore,
    payload: bytes,
    *,
    lane: str,
    kind: PartitionKind,
    day: date,
    lane_root: str,
    error: Exception,
) -> AvailabilityExtensionOutcome:
    """Park an unreplayable claim out of the retry ledger and report the day as permanently lost.

    `retry_claim_failed` rather than `retry_owed`: nothing is owed any more. See `AGENTS.md`,
    "A malformed claim can never be retried".
    """
    parked: str | None = None
    try:
        parked = store.quarantine_availability_retry(payload, layer=lane, kind=kind, day=day)
    except Exception:  # a claim that can never be replayed must leave the ledger even unparked
        with suppress(Exception):
            _clear_claim(store, lane=lane, kind=kind, day=day)
    return AvailabilityExtensionOutcome(
        state="retry_claim_failed",
        lane_root=lane_root,
        day=day,
        reason=(
            f"the owed availability claim could not be parsed and can never be replayed, so this terminal day "
            f"leaves the index for good; its bytes are "
            f"{'quarantined at ' + parked if parked is not None else 'unparkable and were dropped'}: "
            f"{type(error).__name__}: {error}"
        ),
        error_kind="retry_marker_malformed",
        retry_marker=parked,
    )


def _quarantine_sweep(lane_root: str, quarantined: Sequence[date]) -> AvailabilityExtensionOutcome:
    """State how many parked claims one lane is carrying, naming a bounded sample of the oldest.

    ONE OUTCOME FOR THE WHOLE SET, carrying its own `counted_days`. Every parked day is a terminal
    day the index does not hold and nothing will retry -- a fact worth a number in the tally, not one
    sentence per day in a detail string a driver folds into a single line.
    """
    named = ", ".join(day.isoformat() for day in quarantined[:QUARANTINED_SAMPLE_SIZE])
    remainder = len(quarantined) - min(len(quarantined), QUARANTINED_SAMPLE_SIZE)
    return AvailabilityExtensionOutcome(
        state="quarantined",
        lane_root=lane_root,
        day=None,
        reason=(
            f"{len(quarantined)} availability claim(s) are parked as unreadable and nothing retries them, so those "
            f"terminal days stay outside the index until an admin reads them: {named}"
            f"{f' and {remainder} more' if remainder else ''}"
        ),
        error_kind="retry_claim_quarantined",
        counted_days=len(quarantined),
    )


def _retry_failure(lane_root: str, day: date, error_kind: str, error: Exception) -> AvailabilityExtensionOutcome:
    """Report an owed day that stays owed, keeping its claim for the next turn."""
    return AvailabilityExtensionOutcome(
        state="retry_owed",
        lane_root=lane_root,
        day=day,
        reason=f"the owed availability step did not complete: {type(error).__name__}: {error}",
        error_kind=error_kind,
    )


def _format_datetime(value: datetime) -> str:
    """Render one UTC instant exactly as every availability document spells it."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("availability evidence timestamps must be timezone-aware UTC")
    rendered = value.astimezone(UTC).isoformat(timespec="microseconds")
    return f"{rendered[:-6]}Z"


__all__ = [
    "AVAILABILITY_RETRY_SCHEMA_VERSION",
    "DEFAULT_MAX_RETRIES_PER_LANE",
    "DERIVED_TO_ZERO_ROWS",
    "LADDER_REPAIR_ORIGIN",
    "LANE_EXPORT_SOURCE_SCHEMA_VERSION",
    "POSTGRES_DAY_EXPORT_ORIGIN",
    "QUARANTINED_SAMPLE_SIZE",
    "AvailabilityExtensionOutcome",
    "AvailabilityExtensionState",
    "AvailabilityExtensionTally",
    "FinalizedLaneDay",
    "LaneDaySource",
    "RepairedBaseRung",
    "claim_repaired_lane_day",
    "extend_availability_for_lane_day",
    "retry_pending_availability",
]
