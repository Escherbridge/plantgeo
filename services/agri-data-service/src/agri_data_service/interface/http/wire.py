"""The frozen `/api/v1/parquet` envelope, in the serving side's own vocabulary plus its renderer.

Layer L4. Pure: no I/O, no clock, no object store. See `AGENTS.md` in this directory.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from agri_data_service.foundation.parquet.lane_contract import LaneNature
    from agri_data_service.foundation.parquet.paths import PartitionKind

#: Route segments and query parameter names, spelled ONCE on the serving side.
#: `tests/interface/test_wire_agreement.py` compares every name here against
#: `tests/contract/wire_contract.py`, which is itself compared against the TypeScript client.
BASE_PATH: Final = "/api/v1/parquet"

ROUTE_DAY: Final = "day"
ROUTE_WINDOW: Final = "window"
ROUTE_RELEASE: Final = "release"
ROUTE_COVERAGE: Final = "coverage"

PARAM_LAYER: Final = "layer"
PARAM_KIND: Final = "kind"
PARAM_ZOOM: Final = "zoom"
PARAM_BBOX: Final = "bbox"
PARAM_DAY: Final = "day"
PARAM_FIRST_DAY: Final = "first_day"
PARAM_LAST_DAY: Final = "last_day"
PARAM_AS_OF: Final = "as_of"

STATE_PUBLISHED: Final = "published"
STATE_GOVERNED_ABSENCE: Final = "governed_absence"
STATE_DAY_NOT_WRITTEN: Final = "day_not_written"
STATE_LANE_NEVER_WRITTEN: Final = "lane_never_written"

#: One served row. Deliberately untyped past `object`: the warehouse has a schema per layer per
#: kind, so one row shape here would be a lie about eleven of the twelve streams.
type ServedRow = Mapping[str, object]


def render_day(value: date) -> str:
    """Render a calendar day as `YYYY-MM-DD`; never converts a zone, because a day has none."""
    return value.isoformat()


def render_instant(value: datetime) -> str:
    """Render an instant in UTC with a `Z` designator, preserving the instant exactly."""
    if value.tzinfo is None:
        raise ValueError("a timezone-naive instant cannot be rendered on the wire without inventing a zone")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def render_scalar(value: object) -> object:
    """Render one warehouse cell as JSON: days stay day-shaped, instants carry UTC, bytes go hex."""
    # `bool` is an `int`, so the two arrive here together and both are already JSON.
    if value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        # A non-finite float is not JSON. `null` states "this cell holds no number", which is what
        # a NaN in a measurement column means; emitting 0.0 would fabricate a reading.
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return render_instant(value)
    if isinstance(value, date):
        return render_day(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    # FAIL CLOSED. `str(value)` would serve a Decimal, a list, a struct or a UUID as text under a
    # schema that announced a number or an object -- a contract change nobody declared. No registered
    # schema carries one today; `union_by_name` over a drifted object is how that stops being true.
    raise ValueError(
        f"a {type(value).__name__} cell has no agreed rendering on this plane; stringifying it would put a value "
        "on the wire under a type the contract never announced"
    )


def render_row(row: ServedRow) -> dict[str, object]:
    """Render one warehouse row, cell by cell, preserving column names and order."""
    return {name: render_scalar(value) for name, value in row.items()}


@dataclass(frozen=True, slots=True)
class PublishedDay:
    """Rows the warehouse holds for the day served."""

    requested_day: date
    served_day: date
    rows: tuple[ServedRow, ...]
    truncated: bool

    def to_wire(self) -> dict[str, object]:
        """Render the `published` envelope."""
        return {
            "state": STATE_PUBLISHED,
            "requested_day": render_day(self.requested_day),
            "served_day": render_day(self.served_day),
            "rows": [render_row(row) for row in self.rows],
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class AbsenceEvidence:
    """Why one day is deliberately empty; every field mandatory, mirroring the marker on the store."""

    reason: str
    upstream_response: str
    recorded_at: datetime
    run_id: str

    def to_wire(self) -> dict[str, object]:
        """Render the evidence block carried by a `governed_absence` envelope."""
        return {
            "reason": self.reason,
            "upstream_response": self.upstream_response,
            "recorded_at": render_instant(self.recorded_at),
            "run_id": self.run_id,
        }


@dataclass(frozen=True, slots=True)
class GovernedAbsenceDay:
    """The lane looked at the day it served and the source deliberately had nothing to give."""

    requested_day: date
    served_day: date
    absence: AbsenceEvidence

    def to_wire(self) -> dict[str, object]:
        """Render the `governed_absence` envelope."""
        return {
            "state": STATE_GOVERNED_ABSENCE,
            "requested_day": render_day(self.requested_day),
            "served_day": render_day(self.served_day),
            "absence": self.absence.to_wire(),
        }


@dataclass(frozen=True, slots=True)
class DayNotWritten:
    """Neither a part nor a marker: a real gap, and nothing may be said about it."""

    requested_day: date

    def to_wire(self) -> dict[str, object]:
        """Render the `day_not_written` envelope."""
        return {"state": STATE_DAY_NOT_WRITTEN, "requested_day": render_day(self.requested_day)}


@dataclass(frozen=True, slots=True)
class LaneNeverWritten:
    """The lane has written nothing at this tier, on any day. A slider must not mount an axis over it."""

    requested_day: date

    def to_wire(self) -> dict[str, object]:
        """Render the `lane_never_written` envelope."""
        return {"state": STATE_LANE_NEVER_WRITTEN, "requested_day": render_day(self.requested_day)}


#: The four states, and the only four. Every one leaves as HTTP 200 carrying `state`.
type DayEnvelope = PublishedDay | GovernedAbsenceDay | DayNotWritten | LaneNeverWritten


def render_window(days: Sequence[DayEnvelope]) -> dict[str, object]:
    """Render a closed day range: every day stated, in the order the resolver produced them."""
    return {"days": [day.to_wire() for day in days]}


@dataclass(frozen=True, slots=True)
class DayRange:
    """A closed day range, both ends inclusive."""

    first_day: date
    last_day: date

    def __post_init__(self) -> None:
        if self.last_day < self.first_day:
            raise ValueError(f"day range {self.first_day.isoformat()}..{self.last_day.isoformat()} runs backwards")

    def to_wire(self) -> dict[str, object]:
        """Render the range under the wire's own `from`/`to` names."""
        return {"from": render_day(self.first_day), "to": render_day(self.last_day)}


