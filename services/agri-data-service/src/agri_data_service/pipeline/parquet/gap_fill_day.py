"""Filling ONE lane-day: export, prune, mark, and the governed absence that stands in for no rows.

Rationale: see `AGENTS.md` in this directory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from agri_data_service.db.vegetation_publication import (
    try_postgres_vegetation_publication_barrier,
    unlocked_vegetation_publication_barrier,
)
from agri_data_service.foundation.parquet.completion import PartitionCompletion
from agri_data_service.pipeline.parquet.availability_extension import (
    POSTGRES_DAY_EXPORT_ORIGIN,
    AvailabilityExtensionTally,
    FinalizedLaneDay,
    LaneDaySource,
    extend_availability_for_lane_day,
)
from agri_data_service.pipeline.parquet.derivation import derive_and_write_day_tiers
from agri_data_service.pipeline.parquet.gap_fill_contract import (
    _ABSENCE_LADDER_TIERS,
    _DERIVED_GAP_FILL_TIERS,
    DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    GAP_FILL_PARTITION_KIND,
    GAP_FILL_ZOOM_TIER,
    MAX_STATIC_EXPORT_ATTEMPTS,
    LaneDayOutcome,
    LaneWatermarkReading,
    _append_note,
    _lane_day_lock_key,
    _pin_statement_timeout,
    zero_row_absence,
    zero_row_absence_reason,
)
from agri_data_service.pipeline.parquet.lane_ceiling import allowed_source_ceiling
from agri_data_service.pipeline.parquet.objectstore import (
    EmptyPartitionError,
    GovernedAbsenceConflictError,
    SurplusPruneResult,
)
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_STREAM

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import date, datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.lane_contract import SourceWatermark
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.gap_fill_contract import (
        LaneDayLock,
        TierDeriver,
        VegetationPublicationBarrier,
    )
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, WrittenObjectLedger


async def resolve_lane_watermarks(
    session: AsyncSession,
    store: ObjectStore,
    *,
    lanes: Sequence[LaneRegistration],
    today: date,
) -> dict[str, LaneWatermarkReading]:
    """Read every static lane's source watermark, isolating one failed read from the rest of the tick.

    Only `static_lookup` lanes declare a resolver, so a run over series lanes alone touches nothing
    here. A lane whose read raises gets a reading carrying the reason, which its census reports as
    `watermark_unread` -- never as zero gaps, which would read as "current" and is a different claim.
    """
    readings: dict[str, LaneWatermarkReading] = {}
    for lane in lanes:
        resolver = lane.watermark
        if resolver is None:
            continue
        await _pin_statement_timeout(session)
        try:
            readings[lane.slug] = LaneWatermarkReading(watermark=await resolver(session, store, today=today))
        except Exception as error:  # one unreadable watermark must not end the tick
            readings[lane.slug] = LaneWatermarkReading(
                error=f"reading {lane.slug!r}'s source watermark failed: {type(error).__name__}: {error}"
            )
        finally:
            # Same discipline as a lane-day: these reads are read-only, and holding one snapshot
            # across a whole tick would pin a production xmin horizon for nothing.
            await session.rollback()
    return readings


async def _export_one_day(  # noqa: PLR0913 - one caller-supplied coordinate per arg, none foldable
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
    derive_tiers: TierDeriver = derive_and_write_day_tiers,
    statement_timeout_seconds: int = DEFAULT_STATEMENT_TIMEOUT_SECONDS,
) -> tuple[LaneDayOutcome, int, int, int, str | None]:
    """Export one lane-day, returning `(outcome, parts, rows, bytes, detail)` and never raising.

    THE COMPLETION MARKER IS RETRACTED BY THE FIRST PART WRITE, NOT HERE. `write_partition`
    retracts it as it uploads `part-0`, so an attempt that fails before writing anything -- a
    statement timeout, a transient database error, a source that now returns nothing -- leaves a
    previously-complete day exactly as it found it. Retracting up front instead would have stripped
    the completion claim off an intact release every time an unrelated export attempt failed.

    The session is rolled back on EVERY path, success included. These exports are read-only, so
    holding one snapshot open across a 600-second tick would pin the xmin horizon of a production
    database for no benefit -- and after a failed statement the rollback is what lets the NEXT lane
    run at all, which is what makes per-lane isolation real rather than asserted.
    """
    await _pin_statement_timeout(session, statement_timeout_seconds)
    try:
        result = await lane.adapter(session, store, day=day, run_id=run_id)
    except EmptyPartitionError as empty:
        await session.rollback()
        return _govern_absent_day(store, lane, day=day, run_id=run_id, now=now, observed=str(empty))
    except Exception as error:  # per-lane isolation: one lane's fault must not end the tick
        await session.rollback()
        return "raised", 0, 0, 0, f"{day.isoformat()}: {type(error).__name__}: {error}"
    await session.rollback()
    if result.absence_recorded:
        # A governed absence is ONE object and cannot be half-written, so it asserts its own
        # completion and never gets a marker. Writing one here would put two markers on a day whose
        # only honest reading is `absent`.
        return "absent", result.part_count, result.row_count, result.byte_count, None
    return _finalize_written_day(
        store,
        lane,
        day=day,
        parts=result.part_count,
        rows=result.row_count,
        written_bytes=result.byte_count,
        run_id=run_id,
        now=now,
        derive_tiers=derive_tiers,
    )


def _govern_absent_day(  # noqa: PLR0913 - one coordinate of the day being governed per arg
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
    observed: str,
) -> tuple[LaneDayOutcome, int, int, int, str | None]:
    """Govern one whole day as absent at EVERY rung, or write no marker at any rung at all.

    THE WHOLE LADDER IS CHECKED BEFORE THE FIRST MARKER IS WRITTEN. Writing coarse-first and
    refusing on the first conflict left the earlier rungs marked absent while z13 went on serving
    rows -- the exact stable lie the marker contract exists to prevent, and one no census brings
    back: `build_gap_census` walks the base tier, which still holds its parts and its completion.
    A rung that a later write still fails on is ROLLED BACK, marker by marker, for the same reason.

    THE COARSE RUNGS FIRST, THE BASE RUNG LAST, for the reason `_finalize_written_day` derives
    before it marks: only the base tier is censused, so a run that died after the base marker would
    leave a day covered and never revisited while every rung above it said nothing at all.
    """
    blocked = tuple(
        (zoom, part)
        for zoom in _ABSENCE_LADDER_TIERS
        if (part := store.part_blocking_absence(lane.slug, GAP_FILL_PARTITION_KIND, zoom, day)) is not None
    )
    if blocked:
        # Only an admin can say whether those parts remain valid, so this driver refuses to guess --
        # but it also refuses to stop the lane over it, because this day is the NEWEST one and every
        # older gap sits behind it. See FAILING_LANE_OUTCOMES.
        rungs = ", ".join(f"z{zoom} ({part})" for zoom, part in blocked)
        return (
            "blocked",
            0,
            0,
            0,
            f"{day.isoformat()}: the export returned zero rows but {rungs} still holds part files, so the day "
            f"can be neither written nor governed as absent without an admin deciding whether those parts are "
            f"still valid; no absence marker was written at any rung",
        )
    marked = 0
    written: list[ZoomTier] = []
    for zoom in _ABSENCE_LADDER_TIERS:
        try:
            receipt = store.write_absence(
                zero_row_absence(
                    lane.slug,
                    zoom=zoom,
                    day=day,
                    run_id=run_id,
                    observed=observed,
                    recorded_at=now(),
                ),
                layer=lane.slug,
                kind=GAP_FILL_PARTITION_KIND,
                zoom=zoom,
                day=day,
            )
        except Exception as refusal:  # a marker that cannot be written is a real failure, not an absence
            rolled_back = _retract_absence_ladder(store, lane, day=day, written=tuple(written))
            return (
                "raised",
                0,
                0,
                marked,
                f"{day.isoformat()}: z{zoom} absence marker refused: {refusal}. {rolled_back}",
            )
        written.append(zoom)
        marked += receipt.byte_count
    return "absent", 0, 0, marked, None


def _retract_absence_ladder(
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    written: tuple[ZoomTier, ...],
) -> str:
    """Undo a partly-written absence ladder, so no rung governs a day the others do not."""
    if not written:
        return "no rung had been marked, so the day is exactly as this attempt found it"
    failures: list[str] = []
    for zoom in written:
        try:
            store.clear_absence_marker(lane.slug, GAP_FILL_PARTITION_KIND, zoom, day)
        except Exception as error:  # one rung's rollback must not skip the rest
            failures.append(f"z{zoom}: {type(error).__name__}: {error}")
    if failures:
        return (
            "the markers this attempt had already written could NOT all be retracted, so the day now "
            f"governs some rungs and not others and needs an admin: {'; '.join(failures)}"
        )
    return f"the {len(written)} marker(s) this attempt had written were retracted, leaving the day unmarked"


def _finalize_written_day(  # noqa: PLR0913 - one coordinate of the day being closed per arg
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    parts: int,
    rows: int,
    written_bytes: int,
    run_id: str,
    now: Callable[[], datetime],
    derive_tiers: TierDeriver = derive_and_write_day_tiers,
) -> tuple[LaneDayOutcome, int, int, int, str | None]:
    """Close a written lane-day: prune what this export no longer wrote, then assert that it finished.

    PRUNE BEFORE MARK. The marker's `part_count` is the export's own claim about what the day holds,
    so asserting it while a larger earlier export's tail is still published would make the marker
    disagree with the bucket at the very moment it was written.

    A FAILED MARK IS `raised`, not a note on an otherwise successful day, and that is deliberate: an
    unmarked day is re-exported next tick, so a mark that keeps failing is a lane silently
    re-exporting the same day every hour forever while reporting success. The same reasoning already
    makes an absence marker that cannot be written a failure rather than an absence.

    A FAILED PRUNE STILL DOES NOT FAIL THE DAY -- the rows this export wrote are correct and no prune
    may undo that -- BUT IT WITHHOLDS THE MARK. Marking a day whose surplus parts survived would
    publish a completion claim over a two-generation mixture, which is the one statement this marker
    exists to make trustworthy. Leaving it unmarked keeps the day `incomplete`, so the next tick
    re-exports and re-prunes it: the outcome stays self-healing instead of becoming a stable lie.
    """
    if parts <= 0:
        return (
            "raised",
            parts,
            rows,
            written_bytes,
            f"{day.isoformat()}: the export reported {parts} part files while reporting data, so there "
            "is nothing a completion marker could honestly claim",
        )
    notes: list[str] = []
    # WRITE FIRST, PRUNE SECOND. A prune that ran first and then failed would leave the day EMPTY,
    # which reads as a present-but-thin version and is worse than the orphan it was removing.
    pruned = _prune_surplus(store, lane, day=day, written_part_count=parts)
    report = pruned.report
    if report is not None:
        notes.append(report)
    if pruned.failures:
        notes.append(
            f"{day.isoformat()}: the day is NOT being marked complete, because a surplus part from a "
            "larger earlier export is still published beside this one and a completion marker over "
            "that mixture would be false. The next tick re-exports and re-prunes this day."
        )
        return "written", parts, rows, written_bytes, "; ".join(notes)
    # THE COARSE RUNGS, BEFORE THE BASE MARKER. Withholding the base marker leaves the day
    # `incomplete`, which brings it back through the EXPORT queue and redoes all four rungs. Marking
    # it first and then failing to derive would instead leave it base-complete and rung-empty, which
    # now falls to the ladder queue -- a strictly weaker guarantee, since that queue depends on a
    # census being right where this ordering depends on nothing. Deriving first stays.
    try:
        derived = derive_tiers(store, layer=lane.slug, kind=GAP_FILL_PARTITION_KIND, day=day, run_id=run_id, now=now)
    except GovernedAbsenceConflictError as stranded:
        # A COARSE ABSENCE CLAIM SURVIVING A WRITTEN BASE RUNG, retracted here rather than left to
        # fail this day on every tick forever. It decides nothing: `write_partition` refuses a base
        # rung that still carries an absence claim, so the base export having SUCCEEDED proves that
        # claim was already retracted -- by an admin, or by a direct writer whose source began
        # publishing the day. Finishing that retraction across the ladder is the same obligation
        # `derive_tiers` has to finish the ladder, and a rung claiming a day is governed-empty while
        # the base rung serves its rows is the stable lie the whole marker contract exists to
        # prevent. The day still fails this tick and the next one redoes all four rungs.
        notes.append(_retract_derived_absences(store, lane, day=day, conflict=stranded))
        return "raised", parts, rows, written_bytes, "; ".join(notes)
    except Exception as error:  # the base rows are published but the ladder above them is not
        notes.append(
            f"{day.isoformat()}: the base rung is written but its coarse rungs are not, so the day stays "
            f"unfinished and will be re-exported rather than be published as visible only above z13: "
            f"{type(error).__name__}: {error}"
        )
        return "raised", parts, rows, written_bytes, "; ".join(notes)
    notes.extend(derived.notes)
    if derived.tiers:
        rungs = ", ".join(
            f"z{report.tier} {report.row_count} rows in {report.part_count} part(s)" for report in derived.tiers
        )
        notes.append(f"{day.isoformat()}: derived {rungs}")
    # The marker's counts describe the BASE rung alone. The derived rungs carry their own markers
    # with their own counts, written as each landed; folding them together here would make this
    # marker claim a population no single prefix holds.
    #
    # NO `parts=` HERE (unlike `derivation.py::_write_tier`): `parts`/`rows` above are
    # `LaneRunResult`'s folded totals, not receipts, and the ledger that DOES hold per-part digests
    # (`written`, opened by `fill_one_lane_day`) is not threaded this deep, nor safely reusable
    # across `_fill_static_day`'s retries without the mismatch guard `_rung_objects_from_ledger`
    # applies. See `pipeline/parquet/AGENTS.md`, "Per-part digests: `_write_tier` wired,
    # `_finalize_written_day` stays v1 (D3)".
    try:
        store.write_completion_marker(
            PartitionCompletion(part_count=parts, row_count=rows, completed_at=now(), run_id=run_id),
            layer=lane.slug,
            kind=GAP_FILL_PARTITION_KIND,
            zoom=GAP_FILL_ZOOM_TIER,
            day=day,
        )
    except Exception as error:  # the rows are published but nothing may read them as finished
        notes.append(
            f"{day.isoformat()}: the {parts} part file(s) uploaded but the completion marker did not, so "
            f"this day stays unfinished and will be re-exported: {type(error).__name__}: {error}"
        )
        return "raised", parts, rows, written_bytes, "; ".join(notes)
    return "written", parts, rows, written_bytes, "; ".join(notes) or None


async def _read_watermark(
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    today: date,
) -> tuple[SourceWatermark | None, str | None]:
    """Read one static lane's watermark for the race bracket, reporting the reason instead of raising."""
    resolver = lane.watermark
    if resolver is None:
        return None, None
    await _pin_statement_timeout(session)
    try:
        return await resolver(session, store, today=today), None
    except Exception as error:  # an unproven window is reportable; it is never a failed export
        return None, f"{type(error).__name__}: {error}"
    finally:
        # Same discipline as a lane-day: read-only, so never hold the snapshot past the answer.
        await session.rollback()


