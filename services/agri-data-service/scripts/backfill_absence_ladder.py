"""Give every governed-absence day the three coarse markers its base rung was written without.

Run from ``services/agri-data-service``. **Dry-run is the default; ``--apply`` is the only path that
writes anything**, matching ``parquet-rewrite-signal``'s precedent for a destructive-adjacent sweep.

WHY THIS EXISTS. ``ObjectStore.write_absence`` marks ONE tier per call and says so
(``pipeline/parquet/objectstore.py``: "CROSS-TIER AGREEMENT OF ONE DAY IS NOT THIS MODULE'S
INVARIANT ... 'Every tier of a published day is present' is the DERIVATION step's obligation"), and
until 2026-09-06 every lane writer passed it ``zoom=LANE_BASE_ZOOM_TIER`` and stopped. A governed
absence therefore landed at z13 alone. A day like that is not a weaker index entry, it is not an
index entry: ``availability_index.py::_validate_generation_day`` demands the exact four-rung ladder,
and ``_verify_absence_object`` demands each rung's row cite a marker at ITS OWN key, so the three
missing rows can be neither omitted nor synthesised. Measured against production on 2026-09-06, a
full ``--all-time-bearing --dry-run`` of ``compile_availability_bootstrap.py`` refused 3,205 days,
every one of them this shape, and ``burn-severity`` compiled 5 selectable days out of 2,107. The
forward defect is fixed at the writer (``pipeline/parquet/derivation.py::write_absence_ladder``);
this script is the backfill for the days already written.

WHAT IT REFUSES, AND WHY THE REFUSAL IS THE POINT. The decision is keyed on MARKER KIND, never on
rung count. ``sensors`` holds 25 days whose base rung carries real PART FILES stranded there by a
completely different defect (a tier derivation naming ``station_longitude``/``station_latitude``
columns its base table does not have). Those days hold exactly one rung too, and writing a governed
absence over one of them would assert that the source had nothing for a day that demonstrably holds
rows -- data loss dressed as a repair. Any day holding a part file or a completion marker anywhere on
its ladder is refused BY NAME and printed in the receipt, however many rungs it has.

WHAT IT WRITES. The base rung's OWN marker bytes, re-read and re-parsed for each day, are what the
missing coarse rungs are given. No reason is ever minted here: ``_validate_generation_day`` refuses a
day whose rungs disagree about WHY it is empty ("availability day ... mixes absence reasons across
its ladder"), and ``_verify_absence_object`` re-proves each rung's reason against the row that cites
it. A day whose base marker cannot be read or parsed is refused rather than repaired, because the
only alternative is inventing the sentence a later reader will trust.

IT NEVER WRITES ``_complete.empty.json``. ``availability_index.py::_is_published_empty_rung``
reserves that name for a coarse rung whose NON-EMPTY base generalised away -- "the base rung
demonstrably holds the rows this rung dropped" -- and ``objectstore.write_completion_marker`` refuses
one at the base rung outright. Using it here would state that rows existed when none did.

WHAT COMES OUT. One JSON receipt on stdout: per lane, the days examined, the days already marked at
every rung, the days needing markers, the markers that would be (or were) written, and every day it
REFUSED with the reason and the offending object named. ``--out`` also persists it. The exit status
is nonzero only when a lane could not be walked or an ``--apply`` write failed; refused days are an
expected, reportable finding and do not by themselves fail the run.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final

SERVICE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from agri_data_service.foundation.parquet.absence import GovernedAbsence, GovernedAbsenceError  # noqa: E402
from agri_data_service.foundation.parquet.lane_contract import nature_has_time_axis  # noqa: E402
from agri_data_service.foundation.parquet.paths import (  # noqa: E402
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
)
from agri_data_service.parquet_ops.coverage import registered_census_lanes  # noqa: E402
from agri_data_service.pipeline.parquet.derivation import (  # noqa: E402
    ABSENCE_LADDER_TIERS,
    write_absence_ladder,
)
from agri_data_service.pipeline.parquet.objectstore import ObjectStore  # noqa: E402
from agri_data_service.warehouse.schemas.availability_index import AVAILABILITY_REQUIRED_RUNGS  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Sequence

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.parquet_ops.coverage import CensusLane

DEFAULT_WORKERS: Final = 8
RECEIPT_FILE_NAME: Final = "receipt.json"

#: The ladder this script completes, taken from the WRITER rather than restated, so a change to the
#: forward fix's rung order or membership can never leave the backfill filling a different set.
LADDER_RUNGS: Final[tuple[ZoomTier, ...]] = ABSENCE_LADDER_TIERS
BASE_RUNG: Final[ZoomTier] = LADDER_RUNGS[-1]

# THE WRITER'S LADDER AND THE INDEX'S LADDER MUST BE THE SAME SET, checked at import rather than
# assumed. This script exists only because a day that does not hold the exact ladder
# `AVAILABILITY_REQUIRED_RUNGS` names is unindexable; filling a DIFFERENT set of rungs would leave
# every repaired day exactly as unindexable as it started, and the receipt would say it was fixed.
if set(LADDER_RUNGS) != set(AVAILABILITY_REQUIRED_RUNGS):
    raise RuntimeError(
        f"the absence ladder {LADDER_RUNGS} the writer marks is not the ladder "
        f"{AVAILABILITY_REQUIRED_RUNGS} an availability generation requires, so completing one would not make "
        f"a day selectable; reconcile pipeline/parquet/derivation.py with warehouse/schemas/availability_index.py"
    )

#: What one day was found to be. Every day the walk sees lands in exactly one of these, so the counts
#: in a lane's receipt add up to `days_examined` and an operator can prove nothing was dropped.
OUTCOME_ALREADY_COMPLETE: Final = "already_marked_at_every_rung"
OUTCOME_NEEDS_MARKERS: Final = "absence_marker_at_a_subset_of_rungs"
OUTCOME_PUBLISHED: Final = "published_day_left_untouched"
OUTCOME_REFUSED: Final = "refused"

#: Why one day was refused. Each names a different repair, which is the whole reason they are not one
#: word: the first needs an export fixed, the second and third need an admin decision, the fourth
#: needs whoever wrote the disagreeing markers to say which claim is true.
REFUSAL_DAY_HOLDS_DATA: Final = "day_holds_part_files_or_completion_markers"
REFUSAL_NO_BASE_MARKER: Final = "governed_absence_above_the_base_rung_only"
REFUSAL_BASE_MARKER_UNREADABLE: Final = "base_absence_marker_missing_or_unreadable"
REFUSAL_REASONS_DISAGREE: Final = "existing_rungs_state_different_absence_reasons"


class BackfillError(RuntimeError):
    """One lane cannot be walked, and the reason is the operator's to act on."""


