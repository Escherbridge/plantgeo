"""Author the soil-moisture plan pairs: a NASA sampling lattice and its ERA5-Land replay.

The ERA5 contract stores `nasa_lattice_plan_checksum`, and nothing in the codebase recomputes
it, so it is authored here rather than typed by hand.  Running this module is the only
sanctioned way to produce the artifacts, which keeps the checksum binding derivable
instead of asserted.  See services/agri-data-service/plans/AGENTS.md.

Two lattices are authored, both strict subsets of the same canonical North America lattice:

- the original four-cell Pacific Northwest probe, frozen because it has already been run, and
- the Western North America lattice, which widens the sampled extent well beyond the PNW.

Usage (from services/agri-data-service):
    ./.venv/Scripts/python.exe plans/author_pnw_soil_moisture_plans.py
"""

from __future__ import annotations

import calendar
import json
import sys
from dataclasses import dataclass
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
from agri_data_service.execution.historical_open_meteo import (  # noqa: E402
    OPEN_METEO_ARCHIVE_SOIL_MOISTURE_PARAMETERS,
    HistoricalOpenMeteoArchivePlan,
    historical_open_meteo_plan_checksum,
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

# The widened Western North America envelope. CDS prices a retrieval as
# 2 x variables x days and ignores `area` and `grid` entirely (measured against the costing
# endpoint: this envelope and a whole-globe envelope both cost 124 of the 400 limit for a
# 2-variable 31-day request). Widening the sampled extent therefore costs no extra retrievals
# and no extra queue time -- only warehouse rows. See plans/AGENTS.md.
WESTERN_NORTH_AMERICA_NORTH = 51.0
WESTERN_NORTH_AMERICA_WEST = -125.0
WESTERN_NORTH_AMERICA_SOUTH = 31.0
WESTERN_NORTH_AMERICA_EAST = -104.0

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

# Canonical lattice cells that ERA5-Land does not model. The canonical lattice is land-masked
# with Natural Earth, which is not ERA5-Land's land-sea mask, so a few coastal cells exist in
# one and not the other. Measured 2026-08-05 with two single-day CDS probes (2022-01-15 and
# 2022-07-15) over N56/W-126/S25/E-94: both probes returned an identical set of 868 finite grid
# points for both variables, confirming the mask is static rather than seasonal. Requesting
# these cells would write only `is_observed=false` rows -- they are outside the product's
# domain, not missing from it, so they are excluded rather than recorded as gaps.
CELLS_OUTSIDE_ERA5_LAND_DOMAIN = frozenset({"na-sample:1deg:p047.00:m124.00"})

SOIL_MOISTURE_PARAMETERS = ("soil_temperature_level_1", "volumetric_soil_water_layer_1")

NASA_RELEASE_SET_KEY = "nasa-power-pnw-soil-lattice-20220430-20260430"
ERA5_RELEASE_SET_KEY = "era5-land-pnw-soil-20220430-20260430"

# The widened lattice is a different release set, not a revision of the four-cell one. Both keys
# stay resolvable so the partially-fetched PNW probe can never be confused with this run.
WESTERN_NASA_RELEASE_SET_KEY = "nasa-power-western-na-soil-lattice-20220430-20260430"
WESTERN_ERA5_RELEASE_SET_KEY = "era5-land-western-na-soil-20220430-20260430"

# The NASA acquisition as-of time is frozen: this plan has already been run, its checksum is
# recorded in the ERA5 plan below, and its spatial cells are persisted. Changing it would
# orphan that binding. Receipts landed after it, so the acquisition release set is closed by
# the finalization artifact rather than by editing this value.
NASA_ACQUISITION_RELEASE_SET_AS_OF = "2026-08-04T23:59:59Z"
NASA_FINALIZATION_RELEASE_SET_KEY = "nasa-power-pnw-soil-lattice-20220430-20260430-asof-20260805"
NASA_FINALIZATION_RELEASE_SET_AS_OF = "2026-08-05T23:59:59Z"
ERA5_RELEASE_SET_AS_OF = "2026-08-05T23:59:59Z"

# The widened replay is 49 sequential CDS retrievals whose measured wall clock is dominated by
# server-side queue wait (~1 h for a full month), so receipts land over roughly two days. This
# as-of time is set past that horizon so `finalize_era5_release_set` can close the set without a
# second finalization artifact. It is later than every `data_available_at` in the window, which
# is the safe direction: an as-of *before* a receipt blocks publication, it never leaks.
WESTERN_RELEASE_SET_AS_OF = "2026-08-12T23:59:59Z"

# --- Open-Meteo ERA5-Land archive over the Sentinel-2 NDVI lattice ---------------------------
# This lane exists because the CDS contract structurally cannot reach these cells:
# `require_governed_monthly_coverage` rejects any ERA5 cell whose centroid is not on the 1.0-degree
# output grid, and every NDVI cell sits on `.125`/`.375`. The keyless Open-Meteo archive serves the
# same ERA5-Land product at its native 0.1 degrees for arbitrary points, so it can.
#
# The grid is FIXED and is regenerated arithmetically here rather than read from the warehouse, so
# a clone reproduces the plan byte for byte. It must match `ingest/vegetation.py`'s global
# 0.25-degree grid exactly: centroids at cell south/west plus half a cell, rendered with the same
# four-decimal JavaScript-compatible formatting, prefixed with the grid name.
NDVI_GRID_NAME = "sentinel2-ndvi-0p25deg"
NDVI_CELL_SPACING_DEGREES = 0.25
NDVI_CELL_RESOLUTION_METRES = 27_830
NDVI_LATTICE_SOUTH = 42.0
NDVI_LATTICE_NORTH = 49.0
NDVI_LATTICE_WEST = -125.0
NDVI_LATTICE_EAST = -111.0

# Locations per archive request. Measured: 50 cells x 4 years x 4 variables answered in 13.7 s for
# 2.6 MB. Larger chunks do not reduce the provider's quota cost, which scales with
# cells x variables x days, but they do reduce request count and per-request overhead.
OPEN_METEO_CHUNK_CELL_COUNT = 50

# The probe is the 4x4 block of lattice cells around Boise, chosen so the end-to-end path -- fetch,
# cache, persist, finalize -- can be completed and verified inside one session while the full
# lattice is still landing.
OPEN_METEO_PROBE_SOUTH = 43.125
OPEN_METEO_PROBE_NORTH = 43.875
OPEN_METEO_PROBE_WEST = -116.875
OPEN_METEO_PROBE_EAST = -116.125
OPEN_METEO_PROBE_CHUNK_CELL_COUNT = 8

OPEN_METEO_PROBE_RELEASE_SET_KEY = "open-meteo-era5-land-boise-ndvi-probe-20220430-20260430"
OPEN_METEO_LATTICE_RELEASE_SET_KEY = "open-meteo-era5-land-pnw-ndvi-lattice-20220430-20260430"

# An as-of time is the receipt-acquisition boundary, not a data-freshness claim: the observed window
# is fixed by `window`, and `finalize_open_meteo_release_set` refuses a set whose as-of precedes any
# persisted receipt. Setting it before the last receipt lands is therefore unrecoverable without
# re-authoring the plan, which changes the plan checksum and orphans the checkpoint and raw cache.
#
# The probe is 2 chunks, already fetched and finalized under this value; it is frozen.
OPEN_METEO_PROBE_RELEASE_SET_AS_OF = "2026-08-06T23:59:59Z"
# The lattice is 32 chunks against a keyless daily quota that admits roughly one chunk a day, and it
# resumes across quota walls and interruptions, so its completion date is not predictable to a week.
# This value is far past any plausible completion rather than a forecast of one.
OPEN_METEO_LATTICE_RELEASE_SET_AS_OF = "2027-12-31T23:59:59Z"

OPEN_METEO_SOURCE_DEFINITION: dict[str, object] = {
    "key": "open-meteo-era5-land-archive",
    "name": "Open-Meteo ERA5-Land archive (redistributed ECMWF reanalysis)",
    "owner": "Open-Meteo",
    "purpose": (
        "Native 0.1-degree ERA5-Land soil-state covariates for the Sentinel-2 NDVI analysis "
        "lattice, which the 1.0-degree CDS output-grid contract cannot address."
    ),
    "base_url": "https://archive-api.open-meteo.com/v1/archive",
    "license_name": "CC-BY 4.0 (Open-Meteo) over Copernicus/ECMWF ERA5-Land",
    "license_url": "https://open-meteo.com/en/license",
    "citation": (
        "Zippenfenig, P. (2023). Open-Meteo.com Weather API. Generated using Copernicus Climate "
        "Change Service information: Munoz Sabater, J. (2019): ERA5-Land hourly data from 1950 to "
        "present, Copernicus Climate Change Service (C3S) Climate Data Store (CDS). Open-Meteo is "
        "an INTERMEDIARY redistributor: these values were not retrieved from ECMWF or the CDS, so "
        "this provenance is weaker than a first-party CDS receipt and must not be presented as one."
    ),
    "retention_days": None,
    "reviewed_at": "2026-08-05T00:00:00Z",
    "reviewed_by": "local-data-operator",
}

OPEN_METEO_PROBE_PLAN_PATH = SERVICE_ROOT / "plans" / f"{OPEN_METEO_PROBE_RELEASE_SET_KEY}.json"
OPEN_METEO_LATTICE_PLAN_PATH = SERVICE_ROOT / "plans" / f"{OPEN_METEO_LATTICE_RELEASE_SET_KEY}.json"

NASA_PLAN_PATH = SERVICE_ROOT / "plans" / f"{NASA_RELEASE_SET_KEY}.json"
NASA_FINALIZATION_PATH = SERVICE_ROOT / "plans" / f"{NASA_FINALIZATION_RELEASE_SET_KEY}-finalization.json"
NASA_RELEASE_PLAN_PATH = SERVICE_ROOT / "plans" / f"{NASA_FINALIZATION_RELEASE_SET_KEY}.json"
ERA5_PLAN_PATH = SERVICE_ROOT / "plans" / f"{ERA5_RELEASE_SET_KEY}.json"
WESTERN_NASA_PLAN_PATH = SERVICE_ROOT / "plans" / f"{WESTERN_NASA_RELEASE_SET_KEY}.json"
WESTERN_ERA5_PLAN_PATH = SERVICE_ROOT / "plans" / f"{WESTERN_ERA5_RELEASE_SET_KEY}.json"


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


@dataclass(frozen=True)
class ReleaseIdentity:
    """The three fields that distinguish one governed release set from another."""

    key: str
    as_of: str
    description: str


PNW_NASA_RELEASE_IDENTITY = ReleaseIdentity(
    key=NASA_RELEASE_SET_KEY,
    as_of=NASA_ACQUISITION_RELEASE_SET_AS_OF,
    description=(
        "Pacific Northwest soil-moisture sampling lattice: the southwest-Idaho corner of the "
        "reviewed North America 1-degree lattice. This plan exists to establish the analysis "
        "spatial cells that ERA5-Land persistence requires, using the canonical cell geometry."
    ),
)

PNW_ERA5_RELEASE_IDENTITY = ReleaseIdentity(
    key=ERA5_RELEASE_SET_KEY,
    as_of=ERA5_RELEASE_SET_AS_OF,
    description=(
        "Pacific Northwest ERA5-Land soil replay over the -125,42,-111,49 coverage envelope. "
        "Carries soil_temperature_level_1 and volumetric_soil_water_layer_1 for the four "
        "southwest-Idaho lattice cells established by the NASA plan named in "
        "nasa_lattice_plan_checksum."
    ),
)


def _cell_keys_within(north: float, west: float, south: float, east: float) -> tuple[str, ...]:
    """Return every canonical lattice cell inside an inclusive envelope that ERA5-Land models.

    The envelope is the CDS `area`, so a cell outside it would be absent from the downloaded
    NetCDF and fail the reviewed nearest-point tolerance at parse time.
    """
    canonical_cells = _canonical_lattice_cells()
    selected = [
        key
        for key, cell in canonical_cells.items()
        if south <= float(cell["latitude"]) <= north  # type: ignore[arg-type]
        and west <= float(cell["longitude"]) <= east  # type: ignore[arg-type]
        and key not in CELLS_OUTSIDE_ERA5_LAND_DOMAIN
    ]
    if not selected:
        raise SystemExit(f"no canonical lattice cells inside N{north} W{west} S{south} E{east}")
    return tuple(sorted(selected))


def build_nasa_lattice_plan(
    *,
    cell_keys: tuple[str, ...] = PROBE_CELL_KEYS,
    release: ReleaseIdentity = PNW_NASA_RELEASE_IDENTITY,
) -> HistoricalNasaBackfillPlan:
    """Author a sampling lattice as a strict subset of the reviewed North America lattice.

    Reusing the canonical cell_key, grid name, resolution and half-span means the spatial cells
    this plan persists are byte-identical to the ones the full lattice would create, so a later
    full-coverage run stays idempotent instead of colliding on geometry.
    """
    canonical_cells = _canonical_lattice_cells()
    missing = [key for key in cell_keys if key not in canonical_cells]
    if missing:
        raise SystemExit(f"lattice cells are not part of the reviewed North America lattice: {missing}")
    cells = [canonical_cells[key] for key in sorted(cell_keys)]
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
            "release_set_key": release.key,
            "release_set_as_of": release.as_of,
            "description": release.description,
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


def build_era5_plan(
    nasa_lattice_plan_checksum: str,
    *,
    cell_keys: tuple[str, ...] = PROBE_CELL_KEYS,
    area: tuple[float, float, float, float] = (
        PACIFIC_NORTHWEST_NORTH,
        PACIFIC_NORTHWEST_WEST,
        PACIFIC_NORTHWEST_SOUTH,
        PACIFIC_NORTHWEST_EAST,
    ),
    release: ReleaseIdentity = PNW_ERA5_RELEASE_IDENTITY,
) -> HistoricalEra5LandBackfillPlan:
    """Author the ERA5-Land soil replay bound to the lattice checksum that actually established its cells."""
    canonical_cells = _canonical_lattice_cells()
    cells = [canonical_cells[key] for key in sorted(cell_keys)]
    north, west, south, east = area
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
            "requested_area": {"north": north, "west": west, "south": south, "east": east},
            "native_grid_resolution_m": 9000,
            "cells": cells,
            "parameters": sorted(SOIL_MOISTURE_PARAMETERS),
            "periods": _monthly_periods(WINDOW_START_DATE, WINDOW_END_DATE),
            "nasa_lattice_plan_checksum": nasa_lattice_plan_checksum,
            "terms_acceptance_required": True,
            "transform_version": "era5-land-daily-requested-grid-normalization-v1",
            "release_set_key": release.key,
            "release_set_as_of": release.as_of,
            "description": release.description,
        }
    )


