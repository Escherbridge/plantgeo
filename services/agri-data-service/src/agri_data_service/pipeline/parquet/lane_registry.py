"""One entry per Parquet object stream: an async adapter, a history floor, and a publication lag.

Layer L2: may import `foundation`, `warehouse` and `db`; may NOT import method, planes, or
interface. It lives in `pipeline/parquet/` and deliberately NOT in `pipeline/lanes/` -- a module
inside that directory importing its siblings would (correctly) fail
`tests/test_layer_import_contract.py::test_lanes_do_not_import_each_other`. The registry is not a
lane; it is the one module allowed to know all eleven of them.

See `AGENTS.md` in this directory for the floor/lag evidence table, which floors are declared and
which are fallbacks, and why three lanes refuse historical backfill outright.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal, Protocol
from uuid import UUID

from sqlalchemy import text

from agri_data_service.db.sql_queries import load_query_sql
from agri_data_service.foundation.parquet.paths import validate_layer_slug
from agri_data_service.pipeline.lanes.burn_severity import export_burn_severity_release_day
from agri_data_service.pipeline.lanes.drought import export_drought_release
from agri_data_service.pipeline.lanes.evacuation_zones import export_evacuation_zones_day
from agri_data_service.pipeline.lanes.fire_detections import export_fire_detections_day
from agri_data_service.pipeline.lanes.fire_perimeters import export_fire_perimeters_day
from agri_data_service.pipeline.lanes.sensors import export_sensors_day
from agri_data_service.pipeline.lanes.signal import export_signal_day
from agri_data_service.pipeline.lanes.soil_survey import export_soil_survey_release
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
    from collections.abc import Iterable, Mapping, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession

    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

_SPATIAL_CELL_IDS_SQL: Final = text(load_query_sql("pipeline/lane_registry_spatial_cell_ids.sql"))
_LAYER_ID_SQL: Final = text(load_query_sql("pipeline/lane_registry_layer_id.sql"))
_SENSOR_STATION_IDS_SQL: Final = text(load_query_sql("pipeline/lane_registry_sensor_station_ids.sql"))
_SOIL_SURVEY_POLYGON_KEYS_SQL: Final = text(load_query_sql("pipeline/lane_registry_soil_survey_polygon_keys.sql"))

# 200,000 keys is 400 parts at soil_survey.ROWS_PER_PART, and at the 17.3 KB/row that RUNBOOK
# section 0.26.6 measured for the comparable watersheds geometry lane that is roughly 3.5 GB for one
# release day -- already far past what one cron tick can write. The ceiling is therefore a refusal
# to attempt something absurd, not a claim about how many delineations SSURGO holds.
MAX_SOIL_SURVEY_POLYGON_KEYS: Final = 200_000

# A "day" for a lane whose upstream publishes CURRENT STATE only cannot be reconstructed for a past
# date: evacuation_zones_day_export.sql, watersheds_day_export.sql and soil_survey_day_export.sql all
# broadcast the caller's day onto every row and apply NO date predicate, because Postgres holds no
# record of what those feeds published on any day but today. Filling a historical gap for one of them
# would stamp today's state onto a past date -- a fabrication, not a backfill. `gap_fill.py` collapses
# their window to the newest settled day for exactly this reason.
LaneWindowKind = Literal["daily_series", "current_snapshot"]


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


@dataclass(frozen=True, slots=True)
class LaneRegistration:
    """One stream's gap-fill contract: how to export a day, how far back to look, and how far behind to stop."""

    slug: str
    adapter: LaneAdapter
    history_floor: date
    publication_lag_days: int
    window_kind: LaneWindowKind
    floor_basis: str
    # Days between publications, counted from `history_floor`. 1 means "every day is a candidate".
    # A weekly source registered as daily is not wrong, but it spends the whole backlog writing
    # honest-yet-pointless absence markers for the six days a week it was never going to publish --
    # measured at ~2,000 for `burn-severity` before its five real releases are reached.
    cadence_days: int = 1

    def __post_init__(self) -> None:
        validate_layer_slug(self.slug)
        if self.cadence_days < 1:
            raise LaneRegistryError(f"lane {self.slug!r} declares a cadence of under one day")
        if self.publication_lag_days < 0:
            raise LaneRegistryError(f"lane {self.slug!r} declares a negative publication lag")
        if not self.floor_basis.strip():
            raise LaneRegistryError(
                f"lane {self.slug!r} must cite where its history floor came from; an uncited floor is a guess "
                "that reads as a measurement"
            )


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


