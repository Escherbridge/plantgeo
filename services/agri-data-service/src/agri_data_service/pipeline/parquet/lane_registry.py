"""One entry per Parquet object stream: an adapter, a lane NATURE, and the clock that nature keys to.

Layer L2: may import `foundation`, `warehouse` and `db`; may NOT import method, planes, or
interface. It lives in `pipeline/parquet/` and deliberately NOT in `pipeline/lanes/` -- a module
inside that directory importing its siblings would (correctly) fail
`tests/test_layer_import_contract.py::test_lanes_do_not_import_each_other`. The registry is not a
lane; it is the one module allowed to know all thirty-two of them -- thirty-one source-direct
environmental lanes and the calendar dimension. No environmental registration has a PostgreSQL
adapter or fallback; the direct source packages own writes and this registry refuses generic exports.

IT IMPORTS EXACTLY FIVE MODULES FROM `pipeline/direct/`, AND NOTHING ELSE FROM ANY OF THOSE PACKAGES:
`climate/products.py`, `soil/products.py` and `vegetation/products.py` for floors and lags, and
`watersheds/watermark.py` + `evacuation_zones/watermark.py` for the two static lanes whose version
clock is now the source rather than `geo.features`. Every one of those reaches only `foundation`,
`warehouse`, `ingest` or its own package's leaves. NEVER a `forward.py`, an `adapter.py` or a
`products.py` that reads `LANE_REGISTRY`: those close a cycle back through this half-initialised
module, which is also why each package `__init__` is deliberately empty of re-exports, and why
`evacuation_zones/registration.py` exists to hold that package's one edge back here.

EVERY LANE DECLARES WHAT ITS PARTITION DAY MEANS. `daily_series` and `release_series` key to a
publication lag off the calendar; `static_lookup` keys to a SOURCE WATERMARK -- the source's own
"when did this last change" -- and is otherwise idle. See `foundation/parquet/lane_contract.py`
for the vocabulary and `AGENTS.md` in this directory for the floor/lag evidence table, which
floors are declared and which are measured or provisional.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol

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
from agri_data_service.pipeline.constants import (
    FIRE_DETECTIONS_DIRECT_WRITER_START_DAY,
    LANE_BASE_ZOOM_TIER,
    WATER_GAUGES_DIRECT_WRITER_START_DAY,
)
from agri_data_service.pipeline.direct.climate.products import (
    CLIMATE_FIELD_PRODUCTS,
    CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS,
    CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS,
)
from agri_data_service.pipeline.direct.evacuation_zones.watermark import read_evacuation_zones_source_watermark
from agri_data_service.pipeline.direct.soil.products import (
    ERA5_LAND_ARCHIVE_PUBLICATION_LAG_DAYS,
    SOIL_FIELD_PRODUCTS,
)
from agri_data_service.pipeline.direct.vegetation.products import VEGETATION_DIRECT_WRITER_START_DAY
from agri_data_service.pipeline.direct.watersheds.watermark import read_watersheds_source_watermark
from agri_data_service.pipeline.lanes.calendar import export_calendar_version
from agri_data_service.pipeline.parquet.objectstore import (
    AbsenceWriteReceipt,
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

    from agri_data_service.pipeline.direct.climate.products import ClimateFieldProduct
    from agri_data_service.pipeline.direct.soil.products import SoilFieldProduct
    from agri_data_service.pipeline.parquet.objectstore import ObjectStore

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
    # Historical ownership boundary retained for audit; it does not authorize a database writer.
    writer_ceiling: date | None = None

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
        if self.writer_ceiling is not None and self.nature == "static_lookup":
            raise LaneRegistryError(
                f"lane {self.slug!r} is a static_lookup and declares writer ceiling {self.writer_ceiling}; "
                "a version-stamped lane has no calendar window to divide between writers"
            )
        if self.writer_ceiling is not None and self.writer_ceiling < self.history_floor:
            raise LaneRegistryError(
                f"lane {self.slug!r} declares writer ceiling {self.writer_ceiling} before its history floor "
                f"{self.history_floor}"
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


# --- Source watermarks: the clock a static lane keys to ----------------------------------------
#
# A static lane's partition day is a VERSION STAMP, not an observation time, so nothing about a
# calendar day obliges it to write. It writes when its SOURCE changed, at the day the source
# changed, and is otherwise idle. Every query below returns `watermark_at` plus the columns that
# produced it, and every one of those columns is a CHANGE event -- never a poll clock. The rejected
# candidate is `geo.geometry.last_confirmed_at`, which advances on each re-fetch of unchanged
# ground (src/lib/server/services/usda-soil.ts:769,833); putting it in a version stamp would
# reinstate the daily churn this model removes.


async def _watersheds_watermark(
    session: AsyncSession,  # noqa: ARG001 - uniform resolver shape; this lane reads NO database
    store: ObjectStore,  # noqa: ARG001 - uniform resolver shape; the clock is upstream, not in the store
    *,
    today: date,  # noqa: ARG001 - the source's own load date, never this run's date
) -> SourceWatermark:
    """When USGS last loaded a HUC12 boundary in the configured extent, asked of NHDPlus_HR itself.

    SOURCE-DIRECT SINCE 2026-09-06, in the same edit that made this lane's adapter refuse. It used to
    read `sql/pipeline/lane_watermark_watersheds.sql` -- `geo.features`' change-gated
    `updated_at`/`created_at` for this layer -- which was an INGESTION-TIME PROXY for the source's
    own vintage and stopped advancing the moment `postgres-watersheds` stopped writing that table.
    `pipeline/direct/watersheds/watermark.py` reads the vintage itself (`max(loaddate)`), so the
    answer survives the table's deletion. The whole body lives there because this file may not import
    a module that imports it back; see the module docstring.
    """
    return await read_watersheds_source_watermark()


async def _evacuation_zones_watermark(
    session: AsyncSession,  # noqa: ARG001 - uniform resolver shape; this lane reads NO database
    store: ObjectStore,
    *,
    today: date,  # noqa: ARG001 - the capture's own instant, never this run's date
) -> SourceWatermark:
    """Whether Oregon OEM's current statewide set differs from the newest version published here.

    SOURCE-DIRECT SINCE 2026-09-06, in the same edit that made this lane's adapter refuse. `store` is
    genuinely used -- it holds the published version this capture is compared against -- while
    `session` is not, because the three columns this resolver used to read
    (`sql/pipeline/lane_watermark_evacuation_zones.sql`: `features.updated_at`, `features.created_at`,
    `geometry.version_valid_from`) are all in tables this track drops, and Oregon publishes no
    replacement for any of them. The replacement is a CONTENT comparison rather than a clock read,
    and `pipeline/direct/evacuation_zones/watermark.py` is where that decision is argued in full.
    """
    return await read_evacuation_zones_source_watermark(store)


async def _fire_perimeters_watermark(
    session: AsyncSession,  # noqa: ARG001 - uniform resolver shape; no database fallback remains
    store: ObjectStore,  # noqa: ARG001 - direct forward substitutes its source watermark
    *,
    today: date,  # noqa: ARG001 - uniform resolver shape
) -> SourceWatermark:
    """Refuse the retired database watermark; direct forward owns the source clock."""
    raise LaneRegistryError(
        "fire-perimeters has no PostgreSQL watermark; run "
        "`python -m agri_data_service.pipeline.direct.fire_perimeters` for source-direct publication"
    )


async def _soil_survey_watermark(
    session: AsyncSession,  # noqa: ARG001 - uniform resolver shape; no database fallback remains
    store: ObjectStore,  # noqa: ARG001 - no source-direct soil-survey publisher is admitted yet
    *,
    today: date,  # noqa: ARG001 - uniform resolver shape
) -> SourceWatermark:
    """Refuse the retired database watermark until a source-direct SSURGO lane is admitted."""
    raise LaneRegistryError(
        "soil-survey has no PostgreSQL watermark; publish SSURGO through its source-direct Parquet lane"
    )


async def _calendar_watermark(
    session: AsyncSession,  # noqa: ARG001 - uniform resolver shape; this lane reads no database
    store: ObjectStore,
    *,
    today: date,
) -> SourceWatermark:
    """The version day the calendar dimension must carry, from the clock and its own object listing.

    The other source watermarks ask their upstream source what changed. This one has no source system to ask, so
    it asks the requirement instead: a version stamped `D` covers `D + CALENDAR_VERSION_FORWARD_DAYS`,
    and the dimension is current while that still reaches `today + CALENDAR_REQUIRED_FORWARD_DAYS`.
    Returning the newest held version while coverage suffices is what makes the registry rule -- a
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