def _retract_derived_absences(
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    conflict: GovernedAbsenceConflictError,
) -> str:
    """Clear every coarse-rung absence claim over a day whose base rung holds data; report what happened."""
    failures: list[str] = []
    for zoom in _DERIVED_GAP_FILL_TIERS:
        try:
            store.clear_absence_marker(lane.slug, GAP_FILL_PARTITION_KIND, zoom, day)
        except Exception as error:
            failures.append(f"z{zoom}: {type(error).__name__}: {error}")
    if failures:
        return (
            f"{day.isoformat()}: a coarse rung still claims this day is governed as absent while its base rung "
            f"holds data, and the claim could not be retracted, so an admin must remove it: "
            f"{'; '.join(failures)} (from {conflict})"
        )
    return (
        f"{day.isoformat()}: a coarse rung claimed this day was governed as absent while its base rung holds "
        f"data; the base export already proved that claim retracted, so the surviving coarse markers were "
        f"removed and the next tick rebuilds the ladder: {conflict}"
    )


def _prune_surplus(
    store: ObjectStore, lane: LaneRegistration, *, day: date, written_part_count: int
) -> SurplusPruneResult:
    """Trail ANY completed export with the prune removing the parts it no longer wrote.

    Static lanes are no longer the only ones that need this. Before the completion marker, a series
    day holding any part at all read as covered and was never revisited, so a shrinking re-export
    could not arise there; now an unfinished series day IS re-exported, and it can.

    Scoped to the tier that was just written: the same day's coarser tiers hold a DIFFERENT
    resolution of it, not an older export of it, and a prune that reached them would delete a
    derivation this export never replaced.
    """
    try:
        return store.prune_surplus_parts(
            lane.slug, GAP_FILL_PARTITION_KIND, GAP_FILL_ZOOM_TIER, day, written_part_count=written_part_count
        )
    except Exception as error:  # the rows are written and correct; no prune may ever undo that
        # Returned as a FAILURE result rather than a prose note: the caller withholds the completion
        # mark on any failure, and a string it had to pattern-match would make that decision fragile.
        return SurplusPruneResult(
            removed=(),
            failures=(
                f"pruning surplus parts of {lane.slug} z{GAP_FILL_ZOOM_TIER} {day.isoformat()} failed, so parts "
                f"from a larger earlier export may still be published beside this one: "
                f"{type(error).__name__}: {error}",
            ),
        )


