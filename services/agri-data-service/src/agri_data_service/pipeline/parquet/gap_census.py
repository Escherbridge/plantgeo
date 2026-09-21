"""What each lane owes: the two queues, one for days that need an export and one for ladders to re-derive.

Rationale: see `AGENTS.md` in this directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agri_data_service.foundation.parquet.lane_contract import (
    LaneContractError,
    nature_has_time_axis,
    resolve_static_lane,
)
from agri_data_service.foundation.parquet.paths import (
    UNFILLED_PARTITION_STATUSES,
    completed_partition_days,
    completed_rung_days,
    partition_day_statuses,
    try_parse_absence_marker_path,
    try_parse_partition_path,
)
from agri_data_service.pipeline.parquet.gap_fill_contract import (
    _DERIVED_GAP_FILL_TIERS,
    GAP_CENSUS_REPORT_DAY_SAMPLE,
    GAP_FILL_PARTITION_KIND,
    GAP_FILL_ZOOM_TIER,
    GapFillContractError,
    LaneGapCensus,
    LaneWatermarkReading,
    lane_window,
)
from agri_data_service.pipeline.parquet.objectstore import oldest_export_instant

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore


def _census_shell(lane: LaneRegistration, zoom: ZoomTier, **overrides: object) -> LaneGapCensus:
    """Build one census row with the lane's own declared fields and the tier it was taken at filled in."""
    fields: dict[str, object] = {
        "slug": lane.slug,
        "nature": lane.nature,
        "zoom": zoom,
        "forecastable": lane.forecastable,
        "cadence_days": lane.cadence_days,
        "writer_ceiling": lane.writer_ceiling,
        "history_floor": lane.claimed_history_floor,
        "publication_lag_days": lane.publication_lag_days,
        "floor_basis": lane.claimed_floor_basis,
        "first_day": None,
        "last_day": None,
        "data_days": 0,
        "absent_days": 0,
        "conflict_days": 0,
        "incomplete_days": 0,
        "missing_days": (),
        "truncated": False,
    }
    fields.update(overrides)
    return LaneGapCensus(**fields)  # type: ignore[arg-type]


def _listing_failure(lane: LaneRegistration, error: Exception) -> str:
    """The message a per-lane listing failure carries; it must never read as 'no gaps found'."""
    return f"listing {lane.slug!r} failed: {type(error).__name__}: {error}"


def derived_rung_completions(
    store: ObjectStore,
    *,
    layer: str,
    kind: PartitionKind = GAP_FILL_PARTITION_KIND,
    tiers: Sequence[ZoomTier] = _DERIVED_GAP_FILL_TIERS,
) -> dict[ZoomTier, set[date]]:
    """Return, per DERIVED rung, the days that rung holds as `data`. One listing per rung, no reads.

    THE COST, STATED: exactly `len(tiers)` extra `list_partition_keys` calls per lane per census --
    three today -- and not one object GET. See `AGENTS.md` in this directory, "What one ladder census
    costs per tick".

    A RUNG COUNTS AS FINISHED WHEN ITS RECEIPT MATCHES WHAT IT HOLDS -- parts under an ordinary
    marker, or nothing under a derived-empty one. `completed_rung_days` rather than
    `completed_partition_days`, because a marker whose parts were deleted out from under it is a LOST
    rung: counting it finished is what made such a rung unrepairable by any tick.
    """
    if not tiers:
        raise GapFillContractError(
            f"a ladder census of {layer!r} over no rungs would report every published day complete; ask for "
            f"{tuple(_DERIVED_GAP_FILL_TIERS)} or a subset of it"
        )
    return {
        tier: completed_rung_days(store.list_partition_keys(layer, kind, tier), layer=layer, kind=kind, zoom=tier)
        for tier in tiers
    }


