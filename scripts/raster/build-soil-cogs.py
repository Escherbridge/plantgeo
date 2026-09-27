"""Clip ten SoilGrids properties, at three depths, to the PNW as Cloud-Optimized GeoTIFFs.

Reads ISRIC's published VRTs over /vsicurl, so only the blocks covering the bbox cross the wire.
Writes one EPSG:4326 COG per property-depth -- the archival artifact the tile build and any zonal
analysis both read -- plus a manifest carrying the checksum, extent, unit scale and upstream
Last-Modified/ETag of each.

Run from the repository root:

    uv run --with rasterio --no-project python scripts/raster/build-soil-cogs.py

`--properties` and `--depths` each default to the full set; pass either to build a subset, or
`--depths all` for all three spelled out. An existing output is skipped, not rebuilt, unless
`--overwrite` is given, so re-running after adding depths or properties only fetches what is
missing. `--only-new` additionally skips the six property-depths already published at 0-5cm
(phh2o, soc, nitrogen, bdod, cec, ocd), so a WS-B release-expansion run never re-touches the live
COGs. `--list` prints the planned property-depths and exits without any network access or writes.

`--capture-dir <dir>` builds from a WS-A `soil_properties capture` capture manifest (CONTRACT C8)
instead of downloading from ISRIC, so the COGs and the Parquet lane share one download. The
manifest's own pinned Last-Modified/ETag per file is checked against its `.receipt.json` sidecar
and the file's sha256 before it is read; any mismatch refuses rather than building from a drifted
capture. Omit `--capture-dir` to keep reading the published VRTs directly over `/vsicurl`.

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
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime, parsedate_to_datetime

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
#: The three depths ISRIC's REST query and this release both carry. Deeper depths
#: (30-60/60-100/100-200 cm) exist upstream but are out of scope (DESIGN.md §3).
DEPTHS = ("0-5cm", "5-15cm", "15-30cm")
STATISTIC = "mean"

# The bbox the ingestion service already uses (INGEST_BBOX in .env.local), so the raster extent
# matches the lattice every other PNW lane is built on.
DEFAULT_BBOX = (-125.0, 42.0, -111.0, 49.0)

#: Matches `pipeline/direct/soil_properties/capture.py::CAPTURE_MANIFEST_NAME` (CONTRACT C8).
CAPTURE_MANIFEST_NAME = "capture-manifest.json"

#: The six 0-5cm property-depths already public on R2 (AGENTS.md "today's live catalog").
#: `--only-new` builds everything except these, so a rerun after WS-B never re-touches the
#: live release.
PUBLISHED_0_5CM_PROPERTIES = frozenset({"phh2o", "soc", "nitrogen", "bdod", "cec", "ocd"})

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
    #: Divide the stored integer by this to get `unit`. Per ISRIC's mapped-unit conversion table
    #: (isric.org/explore/soilgrids/faq-soilgrids): texture is stored as g/kg (divisor 10 -> %,
    #: i.e. g/100g) and cfvo as cm3/dm3 vol-permille (divisor 10 -> cm3/100cm3 vol%). G-V4 in
    #: `.omc/soil-data-plane-20260927/DESIGN.md` §2.8 checks these against ISRIC's REST endpoint.
    scale_divisor: int
    unit: str


# The same ten properties `soilgrids.ts#PROPERTY_FIELDS` serves to the point query, so the raster
# and the click-through readout describe the same facts.
SOIL_PROPERTIES = (
    SoilProperty("phh2o", "pH (H2O)", 10, "pH"),
    SoilProperty("soc", "Organic carbon", 10, "g/kg"),
    SoilProperty("nitrogen", "Total nitrogen", 100, "g/kg"),
    SoilProperty("bdod", "Bulk density", 100, "kg/dm^3"),
    SoilProperty("cec", "Cation exchange capacity", 10, "cmol(c)/kg"),
    SoilProperty("ocd", "Organic carbon density", 10, "kg/m^3"),
    SoilProperty("clay", "Clay content", 10, "%"),
    SoilProperty("sand", "Sand content", 10, "%"),
    SoilProperty("silt", "Silt content", 10, "%"),
    SoilProperty("cfvo", "Coarse fragments volume", 10, "cm3/100cm3"),
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


def source_http_url(soil_property: SoilProperty, depth: str) -> str:
    """The plain HTTPS path to one property-depth's published VRT (no /vsicurl prefix)."""
    stem = f"{soil_property.name}_{depth}_{STATISTIC}"
    return f"{SOILGRIDS_DATA_ROOT}/{soil_property.name}/{stem}.vrt"


