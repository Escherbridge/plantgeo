"""One entry per Parquet object stream: an adapter, a lane NATURE, and the clock that nature keys to.

Layer L2: may import `foundation`, `warehouse` and `db`; may NOT import method, planes, or
interface. It lives in `pipeline/parquet/` and deliberately NOT in `pipeline/lanes/` -- a module
inside that directory importing its siblings would (correctly) fail
`tests/test_layer_import_contract.py::test_lanes_do_not_import_each_other`. The registry is not a
lane; it is the one module allowed to know all thirteen of them.

EVERY LANE DECLARES WHAT ITS PARTITION DAY MEANS. `daily_series` and `release_series` key to a
publication lag off the calendar; `static_lookup` keys to a SOURCE WATERMARK -- the source's own
"when did this last change" -- and is otherwise idle. See `foundation/parquet/lane_contract.py`
for the vocabulary and `AGENTS.md` in this directory for the floor/lag evidence table, which
floors are declared and which are fallbacks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol
from uuid import UUID

from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.foundation.parquet.calendar import (
    CALENDAR_REQUIRED_FORWARD_DAYS,
    CALENDAR_STREAM,
    CALENDAR_VERSION_FORWARD_DAYS,
    required_calendar_version_day,
)
from agri_data_service.foundation.parquet.lane_contract import (
    LaneNature,
    SourceWatermark,
    nature_permits_cadence,
    nature_permits_forecast,
    newest_covered_day,
    validate_lane_nature,
)
from agri_data_service.foundation.parquet.paths import validate_layer_slug
from agri_data_service.pipeline.lanes import LANE_BASE_ZOOM_TIER
from agri_data_service.pipeline.lanes.burn_severity import export_burn_severity_release_day
from agri_data_service.pipeline.lanes.calendar import export_calendar_version
from agri_data_service.pipeline.lanes.drought import export_drought_release
from agri_data_service.pipeline.lanes.evacuation_zones import export_evacuation_zones_day
from agri_data_service.pipeline.lanes.fire_detections import export_fire_detections_day
from agri_data_service.pipeline.lanes.fire_perimeters import export_fire_perimeters_day
from agri_data_service.pipeline.lanes.sensors import export_sensors_day
from agri_data_service.pipeline.lanes.signal import export_signal_day
from agri_data_service.pipeline.lanes.soil_survey import POLYGON_KEY_BATCH_SIZE, export_soil_survey_release
from agri_data_service.pipeline.lanes.vegetation import export_vegetation_day
from agri_data_service.pipeline.lanes.water_gauges import export_water_gauges_day
from agri_data_service.pipeline.lanes.watersheds import export_watersheds_release
from agri_data_service.pipeline.lanes.weather_observations import export_weather_observations_day
from agri_data_service.pipeline.parquet.objectstore import (
    AbsenceWriteReceipt,
    EmptyPartitionError,
    ParquetWriteReceipt,
)
from agri_data_service.warehouse.parquet.schema import SIGNAL_PLANE_STREAM
from agri_data_service.warehouse.schemas.burn_severity import BURN_SEVERITY_STREAM
from agri_data_service.warehouse.schemas.drought import DROUGHT_STREAM
from agri_data_service.warehouse.schemas.evacuation_zones import EVACUATION_ZONES_STREAM
from agri_data_service.warehouse.schemas.fire_detections import FIRE_DETECTIONS_STREAM
from agri_data_service.warehouse.schemas.fire_perimeters import FIRE_PERIMETERS_STREAM
from agri_data_service.warehouse.schemas.sensors import SENSORS_STREAM
from agri_data_service.warehouse.schemas.soil_survey import SOIL_SURVEY_STREAM
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_STREAM
from agri_data_service.warehouse.schemas.water_gauges import WATER_GAUGES_STREAM
from agri_data_service.warehouse.schemas.watersheds import WATERSHEDS_STREAM
from agri_data_service.warehouse.schemas.weather_observations import WEATHER_OBSERVATIONS_STREAM

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.elements import TextClause

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

_SPATIAL_CELL_IDS_SQL: Final = text(load_query_sql("pipeline/lane_registry_spatial_cell_ids.sql"))
_LAYER_ID_SQL: Final = text(load_query_sql("pipeline/lane_registry_layer_id.sql"))
_SENSOR_STATION_IDS_SQL: Final = text(load_query_sql("pipeline/lane_registry_sensor_station_ids.sql"))
_SOIL_SURVEY_POLYGON_KEYS_SQL: Final = text(load_query_sql("pipeline/lane_registry_soil_survey_polygon_keys.sql"))

# The three static lanes with a Postgres source. Each query is transcribed from its own lane's day
# export so the watermark and the snapshot describe exactly one population; see the file headers.
_WATERSHEDS_WATERMARK_SQL: Final = text(load_query_sql("pipeline/lane_watermark_watersheds.sql"))
_EVACUATION_ZONES_WATERMARK_SQL: Final = text(load_query_sql("pipeline/lane_watermark_evacuation_zones.sql"))
_SOIL_SURVEY_WATERMARK_SQL: Final = text(load_query_sql("pipeline/lane_watermark_soil_survey.sql"))


class LaneRegistryError(RuntimeError):
    """Raised when a lane's arguments cannot be resolved, or an export reports an impossible shape."""


@dataclass(frozen=True, slots=True)
class LaneRunResult:
    """One lane-day's export, normalised across the four shapes the eleven exporters return."""

    part_count: int
    row_count: int
    byte_count: int
    absence_recorded: bool


