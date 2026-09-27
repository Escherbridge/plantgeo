"""`publish`: archive the capture, then write base parts and derived rungs under the lane-day advisory lock.

Base parts are contiguous slices of the prepared, sorted table at `--rows-per-part`; the coarse rungs derive
from that same in-memory base (`derive_and_write_day_tiers(base_table=...)`), completion markers last. See
`pipeline/direct/soil_properties/AGENTS.md`, "Publish".
"""

from __future__ import annotations

import functools
import json
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

import numpy as np

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.foundation.parquet.paths import try_parse_partition_path
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.direct import IDEMPOTENT_NOOP
from agri_data_service.pipeline.direct.soil_properties.capture import CAPTURE_MANIFEST_NAME, read_capture_manifest
from agri_data_service.pipeline.direct.soil_properties.prepare import read_prepared_table
from agri_data_service.pipeline.direct.soil_properties.products import LATTICE_CELL_COUNT, RELEASE_DAY, RELEASE_ID
from agri_data_service.pipeline.direct.soil_properties.source import fail, sha256_hex
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.derivation import derive_and_write_day_tiers
from agri_data_service.pipeline.parquet.gap_fill import fill_one_lane_day, postgres_lane_day_lock
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY, normalise_export_outcome
from agri_data_service.pipeline.parquet.objectstore import ObjectStore, PartitionNotWrittenError
from agri_data_service.warehouse.parquet.schema import observed_stream_schema
from agri_data_service.warehouse.schemas.soil_properties import (
    SOIL_PROPERTIES_STREAM,
    SOIL_PROPERTIES_VALUE_COLUMNS,
)

if TYPE_CHECKING:
    import argparse
    from datetime import date
    from pathlib import Path

    import pyarrow as pa  # type: ignore[import-untyped]
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage
    from agri_data_service.pipeline.parquet.lane_registry import LaneRunResult

ARCHIVE_ROOT: Final = f"layer={SOIL_PROPERTIES_STREAM}/kind=observed/availability/source-captures"
MIN_ROWS_PER_PART: Final = 10_000
MAX_ROWS_PER_PART: Final = 4_000_000
PUBLISH_REPORT_NAME: Final = "publish-report.json"
_INTEGRAL_TOLERANCE: Final = 1e-9


def archive_prefix(manifest_sha256: str) -> str:
    """`layer=soil-properties/kind=observed/availability/source-captures/<manifest-sha>/`."""
    return f"{ARCHIVE_ROOT}/{manifest_sha256}"


def split_parts(table: pa.Table, rows_per_part: int) -> list[pa.Table]:
    """Contiguous slices of the sorted table; never an empty list, never an empty part."""
    if table.num_rows == 0:
        raise fail("the prepared table is empty; nothing may be published", stage="publish")
    return [table.slice(offset, rows_per_part) for offset in range(0, table.num_rows, rows_per_part)]


def validate_rows_per_part(rows_per_part: int | None) -> int:
    """`--rows-per-part` is required (the P1b measurement picks it) and bounded."""
    if rows_per_part is None:
        raise fail(
            "--rows-per-part is required for publish: pass the layout P1b measured (p0-probes.md)",
            stage="publish",
            code="invalid_arguments",
        )
    if not MIN_ROWS_PER_PART <= rows_per_part <= MAX_ROWS_PER_PART:
        raise fail(
            f"--rows-per-part must be within {MIN_ROWS_PER_PART}..{MAX_ROWS_PER_PART}",
            stage="publish",
            code="invalid_arguments",
        )
    return rows_per_part


def validate_prepared_table(table: pa.Table, manifest_sha256: str) -> None:
    """Refuse a table that is not exactly the prepared release of this capture."""
    expected = observed_stream_schema(SOIL_PROPERTIES_STREAM).arrow_schema
    if table.schema.names != expected.names:
        raise fail("the prepared table's columns are not the soil-properties schema", stage="publish")
    if not 0 < table.num_rows <= LATTICE_CELL_COUNT:
        raise fail(f"{table.num_rows} rows cannot come from a {LATTICE_CELL_COUNT}-cell lattice", stage="publish")
    if set(table.column("source_manifest_sha256").to_pylist()) != {manifest_sha256}:
        raise fail("the prepared table was built from a different capture manifest", stage="publish")
    if set(table.column("release_day").to_pylist()) != {RELEASE_DAY}:
        raise fail(f"the prepared table is not stamped {RELEASE_DAY.isoformat()}", stage="publish")
    for column in SOIL_PROPERTIES_VALUE_COLUMNS:
        values = table.column(column).to_numpy()
        if not np.all(np.abs(values - np.round(values)) < _INTEGRAL_TOLERANCE):
            raise fail(f"{column} holds a non-integral base value; z13 carries ISRIC mapped integers", stage="publish")


@dataclass(frozen=True, slots=True)
class SoilPropertiesAdapter:
    """Write the prepared base as contiguous parts while the lane-day lock is held."""

    table: pa.Table
    rows_per_part: int

    async def __call__(self, session: AsyncSession, store: ObjectStore, *, day: date, run_id: str) -> LaneRunResult:
        await session.rollback()
        if day != RELEASE_DAY or not run_id:
            raise fail(f"{RELEASE_ID} publishes only its own version day", stage="publish")
        parts = split_parts(self.table, self.rows_per_part)
        return normalise_export_outcome(
            tuple(
                store.write_partition(
                    part, layer=SOIL_PROPERTIES_STREAM, kind="observed", zoom=13, day=day, part_index=index
                )
                for index, part in enumerate(parts)
            )
        )


