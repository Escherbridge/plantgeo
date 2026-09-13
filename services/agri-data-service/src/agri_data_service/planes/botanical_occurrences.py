"""The bounded Parquet serving read for `botanical-occurrences`: detail, aggregate, refused, unavailable.

Layer L3 (planes): may import `foundation`, `method`, `warehouse`, `pipeline`; may NOT import
`interface`. Parquet only -- this module never opens a PostgreSQL session and never contacts a
portal, so a missing generation is answered as `unavailable` rather than backfilled from anywhere.

Synchronous for the same reason `planes/drought.py` is: the Polars scan is a blocking call, and a
route that needs it should run it in an executor rather than have this module pretend to be async.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import polars as pl

from agri_data_service.config import settings as default_settings
from agri_data_service.pipeline.direct.botanical_occurrences.publish import (
    COMPLETION_MARKER,
    PART_NAME,
    LocalPublicationTarget,
    generation_prefix,
    publication_target,
    read_manifest,
)
from agri_data_service.pipeline.direct.botanical_occurrences.support import SUPPORT_DEGREES, support_for
from agri_data_service.pipeline.parquet.objectstore import polars_storage_options

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.config import Settings
    from agri_data_service.pipeline.direct.botanical_occurrences.publish import PublicationTarget

PRODUCT: Final = "botanical-occurrences"

#: At or above this zoom a request is answered with detail points; below it, with support cells. The
#: floor is a CLAIM BOUNDARY, not a rendering preference: a specimen point at continental zoom reads
#: as a distribution, and this lane does not publish distributions.
DETAIL_ZOOM_FLOOR: Final = 11

#: Which rung answers which zoom band below the detail floor.
COARSE_SUPPORT: Final = "grid-0.25"
FINE_SUPPORT: Final = "grid-0.05"
FINE_SUPPORT_ZOOM_FLOOR: Final = 7

DEFAULT_LIMIT: Final = 500
MAX_LIMIT: Final = 2000

#: Square-degree ceilings per answer state. A bbox wider than its zoom band's ceiling is REFUSED
#: rather than silently coarsened: coarsening would answer a question about one area with evidence
#: about another, and the caller would have no way to tell that happened.
MAX_BBOX_SQUARE_DEGREES: Final[dict[str, float]] = {"detail": 4.0, FINE_SUPPORT: 100.0, COARSE_SUPPORT: 1600.0}

REFUSAL_REASONS: Final[tuple[str, ...]] = (
    "release_not_pinned",
    "release_unknown",
    "release_incomplete",
    "bbox_too_large_for_zoom",
    "name_only_taxon_filter",
    "historical_publication_unsupported",
    "limit_exceeded",
)

_PARAMETERS: Final[frozenset[str]] = frozenset(
    {
        "release_set_id",
        "bbox",
        "zoom",
        "taxon_concept_id",
        "family",
        "collection_key",
        "event_start",
        "event_end",
        "spatial_quality",
        "limit",
        "cursor",
    }
)
#: Filters this lane refuses by name. `family` IS accepted as an exact string because a family is a
#: rank whose name does not carry the homonym problem a species epithet does; a species NAME does,
#: which is why the only species-level filter is a concept id.
_NAME_ONLY_PARAMETERS: Final[frozenset[str]] = frozenset({"scientific_name", "taxon_name", "species_name"})
_SPATIAL_QUALITIES: Final[frozenset[str]] = frozenset({"confirmed", "possible", "all"})
_BBOX_ORDINATES: Final = 4


class BotanicalOccurrenceRequestError(ValueError):
    """The request is not one bounded, release-pinned question this plane can answer."""

    def __init__(self, message: str, reason: str = "invalid_request") -> None:
        super().__init__(message)
        self.reason = reason


class BotanicalOccurrenceServingError(RuntimeError):
    """Raised when a pinned generation cannot be read back as one honest, complete answer."""


@dataclass(frozen=True, slots=True)
class BotanicalOccurrenceRequest:
    """One bounded question: which generation, which extent, which filters, how many rows."""

    release_set_id: str
    bbox: tuple[float, float, float, float]
    zoom: int
    taxon_concept_id: str | None = None
    family: str | None = None
    collection_key: str | None = None
    event_start: date | None = None
    event_end: date | None = None
    spatial_quality: str = "confirmed"
    limit: int = DEFAULT_LIMIT
    offset: int = 0

    @property
    def wants_detail(self) -> bool:
        """True when the zoom is at or above the detail floor."""
        return self.zoom >= DETAIL_ZOOM_FLOOR

    @property
    def support_id(self) -> str | None:
        """The support rung answering this zoom, or None when the answer is detail points."""
        if self.wants_detail:
            return None
        return FINE_SUPPORT if self.zoom >= FINE_SUPPORT_ZOOM_FLOOR else COARSE_SUPPORT


def _parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = raw.split(",")
    if len(parts) != _BBOX_ORDINATES:
        raise BotanicalOccurrenceRequestError("bbox must be minLon,minLat,maxLon,maxLat")
    try:
        min_longitude, min_latitude, max_longitude, max_latitude = (float(part) for part in parts)
    except ValueError as error:
        raise BotanicalOccurrenceRequestError(f"bbox ordinates must be numbers: {error}") from error
    if min_longitude >= max_longitude or min_latitude >= max_latitude:
        raise BotanicalOccurrenceRequestError("bbox must have min ordinates strictly below max ordinates")
    return min_longitude, min_latitude, max_longitude, max_latitude


def _parse_day(raw: str, name: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as error:
        raise BotanicalOccurrenceRequestError(f"{name} must be an ISO calendar day") from error


def encode_cursor(offset: int) -> str:
    """Encode a continuation cursor. Opaque on purpose: its shape is not part of the contract."""
    return base64.urlsafe_b64encode(json.dumps({"offset": offset}).encode("utf-8")).decode("ascii")


def decode_cursor(raw: str) -> int:
    """Decode a continuation cursor, refusing one this plane did not issue."""
    try:
        decoded = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")))
        offset = int(decoded["offset"])
    except (KeyError, TypeError, ValueError, binascii.Error, json.JSONDecodeError) as error:
        raise BotanicalOccurrenceRequestError("cursor is not one this plane issued") from error
    if offset < 0:
        raise BotanicalOccurrenceRequestError("cursor offset must not be negative")
    return offset


def parse_botanical_occurrence_request(parameters: Mapping[str, str]) -> BotanicalOccurrenceRequest:
    """Parse and bound one request, refusing anything unpinned, unbounded or name-only.

    `release_set_id` has NO DEFAULT and `current` is refused by name. A reader that silently followed
    the pointer would answer two identical requests from two different generations and report the
    same thing both times, which is exactly the ambiguity the immutable release set removes.
    """
    # Checked BEFORE the unknown-parameter sweep, so a name filter is refused as the thing it is
    # rather than as a typo. A homonym cannot be resolved by a name-only join, so this lane does not
    # offer one: an exact concept id is the only taxon filter it will answer.
    name_only = sorted(set(parameters) & _NAME_ONLY_PARAMETERS)
    if name_only:
        raise BotanicalOccurrenceRequestError(
            f"parameter(s) {name_only} filter on a NAME; supply an exact taxon_concept_id instead",
            reason="name_only_taxon_filter",
        )
    unknown = sorted(set(parameters) - _PARAMETERS)
    if unknown:
        raise BotanicalOccurrenceRequestError(f"unknown parameter(s) {unknown}")
    release_set_id = (parameters.get("release_set_id") or "").strip()
    if not release_set_id or release_set_id == "current":
        raise BotanicalOccurrenceRequestError(
            "release_set_id must name one exact published generation; `current` is not a pinned release",
            reason="release_not_pinned",
        )
    if "bbox" not in parameters or "zoom" not in parameters:
        raise BotanicalOccurrenceRequestError("bbox and zoom are both required")
    try:
        zoom = int(parameters["zoom"])
    except ValueError as error:
        raise BotanicalOccurrenceRequestError("zoom must be an integer") from error
    limit = int(parameters.get("limit") or DEFAULT_LIMIT)
    if limit > MAX_LIMIT or limit < 1:
        raise BotanicalOccurrenceRequestError(f"limit must be between 1 and {MAX_LIMIT}", reason="limit_exceeded")
    spatial_quality = (parameters.get("spatial_quality") or "confirmed").strip()
    if spatial_quality not in _SPATIAL_QUALITIES:
        raise BotanicalOccurrenceRequestError(f"spatial_quality must be one of {sorted(_SPATIAL_QUALITIES)}")
    return BotanicalOccurrenceRequest(
        release_set_id=release_set_id,
        bbox=_parse_bbox(parameters["bbox"]),
        zoom=zoom,
        taxon_concept_id=(parameters.get("taxon_concept_id") or "").strip() or None,
        family=(parameters.get("family") or "").strip() or None,
        collection_key=(parameters.get("collection_key") or "").strip() or None,
        event_start=_parse_day(parameters["event_start"], "event_start") if parameters.get("event_start") else None,
        event_end=_parse_day(parameters["event_end"], "event_end") if parameters.get("event_end") else None,
        spatial_quality=spatial_quality,
        limit=limit,
        offset=decode_cursor(parameters["cursor"]) if parameters.get("cursor") else 0,
    )


def refused(reason: str, detail: str = "") -> dict[str, Any]:
    """A refusal: the plane did not answer, and NOTHING about the data follows from it."""
    return {
        "product": PRODUCT,
        "state": "refused",
        "reason": reason,
        "detail": detail,
        "note": "This is a refusal, not an absence. Nothing was read, so nothing follows about the collection.",
    }


def unavailable(reason: str) -> dict[str, Any]:
    """No generation could be opened. Also not an absence: the lane was not readable, not empty."""
    return {
        "product": PRODUCT,
        "state": "unavailable",
        "reason": reason,
        "note": "The pinned generation could not be opened. This says nothing about what it contains.",
    }


def _storage_options(root: str, source: Settings | None) -> dict[str, str] | None:
    if not root.startswith("s3://"):
        return None
    resolved = default_settings if source is None else source
    return polars_storage_options(resolved.require_object_store())


@dataclass(frozen=True, slots=True)
class GenerationReader:
    """Where one generation's artifacts are, and how Polars reaches them."""

    root: str
    target: PublicationTarget
    storage_options: dict[str, str] | None

    def scan(self, relative_path: str) -> pl.LazyFrame:
        """Open one artifact lazily, through the object store when the root is a bucket."""
        if self.storage_options is None:
            return pl.scan_parquet(str(Path(self.root) / relative_path))
        return pl.scan_parquet(f"{self.root.rstrip('/')}/{relative_path}", storage_options=self.storage_options)


