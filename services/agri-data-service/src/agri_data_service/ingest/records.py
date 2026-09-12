"""Source records shared by direct-to-Parquet acquisition and adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

GRID_NAME_MAX_LENGTH = 100
CELL_KEY_MAX_LENGTH = 180

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from agri_data_service.ingest.identity import FeatureIdentity


class SourceRecordError(ValueError):
    """Raised when a source record falls outside the direct writer contract."""


@dataclass(frozen=True, slots=True)
class GridCell:
    """One source grid cell carried into a direct Parquet row builder."""

    grid_name: str
    cell_key: str
    resolution_metres: int
    geojson: str

    def __post_init__(self) -> None:
        if not 0 < len(self.grid_name) <= GRID_NAME_MAX_LENGTH:
            raise SourceRecordError("grid_name must be 1-100 characters")
        if not 0 < len(self.cell_key) <= CELL_KEY_MAX_LENGTH:
            raise SourceRecordError("cell_key must be 1-180 characters")
        if self.resolution_metres <= 0:
            raise SourceRecordError("resolution_metres must be positive")
        if not self.geojson.strip():
            raise SourceRecordError("grid cells must carry GeoJSON")


@dataclass(frozen=True, slots=True)
class FeatureWrite:
    """One normalized upstream record ready for a direct Parquet adapter."""

    layer_reference: str
    identity: FeatureIdentity
    properties: Mapping[str, object]
    channel: str
    grid_cell: GridCell | None = None

    @property
    def external_id(self) -> str:
        return self.identity.producer_local_id

    @property
    def natural_key(self) -> str:
        return self.identity.natural_key

    @property
    def data_available_at(self) -> datetime | None:
        return self.identity.data_available_at

    def stored_properties(self) -> dict[str, object]:
        return {**self.properties, "id": self.external_id}


class FeatureWriter(Protocol):
    """Compatibility typing seam for pure source helpers."""

    async def __call__(self, writes: Sequence[FeatureWrite]) -> int: ...


__all__ = ["FeatureWrite", "FeatureWriter", "GridCell", "SourceRecordError"]
