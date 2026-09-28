"""SSURGO shard candidates and the release index: pure pydantic models, no I/O.

Layer L0, the same ruled exception as `receipts.py` (`foundation/AGENTS.md`). The CANDIDATE side of
the archive's `contracts.py` split: one `Candidate` is one shard of at most `MAX_SURVEY_AREAS`
areas, and one `Release` pins up to `MAX_RELEASE_SHARDS` staged shards. Every cap below is a
Freeze-2 constant; the derivations live in `pipeline/direct/soil_survey/AGENTS.md`, "Freeze 2".
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime  # noqa: TC003 - pydantic resolves field annotations at runtime
from typing import TYPE_CHECKING, Annotated, Final, Literal

from pydantic import Field, model_validator

from agri_data_service.foundation.soil_survey.receipts import (
    ROOT,
    WGS84_MAX_LATITUDE,
    WGS84_MAX_LONGITUDE,
    AreaCapture,
    AreaInventory,
    AreaSymbol,
    Blob,
    FrozenModel,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

#: The only rung the GeoJSON candidate carries (owner Q1): below z13 the route answers "zoom in".
NATIVE_RUNG: Final = 13
REQUIRED_RUNGS: Final[tuple[Literal[13], ...]] = (13,)
#: Areas per shard; equals `pipeline/validation/soil_survey.py::MAX_VALIDATION_AREAS`.
MAX_SURVEY_AREAS: Final = 50
#: Rows per shard (Freeze 2): ~1.75x a typical 50-area shard at the pilot's 5,710 rows/area.
MAX_PREPARATION_ROWS: Final = 500_000
MAX_PREPARATION_SECONDS: Final = 1800
#: Rows per Morton-chunked part; the archive's page-sized parts become spatial chunks of this size.
MAX_PART_ROWS: Final = 500
MAX_MANIFEST_BYTES: Final = 8 * 1024 * 1024
#: Shards one release index may pin; 264 censused areas need at least six 50-area shards.
MAX_RELEASE_SHARDS: Final = 32
MAX_RELEASE_BYTES: Final = 1024 * 1024
#: Serving caps (placeholders kept at Freeze 2; the pilot measured rows, not parts).
MAX_PARTS_PER_VIEWPORT: Final = 16
MAX_VIEWPORT_BYTES: Final = 32 * 1024 * 1024
MAX_VIEWPORT_ROWS: Final = 1000

ShardId = Annotated[str, Field(pattern=r"^[A-Z]{2}-[0-9]{1,3}$")]
GeometryQuality = Literal["valid", "repaired", "invalid_unrepaired"]
GEOMETRY_QUALITIES: Final[tuple[GeometryQuality, ...]] = ("valid", "repaired", "invalid_unrepaired")
Bounds = tuple[float, float, float, float]

_SHARD_PATTERN: Final = re.compile(r"[A-Z]{2}-[0-9]{1,3}")


def require_shard(value: str) -> str:
    """Validate an operator-declared shard id such as `WA-1`."""
    if _SHARD_PATTERN.fullmatch(value) is None:
        raise ValueError(f"invalid shard id {value!r}; expected two capitals, a dash and 1-3 digits, e.g. WA-1")
    return value


def is_wgs84_extent(bbox: Bounds, *, positive: bool) -> bool:
    """True for finite, ordered WGS84 bounds; `positive` also demands a nonzero width and height."""
    west, south, east, north = bbox
    if not all(math.isfinite(value) for value in bbox):
        return False
    in_range = west >= -WGS84_MAX_LONGITUDE and east <= WGS84_MAX_LONGITUDE
    in_range = in_range and south >= -WGS84_MAX_LATITUDE and north <= WGS84_MAX_LATITUDE
    ordered = (west < east and south < north) if positive else (west <= east and south <= north)
    return in_range and ordered


def union_bounds(boxes: Sequence[Bounds]) -> Bounds:
    """The smallest bounds containing every box in a nonempty sequence."""
    if not boxes:
        raise ValueError("union of no bounds")
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


def part_count_for(rows: int) -> int:
    """Parts one area's rows chunk into: ceiling division, integers only."""
    return -(-rows // MAX_PART_ROWS)


def manifest_key(sha256: str) -> str:
    """Object key of one staged shard manifest."""
    return f"{ROOT}/manifests/{sha256}.json"


def release_key(sha256: str) -> str:
    """Object key of one staged release index."""
    return f"{ROOT}/releases/{sha256}.json"


class Part(FrozenModel):
    """One immutable zstd Parquet chunk of at most `MAX_PART_ROWS` Morton-adjacent rows of one area."""

    blob: Blob
    area: AreaSymbol
    rung: Literal[13]
    row_count: int = Field(gt=0, le=MAX_PART_ROWS)
    repaired_rows: int = Field(ge=0)
    labelled_rows: int = Field(ge=0)
    bbox: tuple[float, float, float, float]

    @model_validator(mode="after")
    def valid_part(self) -> Part:
        if not is_wgs84_extent(self.bbox, positive=False):
            raise ValueError("part bounds must be finite, ordered WGS84 extent")
        if self.repaired_rows + self.labelled_rows > self.row_count:
            raise ValueError("part quality counts exceed its row count")
        return self


class AreaQuality(FrozenModel):
    """One area's geometry-quality ledger (owner Q4): every row is served, so these sum to its census."""

    area: AreaSymbol
    valid_rows: int = Field(ge=0)
    repaired_rows: int = Field(ge=0)
    labelled_rows: int = Field(ge=0)

    @property
    def total_rows(self) -> int:
        return self.valid_rows + self.repaired_rows + self.labelled_rows


def _check_scope(candidate: Candidate) -> list[str]:
    symbols = [area.opening.area for area in candidate.areas]
    if candidate.required_rungs != REQUIRED_RUNGS:
        raise ValueError(f"candidate must declare exactly the frozen rungs {REQUIRED_RUNGS}")
    if not 1 <= len(symbols) <= MAX_SURVEY_AREAS:
        raise ValueError(f"a shard holds 1..{MAX_SURVEY_AREAS} survey areas, not {len(symbols)}")
    if symbols != sorted(set(symbols)) or any(area.closing is None for area in candidate.areas):
        raise ValueError("shard areas must be unique, sorted and completely captured")
    rows = sum(area.opening.count for area in candidate.areas)
    if rows > MAX_PREPARATION_ROWS:
        raise ValueError(f"shard holds {rows} rows, over the {MAX_PREPARATION_ROWS}-row preparation cap")
    return symbols


def _check_parts(candidate: Candidate, symbols: list[str]) -> None:
    if not candidate.parts or any(part.area not in symbols for part in candidate.parts):
        raise ValueError("empty candidate or part outside declared survey scope")
    if len({part.blob.sha256 for part in candidate.parts}) != len(candidate.parts):
        raise ValueError("duplicate candidate part")
    for area in candidate.areas:
        for rung in candidate.required_rungs:
            owned = [part for part in candidate.parts if part.area == area.opening.area and part.rung == rung]
            if sum(part.row_count for part in owned) != area.opening.count:
                raise ValueError("survey-area counts do not reconcile with the census at every required rung")
            if len(owned) != part_count_for(area.opening.count):
                raise ValueError("survey-area parts are not full Morton chunks")


def _check_quality(candidate: Candidate, symbols: list[str]) -> None:
    if [quality.area for quality in candidate.quality] != symbols:
        raise ValueError("geometry-quality ledger must name every shard area once, in order")
    for area, quality in zip(candidate.areas, candidate.quality, strict=True):
        if quality.total_rows != area.opening.count:
            raise ValueError("valid + repaired + labelled rows must equal the area census")
        native = [part for part in candidate.parts if part.area == quality.area and part.rung == NATIVE_RUNG]
        if (sum(part.repaired_rows for part in native), sum(part.labelled_rows for part in native)) != (
            quality.repaired_rows,
            quality.labelled_rows,
        ):
            raise ValueError("part quality counts do not reconcile with the area ledger")


class Candidate(FrozenModel):
    """One shard: complete area captures, their quality ledger, and native z13 parts."""

    format: Literal["ssurgo-candidate/v2"] = "ssurgo-candidate/v2"
    source: Literal["usda-sda"] = "usda-sda"
    source_url: Literal["https://sdmdataaccess.nrcs.usda.gov/Tabular/post.rest"] = (
        "https://sdmdataaccess.nrcs.usda.gov/Tabular/post.rest"
    )
    crs: Literal["EPSG:4326"] = "EPSG:4326"
    shard: ShardId
    required_rungs: tuple[Literal[13], ...] = REQUIRED_RUNGS
    captured_at: datetime
    release_day: date
    areas: tuple[AreaCapture, ...]
    quality: tuple[AreaQuality, ...]
    parts: tuple[Part, ...]

    @model_validator(mode="after")
    def complete_ladder(self) -> Candidate:
        symbols = _check_scope(self)
        _check_parts(self, symbols)
        _check_quality(self, symbols)
        if self.captured_at.tzinfo is None or self.release_day != max(a.opening.vintage for a in self.areas):
            raise ValueError("capture time and source vintage have different explicit roles")
        if self.captured_at != max(a.closing.checked_at for a in self.areas if a.closing is not None):
            raise ValueError("candidate capture time must bind the last closing source census")
        return self


class ShardArea(FrozenModel):
    """One area as a release index sees it: vintage, served rows and quality counts."""

    area: AreaSymbol
    saverest: str = Field(min_length=10)
    native_rows: int = Field(ge=0)
    repaired_rows: int = Field(ge=0)
    labelled_rows: int = Field(ge=0)


class ShardRef(FrozenModel):
    """One staged shard manifest, summarised so serving can prune shards without opening them."""

    shard: ShardId
    manifest: Blob
    release_day: date
    captured_at: datetime
    areas: tuple[ShardArea, ...] = Field(min_length=1, max_length=MAX_SURVEY_AREAS)
    bbox: tuple[float, float, float, float]

    @model_validator(mode="after")
    def valid_shard(self) -> ShardRef:
        symbols = [area.area for area in self.areas]
        if symbols != sorted(set(symbols)):
            raise ValueError("shard areas must be unique and sorted")
        if not is_wgs84_extent(self.bbox, positive=False) or self.captured_at.tzinfo is None:
            raise ValueError("shard bounds or capture clock are invalid")
        return self

    @property
    def native_rows(self) -> int:
        return sum(area.native_rows for area in self.areas)

    @classmethod
    def from_candidate(cls, candidate: Candidate, manifest: Blob) -> ShardRef:
        """Summarise one validated candidate whose canonical manifest bytes are `manifest`."""
        quality = {entry.area: entry for entry in candidate.quality}
        return cls(
            shard=candidate.shard,
            manifest=manifest,
            release_day=candidate.release_day,
            captured_at=candidate.captured_at,
            areas=tuple(
                ShardArea(
                    area=capture.opening.area,
                    saverest=capture.opening.saverest,
                    native_rows=capture.opening.count,
                    repaired_rows=quality[capture.opening.area].repaired_rows,
                    labelled_rows=quality[capture.opening.area].labelled_rows,
                )
                for capture in candidate.areas
            ),
            bbox=union_bounds([part.bbox for part in candidate.parts]),
        )


class Release(FrozenModel):
    """The pinned release index: staged shards, the scope census, and the areas still pending."""

    format: Literal["ssurgo-release/v1"] = "ssurgo-release/v1"
    source: Literal["usda-sda"] = "usda-sda"
    required_rungs: tuple[Literal[13], ...] = REQUIRED_RUNGS
    scope: AreaInventory
    shards: tuple[ShardRef, ...]
    pending_areas: tuple[AreaSymbol, ...]
    source_evidence: Literal["staged", "capture_volume"]
    release_day: date
    captured_at: datetime

    @model_validator(mode="after")
    def complete_index(self) -> Release:
        if self.required_rungs != REQUIRED_RUNGS:
            raise ValueError(f"release must declare exactly the frozen rungs {REQUIRED_RUNGS}")
        if not 1 <= len(self.shards) <= MAX_RELEASE_SHARDS:
            raise ValueError(f"release index holds {len(self.shards)} shards; the cap is 1..{MAX_RELEASE_SHARDS}")
        if len({shard.shard for shard in self.shards}) != len(self.shards) or len(
            {shard.manifest.sha256 for shard in self.shards}
        ) != len(self.shards):
            raise ValueError("release shards repeat a shard id or manifest")
        scope = {entry.area: entry.saverest for entry in self.scope.areas}
        served = [area for shard in self.shards for area in shard.areas]
        if len({area.area for area in served}) != len(served):
            raise ValueError("a survey area appears in more than one shard")
        if any(scope.get(area.area) != area.saverest for area in served):
            raise ValueError("a shard area is outside the scope census or at a different vintage")
        if list(self.pending_areas) != sorted(set(scope) - {area.area for area in served}):
            raise ValueError("pending areas must be exactly the censused areas no shard serves")
        if self.release_day != max(shard.release_day for shard in self.shards):
            raise ValueError("release day must be the newest shard vintage")
        if self.captured_at != max(shard.captured_at for shard in self.shards):
            raise ValueError("release capture time must bind the newest shard capture")
        return self


__all__ = [
    "GEOMETRY_QUALITIES",
    "MAX_MANIFEST_BYTES",
    "MAX_PARTS_PER_VIEWPORT",
    "MAX_PART_ROWS",
    "MAX_PREPARATION_ROWS",
    "MAX_PREPARATION_SECONDS",
    "MAX_RELEASE_BYTES",
    "MAX_RELEASE_SHARDS",
    "MAX_SURVEY_AREAS",
    "MAX_VIEWPORT_BYTES",
    "MAX_VIEWPORT_ROWS",
    "NATIVE_RUNG",
    "REQUIRED_RUNGS",
    "AreaQuality",
    "Bounds",
    "Candidate",
    "GeometryQuality",
    "Part",
    "Release",
    "ShardArea",
    "ShardId",
    "ShardRef",
    "is_wgs84_extent",
    "manifest_key",
    "part_count_for",
    "release_key",
    "require_shard",
    "union_bounds",
]
