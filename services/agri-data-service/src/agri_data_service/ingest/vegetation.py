"""Sentinel-2 L2A NDVI: STAC scene selection, windowed Cloud-Optimized GeoTIFF reads, and the fixed sample grid."""

from __future__ import annotations

import asyncio
import json
import math
import os
import struct
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final
from urllib.parse import urlencode

import httpx
import numpy as np
import structlog

from agri_data_service.ingest.geometry import GridCell
from agri_data_service.ingest.http import (
    UpstreamBounds,
    UpstreamError,
    UpstreamHttpError,
    UpstreamPayloadError,
    UpstreamTimeoutError,
    UpstreamTransportError,
    fetch_bounded_json,
)
from agri_data_service.ingest.identity import (
    FeatureIdentity,
    MissingNativeKeyError,
    format_coordinate,
    format_javascript_timestamp,
)
from agri_data_service.ingest.policy import BBOX_ORDINATE_COUNT, parse_bbox
from agri_data_service.ingest.source import FreshnessRule, FunctionSource, HistoryCapability
from agri_data_service.ingest.writer import FeatureWrite

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from numpy.typing import NDArray

    from agri_data_service.ingest.source import FetchRequest, HistoryWindow, UpstreamRecord

logger = structlog.get_logger()

VEGETATION_SOURCE: Final = "sentinel2-ndvi"
VEGETATION_CHANNEL: Final = "layer:vegetation"
VEGETATION_PROPERTY_SOURCE: Final = "Sentinel-2 L2A"
VEGETATION_LAYER_VARIABLE: Final = "VEGETATION_LAYER_ID"
DEFAULT_VEGETATION_LAYER_NAME: Final = "vegetation"

# The producer token every key this module mints is namespaced by. It belongs beside FIRMS_PRODUCER and
# friends in identity.py; that module is owned by another lane, so it is declared here for now.
SENTINEL2_NDVI_PRODUCER: Final = "sentinel2-ndvi"

# The grid definition is FIXED, and its spacing is spelled inside its own name. A cell's identity is only
# stable because the grid never moves, so re-defining the spacing must produce a DIFFERENT grid rather than
# silently re-cutting these cells. The grid is anchored on the global origin, never on INGEST_BBOX, so a
# cell keeps its key no matter which bbox an operator ingests.
NDVI_GRID_NAME: Final = "sentinel2-ndvi-0p25deg"
NDVI_CELL_SPACING_DEGREES: Final = 0.25
# The meridional extent of a 0.25 degree cell, which is the dimension that does not vary with latitude.
NDVI_CELL_RESOLUTION_METRES: Final = 27_830

EARTH_SEARCH_URL: Final = "https://earth-search.aws.element84.com/v1/search"
SENTINEL2_L2A_COLLECTION: Final = "sentinel-2-l2a"
# Declared by the collection itself at earth-search.aws.element84.com/v1/collections/sentinel-2-l2a.
SENTINEL2_L2A_EARLIEST_OBSERVATION: Final = datetime(2015, 6, 27, 10, 25, 31, 456_000, tzinfo=UTC)

RED_ASSET_NAME: Final = "red"
NEAR_INFRARED_ASSET_NAME: Final = "nir"
SCENE_CLASSIFICATION_ASSET_NAME: Final = "scl"

STAC_BOUNDS: Final = UpstreamBounds(max_bytes=24 * 1024 * 1024, timeout_seconds=60.0)
COG_BOUNDS: Final = UpstreamBounds(max_bytes=8 * 1024 * 1024, timeout_seconds=30.0)

CURRENT_OBSERVATION_WINDOW: Final = timedelta(days=45)
MAX_SCENE_CLOUD_COVER_PERCENT: Final = 20
SEARCH_PAGE_SIZE: Final = 100
# The catalogue is walked clearest-first and paging stops as soon as the grid is full, so these ceilings
# bound the worst case (a mostly-clouded window) rather than the ordinary one.
MAX_SEARCH_PAGES: Final = 12
MAX_SAMPLED_SCENES: Final = 1_200

# A 5x5 lattice inside each cell, so a cell's value is an area mean rather than one pixel's luck.
CELL_SUBSAMPLE_SIDE: Final = 5
MIN_VALID_SUBSAMPLES: Final = 8
TARGET_SAMPLE_RESOLUTION_METRES: Final = 160.0

# Sentinel-2 scene classification classes whose pixels carry usable surface reflectance: vegetation,
# not-vegetated, water and unclassified. Cloud, cirrus, snow, shadow, saturated and no-data are excluded.
SCENE_CLASSIFICATION_VALID_CLASSES: Final = frozenset({4, 5, 6, 7})

MIN_NDVI: Final = -1.0
MAX_NDVI: Final = 1.0
MIN_NDVI_DENOMINATOR: Final = 1e-9
NDVI_DECIMAL_PLACES: Final = 4
# Reflectance below this is physically impossible, so a plane that produces it is being scaled wrongly and
# must yield no rows at all rather than a plausible-looking wrong NDVI.
MIN_VALID_REFLECTANCE: Final = 0.0

# The pixels in the sentinel-cogs bucket are ALREADY harmonised, so the -0.1 that `raster:bands.offset`
# declares must not be applied a second time. Measured, not assumed: over pixels the scene classification
# calls vegetation, red digital numbers have the same distribution (p50 around 230-440) whether an item
# reports earthsearch:boa_offset_applied true (baseline 05.10) or false (baseline 04.00) -- if the shift
# were still pending on one of them its numbers would sit ~1000 higher. Re-applying the declared offset
# drives 88-97% of those vegetation pixels outside the physical NDVI range, while ignoring it puts them at
# a textbook 0.48-0.91. The scale IS applied; only the offset is refused, and MIN_VALID_REFLECTANCE guards
# the day that stops being true.
SENTINEL2_L2A_REFLECTANCE_OFFSET: Final = 0.0