def _now() -> datetime:
    """The wall clock the receipt is stamped from. A thin seam so a test can freeze it."""
    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class RungObjects:
    """Every object one rung of one lane-day holds, straight from the listing -- no GET, no download.

    The three kinds are kept APART rather than reduced to a status, because the decision this script
    makes is "is this day purely a governed absence" and that is a question about marker kind. A day
    reduced to `("absent",)` or to a rung count cannot answer it: the 25 stranded-part `sensors` days
    and the 2,102 absence-only `burn-severity` days both hold exactly one rung.
    """

    rung: int
    part_paths: tuple[str, ...]
    completion_path: str | None
    absence_path: str | None

    @property
    def is_absence_only(self) -> bool:
        """True when this rung's whole content is one governed-absence marker."""
        return self.absence_path is not None and not self.part_paths and self.completion_path is None

    @property
    def is_published(self) -> bool:
        """True when this rung holds parts closed by a completion marker, and no absence claim."""
        return bool(self.part_paths) and self.completion_path is not None and self.absence_path is None

    @property
    def data_objects(self) -> tuple[str, ...]:
        """Every object here that is NOT a governed-absence marker, in listing order."""
        return (*self.part_paths, *(() if self.completion_path is None else (self.completion_path,)))


@dataclass(frozen=True, slots=True)
class DayVerdict:
    """What one day is, which rungs it still owes, and -- when refused -- exactly why."""

    day: date
    outcome: str
    missing_rungs: tuple[ZoomTier, ...] = ()
    refusal: str | None = None
    detail: str | None = None

    def to_wire(self) -> dict[str, object]:
        """Render one refused day for the receipt, naming the offending object where there is one."""
        return {"day": self.day.isoformat(), "detail": self.detail, "reason": self.refusal}


