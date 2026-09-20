"""Complete missing derived rungs for a bounded range of physically partial lane-days."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Final
from uuid import uuid4

SERVICE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from agri_data_service.config import settings  # noqa: E402
from agri_data_service.db.engine import local_source_loader_session  # noqa: E402
from agri_data_service.foundation.canonical import sha256_digest  # noqa: E402
from agri_data_service.foundation.parquet.completion import CompletedPart, PartitionCompletion  # noqa: E402
from agri_data_service.foundation.parquet.paths import (  # noqa: E402
    try_parse_absence_marker_path,
    try_parse_completion_marker_path,
    try_parse_partition_path,
)
from agri_data_service.pipeline.parquet.derivation import (  # noqa: E402
    DerivationResult,
    derive_and_write_day_tiers,
)
from agri_data_service.pipeline.parquet.gap_fill import _lane_day_lock_key, postgres_lane_day_lock  # noqa: E402
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY  # noqa: E402
from agri_data_service.pipeline.parquet.objectstore import ObjectStore  # noqa: E402
from agri_data_service.pipeline.parquet.publication_barrier import postgres_lane_publication_barrier  # noqa: E402
from agri_data_service.warehouse.parquet.tiers import BASE_ZOOM_TIER, DERIVED_ZOOM_TIERS  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier
    from agri_data_service.pipeline.parquet.lane_registry import LaneRegistration

    LaneDayLock = Callable[[AsyncSession, str], AbstractAsyncContextManager[bool]]
    PublicationBarrier = Callable[[AsyncSession, str], AbstractAsyncContextManager[bool]]

KIND: Final[PartitionKind] = "observed"
BASE_RUNG: Final[ZoomTier] = BASE_ZOOM_TIER
LADDER_RUNGS: Final[tuple[ZoomTier, ...]] = (*DERIVED_ZOOM_TIERS, BASE_RUNG)
MAX_RANGE_DAYS: Final = 366
SUPPORTED_NATURES: Final = frozenset(("daily_series", "static_lookup"))


class LadderCompletionError(RuntimeError):
    """The requested range or one physical day is unsafe to repair automatically."""


@dataclass(frozen=True, slots=True)
class RungState:
    """The listed and decoded state of one rung."""

    tier: ZoomTier
    parts: tuple[str, ...]
    has_absence: bool
    completion: PartitionCompletion | None
    completion_sha256: str | None = None
    physical_parts: tuple[CompletedPart, ...] = ()

    @property
    def complete(self) -> bool:
        return self.completion is not None and (
            (bool(self.parts) and not self.completion.derived_empty)
            or (not self.parts and self.completion.derived_empty)
        )


@dataclass(frozen=True, slots=True)
class DayPlan:
    """One day's immutable preflight decision."""

    day: date
    states: tuple[RungState, ...]
    base_receipts: tuple[CompletedPart, ...] = ()
    missing_derived: tuple[ZoomTier, ...] = ()
    legacy_completion_rungs: tuple[ZoomTier, ...] = ()
    base_marker_missing: bool = False
    refusal: str | None = None

    @property
    def repairable(self) -> bool:
        return self.refusal is None and bool(
            self.missing_derived or self.legacy_completion_rungs or self.base_marker_missing
        )

    def to_wire(self) -> dict[str, object]:
        legacy_receipts = [
            {
                "completion_sha256": state.completion_sha256,
                "parts": [part.to_wire() for part in state.physical_parts],
                "rung": state.tier,
            }
            for state in self.states
            if state.tier in self.legacy_completion_rungs
        ]
        return {
            "base_marker_missing": self.base_marker_missing,
            "day": self.day.isoformat(),
            "legacy_completion_receipts": legacy_receipts,
            "legacy_completion_rungs": list(self.legacy_completion_rungs),
            "missing_derived_rungs": list(self.missing_derived),
            "refusal": self.refusal,
            "status": "refused" if self.refusal else ("repairable" if self.repairable else "complete"),
        }


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _days(first: date, last: date) -> tuple[date, ...]:
    count = (last - first).days + 1
    if count < 1:
        raise LadderCompletionError("--from-day must not be after --through-day")
    if count > MAX_RANGE_DAYS:
        raise LadderCompletionError(f"a range may contain at most {MAX_RANGE_DAYS} days, got {count}")
    return tuple(first + timedelta(days=offset) for offset in range(count))


