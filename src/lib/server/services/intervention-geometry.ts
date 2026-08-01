import { z } from "zod";

const MAX_RING_POINTS = 4_096;
const MAX_RINGS_PER_POLYGON = 64;
const MAX_POLYGONS_PER_GEOMETRY = 32;

const PositionSchema = z
  .array(z.number().finite())
  .min(2)
  .max(3)
  .superRefine((position, context) => {
    if (position[0] < -180 || position[0] > 180) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Longitude must be between -180 and 180",
        path: [0],
      });
    }
    if (position[1] < -90 || position[1] > 90) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Latitude must be between -90 and 90",
        path: [1],
      });
    }
  });

const LinearRingSchema = z
  .array(PositionSchema)
  .min(4)
  .max(MAX_RING_POINTS)
  .superRefine((ring, context) => {
    const firstPosition = ring[0];
    const lastPosition = ring.at(-1);
    if (
      !lastPosition ||
      firstPosition[0] !== lastPosition[0] ||
      firstPosition[1] !== lastPosition[1]
    ) {
      context.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Polygon linear rings must be closed",
      });
    }
  });

const PolygonCoordinatesSchema = z
  .array(LinearRingSchema)
  .min(1)
  .max(MAX_RINGS_PER_POLYGON);

/**
 * Validate the Point/Polygon/MultiPolygon geometries an intervention site may
 * carry. Mirrors the geometry bounds enforced by
 * `src/lib/server/security/geojson.ts` (out of this service's file boundary),
 * restricted to the shapes interventions actually use.
 */
export const InterventionGeometrySchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("Point"), coordinates: PositionSchema }).strict(),
  z
    .object({ type: z.literal("Polygon"), coordinates: PolygonCoordinatesSchema })
    .strict(),
  z
    .object({
      type: z.literal("MultiPolygon"),
      coordinates: z.array(PolygonCoordinatesSchema).min(1).max(MAX_POLYGONS_PER_GEOMETRY),
    })
    .strict(),
]);

export type InterventionGeometry = z.infer<typeof InterventionGeometrySchema>;
