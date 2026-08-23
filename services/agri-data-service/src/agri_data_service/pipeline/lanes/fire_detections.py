"""Export one day of NASA FIRMS active-fire detections, pre-aggregated to cell-day, to Parquet.

Layer L3: may import `foundation` and `warehouse`; may NOT import method, planes, or interface.
See `warehouse/schemas/fire_detections.py` for the grain decision and column provenance.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

import pyarrow as pa  # type: ignore[import-untyped]
from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.foundation.parquet.absence import GovernedAbsence
from agri_data_service.warehouse.schemas.fire_detections import FIRE_DETECTIONS_SCHEMA, FIRE_DETECTIONS_STREAM

if TYPE_CHECKING:
    from datetime import date

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import AbsenceWriteReceipt, ObjectStore, ParquetWriteReceipt

_DAY_EXPORT_SQL: Final = text(load_query_sql("pipeline/fire_detections_day_export.sql"))


class FireDetectionsExportError(RuntimeError):
    """Raised when a fire-detections day cannot be exported as written."""


async def read_fire_detections_day(
    session: AsyncSession,
    *,
    day: date,
    layer_id: str,
) -> pa.Table:
    """Return one day of FIRMS detections for `layer_id`, pre-aggregated to (cell, day) grain.

    Needs no cell-batching loop like the signal plane's: `ix_features_layer_observation_day`
    (`(layer_id, geo.feature_observation_day(properties))`, `drizzle/0031_observation_day_axis.sql`)
    makes one bounded, indexed execution sufficient for a day-scoped, one-layer read -- the same
    shape `weather_observations_day_export.sql` already rides. `layer_id` is resolved by the caller
    once (e.g. via `ingest.writer.resolve_layer_id`) rather than joined on `geo.layers.name` here;
    see the SQL file's own note for why a literal bound value is the proven-fast shape. A day with
    zero matching rows is a real possibility (a quiet day, or an ingest gap) and returns a zero-row
    table rather than raising -- `export_fire_detections_day` is what turns that into either a
    write or a governed absence.
    """
    if not layer_id.strip():
        raise FireDetectionsExportError("a fire-detections export needs a resolved layer_id")

    result = await session.execute(_DAY_EXPORT_SQL, {"layer_id": layer_id, "observed_day": day})
    columns: dict[str, list[object]] = {name: [] for name in FIRE_DETECTIONS_SCHEMA.column_names}
    for row in result.mappings():
        for name, values in columns.items():
            values.append(row[name])

    return pa.table({name: pa.array(values) for name, values in columns.items()}).cast(
        FIRE_DETECTIONS_SCHEMA.arrow_schema
    )


async def export_fire_detections_day(  # noqa: PLR0913 - one caller-supplied coordinate per arg, none foldable
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    layer_id: str,
    run_id: str,
    now: datetime | None = None,
) -> ParquetWriteReceipt | AbsenceWriteReceipt:
    """Export one settled day of the fire-detections cell-day aggregate, or record its absence.

    This lane's own history is the reason a zero-row day is never left to read as a silent no-op:
    a capped FIRMS ingest tick once reported `records_written=0` while quietly dropping 2,239 real
    detections, and the two looked identical without reading `details.dropped`
    (`docs/lanes/fire-detections.md` section 5.1). `write_partition` already refuses a zero-row
    table (`EmptyPartitionError`); rather than let that surface as an uncaught exception, a day with
    no rows is recorded as a governed absence -- honest about what THIS export actually observed
    (Postgres held zero published, geometry-linked, geometrically-placed rows for the day), not a
    claim about what FIRMS itself published, which only `pipeline/validation/fire-detections.py`
    can verify against the live availability table.
    """
    table = await read_fire_detections_day(session, day=day, layer_id=layer_id)
    if table.num_rows == 0:
        absence = GovernedAbsence(
            reason="geo.features held no published, geometry-linked fire-detections rows for this day",
            upstream_response=(
                f"0 rows returned from geo.features for layer_id={layer_id} on {day.isoformat()}; "
                "not cross-checked against FIRMS' live data_availability table"
            ),
            recorded_at=now if now is not None else datetime.now(UTC),
            run_id=run_id,
        )
        return store.write_absence(absence, layer=FIRE_DETECTIONS_STREAM, kind="observed", day=day)
    return store.write_partition(table, layer=FIRE_DETECTIONS_STREAM, kind="observed", day=day)
