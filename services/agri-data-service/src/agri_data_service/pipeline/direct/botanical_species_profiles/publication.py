"""Build and verify immutable, release-pinned botanical profile Parquet artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from pydantic import ValidationError

from agri_data_service.warehouse.botanical_species_profiles.contract import (
    MAX_ASSERTIONS,
    MAX_OBJECT_BYTES,
    MAX_RELEASE_BYTES,
    MAX_TAXA,
    SCHEMA_VERSION,
    TRAIT_SECTIONS,
    ArtifactReceipt,
    FrozenModel,
    ProfileConflictError,
    ProfileError,
    ProfileIntegrityError,
    ProfileUnavailableError,
    PublishedRelease,
    ReconciliationDecision,
    ReleaseManifest,
    ReleaseRequest,
    SpeciesProfile,
    TaxonRecord,
    TraitAssertion,
    canonical_bytes,
    content_sha256,
    require_release_id,
)
from agri_data_service.warehouse.botanical_species_profiles.reconcile import canonical_request, reconcile
from agri_data_service.warehouse.schemas.botanical_species_profile import (
    ASSERTIONS_SCHEMA,
    DECISIONS_SCHEMA,
    MANIFEST_SCHEMA,
    PROFILES_SCHEMA,
    TAXA_SCHEMA,
)

if TYPE_CHECKING:
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage

LOOKUP_ROOT = "lookups/botanical-species-profile"
CURRENT_POINTER_KEY = f"{LOOKUP_ROOT}/current.json"
MAX_POINTER_BYTES = 4096
PARQUET_CONTENT_TYPE = "application/vnd.apache.parquet"
_ARTIFACT_SCHEMAS = {
    "taxa": TAXA_SCHEMA,
    "assertions": ASSERTIONS_SCHEMA,
    "decisions": DECISIONS_SCHEMA,
    "profiles": PROFILES_SCHEMA,
}
_ARTIFACT_MAX_ROWS = {
    "taxa": MAX_TAXA,
    "assertions": MAX_ASSERTIONS,
    "decisions": MAX_TAXA * len(TRAIT_SECTIONS),
    "profiles": MAX_TAXA,
    "manifest": 1,
}


@dataclass(frozen=True, slots=True)
class ReleaseArtifact:
    """One exact immutable artifact payload."""

    name: str
    key: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class ReleaseBundle:
    """A locally built release ready for immutable creation and conditional publication."""

    release: PublishedRelease
    artifacts: tuple[ReleaseArtifact, ...]

    @property
    def release_id(self) -> str:
        """Expose the content-bound release identity."""
        return self.release.release_id


@dataclass(frozen=True, slots=True)
class PublicationReceipt:
    """Verified artifact inventory and conditional pointer outcome."""

    release_id: str
    pointer_etag: str
    state: Literal["published", "replayed", "rolled_back"]
    artifact_count: int


def artifact_key(release_id: str, name: str) -> str:
    """Construct a key only for a known artifact and an explicitly pinned release."""
    require_release_id(release_id)
    if name not in {*_ARTIFACT_SCHEMAS, "manifest"}:
        raise ProfileError("unknown botanical profile artifact")
    return f"{LOOKUP_ROOT}/releases/{release_id}/{name}.parquet"


def build_release(request: ReleaseRequest) -> ReleaseBundle:
    """Build all five deterministic Parquet artifacts from one reviewed authoring snapshot."""
    request = canonical_request(request)
    input_sha256 = content_sha256(canonical_bytes(request.model_dump(mode="json")))
    release_id = "bspf-" + input_sha256
    decisions, profiles = reconcile(request)
    collections: dict[str, tuple[FrozenModel, ...]] = {
        "taxa": request.taxa,
        "assertions": request.assertions,
        "decisions": decisions,
        "profiles": profiles,
    }
    artifacts = tuple(
        ReleaseArtifact(
            name=name,
            key=artifact_key(release_id, name),
            payload=_encode(
                rows,
                _ARTIFACT_SCHEMAS[name],
                release_id,
                name,
            ),
        )
        for name, rows in collections.items()
    )
    manifest = ReleaseManifest(
        release_id=release_id,
        source_releases=request.sources,
        normalization_recipe=request.normalization_recipe,
        reconciliation_policy=request.reconciliation_policy,
        reviewer=request.reviewer,
        review_decision_id=request.review_decision_id,
        reviewed_at=request.reviewed_at,
        authoring_census_state=request.authoring_census_state,
        authoring_census_note=request.authoring_census_note,
        input_sha256=input_sha256,
        artifacts=tuple(
            ArtifactReceipt.model_validate(
                {
                    "name": artifact.name,
                    "key": artifact.key,
                    "sha256": content_sha256(artifact.payload),
                    "byte_count": len(artifact.payload),
                    "row_count": len(collections[artifact.name]),
                }
            )
            for artifact in artifacts
        ),
    )
    artifacts += (
        ReleaseArtifact(
            name="manifest",
            key=artifact_key(release_id, "manifest"),
            payload=_encode((manifest,), MANIFEST_SCHEMA, release_id, "manifest"),
        ),
    )
    if sum(len(artifact.payload) for artifact in artifacts) > MAX_RELEASE_BYTES:
        raise ProfileError("botanical release exceeds the total byte ceiling")
    return ReleaseBundle(
        release=PublishedRelease(
            release_id=release_id,
            manifest=manifest,
            taxa=request.taxa,
            assertions=request.assertions,
            decisions=decisions,
            profiles=profiles,
        ),
        artifacts=artifacts,
    )


def _encode(rows: tuple[FrozenModel, ...], schema: pa.Schema, release_id: str, name: str) -> bytes:
    metadata = {
        b"botanical.schema_version": SCHEMA_VERSION.encode(),
        b"botanical.release_id": release_id.encode(),
        b"botanical.artifact": name.encode(),
    }
    table = pa.Table.from_pylist([row.model_dump(mode="python") for row in rows], schema=schema.with_metadata(metadata))
    if table.nbytes > MAX_OBJECT_BYTES:
        raise ProfileError("botanical artifact exceeds the decoded byte ceiling")
    sink = pa.BufferOutputStream()
    pq.write_table(table, sink, compression="zstd", version="2.6", use_dictionary=False, write_statistics=False)
    payload: bytes = sink.getvalue().to_pybytes()
    if len(payload) > MAX_OBJECT_BYTES:
        raise ProfileError("botanical artifact exceeds the encoded byte ceiling")
    return payload


def _decode[ModelT: FrozenModel](
    payload: bytes,
    schema: pa.Schema,
    release_id: str,
    name: str,
    model: type[ModelT],
) -> tuple[ModelT, ...]:
    try:
        parquet = pq.ParquetFile(pa.BufferReader(payload))
        metadata = parquet.schema_arrow.metadata or {}
        if (
            not parquet.schema_arrow.equals(schema, check_metadata=False)
            or metadata.get(b"botanical.release_id") != release_id.encode()
            or metadata.get(b"botanical.schema_version") != SCHEMA_VERSION.encode()
            or metadata.get(b"botanical.artifact") != name.encode()
        ):
            raise ProfileIntegrityError("botanical artifact schema or pinned metadata does not match")
        if parquet.metadata.num_rows > _ARTIFACT_MAX_ROWS[name]:
            raise ProfileIntegrityError("botanical artifact exceeds its row ceiling")
        decoded_size = sum(parquet.metadata.row_group(index).total_byte_size for index in range(parquet.num_row_groups))
        if decoded_size > MAX_OBJECT_BYTES:
            raise ProfileIntegrityError("botanical artifact exceeds its decoded byte ceiling")
        table = parquet.read(use_threads=False)
        if table.nbytes > MAX_OBJECT_BYTES:
            raise ProfileIntegrityError("decoded botanical artifact exceeds its byte ceiling")
        return tuple(model.model_validate(row) for row in table.to_pylist())
    except (pa.ArrowException, ValidationError, TypeError, KeyError) as exc:
        raise ProfileIntegrityError("malformed botanical Parquet artifact") from exc


def _read_payload(storage: AvailabilityStorage, key: str) -> bytes:
    result = storage.read(key, max_bytes=MAX_OBJECT_BYTES)
    if result is None:
        raise ProfileUnavailableError("the pinned botanical release is absent or incomplete")
    if len(result.payload) > MAX_OBJECT_BYTES:
        raise ProfileIntegrityError("botanical storage violated the read byte ceiling")
    return result.payload


def read_release(storage: AvailabilityStorage, release_id: str) -> PublishedRelease:
    """Read a pinned release and verify all artifacts, source identities and reconciliation."""
    require_release_id(release_id)
    manifest_payload = _read_payload(storage, artifact_key(release_id, "manifest"))
    rows = _decode(manifest_payload, MANIFEST_SCHEMA, release_id, "manifest", ReleaseManifest)
    if len(rows) != 1:
        raise ProfileIntegrityError("release manifest must contain exactly one row")
    manifest = rows[0]
    if manifest.release_id != release_id or "bspf-" + manifest.input_sha256 != release_id:
        raise ProfileIntegrityError("release manifest identity disagrees with the pinned release")
    if tuple(receipt.name for receipt in manifest.artifacts) != tuple(_ARTIFACT_SCHEMAS):
        raise ProfileIntegrityError("release manifest must identify all four data artifacts exactly once")
    payloads: dict[str, bytes] = {}
    for receipt in manifest.artifacts:
        if receipt.key != artifact_key(release_id, receipt.name):
            raise ProfileIntegrityError("artifact receipt escaped the pinned release prefix")
        payload = _read_payload(storage, receipt.key)
        if len(payload) != receipt.byte_count or content_sha256(payload) != receipt.sha256:
            raise ProfileIntegrityError("botanical artifact checksum or byte count does not match")
        payloads[receipt.name] = payload
    if len(manifest_payload) + sum(map(len, payloads.values())) > MAX_RELEASE_BYTES:
        raise ProfileIntegrityError("botanical release exceeds its total byte ceiling")
    try:
        decoded_bytes = 0
        for payload in (manifest_payload, *payloads.values()):
            metadata = pq.ParquetFile(pa.BufferReader(payload)).metadata
            decoded_bytes += sum(metadata.row_group(index).total_byte_size for index in range(metadata.num_row_groups))
        if decoded_bytes > MAX_RELEASE_BYTES:
            raise ProfileIntegrityError("botanical release exceeds its cumulative decoded byte ceiling")
    except pa.ArrowException as error:
        raise ProfileIntegrityError("malformed botanical release metadata") from error
    return _verify_release(manifest, payloads)


def _verify_release(manifest: ReleaseManifest, payloads: dict[str, bytes]) -> PublishedRelease:
    release_id = manifest.release_id
    taxa = _decode(payloads["taxa"], TAXA_SCHEMA, release_id, "taxa", TaxonRecord)
    assertions = _decode(payloads["assertions"], ASSERTIONS_SCHEMA, release_id, "assertions", TraitAssertion)
    decisions = _decode(payloads["decisions"], DECISIONS_SCHEMA, release_id, "decisions", ReconciliationDecision)
    profiles = _decode(payloads["profiles"], PROFILES_SCHEMA, release_id, "profiles", SpeciesProfile)
    counts = {"taxa": len(taxa), "assertions": len(assertions), "decisions": len(decisions), "profiles": len(profiles)}
    if any(counts[receipt.name] != receipt.row_count for receipt in manifest.artifacts):
        raise ProfileIntegrityError("botanical artifact row counts differ from the manifest")
    try:
        request = canonical_request(
            ReleaseRequest.model_validate(
                {
                    "taxa": taxa,
                    "assertions": assertions,
                    "sources": manifest.source_releases,
                    "reviewer": manifest.reviewer,
                    "review_decision_id": manifest.review_decision_id,
                    "reviewed_at": manifest.reviewed_at,
                    "authoring_census_state": manifest.authoring_census_state,
                    "authoring_census_note": manifest.authoring_census_note,
                    "normalization_recipe": manifest.normalization_recipe,
                    "reconciliation_policy": manifest.reconciliation_policy,
                }
            )
        )
        input_sha256 = content_sha256(canonical_bytes(request.model_dump(mode="json")))
        expected_decisions, expected_profiles = reconcile(request)
    except (ValueError, ValidationError) as exc:
        raise ProfileIntegrityError("botanical release contains an invalid reviewed authoring snapshot") from exc
    if input_sha256 != manifest.input_sha256 or decisions != expected_decisions or profiles != expected_profiles:
        raise ProfileIntegrityError("botanical release identity or reconciliation differs from its reviewed inputs")
    return PublishedRelease(
        release_id=release_id,
        manifest=manifest,
        taxa=taxa,
        assertions=assertions,
        decisions=decisions,
        profiles=profiles,
    )


def publish_release(
    storage: AvailabilityStorage,
    bundle: ReleaseBundle,
    *,
    expected_pointer_etag: str | None,
) -> PublicationReceipt:
    """Read-verify every immutable artifact before conditionally advancing the current pointer."""
    expected_names = (*_ARTIFACT_SCHEMAS, "manifest")
    if tuple(artifact.name for artifact in bundle.artifacts) != expected_names:
        raise ProfileError("publication requires all five exact release artifacts")
    for artifact in bundle.artifacts:
        if artifact.key != artifact_key(bundle.release_id, artifact.name):
            raise ProfileError("publication artifact key does not belong to this release")
        storage.put_immutable(artifact.key, artifact.payload, content_type=PARQUET_CONTENT_TYPE)
    verified = read_release(storage, bundle.release_id)
    if verified != bundle.release:
        raise ProfileIntegrityError("read-back release differs from the built authoring snapshot")
    return _move_pointer(storage, bundle.release_id, expected_pointer_etag, rollback=False)


def rollback_release(
    storage: AvailabilityStorage,
    release_id: str,
    *,
    expected_pointer_etag: str,
) -> PublicationReceipt:
    """Point conditionally to a completely verified prior release without mutating artifacts."""
    read_release(storage, release_id)
    return _move_pointer(storage, release_id, expected_pointer_etag, rollback=True)


def _move_pointer(
    storage: AvailabilityStorage,
    release_id: str,
    expected_etag: str | None,
    *,
    rollback: bool,
) -> PublicationReceipt:
    payload = canonical_bytes({"release_id": release_id, "schema_version": SCHEMA_VERSION})
    current = storage.read(CURRENT_POINTER_KEY, max_bytes=MAX_POINTER_BYTES)
    if current is not None and current.payload == payload:
        return PublicationReceipt(release_id, current.etag, "replayed", len(_ARTIFACT_SCHEMAS) + 1)
    if not storage.compare_and_swap(
        CURRENT_POINTER_KEY,
        payload,
        expected_etag=expected_etag,
        content_type="application/json",
    ):
        raise ProfileConflictError("current botanical release changed; inspect the new pointer before retrying")
    observed = storage.read(CURRENT_POINTER_KEY, max_bytes=MAX_POINTER_BYTES)
    if observed is None or observed.payload != payload:
        raise ProfileConflictError("botanical release pointer changed before publication could be confirmed")
    return PublicationReceipt(release_id, observed.etag, "rolled_back" if rollback else "published", 5)


def read_current_pointer(storage: AvailabilityStorage) -> tuple[str, str] | None:
    """Inspect publication state for an explicit operator CAS; serving must still pass a pinned ID."""
    current = storage.read(CURRENT_POINTER_KEY, max_bytes=MAX_POINTER_BYTES)
    if current is None:
        return None
    try:
        pointer = json.loads(current.payload)
        if not isinstance(pointer, dict) or set(pointer) != {"release_id", "schema_version"}:
            raise ProfileIntegrityError("malformed botanical release pointer")
        if pointer["schema_version"] != SCHEMA_VERSION:
            raise ProfileIntegrityError("unsupported botanical pointer schema")
        return require_release_id(pointer["release_id"]), current.etag
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProfileIntegrityError("malformed botanical release pointer") from exc
