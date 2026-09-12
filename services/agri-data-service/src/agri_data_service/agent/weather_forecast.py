"""Unmounted agent/MCP callable parity for the local forecast fixture."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from agri_data_service.parquet_ops.duckdb_session import run_bounded_read
from agri_data_service.planes.weather_forecast import FieldRequest, LocationRequest, read_field, read_location

if TYPE_CHECKING:
    from pathlib import Path

TOOL_NAME = "weather_forecast_location"
TOOL_DESCRIPTION = (
    "Read the exact pinned synthetic fixture run, coordinates and UTC window. "
    "Never describe fixture values as observations, an admitted forecast, probability or confidence. "
    "Never replace a refused run, time or place with a neighbour."
)


async def weather_forecast_location(root: Path, request: LocationRequest) -> dict[str, object]:
    """Use the identical bounded reader for a future agent or MCP registration."""
    return await run_bounded_read(partial(read_location, root, request, requested_zoom=13), operation=TOOL_NAME)


async def weather_forecast_field(root: Path, request: FieldRequest, *, requested_zoom: int) -> dict[str, object]:
    """Use the same field operation and shared concurrency admission as HTTP."""
    return await run_bounded_read(
        partial(read_field, root, request, requested_zoom=requested_zoom), operation="weather_forecast_field"
    )
