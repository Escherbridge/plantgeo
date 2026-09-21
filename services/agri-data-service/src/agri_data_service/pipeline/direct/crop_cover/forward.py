"""Capture, prepare or publish one admitted annual USDA crop classification release."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pyarrow.parquet as pq  # type: ignore[import-untyped]
from filelock import FileLock

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.foundation.geography.bounding_box import inline_bbox_value, parse_bounding_box
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS, validate_zoom_tier
from agri_data_service.pipeline.direct import (
    IDEMPOTENT_NOOP,
    LANE_DAY_OUTCOMES,
    NATURE_FLAGS,
    REFUSE_UNCONFIGURED_BBOX,
    REFUSE_WHOLE_RELEASE,
    DirectWriterContract,
)
from agri_data_service.pipeline.direct.crop_cover.adapter import CropCoverAdapter
from agri_data_service.pipeline.direct.crop_cover.archive import archive_capture, replay_capture
from agri_data_service.pipeline.direct.crop_cover.products import RELEASE_DAYS, capture_envelope
from agri_data_service.pipeline.direct.crop_cover.rows import build_tables
from agri_data_service.pipeline.direct.crop_cover.source import CaptureConfig, fail, read_manifest, sha256
from agri_data_service.pipeline.parquet.availability_extension import (
    FinalizedLaneDay,
    LaneDaySource,
    extend_availability_for_lane_day,
)
from agri_data_service.pipeline.parquet.availability_index import (
    AvailabilityUnavailableError,
    BotoAvailabilityStorage,
    read_latest_availability,
)
from agri_data_service.pipeline.parquet.gap_fill import (
    _lane_day_lock_key,
    fill_one_lane_day,
    postgres_lane_day_lock,
    unlocked_lane_day,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import (
    ConcurrentPrunePartitionError,
    ObjectStore,
    PartitionNotWrittenError,
    availability_lane_root,
)
from agri_data_service.pipeline.source_bindings import resolve_crop_cover_source
from agri_data_service.warehouse.schemas.crop_cover import CROP_COVER_STREAM

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pyarrow as pa

    from agri_data_service.pipeline.parquet.availability_index import AvailabilityRow, AvailabilityStorage

WRITER_CONTRACT: DirectWriterContract = DirectWriterContract(
    slug=CROP_COVER_STREAM,
    identity_defect=REFUSE_WHOLE_RELEASE,
    geometry_defect=REFUSE_WHOLE_RELEASE,
    unconfigured_bbox=REFUSE_UNCONFIGURED_BBOX,
    turn_outcomes=LANE_DAY_OUTCOMES | {IDEMPOTENT_NOOP},
    flags_absent_on_purpose=dict.fromkeys(
        NATURE_FLAGS - {"--bbox", "--time-budget-seconds"},
        "One admitted annual release per turn; bounded tile capture is resumable; the next scheduled turn retries.",
    ),
    policy_basis="Every named annual raster tile must match its dimensions, class legend, projection and SHA receipt; "
    "a partial capture never becomes a completed annual region. Crop areas are counts, not inferred legal boundaries.",
)


def parser() -> argparse.ArgumentParser:
    """Expose bounded source capture and explicit annual publication operations."""
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "--operation",
        choices=("capture", "prepare", "publish", "forward", "repair", "maintain", "replay"),
        default="forward",
    )
    result.add_argument("--year", type=int, choices=tuple(RELEASE_DAYS), default=max(RELEASE_DAYS))
    result.add_argument("--capture-dir", type=Path)
    result.add_argument("--capture-root", type=Path, default=Path("data/crop-cover-captures"))
    result.add_argument("--source-manifest-sha256")
    result.add_argument("--bbox", default=os.getenv("INGEST_BBOX", ",".join(map(str, capture_envelope()))))
    result.add_argument("--analysis-resolution-m", type=int, choices=(10, 30), default=30)
    result.add_argument("--workers", type=int, default=4)
    result.add_argument("--time-budget-seconds", type=int, default=3600)
    result.add_argument("--max-days", type=int, default=1)
    result.add_argument("--run-id", default=None)
    return result


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Preserve a negative leading longitude before argparse interprets option names."""
    return parser().parse_args(inline_bbox_value(list(sys.argv[1:] if argv is None else argv)))


