"""Materialize a checksum-bound North America NASA POWER sampling plan."""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import date
from pathlib import Path

import shapefile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SERVICE_SOURCE = REPOSITORY_ROOT / "services" / "agri-data-service" / "src"
sys.path.insert(0, str(SERVICE_SOURCE))

from agri_data_service.execution.contracts import canonical_json_bytes  # noqa: E402
from agri_data_service.execution.historical_backfill import (  # noqa: E402
    AnalysisGridCell,
    HistoricalBackfillWindow,
    HistoricalNasaBackfillPlan,
    NASA_POWER_DAILY_SCHEMA_VERSION,
    NASA_POWER_GRID_NAME,
    NASA_POWER_GRID_RESOLUTION_M,
    NASA_POWER_SIGNAL_SPECIFICATIONS,
    NasaPowerDailyPlan,
)
from agri_data_service.execution.source_ingestion import SourceDefinition  # noqa: E402


NATURAL_EARTH_BOUNDARY_URL = (
    "https://naturalearth.s3.amazonaws.com/50m_cultural/ne_50m_admin_0_countries.zip"
)
DEFAULT_COUNTRY_CODES = ("CA", "MX", "US")
DEFAULT_SPACING_DEGREES = 1.0
MAX_NASA_PLAN_CELLS = 10_000


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boundary-zip", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--release-set-as-of", required=True)
    parser.add_argument("--reviewed-at", required=True)
    parser.add_argument("--reviewed-by", required=True)
    parser.add_argument(
        "--spacing-degrees", type=float, default=DEFAULT_SPACING_DEGREES
    )
    parser.add_argument(
        "--country-code", action="append", choices=DEFAULT_COUNTRY_CODES
    )
    parser.add_argument("--boundary-url", default=NATURAL_EARTH_BOUNDARY_URL)
    return parser.parse_args()


def _boundary_checksum(path: Path) -> str:
    if not path.is_file():
        raise ValueError("country-boundary ZIP is required")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _shape_crossings(shape: shapefile.Shape, latitude: float) -> list[float]:
    """Return horizontal ray crossings using the even-odd rule for every source ring."""
    crossings: list[float] = []
    points = shape.points
    starts = [*shape.parts, len(points)]
    for start, end in zip(starts, starts[1:]):
        prior_x, prior_y = points[end - 1]
        for current_x, current_y in points[start:end]:
            if (prior_y <= latitude < current_y) or (current_y <= latitude < prior_y):
                crossings.append(
                    prior_x
                    + (latitude - prior_y)
                    * (current_x - prior_x)
                    / (current_y - prior_y)
                )
            prior_x, prior_y = current_x, current_y
    return sorted(crossings)


def _cell_key(latitude: float, longitude: float, spacing_degrees: float) -> str:
    latitude_key = f"{'p' if latitude >= 0 else 'm'}{abs(latitude):06.2f}"
    longitude_key = f"{'p' if longitude >= 0 else 'm'}{abs(longitude):06.2f}"
    return f"na-sample:{spacing_degrees:g}deg:{latitude_key}:{longitude_key}"


def _selected_shapes(path: Path, country_codes: set[str]) -> list[shapefile.Shape]:
    reader = shapefile.Reader(str(path))
    return [
        shape
        for shape, record in zip(reader.shapes(), reader.records(), strict=True)
        if record["ISO_A2_EH"] in country_codes
    ]