async def _fill_static_day(  # noqa: PLR0913 - one caller-supplied coordinate per arg, none foldable
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
    today: date,
    derive_tiers: TierDeriver = derive_and_write_day_tiers,
    statement_timeout_seconds: int = DEFAULT_STATEMENT_TIMEOUT_SECONDS,
) -> tuple[LaneDayOutcome, int, int, int, str | None]:
    """Export one STATIC lane-day, prune what it no longer wrote, and prove the source held still.

    THE EXPORT INSTANT IS PUT TIME, NOT SELECT TIME, and that gap is a race this lane cannot survive
    silently. A source change committed after the export's SELECT but before its PUT lands inside the
    window, so the part file's `LastModified` is at or after the source's own change instant and
    `_resolve_watermark_day` reads it as captured -- permanently, because every later tick compares
    the same two unchanged instants. The snapshot then serves a superseded evacuation level while the
    lane reports `current`.

    So the window is BRACKETED: the watermark is read immediately before the export and again after
    it. A read taken before the SELECT cannot be later than the SELECT, so two equal instants prove
    nothing changed in between and the PUT instant honestly means captured. A moved instant means the
    snapshot's vintage is unknown, and the day is re-exported rather than published as current.
    Bounded by `MAX_STATIC_EXPORT_ATTEMPTS`: a source changing faster than one export takes is
    REPORTED, which is the correct failure mode, not latched, which is the one this closes.

    A lane whose source has no change instant at all (the computed calendar) is not exposed: its
    verdict already resolves at day resolution and never claims instant-resolution currency.
    """
    notes: list[str] = []
    outcome: LaneDayOutcome = "raised"
    parts = rows = written_bytes = 0
    detail: str | None = None
    for attempt in range(1, MAX_STATIC_EXPORT_ATTEMPTS + 1):
        before, before_error = await _read_watermark(session, store, lane, today=today)
        outcome, parts, rows, written_bytes, detail = await _export_one_day(
            session,
            store,
            lane,
            day=day,
            run_id=run_id,
            now=now,
            derive_tiers=derive_tiers,
            statement_timeout_seconds=statement_timeout_seconds,
        )
        # `parts <= 0` is not re-checked: `_finalize_written_day` already returns `raised` in that
        # case, so `written` implies at least one part landed.
        if outcome != "written":
            break
        # `_export_one_day` already pruned this attempt and marked it complete. Its note moves into
        # `notes` rather than staying in `detail`, which the next attempt would overwrite and lose.
        if detail is not None:
            notes.append(detail)
            detail = None
        after, after_error = await _read_watermark(session, store, lane, today=today)
        unread = before_error or after_error
        if unread is not None:
            notes.append(
                "the source watermark could not be re-read around this export, so it is UNPROVEN whether the "
                f"source changed between its select and its upload: {unread}"
            )
            break
        if before is None or after is None or before.instant is None or after.instant is None:
            break
        if after.instant == before.instant:
            break
        if attempt == MAX_STATIC_EXPORT_ATTEMPTS:
            notes.append(
                f"the source changed again during every one of {MAX_STATIC_EXPORT_ATTEMPTS} export attempts, so "
                f"this snapshot of {day.isoformat()} may predate the source change at "
                f"{after.instant.isoformat()} while its upload instant reads as current -- re-export it"
            )
            break
        notes.append(
            f"the source changed at {after.instant.isoformat()} DURING attempt {attempt}'s export window, so "
            "that snapshot's vintage is unknown and the day is being re-exported"
        )
    return outcome, parts, rows, written_bytes, "; ".join(note for note in (detail, *notes) if note) or None


