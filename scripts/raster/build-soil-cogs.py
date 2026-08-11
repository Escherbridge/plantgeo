"""Clip the six SoilGrids topsoil properties to the PNW and persist them as Cloud-Optimized GeoTIFFs.

Reads ISRIC's published VRTs over /vsicurl, so only the blocks covering the bbox cross the wire.
Writes one EPSG:4326 COG per property -- the archival artifact the tile build and any zonal
analysis both read -- plus a manifest carrying the checksum, extent and unit scale of each.

Run from the repository root:

    uv run --with rasterio --no-project python scripts/raster/build-soil-cogs.py

Rationale, measured costs and the traps this pipeline hits live in `scripts/raster/AGENTS.md`.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

# This machine carries a global PROJ_LIB pointing at PostgreSQL's PostGIS PROJ, whose proj.db
# predates the layout GDAL 3.12 requires -- every CRS lookup fails with a LAYOUT.VERSION error.
# find_spec locates rasterio's bundled proj.db WITHOUT importing rasterio, so PROJ is repointed
# before its one-shot initialisation rather than after. See `scripts/raster/AGENTS.md` §proj-collision.
_rasterio_spec = importlib.util.find_spec("rasterio")
if _rasterio_spec is None or _rasterio_spec.origin is None:
    raise SystemExit("rasterio is not installed; run with `uv run --with rasterio --no-project`")
_bundled_proj = Path(_rasterio_spec.origin).parent / "proj_data"
if _bundled_proj.is_dir():
    os.environ["PROJ_LIB"] = str(_bundled_proj)
    os.environ["PROJ_DATA"] = str(_bundled_proj)

import argparse
import hashlib
import json
import sys
import time
from dataclasses import dataclass

import numpy
import rasterio
from rasterio.crs import CRS
from rasterio.env import Env
from rasterio.warp import Resampling, calculate_default_transform, reproject, transform_bounds
from rasterio.windows import Window, from_bounds

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_OUTPUT_DIRECTORY = REPOSITORY_ROOT / "data" / "raster" / "soil"

SOILGRIDS_DATA_ROOT = "https://files.isric.org/soilgrids/latest/data"
SOILGRIDS_RELEASE = "v2.0"
SOILGRIDS_LICENSE = "CC-BY 4.0"
DEPTH = "0-5cm"
STATISTIC = "mean"

# The bbox the ingestion service already uses (INGEST_BBOX in .env.local), so the raster extent
# matches the lattice every other PNW lane is built on.
DEFAULT_BBOX = (-125.0, 42.0, -111.0, 49.0)

# ISRIC's documented settings for reading their VRTs remotely. Without DISABLE_READDIR the
# driver tries to list a WebDAV directory holding tens of thousands of tiles on every open.
GDAL_REMOTE_OPTIONS = dict(
    GDAL_HTTP_MAX_RETRY=5,
    GDAL_HTTP_RETRY_DELAY=2,
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    CPL_VSIL_CURL_USE_HEAD="NO",
    VSI_CACHE="TRUE",
    VSI_CACHE_SIZE=100_000_000,
    GDAL_NUM_THREADS="ALL_CPUS",
)


@dataclass(frozen=True)
class SoilProperty:
    """One SoilGrids property: how it is stored upstream and what the stored integer means."""

    name: str
    label: str
    #: Divide the stored integer by this to get `unit`. Verified against the REST point cache
    #: by scripts/raster/verify-soil-cogs.py rather than trusted from documentation alone.
    scale_divisor: int
    unit: str


# The same six `soilgrids.ts#PROPERTY_FIELDS` serves to the point query, so the raster and the
# click-through readout describe the same six facts.
SOIL_PROPERTIES = (
    SoilProperty("phh2o", "pH (H2O)", 10, "pH"),
    SoilProperty("soc", "Organic carbon", 10, "g/kg"),
    SoilProperty("nitrogen", "Total nitrogen", 100, "g/kg"),
    SoilProperty("bdod", "Bulk density", 100, "kg/dm^3"),
    SoilProperty("cec", "Cation exchange capacity", 10, "cmol(c)/kg"),
    SoilProperty("ocd", "Organic carbon density", 10, "kg/m^3"),
)

#: Written into every COG so a reader that never sees this repo still gets real units.
COG_CREATION_OPTIONS = dict(
    driver="COG",
    compress="DEFLATE",
    predictor=2,
    blocksize=512,
    overview_resampling="average",
    num_threads="all_cpus",
    bigtiff="IF_SAFER",
)


def source_vrt_url(soil_property: SoilProperty) -> str:
    """The /vsicurl path to one property's published VRT."""
    stem = f"{soil_property.name}_{DEPTH}_{STATISTIC}"
    return f"/vsicurl/{SOILGRIDS_DATA_ROOT}/{soil_property.name}/{stem}.vrt"