def _source_direct_refusal(writer_module: str) -> LaneAdapter:
    """Build the refusing adapter for one direct writer's lanes, naming THAT writer in the message.

    A factory rather than eight copies, and a factory rather than one shared message: there are
    eight direct writers registered here now, and an operator told to run the climate module against
    an ERA5-Land lane would get a report that publishes nothing and explains nothing.

    DEFINED ABOVE THE REGISTRATION TABLE, not beside the climate/soil aliases it used to sit with,
    because six hand-written registrations below it now need a refusal too. A `Final` alias is only
    a name for the closure; the closure itself is what
    `tests/direct/test_direct_package_registration.py` probes with a null session and store, so a
    package is "registered" exactly when some adapter here refuses in its name.
    """

    async def refuse(
        session: AsyncSession,  # noqa: ARG001 - uniform adapter shape; this lane has no query to run
        store: ObjectStore,  # noqa: ARG001 - uniform adapter shape; the direct writer owns the write
        *,
        day: date,
        run_id: str,  # noqa: ARG001 - uniform adapter shape; the refusal is not a run outcome
    ) -> LaneRunResult:
        """Refuse a generic export of a source-direct lane, naming the writer that actually owns it."""
        raise LaneRegistryError(
            f"this lane has no PostgreSQL producer, so no generic export may run for "
            f"{day.isoformat()}. Its days are written by `python -m {writer_module}`, which "
            "substitutes its own adapter."
        )

    return refuse


