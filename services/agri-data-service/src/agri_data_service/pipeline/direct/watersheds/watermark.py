"""Read watersheds' version clock from NHDPlus_HR itself. This is the registered resolver's whole body.

`pipeline/parquet/lane_registry.py::_watersheds_watermark` calls exactly this, and nothing else --
the registration's `watermark` field and its `adapter` field were swapped together on 2026-09-06,
because `sql/pipeline/lane_watermark_watersheds.sql` read `geo.features` and `postgres-watersheds`
was the only writer of that table for this layer. A watermark over a table nothing writes any more
freezes at a version that never changes again; a watermark over a table that has been DROPPED fails
the census outright at `watermark_unread`.

ONE IMPLEMENTATION, NOT TWO. `source.py::fetch_watersheds_snapshot` already computes this exact
answer -- `max(loaddate)` across every accepted basin, cited as `WATERMARK_BASIS` -- because
`forward.py` needs it to decide whether a version is owed. This module is a thin, named seam so the
registry can ask the same question without importing the writer (`forward.py` imports
`pipeline/parquet/gap_fill.py`, which imports the registry, so the registry can never import
`forward.py` back). Everything here is bbox policy plus one call.

THE COST IS THE FULL WALK, AND IT IS NOT AVOIDABLE. WBD's id-only query never returns `loaddate`
(`source.py`'s module docstring), so reading this clock costs the same ~47-request, ~9,400-basin
fetch the export itself costs. Nothing calls it on a schedule: the generic `parquet-watersheds` lane
is retired in favour of `watersheds-direct-forward`, which passes its OWN memoised resolver into
`fill_one_lane_day` (`forward.py`, "One fetch, not three") rather than this one. The remaining
callers are `resolve_lane_watermarks` for an operator who explicitly asked
(`--read-watermarks`), and the census of a run that names this lane by hand.

AN UNCONFIGURED BBOX RAISES RATHER THAN ANSWERING `day=None`. Those are different claims and
`resolve_static_lane` treats them differently: `day=None` is `source_empty` ("USGS publishes no
basins here"), which would be a fabrication, while a raised read is reported as `watermark_unread`
("this run could not find out"), which is the truth. `forward.py` refuses the same turn for the same
reason before it opens a socket.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agri_data_service.ingest.policy import UNCONFIGURED_BBOX_REASON, resolve_bounded_bbox
from agri_data_service.pipeline.direct.watersheds.source import fetch_watersheds_snapshot

if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.lane_contract import SourceWatermark


class WatershedsWatermarkError(RuntimeError):
    """Raised when this run cannot ask NHDPlus_HR the question at all, which is never an empty source."""


async def read_watersheds_source_watermark(*, bbox: str | None = None) -> SourceWatermark:
    """Fetch the configured extent once and hand back the source's own `loaddate` watermark.

    Reads NO database. The `session` a `LaneWatermarkResolver` is handed goes unused one level up,
    in the registry's own wrapper, which is where that argument's `noqa` and its reason live.
    """
    resolved = resolve_bounded_bbox(bbox)
    if resolved is None:
        raise WatershedsWatermarkError(
            f"{UNCONFIGURED_BBOX_REASON}; watersheds' version clock is max(loaddate) over the basins "
            "inside the configured extent, so with no extent this run cannot read it. That is an "
            "unread watermark, never an empty source"
        )
    snapshot = await fetch_watersheds_snapshot(bbox=resolved)
    return snapshot.watermark


__all__ = ["WatershedsWatermarkError", "read_watersheds_source_watermark"]