def _validate_lane(layer: str) -> None:
    registration = LANE_REGISTRY.get(layer)
    if registration is None:
        raise LadderCompletionError(f"unknown registered lane {layer!r}")
    if registration.nature not in SUPPORTED_NATURES:
        raise LadderCompletionError(
            f"lane {layer!r} is {registration.nature}; this repair supports only physical daily-series days "
            "and static-lookup version days, never release-series publication semantics"
        )


def _keys_for_day(store: ObjectStore, layer: str, day: date, tier: ZoomTier) -> tuple[str, ...]:
    return tuple(
        key
        for key in store.list_partition_keys(layer, KIND, tier, year=day.year, month=day.month)
        if (
            ((part := try_parse_partition_path(key)) is not None and part.day == day)
            or ((absence := try_parse_absence_marker_path(key)) is not None and absence.day == day)
            or ((completion := try_parse_completion_marker_path(key)) is not None and completion.day == day)
        )
    )


def inspect_day(  # noqa: PLR0912 - each branch refuses one distinct physical-ladder conflict
    store: ObjectStore, *, layer: str, day: date
) -> DayPlan:
    """Classify one physical day and bind every preserved marker to its Parquet payload."""
    states: list[RungState] = []
    problems: list[str] = []
    legacy_completion_rungs: list[ZoomTier] = []
    for tier in LADDER_RUNGS:
        keys = _keys_for_day(store, layer, day, tier)
        parts = tuple(sorted(key for key in keys if try_parse_partition_path(key) is not None))
        absences = tuple(key for key in keys if try_parse_absence_marker_path(key) is not None)
        completion_keys = tuple(key for key in keys if try_parse_completion_marker_path(key) is not None)
        if len(absences) > 1 or len(completion_keys) > 1:
            problems.append(f"z{tier} has duplicate marker names")
        receipt = store.read_completion_receipt(layer, KIND, tier, day) if completion_keys else None
        marker = None if receipt is None else receipt.completion
        physical_parts: tuple[CompletedPart, ...] = ()
        if absences:
            problems.append(f"z{tier} carries a governed-absence marker")
        if marker is not None:
            if marker.derived_empty:
                if parts:
                    problems.append(f"z{tier} has parts beside a derived-empty completion")
            elif marker.part_count != len(parts):
                problems.append(f"z{tier} completion claims {marker.part_count} part(s), listing contains {len(parts)}")
            else:
                physical = store.read_partition_with_receipts(layer, KIND, tier, day)
                physical_parts = tuple(
                    CompletedPart(
                        relative_path=part.relative_path,
                        row_count=part.row_count,
                        byte_count=part.byte_count,
                        sha256=part.sha256,
                    )
                    for part in sorted(physical.parts, key=lambda item: item.relative_path)
                )
                physical_paths = tuple(part.relative_path for part in physical_parts)
                physical_population_matches = physical_paths == parts
                if not physical_population_matches:
                    problems.append(
                        f"z{tier} listing names {len(parts)} part(s), physical read returned "
                        f"{len(physical_parts)} different part(s)"
                    )
                physical_rows = sum(part.row_count for part in physical_parts)
                if marker.row_count != physical_rows:
                    problems.append(
                        f"z{tier} completion claims {marker.row_count} row(s), physical parts hold {physical_rows}"
                    )
                if marker.parts and marker.parts != physical_parts:
                    problems.append(f"z{tier} completion part identities do not match the physical parts")
                elif not marker.parts and marker.row_count == physical_rows and physical_population_matches:
                    legacy_completion_rungs.append(tier)
        elif parts and tier != BASE_RUNG:
            problems.append(f"z{tier} has unclosed derived parts")
        elif parts:
            physical = store.read_partition_with_receipts(layer, KIND, tier, day)
            physical_parts = tuple(
                CompletedPart(
                    relative_path=part.relative_path,
                    row_count=part.row_count,
                    byte_count=part.byte_count,
                    sha256=part.sha256,
                )
                for part in sorted(physical.parts, key=lambda item: item.relative_path)
            )
            if tuple(part.relative_path for part in physical_parts) != parts:
                problems.append(
                    f"z{tier} listing names {len(parts)} part(s), physical read returned "
                    f"{len(physical_parts)} different part(s)"
                )
        state = RungState(
            tier=tier,
            parts=parts,
            has_absence=bool(absences),
            completion=marker,
            completion_sha256=None if receipt is None else receipt.sha256,
            physical_parts=physical_parts,
        )
        states.append(state)

    base = next(state for state in states if state.tier == BASE_RUNG)
    if not base.parts:
        problems.append(f"z{BASE_RUNG} has no source parts")
    if base.completion is not None and not base.complete:
        problems.append(f"z{BASE_RUNG} completion does not close its source parts")
    derived_states = tuple(state for state in states if state.tier != BASE_RUNG)
    base_marker_missing = bool(base.parts and base.completion is None)
    if base_marker_missing and any(state.complete for state in derived_states):
        problems.append("z13 has no completion marker while a derived rung is already complete")
    missing = tuple(
        tier for tier in DERIVED_ZOOM_TIERS if not next(state for state in states if state.tier == tier).complete
    )
    return DayPlan(
        day=day,
        states=tuple(states),
        base_receipts=base.physical_parts,
        missing_derived=missing,
        legacy_completion_rungs=tuple(legacy_completion_rungs),
        base_marker_missing=base_marker_missing,
        refusal="; ".join(problems) if problems else None,
    )


