"""The eight ERA5-Land soil products, the one archive variable each, and the clock they all key to.

THE UPSTREAM IS THE OPEN-METEO ERA5-LAND ARCHIVE, NOT THE COPERNICUS CDS. Every historical day of
all eight streams was written from `open-meteo-era5-land-archive` at support `era5-land-0.1deg`
(`scripts/build_soil_moisture_from_canonical_snapshot.py` SOURCE_KEY/SUPPORT_KEY,
`scripts/soil_temperature_snapshot_breakdown.py` EXPECTED_SOURCE_KEY/EXPECTED_SUPPORT_KEY,
`scripts/vpd_snapshot_breakdown.py` ProductContract). The retired CDS lane
(`execution/historical_era5.py`) fetched a 1.0-degree OUTPUT grid, needs a credential, has no
variable for VPD at all, and carries only soil-water layer 1 of the three this product family
serves -- so a forward writer built on it would extend none of these histories comparably, and
`agri.data_source` has no `era5-land` key for it to write under. See `pipeline/direct/AGENTS.md`,
"ERA5-Land soil: the upstream is Open-Meteo, and why it is not the CDS".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

from agri_data_service.warehouse.parquet.schema import get_stream_schema

# Every module below exports the slug as a bare `STREAM`; the aliases keep the product table reading
# as one table rather than as eight identically-named imports.
from agri_data_service.warehouse.schemas.soil_field_moisture_0_7cm import STREAM as SOIL_FIELD_MOISTURE_0_7CM_STREAM
from agri_data_service.warehouse.schemas.soil_field_moisture_7_28cm import STREAM as SOIL_FIELD_MOISTURE_7_28CM_STREAM
from agri_data_service.warehouse.schemas.soil_field_moisture_28_100cm import (
    STREAM as SOIL_FIELD_MOISTURE_28_100CM_STREAM,
)
from agri_data_service.warehouse.schemas.soil_field_vpd import SOIL_FIELD_VPD_STREAM
from agri_data_service.warehouse.schemas.soil_temperature_0_to_7cm import STREAM as SOIL_TEMPERATURE_0_TO_7CM_STREAM
from agri_data_service.warehouse.schemas.soil_temperature_7_to_28cm import STREAM as SOIL_TEMPERATURE_7_TO_28CM_STREAM
from agri_data_service.warehouse.schemas.soil_temperature_28_to_100cm import (
    STREAM as SOIL_TEMPERATURE_28_TO_100CM_STREAM,
)
from agri_data_service.warehouse.schemas.soil_temperature_100_to_255cm import (
    STREAM as SOIL_TEMPERATURE_100_TO_255CM_STREAM,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema

#: The row shape of a product base rung. Three, not the climate writer's two: the soil families were
#: built by three different snapshot breakdowns and each froze its own column set.
#: `signal_plane` is the frozen twelve-column plane (VPD); `snapshot_lineage` is that plane plus
#: twenty-one lineage columns (moisture); `soil_temperature` is the twenty-one-column lane shape
#: that leads with `data_source_key`/`source_parameter` (temperature). See `pipeline/direct/AGENTS.md`.
SoilRowShape = Literal["signal_plane", "snapshot_lineage", "soil_temperature"]

#: The browser toggle a stream is drawn under. `--product` names one of these, never a stream slug:
#: `src/types/time-slider.ts` SLIDER_STREAM_LAYER_NAMES has exactly three soil entries, and
#: `src/lib/server/services/parquet-trpc-readers.ts` fans each out to its physical lanes.
SoilProductId = Literal["moisture", "temperature", "vpd"]

SOIL_PRODUCT_IDS: Final[tuple[SoilProductId, ...]] = ("moisture", "temperature", "vpd")

#: Last day of the immutable snapshot every one of these eight streams holds, and it is ONE day for
#: all eight: `scripts/vpd_snapshot_breakdown.py` EXPECTED_LAST_DAY,
#: `scripts/build_soil_moisture_from_canonical_snapshot.py` EXPECTED_LAST_DAY, and the `window.end_date`
#: of all three reviewed plans (`plans/open-meteo-era5-land-pnw-{vpd,soiltemp,ndvi-lattice}-20220802-20260802.json`).
ERA5_LAND_SNAPSHOT_LAST_DAY: Final = date(2026, 8, 2)

#: First day this writer owns: the day after that immutable history ends. Imported by
#: `parquet_ops/snapshot_products.py` as the five soil products' `forward_first_day`, so the boundary
#: between the closed manifest and the live lane is ONE constant rather than six copies of a date.
SOIL_DIRECT_WRITER_START_DAY: Final = date(2026, 8, 3)

#: The MEASURED publication lag of this upstream: `execution/coverage_census.py`
#: PUBLICATION_LAG_DAYS["open-meteo-era5-land-archive"] = 9, measured against production 2026-08-11
#: when the archive's newest day was 2026-08-02.
#:
#: It is deliberately NOT the ~5-day ERA5T near-real-time latency of the CDS product itself. Nine is
#: what the REDISTRIBUTOR was observed to publish at, and this writer reads the redistributor. Asking
#: for a day the archive has not mirrored yet returns a present, entirely-null series, which this
#: writer would have to record as a governed absence -- a wrong one that no later run retracts by
#: itself. Over-waiting costs one tick; under-waiting manufactures a false absence.
ERA5_LAND_ARCHIVE_PUBLICATION_LAG_DAYS: Final = 9

#: The `agri.signal_observation.support_key` every row of these eight streams carries. It names the
#: SOURCE's native 0.1-degree lattice, not the 0.25-degree analysis lattice the cells sit on; the two
#: are different facts and both are recorded. `execution/weather_observations/era5_land.py`
#: OPEN_METEO_ARCHIVE_SUPPORT_KEY.
ERA5_LAND_SUPPORT_KEY: Final = "era5-land-0.1deg"

#: The `agri.data_source.key` of the upstream, byte-identical to the value every historical row of
#: these streams carries. A direct row that changed it would not merge with the history it extends.
ERA5_LAND_SOURCE_KEY: Final = "open-meteo-era5-land-archive"

#: One candidate per grain per response, so precedence is trivial -- and named, so a reader never has
#: to infer it. DELIBERATELY NOT the historical rows' `newest-release-retrieved-at-then-highest-
#: observation-id-v1`: that contract describes selecting a winner among several PostgreSQL releases
#: of one cell-day, and a direct fetch has exactly one candidate and no release ledger to rank.
SOIL_DIRECT_PRECEDENCE_CONTRACT: Final = "open-meteo-era5-land-archive-per-support-cell-v1"

#: Prefix of the `source_snapshot_id` (moisture) and `selected_source_release_id` (temperature) a
#: direct row carries, and the discriminator every lineage column depends on. See
#: `pipeline/direct/AGENTS.md`, "Direct lineage namespace".
SOIL_DIRECT_SNAPSHOT_PREFIX: Final = "direct:"


@dataclass(frozen=True, slots=True)
class SoilFieldProduct:
    """One object stream, the single archive variable that fills it, and the clock it keys to."""

    stream: str
    product_id: SoilProductId
    source_parameter: str
    signal_name: str
    normalized_unit: str
    row_shape: SoilRowShape
    publication_lag_days: int = ERA5_LAND_ARCHIVE_PUBLICATION_LAG_DAYS
    snapshot_last_day: date = ERA5_LAND_SNAPSHOT_LAST_DAY

    @property
    def stream_schema(self) -> ParquetStreamSchema:
        """Return the registered observed contract this base rung must conform to."""
        return get_stream_schema(self.stream)

    @property
    def history_floor(self) -> date:
        """Return the first day this writer owns: the day after this product's own immutable history."""
        return self.snapshot_last_day + timedelta(days=1)