def open_generation(
    release_set_id: str,
    *,
    root: str | Path | None = None,
    source: Settings | None = None,
    target: PublicationTarget | None = None,
) -> GenerationReader | dict[str, Any]:
    """Resolve one pinned generation, or return the `unavailable`/`refused` answer explaining why.

    The completion marker is the gate, not the directory listing. A generation whose parts exist but
    whose marker does not was never finished publishing, and serving it would answer from a
    population nobody ever reconciled.
    """
    try:
        resolved_target = target or publication_target(root, source=source)
    except ValueError:
        # No local root was given and object storage is unconfigured: this is a genuinely
        # unanswerable request in this environment, not a writer-time misconfiguration, so it
        # surfaces as governed absence rather than an unhandled exception reaching the caller.
        return unavailable(f"no completed generation is published for release_set_id {release_set_id}")
    if isinstance(resolved_target, LocalPublicationTarget):
        resolved_root = str(resolved_target.root)
    elif root is not None:
        resolved_root = str(root)
    else:
        settings_source = default_settings if source is None else source
        try:
            credentials = settings_source.require_object_store()
        except ValueError:
            return unavailable(f"no completed generation is published for release_set_id {release_set_id}")
        prefix = settings_source.object_store_prefix.strip("/")
        resolved_root = f"s3://{credentials.bucket}/{prefix}".rstrip("/")
    prefix = generation_prefix(release_set_id)
    if not resolved_target.exists(f"{prefix}/{COMPLETION_MARKER}"):
        manifest_present = resolved_target.read_bytes(f"{prefix}/manifest.json") is not None
        if manifest_present:
            return refused("release_incomplete", f"{release_set_id} has no completion marker")
        return unavailable(f"no completed generation is published for release_set_id {release_set_id}")
    if read_manifest(resolved_target, release_set_id) is None:
        return refused("release_incomplete", f"{release_set_id} carries a marker but no readable manifest")
    return GenerationReader(
        root=resolved_root, target=resolved_target, storage_options=_storage_options(resolved_root, source)
    )


