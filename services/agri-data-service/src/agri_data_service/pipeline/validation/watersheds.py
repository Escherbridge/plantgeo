"""Reconcile the watersheds release against USGS WBD -- never against local intermediate state.

Layer L2: may import `foundation`, `warehouse` (the six-layer lattice), plus `agri_data_service.ingest`
(pipeline/AGENTS.md: "ingest/ and historical_*" are this same L2 layer, just outside the `pipeline/`
directory); may NOT import `method`, `planes`, or `interface`. See
`conductor/code_styleguides/layer-lanes.md` section 4 and `docs/lanes/watersheds.md` section 6.

Two checks, deliberately different in what they need:
  * `check_tohuc_integrity` needs only the release itself -- a broken downstream reference is
    checkable entirely within one export (docs/lanes/watersheds.md section 6).
  * `compare_against_source` needs the network -- detecting that USGS republished a boundary (the
    warehouse going stale) or added/retired one is not visible from the release alone.
Nothing invokes this automatically today (docs/lanes/watersheds.md section 6); it is a callable
reconciliation pass, not a registered cron.

THE TIER IS PINNED, NOT A PARAMETER: `WRITTEN_ZOOM_TIER`, the rung the exporter lands on. Both
checks depend on it. `check_tohuc_integrity` walks `huc12 -> tohuc` edges within one release, and a
scan spanning the ladder would hand it several copies of each basin, turning a clean topology into
duplicate keys. `compare_against_source` diffs the release's basins against USGS's, where a generalised
rung's coarser boundary would read as the warehouse having gone stale against a boundary USGS never
changed. Serving may legitimately read a coarser rung (`planes/watersheds.py`); a reconciliation
against the source may not, because only the base tier is what this lane transcribed from USGS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING, Final
from urllib.parse import urlencode

import polars as pl

from agri_data_service.foundation.parquet.paths import day_prefix
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.ingest.http import UpstreamPayloadError, fetch_bounded_json
from agri_data_service.ingest.watersheds import (
    WBDHU12_BATCH_SIZE,
    WBDHU12_BOUNDS,
    WBDHU12_QUERY_ENDPOINT,
    fetch_watershed_object_ids,
    parse_load_date,
)
from agri_data_service.warehouse.schemas.watersheds import WATERSHEDS_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    import httpx

    from agri_data_service.foundation.parquet.zoom import ZoomTier

# The only stream this lane ever writes (`horizon: none`, docs/lanes/watersheds.md section 7);
# hardcoded rather than an accepted parameter so this validator has no code path that could ever
# read a `kind=forecast` partition that does not exist.
_OBSERVED_KIND: Final = "observed"

# The rung the lane's own export lands on: the most detailed one, the one nothing generalised.
# Derived from the ladder so adding a rung above cannot leave this validator checking a stale tier.
WRITTEN_ZOOM_TIER: Final[ZoomTier] = ZOOM_TIERS[-1]

_LANE: Final = WATERSHEDS_STREAM

# Only the three columns a reconciliation pass needs -- never the ~17.3 KB/row geometry column,
# which `pipeline/lanes/watersheds.py`'s own measurement (ROWS_PER_PART's docstring) makes clear
# dominates the row entirely. Column-projection pushdown in `read_written_watersheds` means this
# validator's own Parquet read touches a small fraction of what a full-layer read would.
_WRITTEN_COLUMNS: Final = ("huc12", "tohuc", "observed_at")


@dataclass(frozen=True, slots=True)
class WrittenWatershedRow:
    """One release row, projected to only what reconciliation needs."""

    huc12: str
    tohuc: str | None
    observed_at: datetime | None


@dataclass(frozen=True, slots=True)
class WatershedValidationFailure:
    """One reconciliation defect: which basin, which lane, why, and what the source said."""

    huc12: str
    lane: str
    reason: str
    source_response: str


@dataclass(frozen=True, slots=True)
class WatershedValidationReport:
    """The full reconciliation result for one release day; empty tuples mean a clean release."""

    release_day: date
    tohuc_failures: tuple[WatershedValidationFailure, ...]
    source_failures: tuple[WatershedValidationFailure, ...]

    @property
    def is_clean(self) -> bool:
        """True when neither check found anything to report."""
        return not self.tohuc_failures and not self.source_failures


def _require_str(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string, got {type(value).__name__}")
    return value


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"expected a string or null, got {type(value).__name__}")
    return value


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise ValueError(f"expected a timestamp or null, got {type(value).__name__}")
    return value


def _row_to_written(row: Mapping[str, object]) -> WrittenWatershedRow:
    return WrittenWatershedRow(
        huc12=_require_str(row["huc12"], "huc12"),
        tohuc=_optional_str(row.get("tohuc")),
        observed_at=_optional_datetime(row.get("observed_at")),
    )


def _watersheds_release_glob(root: str, day: date) -> str:
    """Every part file of one release day at the WRITTEN tier -- a release is many parts, read as one glob."""
    written = day_prefix(WATERSHEDS_STREAM, _OBSERVED_KIND, WRITTEN_ZOOM_TIER, day)
    return f"{root.rstrip('/')}/{written}part-*.parquet"


def read_written_watersheds(
    root: str,
    day: date,
    *,
    storage_options: Mapping[str, str] | None = None,
) -> tuple[WrittenWatershedRow, ...]:
    """Read back what THIS lane wrote for one release day, projected to the reconciliation columns.

    Synchronous like `pipeline/parquet/objectstore.py`'s writer, and for the same reason: this
    never runs on the Sanic event loop, only from a batch reconciliation pass.
    """
    frame = (
        pl.scan_parquet(
            _watersheds_release_glob(root, day),
            storage_options=dict(storage_options) if storage_options is not None else None,
            hive_partitioning=False,
        )
        .select(list(_WRITTEN_COLUMNS))
        .collect()
    )
    return tuple(_row_to_written(row) for row in frame.iter_rows(named=True))


def check_tohuc_integrity(written: Sequence[WrittenWatershedRow]) -> tuple[WatershedValidationFailure, ...]:
    """A `tohuc` naming a HUC12 outside this release is a real defect, checkable with no network.

    Not every basin has a downstream reference -- a terminal (outlet) basin's `tohuc` is null, and
    that is never a failure here.
    """
    known = {row.huc12 for row in written}
    return tuple(
        WatershedValidationFailure(
            huc12=row.huc12,
            lane=_LANE,
            reason=f"tohuc {row.tohuc!r} does not name a HUC12 present in this release",
            source_response="not applicable -- checkable entirely within the release",
        )
        for row in written
        if row.tohuc is not None and row.tohuc not in known
    )


def _attribute_query(object_ids: Sequence[int]) -> str:
    """Build a geometry-free query for one id batch: only `huc12` and `loaddate`, no polygon."""
    return urlencode(
        {
            "objectIds": ",".join(str(object_id) for object_id in object_ids),
            "outFields": "huc12,loaddate",
            "returnGeometry": "false",
            "f": "json",
        }
    )


async def _fetch_attribute_batch(client: httpx.AsyncClient, object_ids: Sequence[int]) -> dict[str, object]:
    """Run one attribute-only NHDPlus_HR query, refusing the ArcGIS fault-behind-200 shape."""
    payload = await fetch_bounded_json(
        client,
        f"{WBDHU12_QUERY_ENDPOINT}?{_attribute_query(object_ids)}",
        WBDHU12_BOUNDS,
        {"Accept": "application/json"},
    )
    if not isinstance(payload, dict):
        raise UpstreamPayloadError("NHDPlus_HR returned a non-object body")
    if "error" in payload:
        raise UpstreamPayloadError(f"NHDPlus_HR returned an error object: {payload.get('error')}")
    return payload


async def fetch_source_huc12_vintages(client: httpx.AsyncClient, bbox: str) -> dict[str, datetime | None]:
    """Every currently-published HUC12's own loaddate vintage, fetched without geometry.

    Reuses `fetch_watershed_object_ids` (the same id-batching this endpoint already requires,
    docs/lanes/watersheds.md section 1) so this validator's own upstream footprint stays an
    attribute-only fraction of the 12.4 MB/500-basin geometry batches the ingest adapter measures.
    """
    object_ids = await fetch_watershed_object_ids(client, bbox)
    vintages: dict[str, datetime | None] = {}
    for start in range(0, len(object_ids), WBDHU12_BATCH_SIZE):
        batch = object_ids[start : start + WBDHU12_BATCH_SIZE]
        payload = await _fetch_attribute_batch(client, batch)
        features = payload.get("features")
        if not isinstance(features, list):
            raise UpstreamPayloadError("NHDPlus_HR returned no feature array")
        for feature in features:
            if not isinstance(feature, dict):
                continue
            attributes = feature.get("attributes")
            if not isinstance(attributes, dict):
                continue
            huc12 = attributes.get("huc12")
            if isinstance(huc12, str) and huc12.strip():
                vintages[huc12.strip()] = parse_load_date(attributes.get("loaddate"))
    return vintages


def compare_against_source(
    written: Sequence[WrittenWatershedRow],
    source_vintages: Mapping[str, datetime | None],
) -> tuple[WatershedValidationFailure, ...]:
    """Detect a republish (warehouse gone stale), a retirement, or a basin not yet exported.

    A source `loaddate` that failed to parse is skipped rather than compared -- reporting a
    republish from an unusable source date would be a fabricated gap, not an honest one.
    """
    failures: list[WatershedValidationFailure] = []
    written_by_huc12 = {row.huc12: row for row in written}
    for huc12, row in written_by_huc12.items():
        if huc12 not in source_vintages:
            failures.append(
                WatershedValidationFailure(
                    huc12=huc12,
                    lane=_LANE,
                    reason="retired upstream: WBD no longer returns this basin for the configured extent",
                    source_response="huc12 absent from the source's attribute batch",
                )
            )
            continue
        source_loaddate = source_vintages[huc12]
        if source_loaddate is not None and source_loaddate != row.observed_at:
            warehouse_state = row.observed_at.isoformat() if row.observed_at is not None else "undated"
            failures.append(
                WatershedValidationFailure(
                    huc12=huc12,
                    lane=_LANE,
                    reason=(
                        f"republished upstream: WBD loaddate is now {source_loaddate.isoformat()}, "
                        f"the warehouse holds {warehouse_state}"
                    ),
                    source_response=f"loaddate={source_loaddate.isoformat()}",
                )
            )
    for huc12, source_loaddate in source_vintages.items():
        if huc12 not in written_by_huc12:
            reported = source_loaddate.isoformat() if source_loaddate is not None else "undated"
            failures.append(
                WatershedValidationFailure(
                    huc12=huc12,
                    lane=_LANE,
                    reason="added upstream: WBD now publishes this basin but it is absent from the release",
                    source_response=f"loaddate={reported}",
                )
            )
    return tuple(failures)


async def validate_watersheds_release(
    client: httpx.AsyncClient,
    *,
    root: str,
    day: date,
    bbox: str,
    storage_options: Mapping[str, str] | None = None,
) -> WatershedValidationReport:
    """Run both reconciliation checks for one release day and return a combined, honest report."""
    written = read_written_watersheds(root, day, storage_options=storage_options)
    source_vintages = await fetch_source_huc12_vintages(client, bbox)
    return WatershedValidationReport(
        release_day=day,
        tohuc_failures=check_tohuc_integrity(written),
        source_failures=compare_against_source(written, source_vintages),
    )


__all__ = [
    "WatershedValidationFailure",
    "WatershedValidationReport",
    "WrittenWatershedRow",
    "check_tohuc_integrity",
    "compare_against_source",
    "fetch_source_huc12_vintages",
    "read_written_watersheds",
    "validate_watersheds_release",
]