def source_vrt_url(soil_property: SoilProperty, depth: str) -> str:
    """The /vsicurl path to one property-depth's published VRT, for the windowed GDAL read."""
    return f"/vsicurl/{source_http_url(soil_property, depth)}"


def probe_source_metadata(soil_property: SoilProperty, depth: str) -> dict:
    """HEAD one VRT for its Last-Modified and ETag, so the manifest can prove provenance.

    Plain HTTPS rather than /vsicurl: GDAL's curl layer does not surface response headers the
    way `urllib` does, and a HEAD costs nothing next to the multi-MB windowed read that follows.
    Never refuse here on a per-file date mismatch -- three `ocd` VRTs predate the rest by about a
    week; the release watermark is the MAX Last-Modified over every probed file, not a per-file
    gate. See `scripts/raster/AGENTS.md` §watermark.
    """
    request = urllib.request.Request(source_http_url(soil_property, depth), method="HEAD")
    with urllib.request.urlopen(request, timeout=30) as response:
        return {
            "lastModified": response.headers.get("Last-Modified"),
            "etag": response.headers.get("ETag"),
        }


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
    depth: str,
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
        # Stored as the upstream integer; the scale turns it back into the physical-unit model estimate.
        destination.scales = (1 / soil_property.scale_divisor,)
        destination.set_band_description(1, f"{soil_property.label} ({soil_property.unit})")
        destination.update_tags(
            source="ISRIC SoilGrids",
            source_release=SOILGRIDS_RELEASE,
            source_url=f"{SOILGRIDS_DATA_ROOT}/{soil_property.name}/",
            license=SOILGRIDS_LICENSE,
            property=soil_property.name,
            depth=depth,
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


def is_already_published(soil_property: SoilProperty, depth: str) -> bool:
    """True for the six property-depths already live at 0-5cm; `--only-new` skips these."""
    return depth == "0-5cm" and soil_property.name in PUBLISHED_0_5CM_PROPERTIES


def canonical_json(value) -> str:
    """CONTRACT C5.5 canonical JSON: sorted keys, no spaces, integral floats as integers.

    Mirrors `pipeline/direct/soil_properties/source.py::canonical_json` (read-only there) so the
    capture manifest's own `manifest_sha256` can be recomputed and checked here without importing
    the agri service package.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not numpy.isfinite(value):
            raise ValueError("canonical JSON has no representation for a non-finite number")
        if value.is_integer() and abs(value) < 2**53:
            return str(int(value))
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, dict):
        items = sorted(value.items(), key=lambda item: str(item[0]))
        return "{" + ",".join(f"{json.dumps(str(key))}:{canonical_json(item)}" for key, item in items) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    raise TypeError(f"canonical JSON cannot render {type(value).__name__}")


def load_capture_manifest(capture_dir: Path) -> dict:
    """Read the WS-A capture manifest (CONTRACT C8) and self-verify its digest.

    A mismatched `manifest_sha256` means the manifest is incomplete (capture still running) or
    was hand-edited; either way it is not a base to build the "same capture bytes" guarantee on.
    """
    manifest_path = capture_dir / CAPTURE_MANIFEST_NAME
    if not manifest_path.is_file():
        raise SystemExit(
            f"no capture manifest at {manifest_path}; run "
            "`python -m agri_data_service.pipeline.direct.soil_properties capture "
            f"--capture-dir {capture_dir}` first"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    body = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    digest = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
    if manifest.get("manifest_sha256") != digest:
        raise SystemExit(
            f"{manifest_path}: manifest_sha256 does not match its own canonical JSON; "
            "the capture is corrupt or was hand-edited"
        )
    return manifest


def capture_file_entry(manifest: dict, soil_property: SoilProperty, depth: str) -> dict:
    """The C8 `files[]` entry for one property-depth, or refuse if the capture never wrote it."""
    for entry in manifest.get("files", ()):
        if entry["property"] == soil_property.name and entry["depth"] == depth:
            return entry
    raise SystemExit(f"{soil_property.name} {depth}: not present in the capture manifest")


def verify_capture_file(capture_dir: Path, entry: dict) -> None:
    """Refuse when the capture drifted: missing bytes, a changed checksum, or a moved pin.

    The manifest's own `last_modified`/`etag` for this file is cross-checked against the
    per-file `.receipt.json` sidecar `capture.py` writes alongside it (both are built from the
    same pin at capture time, so any difference means the manifest and the receipts disagree --
    a stale or partially rerun capture, never a normal state).
    """
    image = capture_dir / entry["path"]
    if not image.is_file():
        raise SystemExit(f"{entry['path']}: missing from {capture_dir} (capture is incomplete)")
    receipt_path = capture_dir / f"{entry['path']}.receipt.json"
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        pinned = (entry.get("last_modified"), entry.get("etag"))
        observed = (receipt.get("last_modified"), receipt.get("etag"))
        if observed != pinned:
            raise SystemExit(
                f"{entry['path']}: pinned Last-Modified/ETag in {CAPTURE_MANIFEST_NAME} {pinned} "
                f"differs from its receipt {observed}; the capture drifted -- rerun capture"
            )
    if checksum(image) != entry["sha256"]:
        raise SystemExit(
            f"{entry['path']}: sha256 does not match the capture manifest; bytes changed since capture"
        )


def build_property(
    soil_property: SoilProperty,
    depth: str,
    bbox: tuple[float, float, float, float],
    output_directory: Path,
    overwrite: bool,
    capture_dir: Path | None = None,
    capture_manifest: dict | None = None,
) -> dict:
    """Fetch (or read from the WS-A capture), reproject and persist one property-depth.

    Returns its manifest entry. With `--capture-dir`, `source_path` reads the already-downloaded
    native Homolosine window instead of the remote VRT (CONTRACT C8's "same capture bytes"
    guarantee), and `metadata` comes from the capture manifest's pinned Last-Modified/ETag rather
    than a fresh HEAD, so a capture-based build makes no network call to ISRIC at all.
    """
    output_path = output_directory / f"{soil_property.name}_{depth}_{STATISTIC}_4326.tif"

    if capture_dir is not None:
        entry = capture_file_entry(capture_manifest, soil_property, depth)
        verify_capture_file(capture_dir, entry)
        # The manifest stores the pin as an ISO-8601 instant (capture.py `_instant`); reformat to
        # the RFC 2822 HTTP-date `probe_source_metadata` produces, so `release_watermark`'s
        # `parsedate_to_datetime` reads either source's manifest.json the same way.
        pinned_instant = datetime.strptime(entry["last_modified"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        metadata = {"lastModified": format_datetime(pinned_instant, usegmt=True), "etag": entry["etag"]}
        source_path = capture_dir / entry["path"]
    else:
        # HEAD every file, built or skipped, so the manifest's Last-Modified/ETag table -- and
        # the release watermark derived from it -- covers every property-depth this run
        # touched, not only the ones that happened to need a download.
        metadata = probe_source_metadata(soil_property, depth)
        source_path = None

    if output_path.exists() and not overwrite:
        print(f"[soil-cog] {soil_property.name} {depth}: exists, skipping (--overwrite to rebuild)")
        return manifest_entry(soil_property, depth, output_path, bbox, metadata)

    started_at = time.time()
    source_location = str(source_path) if source_path is not None else source_vrt_url(soil_property, depth)
    with rasterio.open(source_location) as source:
        nodata = source.nodata
        values, source_transform = read_bbox_window(source, bbox)
        read_seconds = time.time() - started_at
        origin = "capture" if source_path is not None else "vsicurl"
        print(
            f"[soil-cog] {soil_property.name} {depth}: read {values.shape[1]}x{values.shape[0]} "
            f"from {origin} in {read_seconds:.0f}s"
        )
        warped, target_transform = warp_to_wgs84(values, source_transform, source.crs, nodata)

    write_cog(output_path, warped, target_transform, nodata, soil_property, depth)
    valid = warped[warped != nodata]
    print(
        f"[soil-cog] {soil_property.name} {depth}: wrote {output_path.name} "
        f"({output_path.stat().st_size / 1e6:.1f} MB, "
        f"{valid.size / warped.size:.1%} valid, "
        f"{valid.min() / soil_property.scale_divisor:.2f}"
        f"..{valid.max() / soil_property.scale_divisor:.2f} {soil_property.unit}) "
        f"in {time.time() - started_at:.0f}s"
    )
    return manifest_entry(soil_property, depth, output_path, bbox, metadata)


def manifest_entry(
    soil_property: SoilProperty, depth: str, path: Path, bbox, metadata: dict
) -> dict:
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
        "depth": depth,
        "statistic": STATISTIC,
        "source": "ISRIC SoilGrids",
        "sourceRelease": SOILGRIDS_RELEASE,
        "sourceUrl": f"{SOILGRIDS_DATA_ROOT}/{soil_property.name}/",
        "license": SOILGRIDS_LICENSE,
        "file": path.name,
        "sizeBytes": path.stat().st_size,
        "checksumSha256": checksum(path),
        "sourceLastModified": metadata["lastModified"],
        "sourceEtag": metadata["etag"],
        "crs": "EPSG:4326",
        "bounds": list(bounds),
        "requestedBbox": list(bbox),
        "widthHeight": list(shape),
        "resolutionDegrees": [round(resolution[0], 8), round(resolution[1], 8)],
        "overviewFactors": list(overviews),
    }


def release_watermark(entries: list[dict]) -> str | None:
    """MAX Last-Modified across every probed file, as an ISO date -- the release's watermark.

    Per-file dates are allowed to differ (three `ocd` VRTs are dated 2020-05-26 against
    2020-06-02 for the rest); this reports the newest instant rather than refusing on the split.
    """
    instants = [
        parsedate_to_datetime(entry["sourceLastModified"])
        for entry in entries
        if entry.get("sourceLastModified")
    ]
    return max(instants).date().isoformat() if instants else None


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--properties",
        nargs="*",
        default=[item.name for item in SOIL_PROPERTIES],
        help="Subset of properties to build (default: all ten).",
    )
    parser.add_argument(
        "--depths",
        nargs="*",
        default=list(DEPTHS),
        help="Subset of depths to build, or `all` for all three (default: all three).",
    )
    parser.add_argument(
        "--bbox",
        default=os.environ.get("INGEST_BBOX", ",".join(str(v) for v in DEFAULT_BBOX)),
        help="west,south,east,north in EPSG:4326 (default: INGEST_BBOX).",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument("--overwrite", action="store_true", help="Rebuild COGs that already exist.")
    parser.add_argument(
        "--capture-dir",
        type=Path,
        default=None,
        help=(
            "Build from a WS-A `soil_properties capture` capture-manifest.json (CONTRACT C8) "
            "instead of downloading from ISRIC. Omit to read the published VRTs directly."
        ),
    )
    parser.add_argument(
        "--only-new",
        action="store_true",
        help=(
            "Skip the six property-depths already published at 0-5cm "
            "(phh2o, soc, nitrogen, bdod, cec, ocd)."
        ),
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the planned property-depth combinations and exit; no network, no files written.",
    )
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
    unknown_properties = set(arguments.properties) - {item.name for item in SOIL_PROPERTIES}
    if unknown_properties:
        print(f"unknown properties: {', '.join(sorted(unknown_properties))}", file=sys.stderr)
        return 2

    # `--depths all` spells out the default rather than adding a fourth depth, so `all` never
    # needs to be a member of DEPTHS itself.
    requested_depths = list(DEPTHS) if arguments.depths == ["all"] else arguments.depths
    selected_depths = [depth for depth in DEPTHS if depth in set(requested_depths)]
    unknown_depths = set(requested_depths) - set(DEPTHS)
    if unknown_depths:
        print(f"unknown depths: {', '.join(sorted(unknown_depths))}", file=sys.stderr)
        return 2

    # depth-major, matching the historical build order.
    combos = [(depth, soil_property) for depth in selected_depths for soil_property in selected]
    if arguments.only_new:
        combos = [pair for pair in combos if not is_already_published(pair[1], pair[0])]

    if arguments.list:
        for depth, soil_property in combos:
            print(f"{soil_property.name} {depth}")
        skip_note = " (--only-new: 6 published 0-5cm artifacts skipped)" if arguments.only_new else ""
        print(f"[soil-cog] {len(combos)} property-depths planned{skip_note}")
        return 0

    print(
        f"[soil-cog] bbox={bbox} properties={[item.name for item in selected]} "
        f"depths={selected_depths}" + (" only-new" if arguments.only_new else "")
    )

    capture_manifest = load_capture_manifest(arguments.capture_dir) if arguments.capture_dir else None

    entries = []
    with Env(**GDAL_REMOTE_OPTIONS):
        for depth, soil_property in combos:
            entries.append(
                build_property(
                    soil_property,
                    depth,
                    bbox,
                    arguments.output_dir,
                    arguments.overwrite,
                    arguments.capture_dir,
                    capture_manifest,
                )
            )

    manifest_path = arguments.output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "generator": "scripts/raster/build-soil-cogs.py",
                "sourceRelease": SOILGRIDS_RELEASE,
                "license": SOILGRIDS_LICENSE,
                "bbox": list(bbox),
                # MAX Last-Modified over every probed VRT -- the release's watermark, not a
                # per-file gate. See `release_watermark` and AGENTS.md §watermark.
                "releaseWatermark": release_watermark(entries),
                "artifacts": entries,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"[soil-cog] manifest written to {manifest_path} "
        f"({len(entries)} artifacts, watermark={release_watermark(entries)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
