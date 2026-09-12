"""Publish and verify bounded immutable forecast runs in conditional object storage; see AGENTS.md."""

from __future__ import annotations

import hashlib
import io
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Literal

import pyarrow.parquet as pq  # type: ignore[import-untyped]
from pydantic import Field, model_validator

from agri_data_service.pipeline.direct.weather_forecast.artifacts import (
    MAX_MANIFEST_BYTES,
    MAX_PARQUET_BYTES,
    MAX_ROWS,
    MAX_SOURCE_BYTES,
    ForecastManifest,
    read_run,
)
from agri_data_service.warehouse.schemas.weather_forecast import WEATHER_FORECAST_SCHEMA
from agri_data_service.warehouse.weather_forecast.contracts import (
    ForecastRun,
    ForecastSeries,
    ForecastValue,
    FrozenContract,
    SafeToken,
    Text,
    UTCInstant,
)

if TYPE_CHECKING:
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage, StoredAvailabilityObject
    from agri_data_service.pipeline.parquet.objectstore import ObjectStoreBackend

ROOT = "weather-forecast/schema=v1"
MAX_RECEIPT_BYTES = 64 * 1024
MAX_CATALOG_BYTES = 256 * 1024
MAX_CAS_ATTEMPTS = 4
MAX_RETAINED_RUNS = 16
MAX_RETAINED_BYTES = MAX_RETAINED_RUNS * (MAX_SOURCE_BYTES + MAX_PARQUET_BYTES + MAX_MANIFEST_BYTES)
SHA_LENGTH = 64
_SHA_PATTERN = r"^[0-9a-f]{64}$"
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}\Z")


class RemoteArtifactError(ValueError):
    """Refuse corrupt, excessive, conflicting or stale remote forecast state."""


class RemoteRunEntry(FrozenContract):
    """One retained run and the immutable objects needed to read it exactly."""

    run_id: SafeToken
    provider: SafeToken
    model: SafeToken
    model_version: Text | None
    manifest_sha256: Annotated[str, Field(pattern=_SHA_PATTERN)]
    receipt_sha256: Annotated[str, Field(pattern=_SHA_PATTERN)]
    source_sha256: Annotated[str, Field(pattern=_SHA_PATTERN)]
    parquet_sha256: Annotated[str, Field(pattern=_SHA_PATTERN)]
    model_init_at: UTCInstant
    provider_issued_at: UTCInstant | None
    published_at: UTCInstant
    valid_start: UTCInstant
    valid_end: UTCInstant
    row_count: Annotated[int, Field(strict=True, ge=1, le=MAX_ROWS)]
    source_bytes: Annotated[int, Field(strict=True, gt=0, le=MAX_SOURCE_BYTES)]
    parquet_bytes: Annotated[int, Field(strict=True, gt=0, le=MAX_PARQUET_BYTES)]
    manifest_bytes: Annotated[int, Field(strict=True, gt=0, le=MAX_MANIFEST_BYTES)]
    receipt_bytes: Annotated[int, Field(strict=True, gt=0, le=MAX_RECEIPT_BYTES)]

    @model_validator(mode="after")
    def valid_window(self) -> RemoteRunEntry:
        if not self.valid_start < self.valid_end:
            raise ValueError("remote run valid-time window must be positive")
        if self.provider_issued_at is not None and not self.model_init_at <= self.provider_issued_at:
            raise ValueError("provider issue cannot precede model initialization")
        if self.published_at < self.model_init_at:
            raise ValueError("publication cannot precede model initialization")
        return self

    @property
    def retained_bytes(self) -> int:
        """Count every immutable object retained for this run."""
        return self.source_bytes + self.parquet_bytes + self.manifest_bytes + self.receipt_bytes


