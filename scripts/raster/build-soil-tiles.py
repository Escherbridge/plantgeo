"""Cut the published soil COGs into z0-10 PNG pyramids and pack each as a PMTiles archive.

Aggregation going up the pyramid happens on VALUES, not on colour: every zoom level is read from
the COG (whose internal overviews are `average`) and only then run through the ramp. Averaging
rendered pixels instead would be wrong wherever the ramp is non-linear, which is everywhere.

The ramp is read back out of `geo.published_raster` rather than restated here, so the tiles are
painted with exactly the ramp the catalog advertises to the legend.

    uv run --with rasterio --with pmtiles --with pillow --with psycopg2-binary --no-project \
        python scripts/raster/build-soil-tiles.py
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

# See build-soil-cogs.py §proj-collision.
_rasterio_spec = importlib.util.find_spec("rasterio")
if _rasterio_spec is None or _rasterio_spec.origin is None:
    raise SystemExit("rasterio is not installed; run with `uv run --with rasterio --no-project`")
_bundled_proj = Path(_rasterio_spec.origin).parent / "proj_data"
if _bundled_proj.is_dir():
    os.environ["PROJ_LIB"] = str(_bundled_proj)
    os.environ["PROJ_DATA"] = str(_bundled_proj)

import argparse
import io
import json
import math
import re
import sys
import time

import numpy
import psycopg2
import rasterio
from PIL import Image
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_COG_DIRECTORY = REPOSITORY_ROOT / "data" / "raster" / "soil"
DEFAULT_TILE_DIRECTORY = REPOSITORY_ROOT / "data" / "raster" / "soil" / "pmtiles"

TILE_SIZE = 256
MIN_ZOOM = 0
#: 250 m is finer than z9 at this latitude (110,700/2^z m/px at 45N gives ~216 m at z9), so z10
#: is the deepest zoom still backed by real pixels. MapLibre overzooms past it.
MAX_ZOOM = 10
WEB_MERCATOR_LIMIT = 20037508.342789244

ENVIRONMENT_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)$")


def read_environment(name: str) -> str | None:
    """Read one variable, treating an empty assignment as absent. See AGENTS.md §env."""
    if os.environ.get(name):
        return os.environ[name]
    for candidate in (".env.local", ".env"):
        path = REPOSITORY_ROOT / candidate
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            match = ENVIRONMENT_LINE.match(line)
            if match and match.group(1) == name:
                value = match.group(2).strip().strip('"').strip("'")
                if value:
                    return value
    return None


def load_published_cogs(database_url: str) -> list[dict]:
    """The live COG releases, with the ramp each one's tiles must be painted with."""
    with psycopg2.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(
            "SELECT property, unit, scale_divisor, color_ramp, object_key, "
            "       bbox_west, bbox_south, bbox_east, bbox_north "
            "  FROM geo.published_raster "
            " WHERE collection = 'soilgrids' AND archive_format = 'cog' "
            " ORDER BY property"
        )
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def tile_window(zoom: int, x: int, y: int) -> Window:
    """The pixel window of one XYZ tile on the pyramid grid the WarpedVRT is defined over.

    Because the VRT spans the whole mercator square at exactly `TILE_SIZE * 2**MAX_ZOOM`
    pixels, every tile at every zoom lands on an integer window fully inside the dataset.
    That is the point of defining it that way: `WarpedVRT` refuses boundless reads, so a VRT
    sized to the data instead would need every edge tile pasted into a nodata canvas by hand.
    """
    span = TILE_SIZE * 2 ** (MAX_ZOOM - zoom)
    return Window(x * span, y * span, span, span)


def tile_range(zoom: int, bounds_4326: tuple[float, float, float, float]) -> tuple[int, int, int, int]:
    """The inclusive x/y tile range covering a lon/lat box at one zoom."""
    west, south, east, north = bounds_4326
    scale = 2**zoom

    def to_x(longitude: float) -> int:
        return int(math.floor((longitude + 180.0) / 360.0 * scale))

    def to_y(latitude: float) -> int:
        clamped = max(min(latitude, 85.05112878), -85.05112878)
        radians = math.radians(clamped)
        fraction = (1.0 - math.asinh(math.tan(radians)) / math.pi) / 2.0
        return int(math.floor(fraction * scale))

    return (
        max(0, to_x(west)),
        max(0, to_y(north)),
        min(scale - 1, to_x(east)),
        min(scale - 1, to_y(south)),
    )