class LaneAdapter(Protocol):
    """Uniform per-lane entry point: resolve this lane's own arguments, then export exactly one day."""

    async def __call__(
        self,
        session: AsyncSession,
        store: ObjectStore,
        *,
        day: date,
        run_id: str,
    ) -> LaneRunResult: ...


class LaneWatermarkResolver(Protocol):
    """Read one static lane's source watermark: the day its reference content last changed."""

    async def __call__(
        self,
        session: AsyncSession,
        store: ObjectStore,
        *,
        today: date,
    ) -> SourceWatermark: ...


@dataclass(frozen=True, slots=True)
class LaneRegistration:
    """One stream's gap-fill contract: what its partition day MEANS, and the clock that decides it."""

    slug: str
    adapter: LaneAdapter
    history_floor: date
    publication_lag_days: int
    nature: LaneNature
    floor_basis: str
    # Days between publications, counted from `history_floor`. 1 means "every day is a candidate".
    # A weekly source registered as daily is not wrong, but it spends the whole backlog writing
    # honest-yet-pointless absence markers for the six days a week it was never going to publish --
    # measured at ~2,000 for `burn-severity` before its five real releases are reached.
    cadence_days: int = 1
    # The `method/monte_carlo/` module stem that forecasts this lane, or None for `horizon: none`.
    # Naming the MODULE rather than carrying a bare boolean is what lets a test compare the claim
    # against the filesystem: `layer-lanes.md` §2 makes shipping-a-forecaster and claiming-a-horizon
    # the same fact, so a lane that disagrees with its own directory is a defect, not a nuance.
    forecast_module: str | None = None
    # A `static_lookup` lane's clock. Mandatory for that nature and forbidden for the others.
    watermark: LaneWatermarkResolver | None = None

    def __post_init__(self) -> None:
        validate_layer_slug(self.slug)
        validate_lane_nature(self.nature)
        if self.cadence_days < 1:
            raise LaneRegistryError(f"lane {self.slug!r} declares a cadence of under one day")
        if self.cadence_days > 1 and not nature_permits_cadence(self.nature):
            raise LaneRegistryError(
                f"lane {self.slug!r} is {self.nature} and declares a {self.cadence_days}-day cadence; only a "
                "release_series has a publication rhythm to step over. A daily series that skips days is "
                "either not daily or not a series."
            )
        if self.publication_lag_days < 0:
            raise LaneRegistryError(f"lane {self.slug!r} declares a negative publication lag")
        if not self.floor_basis.strip():
            raise LaneRegistryError(
                f"lane {self.slug!r} must cite where its history floor came from; an uncited floor is a guess "
                "that reads as a measurement"
            )
        if self.forecast_module is not None and not nature_permits_forecast(self.nature):
            raise LaneRegistryError(
                f"lane {self.slug!r} is a static_lookup and names the forecaster {self.forecast_module!r}; "
                "reference data has no time axis to project along, so there is nothing a forecast of it "
                "could mean"
            )
        if self.nature == "static_lookup" and self.watermark is None:
            raise LaneRegistryError(
                f"lane {self.slug!r} is a static_lookup and declares no source watermark; without one it is "
                "back to being schedule-driven, re-snapshotting whatever day the cron happened to run on"
            )
        if self.nature != "static_lookup" and self.watermark is not None:
            raise LaneRegistryError(
                f"lane {self.slug!r} is {self.nature} and declares a source watermark; a lane with a real time "
                "axis is driven by its publication lag, and two clocks would disagree"
            )
        if self.nature == "static_lookup" and self.publication_lag_days != 0:
            raise LaneRegistryError(
                f"lane {self.slug!r} is a static_lookup and declares a {self.publication_lag_days}-day "
                "publication lag; a version stamp is not settled by waiting, and subtracting a lag from one "
                "would date the snapshot before the change it records"
            )

    @property
    def forecastable(self) -> bool:
        """Whether this lane may publish a `kind=forecast` stream, derived from its nature and forecaster."""
        return nature_permits_forecast(self.nature) and self.forecast_module is not None


def normalise_export_outcome(
    outcome: ParquetWriteReceipt | AbsenceWriteReceipt | Sequence[ParquetWriteReceipt],
) -> LaneRunResult:
    """Fold any of the eleven exporters' return shapes into one result, or refuse an empty one."""
    if isinstance(outcome, AbsenceWriteReceipt):
        return _from_absence(outcome)
    if isinstance(outcome, ParquetWriteReceipt):
        return _from_parts((outcome,))
    return _from_parts(outcome)


def _from_parts(receipts: Sequence[ParquetWriteReceipt]) -> LaneRunResult:
    """Sum one day's part files. An empty tuple is refused: it reads as success and wrote nothing."""
    if not receipts:
        raise LaneRegistryError(
            "an export returned neither a part file nor an absence marker; a day that produced no object "
            "is a gap, and reporting it as a completed export would hide one"
        )
    return LaneRunResult(
        part_count=len(receipts),
        row_count=sum(receipt.row_count for receipt in receipts),
        byte_count=sum(receipt.byte_count for receipt in receipts),
        absence_recorded=False,
    )


def _from_absence(receipt: AbsenceWriteReceipt) -> LaneRunResult:
    """A governed-absence marker is a completed day with zero rows, never a written partition."""
    return LaneRunResult(part_count=0, row_count=0, byte_count=receipt.byte_count, absence_recorded=True)