def build_western_north_america_plans() -> tuple[HistoricalNasaBackfillPlan, HistoricalEra5LandBackfillPlan]:
    """Author the widened Western North America lattice pair and its derived checksum binding.

    The envelope reaches from the Canadian prairie border down to northern Mexico and east to the
    Colorado front range, so it covers the Columbia Basin, Snake River Plain, Central Valley,
    Great Basin, Imperial Valley and the southern Rockies rather than one corner of Idaho.
    """
    cell_keys = _cell_keys_within(
        WESTERN_NORTH_AMERICA_NORTH,
        WESTERN_NORTH_AMERICA_WEST,
        WESTERN_NORTH_AMERICA_SOUTH,
        WESTERN_NORTH_AMERICA_EAST,
    )
    nasa_plan = build_nasa_lattice_plan(
        cell_keys=cell_keys,
        release=ReleaseIdentity(
            key=WESTERN_NASA_RELEASE_SET_KEY,
            as_of=WESTERN_RELEASE_SET_AS_OF,
            description=(
                f"Western North America soil-moisture sampling lattice: the {len(cell_keys)} cells of the "
                "reviewed North America 1-degree lattice inside N51/W-125/S31/E-104 that ERA5-Land models. "
                "This plan exists to establish the analysis spatial cells that ERA5-Land persistence "
                "requires, using the canonical cell geometry. It is a superset of the four-cell Pacific "
                "Northwest probe lattice and reuses its cell keys verbatim, so both remain idempotent."
            ),
        ),
    )
    era5_plan = build_era5_plan(
        historical_nasa_plan_checksum(nasa_plan),
        cell_keys=cell_keys,
        area=(
            WESTERN_NORTH_AMERICA_NORTH,
            WESTERN_NORTH_AMERICA_WEST,
            WESTERN_NORTH_AMERICA_SOUTH,
            WESTERN_NORTH_AMERICA_EAST,
        ),
        release=ReleaseIdentity(
            key=WESTERN_ERA5_RELEASE_SET_KEY,
            as_of=WESTERN_RELEASE_SET_AS_OF,
            description=(
                f"Western North America ERA5-Land soil replay over the -125,31,-104,51 coverage envelope, "
                f"sampling {len(cell_keys)} canonical lattice cells. Carries soil_temperature_level_1 and "
                "volumetric_soil_water_layer_1, whose normalized signal soil_water_content_layer_1 "
                "(m^3/m^3) is a different physical quantity from NASA POWER's soil_wetness_* "
                "(fraction_of_saturation) and must never be blended with it. CDS prices a retrieval as "
                "2 x variables x days and ignores area, so this envelope costs the same 49 monthly "
                "retrievals at 124 of the 400 per-request limit as the four-cell probe it widens. "
                "One canonical cell inside the envelope, na-sample:1deg:p047.00:m124.00, is excluded "
                "because it lies outside ERA5-Land's land-sea mask."
            ),
        ),
    )
    return nasa_plan, era5_plan