class RemoteRunCatalog(FrozenContract):
    """The bounded mutable head that makes retained immutable runs discoverable."""

    schema_version: Literal["weather-forecast-remote-catalog/v1"] = "weather-forecast-remote-catalog/v1"
    product_id: SafeToken
    active_run_id: SafeToken | None
    runs: Annotated[tuple[RemoteRunEntry, ...], Field(max_length=MAX_RETAINED_RUNS)] = ()

    @model_validator(mode="after")
    def bounded_catalog(self) -> RemoteRunCatalog:
        identities = [entry.run_id for entry in self.runs]
        if len(set(identities)) != len(identities):
            raise ValueError("remote run catalog contains duplicate run identities")
        if self.active_run_id is not None and self.active_run_id not in identities:
            raise ValueError("active run must be retained by the catalog")
        if sum(entry.retained_bytes for entry in self.runs) > MAX_RETAINED_BYTES:
            raise ValueError("remote run catalog exceeds retained-byte ceiling")
        if tuple(sorted(self.runs, key=_entry_order)) != self.runs:
            raise ValueError("remote run catalog must be chronologically ordered")
        return self


class RemotePublicationReceipt(FrozenContract):
    """Bind a remote commit time and complete valid-time inventory to immutable objects."""

    schema_version: Literal["weather-forecast-remote-publication/v1"] = "weather-forecast-remote-publication/v1"
    product_id: SafeToken
    run_id: SafeToken
    provider: SafeToken
    model: SafeToken
    model_version: Text | None
    manifest_sha256: Annotated[str, Field(pattern=_SHA_PATTERN)]
    source_sha256: Annotated[str, Field(pattern=_SHA_PATTERN)]
    parquet_sha256: Annotated[str, Field(pattern=_SHA_PATTERN)]
    published_at: UTCInstant
    model_init_at: UTCInstant
    provider_issued_at: UTCInstant | None
    valid_start: UTCInstant
    valid_end: UTCInstant
    row_count: Annotated[int, Field(strict=True, ge=1, le=MAX_ROWS)]
    source_bytes: Annotated[int, Field(strict=True, gt=0, le=MAX_SOURCE_BYTES)]
    parquet_bytes: Annotated[int, Field(strict=True, gt=0, le=MAX_PARQUET_BYTES)]
    manifest_bytes: Annotated[int, Field(strict=True, gt=0, le=MAX_MANIFEST_BYTES)]

    @model_validator(mode="after")
    def temporal_receipt(self) -> RemotePublicationReceipt:
        if not self.model_init_at <= self.published_at or not self.valid_start < self.valid_end:
            raise ValueError("remote publication lifecycle or valid-time window is invalid")
        if (
            self.provider_issued_at is not None
            and not self.model_init_at <= self.provider_issued_at <= self.published_at
        ):
            raise ValueError("remote provider issue time is outside the run lifecycle")
        return self


class RemoteRunCommit(FrozenContract):
    """A set-once run identity mapping used to resume after pointer interruption."""

    schema_version: Literal["weather-forecast-remote-commit/v1"] = "weather-forecast-remote-commit/v1"
    product_id: SafeToken
    run_id: SafeToken
    manifest_sha256: Annotated[str, Field(pattern=_SHA_PATTERN)]
    receipt_sha256: Annotated[str, Field(pattern=_SHA_PATTERN)]


@dataclass(frozen=True, slots=True)
class RemotePublishResult:
    """The retained entry, whether the head moved, and entries retired by the move."""

    entry: RemoteRunEntry
    advanced: bool
    retired: tuple[RemoteRunEntry, ...]
    catalog_etag: str


@dataclass(frozen=True, slots=True)
class RemoteRetentionPolicy:
    """Bound the visible retained inventory; physical cleanup is a separate delayed duty."""

    max_runs: int = MAX_RETAINED_RUNS
    max_bytes: int = MAX_RETAINED_BYTES

    def __post_init__(self) -> None:
        if not 1 <= self.max_runs <= MAX_RETAINED_RUNS or not 1 <= self.max_bytes <= MAX_RETAINED_BYTES:
            raise ValueError("remote retention policy exceeds the reviewed ceilings")