def _coerce_uuid(value: object, *, column: str) -> UUID:
    """Narrow one untrusted result-set value to a UUID, naming the column when it is neither."""
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            raise LaneRegistryError(f"{column} {value!r} is not a uuid") from exc
    raise LaneRegistryError(f"{column} came back as {type(value).__name__}, not a uuid")


def _coerce_text(value: object, *, column: str) -> str:
    """Narrow one untrusted result-set value to a non-blank string, naming the column when it is not."""
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str) or not value.strip():
        raise LaneRegistryError(f"{column} came back as {value!r}, which is not a usable identifier")
    return value


async def _spatial_cell_ids(session: AsyncSession) -> tuple[UUID, ...]:
    """Read every analysis cell the signal and vegetation exports batch over."""
    result = await session.execute(_SPATIAL_CELL_IDS_SQL)
    return tuple(_coerce_uuid(row["cell_id"], column="agri.spatial_cell.id") for row in result.mappings())


async def spatial_cell_ids(session: AsyncSession) -> tuple[UUID, ...]:
    """Expose the bounded analysis-cell population to lane-specific admin commands."""
    return await _spatial_cell_ids(session)


async def _layer_id(session: AsyncSession, layer_name: str) -> str:
    """Resolve one `geo.layers` slug to its id, failing closed when the layer does not exist."""
    result = await session.execute(_LAYER_ID_SQL, {"layer_name": layer_name})
    ids = [_coerce_text(row["layer_id"], column="geo.layers.id") for row in result.mappings()]
    if len(ids) != 1:
        raise LaneRegistryError(
            f"geo.layers holds {len(ids)} rows named {layer_name!r}; a day export scoped to an unresolved "
            "layer would silently export the wrong population"
        )
    return ids[0]


async def _sensor_station_ids(session: AsyncSession, *, day: date) -> tuple[str, ...]:
    """Read the stations that published on one UTC day; an empty result means an empty day."""
    result = await session.execute(_SENSOR_STATION_IDS_SQL, {"observed_day": day})
    return tuple(_coerce_text(row["station_id"], column="sensors.sensor_id") for row in result.mappings())


async def _soil_survey_polygon_key_batches(session: AsyncSession) -> AsyncIterator[tuple[str, ...]]:
    """Walk the published SSURGO delineation keys one keyset page at a time, never as one list.

    THE POPULATION IS THE REASON, AND IT IS MEASURED. The PNW envelope holds 1,507,623 delineations
    (docs/lanes/soil-survey.md section 5 point 8) and production passed 200,000 on 2026-08-23 -- so
    the ceiling that used to guard a single read-everything query was not protecting this lane, it
    was failing it on every tick, which is why soil-survey has never written one object. A page is
    `POLYGON_KEY_BATCH_SIZE` keys, the same bound the export's own row read uses, so one page is one
    round trip on each side.

    Paging by the LAST KEY rather than by OFFSET is what keeps the walk bounded: an offset walk
    re-sorts the whole distinct set for every page, and page N costs N times page 1.
    """
    after_key = ""
    while True:
        result = await session.execute(
            _SOIL_SURVEY_POLYGON_KEYS_SQL, {"after_key": after_key, "page_size": POLYGON_KEY_BATCH_SIZE}
        )
        page = tuple(_coerce_text(row["mupolygonkey"], column="soil-survey.mupolygonkey") for row in result.mappings())
        if not page:
            return
        yield page
        after_key = page[-1]


async def _batches_after(
    first: tuple[str, ...],
    rest: AsyncIterator[tuple[str, ...]],
) -> AsyncIterator[tuple[str, ...]]:
    """Re-attach the page already pulled to prove a release is not empty, so no page is read twice."""
    yield first
    async for page in rest:
        yield page


# --- Source watermarks: the clock a static lane keys to ----------------------------------------
#
# A static lane's partition day is a VERSION STAMP, not an observation time, so nothing about a
# calendar day obliges it to write. It writes when its SOURCE changed, at the day the source
# changed, and is otherwise idle. Every query below returns `watermark_at` plus the columns that
# produced it, and every one of those columns is a CHANGE event -- never a poll clock. The rejected
# candidate is `geo.geometry.last_confirmed_at`, which advances on each re-fetch of unchanged
# ground (src/lib/server/services/usda-soil.ts:769,833); putting it in a version stamp would
# reinstate the daily churn this model removes.


def _coerce_instant(value: object, *, column: str, slug: str) -> datetime | None:
    """Narrow one untrusted `timestamptz` result value, refusing a naive one rather than guessing UTC."""
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise LaneRegistryError(
            f"{slug} watermark column {column} came back as {type(value).__name__}, not a timestamp"
        )
    if value.tzinfo is None:
        raise LaneRegistryError(
            f"{slug} watermark column {column} came back timezone-naive; assuming a zone for a version stamp "
            "would silently shift it by up to a day"
        )
    return value


def _coerce_row_count(value: object, *, slug: str) -> int:
    """Narrow the population count the watermark describes; it rides the basis string as evidence."""
    if isinstance(value, int):
        return value
    raise LaneRegistryError(f"{slug} watermark row_count came back as {type(value).__name__}, not an integer")


