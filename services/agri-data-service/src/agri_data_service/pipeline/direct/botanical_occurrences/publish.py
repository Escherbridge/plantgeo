"""Conditional publication of one immutable generation: every artifact durable, then one pointer.

THE ORDER IS THE CONTRACT. Parts, then `manifest.json`, then `_COMPLETE`, and last of all the
checksum-bound `availability/_LATEST.json` a serving read resolves (layer-lanes §4a). ONE pointer:
the unbound `current.json` this lane also wrote was retired 2026-09-18 once production had been on
`_LATEST.json` for a day (`AGENTS.md`, "The current pointer"). A process killed anywhere before the
marker leaves a generation directory that no reader will open and a pointer that still names the
previous generation, which is what makes interrupted publication recoverable rather than corrupting.
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Protocol

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from agri_data_service.config import settings as default_settings
from agri_data_service.pipeline.direct.botanical_occurrences.pointer import (
    LATEST_POINTER_NAME,
    build_latest_pointer,
    encode_latest_pointer,
)
from agri_data_service.pipeline.parquet.objectstore import conform_to_stream_schema
from agri_data_service.warehouse.schemas.botanical_occurrences import (
    BOTANICAL_CELL_TAXON_SUMMARY_SCHEMA,
    BOTANICAL_IDENTIFICATION_SCHEMA,
    BOTANICAL_NORMALIZED_OCCURRENCE_SCHEMA,
    BOTANICAL_RAW_OCCURRENCE_SCHEMA,
    BOTANICAL_SOURCE_RELEASE_SCHEMA,
    BOTANICAL_SPATIAL_ASSOCIATION_SCHEMA,
    BOTANICAL_SUPPORT_EVALUATION_SCHEMA,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from agri_data_service.config import Settings
    from agri_data_service.foundation.botanical_occurrences.release_identity import ReleaseSetIdentity
    from agri_data_service.pipeline.direct.botanical_occurrences.pointer import BotanicalLatestPointer
    from agri_data_service.pipeline.parquet.objectstore import ObjectStoreBackend
    from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema

#: The lane's own prefix under the object-store root, beside `layer=`-partitioned lanes rather than
#: inside them: this lane's unit is a release set, not a day, so the frozen day layout cannot hold it.
LANE_PREFIX: Final = "botanical-occurrences"
MANIFEST_NAME: Final = "manifest.json"
COMPLETION_MARKER: Final = "_COMPLETE"
PART_NAME: Final = "part-0000.parquet"

#: Matches `warehouse/parquet/schema.py::DEFAULT_PARQUET_COMPRESSION`, measured on the signal plane.
PARQUET_COMPRESSION: Final = "zstd"


class PublicationTarget(Protocol):
    """The three operations a generation needs: read one object, write one object, test for one."""

    def read_bytes(self, relative_path: str) -> bytes | None: ...

    def write_bytes(self, relative_path: str, payload: bytes) -> None: ...

    def exists(self, relative_path: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class LocalPublicationTarget:
    """A filesystem root. What the tests publish into, and what a local pilot would use."""

    root: Path

    def _path(self, relative_path: str) -> Path:
        return self.root / relative_path

    def read_bytes(self, relative_path: str) -> bytes | None:
        path = self._path(relative_path)
        return path.read_bytes() if path.is_file() else None

    def write_bytes(self, relative_path: str, payload: bytes) -> None:
        path = self._path(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    def exists(self, relative_path: str) -> bool:
        return self._path(relative_path).is_file()


@dataclass(frozen=True, slots=True)
class ObjectStorePublicationTarget:
    """The bucket, through the SAME backend `ObjectStore` uses, so credentials resolve one way only."""

    backend: ObjectStoreBackend
    prefix: str = ""

    def _key(self, relative_path: str) -> str:
        head = f"{self.prefix.strip('/')}/" if self.prefix.strip("/") else ""
        return f"{head}{relative_path}"

    def read_bytes(self, relative_path: str) -> bytes | None:
        return self.backend.get(self._key(relative_path))

    def write_bytes(self, relative_path: str, payload: bytes) -> None:
        self.backend.put(self._key(relative_path), payload, content_type="application/octet-stream")

    def exists(self, relative_path: str) -> bool:
        return self.backend.size_of(self._key(relative_path)) is not None


def publication_target(root: str | Path | None = None, *, source: Settings | None = None) -> PublicationTarget:
    """Resolve a target from a local path, or from the configured bucket when none is given."""
    if root is not None and not str(root).startswith("s3://"):
        return LocalPublicationTarget(Path(root))
    from agri_data_service.pipeline.parquet.objectstore import (  # noqa: PLC0415 - only needed for the bucket path
        BotoObjectStoreBackend,
    )

    resolved = default_settings if source is None else source
    credentials = resolved.require_object_store()
    return ObjectStorePublicationTarget(
        BotoObjectStoreBackend.from_credentials(credentials), prefix=resolved.object_store_prefix
    )


def generation_prefix(release_set_id: str) -> str:
    """Return the prefix every artifact of one generation lives under."""
    return f"{LANE_PREFIX}/{release_set_id}"


def latest_pointer_path() -> str:
    """Return the checksum-bound pointer a serving read resolves (layer-lanes §4a)."""
    return f"{LANE_PREFIX}/{LATEST_POINTER_NAME}"


def write_latest_pointer(
    target: PublicationTarget,
    release_set_id: str,
    *,
    manifest_bytes: bytes,
    published_at: str | None,
) -> BotanicalLatestPointer:
    """Write the checksum-bound pointer naming one already-durable generation as current."""
    pointer = build_latest_pointer(
        product=LANE_PREFIX,
        generation_id=release_set_id,
        manifest_key=f"{generation_prefix(release_set_id)}/{MANIFEST_NAME}",
        manifest_bytes=manifest_bytes,
        published_at=published_at,
    )
    target.write_bytes(latest_pointer_path(), encode_latest_pointer(pointer))
    return pointer


def advance_latest_pointer(target: PublicationTarget, release_set_id: str) -> BotanicalLatestPointer | None:
    """Re-derive and write the checksum-bound pointer for an ALREADY published generation.

    The upgrade path for a bucket published before §4a's pointer existed: it reads the generation's
    own manifest for the digest rather than trusting a caller-supplied one, and refuses a generation
    that is not complete, so an upgrade can never make an unfinished directory selectable.
    """
    prefix = generation_prefix(release_set_id)
    if not target.exists(f"{prefix}/{COMPLETION_MARKER}"):
        return None
    manifest_bytes = target.read_bytes(f"{prefix}/{MANIFEST_NAME}")
    if manifest_bytes is None:
        return None
    manifest: dict[str, Any] = json.loads(manifest_bytes)
    published_at = manifest.get("published_at")
    return write_latest_pointer(
        target,
        release_set_id,
        manifest_bytes=manifest_bytes,
        published_at=None if published_at is None else str(published_at),
    )


@dataclass(frozen=True, slots=True)
class GenerationContents:
    """Everything one generation publishes, already shaped as row dicts for its stream schema."""

    releases: Sequence[Mapping[str, Any]]
    raw_occurrences: Sequence[Mapping[str, Any]]
    identifications: Sequence[Mapping[str, Any]]
    occurrences: Sequence[Mapping[str, Any]]
    nonspatial_occurrences: Sequence[Mapping[str, Any]]
    associations: Sequence[Mapping[str, Any]]
    #: support_id -> that support's evaluated cells, and its sparse (cell, concept) summary.
    support_cells: Mapping[str, Sequence[Mapping[str, Any]]]
    cell_taxon_summaries: Mapping[str, Sequence[Mapping[str, Any]]]


@dataclass(frozen=True, slots=True)
class PublishReceipt:
    """What a publish call did: the generation it names, the objects it wrote, and whether it replayed."""

    release_set_id: str
    outcome: str
    objects_written: tuple[str, ...]
    pointer_advanced: bool

    def as_json(self) -> str:
        """Render the receipt as the JSON line the CLI prints."""
        return json.dumps(
            {
                "release_set_id": self.release_set_id,
                "outcome": self.outcome,
                "objects_written": list(self.objects_written),
                "pointer_advanced": self.pointer_advanced,
            },
            sort_keys=True,
        )


def _parquet_bytes(rows: Iterable[Mapping[str, Any]], stream: ParquetStreamSchema) -> bytes:
    """Serialise rows against their stream's storage contract, refusing anything that does not fit."""
    table = pa.Table.from_pylist(list(rows), schema=stream.arrow_schema)
    conformed = conform_to_stream_schema(table, stream)
    buffer = io.BytesIO()
    pq.write_table(conformed, buffer, compression=PARQUET_COMPRESSION, write_statistics=True)
    return buffer.getvalue()


