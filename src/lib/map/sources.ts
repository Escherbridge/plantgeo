import type { SourceSpecification } from "@maplibre/maplibre-gl-style-spec";

const TERRAIN_URL =
  process.env.NEXT_PUBLIC_TERRAIN_URL ||
  "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";

const PMTILES_BASE_URL =
  process.env.NEXT_PUBLIC_PMTILES_URL ||
  "pmtiles://https://build.protomaps.com/20240801T000000Z.pmtiles";

const SATELLITE_URL =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

export function getSources(
  martinUrl: string,
  pmtilesUrl: string = PMTILES_BASE_URL
): Record<string, SourceSpecification> {
  return {
    protomaps: {
      type: "vector",
      url: pmtilesUrl,
    },
    "martin-dynamic": {
      type: "vector",
      tiles: [`${martinUrl}/{z}/{x}/{y}`],
      minzoom: 0,
      maxzoom: 22,
    },
    "terrain-dem": {
      type: "raster-dem",
      tiles: [TERRAIN_URL],
      tileSize: 256,
      encoding: "terrarium",
    },
    satellite: {
      type: "raster",
      tiles: [SATELLITE_URL],
      tileSize: 256,
      attribution: "&copy; Esri, Maxar, Earthstar Geographics",
      maxzoom: 19,
    },
  };
}

export const terrainSource: SourceSpecification = {
  type: "raster-dem",
  tiles: [TERRAIN_URL],
  tileSize: 256,
  encoding: "terrarium",
};

export const pmtilesSource: SourceSpecification = {
  type: "vector",
  url: PMTILES_BASE_URL,
};
