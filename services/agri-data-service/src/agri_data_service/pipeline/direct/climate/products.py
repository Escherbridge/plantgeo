"""The eleven NASA POWER products, their one source parameter each, and their clocks.

Eleven streams under seven browser toggles: eight climate fields plus the three soil-wetness depths,
which ride the SAME point request. One POWER response returns every parameter the URL asked for, so
a product costs a fan-out nothing once its parameter is on the list -- which is why the soil-wetness
depths belong here and not in a second writer with a second 397-cell fan-out over the same lattice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

from agri_data_service.warehouse.parquet.schema import get_stream_schema

# The four modules below export the slug as a bare `STREAM`, unlike the other four; the aliases keep
# every product below reading the same way rather than mixing two spellings of the same fact.
from agri_data_service.warehouse.schemas.climate_field_air_temperature_max import (
    STREAM as CLIMATE_FIELD_AIR_TEMPERATURE_MAX_STREAM,
)
from agri_data_service.warehouse.schemas.climate_field_air_temperature_mean import (
    STREAM as CLIMATE_FIELD_AIR_TEMPERATURE_MEAN_STREAM,
)
from agri_data_service.warehouse.schemas.climate_field_air_temperature_min import (
    STREAM as CLIMATE_FIELD_AIR_TEMPERATURE_MIN_STREAM,
)
from agri_data_service.warehouse.schemas.climate_field_dew_point import STREAM as CLIMATE_FIELD_DEW_POINT_STREAM
from agri_data_service.warehouse.schemas.climate_field_precipitation import CLIMATE_FIELD_PRECIPITATION_STREAM
from agri_data_service.warehouse.schemas.climate_field_relative_humidity import CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM
from agri_data_service.warehouse.schemas.climate_field_shortwave_radiation import (
    CLIMATE_FIELD_SHORTWAVE_RADIATION_STREAM,
)
from agri_data_service.warehouse.schemas.climate_field_wind_speed import CLIMATE_FIELD_WIND_SPEED_STREAM
from agri_data_service.warehouse.schemas.soil_wetness_profile import STREAM as SOIL_WETNESS_PROFILE_STREAM
from agri_data_service.warehouse.schemas.soil_wetness_root_zone import STREAM as SOIL_WETNESS_ROOT_ZONE_STREAM
from agri_data_service.warehouse.schemas.soil_wetness_surface import STREAM as SOIL_WETNESS_SURFACE_STREAM

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema

#: The row shape of a product base rung. `signal_plane` is the frozen twelve-column plane;
#: `snapshot_lineage` is that plane plus twenty-one lineage columns; `snapshot_lane` is that plane
#: plus the seven-column selection vocabulary the soil-wetness breakdown froze
#: (`warehouse/parquet/snapshot_signal_product.py` SOIL_TEMPERATURE_FIELDS[2:]).
#: See `pipeline/direct/AGENTS.md`.
ClimateRowShape = Literal["signal_plane", "snapshot_lineage", "snapshot_lane"]

#: The browser toggle a stream is drawn under. `--product` names one of these, never a stream slug:
#: air temperature is ONE product served by three streams and soil wetness is ONE product served by
#: three depth lanes, so seven toggles cover eleven streams.
ClimateProductId = Literal[
    "air-temperature",
    "dew-point",
    "precipitation",
    "relative-humidity",
    "shortwave-radiation",
    "soil-wetness",
    "wind-speed",
]

CLIMATE_PRODUCT_IDS: Final[tuple[ClimateProductId, ...]] = (
    "air-temperature",
    "dew-point",
    "precipitation",
    "relative-humidity",
    "shortwave-radiation",
    "soil-wetness",
    "wind-speed",
)

#: Last day of the immutable canonical signal snapshot itself, and so the last day the seven
#: meteorology products already hold. `scripts/build_shortwave_radiation_from_canonical_snapshot.py`
#: SOURCE_SNAPSHOT_LAST_DAY.
CANONICAL_SNAPSHOT_LAST_DAY: Final = date(2026, 8, 6)

#: The last day the shortwave lane was actually BUILT to, which is not the snapshot last day. The
#: same script pins EXPECTED_LAST_DAY=2026-05-31: the snapshot's source ledger reached 2026-08-06 for
#: meteorology and only 2026-05-31 for ALLSKY_SFC_SW_DWN, so that product's immutable history ends
#: nine weeks earlier and its forward floor is nine weeks earlier with it.
#: THE FLOOR IS CORRECT AND STAYS. The 2026-09-15 solar measurement (SHORTWAVE_LAG_MEASUREMENT_EVIDENCE
#: below) found real POWER values for every day of June, July and August, so once the lag is honest
#: the forward walk owns and fills 2026-06-01 onward itself: `forward.py` scans
#: CLIMATE_BACKLOG_SCAN_DAYS (400) back from the settled ceiling, which reaches past this floor in one
#: census. Raising the floor to hide the tail would hand those days to nobody.
SHORTWAVE_RADIATION_SNAPSHOT_LAST_DAY: Final = date(2026, 5, 31)

#: First day the direct writer owns for the seven meteorology products: the day after the immutable
#: snapshot's last day. Shortwave radiation derives its own from its own last built day.
CLIMATE_DIRECT_WRITER_START_DAY: Final = date(2026, 8, 7)

#: The measured NASA POWER meteorology publication lag, `execution/coverage_census.py`
#: PUBLICATION_LAG_DAYS["nasa-power-daily"] = 5. Applies to every MERRA-2-derived parameter.
CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS: Final = 5

#: Where the 2026-09-15 solar-edge measurement lives: five PNW point captures, their edge summary,
#: and the production turn reports read off the executor on 2026-09-15 and 2026-09-16. Cited by
#: `lane_registry._climate_floor_basis` so a lag with no artifact behind it fails
#: `tests/direct/climate/test_lane_registrations.py`.
SHORTWAVE_LAG_MEASUREMENT_EVIDENCE: Final = ".omc/research/runbook-20260915-shortwave/"

#: The solar lag, MEASURED against POWER's own live edge on 2026-09-15 (SHORTWAVE_LAG_MEASUREMENT_EVIDENCE,
#: five PNW points including -122.3/47.6 and -116.2/43.6):
#: `https://power.larc.nasa.gov/api/temporal/daily/point?parameters=ALLSKY_SFC_SW_DWN,T2M&
#: community=AG&longitude=-122.3&latitude=47.6&start=20260501&end=20260915&format=JSON`
#: returned ALLSKY_SFC_SW_DWN real through 2026-09-11 and T2M through 2026-09-12 at every point, with
#: NO interior fill days across June, July and August -- a 4-day solar latency beside a 3-day
#: meteorology one, so the two products are ONE day apart, not the 67 the old 75 was inferred from.
#: That 67 was a property of the stale canonical snapshot artifact, never of the provider.
#:
#: THE MARGIN IS THE OBSERVED JITTER OF THE EDGE, NOT A COPY OF THE METEOROLOGY CONSTANT'S. The
#: in-tree 5 was measured on 2026-08-11 with NO margin at all (`execution/coverage_census.py`
#: PUBLICATION_LAG_DAYS: POWER's newest day was then 5 days back); the same edge read 3 days back on
#: 2026-09-15. The edge moves a couple of days between measurements, so 6 is the measured 4 plus that
#: jitter -- about 0-1 day of margin over the solar edge on the September reading, roughly what 5
#: carries over meteorology today. Over-waiting delays a real day by one tick; under-waiting at the
#: frontier is harmless (an all-fill newest day has no later published day to mirror against, so it
#: is `source_unsettled` and writes nothing), and under-waiting BEHIND the frontier is what makes a
#: wrong governed absence, which `forward.CLIMATE_ABSENCE_RECHECK_DAYS` (14, which must stay greater
#: than this lag) exists to retract.
#:
#: WHAT THE ALL-CELL PREDICATE DOES AND DOES NOT PROTECT. A day is governed absent only when EVERY
#: support cell answers a fill, so a late whole-lattice release cannot become a wrong absence at
#: the frontier and is retracted behind it. It does NOTHING for a day where SOME cells still trail:
#: `source.build_climate_day` drops each fill cell with no row and the day is written short, stamped
#: complete at every rung, and (before `forward._partial_days_in_recheck_window`) never selected
#: again. `tests/direct/climate/fixtures/nasa-power-point-response-2026-09-02.json` is the proof of
#: that mode, not of safety: -119/46 was fill for 2026-08-20 THIRTEEN days back, and reads 23.73
#: today. Lag 6 accepts exactly the per-cell exposure the ten lag-5 siblings (seven climate fields
#: and three soil-wetness depths) already carry; the partial-day recheck in `forward.py` is what
#: bounds it, for all eleven products alike.
#:
#: Re-measure with the URL above before moving it, and record the reading in the evidence directory.
CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS: Final = 6

#: The `agri.signal_observation.support_key` every NASA POWER lane writes; `execution/coverage_contract.py`.
NASA_POWER_SUPPORT_KEY: Final = "surface"

#: The `agri.data_source.key` for this source; the value the historical breakdown rows carry.
NASA_POWER_SOURCE_KEY: Final = "nasa-power-daily"

#: One candidate per grain per response, so precedence is trivial -- and named, so a reader never has
#: to infer it from the snapshot contract, which resolves a multi-release race that cannot occur here.
CLIMATE_DIRECT_PRECEDENCE_CONTRACT: Final = "nasa-power-point-per-support-cell-v1"

#: Prefix of the `source_snapshot_id` a direct row carries, and the discriminator every lineage
#: column depends on: on a `direct:` row those columns are scoped to one POWER response object,
#: never to `agri.signal_observation`. See `pipeline/direct/AGENTS.md`, "Direct lineage namespace".
CLIMATE_DIRECT_SNAPSHOT_PREFIX: Final = "direct:"


@dataclass(frozen=True, slots=True)
class ClimateFieldProduct:
    """One object stream, the single POWER parameter that fills it, and the clock it keys to."""

    stream: str
    product_id: ClimateProductId
    source_parameter: str
    signal_name: str
    normalized_unit: str
    row_shape: ClimateRowShape
    publication_lag_days: int
    snapshot_last_day: date = CANONICAL_SNAPSHOT_LAST_DAY

    @property
    def stream_schema(self) -> ParquetStreamSchema:
        """Return the registered observed contract this base rung must conform to."""
        return get_stream_schema(self.stream)

    @property
    def history_floor(self) -> date:
        """Return the first day this writer owns: the day after this product's own immutable history."""
        return self.snapshot_last_day + timedelta(days=1)


