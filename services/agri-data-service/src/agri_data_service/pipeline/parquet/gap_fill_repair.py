"""Re-deriving a ladder whose base parts are already correct, without touching a lane adapter.

Rationale: see `AGENTS.md` in this directory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agri_data_service.db.vegetation_publication import (
    try_postgres_vegetation_publication_barrier,
    unlocked_vegetation_publication_barrier,
)
from agri_data_service.pipeline.parquet.availability_extension import (
    AvailabilityExtensionOutcome,
    RepairedBaseRung,
    claim_repaired_lane_day,
)
from agri_data_service.pipeline.parquet.availability_index import EvidenceReceipt
from agri_data_service.pipeline.parquet.derivation import DerivationResult, derive_and_write_day_tiers
from agri_data_service.pipeline.parquet.gap_fill_contract import (
    GAP_FILL_PARTITION_KIND,
    GAP_FILL_ZOOM_TIER,
    GapFillContractError,
    LadderRepairOutcome,
    _append_note,
    _end_lane_day_transaction,
    _ladder_schema_mismatch,
    _lane_day_lock_key,
)
from agri_data_service.pipeline.parquet.gap_fill_day import _retract_derived_absences
from agri_data_service.pipeline.parquet.lane_ceiling import allowed_source_ceiling
from agri_data_service.pipeline.parquet.objectstore import (
    GovernedAbsenceConflictError,
    availability_lane_root,
)
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_STREAM

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date, datetime

    import pyarrow as pa  # type: ignore[import-untyped]
    from duckdb import DuckDBPyConnection
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.gap_fill_contract import (
        LaneDayLock,
        TierDeriver,
        VegetationPublicationBarrier,
    )
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, PartitionRead, WrittenObjectLedger


async def repair_one_lane_day(  # noqa: PLR0913 - one caller-supplied coordinate per arg, none foldable
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
    lane_day_lock: LaneDayLock,
    today: date | None = None,
    vegetation_publication_barrier: VegetationPublicationBarrier = try_postgres_vegetation_publication_barrier,
    derive_tiers: TierDeriver = derive_and_write_day_tiers,
    connection: DuckDBPyConnection | None = None,
    availability_storage: AvailabilityStorage | None = None,
) -> LadderRepairOutcome:
    """Re-derive one published day's coarse rungs from its base rung, then CLAIM the day for the index.

    THE CLAIM IS NOT OPTIONAL BOOKKEEPING. A repair rewrites three of the day's four rungs, so their
    receipts change; without a claim the day stays complete at every rung, is never re-selected, and
    sits outside the availability generation for good while the tick reports `repaired: 1`. See
    `AGENTS.md`, "A repaired day joins the index through a claim", for the lock, the read-back, the
    stranded-absence retraction and the session discipline.
    """
    barrier = (
        vegetation_publication_barrier
        if lane.slug == VEGETATION_PLANE_STREAM
        else unlocked_vegetation_publication_barrier
    )
    notes: list[str] = []
    try:
        async with barrier(session) as publication_granted:
            if publication_granted is False:
                return LadderRepairOutcome(
                    "contended",
                    0,
                    0,
                    0,
                    f"{day.isoformat()}: exact vegetation audit holds the publication barrier; derivation deferred",
                )
            async with lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted:
                if not granted:
                    return LadderRepairOutcome(
                        "contended",
                        0,
                        0,
                        0,
                        f"{day.isoformat()}: another run holds this lane-day, so its coarse rungs were left alone "
                        "rather than derived beside a base rung being rewritten; a later turn will take it",
                    )
                # ONE READ OF THE BASE RUNG SERVES BOTH HALVES: the derivation gets the rows and the
                # claim gets the key and digest of every part it cites. Letting the deriver read the
                # day again, or hashing the parts afterwards, would download it twice.
                #
                # ONLY WHEN A CLAIM IS OWED. With no availability storage wired there is nothing to
                # cite, so the read stays where it always was -- inside the deriver -- and this path
                # costs exactly what it did before.
                base = (
                    store.read_partition_with_receipts(lane.slug, GAP_FILL_PARTITION_KIND, GAP_FILL_ZOOM_TIER, day)
                    if availability_storage is not None
                    else None
                )
                with store.recording_written_objects() as written:
                    derived = _derive_repaired_rungs(
                        store,
                        lane,
                        day=day,
                        run_id=run_id,
                        now=now,
                        derive_tiers=derive_tiers,
                        connection=connection,
                        base_table=None if base is None else base.table,
                        notes=notes,
                    )
                # THE CLAIM IS WRITTEN UNDER THE SAME LOCK THE RUNGS WERE, exactly as the export path
                # extends availability inside its own lock. Written after the lock released, a
                # concurrent re-export could replace the base parts between the derivation and the
                # claim, and the claim would then name receipts that no longer exist -- a day that
                # spins on verification failure every tick instead of joining the index.
                extension = _claim_repaired_day(
                    store,
                    lane,
                    day=day,
                    run_id=run_id,
                    now=now,
                    today=today,
                    base=base,
                    written=written,
                    availability_storage=availability_storage,
                )
    except Exception as error:
        detail = _append_note(
            "; ".join(notes) or None,
            f"{day.isoformat()}: the coarse rungs could not be derived from the published base rung, so this "
            f"day stays visible only at z{GAP_FILL_ZOOM_TIER}. A base rung that no longer matches its lane's "
            f"schema reads exactly like this and needs retracting and re-exporting, not re-deriving: "
            f"{type(error).__name__}: {error}",
        )
        if _ladder_schema_mismatch(error):
            # UNREPAIRABLE BY THIS DRIVER -- mirroring `_static_lane_census`'s stranded-version prose,
            # which says a version cannot be repaired by this driver and retracting is an admin act. Reported
            # `blocked`, never `raised`: `blocked` is failure that must NOT stop the lane
            # (`FAILING_LANE_OUTCOMES`), and this is the NEWEST-known fact about ONE day -- stopping the
            # lane's repairs over it would starve every other ladder gap behind it, forever.
            return LadderRepairOutcome(
                "blocked",
                0,
                0,
                0,
                _append_note(
                    detail,
                    f"{day.isoformat()}: this day's coarse rungs cannot be repaired by this driver -- the "
                    "base rung's columns no longer match the lane's current tier derivation, which is a data "
                    "problem (a stale base-rung export) and not a transient one. Retracting and re-exporting "
                    "the base rung is an admin action; re-ticking this driver will report the identical "
                    "mismatch every time",
                ),
            )
        # RETRYABLE: nothing above diagnosed this as permanent. The next tick's census reselects the
        # same day from the same unchanged listing and may simply succeed, so this is reported apart
        # from the admin-needed case above rather than folded into it -- see `_record_repair_outcome`.
        return LadderRepairOutcome("raised", 0, 0, 0, detail)
    finally:
        await _end_lane_day_transaction(session)
    notes.extend(derived.notes)
    if derived.tiers:
        rungs = ", ".join(
            f"z{report.tier} {report.row_count} rows in {report.part_count} part(s)" for report in derived.tiers
        )
        notes.append(f"{day.isoformat()}: derived {rungs}")
    if extension is not None:
        notes.append(extension.note)
    return LadderRepairOutcome(
        "written",
        derived.part_count,
        derived.row_count,
        derived.byte_count,
        "; ".join(notes) or None,
        emptied_tiers=tuple(derived.emptied),
        availability=extension,
    )


def _derive_repaired_rungs(  # noqa: PLR0913 - one coordinate of the day being re-derived per arg
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
    derive_tiers: TierDeriver,
    connection: DuckDBPyConnection | None,
    base_table: pa.Table | None,
    notes: list[str],
) -> DerivationResult:
    """Derive the coarse rungs, healing a STRANDED coarse absence once before giving up on the day.

    The base rung demonstrably holds rows -- the ladder census selected this day because it does --
    so a coarse rung still claiming the day is governed as absent is the same stranded state
    `_finalize_written_day` retracts on the export path, and it is retracted the same way here. Left
    to the blanket guard it returned `raised` on every tick forever, because a direct-writer day
    never reaches the export path that knows how to heal it. Exactly ONE retry: a second conflict is
    a claim that could not be cleared, which is an admin's problem and not a loop's.
    """
    try:
        return derive_tiers(
            store,
            layer=lane.slug,
            kind=GAP_FILL_PARTITION_KIND,
            day=day,
            run_id=run_id,
            now=now,
            connection=connection,
            base_table=base_table,
        )
    except GovernedAbsenceConflictError as stranded:
        notes.append(_retract_derived_absences(store, lane, day=day, conflict=stranded))
        return derive_tiers(
            store,
            layer=lane.slug,
            kind=GAP_FILL_PARTITION_KIND,
            day=day,
            run_id=run_id,
            now=now,
            connection=connection,
            base_table=base_table,
        )


def _claim_repaired_day(  # noqa: PLR0913 - one coordinate of the repaired day per arg
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
    today: date | None,
    base: PartitionRead | None,
    written: WrittenObjectLedger,
    availability_storage: AvailabilityStorage | None,
) -> AvailabilityExtensionOutcome | None:
    """Record that the repaired day owes a re-index, or say why it could not be claimed. Never raises."""
    if availability_storage is None or base is None:
        return None
    try:
        marker = store.read_completion_receipt(lane.slug, GAP_FILL_PARTITION_KIND, GAP_FILL_ZOOM_TIER, day)
        if marker is None:
            raise GapFillContractError(
                f"{lane.slug} {day.isoformat()}: the base rung holds parts but no completion marker, so the "
                "repaired day has no receipt an availability row could bind"
            )
        published_at = now()
        return claim_repaired_lane_day(
            store,
            lane=lane.slug,
            kind=GAP_FILL_PARTITION_KIND,
            day=day,
            written=written,
            base_rung=RepairedBaseRung(
                rung=GAP_FILL_ZOOM_TIER,
                # SORTED BY OBJECT KEY, NOT BY PART INDEX. `read_partition_with_receipts` hands its
                # parts back in NUMERIC `part_index` order because that is the order the rows must be
                # concatenated in, while `availability_index._validate_data_receipt_collection`
                # demands LEXICOGRAPHIC key order. `paths.partition_path` mints unpadded names, so
                # the two agree only up to `part-9` and diverge the moment `part-10` exists.
                #
                # THE CLAIM ITSELF NEVER NOTICED. Nothing on this path validates receipt order -- not
                # `_RungObjects`, not `_rung_wire` -- so an unsorted claim was written, returned
                # `retry_owed`, and died one tick later in `_prepare_day`, where `TerminalEvidence`
                # refuses it. `_index_claimed_day` catches that `ValueError`, CLEARS the claim and
                # reports `evidence_unbuildable`, so the day was dropped ONCE and permanently: it is
                # complete at every rung, no census reselects it, and it never rejoins the index --
                # the "permanent and green" loss the claim exists to stop, arriving through the claim.
                # `objectstore.WrittenObjectLedger.parts_for` already sorts for the same reason.
                data_receipts=tuple(
                    EvidenceReceipt(key=part.relative_path, sha256=part.sha256)
                    for part in sorted(base.parts, key=lambda read: read.relative_path)
                ),
                completion_receipt=EvidenceReceipt(key=marker.relative_path, sha256=marker.sha256),
                row_count=marker.completion.row_count,
                part_count=marker.completion.part_count,
            ),
            run_id=run_id,
            # THE LANE'S OWN CEILING, NOT THE DAY, for the reason `_extend_availability_for_result`
            # gives: a day that declared itself its own ceiling closes every lane at its last row.
            source_ceiling=max(allowed_source_ceiling(lane, today=today or day), day),
            published_at=published_at,
        )
    except Exception as error:  # the rungs are written; an unclaimable day may never undo that
        return AvailabilityExtensionOutcome(
            state="retry_claim_failed",
            lane_root=availability_lane_root(lane.slug, GAP_FILL_PARTITION_KIND),
            day=day,
            reason=(
                f"the coarse rungs were re-derived but no availability claim could be recorded, so the day's "
                f"new receipts stay outside the index: {type(error).__name__}: {error}"
            ),
            error_kind="repair_claim_unwritable",
        )