def _format_ndvi_coordinate(value: float) -> str:
    """Render a lattice coordinate exactly as `ingest/vegetation.py` renders it into a cell key."""
    rendered = f"{value:.4f}"
    return "0.0000" if rendered == "-0.0000" else rendered


def ndvi_lattice_cells(
    *,
    south: float = NDVI_LATTICE_SOUTH,
    north: float = NDVI_LATTICE_NORTH,
    west: float = NDVI_LATTICE_WEST,
    east: float = NDVI_LATTICE_EAST,
) -> list[dict[str, object]]:
    """Enumerate the fixed 0.25-degree NDVI grid cells whose centroids fall inside an envelope."""
    spacing = NDVI_CELL_SPACING_DEGREES
    cells: list[dict[str, object]] = []
    for row in range(round(south / spacing), round(north / spacing) + 1):
        latitude = round(row * spacing + spacing / 2, 10)
        if not south <= latitude <= north:
            continue
        for column in range(round(west / spacing), round(east / spacing) + 1):
            longitude = round(column * spacing + spacing / 2, 10)
            if not west <= longitude <= east:
                continue
            cells.append(
                {
                    "cell_key": (
                        f"{NDVI_GRID_NAME}:{_format_ndvi_coordinate(latitude)}:{_format_ndvi_coordinate(longitude)}"
                    ),
                    "latitude": latitude,
                    "longitude": longitude,
                }
            )
    cells.sort(key=lambda cell: str(cell["cell_key"]))
    if not cells:
        raise SystemExit(f"no NDVI lattice cells inside N{north} W{west} S{south} E{east}")
    return cells