@dataclass(slots=True)
class LaneBackfill:
    """Everything one lane's pass found and, under `--apply`, everything it changed."""

    lane: str
    kind: str
    apply_changes: bool
    days_examined: int = 0
    days_already_complete: int = 0
    days_published: int = 0
    days_needing_markers: int = 0
    markers_owed: int = 0
    markers_written: int = 0
    refused: list[DayVerdict] = field(default_factory=list)
    #: An `--apply` write that failed. Reported per day and never retried here: a marker that could
    #: not be written leaves the day exactly as this pass found it, and the next run re-selects it.
    failures: list[str] = field(default_factory=list)

    def to_receipt(self) -> dict[str, object]:
        """One lane's whole finding, with the refusals listed rather than merely counted."""
        return {
            "apply": self.apply_changes,
            "days_already_marked_at_every_rung": self.days_already_complete,
            "days_examined": self.days_examined,
            "days_needing_markers": self.days_needing_markers,
            "failures": list(self.failures),
            "kind": self.kind,
            "lane": self.lane,
            "markers_owed": self.markers_owed,
            "markers_written": self.markers_written,
            "published_days_left_untouched": self.days_published,
            # PRICED BEFORE IT IS PAID, like the bootstrap compiler's `apply_projected_cost`. Each
            # owed rung costs one LIST and one HEAD in `write_absence_ladder`'s pre-flight plus one
            # PUT, and each day needing markers costs one GET to read its base evidence.
            "projected_apply_requests": {
                "get": self.days_needing_markers,
                "head": self.markers_owed,
                "list": self.markers_owed,
                "put": self.markers_owed,
            },
            "refused_day_count": len(self.refused),
            "refused_days": [verdict.to_wire() for verdict in self.refused],
            "refused_days_by_reason": dict(
                sorted(Counter(verdict.refusal for verdict in self.refused if verdict.refusal is not None).items())
            ),
        }


def main(argv: Sequence[str] | None = None) -> int:
    """Walk every requested lane and print one receipt per lane; a failed lane never stops the others."""
    arguments = _parse_arguments(argv)
    lanes = _resolve_lanes(arguments)
    store = ObjectStore.from_settings()
    receipts: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for lane in lanes:
        try:
            result = backfill_lane(
                store,
                layer=lane.layer,
                kind=arguments.kind,
                apply_changes=arguments.apply_changes,
                workers=arguments.workers,
            )
        except (OSError, ValueError, RuntimeError) as error:
            failures.append({"error": str(error), "lane": lane.layer, "type": type(error).__name__})
        else:
            receipts.append(result.to_receipt())
    receipt: dict[str, object] = {
        "apply": arguments.apply_changes,
        "compiled_at": _now().isoformat(),
        "dry_run": not arguments.apply_changes,
        "failed_lanes": failures,
        "lanes": receipts,
        "markers_owed": sum(_require_int(entry, "markers_owed") for entry in receipts),
        "markers_written": sum(_require_int(entry, "markers_written") for entry in receipts),
    }
    payload = json.dumps(receipt, indent=2, sort_keys=True)
    print(payload)
    if arguments.out is not None:
        arguments.out.mkdir(parents=True, exist_ok=True)
        (arguments.out / RECEIPT_FILE_NAME).write_text(f"{payload}\n", encoding="utf-8")
    write_failed = any(entry["failures"] for entry in receipts)
    return 1 if failures or write_failed else 0