def _indexed_rung_matches(store: ObjectStore, row: AvailabilityRow) -> bool:
    """Bind the current physical rung to its indexed receipts and counts."""
    if row.terminal_state != "published" or row.completion_receipt is None:
        return False
    tier = validate_zoom_tier(row.rung)
    marker = store.read_completion_receipt(CROP_COVER_STREAM, "observed", tier, row.day)
    if (
        marker is None
        or marker.relative_path != row.completion_receipt.key
        or marker.sha256 != row.completion_receipt.sha256
    ):
        return False
    try:
        physical = store.read_partition_with_receipts(CROP_COVER_STREAM, "observed", tier, row.day)
    except (PartitionNotWrittenError, ConcurrentPrunePartitionError):
        return False
    return (
        physical.table.num_rows == row.row_count
        and physical.table.num_rows == marker.completion.row_count
        and len(physical.parts) == marker.completion.part_count
        and {(part.relative_path, part.sha256) for part in physical.parts}
        == {(receipt.key, receipt.sha256) for receipt in row.data_receipts}
    )


def indexed_ladder(store: ObjectStore, storage: AvailabilityStorage, day: date) -> bool:
    """Verify indexed completion receipts and the physical parts they publish."""
    try:
        index = read_latest_availability(
            storage,
            lane_root=availability_lane_root(CROP_COVER_STREAM, "observed"),
            expected_product=CROP_COVER_STREAM,
            expected_required_rungs=(0, 5, 9, 13),
        )
    except AvailabilityUnavailableError:
        return False
    return day in index.selectable_days() and all(
        _indexed_rung_matches(store, row) for row in index.rows if row.day == day
    )


async def maintain(options: argparse.Namespace) -> dict[str, object]:
    """Repair oldest owed annual data first; refresh the latest source once per calendar month."""
    today = datetime.now(UTC).date()
    store = ObjectStore.from_settings()
    storage = BotoAvailabilityStorage.from_settings()
    options.capture_root.mkdir(parents=True, exist_ok=True)
    with FileLock(str(options.capture_root / ".maintain.lock"), timeout=0):
        pending = [
            year for year, day in RELEASE_DAYS.items() if day <= today and not indexed_ladder(store, storage, day)
        ]
        latest_year = max(year for year, day in RELEASE_DAYS.items() if day <= today)
        if not pending:
            latest = store.read_completion_marker(CROP_COVER_STREAM, "observed", 13, RELEASE_DAYS[latest_year])
            if latest is not None and (latest.completed_at.year, latest.completed_at.month) == (
                today.year,
                today.month,
            ):
                return {"outcome": IDEMPOTENT_NOOP, "layer": CROP_COVER_STREAM, "admitted_years": sorted(RELEASE_DAYS)}
        year = min(pending) if pending else latest_year
        scoped = argparse.Namespace(**vars(options))
        scoped.year = year
        scoped.operation = "forward"
        scoped.capture_dir = options.capture_root / str(year) / today.strftime("%Y-%m")
        return await run(scoped)


def _capture_config(options: argparse.Namespace) -> CaptureConfig:
    """Validate one immutable annual support before any source or object-store operation."""
    if options.capture_dir is None:
        raise fail("--capture-dir is required except for the scheduled maintain operation")
    if not options.bbox:
        raise fail("The regional crop capture requires an explicit bounded bbox")
    bbox = parse_bounding_box(options.bbox)
    if options.operation in {"publish", "forward", "repair"} and bbox != capture_envelope():
        raise fail("Publication requires the complete admitted PNW envelope; smaller captures are prepare-only")
    config = CaptureConfig(
        options.capture_dir,
        options.year,
        bbox,
        options.analysis_resolution_m,
        options.workers,
        options.time_budget_seconds,
    )
    config.validate()
    if RELEASE_DAYS[options.year] > datetime.now(UTC).date():
        raise fail("An annual classification cannot be exposed before official publication")
    return config


async def _load_capture(options: argparse.Namespace, config: CaptureConfig) -> Path:
    """Load or restore a complete capture with its admitted source binding."""
    manifest_path = config.output / "manifest.json"
    if options.operation == "replay":
        if not options.source_manifest_sha256:
            raise fail("--source-manifest-sha256 is required for durable source replay")
        return await asyncio.to_thread(
            replay_capture,
            BotoAvailabilityStorage.from_settings(),
            options.source_manifest_sha256,
            config.output,
        )
    if options.operation in {"capture", "forward", "repair"}:
        return await asyncio.to_thread(resolve_crop_cover_source().capture, config)
    return manifest_path


