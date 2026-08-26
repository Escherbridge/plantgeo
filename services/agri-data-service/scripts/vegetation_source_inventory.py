"""Read-only inventory of governed vegetation rows and their Parquet ladder."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import Counter
from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import text

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session
from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.foundation.parquet.zoom import ZOOM_TIERS, ZoomTier
from agri_data_service.pipeline.parquet.lane_registry import spatial_cell_ids
from agri_data_service.pipeline.parquet.objectstore import ObjectStore
from agri_data_service.pipeline.parquet.vegetation_rewrite import LEGACY_VEGETATION_BASE_SCHEMA
from agri_data_service.pipeline.vegetation_source import fetch_source_cell_days
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_SCHEMA

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

LAYER = "vegetation"
KIND = "observed"


def _day_hash(days: list[date]) -> str:
    return hashlib.sha256("\n".join(day.isoformat() for day in sorted(days)).encode()).hexdigest()


def _keys(store: ObjectStore, zoom: ZoomTier, first_day: date, last_day: date) -> tuple[str, ...]:
    found: list[str] = []
    for year in range(first_day.year, last_day.year + 1):
        found.extend(store.list_partition_keys(LAYER, KIND, zoom, year=year))
    return tuple(found)


def _schema_label(schema: pa.Schema) -> str:
    """Classify only the two approved vegetation base schemas by exact Arrow equality."""
    if schema.equals(VEGETATION_PLANE_SCHEMA.arrow_schema, check_metadata=False):
        return "current"
    if schema.equals(LEGACY_VEGETATION_BASE_SCHEMA, check_metadata=False):
        return "exact_legacy"
    return "unknown"


async def inventory(first_day: date, last_day: date, coverage_last_day: date) -> dict[str, object]:
    if last_day < first_day:
        raise ValueError("last day precedes first day")
    if not first_day <= coverage_last_day <= last_day:
        raise ValueError("coverage last day must fall inside the inventory window")
    store = ObjectStore.from_settings()
    database_url = settings.require_local_source_loader_database_url()
    async with local_source_loader_session(database_url) as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        cell_ids = await spatial_cell_ids(session)
        source_rows = await fetch_source_cell_days(
            session,
            cell_ids=cell_ids,
            first_day=first_day,
            last_day=last_day,
        )
        await session.rollback()

    source_days = {row.observed_day for row in source_rows}
    statuses_by_zoom = {
        zoom: partition_day_statuses(
            layer=LAYER,
            kind=KIND,
            zoom=zoom,
            first_day=first_day,
            last_day=last_day,
            keys=_keys(store, zoom, first_day, last_day),
        )
        for zoom in ZOOM_TIERS
    }
    base_statuses = statuses_by_zoom[ZOOM_TIERS[-1]]
    missing_source_days = [day for day in source_days if base_statuses[day] != "data"]
    unsettled_source_days = [day for day in source_days if day > coverage_last_day]
    missing_settled_absences = [
        day
        for day, status in base_statuses.items()
        if day <= coverage_last_day and day not in source_days and status != "absent"
    ]
    schema_counts: Counter[str] = Counter()
    for day, status in base_statuses.items():
        if status != "data":
            continue
        try:
            table = store.read_partition(LAYER, KIND, ZOOM_TIERS[-1], day)
        except Exception as error:
            schema_counts[f"read_error:{type(error).__name__}"] += 1
            continue
        schema_counts[_schema_label(table.schema)] += 1

    async with local_source_loader_session(database_url) as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        cell_ids_after = await spatial_cell_ids(session)
        source_rows_after = await fetch_source_cell_days(
            session,
            cell_ids=cell_ids_after,
            first_day=first_day,
            last_day=last_day,
        )
        await session.rollback()
    source_changed = cell_ids_after != cell_ids or source_rows_after != source_rows
    unsafe_schema_count = schema_counts["unknown"] + sum(
        count for label, count in schema_counts.items() if label.startswith("read_error:")
    )
    clean = not source_changed and unsafe_schema_count == 0

    return {
        "clean": clean,
        "first_day": first_day.isoformat(),
        "last_day": last_day.isoformat(),
        "coverage_last_day": coverage_last_day.isoformat(),
        "governed": {
            "cell_days": len(source_rows),
            "cells": len({row.cell_id for row in source_rows}),
            "distinct_days": len(source_days),
            "duplicated_cell_days": sum(row.source_release_count > 1 for row in source_rows),
        },
        "base_statuses": dict(sorted(Counter(base_statuses.values()).items())),
        "tier_statuses": {
            str(zoom): dict(sorted(Counter(statuses.values()).items())) for zoom, statuses in statuses_by_zoom.items()
        },
        "base_schemas": dict(sorted(schema_counts.items())),
        "source_changed_during_inventory": source_changed,
        "missing_source_days": {
            "count": len(missing_source_days),
            "sha256": _day_hash(missing_source_days),
            "first": min(missing_source_days).isoformat() if missing_source_days else None,
            "last": max(missing_source_days).isoformat() if missing_source_days else None,
        },
        "missing_settled_absences": {
            "count": len(missing_settled_absences),
            "sha256": _day_hash(missing_settled_absences),
        },
        "unsettled_source_days": [day.isoformat() for day in sorted(unsettled_source_days)],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--first-day", type=date.fromisoformat, default=date(2022, 8, 5))
    parser.add_argument("--last-day", type=date.fromisoformat, default=date(2026, 8, 25))
    parser.add_argument("--coverage-last-day", type=date.fromisoformat, default=date(2026, 8, 19))
    args = parser.parse_args()
    report = asyncio.run(inventory(args.first_day, args.last_day, args.coverage_last_day))
    print(json.dumps(report, sort_keys=True))
    if not report["clean"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