HTTP_OK: Final = 200
HTTP_PARTIAL_CONTENT: Final = 206

TIFF_LITTLE_ENDIAN_MAGIC: Final = b"II"
TIFF_CLASSIC_VERSION: Final = 42
COG_HEADER_BYTES: Final = 32 * 1024
MAX_IMAGE_FILE_DIRECTORIES: Final = 24
MAX_OVERVIEW_PIXELS: Final = 4_000_000
DEFLATE_COMPRESSION: Final = 8
SINGLE_BAND: Final = 1
NO_PREDICTOR: Final = 1
HORIZONTAL_DIFFERENCING_PREDICTOR: Final = 2

TAG_IMAGE_WIDTH: Final = 256
TAG_IMAGE_LENGTH: Final = 257
TAG_BITS_PER_SAMPLE: Final = 258
TAG_COMPRESSION: Final = 259
TAG_SAMPLES_PER_PIXEL: Final = 277
TAG_PREDICTOR: Final = 317
TAG_TILE_WIDTH: Final = 322
TAG_TILE_LENGTH: Final = 323
TAG_TILE_OFFSETS: Final = 324
TAG_TILE_BYTE_COUNTS: Final = 325

TIFF_ENTRY_BYTES: Final = 12
TIFF_INLINE_PAYLOAD_BYTES: Final = 4
TIFF_NEXT_POINTER_BYTES: Final = 4
# Only the integer field types this reader needs; a float or ASCII tag is skipped rather than truncated.
TIFF_INTEGER_TYPE_SIZES: Final = {1: 1, 3: 2, 4: 4, 8: 2, 9: 4}
TIFF_INTEGER_TYPE_CODES: Final = {1: "B", 3: "H", 4: "I", 8: "h", 9: "i"}

SUPPORTED_BIT_DEPTHS: Final = {8: np.uint8, 16: np.uint16}

WGS84_SEMI_MAJOR_AXIS_METRES: Final = 6_378_137.0
WGS84_FLATTENING: Final = 1 / 298.257223563
UTM_SCALE_FACTOR: Final = 0.9996
UTM_FALSE_EASTING_METRES: Final = 500_000.0
UTM_FALSE_NORTHING_METRES: Final = 10_000_000.0
UTM_NORTHERN_EPSG_BASE: Final = 32_600
UTM_SOUTHERN_EPSG_BASE: Final = 32_700
MIN_UTM_ZONE: Final = 1
MAX_UTM_ZONE: Final = 60
UTM_ZONE_WIDTH_DEGREES: Final = 6
UTM_ZONE_CENTRAL_MERIDIAN_OFFSET: Final = 183

GEOTIFF_TRANSFORM_LENGTH: Final = 6
PROJECTION_SHAPE_LENGTH: Final = 2
CELL_CENTRE_OFFSET: Final = 0.5


class CloudOptimizedGeoTiffError(UpstreamPayloadError):
    """Raised when a remote GeoTIFF is not the tiled, deflated, single-band layout this reader supports."""


class SceneMetadataError(UpstreamPayloadError):
    """Raised when a STAC item omits the projection or scaling metadata a sample cannot be taken without."""


@dataclass(frozen=True, slots=True)
class NdviGridCell:
    """One fixed grid cell: the place a sampled NDVI value is stored against."""

    cell_key: str
    center_latitude: float
    center_longitude: float
    west: float
    south: float
    east: float
    north: float

    @property
    def ring(self) -> list[list[float]]:
        """The cell's own closed polygon ring, counter-clockwise, in WGS84 degrees."""
        return [
            [self.west, self.south],
            [self.east, self.south],
            [self.east, self.north],
            [self.west, self.north],
            [self.west, self.south],
        ]

    @property
    def geometry(self) -> dict[str, object]:
        """The cell's RFC 7946 polygon, which is both its stored geometry and its dimension shape."""
        return {"type": "Polygon", "coordinates": [self.ring]}

    def grid_cell(self) -> GridCell:
        """The dimension's view of this cell: its grid, its key, its resolution and its shape."""
        return GridCell(
            grid_name=NDVI_GRID_NAME,
            cell_key=self.cell_key,
            resolution_metres=NDVI_CELL_RESOLUTION_METRES,
            geojson=json.dumps(self.geometry, separators=(",", ":")),
        )


@dataclass(frozen=True, slots=True)
class SceneAsset:
    """One band of one scene: where its pixels live, how they are georeferenced, and how they scale."""

    url: str
    epsg: int
    origin_easting: float
    origin_northing: float
    pixel_size_metres: float
    width: int
    height: int
    scale: float
    offset: float
    nodata: float | None


@dataclass(frozen=True, slots=True)
class SentinelScene:
    """One Sentinel-2 L2A acquisition, with the three bands an NDVI sample needs."""

    item_id: str
    observed_at: datetime
    cloud_cover_percent: float
    west: float
    south: float
    east: float
    north: float
    red: SceneAsset
    near_infrared: SceneAsset
    scene_classification: SceneAsset

    def covers(self, latitude: float, longitude: float) -> bool:
        """True when a point falls inside the scene's declared footprint box."""
        return self.west <= longitude <= self.east and self.south <= latitude <= self.north


@dataclass(frozen=True, slots=True)
class OverviewPlane:
    """One decoded overview level, holding the physical value lookup for a projected coordinate."""

    values: NDArray[np.uint16]
    epsg: int
    origin_easting: float
    origin_northing: float
    pixel_size_metres: float
    scale: float
    offset: float
    nodata: float | None

    def raw_at(self, easting: float, northing: float) -> int | None:
        """Return the stored digital number under a projected coordinate, or None outside the plane."""
        # floor, never int(): truncation toward zero would fold a point just west or north of the raster
        # onto its first row or column instead of reporting it as outside.
        column = math.floor((easting - self.origin_easting) / self.pixel_size_metres)
        row = math.floor((self.origin_northing - northing) / self.pixel_size_metres)
        if not (0 <= row < self.values.shape[0] and 0 <= column < self.values.shape[1]):
            return None
        return int(self.values[row, column])

    def value_at(self, easting: float, northing: float) -> float | None:
        """Return the physical value under a projected coordinate, or None for nodata or an outside point."""
        raw = self.raw_at(easting, northing)
        if raw is None or (self.nodata is not None and float(raw) == self.nodata):
            return None
        return raw * self.scale + self.offset


