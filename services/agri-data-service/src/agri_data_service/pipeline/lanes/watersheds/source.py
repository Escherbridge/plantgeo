"""Fetch the current NHDPlus_HR WBDHU12 snapshot straight from source, bypassing PostgreSQL entirely.

Reuses `ingest.watersheds`'s fetch/parse surface (`fetch_watersheds`, `build_watershed_identity`,
`WBDHU12_BOUNDS`, `WATERSHEDS_PROPERTY_SOURCE`) rather than reimplementing the paged ArcGIS walk or
the huc12 identity rule -- the recent ingestion removal deliberately kept that module for exactly
this reason. Only the PUBLIC surface is reused; `_object_ids_query` / `_batch_query` / `_query_json`
stay private to that module.

THE WATERMARK IS THE SOURCE'S OWN VINTAGE, NEVER A POSTGRES INGESTION TIMESTAMP. WBD's own
`loaddate` -- reused here through `build_watershed_identity`'s `observed_at`, exactly as
`ingest/watersheds.py::parse_load_date` documents it -- is "when USGS loaded or last touched THIS
basin's boundary", which is what `foundation/parquet/lane_contract.py`'s `static_lookup` nature asks
a watermark to answer. `pipeline/parquet/lane_registry.py::_watersheds_watermark` answers the same
question from `geo.features.updated_at`/`created_at` instead -- an ingestion-time proxy for that
same fact, and one that goes stale the moment nothing writes `geo.features` for this layer anymore.
This module answers it directly from the source, so it keeps working after that lane retires.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from agri_data_service.foundation.parquet.lane_contract import SourceWatermark
from agri_data_service.ingest.http import upstream_client
from agri_data_service.ingest.identity import MissingNativeKeyError
from agri_data_service.ingest.watersheds import WBDHU12_BOUNDS, build_watershed_identity, fetch_watersheds

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import httpx

#: Cited in every `SourceWatermark` this module builds, so a caller reading `basis` never has to
#: guess which columns answered "when did this change" -- see `SourceWatermark.__post_init__`.
WATERMARK_BASIS: Final = (
    "NHDPlus_HR WBDHU12 loaddate (direct fetch), max across every accepted basin in the fetched extent"
)


class WatershedsSourceError(RuntimeError):
    """Raised when a non-empty fetched population cannot honestly support a watermark."""


@dataclass(frozen=True, slots=True)
class WatershedRecord:
    """One accepted HUC12 basin: the field set `ingest/watersheds.py::build_watershed_write` wrote to
    `geo.features` (deleted 2026-09-06, so THIS IS NOW THE LAST COPY), minus the two columns that only
    ever existed on a `geo.features` row (`feature_id`, `data_available_at`) -- a direct fetch has
    neither, and `rows.py` writes both honestly NULL rather than inventing them.
    """

    huc12: str
    name: str | None
    areasqkm: float | None
    tohuc: str | None
    states: str | None
    hutype: str | None
    observed_at: datetime | None
    geometry: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class WatershedsSnapshotSource:
    """One fetch of the whole configured extent: every accepted basin, when it was fetched, and its watermark."""

    bbox: str
    accepted: tuple[WatershedRecord, ...]
    rejected_count: int
    fetched_at: datetime
    watermark: SourceWatermark


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _accept(feature: Mapping[str, object]) -> WatershedRecord | None:
    """Mirror the deleted `build_watershed_write`'s field mapping and rejection rule, minus the
    two Postgres-only fields.

    Rejects on the identical condition that function did -- no `properties`/`geometry` dict, or no
    parseable `huc12` -- by delegating to the SAME `build_watershed_identity` it called, which is why
    that function was kept when the rest of the Postgres writer was deleted on 2026-09-06: the
    rejection rule has one definition, here, and `MissingNativeKeyError`'s trigger is never restated.
    """
    properties = feature.get("properties")
    geometry = feature.get("geometry")
    if not isinstance(properties, dict) or not isinstance(geometry, dict):
        return None
    try:
        identity = build_watershed_identity(properties)
    except (MissingNativeKeyError, ValueError):
        return None
    return WatershedRecord(
        huc12=identity.producer_local_id,
        name=_optional_str(properties.get("name")),
        areasqkm=_optional_float(properties.get("areasqkm")),
        tohuc=_optional_str(properties.get("tohuc")),
        states=_optional_str(properties.get("states")),
        hutype=_optional_str(properties.get("hutype")),
        observed_at=identity.observed_at,
        geometry=geometry,
    )


def _watermark(accepted: Sequence[WatershedRecord]) -> SourceWatermark:
    """Build the source watermark from the accepted population's own `loaddate` vintages.

    `day=None` is reserved for a GENUINELY empty accepted population (`resolve_static_lane` reads
    that as `source_empty`). A non-empty population with no single parseable `loaddate` is a
    different, worse fact -- basins exist but their vintage is completely unknown -- and is refused
    rather than reported as an empty source, matching `foundation/parquet/lane_contract.py`'s own
    refusal to let a version stamp be silently absent while rows exist.
    """
    if not accepted:
        return SourceWatermark(day=None, basis=f"{WATERMARK_BASIS}; the fetch returned no accepted basin")
    dated_instants = [record.observed_at for record in accepted if record.observed_at is not None]
    if not dated_instants:
        raise WatershedsSourceError(
            f"{len(accepted)} basin(s) were fetched but NONE carried a parseable loaddate; a non-empty "
            "population with a completely unknown vintage is a data-integrity failure, not a source_empty "
            "reading, and must not be reported as one"
        )
    newest = max(dated_instants)
    return SourceWatermark(day=newest.date(), basis=WATERMARK_BASIS, instant=newest)


async def fetch_watersheds_snapshot(*, bbox: str, client: httpx.AsyncClient | None = None) -> WatershedsSnapshotSource:
    """Fetch every HUC12 boundary the extent covers, accept what carries a huc12, and date the whole read.

    ONE FETCH, sequential id-batched paging exactly as `ingest.watersheds.fetch_watersheds` performs
    it -- there is no cheaper, geometry-free way to read WBD's per-basin `loaddate`: the ID-only
    query (`ingest/watersheds.py::fetch_watershed_object_ids`) never requests attribute fields at
    all, only the batched geometry query does. The watermark this returns therefore costs the exact
    same request volume the write itself does; see `forward.py`'s module docstring, "One fetch, not
    three", for why that cost is deliberately paid once per turn rather than twice or three times.
    """
    if client is None:
        async with upstream_client(WBDHU12_BOUNDS) as owned_client:
            raw_features = await fetch_watersheds(owned_client, bbox)
    else:
        raw_features = await fetch_watersheds(client, bbox)
    accepted: list[WatershedRecord] = []
    for feature in raw_features:
        record = _accept(feature)
        if record is not None:
            accepted.append(record)
    return WatershedsSnapshotSource(
        bbox=bbox,
        accepted=tuple(accepted),
        rejected_count=len(raw_features) - len(accepted),
        fetched_at=datetime.now(UTC),
        watermark=_watermark(accepted),
    )


__all__ = [
    "WATERMARK_BASIS",
    "WatershedRecord",
    "WatershedsSnapshotSource",
    "WatershedsSourceError",
    "fetch_watersheds_snapshot",
]
