"""Merge one poll's day-bucketed rows into their published day, replacing a superseded station's block.

PORTED FROM `weather_observations/adapter.py` / `water_gauges.py::merge_water_gauges_day`, because
this lane shares their shape -- a day is filled by many INCREMENTAL polls over time, never one
settled-day fetch -- but the MERGE UNIT IS NOT ONE GRAIN, AND THAT IS THE DEVIATION FROM BOTH
TEMPLATES.

`weather_observations`' grain `(latitude, longitude, observed_at)` names one row per repeat poll of
one fixed sample point, so "refresh the source columns on a grain match" is correct: the SAME
instant is always reported the same way. Here the registered grain is
`(sensor_id, observed_day, measurement_name)` (`warehouse/schemas/sensors.py::SENSORS_GRAIN`), and
one station-day is published as ALL OF a single winning report's (at most sixteen) measurement rows
at once (`rows.py::direct_sensor_tables`, mirroring the `DISTINCT ON` reduction now transcribed in
`sql/pipeline/direct/sensors/postgres_day_counts.sql`). If a later, newer report for the SAME
station-day reports a DIFFERENT set
of measurements -- a station that stopped sending `windGust`, say -- a per-grain refresh would leave
the stale `windGust` row behind forever, because "windGust" is simply absent from the new report and
never revisits that grain to delete it. So the merge unit here is the WHOLE (sensor_id, observed_day)
BLOCK: a later report replaces every row of the block at once, never row-by-row.

"Later" is decided by `observed_at`, mirroring that reduction's `observedAt DESC` winner-take-all --
an incoming block whose `observed_at` is at least as new as the published block's REPLACES it
wholesale (`>=` rather than `>`, so a repeat poll of the identical winning instant is an idempotent
refresh, not a no-op skip); an incoming block that is OLDER is discarded, because a newer report was
already captured (by a prior run of this same writer, or -- before 2026-09-07 -- by the
`pipeline/lanes/sensors.py` Postgres adapter this writer replaced and which was deleted with that
swap, whose published days are still in the bucket) and NWS's rolling window can
still resurface that same historical instant on a later poll before it ages out. This is the direct
consequence of the rolling floor `source.py` documents: the SAME station-day can appear across many
consecutive polls as the retention window slides past it, and every one of those polls must agree on
which report won without ever regressing to an older one.

A published day this writer has never merged into carries `existing=None`, matching the sibling
templates: every incoming block is then, trivially, "added" rather than "replaced".
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]

from agri_data_service.foundation.parquet.paths import partition_day_statuses
from agri_data_service.pipeline.constants import LANE_BASE_ZOOM_TIER
from agri_data_service.warehouse.schemas.sensors import SENSORS_GRAIN, SENSORS_SCHEMA, SENSORS_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

#: `Final` so the value narrows to the `PartitionKind` literal rather than to bare `str`. Matches the
#: kind the retired Postgres-reading adapter used (`pipeline/lanes/sensors.py::export_sensors_day`,
#: deleted 2026-09-07), so a day this writer publishes lands in the SAME namespace as the days that
#: adapter already published.
SENSORS_DIRECT_KIND: Final = "observed"

#: The first two components of the registered grain -- the block key this adapter merges at. The
#: third component, `measurement_name`, is not a merge key here: see this module's docstring for why
#: the merge unit is the whole (sensor_id, observed_day) block, not the individual grain.
_SENSOR_ID_COLUMN: Final = SENSORS_GRAIN[0]
_OBSERVED_DAY_COLUMN: Final = SENSORS_GRAIN[1]


class DirectSensorsError(ValueError):
    """Raised when a polled row or a published day cannot satisfy the direct-write contract."""


@dataclass(frozen=True, slots=True)
class DirectSensorsWriteResult:
    """The lane adapter result consumed by the shared lane-day finalizer."""

    part_count: int
    row_count: int
    byte_count: int
    absence_recorded: bool = False


@dataclass(frozen=True, slots=True)
class DirectSensorsMerge:
    """One lossless, block-replacing merge at the registered sensors grain."""

    table: pa.Table
    existing_rows: int
    incoming_rows: int
    added_rows: int
    updated_rows: int
    stale_rows: int
    added_blocks: int
    replaced_blocks: int
    stale_blocks: int


def _validate_table(table: pa.Table, *, label: str) -> None:
    """Refuse a base table that is not exactly the registered schema."""
    if table.schema != SENSORS_SCHEMA.arrow_schema:
        raise DirectSensorsError(f"{label} sensors table does not match the registered Arrow schema")


def _block_sensor_id(row: Mapping[str, object], *, expected_day: date, label: str) -> str:
    """Validate one row belongs to the expected day and return its non-null station identity."""
    observed_day = row.get(_OBSERVED_DAY_COLUMN)
    if observed_day != expected_day:
        raise DirectSensorsError(
            f"a {label} sensors row names observed_day={observed_day!r}, expected {expected_day.isoformat()}"
        )
    sensor_id = row.get(_SENSOR_ID_COLUMN)
    if not isinstance(sensor_id, str) or not sensor_id:
        raise DirectSensorsError(f"a {label} sensors row at the base rung has no sensor_id")
    return sensor_id


def _group_by_sensor(
    rows: Sequence[Mapping[str, object]], *, expected_day: date, label: str
) -> dict[str, list[Mapping[str, object]]]:
    """Group base rows into per-station blocks, validating every row's day along the way."""
    blocks: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in rows:
        sensor_id = _block_sensor_id(row, expected_day=expected_day, label=label)
        blocks[sensor_id].append(row)
    return blocks