async def _read_source_watermark(
    session: AsyncSession,
    statement: TextClause,
    *,
    slug: str,
    evidence_columns: tuple[str, ...],
) -> SourceWatermark:
    """Run one watermark query and fold its single row into a cited `SourceWatermark`."""
    result = await session.execute(statement)
    rows = list(result.mappings())
    if len(rows) != 1:
        raise LaneRegistryError(
            f"{slug} watermark query returned {len(rows)} rows; it aggregates and must return exactly one"
        )
    row = rows[0]
    row_count = _coerce_row_count(row["row_count"], slug=slug)
    evidence = ", ".join(
        f"{column}={_render_instant(_coerce_instant(row[column], column=column, slug=slug))}"
        for column in evidence_columns
    )
    watermark_at = _coerce_instant(row["watermark_at"], column="watermark_at", slug=slug)
    if watermark_at is None:
        return SourceWatermark(day=None, basis=f"{slug}: no published rows ({evidence}; row_count={row_count})")
    # The instant rides ALONGSIDE the day it folds to; the day alone cannot separate two changes
    # made on one UTC date. See `foundation/parquet/lane_contract.py`.
    watermark_utc = watermark_at.astimezone(UTC)
    return SourceWatermark(
        day=watermark_utc.date(),
        instant=watermark_utc,
        basis=f"{slug}: GREATEST({evidence}) over {row_count} published rows",
    )


def _render_instant(value: datetime | None) -> str:
    """Render one evidence column for the basis string; `null` is a real, reportable answer."""
    return "null" if value is None else value.astimezone(UTC).isoformat()


async def _watersheds_watermark(
    session: AsyncSession,
    store: ObjectStore,  # noqa: ARG001 - uniform resolver shape; this lane's clock is in Postgres
    *,
    today: date,  # noqa: ARG001 - the source's own change time, never this run's date
) -> SourceWatermark:
    """When the published WBD HUC12 boundary set last changed."""
    return await _read_source_watermark(
        session,
        _WATERSHEDS_WATERMARK_SQL,
        slug=WATERSHEDS_STREAM,
        evidence_columns=("feature_updated_at", "feature_created_at"),
    )


async def _evacuation_zones_watermark(
    session: AsyncSession,
    store: ObjectStore,  # noqa: ARG001 - uniform resolver shape; this lane's clock is in Postgres
    *,
    today: date,  # noqa: ARG001 - the source's own change time, never this run's date
) -> SourceWatermark:
    """When the published Oregon OEM evacuation-area set last changed."""
    return await _read_source_watermark(
        session,
        _EVACUATION_ZONES_WATERMARK_SQL,
        slug=EVACUATION_ZONES_STREAM,
        evidence_columns=("feature_updated_at", "feature_created_at", "geometry_version_valid_from"),
    )


async def _soil_survey_watermark(
    session: AsyncSession,
    store: ObjectStore,  # noqa: ARG001 - uniform resolver shape; this lane's clock is in Postgres
    *,
    today: date,  # noqa: ARG001 - the source's own vintage, never this run's date
) -> SourceWatermark:
    """The newer of SSURGO's own saverest vintage and the day this warehouse's published set grew."""
    return await _read_source_watermark(
        session,
        _SOIL_SURVEY_WATERMARK_SQL,
        slug=SOIL_SURVEY_STREAM,
        evidence_columns=("source_vintage_at", "feature_created_at"),
    )


async def _calendar_watermark(
    session: AsyncSession,  # noqa: ARG001 - uniform resolver shape; this lane reads no database
    store: ObjectStore,
    *,
    today: date,
) -> SourceWatermark:
    """The version day the calendar dimension must carry, from the clock and its own object listing.

    The other three watermarks ask Postgres what changed. This one has no source system to ask, so
    it asks the requirement instead: a version stamped `D` covers `D + CALENDAR_VERSION_FORWARD_DAYS`,
    and the dimension is current while that still reaches `today + CALENDAR_REQUIRED_FORWARD_DAYS`.
    Returning the newest held version while coverage suffices is what makes the generic rule -- a
    partition dated at or after the watermark means current -- resolve without a special case.

    It carries NO instant, and cannot: a computed requirement has no source change time to compare
    an export against. That is the honest unknown-instant case, and `resolve_static_lane` settles it
    at day resolution while saying in its detail that the answer is day-resolution.

    The listing names `LANE_BASE_ZOOM_TIER` because that is the rung `pipeline/lanes/calendar.py`
    actually writes. A tier-less question does not exist here: versions held at a derived tier would
    say the dimension is current when the tier the lane writes was never carried forward.
    """
    newest = newest_covered_day(
        layer=CALENDAR_STREAM,
        kind="observed",
        zoom=LANE_BASE_ZOOM_TIER,
        keys=store.list_partition_keys(CALENDAR_STREAM, "observed", LANE_BASE_ZOOM_TIER),
    )
    required = required_calendar_version_day(today=today, newest_version_day=newest)
    return SourceWatermark(
        day=required,
        instant=None,
        basis=(
            f"{CALENDAR_STREAM}: pure computation, no source system. Newest version held "
            f"{'none' if newest is None else newest.isoformat()}; each version covers "
            f"{CALENDAR_VERSION_FORWARD_DAYS} days forward and must reach today plus "
            f"{CALENDAR_REQUIRED_FORWARD_DAYS}"
        ),
    )


def _refuse_empty_day(slug: str, *, day: date, subject: str) -> EmptyPartitionError:
    """Build the zero-row refusal `gap_fill.py` turns into a governed absence for this lane-day."""
    return EmptyPartitionError(
        f"refusing to write a zero-row {slug!r} observed partition for {day}: the warehouse held no {subject} "
        "for this day, and an empty file reads as a present day and hides the gap"
    )