def _absence_reason_of_record(store: ObjectStore, slug: str, day: date) -> str:
    """Return the reason the day's BASE marker actually carries, never a second guess at it.

    THIS DRIVER IS NOT THE ONLY WRITER OF THIS NAMESPACE. Every `pipeline/direct/*` adapter writes
    `kind="observed"` under the same `layer=<slug>/` prefix this lane fills, so a day marked absent
    by a direct writer carries THAT lane's own sentence -- "the source served no fire detections in
    the requested extent", say -- not this driver's zero-row phrasing. `availability_index.py:2320`
    compares the two: `if absence.reason != evidence.absence_reason: raise AvailabilityConflictError`.
    Synthesising the reason here therefore refused to index exactly the days the direct writers had
    just governed, and did it in a `try` whose except keeps the day terminal -- so the failure showed
    up as a missing index row and a note, never as a wrong answer, which is why it survived.

    Falls back to the synthesised sentence only when no marker can be read, which is the case this
    driver itself creates and the one `zero_row_absence_reason` was written for.
    """
    absence = store.read_absence(slug, GAP_FILL_PARTITION_KIND, GAP_FILL_ZOOM_TIER, day)
    return zero_row_absence_reason(slug, day) if absence is None else absence.reason


async def _extend_availability_for_result(  # noqa: PLR0913 - one coordinate of the finished day per arg
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    result: tuple[LaneDayOutcome, int, int, int, str | None],
    *,
    day: date,
    today: date,
    run_id: str,
    now: Callable[[], datetime],
    written: WrittenObjectLedger,
    availability_storage: AvailabilityStorage | None,
    tally: AvailabilityExtensionTally | None,
) -> tuple[LaneDayOutcome, int, int, int, str | None]:
    """Extend the lane's availability generation AFTER a terminal day, never turning that day back."""
    outcome, parts, rows, written_bytes, detail = result
    if outcome not in ("written", "absent"):
        return result
    terminal_state: Literal["published", "governed_absence"] = (
        "published" if outcome == "written" else "governed_absence"
    )
    published_at = now()
    # THE LANE'S OWN CEILING, NOT THE DAY. A day that declared itself its own ceiling ratcheted the
    # published pointer to the newest day written, and coverage then closed every lane exactly at
    # its last row -- so no lane could report a gap tail however far behind its source it had
    # fallen. `max` with the day keeps the statement honest for a day a forward writer published
    # past the generic ceiling: a lane cannot have a ceiling below a day it demonstrably holds.
    source_ceiling = max(allowed_source_ceiling(lane, today=today), day)
    try:
        extension = await extend_availability_for_lane_day(
            session,
            store,
            lane=lane.slug,
            kind=GAP_FILL_PARTITION_KIND,
            day=day,
            outcome=FinalizedLaneDay(
                terminal_state=terminal_state,
                day=day,
                written=written,
                source=LaneDaySource(
                    origin=POSTGRES_DAY_EXPORT_ORIGIN,
                    run_id=run_id,
                    row_count=rows,
                    part_count=parts,
                    exported_at=published_at,
                    detail=f"{lane.slug} {lane.nature} day export",
                ),
                published_at=published_at,
                source_ceiling=source_ceiling,
                absence_reason=(
                    None if terminal_state == "published" else _absence_reason_of_record(store, lane.slug, day)
                ),
            ),
            availability=availability_storage,
            now=now,
        )
    except Exception as error:  # the day is terminal; the index owing an entry may never undo that
        return (
            outcome,
            parts,
            rows,
            written_bytes,
            _append_note(
                detail,
                f"{day.isoformat()}: the availability step raised and the day stays terminal: "
                f"{type(error).__name__}: {error}",
            ),
        )
    if tally is not None:
        tally.record(extension)
    return outcome, parts, rows, written_bytes, _append_note(detail, extension.note)