_refuse_climate_direct_export: Final[LaneAdapter] = _source_direct_refusal("agri_data_service.pipeline.direct.climate")
_refuse_soil_direct_export: Final[LaneAdapter] = _source_direct_refusal("agri_data_service.pipeline.direct.soil")
#: Static source-direct lanes use a source-owned watermark and refuse generic exports.
_refuse_watersheds_direct_export: Final[LaneAdapter] = _source_direct_refusal(
    "agri_data_service.pipeline.direct.watersheds"
)
_refuse_evacuation_zones_direct_export: Final[LaneAdapter] = _source_direct_refusal(
    "agri_data_service.pipeline.direct.evacuation_zones"
)
#: Series source-direct lanes likewise refuse generic exports; their direct forward/backfill modules
#: own the full publication window.
_refuse_burn_severity_direct_export: Final[LaneAdapter] = _source_direct_refusal(
    "agri_data_service.pipeline.direct.burn_severity"
)
_refuse_drought_direct_export: Final[LaneAdapter] = _source_direct_refusal("agri_data_service.pipeline.direct.drought")
_refuse_sensors_direct_export: Final[LaneAdapter] = _source_direct_refusal("agri_data_service.pipeline.direct.sensors")
_refuse_weather_observations_direct_export: Final[LaneAdapter] = _source_direct_refusal(
    "agri_data_service.pipeline.direct.weather_observations"
)
_refuse_fire_detections_direct_export: Final[LaneAdapter] = _source_direct_refusal(
    "agri_data_service.pipeline.direct.fire_detections"
)
_refuse_fire_perimeters_direct_export: Final[LaneAdapter] = _source_direct_refusal(
    "agri_data_service.pipeline.direct.fire_perimeters"
)
_refuse_signal_direct_export: Final[LaneAdapter] = _source_direct_refusal(
    "agri_data_service.pipeline.direct.signal"
)
_refuse_soil_survey_direct_export: Final[LaneAdapter] = _source_direct_refusal(
    "agri_data_service.pipeline.direct.soil_survey"
)
_refuse_vegetation_direct_export: Final[LaneAdapter] = _source_direct_refusal(
    "agri_data_service.pipeline.direct.vegetation"
)
_refuse_water_gauges_direct_export: Final[LaneAdapter] = _source_direct_refusal(
    "agri_data_service.pipeline.direct.water_gauges"
)


# --- The twelve hand-written registrations, all source-direct except the calendar dimension ----
#
# Every floor and lag below is either quoted from that lane's `docs/lanes/<slug>.md` contract or
# marked provisional. A floor that is wrong in the early direction invents thousands of phantom
# gap-days the driver will then spend a cron tick a night failing to fill; a floor that is wrong in
# the late direction silently omits real days. Both are recorded honestly rather than smoothed over.
#
# Every registration also declares its NATURE, which says what its partition day means:
#   daily_series   -- the day IS the observation day (7 lanes)
#   release_series -- the day IS the publication's own valid/issue date (burn-severity, drought)
#   static_lookup  -- the day is a VERSION STAMP and the lane keys to a source watermark
#                     (evacuation-zones, soil-survey, watersheds, and `calendar` below)
#
# `interventions` is deliberately absent: it is a control-plane lookup retained separately from the
# environmental Parquet lanes.
#
# The historical tuple name is retained for migration compatibility with imports, but its adapters
# are now all refusal adapters. The source-direct packages own every environmental write; no generic
# registration reads PostgreSQL.