def _base_published_days(keys: Sequence[str], *, layer: str, zoom: ZoomTier) -> set[date]:
    """Every day of the base rung a coarse rung could be derived FROM: parts AND a completion marker.

    THE SPAN COMES FROM THE KEYS, not from `lane_window`, so this answers over the whole bucket and
    serves a `static_lookup` lane's version stamps as readily as a series lane's calendar. It is the
    same shape `drain.build_lane_ladder_census` takes, and it asks the same shared primitive
    (`partition_day_statuses`) rather than re-deciding what `data` means for a third time.
    """
    days = {
        parsed.day
        for key in keys
        if (parsed := try_parse_partition_path(key)) is not None
        and parsed.layer == layer
        and parsed.kind == GAP_FILL_PARTITION_KIND
        and parsed.zoom == zoom
    }
    if not days:
        return set()
    statuses = partition_day_statuses(
        layer=layer,
        kind=GAP_FILL_PARTITION_KIND,
        zoom=zoom,
        first_day=min(days),
        last_day=max(days),
        keys=keys,
    )
    return {day for day, status in statuses.items() if status == "data"}


@dataclass(frozen=True, slots=True)
class _LadderRepairCensus:
    """One lane's ladder half: the days this TICK may repair, and the ones only the bulk drain reaches."""

    days: tuple[date, ...]
    truncated: bool = False
    error: str | None = None
    #: Ladder-incomplete days OUTSIDE this tick's scope. Nothing in an hourly run will select them, so
    #: they are reported as `reindex_owed` rather than left as a silence a green tick reads over.
    out_of_scope: int = 0


def _ladder_repair_census(  # noqa: PLR0913 - one census coordinate per arg, none foldable
    lane: LaneRegistration,
    store: ObjectStore,
    *,
    base_keys: Sequence[str],
    zoom: ZoomTier,
    max_days_per_lane: int | None,
    scope: tuple[date, date] | None = None,
) -> _LadderRepairCensus:
    """Census one lane's derived rungs, newest first, over `scope` when the caller bounds one.

    THE HOURLY TICK IS SCOPED AND THE BULK DRAIN IS NOT. `scope` is the lane's own settled window
    unioned with the days a direct writer owns past it, which is the whole range an hourly run is
    responsible for; the whole-bucket walk stays in `drain --selection ladder`. Days outside it are
    counted, never dropped silently. See `AGENTS.md`, "What one ladder census costs per tick".

    A FAILURE IS REPORTED, NEVER SWALLOWED INTO AN EMPTY SET: an empty repair set means "the ladder
    is whole", which is the one thing an unreadable bucket cannot say. It is scoped to the LADDER
    half -- the base census that produced `base_keys` still stands.
    """
    try:
        base_data_days = _base_published_days(base_keys, layer=lane.slug, zoom=zoom)
        if not base_data_days:
            return _LadderRepairCensus(days=())
        completions = derived_rung_completions(store, layer=lane.slug, kind=GAP_FILL_PARTITION_KIND)
    except Exception as error:  # per-lane isolation: an unreadable rung listing must not end the census
        return _LadderRepairCensus(
            days=(), error=f"censusing {lane.slug!r} derived rungs failed: {type(error).__name__}: {error}"
        )
    # INTERSECTED, NEVER UNIONED: a day is ladder-complete only when EVERY rung holds it, so the
    # complete set is the intersection and everything else owes a re-derivation. A union would call a
    # day whole because one of its three rungs landed.
    complete = set(base_data_days)
    for marked in completions.values():
        complete &= marked
    incomplete = base_data_days - complete
    in_scope = incomplete if scope is None else {day for day in incomplete if scope[0] <= day <= scope[1]}
    ordered = tuple(sorted(in_scope, reverse=True))
    out_of_scope = len(incomplete) - len(in_scope)
    if max_days_per_lane is None:
        return _LadderRepairCensus(days=ordered, out_of_scope=out_of_scope)
    return _LadderRepairCensus(
        days=ordered[:max_days_per_lane],
        truncated=len(ordered) > max_days_per_lane,
        out_of_scope=out_of_scope,
    )


