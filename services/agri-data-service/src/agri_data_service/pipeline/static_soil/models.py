"""Immutable saved SoilGrids identities; see AGENTS.md."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime  # noqa: TC003 - Pydantic resolves these model field types at runtime.
from typing import Annotated, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_OBJECT_BYTES: Final = 64 * 1024 * 1024
MAX_BUNDLE_BYTES: Final = 256 * 1024 * 1024
MAX_COG_BYTES: Final = 64 * 1024 * 1024
MAX_BLOCK_BYTES: Final = 1024 * 1024
MAX_METADATA_BYTES: Final = 64 * 1024
RAMP_STOP_COUNT: Final = 7
MAX_TILE_BOUND_ROUNDING: Final = 0.000001
PROPERTIES: Final = ("phh2o", "soc", "nitrogen", "bdod", "cec", "ocd")
PROPERTY_UNITS: Final = ("pH", "g/kg", "g/kg", "kg/dm^3", "cmol(c)/kg", "kg/m^3")
PROPERTY_DIVISORS: Final = (10, 10, 100, 100, 10, 10)
SHA256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PropertyName = Literal["phh2o", "soc", "nitrogen", "bdod", "cec", "ocd"]


def digest(payload: bytes) -> str:
    """Hash the complete supplied bytes."""
    return hashlib.sha256(payload).hexdigest()


def canonical_bytes(value: object) -> bytes:
    """Serialize an exact immutable manifest."""
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


class FrozenModel(BaseModel):
    """Refuse undeclared fields and implicit string/number coercion."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class AssetIdentity(FrozenModel):
    """A full-object digest, never a sampled-range assertion."""

    sha256: SHA256
    byte_count: Annotated[int, Field(gt=0, le=MAX_OBJECT_BYTES)]
    key: str

    @model_validator(mode="after")
    def require_content_address(self) -> AssetIdentity:
        if self.key != f"static-soil/objects/sha256/{self.sha256}":
            raise ValueError("soil asset key must be its exact immutable content address")
        return self


class RasterGrid(FrozenModel):
    """The saved reprojected full-resolution support."""

    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    transform: tuple[float, float, float, float, float, float]
    bounds: tuple[float, float, float, float]
    block_shape: tuple[int, int]
    crs: Literal["EPSG:4326"] = "EPSG:4326"
    nodata: Literal[-32768] = -32768
    dtype: Literal["int16"] = "int16"

    @model_validator(mode="after")
    def require_bounded_grid(self) -> RasterGrid:
        a, b, c, d, e, f = self.transform
        if a <= 0 or e >= 0 or b != 0 or d != 0:
            raise ValueError("saved soil grid must be north-up without rotation")
        if self.bounds != (c, f + self.height * e, c + self.width * a, f):
            raise ValueError("soil bounds disagree with exact affine transform")
        if min(self.block_shape) <= 0 or self.block_shape[0] * self.block_shape[1] * 2 > MAX_BLOCK_BYTES:
            raise ValueError("full-resolution soil block exceeds the decoded read budget")
        return self


class RampStop(FrozenModel):
    """Physical-unit imagery ramp; not a point-reading source."""

    color: Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")]
    value: float


class SoilPropertyAsset(FrozenModel):
    """One property binds its numeric COG and its separate palette archive."""

    property_name: PropertyName
    unit: str
    scale_divisor: int
    cog: AssetIdentity
    pmtiles: AssetIdentity
    grid: RasterGrid
    color_ramp: tuple[RampStop, ...]
    tile_min_zoom: Literal[0] = 0
    tile_max_zoom: Literal[10] = 10
    addressed_tiles: Annotated[int, Field(gt=0)]
    tile_bounds: tuple[float, float, float, float]
    depth: Literal["0-5cm"] = "0-5cm"
    statistic: Literal["mean"] = "mean"
    source_url: str

    @model_validator(mode="after")
    def require_property_contract(self) -> SoilPropertyAsset:
        index = PROPERTIES.index(self.property_name)
        if self.unit != PROPERTY_UNITS[index] or self.scale_divisor != PROPERTY_DIVISORS[index]:
            raise ValueError("soil property unit or integer divisor disagrees with the saved product")
        if self.source_url != f"https://files.isric.org/soilgrids/latest/data/{self.property_name}/":
            raise ValueError("soil source URL differs from the saved mutable publisher path")
        if any(
            abs(tile - native) > MAX_TILE_BOUND_ROUNDING
            for tile, native in zip(self.tile_bounds, self.grid.bounds, strict=True)
        ):
            raise ValueError("soil imagery bounds disagree with the inspected numeric support")
        if len(self.color_ramp) != RAMP_STOP_COUNT or any(
            left.value > right.value for left, right in zip(self.color_ramp, self.color_ramp[1:], strict=False)
        ):
            raise ValueError("soil imagery requires its seven ordered physical-unit stops")
        return self


class StaticSoilCandidate(FrozenModel):
    """A local preservation candidate, without publication or admission authority."""

    schema_version: Literal["static-soil-candidate/v1"] = "static-soil-candidate/v1"
    prepared_at: datetime
    source_manifest_sha256: SHA256
    asset_receipt_sha256: SHA256
    release_label: Literal["v2.0"] = "v2.0"
    license_name: Literal["CC-BY 4.0"] = "CC-BY 4.0"
    upstream_retrieved_at: None = None
    upstream_immutable_version: None = None
    observed_day: None = None
    admission_state: Literal["local_verified_candidate"] = "local_verified_candidate"
    remote_full_hash_verified: Literal[False] = False
    assets: tuple[SoilPropertyAsset, ...]

    @model_validator(mode="after")
    def require_complete_saved_bundle(self) -> StaticSoilCandidate:
        if self.prepared_at.utcoffset() is None:
            raise ValueError("candidate preparation requires an explicit timezone")
        if tuple(asset.property_name for asset in self.assets) != PROPERTIES:
            raise ValueError("candidate requires exactly the six ordered saved properties")
        if len({asset.grid.model_dump_json() for asset in self.assets}) != 1:
            raise ValueError("the six soil properties must share the same pixel support")
        total = sum(asset.cog.byte_count + asset.pmtiles.byte_count for asset in self.assets)
        if total > MAX_BUNDLE_BYTES or sum(asset.cog.byte_count for asset in self.assets) > MAX_COG_BYTES:
            raise ValueError("soil candidate exceeds its total object-byte budget")
        return self