def backfill_lane(
    store: ObjectStore,
    *,
    layer: str,
    kind: PartitionKind,
    apply_changes: bool = False,
    workers: int = DEFAULT_WORKERS,
) -> LaneBackfill:
    """Classify every day of one lane from ONE walk of its four rungs, then repair the ones that qualify.

    THE CLASSIFICATION IS DECIDED FROM THE LISTING ALONE, before any object is fetched. Only a day
    that is already known to be purely absence-bearing costs a GET, and it costs exactly one: the
    base rung's marker, which is both the evidence to copy and the proof that the day is what the
    listing said it was.
    """
    result = LaneBackfill(lane=layer, kind=kind, apply_changes=apply_changes)
    ladder = _walk_ladder(store, layer=layer, kind=kind)
    verdicts = [_classify_day(day, ladder[day]) for day in sorted(ladder)]
    result.days_examined = len(verdicts)
    result.days_already_complete = sum(1 for verdict in verdicts if verdict.outcome == OUTCOME_ALREADY_COMPLETE)
    result.days_published = sum(1 for verdict in verdicts if verdict.outcome == OUTCOME_PUBLISHED)
    result.refused = [verdict for verdict in verdicts if verdict.outcome == OUTCOME_REFUSED]
    owed = tuple(verdict for verdict in verdicts if verdict.outcome == OUTCOME_NEEDS_MARKERS)
    evidence = _read_base_evidence(
        store,
        layer=layer,
        kind=kind,
        days=[verdict.day for verdict in owed],
        workers=workers,
    )
    for verdict in owed:
        absence = evidence.get(verdict.day)
        if absence is None:
            result.refused.append(
                DayVerdict(
                    day=verdict.day,
                    outcome=OUTCOME_REFUSED,
                    refusal=REFUSAL_BASE_MARKER_UNREADABLE,
                    detail=(
                        f"the base rung z{BASE_RUNG} marker could not be read or parsed, so the reason the coarse "
                        f"rungs would have to carry is unknown; inventing one is the single thing this script may "
                        f"never do"
                    ),
                )
            )
            continue
        disagreement = _reason_disagreement(
            store,
            ladder[verdict.day],
            layer=layer,
            kind=kind,
            day=verdict.day,
            base=absence,
        )
        if disagreement is not None:
            result.refused.append(
                DayVerdict(
                    day=verdict.day,
                    outcome=OUTCOME_REFUSED,
                    refusal=REFUSAL_REASONS_DISAGREE,
                    detail=disagreement,
                )
            )
            continue
        result.days_needing_markers += 1
        result.markers_owed += len(verdict.missing_rungs)
        if not apply_changes:
            continue
        try:
            written = write_absence_ladder(
                store,
                absence,
                layer=layer,
                kind=kind,
                day=verdict.day,
                tiers=verdict.missing_rungs,
            )
        except (OSError, ValueError, RuntimeError) as error:
            result.failures.append(f"{verdict.day.isoformat()}: {type(error).__name__}: {error}")
        else:
            result.markers_written += len(written)
    result.refused.sort(key=lambda verdict: verdict.day)
    return result


def _walk_ladder(store: ObjectStore, *, layer: str, kind: PartitionKind) -> dict[date, dict[int, RungObjects]]:
    """List all four rungs once each and fold them into one object inventory per lane-day.

    Four listings for a whole lane's history, and not one per-day request: the same walk
    `compile_availability_bootstrap.py::_walk_ladder` pays, for the same reason.
    """
    ladder: dict[date, dict[int, RungObjects]] = {}
    for rung in LADDER_RUNGS:
        parts: dict[date, dict[int, str]] = {}
        completions: dict[date, str] = {}
        absences: dict[date, str] = {}
        for relative_path in store.list_partition_keys(layer, kind, rung):
            partition = try_parse_partition_path(relative_path)
            if partition is not None:
                parts.setdefault(partition.day, {})[partition.part_index] = relative_path
                continue
            completion = try_parse_completion_marker_path(relative_path)
            if completion is not None:
                completions[completion.day] = relative_path
                continue
            absence = try_parse_absence_marker_path(relative_path)
            if absence is not None:
                absences[absence.day] = relative_path
        for day in set(parts) | set(completions) | set(absences):
            indexed = parts.get(day, {})
            ladder.setdefault(day, {})[rung] = RungObjects(
                rung=rung,
                part_paths=tuple(indexed[index] for index in sorted(indexed)),
                completion_path=completions.get(day),
                absence_path=absences.get(day),
            )
    return ladder