CLIMATE_FIELD_PRODUCTS: Final[tuple[ClimateFieldProduct, ...]] = (
    ClimateFieldProduct(
        stream=CLIMATE_FIELD_AIR_TEMPERATURE_MAX_STREAM,
        product_id="air-temperature",
        source_parameter="T2M_MAX",
        signal_name="air_temperature_max",
        normalized_unit="C",
        row_shape="signal_plane",
        publication_lag_days=CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS,
    ),
    ClimateFieldProduct(
        stream=CLIMATE_FIELD_AIR_TEMPERATURE_MEAN_STREAM,
        product_id="air-temperature",
        source_parameter="T2M",
        signal_name="air_temperature_mean",
        normalized_unit="C",
        row_shape="signal_plane",
        publication_lag_days=CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS,
    ),
    ClimateFieldProduct(
        stream=CLIMATE_FIELD_AIR_TEMPERATURE_MIN_STREAM,
        product_id="air-temperature",
        source_parameter="T2M_MIN",
        signal_name="air_temperature_min",
        normalized_unit="C",
        row_shape="signal_plane",
        publication_lag_days=CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS,
    ),
    ClimateFieldProduct(
        stream=CLIMATE_FIELD_DEW_POINT_STREAM,
        product_id="dew-point",
        source_parameter="T2MDEW",
        signal_name="dew_point_temperature",
        normalized_unit="C",
        row_shape="signal_plane",
        publication_lag_days=CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS,
    ),
    ClimateFieldProduct(
        stream=CLIMATE_FIELD_PRECIPITATION_STREAM,
        product_id="precipitation",
        source_parameter="PRECTOTCORR",
        signal_name="precipitation",
        normalized_unit="mm/day",
        row_shape="snapshot_lineage",
        publication_lag_days=CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS,
    ),
    ClimateFieldProduct(
        stream=CLIMATE_FIELD_RELATIVE_HUMIDITY_STREAM,
        product_id="relative-humidity",
        source_parameter="RH2M",
        signal_name="relative_humidity",
        normalized_unit="%",
        row_shape="snapshot_lineage",
        publication_lag_days=CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS,
    ),
    ClimateFieldProduct(
        stream=CLIMATE_FIELD_SHORTWAVE_RADIATION_STREAM,
        product_id="shortwave-radiation",
        source_parameter="ALLSKY_SFC_SW_DWN",
        signal_name="surface_shortwave_radiation",
        normalized_unit="MJ/m^2/day",
        row_shape="snapshot_lineage",
        publication_lag_days=CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS,
        snapshot_last_day=SHORTWAVE_RADIATION_SNAPSHOT_LAST_DAY,
    ),
    # The three soil-wetness depths. SAME source, SAME support, SAME meteorology lag and SAME
    # snapshot last day as the seven products above -- one POWER point request already returns every
    # parameter it was asked for, so these three cost the fan-out nothing beyond three more
    # parameters on a URL. They report a DEGREE OF SATURATION, not a volumetric water content, and
    # name their depth in the signal rather than in `support_key`
    # (`execution/weather_observations/nasa_power.py` NASA_POWER_SIGNAL_SPECIFICATIONS). Their
    # `snapshot_lane` row shape is a third one, frozen by `scripts/soil_wetness_snapshot_breakdown.py`.
    ClimateFieldProduct(
        stream=SOIL_WETNESS_SURFACE_STREAM,
        product_id="soil-wetness",
        source_parameter="GWETTOP",
        signal_name="soil_wetness_surface",
        normalized_unit="fraction_of_saturation",
        row_shape="snapshot_lane",
        publication_lag_days=CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS,
    ),
    ClimateFieldProduct(
        stream=SOIL_WETNESS_ROOT_ZONE_STREAM,
        product_id="soil-wetness",
        source_parameter="GWETROOT",
        signal_name="soil_wetness_root_zone",
        normalized_unit="fraction_of_saturation",
        row_shape="snapshot_lane",
        publication_lag_days=CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS,
    ),
    ClimateFieldProduct(
        stream=SOIL_WETNESS_PROFILE_STREAM,
        product_id="soil-wetness",
        source_parameter="GWETPROF",
        signal_name="soil_wetness_profile",
        normalized_unit="fraction_of_saturation",
        row_shape="snapshot_lane",
        publication_lag_days=CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS,
    ),
    ClimateFieldProduct(
        stream=CLIMATE_FIELD_WIND_SPEED_STREAM,
        product_id="wind-speed",
        source_parameter="WS2M",
        signal_name="wind_speed",
        normalized_unit="m/s",
        row_shape="signal_plane",
        publication_lag_days=CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS,
    ),
)

