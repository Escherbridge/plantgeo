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

// Function and table sources must stay in SEPARATE composites: Martin declares
// vector_layers in TileJSON for tables but cannot for functions, and MapLibre
// validates style source-layers against vector_layers whenever the field is
// present -- a mixed composite therefore rejects every function-backed layer.
// building_tiles was a member until 2026-08-15, when the 3D-footprints layer was removed:
// nothing draws geo.osm_buildings any more, so requesting its tiles bought nothing. The
// Martin function is still live and still serves valid (empty) tiles, so restoring the layer
// means re-listing the id here and nothing else.
// Each id below now gets its OWN MapLibre vector source (createMartinDynamicSources), keyed
// by the id itself. Until 2026-08-21 these were comma-joined into a SINGLE composite source,
// which coupled all six layers into one fate: a 404 on any one member failed the whole
// TileJSON, and — measured on production 2026-08-20 — one slow member (fire_risk_tiles did
// not answer in 120s) held the shared source's tiles unresolved, so the five layers that did
// answer in under 5s rendered nothing either. Split sources fail and stall independently.
// Martin runs `auto_publish: false` and reads its function catalogue from
// infra/martin/martin.yaml at startup, so a new function is served only after the Martin
// service itself redeploys; an id may still only be listed here once Martin answers for it,
// because a source declared in the style but never answered holds map.isStyleLoaded() false.
const DYNAMIC_TILE_SOURCE_IDS = [
  "fire_risk_tiles",
  "sensor_tiles",
  "evacuation_zone_tiles",
  "burn_severity_tiles",
  "intervention_tiles",
  "watershed_tiles",
] as const;

// Both are Martin *table* sources, and both geo.osm_roads and geo.osm_waterways also have
// 0 rows in production, with no toggle to withhold: they're baked unconditionally into
// every style's roadsLayer/
// waterwaysLayer (src/lib/map/layers.ts), the same always-on shape as the protomaps
// roads/water basemap layers. An empty source renders nothing rather than erroring (see
// src/components/map/AGENTS.md "The layer toggle is the only source of layer
// visibility"), and there is no switch here to mislead anyone about, so nothing needs to
// change: both start drawing with no code change once the osm2pgsql import
// (infra/db/import/osm-flex-config.lua) has been run for the covered region.
const OSM_TILE_SOURCE_IDS = ["osm_roads", "osm_waterways"] as const;

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

function createMartinCompositeSource(
  baseUrl: string,
  sourceIds: readonly string[]
): SourceSpecification {
  return {
    type: "vector",
    // Martin's TileJSON endpoint keeps client tile paths aligned with its catalog.
    url: `${stripTrailingSlash(baseUrl)}/${sourceIds.join(",")}`,
    minzoom: 0,
    maxzoom: 22,
  };
}

/** One MapLibre vector source over exactly one Martin source id. */
export function createMartinFunctionSource(
  baseUrl: string,
  sourceId: string
): SourceSpecification {
  return createMartinCompositeSource(baseUrl, [sourceId]);
}

/**
 * The function-backed dynamic sources, one MapLibre source per Martin function, keyed by the
 * bare Martin source id (which is also what layers.ts puts in each layer's `source` field).
 * Spread into a style's `sources` map — see styles.ts.
 */
export function createMartinDynamicSources(
  baseUrl: string
): Record<string, SourceSpecification> {
  return Object.fromEntries(
    DYNAMIC_TILE_SOURCE_IDS.map((sourceId) => [
      sourceId,
      createMartinFunctionSource(baseUrl, sourceId),
    ])
  );
}

export function createMartinOsmSource(baseUrl: string): SourceSpecification {
  return createMartinCompositeSource(baseUrl, OSM_TILE_SOURCE_IDS);
}

export function getSources(
  dynamicTilesUrl: string = DYNAMIC_TILES_URL,
  pmtilesArchiveUrl: string = PMTILES_ARCHIVE_URL
): Record<string, SourceSpecification> {
  return {
    protomaps: createPmtilesSource(pmtilesArchiveUrl),
    ...createMartinDynamicSources(dynamicTilesUrl),
    "martin-osm": createMartinOsmSource(dynamicTilesUrl),
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
      // GIBS serves nothing past z9; VegetationLayer's own addSource carries the same cap.
      // If this inert entry is ever wired into a style, the layer's `!getSource` guard will
      // skip its addSource — so the cap must live here too or it silently vanishes.
      maxzoom: 9,
    },
    "ndwi-overlay": {
      type: "raster",
      tiles: [""],
      tileSize: 256,
      attribution: "NASA GIBS",
      maxzoom: 9,
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

export const martinDynamicSources = createMartinDynamicSources(DYNAMIC_TILES_URL);
export const martinOsmSource = createMartinOsmSource(DYNAMIC_TILES_URL);
