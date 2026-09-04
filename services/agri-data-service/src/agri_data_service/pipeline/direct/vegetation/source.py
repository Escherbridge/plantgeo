"""Fetch one settled day of Sentinel-2 L2A NDVI and resolve it into governed cell-day values.

Reuses `ingest/vegetation.py::collect_ndvi_grid_records` byte-for-byte: the STAC scene search, the
windowed Cloud-Optimized GeoTIFF reads, and the 5x5 cloud-screened subsample mean are all upstream
of this module and stay owned by the ingest lane (`pipeline/direct/AGENTS.md`, "Fire detections"
records the same judgement for FIRMS: reuse the fetch, own the governance). This module owns turning
that raw, per-scene output into the ONE governed value per `(cell, day)` this writer's schema needs.

NDVI IS SPARSE BY CONSTRUCTION, UNLIKE CLIMATE/SOIL. Sentinel-2 revisits the whole PNW box roughly
every 5 days and cloud screening removes more of that (`pipeline/parquet/lane_registry.py:880-881`
records a MEASURED median 7-day gap). A settled day filling 40 of 1,568 cells is the ordinary shape
of a real day, not a partial fetch -- so, unlike `soil`'s "exactly 1,470 or refuse", this module
accepts ANY count of filled cells greater than zero. Only an ENTIRELY empty day -- no scene anywhere
in the support's extent on that UTC day -- is treated as possibly unsettled, through the same
"mirrored past" proof `pipeline/direct/AGENTS.md` names for climate and soil.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final

from agri_data_service.ingest.http import UpstreamError, upstream_client
from agri_data_service.ingest.vegetation import (
    COG_BOUNDS,
    NDVI_GRID_NAME,
    GridSampleWindow,
    collect_ndvi_grid_records,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    import httpx

    from agri_data_service.pipeline.direct.vegetation.support import VegetationSupport, VegetationSupportCell

#: Slack over the pinned cell count so a support that is exactly right never reports `truncated`.
#: `ndvi_grid_cells` stops enumerating at `max_cells`; the pinned box tiles to exactly the support's
#: own cell count, so this only ever absorbs floating-point edge inclusion at the box boundary.
_MAX_CELLS_SLACK: Final = 32

#: How a record that reports NO cloud cover at all ranks against ones that do: as the WORST candidate,
#: never as the clearest. `ingest/vegetation.py:541` reads `eo:cloud_cover` as a required STAC number,
#: so a missing value is a malformed response rather than a clear scene, and a `_select_clearest` that
#: preferred it would govern the least trustworthy reading of the day.
_UNREPORTED_CLOUD_COVER_PERCENT: Final = 100.0


class VegetationSourceError(RuntimeError):
    """Raised when a day's fetch cannot support a governed publication."""


class VegetationSourceUnsettledError(VegetationSourceError):
    """Raised when a whole-grid-empty day has no proof the mirror has moved past it."""


class VegetationTimeBudgetExhaustedError(RuntimeError):
    """Raised when the turn's wall clock ran out before or during one day's fetch.

    Deliberately NOT a `VegetationSourceError`: a source error is a statement about Earth Search or
    the COG reads, and the adapter wraps it into a lane failure. This is a statement about the turn,
    so `forward.py` catches it explicitly instead of retrying it as a fetch failure -- the same split
    `SoilTimeBudgetExhaustedError` documents.
    """


@dataclass(frozen=True, slots=True)
class VegetationCellValue:
    """One governed cell-day value: the clearest scene's reading, plus how many scenes it beat."""

    cell: VegetationSupportCell
    metric_value: float
    scene_id: str
    observed_at: datetime
    cloud_cover_percent: float
    sample_count: int
    release_count: int
    record_sha256: str


@dataclass(frozen=True, slots=True)
class VegetationSourceReceipt:
    """What this fetch asked for and how much of it Earth Search/the COGs actually answered."""

    day: date
    bbox: str
    window_start: datetime
    window_end: datetime
    cells_requested: int
    cells_filled: int
    retrieved_at: datetime
    response_sha256: str

    def as_event(self) -> dict[str, object]:
        """Render this receipt as the JSON-safe event a lane-day report or absence marker carries."""
        return {
            "day": self.day.isoformat(),
            "bbox": self.bbox,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "cells_requested": self.cells_requested,
            "cells_filled": self.cells_filled,
            "retrieved_at": self.retrieved_at.isoformat(),
            "response_sha256": self.response_sha256,
        }


@dataclass(frozen=True, slots=True)
class VegetationDaySource:
    """One day's fetch, resolved into the governed values a base-rung table is built from."""

    day: date
    values: tuple[VegetationCellValue, ...]
    receipt: VegetationSourceReceipt

    @property
    def is_governed_absence(self) -> bool:
        """True when no support cell anywhere in the box had a usable scene reading that day."""
        return len(self.values) == 0


