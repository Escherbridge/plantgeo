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
// FIVE ids left this list across the environmental_postgres_retirement_20260904 track --
// sensor_tiles, evacuation_zone_tiles, burn_severity_tiles and watershed_tiles in wave C, and
// fire_risk_tiles last. Their layers now draw from the GeoJSON sources declared in
// PARQUET_FEATURE_SOURCE_IDS below, fed by the private Parquet plane through
// `environmental.getSensorStations` / `getEvacuationZones` / `getBurnSeverity` /
// `getWatershedBoundaries` / `getFirePerimeters`. Every one of the five is also gone from
// infra/martin/martin.yaml, so Martin no longer opens a PostgreSQL read for any of them; the SQL
// functions themselves survive until wave D's three-part drop packet
// (drizzle/0039_drop_environmental_tile_functions.sql).
//
// fire_risk_tiles was the last of the five and stayed here longest for a reason that stopped being
// true on 2026-09-04: the `fire-perimeters` lane was registered `daily_series` on a per-incident
// observation day, so its 177 perimeters sat across 45 partition days and no bounded read
// reproduced the union this layer draws. The lane is now registered `static_lookup` on
// ("snapshot_day", "unique_fire_identifier") -- one published snapshot IS the standing set, the
// same shape evacuation-zones already used. See src/lib/map/AGENTS.md "Which environmental layers
// read Parquet (all of them, since 2026-09-04)".
//
// DEPLOY THE APP BEFORE MARTIN'S CONFIG. Martin runs `auto_publish: false`, so unpublishing an id
// there while a tab still holds a style naming it turns that layer into a 404 until the tab
// reloads. The app deploy is what stops the ask; martin.yaml's own header states the same order.
const DYNAMIC_TILE_SOURCE_IDS = ["intervention_tiles"] as const;

/**
 * The GeoJSON sources whose data the Parquet readers fill, one per style-baked layer that used to
 * be a Martin function.
 *
 * Declared in the style rather than added by a component -- unlike `drought-monitor` or
 * `water-gauges`, which their own components create -- because these layers stay style-baked: their
 * visibility, opacity multiplier and date filter are all written by LayerManager's three appliers,
 * which walk `LAYER_REGISTRY.styleLayerIds` and would have nothing to walk if the layers moved into
 * components. What changed here is where the bytes come from, not who owns the layer.
 *
 * Empty until `LayerManager` sets data on them: an empty GeoJSON source renders nothing and errors
 * on nothing, which is the same "an empty source is not a broken source" property the OSM table
 * sources rely on below.
 */
export const PARQUET_FEATURE_SOURCE_IDS = [
  "sensor-station-features",
  "evacuation-zone-features",
  "burn-severity-features",
  "watershed-features",
  "fire-perimeter-features",
] as const;

export type ParquetFeatureSourceId = (typeof PARQUET_FEATURE_SOURCE_IDS)[number];

const EMPTY_FEATURE_COLLECTION: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

/** The five empty GeoJSON sources, keyed by id, for spreading into a style's `sources` map. */
export function createParquetFeatureSources(): Record<string, SourceSpecification> {
  return Object.fromEntries(
    PARQUET_FEATURE_SOURCE_IDS.map((sourceId) => [
      sourceId,
      { type: "geojson", data: EMPTY_FEATURE_COLLECTION } satisfies SourceSpecification,
    ])
  );
}

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
    ...createParquetFeatureSources(),
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
export const parquetFeatureSources = createParquetFeatureSources();
