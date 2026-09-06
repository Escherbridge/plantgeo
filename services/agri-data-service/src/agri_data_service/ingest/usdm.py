"""US Drought Monitor source adapter: fetch one dated release file and parse it into validated areas.

THE POSTGIS STORE AND THE WEEKLY RETENTION PRUNE THAT LIVED HERE WERE DELETED 2026-09-06 (owner
directive, "remove the code for ingestion into the DB"). What is left is pure: no session, no SQL, no
writer. `pipeline/direct/drought/` is the only consumer that persists anything, and it writes Parquet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

import structlog

from agri_data_service.ingest.http import (
    HTTP_NOT_FOUND,
    UpstreamBounds,
    UpstreamHttpError,
    UpstreamPayloadError,
    fetch_bounded_json,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    import httpx

logger = structlog.get_logger()

# The source token `usdm-drought`, `PostgresDroughtStore`, `run_drought_ingestion_job`, the newest-release
# candidate walk and the release retention window were DELETED 2026-09-06: `geo.drought_areas` has no
# Python producer any more. `pipeline/direct/drought/` owns the full floor-to-settled window and writes
# Parquet, reusing the dated-archive fetch and the release parser kept below.
USDM_BASE_URL: Final = "https://droughtmonitor.unl.edu/data/json"

# The national collection is ~19 MB of full-resolution MultiPolygon rings.
USDM_BOUNDS: Final = UpstreamBounds(max_bytes=48 * 1024 * 1024, timeout_seconds=60.0)

# USDM publishes a drought class as a bare `Polygon` whenever that class happens to be one contiguous
# area, and as a `MultiPolygon` otherwise -- it is the same instrument answering about a simpler map, not
# a different product. Accepting only `MultiPolygon` rejected the WHOLE release over its single-part
# class, which measured against production on 2026-08-05 was 26 of the 29 weeks missing from
# `geo.drought_areas` between 2022-08-09 and 2026-08-04: every one of them a week whose D4 class was
# single-part (2024-02-20..2024-06-04, 2024-12-17..2025-01-28, 2025-11-11, 2025-12-09, 2025-12-16).
# The Parquet writer is unaffected: `pipeline/direct/drought/support.py` runs the same
# `ST_Multi(ST_CollectionExtract(ST_MakeValid(...), 3))` repair chain the deleted PostGIS store ran, so a
# `Polygon` lands identically to the single-part `MultiPolygon` USDM would otherwise have sent. Nothing is
# promoted in Python, nothing is repaired here, and a `GeometryCollection` or a line/point class still
# fails the gate.
DROUGHT_AREA_GEOMETRY_TYPES: Final = frozenset({"Polygon", "MultiPolygon"})

TUESDAY: Final = 1  # date.weekday(): Monday is 0, so Tuesday is 1.

MIN_DROUGHT_CLASS: Final = 0
MAX_DROUGHT_CLASS: Final = 4
DAYS_PER_WEEK: Final = 7


@dataclass(frozen=True, slots=True)
class DroughtArea:
    """One drought class of one release, with the MultiPolygon PostGIS repairs at write time."""

    drought_monitor_category: int
    geometry: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class DroughtRelease:
    """One indivisible weekly USDM release, dated by the request parameter rather than by its payload."""

    valid_date: str
    source_url: str
    areas: tuple[DroughtArea, ...]


def usdm_source_url(valid_date: str) -> str:
    """Return the exact upstream file a release is read from, which is stored as its provenance."""
    return f"{USDM_BASE_URL}/usdm_{valid_date.replace('-', '')}.json"


def _require_tuesday(valid_date: str) -> date:
    """Reject any date that is not a real USDM Tuesday before a request is ever made."""
    try:
        release_date = date.fromisoformat(valid_date)
    except ValueError as error:
        raise ValueError("USDM valid date must be an ISO YYYY-MM-DD string") from error
    if release_date.isoformat() != valid_date:
        raise ValueError("USDM valid date must be an ISO YYYY-MM-DD string")
    if release_date.weekday() != TUESDAY:
        raise ValueError("USDM releases are only valid for Tuesdays")
    return release_date


def parse_drought_release(valid_date: str, payload: object) -> DroughtRelease:
    """Parse one dated USDM collection, rejecting a release that repeats or omits a drought class."""
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise UpstreamPayloadError("USDM returned an unexpected drought feature collection shape")
    features = payload.get("features")
    if not isinstance(features, list):
        raise UpstreamPayloadError("USDM returned an unexpected drought feature collection shape")

    areas: dict[int, DroughtArea] = {}
    for feature in features:
        drought_class, geometry = _parse_drought_feature(feature)
        # A duplicated class makes the release ambiguous rather than mergeable, so the whole
        # release is rejected instead of silently picking one.
        if drought_class in areas:
            raise UpstreamPayloadError(f"USDM release {valid_date} repeats drought class D{drought_class}")
        areas[drought_class] = DroughtArea(drought_monitor_category=drought_class, geometry=geometry)

    if not areas:
        raise UpstreamPayloadError(f"USDM release {valid_date} contained no drought classes")

    return DroughtRelease(
        valid_date=valid_date,
        source_url=usdm_source_url(valid_date),
        areas=tuple(areas[drought_class] for drought_class in sorted(areas)),
    )


def _parse_drought_feature(feature: object) -> tuple[int, Mapping[str, object]]:
    """Validate one USDM feature into its drought class and MultiPolygon geometry."""
    if not isinstance(feature, dict) or feature.get("type") != "Feature":
        raise UpstreamPayloadError("USDM returned an unexpected drought feature collection shape")
    properties = feature.get("properties")
    geometry = feature.get("geometry")
    if not isinstance(properties, dict) or not isinstance(geometry, dict):
        raise UpstreamPayloadError("USDM returned an unexpected drought feature collection shape")
    if geometry.get("type") not in DROUGHT_AREA_GEOMETRY_TYPES or not isinstance(geometry.get("coordinates"), list):
        raise UpstreamPayloadError("USDM returned an unexpected drought feature collection shape")
    drought_class = properties.get("DM")
    if (
        isinstance(drought_class, bool)
        or not isinstance(drought_class, int)
        or not MIN_DROUGHT_CLASS <= drought_class <= MAX_DROUGHT_CLASS
    ):
        raise UpstreamPayloadError("USDM returned an unexpected drought feature collection shape")
    return drought_class, geometry


async def fetch_drought_release(
    client: httpx.AsyncClient,
    valid_date: str,
) -> DroughtRelease | None:
    """Fetch one dated release; a 404 is USDM's documented "not published yet", not a failure."""
    _require_tuesday(valid_date)
    try:
        payload = await fetch_bounded_json(
            client,
            usdm_source_url(valid_date),
            USDM_BOUNDS,
            {"Accept": "application/json"},
        )
    except UpstreamHttpError as error:
        if error.status == HTTP_NOT_FOUND:
            return None
        raise
    return parse_drought_release(valid_date, payload)
