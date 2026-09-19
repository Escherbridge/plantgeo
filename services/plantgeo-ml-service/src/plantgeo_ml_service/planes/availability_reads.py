"""What a lane actually PUBLISHES: its pointer, its current generation, and how far that run reached.

Layer L4. Writing a partition does not publish it (FR-4a), so every serving answer about a forecast
day is judged against the availability pointer and the generation it names, never against a listing.
Why `published_horizon_days` is measured from the generation rather than declared lives in
`AGENTS.md` in this directory.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any, Final, Literal

import pyarrow.parquet as pq  # type: ignore[import-untyped]  # pyarrow ships no stubs

from plantgeo_ml_service.foundation.parquet_paths import availability_pointer_path
from plantgeo_ml_service.planes import refusals
from plantgeo_ml_service.planes.wire import render_day

if TYPE_CHECKING:
    from collections.abc import Mapping

    from plantgeo_ml_service.foundation.parquet_paths import PartitionKind
    from plantgeo_ml_service.pipeline.object_store import ReadOnlyObjectStore

FORECAST_KIND: Final[PartitionKind] = "forecast"

#: The terminal state a day must carry to count toward the published horizon. A governed absence is
#: a published DECISION and not a published day: counting it would make a run that forecast three of
#: its thirty horizons read as a thirty-day run.
PUBLISHED_TERMINAL_STATE: Final = "published"

#: How large a generation one request may download. Generations are bounded at 256 MB by their own
#: contract; a serving read is not the place to spend that, and a lane over this ceiling is a fault
#: an operator must see rather than a request that quietly takes a minute.
MAX_SERVED_GENERATION_BYTES: Final = 16 * 1024 * 1024

#: What one generation's days MEAN, which decides where its issue day comes from.
#:
#: - `forecast_days`: every row is a day the run FORECAST, so the issue day is the day before the
#:   earliest of them (`expected_forecast_days` runs frontier+1 through the ceiling).
#: - `issue_days`: every row is one provider ISSUE, and the future lives inside that issue's file as
#:   `valid_time` rows, so the newest published row IS the issue day (`layer-lanes.md` section 2
#:   carve-out, amended 2026-09-19).
GenerationDays = Literal["forecast_days", "issue_days"]


@dataclass(frozen=True, slots=True)
class LaneAvailability:
    """One lane's published shape, read from its pointer and the generation that pointer names."""

    layer: str
    kind: PartitionKind
    pointer_key: str
    generation_key: str
    generation_sha256: str
    #: The day this generation's horizons are counted FROM.
    issue_day: date
    earliest_terminal_day: date
    latest_terminal_day: date
    source_ceiling: date
    #: The newest day the generation genuinely PUBLISHED, as opposed to owed or absented.
    newest_published_day: date
    #: MEASURED: the MAX published day minus the issue day. A run that published twelve of thirty
    #: declared horizons reads twelve here, which `source_ceiling - issue_day` never would. An
    #: INTERIOR gap does not shorten it: a run that published days 1, 2 and 12 and absented the nine
    #: between still reached day 12, and the horizon is how far it reached, not how solid it is.
    #: `None` on the `issue_days` path, where the horizon lives in the issue file's `valid_time`
    #: column and only a row read can measure it.
    published_horizon_days: int | None
    required_rungs: tuple[int, ...]

    def covers(self, day: date) -> bool:
        """Return whether one day sits inside the generation this pointer names."""
        return self.earliest_terminal_day <= day <= self.latest_terminal_day

    def to_wire(self) -> dict[str, object]:
        """Render the availability block a forecast answer carries."""
        return {
            "state": "selectable",
            "pointer_key": self.pointer_key,
            "generation_key": self.generation_key,
            "generation_sha256": self.generation_sha256,
            "issue_day": render_day(self.issue_day),
            "earliest_terminal_day": render_day(self.earliest_terminal_day),
            "latest_terminal_day": render_day(self.latest_terminal_day),
            "newest_published_day": render_day(self.newest_published_day),
            "source_ceiling": render_day(self.source_ceiling),
            "published_horizon_days": self.published_horizon_days,
            "required_rungs": list(self.required_rungs),
        }