def build_lookup(color_ramp: list[dict], scale_divisor: int) -> tuple[numpy.ndarray, numpy.ndarray]:
    """Ramp stops as parallel arrays of RAW pixel values and RGB triples.

    Stop values are in real units; comparing them against raw pixels means scaling them up
    once here rather than converting a whole tile of pixels down on every read.
    """
    values = numpy.array(
        [stop["value"] * scale_divisor for stop in color_ramp], dtype="float64"
    )
    colors = numpy.array(
        [
            [int(stop["color"][index : index + 2], 16) for index in (1, 3, 5)]
            for stop in color_ramp
        ],
        dtype="float64",
    )
    return values, colors


#: Palette slot reserved for nodata. Index 0 is the transparent entry; 1..255 carry the ramp.
NODATA_INDEX = 0
RAMP_INDEX_COUNT = 255


def build_palette(
    stop_values: numpy.ndarray, stop_colors: numpy.ndarray
) -> tuple[bytes, float, float]:
    """Bake the ramp into a 256-entry PNG palette, and report the value span it covers.

    A soil tile is a smooth gradient, and a truecolour PNG of a smooth gradient is expensive --
    the first cut of these six archives came to 285 MB. A paletted PNG of the same tiles is a
    fraction of that, because a 7-stop ramp never needs more than 255 distinct colours.

    The ramp's PIECEWISE shape is preserved in the palette itself: entry i is the ramp evaluated
    at the value it stands for, so the quantile spacing survives. The only loss is that a value
    is snapped to one of 255 levels across the ramp span, which is finer than the eye resolves
    and far finer than the ramp's own 7 stops.
    """
    low, high = float(stop_values[0]), float(stop_values[-1])
    sampled = numpy.linspace(low, high, RAMP_INDEX_COUNT)
    palette = bytearray([0, 0, 0])  # index 0: nodata, made transparent below
    for channel_values in zip(
        *(numpy.interp(sampled, stop_values, stop_colors[:, channel]) for channel in range(3)),
        strict=True,
    ):
        palette.extend(int(round(value)) for value in channel_values)
    palette.extend(bytes(3 * (256 - RAMP_INDEX_COUNT - 1)))
    return bytes(palette), low, high


def render_tile(
    values: numpy.ndarray,
    nodata: float,
    palette: bytes,
    low: float,
    high: float,
) -> bytes | None:
    """Paint one tile, or return None when it holds no measurement at all.

    Values are clamped to the ramp span before indexing, so a pixel below the first stop takes
    the first colour and one above the last takes the last -- the ramp is fitted to quantiles,
    so the tails are real data that must still be drawn, just not given their own colours.
    """
    measured = values != nodata
    if not measured.any():
        return None

    span = high - low if high > low else 1.0
    scaled = (values.astype("float64") - low) / span
    indexed = numpy.clip(
        numpy.rint(scaled * (RAMP_INDEX_COUNT - 1)) + 1, 1, RAMP_INDEX_COUNT
    ).astype("uint8")
    indexed[~measured] = NODATA_INDEX

    image = Image.fromarray(indexed, mode="P")
    image.putpalette(palette)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True, transparency=NODATA_INDEX)
    return buffer.getvalue()