def _classify_day(day: date, rungs: dict[int, RungObjects]) -> DayVerdict:
    """Decide what one day is FROM ITS OBJECT KINDS, never from how many rungs it happens to hold.

    The order of the branches is the safety property. "Does any rung hold a part file or a completion
    marker" is asked FIRST and answered over the WHOLE day, so a day carrying one governed absence
    beside real rows can never reach the repair path by way of its absence marker. Only after that
    question is settled does rung membership matter at all.
    """
    marked = tuple(rung for rung in LADDER_RUNGS if rung in rungs and rungs[rung].absence_path is not None)
    holding_data = tuple(objects for objects in rungs.values() if objects.data_objects)
    if holding_data:
        if not marked and set(rungs) == set(LADDER_RUNGS) and all(objects.is_published for objects in rungs.values()):
            # THE NEGATIVE CONTROL, and it is silent on purpose. An ordinary published day is not this
            # script's business, and printing thousands of them as refusals would bury the handful of
            # days that genuinely are broken.
            return DayVerdict(day=day, outcome=OUTCOME_PUBLISHED)
        offender = sorted(objects.data_objects[0] for objects in holding_data)[0]
        return DayVerdict(
            day=day,
            outcome=OUTCOME_REFUSED,
            refusal=REFUSAL_DAY_HOLDS_DATA,
            detail=(
                f"{offender} is a part file or completion marker on this day, so it is not a purely absent day and "
                f"a governed absence written over it would claim the source had nothing for a day that holds rows. "
                f"This is the shape of the stranded-part export defect, not of the marker gap this script repairs"
            ),
        )
    if not marked:  # pragma: no cover - `_walk_ladder` only mints a day from an object it found
        return DayVerdict(
            day=day,
            outcome=OUTCOME_REFUSED,
            refusal=REFUSAL_DAY_HOLDS_DATA,
            detail="this day holds no part file, no completion marker and no absence marker at any rung",
        )
    if BASE_RUNG not in marked:
        return DayVerdict(
            day=day,
            outcome=OUTCOME_REFUSED,
            refusal=REFUSAL_NO_BASE_MARKER,
            detail=(
                f"rung(s) {', '.join(f'z{rung}' for rung in marked)} govern this day as absent while the base rung "
                f"z{BASE_RUNG} says nothing, so there is no proven claim to copy DOWN the ladder and a coarse rung "
                f"is asserting an absence it cannot itself prove. An admin decides whether it stands"
            ),
        )
    missing = tuple(rung for rung in LADDER_RUNGS if rung not in marked)
    if not missing:
        return DayVerdict(day=day, outcome=OUTCOME_ALREADY_COMPLETE)
    return DayVerdict(day=day, outcome=OUTCOME_NEEDS_MARKERS, missing_rungs=missing)


def _read_base_evidence(
    store: ObjectStore,
    *,
    layer: str,
    kind: PartitionKind,
    days: Sequence[date],
    workers: int,
) -> dict[date, GovernedAbsence]:
    """Fetch and parse each day's BASE-rung marker in parallel; a day that cannot be read is left out.

    Absent from the mapping means refused, never repaired with a substitute. The parse is the whole
    point of the read: `GovernedAbsence.from_json_bytes` is what the availability contract itself runs
    over these bytes (`availability_index.py::_verify_absence_object`), so a marker this cannot decode
    is one no generation could have cited anyway.
    """
    if not days:
        return {}

    def read(day: date) -> tuple[date, GovernedAbsence | None]:
        try:
            return day, store.read_absence(layer, kind, BASE_RUNG, day)
        except GovernedAbsenceError:
            return day, None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return {day: absence for day, absence in pool.map(read, days) if absence is not None}


