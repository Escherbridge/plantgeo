"""Per-day-partition content-SHA scoped promotion for the governed vegetation NDVI plane.

BACKLOG P4, ARMED BUT SHADOW. Owner decision 2026-09-18 closed the gap `.omc` memory
`agri-vegetation-promotion-unarmed` and `execution/AGENTS.md` name -- the Monte Carlo NDVI
forecaster the governed plane exists to feed had never had a decided checksum scope: promotion of
one `layer=vegetation/kind=observed/year=/month=/day=` partition is keyed by THAT PARTITION'S OWN
content SHA, matching the availability index's `generation=<content-sha>` convention
(`conductor/code_styleguides/layer-lanes.md` §4a; `pipeline/parquet/availability_index.py`), never
a whole-corpus Postgres digest.

The register verb this module calls is
`execution/vegetation_ndvi_plane.py::register_governed_partition_plane`: it takes ONE day's
`(cell_key, metric_value)` rows, touches `agri.*` only, and reads no source table. This module hands
it exactly the cells it read off that day's Parquet partition and never widens that scope; it only
decides, per partition, whether the call is owed at all. Reuses
`foundation.canonical.sha256_digest`/`canonical_json` -- the same digest routine the availability
index binds into every generation key -- rather than a second one.

Every refusal that verb raises is a `PartitionRegistrationError`, and this module renders it as a
named day entry in the turn report rather than letting it escape: a bare exception out of a
scheduled turn prints one `failed` line with no per-day breakdown, which is the shape that rolled
this lane back twice on 2026-09-19.

The AVAILABILITY INDEX is this turn's authority on what a day's outcome is, not a failed object read
(`layer-lanes.md` §4a). The promoter READS the index and never writes it: it consults
`availability/_LATEST.json` through the same verified path serving uses, and reports a day the index
states `governed_absence` with the index's own `absence_reason`. It therefore does not mint a
governed absence of its own -- an absence the index has not recorded is `not_yet_indexed`, and an
index that STILL claims a day the store cannot serve, after one re-read of the pointer, is a
conflict that fails the turn. So is an index that has LOST the row: only a fresh `governed_absence`
reclassifies a day at the re-read (STYLE-REVIEW-W6 S2).

Evaluation-only artifacts (`kind != "observed"`) are never promotable: `VegetationDayPartitionKey`
refuses construction for anything else, so an evaluation-only day can never reach the register verb
through this path.

Lane wiring (`execution/lane_ids.py::VEGETATION_NDVI_PROMOTION_LANE_ID`,
`execution/lane_specs.py`) registers this verb so it CAN be scheduled, but it is disabled by
default: it is registered in `LANE_SPECS` and absent from the deployed
`PLANTGEO_JOB_EXECUTOR_ACTIVE_LANES` allow-list (`execution/AGENTS.md` §Lane activation), which is a
production mutation this backlog change deliberately does not make; the activation command lives
with the allow-list in `execution/AGENTS.md` §Lane activation.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal, Protocol, cast

from agri_data_service.execution.vegetation_ndvi_plane import (
    PartitionRegistrationError,
    RegistrationSummary,
    register_governed_partition_plane,
)
from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_STREAM

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.pipeline.parquet.availability_documents import AvailabilityIndex
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

#: The only kind this verb is ever asked about. Anything else is refused at construction, never
#: filtered after the fact, so a caller cannot accidentally promote what should stay evaluation-only.
PROMOTABLE_KIND: Final = "observed"
_JSON_ENCODING: Final = "utf-8"
_RECEIPT_SCHEMA_VERSION: Final = 1
#: Default backlog width when an operator names no explicit `--day`: the same conservative single-day
#: default `pipeline/direct/vegetation/forward.py::VEGETATION_DEFAULT_MAX_DAYS` uses for its own turn.
DEFAULT_MAX_DAYS: Final = 1

#: The register seam: one day's cells in, one governed registration out. Named for what it now is --
#: the retired `cell_days`-only forward verb it used to point at no longer exists.
RegisterPartitionPlane = Callable[..., Awaitable[RegistrationSummary]]


class EmptyDayPartitionError(ValueError):
    """A day partition that was WRITTEN but holds no cell values.

    Distinct from a day the AVAILABILITY INDEX states as `governed_absence`, which the turn records
    and moves past (`layer-lanes.md` §4). A written-but-empty partition is an anomaly in the
    writer, so it still fails -- loudly, and naming the lane and the day (`engineering-principles.md` §2).
    """

    def __init__(self, *, layer: str, day: date) -> None:
        super().__init__(
            f"{layer} day partition {day.isoformat()} was written but holds no cell values, so it cannot be "
            f"content-addressed; the forward writer, not this promoter, owes the fix"
        )
        self.layer = layer
        self.day = day


class AvailabilityPartitionConflictError(RuntimeError):
    """The availability index states a day PUBLISHED that the object store holds no part file for.

    Not an absence and never reported as one: §4a makes the index the authority on publication, so an
    index that claims a day the store cannot serve is corruption on one of the two sides. It fails
    the turn loudly rather than degrading into "the source had nothing" (`engineering-principles.md`
    §2, STYLE-REVIEW-W4 B2).

    Raised only AFTER the turn re-reads the pointer once, and only when the WINNING generation does
    not state a governed absence for the day. A prune landing between the turn's availability
    snapshot and the object open is a race whose winning generation records that absence with a
    reason, and paging an operator for it would make the benign case indistinguishable from the
    corrupt one (STYLE-REVIEW-W5 S4). The mid-read form of the same event already has its own name,
    `ConcurrentPrunePartitionError` (`pipeline/parquet/objectstore.py`).

    A winning generation with NO ROW for a day the snapshot stated `published` is the other half of
    the same corruption -- the index lost a row it had -- and is raised here rather than reclassified
    as `not_yet_indexed`, which would have made an availability-index regression exit 0 as
    `waiting_for_writer` (STYLE-REVIEW-W6 S2). The `fresh_state` in the message is what tells the two
    apart.

    The message names BOTH generations and the pointer key, because after the re-read there are two
    generations in play and an operator otherwise cannot tell a stale snapshot from a real
    divergence (STYLE-REVIEW-W6 S3, W5 S4's second half).
    """

    def __init__(  # noqa: PLR0913 - the reconciliation's coordinates: one layer, one day, two generations, one pointer
        self,
        *,
        layer: str,
        day: date,
        fresh_state: IndexedDayState,
        snapshot_generation: str | None,
        winning_generation: str | None,
        pointer_key: str | None,
    ) -> None:
        super().__init__(
            f"{layer} availability index states {day.isoformat()} published, but the object store holds no part "
            f"file for it; the winning generation states {fresh_state!r} for that day, so one of the index and "
            f"the partition is corrupt and an operator owes the reconciliation "
            f"(snapshot generation {snapshot_generation or 'unknown'}, re-read generation "
            f"{winning_generation or 'unknown'}, pointer {pointer_key or 'unknown'})"
        )
        self.layer = layer
        self.day = day
        self.fresh_state = fresh_state
        self.snapshot_generation = snapshot_generation
        self.winning_generation = winning_generation
        self.pointer_key = pointer_key


#: What the lane's availability index says about one day, in the index's OWN vocabulary
#: (`pipeline/parquet/availability_documents.py::AvailabilityRow.terminal_state`), plus the state for
#: a day the index has no row for at all.
IndexedDayState = Literal["published", "governed_absence", "not_yet_indexed"]


@dataclass(frozen=True, slots=True)
class IndexedDay:
    """One day's availability verdict, carrying the index's own `absence_reason` when it has one."""

    state: IndexedDayState
    absence_reason: str | None = None


#: The verdict for a day no row states anything about: one value, not a fresh object per lookup.
_UNINDEXED: Final = IndexedDay(state="not_yet_indexed")


class LaneAvailability(Protocol):
    """What the promoter asks the availability index: one day's verdict, and which generation said so.

    The generation identity is part of the protocol rather than read at the raise site because a
    turn holds TWO of these once it re-reads the pointer, and a conflict an operator can act on has
    to name which is which (STYLE-REVIEW-W6 S3). Both are optional: a fabricated availability in a
    test states verdicts without a generation behind them, and `None` prints as `unknown` rather
    than inventing a SHA.
    """

    @property
    def generation_sha256(self) -> str | None:
        """The `generation=<sha256>` this verdict set was read from, or `None` when it has none."""
        ...

    @property
    def pointer_key(self) -> str | None:
        """The mutable `_LATEST.json` key that resolved to it, or `None` when it was fabricated."""
        ...

    def indexed_day(self, day: date) -> IndexedDay: ...


@dataclass(frozen=True, slots=True)
class AvailabilityIndexDays:
    """A `LaneAvailability` backed by one already-verified availability generation."""

    verdicts: Mapping[date, IndexedDay]
    generation_sha256: str | None = None
    pointer_key: str | None = None
    #: Days the index states `published` at EVERY one of its `required_rungs`, which is what §4a
    #: makes selectable ("a selectable day is their intersection, not the observed union"). `None`
    #: means no rung ladder was stated at all -- a fabricated availability in a test -- and the
    #: base-rung verdicts above are then the only thing known about a day.
    servable_days: frozenset[date] | None = None

    def indexed_day(self, day: date) -> IndexedDay:
        """Return the index's verdict, or `not_yet_indexed` for a day it carries no row for."""
        return self.verdicts.get(day, _UNINDEXED)

    def is_servable(self, day: date) -> bool:
        """Whether every required rung of `day` is published, falling back to the base-rung verdict."""
        if self.servable_days is None:
            return self.indexed_day(day).state == "published"
        return day in self.servable_days


def availability_days_at_base_rung(index: AvailabilityIndex) -> AvailabilityIndexDays:
    """Project one availability index onto its BASE-rung row per day, plus the servable intersection.

    The base rung is the rung the promoter itself reads (`LANE_BASE_ZOOM_TIER`), so it is the rung
    whose terminal state can be checked against what the store holds. A day the index carries only
    at coarser rungs is deliberately `not_yet_indexed` here: no row states the base rung's outcome.

    `servable_days` is the OTHER question, and §4a says they are not the same one: a day is
    selectable where its whole `required_rungs` ladder agrees, not where the base rung alone states
    an outcome. That intersection already has ONE definition -- `AvailabilityIndex.selectable_days`,
    the same one serving answers from -- so this narrows it to the agreed state the promoter can act
    on (`published`) rather than computing a second, driftable version of it.
    """
    from agri_data_service.pipeline.parquet.availability_documents import (  # noqa: PLC0415 - CLI-only, like `main()`
        availability_pointer_key,
    )

    verdicts = {
        row.day: IndexedDay(state=row.terminal_state, absence_reason=row.absence_reason)
        for row in index.rows
        if row.rung == LANE_BASE_ZOOM_TIER
    }
    return AvailabilityIndexDays(
        verdicts=verdicts,
        generation_sha256=index.pointer.generation_sha256,
        pointer_key=availability_pointer_key(index.pointer.identity.lane_root),
        servable_days=frozenset(
            day for day in index.selectable_days() if verdicts.get(day, _UNINDEXED).state == "published"
        ),
    )


class EvaluationArtifactNotPromotableError(ValueError):
    """Raised when a partition kind outside `observed` is offered to the governed-plane promoter."""

    def __init__(self, *, kind: str) -> None:
        super().__init__(
            f"vegetation partition kind {kind!r} is evaluation-only and is never promoted to the governed plane"
        )
        self.kind = kind


@dataclass(frozen=True, slots=True)
class VegetationDayPartitionKey:
    """One `layer=vegetation/kind=<kind>/year=/month=/day=` partition identity.

    Zoom-INDEPENDENT on purpose, matching `foundation.parquet.paths.promotion_receipt_path`: the
    governed plane is promoted once per day, not once per rendered rung.
    """

    day: date
    kind: str = PROMOTABLE_KIND
    layer: Literal["vegetation"] = VEGETATION_PLANE_STREAM

    def __post_init__(self) -> None:
        if self.kind != PROMOTABLE_KIND:
            raise EvaluationArtifactNotPromotableError(kind=self.kind)


@dataclass(frozen=True, slots=True)
class VegetationPromotionReceipt:
    """One recorded promotion decision for a single day partition."""

    partition: VegetationDayPartitionKey
    content_sha256: str
    promoted_at: datetime
    release_set_id: str | None
    source_release_id: str | None

    def to_json_bytes(self) -> bytes:
        """Serialize with the same canonical, deterministic JSON the digest itself uses."""
        return canonical_json(
            {
                "schema_version": _RECEIPT_SCHEMA_VERSION,
                "day": self.partition.day.isoformat(),
                "kind": self.partition.kind,
                "content_sha256": self.content_sha256,
                "promoted_at": self.promoted_at.astimezone(UTC).isoformat(),
                "release_set_id": self.release_set_id,
                "source_release_id": self.source_release_id,
            }
        ).encode(_JSON_ENCODING)

    @classmethod
    def from_json_bytes(cls, payload: bytes) -> VegetationPromotionReceipt:
        """Parse a previously written receipt, failing closed on anything not shaped as expected."""
        decoded = json.loads(payload.decode(_JSON_ENCODING))
        if not isinstance(decoded, dict) or decoded.get("schema_version") != _RECEIPT_SCHEMA_VERSION:
            raise ValueError("vegetation promotion receipt is not a recognised schema_version 1 record")
        return cls(
            partition=VegetationDayPartitionKey(day=date.fromisoformat(str(decoded["day"])), kind=str(decoded["kind"])),
            content_sha256=str(decoded["content_sha256"]),
            promoted_at=datetime.fromisoformat(str(decoded["promoted_at"])),
            release_set_id=None if decoded.get("release_set_id") is None else str(decoded["release_set_id"]),
            source_release_id=None if decoded.get("source_release_id") is None else str(decoded["source_release_id"]),
        )


@dataclass(frozen=True, slots=True)
class VegetationPromotionOutcome:
    """Measured effect of one bounded partition-promotion decision."""

    partition: VegetationDayPartitionKey
    content_sha256: str
    status: Literal["promoted", "unchanged"]
    receipt: VegetationPromotionReceipt
    registration: RegistrationSummary | None


def day_partition_content_sha256(cell_values: Sequence[tuple[str, float]]) -> str:
    """Digest one day-partition's exact cell-value content.

    Reuses `foundation.canonical.sha256_digest`/`canonical_json` -- the identical digest routine
    the availability index binds into every `generation=<content-sha>` key
    (`pipeline/parquet/availability_index.py`) -- rather than a second, driftable one scoped to
    this verb. Content-addressed, so an unchanged partition always re-digests to the same value
    regardless of row order the caller happened to read them in.
    """
    if not cell_values:
        raise ValueError("a day partition requires at least one cell value to be content-addressed")
    deduplicated: dict[str, float] = {}
    for cell_key, value in cell_values:
        if cell_key in deduplicated:
            raise ValueError(f"day partition cell key {cell_key!r} is duplicated")
        deduplicated[cell_key] = value
    ordered = [[cell_key, deduplicated[cell_key]] for cell_key in sorted(deduplicated)]
    return sha256_digest(canonical_json(ordered))


async def promote_vegetation_day_partition(  # noqa: PLR0913 - one argument per idempotency input; bundling loses the per-field docstring above
    session: AsyncSession,
    *,
    day: date,
    kind: str,
    cell_values: Sequence[tuple[str, float]],
    previous_receipt: VegetationPromotionReceipt | None,
    now: datetime | None = None,
    register: RegisterPartitionPlane = register_governed_partition_plane,
) -> VegetationPromotionOutcome:
    """Promote exactly one day partition, keyed by its own content SHA, or report it unchanged.

    An unchanged partition (content SHA equal to `previous_receipt.content_sha256`) never calls the
    governed-plane register verb again -- a re-run of an already-promoted, byte-identical partition
    is idempotent by construction, never a second registration attempt against the same day. A
    changed partition re-promotes only itself: the register verb takes ONE observed day and the
    cell VALUES read off that day's partition, so the values this verb already holds are handed
    straight through. They are the register verb's only source; the frozen `agri.vegetation` corpus
    it used to digest is unreachable from either module.

    Raises `EvaluationArtifactNotPromotableError` for any `kind` other than `observed`, before any
    checksum is computed or any register call is attempted, and propagates every
    `PartitionRegistrationError` the register verb raises for `run_vegetation_promotion` to render
    as a named day entry.
    """
    partition = VegetationDayPartitionKey(day=day, kind=kind)  # raises for a non-`observed` kind
    content_sha256 = day_partition_content_sha256(cell_values)
    moment = now or datetime.now(UTC)
    if previous_receipt is not None and previous_receipt.content_sha256 == content_sha256:
        return VegetationPromotionOutcome(
            partition=partition,
            content_sha256=content_sha256,
            status="unchanged",
            receipt=previous_receipt,
            registration=None,
        )
    registration = await register(session, observed_day=day, cell_values=tuple(cell_values))
    receipt = VegetationPromotionReceipt(
        partition=partition,
        content_sha256=content_sha256,
        promoted_at=moment,
        release_set_id=str(registration.plane.release_set_id),
        source_release_id=str(registration.plane.source_release_id),
    )
    return VegetationPromotionOutcome(
        partition=partition,
        content_sha256=content_sha256,
        status="promoted",
        receipt=receipt,
        registration=registration,
    )


def load_promotion_receipt(
    store: ObjectStore, *, day: date, kind: PartitionKind = PROMOTABLE_KIND
) -> VegetationPromotionReceipt | None:
    """Return the last recorded promotion receipt for one day partition, or `None` when never promoted."""
    payload = store.read_promotion_receipt(VEGETATION_PLANE_STREAM, kind, day)
    return None if payload is None else VegetationPromotionReceipt.from_json_bytes(payload)


def save_promotion_receipt(store: ObjectStore, receipt: VegetationPromotionReceipt) -> str:
    """Durably record one promotion decision so the next turn can re-run it as a no-op."""
    # `receipt.partition.kind` is `str` -- `VegetationDayPartitionKey.__post_init__` already refuses
    # construction for anything but `PROMOTABLE_KIND`, so this narrows a runtime-guaranteed value.
    return store.write_promotion_receipt(
        receipt.to_json_bytes(),
        layer=VEGETATION_PLANE_STREAM,
        kind=cast("PartitionKind", receipt.partition.kind),
        day=receipt.partition.day,
    )


def read_day_partition_cell_values(store: ObjectStore, day: date) -> tuple[tuple[str, float], ...]:
    """Read one base-rung day partition's `(cell_id, metric_value)` rows straight off Parquet."""
    table = store.read_partition(VEGETATION_PLANE_STREAM, PROMOTABLE_KIND, LANE_BASE_ZOOM_TIER, day)
    cell_ids = table.column("cell_id").to_pylist()
    metric_values = table.column("metric_value").to_pylist()
    return tuple(
        (cell_id, float(metric_value))
        for cell_id, metric_value in zip(cell_ids, metric_values, strict=True)
        if cell_id is not None
    )


def emit(payload: dict[str, object]) -> None:
    """Write one stable JSON progress record to stderr, leaving stdout for the terminal report."""
    print(json.dumps(payload, sort_keys=True), file=sys.stderr, flush=True)


def parser() -> argparse.ArgumentParser:
    """Build the bounded governed-plane promotion operator.

    Without `--day`, promotes the newest `--max-days` days trailing the vegetation LANE'S OWN
    Parquet availability index (`default_promotion_days`), so this lane is schedulable exactly like
    its sibling direct writers, and refuses as `stale_ceiling` when that ceiling has fallen further
    behind today than `lane_specs.vegetation_promotion_stale_ceiling_days()` allows. Every day it
    touches is still idempotent against its own promotion receipt, so a wider `--max-days` never
    re-registers an unchanged partition.
    """
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--day", action="append", dest="days", default=None, help="one ISO date; repeatable")
    built.add_argument("--max-days", type=int, default=DEFAULT_MAX_DAYS)
    built.add_argument("--run-id", default=None)
    return built


@dataclass(frozen=True, slots=True)
class PromotionCeiling:
    """The newest SERVABLE day the index states at or before `today`, and how far behind it sits.

    `age_days` is the distance from that day to `today`, and it is the only thing in this turn that
    can tell a live lane from a dead one: the ceiling compared against itself is always current, so
    a lane whose forward writer died re-confirms the same ancient day forever (`.omc` memory
    `plantgeo-freshness-yardstick-is-tautological`). Both fields are `None` when the index states no
    servable day at all, which is the already-honest `no_indexed_day_promoted` path and not this one.
    """

    day: date | None
    age_days: int | None

    def is_stale(self, *, stale_after_days: int) -> bool:
        """Whether the ceiling has fallen further behind `today` than the lane's own window allows."""
        return self.age_days is not None and self.age_days > stale_after_days


def promotion_ceiling(*, availability: AvailabilityIndexDays, today: date) -> PromotionCeiling:
    """Return the newest day at or before `today` the index states servable at every required rung.

    SERVABLE, not base-rung-published: `layer-lanes.md` §4a makes a selectable day the intersection
    of `required_rungs`, and `VegetationDayPartitionKey` is deliberately zoom-independent, so a day
    promoted off the base rung alone would register a whole governed day that serving cannot answer
    at the rungs above it (STYLE-REVIEW-W8 S3). `AvailabilityIndexDays.is_servable` carries that
    intersection; the promoter still READS and content-addresses the base rung, which is the rung
    whose bytes exist.
    """
    servable_days = sorted(day for day in availability.verdicts if day <= today and availability.is_servable(day))
    if not servable_days:
        return PromotionCeiling(day=None, age_days=None)
    ceiling = servable_days[-1]
    return PromotionCeiling(day=ceiling, age_days=(today - ceiling).days)


def default_promotion_days(*, availability: AvailabilityIndexDays, today: date, max_days: int) -> tuple[date, ...]:
    """Return the trailing `max_days` calendar days ending at the newest SERVABLE indexed day.

    The ceiling is the vegetation lane's own Parquet availability index (`read_lane_availability` ->
    `availability_days_at_base_rung` -> `promotion_ceiling`), never Postgres:
    `pipeline/direct/vegetation/forward.py`'s
    `settled_through` queries `agri.vegetation`, which was retired 2026-09-04 and holds no rows for
    any day this lane could promote (`.omc` memory `agri-vegetation-promotion-unarmed`; incident
    evidence `conductor/tracks/gapless_parquet_publication_20260901/evidence/ndvi-promotion-activation-20260919.md`).
    Calling that boundary here is what made the lane's first activated tick raise `ValueError: no
    vegetation observations exist at or before <today>` instead of promoting.

    This window is NOT bounded by staleness, and must not be: how OLD the ceiling is decides whether
    the turn runs at all, which is `main()`'s call through `PromotionCeiling.is_stale`, not a silent
    narrowing of which days an operator's turn would touch.

    Returns an empty tuple when the index carries no servable day at or before `today` at all --
    the lane's forward writer having indexed nothing yet is exactly the `not_yet_indexed`/
    `waiting_for_writer` territory `run_vegetation_promotion`/`_promotion_report` already handle for
    an empty `days` sequence, so this never raises for that case.
    """
    if max_days < 1:
        raise ValueError("--max-days must be at least one")
    ceiling = promotion_ceiling(availability=availability, today=today).day
    if ceiling is None:
        return ()
    return tuple(sorted(ceiling - timedelta(days=offset) for offset in range(max_days)))


def _governed_absence_entry(day: date, indexed: IndexedDay) -> dict[str, object]:
    """The turn's result entry for a day the index states absent, carrying the index's own reason.

    Spelled separately from `_non_promotable_entry` because the pointer re-read may reclassify ONLY
    a governed absence (STYLE-REVIEW-W6 S2), and calling a function that can also answer `None` for
    a state already narrowed to `governed_absence` would leave an unreachable branch at the call
    site (`engineering-principles.md` §2).
    """
    return {
        "day": day.isoformat(),
        "layer": VEGETATION_PLANE_STREAM,
        "status": "absent",
        "reason": indexed.absence_reason or "governed_absence_without_recorded_reason",
    }


def _non_promotable_entry(day: date, indexed: IndexedDay) -> dict[str, object] | None:
    """The turn's result entry for a day the index does not state `published`, or `None` if it does."""
    if indexed.state == "governed_absence":
        return _governed_absence_entry(day, indexed)
    if indexed.state == "not_yet_indexed":
        return {
            "day": day.isoformat(),
            "layer": VEGETATION_PLANE_STREAM,
            "status": "not_yet_indexed",
            "reason": "availability_index_has_no_row_for_this_day",
        }
    return None


async def run_vegetation_promotion(  # noqa: PLR0913 - one coordinate of the turn per argument, plus the re-read seam
    session: AsyncSession,
    store: ObjectStore,
    *,
    days: Sequence[date],
    availability: LaneAvailability,
    now: datetime | None = None,
    refresh_availability: Callable[[], LaneAvailability] | None = None,
) -> dict[str, object]:
    """Promote every named day, newest last, each idempotent against its own last receipt.

    The AVAILABILITY INDEX, not a failed object read, decides what a day's outcome is
    (`layer-lanes.md` §4a: the index is the one artifact that states `published|governed_absence`).
    Each day is therefore classified before any object is opened:

    - `governed_absence` -- recorded as an absence carrying the INDEX'S OWN `absence_reason`, with no
      object read attempted at all. This is the honest gap §4 asks for.
    - `not_yet_indexed` -- the lane has published no row for this day yet, so there is nothing to
      promote and nothing to report as missing. Skipped; it cannot turn a turn that DID promote into
      a failure, and a turn whose EVERY day is one exits 0 as `waiting_for_writer` (see
      `_promotion_report`).
    - `published` -- read and promoted. If the store then holds no part file, the index snapshot this
      turn opened with is RE-READ once before anything is raised, and ONLY a fresh `governed_absence`
      reclassifies: that is a prune or retention pass landing inside the turn's window, recorded with
      a reason by the winning generation, which §4a retries from rather than pages for. A fresh
      `not_yet_indexed` is the index having LOST a row it had, and raises with the rest
      (STYLE-REVIEW-W4 B1/B2, W5 S4, W6 S2).

    `refresh_availability` is that re-read, injected so a test can state the winning generation
    without an object store; it defaults to this module's own `read_lane_availability`. It is called
    at most ONCE per turn, and only on the conflict path.

    A partition that exists and is empty still raises, because that is the writer misbehaving rather
    than the source having nothing.

    A `PartitionRegistrationError` from the register verb is the ONE exception class this loop
    catches, because it is the one whose every member names a specific, reportable defect in the day
    it was handed. It becomes a `registration_refused` day entry naming the refusal's class and
    message, its transaction is rolled back, the remaining days still run, and the turn ends
    non-zero on the terminal `registration_refused` status. Everything else still propagates: an
    exception nobody has given a name to is not something this turn can honestly report.
    """
    # Imported here, like `main()`'s own store import: the object-store module carries the heavy
    # client dependencies this module otherwise only needs at CLI time.
    from agri_data_service.pipeline.parquet.objectstore import PartitionNotWrittenError  # noqa: PLC0415

    results: list[dict[str, object]] = []
    #: The winning generation, read lazily and at most once per turn -- `None` means "not yet read",
    #: which is also what narrows it for the raise below, so no separate flag and no dead arm.
    winning_availability: LaneAvailability | None = None
    for day in days:
        skipped = _non_promotable_entry(day, availability.indexed_day(day))
        if skipped is not None:
            results.append(skipped)
            emit({"event": "vegetation_promotion_day", **results[-1]})
            continue
        try:
            cell_values = read_day_partition_cell_values(store, day)
        except PartitionNotWrittenError as conflict:
            if winning_availability is None:
                winning_availability = (refresh_availability or read_lane_availability)()
            fresh = winning_availability.indexed_day(day)
            if fresh.state != "governed_absence":
                # Only a governed ABSENCE is a benign race: the winning generation states the day
                # itself, with a reason. A fresh `not_yet_indexed` means the index LOST a row it had
                # -- half the corruption this error exists for -- and reclassifying it made an
                # availability regression exit 0 as `waiting_for_writer` (STYLE-REVIEW-W6 S2).
                raise AvailabilityPartitionConflictError(
                    layer=VEGETATION_PLANE_STREAM,
                    day=day,
                    fresh_state=fresh.state,
                    snapshot_generation=availability.generation_sha256,
                    winning_generation=winning_availability.generation_sha256,
                    pointer_key=winning_availability.pointer_key or availability.pointer_key,
                ) from conflict
            # The index moved under the turn: classify per the FRESH row, which is the winning
            # generation's own statement about this day, and never the snapshot's stale one.
            results.append(
                {**_governed_absence_entry(day, fresh), "reclassified": "availability_index_advanced_during_turn"}
            )
            emit({"event": "vegetation_promotion_day", **results[-1]})
            continue
        if not cell_values:
            raise EmptyDayPartitionError(layer=VEGETATION_PLANE_STREAM, day=day)
        previous_receipt = load_promotion_receipt(store, day=day)
        try:
            outcome = await promote_vegetation_day_partition(
                session,
                day=day,
                kind=PROMOTABLE_KIND,
                cell_values=cell_values,
                previous_receipt=previous_receipt,
                now=now,
            )
        except PartitionRegistrationError as refusal:
            # The refused registration's partial transaction dies HERE, before the next day opens
            # one. `advisory_lock` is `pg_advisory_xact_lock`, so leaving it open would carry both
            # the poisoned transaction and the publication barrier into every remaining day.
            await session.rollback()
            results.append(
                {
                    "day": day.isoformat(),
                    "layer": VEGETATION_PLANE_STREAM,
                    "status": REGISTRATION_REFUSED_STATUS,
                    "error_class": type(refusal).__name__,
                    "reason": str(refusal),
                }
            )
            emit({"event": "vegetation_promotion_day", **results[-1]})
            continue
        if outcome.status == "promoted":
            # One day, one transaction, committed before its receipt is written. The register verb
            # documents a caller-owned commit and nothing else in this path performs one, so an
            # uncommitted turn would roll every governed release back at session close while the
            # object store kept a receipt the NEXT turn reads as `unchanged` -- green forever
            # against a plane holding nothing.
            await session.commit()
        save_promotion_receipt(store, outcome.receipt)
        results.append(
            {
                "day": day.isoformat(),
                "layer": VEGETATION_PLANE_STREAM,
                "status": outcome.status,
                "content_sha256": outcome.content_sha256,
                "cell_count": len(cell_values),
            }
        )
        emit({"event": "vegetation_promotion_day", **results[-1]})
    report = _promotion_report(results)
    if report["status"] == WAITING_FOR_WRITER_STATUS:
        # Once per turn, not once per day: the whole point of this status is that it must be
        # readable in a log without being an alarm.
        emit(
            {
                "event": "vegetation_promotion_waiting_for_writer",
                "layer": VEGETATION_PLANE_STREAM,
                "days": report["not_yet_indexed_days"],
                "reason": report["reason"],
            }
        )
    return report


#: At least one day promoted or was confirmed unchanged, and no day was refused.
COMPLETED_STATUS: Final = "completed"
#: Every evaluated day was `not_yet_indexed`: the forward writer has published none of them yet.
WAITING_FOR_WRITER_STATUS: Final = "waiting_for_writer"
#: No day made progress for a reason that is not a refusal: an all-absent or empty window.
NO_DAYS_PROMOTED_STATUS: Final = "no_days_promoted"
#: At least one day reached the register verb and was refused by name; see `_promotion_report`.
REGISTRATION_REFUSED_STATUS: Final = "registration_refused"
#: The status a default-window turn ends on when its ceiling is older than the lane can explain.
STALE_CEILING_STATUS: Final = "stale_ceiling"

#: The terminal statuses a turn may exit ZERO on. `waiting_for_writer` is deliberately NOT
#: `completed`: it exits 0, but a reader must be able to tell a turn that promoted from one that had
#: nothing to promote yet.
SUCCESSFUL_TURN_STATUSES: Final[frozenset[str]] = frozenset({COMPLETED_STATUS, WAITING_FOR_WRITER_STATUS})

#: The terminal statuses that exit NON-ZERO. Kept as a set beside the successful one so the two
#: provably PARTITION the vocabulary: a status in neither, or in both, is a bug a test can name
#: rather than an exit code an operator has to infer.
FAILING_TURN_STATUSES: Final[frozenset[str]] = frozenset(
    {NO_DAYS_PROMOTED_STATUS, REGISTRATION_REFUSED_STATUS, STALE_CEILING_STATUS}
)

#: Every status this module can put on a terminal report. Nothing else may reach `exit_code_for`.
TERMINAL_STATUSES: Final[frozenset[str]] = SUCCESSFUL_TURN_STATUSES | FAILING_TURN_STATUSES


def ceiling_fields(ceiling: PromotionCeiling, *, stale_after_days: int) -> dict[str, object]:
    """Render the ceiling an automatic turn chose, its age, and the age it would be called stale at.

    Carried on EVERY default-window report, not only the stale one: a reader who can see only
    `status: completed` cannot tell whether the day it promoted is yesterday's or last year's, which
    is the whole of the freshness-yardstick trap.
    """
    return {
        "ceiling_day": None if ceiling.day is None else ceiling.day.isoformat(),
        "ceiling_age_days": ceiling.age_days,
        "ceiling_stale_after_days": stale_after_days,
    }


def stale_ceiling_report(ceiling: PromotionCeiling, *, stale_after_days: int) -> dict[str, object]:
    """Render the terminal report for a turn whose newest servable day is too old to be progress.

    Its own status and its own vocabulary, exiting NON-ZERO: re-confirming an ancient day is exactly
    what `promoted`/`unchanged` would have reported as progress, so a lane whose forward writer died
    would otherwise stay green forever (`engineering-principles.md` §2 "fail closed and loud";
    `layer-lanes.md` §1a "report current distinctly from not looked at"). No day is evaluated at all
    on this path -- the turn refuses before it promotes, rather than promoting and then complaining.
    """
    return {
        "status": STALE_CEILING_STATUS,
        "days": [],
        "absent_days": [],
        "not_yet_indexed_days": [],
        "registration_refused_days": [],
        "reason": "newest_servable_day_is_older_than_this_lane_can_explain",
        **ceiling_fields(ceiling, stale_after_days=stale_after_days),
    }


def _promotion_report(results: list[dict[str, object]]) -> dict[str, object]:
    """Render the terminal report, naming WHY a turn that promoted nothing ended as it did.

    Four terminal statuses are decided here, in this precedence, because the turn has four
    genuinely different outcomes:

    - `registration_refused` -- at least one day reached the register verb and was refused by name.
      Exit non-zero, and it DOMINATES: a refusal is a defect in the day it names (an unregistered
      lattice cell, an empty or duplicated partition, a non-finite value), never a governed outcome
      the way an absence is, so a turn that promoted one day and was refused on another may not
      report `completed` and exit 0. It is also not folded into `no_days_promoted`, whose name would
      then be false for exactly that mixed turn. `registration_refused_days` names every refused day
      and each day entry carries the refusal's `error_class` and message.
    - `completed` -- at least one day was promoted or confirmed unchanged, and none was refused.
      Exit 0.
    - `waiting_for_writer` -- EVERY day evaluated is `not_yet_indexed`. Exit 0, named, logged once.
      This is the steady state of the intended configuration, not a failure: the lane's forward
      writer has not started (module docstring), `--max-days` defaults to 1, so a scheduled turn
      evaluates exactly one unpublished day. Failing it would page every turn, indefinitely, for a
      lane that is behaving exactly as configured -- and a status nobody can act on is noise, not
      the loudness `engineering-principles.md` §2 asks for (STYLE-REVIEW-W5 S3; the recorded owner
      ruling that bounded catch-up turns exit 0, `.omc` memory `plantgeo-owner-decisions-2026-09-04`).
    - `no_days_promoted` -- everything else with no progress and no refusal: every day a governed
      absence, an empty window, or a mixed turn that promoted nothing. Exit non-zero, which is what
      stops a lane reporting success forever against days that do not exist (STYLE-REVIEW-W4 B1).

    A `not_yet_indexed` day is still neutral WITHIN a turn: it counts as neither progress nor
    absence, so it cannot turn a turn that DID promote into a failure.

    A fifth status, `stale_ceiling`, exists and is NOT decided here: it is decided before any day
    is evaluated (`main` -> `stale_ceiling_report`), because "these days are too old to be progress"
    is a statement about the window, not about what the days in it turned out to hold.
    """
    absent_days = [str(entry["day"]) for entry in results if entry["status"] == "absent"]
    not_yet_indexed_days = [str(entry["day"]) for entry in results if entry["status"] == "not_yet_indexed"]
    refused_days = [str(entry["day"]) for entry in results if entry["status"] == REGISTRATION_REFUSED_STATUS]
    progressed = [entry for entry in results if entry["status"] in ("promoted", "unchanged")]
    waiting_for_writer = bool(results) and len(not_yet_indexed_days) == len(results)
    if refused_days:
        status = REGISTRATION_REFUSED_STATUS
    elif progressed:
        status = COMPLETED_STATUS
    elif waiting_for_writer:
        status = WAITING_FOR_WRITER_STATUS
    else:
        status = NO_DAYS_PROMOTED_STATUS
    report: dict[str, object] = {
        "status": status,
        "days": results,
        # Surfaced at the top level so a scheduled turn's report states its absences and refusals
        # without the reader walking every day entry.
        "absent_days": absent_days,
        "not_yet_indexed_days": not_yet_indexed_days,
        "registration_refused_days": refused_days,
    }
    if status == REGISTRATION_REFUSED_STATUS:
        report["reason"] = "the_registration_verb_refused_at_least_one_day_partition"
    elif status == WAITING_FOR_WRITER_STATUS:
        report["reason"] = "forward_writer_has_indexed_none_of_these_days"
    elif status == NO_DAYS_PROMOTED_STATUS:
        report["reason"] = (
            "all_days_absent" if results and len(absent_days) == len(results) else "no_indexed_day_promoted"
        )
    return report


def exit_code_for(report: Mapping[str, object]) -> int:
    """Map one terminal report onto the process exit code; `TERMINAL_STATUSES` is the whole domain.

    Zero for a turn that promoted (`completed`) and for one that had nothing to promote yet
    (`waiting_for_writer`, named in the report and distinguishable from the first). Non-zero for
    every other terminal status: a refused registration, an all-absent or empty window, and a
    ceiling too old to be progress -- each with its own reason already in the report.

    Fails CLOSED on a status this module does not define, rather than raising: an unknown status is
    a defect in the caller, and the last thing a scheduled lane should do on encountering one is
    turn it into an uncaught exception outside `main()`'s own report. The partition is proved by
    `FAILING_TURN_STATUSES`/`SUCCESSFUL_TURN_STATUSES` and asserted in the lane's tests instead.
    """
    status = report.get("status")
    return 0 if isinstance(status, str) and status in SUCCESSFUL_TURN_STATUSES else 1


def read_lane_availability() -> AvailabilityIndexDays:
    """Read the vegetation lane's checksum-bound availability generation as this turn's authority.

    Uses the same verified read path the serving side does
    (`parquet_ops/availability_coverage.py` -> `availability_index.read_latest_availability`), so a
    missing, stale, malformed or checksum-invalid index fails closed here exactly as it does there
    (`layer-lanes.md` §4a). Returned as the concrete `AvailabilityIndexDays`, not the narrower
    `LaneAvailability` protocol, because `default_promotion_days` needs to enumerate every day's
    verdict to find the newest `published` one -- something a single `indexed_day(day)` lookup
    cannot do.
    """
    from agri_data_service.pipeline.parquet.availability_index import (  # noqa: PLC0415 - CLI-only
        BotoAvailabilityStorage,
        read_latest_availability,
    )
    from agri_data_service.pipeline.parquet.objectstore import availability_lane_root  # noqa: PLC0415 - CLI-only

    index = read_latest_availability(
        BotoAvailabilityStorage.from_settings(),
        lane_root=availability_lane_root(VEGETATION_PLANE_STREAM, PROMOTABLE_KIND),
    )
    return availability_days_at_base_rung(index)


async def main(argv: Sequence[str] | None = None) -> int:
    """Run one bounded promotion turn over explicitly named days and emit one terminal report.

    Exits NON-ZERO on a turn that promoted and confirmed nothing for a reason an operator can act
    on -- an all-absent window, or a mixed turn that promoted nothing -- naming it in the report, so
    a scheduled lane cannot succeed vacuously. A turn whose every day is simply not indexed yet is
    `waiting_for_writer` and exits 0: see `_promotion_report`.

    A DEFAULT-window turn (no `--day`, which is how the scheduled lane always runs) additionally
    refuses before it promotes anything when its ceiling is older than
    `lane_specs.vegetation_promotion_stale_ceiling_days()`, and reports the ceiling day and age
    either way. An operator naming `--day` explicitly is doing a bounded repair and is never gated
    on freshness: the days are the operator's, not this function's, to choose.
    """
    from agri_data_service.config import settings  # noqa: PLC0415 - CLI-only
    from agri_data_service.db.engine import local_source_loader_session  # noqa: PLC0415 - CLI-only
    from agri_data_service.execution.lane_specs import (  # noqa: PLC0415 - CLI-only, and the heavy lane table
        vegetation_promotion_stale_ceiling_days,
    )
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore  # noqa: PLC0415 - CLI-only

    arguments = parser().parse_args(argv)
    store = ObjectStore.from_settings()
    loader_database_url = settings.require_local_source_loader_database_url()
    stale_after_days = vegetation_promotion_stale_ceiling_days()
    today = datetime.now(UTC).date()
    #: `None` for an operator-named `--day` turn, which has no ceiling of this function's choosing.
    ceiling: PromotionCeiling | None = None
    try:
        availability = read_lane_availability()
        if not arguments.days:
            ceiling = promotion_ceiling(availability=availability, today=today)
        if ceiling is not None and ceiling.is_stale(stale_after_days=stale_after_days):
            stale = stale_ceiling_report(ceiling, stale_after_days=stale_after_days)
            emit({"event": "vegetation_promotion_stale_ceiling", "layer": VEGETATION_PLANE_STREAM, **stale})
            print(json.dumps(stale, sort_keys=True))
            return exit_code_for(stale)
        days = (
            tuple(sorted(date.fromisoformat(value) for value in arguments.days))
            if arguments.days
            else default_promotion_days(availability=availability, today=today, max_days=arguments.max_days)
        )
        async with local_source_loader_session(loader_database_url) as session:
            report = await run_vegetation_promotion(session, store, days=days, availability=availability)
    except Exception as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, sort_keys=True))
        return 1
    if ceiling is not None:
        report = {**report, **ceiling_fields(ceiling, stale_after_days=stale_after_days)}
    print(json.dumps(report, sort_keys=True))
    return exit_code_for(report)