# `signal_name` and `source_parameter` below are transcribed from the builders that wrote the
# history, never from the provider's documentation: moisture from
# `scripts/build_soil_moisture_from_canonical_snapshot.py` PRODUCTS, temperature from
# `scripts/soil_temperature_snapshot_breakdown.py` PRODUCTS, VPD from `scripts/vpd_snapshot_breakdown.py`.
# A drifted name is a second signal wearing the first one's slug, which merges silently.
SOIL_FIELD_PRODUCTS: Final[tuple[SoilFieldProduct, ...]] = (
    SoilFieldProduct(
        stream=SOIL_FIELD_MOISTURE_0_7CM_STREAM,
        product_id="moisture",
        source_parameter="soil_moisture_0_to_7cm_mean",
        signal_name="soil_water_content_layer_1",
        normalized_unit="m^3/m^3",
        row_shape="snapshot_lineage",
    ),
    SoilFieldProduct(
        stream=SOIL_FIELD_MOISTURE_7_28CM_STREAM,
        product_id="moisture",
        source_parameter="soil_moisture_7_to_28cm_mean",
        signal_name="soil_water_content_layer_2",
        normalized_unit="m^3/m^3",
        row_shape="snapshot_lineage",
    ),
    SoilFieldProduct(
        stream=SOIL_FIELD_MOISTURE_28_100CM_STREAM,
        product_id="moisture",
        source_parameter="soil_moisture_28_to_100cm_mean",
        signal_name="soil_water_content_layer_3",
        normalized_unit="m^3/m^3",
        row_shape="snapshot_lineage",
    ),
    SoilFieldProduct(
        stream=SOIL_TEMPERATURE_0_TO_7CM_STREAM,
        product_id="temperature",
        source_parameter="soil_temperature_0_to_7cm_mean",
        signal_name="soil_temperature_level_1",
        normalized_unit="C",
        row_shape="soil_temperature",
    ),
    SoilFieldProduct(
        stream=SOIL_TEMPERATURE_7_TO_28CM_STREAM,
        product_id="temperature",
        source_parameter="soil_temperature_7_to_28cm_mean",
        signal_name="soil_temperature_level_2",
        normalized_unit="C",
        row_shape="soil_temperature",
    ),
    SoilFieldProduct(
        stream=SOIL_TEMPERATURE_28_TO_100CM_STREAM,
        product_id="temperature",
        source_parameter="soil_temperature_28_to_100cm_mean",
        signal_name="soil_temperature_level_3",
        normalized_unit="C",
        row_shape="soil_temperature",
    ),
    SoilFieldProduct(
        stream=SOIL_TEMPERATURE_100_TO_255CM_STREAM,
        product_id="temperature",
        source_parameter="soil_temperature_100_to_255cm_mean",
        signal_name="soil_temperature_level_4",
        normalized_unit="C",
        row_shape="soil_temperature",
    ),
    # VPD is NOT derived here. Open-Meteo publishes `vapour_pressure_deficit_max` as a daily
    # variable of the same era5_land model, and that published series is exactly what the immutable
    # history holds (`scripts/vpd_snapshot_breakdown.py` ProductContract, kPa in and kPa out). A
    # forward writer that recomputed VPD from temperature and dew point would be a second, different
    # estimator writing under the first one's `signal_name`. See `pipeline/direct/AGENTS.md`.
    SoilFieldProduct(
        stream=SOIL_FIELD_VPD_STREAM,
        product_id="vpd",
        source_parameter="vapour_pressure_deficit_max",
        signal_name="vapor_pressure_deficit",
        normalized_unit="kPa",
        row_shape="signal_plane",
    ),
)

