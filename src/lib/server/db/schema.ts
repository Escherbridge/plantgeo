import {
  pgTable,
  pgSchema,
  uuid,
  varchar,
  text,
  boolean,
  integer,
  jsonb,
  timestamp,
  doublePrecision,
} from "drizzle-orm/pg-core";

export const geoSchema = pgSchema("geo");
export const trackingSchema = pgSchema("tracking");

// ============================================
// Geo Schema
// ============================================

export const layers = geoSchema.table("layers", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: varchar("name", { length: 100 }).notNull().unique(),
  type: varchar("type", { length: 50 }).notNull().default("vector"),
  description: text("description"),
  style: jsonb("style").default({}),
  isPublic: boolean("is_public").default(false),
  minZoom: integer("min_zoom").default(0),
  maxZoom: integer("max_zoom").default(22),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow(),
});

export const features = geoSchema.table("features", {
  id: uuid("id").defaultRandom().primaryKey(),
  layerId: uuid("layer_id")
    .notNull()
    .references(() => layers.id, { onDelete: "cascade" }),
  properties: jsonb("properties").notNull().default({}),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow(),
});

// ============================================
// Tracking Schema
// ============================================

export const positions = trackingSchema.table("positions", {
  time: timestamp("time", { withTimezone: true }).notNull(),
  assetId: uuid("asset_id").notNull(),
  heading: doublePrecision("heading"),
  speed: doublePrecision("speed"),
  altitude: doublePrecision("altitude"),
  metadata: jsonb("metadata").default({}),
});
