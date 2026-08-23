"""Serving read for the weather-observations current-conditions side lane: Polars over object-store Parquet.

Layer L3 planes: may import `foundation`, `method`, `warehouse`, `pipeline`; may NOT import
`interface`. This is Open-Meteo's current-conditions poll (`ingest/open_meteo.py`'s `WEATHER_LAYER`)
landing in `geo.features` -- NOT the governed NASA POWER / ERA5-Land archive behind
`agri.signal_observation` that `docs/lanes/weather-observations.md` actually describes; that plane is
`signal`, served separately. See `warehouse/schemas/weather_observations.py`'s module docstring for
the full citation trail, and `conductor/code_styleguides/layer-lanes.md` section 2 for why `kind` is a
partition, never a column branch, and is never blended.

This lane has published only `kind="observed"` since its writer (`pipeline/lanes/weather_observations.py`)
never calls `write_partition` with `kind="forecast"` -- a rolling current-conditions poll has no
forecast to publish. `OBSERVED_KIND` is the only kind this module ever builds a scan path for; there
is no parameter that could widen it, so a caller cannot request "forecast" here and receive a row
silently blended in from the `signal` stream. A day nothing was ever polled for -- including every
future date -- reads back as a genuinely empty, correctly-typed frame, never an exception and never a
fallback to another stream's data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

import polars as pl

from agri_data_service.foundation.parquet.paths import MAX_GAP_WINDOW_DAYS, stream_prefix
from agri_data_service.warehouse.schemas.weather_observations import (
    WEATHER_OBSERVATIONS_SCHEMA,
    WEATHER_OBSERVATIONS_STREAM,
)

if TYPE_CHECKING:
    from datetime import date

    from agri_data_service.config import ObjectStoreCredentials
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

# The only partition kind this side lane ever writes. Baked in here rather than accepted as a
# parameter so nothing calling this module can ask for "forecast" and get a silently-wrong answer.
OBSERVED_KIND: Final[Literal["observed"]] = "observed"

# Reused directly from the schema's own registered Arrow contract, so a scan of a day nothing was
# ever written for returns zero TYPED rows instead of raising `polars.exceptions.ComputeError` on an
# empty glob -- see `read_weather_observations_window`. `pl.from_arrow` is typed to return
# `DataFrame | Series`; a `pa.Table` (which `empty_table()` always returns) only ever yields the
# former, and the assertion makes that a checked fact rather than an unstated assumption.
_EMPTY_WEATHER_OBSERVATIONS_FRAME = pl.from_arrow(WEATHER_OBSERVATIONS_SCHEMA.arrow_schema.empty_table())
assert isinstance(_EMPTY_WEATHER_OBSERVATIONS_FRAME, pl.DataFrame)
_WEATHER_OBSERVATIONS_POLARS_SCHEMA: Final = _EMPTY_WEATHER_OBSERVATIONS_FRAME.schema


class WeatherObservationsServingError(ValueError):
    """Raised when a serving-read request cannot be answered honestly."""


@dataclass(frozen=True, slots=True)
class WeatherObservationsReading:
    """One `kind=observed` window, its kind spelled out so it can never be mistaken for a forecast."""

    kind: Literal["observed"]
    first_day: date
    last_day: date
    frame: pl.DataFrame


def weather_observations_scan_pattern(*, root: str) -> str:
    """Return the glob `read_weather_observations_window` scans, rooted at `root`, for `kind=observed` only."""
    normalized_root = root if root.endswith("/") else f"{root}/"
    return f"{normalized_root}{stream_prefix(WEATHER_OBSERVATIONS_STREAM, OBSERVED_KIND)}**/*.parquet"


def bucket_object_root(*, credentials: ObjectStoreCredentials, store: ObjectStore) -> str:
    """Return the production scan root: the bucket URI plus the store's own key prefix, if any."""
    return f"s3://{credentials.bucket}/{store.prefix}" if store.prefix else f"s3://{credentials.bucket}/"


def read_weather_observations_window(
    *,
    root: str,
    first_day: date,
    last_day: date,
    storage_options: dict[str, str] | None = None,
) -> WeatherObservationsReading:
    """Read every `kind=observed` reading in `[first_day, last_day]`; a day nothing was polled for reads empty.

    Rows keep their full per-instant grain (`latitude`, `longitude`, `observed_at`): several distinct
    polls can land on one point in one day, and this never collapses them to one row per point per
    day. A window reaching into the future needs no special case -- no file's `observed_day` can match
    a date nothing has been exported for yet, so the filter alone answers it honestly, and nothing
    here ever opens a `signal`-stream or `kind=forecast` path to fill the gap.
    """
    if last_day < first_day:
        raise WeatherObservationsServingError(f"window {first_day}..{last_day} runs backwards")
    span_days = (last_day - first_day).days + 1
    if span_days > MAX_GAP_WINDOW_DAYS:
        raise WeatherObservationsServingError(
            f"serving window of {span_days} days exceeds the {MAX_GAP_WINDOW_DAYS}-day budget"
        )
    scanned = pl.scan_parquet(
        weather_observations_scan_pattern(root=root),
        hive_partitioning=False,
        schema=_WEATHER_OBSERVATIONS_POLARS_SCHEMA,
        storage_options=storage_options,
    )
    frame = (
        scanned.filter(pl.col("observed_day").is_between(first_day, last_day))
        .select(list(WEATHER_OBSERVATIONS_SCHEMA.column_names))
        .sort(list(WEATHER_OBSERVATIONS_SCHEMA.sort_columns))
        .collect()
    )
    return WeatherObservationsReading(kind=OBSERVED_KIND, first_day=first_day, last_day=last_day, frame=frame)


def read_weather_observations_day(
    *,
    root: str,
    day: date,
    storage_options: dict[str, str] | None = None,
) -> WeatherObservationsReading:
    """Read one day's `kind=observed` readings; a future or never-polled day comes back empty, honestly."""
    return read_weather_observations_window(root=root, first_day=day, last_day=day, storage_options=storage_options)
