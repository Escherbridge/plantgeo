"""Bounded pinned-run fixture reads; see pipeline/direct/weather_forecast/AGENTS.md."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.foundation.parquet.zoom import serving_zoom_tier
from agri_data_service.pipeline.direct.weather_forecast.adapter import LocalForecastStorage, fixture_rows
from agri_data_service.warehouse.parquet.weather_forecast_arrow import arrow as pa
from agri_data_service.warehouse.parquet.weather_forecast_arrow import parquet as pq
from agri_data_service.warehouse.schemas.weather_forecast import (
    ARROW_SCHEMA,
    HOURS_PER_DAY,
    MAX_BYTES,
    MAX_POINTS,
    MAX_ROWS,
    MAX_WINDOW_HOURS,
    SAMPLES,
    SECONDS_PER_HOUR,
    VARIABLES,
    VERSION,
    ForecastRow,
    ForecastRun,
    fixture_run,
    iso,
    utc_instant,
    validate_run_id,
    variable_catalogue,
    wind_from_components,
)

if TYPE_CHECKING:
    from pathlib import Path

LONGITUDE_LIMIT = 180
LATITUDE_LIMIT = 90


@dataclass(frozen=True, slots=True)
class LocationRequest:
    """One exact sample and half-open UTC interval pinned to a fixture run."""

    run_id: str
    lat: float
    lon: float
    start: str
    end: str

    def validate(self) -> tuple[datetime, datetime]:
        """Validate limits before reading any artifact."""
        validate_run_id(self.run_id)
        start, end = utc_instant(self.start), utc_instant(self.end)
        if not 0 < (end - start).total_seconds() <= MAX_WINDOW_HOURS * SECONDS_PER_HOUR:
            raise ValueError("Window must contain 1 through 48 hours")
        if not math.isfinite(self.lat) or not math.isfinite(self.lon):
            raise ValueError("Coordinates must be finite")
        return start, end

    def wire(self) -> dict[str, object]:
        """Preserve the actual requested place, run and interval."""
        return {"run_id": self.run_id, "lat": self.lat, "lon": self.lon, "start": self.start, "end": self.end}


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("Expected a JSON object")
    return cast("dict[str, object]", value)


def _bytes(storage: LocalForecastStorage, key: str) -> bytes:
    stored = storage.read(key, max_bytes=MAX_BYTES)
    if stored is None:
        raise ValueError("Required publication artifact is missing")
    return stored.payload


def _load(root: Path, run_id: str) -> tuple[ForecastRun, list[ForecastRow]] | None:
    storage = LocalForecastStorage(root)
    base = f"runs/{validate_run_id(run_id)}"
    terminal = storage.read(f"{base}/complete.json", max_bytes=MAX_BYTES)
    if terminal is None:
        return None
    marker = _object(json.loads(terminal.payload))
    manifest_bytes = _bytes(storage, f"{base}/manifest.json")
    expected_marker = {
        "schema_version": VERSION,
        "run_id": run_id,
        "complete": True,
        "expected_rows": MAX_ROWS,
        "manifest_sha256": sha256_digest(manifest_bytes),
    }
    if marker != expected_marker:
        raise ValueError("Publication completeness receipt disagrees with the manifest")
    manifest = _object(json.loads(manifest_bytes))
    run = fixture_run(datetime.strptime(run_id[8:-3], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC))
    if (
        manifest.get("run") != run
        or manifest.get("schema_version") != VERSION
        or manifest.get("mode") != "fixture"
        or manifest.get("variables") != variable_catalogue()
        or manifest.get("samples") != [list(point) for point in SAMPLES]
        or manifest.get("required_variables") != sorted(VARIABLES)
        or manifest.get("expected_rows") != MAX_ROWS
    ):
        raise ValueError("Manifest disagrees with the frozen fixture product")
    source = _bytes(storage, f"{base}/source.json")
    body = _bytes(storage, f"{base}/values.parquet")
    for label, payload, suffix in (("source", source, "source.json"), ("artifact", body, "values.parquet")):
        receipt = _object(manifest.get(label))
        expected = {"key": f"{base}/{suffix}", "sha256": sha256_digest(payload), "bytes": len(payload)}
        if label == "artifact":
            expected["rows"] = MAX_ROWS
        if receipt != expected:
            raise ValueError("Artifact checksum or immutable key disagrees with its manifest")
    expected_rows = fixture_rows(run)
    expected_source = canonical_json(
        [
            {key: value.isoformat() if isinstance(value, datetime) else value for key, value in row.items()}
            for row in expected_rows
        ]
    ).encode()
    if source != expected_source:
        raise ValueError("Source fails deterministic fixture reconciliation")
    parquet = pq.ParquetFile(pa.BufferReader(body))
    if parquet.metadata.num_rows != MAX_ROWS or parquet.schema_arrow != ARROW_SCHEMA:
        raise ValueError("Parquet row count or schema disagrees with the fixture")
    decoded_bytes = sum(parquet.metadata.row_group(index).total_byte_size for index in range(parquet.num_row_groups))
    if decoded_bytes > MAX_BYTES:
        raise ValueError("Parquet decoded size exceeds the read ceiling")
    actual_rows = parquet.read().to_pylist()
    expected_rows.sort(key=lambda row: (row["latitude"], row["longitude"], row["valid_time"], row["variable"]))
    if actual_rows != expected_rows:
        raise ValueError("Parquet values do not conserve the source fixture")
    return run, cast("list[ForecastRow]", actual_rows)


def _envelope(request: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": VERSION,
        "mode": "fixture",
        "status": "not-generated",
        "reason": None,
        "run": None,
        "request": request,
        "support": None,
        "variables": variable_catalogue(),
        "hourly": [],
        "daily": [],
        "uncertainty": {
            "kind": "deterministic",
            "message": "Synthetic deterministic fixture; no probability or confidence intervals.",
        },
        "limits": {
            "max_hours": MAX_WINDOW_HOURS,
            "max_points": MAX_POINTS,
            "max_rows": MAX_ROWS,
            "max_bytes": MAX_BYTES,
        },
    }


def _hourly(rows: list[ForecastRow]) -> list[dict[str, object]]:
    grouped: dict[datetime, list[ForecastRow]] = {}
    for row in rows:
        grouped.setdefault(row["valid_time"], []).append(row)
    return [
        {
            "valid_time": iso(valid),
            "interval_start": iso(valid),
            "interval_end": iso(valid + timedelta(hours=1)),
            "lead_hours": entries[0]["lead_hours"],
            "values": {row["variable"]: row["value"] for row in entries},
            "missingness": {
                row["variable"]: row["missing_reason"] for row in entries if row["missing_reason"] is not None
            },
        }
        for valid, entries in sorted(grouped.items())
    ]


def _daily(rows: list[ForecastRow]) -> list[dict[str, object]]:
    days: dict[str, list[ForecastRow]] = {}
    for row in rows:
        days.setdefault(iso(row["valid_time"])[:10], []).append(row)
    result = []
    for day, entries in sorted(days.items()):
        values = {
            name: [row["value"] for row in entries if row["variable"] == name and row["value"] is not None]
            for name in VARIABLES
        }
        hours = len({row["valid_time"] for row in entries})
        complete = hours == HOURS_PER_DAY and all(len(series) == hours for series in values.values())
        summaries = {
            name: (sum(series) / len(series) if len(series) == HOURS_PER_DAY else None)
            for name, series in values.items()
        }
        u, v = summaries["wind_u_10m"], summaries["wind_v_10m"]
        wind = (None, None) if u is None or v is None else wind_from_components(u, v)
        temperatures = values["temperature_2m"]
        result.append(
            {
                "day": day,
                "hours": hours,
                "expected_hours": HOURS_PER_DAY,
                "complete": complete,
                "temperature_min": min(temperatures) if len(temperatures) == HOURS_PER_DAY else None,
                "temperature_max": max(temperatures) if len(temperatures) == HOURS_PER_DAY else None,
                "precipitation_sum": sum(values["precipitation"])
                if len(values["precipitation"]) == HOURS_PER_DAY
                else None,
                "relative_humidity_mean": summaries["relative_humidity_2m"],
                "cloud_cover_mean": summaries["cloud_cover"],
                "wind_speed": wind[0],
                "wind_direction": wind[1],
                "missingness": {
                    **{
                        name: "partial-utc-day" if hours < HOURS_PER_DAY else "source-variable-missing"
                        for name, series in values.items()
                        if len(series) < HOURS_PER_DAY
                    },
                    **(
                        {"wind_direction": "calm-vector-direction-undefined"}
                        if wind[0] is not None and wind[1] is None
                        else {}
                    ),
                },
            }
        )
    return result


def read_location(
    root: Path, request: LocationRequest, *, requested_zoom: int, now: datetime | None = None
) -> dict[str, object]:
    """Read only the requested exact sample and run without a prior-time fallback."""
    response = _envelope(request.wire())
    try:
        response["served_zoom"] = serving_zoom_tier(requested_zoom)
        start, end = request.validate()
        if (request.lat, request.lon) not in SAMPLES:
            response.update(status="outside-domain", reason="Only the four exact fixture samples are supported")
            return response
        loaded = _load(root, request.run_id)
        if loaded is None:
            response["reason"] = "Requested immutable run has no completeness marker"
            return response
        run, rows = loaded
        response["run"] = run
        if start < utc_instant(run["valid_start"]) or end > utc_instant(run["valid_end"]):
            response["reason"] = "Requested interval extends outside this run's generated horizon"
            return response
        if (now or datetime.now(UTC)) >= utc_instant(run["valid_end"]):
            response.update(status="stale-run", reason="Pinned run has passed its complete forecast horizon")
            return response
        selected = [
            row
            for row in rows
            if row["latitude"] == request.lat and row["longitude"] == request.lon and start <= row["valid_time"] < end
        ]
        response.update(
            status="partial" if any(row["missing_reason"] is not None for row in selected) else "ready",
            support={"kind": "sampled_point", "latitude": request.lat, "longitude": request.lon, "distance_km": 0},
            hourly=_hourly(selected),
            daily=_daily(selected),
        )
        if len(canonical_json(response).encode()) > MAX_BYTES:
            raise ValueError("Response exceeds the byte ceiling")
    except (ValueError, OSError, pa.ArrowException) as error:
        response.update(status="refused", reason=str(error), hourly=[], daily=[])
    return response


@dataclass(frozen=True, slots=True)
class FieldRequest:
    """A single variable/time point field inside a bounded viewport."""

    run_id: str
    valid_time: str
    variable: str
    bbox: tuple[float, float, float, float]


def read_field(
    root: Path, request: FieldRequest, *, requested_zoom: int, now: datetime | None = None
) -> dict[str, object]:
    """Return sampled points only, explicitly refusing unsupported interpolation."""
    response = _envelope(
        {
            "run_id": request.run_id,
            "valid_time": request.valid_time,
            "variable": request.variable,
            "bbox": list(request.bbox),
        }
    )
    response["points"] = []
    try:
        response["served_zoom"] = serving_zoom_tier(requested_zoom)
        valid = utc_instant(request.valid_time)
        west, south, east, north = request.bbox
        if (
            request.variable not in VARIABLES
            or not all(math.isfinite(value) for value in request.bbox)
            or not -LONGITUDE_LIMIT <= west < east <= LONGITUDE_LIMIT
            or not -LATITUDE_LIMIT <= south < north <= LATITUDE_LIMIT
        ):
            raise ValueError("Invalid field variable or bbox")
        samples = [(lat, lon) for lat, lon in SAMPLES if west <= lon <= east and south <= lat <= north]
        if not samples:
            response.update(status="outside-domain", reason="Viewport contains no exact fixture samples")
            return response
        loaded = _load(root, request.run_id)
        if loaded is None:
            response["reason"] = "Requested immutable run has no completeness marker"
            return response
        run, rows = loaded
        response["run"] = run
        if not utc_instant(run["valid_start"]) <= valid < utc_instant(run["valid_end"]):
            response["reason"] = "Requested valid hour was not generated by this run"
            return response
        if (now or datetime.now(UTC)) >= utc_instant(run["valid_end"]):
            response.update(status="stale-run", reason="Pinned run has passed its complete forecast horizon")
            return response
        selected = [
            row
            for row in rows
            if (row["latitude"], row["longitude"]) in samples
            and row["variable"] == request.variable
            and row["valid_time"] == valid
        ]
        response.update(
            status="partial" if any(row["missing_reason"] is not None for row in selected) else "ready",
            support={"kind": "sampled_point", "coarsening": "none; no native or interpolated grid"},
            points=[
                {
                    **row,
                    "valid_time": iso(row["valid_time"]),
                    "interval_start": None if row["interval_start"] is None else iso(row["interval_start"]),
                    "interval_end": None if row["interval_end"] is None else iso(row["interval_end"]),
                }
                for row in selected
            ],
        )
    except (ValueError, OSError, pa.ArrowException) as error:
        response.update(status="refused", reason=str(error), points=[])
    return response
