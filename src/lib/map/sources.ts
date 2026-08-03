import type { SourceSpecification } from "@maplibre/maplibre-gl-style-spec";

const TERRAIN_URL =
  process.env.NEXT_PUBLIC_TERRAIN_URL ||
  "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";

// Project-controlled R2 archive (Pacific Northwest pilot extract). Overridable
// per-environment with NEXT_PUBLIC_PMTILES_URL. See infra/tiles/AGENTS.md.
const DEFAULT_PMTILES_ARCHIVE_URL =
  "https://tiles.aevani.com/pnw-2026-08-02.pmtiles";

const PMTILES_ARCHIVE_URL =
  process.env.NEXT_PUBLIC_PMTILES_URL || DEFAULT_PMTILES_ARCHIVE_URL;

// Never derive this from MARTIN_URL: Railway private-network domains cannot be
// resolved by browsers. Railway/public CDN domains belong in this client-safe var.
const DYNAMIC_TILES_URL =
  process.env.NEXT_PUBLIC_DYNAMIC_TILES_URL || "http://localhost:3100";

const DYNAMIC_TILE_SOURCE_IDS = [
  "fire_risk_tiles",
  "sensor_tiles",
  "intervention_tiles",
  "building_tiles",
  "osm_roads",
  "osm_waterways",
] as const;

const SATELLITE_URL =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

function stripTrailingSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

function toPmtilesProtocolUrl(archiveUrl: string): string {
  return archiveUrl.startsWith("pmtiles://")
    ? archiveUrl
    : `pmtiles://${archiveUrl}`;
}

export function createPmtilesSource(archiveUrl: string): SourceSpecification {
  return {
    type: "vector",
    url: toPmtilesProtocolUrl(archiveUrl),
    maxzoom: 15,
    attribution: '&copy; <a href="https://openstreetmap.org">OpenStreetMap</a>',
  };
}

export function createMartinDynamicSource(baseUrl: string): SourceSpecification {
  return {
    type: "vector",
    // Martin's TileJSON endpoint keeps client tile paths aligned with its catalog.
    url: `${stripTrailingSlash(baseUrl)}/${DYNAMIC_TILE_SOURCE_IDS.join(",")}`,
    minzoom: 0,
    maxzoom: 22,
  };
}

export function getSources(
  dynamicTilesUrl: string = DYNAMIC_TILES_URL,
  pmtilesArchiveUrl: string = PMTILES_ARCHIVE_URL
): Record<string, SourceSpecification> {
  return {
    protomaps: createPmtilesSource(pmtilesArchiveUrl),
    "martin-dynamic": createMartinDynamicSource(dynamicTilesUrl),
    "terrain-dem": terrainSource,
    satellite: {
      type: "raster",
      tiles: [SATELLITE_URL],
      tileSize: 256,
      attribution: "&copy; Esri, Maxar, Earthstar Geographics",
      maxzoom: 19,
    },
    "ndvi-overlay": {
      type: "raster",
      tiles: [""],
      tileSize: 256,
      attribution: "NASA GIBS / Copernicus",
    },
    "ndwi-overlay": {
      type: "raster",
      tiles: [""],
      tileSize: 256,
      attribution: "NASA GIBS",
    },
  };
}

export const terrainSource: SourceSpecification = {
  type: "raster-dem",
  tiles: [TERRAIN_URL],
  tileSize: 256,
  encoding: "terrarium",
};

export const pmtilesSource = createPmtilesSource(PMTILES_ARCHIVE_URL);

export const martinDynamicSource = createMartinDynamicSource(DYNAMIC_TILES_URL);
