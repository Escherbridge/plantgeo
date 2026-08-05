"""Author the PNW soil-moisture plan pair: the NASA sampling lattice and the ERA5-Land replay.

The ERA5 contract stores `nasa_lattice_plan_checksum`, and nothing in the codebase recomputes
it, so it is authored here rather than typed by hand.  Running this module is the only
sanctioned way to produce the two artifacts, which keeps the checksum binding derivable
instead of asserted.  See services/agri-data-service/plans/AGENTS.md.

Usage (from services/agri-data-service):
    ./.venv/Scripts/python.exe plans/author_pnw_soil_moisture_plans.py
"""

from __future__ import annotations

import calendar
import json
import sys
from datetime import date, timedelta
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE_ROOT / "src"))

from agri_data_service.execution.contracts import canonical_json_bytes  # noqa: E402
from agri_data_service.execution.historical_backfill import (  # noqa: E402
    HistoricalNasaBackfillPlan,
    historical_nasa_plan_checksum,
)
from agri_data_service.execution.historical_era5 import (  # noqa: E402
    HistoricalEra5LandBackfillPlan,
    historical_era5_plan_checksum,
)

REPOSITORY_ROOT = SERVICE_ROOT.parent.parent
CANONICAL_NORTH_AMERICA_LATTICE_PLAN = (
    REPOSITORY_ROOT
    / "infra"
    / "local-warehouse"
    / "plans"
    / "nasa-power-na-sampling-20220430-20260430-asof-20260721.json"
)

# The reviewed Pacific Northwest coverage envelope, in the CDS north/west/south/east order.
PACIFIC_NORTHWEST_NORTH = 49.0
PACIFIC_NORTHWEST_WEST = -125.0
PACIFIC_NORTHWEST_SOUTH = 42.0
PACIFIC_NORTHWEST_EAST = -111.0

# Four calendar years, matching the already-validated North America lattice window so the
# probe lattice reuses the same governed cell geometry rather than inventing a second grid.
WINDOW_START_DATE = date(2022, 4, 30)
WINDOW_END_DATE = date(2026, 4, 30)

# A deliberately small first run: proving the path end-to-end matters more than coverage.
# These four cells are the southwest-Idaho corner of the canonical lattice, next to the
# existing Boise analysis slice.
PROBE_CELL_KEYS = (
    "na-sample:1deg:p043.00:m116.00",
    "na-sample:1deg:p043.00:m117.00",
    "na-sample:1deg:p044.00:m116.00",
    "na-sample:1deg:p044.00:m117.00",
)

SOIL_MOISTURE_PARAMETERS = ("soil_temperature_level_1", "volumetric_soil_water_layer_1")

NASA_RELEASE_SET_KEY = "nasa-power-pnw-soil-lattice-20220430-20260430"
ERA5_RELEASE_SET_KEY = "era5-land-pnw-soil-20220430-20260430"

# The NASA acquisition as-of time is frozen: this plan has already been run, its checksum is
# recorded in the ERA5 plan below, and its spatial cells are persisted. Changing it would
# orphan that binding. Receipts landed after it, so the acquisition release set is closed by
# the finalization artifact rather than by editing this value.
NASA_ACQUISITION_RELEASE_SET_AS_OF = "2026-08-04T23:59:59Z"
NASA_FINALIZATION_RELEASE_SET_KEY = "nasa-power-pnw-soil-lattice-20220430-20260430-asof-20260805"
NASA_FINALIZATION_RELEASE_SET_AS_OF = "2026-08-05T23:59:59Z"
ERA5_RELEASE_SET_AS_OF = "2026-08-05T23:59:59Z"

NASA_PLAN_PATH = SERVICE_ROOT / "plans" / f"{NASA_RELEASE_SET_KEY}.json"
NASA_FINALIZATION_PATH = SERVICE_ROOT / "plans" / f"{NASA_FINALIZATION_RELEASE_SET_KEY}-finalization.json"
NASA_RELEASE_PLAN_PATH = SERVICE_ROOT / "plans" / f"{NASA_FINALIZATION_RELEASE_SET_KEY}.json"
ERA5_PLAN_PATH = SERVICE_ROOT / "plans" / f"{ERA5_RELEASE_SET_KEY}.json"


