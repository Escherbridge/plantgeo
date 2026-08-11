"""Upload the built soil COGs to R2 and register each one in the publication catalog.

Three things happen per property, in this order, because a catalog row is a claim that specific
bytes are being served: derive the colour ramp from the data, upload, then register the checksum
of what was uploaded. A failed upload therefore leaves no row, and a row always describes an
object that exists.

    uv run --with rasterio --with boto3 --with psycopg2-binary --no-project \
        python scripts/raster/publish-soil-rasters.py

`--dry-run` derives ramps and prints what would be published without touching R2 or the database.
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
import hashlib
import json
import re
import sys

import boto3
import numpy
import psycopg2
import rasterio

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_COG_DIRECTORY = REPOSITORY_ROOT / "data" / "raster" / "soil"

COLLECTION = "soilgrids"
ARCHIVE_FORMAT = "cog"
ATTRIBUTION = "SoilGrids &mdash; ISRIC (CC-BY 4.0)"
OBJECT_PREFIX = "raster/soil/soilgrids-v2.0"
CONTENT_TYPE = "image/tiff; application=geotiff; profile=cloud-optimized"
#: A release is immutable, so the object it points at can be cached indefinitely.
CACHE_CONTROL = "public, max-age=31536000, immutable"

#: EPSG:4326 at 250 m is finer than z9 at this latitude (110,700/2^z m/px at 45N gives ~216 m at
#: z9), so z10 is the deepest zoom that still shows real pixels rather than interpolation.
NATIVE_MIN_ZOOM = 0
NATIVE_MAX_ZOOM = 10

#: Quantiles the ramp is fitted to. Soil properties here are heavily right-skewed -- soc spans
#: 5.7..462 g/kg but sits under 60 for most of the PNW -- so a ramp stretched linearly across the
#: full range renders the entire region as one flat colour and hides everything the data says.
RAMP_QUANTILES = (0.02, 0.15, 0.35, 0.55, 0.75, 0.90, 0.98)

#: Sequential, colour-blind-safe, dark-to-light in the direction the property increases. Six
#: ramps rather than one, because these are six unrelated measurements sharing only a depth.
RAMP_COLORS = {
    # Acid to alkaline, diverging around neutral: pH is the one property whose midpoint means
    # something, so it is the one ramp that is not sequential.
    "phh2o": ("#b2182b", "#ef8a62", "#fddbc7", "#f7f7f7", "#d1e5f0", "#67a9cf", "#2166ac"),
    "soc": ("#fff7ec", "#fee8c8", "#fdd49e", "#fdbb84", "#e34a33", "#b30000", "#7f0000"),
    "nitrogen": ("#f7fcf5", "#e5f5e0", "#c7e9c0", "#a1d99b", "#41ab5d", "#238b45", "#00441b"),
    "bdod": ("#f7fcfd", "#e0ecf4", "#bfd3e6", "#9ebcda", "#8c96c6", "#8856a7", "#810f7c"),
    "cec": ("#fff7fb", "#ece7f2", "#d0d1e6", "#a6bddb", "#67a9cf", "#1c9099", "#016450"),
    "ocd": ("#ffffe5", "#fff7bc", "#fee391", "#fec44f", "#fe9929", "#d95f0e", "#993404"),
}

ENVIRONMENT_LINE = re.compile(r"^\s*(?:export\s+)?([A-Z0-9_]+)\s*=\s*(.*)$")


def read_environment(name: str) -> str | None:
    """Read one variable from the process environment or the local env files.

    An EMPTY assignment is treated as absent and the search continues to the next file. This
    repo's `.env.local` declares the R2_* keys with no values while `.env` holds the real ones,
    so returning on first match rather than first value silently loses the credentials.
    """
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


def require_environment(*names: str) -> dict[str, str]:
    """Fail before any work starts if the run cannot possibly finish."""
    resolved = {name: read_environment(name) for name in names}
    missing = [name for name, value in resolved.items() if not value]
    if missing:
        raise SystemExit(f"missing required environment: {', '.join(missing)}")
    return resolved  # type: ignore[return-value]


def derive_ramp(path: Path, property_name: str) -> tuple[list[dict], float, float]:
    """Fit a colour ramp to the values this raster actually holds, and report its extremes."""
    # Read at FULL resolution, not off an overview. An overview is already averaged, so its
    # extremes are pulled in from the real ones -- pH reads 4.10..8.90 there against a true
    # 3.80..9.50 -- and value_min/value_max are a claim about the data, not about a pyramid
    # level. These are 4-16 MB local files; the cheaper read is not worth a wrong number.
    with rasterio.open(path) as raster:
        scale = raster.scales[0]
        nodata = raster.nodata
        values = raster.read(1)

    measured = values[values != nodata].astype("float64") * scale
    if measured.size == 0:
        raise SystemExit(f"{path.name} holds no measured pixels")

    breaks = numpy.quantile(measured, RAMP_QUANTILES)
    colors = RAMP_COLORS[property_name]
    ramp = [
        {"value": round(float(value), 4), "color": color}
        for value, color in zip(breaks, colors, strict=True)
    ]
    return ramp, float(measured.min()), float(measured.max())


def upload(session_client, bucket: str, key: str, path: Path) -> None:
    """Put one archive on R2 under an immutable cache policy."""
    session_client.upload_file(
        str(path),
        bucket,
        key,
        ExtraArgs={"ContentType": CONTENT_TYPE, "CacheControl": CACHE_CONTROL},
    )


REGISTER_STATEMENT = """
WITH superseded AS (
    UPDATE geo.raster_release
       SET superseded_at = now()
     WHERE collection = %(collection)s
       AND property = %(property)s
       AND depth = %(depth)s
       AND statistic = %(statistic)s
       AND archive_format = %(archive_format)s
       AND superseded_at IS NULL
       AND checksum_sha256 <> %(checksum)s
    RETURNING id
)
INSERT INTO geo.raster_release (
    collection, property, depth, statistic,
    source_name, source_release, source_url, license_name, attribution,
    unit, scale_divisor, nodata_value, value_min, value_max, color_ramp,
    object_key, archive_format, checksum_sha256, size_bytes,
    min_zoom, max_zoom, bounds
) VALUES (
    %(collection)s, %(property)s, %(depth)s, %(statistic)s,
    %(source_name)s, %(source_release)s, %(source_url)s, %(license_name)s, %(attribution)s,
    %(unit)s, %(scale_divisor)s, %(nodata)s, %(value_min)s, %(value_max)s, %(color_ramp)s,
    %(object_key)s, %(archive_format)s, %(checksum)s, %(size_bytes)s,
    %(min_zoom)s, %(max_zoom)s,
    ST_MakeEnvelope(%(west)s, %(south)s, %(east)s, %(north)s, 4326)
)
ON CONFLICT DO NOTHING
RETURNING id
"""


TILE_CONTENT_TYPE = "application/vnd.pmtiles"
TILE_OBJECT_PREFIX = "raster/soil/soilgrids-v2.0/tiles"

#: The COG row is the source of truth for everything a tile archive shares with it -- unit,
#: scale, ramp, bounds, licence. Copying them across in SQL rather than recomputing is what
#: keeps the two rows for one property from ever disagreeing.
REGISTER_TILES_STATEMENT = """
WITH source AS (
    SELECT * FROM geo.published_raster
     WHERE collection = %(collection)s AND property = %(property)s
       AND depth = %(depth)s AND statistic = %(statistic)s
       AND archive_format = 'cog'
), superseded AS (
    UPDATE geo.raster_release
       SET superseded_at = now()
     WHERE collection = %(collection)s AND property = %(property)s
       AND depth = %(depth)s AND statistic = %(statistic)s
       AND archive_format = 'pmtiles' AND superseded_at IS NULL
       AND checksum_sha256 <> %(checksum)s
    RETURNING id
)
INSERT INTO geo.raster_release (
    collection, property, depth, statistic,
    source_name, source_release, source_url, license_name, attribution,
    unit, scale_divisor, nodata_value, value_min, value_max, color_ramp,
    object_key, archive_format, checksum_sha256, size_bytes,
    min_zoom, max_zoom, bounds
)
SELECT
    source.collection, source.property, source.depth, source.statistic,
    source.source_name, source.source_release, source.source_url,
    source.license_name, source.attribution,
    source.unit, source.scale_divisor, source.nodata_value,
    source.value_min, source.value_max, source.color_ramp,
    %(object_key)s, 'pmtiles', %(checksum)s, %(size_bytes)s,
    %(min_zoom)s, %(max_zoom)s,
    ST_MakeEnvelope(source.bbox_west, source.bbox_south, source.bbox_east, source.bbox_north, 4326)
