"""Bounded contracts for U.S. Drought Monitor historical vector backfills."""

from __future__ import annotations

import hashlib
import io
import math
import os
import zipfile
from asyncio import sleep as async_sleep
from asyncio import to_thread
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Literal

import httpx
import shapefile  # type: ignore[import-untyped]
from pydantic import Field, field_validator, model_validator

from agri_data_service.execution.contracts import ContractModel, canonical_json_bytes
from agri_data_service.execution.historical_backfill import HistoricalBackfillWindow  # noqa: TC001
from agri_data_service.execution.source_ingestion import SourceDefinition  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

USDM_SHAPEFILE_URL_TEMPLATE = "https://droughtmonitor.unl.edu/data/shapefiles_m/USDM_{issue_date:%Y%m%d}_M.zip"
USDM_SHAPEFILE_SCHEMA_VERSION: Literal["usdm-shapefile-v1"] = "usdm-shapefile-v1"
USDM_TRANSFORM_VERSION_PREFIX = "usdm-shapefile-normalization"
USDM_MAX_RESPONSE_BYTES = 8_000_000
USDM_MAX_UNCOMPRESSED_BYTES = 64_000_000
USDM_MAX_ZIP_ENTRIES = 12
USDM_MAX_FEATURES = 10_000
USDM_TIMEOUT_SECONDS = 30.0
USDM_MAX_ATTEMPTS = 4
USDM_MAX_RETRY_AFTER_SECONDS = 60.0
USDM_MAX_SEVERITY_CLASS = 4
USDM_MIN_RING_POSITIONS = 4
USDM_MIN_POSITION_DIMENSIONS = 2
WGS84_MAX_LATITUDE = 90
WGS84_MAX_LONGITUDE = 180
HISTORICAL_USDM_CHECKPOINT_SCHEMA_VERSION: Literal[1] = 1
HISTORICAL_USDM_FINALIZATION_SCHEMA_VERSION: Literal[1] = 1
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR_MIN = 500
HTTP_SERVER_ERROR_MAX = 599
_USDM_EXPECTED_FIELDS = ("OBJECTID", "DM", "Shape_Leng", "Shape_Area")


class HistoricalUsdmBackfillPlan(ContractModel):
    """Reviewed four-year USDM source scope, weekly release set, and transform identity."""

    source: SourceDefinition
    window: HistoricalBackfillWindow
    issue_dates: list[date] = Field(min_length=1, max_length=300)
    native_product_scope: str = Field(min_length=1, max_length=180)
    transform_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,99}$")
    release_set_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,253}$")
    release_set_as_of: datetime
    description: str | None = Field(default=None, max_length=10_000)

    @field_validator("issue_dates")
    @classmethod
    def require_complete_sorted_tuesday_schedule(cls, value: list[date]) -> list[date]:
        if value != sorted(value) or len(value) != len(set(value)):
            raise ValueError("USDM issue_dates must be sorted and unique")
        if any(issue_date.weekday() != 1 for issue_date in value):
            raise ValueError("USDM issue_dates must be Tuesday analysis dates")
        return value

    @field_validator("release_set_as_of")
    @classmethod
    def require_aware_release_set_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "release_set_as_of")

    @model_validator(mode="after")
    def require_usdm_governance_and_full_weekly_coverage(self) -> HistoricalUsdmBackfillPlan:
        if self.source.key != "usdm-weekly":
            raise ValueError("USDM backfill plans require source.key='usdm-weekly'")
        expected_dates = _tuesday_dates(self.window)
        if self.issue_dates != expected_dates:
            raise ValueError("USDM issue_dates must cover every Tuesday within the four-year window")
        if self.transform_version.split("-v", maxsplit=1)[0] != USDM_TRANSFORM_VERSION_PREFIX:
            raise ValueError(f"USDM transform_version must begin '{USDM_TRANSFORM_VERSION_PREFIX}-v'")
        forbidden_scope_terms = {"global", "worldwide", "pacific-islands", "virgin-islands"}
        if any(term in self.native_product_scope.lower() for term in forbidden_scope_terms):
            raise ValueError("USDM native_product_scope must describe only the reviewed native product coverage")
        return self


class HistoricalUsdmFinalization(ContractModel):
    """Controlled release-set correction that preserves a completed source replay."""

    schema_version: Literal[1] = HISTORICAL_USDM_FINALIZATION_SCHEMA_VERSION
    source_plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_set_key: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,253}$")
    release_set_as_of: datetime
    description: str = Field(min_length=1, max_length=10_000)

    @field_validator("release_set_as_of")
    @classmethod
    def require_aware_finalization_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "release_set_as_of")