def _block_observed_at(rows: Sequence[Mapping[str, object]], *, sensor_id: str, label: str) -> datetime:
    """Return one station-day block's single shared `observed_at`, refusing a block that disagrees with itself.

    Every row a single winning report fans out to (`rows.py::_measurement_rows`) shares one
    `observed_at` by construction; a block that disagrees indicates rows from two different reports
    were merged into one block upstream, which this adapter must never merge past silently.
    """
    observed_at_values = {row.get("observed_at") for row in rows}
    if len(observed_at_values) != 1:
        raise DirectSensorsError(
            f"{label} sensors block for sensor_id={sensor_id!r} carries {len(observed_at_values)} "
            "distinct observed_at values, expected exactly one winning report per station-day"
        )
    (observed_at,) = observed_at_values
    if not isinstance(observed_at, datetime) or observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise DirectSensorsError(f"{label} sensors block for sensor_id={sensor_id!r} has no timezone-aware observed_at")
    return observed_at


def merge_sensors_day(
    existing: pa.Table | None,
    incoming: pa.Table,
    *,
    day: date,
) -> DirectSensorsMerge:
    """Replace a station's whole measurement block when this poll's report is at least as new, else keep it.

    See this module's docstring for why the merge unit is the (sensor_id, observed_day) block rather
    than the individual (sensor_id, observed_day, measurement_name) grain.
    """
    if incoming.num_rows == 0:
        raise DirectSensorsError("a direct sensors merge cannot publish an empty poll")
    _validate_table(incoming, label="incoming")
    if existing is not None:
        _validate_table(existing, label="published")

    existing_rows = [] if existing is None else existing.to_pylist()
    incoming_rows = incoming.to_pylist()
    existing_blocks = _group_by_sensor(existing_rows, expected_day=day, label="published")
    incoming_blocks = _group_by_sensor(incoming_rows, expected_day=day, label="incoming")

    merged_blocks: dict[str, list[Mapping[str, object]]] = dict(existing_blocks)
    added_rows = updated_rows = stale_rows = 0
    added_blocks = replaced_blocks = stale_blocks = 0

    for sensor_id, rows in incoming_blocks.items():
        # Validated UNCONDITIONALLY, even for a brand-new block with no published counterpart yet:
        # a malformed multi-report block accepted here would not just publish wrong content once, it
        # would poison every LATER merge for this station-day too, since a future poll's comparison
        # against this same block would then raise on the mismatch it should have caught now.
        incoming_observed_at = _block_observed_at(rows, sensor_id=sensor_id, label="incoming")
        published_block = existing_blocks.get(sensor_id)
        if published_block is None:
            merged_blocks[sensor_id] = rows
            added_blocks += 1
            added_rows += len(rows)
            continue
        published_observed_at = _block_observed_at(published_block, sensor_id=sensor_id, label="published")
        if incoming_observed_at >= published_observed_at:
            merged_blocks[sensor_id] = rows
            replaced_blocks += 1
            updated_rows += len(rows)
        else:
            stale_blocks += 1
            stale_rows += len(rows)

    merged_rows = [row for rows in merged_blocks.values() for row in rows]
    return DirectSensorsMerge(
        table=pa.Table.from_pylist(merged_rows, schema=SENSORS_SCHEMA.arrow_schema),
        existing_rows=len(existing_rows),
        incoming_rows=len(incoming_rows),
        added_rows=added_rows,
        updated_rows=updated_rows,
        stale_rows=stale_rows,
        added_blocks=added_blocks,
        replaced_blocks=replaced_blocks,
        stale_blocks=stale_blocks,
    )