async def _fill_signal(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    run_id: str,  # noqa: ARG001 - uniform adapter shape; this lane records no absence of its own
) -> LaneRunResult:
    """Export one settled day of the governed signal plane across every analysis cell."""
    cell_ids = await _spatial_cell_ids(session)
    if not cell_ids:
        raise LaneRegistryError(
            "agri.spatial_cell is empty, so the signal plane has no analysis grid to export; that is a broken "
            "warehouse, not an empty day, and must not be recorded as a governed absence"
        )
    return normalise_export_outcome(await export_signal_day(session, store, day=day, cell_ids=cell_ids))


async def _fill_vegetation(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    run_id: str,  # noqa: ARG001 - uniform adapter shape; this lane records no absence of its own
) -> LaneRunResult:
    """Export one settled day of the governed NDVI plane across every analysis cell."""
    cell_ids = await _spatial_cell_ids(session)
    if not cell_ids:
        raise LaneRegistryError(
            "agri.spatial_cell is empty, so the vegetation plane has no analysis grid to export; that is a "
            "broken warehouse, not an empty day"
        )
    return normalise_export_outcome(await export_vegetation_day(session, store, day=day, cell_ids=cell_ids))


async def _fill_weather_observations(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    run_id: str,  # noqa: ARG001 - uniform adapter shape; this lane records no absence of its own
) -> LaneRunResult:
    """Export one settled day of the Open-Meteo current-conditions side lane."""
    layer_id = await _layer_id(session, WEATHER_OBSERVATIONS_STREAM)
    return normalise_export_outcome(await export_weather_observations_day(session, store, day=day, layer_id=layer_id))


async def _fill_water_gauges(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    run_id: str,  # noqa: ARG001 - uniform adapter shape; this lane records no absence of its own
) -> LaneRunResult:
    """Export one settled day of the USGS NWIS gauge reading log."""
    return normalise_export_outcome(await export_water_gauges_day(session, store, day=day))


async def _fill_sensors(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    run_id: str,  # noqa: ARG001 - uniform adapter shape; the driver records this lane's absences
) -> LaneRunResult:
    """Export one day of NWS station readings, refusing a day no station published on."""
    station_ids = await _sensor_station_ids(session, day=day)
    if not station_ids:
        # NOT a LaneRegistryError: the station list IS day-scoped, so "no station published" is an
        # empty day rather than a broken lane, and belongs behind a governed absence. Raising the
        # writer's own refusal is what routes it there, instead of `SensorsExportError`, which the
        # driver would (correctly) read as the lane itself being broken.
        raise _refuse_empty_day(SENSORS_STREAM, day=day, subject="qualifying station reading")
    return normalise_export_outcome(await export_sensors_day(session, store, day=day, station_ids=station_ids))


async def _fill_fire_detections(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    run_id: str,
) -> LaneRunResult:
    """Export one settled day of the FIRMS cell-day aggregate, or record the lane's own absence."""
    layer_id = await _layer_id(session, FIRE_DETECTIONS_STREAM)
    return normalise_export_outcome(
        await export_fire_detections_day(session, store, day=day, layer_id=layer_id, run_id=run_id)
    )


async def _fill_fire_perimeters(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    run_id: str,
) -> LaneRunResult:
    """Export one day of WFIGS incident perimeters, or record the lane's own absence."""
    outcome = await export_fire_perimeters_day(session, store, day=day, run_id=run_id)
    if outcome.absence is not None:
        return _from_absence(outcome.absence)
    return _from_parts(outcome.parts)


async def _fill_burn_severity(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    run_id: str,
) -> LaneRunResult:
    """Export one MTBS release day, or record the lane's own absence for a day that is not one."""
    return normalise_export_outcome(
        await export_burn_severity_release_day(session, store, release_day=day, run_id=run_id)
    )


async def _fill_watersheds(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    run_id: str,  # noqa: ARG001 - uniform adapter shape; the driver records this lane's absences
) -> LaneRunResult:
    """Snapshot the current WBD HUC12 boundary set under `day`, its source watermark's version date."""
    return normalise_export_outcome(await export_watersheds_release(session, store, day=day))


async def _fill_drought(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    run_id: str,  # noqa: ARG001 - uniform adapter shape; the driver records this lane's absences
) -> LaneRunResult:
    """Export one weekly USDM release; `day` is both the partition day and the release `valid_date`."""
    return normalise_export_outcome(await export_drought_release(session, store, day=day))


async def _fill_evacuation_zones(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    run_id: str,  # noqa: ARG001 - uniform adapter shape; the driver records this lane's absences
) -> LaneRunResult:
    """Snapshot Oregon OEM's evacuation areas under `day`, its source watermark's version date."""
    return normalise_export_outcome(await export_evacuation_zones_day(session, store, day=day))


async def _fill_soil_survey(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    run_id: str,  # noqa: ARG001 - uniform adapter shape; the driver records this lane's absences
) -> LaneRunResult:
    """Stream every published SSURGO delineation under `day`, its source watermark's vintage date.

    The first key page is pulled HERE rather than inside the export, because an empty first page is
    the one answer the streaming export cannot phrase for itself: it would reach `write_partition`
    with a zero-row table and surface the writer's generic refusal, losing the fact that this lane's
    emptiness is a KNOWN, honest state -- soil-survey is warmed lazily by viewport reads
    (docs/lanes/soil-survey.md section 2). The page is handed straight back to the export, so
    proving the release non-empty costs no second query.
    """
    batches = _soil_survey_polygon_key_batches(session)
    first_batch = await anext(batches, None)
    if first_batch is None:
        raise _refuse_empty_day(SOIL_SURVEY_STREAM, day=day, subject="published SSURGO delineation")
    return normalise_export_outcome(
        await export_soil_survey_release(
            session, store, day=day, mupolygonkey_batches=_batches_after(first_batch, batches)
        )
    )


