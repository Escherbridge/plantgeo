"""Daily eligible-stage publication and weekly bounded MTBS source refresh."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import shutil
import tempfile
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.ingest.policy import parse_bbox, resolve_bounded_bbox
from agri_data_service.pipeline.direct.burn_severity.capture import BBOX, YEARS, capture_snapshot, prepare_capture
from agri_data_service.pipeline.direct.burn_severity.current_snapshot import digest
from agri_data_service.pipeline.direct.burn_severity.forward import run_burn_severity_forward
from agri_data_service.pipeline.direct.burn_severity.publish_snapshot import publish_stage
from agri_data_service.pipeline.direct.burn_severity.stage import (
    capture_due,
    eligible_stage,
    read_queue,
    record_unchanged_capture,
    stage_prepared,
)
from agri_data_service.pipeline.parquet.availability_index import BotoAvailabilityStorage
from agri_data_service.pipeline.parquet.objectstore import ObjectStore

if TYPE_CHECKING:
    from agri_data_service.pipeline.direct.burn_severity.forward import BurnSeverityForwardConfig

MAX_RESULT_BYTES = 64 * 1024


def _daily_child(config: BurnSeverityForwardConfig, receipt: str) -> None:
    watchdog = threading.Timer(config.time_budget_seconds, os._exit, args=(2,))
    watchdog.daemon = True
    watchdog.start()
    result = asyncio.run(_run_daily_owned(config))
    Path(receipt).write_text(json.dumps(result, default=str), encoding="utf-8")
    watchdog.cancel()


def _run_bounded(config: BurnSeverityForwardConfig) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="mtbs-daily-result-") as directory:
        receipt = Path(directory) / "result.json"
        return _join_daily(config, receipt)


def _join_daily(config: BurnSeverityForwardConfig, receipt: Path) -> dict[str, object]:
    process = multiprocessing.get_context("spawn").Process(target=_daily_child, args=(config, str(receipt)))
    process.start()
    process.join(timeout=config.time_budget_seconds)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        raise ValueError("MTBS daily lifecycle exceeded its child-process deadline; durable work remains resumable")
    if process.exitcode != 0:
        raise ValueError("MTBS daily lifecycle refused; failed capture journals remain in owned temporary storage")
    with receipt.open("rb") as handle:
        body = handle.read(MAX_RESULT_BYTES + 1)
    if len(body) > MAX_RESULT_BYTES:
        raise ValueError("MTBS daily result exceeds its receipt cap")
    result = json.loads(body)
    if not isinstance(result, dict):
        raise ValueError("MTBS daily result must be an object")
    return cast("dict[str, object]", result)


async def run_daily(config: BurnSeverityForwardConfig) -> dict[str, object]:
    """Own the entire daily lifecycle in a child with a hard termination deadline."""
    return await asyncio.to_thread(_run_bounded, config)


async def _run_daily_owned(config: BurnSeverityForwardConfig) -> dict[str, object]:
    """Keep historical cohorts, staged current snapshots and weekly checks on one bounded schedule."""
    today = config.today or datetime.now(UTC).date()
    if today != datetime.now(UTC).date() or today.year > YEARS[-1]:
        raise ValueError("MTBS daily dispatcher needs the actual current day and a reviewed year horizon")
    configured = resolve_bounded_bbox(config.bbox)
    if configured is None or parse_bbox(configured) != BBOX:
        raise ValueError("MTBS daily dispatcher requires its exact reviewed deployment footprint")
    deadline = time.monotonic() + config.time_budget_seconds
    storage = BotoAvailabilityStorage.from_settings()
    _, queue = read_queue(storage)
    eligible = eligible_stage(queue, today)
    publication: dict[str, object] = {"status": "no_eligible_stage"}
    if eligible is not None:
        async with local_source_loader_session(settings.require_local_source_loader_database_url()) as session:
            publication = await publish_stage(
                session, ObjectStore.from_settings(), storage, identity=eligible["manifest_sha256"], today=today
            )
    if time.monotonic() >= deadline:
        return {"status": "time_budget", "publication": publication}
    _, queue = read_queue(storage)
    if not capture_due(queue, today):
        return {
            "status": "checked",
            "publication": publication,
            "last_capture_day": queue["last_capture_day"],
            "pending": len(queue["pending"]),
        }
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return {"status": "time_budget", "publication": publication}
    root = Path(tempfile.mkdtemp(prefix="plantgeo-mtbs-current-"))
    captured, prepared = root / "capture", root / "prepared"
    # Failed journals stay local. Successful evidence is durably archived before scratch cleanup.
    capture_snapshot(captured)
    manifest_sha = digest((captured / "manifest.json").read_bytes())
    if record_unchanged_capture(storage, capture=captured, identity=manifest_sha):
        staged: dict[str, object] = {"status": "checked_unchanged", "manifest_sha256": manifest_sha}
    else:
        prepare_capture(captured, manifest_sha, prepared)
        staged = stage_prepared(storage, capture=captured, prepared=prepared, manifest_sha256=manifest_sha)
    if root.resolve().parent != Path(tempfile.gettempdir()).resolve() or not root.name.startswith(
        "plantgeo-mtbs-current-"
    ):
        raise ValueError("MTBS scratch cleanup escaped the owned temporary directory")
    shutil.rmtree(root)
    # Current source work owns priority; historical census uses only the remaining budget.
    remaining = deadline - time.monotonic()
    historical = None
    if remaining > 0:
        historical = await run_burn_severity_forward(replace(config, time_budget_seconds=min(300.0, remaining)))
    return {"status": "checked", "publication": publication, "historical": historical, "source": staged}