async def _soil_survey_polygon_keys(session: AsyncSession) -> tuple[str, ...]:
    """Read the published SSURGO delineation keys, refusing a result that hit the query's ceiling."""
    result = await session.execute(
        _SOIL_SURVEY_POLYGON_KEYS_SQL, {"key_ceiling": MAX_SOIL_SURVEY_POLYGON_KEYS + 1}
    )
    keys = tuple(_coerce_text(row["mupolygonkey"], column="soil-survey.mupolygonkey") for row in result.mappings())
    if len(keys) > MAX_SOIL_SURVEY_POLYGON_KEYS:
        raise LaneRegistryError(
            f"soil-survey holds more than {MAX_SOIL_SURVEY_POLYGON_KEYS} published delineations; exporting a "
            "truncated key list would write a partial release that reads back as a complete one. Raise "
            "MAX_SOIL_SURVEY_POLYGON_KEYS deliberately, or shard the release, before running this lane."
        )
    return keys


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
    return normalise_export_outcome(
        await export_weather_observations_day(session, store, day=day, layer_id=layer_id)
    )


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
    """Re-snapshot the current WBD HUC12 boundary set under today's release day."""
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
    """Re-snapshot Oregon OEM's current evacuation areas under today's snapshot day."""
    return normalise_export_outcome(await export_evacuation_zones_day(session, store, day=day))


async def _fill_soil_survey(
    session: AsyncSession,
    store: ObjectStore,
    *,
    day: date,
    run_id: str,  # noqa: ARG001 - uniform adapter shape; the driver records this lane's absences
) -> LaneRunResult:
    """Re-snapshot every published SSURGO delineation under today's release day."""
    mupolygonkeys = await _soil_survey_polygon_keys(session)
    if not mupolygonkeys:
        # Soil-survey is warmed lazily by viewport reads (docs/lanes/soil-survey.md section 2), so
        # "nothing published yet" is a real, honest empty state rather than a broken lane.
        raise _refuse_empty_day(SOIL_SURVEY_STREAM, day=day, subject="published SSURGO delineation")
    return normalise_export_outcome(
        await export_soil_survey_release(session, store, day=day, mupolygonkeys=mupolygonkeys)
    )


# --- The eleven registrations ------------------------------------------------------------------
#
# Every floor and lag below is either quoted from that lane's `docs/lanes/<slug>.md` contract or
# marked FALLBACK. A floor that is wrong in the early direction invents thousands of phantom
# gap-days the driver will then spend a cron tick a night failing to fill; a floor that is wrong in
# the late direction silently omits real days. Both are recorded honestly rather than smoothed over.
#
# `interventions` is deliberately absent: RUNBOOK section 0.26.1 keeps that lane in Postgres.

LANE_REGISTRATIONS: Final[tuple[LaneRegistration, ...]] = (
    LaneRegistration(
        slug=BURN_SEVERITY_STREAM,
        adapter=_fill_burn_severity,
        history_floor=date(2020, 11, 24),
        publication_lag_days=7,
        window_kind="daily_series",
        floor_basis=(
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
        window_kind="daily_series",
        cadence_days=7,
        floor_basis=(
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
        publication_lag_days=1,
        window_kind="current_snapshot",
        floor_basis=(
            "docs/lanes/evacuation-zones.md section 3: HistoryCapability(supported=False) -- Oregon OEM "
            "publishes current state only and no past evacuation level is reconstructable. The floor is "
            "the sampled observedAt span's start (2025-04-14), and it is nearly inert because "
            "window_kind='current_snapshot' collapses this lane to the newest settled day."
        ),
    ),
    LaneRegistration(
        slug=FIRE_DETECTIONS_STREAM,
        adapter=_fill_fire_detections,
        history_floor=date(2000, 11, 2),
        publication_lag_days=2,
        window_kind="daily_series",
        floor_basis=(
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
        window_kind="daily_series",
        floor_basis=(
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
        window_kind="daily_series",
        floor_basis=(
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
        window_kind="daily_series",
        floor_basis=(
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
        publication_lag_days=1,
        window_kind="current_snapshot",
        floor_basis=(
            "docs/lanes/soil-survey.md section 3: vintage-only, not a daily series -- one live vintage per "
            "delineation, keyed by survey-area publication. The floor is section 2's measured saverest span "
            "start (2025-08-26 to 2026-03-19), and window_kind='current_snapshot' collapses the lane to the "
            "newest settled day because its export broadcasts the release day rather than filtering on it."
        ),
    ),
    LaneRegistration(
        slug=VEGETATION_PLANE_STREAM,
        adapter=_fill_vegetation,
        history_floor=date(2022, 8, 5),
        publication_lag_days=7,
        window_kind="daily_series",
        floor_basis=(
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
        window_kind="daily_series",
        floor_basis=(
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
        publication_lag_days=1,
        window_kind="current_snapshot",
        floor_basis=(
            "docs/lanes/watersheds.md section 2: exactly ONE load day exists, 2026-08-07, all 9,396 rows, and "
            "section 3 states the boundaries are a snapshot rather than a series. Nothing invokes the ingest "
            "automatically and revalidation is deliberately off, so window_kind='current_snapshot' keeps this "
            "lane to one re-snapshot of the newest settled day."
        ),
    ),
    LaneRegistration(
        slug=WEATHER_OBSERVATIONS_STREAM,
        adapter=_fill_weather_observations,
        history_floor=date(2026, 8, 1),
        publication_lag_days=2,
        window_kind="daily_series",
        floor_basis=(
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
