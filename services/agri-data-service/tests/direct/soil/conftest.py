"""The REAL 1,568-cell ERA5-Land lattice, read from the reviewed plan, plus in-memory archive bodies."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import pytest

from agri_data_service.execution.open_meteo_lane import canonical_location_document
from agri_data_service.execution.weather_observations.era5_land import (
    OPEN_METEO_ARCHIVE_LANE,
    OPEN_METEO_ARCHIVE_NATIVE_GRID_DEGREES,
)
from agri_data_service.pipeline.direct.soil.products import (
    SOIL_FIELD_PRODUCT_BY_STREAM,
    SOIL_SOURCE_PARAMETERS,
)
from agri_data_service.pipeline.direct.soil.source import (
    SoilSourceCache,
    parse_soil_chunk_body,
    soil_chunk_url,
    support_chunks,
)
from agri_data_service.pipeline.direct.soil.support import (
    ERA5_LAND_SUPPORT_CELL_COUNT,
    ERA5_LAND_VALUE_CELL_COUNT,
    Era5LandSupportCell,
    build_support,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from agri_data_service.pipeline.direct.soil.products import SoilFieldProduct
    from agri_data_service.pipeline.direct.soil.source import Era5LandChunk, SoilChunkDayResponse
    from agri_data_service.pipeline.direct.soil.support import Era5LandSupport

#: One of the three reviewed ERA5-Land plans, and the ONLY in-tree source of the 1,568 cells. A
#: synthetic lattice would be free to sit on the wrong step or the wrong offset, which is the whole
#: class of mistake `require_pinned_lattice_cell` exists to catch.
ERA5_PLAN_PATH: Final = (
    Path(__file__).resolve().parents[3] / "plans" / "open-meteo-era5-land-pnw-vpd-20220802-20260802.json"
)

#: A stable namespace so a plan cell always renders the same `cell_id`; the real ids live in
#: `agri.spatial_cell` and no test reaches a database.
CELL_ID_NAMESPACE: Final = uuid.UUID("00000000-0000-4000-8000-000000000000")

#: A plausible mid-range value for every variable at once. The products' acceptance ranges differ
#: ([0,1] volumetric, [-100,70] Celsius, [0,15] kPa) and this sits inside all of them, so one default
#: keeps a test that is about caching or ordering from also being about physics.
DEFAULT_PARAMETER_VALUE: Final = 0.5

SAMPLE_ELEVATION_METRES: Final = 385.68
FETCHED_AT: Final = datetime(2026, 9, 2, 6, 0, tzinfo=UTC)


def plan_cells() -> tuple[Era5LandSupportCell, ...]:
    """Read the pinned lattice out of the reviewed archive plan, in `cell_key` order."""
    plan = json.loads(ERA5_PLAN_PATH.read_text(encoding="utf-8"))
    return tuple(
        Era5LandSupportCell(
            cell_id=str(uuid.uuid5(CELL_ID_NAMESPACE, entry["cell_key"])),
            cell_key=entry["cell_key"],
            cell_longitude=float(entry["longitude"]),
            cell_latitude=float(entry["latitude"]),
            coverage_fraction=1.0,
        )
        for entry in sorted(plan["cells"], key=lambda entry: str(entry["cell_key"]))
    )


@pytest.fixture
def support() -> Era5LandSupport:
    """The complete pinned lattice every soil test binds a response to."""
    return build_support(plan_cells())


@pytest.fixture
def chunks(support: Era5LandSupport) -> tuple[Era5LandChunk, ...]:
    """The chunking one turn requests that lattice in."""
    return support_chunks(support)


def masked_cell_keys(support: Era5LandSupport) -> frozenset[str]:
    """The 98 cells a complete day answers null for, as a COUNT-FAITHFUL stand-in for the real mask.

    The real 98 are wherever ERA5-Land models no land, and nothing in the tree names them; what IS
    pinned is that exactly 1,470 of 1,568 carry a value on every one of the 1,556 immutable days.
    Taking the first 98 in support order reproduces the count the writer refuses on, which is the
    only property these fixtures are allowed to assert.
    """
    masked = ERA5_LAND_SUPPORT_CELL_COUNT - ERA5_LAND_VALUE_CELL_COUNT
    return frozenset(cell.cell_key for cell in support.cells[:masked])


def native_grid_point(cell: Era5LandSupportCell) -> tuple[float, float]:
    """Snap one analysis centroid to the ERA5-Land box the archive really answers from.

    The provider echoes its own 0.1-degree node, never the 0.25-degree centroid that was asked for,
    so a fixture that echoed the request back would never exercise the attribution guard.
    """
    step = OPEN_METEO_ARCHIVE_NATIVE_GRID_DEGREES
    return (round(round(cell.cell_latitude / step) * step, 6), round(round(cell.cell_longitude / step) * step, 6))


def location_object(
    cell: Era5LandSupportCell,
    *,
    day: date,
    ordinal: int,
    values: Mapping[str, float | None] | None = None,
) -> dict[str, Any]:
    """Render one location entry in the shape `parse_open_meteo_archive_payload` reads."""
    latitude, longitude = native_grid_point(cell)
    daily: dict[str, Any] = {"time": [day.isoformat()]}
    for parameter in SOIL_SOURCE_PARAMETERS:
        value = DEFAULT_PARAMETER_VALUE if values is None else values.get(parameter, DEFAULT_PARAMETER_VALUE)
        daily[parameter] = [value]
    location: dict[str, Any] = {
        "latitude": latitude,
        "longitude": longitude,
        "elevation": SAMPLE_ELEVATION_METRES,
        # Stripped by `canonical_location_document`; present because the provider always sends it.
        "generationtime_ms": 0.123,
        "utc_offset_seconds": 0,
        "timezone": "GMT",
        "timezone_abbreviation": "GMT",
        "daily_units": {"time": "iso8601", **dict.fromkeys(SOIL_SOURCE_PARAMETERS, "unit")},
        "daily": daily,
    }
    if ordinal:
        # The provider omits `location_id` on the first entry and numbers the rest from 1.
        location["location_id"] = ordinal
    return location


def chunk_body(
    chunk: Era5LandChunk,
    *,
    day: date,
    values: Mapping[str, float | None] | None = None,
    null_cell_keys: Sequence[str] = (),
) -> bytes:
    """Render one chunk's canonical archive document, through the real canonicalizer."""
    locations = [
        location_object(
            cell,
            day=day,
            ordinal=ordinal,
            values=dict.fromkeys(SOIL_SOURCE_PARAMETERS) if cell.cell_key in null_cell_keys else values,
        )
        for ordinal, cell in enumerate(chunk.cells)
    ]
    return canonical_location_document(OPEN_METEO_ARCHIVE_LANE, json.dumps(locations).encode("utf-8"))