def _static_lane_census(
    lane: LaneRegistration,
    store: ObjectStore,
    *,
    zoom: ZoomTier,
    today: date,
    reading: LaneWatermarkReading | None,
) -> LaneGapCensus:
    """Classify one `static_lookup` lane's TIER against its source watermark and the objects already written.

    THE COUNTS ARE OVER THE WHOLE STREAM AT ONE TIER, not over a window, because a static lane has
    none: a reference set holds N versions, and how many of them exist is the useful number.
    `missing_days` holds at most one entry -- the version the source says is owed.

    AN UNFINISHED OLD VERSION IS REPORTED, NEVER RE-EXPORTED, and that asymmetry is the point. A
    static lane's partition day is a VERSION STAMP, so re-exporting a stranded 2026-08-20 today
    would write today's population under that day's key and manufacture a version that never
    existed. The half-release therefore stays on disk as garbage only an admin may retract -- but
    the lane must not report `current` while it sits there, so `static_detail` names it and
    `incomplete_days` counts it.
    """
    if reading is not None and reading.error is not None:
        return _census_shell(lane, zoom, static_state="watermark_unread", error=reading.error)
    try:
        listed = store.list_partition_objects(lane.slug, GAP_FILL_PARTITION_KIND, zoom)
    except Exception as error:  # per-lane isolation: an unreadable listing must not end the census
        return _census_shell(lane, zoom, static_state="watermark_unread", error=_listing_failure(lane, error))
    # The listing is already tier-scoped by its prefix; the tier is re-checked from the PARSED key
    # anyway, so a store prefix or a hand-placed object cannot smuggle another rung into these sets.
    part_days = {
        parsed.day
        for entry in listed
        if (parsed := try_parse_partition_path(entry.relative_path)) is not None
        and parsed.layer == lane.slug
        and parsed.kind == GAP_FILL_PARTITION_KIND
        and parsed.zoom == zoom
    }
    # THE SET THAT DECIDES CURRENCY. A static lane's whole verdict hangs off its newest data day, so
    # a release killed part-way through uploading -- every part of it newer than the watermark --
    # would otherwise resolve the lane `current` on top of half a snapshot. That is RUNBOOK 0.33.2
    # hazard 2 verbatim, and restricting the set to days that ASSERTED completion is what closes it.
    # Asked of the shared primitive rather than re-derived here: two spellings of "which days
    # completed" is how the census and the readers drift apart.
    complete_days = part_days & completed_partition_days(
        (entry.relative_path for entry in listed),
        layer=lane.slug,
        kind=GAP_FILL_PARTITION_KIND,
        zoom=zoom,
    )
    marker_days = {
        marker.day
        for entry in listed
        if (marker := try_parse_absence_marker_path(entry.relative_path)) is not None
        and marker.layer == lane.slug
        and marker.kind == GAP_FILL_PARTITION_KIND
        and marker.zoom == zoom
    }
    newest_day = max(complete_days, default=None)
    watermark = None if reading is None else reading.watermark
    try:
        # Handed to the resolver APART, from the sets already built above: for a version stamp a part
        # file and a governed absence make opposite claims. See `resolve_static_lane`.
        verdict = resolve_static_lane(
            watermark=watermark,
            newest_data_day=newest_day,
            newest_data_instant=(
                None
                if newest_day is None
                else oldest_export_instant(
                    listed, layer=lane.slug, kind=GAP_FILL_PARTITION_KIND, zoom=zoom, day=newest_day
                )
            ),
            newest_marker_day=max(marker_days, default=None),
            today=today,
        )
    except LaneContractError as error:
        return _census_shell(lane, zoom, static_state="watermark_unread", error=f"{lane.slug}: {error}")
    version_day = watermark.day if watermark is not None else None
    stranded = sorted(part_days - complete_days - marker_days, reverse=True)
    detail = verdict.detail
    if stranded:
        named = ", ".join(day.isoformat() for day in stranded[:GAP_CENSUS_REPORT_DAY_SAMPLE])
        stranded_note = (
            f"{len(stranded)} version(s) hold part files with no completion marker and cannot be "
            f"repaired by this driver -- a static lane's day is a version stamp, so re-exporting one "
            f"today would date the CURRENT population as that version. Retracting them is an admin "
            f"action: {named}"
        )
        detail = f"{detail}; {stranded_note}" if detail else stranded_note
    # A version stamp is still a lane-day with a four-rung ladder, so a static lane owes its coarse
    # rungs exactly as a series lane does -- and a repair re-derives them from the version's own base
    # parts, which is the one correction a static lane may take without inventing a version. UNCAPPED
    # because a reference set holds versions, not a calendar: capping would defer a rung for a lane
    # that has three days in the bucket.
    ladder = _ladder_repair_census(
        lane,
        store,
        base_keys=tuple(entry.relative_path for entry in listed),
        zoom=zoom,
        max_days_per_lane=None,
    )
    return _census_shell(
        lane,
        zoom,
        first_day=version_day,
        last_day=version_day,
        data_days=len(complete_days),
        ladder_repair_days=ladder.days,
        ladder_truncated=ladder.truncated,
        ladder_error=ladder.error,
        ladder_out_of_scope_days=ladder.out_of_scope,
        # Both arithmetics run over `part_days`, not `complete_days`: a governed absence sitting
        # beside ANY data is the contradiction worth escalating, whether or not that data finished,
        # and scoring it against the completed set alone would quietly downgrade it to `absent`.
        absent_days=len(marker_days - part_days),
        conflict_days=len(marker_days & part_days),
        incomplete_days=len(part_days - complete_days - marker_days),
        missing_days=() if verdict.version_day is None else (verdict.version_day,),
        static_state=verdict.state,
        source_watermark=version_day,
        watermark_basis=None if watermark is None else watermark.basis,
        static_detail=detail,
    )


