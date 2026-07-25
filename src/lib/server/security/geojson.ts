import { z } from "zod";

export const MAX_FEATURES_PER_INGEST_REQUEST = 250;

const MAX_COORDINATES_PER_LINE = 4_096;
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

const LineStringCoordinatesSchema = z
  .array(PositionSchema)
  .min(2)
  .max(MAX_COORDINATES_PER_LINE);

const LinearRingSchema = z
  .array(PositionSchema)
  .min(4)
  .max(MAX_COORDINATES_PER_LINE)
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

const GeoJsonGeometrySchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("Point"), coordinates: PositionSchema }).strict(),
  z
    .object({
      type: z.literal("MultiPoint"),
      coordinates: z.array(PositionSchema).min(1).max(MAX_COORDINATES_PER_LINE),
    })
    .strict(),
  z
    .object({
      type: z.literal("LineString"),
      coordinates: LineStringCoordinatesSchema,
    })
    .strict(),
  z
    .object({
      type: z.literal("MultiLineString"),
      coordinates: z
        .array(LineStringCoordinatesSchema)
        .min(1)
        .max(MAX_RINGS_PER_POLYGON),
    })
    .strict(),
  z
    .object({
      type: z.literal("Polygon"),
      coordinates: PolygonCoordinatesSchema,
    })
    .strict(),
  z
    .object({
      type: z.literal("MultiPolygon"),
      coordinates: z.array(PolygonCoordinatesSchema).min(1).max(MAX_POLYGONS_PER_GEOMETRY),
    })
    .strict(),
]);

const FeatureIdSchema = z.union([z.string().min(1).max(256), z.number().finite()]);

const FeaturePropertiesSchema = z.record(z.unknown()).superRefine((properties, context) => {
  if (Object.keys(properties).length > 100) {
    context.addIssue({
      code: z.ZodIssueCode.custom,
      message: "Feature properties may contain at most 100 keys",
    });
  }
});

const BaseFeatureSchema = z
  .object({
    type: z.literal("Feature"),
    id: FeatureIdSchema.optional(),
    properties: FeaturePropertiesSchema.nullable(),
  })
  .strict();

const FeatureCollectionSchema = z
  .object({
    type: z.literal("FeatureCollection"),
    features: z
      .array(BaseFeatureSchema.extend({ geometry: GeoJsonGeometrySchema }))
      .min(1)
      .max(MAX_FEATURES_PER_INGEST_REQUEST),
  })
  .strict();

const NullableGeometryFeatureCollectionSchema = z
  .object({
    type: z.literal("FeatureCollection"),
    features: z
      .array(BaseFeatureSchema.extend({ geometry: GeoJsonGeometrySchema.nullable() }))
      .min(1)
      .max(MAX_FEATURES_PER_INGEST_REQUEST),
  })
  .strict();

/** Validate bounded GeoJSON accepted by fire-perimeter ingestion. */
export function parseFeatureCollection(input: unknown) {
  return FeatureCollectionSchema.safeParse(input);
}

/** Validate bounded GeoJSON accepted by point-detection ingestion. */
export function parseNullableGeometryFeatureCollection(input: unknown) {
  return NullableGeometryFeatureCollectionSchema.safeParse(input);
}

/** Validate a single sensor reading before it is converted into GeoJSON. */
export const SensorReadingSchema = z
  .object({
    sensor_id: z.string().trim().min(1).max(128),
    lat: z.number().finite().min(-90).max(90),
    lon: z.number().finite().min(-180).max(180),
    timestamp: z.string().datetime({ offset: true }),
    readings: z.record(z.unknown()).superRefine((readings, context) => {
      if (Object.keys(readings).length > 100) {
        context.addIssue({
          code: z.ZodIssueCode.custom,
          message: "Readings may contain at most 100 keys",
        });
      }
    }),
  })
  .strict();

/** Validate a single asset position update before it reaches realtime channels. */
export const TrackingPositionUpdateSchema = z
  .object({
    assetId: z.string().uuid(),
    lat: z.number().finite().min(-90).max(90),
    lon: z.number().finite().min(-180).max(180),
    heading: z.number().finite().min(0).max(360).optional(),
    speed: z.number().finite().min(0).max(2_000).optional(),
    altitude: z.number().finite().min(-1_000).max(100_000).optional(),
    timestamp: z.string().datetime({ offset: true }),
  })
  .strict();
