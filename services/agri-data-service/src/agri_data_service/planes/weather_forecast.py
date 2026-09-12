"""Read pinned local sampled forecast artifacts; see pipeline/direct/weather_forecast/AGENTS.md."""

from __future__ import annotations

import math
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, model_validator

from agri_data_service.foundation.parquet.zoom import ZoomTier, serving_zoom_tier
from agri_data_service.pipeline.direct.weather_forecast.artifacts import read_active_run, read_published_run
from agri_data_service.warehouse.weather_forecast.contracts import (
    Finite,
    ForecastRun,
    ForecastValue,
    FrozenContract,
    SafeToken,
    UTCInstant,
    ValueStatus,
)
from agri_data_service.warehouse.weather_forecast.variables import (
    VariableName,  # noqa: TC001 - Pydantic field runtime type
)

if TYPE_CHECKING:
    from datetime import datetime

    from agri_data_service.pipeline.direct.weather_forecast.artifacts import ForecastManifest
    from agri_data_service.warehouse.weather_forecast.contracts import ForecastSeries

MAX_SELECTED_VALUES = 4000
MAX_FIELD_ROWS = 50_000
MAX_SAMPLE_DISTANCE_M = 100_000
MIN_RUN_AGE_SECONDS = 60
MAX_LONGITUDE = 180
MAX_LATITUDE = 90

CapabilityStatus = Literal["available", "stale_run", "not_yet_generated", "upstream_unavailable"]


class WeatherForecastCapability(FrozenContract):
    """Describe one verified active sampled run or an explicit unavailable state."""

    schema_version: Literal["weather-forecast-capability/v1"] = "weather-forecast-capability/v1"
    product_id: SafeToken
    source: Literal["local"] = "local"
    status: CapabilityStatus
    active_run_id: SafeToken | None = None
    run: ForecastRun | None = None
    support: Literal["sampled_points"] | None = None
    variables: Annotated[tuple[VariableName, ...], Field(min_length=1, max_length=len(VARIABLES))] | None = None
    valid_start: UTCInstant | None = None
    valid_end: UTCInstant | None = None
    sample_count: Annotated[int, Field(strict=True, ge=1, le=256)] | None = None
    row_count: Annotated[int, Field(strict=True, ge=1, le=50_000)] | None = None

    @model_validator(mode="after")
    def complete_active_identity(self) -> Self:
        metadata = (
            self.active_run_id,
            self.run,
            self.support,
            self.variables,
            self.valid_start,
            self.valid_end,
            self.sample_count,
            self.row_count,
        )
        has_complete_metadata = all(value is not None for value in metadata)
        if self.status in ("available", "stale_run") and not has_complete_metadata:
            raise ValueError("available forecast capability requires complete active-run metadata")
        if self.status in ("not_yet_generated", "upstream_unavailable") and any(
            value is not None for value in metadata
        ):
            raise ValueError("unavailable forecast capability cannot expose unverified run metadata")
        if self.run is not None and (
            self.run.product_id != self.product_id or self.run.run_id != self.active_run_id
        ):
            raise ValueError("forecast capability run identity mismatch")
        if self.run is not None and (
            self.support != "sampled_points"
            or self.run.support.kind != "sampled_point"
            or self.variables != self.run.variables
        ):
            raise ValueError("forecast capability must preserve explicit sampled support and variables")
        if self.valid_start is not None and self.valid_end is not None and not self.valid_start < self.valid_end:
            raise ValueError("forecast capability valid-time window must be positive")
        return self


class ForecastSelection(FrozenContract):
    """Preserve requested place, pinned run, valid window and display timezone."""

    run_id: SafeToken
    longitude: Annotated[Finite, Field(ge=-180, le=180)]
    latitude: Annotated[Finite, Field(ge=-90, le=90)]
    start: UTCInstant
    end: UTCInstant
    timezone: Annotated[str, Field(strict=True, min_length=1, max_length=100)]

    @model_validator(mode="after")
    def valid_window(self) -> Self:
        _window(self.start, self.end)
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("forecast selection requires a known IANA timezone") from error
        return self


class SelectedWeatherForecast(FrozenContract):
    """A pinned location response including absence and actual sample distance."""

    selection: ForecastSelection
    status: ValueStatus
    run: ForecastRun | None
    zoom: ZoomTier
    sample_distance_m: float | None
    values: tuple[ForecastValue, ...]


