"""Export the current SSURGO delineation snapshot from Postgres to a Parquet release partition.

Layer L3: may import `foundation` and `warehouse`; may NOT import method, planes, or interface.

THIS LANE IS STATIC, horizon: none (docs/lanes/soil-survey.md sections 2, 7) -- there is no
forecaster and no `kind=forecast` stream for this lane; only `kind=observed` is ever written.
There is also no daily observation to pull: what changes is a survey area's own irregular
republication vintage, not a periodic re-sample. So `day` here names the export RUN's release
day (broadcast onto every row as `release_day`), never a predicate on when a delineation was
observed -- see `sql/pipeline/soil_survey_day_export.sql` for the full reasoning.

THE WHOLE RELEASE IS NEVER IN MEMORY AT ONCE, AND THAT IS WHY THIS LANE CAN RUN AT ALL. The PNW
envelope alone measures 1,507,623 delineations (docs/lanes/soil-survey.md section 5 point 8) and
every row of this lane carries a polygon, unlike signal's scalar observations -- so a release read
into one table before any of it is written would be sized by the population rather than by a
budget. The export therefore STREAMS: the caller hands it key batches, each batch is read,
buffered, and flushed to `part-N` as soon as `ROWS_PER_PART` rows are in hand. Peak memory is one
part plus one batch, whatever the population is.

STREAMING IS ALSO WHAT MAKES THE PARTS ORDERED RATHER THAN MERELY SORTED. Key batches arrive in
`mupolygonkey` order, which IS this stream's grain (`SOIL_SURVEY_GRAIN`), so part N's keys all
precede part N+1's; `write_partition` sorting each part to the grain then produces one global
order across the whole release. The earlier read-everything-then-slice shape could only sort
within an arbitrary slice of an unsorted table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.warehouse.schemas.soil_survey import SOIL_SURVEY_SCHEMA, SOIL_SURVEY_STREAM

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
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
# 250: signal's rows are scalar observations, this lane's rows each carry a polygon. It is also
# the page size the registry's keyset walk pages the KEY list by, so one batch is one round trip
# on each side of the export rather than two unrelated numbers that happen to be near each other.
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


def _write_part(store: ObjectStore, part: pa.Table, *, day: date, part_index: int) -> ParquetWriteReceipt:
    """Upload one part file of this release at the base tier, the only rung a lane export writes."""
    return store.write_partition(
        part,
        layer=SOIL_SURVEY_STREAM,
        kind="observed",
        zoom=LANE_BASE_ZOOM_TIER,
        day=day,
        part_index=part_index,
    )


async def export_soil_survey_release(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    mupolygonkey_batches: AsyncIterator[Sequence[str]],
) -> tuple[ParquetWriteReceipt, ...]:
    """Stream one release of the current SSURGO snapshot to `part-0..part-N`, a bounded buffer at a time.

    EVERY PART IS RETURNED, AND THE COUNT IS THE PRUNE'S ONLY INPUT. `gap_fill._prune_surplus`
    deletes `part-<n>` for n at or above the number written, so a streamed export that reported
    fewer parts than it uploaded would ask the store to delete its own newest objects. The receipt
    tuple is therefore the whole record of the upload, never a sample of it -- which is also why
    the receipts, unlike the rows, are allowed to grow with the population: ~3,000 small frozen
    dataclasses for the full PNW universe, against the ~26 GB of geometry that never lands here.

    A release that matches nothing currently published -- every requested key superseded, or the
    lazily-warmed set still empty -- still makes exactly one `store.write_partition` call, with a
    zero-row table, so the writer's own `EmptyPartitionError` surfaces the governed absence rather
    than this function returning no receipts for a caller to misread as "nothing to export". That
    is the same contract `export_watersheds_release` uses, for the same reason.
    """
    receipts: list[ParquetWriteReceipt] = []
    buffered = SOIL_SURVEY_SCHEMA.arrow_schema.empty_table()
    async for batch in mupolygonkey_batches:
        read = await read_soil_survey_release(session, mupolygonkeys=batch, release_day=day)
        buffered = pa.concat_tables([buffered, read])
        while buffered.num_rows >= ROWS_PER_PART:
            receipts.append(_write_part(store, buffered.slice(0, ROWS_PER_PART), day=day, part_index=len(receipts)))
            # `combine_chunks` copies the carried-over rows into fresh buffers. A bare `slice` is a
            # view, so the remainder would pin every batch it was ever concatenated with alive and
            # the "bounded buffer" would grow with the release after all.
            buffered = buffered.slice(ROWS_PER_PART).combine_chunks()
    if buffered.num_rows or not receipts:
        receipts.append(_write_part(store, buffered, day=day, part_index=len(receipts)))
    return tuple(receipts)
