"""Export one release day of the burn-severity lane from Postgres to a Parquet partition.

Layer L3: may import `foundation` and `warehouse`; may NOT import method, planes, or interface.
See `docs/lanes/burn-severity.md` for the source, cadence, and the release-not-daily grain this
lane carries forward, and the SQL file's own header for the transcription this query is of.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_SCHEMA, BURN_SEVERITY_STREAM

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import (
        AbsenceWriteReceipt,
        ObjectStore,
        ParquetWriteReceipt,
    )

_BURN_SEVERITY_DAY_EXPORT_SQL: Final = text(load_query_sql("pipeline/burn_severity_day_export.sql"))

# MTBS's own paging ceiling (ingest/mtbs.py:180-183): a 50-row page of full-resolution polygon is
# already ~10.7 MB and the host 500s above ~100 rows. One release cohort in this bbox has never
# exceeded that ceiling (541 rows across all FIVE release days combined -- RUNBOOK section
# 0.21.3), but a part file is capped at the same row count the source itself proved safe to move
# in one piece, so an oversized future cohort spills to part-1, part-2, ... rather than growing
# one file without bound.
MAX_ROWS_PER_PART: Final = 100


async def read_burn_severity_release_day(session: AsyncSession, *, release_day: date) -> pa.Table:
    """Return every published MTBS fire dated to `release_day`'s release-publication date.

    Zero rows is an honest fact, not an error: either `release_day` is not one of MTBS's governed
    release dates, or that cohort's fires fell entirely outside this deployment's bounding box.
    `export_burn_severity_release_day` turns that into a governed absence rather than a failure.
    """
    result = await session.execute(_BURN_SEVERITY_DAY_EXPORT_SQL, {"release_day": release_day})
    columns: dict[str, list[object]] = {name: [] for name in BURN_SEVERITY_SCHEMA.column_names}
    for row in result.mappings():
        for name, values in columns.items():
            values.append(row[name])
    return pa.table({name: pa.array(values) for name, values in columns.items()}).cast(
        BURN_SEVERITY_SCHEMA.arrow_schema
    )


async def export_burn_severity_release_day(
    session: AsyncSession,
    store: ObjectStore,
    *,
    release_day: date,
    run_id: str,
) -> tuple[ParquetWriteReceipt, ...] | AbsenceWriteReceipt:
    """Export one MTBS release day, spilling across `part-N` files when the cohort is large.

    A release day the source cannot serve is recorded with `store.write_absence`, never written
    as a zero-row `write_partition` call -- the writer refuses that outright
    (`EmptyPartitionError`) precisely so an empty file can never masquerade as a settled day.
    """
    table = await read_burn_severity_release_day(session, release_day=release_day)
    if table.num_rows == 0:
        absence = GovernedAbsence(
            reason=(
                "no MTBS fire in geo.features (layer='burn-severity') is dated to release day "
                f"{release_day.isoformat()}: either this day is not one of MTBS's governed "
                "release dates, or that cohort's fires fell entirely outside this deployment's "
                "bounding box"
            ),
            upstream_response="geo.features query returned 0 rows",
            recorded_at=datetime.now(UTC),
            run_id=run_id,
        )
        return store.write_absence(
            absence,
            layer=BURN_SEVERITY_STREAM,
            kind="observed",
            zoom=LANE_BASE_ZOOM_TIER,
            day=release_day,
        )

    sorted_table = table.sort_by([(column, "ascending") for column in BURN_SEVERITY_SCHEMA.sort_columns])
    return tuple(
        store.write_partition(
            sorted_table.slice(start, MAX_ROWS_PER_PART),
            layer=BURN_SEVERITY_STREAM,
            kind="observed",
            zoom=LANE_BASE_ZOOM_TIER,
            day=release_day,
            part_index=part_index,
        )
        for part_index, start in enumerate(range(0, sorted_table.num_rows, MAX_ROWS_PER_PART))
    )
