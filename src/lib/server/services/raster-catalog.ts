import { sql } from "drizzle-orm";
import { db } from "@/lib/server/db";
import {
  SOIL_RASTER_PROPERTIES,
  type PublishedSoilRaster,
  type SoilProperty,
  type SoilRasterColorStop,
} from "@/lib/map/soil-raster";

const DEFAULT_PUBLIC_RASTER_BASE_URL = "https://tiles.aevani.com";

function publicRasterBaseUrl(): URL {
  const configured =
    process.env.RASTER_TILES_BASE_URL ??
    process.env.NEXT_PUBLIC_RASTER_TILES_BASE_URL ??
    DEFAULT_PUBLIC_RASTER_BASE_URL;
  const base = new URL(configured.endsWith("/") ? configured : `${configured}/`);
  if (!(["http:", "https:"] as string[]).includes(base.protocol)) {
    throw new Error("RASTER_TILES_BASE_URL must use HTTP or HTTPS");
  }
  if (base.username || base.password || base.search || base.hash) {
    throw new Error("RASTER_TILES_BASE_URL must be a credential-free base URL");
  }
  return base;
}

/** Join a catalogue object key to the reviewed public origin without allowing URL escape. */
export function publicRasterArchiveUrl(objectKey: string): string {
  const segments = objectKey.split("/");
  if (
    objectKey.startsWith("/") ||
    objectKey.includes("\\") ||
    segments.some((segment) => segment === "" || segment === "." || segment === "..")
  ) {
    throw new Error(`Unsafe raster object key: ${objectKey}`);
  }
  const base = publicRasterBaseUrl();
  const encodedKey = segments.map(encodeURIComponent).join("/");
  return new URL(encodedKey, base).toString();
}

function isSoilProperty(value: string): value is SoilProperty {
  return (SOIL_RASTER_PROPERTIES as readonly string[]).includes(value);
}

function parseColorRamp(value: unknown, property: string): SoilRasterColorStop[] {
  if (!Array.isArray(value)) throw new Error(`Invalid color ramp for SoilGrids ${property}`);
  return value.map((stop) => {
    if (
      typeof stop !== "object" ||
      stop === null ||
      typeof (stop as { value?: unknown }).value !== "number" ||
      typeof (stop as { color?: unknown }).color !== "string"
    ) {
      throw new Error(`Invalid color ramp stop for SoilGrids ${property}`);
    }
    return {
      value: (stop as { value: number }).value,
      color: (stop as { color: string }).color,
    };
  });
}

/** Live SoilGrids PMTiles releases, read through the non-superseded catalogue view. */
export async function getPublishedSoilRasters(database: Pick<typeof db, "execute"> = db): Promise<PublishedSoilRaster[]> {
  const rows = await database.execute<{
    property: string;
    unit: string;
    scale_divisor: number;
    value_min: number | null;
    value_max: number | null;
    color_ramp: unknown;
    object_key: string;
    min_zoom: number;
    max_zoom: number;
    attribution: string;
    source_name: string;
    source_release: string;
    license_name: string;
    bbox_west: number;
    bbox_south: number;
    bbox_east: number;
    bbox_north: number;
  }>(sql`
    SELECT property, unit, scale_divisor, value_min, value_max, color_ramp,
           object_key, min_zoom, max_zoom, attribution, source_name,
           source_release, license_name, bbox_west, bbox_south, bbox_east, bbox_north
      FROM geo.published_raster
     WHERE collection = 'soilgrids'
       AND archive_format = 'pmtiles'
       AND depth = '0-5cm'
       AND statistic = 'mean'
     ORDER BY property
  `);

  return rows.map((row) => {
    if (!isSoilProperty(row.property)) {
      throw new Error(`Unknown published SoilGrids property: ${row.property}`);
    }
    return {
      property: row.property,
      unit: row.unit,
      scaleDivisor: Number(row.scale_divisor),
      valueMin: row.value_min === null ? null : Number(row.value_min),
      valueMax: row.value_max === null ? null : Number(row.value_max),
      colorRamp: parseColorRamp(row.color_ramp, row.property),
      archiveUrl: publicRasterArchiveUrl(row.object_key),
      minZoom: Number(row.min_zoom),
      maxZoom: Number(row.max_zoom),
      attribution: row.attribution,
      sourceName: row.source_name,
      sourceRelease: row.source_release,
      licenseName: row.license_name,
      bounds: [
        Number(row.bbox_west),
        Number(row.bbox_south),
        Number(row.bbox_east),
        Number(row.bbox_north),
      ],
    };
  });
}