def read_bbox_window(source: rasterio.DatasetReader, bbox: tuple[float, float, float, float]):
    """Read just the bbox out of a Homolosine source, returning the array and its transform.

    The bbox is reprojected into the source CRS first. SoilGrids is Interrupted Goode
    Homolosine, so treating a lon/lat box as native coordinates reads a different continent
    rather than failing -- see `scripts/raster/AGENTS.md` §homolosine.
    """
    native_bounds = transform_bounds("EPSG:4326", source.crs, *bbox, densify_pts=64)
    window = from_bounds(*native_bounds, transform=source.transform).round_offsets().round_lengths()
    # A window is only meaningful inside the raster; clamping keeps a bbox that overhangs the
    # source edge from producing negative offsets.
    window = window.intersection(Window(0, 0, source.width, source.height))
    return source.read(1, window=window), source.window_transform(window)


def warp_to_wgs84(
    values: numpy.ndarray,
    source_transform,
    source_crs: CRS,
    nodata: float,
) -> tuple[numpy.ndarray, object]:
    """Reproject a Homolosine block onto EPSG:4326, the CRS every PostGIS object here uses."""
    height, width = values.shape
    source_bounds = rasterio.transform.array_bounds(height, width, source_transform)
    target_transform, target_width, target_height = calculate_default_transform(
        source_crs, CRS.from_epsg(4326), width, height, *source_bounds
    )
    destination = numpy.full((target_height, target_width), nodata, dtype=values.dtype)
    reproject(
        source=values,
        destination=destination,
        src_transform=source_transform,
        src_crs=source_crs,
        src_nodata=nodata,
        dst_transform=target_transform,
        dst_crs=CRS.from_epsg(4326),
        dst_nodata=nodata,
        # Bilinear over a continuous surface. Nearest would quantise a soil gradient into the
        # source's 250 m stair-steps; anything wider would invent detail between them.
        resampling=Resampling.bilinear,
        num_threads=os.cpu_count() or 4,
    )
    return destination, target_transform


def write_cog(
    path: Path,
    values: numpy.ndarray,
    transform,
    nodata: float,
    soil_property: SoilProperty,
) -> None:
    """Write one property as a COG whose internal overviews are the aggregation going up."""
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = dict(
        COG_CREATION_OPTIONS,
        dtype=values.dtype.name,
        count=1,
        height=values.shape[0],
        width=values.shape[1],
        crs=CRS.from_epsg(4326),
        transform=transform,
        nodata=nodata,
    )
    with rasterio.open(path, "w", **profile) as destination:
        destination.write(values, 1)
        # Stored as the upstream integer; the scale is what turns it back into a measurement.
        destination.scales = (1 / soil_property.scale_divisor,)
        destination.set_band_description(1, f"{soil_property.label} ({soil_property.unit})")
        destination.update_tags(
            source="ISRIC SoilGrids",
            source_release=SOILGRIDS_RELEASE,
            source_url=f"{SOILGRIDS_DATA_ROOT}/{soil_property.name}/",
            license=SOILGRIDS_LICENSE,
            property=soil_property.name,
            depth=DEPTH,
            statistic=STATISTIC,
            unit=soil_property.unit,
            scale_divisor=str(soil_property.scale_divisor),
        )