def _bbox_area(bbox: tuple[float, float, float, float]) -> float:
    min_longitude, min_latitude, max_longitude, max_latitude = bbox
    return (max_longitude - min_longitude) * (max_latitude - min_latitude)


def _apply_common_filters(frame: pl.LazyFrame, request: BotanicalOccurrenceRequest) -> pl.LazyFrame:
    if request.taxon_concept_id:
        frame = frame.filter(pl.col("taxon_concept_id") == request.taxon_concept_id)
    if request.family:
        frame = frame.filter(pl.col("family") == request.family)
    if request.collection_key:
        frame = frame.filter(pl.col("collection_key") == request.collection_key)
    if request.event_start is not None:
        # OVERLAP, not containment: a record whose interval ends inside the window is evidence for it.
        frame = frame.filter(pl.col("event_end").is_not_null() & (pl.col("event_end") >= request.event_start))
    if request.event_end is not None:
        frame = frame.filter(pl.col("event_start").is_not_null() & (pl.col("event_start") <= request.event_end))
    return frame


def _feature(row: Mapping[str, Any], membership: str | None) -> dict[str, Any]:
    return {
        "occurrence_id": row["occurrence_id"],
        "collection_key": row["collection_key"],
        "source_record_key": row["source_record_key"],
        "taxon_concept_id": row["taxon_concept_id"],
        "resolution_state": row["resolution_state"],
        "scientific_name": row["scientific_name"],
        "family": row["family"],
        "event_interval": {
            "start": row["event_start"].isoformat() if row["event_start"] else None,
            "end": row["event_end"].isoformat() if row["event_end"] else None,
            "precision": row["event_precision"],
        },
        "longitude": row["longitude"],
        "latitude": row["latitude"],
        "coordinate_uncertainty_m": row["coordinate_uncertainty_m"],
        "spatial_class": row["spatial_class"],
        "membership": membership,
        "catalog_number": row["catalog_number"],
        "recorded_by": row["recorded_by"],
        "basis_of_record": row["basis_of_record"],
        "rights_uri": row["rights_uri"],
        "attribution_text": row["attribution_text"],
    }