CLIMATE_FIELD_PRODUCT_BY_STREAM: Final[Mapping[str, ClimateFieldProduct]] = MappingProxyType(
    {product.stream: product for product in CLIMATE_FIELD_PRODUCTS}
)

#: The CLI's default per-turn wall clock, HERE rather than in `forward.py` because
#: `execution/job_executor_service.py` derives this lane's `command_timeout_seconds` from it and
#: importing the forward driver into the scheduler would drag the object store and the engine along.
#: The executor command passes no override, so this is the budget that actually runs.
CLIMATE_DEFAULT_TIME_BUDGET_SECONDS: Final = 900.0

#: Every parameter one point request carries, so one cell-day response serves all eleven streams.
CLIMATE_SOURCE_PARAMETERS: Final[tuple[str, ...]] = tuple(
    sorted({product.source_parameter for product in CLIMATE_FIELD_PRODUCTS})
)

#: How many distinct settled edges one turn can select days at: the meteorology lag and the solar
#: one. It is what a per-turn request budget multiplies `--max-days` by; see `pipeline/direct/AGENTS.md`.
#: DERIVED, NOT PINNED: were every product to share one lag this would read 1 and the budget 397,
#: which would be exactly right -- one distinct day is one 397-cell fan-out however many products
#: read it (`source.ClimateSourceCache` is keyed by cell and day). A second clock is a second
#: fan-out in the same turn, and that second fan-out is what POWER answered 429 in production.
CLIMATE_DISTINCT_PUBLICATION_CLOCKS: Final = len({product.publication_lag_days for product in CLIMATE_FIELD_PRODUCTS})