def archive_capture(storage: AvailabilityStorage, capture_dir: Path, manifest: dict[str, Any]) -> str:
    """Copy every captured window, then the manifest LAST, to the immutable capture archive."""
    prefix = archive_prefix(str(manifest["manifest_sha256"]))
    for entry in manifest["files"]:
        body = (capture_dir / entry["path"]).read_bytes()
        if sha256_hex(body) != entry["sha256"]:
            raise fail(f"{entry['path']} changed while archiving", stage="publish")
        storage.put_immutable(f"{prefix}/{entry['path']}", body, content_type="image/tiff")
    key = f"{prefix}/{CAPTURE_MANIFEST_NAME}"
    storage.put_immutable(key, (capture_dir / CAPTURE_MANIFEST_NAME).read_bytes(), content_type="application/json")
    return key


def ladder_markers(store: ObjectStore) -> dict[int, Any]:
    """The completion marker of every rung of the release day, or None where a rung has none."""
    return {
        tier: store.read_completion_marker(SOIL_PROPERTIES_STREAM, "observed", tier, RELEASE_DAY) for tier in ZOOM_TIERS
    }


def _is_first_part(relative_path: str) -> bool:
    partition = try_parse_partition_path(relative_path)
    return partition is not None and partition.part_index == 0


def published_manifest_sha256(store: ObjectStore) -> str | None:
    """The capture manifest the published base was built from, read from its first part's rows alone."""
    try:
        read = store.read_partition_with_receipts(
            SOIL_PROPERTIES_STREAM, "observed", 13, RELEASE_DAY, part_selector=_is_first_part
        )
    except PartitionNotWrittenError:
        return None
    if read.table.num_rows == 0:
        return None
    hashes = set(read.table.column("source_manifest_sha256").to_pylist())
    return str(next(iter(hashes))) if len(hashes) == 1 else None


def already_published(store: ObjectStore, *, rows: int, parts: int, manifest_sha256: str) -> bool:
    """All four markers exist with this table's counts, and the base was built from THIS capture manifest.

    Review m2: a different capture with equal counts is a republish, never a no-op. The manifest is read
    only once the counts already match.
    """
    markers = ladder_markers(store)
    base = markers[13]
    counts_match = all(marker is not None for marker in markers.values()) and (
        base is not None and base.row_count == rows and base.part_count == parts
    )
    return counts_match and published_manifest_sha256(store) == manifest_sha256


async def publish_release(options: argparse.Namespace) -> dict[str, Any]:
    """Verify capture and table, archive, publish all four rungs under the lock, then prove the ladder."""
    if options.capture_dir is None:
        raise fail("--capture-dir is required for publish", stage="publish", code="invalid_arguments")
    rows_per_part = validate_rows_per_part(options.rows_per_part)
    capture_dir: Path = options.capture_dir
    manifest = read_capture_manifest(capture_dir, stage="publish")
    table = read_prepared_table(capture_dir)
    validate_prepared_table(table, str(manifest["manifest_sha256"]))
    parts = len(split_parts(table, rows_per_part))
    store = ObjectStore.from_settings()
    report: dict[str, Any] = {
        "verb": "publish",
        "release_id": RELEASE_ID,
        "manifest_sha256": manifest["manifest_sha256"],
        "rows": table.num_rows,
        "base_parts": parts,
        "rows_per_part": rows_per_part,
    }
    if not options.force and already_published(
        store, rows=table.num_rows, parts=parts, manifest_sha256=str(manifest["manifest_sha256"])
    ):
        return {
            **report,
            "outcome": IDEMPOTENT_NOOP,
            "detail": "all four rungs are marked with these counts, built from this capture manifest",
        }
    report["archived_manifest_key"] = archive_capture(BotoAvailabilityStorage.from_settings(), capture_dir, manifest)
    lane = replace(LANE_REGISTRY[SOIL_PROPERTIES_STREAM], adapter=SoilPropertiesAdapter(table, rows_per_part))
    run_id = options.run_id or f"{SOIL_PROPERTIES_STREAM}:{uuid.uuid4()}"
    async with local_source_loader_session(settings.require_local_source_loader_database_url()) as session:
        outcome, written_parts, rows, written_bytes, detail = await fill_one_lane_day(
            session,
            store,
            lane,
            day=RELEASE_DAY,
            run_id=run_id,
            now=lambda: datetime.now(UTC),
            today=datetime.now(UTC).date(),
            lane_day_lock=postgres_lane_day_lock,
            derive_tiers=functools.partial(derive_and_write_day_tiers, base_table=table),
            extend_availability=False,
        )
    report.update(run_id=run_id, parts_written=written_parts, rows_written=rows, bytes_written=written_bytes)
    if outcome == "contended":
        return {**report, "outcome": outcome, "detail": detail}
    if outcome != "written":
        raise fail(f"{RELEASE_ID} publication did not complete: {outcome}: {detail}", stage="publish")
    markers = ladder_markers(store)
    missing = sorted(tier for tier, marker in markers.items() if marker is None)
    base = markers[13]
    if missing or base is None or base.row_count != table.num_rows:
        raise fail(f"{RELEASE_ID} wrote parts but its ladder is incomplete (unmarked rungs {missing})", stage="publish")
    rung_rows = {str(tier): marker.row_count for tier, marker in markers.items()}
    report.update(outcome=outcome, detail=detail, rung_rows=rung_rows)
    rendered = json.dumps(report, indent=2, sort_keys=True, default=str)
    (capture_dir / PUBLISH_REPORT_NAME).write_text(rendered, encoding="utf-8")
    return report


__all__ = [
    "ARCHIVE_ROOT",
    "SoilPropertiesAdapter",
    "already_published",
    "archive_capture",
    "archive_prefix",
    "ladder_markers",
    "publish_release",
    "published_manifest_sha256",
    "split_parts",
    "validate_prepared_table",
    "validate_rows_per_part",
]