def complete_day(  # noqa: PLR0913 - explicit seams keep object writes and clocks testable
    store: ObjectStore,
    *,
    layer: str,
    day: date,
    run_id: str,
    now: Callable[[], datetime] = _now,
    derive: Callable[..., DerivationResult] = derive_and_write_day_tiers,
    expected_plan: DayPlan | None = None,
) -> DayPlan:
    """Re-preflight and complete one repairable day, leaving availability untouched."""
    plan = inspect_day(store, layer=layer, day=day)
    if expected_plan is not None and plan != expected_plan:
        raise LadderCompletionError(f"{layer} {day.isoformat()}: physical ladder changed after locked preflight")
    if plan.refusal:
        raise LadderCompletionError(f"{layer} {day.isoformat()}: {plan.refusal}")
    if not plan.repairable:
        return plan

    base = store.read_partition_with_receipts(layer, KIND, BASE_RUNG, day)
    if not base.parts:
        raise LadderCompletionError(f"{layer} {day.isoformat()}: z{BASE_RUNG} yielded no readable source parts")
    exact_base = tuple(
        CompletedPart(
            relative_path=part.relative_path,
            row_count=part.row_count,
            byte_count=part.byte_count,
            sha256=part.sha256,
        )
        for part in sorted(base.parts, key=lambda item: item.relative_path)
    )
    if exact_base != plan.base_receipts:
        raise LadderCompletionError(f"{layer} {day.isoformat()}: z13 source receipts changed before the first PUT")
    if plan.missing_derived:
        derive(
            store,
            layer=layer,
            kind=KIND,
            day=day,
            run_id=run_id,
            now=now,
            base_table=base.table,
            tiers=plan.missing_derived,
        )
    expected_upgrades: dict[ZoomTier, tuple[PartitionCompletion, str]] = {}
    for tier in plan.legacy_completion_rungs:
        state = next(item for item in plan.states if item.tier == tier)
        if state.completion is None or not state.physical_parts:
            raise LadderCompletionError(
                f"{layer} {day.isoformat()} z{tier}: legacy completion lost its pinned marker or parts"
            )
        upgraded = PartitionCompletion(
            part_count=len(state.physical_parts),
            row_count=sum(part.row_count for part in state.physical_parts),
            completed_at=now(),
            run_id=run_id,
            parts=state.physical_parts,
        )
        store.write_completion_marker(
            upgraded,
            layer=layer,
            kind=KIND,
            zoom=tier,
            day=day,
        )
        expected_upgrades[tier] = (upgraded, sha256_digest(upgraded.to_json_bytes()))
    if plan.base_marker_missing:
        completed_parts = tuple(
            CompletedPart(
                relative_path=part.relative_path,
                row_count=part.row_count,
                byte_count=part.byte_count,
                sha256=part.sha256,
            )
            for part in sorted(base.parts, key=lambda item: item.relative_path)
        )
        store.write_completion_marker(
            PartitionCompletion(
                part_count=len(completed_parts),
                row_count=sum(part.row_count for part in completed_parts),
                completed_at=now(),
                run_id=run_id,
                parts=completed_parts,
            ),
            layer=layer,
            kind=KIND,
            zoom=BASE_RUNG,
            day=day,
        )
    final = inspect_day(store, layer=layer, day=day)
    if final.refusal or final.repairable:
        raise LadderCompletionError(
            f"{layer} {day.isoformat()}: apply ended without a complete ladder: "
            f"{final.refusal or final.missing_derived}"
        )
    for tier, (expected, expected_sha256) in expected_upgrades.items():
        final_state = next(item for item in final.states if item.tier == tier)
        planned_state = next(item for item in plan.states if item.tier == tier)
        if (
            final_state.completion != expected
            or final_state.completion_sha256 != expected_sha256
            or final_state.physical_parts != planned_state.physical_parts
        ):
            raise LadderCompletionError(
                f"{layer} {day.isoformat()} z{tier}: upgraded completion or physical parts changed after PUT"
            )
    return final