async def _fill_calendar(
    session: AsyncSession,  # noqa: ARG001 - uniform adapter shape; this lane reads NO database
    store: ObjectStore,
    *,
    day: date,
    run_id: str,  # noqa: ARG001 - uniform adapter shape; this lane records no absence of its own
) -> LaneRunResult:
    """Write one version of the conformed calendar dimension, covering every lane's floor forward.

    The session is accepted and ignored on purpose: `pipeline/lanes/calendar.py` takes no session,
    because a date dimension has no source system. The uniform adapter shape absorbs that
    difference here, in one annotated place, rather than putting a lie in the lane's own signature.
    """
    # `CALENDAR_HISTORY_FLOOR` is defined below the registration table because it is DERIVED from
    # it; a module-level name resolves at call time, so the forward reference is fine.
    return normalise_export_outcome(export_calendar_version(store, day=day, floor=CALENDAR_HISTORY_FLOOR))


# --- The twelve database-backed registrations ---------------------------------------------------
#
# Every floor and lag below is either quoted from that lane's `docs/lanes/<slug>.md` contract or
# marked FALLBACK. A floor that is wrong in the early direction invents thousands of phantom
# gap-days the driver will then spend a cron tick a night failing to fill; a floor that is wrong in
# the late direction silently omits real days. Both are recorded honestly rather than smoothed over.
#
# Every registration also declares its NATURE, which says what its partition day means:
#   daily_series   -- the day IS the observation day (7 lanes)
#   release_series -- the day IS the publication's own valid/issue date (burn-severity, drought)
#   static_lookup  -- the day is a VERSION STAMP and the lane keys to a source watermark
#                     (evacuation-zones, soil-survey, watersheds, and `calendar` below)
#
# `interventions` is deliberately absent: RUNBOOK section 0.26.1 keeps that lane in Postgres.