def build_open_meteo_archive_plan(
    *,
    cells: list[dict[str, object]],
    chunk_cell_count: int,
    release: ReleaseIdentity,
) -> HistoricalOpenMeteoArchivePlan:
    """Author an Open-Meteo ERA5-Land archive replay over already-established analysis cells.

    The plan mints no spatial cells: persistence resolves every `cell_key` against the lattice the
    Sentinel-2 NDVI backfill already wrote, and fails closed if one is absent.
    """
    return HistoricalOpenMeteoArchivePlan.model_validate(
        {
            "source": OPEN_METEO_SOURCE_DEFINITION,
            "window": {
                "start_date": WINDOW_START_DATE.isoformat(),
                "end_date": WINDOW_END_DATE.isoformat(),
            },
            "grid_name": NDVI_GRID_NAME,
            "grid_resolution_m": NDVI_CELL_RESOLUTION_METRES,
            "native_grid_degrees": 0.1,
            "native_grid_resolution_m": 9000,
            "cells": cells,
            "chunk_cell_count": chunk_cell_count,
            "parameters": sorted(OPEN_METEO_ARCHIVE_SOIL_MOISTURE_PARAMETERS),
            "transform_version": "open-meteo-era5-land-archive-daily-mean-normalization-v1",
            "release_set_key": release.key,
            "release_set_as_of": release.as_of,
            "description": release.description,
        }
    )