_HAND_WRITTEN_REGISTRATIONS: Final[tuple[LaneRegistration, ...]] = (
    LaneRegistration(
        # SOURCE-DIRECT SINCE 2026-09-07. `burn-severity-direct-forward`
        # (execution/job_executor_service.py) is ACTIVE and `parquet-burn-severity` is retired, so the
        # Parquet stream this registration describes is written by
        # `python -m agri_data_service.pipeline.direct.burn_severity` and by nothing else. The adapter
        # refuses a generic export and names that package.
        #
        # `mtbs-forward`/`ingest-mtbs` IS STILL ACTIVE, AND THAT IS NOT A SECOND WRITER OF THIS STREAM.
        # It writes `geo.features` (`ingest/mtbs.py:947`); the direct lane writes Parquet -- different
        # stores, and the audit that checked
        # (`conductor/tracks/environmental_postgres_retirement_20260904/evidence/`
        # `layer-uniformity-audit-20260907.md`) refuted the double-write alarm outright. What the swap
        # DOES do is finish emptying `mtbs-forward`'s consumer set: `parquet-burn-severity` read those
        # rows through this adapter, and after this edit no lane does.
        #
        # NO `writer_ceiling`, and none is possible to cite: `forward.py` (newest-first) and
        # `backfill.py` (oldest-first) walk ONE candidate set, `products.governed_release_days()`, and
        # that set is the whole window this registration covers -- there is no boundary day between a
        # generic and direct writers to divide. The executor no longer registers a generic lane, and
        # this refusal remains as a defensive guard for direct callers of the registry.
        slug=BURN_SEVERITY_STREAM,
        adapter=_refuse_burn_severity_direct_export,
        history_floor=date(2020, 11, 24),
        publication_lag_days=7,
        nature="release_series",
        floor_basis=(
            "NATURE release_series, SOURCE-DIRECT since 2026-09-07: MTBS publishes fire-year cohorts "
            "quarterly and each release IS a dated fact -- five of them, at 2020-11-24 .. 2024-08-22. The "
            "floor and lag are unchanged by the swap; they describe MTBS's publication, not which writer "
            "reads it. cadence_days stays 1 DELIBERATELY, unlike "
            "drought's 7: those five dates do not sit on any fixed step from the floor, so a cadence above "
            "one would step straight past real releases. The ~2,000 honest absence markers this costs are the "
            "price of an IRREGULAR release series, and RUNBOOK section 0.27.5 item 5's 'give it a cadence' is "
            "answered here: there is no honest number to give it. "
            "docs/lanes/burn-severity.md section 3: the published rows' own observedAt span is "
            "2020-11-24 to 2024-08-22, the five release dates of the five ingested fire-year cohorts. "
            "Lag 7 from section 2's weekly executor lane (Tuesdays 07:55 UTC). Most days "
            "in this window are correctly a governed absence -- MTBS publishes quarterly, and the lane's "
            "own export already records a non-release day as one."
        ),
    ),
    LaneRegistration(
        slug=DROUGHT_STREAM,
        # SOURCE-DIRECT SINCE 2026-09-07, in the push this registration's own text asked for. The
        # condition it carried was "swap it in the same owner-confirmed push that activates
        # `drought-direct-forward`, never before it"; that lane is now ACTIVE and `parquet-drought` is
        # retired, so the condition is discharged and the adapter refuses, naming
        # `pipeline/direct/drought`. Leaving the gate written after it had been met would have been the
        # worse of the two errors: a reader cannot tell a live gate from a discharged one.
        #
        # STILL NO `writer_ceiling`, and the reason is unchanged by the swap -- unlike
        # vegetation/fire-detections/water-gauges, which have a cited ownership-boundary day where the
        # generic and direct writers abut, drought has none and there is no honest one to derive.
        # `pipeline/direct/drought/forward.py:125` starts its own scan at
        # `max(lane.history_floor, settled_through - DROUGHT_BACKLOG_SCAN_WEEKS)` and `backfill.py:72`
        # walks `release_weeks(lane.history_floor, settled_through)` -- both floors ARE this
        # registration's `history_floor`, because the direct writer claims the FULL floor-to-settled
        # window (forward.py module docstring: "this module owns the FULL floor-to-settled window"). A
        # ceiling separating the two would have to sit at or below `history_floor`: below it
        # `__post_init__` rejects outright ("before its history floor"), and AT it would be an invented
        # boundary handing a database writer exactly one day, cited to nothing. The direct package
        # owns the full floor-to-settled window, and this refusal protects direct callers from a
        # stale database export.
        adapter=_refuse_drought_direct_export,
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
            "preceding Tuesday, plus slack. "
            "SOURCE-DIRECT since 2026-09-07. The 2026-09-04 join registered a direct writer BESIDE this "
            "lane; the 2026-09-07 swap put it IN PLACE OF it. pipeline/direct/drought/forward.py "
            "(newest-first) and backfill.py (oldest-first) claim the same full floor-to-settled window "
            "this registration describes, so the two are total substitutes with no boundary day between "
            "them and this lane carries NO writer_ceiling. The floor and lag above are unchanged by the "
            "swap -- they are properties of USDM's publication, not of which writer reads it -- and they "
            "stay MEASURED against production even though the measurement was taken through Postgres."
        ),
    ),
    LaneRegistration(
        # SOURCE-DIRECT SINCE 2026-09-06: both fields point away from Postgres, swapped in one edit.
        # The adapter refuses and names `pipeline/direct/evacuation_zones`; the watermark is that
        # package's `watermark.py`. Nothing in this registration reads `geo.features` or
        # `geo.geometry`, so it survives their deletion -- which is the whole reason the swap ran
        # BEFORE the drop rather than with it: a Postgres watermark over a dropped table fails the
        # census at `watermark_unread`, while a source-direct one works whether or not the table is
        # still there.
        #
        # A `static_lookup` CANNOT carry a `writer_ceiling` (`__post_init__` refuses one: a
        # version-stamped lane has no calendar window to divide between two writers). The generic
        # executor lane is removed; direct callers receive a loud refusal naming the owning writer.
        #
        # THE WATERMARK WAS THE HARDER HALF, and it was not a like-for-like SQL edit.
        # `sql/pipeline/lane_watermark_evacuation_zones.sql` (deleted in this edit) read `geo.features`
        # AND `geo.geometry`, and Oregon OEM publishes no replacement column: `created_date` never moves
        # when a level is raised, and `last_edited_date` is re-stamped on unchanged areas every few
        # minutes, so a clock built on it reports a change on every poll. The replacement therefore
        # answers the question a different way -- it digests the captured population's source-determined
        # content and calls the set changed when THAT differs from the newest published version, the
        # same test `pipeline/direct/evacuation_zones/forward.py` publishes on, so census and writer
        # cannot disagree.
        slug=EVACUATION_ZONES_STREAM,
        adapter=_refuse_evacuation_zones_direct_export,
        history_floor=date(2025, 4, 14),
        publication_lag_days=0,
        nature="static_lookup",
        watermark=_evacuation_zones_watermark,
        floor_basis=(
            "NATURE static_lookup, WATERMARK-DRIVEN, SOURCE-DIRECT since 2026-09-06. "
            "docs/lanes/evacuation-zones.md section 3: "
            "HistoryCapability(supported=False) -- Oregon OEM publishes current state only and no past "
            "evacuation level is reconstructable. The floor is the sampled observedAt span's start "
            "(2025-04-14) and is INERT: this lane's partition day is the UTC date of the capture whose "
            "content first differs from the published version (pipeline/direct/evacuation_zones/"
            "watermark.py), not the floor and not the cron's run date. Lag 0 because a version stamp is "
            "not settled by waiting."
        ),
    ),
    LaneRegistration(
        slug=FIRE_DETECTIONS_STREAM,
        adapter=_refuse_fire_detections_direct_export,
        history_floor=date(2000, 11, 1),
        publication_lag_days=2,
        nature="daily_series",
        forecast_module="fire_detections",
        writer_ceiling=FIRE_DETECTIONS_DIRECT_WRITER_START_DAY - timedelta(days=1),
        floor_basis=(
            "NATURE daily_series, forecastable (method/monte_carlo/fire_detections.py, horizon 30d). "
            "The MODIS_SP floor is 2000-11-01 and production now holds four eligible detections on that "
            "day. Lag 2 from docs/lanes/fire-detections.md section 2's FIRMS_DAY_RANGE "
            "rolling NRT lookback (default 2, clamped 1-5). This is the deepest window of any lane -- roughly "
            "9,400 days -- and is exactly what the newest-first ordering exists to keep tolerable."
        ),
    ),
    LaneRegistration(
        # Source-direct publication owns this current-state snapshot. The generic registry refuses
        # export and the source watermark is intentionally retired with the PostgreSQL tables.
        slug=FIRE_PERIMETERS_STREAM,
        adapter=_refuse_fire_perimeters_direct_export,
        history_floor=date(2025, 7, 28),
        publication_lag_days=0,
        nature="static_lookup",
        watermark=_fire_perimeters_watermark,
        floor_basis=(
            "NATURE static_lookup, WATERMARK-DRIVEN. RE-REGISTERED 2026-09-04 from daily_series, and the "
            "measurement is the whole argument. docs/lanes/fire-perimeters.md section 4: geo.features holds "
            "ONE ROW PER WFIGS INCIDENT refreshed in place -- 'NOT one row per (incident, day)' -- because "
            "build_fire_perimeter_identity keys on the bare uniqueFireIdentifier with no date component. "
            "Registered as a daily series on geo.feature_observation_day, that put 177 published perimeters "
            "across 45 partition days with 287 governed-absence days beside them "
            "(conductor/layer-sessions/fire-perimeters.md, measured 2026-08-25), so ONE day read returned "
            "only the incidents redrawn that day and reproducing what geo.fire_risk_tiles draws needed the "
            "union of a 404-day window. Section 6 refuses release_series too: WFIGS _Current is 'a live "
            "mutable snapshot, not a versioned release' and 'does not retain what it reported yesterday', "
            "unlike MTBS's quarterly or USDM's weekly publications. What is left is what it always was -- a "
            "current-state reference set with a version -- which is the identical shape evacuation-zones "
            "already publishes off the identical table. "
            "The floor stays section 3's oldest isolated row (2025-07-28, the residue of the hourly _Current "
            "poller) and is now INERT: the partition day comes from "
            "sql/pipeline/lane_watermark_fire_perimeters.sql, not from the floor and not from the cron's run "
            "date. NOTHING IS DISCARDED BY THE CHANGE -- there was no per-day history to lose. geo.features "
            "keeps no past state, and geo.geometry's Type-2 chain is not a substitute: only 6 of thousands "
            "of dimension entries across every producer ever reached a second WFIGS version, and its "
            "forward path has a known silent-freeze failure mode (section 4), which is why this lane reads "
            "features.geom and never the dimension. Lag 0 because a version stamp is not settled by "
            "waiting. NOT forecastable is now structural rather than declared: section 7's horizon: none "
            "matched a nature that merely permitted a forecaster, and a static_lookup may not name one at "
            "all."
        ),
    ),
    LaneRegistration(
        # SOURCE-DIRECT SINCE 2026-09-07. `sensors-direct-forward` (execution/job_executor_service.py)
        # is ACTIVE and `parquet-sensors` is retired, so this adapter refuses and names
        # `pipeline/direct/sensors`.
        #
        # WHAT THE OLD GATE SAID, AND WHY IT NO LONGER HOLDS. This registration used to keep a
        # Historical notes: NWS retains only a rolling ~6 days
        # (`pipeline/direct/sensors/forward.py`, SENSORS_MAX_DAYS = NWS_OBSERVATION_RETENTION.days + 1),
        # so the append-only `geo.features` record was the ONLY path to any day older than that window.
        # That is still true of the SOURCE and no longer decides anything here: `postgres-sensors`, the
        # forward producer that appended those rows, was DELETED on 2026-09-07, so the table is frozen.
        # A frozen table is not a deeper archive -- it is a fixed set of past days that a generic export
        # would re-publish forever under a lane the direct writer owns. Whatever of those days is worth
        # keeping has to be RETRACTED AND RE-EXPORTED as a one-off before `geo.features` is dropped
        # (`evidence/sensors-stranded-days-20260906.md` counts 25 such days stranded at z13); it is a
        # migration, not a lane, and keeping a scheduled adapter pointed at it was never how it would
        # have been done.
        #
        # STILL NO `writer_ceiling`: this package ships no `*_DIRECT_WRITER_START_DAY`-equivalent
        # constant and no `backfill.py`, so there is no cited ownership-boundary day to declare, and an
        # invented one would divide the window on nothing. `conflicts_with` on the two executor specs is
        # direct callers receive a refusal naming the source-direct writer.
        slug=SENSORS_STREAM,
        adapter=_refuse_sensors_direct_export,
        history_floor=date(2026, 7, 29),
        publication_lag_days=1,
        nature="daily_series",
        forecast_module="sensors",
        floor_basis=(
            "NATURE daily_series, forecastable (method/monte_carlo/sensors.py, horizon 30d). "
            "docs/lanes/sensors.md section 3: NWS keeps a rolling ~6-day window and no deeper archive exists, "
            "so the whole record is what this producer has accreted since 2026-08-04 plus its first run's "
            "~6-day reach -- derived there as 2026-07-29 to 2026-08-04, and the earlier end is taken. "
            "SOURCE-DIRECT since 2026-09-07, and the floor is now a claim about the PAST rather than a "
            "window anything can still reach: geo.features was append-only for this lane, and its producer "
            "(postgres-sensors) was deleted the same day, so nothing extends the record backwards or "
            "forwards there. Re-measure min(observed_day) for this layer BEFORE geo.features is dropped -- "
            "it is the last chance to confirm 2026-07-29 rather than inherit it."
        ),
    ),
    LaneRegistration(
        slug=SIGNAL_PLANE_STREAM,
        adapter=_refuse_signal_direct_export,
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
        adapter=_refuse_soil_survey_direct_export,
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
        # Source-direct publication owns the full vegetation window. The generic registry refuses
        # export while historical parity/backfill work is handled by the direct package.
        adapter=_refuse_vegetation_direct_export,
        history_floor=date(2022, 8, 5),
        publication_lag_days=7,
        nature="daily_series",
        # The one forecaster whose module stem is NOT its slug. It predates `layer-lanes.md` (§3
        # says bring it into conformance rather than writing a second one beside it), so the
        # registration records the real filename instead of the convention it breaks.
        forecast_module="vegetation_ndvi_forecast",
        # The ceiling remains ownership metadata for reconciliation reports; it does not enable a
        # PostgreSQL writer or create a fallback window.
        writer_ceiling=VEGETATION_DIRECT_WRITER_START_DAY,
        floor_basis=(
            "NATURE daily_series, forecastable (method/monte_carlo/vegetation_ndvi_forecast.py, horizon 30d). "
            "docs/lanes/vegetation.md section 3: the governed forecastable plane holds 2022-08-05 to "
            "2026-08-04, the deepest record of any lane. Lag 7 from section 2's MEASURED median 7-day gap "
            "between observation days, which is worse than the nominal 5-day Sentinel-2 revisit because cloud "
            "screening removes scenes. Most days in this window are correctly a governed absence. "
            "The generic exporter stops at 2026-09-05, the direct-writer ownership handoff boundary "
            "(`pipeline/direct/vegetation/products.py::VEGETATION_DIRECT_WRITER_START_DAY`, "
            "OPERATOR-VERIFY BEFORE ACTIVATION per that module's own comment): "
            "`pipeline/direct/vegetation/forward.py` owns every day after it, and `backfill.py` -- through "
            "this SAME unchanged adapter -- owns every day at or before it. The adapter itself must not be "
            "swapped to a source-direct refusal before that backfill discharges."
        ),
    ),
    LaneRegistration(
        slug=WATER_GAUGES_STREAM,
        adapter=_refuse_water_gauges_direct_export,
        history_floor=date(2026, 5, 24),
        publication_lag_days=2,
        nature="daily_series",
        forecast_module="water_gauges",
        writer_ceiling=WATER_GAUGES_DIRECT_WRITER_START_DAY - timedelta(days=1),
        floor_basis=(
            "NATURE daily_series, forecastable (method/monte_carlo/water_gauges.py, horizon 30d). "
            "docs/lanes/water-gauges.md section 3: the DENSE record starts 2026-05-24. The code floor "
            "USGS_DAILY_VALUES_EARLIEST = 2022-08-05 is explicitly BORROWED from the vegetation layer, not "
            "source-imposed, and nothing confirms the archive walk has reached it -- using it would invent "
            "~1,400 phantom gap-days. The bare min(observed_day) of 1990-10-01 is documented there as a trap. "
            "Lag 2: USGS daily values are provisional same-day-to-next-day (UNVERIFIED for this bbox). "
            "The generic exporter stops before the 2026-09-02 direct-writer ownership floor."
        ),
    ),
    LaneRegistration(
        # SOURCE-DIRECT SINCE 2026-09-06: both fields moved in ONE edit, because on this lane neither
        # could move alone. `_watersheds_watermark` read `geo.features`, which `postgres-watersheds`
        # was the only writer of; the moment that lane stops, such a watermark stops advancing and
        # reports a version that never changes again -- so a swap of the adapter alone would have left
        # a source-direct writer keyed to a frozen clock, and a swap of the watermark alone would have
        # left the Postgres export publishing under a version day it did not produce. Both now read
        # NHDPlus_HR: the adapter refuses and names `pipeline/direct/watersheds`, and the watermark is
        # that package's `watermark.py`, which reads the source's own `loaddate` through `source.py`.
        #
        # `writer_ceiling` is refused here as on every `static_lookup` (`__post_init__`: a
        # version-stamped lane has no calendar window to divide between two writers). The generic
        # executor lane is removed, so only the source watermark can drive this stream.
        slug=WATERSHEDS_STREAM,
        adapter=_refuse_watersheds_direct_export,
        history_floor=date(2026, 8, 7),
        publication_lag_days=0,
        nature="static_lookup",
        watermark=_watersheds_watermark,
        floor_basis=(
            "NATURE static_lookup, WATERMARK-DRIVEN, SOURCE-DIRECT since 2026-09-06. "
            "docs/lanes/watersheds.md section 2: exactly ONE load day "
            "exists, 2026-08-07, all 9,396 rows, and section 3 states the boundaries are a snapshot rather "
            "than a series. A HUC12 boundary is a reference fact with a VERSION, so the partition day comes "
            "from NHDPlus_HR's own max(loaddate) over the accepted basins in the configured extent "
            "(pipeline/direct/watersheds/watermark.py, measured 2019-11-21 against production 2026-09-06) "
            "and the floor is INERT. It used to come from geo.features' change-gated updated_at/created_at, "
            "which was an ingestion-time proxy for that same fact and stopped advancing with its producer. "
            "Lag 0 because a version stamp is not settled by waiting."
        ),
    ),
    LaneRegistration(
        # SOURCE-DIRECT SINCE 2026-09-07. `weather-observations-direct-forward`
        # (execution/job_executor_service.py) is ACTIVE and `parquet-weather-observations` is retired, so
        # this adapter refuses and names `pipeline/direct/weather_observations`.
        #
        # THE OLD GATE WANTED A CITED OWNERSHIP-BOUNDARY DAY, AND THERE STILL IS NONE -- the package
        # ships no `*_DIRECT_WRITER_START_DAY`-equivalent constant and no `backfill.py`. What changed is
        # that a boundary day is only worth having while TWO writers share the window, and they no
        # longer do: the direct lane is the only writer this stream has. A `writer_ceiling` would now be
        # an invented number dividing a window nobody else writes, which is why this registration
        # declares none rather than guessing one to look symmetrical with
        # vegetation/fire-detections/water-gauges.
        #
        # THE FLOOR AND LAG BELOW ARE STILL UNCITED, and the swap does not fix that -- it changes who
        # writes the lane, not what its contract says. `docs/lanes/weather-observations.md` still
        # describes the SIGNAL stream instead of this one. See the FALLBACK paragraph.
        slug=WEATHER_OBSERVATIONS_STREAM,
        adapter=_refuse_weather_observations_direct_export,
        history_floor=date(2026, 8, 1),
        publication_lag_days=2,
        nature="daily_series",
        floor_basis=(
            "NATURE daily_series, SOURCE-DIRECT since 2026-09-07, NOT forecastable: no "
            "method/monte_carlo/weather_observations.py exists and "
            "the lane's contract declares no horizon -- because it declares nothing at all, see below. "
            "FALLBACK -- NOT DECLARED ANYWHERE, AND THE GUESS IS DELIBERATELY SHALLOW. RUNBOOK section 0.26.8: "
            "docs/lanes/weather-observations.md describes the NASA POWER / ERA5-Land archive, which is the "
            "SIGNAL stream, already registered above. The producer THIS lane exports -- ingest/open_meteo.py's "
            "WEATHER_LAYER current-conditions poll into geo.features -- has no contract content at all: no "
            "declared cadence, horizon, historical depth or known-gaps list. 2026-08-01 is a conservative "
            "recent floor chosen so a wrong guess costs a few dozen phantom gap-days instead of thousands, and "
            "lag 2 is borrowed from the hourly ingest-all tick. WRITE THAT HALF OF THE CONTRACT, then measure "
            "min(geo.feature_observation_day) for this layer and replace both numbers -- BEFORE geo.features "
            "is dropped, because that measurement is only available while the table exists and this lane no "
            "longer reads it for anything else."
        ),
    ),
)