def chunk_day_response(
    chunk: Era5LandChunk,
    *,
    day: date,
    values: Mapping[str, float | None] | None = None,
    null_cell_keys: Sequence[str] = (),
    retrieved_at: datetime = FETCHED_AT,
) -> SoilChunkDayResponse:
    """Parse one rendered chunk body exactly as the live fetch would, so no test bypasses the parser."""
    return parse_soil_chunk_body(
        chunk,
        day=day,
        body=chunk_body(chunk, day=day, values=values, null_cell_keys=null_cell_keys),
        request_url=soil_chunk_url(chunk, day=day),
        retrieved_at=retrieved_at,
    )


def filled_cache(  # noqa: PLR0913 - one dimension of the day being staged per argument
    support: Era5LandSupport,
    chunks: Sequence[Era5LandChunk],
    *,
    day: date,
    values: Mapping[str, float | None] | None = None,
    null_cell_keys: Sequence[str] | None = None,
    omit_chunk_keys: Sequence[str] = (),
    request_budget: int | None = None,
) -> SoilSourceCache:
    """Build a turn cache already holding this day, so no test opens a socket to exercise the writer.

    `null_cell_keys` defaults to the 98-cell mask, so the staged day carries the 1,470 values the
    writer requires; pass `()` for an all-answering day and a full set for a governed absence.
    """
    masked = masked_cell_keys(support) if null_cell_keys is None else frozenset(null_cell_keys)
    cache = SoilSourceCache(request_budget=len(chunks) if request_budget is None else request_budget)
    for chunk in chunks:
        if chunk.key in omit_chunk_keys:
            continue
        cache.hold(chunk_day_response(chunk, day=day, values=values, null_cell_keys=tuple(masked)))
    return cache


def product_for(stream: str) -> SoilFieldProduct:
    """Resolve one stream slug to its product descriptor, so a test names the stream it means."""
    return SOIL_FIELD_PRODUCT_BY_STREAM[stream]
