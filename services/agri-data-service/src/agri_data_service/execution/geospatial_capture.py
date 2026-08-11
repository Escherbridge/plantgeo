"""Bounded, checksum-governed capture of reviewed public geospatial sources."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agri_data_service.execution.contracts import (
    MAX_SOURCE_GEOJSON_BYTES,
    SHA256_PATTERN,
    canonical_json_bytes,
    reject_credential_url,
    reject_sensitive_fields,
)

GEOSPATIAL_CAPTURE_SCHEMA_VERSION: Literal[1] = 1
MAX_CAPTURE_SOURCES = 20
MAX_CAPTURE_FEATURES = 5_000
MAX_CAPTURE_JSON_NODES = 100_000
MAX_CAPTURE_JSON_DEPTH = 64
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR_MIN = 500
HTTP_SERVER_ERROR_MAX = 600
POSITION_COORDINATE_COUNT = 2
LONGITUDE_MIN = -180
LONGITUDE_MAX = 180
LATITUDE_MIN = -90
LATITUDE_MAX = 90
MIN_LINE_POSITIONS = 2
MIN_RING_POSITIONS = 4
MIN_RING_DISTINCT_POSITIONS = 3
VALID_GEOMETRY_TYPES = {
    "Point",
    "MultiPoint",
    "LineString",
    "MultiLineString",
    "Polygon",
    "MultiPolygon",
}
Sleep = Callable[[float], Awaitable[None]]


class CaptureContract(BaseModel):
    """Strict base for persisted pilot-capture contracts."""

    model_config = ConfigDict(extra="forbid")


class GeospatialReferenceCapture(CaptureContract):
    """One authoritative JSON metadata response bound to a source capture."""

    url: str = Field(min_length=1, max_length=4_000)
    allowed_host: str = Field(pattern=r"^[a-z0-9.-]+$")
    output_file: str = Field(min_length=1, max_length=255)
    expected_media_type: Literal["application/json"]
    expected_sha256: str = Field(pattern=SHA256_PATTERN)
    expected_json_values: dict[str, str | int | float | bool] = Field(
        min_length=1,
        max_length=20,
    )

    @field_validator("url")
    @classmethod
    def url_is_public_https_without_credentials(cls, value: str) -> str:
        reject_credential_url(value)
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("reference capture URLs must use public HTTPS")
        return value

    @field_validator("output_file")
    @classmethod
    def output_file_is_a_safe_leaf(cls, value: str) -> str:
        if (
            PurePosixPath(value).name != value
            or PureWindowsPath(value).name != value
            or value in {".", ".."}
            or not value.endswith(".json")
        ):
            raise ValueError("reference output_file must be a safe JSON leaf name")
        return value

    @model_validator(mode="after")
    def reference_identity_is_consistent(self) -> GeospatialReferenceCapture:
        if urlsplit(self.url).hostname != self.allowed_host:
            raise ValueError("reference URL host must exactly match allowed_host")
        return self


class GeospatialCaptureSource(CaptureContract):
    """One immutable public-source request in a reviewed capture plan."""

    key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,98}$")
    source_kind: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    url: str = Field(min_length=1, max_length=4_000)
    allowed_host: str = Field(pattern=r"^[a-z0-9.-]+$")
    output_file: str = Field(min_length=1, max_length=255)
    expected_media_type: Literal["application/geo+json", "application/json"]
    expected_sha256: str = Field(pattern=SHA256_PATTERN)
    expected_feature_count: int = Field(ge=1, le=MAX_CAPTURE_FEATURES)
    expected_geometry_types: list[str] = Field(min_length=1, max_length=6)
    feature_key_property: str = Field(min_length=1, max_length=100)
    expected_feature_keys: list[str] = Field(min_length=1, max_length=MAX_CAPTURE_FEATURES)
    provider: str = Field(min_length=1, max_length=255)
    provider_version: str = Field(min_length=1, max_length=255)
    metadata_url: str = Field(min_length=1, max_length=2_000)
    licence_name: str = Field(min_length=1, max_length=500)
    licence_url: str = Field(min_length=1, max_length=2_000)
    citation: str = Field(min_length=1, max_length=2_000)
    redistribution_status: Literal["allowed", "local_only", "blocked"]
    spatial_support_kind: Literal[
        "native_grid_cell",
        "native_polygon",
        "point_sample",
        "area_aggregate",
        "model_grid_cell",
        "unknown",
    ]
    native_resolution_m: float | None = Field(default=None, gt=0)
    native_scale_denominator: int | None = Field(default=None, gt=0)
    maximum_inference_scale: Literal[
        "structure",
        "parcel",
        "neighborhood",
        "city",
        "landscape",
        "regional",
    ]
    reference_capture: GeospatialReferenceCapture | None = None

    @field_validator("url", "metadata_url", "licence_url")
    @classmethod
    def urls_are_public_https_without_credentials(cls, value: str) -> str:
        reject_credential_url(value)
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("capture URLs must use public HTTPS")
        return value

    @field_validator("output_file")
    @classmethod
    def output_file_is_a_safe_leaf(cls, value: str) -> str:
        if (
            PurePosixPath(value).name != value
            or PureWindowsPath(value).name != value
            or value in {".", ".."}
            or not value.endswith((".geojson", ".json"))
        ):
            raise ValueError("output_file must be a safe JSON leaf name")
        return value

    @field_validator("expected_geometry_types")
    @classmethod
    def geometry_types_are_sorted_and_supported(cls, value: list[str]) -> list[str]:
        if value != sorted(set(value)) or not set(value) <= VALID_GEOMETRY_TYPES:
            raise ValueError("expected_geometry_types must be sorted, unique, and supported")
        return value

    @field_validator("expected_feature_keys")
    @classmethod
    def feature_keys_are_sorted_and_unique(cls, value: list[str]) -> list[str]:
        if value != sorted(set(value)):
            raise ValueError("expected_feature_keys must be sorted and unique")
        return value

    @model_validator(mode="after")
    def source_identity_is_consistent(self) -> GeospatialCaptureSource:
        if urlsplit(self.url).hostname != self.allowed_host:
            raise ValueError("source URL host must exactly match allowed_host")
        if len(self.expected_feature_keys) != self.expected_feature_count:
            raise ValueError("expected feature keys must cover every expected feature")
        return self


class GeospatialCapturePlan(CaptureContract):
    """Frozen multi-source plan for a bounded local pilot capture."""

    schema_version: Literal[1] = GEOSPATIAL_CAPTURE_SCHEMA_VERSION
    pilot_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,126}$")
    user_agent: str = Field(min_length=10, max_length=255)
    timeout_seconds: float = Field(ge=1, le=120)
    max_attempts: int = Field(ge=1, le=5)
    min_interval_seconds: float = Field(ge=0.1, le=60)
    sources: list[GeospatialCaptureSource] = Field(min_length=1, max_length=MAX_CAPTURE_SOURCES)

    @model_validator(mode="after")
    def source_set_is_stable(self) -> GeospatialCapturePlan:
        keys = [source.key for source in self.sources]
        files = [
            output_file
            for source in self.sources
            for output_file in (
                source.output_file,
                *([source.reference_capture.output_file] if source.reference_capture is not None else []),
            )
        ]
        if keys != sorted(set(keys)):
            raise ValueError("sources must be sorted and unique by key")
        if len(files) != len(set(files)):
            raise ValueError("source output files must be unique")
        if any(source.redistribution_status == "blocked" for source in self.sources):
            raise ValueError("blocked sources cannot enter a capture plan")
        return self


class GeospatialReferenceReceipt(CaptureContract):
    """Validated receipt for authoritative source metadata bytes."""

    output_file: str
    request_url: str
    response_url: str
    retrieved_at: datetime
    checksum_sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(ge=0)
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None

    @field_validator("retrieved_at")
    @classmethod
    def retrieved_at_is_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("reference retrieved_at must include a timezone")
        return value.astimezone(UTC)


class GeospatialCaptureReceipt(CaptureContract):
    """Validated raw-byte receipt for one source request."""

    source_key: str
    output_file: str
    request_url: str
    response_url: str
    retrieved_at: datetime
    checksum_sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(ge=0)
    feature_count: int = Field(ge=0)
    geometry_types: list[str]
    feature_keys: list[str]
    content_type: str | None = None
    etag: str | None = None
    last_modified: str | None = None
    reference_receipt: GeospatialReferenceReceipt | None = None

    @field_validator("retrieved_at")
    @classmethod
    def retrieved_at_is_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieved_at must include a timezone")
        return value.astimezone(UTC)


class GeospatialCaptureManifest(CaptureContract):
    """Atomic receipt set required before a warehouse transaction."""

    schema_version: Literal[1] = GEOSPATIAL_CAPTURE_SCHEMA_VERSION
    pilot_key: str
    plan_checksum: str = Field(pattern=SHA256_PATTERN)
    receipt_set_checksum: str = Field(pattern=SHA256_PATTERN)
    captured_at: datetime
    receipts: list[GeospatialCaptureReceipt]

    @field_validator("captured_at")
    @classmethod
    def captured_at_is_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def receipts_bind_their_checksum(self) -> GeospatialCaptureManifest:
        if self.receipt_set_checksum != _receipt_set_checksum(self.receipts):
            raise ValueError("capture receipts do not match their set checksum")
        return self


def load_geospatial_capture_plan(path: Path) -> GeospatialCapturePlan:
    """Load a frozen capture plan without opening a network connection."""
    return GeospatialCapturePlan.model_validate_json(path.read_bytes())


def geospatial_capture_plan_checksum(plan: GeospatialCapturePlan) -> str:
    """Hash the complete reviewed plan."""
    return hashlib.sha256(canonical_json_bytes(plan.model_dump(mode="json"))).hexdigest()


def geospatial_capture_root(root: Path, plan: GeospatialCapturePlan) -> Path:
    """Derive the immutable cache root from plan identity."""
    return root / plan.pilot_key / geospatial_capture_plan_checksum(plan)


def load_existing_geospatial_capture(
    target: Path,
    plan: GeospatialCapturePlan,
) -> tuple[GeospatialCaptureManifest, dict[str, bytes]]:
    """Return a validated manifest and the exact immutable byte buffers it governs."""
    return _load_existing_capture(target, plan)


async def capture_geospatial_plan(
    plan: GeospatialCapturePlan,
    root: Path,
    *,
    client: httpx.AsyncClient | None = None,
    sleep: Sleep = asyncio.sleep,
    captured_at: datetime | None = None,
) -> tuple[Path, GeospatialCaptureManifest]:
    """Capture and atomically publish all raw sources or reuse a valid cache."""
    target = geospatial_capture_root(root, plan)
    if target.exists():
        return target, _validate_existing_capture(target, plan)

    staging = target.with_name(f"{target.name}.building-{uuid.uuid4().hex}")
    staging.mkdir(parents=True, exist_ok=False)
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            headers={"User-Agent": plan.user_agent, "Accept": "application/geo+json, application/json"},
            follow_redirects=False,
            timeout=plan.timeout_seconds,
        )
    try:
        receipts: list[GeospatialCaptureReceipt] = []
        for index, source in enumerate(plan.sources):
            if index:
                await sleep(plan.min_interval_seconds)
            payload, response = await _fetch_source(plan, source, client, sleep)
            summary = validate_geospatial_payload(source, payload)
            retrieval_time = captured_at or datetime.now(UTC)
            (staging / source.output_file).write_bytes(payload)
            reference_receipt: GeospatialReferenceReceipt | None = None
            if source.reference_capture is not None:
                await sleep(plan.min_interval_seconds)
                reference_payload, reference_response = await _fetch_reference(
                    plan,
                    source.reference_capture,
                    client,
                    sleep,
                )
                validate_reference_payload(source.reference_capture, reference_payload)
                reference_retrieved_at = captured_at or datetime.now(UTC)
                (staging / source.reference_capture.output_file).write_bytes(reference_payload)
                reference_receipt = GeospatialReferenceReceipt(
                    output_file=source.reference_capture.output_file,
                    request_url=source.reference_capture.url,
                    response_url=str(reference_response.url),
                    retrieved_at=reference_retrieved_at,
                    checksum_sha256=hashlib.sha256(reference_payload).hexdigest(),
                    size_bytes=len(reference_payload),
                    content_type=reference_response.headers.get("content-type"),
                    etag=reference_response.headers.get("etag"),
                    last_modified=reference_response.headers.get("last-modified"),
                )
            receipts.append(
                GeospatialCaptureReceipt(
                    source_key=source.key,
                    output_file=source.output_file,
                    request_url=source.url,
                    response_url=str(response.url),
                    retrieved_at=retrieval_time,
                    checksum_sha256=hashlib.sha256(payload).hexdigest(),
                    size_bytes=len(payload),
                    feature_count=summary["feature_count"],
                    geometry_types=summary["geometry_types"],
                    feature_keys=summary["feature_keys"],
                    content_type=response.headers.get("content-type"),
                    etag=response.headers.get("etag"),
                    last_modified=response.headers.get("last-modified"),
                    reference_receipt=reference_receipt,
                )
            )
        retrieval_times = [
            timestamp
            for receipt in receipts
            for timestamp in (
                receipt.retrieved_at,
                *([receipt.reference_receipt.retrieved_at] if receipt.reference_receipt is not None else []),
            )
        ]
        manifest = GeospatialCaptureManifest(
            pilot_key=plan.pilot_key,
            plan_checksum=geospatial_capture_plan_checksum(plan),
            receipt_set_checksum=_receipt_set_checksum(receipts),
            captured_at=max(retrieval_times),
            receipts=receipts,
        )
        (staging / "capture-manifest.json").write_bytes(canonical_json_bytes(manifest.model_dump(mode="json")))
        target.parent.mkdir(parents=True, exist_ok=True)
        os.rename(staging, target)
        return target, manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        if owns_client:
            await client.aclose()


async def _fetch_source(
    plan: GeospatialCapturePlan,
    source: GeospatialCaptureSource,
    client: httpx.AsyncClient,
    sleep: Sleep,
) -> tuple[bytes, httpx.Response]:
    for attempt in range(plan.max_attempts):
        async with client.stream("GET", source.url) as response:
            response_host = urlsplit(str(response.url)).hostname
            if response_host != source.allowed_host:
                raise ValueError("capture response escaped its allowed provider host")
            if (
                response.status_code == HTTP_TOO_MANY_REQUESTS
                or HTTP_SERVER_ERROR_MIN <= response.status_code < HTTP_SERVER_ERROR_MAX
            ):
                if attempt + 1 == plan.max_attempts:
                    response.raise_for_status()
                retry_after = response.headers.get("retry-after")
                delay = min(float(retry_after), 60) if retry_after and retry_after.isdigit() else min(2**attempt, 60)
                await sleep(delay)
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type not in {
                source.expected_media_type,
                "application/json",
                "application/geo+json",
            }:
                raise ValueError("capture response media type is not JSON/GeoJSON")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_SOURCE_GEOJSON_BYTES:
                    raise ValueError(f"payload exceeds {MAX_SOURCE_GEOJSON_BYTES} bytes")
                chunks.append(chunk)
            return b"".join(chunks), response
    raise RuntimeError("capture retry loop ended without a response")


async def _fetch_reference(
    plan: GeospatialCapturePlan,
    reference: GeospatialReferenceCapture,
    client: httpx.AsyncClient,
    sleep: Sleep,
) -> tuple[bytes, httpx.Response]:
    for attempt in range(plan.max_attempts):
        async with client.stream("GET", reference.url) as response:
            if urlsplit(str(response.url)).hostname != reference.allowed_host:
                raise ValueError("reference response escaped its allowed provider host")
            if (
                response.status_code == HTTP_TOO_MANY_REQUESTS
                or HTTP_SERVER_ERROR_MIN <= response.status_code < HTTP_SERVER_ERROR_MAX
            ):
                if attempt + 1 == plan.max_attempts:
                    response.raise_for_status()
                retry_after = response.headers.get("retry-after")
                delay = min(float(retry_after), 60) if retry_after and retry_after.isdigit() else min(2**attempt, 60)
                await sleep(delay)
                continue
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type != reference.expected_media_type:
                raise ValueError("reference response media type changed")
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_SOURCE_GEOJSON_BYTES:
                    raise ValueError(f"reference payload exceeds {MAX_SOURCE_GEOJSON_BYTES} bytes")
                chunks.append(chunk)
            return b"".join(chunks), response
    raise RuntimeError("reference retry loop ended without a response")


def validate_reference_payload(
    reference: GeospatialReferenceCapture,
    payload: bytes,
) -> None:
    """Validate authoritative metadata bytes and exact reviewed JSON values."""
    if hashlib.sha256(payload).hexdigest() != reference.expected_sha256:
        raise ValueError("reference checksum changed; review a new provider version before ingestion")
    try:
        value = json.loads(payload, parse_constant=_reject_non_finite_constant)
    except (TypeError, ValueError) as exc:
        raise ValueError("reference payload must be finite UTF-8 JSON") from exc
    _enforce_json_bounds(value)
    reject_sensitive_fields(value)
    for path, expected in reference.expected_json_values.items():
        current: Any = value
        for component in path.split("."):
            if isinstance(current, list) and component.isdigit():
                index = int(component)
                if index >= len(current):
                    raise ValueError(f"reference assertion path is missing: {path}")
                current = current[index]
            elif isinstance(current, dict) and component in current:
                current = current[component]
            else:
                raise ValueError(f"reference assertion path is missing: {path}")
        if current != expected:
            raise ValueError(f"reference assertion changed: {path}")


def validate_geospatial_payload(source: GeospatialCaptureSource, payload: bytes) -> dict[str, Any]:
    """Validate checksum, bounded GeoJSON structure, feature identity, and WGS84 coordinates."""
    checksum = hashlib.sha256(payload).hexdigest()
    if checksum != source.expected_sha256:
        raise ValueError(f"{source.key} checksum changed; review a new provider release before ingestion")
    try:
        value = json.loads(payload, parse_constant=_reject_non_finite_constant)
    except (TypeError, ValueError) as exc:
        raise ValueError("capture payload must be finite UTF-8 JSON") from exc
    _enforce_json_bounds(value)
    reject_sensitive_fields(value)
    if not isinstance(value, dict) or value.get("type") != "FeatureCollection":
        raise ValueError("capture payload must be a GeoJSON FeatureCollection")
    features = value.get("features")
    if not isinstance(features, list) or len(features) != source.expected_feature_count:
        raise ValueError("capture feature count changed")

    geometry_types: list[str] = []
    feature_keys: list[str] = []
    for feature in features:
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ValueError("every capture member must be a GeoJSON Feature")
        geometry = feature.get("geometry")
        properties = feature.get("properties")
        if not isinstance(geometry, dict) or not isinstance(properties, dict):
            raise ValueError("every feature requires geometry and object properties")
        geometry_type = geometry.get("type")
        if geometry_type not in source.expected_geometry_types:
            raise ValueError("capture geometry type changed")
        _validate_geometry_coordinates(geometry_type, geometry.get("coordinates"))
        key = properties.get(source.feature_key_property)
        if key is None:
            raise ValueError("capture feature key is missing")
        geometry_types.append(str(geometry_type))
        feature_keys.append(str(key).strip())

    if sorted(feature_keys) != source.expected_feature_keys:
        raise ValueError("capture feature identity changed")
    return {
        "feature_count": len(features),
        "geometry_types": sorted(set(geometry_types)),
        "feature_keys": sorted(feature_keys),
    }


def _validate_existing_capture(target: Path, plan: GeospatialCapturePlan) -> GeospatialCaptureManifest:
    manifest, _ = _load_existing_capture(target, plan)
    return manifest


def _load_existing_capture(
    target: Path,
    plan: GeospatialCapturePlan,
) -> tuple[GeospatialCaptureManifest, dict[str, bytes]]:
    manifest_path = target / "capture-manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = GeospatialCaptureManifest.model_validate_json(manifest_bytes)
    if manifest_bytes != canonical_json_bytes(manifest.model_dump(mode="json")):
        raise ValueError("existing capture manifest is not canonical")
    if manifest.plan_checksum != geospatial_capture_plan_checksum(plan) or manifest.pilot_key != plan.pilot_key:
        raise ValueError("existing capture manifest does not bind the reviewed plan")
    sources = {source.key: source for source in plan.sources}
    payloads: dict[str, bytes] = {}
    if [receipt.source_key for receipt in manifest.receipts] != [source.key for source in plan.sources]:
        raise ValueError("existing capture receipt set is incomplete or reordered")
    for receipt in manifest.receipts:
        source = sources[receipt.source_key]
        if (
            receipt.output_file != source.output_file
            or receipt.request_url != source.url
            or urlsplit(receipt.response_url).hostname != source.allowed_host
            or receipt.checksum_sha256 != source.expected_sha256
        ):
            raise ValueError("existing capture receipt differs from the reviewed source")
        payload = (target / receipt.output_file).read_bytes()
        if hashlib.sha256(payload).hexdigest() != receipt.checksum_sha256 or len(payload) != receipt.size_bytes:
            raise ValueError("existing raw capture does not match its receipt")
        summary = validate_geospatial_payload(source, payload)
        if (
            receipt.feature_count != summary["feature_count"]
            or receipt.geometry_types != summary["geometry_types"]
            or receipt.feature_keys != summary["feature_keys"]
        ):
            raise ValueError("existing capture summary differs from its raw bytes")
        payloads[receipt.source_key] = payload
        reference = source.reference_capture
        reference_receipt = receipt.reference_receipt
        if reference is None:
            if reference_receipt is not None:
                raise ValueError("existing capture has an unplanned reference receipt")
            continue
        if reference_receipt is None:
            raise ValueError("existing capture is missing its planned reference receipt")
        if (
            reference_receipt.output_file != reference.output_file
            or reference_receipt.request_url != reference.url
            or urlsplit(reference_receipt.response_url).hostname != reference.allowed_host
            or reference_receipt.checksum_sha256 != reference.expected_sha256
        ):
            raise ValueError("existing reference receipt differs from the reviewed source")
        reference_payload = (target / reference_receipt.output_file).read_bytes()
        if (
            hashlib.sha256(reference_payload).hexdigest() != reference_receipt.checksum_sha256
            or len(reference_payload) != reference_receipt.size_bytes
        ):
            raise ValueError("existing reference capture does not match its receipt")
        validate_reference_payload(reference, reference_payload)
        payloads[f"{receipt.source_key}:reference"] = reference_payload
    return manifest, payloads


def _enforce_json_bounds(value: object) -> None:
    stack = [(value, 0)]
    visited = 0
    while stack:
        current, depth = stack.pop()
        visited += 1
        if visited > MAX_CAPTURE_JSON_NODES or depth > MAX_CAPTURE_JSON_DEPTH:
            raise ValueError("capture JSON exceeds the bounded custody contract")
        if isinstance(current, dict):
            stack.extend((nested, depth + 1) for nested in current.values())
        elif isinstance(current, list):
            stack.extend((nested, depth + 1) for nested in current)


def _reject_non_finite_constant(_: str) -> None:
    raise ValueError("non-finite JSON values are not permitted")


def _receipt_set_checksum(receipts: list[GeospatialCaptureReceipt]) -> str:
    return hashlib.sha256(canonical_json_bytes([receipt.model_dump(mode="json") for receipt in receipts])).hexdigest()


def _validate_geometry_coordinates(geometry_type: str, coordinates: object) -> None:
    depths = {
        "Point": 1,
        "MultiPoint": 2,
        "LineString": 2,
        "MultiLineString": 3,
        "Polygon": 3,
        "MultiPolygon": 4,
    }

    def visit(value: object, depth: int) -> None:
        if depth == 1:
            if (
                not isinstance(value, list)
                or len(value) != POSITION_COORDINATE_COUNT
                or isinstance(value[0], bool)
                or isinstance(value[1], bool)
                or not isinstance(value[0], int | float)
                or not isinstance(value[1], int | float)
                or not LONGITUDE_MIN <= value[0] <= LONGITUDE_MAX
                or not LATITUDE_MIN <= value[1] <= LATITUDE_MAX
            ):
                raise ValueError("capture coordinates must be finite WGS84 positions")
            return
        if not isinstance(value, list) or not value:
            raise ValueError("capture geometry coordinate nesting is invalid")
        for nested in value:
            visit(nested, depth - 1)

    visit(coordinates, depths[geometry_type])
    _validate_geometry_structure(geometry_type, coordinates)


def _validate_geometry_structure(geometry_type: str, coordinates: object) -> None:
    """Reject empty, open, or degenerate GeoJSON components before PostGIS."""

    def require_line(line: object) -> None:
        if not isinstance(line, list) or len(line) < MIN_LINE_POSITIONS:
            raise ValueError("capture line geometry requires at least two positions")
        if len({tuple(position) for position in line}) < MIN_LINE_POSITIONS:
            raise ValueError("capture line geometry is degenerate")

    def require_ring(ring: object) -> None:
        if not isinstance(ring, list) or len(ring) < MIN_RING_POSITIONS or ring[0] != ring[-1]:
            raise ValueError("capture polygon rings must be closed with at least four positions")
        if len({tuple(position) for position in ring[:-1]}) < MIN_RING_DISTINCT_POSITIONS:
            raise ValueError("capture polygon ring is degenerate")
        signed_area = sum(
            float(ring[index][0]) * float(ring[index + 1][1]) - float(ring[index + 1][0]) * float(ring[index][1])
            for index in range(len(ring) - 1)
        )
        if signed_area == 0:
            raise ValueError("capture polygon ring has zero signed area")

    if geometry_type == "LineString":
        require_line(coordinates)
    elif geometry_type == "MultiLineString":
        assert isinstance(coordinates, list)
        for line in coordinates:
            require_line(line)
    elif geometry_type == "Polygon":
        assert isinstance(coordinates, list)
        for ring in coordinates:
            require_ring(ring)
    elif geometry_type == "MultiPolygon":
        assert isinstance(coordinates, list)
        for polygon in coordinates:
            for ring in polygon:
                require_ring(ring)


def main() -> None:
    """Capture a reviewed plan without opening a database connection."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    plan = load_geospatial_capture_plan(args.plan)
    target, manifest = asyncio.run(capture_geospatial_plan(plan, args.output_root))
    print(
        json.dumps(
            {
                "capture_root": str(target),
                "plan_checksum": manifest.plan_checksum,
                "source_count": len(manifest.receipts),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
