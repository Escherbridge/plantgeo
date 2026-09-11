"""Bounded numerical point reads from verified saved COGs; see AGENTS.md."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from agri_data_service.pipeline.static_soil.models import (
    MAX_METADATA_BYTES,
    PROPERTIES,
    SoilPropertyAsset,
    StaticSoilCandidate,
    digest,
)
from agri_data_service.pipeline.static_soil.prepare import read_bounded
from agri_data_service.pipeline.static_soil.raster import inspect_grid, memory_raster, validate_tags

if TYPE_CHECKING:
    from datetime import date
    from pathlib import Path

MAX_ABS_LONGITUDE: Final = 180
MAX_ABS_LATITUDE: Final = 90


@dataclass(frozen=True, slots=True)
class VerifiedSoilBundle:
    """Pinned immutable bytes, loaded once before any point requests."""

    manifest: StaticSoilCandidate
    manifest_sha256: str
    cogs: tuple[bytes, ...]


@dataclass(frozen=True, slots=True)
class SoilPixelValue:
    """A saved-grid pixel with its own support, numerical value and identity."""

    property_name: str
    status: Literal["value", "nodata", "out_of_extent"]
    raw_value: int | None
    value: float | None
    unit: str
    row: int | None
    column: int | None
    center_longitude: float | None
    center_latitude: float | None
    distance_m: float | None
    source_sha256: str


@dataclass(frozen=True, slots=True)
class SoilPointResult:
    """Static estimates acknowledge the selected day without inventing observations."""

    selected_day: date
    longitude: float
    latitude: float
    manifest_sha256: str
    values: tuple[SoilPixelValue, ...]
    temporal_status: str = "static_estimate_without_observation_date"
    support: str = "saved_reprojected_full_resolution_pixel"
    admission_state: str = "local_verified_candidate"
    observed_day: None = None
    temporal_distance_days: None = None


def open_verified_soil_bundle(root: Path, *, expected_manifest_sha256: str) -> VerifiedSoilBundle:
    """Load at most 64 MiB of fully rehashed COGs into an immutable local snapshot."""
    payload = read_bounded(root / "candidate.json", MAX_METADATA_BYTES)
    if digest(payload) != expected_manifest_sha256:
        raise ValueError("static soil candidate manifest identity disagrees")
    manifest = StaticSoilCandidate.model_validate_json(payload)
    cogs: list[bytes] = []
    for asset in manifest.assets:
        cog = read_bounded(root / "objects" / asset.cog.sha256, asset.cog.byte_count)
        if len(cog) != asset.cog.byte_count or digest(cog) != asset.cog.sha256:
            raise ValueError("soil COG complete-object verification failed")
        cogs.append(cog)
    return VerifiedSoilBundle(manifest, digest(payload), tuple(cogs))


def _distance_m(longitude: float, latitude: float, center_longitude: float, center_latitude: float) -> float:
    earth_radius_m = 6_371_008.8
    dlat = math.radians(center_latitude - latitude)
    dlon = math.radians(center_longitude - longitude)
    term = math.sin(dlat / 2) ** 2
    term += math.cos(math.radians(latitude)) * math.cos(math.radians(center_latitude)) * math.sin(dlon / 2) ** 2
    return 2 * earth_radius_m * math.asin(min(1.0, math.sqrt(term)))


def _pixel(asset: SoilPropertyAsset, payload: bytes, longitude: float, latitude: float) -> SoilPixelValue:
    grid = asset.grid
    a, _, c, _, e, f = grid.transform
    column, row = math.floor((longitude - c) / a), math.floor((latitude - f) / e)
    if not (0 <= column < grid.width and 0 <= row < grid.height):
        return SoilPixelValue(
            asset.property_name, "out_of_extent", None, None, asset.unit, None, None, None, None, None, asset.cog.sha256
        )
    with memory_raster(payload) as memory, memory.open() as source:
        if inspect_grid(source) != grid:
            raise ValueError("soil COG pixel grid disagrees with its pinned manifest")
        validate_tags(source, property_name=asset.property_name, divisor=asset.scale_divisor, unit=asset.unit)
        raw_value = int(source.read(1, window=((row, row + 1), (column, column + 1)))[0, 0])
    center_longitude, center_latitude = c + (column + 0.5) * a, f + (row + 0.5) * e
    return SoilPixelValue(
        property_name=asset.property_name,
        status="nodata" if raw_value == grid.nodata else "value",
        raw_value=raw_value,
        value=None if raw_value == grid.nodata else raw_value / asset.scale_divisor,
        unit=asset.unit,
        row=row,
        column=column,
        center_longitude=center_longitude,
        center_latitude=center_latitude,
        distance_m=_distance_m(longitude, latitude, center_longitude, center_latitude),
        source_sha256=asset.cog.sha256,
    )


def soil_point(
    bundle: VerifiedSoilBundle,
    *,
    longitude: float,
    latitude: float,
    selected_day: date,
    properties: tuple[str, ...] = PROPERTIES,
) -> SoilPointResult:
    """Read at most six base pixels, with nodata, extent and temporal semantics explicit."""
    if (
        not math.isfinite(longitude)
        or not math.isfinite(latitude)
        or not (
            -MAX_ABS_LONGITUDE <= longitude <= MAX_ABS_LONGITUDE and -MAX_ABS_LATITUDE <= latitude <= MAX_ABS_LATITUDE
        )
    ):
        raise ValueError("soil point requires finite WGS84 coordinates")
    if not properties or len(set(properties)) != len(properties) or not set(properties) <= set(PROPERTIES):
        raise ValueError("soil point requires one to six distinct supported properties")
    values = tuple(
        _pixel(asset, payload, longitude, latitude)
        for asset, payload in zip(bundle.manifest.assets, bundle.cogs, strict=True)
        if asset.property_name in properties
    )
    return SoilPointResult(selected_day, longitude, latitude, bundle.manifest_sha256, values)