@dataclass(frozen=True, slots=True)
class LaneCoverage:
    """One lane's census. Tier-agnostic: a day counts as covered when any published tier holds it."""

    layer: str
    nature: LaneNature
    kind: PartitionKind
    earliest_day: date | None
    latest_day: date | None
    gap_ranges: tuple[DayRange, ...]
    governed_absence_ranges: tuple[DayRange, ...]

    def to_wire(self) -> dict[str, object]:
        """Render one lane's census row."""
        return {
            "layer": self.layer,
            "nature": self.nature,
            "kind": self.kind,
            "earliest_day": None if self.earliest_day is None else render_day(self.earliest_day),
            "latest_day": None if self.latest_day is None else render_day(self.latest_day),
            "gap_ranges": [entry.to_wire() for entry in self.gap_ranges],
            "governed_absence_ranges": [entry.to_wire() for entry in self.governed_absence_ranges],
        }


@dataclass(frozen=True, slots=True)
class WarehouseCoverage:
    """The whole-warehouse census the slider's capability rows are built from."""

    generated_at: datetime
    lanes: tuple[LaneCoverage, ...]

    def to_wire(self) -> dict[str, object]:
        """Render the census."""
        return {
            "generated_at": render_instant(self.generated_at),
            "lanes": [lane.to_wire() for lane in self.lanes],
        }


def contiguous_ranges(days: Iterable[date]) -> tuple[DayRange, ...]:
    """Fold a set of days into ascending closed runs; a four-year lane's day list is noise."""
    ordered = sorted(set(days))
    if not ordered:
        return ()
    ranges: list[DayRange] = []
    first = previous = ordered[0]
    for day in ordered[1:]:
        if (day - previous).days == 1:
            previous = day
            continue
        ranges.append(DayRange(first_day=first, last_day=previous))
        first = previous = day
    ranges.append(DayRange(first_day=first, last_day=previous))
    return tuple(ranges)