_DATABASE_BACKED_REGISTRATIONS: Final[tuple[LaneRegistration, ...]] = (
    LaneRegistration(
        slug=BURN_SEVERITY_STREAM,
        adapter=_fill_burn_severity,
        history_floor=date(2020, 11, 24),
        publication_lag_days=7,
        nature="release_series",
        floor_basis=(
            "NATURE release_series: MTBS publishes fire-year cohorts quarterly and each release IS a dated "
            "fact -- five of them, at 2020-11-24 .. 2024-08-22. cadence_days stays 1 DELIBERATELY, unlike "
            "drought's 7: those five dates do not sit on any fixed step from the floor, so a cadence above "
            "one would step straight past real releases. The ~2,000 honest absence markers this costs are the "
            "price of an IRREGULAR release series, and RUNBOOK section 0.27.5 item 5's 'give it a cadence' is "
            "answered here: there is no honest number to give it. "
            "docs/lanes/burn-severity.md section 3: the published rows' own observedAt span is "
            "2020-11-24 to 2024-08-22, the five release dates of the five ingested fire-year cohorts. "
            "Lag 7 from section 2's weekly ingest cron (infra/cron-mtbs, Tuesdays 07:55 UTC). Most days "
            "in this window are correctly a governed absence -- MTBS publishes quarterly, and the lane's "
            "own export already records a non-release day as one."
        ),
    ),
    LaneRegistration(
        slug=DROUGHT_STREAM,
        adapter=_fill_drought,
        history_floor=date(2022, 8, 9),
        publication_lag_days=4,
        nature="release_series",
        cadence_days=7,
        floor_basis=(
            "NATURE release_series: a USDM map is a dated publication, and valid_date IS the release's own "
            "fact rather than a day anyone observed. "
            "MEASURED against production 2026-08-22, not cited from a document: "
            "min(valid_date)=2022-08-09, max=2026-08-18, 209 distinct releases, 1,045 rows. "
            "docs/lanes/drought.md section 7 explicitly refused to declare a floor without this "
            "measurement, and the ingest code's USDM_ARCHIVE_START of 2000-01-04 is an archive "
            "capability, NOT what production holds -- using it would invent ~1,100 phantom weeks. "
            "cadence 7: USDM publishes weekly, valid_date always a Tuesday, and 2022-08-09 is a "
            "Tuesday so the step lands on real release days. Lag 4: released Thursday for the "
            "preceding Tuesday, plus slack."
        ),
    ),
    LaneRegistration(
        slug=EVACUATION_ZONES_STREAM,
        adapter=_fill_evacuation_zones,
        history_floor=date(2025, 4, 14),
        publication_lag_days=0,
        nature="static_lookup",
        watermark=_evacuation_zones_watermark,
        floor_basis=(
            "NATURE static_lookup, WATERMARK-DRIVEN. docs/lanes/evacuation-zones.md section 3: "
            "HistoryCapability(supported=False) -- Oregon OEM publishes current state only and no past "
            "evacuation level is reconstructable. The floor is the sampled observedAt span's start "
            "(2025-04-14) and is INERT: this lane's partition day comes from "
            "sql/pipeline/lane_watermark_evacuation_zones.sql, not from the floor and not from the cron's "
            "run date. Lag 0 because a version stamp is not settled by waiting."
        ),
    ),
    LaneRegistration(
        slug=FIRE_DETECTIONS_STREAM,
        adapter=_fill_fire_detections,
        history_floor=date(2000, 11, 2),
        publication_lag_days=2,
        nature="daily_series",
        forecast_module="fire_detections",
        floor_basis=(
            "NATURE daily_series, forecastable (method/monte_carlo/fire_detections.py, horizon 30d). "
            "docs/lanes/fire-detections.md section 3: production's sampled minimum observedAt is 2000-11-02, "
            "one day after the archive walk's own 2000-11-01 floor. Lag 2 from section 2's FIRMS_DAY_RANGE "
            "rolling NRT lookback (default 2, clamped 1-5). This is the deepest window of any lane -- roughly "
            "9,400 days -- and is exactly what the newest-first ordering exists to keep tolerable."
        ),
    ),
    LaneRegistration(
        slug=FIRE_PERIMETERS_STREAM,
        adapter=_fill_fire_perimeters,
        history_floor=date(2025, 7, 28),
        publication_lag_days=1,
        nature="daily_series",
        floor_basis=(
            "NATURE daily_series, NOT forecastable: docs/lanes/fire-perimeters.md section 7 declares "
            "horizon: none for the polygon geometry, and no method/monte_carlo/fire_perimeters.py exists. "
            "A daily series is ALLOWED to decline a forecast -- the nature is the ceiling, the shipped "
            "forecaster is the claim, and here the claim is deliberately absent. "
            "docs/lanes/fire-perimeters.md section 3: what is actually held is the residue of the hourly "
            "_Current poller, whose oldest isolated row is 2025-07-28. The declared 2020-01-01 floor "
            "(WFIGS_PERIMETER_HISTORY_EARLIEST) is documentation-derived, has NO fetcher wired, and would "
            "invent ~2,000 phantom gap-days, so it is deliberately not used. Lag 1: the poll is hourly."
        ),
    ),
    LaneRegistration(
        slug=SENSORS_STREAM,
        adapter=_fill_sensors,
        history_floor=date(2026, 7, 29),
        publication_lag_days=1,
        nature="daily_series",
        forecast_module="sensors",
        floor_basis=(
            "NATURE daily_series, forecastable (method/monte_carlo/sensors.py, horizon 30d). "
            "docs/lanes/sensors.md section 3: NWS keeps a rolling ~6-day window and no deeper archive exists, "
            "so the whole record is what this producer has accreted since 2026-08-04 plus its first run's "
            "~6-day reach -- derived there as 2026-07-29 to 2026-08-04, and the earlier end is taken. "
            "geo.features is append-only for this lane, so the floor is static even though the SOURCE's is not."
        ),
    ),
    LaneRegistration(
        slug=SIGNAL_PLANE_STREAM,
        adapter=_fill_signal,
        history_floor=date(2022, 4, 30),
        publication_lag_days=9,
        nature="daily_series",
        forecast_module="signal",
        floor_basis=(
            "NATURE daily_series, forecastable (method/monte_carlo/signal.py, horizon 30d). "
            "docs/lanes/weather-observations.md section 3: the whole plane's measured extent is 2022-04-30 to "
            "2026-08-06 across both producers. Lag 9 is ERA5-Land's measured PUBLICATION_LAG_DAYS "
            "(execution/coverage_census.py); NASA POWER's is 5. The LARGER is used deliberately -- at lag 5 "
            "the four newest days would be declared missing while ERA5-Land has genuinely not published them."
        ),
    ),
    LaneRegistration(
        slug=SOIL_SURVEY_STREAM,
        adapter=_fill_soil_survey,
        history_floor=date(2025, 8, 26),
        publication_lag_days=0,
        nature="static_lookup",
        watermark=_soil_survey_watermark,
        floor_basis=(
            "NATURE static_lookup, WATERMARK-DRIVEN. docs/lanes/soil-survey.md section 3: vintage-only, not a "
            "daily series -- one live vintage per delineation, keyed by survey-area publication. The floor is "
            "section 2's measured saverest span start (2025-08-26 to 2026-03-19) and is INERT: the partition "
            "day comes from sql/pipeline/lane_watermark_soil_survey.sql, which takes the newer of SSURGO's own "
            "saverest vintage and the day this warehouse's lazily-warmed published set last grew. Lag 0 "
            "because a vintage is not settled by waiting."
        ),
    ),
    LaneRegistration(
        slug=VEGETATION_PLANE_STREAM,
        adapter=_fill_vegetation,
        history_floor=date(2022, 8, 5),
        publication_lag_days=7,
        nature="daily_series",
        # The one forecaster whose module stem is NOT its slug. It predates `layer-lanes.md` (§3
        # says bring it into conformance rather than writing a second one beside it), so the
        # registration records the real filename instead of the convention it breaks.
        forecast_module="vegetation_ndvi_forecast",
        floor_basis=(
            "NATURE daily_series, forecastable (method/monte_carlo/vegetation_ndvi_forecast.py, horizon 30d). "
            "docs/lanes/vegetation.md section 3: the governed forecastable plane holds 2022-08-05 to "
            "2026-08-04, the deepest record of any lane. Lag 7 from section 2's MEASURED median 7-day gap "
            "between observation days, which is worse than the nominal 5-day Sentinel-2 revisit because cloud "
            "screening removes scenes. Most days in this window are correctly a governed absence."
        ),
    ),
    LaneRegistration(
        slug=WATER_GAUGES_STREAM,
        adapter=_fill_water_gauges,
        history_floor=date(2026, 5, 24),
        publication_lag_days=2,
        nature="daily_series",
        forecast_module="water_gauges",
        floor_basis=(
            "NATURE daily_series, forecastable (method/monte_carlo/water_gauges.py, horizon 30d). "
            "docs/lanes/water-gauges.md section 3: the DENSE record starts 2026-05-24. The code floor "
            "USGS_DAILY_VALUES_EARLIEST = 2022-08-05 is explicitly BORROWED from the vegetation layer, not "
            "source-imposed, and nothing confirms the archive walk has reached it -- using it would invent "
            "~1,400 phantom gap-days. The bare min(observed_day) of 1990-10-01 is documented there as a trap. "
            "Lag 2: USGS daily values are provisional same-day-to-next-day (UNVERIFIED for this bbox)."
        ),
    ),
    LaneRegistration(
        slug=WATERSHEDS_STREAM,
        adapter=_fill_watersheds,
        history_floor=date(2026, 8, 7),
        publication_lag_days=0,
        nature="static_lookup",
        watermark=_watersheds_watermark,
        floor_basis=(
            "NATURE static_lookup, WATERMARK-DRIVEN. docs/lanes/watersheds.md section 2: exactly ONE load day "
            "exists, 2026-08-07, all 9,396 rows, and section 3 states the boundaries are a snapshot rather "
            "than a series. A HUC12 boundary is a reference fact with a VERSION, so the partition day comes "
            "from sql/pipeline/lane_watermark_watersheds.sql -- geo.features' change-gated updated_at/created_at "
            "for this layer -- and the floor is INERT. Lag 0 because a version stamp is not settled by waiting."
        ),
    ),
    LaneRegistration(
        slug=WEATHER_OBSERVATIONS_STREAM,
        adapter=_fill_weather_observations,
        history_floor=date(2026, 8, 1),
        publication_lag_days=2,
        nature="daily_series",
        floor_basis=(
            "NATURE daily_series, NOT forecastable: no method/monte_carlo/weather_observations.py exists and "
            "the lane's contract declares no horizon -- because it declares nothing at all, see below. "
            "FALLBACK -- NOT DECLARED ANYWHERE, AND THE GUESS IS DELIBERATELY SHALLOW. RUNBOOK section 0.26.8: "
            "docs/lanes/weather-observations.md describes the NASA POWER / ERA5-Land archive, which is the "
            "SIGNAL stream, already registered above. The producer THIS lane exports -- ingest/open_meteo.py's "
            "WEATHER_LAYER current-conditions poll into geo.features -- has no contract content at all: no "
            "declared cadence, horizon, historical depth or known-gaps list. 2026-08-01 is a conservative "
            "recent floor chosen so a wrong guess costs a few dozen phantom gap-days instead of thousands, and "
            "lag 2 is borrowed from the hourly ingest-all tick. WRITE THAT HALF OF THE CONTRACT, then measure "
            "min(geo.feature_observation_day) for this layer and replace both numbers."
        ),
    ),
)

