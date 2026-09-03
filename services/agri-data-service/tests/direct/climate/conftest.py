"""The REAL 397-cell POWER lattice, read from the plan, plus in-memory point responses over it."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import pytest

from agri_data_service.pipeline.direct.climate.products import (
    CLIMATE_FIELD_PRODUCT_BY_STREAM,
    CLIMATE_SOURCE_PARAMETERS,
)
from agri_data_service.pipeline.direct.climate.source import ClimateSourceCache, parse_climate_point_body
from agri_data_service.pipeline.direct.climate.support import NasaPowerSupportCell, build_support

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import date

    from agri_data_service.pipeline.direct.climate.products import ClimateFieldProduct
    from agri_data_service.pipeline.direct.climate.source import ClimateCellDayResponse
    from agri_data_service.pipeline.direct.climate.support import NasaPowerSupport

#: The plan the historical NASA POWER backfill ran, and the ONLY in-tree source of the 397 cells.
#: A synthetic lattice would be free to sit on the wrong step, which is exactly the mistake the
#: writer made; see `pipeline/direct/AGENTS.md`, "The `grid_name` misnomer".
NASA_PLAN_PATH: Final = (
    Path(__file__).resolve().parents[3] / "plans" / "nasa-power-western-na-weather-fast-20220806-20260806.json"
)

#: A stable namespace so a plan cell always renders the same `cell_id`; the real ids live in
#: `agri.spatial_cell` and no test reaches a database.
CELL_ID_NAMESPACE: Final = uuid.UUID("00000000-0000-4000-8000-000000000000")

#: POWER's declared fill value, read off the live capture's `header.fill_value`.
POWER_FILL_VALUE: Final = -999.0

#: The elevation ordinate the live point response carries as its third coordinate.
SAMPLE_ELEVATION_METRES: Final = 385.68

DEFAULT_PARAMETER_VALUE: Final = 12.5
FETCHED_AT: Final = datetime(2026, 8, 26, 6, 0, tzinfo=UTC)


def plan_cells() -> tuple[NasaPowerSupportCell, ...]:
    """Read the pinned lattice out of the immutable backfill plan, in `cell_key` order."""
    plan = json.loads(NASA_PLAN_PATH.read_text(encoding="utf-8"))
    return tuple(
        NasaPowerSupportCell(
            cell_id=str(uuid.uuid5(CELL_ID_NAMESPACE, entry["cell_key"])),
            cell_key=entry["cell_key"],
            cell_longitude=float(entry["longitude"]),
            cell_latitude=float(entry["latitude"]),
            coverage_fraction=1.0,
        )
        for entry in sorted(plan["nasa"]["cells"], key=lambda entry: str(entry["cell_key"]))
    )


@pytest.fixture
def support() -> NasaPowerSupport:
    """The complete pinned POWER support every climate test binds a response to."""
    return build_support(plan_cells())


def point_body(
    cell: NasaPowerSupportCell,
    *,
    day: date,
    values: Mapping[str, float | None] | None = None,
) -> bytes:
    """Render one point response in the shape the live capture proved, carrying every parameter."""
    stamp = day.strftime("%Y%m%d")
    parameters: dict[str, Any] = {}
    for parameter in CLIMATE_SOURCE_PARAMETERS:
        value = DEFAULT_PARAMETER_VALUE if values is None else values.get(parameter, DEFAULT_PARAMETER_VALUE)
        parameters[parameter] = {stamp: POWER_FILL_VALUE if value is None else value}
    return json.dumps(
        {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [cell.cell_longitude, cell.cell_latitude, SAMPLE_ELEVATION_METRES],
            },
            "properties": {"parameter": parameters},
            "header": {"fill_value": POWER_FILL_VALUE, "time_standard": "UTC", "start": stamp, "end": stamp},
        }
    ).encode("utf-8")


def cell_day_response(
    cell: NasaPowerSupportCell,
    *,
    day: date,
    values: Mapping[str, float | None] | None = None,
    retrieved_at: datetime = FETCHED_AT,
) -> ClimateCellDayResponse:
    """Parse one rendered point body exactly as the live fetch would, so no test bypasses the parser."""
    return parse_climate_point_body(
        cell,
        day=day,
        body=point_body(cell, day=day, values=values),
        request_url=f"https://power.larc.nasa.gov/api/temporal/daily/point?cell={cell.cell_key}",
        retrieved_at=retrieved_at,
    )


def filled_cache(  # noqa: PLR0913 - one dimension of the day being staged per argument
    support: NasaPowerSupport,
    *,
    day: date,
    values: Mapping[str, float | None] | None = None,
    fill_cell_keys: Sequence[str] = (),
    omit_cell_keys: Sequence[str] = (),
    request_budget: int | None = None,
) -> ClimateSourceCache:
    """Build a turn cache already holding this day, so no test opens a socket to exercise the writer."""
    cache = ClimateSourceCache(request_budget=len(support.cells) if request_budget is None else request_budget)
    for cell in support.cells:
        if cell.cell_key in omit_cell_keys:
            continue
        cell_values = dict.fromkeys(CLIMATE_SOURCE_PARAMETERS) if cell.cell_key in fill_cell_keys else values
        cache.hold(cell_day_response(cell, day=day, values=cell_values))
    return cache


def product_for(stream: str) -> ClimateFieldProduct:
    """Resolve one stream slug to its product descriptor, so a test names the stream it means."""
    return CLIMATE_FIELD_PRODUCT_BY_STREAM[stream]