def publish_generation(
    target: PublicationTarget,
    identity: ReleaseSetIdentity,
    contents: GenerationContents,
    *,
    advance_pointer: bool = True,
) -> PublishReceipt:
    """Write one generation durably, then advance the pointer; replay the same id as a no-op.

    IDEMPOTENT BY IDENTITY, not by timestamp. A release set's id is a function of its releases and
    its three recipes, so re-running the same inputs addresses the same directory; if that directory
    already carries `_COMPLETE`, nothing is rewritten and the receipt says `idempotent_noop`. That is
    what makes a retried turn safe after a crash of unknown depth.
    """
    prefix = generation_prefix(identity.release_set_id)
    if target.exists(f"{prefix}/{COMPLETION_MARKER}"):
        return PublishReceipt(identity.release_set_id, "idempotent_noop", (), pointer_advanced=False)

    written: list[str] = []

    def write(relative_path: str, payload: bytes) -> None:
        target.write_bytes(relative_path, payload)
        written.append(relative_path)

    write(
        f"{prefix}/releases/{PART_NAME}",
        _parquet_bytes(contents.releases, BOTANICAL_SOURCE_RELEASE_SCHEMA),
    )
    write(
        f"{prefix}/raw/{PART_NAME}",
        _parquet_bytes(contents.raw_occurrences, BOTANICAL_RAW_OCCURRENCE_SCHEMA),
    )
    write(
        f"{prefix}/identifications/{PART_NAME}",
        _parquet_bytes(contents.identifications, BOTANICAL_IDENTIFICATION_SCHEMA),
    )
    write(
        f"{prefix}/occurrences/{PART_NAME}",
        _parquet_bytes(contents.occurrences, BOTANICAL_NORMALIZED_OCCURRENCE_SCHEMA),
    )
    # A separate artifact, not a filtered read: a nonspatial record must never be reachable by a
    # bbox query, and the cheapest way to guarantee that is for it not to be in the spatial file.
    write(
        f"{prefix}/nonspatial/{PART_NAME}",
        _parquet_bytes(contents.nonspatial_occurrences, BOTANICAL_NORMALIZED_OCCURRENCE_SCHEMA),
    )
    write(
        f"{prefix}/associations/{PART_NAME}",
        _parquet_bytes(contents.associations, BOTANICAL_SPATIAL_ASSOCIATION_SCHEMA),
    )
    for support_id, cells in sorted(contents.support_cells.items()):
        write(
            f"{prefix}/support/{support_id}/cells.parquet", _parquet_bytes(cells, BOTANICAL_SUPPORT_EVALUATION_SCHEMA)
        )
    for support_id, summaries in sorted(contents.cell_taxon_summaries.items()):
        write(
            f"{prefix}/summary/{support_id}/cell_taxon.parquet",
            _parquet_bytes(summaries, BOTANICAL_CELL_TAXON_SUMMARY_SCHEMA),
        )

    manifest = {
        "release_set_id": identity.release_set_id,
        "release_keys": list(identity.release_keys),
        "taxonomy_recipe_version": identity.taxonomy_recipe_version,
        "qc_policy_version": identity.qc_policy_version,
        "support_version": identity.support_version,
        "supports": sorted(contents.support_cells),
        "counts": {
            "releases": len(contents.releases),
            "raw_occurrences": len(contents.raw_occurrences),
            "identifications": len(contents.identifications),
            "occurrences": len(contents.occurrences),
            "nonspatial_occurrences": len(contents.nonspatial_occurrences),
            "associations": len(contents.associations),
        },
        "published_at": datetime.now(UTC).isoformat(),
        "artifacts": sorted(written),
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
    write(f"{prefix}/{MANIFEST_NAME}", manifest_bytes)
    # LAST. Everything above must be durable before anything is allowed to read this directory.
    write(f"{prefix}/{COMPLETION_MARKER}", b"")

    pointer_advanced = False
    if advance_pointer:
        # The checksum-bound pointer advances LAST of everything, per layer-lanes §4a: it is the
        # object a serving read trusts, so it must never name a generation whose manifest is not
        # already durable.
        write_latest_pointer(
            target,
            identity.release_set_id,
            manifest_bytes=manifest_bytes,
            published_at=str(manifest["published_at"]),
        )
        written.append(latest_pointer_path())
        pointer_advanced = True
    return PublishReceipt(identity.release_set_id, "published", tuple(written), pointer_advanced=pointer_advanced)


def read_manifest(target: PublicationTarget, release_set_id: str) -> dict[str, Any] | None:
    """Return a COMPLETE generation's manifest, or None when it is absent or unfinished.

    The completion marker is checked first and the manifest is not trusted without it: a manifest
    written by a turn that then died is a description of a generation that was never finished.
    """
    prefix = generation_prefix(release_set_id)
    if not target.exists(f"{prefix}/{COMPLETION_MARKER}"):
        return None
    payload = target.read_bytes(f"{prefix}/{MANIFEST_NAME}")
    if payload is None:
        return None
    loaded: dict[str, Any] = json.loads(payload)
    return loaded


__all__ = [
    "COMPLETION_MARKER",
    "LANE_PREFIX",
    "LATEST_POINTER_NAME",
    "MANIFEST_NAME",
    "PART_NAME",
    "GenerationContents",
    "LocalPublicationTarget",
    "ObjectStorePublicationTarget",
    "PublicationTarget",
    "PublishReceipt",
    "advance_latest_pointer",
    "generation_prefix",
    "latest_pointer_path",
    "publication_target",
    "publish_generation",
    "read_manifest",
    "write_latest_pointer",
]
