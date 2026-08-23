"""Export one day of the `fire-perimeters` lane from `geo.features` to a Parquet partition.

Layer L3: may import `foundation` and `warehouse`; may NOT import method, planes, or interface.
See `docs/lanes/fire-perimeters.md` for the source/grain evidence and
`sql/pipeline/fire_perimeters_day_export.sql` for the query this module executes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.warehouse.schemas.fire_perimeters import (
    FIRE_PERIMETERS_GRAIN,
    FIRE_PERIMETERS_SCHEMA,
    FIRE_PERIMETERS_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import AbsenceWriteReceipt, ObjectStore, ParquetWriteReceipt

_DAY_EXPORT_SQL: Final = text(load_query_sql("pipeline/fire_perimeters_day_export.sql"))

# `docs/lanes/fire-perimeters.md` #5 measured 130,583 B of geometry per published row on average --
# the heaviest lane besides `geo.drought_areas`/`burn-severity` -- so a day dense enough to land
# many incidents at once could still spill a single part past a sane upload size even though the
# WHOLE layer is only 177 rows. 8 MiB keeps each part comfortably under WFIGS_BOUNDS' own 16 MiB
# response cap (`ingest/wfigs.py:81`), with headroom for Parquet's own framing. Never size this
# lane by row count -- size it by geometry bytes.
MAX_PART_PAYLOAD_BYTES: Final = 8 * 1024 * 1024


class FirePerimetersExportError(RuntimeError):
    """Raised when a fire-perimeters export outcome violates its own carries-one-or-the-other invariant."""


@dataclass(frozen=True, slots=True)
class FirePerimetersExportOutcome:
    """One day's export result: either the part files it produced, or the governed absence it recorded."""

    parts: tuple[ParquetWriteReceipt, ...]
    absence: AbsenceWriteReceipt | None

    def __post_init__(self) -> None:
        if bool(self.parts) == (self.absence is not None):
            raise FirePerimetersExportError(
                "an export outcome must carry either part files or an absence marker, never both or neither"
            )


async def read_fire_perimeters_day(session: AsyncSession, *, day: date) -> pa.Table:
    """Return every WFIGS incident `geo.feature_observation_day` dates to `day`, unsorted.

    Zero rows is a legitimate answer -- most calendar days see no incident dated to them at all
    (this lane is event-shaped, not daily_series; `docs/lanes/fire-perimeters.md` #4) -- so unlike
    the signal plane's cell-batch precondition, this function never raises on an empty result.
    """
    result = await session.execute(_DAY_EXPORT_SQL, {"observed_day": day})
    columns: dict[str, list[object]] = {name: [] for name in FIRE_PERIMETERS_SCHEMA.column_names}
    for row in result.mappings():
        for name, values in columns.items():
            values.append(row[name])
    return pa.table({name: pa.array(values) for name, values in columns.items()}).cast(
        FIRE_PERIMETERS_SCHEMA.arrow_schema
    )


def _chunk_row_indices_by_geometry_bytes(geometry_lengths: Sequence[int], *, max_bytes: int) -> list[list[int]]:
    """Split row positions into contiguous runs whose summed geometry bytes stay under `max_bytes`.

    Every chunk holds at least one row -- a single perimeter whose own WKB already exceeds the
    budget is not split further, mirroring `ingest/wfigs.py`'s own per-page backstop: "a single
    page that is still too heavy ... fails that page rather than being silently permitted through
    a wider ceiling."
    """
    chunks: list[list[int]] = [[]]
    running = 0
    for index, length in enumerate(geometry_lengths):
        if running and running + length > max_bytes:
            chunks.append([])
            running = 0
        chunks[-1].append(index)
        running += length
    return chunks


async def export_fire_perimeters_day(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    run_id: str,
) -> FirePerimetersExportOutcome:
    """Export one day of the fire-perimeters lane, spilling across parts by geometry bytes.

    `write_partition` refuses a zero-row table by design (`foundation/parquet/absence.py`), and a
    day with no incident dated to it is routine for an event-shaped lane rather than an upstream
    failure -- so an empty result is recorded with `store.write_absence` instead of attempted as a
    write. When rows exist they are sorted to the lane's grain BEFORE chunking, so each spilled
    part is a contiguous, non-overlapping slice of that order rather than an independently
    re-sorted arbitrary sample.
    """
    table = await read_fire_perimeters_day(session, day=day)
    if table.num_rows == 0:
        absence = GovernedAbsence(
            reason=(
                "no WFIGS incident's geo.feature_observation_day falls on this UTC day in "
                "geo.features; fire-perimeters is event-shaped, not daily_series "
                "(docs/lanes/fire-perimeters.md #4), so an empty day is routine, not a fetch failure"
            ),
            upstream_response=(
                "geo.features joined to geo.layers WHERE name='fire-perimeters' AND "
                f"geo.feature_observation_day(properties) = {day.isoformat()} returned 0 rows"
            ),
            recorded_at=datetime.now(UTC),
            run_id=run_id,
        )
        absence_receipt = store.write_absence(absence, layer=FIRE_PERIMETERS_STREAM, kind="observed", day=day)
        return FirePerimetersExportOutcome(parts=(), absence=absence_receipt)

    sorted_table = table.sort_by([(column, "ascending") for column in FIRE_PERIMETERS_GRAIN])
    geometry_lengths = [len(value.as_py()) for value in sorted_table.column("geometry_wkb")]
    parts = tuple(
        store.write_partition(
            sorted_table.take(pa.array(indices)),
            layer=FIRE_PERIMETERS_STREAM,
            kind="observed",
            day=day,
            part_index=part_index,
        )
        for part_index, indices in enumerate(
            _chunk_row_indices_by_geometry_bytes(geometry_lengths, max_bytes=MAX_PART_PAYLOAD_BYTES)
        )
    )
    return FirePerimetersExportOutcome(parts=parts, absence=None)