def _read_detail(reader: GenerationReader, request: BotanicalOccurrenceRequest) -> dict[str, Any]:
    min_longitude, min_latitude, max_longitude, max_latitude = request.bbox
    occurrences = _apply_common_filters(reader.scan(f"{generation_prefix(request.release_set_id)}/occurrences/{PART_NAME}"), request)
    occurrences = occurrences.filter(
        pl.col("longitude").is_between(min_longitude, max_longitude)
        & pl.col("latitude").is_between(min_latitude, max_latitude)
    )
    if request.spatial_quality == "confirmed":
        # `exact` is the only class a confirmed detail point can come from; a generalized point is
        # the publisher telling us the specimen is NOT where the coordinates say.
        occurrences = occurrences.filter(pl.col("spatial_class") == "exact")
    elif request.spatial_quality == "possible":
        occurrences = occurrences.filter(pl.col("spatial_class") == "generalized")
    collected = occurrences.collect()
    matched = collected.height
    window = collected.slice(request.offset, request.limit)
    nonspatial = _apply_common_filters(
        reader.scan(f"{generation_prefix(request.release_set_id)}/nonspatial/{PART_NAME}"), request
    ).collect()
    withheld = int((nonspatial["spatial_class"] == "withheld").sum()) if nonspatial.height else 0
    truncated = request.offset + window.height < matched
    manifest = read_manifest(reader.target, request.release_set_id) or {}
    return {
        "product": PRODUCT,
        "state": "detail",
        "release_set_id": request.release_set_id,
        "taxonomy_recipe_version": manifest.get("taxonomy_recipe_version"),
        "qc_policy_version": manifest.get("qc_policy_version"),
        "support_id": None,
        "features": [
            _feature(row, "confirmed" if row["spatial_class"] == "exact" else "possible")
            for row in window.iter_rows(named=True)
        ],
        "truncated": truncated,
        "next_cursor": encode_cursor(request.offset + window.height) if truncated else None,
        "counts": {
            "returned": window.height,
            "matched": matched,
            "withheld": withheld,
            "nonspatial": nonspatial.height - withheld,
            # Nonspatial and withheld records match no bbox, so they are counted at the generation
            # level under the same non-spatial filters. Reporting zero would hide the population.
            "excluded_by_qc": nonspatial.height,
        },
    }


