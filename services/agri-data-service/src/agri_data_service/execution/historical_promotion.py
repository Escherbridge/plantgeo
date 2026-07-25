"""Bounded, credential-free typed contracts for historical warehouse promotion."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal, cast

from pydantic import Field, TypeAdapter, field_validator, model_validator

from agri_data_service.execution.contracts import (
    SHA256_PATTERN,
    ContractModel,
    canonical_json_bytes,
    reject_credential_url,
    reject_sensitive_fields,
)

HISTORICAL_PROMOTION_SCHEMA_VERSION: Literal[1] = 1
HISTORICAL_PROMOTION_FORMAT: Literal["plantgeo-historical-promotion-v1"] = "plantgeo-historical-promotion-v1"
MAX_HISTORICAL_PROMOTION_CHUNK_BYTES = 8_000_000
MAX_HISTORICAL_PROMOTION_CHUNKS = 20_000
MAX_HISTORICAL_PROMOTION_RECORDS = 50_000_000
MAX_HISTORICAL_ARTIFACT_BYTES = 8_000_000
MAX_HISTORICAL_METADATA_DEPTH = 16
MAX_HISTORICAL_METADATA_ITEMS = 10_000
SOURCE_KEY_PATTERN = r"^[a-z0-9][a-z0-9-]{1,98}$"
RELEASE_SET_KEY_PATTERN = r"^[a-z0-9][a-z0-9-]{1,253}$"
CELL_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,179}$"
FEATURE_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,254}$"
TRANSFORM_VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$"
WGS84_COORDINATE_DIMENSIONS = 2
MINIMUM_LINEAR_RING_POSITIONS = 4
WGS84_MIN_LONGITUDE = -180
WGS84_MAX_LONGITUDE = 180
WGS84_MIN_LATITUDE = -90
WGS84_MAX_LATITUDE = 90
NASA_POWER_SOURCE_KEY = "nasa-power-daily"
ERA5_LAND_SOURCE_KEY = "era5-land"
USDM_SOURCE_KEY = "usdm-weekly"


def _require_aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _validate_json_value(value: Any, *, depth: int = 0, item_count: list[int] | None = None) -> None:
    """Reject non-JSON, credentials, non-finite, and obvious local-ID metadata."""
    if depth > MAX_HISTORICAL_METADATA_DEPTH:
        raise ValueError("metadata exceeds the allowed nesting depth")
    counter = item_count if item_count is not None else [0]
    counter[0] += 1
    if counter[0] > MAX_HISTORICAL_METADATA_ITEMS:
        raise ValueError("metadata exceeds the allowed item count")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("metadata cannot contain non-finite numbers")
        return
    if isinstance(value, list):
        for nested in value:
            _validate_json_value(nested, depth=depth + 1, item_count=counter)
        return
    if isinstance(value, dict):
        for key, nested in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError("metadata keys must be non-empty strings")
            normalized = key.lower()
            if normalized in {
                "id",
                "artifact_id",
                "cell_id",
                "data_source_id",
                "parent_cell_id",
                "release_set_id",
                "source_release_id",
            }:
                raise ValueError("historical promotion metadata cannot contain local database IDs")
            _validate_json_value(nested, depth=depth + 1, item_count=counter)
        return
    raise ValueError("metadata must contain only JSON-compatible values")


def _validate_metadata(value: dict[str, Any]) -> dict[str, Any]:
    _validate_json_value(value)
    reject_sensitive_fields(value)
    return value


def _canonical_geometry(value: str, expected_type: str) -> str:
    """Require bounded canonical WGS84 GeoJSON geometry text without PostGIS."""
    encoded = value.encode("utf-8")
    if len(encoded) > MAX_HISTORICAL_PROMOTION_CHUNK_BYTES:
        raise ValueError("geometry exceeds the maximum promotion chunk size")
    try:
        geometry = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (TypeError, ValueError) as exc:
        raise ValueError("geometry must be valid JSON") from exc
    if not isinstance(geometry, dict) or geometry.get("type") != expected_type:
        raise ValueError(f"geometry must be a GeoJSON {expected_type}")
    _validate_geometry_coordinates(expected_type, geometry.get("coordinates"))
    canonical = canonical_json_bytes(geometry).decode("utf-8")
    if value != canonical:
        raise ValueError("geometry must use canonical JSON serialization")
    return value


def _validate_geometry_coordinates(expected_type: str, value: Any) -> None:
    if expected_type == "Point":
        _validate_position(value)
    elif expected_type == "Polygon":
        _validate_polygon(value)
    elif expected_type == "MultiPolygon":
        if not isinstance(value, list) or not value:
            raise ValueError("MultiPolygon coordinates must contain at least one polygon")
        for polygon in value:
            _validate_polygon(polygon)
    else:  # pragma: no cover - callers use the three explicit storage geometry types
        raise ValueError("unsupported historical promotion geometry type")


def _validate_polygon(value: Any) -> None:
    if not isinstance(value, list) or not value:
        raise ValueError("Polygon coordinates must contain at least one linear ring")
    for ring in value:
        if not isinstance(ring, list) or len(ring) < MINIMUM_LINEAR_RING_POSITIONS:
            raise ValueError("Polygon linear rings must contain at least four positions")
        for position in ring:
            _validate_position(position)
        if ring[0] != ring[-1]:
            raise ValueError("Polygon linear rings must be closed")


def _validate_position(value: Any) -> None:
    if (
        not isinstance(value, list)
        or len(value) != WGS84_COORDINATE_DIMENSIONS
        or any(not isinstance(item, (int, float)) or isinstance(item, bool) for item in value)
    ):
        raise ValueError("geometry positions must be two-dimensional numeric WGS84 coordinates")
    longitude, latitude = value
    if not math.isfinite(longitude) or not math.isfinite(latitude):
        raise ValueError("geometry coordinates must be finite")
    if not (
        WGS84_MIN_LONGITUDE <= longitude <= WGS84_MAX_LONGITUDE and WGS84_MIN_LATITUDE <= latitude <= WGS84_MAX_LATITUDE
    ):
        raise ValueError("geometry coordinates must be WGS84 longitude/latitude")


class HistoricalSourceReleaseIdentity(ContractModel):
    """Portable source-release identity; never a database surrogate key."""

    source_key: str = Field(pattern=SOURCE_KEY_PATTERN)
    source_version: str = Field(min_length=1, max_length=255)
    payload_checksum: str = Field(pattern=SHA256_PATTERN)
    transform_version: str = Field(pattern=TRANSFORM_VERSION_PATTERN)


class HistoricalReleaseSetRoot(ContractModel):
    """Validated release-set root pinned exclusively by natural source identities."""

    logical_key: str = Field(pattern=RELEASE_SET_KEY_PATTERN)
    as_of_time: datetime
    release_set_manifest_checksum: str = Field(pattern=SHA256_PATTERN)
    state: Literal["validated"] = "validated"
    members: list[HistoricalSourceReleaseIdentity] = Field(min_length=1, max_length=MAX_HISTORICAL_PROMOTION_RECORDS)
    description: str | None = Field(default=None, max_length=10_000)

    @field_validator("as_of_time")
    @classmethod
    def require_aware_as_of_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "as_of_time")

    @field_validator("members")
    @classmethod
    def require_sorted_unique_members(
        cls, value: list[HistoricalSourceReleaseIdentity]
    ) -> list[HistoricalSourceReleaseIdentity]:
        keys = [historical_source_release_sort_key(member) for member in value]
        if keys != sorted(keys) or len(keys) != len(set(keys)):
            raise ValueError("release-set members must be sorted and unique by natural identity")
        if {member.source_key for member in value} - {NASA_POWER_SOURCE_KEY, ERA5_LAND_SOURCE_KEY, USDM_SOURCE_KEY}:
            raise ValueError("historical promotion v1 supports only NASA POWER, ERA5-Land, and USDM source keys")
        return value


class HistoricalDataSourceRecord(ContractModel):
    """Governed source definition with no target or local database IDs."""

    record_type: Literal["data_source"] = "data_source"
    source_key: str = Field(pattern=SOURCE_KEY_PATTERN)
    name: str = Field(min_length=1, max_length=255)
    owner: str = Field(min_length=1, max_length=255)
    purpose: str = Field(min_length=1, max_length=10_000)
    license_name: str = Field(min_length=1, max_length=255)
    citation: str = Field(min_length=1, max_length=10_000)
    base_url: str | None = Field(default=None, max_length=1_000)
    license_url: str | None = Field(default=None, max_length=1_000)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    reviewed_at: datetime
    reviewed_by: str = Field(min_length=1, max_length=255)
    configuration: dict[str, Any] = Field(default_factory=dict)

    @field_validator("base_url", "license_url")
    @classmethod
    def reject_credential_urls(cls, value: str | None) -> str | None:
        if value is not None:
            reject_credential_url(value)
        return value

    @field_validator("reviewed_at")
    @classmethod
    def require_aware_reviewed_at(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "reviewed_at")

    @field_validator("configuration")
    @classmethod
    def require_safe_configuration(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value)


class HistoricalSourceReleaseRecord(ContractModel):
    """Validated source-release metadata keyed by its immutable natural identity."""

    record_type: Literal["source_release"] = "source_release"
    release: HistoricalSourceReleaseIdentity
    retrieved_at: datetime
    data_available_at: datetime
    observed_from: datetime | None = None
    observed_to: datetime | None = None
    payload_bytes: int = Field(ge=1)
    schema_version: str = Field(min_length=1, max_length=100)
    license_snapshot: str = Field(min_length=1, max_length=10_000)
    query_parameters: dict[str, Any] = Field(default_factory=dict)
    quality_summary: dict[str, Any] = Field(default_factory=dict)
    validation_state: Literal["valid"] = "valid"
    validated_at: datetime

    @field_validator("retrieved_at", "data_available_at", "observed_from", "observed_to", "validated_at")
    @classmethod
    def require_aware_times(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware_utc(value, "source-release timestamp")

    @field_validator("query_parameters", "quality_summary")
    @classmethod
    def require_safe_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value)

    @model_validator(mode="after")
    def require_ordered_window(self) -> HistoricalSourceReleaseRecord:
        if self.observed_from is not None and self.observed_to is not None and self.observed_to < self.observed_from:
            raise ValueError("observed_to must not precede observed_from")
        if self.validated_at < self.retrieved_at:
            raise ValueError("validated_at must not precede retrieved_at")
        return self


class HistoricalArtifactRecord(ContractModel):
    """Immutable raw-source receipt descriptor; bytes travel in a separate stream."""

    record_type: Literal["artifact"] = "artifact"
    release: HistoricalSourceReleaseIdentity
    kind: Literal["raw_source"] = "raw_source"
    uri: str = Field(min_length=1, max_length=2_000)
    media_type: str | None = Field(default=None, max_length=255)
    checksum_sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(ge=1, le=MAX_HISTORICAL_ARTIFACT_BYTES)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("uri")
    @classmethod
    def reject_credential_uri(cls, value: str) -> str:
        reject_credential_url(value)
        return value

    @field_validator("metadata")
    @classmethod
    def require_safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value)


class _HistoricalGridCellRecord(ContractModel):
    """Shared grid-cell fields for source-specific portable records."""

    cell_key: str = Field(pattern=CELL_KEY_PATTERN)
    grid_name: str = Field(min_length=1, max_length=100)
    resolution_m: int = Field(gt=0)
    geometry_json: str = Field(min_length=1)
    centroid_json: str = Field(min_length=1)
    coverage_fraction: float = Field(gt=0, le=1)

    @field_validator("geometry_json")
    @classmethod
    def require_canonical_polygon(cls, value: str) -> str:
        return _canonical_geometry(value, "Polygon")

    @field_validator("centroid_json")
    @classmethod
    def require_canonical_point(cls, value: str) -> str:
        return _canonical_geometry(value, "Point")


class _HistoricalGridCrosswalkRecord(ContractModel):
    """Shared point-to-cell allocation fields for source-specific grid records."""

    release: HistoricalSourceReleaseIdentity
    cell_key: str = Field(pattern=CELL_KEY_PATTERN)
    native_feature_key: str = Field(pattern=FEATURE_KEY_PATTERN)
    native_geometry_json: str = Field(min_length=1)
    native_resolution_m: int | None = Field(default=None, gt=0)
    spatial_support_kind: Literal["native_grid_cell", "native_polygon", "point_sample", "area_aggregate", "unknown"] = (
        "unknown"
    )
    mapping_method: str = Field(min_length=1, max_length=100)
    coverage_fraction: float = Field(gt=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("native_geometry_json")
    @classmethod
    def require_canonical_point(cls, value: str) -> str:
        return _canonical_geometry(value, "Point")

    @field_validator("metadata")
    @classmethod
    def require_safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value)


class _HistoricalGridObservationRecord(ContractModel):
    """Shared normalized grid-fact fields with preserved source missingness."""

    release: HistoricalSourceReleaseIdentity
    cell_key: str = Field(pattern=CELL_KEY_PATTERN)
    signal_name: str = Field(min_length=1, max_length=150)
    source_parameter: str = Field(min_length=1, max_length=150)
    support_key: str = Field(default="surface", min_length=1, max_length=150)
    observed_at: datetime
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    data_available_at: datetime
    original_value: float | None = None
    original_unit: str | None = Field(default=None, max_length=64)
    normalized_value: float | None = None
    normalized_unit: str | None = Field(default=None, max_length=64)
    quality_flag: str = Field(default="accepted", min_length=1, max_length=64)
    coverage_fraction: float = Field(default=1, ge=0, le=1)
    is_observed: bool
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("observed_at", "valid_from", "valid_to", "data_available_at")
    @classmethod
    def require_aware_times(cls, value: datetime | None) -> datetime | None:
        return None if value is None else _require_aware_utc(value, "observation timestamp")

    @field_validator("original_value", "normalized_value")
    @classmethod
    def require_finite_values(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("observation values must be finite when present")
        return value

    @field_validator("metadata")
    @classmethod
    def require_safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value)

    @model_validator(mode="after")
    def require_ordered_valid_window(self) -> _HistoricalGridObservationRecord:
        if self.valid_from is not None and self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not precede valid_from")
        return self


class _HistoricalGridCoverageAuditRecord(ContractModel):
    """Shared per-signal completeness evidence for a grid source window."""

    release: HistoricalSourceReleaseIdentity
    cell_key: str = Field(pattern=CELL_KEY_PATTERN)
    signal_name: str = Field(min_length=1, max_length=150)
    source_parameter: str = Field(min_length=1, max_length=150)
    support_key: str = Field(default="surface", min_length=1, max_length=150)
    window_start: datetime
    window_end: datetime
    expected_observation_count: int = Field(ge=0)
    received_observation_count: int = Field(ge=0)
    status: Literal["complete", "partial", "no_data", "failed"]
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("window_start", "window_end")
    @classmethod
    def require_aware_times(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "coverage timestamp")

    @field_validator("details")
    @classmethod
    def require_safe_details(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value)

    @model_validator(mode="after")
    def require_consistent_counts(self) -> _HistoricalGridCoverageAuditRecord:
        if self.window_end < self.window_start:
            raise ValueError("coverage window_end must not precede window_start")
        _require_coverage_status(self.status, self.expected_observation_count, self.received_observation_count)
        return self


class HistoricalNasaCellRecord(_HistoricalGridCellRecord):
    """One stable NASA analysis cell, represented without its warehouse UUID."""

    record_type: Literal["nasa_cell"] = "nasa_cell"


class HistoricalNasaCrosswalkRecord(_HistoricalGridCrosswalkRecord):
    """Native NASA point-to-cell allocation keyed by release and source feature."""

    record_type: Literal["nasa_crosswalk"] = "nasa_crosswalk"


class HistoricalNasaObservationRecord(_HistoricalGridObservationRecord):
    """One normalized NASA daily fact with preserved source missingness."""

    record_type: Literal["nasa_observation"] = "nasa_observation"


class HistoricalNasaCoverageAuditRecord(_HistoricalGridCoverageAuditRecord):
    """Per-signal NASA completeness evidence for the requested historical window."""

    record_type: Literal["nasa_coverage"] = "nasa_coverage"


class HistoricalEra5CellRecord(_HistoricalGridCellRecord):
    """One stable ERA5-Land analysis cell without a warehouse surrogate key."""

    record_type: Literal["era5_cell"] = "era5_cell"


class HistoricalEra5CrosswalkRecord(_HistoricalGridCrosswalkRecord):
    """One ERA5-Land native grid allocation keyed by source release and cell."""

    record_type: Literal["era5_crosswalk"] = "era5_crosswalk"


class HistoricalEra5ObservationRecord(_HistoricalGridObservationRecord):
    """One normalized ERA5-Land daily fact with preserved source missingness."""

    record_type: Literal["era5_observation"] = "era5_observation"


class HistoricalEra5CoverageAuditRecord(_HistoricalGridCoverageAuditRecord):
    """Per-signal ERA5-Land completeness evidence for its retrospective window."""

    record_type: Literal["era5_coverage"] = "era5_coverage"


class HistoricalUsdmPolygonRecord(ContractModel):
    """One canonical native USDM D0-D4 polygon keyed without a database surrogate."""

    record_type: Literal["usdm_polygon"] = "usdm_polygon"
    release: HistoricalSourceReleaseIdentity
    issue_date: date
    feature_key: str = Field(pattern=FEATURE_KEY_PATTERN)
    severity_class: int = Field(ge=0, le=4)
    impact_type: Literal["none"] = "none"
    geometry_json: str = Field(min_length=1)
    geometry_checksum: str = Field(pattern=SHA256_PATTERN)
    data_available_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("geometry_json")
    @classmethod
    def require_canonical_multipolygon(cls, value: str) -> str:
        return _canonical_geometry(value, "MultiPolygon")

    @field_validator("data_available_at")
    @classmethod
    def require_aware_data_available_at(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "data_available_at")

    @field_validator("metadata")
    @classmethod
    def require_safe_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value)

    @model_validator(mode="after")
    def require_matching_geometry_checksum(self) -> HistoricalUsdmPolygonRecord:
        if hashlib.sha256(self.geometry_json.encode("utf-8")).hexdigest() != self.geometry_checksum:
            raise ValueError("geometry_checksum does not match canonical geometry_json")
        return self


class HistoricalUsdmCoverageAuditRecord(ContractModel):
    """USDM source-package completeness evidence, retained per weekly release."""

    record_type: Literal["usdm_coverage"] = "usdm_coverage"
    release: HistoricalSourceReleaseIdentity
    scope_key: str = Field(min_length=1, max_length=180)
    window_start: datetime
    window_end: datetime
    expected_feature_count: int = Field(ge=0)
    received_feature_count: int = Field(ge=0)
    status: Literal["complete", "partial", "no_data", "failed"]
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("window_start", "window_end")
    @classmethod
    def require_aware_times(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "coverage timestamp")

    @field_validator("details")
    @classmethod
    def require_safe_details(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_metadata(value)

    @model_validator(mode="after")
    def require_consistent_counts(self) -> HistoricalUsdmCoverageAuditRecord:
        if self.window_end < self.window_start:
            raise ValueError("coverage window_end must not precede window_start")
        _require_coverage_status(self.status, self.expected_feature_count, self.received_feature_count)
        return self


def _require_coverage_status(status: str, expected: int, received: int) -> None:
    if received > expected:
        raise ValueError("received coverage count cannot exceed expected count")
    if status == "complete" and received != expected:
        raise ValueError("complete coverage must receive every expected item")
    if status == "partial" and not 0 < received < expected:
        raise ValueError("partial coverage must receive a strict subset")
    if status == "no_data" and received != 0:
        raise ValueError("no_data coverage must receive zero items")
    if status == "failed" and received >= expected:
        raise ValueError("failed coverage must receive fewer than expected items")


type HistoricalPromotionRecord = Annotated[
    HistoricalDataSourceRecord
    | HistoricalSourceReleaseRecord
    | HistoricalArtifactRecord
    | HistoricalNasaCellRecord
    | HistoricalNasaCrosswalkRecord
    | HistoricalNasaObservationRecord
    | HistoricalNasaCoverageAuditRecord
    | HistoricalEra5CellRecord
    | HistoricalEra5CrosswalkRecord
    | HistoricalEra5ObservationRecord
    | HistoricalEra5CoverageAuditRecord
    | HistoricalUsdmPolygonRecord
    | HistoricalUsdmCoverageAuditRecord,
    Field(discriminator="record_type"),
]

type HistoricalGridReleaseRecord = (
    HistoricalNasaCrosswalkRecord
    | HistoricalNasaObservationRecord
    | HistoricalNasaCoverageAuditRecord
    | HistoricalEra5CrosswalkRecord
    | HistoricalEra5ObservationRecord
    | HistoricalEra5CoverageAuditRecord
)

_HISTORICAL_PROMOTION_RECORD_ADAPTER: TypeAdapter[HistoricalPromotionRecord] = TypeAdapter(HistoricalPromotionRecord)

_RECORD_TYPE_ORDER = {
    "data_source": 0,
    "source_release": 1,
    "artifact": 2,
    "nasa_cell": 3,
    "nasa_crosswalk": 4,
    "nasa_observation": 5,
    "nasa_coverage": 6,
    "era5_cell": 7,
    "era5_crosswalk": 8,
    "era5_observation": 9,
    "era5_coverage": 10,
    "usdm_polygon": 11,
    "usdm_coverage": 12,
}


def historical_source_release_key(identity: HistoricalSourceReleaseIdentity) -> str:
    """Serialize the four immutable source identity dimensions deterministically."""
    return canonical_json_bytes(identity.model_dump(mode="json")).decode("utf-8")


def historical_source_release_token(identity: HistoricalSourceReleaseIdentity) -> str:
    """Return the opaque path token for one natural source-release identity."""
    return hashlib.sha256(historical_source_release_key(identity).encode("utf-8")).hexdigest()


def historical_source_release_sort_key(identity: HistoricalSourceReleaseIdentity) -> tuple[str, str, str, str]:
    """Order natural source identities by their explicit dimensions, never checksum JSON key order."""
    return (
        identity.source_key,
        identity.source_version,
        identity.payload_checksum,
        identity.transform_version,
    )


def historical_record_key(record: HistoricalPromotionRecord) -> str:
    """Return the canonical natural key used for duplicate and ordering checks."""
    release_key = historical_source_release_key(record.release) if hasattr(record, "release") else ""
    if isinstance(record, HistoricalDataSourceRecord):
        identity = record.source_key
    elif isinstance(record, HistoricalSourceReleaseRecord):
        identity = release_key
    elif isinstance(record, HistoricalArtifactRecord):
        identity = f"{release_key}|{record.kind}|{record.checksum_sha256}"
    elif isinstance(record, (HistoricalNasaCellRecord, HistoricalEra5CellRecord)):
        identity = record.cell_key
    elif isinstance(record, (HistoricalNasaCrosswalkRecord, HistoricalEra5CrosswalkRecord)):
        identity = f"{release_key}|{record.native_feature_key}|{record.cell_key}"
    elif isinstance(record, (HistoricalNasaObservationRecord, HistoricalEra5ObservationRecord)):
        identity = "|".join(
            (
                release_key,
                record.cell_key,
                record.signal_name,
                record.source_parameter,
                record.support_key,
                record.observed_at.isoformat(),
            )
        )
    elif isinstance(record, (HistoricalNasaCoverageAuditRecord, HistoricalEra5CoverageAuditRecord)):
        identity = "|".join(
            (
                release_key,
                record.cell_key,
                record.signal_name,
                record.source_parameter,
                record.support_key,
                record.window_start.isoformat(),
                record.window_end.isoformat(),
            )
        )
    elif isinstance(record, HistoricalUsdmPolygonRecord):
        identity = f"{release_key}|{record.severity_class}|{record.impact_type}|{record.geometry_checksum}"
    elif isinstance(record, HistoricalUsdmCoverageAuditRecord):
        identity = f"{release_key}|{record.scope_key}|{record.window_start.isoformat()}|{record.window_end.isoformat()}"
    else:  # pragma: no cover - discriminated union exhaustiveness guard
        raise TypeError(f"unsupported historical record type: {type(record)!r}")
    return f"{_RECORD_TYPE_ORDER[record.record_type]:02d}:{identity}"


class HistoricalPromotionChunkDescriptor(ContractModel):
    """Small root-manifest descriptor for one ordered, independently hashable chunk."""

    sequence: int = Field(ge=1, le=MAX_HISTORICAL_PROMOTION_CHUNKS)
    payload_sha256: str = Field(pattern=SHA256_PATTERN)
    payload_bytes: int = Field(gt=0, le=MAX_HISTORICAL_PROMOTION_CHUNK_BYTES)
    record_count: int = Field(gt=0, le=MAX_HISTORICAL_PROMOTION_RECORDS)
    first_record_key: str = Field(min_length=1, max_length=2_000)
    last_record_key: str = Field(min_length=1, max_length=2_000)
    record_type_counts: dict[str, int] = Field(min_length=1, max_length=len(_RECORD_TYPE_ORDER))

    @field_validator("record_type_counts")
    @classmethod
    def require_sorted_known_counts(cls, value: dict[str, int]) -> dict[str, int]:
        if (
            list(value) != sorted(value)
            or any(key not in _RECORD_TYPE_ORDER for key in value)
            or any(count <= 0 for count in value.values())
        ):
            raise ValueError("chunk record type counts must be sorted, known, and positive")
        return value

    @model_validator(mode="after")
    def require_count_and_key_order(self) -> HistoricalPromotionChunkDescriptor:
        if sum(self.record_type_counts.values()) != self.record_count:
            raise ValueError("chunk record type counts must equal record_count")
        if self.first_record_key > self.last_record_key:
            raise ValueError("chunk first_record_key must not follow last_record_key")
        return self


class HistoricalPromotionChunk(ContractModel):
    """One bounded ordered typed-record payload accompanied by its manifest descriptor."""

    descriptor: HistoricalPromotionChunkDescriptor
    records: list[HistoricalPromotionRecord] = Field(min_length=1, max_length=MAX_HISTORICAL_PROMOTION_RECORDS)

    @model_validator(mode="after")
    def require_matching_descriptor(self) -> HistoricalPromotionChunk:
        expected = historical_chunk_descriptor(self.descriptor.sequence, self.records)
        if self.descriptor != expected:
            raise ValueError("chunk descriptor does not match canonical record payload")
        return self


class HistoricalPromotionManifest(ContractModel):
    """Release-set-root manifest binding every ordered historical chunk by checksum."""

    schema_version: Literal[1] = HISTORICAL_PROMOTION_SCHEMA_VERSION
    format: Literal["plantgeo-historical-promotion-v1"] = HISTORICAL_PROMOTION_FORMAT
    release_set: HistoricalReleaseSetRoot
    minimum_target_revision: str = Field(min_length=1, max_length=128)
    chunks: list[HistoricalPromotionChunkDescriptor] = Field(min_length=1, max_length=MAX_HISTORICAL_PROMOTION_CHUNKS)
    total_record_count: int = Field(gt=0, le=MAX_HISTORICAL_PROMOTION_RECORDS)
    manifest_checksum: str = Field(pattern=SHA256_PATTERN)

    @field_validator("chunks")
    @classmethod
    def require_contiguous_ordered_chunks(
        cls, value: list[HistoricalPromotionChunkDescriptor]
    ) -> list[HistoricalPromotionChunkDescriptor]:
        sequences = [chunk.sequence for chunk in value]
        if sequences != list(range(1, len(value) + 1)):
            raise ValueError("promotion chunks must be ordered by contiguous sequence starting at one")
        return value

    @model_validator(mode="after")
    def require_manifest_integrity(self) -> HistoricalPromotionManifest:
        if sum(chunk.record_count for chunk in self.chunks) != self.total_record_count:
            raise ValueError("manifest total_record_count does not match chunk descriptors")
        if any(
            previous.last_record_key >= current.first_record_key
            for previous, current in zip(self.chunks, self.chunks[1:], strict=False)
        ):
            raise ValueError("promotion chunk key ranges must be globally ordered and non-overlapping")
        if self.manifest_checksum != historical_manifest_checksum(self):
            raise ValueError("manifest_checksum does not match manifest content")
        return self


class HistoricalPromotionBundle(ContractModel):
    """Complete independently-checkable promotion payload, still free of database IDs."""

    manifest: HistoricalPromotionManifest
    chunks: list[HistoricalPromotionChunk] = Field(min_length=1, max_length=MAX_HISTORICAL_PROMOTION_CHUNKS)

    @model_validator(mode="after")
    def require_complete_bound_bundle(self) -> HistoricalPromotionBundle:
        self.manifest = HistoricalPromotionManifest.model_validate(self.manifest.model_dump(mode="json"))
        self.chunks = [
            HistoricalPromotionChunk.model_validate(
                {
                    "descriptor": chunk.descriptor.model_dump(mode="json"),
                    "records": [record.model_dump(mode="json") for record in chunk.records],
                }
            )
            for chunk in self.chunks
        ]
        descriptors = [chunk.descriptor for chunk in self.chunks]
        if descriptors != self.manifest.chunks:
            raise ValueError("bundle chunks do not match the manifest descriptor list")
        records = [record for chunk in self.chunks for record in chunk.records]
        record_keys = [historical_record_key(record) for record in records]
        if record_keys != sorted(record_keys) or len(record_keys) != len(set(record_keys)):
            raise ValueError("bundle records must be globally sorted and unique by natural key")
        _validate_root_membership(self.manifest.release_set, records)
        return self


def historical_chunk_payload(records: list[HistoricalPromotionRecord]) -> bytes:
    """Return the checksum payload for a record chunk, independent of transport framing."""
    return canonical_json_bytes([record.model_dump(mode="json") for record in records])


def historical_chunk_descriptor(
    sequence: int, records: list[HistoricalPromotionRecord]
) -> HistoricalPromotionChunkDescriptor:
    """Derive a descriptor only from ordered typed records and their canonical JSON bytes."""
    if not records:
        raise ValueError("historical promotion chunks cannot be empty")
    keys = [historical_record_key(record) for record in records]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise ValueError("chunk records must be sorted and unique by natural key")
    payload = historical_chunk_payload(records)
    if len(payload) > MAX_HISTORICAL_PROMOTION_CHUNK_BYTES:
        raise ValueError("chunk payload exceeds the configured byte limit")
    counts: dict[str, int] = {}
    for record in records:
        counts[record.record_type] = counts.get(record.record_type, 0) + 1
    return HistoricalPromotionChunkDescriptor(
        sequence=sequence,
        payload_sha256=hashlib.sha256(payload).hexdigest(),
        payload_bytes=len(payload),
        record_count=len(records),
        first_record_key=keys[0],
        last_record_key=keys[-1],
        record_type_counts={key: counts[key] for key in sorted(counts)},
    )


def historical_manifest_checksum(manifest: HistoricalPromotionManifest) -> str:
    """Hash root content excluding the self-referential manifest checksum."""
    payload = manifest.model_dump(mode="json", exclude={"manifest_checksum"})
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def build_historical_promotion_bundle(
    *,
    release_set: HistoricalReleaseSetRoot,
    minimum_target_revision: str,
    records: list[HistoricalPromotionRecord],
    max_chunk_bytes: int = MAX_HISTORICAL_PROMOTION_CHUNK_BYTES,
) -> HistoricalPromotionBundle:
    """Deterministically split a complete release set into bounded canonical typed chunks."""
    validated_release_set = HistoricalReleaseSetRoot.model_validate(release_set.model_dump(mode="json"))
    validated_records = [
        _HISTORICAL_PROMOTION_RECORD_ADAPTER.validate_python(record.model_dump(mode="json")) for record in records
    ]
    if not 1 <= max_chunk_bytes <= MAX_HISTORICAL_PROMOTION_CHUNK_BYTES:
        raise ValueError("max_chunk_bytes must be within the historical promotion bound")
    if not records or len(records) > MAX_HISTORICAL_PROMOTION_RECORDS:
        raise ValueError("historical promotion must contain a bounded non-empty record set")
    ordered_records = sorted(validated_records, key=historical_record_key)
    ordered_keys = [historical_record_key(record) for record in ordered_records]
    if len(ordered_keys) != len(set(ordered_keys)):
        raise ValueError("historical promotion records must be unique by natural key")
    _validate_root_membership(validated_release_set, ordered_records)

    record_chunks: list[list[HistoricalPromotionRecord]] = []
    pending: list[HistoricalPromotionRecord] = []
    for record in ordered_records:
        candidate = [*pending, record]
        if len(historical_chunk_payload(candidate)) <= max_chunk_bytes:
            pending = candidate
            continue
        if not pending:
            raise ValueError("one historical promotion record exceeds the configured chunk bound")
        record_chunks.append(pending)
        pending = [record]
        if len(historical_chunk_payload(pending)) > max_chunk_bytes:
            raise ValueError("one historical promotion record exceeds the configured chunk bound")
    if pending:
        record_chunks.append(pending)
    if len(record_chunks) > MAX_HISTORICAL_PROMOTION_CHUNKS:
        raise ValueError("historical promotion exceeds the maximum chunk count")

    chunks = [
        HistoricalPromotionChunk(
            descriptor=historical_chunk_descriptor(sequence, chunk_records),
            records=chunk_records,
        )
        for sequence, chunk_records in enumerate(record_chunks, start=1)
    ]
    provisional: dict[str, Any] = {
        "schema_version": HISTORICAL_PROMOTION_SCHEMA_VERSION,
        "format": HISTORICAL_PROMOTION_FORMAT,
        "release_set": validated_release_set.model_dump(mode="json"),
        "minimum_target_revision": minimum_target_revision,
        "chunks": [chunk.descriptor.model_dump(mode="json") for chunk in chunks],
        "total_record_count": len(ordered_records),
    }
    manifest_checksum = hashlib.sha256(canonical_json_bytes(provisional)).hexdigest()
    manifest = HistoricalPromotionManifest.model_validate({**provisional, "manifest_checksum": manifest_checksum})
    return HistoricalPromotionBundle(manifest=manifest, chunks=chunks)


def _validate_root_membership(root: HistoricalReleaseSetRoot, records: list[HistoricalPromotionRecord]) -> None:
    """Require a closed, complete, source-typed release set before promotion."""
    member_keys = {historical_source_release_key(member) for member in root.members}
    release_records = [record for record in records if isinstance(record, HistoricalSourceReleaseRecord)]
    release_by_key = {historical_source_release_key(record.release): record for record in release_records}
    release_keys = set(release_by_key)
    if release_keys != member_keys or len(release_records) != len(release_keys):
        raise ValueError("release-set members must match one and only one source-release record each")
    source_records = [record for record in records if isinstance(record, HistoricalDataSourceRecord)]
    source_by_key = {record.source_key: record for record in source_records}
    source_keys = set(source_by_key)
    if source_keys != {member.source_key for member in root.members} or len(source_records) != len(source_keys):
        raise ValueError("release-set members must match one and only one data-source record per source key")
    if any(record.reviewed_at > root.as_of_time for record in source_records):
        raise ValueError("data-source review evidence must not postdate the release-set as_of_time")
    if any(
        record.retrieved_at > root.as_of_time
        or record.data_available_at > root.as_of_time
        or record.validated_at > root.as_of_time
        for record in release_records
    ):
        raise ValueError("source-release evidence must not postdate the release-set as_of_time")
    for record in records:
        if hasattr(record, "release") and historical_source_release_key(record.release) not in member_keys:
            raise ValueError("record references a source release outside the release-set root")
    for release_key, release_record in release_by_key.items():
        if release_record.release.source_key not in source_by_key:
            raise ValueError("source release does not have a governed data source")
        raw_artifacts = [
            record
            for record in records
            if isinstance(record, HistoricalArtifactRecord)
            and historical_source_release_key(record.release) == release_key
            and record.checksum_sha256 == release_record.release.payload_checksum
            and record.size_bytes == release_record.payload_bytes
        ]
        if len(raw_artifacts) != 1:
            raise ValueError("each source release requires exactly one matching raw artifact receipt")

    _validate_grid_source_records(records, release_by_key, NASA_POWER_SOURCE_KEY, "nasa")
    _validate_grid_source_records(records, release_by_key, ERA5_LAND_SOURCE_KEY, "era5")
    _validate_usdm_source_records(records, release_by_key)


def _validate_grid_source_records(
    records: list[HistoricalPromotionRecord],
    release_by_key: dict[str, HistoricalSourceReleaseRecord],
    source_key: str,
    record_prefix: str,
) -> None:
    grid_types = (
        (
            HistoricalNasaCellRecord,
            HistoricalNasaCrosswalkRecord,
            HistoricalNasaObservationRecord,
            HistoricalNasaCoverageAuditRecord,
        )
        if record_prefix == "nasa"
        else (
            HistoricalEra5CellRecord,
            HistoricalEra5CrosswalkRecord,
            HistoricalEra5ObservationRecord,
            HistoricalEra5CoverageAuditRecord,
        )
    )
    source_release_keys = {
        release_key for release_key, record in release_by_key.items() if record.release.source_key == source_key
    }
    typed_records = [record for record in records if isinstance(record, grid_types)]
    if not source_release_keys:
        if typed_records:
            raise ValueError(f"{record_prefix} records require a matching source release")
        return
    cells = {record.cell_key for record in typed_records if isinstance(record, grid_types[0])}
    if not cells:
        raise ValueError(f"{record_prefix} source releases require one or more spatial cells")
    for record in typed_records:
        if (
            not isinstance(record, grid_types[0])
            and cast("HistoricalGridReleaseRecord", record).release.source_key != source_key
        ):
            raise ValueError(f"{record_prefix} record uses the wrong source release type")
        if not isinstance(record, grid_types[0]) and record.cell_key not in cells:
            raise ValueError(f"{record_prefix} record references an unknown spatial cell")

    for release_key in source_release_keys:
        release_records = [
            record
            for record in typed_records
            if not isinstance(record, grid_types[0])
            and historical_source_release_key(cast("HistoricalGridReleaseRecord", record).release) == release_key
        ]
        crosswalks = [record for record in release_records if isinstance(record, grid_types[1])]
        observations = [record for record in release_records if isinstance(record, grid_types[2])]
        coverage = [record for record in release_records if isinstance(record, grid_types[3])]
        if not crosswalks or not observations or not coverage:
            raise ValueError(f"{record_prefix} source releases require crosswalk, observation, and coverage records")
        for audit in coverage:
            if audit.status != "complete":
                raise ValueError("promotable historical coverage must be complete")
            matching_observation_count = sum(
                1
                for observation in observations
                if observation.cell_key == audit.cell_key
                and observation.signal_name == audit.signal_name
                and observation.source_parameter == audit.source_parameter
                and observation.support_key == audit.support_key
                and observation.is_observed
                and audit.window_start <= observation.observed_at <= audit.window_end
            )
            if matching_observation_count != audit.received_observation_count:
                raise ValueError("grid coverage receipt does not match its normalized observation rows")


def _validate_usdm_source_records(
    records: list[HistoricalPromotionRecord],
    release_by_key: dict[str, HistoricalSourceReleaseRecord],
) -> None:
    source_release_keys = {
        release_key for release_key, record in release_by_key.items() if record.release.source_key == USDM_SOURCE_KEY
    }
    typed_records = [
        record
        for record in records
        if isinstance(record, (HistoricalUsdmPolygonRecord, HistoricalUsdmCoverageAuditRecord))
    ]
    if not source_release_keys:
        if typed_records:
            raise ValueError("USDM records require a matching source release")
        return
    for record in typed_records:
        if record.release.source_key != USDM_SOURCE_KEY:
            raise ValueError("USDM record uses the wrong source release type")
    for release_key in source_release_keys:
        polygons = [
            record
            for record in typed_records
            if isinstance(record, HistoricalUsdmPolygonRecord)
            and historical_source_release_key(record.release) == release_key
        ]
        coverage = [
            record
            for record in typed_records
            if isinstance(record, HistoricalUsdmCoverageAuditRecord)
            and historical_source_release_key(record.release) == release_key
        ]
        if len(coverage) != 1 or coverage[0].status != "complete":
            raise ValueError("USDM source releases require exactly one complete coverage receipt")
        if coverage[0].received_feature_count != len(polygons):
            raise ValueError("USDM coverage receipt does not match its native polygon rows")
