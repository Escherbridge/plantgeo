"""Client-side GHISACONUS lineage loader for the model-delivery public-evaluation track.

Executes the same logical inserts as the vetted-but-blocked
`conductor/tracks/model_delivery_public_evaluation_20260726/plantgeo-retain-ghisaconus-vetted-2026-08-14.sql`
(data_source -> source_release -> release_set -> release_set_item -> 3 artifacts),
but reads the three frozen local files as client-side bytes rather than through
`pg_read_binary_file`, which cannot reach a remote server's filesystem. All three
artifacts use `storage_class = 'local_raw_cache'` with `content_bytes = NULL`,
the same reference-plus-checksum mode `historical_writer/era5.py`'s
`_ensure_era5_artifact` uses for its ZIPs -- `agri.artifact`'s
`ck_artifact_inline_artifact_has_content` CHECK only demands `content_bytes` when
`storage_class = 'database_inline'`, so no payload ever crosses the wire here.
See `lineage-receipt-2026-08-14.md` in the track directory for the storage-mode
rationale and the executed run's row counts and keys, and
`conductor/retros/restoration_ag_demo_20260726/receipts/kaggle-ghisaconus-v1.json`
for the already-reviewed intake receipt this loader's fixed literals are drawn from.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.execution.provenance import (
    advisory_lock,
    ensure_artifact,
    ensure_data_source,
    ensure_source_release,
    find_release_set,
    release_set_member_ids,
)
from agri_data_service.execution.public_evaluation_rehash import (
    DEFAULT_GHISACONUS_CSV_PATH,
    GHISACONUS_CSV_EXPECTED_SHA256,
    FrozenInputRehash,
    rehash_frozen_input,
)
from agri_data_service.foundation.canonical import utc_now
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

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

EXPECTED_ARTIFACT_COUNT = 3

DEFAULT_GHISACONUS_ARCHIVE_PATH = Path(r"C:\tmp\plantgeo-kaggle-ghisaconus-v1.zip")
DEFAULT_GHISACONUS_METADATA_PATH = Path(r"C:\tmp\plantgeo-kaggle-ghisaconus-metadata.json")

# Digests bound in the vetted SQL / the accepted receipt
# (conductor/retros/restoration_ag_demo_20260726/receipts/kaggle-ghisaconus-v1.json).
GHISACONUS_ARCHIVE_EXPECTED_SHA256 = "3bb701ab61eb2069c09ae7cc9cb66fbd0995165f127478af2b6afb9d406abac0"
GHISACONUS_METADATA_EXPECTED_SHA256 = "42480207bdeed7b40753637f4b24b07d44232ba4ed6f8455fabdecd4c1b6b220"

# Fixed instants carried forward from the accepted intake receipt, not re-derived by this loader.
_GHISACONUS_REVIEWED_AT = datetime(2026, 7, 26, 13, 49, 9, tzinfo=UTC)
_GHISACONUS_RETRIEVED_AT = datetime(2026, 7, 26, 13, 49, 30, tzinfo=UTC)
_GHISACONUS_OBSERVED_FROM = datetime(2008, 1, 1, tzinfo=UTC)
_GHISACONUS_OBSERVED_TO = datetime(2015, 12, 31, 23, 59, 59, tzinfo=UTC)

_DATA_SOURCE_INACTIVE_MESSAGE = "GHISACONUS data source exists but is not approved/active"
_RELEASE_CONFLICT_MESSAGE = "GHISACONUS source release identity is already governed by different metadata"
_ARTIFACT_CONFLICT_MESSAGE = "GHISACONUS artifact identity is already governed by different content"
_RELEASE_SET_CONFLICT_MESSAGE = "GHISACONUS release set key and manifest checksum identify different release sets"


@dataclass(frozen=True)
class GhisaconusRehashResults:
    """Rehash outcome for all three local GHISACONUS files this lineage write binds."""

    csv: FrozenInputRehash
    archive: FrozenInputRehash
    metadata: FrozenInputRehash

    def require_all_match(self) -> None:
        """Refuse to build lineage fields from a file whose bytes no longer match its pinned digest."""
        mismatched = [item.label for item in (self.csv, self.archive, self.metadata) if not item.matches]
        if mismatched:
            raise ValueError(f"GHISACONUS lineage refuses to persist on digest mismatch: {mismatched}")


def rehash_ghisaconus_lineage_inputs(
    csv_path: Path = DEFAULT_GHISACONUS_CSV_PATH,
    archive_path: Path = DEFAULT_GHISACONUS_ARCHIVE_PATH,
    metadata_path: Path = DEFAULT_GHISACONUS_METADATA_PATH,
) -> GhisaconusRehashResults:
    """Read and rehash all three local GHISACONUS files against their pinned digests."""
    return GhisaconusRehashResults(
        csv=rehash_frozen_input(csv_path, GHISACONUS_CSV_EXPECTED_SHA256, "ghisaconus_csv_v1"),
        archive=rehash_frozen_input(archive_path, GHISACONUS_ARCHIVE_EXPECTED_SHA256, "ghisaconus_archive_v1"),
        metadata=rehash_frozen_input(metadata_path, GHISACONUS_METADATA_EXPECTED_SHA256, "ghisaconus_metadata_v1"),
    )


@dataclass(frozen=True)
class GhisaconusLineageFields:
    """Pure, DB-free field dicts for every row this loader writes, keyed by ORM constructor kwargs."""

    data_source: dict[str, Any]
    source_release: dict[str, Any]
    release_set: dict[str, Any]
    release_set_item_role: str
    artifacts: tuple[dict[str, Any], ...]


def build_ghisaconus_lineage_fields(rehash: GhisaconusRehashResults) -> GhisaconusLineageFields:
    """Build every row's field dict from rehashed local files; refuses on any digest mismatch.

    Checksums and byte counts are read from `rehash`'s measured results rather than duplicated as
    separate literals, so a persisted row's checksum is always what this run actually read from disk.
    """
    rehash.require_all_match()

    data_source = {
        "key": "kaggle-ghisaconus-mirror",
        "name": "Hyperspectral Library of Agricultural Crops (USGS)",
        "owner": "Bill Basener / Kaggle listing",
        "purpose": "Evaluation-only historical crop-spectrum classification benchmark.",
        "base_url": "https://www.kaggle.com/datasets/billbasener/hyperspectral-library-of-agricultural-crops-usgs",
        "license_name": "U.S. Government Works",
        "license_url": "https://www.kaggle.com/terms",
        "citation": "Kaggle GHISACONUS Version 1 listing; USGS/NASA lineage is retained as a provenance caveat.",
        "refresh_policy": {"mode": "manual", "append_only": True},
        "retention_days": 3650,
        "allowed_client_exposure": False,
        "review_state": SourceReviewState.APPROVED,
        "reviewed_at": _GHISACONUS_REVIEWED_AT,
        "reviewed_by": "local-evaluation-operator",
        "is_active": True,
        "configuration": {
            "benchmark_scope": "evaluation_only",
            "causal_role": "none",
            "source_kind": "public_kaggle_csv",
        },
    }

    source_release = {
        "source_version": "1",
        "retrieved_at": _GHISACONUS_RETRIEVED_AT,
        "data_available_at": _GHISACONUS_RETRIEVED_AT,
        "observed_from": _GHISACONUS_OBSERVED_FROM,
        "observed_to": _GHISACONUS_OBSERVED_TO,
        "payload_checksum": rehash.csv.actual_sha256,
        "payload_bytes": rehash.csv.byte_count,
        "schema_version": "ghisaconus_csv_v1",
        "transform_version": "source-native",
        "license_snapshot": "Kaggle listing: U.S. Government Works; terms https://www.kaggle.com/terms",
        "query_parameters": {
            "dataset_ref": "billbasener/hyperspectral-library-of-agricultural-crops-usgs",
            "version": 1,
        },
        "quality_summary": {
            "row_count": 6988,
            "column_count": 142,
            "spectral_column_count": 131,
            "source_image_count": 99,
            "missing_metadata_cells": 0,
            "missing_spectral_cells": 0,
            "synthetic_data_detected": False,
        },
        "validation_state": ReleaseValidationState.VALID,
        "validated_at": utc_now(),
    }

    release_set = {
        "logical_key": "ghisaconus-v1-public-benchmark-20260726",
        "as_of_time": _GHISACONUS_RETRIEVED_AT,
        # This manifest checksum is carried forward, unrecomputed, from the already-reviewed intake
        # receipt's warehouse_retention block; see the module docstring for the receipt's path.
        "manifest_checksum": "535fa6a2f412b627e735689d756b2ce29f9e288f9e790611719b0868d9920f0b",
        "description": (
            "Evaluation-only public GHISACONUS crop-spectrum benchmark; not a causal or operational release."
        ),
    }

    artifacts = (
        {
            "kind": "source_csv",
            "uri": f"warehouse://public-benchmarks/ghisaconus/v1/csv/{rehash.csv.actual_sha256}",
            "media_type": "text/csv",
            "checksum_sha256": rehash.csv.actual_sha256,
            "size_bytes": rehash.csv.byte_count,
            "storage_class": "local_raw_cache",
            "metadata_json": {
                "retention_role": "primary_payload",
                "source_release_payload": True,
                "local_path": rehash.csv.path,
                "upstream_source_url": data_source["base_url"],
            },
            "content_bytes": None,
        },
        {
            "kind": "source_archive",
            "uri": f"warehouse://public-benchmarks/ghisaconus/v1/archive/{rehash.archive.actual_sha256}",
            "media_type": "application/zip",
            "checksum_sha256": rehash.archive.actual_sha256,
            "size_bytes": rehash.archive.byte_count,
            "storage_class": "local_raw_cache",
            "metadata_json": {
                "retention_role": "distribution_archive",
                "source_release_payload": False,
                "local_path": rehash.archive.path,
                "upstream_source_url": data_source["base_url"],
            },
            "content_bytes": None,
        },
        {
            "kind": "source_metadata",
            "uri": f"warehouse://public-benchmarks/ghisaconus/v1/metadata/{rehash.metadata.actual_sha256}",
            "media_type": "application/json",
            "checksum_sha256": rehash.metadata.actual_sha256,
            "size_bytes": rehash.metadata.byte_count,
            "storage_class": "local_raw_cache",
            "metadata_json": {
                "retention_role": "source_metadata",
                "source_release_payload": False,
                "local_path": rehash.metadata.path,
                "upstream_source_url": data_source["base_url"],
            },
            "content_bytes": None,
        },
    )

    return GhisaconusLineageFields(
        data_source=data_source,
        source_release=source_release,
        release_set=release_set,
        release_set_item_role="benchmark_input",
        artifacts=artifacts,
    )


class GhisaconusLineageVerificationCounts(BaseModel):
    """Row counts read back inside the open transaction, before commit."""

    model_config = ConfigDict(frozen=True)

    data_source_count: int
    source_release_count: int
    release_set_count: int
    release_set_item_count: int
    artifact_count: int
    release_set_state: str

    @property
    def is_complete(self) -> bool:
        """True only when every table holds exactly the rows this loader is meant to leave behind."""
        return (
            self.data_source_count == 1
            and self.source_release_count == 1
            and self.release_set_count == 1
            and self.release_set_item_count == 1
            and self.artifact_count == EXPECTED_ARTIFACT_COUNT
            and self.release_set_state == "validated"
        )


class GhisaconusLineageReceipt(BaseModel):
    """Persisted lineage row identities, idempotency flags, and pre-commit verification counts."""

    model_config = ConfigDict(frozen=True)

    data_source_id: str
    data_source_idempotent: bool
    source_release_id: str
    source_release_idempotent: bool
    release_set_id: str
    release_set_state: str
    release_set_idempotent: bool
    artifact_ids: dict[str, str]
    artifact_idempotent: dict[str, bool]
    verification: GhisaconusLineageVerificationCounts


async def _ensure_ghisaconus_release_set(
    session: AsyncSession,
    release: SourceRelease,
    release_set_fields: dict[str, Any],
    item_role: str,
) -> tuple[ReleaseSet, bool]:
    """Create-or-return the one-member GHISACONUS release set, validating it only after its item lands.

    Mirrors `historical_writer/_release_sets.py`'s freeze-after-validate ordering using only the public
    `provenance.py` helpers, scoped to this loader's single release rather than the historical lanes'
    multi-release reconciliation.
    """
    await advisory_lock(session, f"public-evaluation-lineage:{release_set_fields['logical_key']}")
    release_set = await find_release_set(
        session,
        logical_key=release_set_fields["logical_key"],
        manifest_checksum=release_set_fields["manifest_checksum"],
        conflict_message=_RELEASE_SET_CONFLICT_MESSAGE,
    )
    if release_set is not None:
        members = await release_set_member_ids(session, release_set.id)
        if members != {release.id}:
            raise ValueError("GHISACONUS release set already exists with different membership")
        return release_set, True

    release_set = ReleaseSet(state=ReleaseSetState.DRAFT, **release_set_fields)
    session.add(release_set)
    await session.flush()
    session.add(ReleaseSetItem(release_set_id=release_set.id, source_release_id=release.id, source_role=item_role))
    await session.flush()
    members = await release_set_member_ids(session, release_set.id)
    if members != {release.id}:
        raise ValueError("GHISACONUS release set did not retain its one source release")
    release_set.state = ReleaseSetState.VALIDATED
    release_set.validated_at = utc_now()
    await session.flush()
    return release_set, False


async def _verify_ghisaconus_lineage(
    session: AsyncSession, source_key: str, release_set_logical_key: str
) -> GhisaconusLineageVerificationCounts:
    """Read back every row this loader is responsible for, scoped by identity, before commit."""
    data_source_ids = select(DataSource.id).where(DataSource.key == source_key)
    data_source_count = (
        await session.execute(select(func.count()).select_from(DataSource).where(DataSource.key == source_key))
    ).scalar_one()
    source_release_ids = select(SourceRelease.id).where(SourceRelease.data_source_id.in_(data_source_ids))
    source_release_count = (
        await session.execute(
            select(func.count()).select_from(SourceRelease).where(SourceRelease.data_source_id.in_(data_source_ids))
        )
    ).scalar_one()
    release_set = (
        await session.execute(select(ReleaseSet).where(ReleaseSet.logical_key == release_set_logical_key))
    ).scalar_one()
    release_set_item_count = (
        await session.execute(
            select(func.count()).select_from(ReleaseSetItem).where(ReleaseSetItem.release_set_id == release_set.id)
        )
    ).scalar_one()
    artifact_count = (
        await session.execute(
            select(func.count()).select_from(Artifact).where(Artifact.source_release_id.in_(source_release_ids))
        )
    ).scalar_one()
    return GhisaconusLineageVerificationCounts(
        data_source_count=data_source_count,
        source_release_count=source_release_count,
        release_set_count=1,
        release_set_item_count=release_set_item_count,
        artifact_count=artifact_count,
        release_set_state=str(release_set.state),
    )


async def persist_ghisaconus_lineage(
    session: AsyncSession, fields: GhisaconusLineageFields
) -> GhisaconusLineageReceipt:
    """Write the governed data_source/source_release/release_set/artifact rows and verify them before return.

    Idempotent: re-running against an already-persisted identity returns the existing rows rather than
    duplicating them, via the same `ensure_*` contract every historical lane uses.
    """
    source, source_idempotent = await ensure_data_source(
        session,
        DataSource(**fields.data_source),
        expected={
            "name": fields.data_source["name"],
            "owner": fields.data_source["owner"],
            "license_name": fields.data_source["license_name"],
            "configuration": fields.data_source["configuration"],
        },
        inactive_message=_DATA_SOURCE_INACTIVE_MESSAGE,
        conflict_message="GHISACONUS data source identity is already governed by different metadata",
    )

    release, release_idempotent = await ensure_source_release(
        session,
        SourceRelease(data_source_id=source.id, **fields.source_release),
        expected={
            "observed_from": fields.source_release["observed_from"],
            "observed_to": fields.source_release["observed_to"],
            "payload_bytes": fields.source_release["payload_bytes"],
            "schema_version": fields.source_release["schema_version"],
            "transform_version": fields.source_release["transform_version"],
            "license_snapshot": fields.source_release["license_snapshot"],
            "query_parameters": fields.source_release["query_parameters"],
            "quality_summary": fields.source_release["quality_summary"],
            "validation_state": fields.source_release["validation_state"],
        },
        conflict_message=_RELEASE_CONFLICT_MESSAGE,
    )

    artifact_ids: dict[str, str] = {}
    artifact_idempotent: dict[str, bool] = {}
    for artifact_fields in fields.artifacts:
        artifact, idempotent = await ensure_artifact(
            session,
            Artifact(source_release_id=release.id, **artifact_fields),
            expected={
                "source_release_id": release.id,
                "kind": artifact_fields["kind"],
                "media_type": artifact_fields["media_type"],
                "size_bytes": artifact_fields["size_bytes"],
                "storage_class": artifact_fields["storage_class"],
                "metadata_json": artifact_fields["metadata_json"],
                "content_bytes": None,
            },
            defer_content_bytes=False,
            conflict_message=_ARTIFACT_CONFLICT_MESSAGE,
        )
        artifact_ids[artifact_fields["kind"]] = str(artifact.id)
        artifact_idempotent[artifact_fields["kind"]] = idempotent

    release_set, release_set_idempotent = await _ensure_ghisaconus_release_set(
        session, release, fields.release_set, fields.release_set_item_role
    )

    verification = await _verify_ghisaconus_lineage(session, source.key, release_set.logical_key)
    if not verification.is_complete:
        raise ValueError(f"GHISACONUS lineage verification incomplete before commit: {verification!r}")

    return GhisaconusLineageReceipt(
        data_source_id=str(source.id),
        data_source_idempotent=source_idempotent,
        source_release_id=str(release.id),
        source_release_idempotent=release_idempotent,
        release_set_id=str(release_set.id),
        release_set_state=verification.release_set_state,
        release_set_idempotent=release_set_idempotent,
        artifact_ids=artifact_ids,
        artifact_idempotent=artifact_idempotent,
        verification=verification,
    )


async def run_ghisaconus_lineage_loader(
    *,
    csv_path: Path = DEFAULT_GHISACONUS_CSV_PATH,
    archive_path: Path = DEFAULT_GHISACONUS_ARCHIVE_PATH,
    metadata_path: Path = DEFAULT_GHISACONUS_METADATA_PATH,
    database_url: str | None = None,
) -> GhisaconusLineageReceipt:
    """Rehash, build fields, and persist inside one explicit transaction; commits only on success."""
    rehash = rehash_ghisaconus_lineage_inputs(csv_path, archive_path, metadata_path)
    fields = build_ghisaconus_lineage_fields(rehash)
    resolved_database_url = database_url or settings.require_local_source_loader_database_url()
    async with local_source_loader_session(resolved_database_url) as session, session.begin():
        return await persist_ghisaconus_lineage(session, fields)


def main() -> None:
    """Run the GHISACONUS lineage loader against prod and print the receipt as JSON."""
    parser = argparse.ArgumentParser(
        description="Retain GHISACONUS source/release/artifact/release-set lineage via client-side loading."
    )
    parser.add_argument("--csv-path", type=Path, default=DEFAULT_GHISACONUS_CSV_PATH)
    parser.add_argument("--archive-path", type=Path, default=DEFAULT_GHISACONUS_ARCHIVE_PATH)
    parser.add_argument("--metadata-path", type=Path, default=DEFAULT_GHISACONUS_METADATA_PATH)
    parser.add_argument("--database-url", type=str, default=None)
    args = parser.parse_args()
    receipt = asyncio.run(
        run_ghisaconus_lineage_loader(
            csv_path=args.csv_path,
            archive_path=args.archive_path,
            metadata_path=args.metadata_path,
            database_url=args.database_url,
        )
    )
    print(json.dumps(receipt.model_dump(), sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
