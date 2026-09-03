import { z } from "zod";
import { router, publicProcedure } from "@/lib/server/trpc/init";
import {
  searchByCategory,
  searchByText,
  searchNearby,
  getById,
  POI_CATEGORIES,
} from "@/lib/server/services/places";

/**
 * The widest circle an unauthenticated POI browse may ask for.
 *
 * A places search is a viewport question, so this is deliberately far below `alertsRouter`'s
 * 500 km dispatch radius: the row cap bounds the payload either way, but only a bounded radius
 * bounds the rows PostGIS has to measure to find the nearest hundred.
 */
const MAX_NEARBY_RADIUS_METERS = 50_000;
const DEFAULT_NEARBY_RADIUS_METERS = 1_000;

/**
 * Caps the viewport area for every places bbox, mirroring `areaBoundedBbox` in
 * `trpc/routers/environmental.ts` (not exported, and shaped for that router's comma-string
 * bbox rather than this router's object one). Every procedure below is unauthenticated and
 * pages the whole GiST-indexed table per request, so nothing amortizes a planet-wide ask; four
 * square degrees is roughly a 200 km-wide metro viewport at the equator, the widest region a
 * places browse should ever page through.
 */
const MAX_PLACES_BBOX_SQUARE_DEGREES = 4;

/**
 * A viewport in EPSG:4326 degrees, ordered west,south,east,north and bounded to
 * `MAX_PLACES_BBOX_SQUARE_DEGREES`. The ordering check rejects a wrapped (antimeridian-crossing)
 * viewport on purpose: `west < east` cannot express "180 to -170", so a client whose
 * `getBounds()` straddles +/-180 degrees must split the request into two boxes or clamp it
 * before calling.
 */
const bboxSchema = z
  .object({
    west: z.number().min(-180).max(180),
    south: z.number().min(-90).max(90),
    east: z.number().min(-180).max(180),
    north: z.number().min(-90).max(90),
  })
  .refine(({ west, south, east, north }) => west < east && south < north, {
    message: "bbox must be ordered west,south,east,north",
  })
  .refine(
    ({ west, south, east, north }) =>
      (east - west) * (north - south) <= MAX_PLACES_BBOX_SQUARE_DEGREES,
    { message: `bbox exceeds ${MAX_PLACES_BBOX_SQUARE_DEGREES} square degrees; zoom in` }
  );

/** Every procedure is public, so every input is bounded here rather than in the service. */
export const placesRouter = router({
  search: publicProcedure
    .input(
      z.object({
        query: z.string().trim().min(1).max(200),
        // Required, not optional: an unbounded ILIKE '%…%' is a full scan of geo.poi, and
        // nothing here amortizes that for an anonymous caller. See searchByText's own doc.
        bbox: bboxSchema,
      })
    )
    .query(async ({ input }) => {
      return searchByText(input.query, input.bbox);
    }),

  byCategory: publicProcedure
    .input(
      z.object({
        // Bounded to the column width rather than to POI_CATEGORIES: geo.poi.category carries
        // whatever the OSM import wrote, which is a superset of the browsable list.
        category: z.string().trim().min(1).max(50),
        bbox: bboxSchema,
      })
    )
    .query(async ({ input }) => {
      return searchByCategory(input.category, input.bbox);
    }),

  nearby: publicProcedure
    .input(
      z.object({
        lat: z.number().min(-90).max(90),
        lon: z.number().min(-180).max(180),
        /**
         * Metres, not necessarily a whole number. `ST_DWithin`'s third argument is a typed
         * `double precision` PostGIS parameter, so a fractional value bound into it needs no
         * `::numeric` cast -- the postgres-js bigint trap
         * (`src/lib/server/services/usda-soil.ts:1050-1055`,
         * `environmental-read-model.ts:2537-2545`) is a fractional parameter resolved against an
         * untyped bigint column, which this argument never is.
         */
        radius: z
          .number()
          .min(1)
          .max(MAX_NEARBY_RADIUS_METERS)
          .default(DEFAULT_NEARBY_RADIUS_METERS),
        limit: z.number().int().min(1).max(100).default(20),
      })
    )
    .query(async ({ input }) => {
      return searchNearby(input.lat, input.lon, input.radius, input.limit);
    }),

  categories: publicProcedure.query(() => {
    return POI_CATEGORIES;
  }),

  getById: publicProcedure
    .input(z.object({ id: z.string().uuid() }))
    .query(async ({ input }) => {
      return getById(input.id);
    }),
});