def checksum(path: Path) -> str:
    """SHA-256 of a built artifact, so the manifest can prove what was uploaded."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_property(
    soil_property: SoilProperty,
    bbox: tuple[float, float, float, float],
    output_directory: Path,
    overwrite: bool,
) -> dict:
    """Fetch, reproject and persist one property, returning its manifest entry."""
    output_path = output_directory / f"{soil_property.name}_{DEPTH}_{STATISTIC}_4326.tif"
    if output_path.exists() and not overwrite:
        print(f"[soil-cog] {soil_property.name}: exists, skipping (--overwrite to rebuild)")
        return manifest_entry(soil_property, output_path, bbox)

    started_at = time.time()
    with rasterio.open(source_vrt_url(soil_property)) as source:
        nodata = source.nodata
        values, source_transform = read_bbox_window(source, bbox)
        read_seconds = time.time() - started_at
        print(
            f"[soil-cog] {soil_property.name}: read {values.shape[1]}x{values.shape[0]} "
            f"in {read_seconds:.0f}s"
        )
        warped, target_transform = warp_to_wgs84(values, source_transform, source.crs, nodata)

    write_cog(output_path, warped, target_transform, nodata, soil_property)
    measured = warped[warped != nodata]
    print(
        f"[soil-cog] {soil_property.name}: wrote {output_path.name} "
        f"({output_path.stat().st_size / 1e6:.1f} MB, "
        f"{measured.size / warped.size:.1%} measured, "
        f"{measured.min() / soil_property.scale_divisor:.2f}"
        f"..{measured.max() / soil_property.scale_divisor:.2f} {soil_property.unit}) "
        f"in {time.time() - started_at:.0f}s"
    )
    return manifest_entry(soil_property, output_path, bbox)


def manifest_entry(soil_property: SoilProperty, path: Path, bbox) -> dict:
    """Describe a built COG well enough to register it as an immutable release."""
    with rasterio.open(path) as built:
        bounds = tuple(round(value, 6) for value in built.bounds)
        resolution = built.res
        overviews = built.overviews(1)
        shape = (built.width, built.height)
    return {
        "property": soil_property.name,
        "label": soil_property.label,
        "unit": soil_property.unit,
        "scaleDivisor": soil_property.scale_divisor,
        "depth": DEPTH,
        "statistic": STATISTIC,
        "source": "ISRIC SoilGrids",
        "sourceRelease": SOILGRIDS_RELEASE,
        "sourceUrl": f"{SOILGRIDS_DATA_ROOT}/{soil_property.name}/",
        "license": SOILGRIDS_LICENSE,
        "file": path.name,
        "sizeBytes": path.stat().st_size,
        "checksumSha256": checksum(path),
        "crs": "EPSG:4326",
        "bounds": list(bounds),
        "requestedBbox": list(bbox),
        "widthHeight": list(shape),
        "resolutionDegrees": [round(resolution[0], 8), round(resolution[1], 8)],
        "overviewFactors": list(overviews),
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--properties",
        nargs="*",
        default=[item.name for item in SOIL_PROPERTIES],
        help="Subset of properties to build (default: all six).",
    )
    parser.add_argument(
        "--bbox",
        default=os.environ.get("INGEST_BBOX", ",".join(str(v) for v in DEFAULT_BBOX)),
        help="west,south,east,north in EPSG:4326 (default: INGEST_BBOX).",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument("--overwrite", action="store_true", help="Rebuild COGs that already exist.")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        bbox = tuple(float(part) for part in arguments.bbox.split(","))
        if len(bbox) != 4:
            raise ValueError
    except ValueError:
        print(f"--bbox must be west,south,east,north; got {arguments.bbox!r}", file=sys.stderr)
        return 2

    selected = [item for item in SOIL_PROPERTIES if item.name in set(arguments.properties)]
    unknown = set(arguments.properties) - {item.name for item in SOIL_PROPERTIES}
    if unknown:
        print(f"unknown properties: {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2

    print(f"[soil-cog] bbox={bbox} properties={[item.name for item in selected]}")
    entries = []
    with Env(**GDAL_REMOTE_OPTIONS):
        for soil_property in selected:
            entries.append(
                build_property(soil_property, bbox, arguments.output_dir, arguments.overwrite)
            )

    manifest_path = arguments.output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "generator": "scripts/raster/build-soil-cogs.py",
                "sourceRelease": SOILGRIDS_RELEASE,
                "license": SOILGRIDS_LICENSE,
                "bbox": list(bbox),
                "artifacts": entries,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[soil-cog] manifest written to {manifest_path} ({len(entries)} artifacts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
