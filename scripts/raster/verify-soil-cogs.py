"""Check the built soil COGs against the SoilGrids point cache the app already trusts.

`public.soil_grid_cache` holds ~1,400 cells fetched one at a time from ISRIC's REST API by
scripts/warm-soilgrids.mjs. Those readings came down a completely different pipe from the bulk
VRTs this raster is clipped from, so sampling the COGs at those coordinates is a genuine
independent check on the clip, the Homolosine reprojection and the unit scaling all at once.

A disagreement here means the raster is describing different ground than the point query -- the
one failure mode that would ship a plausible-looking, wrong soil map.

    uv run --with rasterio --with postgres --no-project python scripts/raster/verify-soil-cogs.py
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

# See build-soil-cogs.py: a global PROJ_LIB from PostgreSQL shadows GDAL's own proj.db.
_rasterio_spec = importlib.util.find_spec("rasterio")
if _rasterio_spec is None or _rasterio_spec.origin is None:
    raise SystemExit("rasterio is not installed; run with `uv run --with rasterio --no-project`")
_bundled_proj = Path(_rasterio_spec.origin).parent / "proj_data"
if _bundled_proj.is_dir():
    os.environ["PROJ_LIB"] = str(_bundled_proj)
    os.environ["PROJ_DATA"] = str(_bundled_proj)

import argparse
import re
import sys

import numpy
import psycopg2
import rasterio

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_COG_DIRECTORY = REPOSITORY_ROOT / "data" / "raster" / "soil"

# The cache column each raster property was fetched into, the disagreement that still counts as
# agreement, and the unit. REST returns the value AT A POINT; a reprojected pixel is an AREA
# AVERAGE, so exact equality is not the expectation. The tolerance is therefore the larger of an
# absolute floor (roughly one quantisation step) and a relative fraction -- soc and nitrogen are
# log-normal and spatially heterogeneous, so a fixed absolute band would reject the raster for
# being correct about a bog sitting 250 m from a ridge.
PROPERTY_COLUMNS = {
    "phh2o": ("ph", 0.2, 0.02, "pH"),
    "soc": ("organic_carbon", 3.0, 0.25, "g/kg"),
    "nitrogen": ("nitrogen", 0.3, 0.25, "g/kg"),
    "bdod": ("bulk_density", 0.05, 0.05, "kg/dm^3"),
    "cec": ("cec", 1.5, 0.15, "cmol(c)/kg"),
    "ocd": ("ocd", 3.0, 0.15, "kg/m^3"),
}

#: A control offset applied to every coordinate for the locality pass. Roughly 28 km -- the
#: lattice step of the point cache itself. Soil is autocorrelated at this range, so a shifted
#: sample still correlates well; what matters is that it correlates measurably WORSE, which is
#: what shows the raster resolves local structure rather than a smooth regional trend.
CONTROL_OFFSET_DEGREES = 0.25

#: Fixed so the null is reproducible run to run; nothing here depends on the draw.
PERMUTATION_SEED = 0

ENVIRONMENT_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)$")


def read_database_url() -> str:
    """Read DATABASE_URL from the environment or the local env files, never echoing it."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    for candidate in (".env.local", ".env"):
        path = REPOSITORY_ROOT / candidate
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            match = ENVIRONMENT_LINE.match(line)
            if match and match.group(1) == "DATABASE_URL":
                return match.group(2).strip().strip('"').strip("'")
    raise SystemExit("DATABASE_URL is not set and was not found in .env.local or .env")


def load_cached_points(database_url: str) -> list[tuple]:
    """Every cell the REST warm driver actually measured, with all six properties."""
    columns = ", ".join(entry[0] for entry in PROPERTY_COLUMNS.values())
    with psycopg2.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(
            f"SELECT lat, lon, {columns} FROM public.soil_grid_cache "
            "WHERE complete AND ph IS NOT NULL"
        )
        return cursor.fetchall()