# The GENERATED source-direct registrations: one per climate/soil product, so each has a floor, a
# lag, a nature and a census, but with no PostgreSQL producer behind it, so the registered adapter
# refuses. See `pipeline/direct/AGENTS.md`, "Ownership, and why the registered adapter refuses".
#
# ONLY `climate` AND `soil` ARE GENERATED HERE, but they are far from the only source-direct lanes.
# Six hand-written registrations above carry the same kind of refusing adapter, with the measured
# floors those lanes had already earned: `watersheds` and `evacuation-zones` (swapped 2026-09-06),
# and `burn-severity`, `drought`, `sensors` and `weather-observations` (swapped 2026-09-07, once
# each one's own stated condition had been discharged). They are source-direct in every sense this
# comment means, and they live above only because their registrations are not derived from a
# product list.
#
# TWO PACKAGES ARE STILL UNSWAPPED, each for a reason recorded on its own registration above and in
# `tests/direct/test_direct_package_registration.py::PENDING_REGISTRATION`. Neither reason is "the
# join has not got to it yet":
#   `vegetation`     -- `pipeline/direct/vegetation/backfill.py:149-155` republishes every day at or
#                       below the ownership boundary THROUGH the registered `_fill_vegetation`
#                       adapter to reach D2 parity, so a refusal here would make
#                       `refuse_pre_ownership_day` reject the backfill's entire window by
#                       construction. This one is load-bearing, not pending.
#   `fire-perimeters` -- owes TWO substitutions (adapter AND watermark) and its direct lane is still
#                       shadow, so the generic lane is the writer the object stream actually has.
# A shadow writer cannot be a registration's adapter while the generic `parquet-*` lane beside it is
# the writer the object stream actually has -- which is the test the other six all passed before
# they moved.