# --- The conformed calendar dimension -----------------------------------------------------------
#
# The floor is DERIVED, not declared: the union of every database-backed lane's own floor, so the
# dimension covers every day any lane can key to it. Deriving it is what stops the calendar and the
# deepest lane (`fire-detections`, 2000-11-02) drifting apart when a floor is next corrected.

CALENDAR_HISTORY_FLOOR: Final[date] = min(registration.history_floor for registration in _DATABASE_BACKED_REGISTRATIONS)

CALENDAR_REGISTRATION: Final = LaneRegistration(
    slug=CALENDAR_STREAM,
    adapter=_fill_calendar,
    history_floor=CALENDAR_HISTORY_FLOOR,
    publication_lag_days=0,
    nature="static_lookup",
    watermark=_calendar_watermark,
    floor_basis=(
        "NATURE static_lookup, WATERMARK-DRIVEN, and the ONE lane with no source system. The floor is DERIVED "
        f"as min(history_floor) across the twelve database-backed lanes -- {CALENDAR_HISTORY_FLOOR.isoformat()}, "
        "which is fire-detections' -- so every day any lane can key to the dimension is in it. Each version "
        f"covers its own day plus {CALENDAR_VERSION_FORWARD_DAYS} days, and must reach today plus "
        f"{CALENDAR_REQUIRED_FORWARD_DAYS}, so a 30-day horizon from any as-of date always resolves and the "
        "lane regenerates roughly once a year instead of once a day. Lag 0: pure computation settles instantly."
    ),
)

LANE_REGISTRATIONS: Final[tuple[LaneRegistration, ...]] = tuple(
    sorted((*_DATABASE_BACKED_REGISTRATIONS, CALENDAR_REGISTRATION), key=lambda entry: entry.slug)
)

LANE_REGISTRY: Final[Mapping[str, LaneRegistration]] = MappingProxyType(
    {registration.slug: registration for registration in LANE_REGISTRATIONS}
)


def registered_lane_slugs() -> tuple[str, ...]:
    """Return every registered stream slug, in the order the driver visits them."""
    return tuple(registration.slug for registration in LANE_REGISTRATIONS)


def resolve_lanes(slugs: Iterable[str]) -> tuple[LaneRegistration, ...]:
    """Return the named registrations in registry order, naming every slug that is not one."""
    requested = frozenset(slugs)
    unknown = sorted(requested - set(LANE_REGISTRY))
    if unknown:
        raise LaneRegistryError(
            f"unknown lane(s) {', '.join(unknown)}; registered lanes are {', '.join(registered_lane_slugs())}"
        )
    return tuple(entry for entry in LANE_REGISTRATIONS if entry.slug in requested)