@dataclass(frozen=True, slots=True)
class ScenePlanes:
    """The three decoded planes one scene contributes: red, near-infrared and scene classification."""

    red: OverviewPlane
    near_infrared: OverviewPlane
    scene_classification: OverviewPlane


@dataclass(frozen=True, slots=True)
class _RemoteTiff:
    """A remote GeoTIFF's cached header, plus the client that reads anything the cache does not hold."""

    client: httpx.AsyncClient
    url: str
    header: bytes

    async def bytes_at(self, offset: int, size: int) -> bytes:
        """Serve a byte span from the cached header, falling back to one more range request."""
        if offset >= 0 and offset + size <= len(self.header):
            return self.header[offset : offset + size]
        return await fetch_bounded_range(self.client, self.url, offset, size)


def resolve_vegetation_layer_name() -> str:
    """Read VEGETATION_LAYER_ID at call time so a cron environment change needs no restart."""
    return os.environ.get(VEGETATION_LAYER_VARIABLE, "").strip() or DEFAULT_VEGETATION_LAYER_NAME


async def fetch_bounded_range(
    client: httpx.AsyncClient,
    url: str,
    start: int,
    length: int,
    bounds: UpstreamBounds = COG_BOUNDS,
) -> bytes:
    """Fetch one byte range as raw bytes; http.py's text path would mangle a binary GeoTIFF body."""
    if start < 0 or length <= 0 or length > bounds.max_bytes:
        raise UpstreamPayloadError("requested byte range is outside the bounded read window")
    headers = {"Range": f"bytes={start}-{start + length - 1}"}
    try:
        response = await client.get(url, headers=headers, timeout=bounds.timeout_seconds)
    except httpx.TimeoutException as error:
        raise UpstreamTimeoutError("upstream request timed out") from error
    except httpx.HTTPError as error:
        raise UpstreamTransportError(f"upstream request failed ({error.__class__.__name__})") from error
    if response.status_code not in (HTTP_OK, HTTP_PARTIAL_CONTENT):
        raise UpstreamHttpError(response.status_code)
    body = response.content
    if len(body) > bounds.max_bytes:
        raise UpstreamPayloadError("upstream response exceeded the byte limit")
    return body


def ndvi_grid_cells(bbox: str, max_cells: int) -> tuple[list[NdviGridCell], bool]:
    """Enumerate the fixed grid cells whose centres fall inside a bbox, and whether the cap truncated them."""
    west, south, east, north = parse_bbox(bbox)
    spacing = NDVI_CELL_SPACING_DEGREES
    cells: list[NdviGridCell] = []
    for row in range(math.floor(south / spacing), math.ceil(north / spacing) + 1):
        cell_south = row * spacing
        center_latitude = cell_south + spacing * CELL_CENTRE_OFFSET
        if not south <= center_latitude <= north:
            continue
        for column in range(math.floor(west / spacing), math.ceil(east / spacing) + 1):
            cell_west = column * spacing
            center_longitude = cell_west + spacing * CELL_CENTRE_OFFSET
            if not west <= center_longitude <= east:
                continue
            cells.append(
                NdviGridCell(
                    cell_key=f"{format_coordinate(center_latitude)}:{format_coordinate(center_longitude)}",
                    center_latitude=center_latitude,
                    center_longitude=center_longitude,
                    west=cell_west,
                    south=cell_south,
                    east=cell_west + spacing,
                    north=cell_south + spacing,
                )
            )

    cells.sort(key=lambda cell: cell.cell_key)
    if len(cells) <= max_cells:
        return cells, False
    return cells[:max_cells], True


def cell_subsample_points(cell: NdviGridCell) -> list[tuple[float, float]]:
    """Return the lattice of latitude/longitude points whose valid values average into the cell's value."""
    step = NDVI_CELL_SPACING_DEGREES / CELL_SUBSAMPLE_SIDE
    return [
        (cell.south + (row + CELL_CENTRE_OFFSET) * step, cell.west + (column + CELL_CENTRE_OFFSET) * step)
        for row in range(CELL_SUBSAMPLE_SIDE)
        for column in range(CELL_SUBSAMPLE_SIDE)
    ]


def utm_zone_of(epsg: int) -> tuple[int, bool]:
    """Split a UTM EPSG code into its zone and hemisphere, refusing a code this reader cannot project into."""
    for base, northern in ((UTM_NORTHERN_EPSG_BASE, True), (UTM_SOUTHERN_EPSG_BASE, False)):
        zone = epsg - base
        if MIN_UTM_ZONE <= zone <= MAX_UTM_ZONE:
            return zone, northern
    raise SceneMetadataError("scene projection is not a WGS84 UTM zone")


