"""Inspect, verify and (only when told to) rebuild the warehouse coverage rollup.

The rollup is refreshed by every publication, so in steady state this script exists to ANSWER
QUESTIONS about it -- which lanes it covers, which entries their pointers no longer vouch for, and
what a cold `GET /api/v1/parquet/coverage` would still have to read in full. A warehouse whose lanes
were bootstrapped before the rollup existed has an empty one until each lane next publishes, and
`--rebuild` is how an operator fills it in a single pass instead of waiting for that.

DEFAULT IS READ-ONLY. Without `--rebuild` this script performs GETs only: one for the rollup and one
pointer per lane. `--rebuild` reads each lane's generation in full, re-verifies it exactly as the
serving path does, and merges the resulting entry under compare-and-swap -- so it is safe to run
while lanes are publishing, and it never advances or rewrites a lane pointer.

Credentials come from services/agri-data-service/.env and are never printed.

    uv run python scripts/coverage_rollup_status.py                 # from services/agri-data-service
    uv run python scripts/coverage_rollup_status.py --json
    uv run python scripts/coverage_rollup_status.py --rebuild       # WRITES the rollup object
    uv run python scripts/coverage_rollup_status.py --rebuild --lane vegetation
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

from agri_data_service.config import settings
from agri_data_service.foundation.parquet.lane_contract import nature_has_time_axis
from agri_data_service.parquet_ops.availability_coverage import lane_root, required_source_ceiling
from agri_data_service.parquet_ops.coverage import CensusLane, registered_census_lanes
from agri_data_service.parquet_ops.snapshot_products import FORWARD_PARTITION_KIND, SNAPSHOT_PRODUCTS
from agri_data_service.pipeline.parquet.availability_index import (
    AvailabilityError,
    BotoAvailabilityStorage,
    read_availability_pointer,
    read_latest_availability,
)
from agri_data_service.pipeline.parquet.coverage_rollup import (
    COVERAGE_ROLLUP_KEY,
    CoverageRollup,
    CoverageRollupMalformedError,
    entry_from_index,
    read_coverage_rollup,
    refresh_coverage_rollup_entry,
)
from agri_data_service.pipeline.parquet.objectstore import availability_lane_root

#: A verdict per lane, in the order an operator wants to read them.
FRESH = "fresh"
STALE = "stale"
ABSENT = "absent"
NO_POINTER = "no_pointer"
FAULT = "fault"


@dataclass(frozen=True, slots=True)
class LaneProbe:
    """One lane root's rollup verdict and the two digests that decided it."""

    lane_root: str
    lane: str
    verdict: str
    entry_generation_sha256: str | None
    pointer_generation_sha256: str | None
    detail: str

    def to_wire(self) -> dict[str, object]:
        """Render one probe for `--json`."""
        return {
            "detail": self.detail,
            "entry_generation_sha256": self.entry_generation_sha256,
            "lane": self.lane,
            "lane_root": self.lane_root,
            "pointer_generation_sha256": self.pointer_generation_sha256,
            "verdict": self.verdict,
        }


def availability_lane_roots() -> tuple[tuple[str, str], ...]:
    """Return every `(lane_root, lane)` the coverage answer proves from an availability index.

    Both halves: the registered TIME-BEARING lanes, and every snapshot product's forward lane. A
    `static_lookup` is deliberately absent -- it owns no index, so it owns no rollup entry either.
    """
    registered = tuple(
        (lane_root(lane), lane.layer) for lane in registered_census_lanes() if nature_has_time_axis(lane.nature)
    )
    forward = tuple(
        (availability_lane_root(product.layer, FORWARD_PARTITION_KIND), product.layer)
        for product in SNAPSHOT_PRODUCTS
        if product.forward_first_day is not None
    )
    seen: dict[str, str] = {}
    for root, lane in registered + forward:
        seen.setdefault(root, lane)
    return tuple(sorted(seen.items()))


def lane_by_layer() -> dict[str, CensusLane]:
    """Index the registered census lanes by layer, for the ceiling a staleness check needs."""
    return {lane.layer: lane for lane in registered_census_lanes()}


