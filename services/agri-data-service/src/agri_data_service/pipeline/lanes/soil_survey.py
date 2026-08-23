"""Export the current SSURGO delineation snapshot from Postgres to a Parquet release partition.

Layer L3: may import `foundation` and `warehouse`; may NOT import method, planes, or interface.

THIS LANE IS STATIC, horizon: none (docs/lanes/soil-survey.md sections 2, 7) -- there is no
forecaster and no `kind=forecast` stream for this lane; only `kind=observed` is ever written.
There is also no daily observation to pull: what changes is a survey area's own irregular
republication vintage, not a periodic re-sample. So `day` here names the export RUN's release
day (broadcast onto every row as `release_day`), never a predicate on when a delineation was
observed -- see `sql/pipeline/soil_survey_day_export.sql` for the full reasoning.

Combines both proven shapes rather than picking one: batched reads on the way IN, like
`signal.read_signal_day`, because a release's key list can be enormous (the PNW envelope alone
measures 1,507,623 delineations, docs/lanes/soil-survey.md section 5 point 8) and this lane's
rows each carry a polygon, unlike signal's scalar observations. Sliced part files on the way
OUT, like `watersheds.export_watersheds_release`, because a geometry relation must never be
sized by row count alone -- geo.drought_areas is 995 rows and 500 MB.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.warehouse.schemas.soil_survey import SOIL_SURVEY_SCHEMA, SOIL_SURVEY_STREAM

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore, ParquetWriteReceipt

_RELEASE_EXPORT_SQL: Final = text(load_query_sql("pipeline/soil_survey_day_export.sql"))

# Matches SOIL_SURVEY_PRODUCER in src/lib/server/services/usda-soil.ts:69, the producer
# namespace `geo.geometry.natural_key` is built from. Restated rather than imported: this
# lane's pipeline layer cannot reach the TS module, and Python's own identity.py lives under
# `ingest`, which the six-layer lattice does not let `pipeline` import either (layer-lanes.md
# section 1).
_PRODUCER: Final = "usda-sda"

# `uq_geometry_current` makes a natural_key lookup index-backed regardless of batch size
# (drizzle/0008_geometry_dimension.sql:76), so unlike signal.py's CELL_BATCH_SIZE this bound is
# not working around a missing index. It exists to keep one Postgres round trip -- the array
# parameter and the full SSURGO attribute set plus WKB per delineation it returns -- bounded,
# given the measured scale of a real release (see the module docstring). Smaller than signal's
# 250: signal's rows are scalar observations, this lane's rows each carry a polygon.
POLYGON_KEY_BATCH_SIZE: Final = 200

# Bounds one PART FILE's row count, independent of POLYGON_KEY_BATCH_SIZE above -- that constant
# bounds a Postgres round trip, this one bounds a Parquet object. Deliberately conservative:
# never size a geometry relation by row count alone, geo.drought_areas is 995 rows and 500 MB.
# Multiple parts under one day still read as one present day; gap detection lists the day
# directory rather than opening a file (foundation/parquet/paths.py MAX_PART_INDEX).
ROWS_PER_PART: Final = 500


class SoilSurveyExportError(RuntimeError):
    """Raised when a soil-survey release cannot be exported as requested."""


def _natural_key(mupolygonkey: str) -> str:
    """Build the namespaced `geo.geometry.natural_key` one delineation is stored under."""
    return f"{_PRODUCER}:{mupolygonkey}"


async def read_soil_survey_release(
    session: AsyncSession,
    *,
    mupolygonkeys: Sequence[str],
    release_day: date,
) -> pa.Table:
    """Return the current published state of every requested SSURGO delineation.

    Reads in `POLYGON_KEY_BATCH_SIZE` batches and concatenates, the same shape as
    `signal.read_signal_day`. The result is unsorted; `write_partition` sorts each part to the
    grain, which is what produces the compression clustering.
    """
    if not mupolygonkeys:
        raise SoilSurveyExportError(
            "a soil-survey release needs at least one mupolygonkey; an empty batch queries nothing"
        )

    columns: dict[str, list[object]] = {name: [] for name in SOIL_SURVEY_SCHEMA.column_names}
    for start in range(0, len(mupolygonkeys), POLYGON_KEY_BATCH_SIZE):
        batch = list(mupolygonkeys[start : start + POLYGON_KEY_BATCH_SIZE])
        natural_keys = [_natural_key(key) for key in batch]
        result = await session.execute(_RELEASE_EXPORT_SQL, {"natural_keys": natural_keys, "release_day": release_day})
        for row in result.mappings():
            for name, values in columns.items():
                values.append(row[name])

    return pa.table({name: pa.array(values) for name, values in columns.items()}).cast(SOIL_SURVEY_SCHEMA.arrow_schema)


async def export_soil_survey_release(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    mupolygonkeys: Sequence[str],
) -> tuple[ParquetWriteReceipt, ...]:
    """Export one release of the current SSURGO snapshot, spilling across parts as needed.

    A batch that matches nothing currently published (every requested key has since been
    superseded or was never real) still reaches `store.write_partition` with a zero-row table on
    its one (and only) part, so the writer's own `EmptyPartitionError` surfaces the governed
    absence, rather than this function silently returning no receipts for a caller to misread as
    "nothing to export" -- the same contract `export_watersheds_release` uses for the same
    reason.
    """
    table = await read_soil_survey_release(session, mupolygonkeys=mupolygonkeys, release_day=day)
    part_count = max(1, math.ceil(table.num_rows / ROWS_PER_PART))
    return tuple(
        store.write_partition(
            table.slice(part_index * ROWS_PER_PART, ROWS_PER_PART),
            layer=SOIL_SURVEY_STREAM,
            kind="observed",
            day=day,
            part_index=part_index,
        )
        for part_index in range(part_count)
    )