def project_to_utm(latitude: float, longitude: float, epsg: int) -> tuple[float, float]:
    """Project a WGS84 coordinate into a UTM zone's easting and northing, in metres (Snyder's series)."""
    zone, northern = utm_zone_of(epsg)
    eccentricity_squared = WGS84_FLATTENING * (2 - WGS84_FLATTENING)
    second_eccentricity_squared = eccentricity_squared / (1 - eccentricity_squared)
    latitude_radians = math.radians(latitude)
    central_meridian = math.radians(zone * UTM_ZONE_WIDTH_DEGREES - UTM_ZONE_CENTRAL_MERIDIAN_OFFSET)

    sine = math.sin(latitude_radians)
    cosine = math.cos(latitude_radians)
    curvature_radius = WGS84_SEMI_MAJOR_AXIS_METRES / math.sqrt(1 - eccentricity_squared * sine * sine)
    tangent_squared = math.tan(latitude_radians) ** 2
    eccentricity_ratio = second_eccentricity_squared * cosine * cosine
    angular_distance = cosine * (math.radians(longitude) - central_meridian)
    meridian_arc = _meridian_arc(latitude_radians, eccentricity_squared)

    easting = (
        UTM_SCALE_FACTOR
        * curvature_radius
        * (
            angular_distance
            + (1 - tangent_squared + eccentricity_ratio) * angular_distance**3 / 6
            + (
                5
                - 18 * tangent_squared
                + tangent_squared**2
                + 72 * eccentricity_ratio
                - 58 * second_eccentricity_squared
            )
            * angular_distance**5
            / 120
        )
        + UTM_FALSE_EASTING_METRES
    )
    northing = UTM_SCALE_FACTOR * (
        meridian_arc
        + curvature_radius
        * math.tan(latitude_radians)
        * (
            angular_distance**2 / 2
            + (5 - tangent_squared + 9 * eccentricity_ratio + 4 * eccentricity_ratio**2) * angular_distance**4 / 24
            + (
                61
                - 58 * tangent_squared
                + tangent_squared**2
                + 600 * eccentricity_ratio
                - 330 * second_eccentricity_squared
            )
            * angular_distance**6
            / 720
        )
    )
    if not northern:
        northing += UTM_FALSE_NORTHING_METRES
    return easting, northing


def _meridian_arc(latitude_radians: float, eccentricity_squared: float) -> float:
    """Return the meridional arc length from the equator, the series Snyder's transverse Mercator needs."""
    return WGS84_SEMI_MAJOR_AXIS_METRES * (
        (1 - eccentricity_squared / 4 - 3 * eccentricity_squared**2 / 64 - 5 * eccentricity_squared**3 / 256)
        * latitude_radians
        - (3 * eccentricity_squared / 8 + 3 * eccentricity_squared**2 / 32 + 45 * eccentricity_squared**3 / 1024)
        * math.sin(2 * latitude_radians)
        + (15 * eccentricity_squared**2 / 256 + 45 * eccentricity_squared**3 / 1024) * math.sin(4 * latitude_radians)
        - (35 * eccentricity_squared**3 / 3072) * math.sin(6 * latitude_radians)
    )


def _required_number(payload: Mapping[str, object], field_name: str) -> float:
    """Return one finite numeric metadata field, refusing an absent, boolean or non-finite value."""
    value = payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value)):
        raise SceneMetadataError(f"scene metadata field {field_name} is missing or unusable")
    return float(value)


def _optional_number(payload: Mapping[str, object], field_name: str, fallback: float) -> float:
    """Return one numeric metadata field, or a documented physical default when the item omits it."""
    if payload.get(field_name) is None:
        return fallback
    return _required_number(payload, field_name)


def _numeric_sequence(value: object, length: int, field_name: str) -> list[float]:
    """Return a fixed-length numeric metadata array, refusing a mistyped or wrong-length one."""
    if not isinstance(value, list) or len(value) != length:
        raise SceneMetadataError(f"scene metadata field {field_name} is missing or unusable")
    numbers: list[float] = []
    for entry in value:
        if isinstance(entry, bool) or not isinstance(entry, int | float) or not math.isfinite(float(entry)):
            raise SceneMetadataError(f"scene metadata field {field_name} is missing or unusable")
        numbers.append(float(entry))
    return numbers


def parse_scene_asset(asset: Mapping[str, object], epsg: int) -> SceneAsset:
    """Read one STAC asset's href, affine transform, shape and reflectance scaling into a typed asset."""
    href = asset.get("href")
    if not isinstance(href, str) or not href.startswith("https://"):
        raise SceneMetadataError("scene asset does not publish an https href")

    transform = _numeric_sequence(asset.get("proj:transform"), GEOTIFF_TRANSFORM_LENGTH, "proj:transform")
    shape = _numeric_sequence(asset.get("proj:shape"), PROJECTION_SHAPE_LENGTH, "proj:shape")
    if transform[0] <= 0 or transform[4] >= 0:
        raise SceneMetadataError("scene asset transform is not a north-up square pixel grid")

    bands = asset.get("raster:bands")
    if not isinstance(bands, list) or not bands or not isinstance(bands[0], dict):
        raise SceneMetadataError("scene asset does not publish raster band scaling")
    band: Mapping[str, object] = bands[0]
    nodata = band.get("nodata")
    return SceneAsset(
        url=href,
        epsg=epsg,
        origin_easting=transform[2],
        origin_northing=transform[5],
        pixel_size_metres=transform[0],
        width=int(shape[1]),
        height=int(shape[0]),
        # A band that publishes no scaling is already in physical units; the L2A bands publish both.
        scale=_optional_number(band, "scale", 1.0),
        offset=SENTINEL2_L2A_REFLECTANCE_OFFSET,
        nodata=None if nodata is None else _required_number(band, "nodata"),
    )


def parse_scene(item: Mapping[str, object]) -> SentinelScene:
    """Read one STAC item into the typed scene an NDVI sample is taken from."""
    item_id = item.get("id")
    if not isinstance(item_id, str) or not item_id.strip():
        raise SceneMetadataError("scene item carries no identifier")
    properties = item.get("properties")
    assets = item.get("assets")
    if not isinstance(properties, dict) or not isinstance(assets, dict):
        raise SceneMetadataError("scene item carries no properties or assets")

    observed_at = _scene_observation_time(properties)
    epsg = int(_required_number(properties, "proj:epsg"))
    bbox = _numeric_sequence(item.get("bbox"), BBOX_ORDINATE_COUNT, "bbox")
    band_assets: dict[str, SceneAsset] = {}
    for name in (RED_ASSET_NAME, NEAR_INFRARED_ASSET_NAME, SCENE_CLASSIFICATION_ASSET_NAME):
        asset = assets.get(name)
        if not isinstance(asset, dict):
            raise SceneMetadataError(f"scene item is missing its {name} asset")
        band_assets[name] = parse_scene_asset(asset, epsg)

    return SentinelScene(
        item_id=item_id,
        observed_at=observed_at,
        cloud_cover_percent=_required_number(properties, "eo:cloud_cover"),
        west=bbox[0],
        south=bbox[1],
        east=bbox[2],
        north=bbox[3],
        red=band_assets[RED_ASSET_NAME],
        near_infrared=band_assets[NEAR_INFRARED_ASSET_NAME],
        scene_classification=band_assets[SCENE_CLASSIFICATION_ASSET_NAME],
    )