def _series_lane_census(
    lane: LaneRegistration,
    store: ObjectStore,
    *,
    zoom: ZoomTier,
    today: date,
    max_days_per_lane: int | None,
) -> LaneGapCensus:
    """Classify one `daily_series` or `release_series` lane's settled window AT ONE TIER, from the LISTING alone."""
    window = lane_window(lane, today=today, first_day=lane.claimed_history_floor)
    if window is None:
        return _census_shell(lane, zoom)
    first_day, last_day = window
    try:
        base_keys = store.list_partition_keys(lane.slug, GAP_FILL_PARTITION_KIND, zoom)
        statuses = partition_day_statuses(
            layer=lane.slug,
            kind=GAP_FILL_PARTITION_KIND,
            zoom=zoom,
            first_day=first_day,
            last_day=last_day,
            keys=base_keys,
        )
    except Exception as error:  # per-lane isolation: an unreadable listing must not end the census
        return _census_shell(lane, zoom, first_day=first_day, last_day=last_day, error=_listing_failure(lane, error))
    # NEWEST-FIRST. `partition_day_statuses` answers chronologically; this reversal is the whole
    # reason one driver serves both the leading edge and the backlog. See the module docstring.
    #
    # Cadence filters the candidates BEFORE they become work: a release series only publishes on its
    # own step from the floor, so the six intervening days are not gaps the driver should chase.
    # It never suppresses a day that already holds data or a marker -- those are read from the
    # listing above and reported as-is, so a real partition off the expected step stays visible.
    # The cadence filter guards `missing` ONLY. An `incomplete` day is one this lane demonstrably
    # exported before, so whether it sits on the declared step is already settled by the fact that
    # something wrote it -- and suppressing it here would strand a half-written off-step day forever.
    missing = tuple(
        day
        for day, status in sorted(statuses.items(), reverse=True)
        if status in UNFILLED_PARTITION_STATUSES
        and (
            status != "missing"
            or (
                day in lane.release_days
                if lane.release_days is not None
                else (day - first_day).days % lane.cadence_days == 0
            )
        )
    )
    # THE LADDER SCOPE IS THE SETTLED WINDOW UNIONED WITH THE DIRECT-WRITER TAIL, not the settled
    # window alone and not the whole bucket. `lane_window` clamps `last_day` to `writer_ceiling`, so
    # every day a DIRECT writer owns sits outside it -- correct for exports, which this driver must
    # not attempt there, and wrong for rungs, which are derived from published base parts and invoke
    # no writer at all. Bounding it at `today` keeps an hourly tick responsible for the range it can
    # actually serve while `drain --selection ladder` still walks the whole bucket; whatever falls
    # outside is counted as `reindex_owed` rather than dropped. It costs no extra request: the base
    # keys are the ones already listed above.
    ladder = _ladder_repair_census(
        lane,
        store,
        base_keys=base_keys,
        zoom=zoom,
        max_days_per_lane=max_days_per_lane,
        scope=(first_day, max(last_day, today)),
    )
    return _census_shell(
        lane,
        zoom,
        first_day=first_day,
        last_day=last_day,
        data_days=sum(1 for status in statuses.values() if status == "data"),
        ladder_repair_days=ladder.days,
        ladder_truncated=ladder.truncated,
        ladder_error=ladder.error,
        ladder_out_of_scope_days=ladder.out_of_scope,
        absent_days=sum(1 for status in statuses.values() if status == "absent"),
        conflict_days=sum(1 for status in statuses.values() if status == "conflict"),
        incomplete_days=sum(1 for status in statuses.values() if status == "incomplete"),
        missing_days=missing if max_days_per_lane is None else missing[:max_days_per_lane],
        truncated=max_days_per_lane is not None and len(missing) > max_days_per_lane,
    )