def verify_property(
    index: int,
    property_name: str,
    points: list[tuple],
    cog_directory: Path,
) -> tuple[str, bool]:
    """Sample one COG at every cached coordinate and report how far it drifts from REST.

    Two independent verdicts, because a tolerance alone cannot tell "correct" from "smooth":

    - agreement -- the share of points inside max(absolute, relative) of the REST reading.
    - alignment -- correlation at the true coordinates, against a permutation null that pairs
      each sampled pixel with someone else's REST reading. A raster clipped with the wrong CRS,
      or with the Homolosine bounds mishandled, still produces entirely plausible soil values,
      and a loose tolerance would wave it through; it cannot survive a null that must sit at
      zero. The quarter-degree shifted correlation is reported alongside as a locality
      diagnostic -- it stays high because soil is autocorrelated at 28 km, so it is evidence
      about resolved detail, not a pass/fail gate.
    """
    _column, absolute_tolerance, relative_tolerance, unit = PROPERTY_COLUMNS[property_name]
    path = cog_directory / f"{property_name}_0-5cm_mean_4326.tif"
    if not path.is_file():
        return f"{property_name:9s} MISSING {path.name}", False

    expected = numpy.array([point[2 + index] for point in points], dtype="float64")
    with rasterio.open(path) as raster:
        scale = raster.scales[0]
        nodata = raster.nodata
        sampled = numpy.array(
            [value[0] for value in raster.sample([(p[1], p[0]) for p in points])], dtype="float64"
        )
        control = numpy.array(
            [
                value[0]
                for value in raster.sample(
                    [(p[1] + CONTROL_OFFSET_DEGREES, p[0]) for p in points]
                )
            ],
            dtype="float64",
        )

    # A cached cell can sit on a pixel the clip left as nodata (coastline, lake); those are not
    # disagreements, they are places the raster makes no claim, so they leave the comparison.
    comparable = (sampled != nodata) & numpy.isfinite(expected)
    if not comparable.any():
        return f"{property_name:9s} no comparable points", False

    actual = sampled[comparable] * scale
    reference = expected[comparable]
    difference = numpy.abs(actual - reference)
    tolerance = numpy.maximum(absolute_tolerance, numpy.abs(reference) * relative_tolerance)
    agreement = (difference <= tolerance).mean()

    aligned_correlation = float(numpy.corrcoef(actual, reference)[0, 1])
    permuted = numpy.random.default_rng(PERMUTATION_SEED).permutation(reference)
    null_correlation = float(numpy.corrcoef(actual, permuted)[0, 1])
    shifted = comparable & (control != nodata)
    shifted_correlation = float(
        numpy.corrcoef(control[shifted] * scale, expected[shifted])[0, 1]
    )

    passed = (
        agreement >= 0.95
        and aligned_correlation >= 0.9
        and abs(null_correlation) <= 0.3
        and aligned_correlation > shifted_correlation
    )
    return (
        f"{property_name:9s} {'PASS' if passed else 'FAIL'} "
        f"n={comparable.sum():5d} agree={agreement:6.1%} "
        f"r={aligned_correlation:5.3f} r_null={null_correlation:+6.3f} "
        f"r_shifted={shifted_correlation:5.3f} "
        f"median|d|={numpy.median(difference):7.3f} {unit}",
        passed,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cog-dir", type=Path, default=DEFAULT_COG_DIRECTORY)
    arguments = parser.parse_args()

    points = load_cached_points(read_database_url())
    if not points:
        print("no measured rows in public.soil_grid_cache to verify against", file=sys.stderr)
        return 1
    print(f"[verify] {len(points)} measured cells in the SoilGrids point cache\n")

    results = [
        verify_property(index, name, points, arguments.cog_dir)
        for index, name in enumerate(PROPERTY_COLUMNS)
    ]
    for line, _ in results:
        print(line)

    failed = [line for line, passed in results if not passed]
    print(f"\n[verify] {len(results) - len(failed)}/{len(results)} properties agree with REST")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