def _scene_observation_time(properties: Mapping[str, object]) -> datetime:
    """Return the sensor acquisition instant the scene itself publishes; never a run clock."""
    observed_at_text = properties.get("datetime")
    if not isinstance(observed_at_text, str) or not observed_at_text.strip():
        raise SceneMetadataError("scene item carries no acquisition datetime")
    return parse_scene_timestamp(observed_at_text)


def parse_scene_timestamp(value: str) -> datetime:
    """Parse an upstream ISO-8601 acquisition instant into UTC, refusing an unzoned one."""
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as error:
        raise SceneMetadataError("scene acquisition datetime is not an ISO-8601 instant") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SceneMetadataError("scene acquisition datetime carries no UTC offset")
    return parsed.astimezone(UTC)


def scene_search_url(bbox: str, start: datetime, end: datetime) -> str:
    """Build the Earth Search item-search URL: this bbox, this window, clearest scenes first."""
    query = urlencode(
        {
            "collections": SENTINEL2_L2A_COLLECTION,
            "bbox": bbox,
            "datetime": f"{format_javascript_timestamp(start)}/{format_javascript_timestamp(end)}",
            "limit": SEARCH_PAGE_SIZE,
            "query": json.dumps({"eo:cloud_cover": {"lt": MAX_SCENE_CLOUD_COVER_PERCENT}}, separators=(",", ":")),
            "sortby": "+properties.eo:cloud_cover",
        }
    )
    return f"{EARTH_SEARCH_URL}?{query}"


def next_page_url(payload: Mapping[str, object]) -> str | None:
    """Return the href of a STAC `next` link, or None when the catalogue served the last page."""
    links = payload.get("links")
    if not isinstance(links, list):
        return None
    for link in links:
        if not isinstance(link, dict) or link.get("rel") != "next":
            continue
        href = link.get("href")
        if isinstance(href, str) and href.startswith("https://"):
            return href
    return None


async def search_sentinel2_scenes(
    client: httpx.AsyncClient,
    bbox: str,
    start: datetime,
    end: datetime,
) -> list[SentinelScene]:
    """Page the Earth Search catalogue for clear scenes over a bbox and window, clearest first."""
    url: str | None = scene_search_url(bbox, start, end)
    scenes: list[SentinelScene] = []
    for _ in range(MAX_SEARCH_PAGES):
        if url is None or len(scenes) >= MAX_SAMPLED_SCENES:
            break
        page, url = await fetch_scene_page(client, url)
        scenes.extend(page)
    return scenes[:MAX_SAMPLED_SCENES]


async def fetch_scene_page(client: httpx.AsyncClient, url: str) -> tuple[list[SentinelScene], str | None]:
    """Read one catalogue page into typed scenes, plus the URL of the page after it."""
    payload = await fetch_bounded_json(client, url, STAC_BOUNDS)
    if not isinstance(payload, dict):
        raise UpstreamPayloadError("Earth Search returned an invalid item collection")
    features = payload.get("features")
    if not isinstance(features, list):
        raise UpstreamPayloadError("Earth Search returned an invalid item collection")

    scenes: list[SentinelScene] = []
    unusable_items = 0
    for feature in features:
        if not isinstance(feature, dict):
            unusable_items += 1
            continue
        try:
            scenes.append(parse_scene(feature))
        except SceneMetadataError:
            unusable_items += 1
    if unusable_items:
        logger.info("sentinel2_items_unusable", items=unusable_items, scenes=len(scenes))
    return scenes, next_page_url(payload)


def _tiff_integer_values(raw: bytes, field_type: int, count: int) -> tuple[int, ...]:
    """Decode one already-resolved integer TIFF field payload into its values."""
    size = TIFF_INTEGER_TYPE_SIZES[field_type] * count
    if len(raw) < size:
        raise CloudOptimizedGeoTiffError("GeoTIFF field payload was truncated")
    return struct.unpack("<" + TIFF_INTEGER_TYPE_CODES[field_type] * count, raw[:size])


async def _read_image_file_directory(tiff: _RemoteTiff, offset: int) -> tuple[dict[int, tuple[int, ...]], int]:
    """Read one image file directory's integer-valued tags and the offset of the next directory."""
    entry_count = struct.unpack("<H", await tiff.bytes_at(offset, 2))[0]
    body_bytes = entry_count * TIFF_ENTRY_BYTES + TIFF_NEXT_POINTER_BYTES
    body = await tiff.bytes_at(offset + 2, body_bytes)
    if len(body) < body_bytes:
        raise CloudOptimizedGeoTiffError("GeoTIFF directory was truncated")

    entries: dict[int, tuple[int, ...]] = {}
    for index in range(entry_count):
        entry = body[index * TIFF_ENTRY_BYTES : (index + 1) * TIFF_ENTRY_BYTES]
        tag, field_type, value_count = struct.unpack("<HHI", entry[:8])
        if field_type not in TIFF_INTEGER_TYPE_SIZES:
            continue
        size = TIFF_INTEGER_TYPE_SIZES[field_type] * value_count
        payload = entry[8:12]
        raw = (
            payload if size <= TIFF_INLINE_PAYLOAD_BYTES else await tiff.bytes_at(struct.unpack("<I", payload)[0], size)
        )
        entries[tag] = _tiff_integer_values(raw, field_type, value_count)
    next_offset: int = struct.unpack("<I", body[entry_count * TIFF_ENTRY_BYTES :][:TIFF_NEXT_POINTER_BYTES])[0]
    return entries, next_offset


