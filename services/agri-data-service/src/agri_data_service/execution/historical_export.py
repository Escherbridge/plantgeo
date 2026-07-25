"""Disk-spooled, resumable transfer of a validated historical release set."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast
from uuid import UUID, uuid4

import httpx
from pydantic import Field, field_validator
from sqlalchemy import func, select

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

from agri_data_service.execution.contracts import SHA256_PATTERN, ContractModel, canonical_json_bytes
from agri_data_service.execution.historical_promotion import (
    ERA5_LAND_SOURCE_KEY,
    MAX_HISTORICAL_PROMOTION_CHUNK_BYTES,
    NASA_POWER_SOURCE_KEY,
    USDM_SOURCE_KEY,
    HistoricalArtifactRecord,
    HistoricalDataSourceRecord,
    HistoricalEra5CellRecord,
    HistoricalEra5CoverageAuditRecord,
    HistoricalEra5CrosswalkRecord,
    HistoricalEra5ObservationRecord,
    HistoricalNasaCellRecord,
    HistoricalNasaCoverageAuditRecord,
    HistoricalNasaCrosswalkRecord,
    HistoricalNasaObservationRecord,
    HistoricalPromotionChunk,
    HistoricalPromotionChunkDescriptor,
    HistoricalPromotionManifest,
    HistoricalPromotionRecord,
    HistoricalReleaseSetRoot,
    HistoricalSourceReleaseIdentity,
    HistoricalSourceReleaseRecord,
    HistoricalUsdmCoverageAuditRecord,
    HistoricalUsdmPolygonRecord,
    historical_chunk_descriptor,
    historical_chunk_payload,
    historical_record_key,
    historical_source_release_key,
    historical_source_release_token,
)
from agri_data_service.models.historical import (
    CellSourceCrosswalk,
    DroughtPolygonSnapshot,
    SignalCoverageAudit,
    SignalObservation,
    SourceCoverageAudit,
    SpatialCell,
)
from agri_data_service.models.provenance import (
    Artifact,
    DataSource,
    ReleaseSet,
    ReleaseSetItem,
    ReleaseSetState,
    ReleaseValidationState,
    SourceRelease,
    SourceReviewState,
)

HISTORICAL_PROMOTION_CHECKPOINT_SCHEMA_VERSION: Literal[1] = 1
HISTORICAL_PROMOTION_MANIFEST_FILE = "manifest.json"
HISTORICAL_PROMOTION_ARTIFACTS_FILE = "artifacts.json"
HISTORICAL_PROMOTION_CHECKPOINT_FILE = "checkpoint.json"
HISTORICAL_PROMOTION_CHUNK_DIRECTORY = "chunks"
_COMPLETE_SOURCE_KEYS = frozenset((NASA_POWER_SOURCE_KEY, ERA5_LAND_SOURCE_KEY, USDM_SOURCE_KEY))
_SHA256_HEX_LENGTH = 64
_RETRYABLE_HTTP_STATUS_CODES = (408, 429)
_HTTP_SERVER_ERROR_MIN = 500
_COVERAGE_STATUSES = frozenset(("complete", "partial", "no_data", "failed"))


class HistoricalPromotionError(ValueError):
    """A safe, durable boundary failure while preparing or sending a promotion."""


class HistoricalPromotionArtifactUpload(ContractModel):
    """Natural identity and opaque token for one separately uploaded raw artifact."""

    release: HistoricalSourceReleaseIdentity
    token: str = Field(pattern=SHA256_PATTERN)


class HistoricalPromotionCheckpoint(ContractModel):
    """Credential-free cursor for one independently recoverable transfer."""

    schema_version: Literal[1] = HISTORICAL_PROMOTION_CHECKPOINT_SCHEMA_VERSION
    state: Literal["spooled", "staged", "uploading", "finalized", "blocked"]
    release_set_key: str = Field(min_length=1, max_length=255)
    release_set_manifest_checksum: str = Field(pattern=SHA256_PATTERN)
    manifest_checksum: str = Field(pattern=SHA256_PATTERN)
    bundle_id: UUID | None = None
    next_chunk_sequence: int = Field(ge=1)
    uploaded_artifact_tokens: list[str] = Field(default_factory=list)
    updated_at: datetime
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("updated_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("checkpoint updated_at must include a timezone")
        return value.astimezone(UTC)

    @field_validator("uploaded_artifact_tokens")
    @classmethod
    def require_sorted_unique_tokens(cls, value: list[str]) -> list[str]:
        if value != sorted(value) or len(value) != len(set(value)):
            raise ValueError("uploaded artifact tokens must be sorted and unique")
        if any(
            len(token) != _SHA256_HEX_LENGTH or any(char not in "0123456789abcdef" for char in token) for token in value
        ):
            raise ValueError("uploaded artifact tokens must be SHA-256 digests")
        return value


@dataclass(frozen=True)
class HistoricalPromotionSpool:
    """Resolved bounded files and immutable manifest for one transfer attempt."""

    directory: Path
    manifest: HistoricalPromotionManifest
    artifacts: tuple[HistoricalPromotionArtifactUpload, ...]

    @property
    def manifest_path(self) -> Path:
        return self.directory / HISTORICAL_PROMOTION_MANIFEST_FILE

    @property
    def checkpoint_path(self) -> Path:
        return self.directory / HISTORICAL_PROMOTION_CHECKPOINT_FILE

    @property
    def chunks_directory(self) -> Path:
        return self.directory / HISTORICAL_PROMOTION_CHUNK_DIRECTORY

    def chunk_path(self, sequence: int) -> Path:
        return self.chunks_directory / f"{sequence:06d}.json"


def _write_canonical_json(path: Path, value: object) -> None:
    """Atomically write an immutable canonical JSON document below a caller-owned directory."""
    encoded = canonical_json_bytes(value)
    temporary = path.with_suffix(f"{path.suffix}.tmp-{uuid4().hex}")
    temporary.write_bytes(encoded)
    temporary.replace(path)


def _load_checkpoint(spool: HistoricalPromotionSpool) -> HistoricalPromotionCheckpoint:
    """Load a checkpoint only when it is bound to the exact spooled manifest."""
    try:
        checkpoint = HistoricalPromotionCheckpoint.model_validate_json(spool.checkpoint_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise HistoricalPromotionError("historical promotion checkpoint is unreadable") from exc
    if (
        checkpoint.manifest_checksum != spool.manifest.manifest_checksum
        or checkpoint.release_set_key != spool.manifest.release_set.logical_key
        or checkpoint.release_set_manifest_checksum != spool.manifest.release_set.release_set_manifest_checksum
    ):
        raise HistoricalPromotionError("historical promotion checkpoint is not bound to this manifest")
    return checkpoint


def _write_checkpoint(spool: HistoricalPromotionSpool, checkpoint: HistoricalPromotionCheckpoint) -> None:
    """Persist only a redacted retry cursor bound to this exact root manifest."""
    _write_canonical_json(spool.checkpoint_path, checkpoint.model_dump(mode="json"))


class HistoricalPromotionSpoolWriter:
    """Append sorted portable records into independently bounded on-disk chunks."""

    def __init__(
        self,
        *,
        root_directory: Path,
        release_set: HistoricalReleaseSetRoot,
        minimum_target_revision: str,
        max_chunk_bytes: int = MAX_HISTORICAL_PROMOTION_CHUNK_BYTES,
    ) -> None:
        if not 1 <= max_chunk_bytes <= MAX_HISTORICAL_PROMOTION_CHUNK_BYTES:
            raise HistoricalPromotionError("historical promotion chunk bound is invalid")
        self.release_set = HistoricalReleaseSetRoot.model_validate(release_set.model_dump(mode="json"))
        self.minimum_target_revision = minimum_target_revision
        self.max_chunk_bytes = max_chunk_bytes
        self.work_directory = root_directory / f".building-{uuid4().hex}"
        self.chunks_directory = self.work_directory / HISTORICAL_PROMOTION_CHUNK_DIRECTORY
        self.chunks_directory.mkdir(parents=True, exist_ok=False)
        self._pending: list[HistoricalPromotionRecord] = []
        self._descriptors: list[HistoricalPromotionChunkDescriptor] = []
        self._record_count = 0
        self._previous_key: str | None = None
        self._record_type_counts: Counter[tuple[str, str]] = Counter()
        self._data_sources: set[str] = set()
        self._source_releases: set[str] = set()
        self._artifacts: set[str] = set()

    def append(self, record: HistoricalPromotionRecord) -> None:
        """Validate, order, and append one record without retaining prior chunks in memory."""
        key = historical_record_key(record)
        if self._previous_key is not None and key <= self._previous_key:
            raise HistoricalPromotionError("historical promotion records must be globally sorted and unique")
        candidate = [*self._pending, record]
        if len(historical_chunk_payload(candidate)) > self.max_chunk_bytes:
            if not self._pending:
                raise HistoricalPromotionError("one historical promotion record exceeds the configured chunk bound")
            self._flush_pending()
            if len(historical_chunk_payload([record])) > self.max_chunk_bytes:
                raise HistoricalPromotionError("one historical promotion record exceeds the configured chunk bound")
            self._pending = [record]
        else:
            self._pending = candidate
        self._previous_key = key
        self._record_count += 1
        self._track(record)

    def _track(self, record: HistoricalPromotionRecord) -> None:
        if isinstance(record, HistoricalDataSourceRecord):
            if record.source_key in self._data_sources:
                raise HistoricalPromotionError("each source requires exactly one data-source record")
            if record.reviewed_at > self.release_set.as_of_time:
                raise HistoricalPromotionError("source review evidence postdates the release-set root")
            self._data_sources.add(record.source_key)
            return
        if isinstance(record, HistoricalSourceReleaseRecord):
            release_key = historical_source_release_key(record.release)
            if release_key in self._source_releases:
                raise HistoricalPromotionError("each release-set member requires exactly one source-release record")
            if any(
                observed > self.release_set.as_of_time
                for observed in (record.retrieved_at, record.data_available_at, record.validated_at)
            ):
                raise HistoricalPromotionError("source-release evidence postdates the release-set root")
            self._source_releases.add(release_key)
            return
        if isinstance(record, HistoricalArtifactRecord):
            release_key = historical_source_release_key(record.release)
            if release_key in self._artifacts:
                raise HistoricalPromotionError("each release-set member requires exactly one raw artifact record")
            self._artifacts.add(release_key)
            return
        if (
            isinstance(
                record,
                (
                    HistoricalNasaCoverageAuditRecord,
                    HistoricalEra5CoverageAuditRecord,
                    HistoricalUsdmCoverageAuditRecord,
                ),
            )
            and record.status != "complete"
        ):
            raise HistoricalPromotionError("only complete historical coverage may be promoted")
        release = getattr(record, "release", None)
        if release is not None:
            self._record_type_counts[(historical_source_release_key(release), record.record_type)] += 1

    def _flush_pending(self) -> None:
        if not self._pending:
            return
        sequence = len(self._descriptors) + 1
        descriptor = historical_chunk_descriptor(sequence, self._pending)
        chunk = HistoricalPromotionChunk(descriptor=descriptor, records=self._pending)
        _write_canonical_json(
            self.chunks_directory / f"{sequence:06d}.json",
            chunk.model_dump(mode="json"),
        )
        self._descriptors.append(descriptor)
        self._pending = []

    def _validate_closure(self) -> None:
        expected_members = {historical_source_release_key(member) for member in self.release_set.members}
        expected_sources = {member.source_key for member in self.release_set.members}
        if expected_sources != _COMPLETE_SOURCE_KEYS:
            raise HistoricalPromotionError("promotion requires complete NASA POWER, ERA5-Land, and USDM membership")
        if self._data_sources != expected_sources:
            raise HistoricalPromotionError("promotion data-source records do not match release-set membership")
        if self._source_releases != expected_members:
            raise HistoricalPromotionError("promotion source-release records do not match release-set membership")
        if self._artifacts != expected_members:
            raise HistoricalPromotionError("promotion raw artifacts do not match release-set membership")
        for member in self.release_set.members:
            release_key = historical_source_release_key(member)
            counts = self._record_type_counts
            if member.source_key == NASA_POWER_SOURCE_KEY and not all(
                counts[(release_key, record_type)] > 0
                for record_type in ("nasa_crosswalk", "nasa_observation", "nasa_coverage")
            ):
                raise HistoricalPromotionError(
                    "NASA source releases require crosswalk, observation, and coverage records"
                )
            if member.source_key == ERA5_LAND_SOURCE_KEY and not all(
                counts[(release_key, record_type)] > 0
                for record_type in ("era5_crosswalk", "era5_observation", "era5_coverage")
            ):
                raise HistoricalPromotionError(
                    "ERA5 source releases require crosswalk, observation, and coverage records"
                )
            if member.source_key == USDM_SOURCE_KEY and not all(
                counts[(release_key, record_type)] > 0 for record_type in ("usdm_polygon", "usdm_coverage")
            ):
                raise HistoricalPromotionError("USDM source releases require polygon and coverage records")

    def finish(self, artifacts: Iterable[HistoricalPromotionArtifactUpload]) -> HistoricalPromotionSpool:
        """Close the stream, persist the root manifest, and atomically publish its spool directory."""
        self._flush_pending()
        if {member.source_key for member in self.release_set.members} != _COMPLETE_SOURCE_KEYS:
            raise HistoricalPromotionError("promotion requires complete NASA POWER, ERA5-Land, and USDM membership")
        if not self._descriptors or self._record_count <= 0:
            raise HistoricalPromotionError("historical promotion must contain records")
        self._validate_closure()
        provisional = {
            "schema_version": 1,
            "format": "plantgeo-historical-promotion-v1",
            "release_set": self.release_set.model_dump(mode="json"),
            "minimum_target_revision": self.minimum_target_revision,
            "chunks": [descriptor.model_dump(mode="json") for descriptor in self._descriptors],
            "total_record_count": self._record_count,
        }
        manifest = HistoricalPromotionManifest.model_validate(
            {
                **provisional,
                "manifest_checksum": hashlib.sha256(canonical_json_bytes(provisional)).hexdigest(),
            }
        )
        uploads = tuple(sorted(artifacts, key=lambda value: value.token))
        expected_tokens = {historical_source_release_token(member) for member in self.release_set.members}
        if {upload.token for upload in uploads} != expected_tokens or len(uploads) != len(expected_tokens):
            raise HistoricalPromotionError("raw artifact upload tokens do not match release-set membership")
        _write_canonical_json(
            self.work_directory / HISTORICAL_PROMOTION_MANIFEST_FILE, manifest.model_dump(mode="json")
        )
        _write_canonical_json(
            self.work_directory / HISTORICAL_PROMOTION_ARTIFACTS_FILE,
            [upload.model_dump(mode="json") for upload in uploads],
        )
        _write_canonical_json(
            self.work_directory / HISTORICAL_PROMOTION_CHECKPOINT_FILE,
            HistoricalPromotionCheckpoint(
                state="spooled",
                release_set_key=manifest.release_set.logical_key,
                release_set_manifest_checksum=manifest.release_set.release_set_manifest_checksum,
                manifest_checksum=manifest.manifest_checksum,
                next_chunk_sequence=1,
                updated_at=datetime.now(UTC),
            ).model_dump(mode="json"),
        )
        final_directory = self.work_directory.parent / manifest.manifest_checksum
        if final_directory.exists():
            existing = HistoricalPromotionManifest.model_validate_json(
                (final_directory / HISTORICAL_PROMOTION_MANIFEST_FILE).read_bytes()
            )
            if existing != manifest:
                raise HistoricalPromotionError("existing promotion spool conflicts with the immutable manifest")
            return HistoricalPromotionSpool(final_directory, existing, uploads)
        self.work_directory.rename(final_directory)
        return HistoricalPromotionSpool(final_directory, manifest, uploads)


def load_historical_promotion_spool(directory: Path) -> HistoricalPromotionSpool:
    """Load a complete spool without connecting to either database or receiver."""
    try:
        manifest = HistoricalPromotionManifest.model_validate_json(
            (directory / HISTORICAL_PROMOTION_MANIFEST_FILE).read_bytes()
        )
        raw_uploads = json.loads((directory / HISTORICAL_PROMOTION_ARTIFACTS_FILE).read_bytes())
        if not isinstance(raw_uploads, list):
            raise ValueError("artifact upload sidecar must be a list")
        uploads = tuple(HistoricalPromotionArtifactUpload.model_validate(value) for value in raw_uploads)
    except (OSError, TypeError, ValueError) as exc:
        raise HistoricalPromotionError("historical promotion spool is unreadable") from exc
    spool = HistoricalPromotionSpool(directory, manifest, tuple(sorted(uploads, key=lambda value: value.token)))
    for descriptor in manifest.chunks:
        chunk = HistoricalPromotionChunk.model_validate_json(spool.chunk_path(descriptor.sequence).read_bytes())
        if chunk.descriptor != descriptor:
            raise HistoricalPromotionError("spooled chunk descriptor does not match its manifest")
    _load_checkpoint(spool)
    return spool


class HistoricalPromotionUploader:
    """Authenticated retrying sender for a prebuilt bounded promotion spool."""

    def __init__(self, *, api_url: str, token: str, retry_attempts: int = 5, retry_base_seconds: float = 0.5) -> None:
        if not api_url.startswith("https://"):
            raise HistoricalPromotionError("historical promotion receiver must use HTTPS")
        if not token or retry_attempts <= 0 or retry_base_seconds < 0:
            raise HistoricalPromotionError("historical promotion client configuration is invalid")
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.retry_attempts = retry_attempts
        self.retry_base_seconds = retry_base_seconds

    async def upload(
        self,
        spool: HistoricalPromotionSpool,
        artifact_loader: Callable[[HistoricalSourceReleaseIdentity], Awaitable[bytes]],
    ) -> HistoricalPromotionCheckpoint:
        """Stage, replay bounded chunks/artifacts, and finalize with an idempotent durable cursor."""
        checkpoint = _load_checkpoint(spool)
        headers = {"Authorization": f"Bearer {self.token}"}
        timeout = httpx.Timeout(60.0, connect=15.0)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                staged = await self._request_json(
                    client,
                    "POST",
                    "/bundles",
                    headers=headers,
                    json_payload=spool.manifest.model_dump(mode="json"),
                )
                bundle_id = UUID(_required_string(staged, "bundle_id"))
                if _required_string(staged, "manifest_checksum") != spool.manifest.manifest_checksum:
                    raise HistoricalPromotionError("receiver staged an unexpected manifest")
                if _required_string(staged, "state") == "finalized":
                    checkpoint = self._checkpoint(spool, checkpoint, state="finalized", bundle_id=bundle_id)
                    _write_checkpoint(spool, checkpoint)
                    return checkpoint
                checkpoint = self._checkpoint(spool, checkpoint, state="staged", bundle_id=bundle_id)
                _write_checkpoint(spool, checkpoint)
                for descriptor in spool.manifest.chunks:
                    if descriptor.sequence < checkpoint.next_chunk_sequence:
                        continue
                    response = await self._request_json(
                        client,
                        "PUT",
                        f"/bundles/{bundle_id}/chunks/{descriptor.sequence}",
                        headers={**headers, "Content-Type": "application/json"},
                        content=spool.chunk_path(descriptor.sequence).read_bytes(),
                    )
                    if (
                        response.get("payload_sha256") != descriptor.payload_sha256
                        or response.get("received") is not True
                    ):
                        raise HistoricalPromotionError("receiver did not acknowledge the expected historical chunk")
                    checkpoint = self._checkpoint(
                        spool,
                        checkpoint,
                        state="uploading",
                        bundle_id=bundle_id,
                        next_chunk_sequence=descriptor.sequence + 1,
                    )
                    _write_checkpoint(spool, checkpoint)
                uploaded = set(checkpoint.uploaded_artifact_tokens)
                for artifact in spool.artifacts:
                    if artifact.token in uploaded:
                        continue
                    content = await artifact_loader(artifact.release)
                    if not content:
                        raise HistoricalPromotionError("local raw artifact content is unavailable")
                    response = await self._request_json(
                        client,
                        "PUT",
                        f"/bundles/{bundle_id}/artifacts/{artifact.token}",
                        headers=headers,
                        content=content,
                    )
                    if (
                        response.get("artifact_checksum") != artifact.release.payload_checksum
                        or response.get("uploaded") is not True
                    ):
                        raise HistoricalPromotionError("receiver did not acknowledge the expected raw artifact")
                    uploaded.add(artifact.token)
                    checkpoint = self._checkpoint(
                        spool,
                        checkpoint,
                        state="uploading",
                        bundle_id=bundle_id,
                        uploaded_artifact_tokens=sorted(uploaded),
                    )
                    _write_checkpoint(spool, checkpoint)
                finalized = await self._request_json(
                    client,
                    "POST",
                    f"/bundles/{bundle_id}/finalize",
                    headers=headers,
                )
                if _required_string(finalized, "state") != "finalized":
                    raise HistoricalPromotionError("receiver did not finalize the historical promotion")
        except (httpx.HTTPError, OSError, ValueError, HistoricalPromotionError) as exc:
            checkpoint = self._checkpoint(spool, checkpoint, state="blocked", reason=_safe_failure_reason(exc))
            _write_checkpoint(spool, checkpoint)
            if isinstance(exc, HistoricalPromotionError):
                raise
            raise HistoricalPromotionError(_safe_failure_reason(exc)) from exc
        checkpoint = self._checkpoint(spool, checkpoint, state="finalized")
        _write_checkpoint(spool, checkpoint)
        return checkpoint

    def _checkpoint(
        self, spool: HistoricalPromotionSpool, previous: HistoricalPromotionCheckpoint, **updates: object
    ) -> HistoricalPromotionCheckpoint:
        return HistoricalPromotionCheckpoint.model_validate(
            {
                **previous.model_dump(mode="json"),
                **updates,
                "release_set_key": spool.manifest.release_set.logical_key,
                "release_set_manifest_checksum": spool.manifest.release_set.release_set_manifest_checksum,
                "manifest_checksum": spool.manifest.manifest_checksum,
                "updated_at": datetime.now(UTC),
                "reason": updates.get("reason"),
            }
        )

    async def _request_json(  # noqa: PLR0913
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        headers: dict[str, str],
        json_payload: dict[str, object] | None = None,
        content: bytes | None = None,
    ) -> dict[str, object]:
        for attempt in range(self.retry_attempts):
            try:
                response = await client.request(
                    method,
                    f"{self.api_url}{path}",
                    headers=headers,
                    json=json_payload,
                    content=content,
                )
            except httpx.HTTPError:
                if attempt + 1 == self.retry_attempts:
                    raise
            else:
                if response.status_code in (200, 201):
                    payload = response.json()
                    if isinstance(payload, dict):
                        return payload
                    raise HistoricalPromotionError("historical promotion receiver returned an invalid response")
                if (
                    response.status_code not in _RETRYABLE_HTTP_STATUS_CODES
                    and response.status_code < _HTTP_SERVER_ERROR_MIN
                ):
                    raise HistoricalPromotionError(
                        f"historical promotion receiver rejected the request ({response.status_code})"
                    )
                if attempt + 1 == self.retry_attempts:
                    raise HistoricalPromotionError(
                        f"historical promotion receiver is unavailable ({response.status_code})"
                    )
            if self.retry_base_seconds:
                await asyncio.sleep(self.retry_base_seconds * (2**attempt))
        raise HistoricalPromotionError("historical promotion request exhausted its retry budget")


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise HistoricalPromotionError("historical promotion receiver response is incomplete")
    return value


def _safe_failure_reason(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPError):
        return f"historical promotion request failed ({exc.__class__.__name__})"
    if isinstance(exc, OSError):
        return f"historical promotion spool operation failed ({exc.__class__.__name__})"
    return str(exc)[:500]


@dataclass(frozen=True)
class _LocalHistoricalMember:
    """One exact local release-set member stripped of its database surrogate identifiers."""

    source_release_id: UUID
    source: DataSource
    release: SourceRelease
    identity: HistoricalSourceReleaseIdentity


@dataclass(frozen=True)
class _LocalArtifactReceipt:
    """One validated inline source receipt kept without loading its bytes into the spool builder."""

    source_release_id: UUID
    kind: str
    uri: str
    media_type: str | None
    checksum_sha256: str
    size_bytes: int
    storage_class: str
    metadata: dict[str, object]
    inline_size_bytes: int | None


class LocalHistoricalPromotionExporter:
    """Read one validated local root, spool portable rows, and re-read raw bytes only while uploading."""

    def __init__(
        self,
        *,
        spool_root: Path,
        minimum_target_revision: str = "20260720_0004",
        max_chunk_bytes: int = MAX_HISTORICAL_PROMOTION_CHUNK_BYTES,
    ) -> None:
        self.spool_root = spool_root
        self.minimum_target_revision = minimum_target_revision
        self.max_chunk_bytes = max_chunk_bytes

    async def spool(self, session: AsyncSession, *, release_set_key: str) -> HistoricalPromotionSpool:
        """Stream one complete, validated local release set to a deterministic disk spool."""
        release_set, members, artifacts = await self._load_root(session, release_set_key)
        root = HistoricalReleaseSetRoot(
            logical_key=release_set.logical_key,
            as_of_time=release_set.as_of_time,
            release_set_manifest_checksum=release_set.manifest_checksum,
            members=sorted(
                (member.identity for member in members),
                key=lambda value: (
                    value.source_key,
                    value.source_version,
                    value.payload_checksum,
                    value.transform_version,
                ),
            ),
            description=release_set.description,
        )
        writer = HistoricalPromotionSpoolWriter(
            root_directory=self.spool_root / "historical-promotion",
            release_set=root,
            minimum_target_revision=self.minimum_target_revision,
            max_chunk_bytes=self.max_chunk_bytes,
        )
        artifact_uploads = tuple(
            HistoricalPromotionArtifactUpload(
                release=member.identity,
                token=historical_source_release_token(member.identity),
            )
            for member in members
        )
        try:
            for source in sorted(
                {member.source.key: member.source for member in members}.values(), key=lambda value: value.key
            ):
                writer.append(self._data_source_record(source))
            release_records = [self._source_release_record(member) for member in members]
            for record in sorted(release_records, key=historical_record_key):
                writer.append(record)
            artifact_records = [
                self._artifact_record(member, artifacts[member.source_release_id]) for member in members
            ]
            for artifact_record in sorted(artifact_records, key=historical_record_key):
                writer.append(artifact_record)
            for source_key in (NASA_POWER_SOURCE_KEY, ERA5_LAND_SOURCE_KEY):
                await self._emit_grid_source(session, writer, members, source_key)
            for member in self._ordered_members(members, USDM_SOURCE_KEY):
                await self._emit_usdm_polygons(session, writer, member)
            for member in self._ordered_members(members, USDM_SOURCE_KEY):
                await self._emit_usdm_coverage(session, writer, member)
            return writer.finish(artifact_uploads)
        except Exception as exc:
            raise HistoricalPromotionError(_safe_failure_reason(exc)) from exc

    async def _emit_grid_source(
        self,
        session: AsyncSession,
        writer: HistoricalPromotionSpoolWriter,
        members: tuple[_LocalHistoricalMember, ...],
        source_key: str,
    ) -> None:
        """Emit one source family in record-key order without retaining its observations."""
        await self._emit_grid_cells(session, writer, members, source_key)
        for member in self._ordered_members(members, source_key):
            await self._emit_grid_crosswalks(session, writer, member)
        for member in self._ordered_members(members, source_key):
            await self._emit_grid_observations(session, writer, member)
        for member in self._ordered_members(members, source_key):
            await self._emit_grid_coverage(session, writer, member)

    async def upload(
        self,
        session: AsyncSession,
        *,
        spool: HistoricalPromotionSpool,
        uploader: HistoricalPromotionUploader,
    ) -> HistoricalPromotionCheckpoint:
        """Upload an existing spool while reading no more than one bounded raw artifact at a time."""
        return await uploader.upload(spool, lambda identity: self._artifact_bytes(session, identity))

    async def _load_root(
        self, session: AsyncSession, release_set_key: str
    ) -> tuple[ReleaseSet, tuple[_LocalHistoricalMember, ...], dict[UUID, _LocalArtifactReceipt]]:
        release_set = (
            await session.execute(select(ReleaseSet).where(ReleaseSet.logical_key == release_set_key))
        ).scalar_one_or_none()
        if release_set is None or release_set.state not in (ReleaseSetState.VALIDATED, ReleaseSetState.PUBLISHED):
            raise HistoricalPromotionError("local historical release set is not validated")
        if release_set.validated_at is None or release_set.validated_at > release_set.as_of_time:
            raise HistoricalPromotionError("local historical release set has invalid validation timing")
        rows = (
            await session.execute(
                select(ReleaseSetItem, SourceRelease, DataSource)
                .join(SourceRelease, SourceRelease.id == ReleaseSetItem.source_release_id)
                .join(DataSource, DataSource.id == SourceRelease.data_source_id)
                .where(ReleaseSetItem.release_set_id == release_set.id)
            )
        ).all()
        if not rows:
            raise HistoricalPromotionError("local historical release set has no members")
        members: list[_LocalHistoricalMember] = []
        for item, release, source in rows:
            if item.source_role != "input":
                raise HistoricalPromotionError("historical promotion does not export non-input release-set roles")
            if (
                source.review_state != SourceReviewState.APPROVED
                or not source.is_active
                or source.reviewed_at is None
                or source.reviewed_by is None
            ):
                raise HistoricalPromotionError("historical promotion requires an active approved source")
            if release.validation_state != ReleaseValidationState.VALID or release.validated_at is None:
                raise HistoricalPromotionError("historical promotion requires a validated source release")
            if release.payload_bytes is None or release.payload_bytes <= 0:
                raise HistoricalPromotionError("historical promotion requires a source payload byte count")
            members.append(
                _LocalHistoricalMember(
                    source_release_id=release.id,
                    source=source,
                    release=release,
                    identity=HistoricalSourceReleaseIdentity(
                        source_key=source.key,
                        source_version=release.source_version,
                        payload_checksum=release.payload_checksum,
                        transform_version=release.transform_version,
                    ),
                )
            )
        if {member.source.key for member in members} != _COMPLETE_SOURCE_KEYS:
            raise HistoricalPromotionError("local historical release set does not contain every required source")
        artifacts = await self._load_artifacts(session, members)
        return release_set, tuple(members), artifacts

    async def _load_artifacts(
        self, session: AsyncSession, members: list[_LocalHistoricalMember]
    ) -> dict[UUID, _LocalArtifactReceipt]:
        member_ids = [member.source_release_id for member in members]
        rows = (
            await session.execute(
                select(
                    Artifact.source_release_id,
                    Artifact.kind,
                    Artifact.uri,
                    Artifact.media_type,
                    Artifact.checksum_sha256,
                    Artifact.size_bytes,
                    Artifact.storage_class,
                    Artifact.metadata_json,
                    func.octet_length(Artifact.content_bytes).label("inline_size_bytes"),
                ).where(Artifact.source_release_id.in_(member_ids))
            )
        ).mappings()
        receipts: dict[UUID, _LocalArtifactReceipt] = {}
        for row in rows:
            release_id = row["source_release_id"]
            if not isinstance(release_id, UUID) or release_id in receipts:
                raise HistoricalPromotionError("historical source releases require exactly one source artifact")
            receipt = _LocalArtifactReceipt(
                source_release_id=release_id,
                kind=str(row["kind"]),
                uri=str(row["uri"]),
                media_type=row["media_type"],
                checksum_sha256=str(row["checksum_sha256"]),
                size_bytes=int(row["size_bytes"]),
                storage_class=str(row["storage_class"]),
                metadata=dict(row["metadata_json"]),
                inline_size_bytes=None if row["inline_size_bytes"] is None else int(row["inline_size_bytes"]),
            )
            receipts[release_id] = receipt
        for member in members:
            artifact = receipts.get(member.source_release_id)
            if (
                artifact is None
                or artifact.storage_class != "database_inline"
                or artifact.inline_size_bytes != artifact.size_bytes
                or artifact.size_bytes != member.release.payload_bytes
                or artifact.checksum_sha256 != member.release.payload_checksum
            ):
                raise HistoricalPromotionError("source artifact does not prove the validated local source payload")
        return receipts

    @staticmethod
    def _data_source_record(source: DataSource) -> HistoricalDataSourceRecord:
        if source.reviewed_at is None or source.reviewed_by is None:
            raise HistoricalPromotionError("approved source is missing review evidence")
        return HistoricalDataSourceRecord(
            source_key=source.key,
            name=source.name,
            owner=source.owner,
            purpose=source.purpose,
            base_url=source.base_url,
            license_name=source.license_name,
            license_url=source.license_url,
            citation=source.citation,
            retention_days=source.retention_days,
            reviewed_at=source.reviewed_at,
            reviewed_by=source.reviewed_by,
            configuration=dict(source.configuration),
        )

    @staticmethod
    def _source_release_record(member: _LocalHistoricalMember) -> HistoricalSourceReleaseRecord:
        release = member.release
        if release.validated_at is None or release.payload_bytes is None:
            raise HistoricalPromotionError("validated release is incomplete")
        return HistoricalSourceReleaseRecord(
            release=member.identity,
            retrieved_at=release.retrieved_at,
            data_available_at=release.data_available_at,
            observed_from=release.observed_from,
            observed_to=release.observed_to,
            payload_bytes=release.payload_bytes,
            schema_version=release.schema_version,
            license_snapshot=release.license_snapshot,
            query_parameters=dict(release.query_parameters),
            quality_summary=dict(release.quality_summary),
            validated_at=release.validated_at,
        )

    @staticmethod
    def _artifact_record(member: _LocalHistoricalMember, artifact: _LocalArtifactReceipt) -> HistoricalArtifactRecord:
        return HistoricalArtifactRecord(
            release=member.identity,
            uri=artifact.uri,
            media_type=artifact.media_type,
            checksum_sha256=artifact.checksum_sha256,
            size_bytes=artifact.size_bytes,
            metadata={**artifact.metadata, "source_artifact_kind": artifact.kind},
        )

    @staticmethod
    def _ordered_members(
        members: Iterable[_LocalHistoricalMember], source_key: str
    ) -> tuple[_LocalHistoricalMember, ...]:
        return tuple(
            sorted(
                (member for member in members if member.source.key == source_key),
                key=lambda member: historical_source_release_key(member.identity),
            )
        )

    async def _emit_grid_cells(
        self,
        session: AsyncSession,
        writer: HistoricalPromotionSpoolWriter,
        members: tuple[_LocalHistoricalMember, ...],
        source_key: str,
    ) -> None:
        release_ids = [member.source_release_id for member in members if member.source.key == source_key]
        statement = (
            select(
                SpatialCell.cell_key,
                SpatialCell.grid_name,
                SpatialCell.resolution_m,
                SpatialCell.coverage_fraction,
                func.ST_AsGeoJSON(SpatialCell.geometry).label("geometry_json"),
                func.ST_AsGeoJSON(SpatialCell.centroid).label("centroid_json"),
            )
            .join(CellSourceCrosswalk, CellSourceCrosswalk.cell_id == SpatialCell.id)
            .where(CellSourceCrosswalk.source_release_id.in_(release_ids))
            .order_by(SpatialCell.cell_key)
        )
        emitted: set[str] = set()
        result = await session.stream(statement)
        async for row in result.mappings():
            cell_key = str(row["cell_key"])
            if cell_key in emitted:
                continue
            emitted.add(cell_key)
            grid_name = str(row["grid_name"])
            resolution_m = int(row["resolution_m"])
            geometry_json = _canonical_geojson(row["geometry_json"])
            centroid_json = _canonical_geojson(row["centroid_json"])
            coverage_fraction = float(row["coverage_fraction"])
            writer.append(
                HistoricalNasaCellRecord(
                    cell_key=cell_key,
                    grid_name=grid_name,
                    resolution_m=resolution_m,
                    geometry_json=geometry_json,
                    centroid_json=centroid_json,
                    coverage_fraction=coverage_fraction,
                )
                if source_key == NASA_POWER_SOURCE_KEY
                else HistoricalEra5CellRecord(
                    cell_key=cell_key,
                    grid_name=grid_name,
                    resolution_m=resolution_m,
                    geometry_json=geometry_json,
                    centroid_json=centroid_json,
                    coverage_fraction=coverage_fraction,
                )
            )

    async def _emit_grid_crosswalks(
        self, session: AsyncSession, writer: HistoricalPromotionSpoolWriter, member: _LocalHistoricalMember
    ) -> None:
        statement = (
            select(
                SpatialCell.cell_key,
                CellSourceCrosswalk.native_feature_key,
                CellSourceCrosswalk.native_resolution_m,
                CellSourceCrosswalk.spatial_support_kind,
                CellSourceCrosswalk.mapping_method,
                CellSourceCrosswalk.coverage_fraction,
                CellSourceCrosswalk.metadata_json,
                func.ST_AsGeoJSON(CellSourceCrosswalk.native_geometry).label("native_geometry_json"),
            )
            .join(SpatialCell, SpatialCell.id == CellSourceCrosswalk.cell_id)
            .where(CellSourceCrosswalk.source_release_id == member.source_release_id)
            .order_by(CellSourceCrosswalk.native_feature_key, SpatialCell.cell_key)
        )
        result = await session.stream(statement)
        async for row in result.mappings():
            fields = {
                "release": member.identity,
                "cell_key": str(row["cell_key"]),
                "native_feature_key": str(row["native_feature_key"]),
                "native_geometry_json": _canonical_geojson(row["native_geometry_json"]),
                "native_resolution_m": row["native_resolution_m"],
                "spatial_support_kind": str(row["spatial_support_kind"]),
                "mapping_method": str(row["mapping_method"]),
                "coverage_fraction": float(row["coverage_fraction"]),
                "metadata": dict(row["metadata_json"]),
            }
            writer.append(
                HistoricalNasaCrosswalkRecord(**fields)
                if member.source.key == NASA_POWER_SOURCE_KEY
                else HistoricalEra5CrosswalkRecord(**fields)
            )

    async def _emit_grid_observations(
        self, session: AsyncSession, writer: HistoricalPromotionSpoolWriter, member: _LocalHistoricalMember
    ) -> None:
        statement = (
            select(SignalObservation, SpatialCell.cell_key)
            .join(SpatialCell, SpatialCell.id == SignalObservation.cell_id)
            .where(SignalObservation.source_release_id == member.source_release_id)
            .order_by(
                SpatialCell.cell_key,
                SignalObservation.signal_name,
                SignalObservation.source_parameter,
                SignalObservation.support_key,
                SignalObservation.observed_at,
            )
        )
        result = await session.stream(statement)
        async for observation, cell_key in result:
            fields = {
                "release": member.identity,
                "cell_key": cell_key,
                "signal_name": observation.signal_name,
                "source_parameter": observation.source_parameter,
                "support_key": observation.support_key,
                "observed_at": observation.observed_at,
                "valid_from": observation.valid_from,
                "valid_to": observation.valid_to,
                "data_available_at": observation.data_available_at,
                "original_value": observation.original_value,
                "original_unit": observation.original_unit,
                "normalized_value": observation.normalized_value,
                "normalized_unit": observation.normalized_unit,
                "quality_flag": observation.quality_flag,
                "coverage_fraction": observation.coverage_fraction,
                "is_observed": observation.is_observed,
                "metadata": dict(observation.metadata_json),
            }
            writer.append(
                HistoricalNasaObservationRecord(**fields)
                if member.source.key == NASA_POWER_SOURCE_KEY
                else HistoricalEra5ObservationRecord(**fields)
            )

    async def _emit_grid_coverage(
        self, session: AsyncSession, writer: HistoricalPromotionSpoolWriter, member: _LocalHistoricalMember
    ) -> None:
        statement = (
            select(SignalCoverageAudit, SpatialCell.cell_key)
            .join(SpatialCell, SpatialCell.id == SignalCoverageAudit.cell_id)
            .where(SignalCoverageAudit.source_release_id == member.source_release_id)
            .order_by(
                SpatialCell.cell_key,
                SignalCoverageAudit.signal_name,
                SignalCoverageAudit.source_parameter,
                SignalCoverageAudit.support_key,
                SignalCoverageAudit.window_start,
                SignalCoverageAudit.window_end,
            )
        )
        result = await session.stream(statement)
        async for audit, cell_key in result:
            fields = {
                "release": member.identity,
                "cell_key": cell_key,
                "signal_name": audit.signal_name,
                "source_parameter": audit.source_parameter,
                "support_key": audit.support_key,
                "window_start": audit.window_start,
                "window_end": audit.window_end,
                "expected_observation_count": audit.expected_observation_count,
                "received_observation_count": audit.received_observation_count,
                "status": audit.status,
                "details": dict(audit.details),
            }
            writer.append(
                HistoricalNasaCoverageAuditRecord(**fields)
                if member.source.key == NASA_POWER_SOURCE_KEY
                else HistoricalEra5CoverageAuditRecord(**fields)
            )

    async def _emit_usdm_polygons(
        self, session: AsyncSession, writer: HistoricalPromotionSpoolWriter, member: _LocalHistoricalMember
    ) -> None:
        statement = select(
            DroughtPolygonSnapshot.issue_date,
            DroughtPolygonSnapshot.severity_class,
            DroughtPolygonSnapshot.impact_type,
            DroughtPolygonSnapshot.geometry_checksum,
            DroughtPolygonSnapshot.data_available_at,
            DroughtPolygonSnapshot.metadata_json,
            func.ST_AsGeoJSON(DroughtPolygonSnapshot.geometry).label("geometry_json"),
        ).where(DroughtPolygonSnapshot.source_release_id == member.source_release_id)
        values: list[HistoricalUsdmPolygonRecord] = []
        result = await session.stream(statement)
        async for row in result.mappings():
            metadata = dict(row["metadata_json"])
            feature_key = metadata.get("feature_key")
            if not isinstance(feature_key, str) or not feature_key:
                raise HistoricalPromotionError("USDM polygon is missing its native feature key")
            if row["impact_type"] != "none":
                raise HistoricalPromotionError("USDM polygon has an unsupported impact type")
            geometry_json = _canonical_geojson(row["geometry_json"])
            values.append(
                HistoricalUsdmPolygonRecord(
                    release=member.identity,
                    issue_date=row["issue_date"],
                    feature_key=feature_key,
                    severity_class=int(row["severity_class"]),
                    impact_type="none",
                    geometry_json=geometry_json,
                    geometry_checksum=hashlib.sha256(geometry_json.encode()).hexdigest(),
                    data_available_at=row["data_available_at"],
                    metadata={**metadata, "source_geometry_checksum": str(row["geometry_checksum"])},
                )
            )
        for record in sorted(values, key=historical_record_key):
            writer.append(record)

    async def _emit_usdm_coverage(
        self, session: AsyncSession, writer: HistoricalPromotionSpoolWriter, member: _LocalHistoricalMember
    ) -> None:
        statement = (
            select(SourceCoverageAudit)
            .where(SourceCoverageAudit.source_release_id == member.source_release_id)
            .order_by(SourceCoverageAudit.scope_key, SourceCoverageAudit.window_start, SourceCoverageAudit.window_end)
        )
        result = await session.stream_scalars(statement)
        async for audit in result:
            if audit.status not in _COVERAGE_STATUSES:
                raise HistoricalPromotionError("USDM coverage has an unsupported status")
            writer.append(
                HistoricalUsdmCoverageAuditRecord(
                    release=member.identity,
                    scope_key=audit.scope_key,
                    window_start=audit.window_start,
                    window_end=audit.window_end,
                    expected_feature_count=audit.expected_feature_count,
                    received_feature_count=audit.received_feature_count,
                    status=cast("Literal['complete', 'partial', 'no_data', 'failed']", audit.status),
                    details=dict(audit.details),
                )
            )

    async def _artifact_bytes(self, session: AsyncSession, identity: HistoricalSourceReleaseIdentity) -> bytes:
        rows = (
            await session.execute(
                select(Artifact.content_bytes, Artifact.checksum_sha256, Artifact.size_bytes, Artifact.storage_class)
                .join(SourceRelease, SourceRelease.id == Artifact.source_release_id)
                .join(DataSource, DataSource.id == SourceRelease.data_source_id)
                .where(
                    DataSource.key == identity.source_key,
                    SourceRelease.source_version == identity.source_version,
                    SourceRelease.payload_checksum == identity.payload_checksum,
                    SourceRelease.transform_version == identity.transform_version,
                )
            )
        ).all()
        if len(rows) != 1:
            raise HistoricalPromotionError("local raw artifact identity is ambiguous")
        content, checksum, size_bytes, storage_class = rows[0]
        if not isinstance(content, bytes) or storage_class != "database_inline":
            raise HistoricalPromotionError("local raw artifact content is unavailable")
        if len(content) != size_bytes or hashlib.sha256(content).hexdigest() != checksum:
            raise HistoricalPromotionError("local raw artifact content no longer matches its receipt")
        return content


def _canonical_geojson(value: object) -> str:
    if not isinstance(value, str):
        raise HistoricalPromotionError("warehouse geometry could not be serialized as GeoJSON")
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise HistoricalPromotionError("warehouse geometry is not valid GeoJSON") from exc
    return canonical_json_bytes(parsed).decode("utf-8")