def canonical_bytes(value: object) -> bytes:
    """Encode one strict, stable object-storage payload."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(payload: bytes) -> str:
    """Return the lowercase SHA-256 identity used by every immutable remote object."""
    return hashlib.sha256(payload).hexdigest()


def product_root(product_id: str) -> str:
    """Return the product-owned remote prefix after validating its safe token."""
    if not _TOKEN.fullmatch(product_id):
        raise RemoteArtifactError("invalid remote forecast product identity")
    return f"{ROOT}/product={product_id}"


def catalog_key(product_id: str) -> str:
    return f"{product_root(product_id)}/_CATALOG.json"


def source_key(sha256: str) -> str:
    return f"{ROOT}/blobs/source/sha256={_checked_sha(sha256)}.source"


def parquet_key(sha256: str) -> str:
    return f"{ROOT}/blobs/parquet/sha256={_checked_sha(sha256)}.parquet"


def manifest_key(sha256: str) -> str:
    return f"{ROOT}/manifests/sha256={_checked_sha(sha256)}.json"


def receipt_key(sha256: str) -> str:
    return f"{ROOT}/receipts/sha256={_checked_sha(sha256)}.json"


def commit_key(product_id: str, run_id: str) -> str:
    root = product_root(product_id)
    if not _TOKEN.fullmatch(run_id):
        raise RemoteArtifactError("invalid remote forecast run identity")
    return f"{root}/commits/run={run_id}.json"


def load_catalog(
    storage: AvailabilityStorage, product_id: str
) -> tuple[StoredAvailabilityObject | None, RemoteRunCatalog]:
    """Read and strictly validate the bounded mutable run head."""
    stored = storage.read(catalog_key(product_id), max_bytes=MAX_CATALOG_BYTES)
    if stored is None:
        return None, RemoteRunCatalog(product_id=product_id, active_run_id=None)
    try:
        catalog = RemoteRunCatalog.model_validate_json(stored.payload)
    except ValueError as exc:
        raise RemoteArtifactError("remote forecast catalog is malformed") from exc
    if catalog.product_id != product_id:
        raise RemoteArtifactError("remote forecast catalog product mismatch")
    return stored, catalog


def publish_prepared_run(  # noqa: PLR0913 - remote commit binds distinct local and storage identities
    storage: AvailabilityStorage,
    *,
    local_root: Path,
    product_id: str,
    run_id: str,
    published_at: UTCInstant,
    retention: RemoteRetentionPolicy | None = None,
) -> RemotePublishResult:
    """Copy a verified local run, set-once its commit, then conditionally advance the catalog."""
    manifest, _series = read_run(root=local_root, product_id=product_id, run_id=run_id)
    if published_at.utcoffset() != timedelta(0) or published_at < manifest.run.admitted_at:
        raise RemoteArtifactError("remote publication must follow admission at an explicit UTC instant")
    retention = RemoteRetentionPolicy() if retention is None else retention
    manifest_payload = _bounded_read(
        local_root / product_id / "runs" / f"{run_id}.json",
        MAX_MANIFEST_BYTES,
    )
    source_payload = _bounded_read(
        local_root / "blobs" / f"{manifest.run.source_payload_sha256}.source", MAX_SOURCE_BYTES
    )
    parquet_payload = _bounded_read(
        local_root / "blobs" / f"{manifest.parquet_sha256}.parquet",
        MAX_PARQUET_BYTES,
    )
    manifest_sha256 = digest(manifest_payload)
    if (
        digest(source_payload) != manifest.run.source_payload_sha256
        or digest(parquet_payload) != manifest.parquet_sha256
    ):
        raise RemoteArtifactError("verified local artifacts changed before remote copy")

    storage.put_immutable(
        source_key(manifest.run.source_payload_sha256),
        source_payload,
        content_type="application/octet-stream",
    )
    storage.put_immutable(
        parquet_key(manifest.parquet_sha256),
        parquet_payload,
        content_type="application/vnd.apache.parquet",
    )
    storage.put_immutable(manifest_key(manifest_sha256), manifest_payload, content_type="application/json")
    commit, receipt_payload = _load_or_create_commit(
        storage,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        published_at=published_at,
    )
    receipt = RemotePublicationReceipt.model_validate_json(receipt_payload)
    entry = RemoteRunEntry(
        run_id=run_id,
        provider=manifest.run.provider,
        model=manifest.run.model,
        model_version=manifest.run.model_version,
        manifest_sha256=manifest_sha256,
        receipt_sha256=commit.receipt_sha256,
        source_sha256=manifest.run.source_payload_sha256,
        parquet_sha256=manifest.parquet_sha256,
        model_init_at=manifest.run.model_init_at,
        provider_issued_at=manifest.run.provider_issued_at,
        published_at=receipt.published_at,
        valid_start=manifest.start,
        valid_end=manifest.end,
        row_count=manifest.row_count,
        source_bytes=manifest.source_bytes,
        parquet_bytes=manifest.parquet_bytes,
        manifest_bytes=len(manifest_payload),
        receipt_bytes=len(receipt_payload),
    )
    return _advance_catalog(storage, product_id=product_id, entry=entry, retention=retention)


def read_remote_run(
    storage: AvailabilityStorage, *, product_id: str, run_id: str
) -> tuple[ForecastManifest, ForecastSeries, RemoteRunEntry]:
    """Read one retained published run without consulting or changing the active run."""
    _stored, catalog = load_catalog(storage, product_id)
    entry = next((candidate for candidate in catalog.runs if candidate.run_id == run_id), None)
    if entry is None:
        raise RemoteArtifactError("remote forecast run is not retained")
    manifest_payload = _required(storage, manifest_key(entry.manifest_sha256), entry.manifest_bytes)
    receipt_payload = _required(storage, receipt_key(entry.receipt_sha256), entry.receipt_bytes)
    source_payload = _required(storage, source_key(entry.source_sha256), entry.source_bytes)
    parquet_payload = _required(storage, parquet_key(entry.parquet_sha256), entry.parquet_bytes)
    commit_payload = _required(storage, commit_key(product_id, run_id), MAX_RECEIPT_BYTES)
    if (
        len(manifest_payload) != entry.manifest_bytes
        or len(receipt_payload) != entry.receipt_bytes
        or len(source_payload) != entry.source_bytes
        or len(parquet_payload) != entry.parquet_bytes
        or digest(manifest_payload) != entry.manifest_sha256
        or digest(receipt_payload) != entry.receipt_sha256
        or digest(source_payload) != entry.source_sha256
        or digest(parquet_payload) != entry.parquet_sha256
    ):
        raise RemoteArtifactError("remote forecast object integrity failure")
    manifest = ForecastManifest.model_validate_json(manifest_payload)
    receipt = RemotePublicationReceipt.model_validate_json(receipt_payload)
    commit = RemoteRunCommit.model_validate_json(commit_payload)
    if (
        commit.product_id != product_id
        or commit.run_id != run_id
        or commit.manifest_sha256 != entry.manifest_sha256
        or commit.receipt_sha256 != entry.receipt_sha256
    ):
        raise RemoteArtifactError("remote run commit differs from the retained catalog entry")
    _verify_entry(entry, manifest, receipt, product_id=product_id)
    parquet = pq.ParquetFile(io.BytesIO(parquet_payload))
    if not parquet.schema_arrow.equals(WEATHER_FORECAST_SCHEMA.arrow_schema, check_metadata=True):
        raise RemoteArtifactError("remote Parquet schema differs from the canonical forecast schema")
    if parquet.metadata.num_rows != entry.row_count:
        raise RemoteArtifactError("remote Parquet row count differs from its publication receipt")
    values = tuple(ForecastValue.model_validate(row) for row in parquet.read().to_pylist())
    run_fields = manifest.run.model_dump(mode="json")
    run_fields["published_at"] = receipt.published_at.isoformat()
    run = ForecastRun.model_validate(run_fields)
    series = ForecastSeries(run=run, values=values)
    _verify_inventory(manifest, series)
    return manifest.model_copy(update={"run": run}), series, entry


def rollback_remote_run(
    storage: AvailabilityStorage,
    *,
    product_id: str,
    run_id: str,
    expected_active_run_id: str,
) -> RemoteRunCatalog:
    """Conditionally restore a retained run; a concurrent head change refuses the rollback."""
    stored, catalog = load_catalog(storage, product_id)
    if stored is None or catalog.active_run_id != expected_active_run_id:
        raise RemoteArtifactError("rollback guard does not match the active remote run")
    if run_id not in {entry.run_id for entry in catalog.runs}:
        raise RemoteArtifactError("rollback target is not retained")
    target = catalog.model_copy(update={"active_run_id": run_id})
    payload = canonical_bytes(target.model_dump(mode="json"))
    if not storage.compare_and_swap(
        catalog_key(product_id), payload, expected_etag=stored.etag, content_type="application/json"
    ):
        raise RemoteArtifactError("remote catalog changed during rollback")
    refreshed = storage.read(catalog_key(product_id), max_bytes=MAX_CATALOG_BYTES)
    if refreshed is None:
        raise RemoteArtifactError("remote catalog disappeared after rollback")
    return RemoteRunCatalog.model_validate_json(refreshed.payload)


def delete_retired_entry(
    storage: AvailabilityStorage,
    backend: ObjectStoreBackend,
    *,
    product_id: str,
    entry: RemoteRunEntry,
    object_prefix: str = "",
) -> tuple[str, ...]:
    """Idempotently remove a retired product commit while preserving globally shared immutable blobs."""
    _stored, catalog = load_catalog(storage, product_id)
    if entry.run_id in {candidate.run_id for candidate in catalog.runs}:
        raise RemoteArtifactError("cannot prune a retained remote forecast run")
    # See AGENTS.md §Remote publication and retained-run catalogue.
    keys = [commit_key(product_id, entry.run_id)]
    prefix = f"{object_prefix.strip('/')}/" if object_prefix.strip("/") else ""
    for key in keys:
        backend.delete(prefix + key)
    return tuple(keys)


def _load_or_create_commit(
    storage: AvailabilityStorage,
    *,
    manifest: ForecastManifest,
    manifest_sha256: str,
    published_at: UTCInstant,
) -> tuple[RemoteRunCommit, bytes]:
    key = commit_key(manifest.run.product_id, manifest.run.run_id)
    for _attempt in range(MAX_CAS_ATTEMPTS):
        existing = storage.read(key, max_bytes=MAX_RECEIPT_BYTES)
        if existing is not None:
            commit = RemoteRunCommit.model_validate_json(existing.payload)
            if (commit.product_id, commit.run_id, commit.manifest_sha256) != (
                manifest.run.product_id,
                manifest.run.run_id,
                manifest_sha256,
            ):
                raise RemoteArtifactError("remote run identity is already committed to different bytes")
            payload = _required(storage, receipt_key(commit.receipt_sha256), MAX_RECEIPT_BYTES)
            if len(payload) > MAX_RECEIPT_BYTES or digest(payload) != commit.receipt_sha256:
                raise RemoteArtifactError("remote publication receipt integrity failure")
            _verify_receipt_for_manifest(
                RemotePublicationReceipt.model_validate_json(payload),
                manifest,
                manifest_sha256,
            )
            return commit, payload
        receipt = RemotePublicationReceipt(
            product_id=manifest.run.product_id,
            run_id=manifest.run.run_id,
            provider=manifest.run.provider,
            model=manifest.run.model,
            model_version=manifest.run.model_version,
            manifest_sha256=manifest_sha256,
            source_sha256=manifest.run.source_payload_sha256,
            parquet_sha256=manifest.parquet_sha256,
            published_at=published_at,
            model_init_at=manifest.run.model_init_at,
            provider_issued_at=manifest.run.provider_issued_at,
            valid_start=manifest.start,
            valid_end=manifest.end,
            row_count=manifest.row_count,
            source_bytes=manifest.source_bytes,
            parquet_bytes=manifest.parquet_bytes,
            manifest_bytes=len(canonical_bytes(manifest.model_dump(mode="json"))),
        )
        receipt_payload = canonical_bytes(receipt.model_dump(mode="json"))
        if len(receipt_payload) > MAX_RECEIPT_BYTES:
            raise RemoteArtifactError("remote publication receipt exceeds its byte ceiling")
        receipt_sha256 = digest(receipt_payload)
        storage.put_immutable(receipt_key(receipt_sha256), receipt_payload, content_type="application/json")
        commit = RemoteRunCommit(
            product_id=manifest.run.product_id,
            run_id=manifest.run.run_id,
            manifest_sha256=manifest_sha256,
            receipt_sha256=receipt_sha256,
        )
        commit_payload = canonical_bytes(commit.model_dump(mode="json"))
        if storage.compare_and_swap(key, commit_payload, expected_etag=None, content_type="application/json"):
            return commit, receipt_payload
    raise RemoteArtifactError("remote run commit remained contended after bounded retries")


def _advance_catalog(
    storage: AvailabilityStorage,
    *,
    product_id: str,
    entry: RemoteRunEntry,
    retention: RemoteRetentionPolicy,
) -> RemotePublishResult:
    for _attempt in range(MAX_CAS_ATTEMPTS):
        stored, catalog = load_catalog(storage, product_id)
        previous = next((candidate for candidate in catalog.runs if candidate.run_id == entry.run_id), None)
        if previous is not None and previous != entry:
            raise RemoteArtifactError("remote run catalog identity conflict")
        if previous == entry and catalog.active_run_id == entry.run_id:
            return RemotePublishResult(
                entry=entry,
                advanced=False,
                retired=(),
                catalog_etag=stored.etag if stored is not None else "",
            )
        active = next((candidate for candidate in catalog.runs if candidate.run_id == catalog.active_run_id), None)
        if active is not None and entry.model_init_at < active.model_init_at:
            raise RemoteArtifactError("older run cannot supersede the active remote run; use explicit rollback")
        candidates = {candidate.run_id: candidate for candidate in catalog.runs}
        candidates[entry.run_id] = entry
        ordered = tuple(sorted(candidates.values(), key=_entry_order))
        retained = list(ordered)
        while (
            len(retained) > retention.max_runs
            or sum(candidate.retained_bytes for candidate in retained) > retention.max_bytes
        ):
            retired_candidate = retained.pop(0)
            if retired_candidate.run_id == entry.run_id:
                raise RemoteArtifactError("one forecast run exceeds the configured retained-byte ceiling")
        retained_tuple = tuple(retained)
        retired = tuple(candidate for candidate in ordered if candidate not in retained_tuple)
        target = RemoteRunCatalog(product_id=product_id, active_run_id=entry.run_id, runs=retained_tuple)
        payload = canonical_bytes(target.model_dump(mode="json"))
        if len(payload) > MAX_CATALOG_BYTES:
            raise RemoteArtifactError("remote forecast catalog exceeds its byte ceiling")
        if storage.compare_and_swap(
            catalog_key(product_id),
            payload,
            expected_etag=None if stored is None else stored.etag,
            content_type="application/json",
        ):
            refreshed = storage.read(catalog_key(product_id), max_bytes=MAX_CATALOG_BYTES)
            if refreshed is None:
                raise RemoteArtifactError("remote catalog disappeared after publication")
            return RemotePublishResult(entry=entry, advanced=True, retired=retired, catalog_etag=refreshed.etag)
    raise RemoteArtifactError("remote forecast catalog remained contended after bounded retries")


def _verify_entry(
    entry: RemoteRunEntry,
    manifest: ForecastManifest,
    receipt: RemotePublicationReceipt,
    *,
    product_id: str,
) -> None:
    if (
        manifest.run.product_id != product_id
        or receipt.product_id != product_id
        or manifest.run.run_id != entry.run_id
        or manifest.run.provider != entry.provider
        or manifest.run.model != entry.model
        or manifest.run.model_version != entry.model_version
        or manifest.parquet_sha256 != entry.parquet_sha256
        or manifest.run.source_payload_sha256 != entry.source_sha256
        or manifest.start != entry.valid_start
        or manifest.end != entry.valid_end
        or manifest.row_count != entry.row_count
        or receipt.product_id != manifest.run.product_id
        or receipt.run_id != entry.run_id
        or receipt.provider != entry.provider
        or receipt.model != entry.model
        or receipt.model_version != entry.model_version
        or receipt.manifest_sha256 != entry.manifest_sha256
        or receipt.source_sha256 != entry.source_sha256
        or receipt.parquet_sha256 != entry.parquet_sha256
        or receipt.published_at != entry.published_at
        or receipt.model_init_at != entry.model_init_at
        or receipt.provider_issued_at != entry.provider_issued_at
        or receipt.valid_start != entry.valid_start
        or receipt.valid_end != entry.valid_end
        or receipt.row_count != entry.row_count
        or receipt.source_bytes != entry.source_bytes
        or receipt.parquet_bytes != entry.parquet_bytes
        or receipt.manifest_bytes != entry.manifest_bytes
    ):
        raise RemoteArtifactError("remote forecast manifest, receipt and catalog disagree")


def _verify_receipt_for_manifest(
    receipt: RemotePublicationReceipt, manifest: ForecastManifest, manifest_sha256: str
) -> None:
    if (
        receipt.product_id != manifest.run.product_id
        or receipt.run_id != manifest.run.run_id
        or receipt.provider != manifest.run.provider
        or receipt.model != manifest.run.model
        or receipt.model_version != manifest.run.model_version
        or receipt.manifest_sha256 != manifest_sha256
        or receipt.source_sha256 != manifest.run.source_payload_sha256
        or receipt.parquet_sha256 != manifest.parquet_sha256
        or receipt.model_init_at != manifest.run.model_init_at
        or receipt.provider_issued_at != manifest.run.provider_issued_at
        or receipt.valid_start != manifest.start
        or receipt.valid_end != manifest.end
        or receipt.row_count != manifest.row_count
        or receipt.source_bytes != manifest.source_bytes
        or receipt.parquet_bytes != manifest.parquet_bytes
        or receipt.manifest_bytes != len(canonical_bytes(manifest.model_dump(mode="json")))
        or receipt.published_at < manifest.run.admitted_at
    ):
        raise RemoteArtifactError("remote publication receipt differs from its prepared manifest")


def _verify_inventory(manifest: ForecastManifest, series: ForecastSeries) -> None:
    hours = int((manifest.end - manifest.start).total_seconds() // 3600)
    expected_count = len(manifest.samples) * len(manifest.run.variables) * hours
    if expected_count != manifest.row_count or len(series.values) != manifest.row_count:
        raise RemoteArtifactError("remote forecast inventory is incomplete")
    coordinates = {sample.sample_id: (sample.longitude, sample.latitude) for sample in manifest.samples}
    expected = {
        (sample_id, variable, manifest.start + timedelta(hours=hour))
        for sample_id in coordinates
        for variable in manifest.run.variables
        for hour in range(hours)
    }
    for value in series.values:
        identity = (value.sample_id, value.variable, value.valid_at)
        if identity not in expected or coordinates.get(value.sample_id) != (value.longitude, value.latitude):
            raise RemoteArtifactError("remote forecast inventory contains an unexpected value")
        expected.remove(identity)
    if expected or dict(Counter(value.status for value in series.values)) != manifest.status_counts:
        raise RemoteArtifactError("remote forecast inventory or missingness receipt is incomplete")


def _required(storage: AvailabilityStorage, key: str, max_bytes: int) -> bytes:
    stored = storage.read(key, max_bytes=max_bytes)
    if stored is None:
        raise RemoteArtifactError(f"required remote forecast object is missing: {key}")
    return stored.payload


def _bounded_read(path: Path, limit: int) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise RemoteArtifactError("verified local artifact disappeared before remote copy") from exc
    if size <= 0 or size > limit:
        raise RemoteArtifactError("local artifact exceeds its remote copy byte ceiling")
    payload = path.read_bytes()
    if len(payload) != size:
        raise RemoteArtifactError("local artifact changed during remote copy")
    return payload


def _checked_sha(value: str) -> str:
    if len(value) != SHA_LENGTH or any(character not in "0123456789abcdef" for character in value):
        raise RemoteArtifactError("invalid remote forecast content identity")
    return value


def _entry_order(entry: RemoteRunEntry) -> tuple[UTCInstant, str]:
    return entry.model_init_at, entry.run_id