def _record_sha256(record: Mapping[str, object]) -> str:
    """Fingerprint one raw sampled-cell record over its own canonical, sorted, UTC-rendered fields."""
    payload = json.dumps(
        {
            "cellKey": record.get("cellKey"),
            "gridName": record.get("gridName"),
            "ndvi": record.get("ndvi"),
            "observedAt": record.get("observedAt"),
            "sceneId": record.get("sceneId"),
            "cloudCover": record.get("cloudCover"),
            "sampleCount": record.get("sampleCount"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _required_number(record: Mapping[str, object], field: str, *, cell_key: str) -> float:
    """Narrow one UNTRUSTED upstream field to a finite float, as this module's own typed failure.

    Every other failure in this file surfaces as a `VegetationSourceError` the adapter can classify;
    a bare `float(...)` over a `Mapping[str, object]` would instead let a malformed STAC/COG field
    escape as a raw `TypeError`/`ValueError` from the middle of a fetch, which reads as a bug in this
    writer rather than as what it is -- a response that cannot be governed.
    """
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise VegetationSourceError(
            f"cell {cell_key}: the sampled record's {field!r} came back as {type(value).__name__}, not a number"
        )
    number = float(value)
    if not math.isfinite(number):
        raise VegetationSourceError(f"cell {cell_key}: the sampled record's {field!r} came back non-finite")
    return number


def _required_count(record: Mapping[str, object], field: str, *, cell_key: str) -> int:
    """Narrow one UNTRUSTED upstream field to a non-negative whole count, never a truncated float."""
    value = record.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise VegetationSourceError(
            f"cell {cell_key}: the sampled record's {field!r} came back as {type(value).__name__}, not a count"
        )
    if value < 0:
        raise VegetationSourceError(f"cell {cell_key}: the sampled record's {field!r} came back as {value}")
    return value


def _required_text(record: Mapping[str, object], field: str, *, cell_key: str) -> str:
    """Narrow one UNTRUSTED upstream field to a non-blank identifier, naming the field when it is not."""
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise VegetationSourceError(
            f"cell {cell_key}: the sampled record's {field!r} came back as {value!r}, which is not an identifier"
        )
    return value


def _cloud_cover_percent(record: Mapping[str, object], *, cell_key: str) -> float:
    """Read one record's cloud cover, ranking an ABSENT one worst rather than perfectly clear."""
    if record.get("cloudCover") is None:
        return _UNREPORTED_CLOUD_COVER_PERCENT
    return _required_number(record, "cloudCover", cell_key=cell_key)


def _select_clearest(
    cell: VegetationSupportCell,
    records: Sequence[Mapping[str, object]],
) -> VegetationCellValue:
    """Pick the lowest-cloud-cover record for one cell-day, carrying how many candidates it beat.

    Ordinarily one record per cell per day: Sentinel-2's own revisit cadence rarely double-covers a
    cell inside one UTC day. When it does (an orbit overlap), the CLEAREST reading is the governed
    one -- the same "clearest scenes first" preference `ingest/vegetation.py::collect_ndvi_grid_records`
    already applies when FILLING a cell, restated here for the rarer case of picking among several
    fills of the SAME cell on the SAME day. `release_count` records how many candidates existed, so a
    reader can tell a genuinely single-scene day from a resolved multi-scene one; it does not average
    them, because an area mean of two different acquisition instants is not one physical measurement.

    A REPORTED ZERO IS THE CLEAREST SCENE, NOT THE CLOUDIEST. The earlier `record.get("cloudCover",
    100.0) or 100.0` folded a genuine `0.0` into the missing-value default, so a perfectly cloud-free
    acquisition ranked LAST among its candidates -- the exact inversion of this function's purpose.
    `_cloud_cover_percent` tests for `None` explicitly instead of leaning on falsiness.
    """
    ranked = sorted(records, key=lambda record: _cloud_cover_percent(record, cell_key=cell.cell_key))
    chosen = ranked[0]
    return VegetationCellValue(
        cell=cell,
        metric_value=_required_number(chosen, "ndvi", cell_key=cell.cell_key),
        scene_id=_required_text(chosen, "sceneId", cell_key=cell.cell_key),
        observed_at=_parse_scene_instant(
            _required_text(chosen, "observedAt", cell_key=cell.cell_key), cell_key=cell.cell_key
        ),
        cloud_cover_percent=_cloud_cover_percent(chosen, cell_key=cell.cell_key),
        sample_count=_required_count(chosen, "sampleCount", cell_key=cell.cell_key),
        release_count=len(records),
        record_sha256=_record_sha256(chosen),
    )


def _parse_scene_instant(observed_at_text: str, *, cell_key: str = "unknown") -> datetime:
    """Parse the JS-millisecond ISO timestamp `ndvi_grid_record` carries, always timezone-aware."""
    try:
        parsed = datetime.fromisoformat(observed_at_text.replace("Z", "+00:00"))
    except ValueError as error:
        raise VegetationSourceError(
            f"cell {cell_key}: the sampled record's 'observedAt' {observed_at_text!r} is not an ISO instant"
        ) from error
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _day_response_sha256(records: Sequence[Mapping[str, object]]) -> str:
    """Fingerprint the whole day's retained record set under a TOTAL order, so the digest is stable.

    Sorting on `cellKey` alone is not a total order -- a Sentinel-2 revisit gives one cell several raw
    records inside one UTC day, which is exactly why `_select_clearest` exists -- and Python's stable
    sort then preserves arrival order, making this digest depend on the order Earth Search happened to
    answer in. The record's own canonical digest is the final tiebreaker, so two records that sort
    equal here are byte-identical and interchangeable by construction
    (`code_styleguides/engineering-principles.md` #3, "stable ordering for anything checksummed").
    """
    ordered = sorted(
        (str(record.get("cellKey", "")), str(record.get("sceneId", "")), _record_sha256(record)) for record in records
    )
    payload = json.dumps([digest for _, _, digest in ordered], separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def fetch_vegetation_day(
    *,
    day: date,
    support: VegetationSupport,
    deadline: float,
    client: httpx.AsyncClient | None = None,
) -> VegetationDaySource:
    """Fetch one UTC calendar day over the pinned support and resolve it to governed cell-day values.

    The window is the exact half-open UTC day `[day 00:00, day+1 00:00)` -- the same window
    `sql/pipeline/vegetation_day_export.sql:79-83` uses to recover `observed_day` from
    `agri.forecast_observation.observed_at`, so a direct day and a Postgres-governed day of the same
    date mean the same calendar day by construction.
    """
    if time.monotonic() >= deadline:
        raise VegetationTimeBudgetExhaustedError(f"the turn's time budget ran out before fetching {day.isoformat()}")
    window_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    window_end = window_start + timedelta(days=1)
    window = GridSampleWindow(
        bbox=support.bbox,
        start=window_start,
        end=window_end,
        max_cells=len(support.cells) + _MAX_CELLS_SLACK,
    )
    try:
        if client is not None:
            outcome = await collect_ndvi_grid_records(client, window)
        else:
            async with upstream_client(COG_BOUNDS) as owned_client:
                outcome = await collect_ndvi_grid_records(owned_client, window)
    except UpstreamError as error:
        raise VegetationSourceError(f"vegetation {day.isoformat()}: {error}") from error
    if outcome.truncated:
        raise VegetationSourceError(
            f"vegetation {day.isoformat()}: the grid fetch truncated at {window.max_cells} cells, which is "
            f"impossible for a box tiling to {len(support.cells)}; the pinned support and the fetch bbox "
            "have drifted apart"
        )
    # THE CELL IS RESOLVED ONCE, HERE, and carried as the grouping key's own value. Resolving again
    # at the `_select_clearest` call site returned `VegetationSupportCell | None` a second time and
    # was silenced with `# type: ignore[arg-type]`; `python.md` allows a coded ignore only for a
    # third-party no-stub gap, never to suppress an Optional this module has already narrowed.
    grouped: dict[VegetationSupportCell, list[Mapping[str, object]]] = {}
    off_lattice = 0
    for record in outcome.records:
        raw_cell_key = record.get("cellKey")
        cell = support.resolve(raw_cell_key) if isinstance(raw_cell_key, str) else None
        if cell is None:
            off_lattice += 1
            continue
        grouped.setdefault(cell, []).append(record)
    if off_lattice:
        raise VegetationSourceError(
            f"vegetation {day.isoformat()}: {off_lattice} sampled record(s) named a cellKey the pinned "
            f"{NDVI_GRID_NAME} support does not carry; the fetch bbox is not tightly bound to the support"
        )
    values = tuple(
        _select_clearest(cell, records)
        for cell, records in sorted(grouped.items(), key=lambda entry: entry[0].cell_key)
    )
    receipt = VegetationSourceReceipt(
        day=day,
        bbox=window.bbox,
        window_start=window_start,
        window_end=window_end,
        cells_requested=len(support.cells),
        cells_filled=len(values),
        retrieved_at=datetime.now(UTC),
        response_sha256=_day_response_sha256(outcome.records),
    )
    return VegetationDaySource(day=day, values=values, receipt=receipt)


__all__ = [
    "VegetationCellValue",
    "VegetationDaySource",
    "VegetationSourceError",
    "VegetationSourceReceipt",
    "VegetationSourceUnsettledError",
    "VegetationTimeBudgetExhaustedError",
    "fetch_vegetation_day",
]
