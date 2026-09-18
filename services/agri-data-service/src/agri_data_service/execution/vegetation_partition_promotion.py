"""Per-day-partition content-SHA scoped promotion for the governed vegetation NDVI plane.

BACKLOG P4, ARMED BUT SHADOW. `execution/vegetation_ndvi_plane.py::register_governed_forward_plane`
has had no caller anywhere in this service (grepped clean 2026-09-18) since it was written: the
Monte Carlo NDVI forecaster it exists to feed has never had a decided checksum scope, which is the
gap `.omc` memory `agri-vegetation-promotion-unarmed` and `execution/AGENTS.md` name. Owner decision
2026-09-18 closes that gap: promotion of one `layer=vegetation/kind=observed/year=/month=/day=`
partition is keyed by THAT PARTITION'S OWN content SHA, matching the availability index's
`generation=<content-sha>` convention (`conductor/code_styleguides/layer-lanes.md` §4a;
`pipeline/parquet/availability_index.py`), not the whole-corpus digest
`execution/vegetation_ndvi_plane._corpus_digest` computes for its own, unrelated Postgres purpose.

This module NEVER widens `register_governed_forward_plane`'s existing per-day-touched-cells
scoping (`execution/vegetation_ndvi_plane.py::register_governed_forward_plane`, its `cell_days`
argument); it only decides, per partition, whether that
call is owed at all. Reuses `foundation.canonical.sha256_digest`/`canonical_json` -- the same digest
routine the availability index binds into every generation key -- rather than a second one.

The AVAILABILITY INDEX is this turn's authority on what a day's outcome is, not a failed object read
(`layer-lanes.md` §4a). The promoter READS the index and never writes it: it consults
`availability/_LATEST.json` through the same verified path serving uses, and reports a day the index
states `governed_absence` with the index's own `absence_reason`. It therefore does not mint a
governed absence of its own -- an absence the index has not recorded is `not_yet_indexed`, and an
index that STILL claims a day the store cannot serve, after one re-read of the pointer, is a
conflict that fails the turn.

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
    RegistrationSummary,
    register_governed_forward_plane,
)
from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.direct.vegetation.forward import settled_through
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

RegisterForwardPlane = Callable[..., Awaitable[RegistrationSummary]]


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

    Raised only AFTER the turn re-reads the pointer once: a prune landing between the turn's
    availability snapshot and the object open is a race whose winning generation states the day
    itself, and paging an operator for it would make the benign case indistinguishable from the
    corrupt one (STYLE-REVIEW-W5 S4). The mid-read form of the same event already has its own name,
    `ConcurrentPrunePartitionError` (`pipeline/parquet/objectstore.py`).
    """

    def __init__(self, *, layer: str, day: date) -> None:
        super().__init__(
            f"{layer} availability index states {day.isoformat()} published, but the object store holds no part "
            f"file for it; one of the index and the partition is corrupt and an operator owes the reconciliation"
        )
        self.layer = layer
        self.day = day


#: What the lane's availability index says about one day, in the index's OWN vocabulary
#: (`pipeline/parquet/availability_documents.py::AvailabilityRow.terminal_state`), plus the state for
#: a day the index has no row for at all.
IndexedDayState = Literal["published", "governed_absence", "not_yet_indexed"]


@dataclass(frozen=True, slots=True)
class IndexedDay:
    """One day's availability verdict, carrying the index's own `absence_reason` when it has one."""

    state: IndexedDayState
    absence_reason: str | None = None


class LaneAvailability(Protocol):
    """The one question the promoter asks the availability index before it opens any object."""

    def indexed_day(self, day: date) -> IndexedDay: ...


@dataclass(frozen=True, slots=True)
class AvailabilityIndexDays:
    """A `LaneAvailability` backed by one already-verified availability generation."""

    verdicts: Mapping[date, IndexedDay]

    def indexed_day(self, day: date) -> IndexedDay:
        """Return the index's verdict, or `not_yet_indexed` for a day it carries no row for."""
        return self.verdicts.get(day, IndexedDay(state="not_yet_indexed"))