FROM source
ON CONFLICT DO NOTHING
RETURNING id
"""


def publish_tile_archives(client, bucket: str, connection, tile_directory: Path) -> int:
    """Upload each PMTiles archive and register it against its COG release."""
    published = 0
    for archive in sorted(tile_directory.glob("*_0-5cm_mean.pmtiles")):
        property_name = archive.name.split("_")[0]
        object_key = f"{TILE_OBJECT_PREFIX}/{archive.name}"
        digest = hashlib.sha256()
        with archive.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)

        client.upload_file(
            str(archive),
            bucket,
            object_key,
            ExtraArgs={"ContentType": TILE_CONTENT_TYPE, "CacheControl": CACHE_CONTROL},
        )
        with connection, connection.cursor() as cursor:
            cursor.execute(
                REGISTER_TILES_STATEMENT,
                {
                    "collection": COLLECTION,
                    "property": property_name,
                    "depth": "0-5cm",
                    "statistic": "mean",
                    "object_key": object_key,
                    "checksum": digest.hexdigest(),
                    "size_bytes": archive.stat().st_size,
                    "min_zoom": NATIVE_MIN_ZOOM,
                    "max_zoom": NATIVE_MAX_ZOOM,
                },
            )
            registered = cursor.fetchone()
        published += 1
        print(
            f"[publish] {property_name:9s} {'registered' if registered else 'unchanged'} "
            f"{object_key} ({archive.stat().st_size / 1e6:.1f}MB)"
        )
    return published


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cog-dir", type=Path, default=DEFAULT_COG_DIRECTORY)
    parser.add_argument(
        "--tiles",
        action="store_true",
        help="Publish the PMTiles archives instead of the COGs (build-soil-tiles.py first).",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    manifest_path = arguments.cog_dir / "manifest.json"
    if not manifest_path.is_file():
        print(f"no manifest at {manifest_path}; run build-soil-cogs.py first", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    if arguments.dry_run:
        client = bucket = connection = None
    else:
        environment = require_environment(
            "R2_BUCKET", "R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "DATABASE_URL"
        )
        bucket = environment["R2_BUCKET"]
        client = boto3.client(
            "s3",
            endpoint_url=environment["R2_ENDPOINT"],
            aws_access_key_id=environment["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=environment["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
        )
        connection = psycopg2.connect(environment["DATABASE_URL"])

    if arguments.tiles:
        count = publish_tile_archives(
            client, bucket, connection, arguments.cog_dir / "pmtiles"
        )
        connection.close()
        print(f"[publish] {count} tile archives published to R2 and catalogued")
        return 0

    published = 0
    for artifact in manifest["artifacts"]:
        path = arguments.cog_dir / artifact["file"]
        if not path.is_file():
            print(f"[publish] {artifact['property']}: missing {path.name}, skipping")
            continue

        ramp, value_min, value_max = derive_ramp(path, artifact["property"])
        object_key = f"{OBJECT_PREFIX}/{artifact['file']}"
        west, south, east, north = artifact["bounds"]

        if arguments.dry_run:
            print(
                f"[publish] {artifact['property']:9s} DRY {object_key} "
                f"{artifact['sizeBytes'] / 1e6:5.1f}MB "
                f"range={value_min:.2f}..{value_max:.2f} {artifact['unit']} "
                f"ramp={[stop['value'] for stop in ramp]}"
            )
            continue

        upload(client, bucket, object_key, path)
        with connection, connection.cursor() as cursor:
            cursor.execute(
                REGISTER_STATEMENT,
                {
                    "collection": COLLECTION,
                    "property": artifact["property"],
                    "depth": artifact["depth"],
                    "statistic": artifact["statistic"],
                    "source_name": artifact["source"],
                    "source_release": artifact["sourceRelease"],
                    "source_url": artifact["sourceUrl"],
                    "license_name": artifact["license"],
                    "attribution": ATTRIBUTION,
                    "unit": artifact["unit"],
                    "scale_divisor": artifact["scaleDivisor"],
                    "nodata": -32768.0,
                    "value_min": value_min,
                    "value_max": value_max,
                    "color_ramp": json.dumps(ramp),
                    "object_key": object_key,
                    "archive_format": ARCHIVE_FORMAT,
                    "checksum": artifact["checksumSha256"],
                    "size_bytes": artifact["sizeBytes"],
                    "min_zoom": NATIVE_MIN_ZOOM,
                    "max_zoom": NATIVE_MAX_ZOOM,
                    "west": west,
                    "south": south,
                    "east": east,
                    "north": north,
                },
            )
            registered = cursor.fetchone()
        published += 1
        print(
            f"[publish] {artifact['property']:9s} {'registered' if registered else 'unchanged'} "
            f"{object_key} ({artifact['sizeBytes'] / 1e6:.1f}MB, "
            f"{value_min:.2f}..{value_max:.2f} {artifact['unit']})"
        )

    if connection is not None:
        connection.close()
    print(f"[publish] {published} artifacts published to R2 and catalogued")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