SOIL_FIELD_PRODUCT_BY_STREAM: Final[Mapping[str, SoilFieldProduct]] = MappingProxyType(
    {product.stream: product for product in SOIL_FIELD_PRODUCTS}
)

#: The CLI's default per-turn wall clock, HERE rather than in `forward.py` because
#: `execution/job_executor_service.py` derives this lane's `command_timeout_seconds` from it and
#: importing the forward driver into the scheduler would drag the object store and the engine along.
#: The executor command passes no override, so this is the budget that actually runs.
SOIL_DEFAULT_TIME_BUDGET_SECONDS: Final = 900.0

#: Every variable one chunk request carries, so ONE fetch of a day serves all eight streams. Sorted
#: because `ingest/open_meteo.py` `_archive_daily_parameters` refuses an unsorted `daily` list.
SOIL_SOURCE_PARAMETERS: Final[tuple[str, ...]] = tuple(
    sorted({product.source_parameter for product in SOIL_FIELD_PRODUCTS})
)

#: How many distinct settled edges one turn can select days at. ONE, unlike the climate writer's two:
#: all eight variables come off one model on one release schedule, so every product's ceiling is the
#: same day and a turn never straddles two clocks.
SOIL_DISTINCT_PUBLICATION_CLOCKS: Final = len({product.publication_lag_days for product in SOIL_FIELD_PRODUCTS})


def products_for(product_id: str) -> tuple[SoilFieldProduct, ...]:
    """Resolve one browser toggle -- or every one -- to the streams that serve it, in table order."""
    if product_id == "all":
        return SOIL_FIELD_PRODUCTS
    selected = tuple(product for product in SOIL_FIELD_PRODUCTS if product.product_id == product_id)
    if not selected:
        raise ValueError(f"unknown soil product {product_id!r}; expected one of {SOIL_PRODUCT_IDS} or 'all'")
    return selected


__all__ = [
    "ERA5_LAND_ARCHIVE_PUBLICATION_LAG_DAYS",
    "ERA5_LAND_SNAPSHOT_LAST_DAY",
    "ERA5_LAND_SOURCE_KEY",
    "ERA5_LAND_SUPPORT_KEY",
    "SOIL_DEFAULT_TIME_BUDGET_SECONDS",
    "SOIL_DIRECT_PRECEDENCE_CONTRACT",
    "SOIL_DIRECT_SNAPSHOT_PREFIX",
    "SOIL_DIRECT_WRITER_START_DAY",
    "SOIL_DISTINCT_PUBLICATION_CLOCKS",
    "SOIL_FIELD_PRODUCTS",
    "SOIL_FIELD_PRODUCT_BY_STREAM",
    "SOIL_PRODUCT_IDS",
    "SOIL_SOURCE_PARAMETERS",
    "SoilFieldProduct",
    "SoilProductId",
    "SoilRowShape",
    "products_for",
]
