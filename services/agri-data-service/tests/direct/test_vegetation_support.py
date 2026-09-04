"""The pinned 1,568-cell NDVI lattice: the half-step offset trap, the count guard, the cellKey join."""

from __future__ import annotations

import itertools

import pytest

from agri_data_service.pipeline.direct.vegetation.support import (
    NDVI_SUPPORT_CELL_COUNT,
    NDVI_SUPPORT_EAST,
    NDVI_SUPPORT_NORTH,
    NDVI_SUPPORT_SOUTH,
    NDVI_SUPPORT_STEP_DEGREES,
    NDVI_SUPPORT_WEST,
    VegetationSupportCell,
    VegetationSupportError,
    build_support,
    require_pinned_lattice_cell,
)

#: A minimal, correctly-shaped cell at the pinned lattice's own step and offset -- centroids at ODD
#: multiples of 0.125, the trap this track's brief names by name.
_VALID_CELL = VegetationSupportCell(
    cell_id="00000000-0000-4000-8000-000000000001",
    cell_key="sentinel2-ndvi-0p25deg:43.1250:-116.3750",
    cell_longitude=-116.375,
    cell_latitude=43.125,
)


def _cell(*, longitude: float, latitude: float, key: str | None = None) -> VegetationSupportCell:
    return VegetationSupportCell(
        cell_id="00000000-0000-4000-8000-000000000002",
        cell_key=key or f"sentinel2-ndvi-0p25deg:{latitude:.4f}:{longitude:.4f}",
        cell_longitude=longitude,
        cell_latitude=latitude,
    )


def test_a_correctly_offset_cell_is_accepted() -> None:
    require_pinned_lattice_cell(_VALID_CELL)  # must not raise


def test_an_integer_degree_cell_is_refused() -> None:
    """The odd-multiple-of-0.125 trap: a cell on the whole degree is off THIS lattice's step."""
    off_lattice = _cell(longitude=-116.0, latitude=43.0)

    with pytest.raises(VegetationSupportError, match="step"):
        require_pinned_lattice_cell(off_lattice)


def test_a_cell_outside_the_pinned_extent_is_refused() -> None:
    outside = _cell(longitude=-90.125, latitude=43.125)

    with pytest.raises(VegetationSupportError, match="extent"):
        require_pinned_lattice_cell(outside)


def test_a_cell_key_missing_the_grid_prefix_is_refused() -> None:
    wrong_prefix = _cell(longitude=-116.375, latitude=43.125, key="43.1250:-116.3750")

    with pytest.raises(VegetationSupportError, match="key"):
        require_pinned_lattice_cell(wrong_prefix)


def test_build_support_refuses_a_count_other_than_the_pinned_lattice() -> None:
    with pytest.raises(VegetationSupportError, match=str(NDVI_SUPPORT_CELL_COUNT)):
        build_support([_VALID_CELL])


def test_build_support_refuses_a_duplicate_cell_key() -> None:
    cells = [_VALID_CELL] * NDVI_SUPPORT_CELL_COUNT

    with pytest.raises(VegetationSupportError, match="duplicated"):
        build_support(cells)


def _full_pinned_grid() -> list[VegetationSupportCell]:
    """Tile the pinned extent exactly: 56 longitudes x 28 latitudes = 1,568, the real lattice's shape."""
    step = float(NDVI_SUPPORT_STEP_DEGREES)
    longitudes = [float(NDVI_SUPPORT_WEST) + step * index for index in range(56)]
    latitudes = [float(NDVI_SUPPORT_SOUTH) + step * index for index in range(28)]
    assert longitudes[-1] == pytest.approx(float(NDVI_SUPPORT_EAST))
    assert latitudes[-1] == pytest.approx(float(NDVI_SUPPORT_NORTH))
    return [
        _cell(longitude=longitude, latitude=latitude)
        for latitude, longitude in itertools.product(latitudes, longitudes)
    ]


def test_resolve_joins_an_unprefixed_raw_fetch_cellkey_to_its_prefixed_support_cell() -> None:
    """`ingest/vegetation.py::ndvi_grid_cells` mints `"{lat}:{lon}"`; the support carries the grid prefix."""
    support = build_support(_full_pinned_grid())

    resolved = support.resolve("43.1250:-116.3750")

    assert resolved is not None
    assert resolved.cell_key == "sentinel2-ndvi-0p25deg:43.1250:-116.3750"


def test_resolve_returns_none_for_a_cellkey_off_the_pinned_lattice() -> None:
    support = build_support(_full_pinned_grid())

    assert support.resolve("0.0000:0.0000") is None


def test_the_full_pinned_grid_tiles_to_exactly_the_pinned_count() -> None:
    assert len(_full_pinned_grid()) == NDVI_SUPPORT_CELL_COUNT