def _climate_floor_basis(product: ClimateFieldProduct) -> str:
    """Cite this product's floor and lag from the artifacts they were read off, never from a guess."""
    lag_citation = (
        "Lag 5 is NASA POWER's MEASURED meteorology publication lag, "
        "`execution/coverage_census.py` PUBLICATION_LAG_DAYS['nasa-power-daily']."
        if product.publication_lag_days == CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS
        else (
            f"Lag {CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS} is CONSERVATIVE AND NOT MEASURED against "
            "POWER's live solar edge. It is 5 (the measured meteorology lag) plus the 67-day difference between "
            "the canonical snapshot's meteorology last day (2026-08-06) and its ALLSKY_SFC_SW_DWN last day "
            "(2026-05-31) in the same build, plus three days of slack. MEASURE POWER's own solar edge and "
            "replace it: over-waiting delays a real day by one tick, under-waiting manufactures a wrong "
            "governed absence."
        )
    )
    return (
        "NATURE daily_series, NOT forecastable: no method/monte_carlo module projects a climate field, and the "
        "lane deliberately claims no horizon. SOURCE-DIRECT: there is no PostgreSQL producer, so the registered "
        "adapter refuses and `pipeline/direct/climate/forward.py` writes every day through this same "
        f"registration. Floor {product.history_floor.isoformat()} is the day after this product's OWN immutable "
        f"history ends ({product.snapshot_last_day.isoformat()}), which for shortwave radiation is nine weeks "
        "earlier than for the meteorology products -- "
        "`scripts/build_shortwave_radiation_from_canonical_snapshot.py` pins SOURCE_SNAPSHOT_LAST_DAY=2026-08-06 "
        "and EXPECTED_LAST_DAY=2026-05-31. Those days are immutable and the adapter refuses to republish them. "
        f"{lag_citation}"
    )


