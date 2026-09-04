"""The one vegetation product this writer publishes, and the ownership boundary it starts at.

ONE STREAM, NOT EIGHT. Unlike `climate/` and `soil/`, which fan one upstream fetch out to many
Parquet streams, `vegetation` registers exactly one series per spatial cell for a fixed
`metric_name = 'ndvi'` (`warehouse/schemas/vegetation.py:27-29`). This module still exists, kept
apart from `source.py`/`rows.py`, so the module layout matches its siblings' -- a second product
would be one entry here, not a restructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

from agri_data_service.warehouse.parquet.schema import get_stream_schema
from agri_data_service.warehouse.schemas.vegetation import VEGETATION_PLANE_STREAM

if TYPE_CHECKING:
    from agri_data_service.warehouse.parquet.schema import ParquetStreamSchema

#: `agri.data_source.key` the registered Postgres exporter reads under
#: (`sql/pipeline/vegetation_day_export.sql:71,136`). Recorded here for the parity/backfill modules,
#: which read Postgres directly rather than through `pipeline/lanes/vegetation.py` (a sibling
#: owner's module they call but do not modify).
VEGETATION_SOURCE_KEY: Final = "sentinel2-ndvi-l2a"
VEGETATION_METRIC_NAME: Final = "ndvi"
VEGETATION_METRIC_UNIT: Final = "unitless"
VEGETATION_TRANSFORM_VERSION: Final = "sentinel2-ndvi-daily-cell-mean-v1"

#: `pipeline/parquet/lane_registry.py:869-870` registers this lane's `history_floor` and
#: `publication_lag_days` against the governed Postgres plane. This direct writer targets the SAME
#: stream slug, so it reads those two numbers off the live registration at call time
#: (`forward.py::_registered_lane`) rather than duplicating them as a second, driftable copy --
#: unlike `climate`/`soil`, which had no prior registration to read them from.
VEGETATION_DIRECT_KIND: Final = "observed"

#: The day the ten `postgres-*` executor lanes -- `postgres-vegetation` among them -- were stopped
#: (`conductor/tracks/environmental_postgres_retirement_20260904/spec.md`, "Owner decisions
#: 2026-09-04"). This direct writer never republishes a day at or before the stop: the tripwire "A
#: source handoff pauses and proves the old owner inactive before activating the new owner" reads as
#: "the old owner's last possible write, plus one" here, since the stop's exact last-committed day
#: was not independently measured for this lane.
#:
#: OPERATOR-VERIFY BEFORE ACTIVATION: `parity.py` reports the newest day Postgres's governed plane
#: actually holds a row for. If that day is LATER than this constant, raise this constant to match it
#: before the two writers' domains are treated as non-overlapping; if it is earlier, this constant is
#: already conservative and needs no change.
VEGETATION_DIRECT_WRITER_START_DAY: Final = date(2026, 9, 5)


@dataclass(frozen=True, slots=True)
class VegetationProduct:
    """The one object stream this writer publishes and the clock it keys to."""

    stream: str = VEGETATION_PLANE_STREAM
    metric_name: str = VEGETATION_METRIC_NAME
    metric_unit: str = VEGETATION_METRIC_UNIT

    @property
    def stream_schema(self) -> ParquetStreamSchema:
        """Return the registered base-rung contract this writer's rows must conform to."""
        return get_stream_schema(self.stream)


VEGETATION_PRODUCT: Final = VegetationProduct()

__all__ = [
    "VEGETATION_DIRECT_KIND",
    "VEGETATION_DIRECT_WRITER_START_DAY",
    "VEGETATION_METRIC_NAME",
    "VEGETATION_METRIC_UNIT",
    "VEGETATION_PRODUCT",
    "VEGETATION_SOURCE_KEY",
    "VEGETATION_TRANSFORM_VERSION",
    "VegetationProduct",
]
