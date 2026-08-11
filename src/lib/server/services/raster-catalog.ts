import { sql } from "drizzle-orm";
import { db } from "@/lib/server/db";

/**
 * Where published raster archives are served from. The basemap archive already lives on this
 * host; raster releases are namespaced under `raster/` so they cannot collide with it.
 */
const RASTER_TILES_BASE_URL =
  process.env.NEXT_PUBLIC_RASTER_TILES_BASE_URL ?? "https://tiles.aevani.com";

/** One ramp stop, in the release's own `unit`. */
export interface RasterColorStop {
  value: number;
  color: string;
}

/** A live raster release: what it measures, how to draw it, and who to credit. */
export interface PublishedRaster {
  property: string;
  unit: string;
  /** Divide a stored pixel by this to get `unit`; carried for readers of the COG. */
  scaleDivisor: number;
  valueMin: number | null;
  valueMax: number | null;
  colorRamp: RasterColorStop[];
  archiveUrl: string;
  archiveFormat: string;
  minZoom: number;
  maxZoom: number;
  attribution: string;
  sourceName: string;
  sourceRelease: string;
  licenseName: string;
  bounds: [number, number, number, number];
}

/**
 * Live releases for one collection and archive format, newest publication per property.
 *
 * Reads `geo.published_raster`, never `geo.raster_release` -- the view is what applies the
 * `superseded_at IS NULL` predicate, so no call site can serve a withdrawn archive. See
 * `scripts/raster/AGENTS.md` §catalog.
 */
export async function getPublishedRasters(
  collection: string,
  archiveFormat = "pmtiles"
): Promise<PublishedRaster[]> {
  const rows = await db.execute<{
    property: string;
    unit: string;
    scale_divisor: number;
    value_min: number | null;
    value_max: number | null;
    color_ramp: RasterColorStop[];
    object_key: string;
    archive_format: string;
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
           object_key, archive_format, min_zoom, max_zoom,
           attribution, source_name, source_release, license_name,
           bbox_west, bbox_south, bbox_east, bbox_north
      FROM geo.published_raster
     WHERE collection = ${collection}
       AND archive_format = ${archiveFormat}
     ORDER BY property
  `);

  return rows.map((row) => ({
    property: row.property,
    unit: row.unit,
    scaleDivisor: Number(row.scale_divisor),
    valueMin: row.value_min === null ? null : Number(row.value_min),
    valueMax: row.value_max === null ? null : Number(row.value_max),
    colorRamp: row.color_ramp,
    archiveUrl: `${RASTER_TILES_BASE_URL}/${row.object_key}`,
    archiveFormat: row.archive_format,
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
  }));
}