def build_lane_census(
    lane: LaneRegistration,
    store: ObjectStore,
    *,
    today: date,
    max_days_per_lane: int | None = None,
    reading: LaneWatermarkReading | None = None,
) -> LaneGapCensus:
    """Classify one lane's coverage from the object LISTING alone -- never by opening a file.

    What counts as covered, why the two queues are separate fields, and why they are scoped
    differently: see `AGENTS.md` in this directory, "What a lane census counts, and what it does
    not" and "What one ladder census costs per tick".
    """
    if nature_has_time_axis(lane.nature):
        return _series_lane_census(
            lane, store, zoom=GAP_FILL_ZOOM_TIER, today=today, max_days_per_lane=max_days_per_lane
        )
    return _static_lane_census(lane, store, zoom=GAP_FILL_ZOOM_TIER, today=today, reading=reading)


def build_gap_census(
    lanes: Sequence[LaneRegistration],
    store: ObjectStore,
    *,
    today: date,
    max_days_per_lane: int | None = None,
    watermarks: Mapping[str, LaneWatermarkReading] | None = None,
) -> tuple[LaneGapCensus, ...]:
    """Census every requested lane, isolating one lane's listing failure from the rest."""
    readings = watermarks or {}
    return tuple(
        build_lane_census(
            lane,
            store,
            today=today,
            max_days_per_lane=max_days_per_lane,
            reading=readings.get(lane.slug),
        )
        for lane in lanes
    )


def gap_census_report(census: Sequence[LaneGapCensus]) -> dict[str, object]:
    """Render `--dry-run`'s whole answer: what WOULD be filled, without writing one object."""
    return {
        "lanes": [entry.to_report() for entry in census],
        "lane_count": len(census),
        "missing_days": sum(len(entry.missing_days) for entry in census),
        "lanes_with_gaps": [entry.slug for entry in census if entry.missing_days],
        # The days that are PUBLISHED and invisible below z13. Reported at the top level because a
        # census that only totalled `missing_days` is exactly how 1,040 of them stayed hidden.
        "ladder_repair_days": sum(len(entry.ladder_repair_days) for entry in census),
        # The ladder-incomplete days an HOURLY tick will never select, because they sit outside the
        # window it is responsible for. Only `drain --selection ladder` reaches them.
        "ladder_out_of_scope_days": sum(entry.ladder_out_of_scope_days for entry in census),
        "lanes_with_ladder_repairs": [entry.slug for entry in census if entry.ladder_repair_days],
        "lanes_with_ladder_errors": [entry.slug for entry in census if entry.ladder_error is not None],
        # Surfaced by name, not just summed: a lane accumulating unfinished days every tick is
        # crashing mid-export, and that reads as ordinary backlog in a `missing_days` total.
        "lanes_with_unfinished_days": [entry.slug for entry in census if entry.incomplete_days],
        "lanes_with_errors": [entry.slug for entry in census if entry.error is not None],
        # Reported separately from `lanes_with_gaps` so an operator can tell a reference set that
        # MATCHES its source from one nobody asked about. Both show zero missing days.
        "static_lanes_current": [entry.slug for entry in census if entry.static_state == "current"],
        "static_lanes_unread": [entry.slug for entry in census if entry.static_state == "watermark_unread"],
    }