def read_lane_availability(
    store: ReadOnlyObjectStore,
    *,
    layer: str,
    kind: PartitionKind = FORECAST_KIND,
    generation_days: GenerationDays = "forecast_days",
) -> LaneAvailability:
    """Read one lane's pointer and the generation it names, or refuse with a typed reason."""
    pointer_key = availability_pointer_path(layer, kind)
    payload = store.read_object(pointer_key)
    if payload is None:
        raise refusals.availability_unpublished(layer=layer)
    document = _decoded_pointer(payload, layer=layer)
    generation_key = _text(document, "generation_key", layer=layer)
    earliest = _day(document, "earliest_terminal_day", layer=layer)
    newest_published = _newest_published_day(store, generation_key, layer=layer)
    issue_day = earliest - timedelta(days=1) if generation_days == "forecast_days" else newest_published
    return LaneAvailability(
        layer=layer,
        kind=kind,
        pointer_key=pointer_key,
        generation_key=generation_key,
        generation_sha256=_text(document, "generation_sha256", layer=layer),
        issue_day=issue_day,
        earliest_terminal_day=earliest,
        latest_terminal_day=_day(document, "latest_terminal_day", layer=layer),
        source_ceiling=_day(document, "source_ceiling", layer=layer),
        newest_published_day=newest_published,
        # On the `issue_days` path the horizon lives inside the issue file, so nothing the
        # generation holds can measure it and a number here would be a guess.
        published_horizon_days=(newest_published - issue_day).days if generation_days == "forecast_days" else None,
        required_rungs=_rungs(document, layer=layer),
    )


def _newest_published_day(store: ReadOnlyObjectStore, generation_key: str, *, layer: str) -> date:
    """Return the newest day the current generation genuinely PUBLISHED, refusing when it published none.

    Read from the generation's own rows, and `published` only: a governed absence is a published
    DECISION and not a published day, so a run that forecast twelve of thirty horizons and absented
    the rest reads twelve here. The declared alternative, `source_ceiling - issue_day`, is the same
    number every day and can never report a run that fell short.

    The serving ceiling is applied to the MEASURED size of the generation object, never to the
    `generation_bytes` the pointer declares about itself: a pointer is the thing under suspicion
    here, and a document that both names a download and states its size bounds nothing.
    """
    measured_bytes = store.object_size(generation_key)
    if measured_bytes is None:
        raise refusals.availability_malformed(
            layer=layer, detail="the pointer names a generation this store does not hold"
        )
    if measured_bytes > MAX_SERVED_GENERATION_BYTES:
        raise refusals.read_over_budget(
            operation="availability_generation",
            detail=f"{layer} publishes a {measured_bytes}-byte generation, over the serving ceiling",
        )
    payload = store.read_object(generation_key, max_bytes=MAX_SERVED_GENERATION_BYTES)
    if payload is None:
        raise refusals.availability_malformed(
            layer=layer, detail="the pointer names a generation this store does not hold"
        )
    try:
        table = pq.read_table(io.BytesIO(payload), columns=["day", "terminal_state"])
    except Exception as error:  # pyarrow raises a family of read errors; the reason is what matters
        raise refusals.availability_malformed(
            layer=layer, detail=f"the generation is not a readable index ({type(error).__name__})"
        ) from error
    published: list[date] = []
    for row in table.to_pylist():
        day = row.get("day")
        if row.get("terminal_state") == PUBLISHED_TERMINAL_STATE and isinstance(day, date):
            published.append(day)
    if not published:
        raise refusals.availability_no_published_day(layer=layer)
    return max(published)


def _decoded_pointer(payload: bytes, *, layer: str) -> Mapping[str, Any]:
    """Decode one pointer document, refusing anything that is not a JSON object."""
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise refusals.availability_malformed(layer=layer, detail="the pointer is not decodable JSON") from error
    if not isinstance(document, dict):
        raise refusals.availability_malformed(layer=layer, detail="the pointer is not a JSON object")
    return document


def _text(document: Mapping[str, Any], field: str, *, layer: str) -> str:
    """Read one required string field out of a pointer document."""
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise refusals.availability_malformed(layer=layer, detail=f"the pointer carries no {field}")
    return value


def _day(document: Mapping[str, Any], field: str, *, layer: str) -> date:
    """Read one required `YYYY-MM-DD` field out of a pointer document."""
    value = document.get(field)
    if not isinstance(value, str):
        raise refusals.availability_malformed(layer=layer, detail=f"the pointer carries no {field}")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise refusals.availability_malformed(
            layer=layer, detail=f"the pointer's {field} {value!r} is not a calendar day"
        ) from error


def _rungs(document: Mapping[str, Any], *, layer: str) -> tuple[int, ...]:
    """Read the authoritative rung ladder the pointer binds its days to."""
    value = document.get("required_rungs")
    if not isinstance(value, list) or not value or not all(isinstance(rung, int) for rung in value):
        raise refusals.availability_malformed(layer=layer, detail="the pointer carries no required_rungs ladder")
    return tuple(int(rung) for rung in value)


__all__ = [
    "MAX_SERVED_GENERATION_BYTES",
    "PUBLISHED_TERMINAL_STATE",
    "GenerationDays",
    "LaneAvailability",
    "read_lane_availability",
]