def _read_aggregate(reader: GenerationReader, request: BotanicalOccurrenceRequest) -> dict[str, Any]:
    support_id = request.support_id or COARSE_SUPPORT
    support = support_for(support_id)
    min_longitude, min_latitude, max_longitude, max_latitude = request.bbox
    cells = reader.scan(f"{generation_prefix(request.release_set_id)}/support/{support_id}/cells.parquet").collect()
    selected: list[dict[str, Any]] = []
    for row in cells.iter_rows(named=True):
        _, column, cell_row = row["cell_id"].rsplit(":", 2)
        cell_min_longitude, cell_min_latitude, cell_max_longitude, cell_max_latitude = support.bounds(
            int(column), int(cell_row)
        )
        if cell_max_longitude < min_longitude or cell_min_longitude > max_longitude:
            continue
        if cell_max_latitude < min_latitude or cell_min_latitude > max_latitude:
            continue
        selected.append(
            {
                "cell_id": row["cell_id"],
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [cell_min_longitude, cell_min_latitude],
                            [cell_max_longitude, cell_min_latitude],
                            [cell_max_longitude, cell_max_latitude],
                            [cell_min_longitude, cell_max_latitude],
                            [cell_min_longitude, cell_min_latitude],
                        ]
                    ],
                },
                "evaluation": row["evaluation"],
                "documented_taxa": row["documented_taxa"],
                "record_count": row["record_count"],
                "event_estimate": row["event_estimate"],
                "collection_count": row["collection_count"],
                "excluded_by_qc": row["excluded_by_qc"],
                "possible_only_records": row["possible_only_records"],
            }
        )
    window = selected[request.offset : request.offset + request.limit]
    truncated = request.offset + len(window) < len(selected)
    manifest = read_manifest(reader.target, request.release_set_id) or {}
    return {
        "product": PRODUCT,
        "state": "aggregate",
        "release_set_id": request.release_set_id,
        "taxonomy_recipe_version": manifest.get("taxonomy_recipe_version"),
        "qc_policy_version": manifest.get("qc_policy_version"),
        "support_id": support_id,
        "cells": window,
        "truncated": truncated,
        "next_cursor": encode_cursor(request.offset + len(window)) if truncated else None,
        "counts": {"returned": len(window), "matched": len(selected)},
    }


def read_botanical_occurrences(
    request: BotanicalOccurrenceRequest,
    *,
    root: str | Path | None = None,
    source: Settings | None = None,
    target: PublicationTarget | None = None,
) -> dict[str, Any]:
    """Answer one bounded request in exactly one of the four states, and never in two.

    The bbox ceiling is checked BEFORE the generation is opened, because a refusal that depends on
    what is published would be a refusal that leaks what is published.
    """
    band = "detail" if request.wants_detail else (request.support_id or COARSE_SUPPORT)
    if _bbox_area(request.bbox) > MAX_BBOX_SQUARE_DEGREES[band]:
        return refused(
            "bbox_too_large_for_zoom",
            f"a {band} answer is bounded at {MAX_BBOX_SQUARE_DEGREES[band]} square degrees",
        )
    opened = open_generation(request.release_set_id, root=root, source=source, target=target)
    if isinstance(opened, dict):
        return opened
    try:
        return _read_detail(opened, request) if request.wants_detail else _read_aggregate(opened, request)
    except (pl.exceptions.PolarsError, FileNotFoundError, OSError) as error:
        raise BotanicalOccurrenceServingError(
            f"{request.release_set_id} is marked complete but one of its artifacts could not be read: {error}"
        ) from error


def encode_botanical_occurrences(result: Mapping[str, Any]) -> bytes:
    """Render one answer as the JSON body a route or tool returns."""
    return json.dumps(result, sort_keys=True, default=str).encode("utf-8")


def supports() -> tuple[str, ...]:
    """Every support rung this plane can answer an aggregate at."""
    return tuple(sorted(SUPPORT_DEGREES))


__all__ = [
    "COARSE_SUPPORT",
    "DEFAULT_LIMIT",
    "DETAIL_ZOOM_FLOOR",
    "FINE_SUPPORT",
    "MAX_LIMIT",
    "PRODUCT",
    "REFUSAL_REASONS",
    "BotanicalOccurrenceRequest",
    "BotanicalOccurrenceRequestError",
    "BotanicalOccurrenceServingError",
    "GenerationReader",
    "decode_cursor",
    "encode_botanical_occurrences",
    "encode_cursor",
    "open_generation",
    "parse_botanical_occurrence_request",
    "read_botanical_occurrences",
    "refused",
    "supports",
    "unavailable",
]