async def read_image_file_directories(client: httpx.AsyncClient, url: str) -> list[dict[int, tuple[int, ...]]]:
    """Read a remote GeoTIFF's full-resolution directory and its overview directories from one header fetch."""
    header = await fetch_bounded_range(client, url, 0, COG_HEADER_BYTES)
    if header[:2] != TIFF_LITTLE_ENDIAN_MAGIC or struct.unpack("<H", header[2:4])[0] != TIFF_CLASSIC_VERSION:
        raise CloudOptimizedGeoTiffError("upstream asset is not a little-endian classic GeoTIFF")

    tiff = _RemoteTiff(client=client, url=url, header=header)
    directories: list[dict[int, tuple[int, ...]]] = []
    offset: int = struct.unpack("<I", header[4:8])[0]
    while offset and len(directories) < MAX_IMAGE_FILE_DIRECTORIES:
        entries, offset = await _read_image_file_directory(tiff, offset)
        directories.append(entries)
    if not directories:
        raise CloudOptimizedGeoTiffError("GeoTIFF published no image directories")
    return directories


def choose_overview_level(
    directories: Sequence[Mapping[int, tuple[int, ...]]],
    full_width: int,
    full_pixel_size_metres: float,
    target_metres: float,
) -> int:
    """Pick the coarsest overview still at least as fine as the target, so a coarse grid reads few bytes."""
    chosen = 0
    chosen_pixel_size = full_pixel_size_metres
    for level, directory in enumerate(directories):
        width = directory.get(TAG_IMAGE_WIDTH, (0,))[0]
        if width <= 0:
            continue
        pixel_size = full_pixel_size_metres * full_width / width
        if chosen_pixel_size <= pixel_size <= target_metres:
            chosen, chosen_pixel_size = level, pixel_size
    return chosen


@dataclass(frozen=True, slots=True)
class _TiledLayout:
    """The decodable layout of one image file directory: its size, its tiling and its predictor."""

    width: int
    height: int
    tile_width: int
    tile_height: int
    predictor: int
    bit_depth: int


def _tiled_layout(directory: Mapping[int, tuple[int, ...]]) -> _TiledLayout:
    """Read a directory's layout, refusing anything outside the tiled deflate single-band form we decode."""
    layout = _TiledLayout(
        width=directory.get(TAG_IMAGE_WIDTH, (0,))[0],
        height=directory.get(TAG_IMAGE_LENGTH, (0,))[0],
        tile_width=directory.get(TAG_TILE_WIDTH, (0,))[0],
        tile_height=directory.get(TAG_TILE_LENGTH, (0,))[0],
        predictor=directory.get(TAG_PREDICTOR, (NO_PREDICTOR,))[0],
        bit_depth=directory.get(TAG_BITS_PER_SAMPLE, (0,))[0],
    )
    if directory.get(TAG_COMPRESSION, (0,))[0] != DEFLATE_COMPRESSION:
        raise CloudOptimizedGeoTiffError("GeoTIFF overview is not deflate compressed")
    if directory.get(TAG_SAMPLES_PER_PIXEL, (SINGLE_BAND,))[0] != SINGLE_BAND:
        raise CloudOptimizedGeoTiffError("GeoTIFF overview is not a single-band image")
    if layout.predictor not in (NO_PREDICTOR, HORIZONTAL_DIFFERENCING_PREDICTOR):
        raise CloudOptimizedGeoTiffError("GeoTIFF overview uses an unsupported predictor")
    if layout.bit_depth not in SUPPORTED_BIT_DEPTHS:
        raise CloudOptimizedGeoTiffError("GeoTIFF overview is not an 8 or 16 bit integer band")
    if min(layout.width, layout.height, layout.tile_width, layout.tile_height) <= 0:
        raise CloudOptimizedGeoTiffError("GeoTIFF overview is not tiled")
    if layout.width * layout.height > MAX_OVERVIEW_PIXELS:
        raise CloudOptimizedGeoTiffError("GeoTIFF overview is larger than the bounded read window")
    return layout


def _decode_tile(raw: bytes, layout: _TiledLayout) -> NDArray[np.uint16]:
    """Inflate one tile and undo horizontal differencing in its native width, so an 8 bit band wraps as TIFF wants."""
    flat = np.frombuffer(zlib.decompress(raw), dtype=SUPPORTED_BIT_DEPTHS[layout.bit_depth])
    if flat.size != layout.tile_height * layout.tile_width:
        raise CloudOptimizedGeoTiffError("GeoTIFF tile did not inflate to its declared size")
    tile = flat.reshape(layout.tile_height, layout.tile_width)
    if layout.predictor == HORIZONTAL_DIFFERENCING_PREDICTOR:
        tile = np.cumsum(tile, axis=1, dtype=tile.dtype)
    return tile.astype(np.uint16)