def availability_days_at_base_rung(index: AvailabilityIndex) -> AvailabilityIndexDays:
    """Project one availability index onto its BASE-rung row per day.

    The base rung is the rung the promoter itself reads (`LANE_BASE_ZOOM_TIER`), so it is the rung
    whose terminal state can be checked against what the store holds. A day the index carries only
    at coarser rungs is deliberately `not_yet_indexed` here: no row states the base rung's outcome.
    """
    verdicts = {
        row.day: IndexedDay(state=row.terminal_state, absence_reason=row.absence_reason)
        for row in index.rows
        if row.rung == LANE_BASE_ZOOM_TIER
    }
    return AvailabilityIndexDays(verdicts=verdicts)


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
    register: RegisterForwardPlane = register_governed_forward_plane,
) -> VegetationPromotionOutcome:
    """Promote exactly one day partition, keyed by its own content SHA, or report it unchanged.

    An unchanged partition (content SHA equal to `previous_receipt.content_sha256`) never calls the
    governed-plane register verb again -- a re-run of an already-promoted, byte-identical partition
    is idempotent by construction, never a second registration attempt against the same day. A
    changed partition re-promotes only itself: `register_governed_forward_plane` is already scoped
    to the touched cell-days it is given, so this verb hands it exactly this day's cells, never the
    whole corpus.

    Raises `EvaluationArtifactNotPromotableError` for any `kind` other than `observed`, before any
    checksum is computed or any register call is attempted.
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
    cell_days = tuple((cell_key, day) for cell_key, _value in cell_values)
    registration = await register(session, cutoff_day=day, cell_days=cell_days)
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

    Without `--day`, promotes the newest `--max-days` settled vegetation days (the same
    `settled_through` boundary `pipeline/direct/vegetation/forward.py` publishes against), so this
    lane is schedulable exactly like its sibling direct writers. Every day it touches is still
    idempotent against its own promotion receipt, so a wider `--max-days` never re-registers an
    unchanged partition.
    """
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--day", action="append", dest="days", default=None, help="one ISO date; repeatable")
    built.add_argument("--max-days", type=int, default=DEFAULT_MAX_DAYS)
    built.add_argument("--run-id", default=None)
    return built


def default_promotion_days(*, today: date, max_days: int) -> tuple[date, ...]:
    """Return the newest `max_days` settled vegetation days, oldest first, with no explicit `--day`."""
    if max_days < 1:
        raise ValueError("--max-days must be at least one")
    ceiling = settled_through(today=today)
    return tuple(sorted(ceiling - timedelta(days=offset) for offset in range(max_days)))