def build_open_meteo_ndvi_plans() -> tuple[HistoricalOpenMeteoArchivePlan, HistoricalOpenMeteoArchivePlan]:
    """Author the Boise probe and the full Pacific Northwest NDVI-lattice archive replays."""
    probe_cells = ndvi_lattice_cells(
        south=OPEN_METEO_PROBE_SOUTH,
        north=OPEN_METEO_PROBE_NORTH,
        west=OPEN_METEO_PROBE_WEST,
        east=OPEN_METEO_PROBE_EAST,
    )
    lattice_cells = ndvi_lattice_cells()
    probe_plan = build_open_meteo_archive_plan(
        cells=probe_cells,
        chunk_cell_count=OPEN_METEO_PROBE_CHUNK_CELL_COUNT,
        release=ReleaseIdentity(
            key=OPEN_METEO_PROBE_RELEASE_SET_KEY,
            as_of=OPEN_METEO_PROBE_RELEASE_SET_AS_OF,
            description=(
                f"Boise-area probe of the Open-Meteo ERA5-Land archive lane: the {len(probe_cells)} "
                "Sentinel-2 NDVI lattice cells around Boise, carrying the three ERA5-Land volumetric "
                "soil-water layers at their native 0.1-degree support. It exists so the whole path -- "
                "fetch, canonical cache, warehouse persistence, release-set finalization -- can be "
                "completed and verified while the full lattice is still quota-bound. Its cells are a "
                "strict subset of the lattice plan's and reuse the same cell keys, so both stay "
                "idempotent. soil_water_content_layer_* (m^3/m^3) is a volumetric water content and "
                "is NOT NASA POWER's soil_wetness_* (fraction_of_saturation); the two must never "
                "share a scale or legend."
            ),
        ),
    )
    lattice_plan = build_open_meteo_archive_plan(
        cells=lattice_cells,
        chunk_cell_count=OPEN_METEO_CHUNK_CELL_COUNT,
        release=ReleaseIdentity(
            key=OPEN_METEO_LATTICE_RELEASE_SET_KEY,
            as_of=OPEN_METEO_LATTICE_RELEASE_SET_AS_OF,
            description=(
                f"The complete {len(lattice_cells)}-cell Sentinel-2 NDVI analysis lattice "
                "(-125,42,-111,49) covered with ERA5-Land volumetric soil water at native 0.1-degree "
                "support, read through the keyless Open-Meteo archive. These are the cells the ML "
                "covariate layer returns all-NULL for: they exist in agri.spatial_cell with zero "
                "signal rows, and the CDS contract structurally cannot address them because "
                "require_governed_monthly_coverage rejects any cell off the 1.0-degree output grid. "
                "Ocean and out-of-domain cells come back null, never zero, and are recorded as "
                "no_data coverage rather than fabricated values."
            ),
        ),
    )
    return probe_plan, lattice_plan


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

    western_nasa_plan, western_era5_plan = build_western_north_america_plans()
    western_nasa_checksum = historical_nasa_plan_checksum(western_nasa_plan)
    western_era5_checksum = historical_era5_plan_checksum(western_era5_plan)
    WESTERN_NASA_PLAN_PATH.write_bytes(canonical_json_bytes(western_nasa_plan.model_dump(mode="json")))
    WESTERN_ERA5_PLAN_PATH.write_bytes(canonical_json_bytes(western_era5_plan.model_dump(mode="json")))

    open_meteo_probe_plan, open_meteo_lattice_plan = build_open_meteo_ndvi_plans()
    OPEN_METEO_PROBE_PLAN_PATH.write_bytes(canonical_json_bytes(open_meteo_probe_plan.model_dump(mode="json")))
    OPEN_METEO_LATTICE_PLAN_PATH.write_bytes(canonical_json_bytes(open_meteo_lattice_plan.model_dump(mode="json")))

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
                "western_nasa_plan": str(WESTERN_NASA_PLAN_PATH),
                "western_nasa_plan_checksum": western_nasa_checksum,
                "western_nasa_cell_count": len(western_nasa_plan.nasa.cells),
                "western_era5_plan": str(WESTERN_ERA5_PLAN_PATH),
                "western_era5_plan_checksum": western_era5_checksum,
                "western_era5_nasa_lattice_plan_checksum": western_era5_plan.nasa_lattice_plan_checksum,
                "western_era5_cell_count": len(western_era5_plan.cells),
                "western_era5_requested_area": western_era5_plan.requested_area.model_dump(),
                "western_era5_row_projection": (
                    len(western_era5_plan.cells) * len(western_era5_plan.parameters) * 1462
                ),
                "open_meteo_probe_plan": str(OPEN_METEO_PROBE_PLAN_PATH),
                "open_meteo_probe_plan_checksum": historical_open_meteo_plan_checksum(open_meteo_probe_plan),
                "open_meteo_probe_cell_count": len(open_meteo_probe_plan.cells),
                "open_meteo_probe_chunk_count": len(open_meteo_probe_plan.chunks),
                "open_meteo_lattice_plan": str(OPEN_METEO_LATTICE_PLAN_PATH),
                "open_meteo_lattice_plan_checksum": historical_open_meteo_plan_checksum(open_meteo_lattice_plan),
                "open_meteo_lattice_cell_count": len(open_meteo_lattice_plan.cells),
                "open_meteo_lattice_chunk_count": len(open_meteo_lattice_plan.chunks),
                "open_meteo_parameters": open_meteo_lattice_plan.parameters,
                "open_meteo_lattice_row_projection": (
                    len(open_meteo_lattice_plan.cells)
                    * len(open_meteo_lattice_plan.parameters)
                    * open_meteo_lattice_plan.window.day_count
                ),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
