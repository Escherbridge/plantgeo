"""`maintain`: re-probe the thirty pins and report the lane's static state and any drift. Writes nothing.

Drift is reported as a suspected new ISRIC release to republish under the CQ-8 carve-out, never as an owed
day. See `pipeline/direct/soil_properties/AGENTS.md`, "Watermark and drift".
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx

from agri_data_service.foundation.parquet.lane_contract import resolve_static_lane
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS
from agri_data_service.pipeline.direct.soil_properties.products import RELEASE_DAY, RELEASE_ID
from agri_data_service.pipeline.direct.soil_properties.source import RetryPolicy, drift_report, probe_pins
from agri_data_service.pipeline.direct.soil_properties.watermark import pinned_source_watermark
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.warehouse.schemas.soil_properties import SOIL_PROPERTIES_STREAM

if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence
    from datetime import date

    from agri_data_service.pipeline.direct.soil_properties.source import ProbedFile


def lane_state(store: ObjectStore, *, today: date) -> dict[str, Any]:
    """The static-lane verdict from the pinned watermark and the base marker, plus every rung's marker."""
    markers = {
        tier: store.read_completion_marker(SOIL_PROPERTIES_STREAM, "observed", tier, RELEASE_DAY) for tier in ZOOM_TIERS
    }
    base = markers[13]
    verdict = resolve_static_lane(
        watermark=pinned_source_watermark(today=today),
        newest_data_day=RELEASE_DAY if base is not None else None,
        newest_data_instant=base.completed_at if base is not None else None,
        newest_marker_day=None,
        today=today,
    )
    return {
        "static_lane_state": verdict.state,
        "version_day": verdict.version_day.isoformat() if verdict.version_day else None,
        "detail": verdict.detail,
        "rungs": {
            str(tier): None if marker is None else {"rows": marker.row_count, "parts": marker.part_count}
            for tier, marker in markers.items()
        },
    }


def maintenance_report(probes: Sequence[ProbedFile], state: dict[str, Any]) -> dict[str, Any]:
    """Combine drift and state; drift names a republish, never an owed day."""
    drift = drift_report(probes)
    return {
        "verb": "maintain",
        "release_id": RELEASE_ID,
        "status": "drift_suspected" if drift else "pinned",
        "drift": drift,
        "action": (
            "new ISRIC release suspected; republish under the CQ-8 carve-out (re-pin products.py first)"
            if drift
            else "none"
        ),
        "probes": [probe.to_report() for probe in probes],
        **state,
    }


def maintain_release(options: argparse.Namespace) -> dict[str, Any]:
    """HEAD all thirty pins, read the ladder's markers, and report; nothing is written."""
    policy = RetryPolicy(options.retry_attempts, options.retry_base_seconds, options.retry_max_seconds)
    with httpx.Client() as client:
        probes = probe_pins(client, policy)
    state = lane_state(ObjectStore.from_settings(), today=datetime.now(UTC).date())
    return maintenance_report(probes, state)


async def run_maintain(options: argparse.Namespace) -> dict[str, Any]:
    """The `maintain` verb."""
    return await asyncio.to_thread(maintain_release, options)


__all__ = ["lane_state", "maintain_release", "maintenance_report", "run_maintain"]