def _soil_floor_basis(product: SoilFieldProduct) -> str:
    """Cite this product's floor and lag from the artifacts they were read off, never from a guess."""
    return (
        "NATURE daily_series, NOT forecastable: no method/monte_carlo module projects a soil field, and the lane "
        "deliberately claims no horizon. SOURCE-DIRECT: there is no PostgreSQL producer, so the registered "
        "adapter refuses and `pipeline/direct/soil/forward.py` writes every day through this same registration. "
        f"Floor {product.history_floor.isoformat()} is the day after the immutable history of all eight ERA5-Land "
        f"streams ends ({product.snapshot_last_day.isoformat()}) -- `scripts/vpd_snapshot_breakdown.py` and "
        "`scripts/build_soil_moisture_from_canonical_snapshot.py` both pin EXPECTED_LAST_DAY=2026-08-02, and the "
        "three reviewed plans' window.end_date agrees. Those days are immutable and the adapter refuses to "
        f"republish them. Lag {ERA5_LAND_ARCHIVE_PUBLICATION_LAG_DAYS} is the MEASURED publication lag of the "
        "REDISTRIBUTOR this writer reads, `execution/coverage_census.py` "
        "PUBLICATION_LAG_DAYS['open-meteo-era5-land-archive'], measured against production 2026-08-11. It is "
        "deliberately not the ~5-day ERA5T latency of the Copernicus product itself: asking for a day "
        "Open-Meteo has not mirrored returns an all-null series, which this writer would record as a governed "
        "absence that is simply wrong."
    )


