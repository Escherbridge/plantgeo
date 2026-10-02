"""The write side of a turn: the S11 rewrite rules, the writer port, and its `pipeline/parquet` binding.

`decide_rewrite` is the one statement of S11. `ObjectStoreLaneWriter` delegates every write to
`pipeline/parquet/*` (lane-day lock, base rung, tiers, completion marker, availability generation
and pointer) and cannot be constructed without a write permit, which a `--compare` turn never
holds. See `pipeline/runner/AGENTS.md` "Writer".
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final, Literal, Protocol

from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.parquet.availability_extension import (
    AvailabilityExtensionTally,
    retry_pending_availability,
)
from agri_data_service.pipeline.parquet.derivation import derive_and_write_day_tiers, govern_day_absent
from agri_data_service.pipeline.parquet.gap_fill import (
    _lane_day_lock_key,
    fill_one_lane_day,
    postgres_lane_day_lock,
    repair_one_lane_day,
    unlocked_lane_day,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY, normalise_export_outcome
from agri_data_service.pipeline.runner.census import CONFIG_LANE_KIND, FULL_LADDER_TIERS
from agri_data_service.pipeline.runner.clock import utc_today
from agri_data_service.pipeline.runner.completeness import SourceCompleteness
from agri_data_service.pipeline.runner.contract import Absent, Written

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    import pyarrow as pa  # type: ignore[import-untyped]
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.lane_config.models import PartialDayPolicy
    from agri_data_service.foundation.parquet.paths import PartitionDayStatus
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.gap_fill import LaneDayLock, TierDeriver
    from agri_data_service.pipeline.parquet.lane_registry import LaneAdapter, LaneRegistration, LaneRunResult
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore
    from agri_data_service.pipeline.runner.clock import TurnClock
    from agri_data_service.pipeline.runner.contract import Settlement
    from agri_data_service.pipeline.runner.receipts import DayReceipt, TurnReceipts

RewriteReason = Literal[
    "new",
    "retract_disproven_absence",
    "digest_changed",
    "more_units",
    "no_receipt",
    "republish",
    "absent",
    "digest_unchanged",
    "no_more_units",
    "fewer_units",
    "coverage_unproven",
    "coverage_lost",
    "source_completed",
    "absence_unchanged",
    "data_never_retracted_by_absence",
]
_WRITING_REASONS: Final[frozenset[RewriteReason]] = frozenset(
    {
        "new",
        "retract_disproven_absence",
        "digest_changed",
        "more_units",
        "no_receipt",
        "republish",
        "absent",
        "source_completed",
    }
)


class CompareModeWriteError(RuntimeError):
    """A writer was asked for under a `--compare` turn; compare builds rows and diffs, it never writes."""


class LaneWriteError(RuntimeError):
    """A stream-day did not publish (blocked, or refused by the store); the turn stops (exit 70)."""


class LaneDayContendedError(LaneWriteError):
    """Another run holds the stream-day's lane-day lock: that day is `contended` and the turn goes on (M4)."""


class LadderRepairError(LaneWriteError):
    """A published base rung's coarse rungs could not be derived this turn: that day is `ladder_owed` (H1)."""


class UnregisteredStreamError(RuntimeError):
    """A config stream has no `LaneRegistration` (the S18 mirror row is missing): a configuration error (exit 78)."""


@dataclass(frozen=True, slots=True)
class RewriteDecision:
    """Whether one stream-day is (re)written, and the S11 word for why."""

    reason: RewriteReason

    @property
    def writes(self) -> bool:
        """True when the rule says write."""
        return self.reason in _WRITING_REASONS


def _is_partial(receipt: DayReceipt | None) -> bool:
    return (
        receipt is not None
        and receipt.present_units is not None
        and receipt.expected_units is not None
        and receipt.present_units < receipt.expected_units
    )


def _coverage_refusal(receipt: DayReceipt | None, settlement: Written) -> RewriteDecision | None:
    if receipt is not None and receipt.publication_state == "pending" and settlement.partial:
        return RewriteDecision("coverage_unproven")
    if receipt is not None and settlement.present_units < (receipt.present_units or 0):
        return RewriteDecision("fewer_units")
    previous_ids = None if receipt is None else receipt.present_unit_ids
    current_ids = settlement.present_unit_ids
    if previous_ids is not None:
        if current_ids is None or not previous_ids <= current_ids:
            return RewriteDecision("coverage_lost")
    elif settlement.partial:
        return RewriteDecision("coverage_unproven")
    return None