class HistoricalUsdmReceipt(ContractModel):
    """One committed, validated weekly USDM source package."""

    issue_date: date
    payload_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    payload_bytes: int = Field(ge=1, le=USDM_MAX_RESPONSE_BYTES)
    feature_count: int = Field(ge=1, le=USDM_MAX_FEATURES)
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def require_aware_retrieval_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "retrieved_at")


class HistoricalUsdmCheckpoint(ContractModel):
    """Resumable local state that binds each weekly receipt to the reviewed plan."""

    schema_version: Literal[1] = HISTORICAL_USDM_CHECKPOINT_SCHEMA_VERSION
    state: Literal["initialized", "running", "validated", "blocked"]
    plan_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    receipts: list[HistoricalUsdmReceipt] = Field(default_factory=list, max_length=300)
    updated_at: datetime
    reason: str | None = Field(default=None, max_length=1_000)

    @field_validator("receipts")
    @classmethod
    def require_sorted_unique_receipts(cls, value: list[HistoricalUsdmReceipt]) -> list[HistoricalUsdmReceipt]:
        dates = [receipt.issue_date for receipt in value]
        if dates != sorted(dates) or len(dates) != len(set(dates)):
            raise ValueError("USDM receipts must be sorted and unique by issue_date")
        return value

    @field_validator("updated_at")
    @classmethod
    def require_aware_update_time(cls, value: datetime) -> datetime:
        return _require_aware_utc(value, "updated_at")


@dataclass(frozen=True)
class UsdmDroughtPolygon:
    """One canonical WGS84 USDM drought polygon retained from the source package."""

    feature_key: str
    severity_class: int
    geometry_json: str
    geometry_checksum: str
    metadata: dict[str, int | float | str]


@dataclass(frozen=True)
class UsdmShapefileResult:
    """Validated weekly USDM ZIP receipt and its typed drought polygons."""

    issue_date: date
    retrieved_at: datetime
    payload: bytes
    payload_checksum: str
    declared_feature_count: int
    polygons: tuple[UsdmDroughtPolygon, ...]


def historical_usdm_plan_checksum(plan: HistoricalUsdmBackfillPlan) -> str:
    """Fingerprint every reviewed input that controls the weekly replay."""
    return hashlib.sha256(canonical_json_bytes(plan.model_dump(mode="json"))).hexdigest()


def historical_usdm_checkpoint_path(root: Path, plan: HistoricalUsdmBackfillPlan) -> Path:
    """Derive one durable, plan-bound local checkpoint path."""
    return root / "historical-usdm" / f"{historical_usdm_plan_checksum(plan)}.json"


def rebind_historical_usdm_checkpoint_for_finalization(
    source_plan: HistoricalUsdmBackfillPlan,
    finalization: HistoricalUsdmFinalization,
    checkpoint: HistoricalUsdmCheckpoint,
    *,
    updated_at: datetime | None = None,
) -> tuple[HistoricalUsdmBackfillPlan, HistoricalUsdmCheckpoint]:
    """Create a new governed release plan only from a complete immutable source replay."""
    source_checksum = historical_usdm_plan_checksum(source_plan)
    if finalization.source_plan_checksum != source_checksum:
        raise ValueError("USDM finalization does not identify the reviewed source plan")
    if checkpoint.plan_checksum != source_checksum:
        raise ValueError("USDM checkpoint does not match the reviewed source plan")
    if [receipt.issue_date for receipt in checkpoint.receipts] != source_plan.issue_dates:
        raise ValueError("USDM receipt coverage does not match the reviewed issue-date plan")
    if finalization.release_set_as_of <= source_plan.release_set_as_of:
        raise ValueError("USDM finalization as-of time must advance the reviewed source plan")
    if any(receipt.retrieved_at > finalization.release_set_as_of for receipt in checkpoint.receipts):
        raise ValueError("USDM finalization as-of time must not precede a persisted source receipt")
    release_plan = HistoricalUsdmBackfillPlan.model_validate(
        {
            **source_plan.model_dump(mode="json"),
            "release_set_key": finalization.release_set_key,
            "release_set_as_of": finalization.release_set_as_of,
            "description": finalization.description,
        }
    )
    return (
        release_plan,
        checkpoint.model_copy(
            update={
                "state": "validated",
                "plan_checksum": historical_usdm_plan_checksum(release_plan),
                "updated_at": _require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
                "reason": None,
            }
        ),
    )


