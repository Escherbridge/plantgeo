"""Typed serving refusals: one stable code per reason a bounded read states nothing.

Layer L4. The code vocabulary mirrors agri-data-service's `parquet_ops/faults.py` shape (a code, a
message, no transport status), and the rendering matches its `_refusal_body` envelope. Rationale
lives in `AGENTS.md` in this directory.
"""

from __future__ import annotations

from typing import Final

#: The four partition-day statuses a bounded read can meet, and what each one is called on the wire.
#: Spelled here rather than derived from `PartitionDayStatus`, because the wire vocabulary is a
#: contract a client branches on and a status rename must not silently re-code an answer.
PARTITION_DAY_NOT_WRITTEN: Final = "partition_day_not_written"
PARTITION_DAY_GOVERNED_ABSENCE: Final = "partition_day_governed_absence"
PARTITION_DAY_INCOMPLETE: Final = "partition_day_incomplete"
PARTITION_DAY_CONFLICT: Final = "partition_day_conflict"

#: Availability vocabulary, copied from the sibling's `parquet_ops/wire.py` withholding names so an
#: operator reading both services reads one word for one fault.
AVAILABILITY_UNPUBLISHED: Final = "availability_unpublished"
AVAILABILITY_MALFORMED: Final = "availability_malformed"
AVAILABILITY_DAY_NOT_COVERED: Final = "availability_day_not_covered"
AVAILABILITY_NO_PUBLISHED_DAY: Final = "availability_no_published_day"

FORECAST_WINDOW_UNWRITTEN: Final = "forecast_window_unwritten"
CELL_NOT_COVERED: Final = "cell_not_covered"
CELL_SERIES_ABSENT: Final = "cell_series_absent"
ARTIFACT_KIND_UNKNOWN: Final = "artifact_kind_unknown"
ARTIFACT_UNREADABLE: Final = "artifact_unreadable"
LANE_UNKNOWN: Final = "lane_unknown"
LANE_NOT_FORECAST: Final = "lane_not_forecast"
READ_OVER_BUDGET: Final = "read_over_budget"
READ_TIMED_OUT: Final = "read_timed_out"
SERVING_AT_CAPACITY: Final = "serving_at_capacity"
SERVING_FAULT: Final = "serving_fault"
OBJECT_STORE_UNCONFIGURED: Final = "object_store_unconfigured"
INVALID_REQUEST: Final = "invalid_request"


class MachineLearningRefusalError(Exception):
    """A read that reached the warehouse and refuses to state what a day holds, with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_wire(self) -> dict[str, object]:
        """Render this refusal in the envelope the sibling's serving plane already uses."""
        return {"error": {"code": self.code, "message": self.message}}


def day_not_written(*, layer: str, day: str) -> MachineLearningRefusalError:
    """Neither a part nor a marker: a real gap, and nothing may be said about it."""
    return MachineLearningRefusalError(
        PARTITION_DAY_NOT_WRITTEN,
        f"{layer} {day} holds neither a part file nor a governed-absence marker, so nothing was ever written "
        "for it; answering zero risk here would state a measurement nobody made",
    )


def day_governed_absence(*, layer: str, day: str) -> MachineLearningRefusalError:
    """The lane deliberately holds nothing for this day, and says so with a marker."""
    return MachineLearningRefusalError(
        PARTITION_DAY_GOVERNED_ABSENCE,
        f"{layer} {day} is a governed absence: the run looked at the day and deliberately produced no rows",
    )


def day_incomplete(*, layer: str, day: str) -> MachineLearningRefusalError:
    """Parts with no completion marker: half a release, and half a release is not a day."""
    return MachineLearningRefusalError(
        PARTITION_DAY_INCOMPLETE,
        f"{layer} {day} holds part files with no completion marker, so its write has not finished; serving a "
        "prefix of a run would put an unfinished forecast on the map",
    )


def day_conflict(*, layer: str, day: str) -> MachineLearningRefusalError:
    """Both a release and a governed absence; serving either half would pick a side."""
    return MachineLearningRefusalError(
        PARTITION_DAY_CONFLICT,
        f"{layer} {day} carries both part files and a governed-absence marker; serving one of them would pick "
        "a side, and retracting either is a manual admin action",
    )


def forecast_window_unwritten(*, layer: str, first_day: str, last_day: str) -> MachineLearningRefusalError:
    """Not one day of a whole horizon window holds a readable partition."""
    return MachineLearningRefusalError(
        FORECAST_WINDOW_UNWRITTEN,
        f"{layer} holds no readable forecast partition anywhere in {first_day}..{last_day}; the run this window "
        "belongs to either never wrote or was never completed, and neither is a forecast",
    )


def cell_not_covered(*, layer: str, day: str, longitude: float, latitude: float) -> MachineLearningRefusalError:
    """The day was written and holds no cell near the point asked for."""
    return MachineLearningRefusalError(
        CELL_NOT_COVERED,
        f"{layer} {day} holds no cell within the search radius of ({longitude}, {latitude}); the lane covers a "
        "bounded footprint and a point outside it is refused rather than answered from the nearest lane",
    )


def cell_series_absent(*, layer: str, cell_id: str, origin: str) -> MachineLearningRefusalError:
    """The origin's partitions exist and carry no row for the cell asked for."""
    return MachineLearningRefusalError(
        CELL_SERIES_ABSENT,
        f"{layer} holds no rows for cell {cell_id!r} issued from {origin}; the run either refused this cell or "
        "never reached it, and neither is a forecast",
    )


