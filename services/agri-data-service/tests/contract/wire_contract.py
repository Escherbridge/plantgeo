"""The frozen `/api/v1/parquet` wire contract, declared once for both sides of it.

Rationale, and what a change here costs: see `AGENTS.md` in this directory.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

#: Route segments and query parameter names. Mirrors the `WIRE` block in
#: `src/lib/server/services/parquet-plane-client.ts`; the test asserts they still agree.
WIRE_BASE_PATH = "/api/v1/parquet"
WIRE_ROUTES = {"day": "day", "window": "window", "release": "release", "coverage": "coverage"}
WIRE_PARAMS = {
    "layer": "layer",
    "kind": "kind",
    "zoom": "zoom",
    "bbox": "bbox",
    "day": "day",
    "firstDay": "first_day",
    "lastDay": "last_day",
    "asOfDay": "as_of",
}

#: The four warehouse states. Every one arrives as HTTP 200 carrying `state`.
WIRE_STATES = ("published", "governed_absence", "day_not_written", "lane_never_written")

LANE_NATURES = ("daily_series", "release_series", "static_lookup")
PARTITION_KINDS = ("observed", "forecast")

#: Which evidence proved one coverage row. `availability` is one pointer GET plus one bounded
#: generation GET; `census` is the whole-stream object listing that artifact replaces.
COVERAGE_AUTHORITIES = ("availability", "census")

#: Why an availability-authority lane publishes no selectable days. Four reasons, and only four.
COVERAGE_WITHHELD_REASONS = (
    "availability_unpublished",
    "availability_stale",
    "availability_malformed",
    "availability_checksum_invalid",
)

#: `1` was the field set frozen before availability indexes existed; `2` adds the six provenance
#: fields below. A client reading a cached body uses this to tell which shape it holds.
COVERAGE_SCHEMA_VERSION = 2

#: `YYYY-MM-DD`, checked by shape only -- the server owns the calendar.
CalendarDay = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]


class _Frozen(BaseModel):
    """Rejects unknown fields: an unannounced field is a contract break, not a courtesy."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class WireAbsence(_Frozen):
    """Why a day is deliberately empty. All four fields mandatory and non-blank."""

    reason: str = Field(min_length=1)
    upstream_response: str = Field(min_length=1)
    recorded_at: str = Field(min_length=1)
    run_id: str = Field(min_length=1)


class WirePublished(_Frozen):
    """Rows for the day served. `rows` is deliberately untyped -- schema is per layer per kind."""

    state: Literal["published"]
    requested_day: CalendarDay
    served_day: CalendarDay
    rows: list[dict[str, object]]
    truncated: bool


class WireGovernedAbsence(_Frozen):
    """The lane looked and the source deliberately had nothing to give."""

    state: Literal["governed_absence"]
    requested_day: CalendarDay
    served_day: CalendarDay
    absence: WireAbsence


class WireDayNotWritten(_Frozen):
    """Neither a part nor a marker: a real gap, and nothing may be said about it."""

    state: Literal["day_not_written"]
    requested_day: CalendarDay


class WireLaneNeverWritten(_Frozen):
    """The lane has never written anything, on any day. A slider must not mount an axis over it."""

    state: Literal["lane_never_written"]
    requested_day: CalendarDay


WireEnvelope = Annotated[
    WirePublished | WireGovernedAbsence | WireDayNotWritten | WireLaneNeverWritten,
    Field(discriminator="state"),
]


class WireWindow(_Frozen):
    """Every day in the closed range, ascending. A gap is stated, never omitted."""

    days: list[WireEnvelope]


class WireDayRange(_Frozen):
    """A closed day range, both ends inclusive."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    from_: CalendarDay = Field(alias="from")
    to: CalendarDay


class WireCoverageLane(_Frozen):
    """One physical lane and published zoom rung's independently readable coverage evidence.

    The six provenance fields say WHICH evidence proved the row. `availability` rows carry the
    generation digest and pointer key an operator can fetch to re-derive them; `census` rows carry
    nulls and an empty `required_rungs`, because a listing binds no cross-rung contract.
    """

    layer: str = Field(min_length=1)
    nature: Literal["daily_series", "release_series", "static_lookup"]
    kind: Literal["observed", "forecast"]
    zoom: Literal[0, 5, 9, 13]
    earliest_day: CalendarDay | None
    latest_day: CalendarDay | None
    published_ranges: list[WireDayRange]
    gap_ranges: list[WireDayRange]
    governed_absence_ranges: list[WireDayRange]
    coverage_authority: Literal["availability", "census"]
    availability_generation_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None
    availability_pointer_key: str | None
    #: The lane's OWN horizon. `evaluated_through_day` says when the answer was computed; this says
    #: how far the lane's source reaches, so a lane behind the live edge is not read as dead.
    source_ceiling_day: CalendarDay | None
    required_rungs: list[Literal[0, 5, 9, 13]]
    withheld_reason: (
        Literal[
            "availability_unpublished",
            "availability_stale",
            "availability_malformed",
            "availability_checksum_invalid",
        ]
        | None
    )


class WireCoverage(_Frozen):
    """The whole-warehouse census the slider's capability rows are built from."""

    coverage_schema_version: int
    generated_at: str = Field(min_length=1)
    evaluated_through_day: CalendarDay
    lanes: list[WireCoverageLane]