class ForecastField(FrozenContract):
    """One bounded variable/time window of sampled points without inferred cells."""

    product_id: SafeToken
    run_id: SafeToken
    bbox: tuple[float, float, float, float]
    start: UTCInstant
    end: UTCInstant
    variable: VariableName
    zoom: ZoomTier
    status: ValueStatus
    run: ForecastRun | None
    values: tuple[ForecastValue, ...]


def _unavailable_capability(
    product_id: str, status: Literal["not_yet_generated", "upstream_unavailable"]
) -> WeatherForecastCapability:
    return WeatherForecastCapability(product_id=product_id, status=status)


def _verified_capability(
    *,
    product_id: str,
    manifest: ForecastManifest,
    series: ForecastSeries,
    now: datetime,
    max_run_age_seconds: int,
) -> WeatherForecastCapability:
    status = _run_status(manifest, manifest.start, manifest.end, now, max_run_age_seconds)
    if status == "not_yet_generated":
        return _unavailable_capability(product_id, status)
    if status not in ("available", "stale_run"):
        return _unavailable_capability(product_id, "upstream_unavailable")
    return WeatherForecastCapability(
        product_id=product_id,
        status=status,
        active_run_id=series.run.run_id,
        run=series.run,
        support="sampled_points",
        variables=series.run.variables,
        valid_start=manifest.start,
        valid_end=manifest.end,
        sample_count=len(manifest.samples),
        row_count=manifest.row_count,
    )


def read_local_forecast_capability(
    *,
    root: Path,
    product_id: str,
    now: UTCInstant,
    max_run_age_seconds: int = 172800,
) -> WeatherForecastCapability:
    """Resolve the local active pointer only after verifying its committed sampled run."""
    try:
        manifest, series = read_active_run(root=root, product_id=product_id)
    except FileNotFoundError as error:
        missing_pointer = error.filename is not None and Path(error.filename).name == "active.json"
        status = "not_yet_generated" if missing_pointer else "upstream_unavailable"
        return _unavailable_capability(product_id, status)
    except (OSError, ValueError):
        return _unavailable_capability(product_id, "upstream_unavailable")
    return _verified_capability(
        product_id=product_id,
        manifest=manifest,
        series=series,
        now=now,
        max_run_age_seconds=max_run_age_seconds,
    )


def _window(start: datetime, end: datetime) -> None:
    if start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0):
        raise ValueError("forecast reader requires explicit UTC times")
    if start.minute or start.second or start.microsecond or end.minute or end.second or end.microsecond:
        raise ValueError("forecast reader requires hourly boundaries")
    if not timedelta(0) < end - start <= timedelta(days=10):
        raise ValueError("forecast reader window exceeds ten days or is invalid")


def _load(
    root: Path, product_id: str, run_id: str
) -> tuple[ForecastManifest | None, ForecastSeries | None, ValueStatus]:
    try:
        manifest, series = read_published_run(root=root, product_id=product_id, run_id=run_id)
        return manifest, series, "available"
    except FileNotFoundError as error:
        missing_manifest = (
            error.filename is not None
            and Path(error.filename).parent.name == "published"
            and Path(error.filename).name == run_id + ".json"
        )
        return None, None, "not_yet_generated" if missing_manifest else "upstream_unavailable"
    except (OSError, ValueError):
        return None, None, "upstream_unavailable"


def _run_status(
    manifest: ForecastManifest, start: datetime, end: datetime, now: datetime, max_run_age_seconds: int
) -> ValueStatus:
    if now.utcoffset() != timedelta(0) or not MIN_RUN_AGE_SECONDS <= max_run_age_seconds <= 7 * 86400:
        raise ValueError("invalid reader clock or stale-run age policy")
    if manifest.run.published_at > now:
        return "not_yet_generated"
    if start < manifest.start or end > manifest.end:
        return "exact_absence"
    if (now - manifest.run.model_init_at).total_seconds() > max_run_age_seconds:
        return "stale_run"
    return "available"


def _value_status(values: tuple[ForecastValue, ...]) -> ValueStatus:
    if any(row.status == "available" for row in values):
        return "available"
    statuses = {row.status for row in values}
    return next(iter(statuses)) if len(statuses) == 1 else "missing"