def availability_unpublished(*, layer: str) -> MachineLearningRefusalError:
    """The lane has no availability pointer, so no day of it is selectable (FR-4a)."""
    return MachineLearningRefusalError(
        AVAILABILITY_UNPUBLISHED,
        f"{layer} kind=forecast has no availability pointer; writing a partition does not publish it, so no day "
        "of this lane is selectable yet",
    )


def availability_malformed(*, layer: str, detail: str) -> MachineLearningRefusalError:
    """The pointer exists and is not a pointer this reader can trust.

    `detail` names the SHAPE that failed, never the object key that held it: a key in a public body
    tells a caller where the warehouse keeps things, which is not part of the question they asked.
    """
    return MachineLearningRefusalError(
        AVAILABILITY_MALFORMED,
        f"{layer} kind=forecast carries an availability pointer this reader cannot decode ({detail}); a pointer "
        "that does not parse proves nothing about the days it names",
    )


def availability_day_not_covered(*, layer: str, day: str, detail: str) -> MachineLearningRefusalError:
    """The pointer is healthy and the day asked for sits outside the generation it names."""
    return MachineLearningRefusalError(
        AVAILABILITY_DAY_NOT_COVERED,
        f"{layer} kind=forecast publishes {detail}, which does not cover {day}; a day outside the published "
        "generation is not selectable even when part files exist under its prefix",
    )


def availability_no_published_day(*, layer: str) -> MachineLearningRefusalError:
    """The generation parses and every day in it is owed or absented, so it publishes nothing."""
    return MachineLearningRefusalError(
        AVAILABILITY_NO_PUBLISHED_DAY,
        f"{layer}'s current generation carries no day in the published terminal state; a lane whose every day "
        "is a governed absence has published a decision, not a forecast",
    )


def lane_unknown(*, layer: str, known: tuple[str, ...]) -> MachineLearningRefusalError:
    """A lane slug this service pins no schema for."""
    return MachineLearningRefusalError(
        LANE_UNKNOWN,
        f"lane {layer!r} has no pinned stream schema in this service; it knows {known}",
    )


def lane_not_forecast(*, layer: str, detail: str) -> MachineLearningRefusalError:
    """A lane that exists and publishes no `kind=forecast` stream."""
    return MachineLearningRefusalError(LANE_NOT_FORECAST, f"lane {layer!r} publishes no forecast stream: {detail}")


def artifact_kind_unknown(*, kind: str, known: tuple[str, ...]) -> MachineLearningRefusalError:
    """An artifact family this service does not write."""
    return MachineLearningRefusalError(
        ARTIFACT_KIND_UNKNOWN,
        f"model kind {kind!r} is not written by this service; it writes {known}",
    )


def artifact_unreadable(*, kind: str, detail: str) -> MachineLearningRefusalError:
    """A listed artifact object cannot be decoded, which is never reported as "no artifacts".

    The key is logged, not rendered: a caller learns that ONE artifact of this kind is corrupt,
    which is the actionable fact, without being handed the warehouse's internal layout.
    """
    return MachineLearningRefusalError(
        ARTIFACT_UNREADABLE,
        f"one stored {kind} artifact could not be read as a canonical JSON document ({detail}); reporting it "
        "as absent would hide a corrupt artifact behind an empty list",
    )


def read_over_budget(*, operation: str, detail: str) -> MachineLearningRefusalError:
    """The read does not fit its bound. A serving limit, never a claim about content."""
    return MachineLearningRefusalError(
        READ_OVER_BUDGET,
        f"the {operation} read does not fit its bound ({detail}); narrow the request. This is a serving limit "
        "and says nothing about what the warehouse holds",
    )


def read_timed_out(*, operation: str, timeout_seconds: float) -> MachineLearningRefusalError:
    """The read did not finish inside its budget."""
    return MachineLearningRefusalError(
        READ_TIMED_OUT,
        f"the {operation} read did not finish inside {timeout_seconds:.0f}s; this is a serving fault and says "
        "nothing about what the warehouse holds",
    )


def serving_at_capacity(*, operation: str, concurrent_reads: int) -> MachineLearningRefusalError:
    """Every read slot is taken. Refused rather than queued: a retry would deepen the queue."""
    return MachineLearningRefusalError(
        SERVING_AT_CAPACITY,
        f"the {operation} read found all {concurrent_reads} serving slots busy; each one holds a memory-capped "
        "DuckDB session, so the read is refused rather than queued",
    )


def serving_fault(*, operation: str, fault: str) -> MachineLearningRefusalError:
    """An unexpected fault, rendered as a refusal so it can never read as a claim about content."""
    return MachineLearningRefusalError(
        SERVING_FAULT,
        f"the {operation} read failed with an unexpected {fault}; this is a serving fault and says nothing "
        "about what the warehouse holds",
    )


def object_store_unconfigured() -> MachineLearningRefusalError:
    """No bucket coordinates, so there is nothing to read from and no other store to fall back to.

    The names of the variables still missing are an OPERATOR fact and are logged; putting them in
    the body tells an anonymous caller how this deployment is wired in exchange for nothing.
    """
    return MachineLearningRefusalError(
        OBJECT_STORE_UNCONFIGURED,
        "this process has no object-store coordinates; there is no second store to answer from",
    )


def invalid_request(*, detail: str) -> MachineLearningRefusalError:
    """The caller, not the warehouse, was wrong."""
    return MachineLearningRefusalError(INVALID_REQUEST, detail)