async def fill_one_lane_day(  # noqa: PLR0913 - one caller-supplied coordinate per arg, none foldable
    session: AsyncSession,
    store: ObjectStore,
    lane: LaneRegistration,
    *,
    day: date,
    run_id: str,
    now: Callable[[], datetime],
    today: date,
    lane_day_lock: LaneDayLock,
    vegetation_publication_barrier: VegetationPublicationBarrier = try_postgres_vegetation_publication_barrier,
    derive_tiers: TierDeriver = derive_and_write_day_tiers,
    statement_timeout_seconds: int = DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    extend_availability: bool = True,
    availability_storage: AvailabilityStorage | None = None,
    availability_tally: AvailabilityExtensionTally | None = None,
) -> tuple[LaneDayOutcome, int, int, int, str | None]:
    """Export one lane-day under its advisory lock. Static lanes also bracket their window.

    PUBLIC because the bulk drain (`pipeline/parquet/drain.py`) calls exactly this. The drain and
    the hourly cron must not drift on what one lane-day MEANS -- the lock, the prune, the coarse
    rungs and the marker are one indivisible unit, and a drain that reimplemented them would be
    the second definition of a contract that already took three review passes to settle.

    THE LOCK SPANS THE WHOLE DAY, export and prune and mark together, because the prune DELETES.
    Two unsynchronised runs on one lane-day can otherwise interleave so that the slower one's prune
    removes parts the faster one just wrote and then stamps a completion marker whose `part_count`
    matches the truncated remainder exactly -- the bucket and its receipt agreeing on a population
    that lost rows, which no later census or audit can detect. Nothing else in this path is
    serialised: `interface/cli/commands.py`'s `parquet-gap-fill` verb takes no lease, and RUNBOOK 0.33.3 B has the bulk
    drain running CONCURRENTLY with this driver by design ("build drain -> run drain -> THEN stop
    the cron"), so the overlap is planned rather than hypothetical.
    """
    barrier = (
        vegetation_publication_barrier
        if lane.slug == VEGETATION_PLANE_STREAM
        else unlocked_vegetation_publication_barrier
    )
    async with barrier(session) as publication_granted:
        if publication_granted is False:
            await session.rollback()
            return (
                "contended",
                0,
                0,
                0,
                f"{day.isoformat()}: exact vegetation audit holds the publication barrier; this day was deferred",
            )
        async with lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted:
            if not granted:
                return (
                    "contended",
                    0,
                    0,
                    0,
                    f"{day.isoformat()}: another run holds this lane-day, so it was skipped rather than "
                    "written twice; it stays missing and the next tick will take it",
                )
            # THE LEDGER SPANS THE WHOLE EXPORT so the availability step can name every object this
            # run wrote -- part keys and digests included -- without re-reading a single byte of them.
            # It records nothing unless this scope is open, so no other caller pays for it.
            with store.recording_written_objects() as written:
                if lane.watermark is None:
                    result = await _export_one_day(
                        session,
                        store,
                        lane,
                        day=day,
                        run_id=run_id,
                        now=now,
                        derive_tiers=derive_tiers,
                        statement_timeout_seconds=statement_timeout_seconds,
                    )
                else:
                    result = await _fill_static_day(
                        session,
                        store,
                        lane,
                        day=day,
                        run_id=run_id,
                        now=now,
                        today=today,
                        derive_tiers=derive_tiers,
                        statement_timeout_seconds=statement_timeout_seconds,
                    )
            # SILENT WHEN IT IS NOT WIRED. A run with no conditional storage has the same thing to
            # say about every day it writes, and saying it on each one would bury the notes that
            # describe what actually happened to that day.
            if not extend_availability or availability_storage is None:
                return result
            return await _extend_availability_for_result(
                session,
                store,
                lane,
                result,
                day=day,
                today=today,
                run_id=run_id,
                now=now,
                written=written,
                availability_storage=availability_storage,
                tally=availability_tally,
            )
