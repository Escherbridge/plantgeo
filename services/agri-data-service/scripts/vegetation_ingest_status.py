"""Read-only PostgreSQL proof for the restored vegetation ingestion path."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import text

from agri_data_service.config import settings
from agri_data_service.db.engine import local_source_loader_session

if TYPE_CHECKING:
    from collections.abc import Mapping

_STATUS = text(
    """
    WITH raw AS (
        SELECT
            feature.properties->>'cellKey' AS cell_key,
            CASE
                WHEN pg_input_is_valid(substring(feature.properties->>'observedAt', 1, 10), 'date')
                THEN substring(feature.properties->>'observedAt', 1, 10)::date
            END AS observed_day,
            feature.created_at,
            feature.updated_at
        FROM geo.features AS feature
        INNER JOIN geo.layers AS layer ON layer.id = feature.layer_id
        WHERE layer.name = 'vegetation'
    ),
    changed_cell_days AS (
        SELECT DISTINCT cell_key, observed_day
        FROM raw
        WHERE (created_at >= :since OR updated_at >= :since)
          AND cell_key IS NOT NULL
          AND btrim(cell_key) <> ''
          AND observed_day IS NOT NULL
    ),
    governed_changed_cell_days AS (
        SELECT DISTINCT changed.cell_key, changed.observed_day
        FROM changed_cell_days AS changed
        INNER JOIN agri.spatial_cell AS cell
          ON cell.cell_key = 'sentinel2-ndvi-0p25deg:' || changed.cell_key
        INNER JOIN agri.forecast_series AS series ON series.spatial_cell_id = cell.id
        INNER JOIN agri.forecast_observation AS observation ON observation.series_id = series.id
        INNER JOIN agri.source_release AS release ON release.id = observation.source_release_id
        INNER JOIN agri.data_source AS source ON source.id = release.data_source_id
        WHERE series.metric_name = 'ndvi'
          AND series.source_transform_version = 'sentinel2-ndvi-daily-cell-mean-v1'
          AND source.key = 'sentinel2-ndvi-l2a'
          AND observation.quality_flag = 'accepted'
          AND (observation.observed_at AT TIME ZONE 'UTC')::date = changed.observed_day
    )
    SELECT
        COUNT(*)::bigint AS raw_rows,
        COUNT(DISTINCT cell_key)::bigint AS cells,
        MIN(observed_day) AS first_day,
        MAX(observed_day) AS last_day,
        MAX(created_at) AS newest_created_at,
        MAX(updated_at) AS newest_updated_at,
        COUNT(*) FILTER (
            WHERE created_at >= :since OR updated_at >= :since
        )::bigint AS rows_changed_since,
        COUNT(*) FILTER (WHERE observed_day IS NULL)::bigint AS invalid_observed_day_rows,
        (SELECT COUNT(*)::bigint FROM changed_cell_days) AS changed_cell_days_since,
        (
            SELECT COUNT(*)::bigint
            FROM changed_cell_days AS changed
            WHERE NOT EXISTS (
                SELECT 1
                FROM governed_changed_cell_days AS governed
                WHERE governed.cell_key = changed.cell_key
                  AND governed.observed_day = changed.observed_day
            )
        ) AS changed_cell_days_not_governed
    FROM raw
    """
)


def _optional_isoformat(value: object) -> str | None:
    """Render one nullable date or timestamp from the aggregate row."""
    if value is None:
        return None
    if not isinstance(value, (date, datetime)):
        raise TypeError(f"expected a date or timestamp, got {type(value).__name__}")
    return value.isoformat()


def _required_int(row: Mapping[str, object], key: str) -> int:
    value = row[key]
    if not isinstance(value, int):
        raise TypeError(f"expected integer aggregate {key}, got {type(value).__name__}")
    return value


def _status_payload(row: Mapping[str, object], since: datetime) -> dict[str, object]:
    """Convert one aggregate row into the stable empty-safe operator payload."""
    return {
        "cells": _required_int(row, "cells"),
        "changed_cell_days_not_governed": _required_int(row, "changed_cell_days_not_governed"),
        "changed_cell_days_since": _required_int(row, "changed_cell_days_since"),
        "first_day": _optional_isoformat(row["first_day"]),
        "invalid_observed_day_rows": _required_int(row, "invalid_observed_day_rows"),
        "last_day": _optional_isoformat(row["last_day"]),
        "newest_created_at": _optional_isoformat(row["newest_created_at"]),
        "newest_updated_at": _optional_isoformat(row["newest_updated_at"]),
        "raw_rows": _required_int(row, "raw_rows"),
        "rows_changed_since": _required_int(row, "rows_changed_since"),
        "since": since.isoformat(),
    }


async def status(since: datetime) -> dict[str, object]:
    async with local_source_loader_session(settings.require_local_source_loader_database_url()) as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        row = (await session.execute(_STATUS, {"since": since})).mappings().one()
        await session.rollback()
    return _status_payload(row, since)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", type=datetime.fromisoformat, required=True)
    args = parser.parse_args()
    if args.since.utcoffset() is None:
        parser.error("--since must include a timezone offset")
    print(json.dumps(asyncio.run(status(args.since)), sort_keys=True))


if __name__ == "__main__":
    main()