async def run(options: argparse.Namespace) -> dict[str, object]:
    """Repair missing ladders or refresh a whole immutable capture generation."""
    if options.max_days != 1:
        raise fail("An annual release is indivisible; --max-days must be 1")
    resolve_crop_cover_source()
    if options.operation == "maintain":
        return await maintain(options)
    config = _capture_config(options)
    day = RELEASE_DAYS[options.year]
    if options.operation == "repair":
        store = ObjectStore.from_settings()
        if indexed_ladder(store, BotoAvailabilityStorage.from_settings(), day):
            return {"outcome": IDEMPOTENT_NOOP, "layer": CROP_COVER_STREAM, "release_day": day.isoformat()}
    config.output.mkdir(parents=True, exist_ok=True)
    with FileLock(str(config.output / ".capture.lock"), timeout=0):
        manifest_path = await _load_capture(options, config)
        manifest = await asyncio.to_thread(read_manifest, manifest_path)
        if manifest["observed_year"] != options.year or tuple(manifest["bbox"]) != config.bbox:
            raise fail("Selected annual release and captured source disagree")
        report: dict[str, object] = {
            "layer": CROP_COVER_STREAM,
            "observed_year": options.year,
            "release_day": day.isoformat(),
            "manifest": str(manifest_path),
            "source_sha256": sha256(manifest_path.read_bytes()),
            "source_tiles": len(manifest["tiles"]),
            "coverage": manifest["coverage"],
        }
        if options.operation in {"capture", "replay"}:
            return report
        tables = await asyncio.to_thread(build_tables, manifest_path)
        report["rows_by_tier"] = {str(tier): table.num_rows for tier, table in tables.items()}
        if options.operation == "prepare":
            for tier, table in tables.items():
                pq.write_table(table, config.output / f"crop-cover-z{tier}.parquet", compression="zstd")
            return report
        return await _publish(options, manifest_path, report, tables)


async def _publish(
    options: argparse.Namespace,
    manifest_path: Path,
    report: dict[str, object],
    tables: dict[int, pa.Table],
) -> dict[str, object]:
    """Archive the source graph, then finalize and index one complete annual ladder."""
    report["archived_source_manifest_key"] = await asyncio.to_thread(
        archive_capture,
        BotoAvailabilityStorage.from_settings(),
        manifest_path,
    )
    adapter = CropCoverAdapter(tables)
    lane = replace(LANE_REGISTRY[CROP_COVER_STREAM], adapter=adapter)
    store = ObjectStore.from_settings()
    day = RELEASE_DAYS[options.year]
    run_id = options.run_id or f"crop-cover:{uuid.uuid4()}"
    async with (
        local_source_loader_session(settings.require_local_source_loader_database_url()) as session,
        postgres_lane_day_lock(session, _lane_day_lock_key(lane, day)) as granted,
    ):
        if not granted:
            return {**report, "outcome": "contended"}
        with store.recording_written_objects() as written:
            outcome, parts, rows, byte_count, detail = await fill_one_lane_day(
                session,
                store,
                lane,
                day=day,
                run_id=run_id,
                now=lambda: datetime.now(UTC),
                today=datetime.now(UTC).date(),
                lane_day_lock=unlocked_lane_day,
                derive_tiers=adapter.derive,
                extend_availability=False,
            )
        if outcome == "written":
            published_at = datetime.now(UTC)
            extension = await extend_availability_for_lane_day(
                session,
                store,
                lane=CROP_COVER_STREAM,
                kind="observed",
                day=day,
                outcome=FinalizedLaneDay(
                    terminal_state="published",
                    day=day,
                    written=written,
                    published_at=published_at,
                    source_ceiling=max(release for release in RELEASE_DAYS.values() if release <= published_at.date()),
                    source=LaneDaySource(
                        origin="usda-cdl-classified-raster",
                        run_id=run_id,
                        row_count=rows,
                        part_count=parts,
                        exported_at=published_at,
                        detail=f"Annual USDA CDL {options.year}; source manifest sha256={report['source_sha256']}; "
                        f"archived manifest={report['archived_source_manifest_key']}",
                    ),
                ),
                availability=BotoAvailabilityStorage.from_settings(),
                now=lambda: datetime.now(UTC),
            )
            report["availability_state"] = extension.state
    if outcome != "written":
        raise fail(f"Annual crop publication did not complete: {outcome}: {detail}")
    if not all(store.read_completion_marker(CROP_COVER_STREAM, "observed", tier, day) for tier in ZOOM_TIERS):
        raise fail("Annual crop publication has an incomplete ladder")
    if not indexed_ladder(store, BotoAvailabilityStorage.from_settings(), day):
        raise fail("Physical crop data is complete but availability publication remains incomplete")
    report.update(outcome=outcome, run_id=run_id, parts=parts, rows=rows, bytes=byte_count, detail=detail)
    return report


async def main(argv: Sequence[str] | None = None) -> int:
    """Run one explicit bounded annual operation."""
    options = _parse_args(argv)
    report = await run(options)
    print(json.dumps(report, sort_keys=True, default=str))
    return 0