def initialize_historical_usdm_checkpoint(
    plan: HistoricalUsdmBackfillPlan,
    *,
    updated_at: datetime | None = None,
) -> HistoricalUsdmCheckpoint:
    """Create empty checkpoint state without a provider or database call."""
    return HistoricalUsdmCheckpoint(
        state="initialized",
        plan_checksum=historical_usdm_plan_checksum(plan),
        updated_at=_require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
    )


def load_historical_usdm_checkpoint(path: Path) -> HistoricalUsdmCheckpoint:
    """Read a durable USDM checkpoint without side effects."""
    return HistoricalUsdmCheckpoint.model_validate_json(path.read_bytes())


def write_historical_usdm_checkpoint(path: Path, checkpoint: HistoricalUsdmCheckpoint) -> None:
    """Atomically write credential-free receipt metadata to the local operator store."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(canonical_json_bytes(checkpoint.model_dump(mode="json")))
    os.replace(temporary, path)


def record_historical_usdm_result(
    plan: HistoricalUsdmBackfillPlan,
    checkpoint: HistoricalUsdmCheckpoint,
    result: UsdmShapefileResult,
    *,
    updated_at: datetime | None = None,
) -> HistoricalUsdmCheckpoint:
    """Advance a checkpoint only after one complete weekly package committed locally."""
    expected_checksum = historical_usdm_plan_checksum(plan)
    if checkpoint.plan_checksum != expected_checksum:
        raise ValueError("USDM checkpoint does not match the reviewed plan")
    if result.issue_date not in plan.issue_dates:
        raise ValueError("USDM result is not part of the reviewed historical plan")
    require_complete_usdm_result(plan, result)
    receipt = HistoricalUsdmReceipt(
        issue_date=result.issue_date,
        payload_checksum=result.payload_checksum,
        payload_bytes=len(result.payload),
        feature_count=len(result.polygons),
        retrieved_at=result.retrieved_at,
    )
    prior = {item.issue_date: item for item in checkpoint.receipts}
    existing = prior.get(receipt.issue_date)
    if existing is not None and existing != receipt:
        raise ValueError("USDM checkpoint already binds this issue date to different source content")
    prior[receipt.issue_date] = receipt
    receipts = [prior[issue_date] for issue_date in sorted(prior)]
    state: Literal["initialized", "running", "validated", "blocked"] = (
        "validated" if set(prior) == set(plan.issue_dates) else "running"
    )
    return HistoricalUsdmCheckpoint(
        state=state,
        plan_checksum=expected_checksum,
        receipts=receipts,
        updated_at=_require_aware_utc(updated_at or datetime.now(UTC), "updated_at"),
    )


def historical_usdm_release_manifest(
    plan: HistoricalUsdmBackfillPlan,
    checkpoint: HistoricalUsdmCheckpoint,
) -> str:
    """Hash the exact complete weekly receipt set that a release set must pin."""
    expected_checksum = historical_usdm_plan_checksum(plan)
    if checkpoint.plan_checksum != expected_checksum or checkpoint.state != "validated":
        raise ValueError("a complete validated USDM checkpoint is required for a release manifest")
    if [receipt.issue_date for receipt in checkpoint.receipts] != plan.issue_dates:
        raise ValueError("USDM receipt coverage does not match the reviewed issue-date plan")
    return hashlib.sha256(
        canonical_json_bytes(
            {
                "plan_checksum": expected_checksum,
                "transform_version": plan.transform_version,
                "receipts": [receipt.model_dump(mode="json") for receipt in checkpoint.receipts],
            }
        )
    ).hexdigest()


def usdm_shapefile_url(issue_date: date) -> httpx.URL:
    """Build the official, credential-free weekly USDM medium-resolution ZIP URL."""
    return httpx.URL(USDM_SHAPEFILE_URL_TEMPLATE.format(issue_date=issue_date))


async def fetch_usdm_shapefile(  # noqa: PLR0913
    plan: HistoricalUsdmBackfillPlan,
    issue_date: date,
    *,
    client: httpx.AsyncClient | None = None,
    retrieved_at: datetime | None = None,
    max_attempts: int = USDM_MAX_ATTEMPTS,
    retry_base_seconds: float = 1.0,
    sleep: Callable[[float], Awaitable[None]] = async_sleep,
) -> UsdmShapefileResult:
    """Fetch one bounded official USDM ZIP and parse it into canonical drought polygons."""
    if issue_date not in plan.issue_dates:
        raise ValueError("USDM issue date is not present in the immutable historical plan")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")
    if retry_base_seconds < 0:
        raise ValueError("retry_base_seconds cannot be negative")
    timestamp = _require_aware_utc(retrieved_at or datetime.now(UTC), "retrieved_at")
    if client is not None:
        return await _fetch_with_client(
            client,
            plan,
            issue_date,
            timestamp,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        )
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(USDM_TIMEOUT_SECONDS),
        follow_redirects=False,
    ) as owned_client:
        return await _fetch_with_client(
            owned_client,
            plan,
            issue_date,
            timestamp,
            max_attempts=max_attempts,
            retry_base_seconds=retry_base_seconds,
            sleep=sleep,
        )


def parse_usdm_shapefile_zip(
    plan: HistoricalUsdmBackfillPlan,
    issue_date: date,
    payload: bytes,
    *,
    retrieved_at: datetime,
) -> UsdmShapefileResult:
    """Fail closed unless the weekly ZIP is the reviewed WGS84 USDM vector product."""
    if issue_date not in plan.issue_dates:
        raise ValueError("USDM issue date is not present in the immutable historical plan")
    if not payload:
        raise ValueError("USDM response is empty")
    if len(payload) > USDM_MAX_RESPONSE_BYTES:
        raise ValueError(f"USDM response exceeds {USDM_MAX_RESPONSE_BYTES} bytes")
    timestamp = _require_aware_utc(retrieved_at, "retrieved_at")
    contents = _read_usdm_archive(issue_date, payload)
    _require_wgs84(contents["prj"])
    reader = shapefile.Reader(
        shp=io.BytesIO(contents["shp"]),
        shx=io.BytesIO(contents["shx"]),
        dbf=io.BytesIO(contents["dbf"]),
    )
    fields = tuple(field[0] for field in reader.fields[1:])
    if fields != _USDM_EXPECTED_FIELDS:
        raise ValueError("USDM DBF fields do not match the reviewed weekly product schema")
    declared_feature_count = _require_declared_feature_count(reader.numRecords)
    if _require_declared_feature_count(reader.numShapes) != declared_feature_count:
        raise ValueError("USDM shapefile declares different shape and DBF record counts")
    polygons: list[UsdmDroughtPolygon] = []
    for index, shape_record in enumerate(reader.iterShapeRecords()):
        if index >= USDM_MAX_FEATURES:
            raise ValueError(f"USDM response exceeds {USDM_MAX_FEATURES} vector features")
        record = shape_record.record.as_dict()
        severity = _require_severity(record.get("DM"))
        geometry_json, geometry_checksum = _canonical_multipolygon(shape_record.shape.__geo_interface__)
        object_id = _require_object_id(record.get("OBJECTID"))
        polygons.append(
            UsdmDroughtPolygon(
                feature_key=f"{issue_date:%Y%m%d}:DM{severity}:{geometry_checksum}",
                severity_class=severity,
                geometry_json=geometry_json,
                geometry_checksum=geometry_checksum,
                metadata={
                    "object_id": object_id,
                    "shape_length": _require_finite_numeric(record.get("Shape_Leng"), "Shape_Leng"),
                    "shape_area": _require_finite_numeric(record.get("Shape_Area"), "Shape_Area"),
                },
            )
        )
    if not polygons:
        raise ValueError("USDM response contains no drought-category polygons")
    if len(polygons) != declared_feature_count:
        raise ValueError("USDM parsed feature count does not match the source package declaration")
    checksum = hashlib.sha256(payload).hexdigest()
    return UsdmShapefileResult(
        issue_date=issue_date,
        retrieved_at=timestamp,
        payload=payload,
        payload_checksum=checksum,
        declared_feature_count=declared_feature_count,
        polygons=tuple(sorted(polygons, key=lambda polygon: polygon.feature_key)),
    )


def require_complete_usdm_result(plan: HistoricalUsdmBackfillPlan, result: UsdmShapefileResult) -> None:
    """Ensure one parsed result is eligible for an append-only weekly release."""
    if result.issue_date not in plan.issue_dates:
        raise ValueError("USDM result is not part of the reviewed historical plan")
    if not result.polygons:
        raise ValueError("USDM result contains no source polygons")
    if result.declared_feature_count != len(result.polygons):
        raise ValueError("USDM result does not match the source package feature-count declaration")
    if result.payload_checksum != hashlib.sha256(result.payload).hexdigest():
        raise ValueError("USDM payload checksum does not match the retained source receipt")
    feature_keys = [polygon.feature_key for polygon in result.polygons]
    if feature_keys != sorted(feature_keys) or len(feature_keys) != len(set(feature_keys)):
        raise ValueError("USDM result polygon identities must be sorted and unique")
    if any(
        polygon.severity_class < 0 or polygon.severity_class > USDM_MAX_SEVERITY_CLASS for polygon in result.polygons
    ):
        raise ValueError("USDM result contains a severity outside D0 through D4")


async def _fetch_with_client(  # noqa: PLR0913
    client: httpx.AsyncClient,
    plan: HistoricalUsdmBackfillPlan,
    issue_date: date,
    retrieved_at: datetime,
    *,
    max_attempts: int,
    retry_base_seconds: float,
    sleep: Callable[[float], Awaitable[None]],
) -> UsdmShapefileResult:
    url = usdm_shapefile_url(issue_date)
    for attempt in range(1, max_attempts + 1):
        try:
            async with client.stream(
                "GET",
                url,
                headers={
                    "Accept": "application/x-zip-compressed, application/zip, application/octet-stream;q=0.9, */*;q=0.8"
                },
                follow_redirects=False,
                timeout=USDM_TIMEOUT_SECONDS,
            ) as response:
                if _is_retryable_status(response.status_code) and attempt < max_attempts:
                    retry_after = _retry_delay(response, attempt, retry_base_seconds)
                else:
                    response.raise_for_status()
                    if "zip" not in response.headers.get("content-type", "").lower():
                        raise ValueError("USDM response must declare a ZIP content type")
                    payload = bytearray()
                    async for chunk in response.aiter_bytes():
                        payload.extend(chunk)
                        if len(payload) > USDM_MAX_RESPONSE_BYTES:
                            raise ValueError(f"USDM response exceeds {USDM_MAX_RESPONSE_BYTES} bytes")
                    # Up to USDM_MAX_UNCOMPRESSED_BYTES of decompression plus one canonical-JSON
                    # serialisation and sha256 per feature: pure CPU with no yield point, so it belongs
                    # off the loop for the same reason the ERA5 parse does.
                    return await to_thread(
                        parse_usdm_shapefile_zip, plan, issue_date, bytes(payload), retrieved_at=retrieved_at
                    )
        except httpx.TransportError:
            if attempt == max_attempts:
                raise
            retry_after = retry_base_seconds * (2 ** (attempt - 1))
        await sleep(retry_after)
    raise RuntimeError("USDM fetch exhausted without a response")


def _read_usdm_archive(issue_date: date, payload: bytes) -> dict[str, bytes]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise ValueError("USDM response must be a valid ZIP archive") from exc
    with archive:
        entries = archive.infolist()
        if len(entries) > USDM_MAX_ZIP_ENTRIES:
            raise ValueError("USDM ZIP contains too many entries")
        if any(_unsafe_archive_name(entry.filename) for entry in entries):
            raise ValueError("USDM ZIP contains an unsafe archive path")
        if len({entry.filename for entry in entries}) != len(entries):
            raise ValueError("USDM ZIP contains duplicate archive entries")
        if sum(entry.file_size for entry in entries) > USDM_MAX_UNCOMPRESSED_BYTES:
            raise ValueError("USDM ZIP expands beyond the permitted size")
        required = _usdm_required_members(issue_date, {entry.filename for entry in entries})
        if required is None:
            raise ValueError("USDM ZIP entries do not match the reviewed weekly package")
        return {key: archive.read(name) for key, name in required.items()}


def _usdm_required_members(issue_date: date, entries: set[str]) -> dict[str, str] | None:
    """Accept only the two verified historical basename conventions with exact members."""
    suffixes = {
        "shp": ".shp",
        "shx": ".shx",
        "dbf": ".dbf",
        "prj": ".prj",
        "cpg": ".cpg",
        "sbn": ".sbn",
        "sbx": ".sbx",
        "xml": ".shp.xml",
    }
    for base_name in (f"USDM_{issue_date:%Y%m%d}_M", f"USDM_{issue_date:%Y%m%d}"):
        required = {key: f"{base_name}{suffix}" for key, suffix in suffixes.items()}
        if entries == set(required.values()):
            return required
    return None


def _canonical_multipolygon(geometry: object) -> tuple[str, str]:
    if not isinstance(geometry, dict):
        raise ValueError("USDM vector feature geometry must be an object")
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    polygons: object
    if geometry_type == "Polygon":
        polygons = [coordinates]
    elif geometry_type == "MultiPolygon":
        polygons = coordinates
    else:
        raise ValueError("USDM vector feature must be a Polygon or MultiPolygon")
    canonical_coordinates = _normalize_multipolygon_coordinates(polygons)
    canonical = canonical_json_bytes({"type": "MultiPolygon", "coordinates": canonical_coordinates})
    return canonical.decode("utf-8"), hashlib.sha256(canonical).hexdigest()


def _normalize_multipolygon_coordinates(value: object) -> list[list[list[list[float]]]]:
    if not isinstance(value, list | tuple) or not value:
        raise ValueError("USDM multipolygon must contain at least one polygon")
    normalized_polygons: list[list[list[list[float]]]] = []
    for polygon in value:
        if not isinstance(polygon, list | tuple) or not polygon:
            raise ValueError("USDM polygon must contain at least one ring")
        normalized_rings: list[list[list[float]]] = []
        for ring in polygon:
            if not isinstance(ring, list | tuple) or len(ring) < USDM_MIN_RING_POSITIONS:
                raise ValueError("USDM polygon rings must contain at least four positions")
            positions: list[list[float]] = []
            for position in ring:
                if not isinstance(position, list | tuple) or len(position) < USDM_MIN_POSITION_DIMENSIONS:
                    raise ValueError("USDM geometry positions must contain longitude and latitude")
                longitude = _require_finite_numeric(position[0], "longitude")
                latitude = _require_finite_numeric(position[1], "latitude")
                if (
                    not -WGS84_MAX_LONGITUDE <= longitude <= WGS84_MAX_LONGITUDE
                    or not -WGS84_MAX_LATITUDE <= latitude <= WGS84_MAX_LATITUDE
                ):
                    raise ValueError("USDM geometry positions must remain in WGS84 bounds")
                positions.append([longitude, latitude])
            if positions[0] != positions[-1]:
                raise ValueError("USDM polygon rings must be closed")
            normalized_rings.append(positions)
        normalized_polygons.append(normalized_rings)
    return normalized_polygons


def _require_wgs84(value: bytes) -> None:
    try:
        normalized = value.decode("utf-8-sig").upper().replace(" ", "")
    except UnicodeDecodeError as exc:
        raise ValueError("USDM projection text must be UTF-8") from exc
    if "WGS_1984" not in normalized and "WGS84" not in normalized:
        raise ValueError("USDM vector projection must be WGS84")


def _require_severity(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= USDM_MAX_SEVERITY_CLASS:
        raise ValueError("USDM DM values must be integer severity classes D0 through D4")
    return value


def _require_object_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("USDM OBJECTID values must be nonnegative integers")
    return value


def _require_declared_feature_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= USDM_MAX_FEATURES:
        raise ValueError("USDM source package must declare a bounded positive feature count")
    return value


def _require_finite_numeric(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise ValueError(f"USDM {label} must be finite numeric data")
    return float(value)


def _tuesday_dates(window: HistoricalBackfillWindow) -> list[date]:
    first = window.start_date + timedelta(days=(1 - window.start_date.weekday()) % 7)
    dates: list[date] = []
    current = first
    while current <= window.end_date:
        dates.append(current)
        current += timedelta(days=7)
    return dates


def _is_retryable_status(status_code: int) -> bool:
    return status_code == HTTP_TOO_MANY_REQUESTS or HTTP_SERVER_ERROR_MIN <= status_code <= HTTP_SERVER_ERROR_MAX


def _retry_delay(response: httpx.Response, attempt: int, retry_base_seconds: float) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after and retry_after.isdigit():
        return min(float(retry_after), USDM_MAX_RETRY_AFTER_SECONDS)
    return float(min(retry_base_seconds * (2 ** (attempt - 1)), USDM_MAX_RETRY_AFTER_SECONDS))


def _unsafe_archive_name(value: str) -> bool:
    return not value or "/" in value or "\\" in value or value.startswith(".") or ".." in value


def _require_aware_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return value.astimezone(UTC)