def build_archive(release: dict, cog_directory: Path, tile_directory: Path) -> dict:
    """Cut one property's whole pyramid and write it as a PMTiles archive."""
    property_name = release["property"]
    cog_path = cog_directory / Path(release["object_key"]).name
    if not cog_path.is_file():
        raise SystemExit(f"{property_name}: no local COG at {cog_path}")

    bounds_4326 = (
        release["bbox_west"],
        release["bbox_south"],
        release["bbox_east"],
        release["bbox_north"],
    )
    color_ramp = release["color_ramp"]
    if isinstance(color_ramp, str):
        color_ramp = json.loads(color_ramp)
    stop_values, stop_colors = build_lookup(color_ramp, release["scale_divisor"])
    palette, ramp_low, ramp_high = build_palette(stop_values, stop_colors)

    started_at = time.time()
    rendered: dict[int, bytes] = {}
    empty = 0

    with rasterio.open(cog_path) as source:
        nodata = source.nodata
        # Reprojecting to the tiling CRS once, as a virtual dataset, lets every tile read be a
        # plain window read -- and a windowed read at a coarse out_shape is what lets GDAL pick
        # the COG's own averaged overview instead of decoding full resolution for a z3 tile.
        # The VRT is laid out on the XYZ pyramid grid itself (see tile_window), so it is a
        # virtual 262144^2 raster; nothing is materialised and reads outside the source's
        # footprint come back as nodata.
        pyramid_pixels = TILE_SIZE * 2**MAX_ZOOM
        pyramid_resolution = 2 * WEB_MERCATOR_LIMIT / pyramid_pixels
        with WarpedVRT(
            source,
            crs="EPSG:3857",
            transform=rasterio.transform.from_origin(
                -WEB_MERCATOR_LIMIT, WEB_MERCATOR_LIMIT, pyramid_resolution, pyramid_resolution
            ),
            width=pyramid_pixels,
            height=pyramid_pixels,
            resampling=Resampling.average,
            src_nodata=nodata,
            nodata=nodata,
        ) as warped:
            for zoom in range(MIN_ZOOM, MAX_ZOOM + 1):
                min_x, min_y, max_x, max_y = tile_range(zoom, bounds_4326)
                for x in range(min_x, max_x + 1):
                    for y in range(min_y, max_y + 1):
                        values = warped.read(
                            1,
                            window=tile_window(zoom, x, y),
                            out_shape=(TILE_SIZE, TILE_SIZE),
                            resampling=Resampling.average,
                        )
                        png = render_tile(values, nodata, palette, ramp_low, ramp_high)
                        if png is None:
                            empty += 1
                            continue
                        rendered[zxy_to_tileid(zoom, x, y)] = png

    if not rendered:
        raise SystemExit(f"{property_name}: every tile was empty; refusing to write an archive")

    tile_directory.mkdir(parents=True, exist_ok=True)
    archive_path = tile_directory / f"{property_name}_0-5cm_mean.pmtiles"
    west, south, east, north = bounds_4326

    with archive_path.open("wb") as handle:
        writer = Writer(handle)
        # PMTiles requires ascending tile ids; the render loop walks zoom-major, which is not
        # that order, so the sort here is load-bearing rather than tidiness.
        for tile_id in sorted(rendered):
            writer.write_tile(tile_id, rendered[tile_id])
        writer.finalize(
            {
                "tile_type": TileType.PNG,
                # PNG carries its own DEFLATE; wrapping it again costs CPU for no bytes.
                "tile_compression": Compression.NONE,
                "internal_compression": Compression.GZIP,
                "clustered": True,
                "min_zoom": MIN_ZOOM,
                "max_zoom": MAX_ZOOM,
                "min_lon_e7": int(west * 1e7),
                "min_lat_e7": int(south * 1e7),
                "max_lon_e7": int(east * 1e7),
                "max_lat_e7": int(north * 1e7),
                "center_zoom": 6,
                "center_lon_e7": int((west + east) / 2 * 1e7),
                "center_lat_e7": int((south + north) / 2 * 1e7),
            },
            {
                "name": f"SoilGrids {property_name} 0-5cm mean",
                "description": (
                    f"ISRIC SoilGrids v2.0 {property_name}, 0-5 cm mean, "
                    f"in {release['unit']}, clipped to the PNW."
                ),
                "attribution": "SoilGrids &mdash; ISRIC (CC-BY 4.0)",
                "type": "overlay",
                "unit": release["unit"],
                "colorRamp": color_ramp,
            },
        )

    size = archive_path.stat().st_size
    print(
        f"[soil-tiles] {property_name:9s} {len(rendered):5d} tiles ({empty} empty) "
        f"-> {archive_path.name} {size / 1e6:.1f}MB in {time.time() - started_at:.0f}s"
    )
    return {"property": property_name, "file": archive_path.name, "tiles": len(rendered)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cog-dir", type=Path, default=DEFAULT_COG_DIRECTORY)
    parser.add_argument("--tile-dir", type=Path, default=DEFAULT_TILE_DIRECTORY)
    parser.add_argument("--properties", nargs="*")
    arguments = parser.parse_args()

    database_url = read_environment("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2

    releases = load_published_cogs(database_url)
    if arguments.properties:
        wanted = set(arguments.properties)
        releases = [release for release in releases if release["property"] in wanted]
    if not releases:
        print(
            "no live COG releases in geo.published_raster; run publish-soil-rasters.py first",
            file=sys.stderr,
        )
        return 1

    for release in releases:
        build_archive(release, arguments.cog_dir, arguments.tile_dir)
    print(f"[soil-tiles] {len(releases)} archives written to {arguments.tile_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
