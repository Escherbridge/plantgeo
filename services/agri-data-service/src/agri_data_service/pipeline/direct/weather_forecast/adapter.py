"""Build restartable local fixture artifacts; see AGENTS.md for admission limits."""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from filelock import FileLock

from agri_data_service.foundation.canonical import canonical_json, sha256_digest
from agri_data_service.pipeline.parquet.availability_index import StoredAvailabilityObject
from agri_data_service.warehouse.parquet.weather_forecast_arrow import arrow as pa
from agri_data_service.warehouse.parquet.weather_forecast_arrow import parquet as pq
from agri_data_service.warehouse.schemas.weather_forecast import (
    ARROW_SCHEMA,
    MAX_BYTES,
    MAX_HOURS,
    MAX_ROWS,
    SAMPLES,
    VARIABLES,
    VERSION,
    ForecastRow,
    ForecastRun,
    fixture_run,
    utc_instant,
    validate_run_id,
    variable_catalogue,
    wind_from_components,
)

MISSING_CLOUD_LEAD_HOUR = 6


class LocalForecastStorage:
    """Implement the existing AvailabilityStorage port inside an explicit local root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root) or path == self.root:
            raise ValueError("Artifact path escapes the local fixture root")
        return path

    def read(self, key: str, *, max_bytes: int) -> StoredAvailabilityObject | None:
        """Read at most the declared byte ceiling, preserving content identity."""
        path = self._path(key)
        try:
            with path.open("rb") as stream:
                payload = stream.read(max_bytes + 1)
        except FileNotFoundError:
            return None
        if len(payload) > max_bytes:
            raise ValueError("Artifact exceeds the byte ceiling")
        return StoredAvailabilityObject(payload, sha256_digest(payload))

    def _replace(self, key: str, payload: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def put_immutable(self, key: str, payload: bytes, *, content_type: str) -> None:
        """Allow identical replay while refusing immutable-key replacement."""
        if not content_type or len(payload) > MAX_BYTES:
            raise ValueError("Invalid local artifact content contract")
        with FileLock(str(self.root / ".publication.lock")):
            previous = self.read(key, max_bytes=MAX_BYTES)
            if previous is not None:
                if previous.payload != payload:
                    raise ValueError("Immutable artifact conflict")
                return
            self._replace(key, payload)

    def compare_and_swap(self, key: str, payload: bytes, *, expected_etag: str | None, content_type: str) -> bool:
        """Advance a pointer only while holding its expected content identity."""
        if not content_type or len(payload) > MAX_BYTES:
            raise ValueError("Invalid local pointer content contract")
        with FileLock(str(self.root / ".publication.lock")):
            previous = self.read(key, max_bytes=MAX_BYTES)
            if (None if previous is None else previous.etag) != expected_etag:
                return False
            self._replace(key, payload)
            return True


def fixture_rows(run: ForecastRun) -> list[ForecastRow]:
    """Generate finite synthetic values and one deliberate source-declared cloud gap."""
    start = utc_instant(run["initialization_time"])
    rows: list[ForecastRow] = []
    for point_index, (latitude, longitude) in enumerate(SAMPLES):
        for hour in range(MAX_HOURS):
            phase = hour * math.tau / 24
            u, v = 3 * math.sin(phase), -3 * math.cos(phase)
            speed, direction = wind_from_components(u, v)
            values = {
                "temperature_2m": 18 + 6 * math.sin(phase) + point_index,
                "relative_humidity_2m": 55 - 20 * math.sin(phase),
                "cloud_cover": None
                if point_index == 0 and hour == MISSING_CLOUD_LEAD_HOUR
                else 40 + 25 * math.cos(phase),
                "precipitation": 0.4 if hour % 12 == 0 else 0.0,
                "wind_u_10m": u,
                "wind_v_10m": v,
                "wind_speed_10m": speed,
                "wind_direction_10m": direction,
            }
            valid = start + timedelta(hours=hour)
            for variable, value in values.items():
                rows.append(
                    {
                        "run_id": run["run_id"],
                        "latitude": latitude,
                        "longitude": longitude,
                        "valid_time": valid,
                        "lead_hours": hour,
                        "variable": variable,
                        "interval_start": valid if variable == "precipitation" else None,
                        "interval_end": valid + timedelta(hours=1) if variable == "precipitation" else None,
                        "value": value,
                        "missing_reason": "synthetic-source-missing" if value is None else None,
                    }
                )
    return rows


def _immutable_json(storage: LocalForecastStorage, key: str, value: object) -> str:
    payload = canonical_json(value).encode()
    storage.put_immutable(key, payload, content_type="application/json")
    return sha256_digest(payload)


def publish_fixture(
    root: Path, *, initialization: str = "2026-09-12T00:00:00Z", interrupt_after_parts: bool = False
) -> dict[str, object]:
    """Publish a complete fixture run, with deterministic replay after interrupted staging."""
    storage = LocalForecastStorage(root)
    storage.root.mkdir(parents=True, exist_ok=True)
    run = fixture_run(utc_instant(initialization))
    run_id = validate_run_id(run["run_id"])
    base = f"runs/{run_id}"
    rows = fixture_rows(run)
    source = canonical_json(
        [
            {key: value.isoformat() if isinstance(value, datetime) else value for key, value in row.items()}
            for row in rows
        ]
    ).encode()
    storage.put_immutable(f"{base}/source.json", source, content_type="application/json")
    table = pa.Table.from_pylist(rows, schema=ARROW_SCHEMA).sort_by(
        [
            ("latitude", "ascending"),
            ("longitude", "ascending"),
            ("valid_time", "ascending"),
            ("variable", "ascending"),
        ]
    )
    buffer = pa.BufferOutputStream()
    pq.write_table(table, buffer, compression="zstd", row_group_size=MAX_ROWS)
    body = buffer.getvalue().to_pybytes()
    storage.put_immutable(f"{base}/values.parquet", body, content_type="application/vnd.apache.parquet")
    if interrupt_after_parts:
        raise InterruptedError("Fixture stopped after immutable parts; replay the same run to complete")
    manifest = {
        "schema_version": VERSION,
        "mode": "fixture",
        "run": run,
        "variables": variable_catalogue(),
        "samples": [list(point) for point in SAMPLES],
        "expected_rows": MAX_ROWS,
        "required_variables": sorted(VARIABLES),
        "source": {"key": f"{base}/source.json", "sha256": sha256_digest(source), "bytes": len(source)},
        "artifact": {
            "key": f"{base}/values.parquet",
            "sha256": sha256_digest(body),
            "bytes": len(body),
            "rows": len(rows),
        },
    }
    digest = _immutable_json(storage, f"{base}/manifest.json", manifest)
    _immutable_json(
        storage,
        f"{base}/complete.json",
        {
            "schema_version": VERSION,
            "run_id": run_id,
            "manifest_sha256": digest,
            "expected_rows": MAX_ROWS,
            "complete": True,
        },
    )
    pointer = canonical_json({"run_id": run_id, "manifest_sha256": digest}).encode()
    previous = storage.read("current.json", max_bytes=MAX_BYTES)
    if previous is not None:
        prior_run = json.loads(previous.payload)["run_id"]
        if prior_run >= run_id:
            return manifest
    if not storage.compare_and_swap(
        "current.json",
        pointer,
        expected_etag=None if previous is None else previous.etag,
        content_type="application/json",
    ):
        raise ValueError("Concurrent pointer advance; immutable run remains safely published")
    return manifest


def main() -> None:
    """Write only the explicitly selected local fixture directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--initialization", default="2026-09-12T00:00:00Z")
    args = parser.parse_args()
    print(canonical_json(publish_fixture(args.root, initialization=args.initialization)))


if __name__ == "__main__":
    main()