def decide_rewrite(  # noqa: PLR0911, PLR0913 - one return per S11 row; the census, receipt, answer and policy are distinct
    *,
    status: PartitionDayStatus,
    receipt: DayReceipt | None,
    settlement: Settlement,
    source_digest: str | None,
    partial_day: PartialDayPolicy,
    force: bool = False,
    source_owed: bool = False,
) -> RewriteDecision:
    """S11: write an unwritten day; rewrite a settled day on a digest change, a partial recheck day only on more units.

    A governed absence never overwrites data (fail-closed), and data always retracts a disproven
    absence. A data day with no runner receipt (written before cut-over) is rewritten once, which
    is how it gains one. A `write_and_recheck` answer with fewer units than the written day never
    replaces it: a tile that failed this turn must not erase the gauges it served last turn.
    """
    if isinstance(settlement, Absent):
        if status == "data":
            return RewriteDecision("data_never_retracted_by_absence")
        return RewriteDecision("absence_unchanged" if status == "absent" else "absent")
    if not isinstance(settlement, Written):
        raise ValueError("an unsettled day is never offered to the rewrite rules")
    if status in {"missing", "incomplete"}:
        return RewriteDecision("new")
    if status == "absent":
        return RewriteDecision("retract_disproven_absence")
    if force:
        return RewriteDecision("republish")
    if partial_day == "write_and_recheck":
        refusal = _coverage_refusal(receipt, settlement)
        if refusal is not None:
            return refusal
    if receipt is None:
        return RewriteDecision("no_receipt")
    if receipt.publication_state == "pending":
        return RewriteDecision("coverage_unproven" if settlement.partial else "source_completed")
    if partial_day == "write_and_recheck" and _is_partial(receipt):
        previous = receipt.present_units or 0
        return RewriteDecision("more_units" if settlement.present_units > previous else "no_more_units")
    if source_owed and not settlement.partial:
        return RewriteDecision("source_completed")
    return RewriteDecision("digest_changed" if source_digest != receipt.source_digest else "digest_unchanged")


@dataclass(frozen=True, slots=True)
class WritePermit:
    """Proof the turn may write. `--compare` turns get one that refuses, so no writer can be built."""

    allows_writes: bool

    @classmethod
    def for_turn(cls, *, compare: bool) -> WritePermit:
        """The permit a turn of this kind holds."""
        return cls(allows_writes=not compare)


@dataclass(frozen=True, slots=True)
class WriteResult:
    """What one write put in the store."""

    partitions: int = 0
    rows: int = 0
    bytes_written: int = 0


class LaneWriter(Protocol):
    """The turn's write port; `ObjectStoreLaneWriter` is the production binding."""

    async def retry_owed_availability(self, streams: Sequence[str]) -> int: ...

    async def write_day(
        self, stream: str, day: date, table: pa.Table, receipt: DayReceipt, *, availability: bool
    ) -> WriteResult: ...

    async def write_absence(self, stream: str, day: date, absence: Absent, receipt: DayReceipt) -> WriteResult: ...

    async def write_receipt(self, receipt: DayReceipt) -> None: ...

    async def prune(self, stream: str, day: date) -> WriteResult: ...

    async def repair_ladder(self, stream: str, day: date) -> WriteResult: ...

    def publication_summary(self) -> Mapping[str, int]: ...


def _retract_absence_ladder(store: ObjectStore, stream: str, day: date) -> None:
    """A data answer disproves an earlier absence at every rung it was propagated to (soil's rule)."""
    for tier in ZOOM_TIERS:
        if store.absence_exists(stream, CONFIG_LANE_KIND, tier, day):
            store.clear_absence_marker(stream, CONFIG_LANE_KIND, tier, day)


@dataclass(slots=True)
class _TableAdapter:
    """A `LaneAdapter` that writes one prepared base-rung table (or one governed absence) for its day."""

    stream: str
    day: date
    table: pa.Table | None = None
    absence: GovernedAbsence | None = None

    async def __call__(self, session: AsyncSession, store: ObjectStore, *, day: date, run_id: str) -> LaneRunResult:  # noqa: ARG002 - the LaneAdapter shape
        """Write the prepared answer; the caller holds the lane-day lock."""
        if day != self.day:
            raise LaneWriteError(
                f"{self.stream}: the adapter for {self.day.isoformat()} was asked for {day.isoformat()}"
            )
        if self.absence is not None:
            return normalise_export_outcome(
                govern_day_absent(store, self.absence, layer=self.stream, kind=CONFIG_LANE_KIND, day=day)
            )
        if self.table is None:
            raise LaneWriteError(f"{self.stream} {day.isoformat()}: neither rows nor an absence to write")
        _retract_absence_ladder(store, self.stream, day)
        return normalise_export_outcome(
            store.write_partition(
                self.table, layer=self.stream, kind=CONFIG_LANE_KIND, zoom=LANE_BASE_ZOOM_TIER, day=day
            )
        )