async def read_overview_plane(
    client: httpx.AsyncClient,
    asset: SceneAsset,
    target_metres: float = TARGET_SAMPLE_RESOLUTION_METRES,
) -> OverviewPlane:
    """Read the overview level closest to a target ground resolution and decode it into one array."""
    directories = await read_image_file_directories(client, asset.url)
    level = choose_overview_level(directories, asset.width, asset.pixel_size_metres, target_metres)
    layout = _tiled_layout(directories[level])

    offsets = directories[level].get(TAG_TILE_OFFSETS, ())
    byte_counts = directories[level].get(TAG_TILE_BYTE_COUNTS, ())
    tiles_across = math.ceil(layout.width / layout.tile_width)
    tiles_down = math.ceil(layout.height / layout.tile_height)
    if len(offsets) != tiles_across * tiles_down or len(byte_counts) != len(offsets):
        raise CloudOptimizedGeoTiffError("GeoTIFF overview tile index does not match its tile grid")

    fetched = await asyncio.gather(
        *(
            fetch_bounded_range(client, asset.url, offset, count)
            for offset, count in zip(offsets, byte_counts, strict=True)
        )
    )
    plane = np.zeros((tiles_down * layout.tile_height, tiles_across * layout.tile_width), dtype=np.uint16)
    for index, raw in enumerate(fetched):
        row, column = divmod(index, tiles_across)
        plane[
            row * layout.tile_height : (row + 1) * layout.tile_height,
            column * layout.tile_width : (column + 1) * layout.tile_width,
        ] = _decode_tile(raw, layout)

    return OverviewPlane(
        values=plane[: layout.height, : layout.width],
        epsg=asset.epsg,
        origin_easting=asset.origin_easting,
        origin_northing=asset.origin_northing,
        pixel_size_metres=asset.pixel_size_metres * asset.width / layout.width,
        scale=asset.scale,
        offset=asset.offset,
        nodata=asset.nodata,
    )


async def read_scene_planes(client: httpx.AsyncClient, scene: SentinelScene) -> ScenePlanes:
    """Read the red, near-infrared and scene-classification overviews one scene contributes."""
    red, near_infrared, classification = await asyncio.gather(
        read_overview_plane(client, scene.red),
        read_overview_plane(client, scene.near_infrared),
        read_overview_plane(client, scene.scene_classification),
    )
    return ScenePlanes(red=red, near_infrared=near_infrared, scene_classification=classification)


def sample_cell_ndvi(cell: NdviGridCell, planes: ScenePlanes) -> tuple[float, int] | None:
    """Average NDVI over a cell's cloud-free lattice points, or None when too few of them are usable."""
    values: list[float] = []
    for latitude, longitude in cell_subsample_points(cell):
        easting, northing = project_to_utm(latitude, longitude, planes.red.epsg)
        classification = planes.scene_classification.raw_at(easting, northing)
        if classification not in SCENE_CLASSIFICATION_VALID_CLASSES:
            continue
        red = planes.red.value_at(easting, northing)
        near_infrared = planes.near_infrared.value_at(easting, northing)
        if red is None or near_infrared is None:
            continue
        if red <= MIN_VALID_REFLECTANCE or near_infrared <= MIN_VALID_REFLECTANCE:
            continue
        if abs(near_infrared + red) < MIN_NDVI_DENOMINATOR:
            continue
        ndvi = (near_infrared - red) / (near_infrared + red)
        if math.isfinite(ndvi) and MIN_NDVI <= ndvi <= MAX_NDVI:
            values.append(ndvi)
    if len(values) < MIN_VALID_SUBSAMPLES:
        return None
    return sum(values) / len(values), len(values)


def ndvi_grid_record(cell: NdviGridCell, scene: SentinelScene, ndvi: float, sample_count: int) -> dict[str, object]:
    """Build the upstream record one sampled cell contributes, dated by the scene that supplied its pixels."""
    return {
        "cellKey": cell.cell_key,
        "gridName": NDVI_GRID_NAME,
        "centerLatitude": cell.center_latitude,
        "centerLongitude": cell.center_longitude,
        "west": cell.west,
        "south": cell.south,
        "east": cell.east,
        "north": cell.north,
        "ndvi": round(ndvi, NDVI_DECIMAL_PLACES),
        "observedAt": format_javascript_timestamp(scene.observed_at),
        "sceneId": scene.item_id,
        "cloudCover": scene.cloud_cover_percent,
        "sampleCount": sample_count,
    }


async def fill_cells_from_scene(
    client: httpx.AsyncClient,
    scene: SentinelScene,
    pending: dict[str, NdviGridCell],
) -> list[dict[str, object]]:
    """Sample every still-unfilled cell this scene's footprint covers, removing the ones it filled."""
    candidates = [cell for cell in pending.values() if scene.covers(cell.center_latitude, cell.center_longitude)]
    if not candidates:
        return []
    planes = await read_scene_planes(client, scene)
    records: list[dict[str, object]] = []
    for cell in candidates:
        sampled = sample_cell_ndvi(cell, planes)
        if sampled is None:
            continue
        ndvi, sample_count = sampled
        records.append(ndvi_grid_record(cell, scene, ndvi, sample_count))
        pending.pop(cell.cell_key, None)
    return records


@dataclass(frozen=True, slots=True)
class GridSampleWindow:
    """One bounded sampling pass: the area, the acquisition window, and the cell ceiling."""

    bbox: str
    start: datetime
    end: datetime
    max_cells: int


@dataclass(frozen=True, slots=True)
class GridSampleOutcome:
    """What one sampling pass produced: the records, the cells it asked for, and whether the cap truncated them."""

    records: list[dict[str, object]]
    cells_requested: int
    truncated: bool


async def collect_ndvi_grid_records(client: httpx.AsyncClient, window: GridSampleWindow) -> GridSampleOutcome:
    """Fill the grid greedily from the clearest scenes first, one scene's failure never discarding the rest.

    Paging and filling are interleaved on purpose. The catalogue is sorted clearest-first, so a later page
    is only worth downloading while cells are still empty, and a scene whose footprint holds no unfilled
    cell costs one bbox test rather than three raster reads.
    """
    cells, truncated = ndvi_grid_cells(window.bbox, window.max_cells)
    pending = {cell.cell_key: cell for cell in cells}
    url: str | None = scene_search_url(window.bbox, window.start, window.end)

    records: list[dict[str, object]] = []
    scenes_considered = 0
    unreadable_scenes = 0
    for _ in range(MAX_SEARCH_PAGES):
        if url is None or not pending or scenes_considered >= MAX_SAMPLED_SCENES:
            break
        page, url = await fetch_scene_page(client, url)
        for scene in page:
            if not pending or scenes_considered >= MAX_SAMPLED_SCENES:
                break
            scenes_considered += 1
            try:
                records.extend(await fill_cells_from_scene(client, scene, pending))
            except UpstreamError:
                # One unreadable scene must not discard the cells every other scene can still fill.
                unreadable_scenes += 1

    logger.info(
        "sentinel2_ndvi_grid_sampled",
        cells=len(cells),
        filled=len(records),
        unfilled=len(pending),
        scenes=scenes_considered,
        unreadable_scenes=unreadable_scenes,
    )
    return GridSampleOutcome(records=records, cells_requested=len(cells), truncated=truncated)