__all__ = [
    "COMPLETED_STATUS",
    "DEFAULT_MAX_DAYS",
    "FAILING_TURN_STATUSES",
    "NO_DAYS_PROMOTED_STATUS",
    "PROMOTABLE_KIND",
    "REGISTRATION_REFUSED_STATUS",
    "STALE_CEILING_STATUS",
    "SUCCESSFUL_TURN_STATUSES",
    "TERMINAL_STATUSES",
    "WAITING_FOR_WRITER_STATUS",
    "AvailabilityIndexDays",
    "AvailabilityPartitionConflictError",
    "EmptyDayPartitionError",
    "EvaluationArtifactNotPromotableError",
    "IndexedDay",
    "IndexedDayState",
    "LaneAvailability",
    "PromotionCeiling",
    "VegetationDayPartitionKey",
    "VegetationPromotionOutcome",
    "VegetationPromotionReceipt",
    "availability_days_at_base_rung",
    "ceiling_fields",
    "day_partition_content_sha256",
    "default_promotion_days",
    "exit_code_for",
    "load_promotion_receipt",
    "main",
    "promote_vegetation_day_partition",
    "promotion_ceiling",
    "read_day_partition_cell_values",
    "read_lane_availability",
    "run_vegetation_promotion",
    "save_promotion_receipt",
    "stale_ceiling_report",
]


if __name__ == "__main__":  # pragma: no cover - process entry point
    import asyncio

    raise SystemExit(asyncio.run(main()))
