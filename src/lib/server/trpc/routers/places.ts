import { z } from "zod";
import { router, publicProcedure } from "@/lib/server/trpc/init";
import {
  searchByCategory,
  searchByText,
  searchNearby,
  getById,
  POI_CATEGORIES,
} from "@/lib/server/services/places";

const bboxSchema = z.object({
  west: z.number(),
  south: z.number(),
  east: z.number(),
  north: z.number(),
});

export const placesRouter = router({
  search: publicProcedure
    .input(
      z.object({
        query: z.string().min(1),
        bbox: bboxSchema.optional(),
      })
    )
    .query(async ({ input }) => {
      return searchByText(input.query, input.bbox);
    }),

  byCategory: publicProcedure
    .input(
      z.object({
        category: z.string(),
        bbox: bboxSchema,
      })
    )
    .query(async ({ input }) => {
      return searchByCategory(input.category, input.bbox);
    }),

  nearby: publicProcedure
    .input(
      z.object({
        lat: z.number(),
        lon: z.number(),
        radius: z.number().default(1000),
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