def build_ndvi_identity(record: Mapping[str, object]) -> FeatureIdentity:
    """Key one sampled cell by its fixed grid centre and the acquisition instant of the scene that filled it.

    This builder and SENTINEL2_NDVI_PRODUCER belong in identity.py beside build_weather_observation_identity;
    that module is owned by another lane, so the key is assembled here through identity.py's own
    FeatureIdentity, format_coordinate and validation rather than by a second key-building implementation.
    """
    cell_key = record.get("cellKey")
    observed_at_text = record.get("observedAt")
    if not isinstance(cell_key, str) or not cell_key.strip():
        raise MissingNativeKeyError("cellKey is required and must not be blank")
    if not isinstance(observed_at_text, str) or not observed_at_text.strip():
        raise MissingNativeKeyError("observedAt is required and must not be blank")
    return FeatureIdentity(
        producer=SENTINEL2_NDVI_PRODUCER,
        producer_local_id=f"{cell_key}:{observed_at_text}",
        observed_at=parse_scene_timestamp(observed_at_text),
    )


def cell_of_record(record: Mapping[str, object]) -> NdviGridCell:
    """Rebuild the grid cell a record was sampled on, so its polygon is written from its own bounds."""
    cell_key = record.get("cellKey")
    if not isinstance(cell_key, str) or not cell_key.strip():
        raise MissingNativeKeyError("cellKey is required and must not be blank")
    return NdviGridCell(
        cell_key=cell_key,
        center_latitude=_required_number(record, "centerLatitude"),
        center_longitude=_required_number(record, "centerLongitude"),
        west=_required_number(record, "west"),
        south=_required_number(record, "south"),
        east=_required_number(record, "east"),
        north=_required_number(record, "north"),
    )


def build_ndvi_write(record: Mapping[str, object], layer_name: str) -> FeatureWrite | None:
    """Build one sampled cell's write, returning None when the record cannot be honestly keyed."""
    try:
        identity = build_ndvi_identity(record)
        cell = cell_of_record(record)
        ndvi = _required_number(record, "ndvi")
        sample_count = int(_required_number(record, "sampleCount"))
    except (MissingNativeKeyError, SceneMetadataError, ValueError):
        return None
    if not MIN_NDVI <= ndvi <= MAX_NDVI or sample_count < MIN_VALID_SUBSAMPLES:
        return None
    return FeatureWrite(
        layer_reference=layer_name,
        identity=identity,
        properties={
            "ndvi": ndvi,
            "observedAt": record.get("observedAt"),
            "sceneId": record.get("sceneId"),
            "cloudCover": record.get("cloudCover"),
            "sampleCount": sample_count,
            "gridName": NDVI_GRID_NAME,
            "cellKey": cell.cell_key,
            "resolutionMetres": NDVI_CELL_RESOLUTION_METRES,
            "source": VEGETATION_PROPERTY_SOURCE,
            "geometry": cell.geometry,
        },
        channel=VEGETATION_CHANNEL,
        grid_cell=cell.grid_cell(),
    )


def _require_client(request: FetchRequest) -> httpx.AsyncClient:
    """Return the caller's upstream client; sampling a remote raster cannot open one per byte range."""
    if request.client is None:
        raise ValueError("Sentinel-2 NDVI sampling requires an upstream client on the fetch request")
    return request.client


async def fetch_current_ndvi_records(request: FetchRequest) -> Sequence[UpstreamRecord]:
    """Fetch the current window: the clearest Sentinel-2 scenes of the most recent observation window."""
    end = request.now if request.now is not None else datetime.now(UTC)
    outcome = await collect_ndvi_grid_records(
        _require_client(request),
        GridSampleWindow(
            bbox=request.bbox,
            start=end - CURRENT_OBSERVATION_WINDOW,
            end=end,
            max_cells=request.max_records,
        ),
    )
    return outcome.records


async def fetch_history_ndvi_records(request: FetchRequest, window: HistoryWindow) -> Sequence[UpstreamRecord]:
    """Fetch one past window from the same archive; the catalogue serves any past date the collection covers."""
    outcome = await collect_ndvi_grid_records(
        _require_client(request),
        GridSampleWindow(
            bbox=request.bbox,
            start=window.start,
            end=window.end,
            max_cells=request.max_records,
        ),
    )
    return outcome.records


def build_vegetation_write(record: UpstreamRecord, _request: FetchRequest) -> FeatureWrite | None:
    """Map one sampled cell to a write, resolving the target layer at call time."""
    return build_ndvi_write(record, resolve_vegetation_layer_name())


def build_vegetation_source() -> FunctionSource:
    """Compose this module's callables into the IngestionSource contract a runner or backfill consumes."""
    return FunctionSource(
        source_name=VEGETATION_SOURCE,
        producer=SENTINEL2_NDVI_PRODUCER,
        channel=VEGETATION_CHANNEL,
        # Every record is dated by its own scene and the search window already bounds how old that can be,
        # so an extra age rule would only reject the backfill's own honest history.
        freshness=FreshnessRule(max_observation_age=None, accepts_undated_records=False),
        resolve_layer_reference=resolve_vegetation_layer_name,
        fetch_current_records=fetch_current_ndvi_records,
        build_feature_write=build_vegetation_write,
        history=HistoryCapability(supported=True, earliest=SENTINEL2_L2A_EARLIEST_OBSERVATION),
        fetch_history_records=fetch_history_ndvi_records,
        shape="grid_cell",
    )
