"""`retract`, the rollback verb (P3): a dry run by default; `--confirm soilgrids-v2.0/2020-06-02` acts.

Every rung's completion marker is cleared FIRST, then z0, z5, z9 and z13 are emptied in that order, under the
lane-day advisory lock. Readers fall back to `lane_never_written`. The capture archive is kept (immutable
evidence). See `pipeline/direct/soil_properties/AGENTS.md`, "Retract".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, Protocol

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.pipeline.direct.soil_properties.products import RELEASE_DAY, RELEASE_ID
from agri_data_service.pipeline.direct.soil_properties.source import fail
from agri_data_service.pipeline.parquet.gap_fill import _lane_day_lock_key, postgres_lane_day_lock
from agri_data_service.pipeline.parquet.lane_registry import LANE_REGISTRY
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.soil_properties import SOIL_PROPERTIES_STREAM

if TYPE_CHECKING:
    import argparse
    from datetime import date

    from agri_data_service.foundation.parquet.paths import PartitionKind
    from agri_data_service.foundation.parquet.zoom import ZoomTier

#: Coarse rungs first, the base rung last: the base marker is the last claim a reader loses.
RETRACT_ORDER: Final[tuple[ZoomTier, ...]] = (0, 5, 9, 13)


class RetractableStore(Protocol):
    """The slice of `ObjectStore` a retraction uses; a test records the calls."""

    def list_day_parts(self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> tuple[str, ...]: ...

    def read_completion_marker(self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> Any: ...

    def clear_completion_marker(self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> None: ...

    def retract_partition_tier(self, layer: str, kind: PartitionKind, zoom: ZoomTier, day: date) -> Any: ...


def retraction_plan(store: RetractableStore) -> dict[str, Any]:
    """What a retraction would remove, rung by rung, in order; reads only."""
    return {
        str(tier): {
            "parts": len(store.list_day_parts(SOIL_PROPERTIES_STREAM, "observed", tier, RELEASE_DAY)),
            "marked": store.read_completion_marker(SOIL_PROPERTIES_STREAM, "observed", tier, RELEASE_DAY) is not None,
        }
        for tier in RETRACT_ORDER
    }


def retract_rungs(store: RetractableStore) -> dict[str, Any]:
    """Clear all four markers, then empty z0, z5, z9, z13; any failure to delete is fatal and named."""
    for tier in RETRACT_ORDER:
        store.clear_completion_marker(SOIL_PROPERTIES_STREAM, "observed", tier, RELEASE_DAY)
    removed: dict[str, int] = {}
    failures: list[str] = []
    for tier in RETRACT_ORDER:
        result = store.retract_partition_tier(SOIL_PROPERTIES_STREAM, "observed", tier, RELEASE_DAY)
        removed[str(tier)] = len(result.removed)
        failures.extend(result.failures)
    if failures:
        raise fail(f"{RELEASE_ID} retraction left objects behind: {failures}", stage="retract")
    return removed


async def retract_release(options: argparse.Namespace) -> dict[str, Any]:
    """Dry-run the plan, or, confirmed by the exact release id, retract the whole ladder under the lock."""
    store = ObjectStore.from_settings()
    plan = retraction_plan(store)
    report: dict[str, Any] = {"verb": "retract", "release_id": RELEASE_ID, "plan": plan}
    if options.confirm is None:
        return {**report, "status": "dry_run", "detail": f"pass --confirm {RELEASE_ID} to retract"}
    if options.confirm != RELEASE_ID:
        raise fail(f"--confirm must be exactly {RELEASE_ID}", stage="retract", code="invalid_arguments")
    lane = LANE_REGISTRY[SOIL_PROPERTIES_STREAM]
    async with (
        local_source_loader_session(settings.require_local_source_loader_database_url()) as session,
        postgres_lane_day_lock(session, _lane_day_lock_key(lane, RELEASE_DAY)) as granted,
    ):
        if not granted:
            return {**report, "status": "contended", "detail": "another run holds the lane-day lock; rerun"}
        removed = retract_rungs(store)
    return {**report, "status": "retracted", "removed_parts": removed, "capture_archive": "kept"}


async def run_retract(options: argparse.Namespace) -> dict[str, Any]:
    """The `retract` verb."""
    return await retract_release(options)


__all__ = ["RETRACT_ORDER", "retract_release", "retract_rungs", "retraction_plan", "run_retract"]