def products_for(product_id: str) -> tuple[ClimateFieldProduct, ...]:
    """Resolve one browser toggle -- or every one -- to the streams that serve it, in slug order."""
    if product_id == "all":
        return CLIMATE_FIELD_PRODUCTS
    selected = tuple(product for product in CLIMATE_FIELD_PRODUCTS if product.product_id == product_id)
    if not selected:
        raise ValueError(f"unknown climate product {product_id!r}; expected one of {CLIMATE_PRODUCT_IDS} or 'all'")
    return selected


__all__ = [
    "CANONICAL_SNAPSHOT_LAST_DAY",
    "CLIMATE_DEFAULT_TIME_BUDGET_SECONDS",
    "CLIMATE_DIRECT_PRECEDENCE_CONTRACT",
    "CLIMATE_DIRECT_SNAPSHOT_PREFIX",
    "CLIMATE_DIRECT_WRITER_START_DAY",
    "CLIMATE_DISTINCT_PUBLICATION_CLOCKS",
    "CLIMATE_FIELD_PRODUCTS",
    "CLIMATE_FIELD_PRODUCT_BY_STREAM",
    "CLIMATE_METEOROLOGY_PUBLICATION_LAG_DAYS",
    "CLIMATE_PRODUCT_IDS",
    "CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS",
    "CLIMATE_SOURCE_PARAMETERS",
    "NASA_POWER_SOURCE_KEY",
    "NASA_POWER_SUPPORT_KEY",
    "SHORTWAVE_LAG_MEASUREMENT_EVIDENCE",
    "SHORTWAVE_RADIATION_SNAPSHOT_LAST_DAY",
    "ClimateFieldProduct",
    "ClimateProductId",
    "ClimateRowShape",
    "products_for",
]