def _materialize_cells(
    path: Path, country_codes: set[str], spacing_degrees: float
) -> list[AnalysisGridCell]:
    """Select lattice points inside the reviewed country boundary without loading a geometry engine."""
    shapes = _selected_shapes(path, country_codes)
    if len(shapes) != len(country_codes):
        raise ValueError("country boundary is missing a requested ISO_A2_EH country")
    spacing_ticks = round(spacing_degrees * 4)
    if spacing_ticks <= 0 or spacing_ticks / 4 != spacing_degrees:
        raise ValueError("spacing-degrees must be a positive multiple of 0.25")
    cells: dict[str, AnalysisGridCell] = {}
    for latitude_tick in range(-360, 361, spacing_ticks):
        latitude = latitude_tick / 4
        for shape in shapes:
            west, south, east, north = shape.bbox
            if not south <= latitude <= north:
                continue
            crossings = _shape_crossings(shape, latitude)
            for longitude_tick in range(-720, 720, spacing_ticks):
                longitude = longitude_tick / 4
                if not west <= longitude <= east or not any(
                    left < longitude < right
                    for left, right in zip(crossings[::2], crossings[1::2], strict=True)
                ):
                    continue
                cell = AnalysisGridCell(
                    cell_key=_cell_key(latitude, longitude, spacing_degrees),
                    latitude=latitude,
                    longitude=longitude,
                )
                cells[cell.cell_key] = cell
    selected = [cells[key] for key in sorted(cells)]
    if not selected or len(selected) > MAX_NASA_PLAN_CELLS:
        raise ValueError(
            "materialized country sampling grid is outside the NASA plan cell bound"
        )
    return selected


def main() -> None:
    arguments = _parse_arguments()
    if arguments.output.exists():
        raise ValueError("refusing to overwrite an existing governed plan")
    country_codes = set(arguments.country_code or DEFAULT_COUNTRY_CODES)
    boundary_checksum = _boundary_checksum(arguments.boundary_zip)
    cells = _materialize_cells(
        arguments.boundary_zip, country_codes, arguments.spacing_degrees
    )
    start_date = date(
        arguments.end_date.year - 4, arguments.end_date.month, arguments.end_date.day
    )
    source_scope = ", ".join(sorted(country_codes))
    plan = HistoricalNasaBackfillPlan(
        source=SourceDefinition(
            key="nasa-power-daily",
            name="NASA POWER Daily",
            owner="NASA POWER",
            purpose="North America regional meteorology point-sampling baseline",
            base_url="https://power.larc.nasa.gov/api/temporal/daily/point",
            license_name="NASA POWER data policy",
            license_url="https://power.larc.nasa.gov/docs/services/api/temporal/daily/",
            citation="NASA POWER daily point API; Natural Earth Admin 0 Countries 1:50m",
            reviewed_at=arguments.reviewed_at,
            reviewed_by=arguments.reviewed_by,
        ),
        nasa=NasaPowerDailyPlan(
            schema_version=NASA_POWER_DAILY_SCHEMA_VERSION,
            window=HistoricalBackfillWindow(
                start_date=start_date, end_date=arguments.end_date
            ),
            cells=cells,
            parameters=sorted(NASA_POWER_SIGNAL_SPECIFICATIONS),
            time_standard="UTC",
            grid_name=NASA_POWER_GRID_NAME,
            grid_resolution_m=NASA_POWER_GRID_RESOLUTION_M,
        ),
        transform_version="nasa-power-point-sample-normalization-v2",
        release_set_key=(
            f"nasa-power-na-sampling-{start_date:%Y%m%d}-{arguments.end_date:%Y%m%d}-acquisition"
        ),
        release_set_as_of=arguments.release_set_as_of,
        description=(
            f"Four-year {arguments.spacing_degrees:g}-degree point-sampling baseline for {source_scope}; "
            f"{len(cells)} requested NASA POWER points selected inside Natural Earth Admin 0 Countries 1:50m "
            f"boundary SHA-256 {boundary_checksum} from {arguments.boundary_url}. "
            "Sampling areas support regional coverage accounting only and must not be represented as native pixels, "
            "field, or acre observations."
        ),
    )
    encoded = canonical_json_bytes(plan.model_dump(mode="json"))
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = arguments.output.with_suffix(f"{arguments.output.suffix}.tmp")
    temporary.write_bytes(encoded)
    temporary.replace(arguments.output)
    print(
        canonical_json_bytes(
            {
                "plan": str(arguments.output),
                "plan_checksum": hashlib.sha256(encoded).hexdigest(),
                "country_codes": sorted(country_codes),
                "cell_count": len(cells),
                "spacing_degrees": arguments.spacing_degrees,
                "boundary_checksum": boundary_checksum,
            }
        ).decode()
    )


if __name__ == "__main__":
    main()
