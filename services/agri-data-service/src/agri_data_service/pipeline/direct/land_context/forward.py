"""Capture, publish, and reconcile one current BLM reference snapshot."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.foundation.parquet.lane_contract import SourceWatermark
from agri_data_service.foundation.parquet.paths import classify_partition_day, tier_day_objects
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.direct import (
    LANE_DAY_OUTCOMES,
    NATURE_FLAGS,
    NOT_BBOX_BOUNDED,
    REFUSE_WHOLE_RELEASE,
    UNCHANGED,
    DirectWriterContract,
)
from agri_data_service.pipeline.direct.land_context.adapter import LandContextAdapter
from agri_data_service.pipeline.direct.land_context.products import HISTORY_FLOOR
from agri_data_service.pipeline.direct.land_context.rows import snapshot_tables
from agri_data_service.pipeline.direct.land_context.source import canonical_bytes, replay_snapshot
from agri_data_service.pipeline.direct.land_context.watermark import STATE_KEY, read_state
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.gap_fill import (
    _lane_day_lock_key,
    fill_one_lane_day,
    postgres_lane_day_lock,
)
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import (
    ConcurrentPrunePartitionError,
    ObjectStore,
    PartitionNotWrittenError,
)
from agri_data_service.warehouse.parquet.schema import get_stream_schema

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pyarrow as pa  # type: ignore[import-untyped]
    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.direct.land_context.source import LandContextSnapshot
    from agri_data_service.pipeline.parquet.availability_index import AvailabilityStorage, StoredAvailabilityObject

MAX_TIME_BUDGET_SECONDS = 3600

WRITER_CONTRACT: DirectWriterContract = DirectWriterContract(
    slug="land-context",
    identity_defect=REFUSE_WHOLE_RELEASE,
    geometry_defect=REFUSE_WHOLE_RELEASE,
    unconfigured_bbox=NOT_BBOX_BOUNDED,
    turn_outcomes=LANE_DAY_OUTCOMES | {UNCHANGED},
    flags_absent_on_purpose=dict.fromkeys(
        NATURE_FLAGS - {"--time-budget-seconds"},
        (
            "One complete three-state reference snapshot; "
            "pinned source/record limits and the next scheduled turn own retries."
        ),
    ),
    policy_basis=(
        "Missing IDs, partial ArcGIS pages, duplicate native keys and invalid polygonal area "
        "refuse the whole BLM snapshot; immutable complete captures retain the original acquisition clock on repair."
    ),
)


def _coordinates(snapshot: LandContextSnapshot) -> dict[str, str]:
    return {
        "day": snapshot.captured_at.date().isoformat(),
        "captured_at": snapshot.captured_at.isoformat(),
        "manifest_sha256": snapshot.manifest_sha256,
        "content_sha256": snapshot.content_sha256,
    }


def _write_state(storage: AvailabilityStorage, stored: StoredAvailabilityObject | None, state: dict[str, Any]) -> None:
    if not storage.compare_and_swap(
        STATE_KEY,
        canonical_bytes(state),
        expected_etag=None if stored is None else stored.etag,
        content_type="application/json",
    ):
        raise ValueError("BLM publication state changed despite its package lock; publication refused")


def ladder_complete(store: ObjectStore, layer: str, *, day: date, manifest: str) -> bool:
    """Inspect every rung and its source identity before an unchanged snapshot may skip work."""
    for tier in ZOOM_TIERS:
        keys = tuple(
            entry.relative_path
            for entry in store.list_partition_objects(layer, "observed", tier, year=day.year, month=day.month)
        )
        objects = tier_day_objects(keys, layer=layer, kind="observed", zoom=tier)
        if classify_partition_day(day, objects, zoom=tier) != "data":
            return False
        completion = store.read_completion_marker(layer, "observed", tier, day)
        if completion is None:
            return False
        try:
            physical = store.read_partition_with_receipts(layer, "observed", tier, day)
        except (PartitionNotWrittenError, ConcurrentPrunePartitionError):
            return False
        if (
            physical.table.num_rows != completion.row_count
            or len(physical.parts) != completion.part_count
            or set(physical.table.column("source_manifest_sha256").to_pylist()) != {manifest}
        ):
            return False
        if completion.parts and {
            (part.relative_path, part.sha256, part.row_count, part.byte_count) for part in physical.parts
        } != {(part.relative_path, part.sha256, part.row_count, part.byte_count) for part in completion.parts}:
            return False
    return True


async def _publish_product(  # noqa: PLR0913 - session, store and exact product publication coordinates
    session: AsyncSession,
    store: ObjectStore,
    *,
    layer: str,
    table: pa.Table,
    snapshot: LandContextSnapshot,
    run_id: str,
) -> dict[str, Any]:
    day = snapshot.captured_at.date()

    async def captured_watermark(session: AsyncSession, store: ObjectStore, *, today: date) -> SourceWatermark:
        del session, store
        if day > today:
            raise ValueError("A BLM snapshot cannot precede its capture")
        return SourceWatermark(
            day=day, basis=f"immutable BLM capture {snapshot.manifest_sha256}", instant=snapshot.captured_at
        )

    lane = replace(LANE_REGISTRY[layer], adapter=LandContextAdapter(layer, table), watermark=captured_watermark)
    published_at = datetime.now(UTC)
    outcome, parts, rows, written_bytes, detail = await fill_one_lane_day(
        session,
        store,
        lane,
        day=day,
        run_id=run_id,
        now=lambda: datetime.now(UTC),
        today=published_at.date(),
        lane_day_lock=postgres_lane_day_lock,
        extend_availability=False,
    )
    if outcome != "written" or not ladder_complete(store, layer, day=day, manifest=snapshot.manifest_sha256):
        raise ValueError(f"BLM {layer} publication remains incomplete: {outcome}: {detail}")
    readback = store.read_partition(layer, "observed", 13, day)
    sort_keys = [(name, "ascending") for name in get_stream_schema(layer).sort_columns]
    if not table.sort_by(sort_keys).equals(readback.sort_by(sort_keys)):
        raise ValueError(f"BLM {layer} base readback differs from its archived source reconstruction")
    return {
        "layer": layer,
        "outcome": outcome,
        "base_rows": table.num_rows,
        "parts": parts,
        "rows_across_write": rows,
        "written_bytes": written_bytes,
        "verified_static_snapshot": True,
        "source": _coordinates(snapshot),
    }


async def _owned_turn(
    session: AsyncSession,
    store: ObjectStore,
    storage: AvailabilityStorage,
    args: argparse.Namespace,
) -> dict[str, Any]:
    from agri_data_service.pipeline.source_bindings import resolve_land_context_source  # noqa: PLC0415
    from agri_data_service.warehouse.schemas.land_context import LAND_CONTEXT_STREAMS  # noqa: PLC0415

    source = resolve_land_context_source()
    stored, state = read_state(storage)
    prior = state["published"]
    pending = state["pending"]
    if pending is not None:
        snapshot = await replay_snapshot(storage, pending["manifest_sha256"])
    elif args.capture_manifest:
        snapshot = await replay_snapshot(storage, args.capture_manifest)
    elif prior is not None and args.mode != "refresh":
        day = date.fromisoformat(prior["day"])
        if all(
            ladder_complete(store, layer, day=day, manifest=prior["manifest_sha256"]) for layer in LAND_CONTEXT_STREAMS
        ):
            return {"status": "completed", "outcome": "unchanged", "day": day.isoformat(), "mode": args.mode}
        snapshot = await replay_snapshot(storage, prior["manifest_sha256"])
    else:
        snapshot = await source.capture(storage, timeout_seconds=args.time_budget_seconds)
    day = snapshot.captured_at.date()
    if day < HISTORY_FLOOR or day > datetime.now(UTC).date():
        raise ValueError("BLM capture lies outside the admitted acquisition horizon")
    if prior is not None and day < date.fromisoformat(prior["day"]):
        raise ValueError("An older BLM capture cannot supersede a newer published snapshot")
    if prior is not None and snapshot.captured_at < datetime.fromisoformat(prior["captured_at"]):
        raise ValueError("An older same-day BLM capture cannot supersede a newer source capture")
    if pending is None and prior is not None and prior["content_sha256"] == snapshot.content_sha256:
        prior_day = date.fromisoformat(prior["day"])
        if all(
            ladder_complete(store, layer, day=prior_day, manifest=prior["manifest_sha256"])
            for layer in LAND_CONTEXT_STREAMS
        ):
            return {
                "status": "completed",
                "outcome": "unchanged",
                "day": prior["day"],
                "checked_manifest_sha256": snapshot.manifest_sha256,
                "mode": args.mode,
            }
        snapshot = await replay_snapshot(storage, prior["manifest_sha256"])
        day = snapshot.captured_at.date()
    tables = snapshot_tables(snapshot, release_day=day)
    if pending is None:
        state["pending"] = _coordinates(snapshot)
        _write_state(storage, stored, state)
    results = [
        await _publish_product(session, store, layer=layer, table=tables[layer], snapshot=snapshot, run_id=args.run_id)
        for layer in LAND_CONTEXT_STREAMS
    ]
    ready = all(result["verified_static_snapshot"] for result in results)
    if ready:
        stored, state = read_state(storage)
        state["published"], state["pending"] = _coordinates(snapshot), None
        _write_state(storage, stored, state)
    return {
        "status": "completed" if ready else "incomplete",
        "outcome": "written",
        "day": day.isoformat(),
        "manifest_sha256": snapshot.manifest_sha256,
        "products": results,
        "mode": args.mode,
    }


async def run_forward(args: argparse.Namespace) -> dict[str, Any]:
    """One package lock excludes stale captures; each product also takes its ordinary lane-day lock."""
    store = ObjectStore.from_settings()
    storage = BotoAvailabilityStorage.from_settings()
    async with local_source_loader_session(settings.require_local_source_loader_database_url()) as session:
        lock = _lane_day_lock_key(LANE_REGISTRY["land-context-boundaries"], HISTORY_FLOOR)
        async with postgres_lane_day_lock(session, lock) as granted:
            if not granted:
                return {"status": "completed", "outcome": "contended"}
            await session.rollback()
            async with asyncio.timeout(args.time_budget_seconds):
                return await _owned_turn(session, store, storage, args)


def parser() -> argparse.ArgumentParser:
    built = argparse.ArgumentParser(description=__doc__)
    built.add_argument("--max-days", type=int, default=1)
    built.add_argument("--time-budget-seconds", type=int, default=1800)
    built.add_argument("--run-id", default=None)
    built.add_argument("--mode", choices=("refresh", "reconcile", "backfill"), default="refresh")
    built.add_argument("--capture-manifest", default=None)
    return built


async def main(argv: Sequence[str] | None = None) -> int:
    built = parser()
    args = built.parse_args(argv)
    if args.max_days != 1 or not 1 <= args.time_budget_seconds <= MAX_TIME_BUDGET_SECONDS:
        built.error("--max-days must be 1 and --time-budget-seconds must be in 1..3600")
    args.run_id = args.run_id or f"land-context-forward:{uuid.uuid4()}"
    try:
        report = await run_forward(args)
    except Exception as error:
        print(json.dumps({"status": "failed", "error": f"{type(error).__name__}: {error}", "run_id": args.run_id}))
        return 1
    print(json.dumps({"run_id": args.run_id, **report}, sort_keys=True))
    return 0 if report["status"] == "completed" else 1