def _monthly_periods(start: date, end: date) -> list[dict[str, object]]:
    """Split the reviewed window into calendar-month periods that exactly cover every day."""
    periods: list[dict[str, object]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        period_start = max(start, date(year, month, 1))
        period_end = min(end, date(year, month, calendar.monthrange(year, month)[1]))
        periods.append(
            {
                "key": f"{year:04d}-{month:02d}",
                "start_date": period_start.isoformat(),
                "end_date": period_end.isoformat(),
                "year": f"{year:04d}",
                "month": f"{month:02d}",
                "days": [
                    f"{(period_start + timedelta(days=offset)).day:02d}"
                    for offset in range((period_end - period_start).days + 1)
                ],
            }
        )
        month += 1
        if month > 12:  # noqa: PLR2004
            year, month = year + 1, 1
    return periods


def _canonical_lattice_cells() -> dict[str, dict[str, object]]:
    """Return the already-reviewed North America lattice cells, keyed by their stable cell_key."""
    if not CANONICAL_NORTH_AMERICA_LATTICE_PLAN.exists():
        raise SystemExit(f"canonical NASA lattice plan is missing: {CANONICAL_NORTH_AMERICA_LATTICE_PLAN}")
    document = json.loads(CANONICAL_NORTH_AMERICA_LATTICE_PLAN.read_bytes())
    return {cell["cell_key"]: cell for cell in document["nasa"]["cells"]}


def build_nasa_lattice_plan() -> HistoricalNasaBackfillPlan:
    """Author the PNW sampling lattice as a strict subset of the reviewed North America lattice.

    Reusing the canonical cell_key, grid name, resolution and half-span means the spatial cells
    this plan persists are byte-identical to the ones the full lattice would create, so a later
    full-coverage run stays idempotent instead of colliding on geometry.
    """
    canonical_cells = _canonical_lattice_cells()
    missing = [key for key in PROBE_CELL_KEYS if key not in canonical_cells]
    if missing:
        raise SystemExit(f"probe cells are not part of the reviewed North America lattice: {missing}")
    cells = [canonical_cells[key] for key in sorted(PROBE_CELL_KEYS)]
    return HistoricalNasaBackfillPlan.model_validate(
        {
            "source": {
                "key": "nasa-power-daily",
                "name": "NASA POWER Daily",
                "owner": "NASA POWER",
                "purpose": "North America regional meteorology point-sampling baseline",
                "base_url": "https://power.larc.nasa.gov/api/temporal/daily/point",
                "license_name": "NASA POWER data policy",
                "license_url": "https://power.larc.nasa.gov/docs/services/api/temporal/daily/",
                "citation": "NASA POWER daily point API; Natural Earth Admin 0 Countries 1:50m",
                "retention_days": None,
                "reviewed_at": "2026-07-20T00:00:00Z",
                "reviewed_by": "local-data-operator",
            },
            "nasa": {
                "schema_version": "nasa-power-daily-v1",
                "window": {
                    "start_date": WINDOW_START_DATE.isoformat(),
                    "end_date": WINDOW_END_DATE.isoformat(),
                },
                "cells": cells,
                "parameters": [
                    "ALLSKY_SFC_SW_DWN",
                    "PRECTOTCORR",
                    "RH2M",
                    "T2M",
                    "T2MDEW",
                    "T2M_MAX",
                    "T2M_MIN",
                    "WS2M",
                ],
                "time_standard": "UTC",
                "grid_name": "nasa-power-0.5-degree",
                "grid_resolution_m": 55660,
                "cell_half_span_degrees": 0.25,
            },
            "transform_version": "nasa-power-point-sample-normalization-v2",
            "release_set_key": NASA_RELEASE_SET_KEY,
            "release_set_as_of": NASA_ACQUISITION_RELEASE_SET_AS_OF,
            "description": (
                "Pacific Northwest soil-moisture sampling lattice: the southwest-Idaho corner of the "
                "reviewed North America 1-degree lattice. This plan exists to establish the analysis "
                "spatial cells that ERA5-Land persistence requires, using the canonical cell geometry."
            ),
        }
    )


def build_nasa_finalization(nasa_lattice_plan_checksum: str) -> dict[str, object]:
    """Author the correction that closes the lattice release set after its receipts landed.

    NASA POWER retrievals completed at 2026-08-05T03:1x UTC, past the acquisition as-of time,
    so `finalize_nasa_release_set` correctly refused to publish. This advances only the release
    identity; it never re-requests or rewrites source content.
    """
    return {
        "schema_version": 1,
        "source_plan_checksum": nasa_lattice_plan_checksum,
        "release_set_key": NASA_FINALIZATION_RELEASE_SET_KEY,
        "release_set_as_of": NASA_FINALIZATION_RELEASE_SET_AS_OF,
        "description": (
            "Close the Pacific Northwest soil-moisture sampling lattice release set under a UTC "
            "as-of time that follows its persisted receipts. Source content is unchanged."
        ),
    }


def build_era5_plan(nasa_lattice_plan_checksum: str) -> HistoricalEra5LandBackfillPlan:
    """Author the ERA5-Land soil replay bound to the lattice checksum that actually established its cells."""
    canonical_cells = _canonical_lattice_cells()
    cells = [canonical_cells[key] for key in sorted(PROBE_CELL_KEYS)]
    return HistoricalEra5LandBackfillPlan.model_validate(
        {
            "source": {
                "key": "era5-land",
                "name": "ERA5-Land post-processed daily statistics",
                "owner": "Copernicus Climate Change Service",
                "purpose": "Pacific Northwest retrospective soil-state baseline for agronomic analysis",
                "base_url": "https://cds.climate.copernicus.eu/datasets/derived-era5-land-daily-statistics",
                "license_name": "CC-BY licence",
                "license_url": "https://spdx.org/licenses/CC-BY-4.0",
                "citation": (
                    "Copernicus Climate Change Service (C3S): ERA5-Land post-processed daily statistics "
                    "from 1950 to present, Climate Data Store."
                ),
                "retention_days": None,
                "reviewed_at": "2026-08-04T00:00:00Z",
                "reviewed_by": "local-data-operator",
            },
            "window": {
                "start_date": WINDOW_START_DATE.isoformat(),
                "end_date": WINDOW_END_DATE.isoformat(),
            },
            "daily_statistic": "daily_mean",
            "frequency": "1_hourly",
            "time_zone": "utc+00:00",
            "requested_grid_degrees": 1.0,
            "requested_area": {
                "north": PACIFIC_NORTHWEST_NORTH,
                "west": PACIFIC_NORTHWEST_WEST,
                "south": PACIFIC_NORTHWEST_SOUTH,
                "east": PACIFIC_NORTHWEST_EAST,
            },
            "native_grid_resolution_m": 9000,
            "cells": cells,
            "parameters": sorted(SOIL_MOISTURE_PARAMETERS),
            "periods": _monthly_periods(WINDOW_START_DATE, WINDOW_END_DATE),
            "nasa_lattice_plan_checksum": nasa_lattice_plan_checksum,
            "terms_acceptance_required": True,
            "transform_version": "era5-land-daily-requested-grid-normalization-v1",
            "release_set_key": ERA5_RELEASE_SET_KEY,
            "release_set_as_of": ERA5_RELEASE_SET_AS_OF,
            "description": (
                "Pacific Northwest ERA5-Land soil replay over the -125,42,-111,49 coverage envelope. "
                "Carries soil_temperature_level_1 and volumetric_soil_water_layer_1 for the four "
                "southwest-Idaho lattice cells established by the NASA plan named in "
                "nasa_lattice_plan_checksum."
            ),
        }
    )


def main() -> None:
    nasa_plan = build_nasa_lattice_plan()
    nasa_checksum = historical_nasa_plan_checksum(nasa_plan)
    nasa_payload = canonical_json_bytes(nasa_plan.model_dump(mode="json"))
    if NASA_PLAN_PATH.exists() and NASA_PLAN_PATH.read_bytes() != nasa_payload:
        raise SystemExit(
            "the NASA lattice plan on disk has already been run and persisted; regenerating it "
            "would orphan the ERA5 nasa_lattice_plan_checksum binding"
        )
    NASA_PLAN_PATH.write_bytes(nasa_payload)
    NASA_FINALIZATION_PATH.write_bytes(canonical_json_bytes(build_nasa_finalization(nasa_checksum)))

    era5_plan = build_era5_plan(nasa_checksum)
    era5_checksum = historical_era5_plan_checksum(era5_plan)
    ERA5_PLAN_PATH.write_bytes(canonical_json_bytes(era5_plan.model_dump(mode="json")))

    print(
        json.dumps(
            {
                "nasa_plan": str(NASA_PLAN_PATH),
                "nasa_plan_checksum": nasa_checksum,
                "nasa_finalization": str(NASA_FINALIZATION_PATH),
                "nasa_cell_count": len(nasa_plan.nasa.cells),
                "era5_plan": str(ERA5_PLAN_PATH),
                "era5_plan_checksum": era5_checksum,
                "era5_nasa_lattice_plan_checksum": era5_plan.nasa_lattice_plan_checksum,
                "era5_period_count": len(era5_plan.periods),
                "era5_cell_count": len(era5_plan.cells),
                "era5_parameters": era5_plan.parameters,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
