"""Inspect exact in-memory COG bytes and read one native pixel; see AGENTS.md."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Final, Protocol, cast

from agri_data_service.pipeline.static_soil.models import RasterGrid

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from contextlib import AbstractContextManager

    import numpy as np
    from numpy.typing import NDArray

NODATA_VALUE: Final = -32768


class RasterDataset(Protocol):
    """Only the rasterio surface this product uses."""

    width: int
    height: int
    count: int
    transform: Sequence[float]
    bounds: Sequence[float]
    block_shapes: Sequence[tuple[int, int]]
    dtypes: Sequence[str]
    scales: Sequence[float]
    offsets: Sequence[float]
    nodata: float | None
    crs: object

    def tags(self, *, ns: str | None = None) -> Mapping[str, str]: ...

    def read(self, indexes: int, *, window: tuple[tuple[int, int], tuple[int, int]]) -> NDArray[np.int16]: ...


class RasterMemoryFile(Protocol):
    """An isolated local memory buffer with no filesystem or network resolution."""

    def open(self) -> AbstractContextManager[RasterDataset]: ...


class RasterModule(Protocol):
    """The lazy rasterio MemoryFile constructor."""

    MemoryFile: Callable[[bytes], AbstractContextManager[RasterMemoryFile]]


def memory_raster(payload: bytes) -> AbstractContextManager[RasterMemoryFile]:
    """Keep exact verified bytes open throughout decoding."""
    module = cast("RasterModule", importlib.import_module("rasterio.io"))
    return module.MemoryFile(payload)


def inspect_grid(source: RasterDataset) -> RasterGrid:
    """Refuse unsupported metadata before allocating a pixel block."""
    if source.count != 1 or tuple(source.dtypes) != ("int16",) or source.nodata != NODATA_VALUE:
        raise ValueError("saved soil COG requires one int16 band with nodata -32768")
    if str(source.crs) != "EPSG:4326" or source.tags(ns="IMAGE_STRUCTURE").get("LAYOUT") != "COG":
        raise ValueError("saved soil COG requires inspected EPSG:4326 and COG layout")
    transform = tuple(float(value) for value in source.transform[:6])
    return RasterGrid.model_validate(
        {
            "width": source.width,
            "height": source.height,
            "transform": transform,
            "bounds": tuple(float(value) for value in source.bounds),
            "block_shape": tuple(source.block_shapes[0]),
        }
    )


def validate_tags(source: RasterDataset, *, property_name: str, divisor: int, unit: str) -> None:
    """Bind saved tags and raster scale without applying that scale twice."""
    expected = {
        "property": property_name,
        "scale_divisor": str(divisor),
        "unit": unit,
        "depth": "0-5cm",
        "statistic": "mean",
        "source": "ISRIC SoilGrids",
        "source_release": "v2.0",
        "license": "CC-BY 4.0",
        "source_url": f"https://files.isric.org/soilgrids/latest/data/{property_name}/",
    }
    tags = source.tags()
    if any(tags.get(key) != value for key, value in expected.items()):
        raise ValueError("saved soil raster metadata disagrees with the property contract")
    if tuple(source.scales) != (1.0 / divisor,) or tuple(source.offsets) != (0.0,):
        raise ValueError("saved soil raster scale/offset disagrees with the integer divisor")
