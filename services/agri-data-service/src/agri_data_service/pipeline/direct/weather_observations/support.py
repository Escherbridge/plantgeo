"""The weather-observations support: a bbox-derived sample grid, not a pinned warehouse dimension.

UNLIKE `pipeline/direct/climate/support.py` and `pipeline/direct/soil/support.py`, which read a
support lattice out of `agri.spatial_cell` because the immutable history they extend was built
against that exact dimension, this lane's history was never built against a stored lattice at all:
`ingest/open_meteo.py::bounded_sample_points` computes a grid from `INGEST_BBOX` and a spacing at
CALL TIME, on demand, and every historical `geo.features` row for this layer was written by exactly
that function. Reproducing the identical call here -- not inventing a `spatial_cell` grid_name for it
-- is what keeps a direct-written day's sample points identical to what the retired ingest cron would
have sampled for the same bbox and spacing.
"""

from __future__ import annotations

from agri_data_service.ingest.open_meteo import bounded_sample_points
from agri_data_service.ingest.policy import (
    MAX_WEATHER_SAMPLE_POINTS,
    resolve_bounded_bbox,
    resolve_weather_sample_spacing_degrees,
)


class WeatherSupportError(ValueError):
    """Raised when no bbox is configured to sample the current-conditions grid from."""


def weather_sample_points(bbox: str | None = None) -> tuple[tuple[float, float], ...]:
    """Return the bounded sample grid for one poll: (latitude, longitude) pairs, deterministic per bbox.

    `bbox` defaults to `INGEST_BBOX` (via `resolve_bounded_bbox`), the same environment fact
    `run_weather_ingestion_job` reads. The spacing is read at call time
    (`resolve_weather_sample_spacing_degrees`) so an environment change needs no restart, matching
    the ingest job's own discipline.
    """
    resolved = resolve_bounded_bbox(bbox)
    if resolved is None:
        raise WeatherSupportError("no bbox configured: pass --bbox or set INGEST_BBOX")
    return tuple(bounded_sample_points(resolved, resolve_weather_sample_spacing_degrees(), MAX_WEATHER_SAMPLE_POINTS))


__all__ = ["WeatherSupportError", "weather_sample_points"]