def _distance(lon: float, lat: float, sample_lon: float, sample_lat: float) -> float:
    delta_lat = math.radians(sample_lat - lat)
    delta_lon = math.radians(sample_lon - lon)
    haversine = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(math.radians(lat)) * math.cos(math.radians(sample_lat)) * math.sin(delta_lon / 2) ** 2
    )
    return 6_371_008.8 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, haversine))))


def read_selected_forecast(  # noqa: PLR0913 - explicit host policy stays separate from the pinned selection
    *,
    root: Path,
    product_id: str,
    selection: ForecastSelection,
    requested_zoom: int,
    now: UTCInstant,
    max_distance_m: float,
    max_run_age_seconds: int = 172800,
) -> SelectedWeatherForecast:
    """Return one nearest sample within an explicit distance bound for the pinned place."""
    if type(requested_zoom) is not int:
        raise ValueError("requested zoom must be an integer")
    zoom = serving_zoom_tier(requested_zoom)
    selection = ForecastSelection.model_validate(selection.model_dump(mode="json"))
    if not math.isfinite(max_distance_m) or not 0 <= max_distance_m <= MAX_SAMPLE_DISTANCE_M:
        raise ValueError("sample-distance limit must be within 100 km")
    manifest, series, status = _load(root, product_id, selection.run_id)
    run = series.run if series is not None else None
    values: tuple[ForecastValue, ...] = ()
    distance: float | None = None
    if manifest is not None and series is not None:
        status = _run_status(manifest, selection.start, selection.end, now, max_run_age_seconds)
        if status in ("available", "stale_run"):
            distance, sample_id = min(
                (
                    _distance(selection.longitude, selection.latitude, sample.longitude, sample.latitude),
                    sample.sample_id,
                )
                for sample in manifest.samples
            )
            if distance > max_distance_m:
                status = "outside_domain"
            else:
                count = int((selection.end - selection.start).total_seconds() // 3600) * len(series.run.variables)
                if count > MAX_SELECTED_VALUES:
                    raise ValueError("selected forecast exceeds value budget")
                values = tuple(
                    row
                    for row in series.values
                    if row.sample_id == sample_id and selection.start <= row.valid_at < selection.end
                )
                if status == "available":
                    status = _value_status(values)
    return SelectedWeatherForecast(
        selection=selection, status=status, run=run, zoom=zoom, sample_distance_m=distance, values=values
    )


def read_forecast_field(  # noqa: PLR0913 - bounded field request names independent spatial, temporal and host policy axes
    *,
    root: Path,
    product_id: str,
    run_id: str,
    requested_zoom: int,
    bbox: tuple[float, float, float, float],
    start: UTCInstant,
    end: UTCInstant,
    variable: VariableName,
    now: UTCInstant,
    max_rows: int = MAX_FIELD_ROWS,
    max_run_age_seconds: int = 172800,
) -> ForecastField:
    """Return bounded source samples for one scalar or vector component without interpolation."""
    if type(requested_zoom) is not int:
        raise ValueError("requested zoom must be an integer")
    zoom = serving_zoom_tier(requested_zoom)
    _window(start, end)
    west, south, east, north = bbox
    if not all(math.isfinite(value) for value in bbox) or not (
        -MAX_LONGITUDE <= west < east <= MAX_LONGITUDE and -MAX_LATITUDE <= south < north <= MAX_LATITUDE
    ):
        raise ValueError("invalid bounded bbox; antimeridian windows require separate requests")
    if isinstance(max_rows, bool) or not 1 <= max_rows <= MAX_FIELD_ROWS:
        raise ValueError("field row budget must be within 50000")
    manifest, series, status = _load(root, product_id, run_id)
    run = series.run if series is not None else None
    values: tuple[ForecastValue, ...] = ()
    if manifest is not None and series is not None:
        status = _run_status(manifest, start, end, now, max_run_age_seconds)
        if variable not in series.run.variables:
            status = "exact_absence"
        elif status in ("available", "stale_run"):
            values = tuple(
                row
                for row in series.values
                if row.variable == variable
                and start <= row.valid_at < end
                and west <= row.longitude <= east
                and south <= row.latitude <= north
            )
            if len(values) > max_rows:
                raise ValueError("field response exceeds row budget; narrow the request")
            if not values:
                status = "outside_domain"
            elif status == "available":
                status = _value_status(values)
    return ForecastField(
        product_id=product_id,
        run_id=run_id,
        bbox=bbox,
        start=start,
        end=end,
        variable=variable,
        zoom=zoom,
        status=status,
        run=run,
        values=values,
    )