def probe(  # noqa: PLR0913 - one lane-identity or clock coordinate per arg, none foldable
    store: BotoAvailabilityStorage,
    rollup: CoverageRollup,
    *,
    lane_root_value: str,
    lane: str,
    now: datetime,
    registrations: dict[str, CensusLane],
) -> LaneProbe:
    """Compare one lane's rollup entry against its live pointer, without reading its generation."""
    entry = rollup.entry_for(lane_root_value)
    registration = registrations.get(lane)
    ceiling = (
        required_source_ceiling(registration, now=now)
        if registration is not None and nature_has_time_axis(registration.nature)
        else None
    )
    try:
        pointer = read_availability_pointer(store, lane_root=lane_root_value, required_source_ceiling=ceiling)
    except AvailabilityError as exc:
        verdict = NO_POINTER if "missing" in str(exc) else FAULT
        return LaneProbe(
            lane_root=lane_root_value,
            lane=lane,
            verdict=verdict,
            entry_generation_sha256=None if entry is None else entry.generation_sha256,
            pointer_generation_sha256=None,
            detail=str(exc),
        )
    if entry is None:
        return LaneProbe(
            lane_root=lane_root_value,
            lane=lane,
            verdict=ABSENT,
            entry_generation_sha256=None,
            pointer_generation_sha256=pointer.generation_sha256,
            detail="the rollup has never held this lane, so coverage reads it in full",
        )
    if entry.answers(pointer):
        return LaneProbe(
            lane_root=lane_root_value,
            lane=lane,
            verdict=FRESH,
            entry_generation_sha256=entry.generation_sha256,
            pointer_generation_sha256=pointer.generation_sha256,
            detail="the entry is bound to the pointer document this lane currently publishes",
        )
    return LaneProbe(
        lane_root=lane_root_value,
        lane=lane,
        verdict=STALE,
        entry_generation_sha256=entry.generation_sha256,
        pointer_generation_sha256=pointer.generation_sha256,
        detail="the entry describes another generation; coverage reads this lane in full and logs it",
    )


def rebuild(store: BotoAvailabilityStorage, *, lane_root_value: str, now: datetime) -> str:
    """Read one lane's index in full, re-verify it, and merge its entry under compare-and-swap."""
    index = read_latest_availability(store, lane_root=lane_root_value)
    landed = refresh_coverage_rollup_entry(store, entry=entry_from_index(index, updated_at=now))
    return "merged" if landed else "contended"


def render(probes: tuple[LaneProbe, ...], *, rollup_present: bool) -> None:
    """Print the operator report: one line per lane, then the verdict that matters."""
    print(f"rollup object: {COVERAGE_ROLLUP_KEY} ({'present' if rollup_present else 'ABSENT'})")
    print(f"{'lane_root':60s} {'verdict':10s} detail")
    for entry in probes:
        print(f"{entry.lane_root:60s} {entry.verdict:10s} {entry.detail}")
    counts = {
        verdict: sum(1 for entry in probes if entry.verdict == verdict)
        for verdict in (FRESH, STALE, ABSENT, NO_POINTER, FAULT)
    }
    served = counts[FRESH]
    full = counts[STALE] + counts[ABSENT] + counts[FAULT]
    print(
        f"\n{served} lane(s) answer from the rollup; {full} still cost a full generation read; "
        f"{counts[NO_POINTER]} have no pointer at all."
    )


def main(argv: list[str] | None = None) -> int:
    """Report the rollup's state, and rebuild it only when explicitly asked."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="emit the probe table as JSON")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="WRITE: read each stale or absent lane's generation and merge its entry into the rollup",
    )
    parser.add_argument(
        "--lane",
        action="append",
        default=[],
        help="restrict to one layer slug; repeatable. Without it every availability lane is probed.",
    )
    args = parser.parse_args(argv)

    store = BotoAvailabilityStorage.from_settings(settings)
    now = datetime.now(UTC)
    try:
        held = read_coverage_rollup(store)
    except CoverageRollupMalformedError as exc:
        print(f"the rollup object exists and is not the frozen shape: {exc}", file=sys.stderr)
        held = None
    rollup = CoverageRollup.empty() if held is None else held
    registrations = lane_by_layer()
    wanted = set(args.lane)
    roots = tuple((root, lane) for root, lane in availability_lane_roots() if not wanted or lane in wanted)
    probes = tuple(
        probe(store, rollup, lane_root_value=root, lane=lane, now=now, registrations=registrations)
        for root, lane in roots
    )

    rebuilt: dict[str, str] = {}
    if args.rebuild:
        for entry in probes:
            if entry.verdict not in {STALE, ABSENT}:
                continue
            try:
                rebuilt[entry.lane_root] = rebuild(store, lane_root_value=entry.lane_root, now=now)
            except AvailabilityError as exc:
                rebuilt[entry.lane_root] = f"refused: {exc}"

    if args.json:
        print(
            json.dumps(
                {
                    "generated_at": now.isoformat(),
                    "lanes": [entry.to_wire() for entry in probes],
                    "rebuilt": rebuilt,
                    "rollup_key": COVERAGE_ROLLUP_KEY,
                    "rollup_present": held is not None,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        render(probes, rollup_present=held is not None)
        for root, outcome in sorted(rebuilt.items()):
            print(f"rebuild {root}: {outcome}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