@dataclass(slots=True)
class ObjectStoreLaneWriter:
    """The production writer: every write goes through `pipeline/parquet/gap_fill.py::fill_one_lane_day`."""

    permit: WritePermit
    session: AsyncSession
    store: ObjectStore
    availability_storage: AvailabilityStorage
    receipts: TurnReceipts
    run_id: str
    clock: TurnClock
    #: `postgres_lane_day_lock` in production; `gap_fill.py::unlocked_lane_day` is the test seam.
    lane_day_lock: LaneDayLock = postgres_lane_day_lock
    #: The coarse-rung writer; `gap_fill.py::no_derived_tiers` is the test seam when the ladder is not the subject.
    derive_tiers: TierDeriver = derive_and_write_day_tiers
    #: `LANE_REGISTRY` in production, the S18 mirror included; a test registers a fixture stream here.
    registrations: Mapping[str, LaneRegistration] = field(default_factory=lambda: LANE_REGISTRY)
    tally: AvailabilityExtensionTally = field(default_factory=AvailabilityExtensionTally)
    #: Streams whose owed-claim retry raised this turn; the retry never blocks a write (CA20).
    availability_retry_failures: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.permit.allows_writes:
            raise CompareModeWriteError("a --compare turn builds rows and diffs them; it cannot construct a writer")

    async def retry_owed_availability(self, streams: Sequence[str]) -> int:
        """CA20: retry each stream's owed `availability/pending/` claims first; a fault never blocks a write.

        The legacy `_retry_owed_availability` contract (climate, soil, vegetation, water gauges): a
        bounded batch per stream (`DEFAULT_MAX_RETRIES_PER_LANE`), outcomes folded into the turn's
        tally, and a raising retry recorded rather than raised.
        """
        retried = 0
        for stream in streams:
            try:
                outcomes = await retry_pending_availability(
                    self.session,
                    self.store,
                    lane=stream,
                    kind=CONFIG_LANE_KIND,
                    availability=self.availability_storage,
                    now=self.clock.now,
                )
            except Exception:  # an owed index entry may never stop a lane from publishing
                self.availability_retry_failures.append(stream)
                await self.session.rollback()
                continue
            for outcome in outcomes:
                self.tally.record(outcome)
            retried += len(outcomes)
        return retried

    async def write_day(
        self, stream: str, day: date, table: pa.Table, receipt: DayReceipt, *, availability: bool
    ) -> WriteResult:
        """Write one stream-day's base rung, tiers and marker under its lock, then its turn receipt."""
        if (receipt.stream, receipt.day) != (stream, day):
            raise LaneWriteError("the turn receipt does not name the stream-day being written")
        registration = self._registration(stream)
        async with self.lane_day_lock(self.session, _lane_day_lock_key(registration, day)) as granted:
            if not granted:
                raise LaneDayContendedError(f"{stream} {day.isoformat()}: another run holds this lane-day")
            if receipt.expected_unit_ids is not None and receipt.present_unit_ids is not None:
                current = await asyncio.to_thread(self.receipts.read, stream, day)
                answer = Written(
                    expected_units=len(receipt.expected_unit_ids),
                    present_units=len(receipt.present_unit_ids),
                    expected_unit_ids=receipt.expected_unit_ids,
                    present_unit_ids=receipt.present_unit_ids,
                )
                if current is not None and _coverage_refusal(current, answer) is not None:
                    raise LaneDayContendedError(
                        f"{stream} {day.isoformat()}: published coverage changed before the lock"
                    )
            completeness = SourceCompleteness(self.receipts.storage)
            await asyncio.to_thread(completeness.invalidate, stream, day)
            await asyncio.to_thread(self.receipts.write, replace(receipt, publication_state="pending"))
            result = await self._fill(
                stream,
                day,
                _TableAdapter(stream=stream, day=day, table=table),
                availability=availability,
                already_locked=True,
            )
            completed = replace(receipt, publication_state="complete")
            await asyncio.to_thread(self.receipts.write, completed)
            await asyncio.to_thread(completeness.confirm, completed)
            return result

    async def write_absence(self, stream: str, day: date, absence: Absent, receipt: DayReceipt) -> WriteResult:
        """Govern one stream-day as absent across the ladder, citing its proof, then write its receipt."""
        governed = GovernedAbsence(
            reason=absence.reason,
            upstream_response=json.dumps(
                {"proof": absence.proof, "source_digest": receipt.source_digest}, sort_keys=True
            ),
            recorded_at=self.clock.now(),
            run_id=self.run_id,
        )
        result = await self._fill(
            stream, day, _TableAdapter(stream=stream, day=day, absence=governed), availability=True
        )
        await asyncio.to_thread(self.receipts.write, receipt)
        return result

    async def write_receipt(self, receipt: DayReceipt) -> None:
        """Replace one stream-day's turn receipt alone: a transform whose rebuilt output did not change."""
        await asyncio.to_thread(self.receipts.write, receipt)

    async def prune(self, stream: str, day: date) -> WriteResult:
        """Retract every rung of one superseded provisional stream-day, under its lane-day lock (§4.6)."""
        registration = self._registration(stream)
        async with self.lane_day_lock(self.session, _lane_day_lock_key(registration, day)) as granted:
            if not granted:
                raise LaneDayContendedError(
                    f"{stream} {day.isoformat()}: another run holds the lane-day; prune deferred"
                )
            removed = 0
            for tier in reversed(FULL_LADDER_TIERS):  # coarse rungs first, the base last
                pruned = self.store.retract_partition_tier(stream, CONFIG_LANE_KIND, tier, day)
                if pruned.failures:
                    raise LaneWriteError(f"{stream} {day.isoformat()} z{tier}: prune failed: {pruned.failures[0]}")
                removed += len(pruned.removed)
        return WriteResult(partitions=removed)

    async def repair_ladder(self, stream: str, day: date) -> WriteResult:
        """Derive a published day's missing coarse rungs from its base rung, under the lane-day lock (H1).

        The generic ladder repair (`gap_fill_repair.py::repair_one_lane_day`) touches no adapter and no
        source; it also claims the repaired day for the availability index.
        """
        repair = await repair_one_lane_day(
            self.session,
            self.store,
            self._registration(stream),
            day=day,
            run_id=self.run_id,
            now=self.clock.now,
            lane_day_lock=self.lane_day_lock,
            today=utc_today(self.clock),
            derive_tiers=self.derive_tiers,
            availability_storage=self.availability_storage,
        )
        if repair.availability is not None:
            self.tally.record(repair.availability)
        if repair.outcome == "contended":
            raise LaneDayContendedError(f"{stream} {day.isoformat()}: {repair.detail}")
        if repair.outcome != "written":
            raise LadderRepairError(f"{stream} {day.isoformat()}: outcome={repair.outcome}, detail={repair.detail}")
        return WriteResult(partitions=repair.parts, rows=repair.rows, bytes_written=repair.written_bytes)

    def publication_summary(self) -> Mapping[str, int]:
        """The availability tally, in the keys `execution/job_executor_service.py` reads as publication debt."""
        return {name: value for name, value in self.tally.to_summary().items() if isinstance(value, int)}

    def _registration(self, stream: str) -> LaneRegistration:
        registration = self.registrations.get(stream)
        if registration is None:
            raise UnregisteredStreamError(
                f"stream {stream!r} has no LaneRegistration; its S18 mirror row "
                "(pipeline/parquet/config_stream_registrations.py) is missing"
            )
        return registration

    async def _fill(
        self, stream: str, day: date, adapter: LaneAdapter, *, availability: bool, already_locked: bool = False
    ) -> WriteResult:
        registration = self._registration(stream)
        outcome, parts, rows, written_bytes, detail = await fill_one_lane_day(
            self.session,
            self.store,
            replace(registration, adapter=adapter),
            day=day,
            run_id=self.run_id,
            now=self.clock.now,
            today=utc_today(self.clock),
            lane_day_lock=unlocked_lane_day if already_locked else self.lane_day_lock,
            derive_tiers=self.derive_tiers,
            availability_storage=self.availability_storage if availability else None,
            availability_tally=self.tally,
        )
        await self.session.rollback()
        if outcome == "contended":
            raise LaneDayContendedError(f"{stream} {day.isoformat()} was not written: {detail}")
        if outcome not in {"written", "absent"}:
            raise LaneWriteError(f"{stream} {day.isoformat()} did not publish: outcome={outcome}, detail={detail}")
        return WriteResult(partitions=parts, rows=rows, bytes_written=written_bytes)


__all__ = [
    "CompareModeWriteError",
    "LadderRepairError",
    "LaneDayContendedError",
    "LaneWriteError",
    "LaneWriter",
    "ObjectStoreLaneWriter",
    "RewriteDecision",
    "RewriteReason",
    "UnregisteredStreamError",
    "WritePermit",
    "WriteResult",
    "decide_rewrite",
]
