"""Prepare and activate bounded local immutable forecast runs; see AGENTS.md."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from filelock import FileLock
from pydantic import Field

from agri_data_service.warehouse.schemas.weather_forecast import WEATHER_FORECAST_SCHEMA
from agri_data_service.warehouse.weather_forecast.contracts import (
    Finite,
    ForecastRun,
    ForecastSeries,
    ForecastValue,
    FrozenContract,
    SafeToken,
    UTCInstant,
)

MAX_ROWS = 50_000
MAX_SAMPLES = 256
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_PARQUET_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_WINDOW_HOURS = 240
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class ArtifactError(ValueError):
    """Refuse invalid, incomplete, corrupt or conflicting local forecast artifacts."""


class ForecastSample(FrozenContract):
    """An explicit sampled coordinate, without an area footprint."""

    sample_id: SafeToken
    longitude: Annotated[Finite, Field(ge=-180, le=180)]
    latitude: Annotated[Finite, Field(ge=-90, le=90)]


class ForecastManifest(FrozenContract):
    """Bind immutable payloads to a complete hourly inventory."""

    schema_version: Literal["weather-forecast-local/v1"] = "weather-forecast-local/v1"
    run: ForecastRun
    samples: Annotated[tuple[ForecastSample, ...], Field(min_length=1, max_length=MAX_SAMPLES)]
    start: UTCInstant
    end: UTCInstant
    row_count: Annotated[int, Field(strict=True, ge=1, le=MAX_ROWS)]
    parquet_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    parquet_bytes: Annotated[int, Field(strict=True, gt=0, le=MAX_PARQUET_BYTES)]
    source_bytes: Annotated[int, Field(strict=True, gt=0, le=MAX_SOURCE_BYTES)]
    status_counts: dict[str, int]


class PublicationReceipt(FrozenContract):
    """Bind a committed local publication instant to the immutable prepared manifest."""

    product_id: SafeToken
    run_id: SafeToken
    manifest_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    published_at: UTCInstant


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _path(root: Path, *parts: str) -> Path:
    base = root.resolve()
    path = base.joinpath(*parts)
    for parent in (path, *path.parents):
        if parent == base:
            break
        if parent.is_symlink():
            raise ArtifactError("symlink is outside the local artifact contract")
    if not path.resolve().is_relative_to(base):
        raise ArtifactError("artifact path escapes root")
    return path


def _identity(product_id: str, run_id: str) -> None:
    if not _TOKEN.fullmatch(product_id) or not _TOKEN.fullmatch(run_id):
        raise ArtifactError("invalid product or run identity")


def _read(path: Path, limit: int) -> bytes:
    if path.stat().st_size > limit:
        raise ArtifactError("artifact exceeds byte budget")
    with path.open("rb") as handle:
        value = handle.read(limit + 1)
    if len(value) > limit:
        raise ArtifactError("artifact exceeds byte budget")
    return value


def _atomic(path: Path, data: bytes, *, immutable: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if immutable and path.exists():
        if _read(path, len(data)) != data:
            raise ArtifactError("immutable artifact conflict")
        return
    descriptor, name = tempfile.mkstemp(prefix=".forecast-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _hours(start: UTCInstant, end: UTCInstant) -> tuple[UTCInstant, ...]:
    if start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0):
        raise ArtifactError("inventory requires explicit UTC times")
    span = (end - start).total_seconds()
    if start.minute or start.second or start.microsecond or end.minute or end.second or end.microsecond:
        raise ArtifactError("inventory requires UTC hour boundaries")
    if span <= 0 or span > MAX_WINDOW_HOURS * 3600 or span % 3600:
        raise ArtifactError("inventory window exceeds ten days or is invalid")
    return tuple(start + timedelta(hours=i) for i in range(int(span // 3600)))


def _inventory(series: ForecastSeries, samples: tuple[ForecastSample, ...], start: UTCInstant, end: UTCInstant) -> None:
    hours = _hours(start, end)
    coordinates = {sample.sample_id: (sample.longitude, sample.latitude) for sample in samples}
    if not samples or len(samples) > MAX_SAMPLES or len(coordinates) != len(samples):
        raise ArtifactError("sample inventory must be nonempty, unique and bounded")
    if len(set(coordinates.values())) != len(samples):
        raise ArtifactError("sample coordinates must be unique")
    count = len(samples) * len(series.run.variables) * len(hours)
    if count > MAX_ROWS or len(series.values) != count:
        raise ArtifactError("complete inventory exceeds budget or has missing values")
    expected = {
        (sample, variable, hour) for sample in coordinates for variable in series.run.variables for hour in hours
    }
    for row in series.values:
        key = (row.sample_id, row.variable, row.valid_at)
        if key not in expected or coordinates.get(row.sample_id) != (row.longitude, row.latitude):
            raise ArtifactError("duplicate or unexpected inventory value")
        expected.remove(key)
    if expected:
        raise ArtifactError("missing inventory requires explicit absent values")


def prepare_run(  # noqa: PLR0913 - immutable preparation binds independent source, inventory and storage inputs
    *,
    root: Path,
    series: ForecastSeries,
    samples: tuple[ForecastSample, ...],
    start: UTCInstant,
    end: UTCInstant,
    source_payload: bytes,
) -> ForecastManifest:
    """Prepare one immutable local run without activating it."""
    series = ForecastSeries.model_validate(series.model_dump(mode="json"))
    samples = tuple(ForecastSample.model_validate(sample.model_dump()) for sample in samples)
    _identity(series.run.product_id, series.run.run_id)
    _inventory(series, samples, start, end)
    if not 0 < len(source_payload) <= MAX_SOURCE_BYTES or _digest(source_payload) != series.run.source_payload_sha256:
        raise ArtifactError("source payload hash or byte budget mismatch")
    ordered = sorted(series.values, key=lambda row: (row.sample_id, row.variable, row.valid_at))
    table = pa.Table.from_pylist(
        [row.model_dump(mode="python") for row in ordered], schema=WEATHER_FORECAST_SCHEMA.arrow_schema
    )
    if table.nbytes > MAX_PARQUET_BYTES:
        raise ArtifactError("decoded Parquet exceeds byte budget")
    sink = io.BytesIO()
    pq.write_table(table, sink, compression="zstd", row_group_size=4096)
    payload = sink.getvalue()
    manifest = ForecastManifest(
        run=series.run,
        samples=tuple(sorted(samples, key=lambda s: s.sample_id)),
        start=start,
        end=end,
        row_count=len(ordered),
        parquet_sha256=_digest(payload),
        parquet_bytes=len(payload),
        source_bytes=len(source_payload),
        status_counts=dict(Counter(row.status for row in ordered)),
    )
    encoded = _json(manifest.model_dump(mode="json"))
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise ArtifactError("manifest exceeds byte budget")
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(str(_path(root, ".publication.lock")), timeout=10):
        manifest_path = _path(root, series.run.product_id, "runs", series.run.run_id + ".json")
        if manifest_path.exists() and _read(manifest_path, MAX_MANIFEST_BYTES) != encoded:
            raise ArtifactError("immutable run identity conflict")
        _atomic(_path(root, "blobs", series.run.source_payload_sha256 + ".source"), source_payload, immutable=True)
        _atomic(_path(root, "blobs", manifest.parquet_sha256 + ".parquet"), payload, immutable=True)
        _atomic(manifest_path, encoded, immutable=True)
    return manifest


def read_run(*, root: Path, product_id: str, run_id: str) -> tuple[ForecastManifest, ForecastSeries]:
    """Verify both immutable blobs and complete inventory before returning any values."""
    _identity(product_id, run_id)
    manifest = ForecastManifest.model_validate_json(
        _read(_path(root, product_id, "runs", run_id + ".json"), MAX_MANIFEST_BYTES)
    )
    if (manifest.run.product_id, manifest.run.run_id) != (product_id, run_id):
        raise ArtifactError("manifest identity mismatch")
    source = _read(_path(root, "blobs", manifest.run.source_payload_sha256 + ".source"), MAX_SOURCE_BYTES)
    payload = _read(_path(root, "blobs", manifest.parquet_sha256 + ".parquet"), MAX_PARQUET_BYTES)
    if len(source) != manifest.source_bytes or _digest(source) != manifest.run.source_payload_sha256:
        raise ArtifactError("source integrity failure")
    if len(payload) != manifest.parquet_bytes or _digest(payload) != manifest.parquet_sha256:
        raise ArtifactError("Parquet integrity failure")
    parquet = pq.ParquetFile(io.BytesIO(payload))
    if not parquet.schema_arrow.equals(WEATHER_FORECAST_SCHEMA.arrow_schema, check_metadata=True):
        raise ArtifactError("Parquet schema differs from canonical forecast schema")
    if parquet.metadata.num_rows != manifest.row_count or parquet.metadata.num_columns != len(
        ForecastValue.model_fields
    ):
        raise ArtifactError("Parquet inventory metadata mismatch")
    if sum(parquet.metadata.row_group(i).total_byte_size for i in range(parquet.num_row_groups)) > MAX_PARQUET_BYTES:
        raise ArtifactError("decoded Parquet exceeds byte budget")
    values = tuple(ForecastValue.model_validate(row) for row in parquet.read().to_pylist())
    series = ForecastSeries(run=manifest.run, values=values)
    _inventory(series, manifest.samples, manifest.start, manifest.end)
    if dict(Counter(row.status for row in values)) != manifest.status_counts:
        raise ArtifactError("missingness receipt mismatch")
    return manifest, series


def read_published_run(*, root: Path, product_id: str, run_id: str) -> tuple[ForecastManifest, ForecastSeries]:
    """Read a committed pinned run; prepared manifests alone are not publication authority."""
    _identity(product_id, run_id)
    receipt = PublicationReceipt.model_validate_json(
        _read(_path(root, product_id, "published", run_id + ".json"), MAX_MANIFEST_BYTES)
    )
    if (receipt.product_id, receipt.run_id) != (product_id, run_id):
        raise ArtifactError("publication identity mismatch")
    manifest_bytes = _read(_path(root, product_id, "runs", run_id + ".json"), MAX_MANIFEST_BYTES)
    if _digest(manifest_bytes) != receipt.manifest_sha256:
        raise ArtifactError("published manifest integrity failure")
    manifest, series = read_run(root=root, product_id=product_id, run_id=run_id)
    run_fields = series.run.model_dump(mode="json")
    run_fields["published_at"] = receipt.published_at.isoformat()
    published_run = ForecastRun.model_validate(run_fields)
    return manifest.model_copy(update={"run": published_run}), ForecastSeries(run=published_run, values=series.values)


def activate_run(
    *, root: Path, product_id: str, run_id: str, expected_manifest_sha256: str | None, now: UTCInstant | None = None
) -> str:
    """Compare-and-swap an explicit local active run; also supports explicit rollback."""
    _identity(product_id, run_id)
    if expected_manifest_sha256 is not None and not _HASH.fullmatch(expected_manifest_sha256):
        raise ArtifactError("invalid expected pointer")
    root.mkdir(parents=True, exist_ok=True)
    with FileLock(str(_path(root, ".publication.lock")), timeout=10):
        manifest, _series = read_run(root=root, product_id=product_id, run_id=run_id)
        manifest_hash = _digest(_read(_path(root, product_id, "runs", run_id + ".json"), MAX_MANIFEST_BYTES))
        pointer = _path(root, product_id, "active.json")
        current = json.loads(_read(pointer, 1024)) if pointer.exists() else None
        if current is not None and (
            not isinstance(current, dict)
            or set(current) != {"run_id", "manifest_sha256"}
            or not isinstance(current["run_id"], str)
            or not _TOKEN.fullmatch(current["run_id"])
            or not isinstance(current["manifest_sha256"], str)
            or not _HASH.fullmatch(current["manifest_sha256"])
        ):
            raise ArtifactError("invalid active pointer")
        target = {"run_id": run_id, "manifest_sha256": manifest_hash}
        if current == target:
            read_published_run(root=root, product_id=product_id, run_id=run_id)
            return manifest_hash
        if (current["manifest_sha256"] if current else None) != expected_manifest_sha256:
            raise ArtifactError("active pointer compare-and-swap conflict")
        receipt_path = _path(root, product_id, "published", run_id + ".json")
        if receipt_path.exists():
            read_published_run(root=root, product_id=product_id, run_id=run_id)
        else:
            publication_time = now if now is not None else datetime.now(UTC)
            if publication_time.utcoffset() != timedelta(0) or publication_time < manifest.run.admitted_at:
                raise ArtifactError("publication must follow admission at an explicit UTC instant")
            receipt = PublicationReceipt(
                product_id=product_id, run_id=run_id, manifest_sha256=manifest_hash, published_at=publication_time
            )
            _atomic(receipt_path, _json(receipt.model_dump(mode="json")), immutable=True)
        _atomic(pointer, _json(target), immutable=False)
    return manifest_hash