def _non_promotable_entry(day: date, indexed: IndexedDay) -> dict[str, object] | None:
    """The turn's result entry for a day the index does not state `published`, or `None` if it does."""
    if indexed.state == "governed_absence":
        return {
            "day": day.isoformat(),
            "layer": VEGETATION_PLANE_STREAM,
            "status": "absent",
            "reason": indexed.absence_reason or "governed_absence_without_recorded_reason",
        }
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
      turn opened with is RE-READ once before anything is raised: a pointer that advanced in between
      means a prune or retention pass landed inside the turn's window, which is a race retried from
      the winning generation (§4a), not corruption. Only a still-current pointer claiming a day the
      store cannot serve raises `AvailabilityPartitionConflictError` (STYLE-REVIEW-W4 B1/B2, W5 S4).

    `refresh_availability` is that re-read, injected so a test can state the winning generation
    without an object store; it defaults to this module's own `read_lane_availability`. It is called
    at most ONCE per turn, and only on the conflict path.

    A partition that exists and is empty still raises, because that is the writer misbehaving rather
    than the source having nothing.
    """
    # Imported here, like `main()`'s own store import: the object-store module carries the heavy
    # client dependencies this module otherwise only needs at CLI time.
    from agri_data_service.pipeline.parquet.objectstore import PartitionNotWrittenError  # noqa: PLC0415

    results: list[dict[str, object]] = []
    #: The winning generation, read lazily and at most once, for the whole turn.
    winning_availability: LaneAvailability | None = None
    has_reread_availability = False
    for day in days:
        skipped = _non_promotable_entry(day, availability.indexed_day(day))
        if skipped is not None:
            results.append(skipped)
            emit({"event": "vegetation_promotion_day", **results[-1]})
            continue
        try:
            cell_values = read_day_partition_cell_values(store, day)
        except PartitionNotWrittenError as conflict:
            if not has_reread_availability:
                has_reread_availability = True
                winning_availability = (refresh_availability or read_lane_availability)()
            reclassified = (
                None
                if winning_availability is None
                else _non_promotable_entry(day, winning_availability.indexed_day(day))
            )
            if reclassified is None:
                raise AvailabilityPartitionConflictError(layer=VEGETATION_PLANE_STREAM, day=day) from conflict
            # The index moved under the turn: classify per the FRESH row, which is the winning
            # generation's own statement about this day, and never the snapshot's stale one.
            results.append({**reclassified, "reclassified": "availability_index_advanced_during_turn"})
            emit({"event": "vegetation_promotion_day", **results[-1]})
            continue
        if not cell_values:
            raise EmptyDayPartitionError(layer=VEGETATION_PLANE_STREAM, day=day)
        previous_receipt = load_promotion_receipt(store, day=day)
        outcome = await promote_vegetation_day_partition(
            session,
            day=day,
            kind=PROMOTABLE_KIND,
            cell_values=cell_values,
            previous_receipt=previous_receipt,
            now=now,
        )
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
    if report["status"] == "waiting_for_writer":
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


#: The terminal statuses a turn may end on. `waiting_for_writer` is deliberately NOT `completed`:
#: it exits 0, but a reader must be able to tell a turn that promoted from one that had nothing to
#: promote yet.
SUCCESSFUL_TURN_STATUSES: Final[frozenset[str]] = frozenset({"completed", "waiting_for_writer"})


def _promotion_report(results: list[dict[str, object]]) -> dict[str, object]:
    """Render the terminal report, naming WHY a turn that promoted nothing ended as it did.

    Three terminal statuses, because the turn has three genuinely different outcomes:

    - `completed` -- at least one day was promoted or confirmed unchanged. Exit 0.
    - `waiting_for_writer` -- EVERY day evaluated is `not_yet_indexed`. Exit 0, named, logged once.
      This is the steady state of the intended configuration, not a failure: the lane's forward
      writer has not started (module docstring), `--max-days` defaults to 1, so a scheduled turn
      evaluates exactly one unpublished day. Failing it would page every turn, indefinitely, for a
      lane that is behaving exactly as configured -- and a status nobody can act on is noise, not
      the loudness `engineering-principles.md` §2 asks for (STYLE-REVIEW-W5 S3; the recorded owner
      ruling that bounded catch-up turns exit 0, `.omc` memory `plantgeo-owner-decisions-2026-09-04`).
    - `no_days_promoted` -- everything else: every day a governed absence, or a mixed turn that
      promoted nothing. Exit non-zero, which is what stops a lane reporting success forever against
      days that do not exist (STYLE-REVIEW-W4 B1).

    A `not_yet_indexed` day is still neutral WITHIN a turn: it counts as neither progress nor
    absence, so it cannot turn a turn that DID promote into a failure.
    """
    absent_days = [str(entry["day"]) for entry in results if entry["status"] == "absent"]
    not_yet_indexed_days = [str(entry["day"]) for entry in results if entry["status"] == "not_yet_indexed"]
    progressed = [entry for entry in results if entry["status"] in ("promoted", "unchanged")]
    waiting_for_writer = bool(results) and len(not_yet_indexed_days) == len(results)
    if progressed:
        status = "completed"
    elif waiting_for_writer:
        status = "waiting_for_writer"
    else:
        status = "no_days_promoted"
    report: dict[str, object] = {
        "status": status,
        "days": results,
        # Surfaced at the top level so a scheduled turn's report states its absences without the
        # reader walking every day entry.
        "absent_days": absent_days,
        "not_yet_indexed_days": not_yet_indexed_days,
    }
    if status == "waiting_for_writer":
        report["reason"] = "forward_writer_has_indexed_none_of_these_days"
    elif status == "no_days_promoted":
        report["reason"] = (
            "all_days_absent" if results and len(absent_days) == len(results) else "no_indexed_day_promoted"
        )
    return report


def exit_code_for(report: Mapping[str, object]) -> int:
    """Map one terminal report onto the process exit code.

    Zero for a turn that promoted (`completed`) and for one that had nothing to promote yet
    (`waiting_for_writer`, named in the report and distinguishable from the first). Non-zero for a
    turn that made no progress for any OTHER reason -- an all-absent window, or a mixed turn that
    promoted nothing -- with that reason already in the report.
    """
    status = report.get("status")
    return 0 if isinstance(status, str) and status in SUCCESSFUL_TURN_STATUSES else 1


def read_lane_availability() -> LaneAvailability:
    """Read the vegetation lane's checksum-bound availability generation as this turn's authority.

    Uses the same verified read path the serving side does
    (`parquet_ops/availability_coverage.py` -> `availability_index.read_latest_availability`), so a
    missing, stale, malformed or checksum-invalid index fails closed here exactly as it does there
    (`layer-lanes.md` §4a).
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
    """
    from agri_data_service.config import settings  # noqa: PLC0415 - CLI-only
    from agri_data_service.db.engine import local_source_loader_session  # noqa: PLC0415 - CLI-only
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore  # noqa: PLC0415 - CLI-only

    arguments = parser().parse_args(argv)
    days = (
        tuple(sorted(date.fromisoformat(value) for value in arguments.days))
        if arguments.days
        else default_promotion_days(today=datetime.now(UTC).date(), max_days=arguments.max_days)
    )
    store = ObjectStore.from_settings()
    loader_database_url = settings.require_local_source_loader_database_url()
    try:
        availability = read_lane_availability()
        async with local_source_loader_session(loader_database_url) as session:
            report = await run_vegetation_promotion(session, store, days=days, availability=availability)
    except Exception as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}"}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return exit_code_for(report)


__all__ = [
    "DEFAULT_MAX_DAYS",
    "PROMOTABLE_KIND",
    "SUCCESSFUL_TURN_STATUSES",
    "AvailabilityIndexDays",
    "AvailabilityPartitionConflictError",
    "EmptyDayPartitionError",
    "EvaluationArtifactNotPromotableError",
    "IndexedDay",
    "IndexedDayState",
    "LaneAvailability",
    "VegetationDayPartitionKey",
    "VegetationPromotionOutcome",
    "VegetationPromotionReceipt",
    "availability_days_at_base_rung",
    "day_partition_content_sha256",
    "default_promotion_days",
    "exit_code_for",
    "load_promotion_receipt",
    "main",
    "promote_vegetation_day_partition",
    "read_day_partition_cell_values",
    "read_lane_availability",
    "run_vegetation_promotion",
    "save_promotion_receipt",
]


if __name__ == "__main__":  # pragma: no cover - process entry point
    import asyncio

    raise SystemExit(asyncio.run(main()))