_SOURCE_DIRECT_REGISTRATIONS: Final[tuple[LaneRegistration, ...]] = (
    *(
        LaneRegistration(
            slug=product.stream,
            adapter=_refuse_climate_direct_export,
            history_floor=product.history_floor,
            publication_lag_days=product.publication_lag_days,
            nature="daily_series",
            floor_basis=_climate_floor_basis(product),
        )
        for product in CLIMATE_FIELD_PRODUCTS
    ),
    *(
        LaneRegistration(
            slug=product.stream,
            adapter=_refuse_soil_direct_export,
            history_floor=product.history_floor,
            publication_lag_days=product.publication_lag_days,
            nature="daily_series",
            floor_basis=_soil_floor_basis(product),
        )
        for product in SOIL_FIELD_PRODUCTS
    ),
)

# --- The conformed calendar dimension -----------------------------------------------------------
#
# The floor is DERIVED, not declared: the union of every source-bearing lane's own floor, so the
# dimension covers every day any lane can key to it. Deriving it is what stops the calendar and the
# deepest lane (`fire-detections`, 2000-11-01) drifting apart when a floor is next corrected.

CALENDAR_HISTORY_FLOOR: Final[date] = min(
    registration.history_floor for registration in (*_HAND_WRITTEN_REGISTRATIONS, *_SOURCE_DIRECT_REGISTRATIONS)
)

CALENDAR_REGISTRATION: Final = LaneRegistration(
    slug=CALENDAR_STREAM,
    adapter=_fill_calendar,
    history_floor=CALENDAR_HISTORY_FLOOR,
    publication_lag_days=0,
    nature="static_lookup",
    watermark=_calendar_watermark,
    floor_basis=(
        "NATURE static_lookup, WATERMARK-DRIVEN, and the ONE lane with no source system. The floor is DERIVED "
        f"as min(history_floor) across the thirty-one source-bearing lanes -- {CALENDAR_HISTORY_FLOOR.isoformat()}, "
        "which is fire-detections' -- so every day any lane can key to the dimension is in it. Each version "
        f"covers its own day plus {CALENDAR_VERSION_FORWARD_DAYS} days, and must reach today plus "
        f"{CALENDAR_REQUIRED_FORWARD_DAYS}, so a 30-day horizon from any as-of date always resolves and the "
        "lane regenerates roughly once a year instead of once a day. Lag 0: pure computation settles instantly."
    ),
)

LANE_REGISTRATIONS: Final[tuple[LaneRegistration, ...]] = tuple(
    sorted(
        (*_HAND_WRITTEN_REGISTRATIONS, *_SOURCE_DIRECT_REGISTRATIONS, CALENDAR_REGISTRATION),
        key=lambda entry: entry.slug,
    )
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