def _reason_disagreement(  # noqa: PLR0913 - one coordinate of the day being compared per arg
    store: ObjectStore,
    rungs: dict[int, RungObjects],
    *,
    layer: str,
    kind: PartitionKind,
    day: date,
    base: GovernedAbsence,
) -> str | None:
    """Return why an already-marked coarse rung cannot stand beside the base marker, or `None`.

    ONLY THE REASON IS COMPARED, because only the reason is load-bearing:
    `_validate_generation_day` refuses a day whose rungs disagree about it, and
    `_verify_absence_object` re-proves it per rung against the row that cites it. `run_id` and
    `recorded_at` legitimately differ between a first marker and a later one over the same day, and
    refusing on those would strand days nothing is wrong with.
    """
    for rung in LADDER_RUNGS:
        objects = rungs.get(rung)
        if rung == BASE_RUNG or objects is None or objects.absence_path is None:
            continue
        try:
            existing = store.read_absence(layer, kind, rung, day)
        except GovernedAbsenceError as error:
            return (
                f"the z{rung} marker already on this day could not be parsed, so it cannot be shown to agree "
                f"with the base rung it would have to stand beside: {error}"
            )
        if existing is None:  # pragma: no cover - the listing found it; a concurrent delete is the only path
            continue
        if existing.reason != base.reason:
            return (
                f"z{rung} already states {existing.reason!r} while the base rung z{BASE_RUNG} states {base.reason!r}; "
                f"a day whose rungs disagree about WHY it is empty is refused by "
                f"availability_index._validate_generation_day, and choosing between two governed claims is an "
                f"admin decision"
            )
    return None


def _resolve_lanes(arguments: argparse.Namespace) -> tuple[CensusLane, ...]:
    """Return the lanes to walk, refusing a lane with no time axis; identical selection to the compiler."""
    time_bearing = tuple(lane for lane in registered_census_lanes() if nature_has_time_axis(lane.nature))
    if arguments.all_time_bearing:
        return time_bearing
    by_layer = {lane.layer: lane for lane in time_bearing}
    unknown = [slug for slug in arguments.lane if slug not in by_layer]
    if unknown:
        known = ", ".join(sorted(by_layer))
        raise SystemExit(f"unknown or non-time-bearing lane(s): {', '.join(unknown)}; time-bearing lanes are: {known}")
    return tuple(by_layer[slug] for slug in arguments.lane)


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lane", action="append", default=[], help="Lane slug to walk; repeatable.")
    parser.add_argument(
        "--all-time-bearing",
        action="store_true",
        help="Walk every registered lane whose partition day is a time the source itself stamped.",
    )
    parser.add_argument("--kind", default="observed", choices=("observed", "forecast"), help="Stream kind.")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="Parallel base-marker reads.")
    parser.add_argument("--out", type=Path, default=None, help="Directory to also persist receipt.json into.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Walk, classify and report, writing nothing. THIS IS THE DEFAULT and passing it changes "
            "nothing; it exists so an operator can say what they mean, and so passing it beside "
            "--apply is a refusal rather than a coin toss."
        ),
    )
    parser.add_argument(
        "--apply",
        dest="apply_changes",
        action="store_true",
        help="Write the missing coarse absence markers. THE ONLY PATH THAT WRITES ANYTHING.",
    )
    arguments = parser.parse_args(argv)
    if not arguments.lane and not arguments.all_time_bearing:
        parser.error("pass --lane at least once, or --all-time-bearing")
    if arguments.workers < 1:
        parser.error("--workers must be at least 1")
    if arguments.dry_run and arguments.apply_changes:
        # Refused rather than resolved by precedence. Whichever way it resolved, half the operators
        # who typed both would be surprised, and one of those halves would be surprised by a write.
        parser.error("--dry-run and --apply contradict each other; pass exactly one, or neither for a dry run")
    return arguments


def _require_int(document: dict[str, object], key: str) -> int:
    """Narrow one value out of a receipt this module built, naming the key when it is not an integer.

    The receipts are `dict[str, object]` at the boundary rather than `TypedDict` on purpose; see
    `scripts/AGENTS.md`, "Why `mypy` now covers this directory".
    """
    value = document[key]
    if not isinstance(value, int):
        raise BackfillError(f"lane receipt {key!r} came back as {type(value).__name__}, not an integer")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
