"""The S18 registration mirror: one literal data row per Parquet stream a config lane writes.

Layer L2 leaf: stdlib plus `foundation` types only, and it must never import `lane_registry.py`,
which imports it. `lane_registry.py::registration_from_config_stream_row` turns each row into a
refusing `LaneRegistration` (the runner writes; the generic exporter refuses and names the lane's
strategy module) and splices it into `LANE_REGISTRATIONS`, `LANE_REGISTRY` and
`CALENDAR_HISTORY_FLOOR`.

Rows are literals, never synthesised from `lanes/*.toml` at import: `LANE_REGISTRY` is read at
import by `execution/lane_specs.py`, `parquet_ops/authorized_serving.py::_LANES` and the calendar
floor, and the lane loader must stay lazy (spec S18, review N2). The TOML stays the one authored
fact because `tests/parquet/test_config_stream_registrations.py` holds every row equal to its lane
TOML's `[[streams]]` entry through `lane_registry.py::config_stream_mirror_violations`.

Appending a row is the whole registration step for a new stream: `w3-water-gauges` appended
`water-gauges-daily`; `p4-contract-freeze` wrote the 24 climate streams as `CLIMATE_STREAM_ROWS`.

The climate rows are STAGED, not yet spliced into `CONFIG_STREAM_ROWS`: a spliced row must mirror a
loaded lane TOML and its slug needs a schema module and a tier derivation, all of which are
`s-climate`'s. See `pipeline/parquet/AGENTS.md` "S18 climate rows" for the splice rule and A19.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from agri_data_service.foundation.parquet.lane_contract import LaneNature


@dataclass(frozen=True, slots=True)
class ConfigStreamRow:
    """One config-lane stream's registration facts, copied from `lanes/<lane_id>.toml`.

    `history_floor` is the `[[streams]]` entry's own floor, or the lane's `[days] floor` when the
    entry declares none; `publication_lag_days` is the lane's `[days]` lag; `strategy` is the lane's
    S14 strategy key, carried so the refusal can name the module without reading the TOML.
    """

    slug: str
    lane_id: str
    strategy: str
    nature: LaneNature
    history_floor: date
    publication_lag_days: int
    floor_basis: str
    complete_history_floor: date | None = None


#: `water-gauges-daily`'s floor: `lane_registry.py::_MEASURED_COMPLETE_HISTORY_FLOORS["water-gauges"]` (spec §7a).
_WATER_GAUGES_DAILY_FLOOR: Final = date(1990, 9, 30)

#: The shipped mirror, one row per `[[streams]]` entry of a config lane TOML.
CONFIG_STREAM_ROWS: Final[tuple[ConfigStreamRow, ...]] = (
    # lanes/water-gauges-daily.toml (w3-water-gauges). Its 1990 floor sets CALENDAR_HISTORY_FLOOR (A19) until
    # CLIMATE_STREAM_ROWS splices in; the ERA5 1984-01-01 floor then sets it.
    ConfigStreamRow(
        slug="water-gauges-daily",
        lane_id="water-gauges-daily",
        strategy="water_gauges.usgs_water_data",
        nature="daily_series",
        history_floor=_WATER_GAUGES_DAILY_FLOOR,
        publication_lag_days=2,
        floor_basis=(
            "lane_registry.py::_MEASURED_COMPLETE_HISTORY_FLOORS['water-gauges'] (spec 7a, N5); "
            "P4 2026-09-28: all 10 probed gauges serve 1990-09-30"
        ),
        complete_history_floor=_WATER_GAUGES_DAILY_FLOOR,
    ),
)


# --- The 24 climate streams (spec §6.1; p4-contract-freeze) --------------------------------------
#
# Staged (module docstring). Lane ids are spec §6.1's; the slugs are its stream list (7 ERA5 + 7 IFS +
# `shortwave-ifs` + `shortwave-power` + 7 meteorology + `shortwave-radiation`). Each `[[streams]]` entry
# `s-climate` writes must equal its row here field for field, which the mirror test enforces on splice.

#: The seven meteorology variables as stream suffixes: Open-Meteo daily `temperature_2m_mean/max/min`,
#: `dew_point_2m_mean`, `precipitation_sum`, `relative_humidity_2m_mean`, `wind_speed_10m_mean`
#: (spec §6.1; wind is 10 m, S1).
METEOROLOGY_VARIABLES: Final = (
    "air-temperature-mean",
    "air-temperature-max",
    "air-temperature-min",
    "dew-point",
    "precipitation",
    "relative-humidity",
    "wind-speed-10m",
)

ERA5_SETTLED_LANE: Final = "meteorology-era5-settled"
IFS_PROVISIONAL_LANE: Final = "meteorology-ifs-provisional"
POWER_SHORTWAVE_SETTLED_LANE: Final = "shortwave-nasa-power-settled"
METEOROLOGY_TRANSFORM_LANE: Final = "meteorology"
SHORTWAVE_TRANSFORM_LANE: Final = "shortwave"

#: O3: all five meteorology layers are re-pulled from ERA5 back to this day.
_ERA5_FLOOR: Final = date(1984, 1, 1)
#: Spec §6.3: the provisional bootstrap through the historical-forecast endpoint starts here.
_IFS_FLOOR: Final = date(2026, 7, 1)
#: Spec §6.1 and O7: the mandatory POWER solar re-grid's gap-fill starts here.
_POWER_SHORTWAVE_FLOOR: Final = date(2022, 4, 30)

#: Spec §6.1: the ERA5 forward edge is the probed edge, about today - 6; this is the S6 lag fallback.
_ERA5_LAG_DAYS: Final = 6
#: O1: the provisional window ends yesterday UTC, so yesterday is the newest day a provisional or
#: transform stream owes.
_PROVISIONAL_LAG_DAYS: Final = 1
#: POWER's measured UTC solar lag, `warehouse/field_products/climate.py::
#: CLIMATE_SHORTWAVE_RADIATION_PUBLICATION_LAG_DAYS` (a literal: this leaf imports `foundation` only).
_POWER_SHORTWAVE_LAG_DAYS: Final = 6

_ERA5_BASIS: Final = (
    "spec 6.1 and O3: Open-Meteo archive models=era5 at the S10 NE-corner nodes, gap-filled 1984-01-01 -> edge "
    "for all five meteorology layers; lag 6 is the spec's probed edge (about today - 6), the S6 fallback"
)
_IFS_BASIS: Final = (
    "spec 6.1 and 6.3: ECMWF IFS at the lattice centroids, bootstrapped through the historical-forecast "
    "endpoint from 2026-07-01; lag 1 because the provisional window ends yesterday UTC (O1)"
)
_POWER_BASIS: Final = (
    "spec 6.1 and O7: NASA POWER ALLSKY_SFC_SW_DWN, time-standard=UTC, re-gridded from 2022-04-30; lag 6 is "
    "POWER's measured UTC solar lag (warehouse/field_products/climate.py)"
)
_METEOROLOGY_BASIS: Final = (
    "spec 6.1 and S7: settled ERA5 over provisional IFS; the floor is the deepest input's (ERA5 1984-01-01, O3) "
    "and the lag the provisional input's (1, O1)"
)
_SHORTWAVE_BASIS: Final = (
    "spec 6.1 and S7: settled POWER over provisional IFS; the floor is the deepest input's (POWER 2022-04-30, "
    "O7) and the lag the provisional input's (1, O1)"
)


def _climate_row(  # noqa: PLR0913 - one argument per frozen contract fact
    slug: str, lane_id: str, strategy: str, floor: date, lag_days: int, floor_basis: str
) -> ConfigStreamRow:
    return ConfigStreamRow(
        slug=slug,
        lane_id=lane_id,
        strategy=strategy,
        nature="daily_series",
        history_floor=floor,
        publication_lag_days=lag_days,
        floor_basis=floor_basis,
    )


#: The frozen climate stream contract: one row per `[[streams]]` entry of the five climate lane TOMLs.
CLIMATE_STREAM_ROWS: Final[tuple[ConfigStreamRow, ...]] = (
    *(
        _climate_row(
            f"meteorology-era5-{variable}",
            ERA5_SETTLED_LANE,
            "meteorology.open_meteo_era5",
            _ERA5_FLOOR,
            _ERA5_LAG_DAYS,
            _ERA5_BASIS,
        )
        for variable in METEOROLOGY_VARIABLES
    ),
    # One IFS request carries the seven variables plus `shortwave_radiation_sum` (spec §6.1): two families.
    *(
        _climate_row(
            f"meteorology-ifs-{variable}",
            IFS_PROVISIONAL_LANE,
            "meteorology.open_meteo_ifs",
            _IFS_FLOOR,
            _PROVISIONAL_LAG_DAYS,
            _IFS_BASIS,
        )
        for variable in METEOROLOGY_VARIABLES
    ),
    _climate_row(
        "shortwave-ifs",
        IFS_PROVISIONAL_LANE,
        "meteorology.open_meteo_ifs",
        _IFS_FLOOR,
        _PROVISIONAL_LAG_DAYS,
        _IFS_BASIS,
    ),
    _climate_row(
        "shortwave-power",
        POWER_SHORTWAVE_SETTLED_LANE,
        "shortwave.nasa_power",
        _POWER_SHORTWAVE_FLOOR,
        _POWER_SHORTWAVE_LAG_DAYS,
        _POWER_BASIS,
    ),
    *(
        _climate_row(
            f"meteorology-{variable}",
            METEOROLOGY_TRANSFORM_LANE,
            "transforms.precedence",
            _ERA5_FLOOR,
            _PROVISIONAL_LAG_DAYS,
            _METEOROLOGY_BASIS,
        )
        for variable in METEOROLOGY_VARIABLES
    ),
    _climate_row(
        "shortwave-radiation",
        SHORTWAVE_TRANSFORM_LANE,
        "transforms.precedence",
        _POWER_SHORTWAVE_FLOOR,
        _PROVISIONAL_LAG_DAYS,
        _SHORTWAVE_BASIS,
    ),
)


__all__ = [
    "CLIMATE_STREAM_ROWS",
    "CONFIG_STREAM_ROWS",
    "ERA5_SETTLED_LANE",
    "IFS_PROVISIONAL_LANE",
    "METEOROLOGY_TRANSFORM_LANE",
    "METEOROLOGY_VARIABLES",
    "POWER_SHORTWAVE_SETTLED_LANE",
    "SHORTWAVE_TRANSFORM_LANE",
    "ConfigStreamRow",
]
