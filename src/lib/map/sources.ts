import type { SourceSpecification } from "@maplibre/maplibre-gl-style-spec";

const TERRAIN_URL =
  process.env.NEXT_PUBLIC_TERRAIN_URL ||
  "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";

const PROTOMAPS_TILES_URL =
  process.env.NEXT_PUBLIC_PROTOMAPS_TILES_URL ||
  "https://api.protomaps.com/tiles/v4/{z}/{x}/{y}.mvt";

const SATELLITE_URL =
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";

export function getSources(
  martinUrl: string,
  tilesUrl: string = PROTOMAPS_TILES_URL
): Record<string, SourceSpecification> {
  return {
    protomaps: {
      type: "vector",
      tiles: [tilesUrl],
      maxzoom: 15,
      attribution: '&copy; <a href="https://openstreetmap.org">OpenStreetMap</a>',
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
    "landfire-evt": {
      type: "raster",
      tiles: [
        "https://www.landfire.gov/arcgis/rest/services/Landfire/US_200/MapServer/export?bbox={bbox-epsg-3857}&bboxSR=3857&size=256,256&format=png&f=image",
      ],
      tileSize: 256,
      attribution: "&copy; LANDFIRE, USDA/DOI",
      minzoom: 8,
    },
    "nbr-recovery": {
      type: "raster",
      tiles: [
        "https://tiles.arcgis.com/tiles/P3ePLMYs2RVChkJx/arcgis/rest/services/NatureServe_LandscapeIntegrity/MapServer/tile/{z}/{y}/{x}",
      ],
      tileSize: 256,
      attribution: "&copy; NatureServe",
    },
    "ndvi-overlay": {
      type: "raster",
      // Tiles URL is updated dynamically by VegetationLayer via map.getSource().setTiles()
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
    "nlcd-wms": {
      type: "raster",
      tiles: [
        "https://www.mrlc.gov/geoserver/mrlc_display/NLCD_2021_Land_Cover_L48/wms?SERVICE=WMS&REQUEST=GetMap&LAYERS=NLCD_2021_Land_Cover_L48&FORMAT=image/png&TRANSPARENT=true&VERSION=1.3.0&CRS=EPSG:3857&BBOX={bbox-epsg-3857}&WIDTH=256&HEIGHT=256",
      ],
      tileSize: 256,
    },
    "nlcd-change": {
      type: "raster",
      tiles: [
        "https://www.mrlc.gov/geoserver/mrlc_change/nlcd_2019_2021_change_l48/wms?SERVICE=WMS&REQUEST=GetMap&LAYERS=nlcd_2019_2021_change_l48&FORMAT=image/png&TRANSPARENT=true&VERSION=1.3.0&CRS=EPSG:3857&BBOX={bbox-epsg-3857}&WIDTH=256&HEIGHT=256",
      ],
      tileSize: 256,
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
  tiles: [PROTOMAPS_TILES_URL],
  maxzoom: 15,
  attribution: '&copy; <a href="https://openstreetmap.org">OpenStreetMap</a>',
};