async def complete_range(  # noqa: PLR0913 - one bounded operator request plus its ownership seams
    store: ObjectStore,
    *,
    layer: str,
    first: date,
    last: date,
    apply_changes: bool = False,
    run_id: str | None = None,
    session: AsyncSession | None = None,
    publication_barrier: PublicationBarrier = postgres_lane_publication_barrier,
    lane_day_lock: LaneDayLock = postgres_lane_day_lock,
    now: Callable[[], datetime] = _now,
    derive: Callable[..., DerivationResult] = derive_and_write_day_tiers,
) -> dict[str, object]:
    """Plan or apply one bounded lane range and return its machine-readable receipt."""
    _validate_lane(layer)
    requested_days = _days(first, last)
    plans = [inspect_day(store, layer=layer, day=day) for day in requested_days]
    refused = [plan for plan in plans if plan.refusal]
    if apply_changes and refused:
        named = ", ".join(f"{plan.day.isoformat()} ({plan.refusal})" for plan in refused)
        raise LadderCompletionError(
            f"{layer}: range preflight refused {len(refused)} day(s); no writes were attempted: {named}"
        )
    failures: list[dict[str, str]] = []
    applied: list[str] = []
    effective_run_id = run_id or f"ladder-completion-{uuid4()}"
    if apply_changes:
        if session is None:
            raise LadderCompletionError("--apply requires one pinned database session for publication/day ownership")
        registration: LaneRegistration = LANE_REGISTRY[layer]
        lane_root = f"layer={layer}/kind={KIND}"
        async with AsyncExitStack() as ownership:
            if not await ownership.enter_async_context(publication_barrier(session, lane_root)):
                raise LadderCompletionError(f"{lane_root}: publication barrier is contended; no writes were attempted")
            for day in requested_days:
                if not await ownership.enter_async_context(
                    lane_day_lock(session, _lane_day_lock_key(registration, day))
                ):
                    raise LadderCompletionError(
                        f"{layer} {day.isoformat()}: lane-day lock is contended; no writes were attempted"
                    )
            locked_plans = [inspect_day(store, layer=layer, day=day) for day in requested_days]
            if locked_plans != plans:
                raise LadderCompletionError(
                    f"{layer}: physical ladder changed between dry preflight and locked preflight; "
                    "no writes were attempted"
                )
            for plan in locked_plans:
                if not plan.repairable:
                    continue
                try:
                    complete_day(
                        store,
                        layer=layer,
                        day=plan.day,
                        run_id=effective_run_id,
                        now=now,
                        derive=derive,
                        expected_plan=plan,
                    )
                except (OSError, ValueError, RuntimeError) as error:
                    failures.append({"day": plan.day.isoformat(), "error": str(error), "type": type(error).__name__})
                else:
                    applied.append(plan.day.isoformat())
    return {
        "apply": apply_changes,
        "applied_days": applied,
        "dry_run": not apply_changes,
        "failures": failures,
        "from_day": first.isoformat(),
        "lane": layer,
        "plans": [plan.to_wire() for plan in plans],
        "refused_day_count": len(refused),
        "run_id": effective_run_id,
        "through_day": last.isoformat(),
    }


def _parse_day(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from error


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lane", required=True)
    parser.add_argument("--from-day", required=True, type=_parse_day)
    parser.add_argument("--through-day", required=True, type=_parse_day)
    parser.add_argument("--run-id")
    parser.add_argument("--apply", action="store_true", help="write missing rungs; omission is a dry run")
    return parser.parse_args(argv)


async def _execute(arguments: argparse.Namespace) -> dict[str, object]:
    store = ObjectStore.from_settings()
    if not arguments.apply:
        return await complete_range(
            store,
            layer=arguments.lane,
            first=arguments.from_day,
            last=arguments.through_day,
            run_id=arguments.run_id,
        )
    async with local_source_loader_session(settings.require_local_source_loader_database_url()) as session:
        return await complete_range(
            store,
            layer=arguments.lane,
            first=arguments.from_day,
            last=arguments.through_day,
            apply_changes=True,
            run_id=arguments.run_id,
            session=session,
        )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parse_arguments(argv)
    try:
        receipt = asyncio.run(_execute(arguments))
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"error": str(error), "type": type(error).__name__}, sort_keys=True))
        return 2
    print(json.dumps(receipt, indent=2, sort_keys=True))
    return 1 if receipt["failures"] or receipt["refused_day_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