@dataclass(slots=True)
class DirectSensorsForwardAdapter:
    """Merge one poll's day-bucketed rows into its published day while the caller holds the lane-day lock."""

    incoming: pa.Table
    merge: DirectSensorsMerge | None = field(default=None, init=False)

    async def __call__(
        self,
        session: AsyncSession,
        store: ObjectStore,
        *,
        day: date,
        run_id: str,
    ) -> DirectSensorsWriteResult:
        """Write one full z13 part; the shared finalizer owns tiers, prune, and markers."""
        del session, run_id
        keys = store.list_partition_keys(
            SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, year=day.year, month=day.month
        )
        status = partition_day_statuses(
            layer=SENSORS_STREAM,
            kind=SENSORS_DIRECT_KIND,
            zoom=LANE_BASE_ZOOM_TIER,
            first_day=day,
            last_day=day,
            keys=keys,
        )[day]
        if status == "data":
            existing = store.read_partition(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day)
            merged = merge_sensors_day(existing, self.incoming, day=day)
        elif status == "missing":
            merged = merge_sensors_day(None, self.incoming, day=day)
        elif status == "incomplete":
            if self.merge is not None:
                # The previous attempt formed this table before its first R2 mutation. Replay it
                # exactly, matching `water_gauges.py`'s/`weather_observations`'s discipline for the
                # same crash-recovery case.
                merged = self.merge
            else:
                existing = store.read_partition(SENSORS_STREAM, SENSORS_DIRECT_KIND, LANE_BASE_ZOOM_TIER, day)
                merged = merge_sensors_day(existing, self.incoming, day=day)
        else:
            raise DirectSensorsError(
                f"refusing to merge poll rows into sensors z13 {day.isoformat()} with status={status}"
            )
        # Save the complete intended population before the first object mutation, matching the
        # sibling writers' checkpoint discipline for object-store retries.
        self.merge = merged
        receipt = store.write_partition(
            merged.table,
            layer=SENSORS_STREAM,
            kind=SENSORS_DIRECT_KIND,
            zoom=LANE_BASE_ZOOM_TIER,
            day=day,
        )
        return DirectSensorsWriteResult(
            part_count=1,
            row_count=receipt.row_count,
            byte_count=receipt.byte_count,
        )


__all__ = [
    "SENSORS_DIRECT_KIND",
    "DirectSensorsError",
    "DirectSensorsForwardAdapter",
    "DirectSensorsMerge",
    "DirectSensorsWriteResult",
    "merge_sensors_day",
]
