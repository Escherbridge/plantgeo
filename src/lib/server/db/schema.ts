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
  real,
  primaryKey,
} from "drizzle-orm/pg-core";

export const geoSchema = pgSchema("geo");
export const trackingSchema = pgSchema("tracking");

// ============================================
// Auth Tables (public schema)
// ============================================

export const users = pgTable("users", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: text("name"),
  email: text("email").unique().notNull(),
  passwordHash: text("password_hash"),
  platformRole: varchar("platform_role", { length: 20 }).default("contributor"),
  verified: boolean("verified").default(false),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

export const sessions = pgTable("sessions", {
  sessionToken: text("session_token").primaryKey(),
  userId: uuid("user_id")
    .notNull()
    .references(() => users.id, { onDelete: "cascade" }),
  expires: timestamp("expires", { withTimezone: true }).notNull(),
});

export const accounts = pgTable("accounts", {
  userId: uuid("user_id")
    .notNull()
    .references(() => users.id, { onDelete: "cascade" }),
  type: text("type").notNull(),
  provider: text("provider").notNull(),
  providerAccountId: text("provider_account_id").notNull(),
  refresh_token: text("refresh_token"),
  access_token: text("access_token"),
  expires_at: integer("expires_at"),
  token_type: text("token_type"),
  scope: text("scope"),
  id_token: text("id_token"),
  session_state: text("session_state"),
}, (account) => ({
  pk: primaryKey({ columns: [account.provider, account.providerAccountId] }),
}));

export const verificationTokens = pgTable("verification_tokens", {
  identifier: text("identifier").notNull(),
  token: text("token").notNull(),
  expires: timestamp("expires", { withTimezone: true }).notNull(),
}, (vt) => ({
  pk: primaryKey({ columns: [vt.identifier, vt.token] }),
}));

// ============================================
// Teams Tables (public schema)
// ============================================

export const teams = pgTable("teams", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: text("name").notNull(),
  slug: varchar("slug", { length: 100 }).unique(),
  description: text("description"),
  orgType: varchar("org_type", { length: 50 }),
  specialties: jsonb("specialties").$type<string[]>(),
  website: text("website"),
  serviceArea: jsonb("service_area").$type<Record<string, unknown>>(),
  isVerified: boolean("is_verified").default(false),
  verifiedAt: timestamp("verified_at", { withTimezone: true }),
  createdBy: uuid("created_by").references(() => users.id),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

export const teamMembers = pgTable("team_members", {
  teamId: uuid("team_id")
    .notNull()
    .references(() => teams.id, { onDelete: "cascade" }),
  userId: uuid("user_id")
    .notNull()
    .references(() => users.id, { onDelete: "cascade" }),
  teamRole: varchar("team_role", { length: 20 }).default("member"),
  joinedAt: timestamp("joined_at", { withTimezone: true }).defaultNow(),
}, (tm) => ({
  pk: primaryKey({ columns: [tm.teamId, tm.userId] }),
}));

// ============================================
// API Keys Table (public schema)
// ============================================

export const apiKeys = pgTable("api_keys", {
  id: uuid("id").defaultRandom().primaryKey(),
  keyHash: text("key_hash").notNull(),
  userId: uuid("user_id").references(() => users.id),
  teamId: uuid("team_id").references(() => teams.id),
  name: text("name"),
  permissions: jsonb("permissions").default([]),
  rateLimit: integer("rate_limit").default(1000),
  lastUsed: timestamp("last_used", { withTimezone: true }),
});

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
  teamId: uuid("team_id"),
  sortOrder: integer("sort_order").default(0),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow(),
});

export const features = geoSchema.table("features", {
  id: uuid("id").defaultRandom().primaryKey(),
  layerId: uuid("layer_id")
    .notNull()
    .references(() => layers.id, { onDelete: "cascade" }),
  properties: jsonb("properties").notNull().default({}),
  status: varchar("status", { length: 20 }).default("published"),
  reviewNote: text("review_note"),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow(),
});

// ============================================
// Fire Detections (geo schema)
// Note: geom geometry(POINT,4326) column is added via migration
// ============================================

export const fireDetections = geoSchema.table("fire_detections", {
  id: uuid("id").defaultRandom().primaryKey(),
  detectedAt: timestamp("detected_at", { withTimezone: true }).notNull(),
  confidence: varchar("confidence", { length: 20 }),
  brightness: real("brightness"),
  frp: real("frp"),
  satellite: varchar("satellite", { length: 50 }),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
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

export const assets = trackingSchema.table("assets", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: varchar("name", { length: 100 }).notNull(),
  type: varchar("type", { length: 50 }).default("vehicle"),
  status: varchar("status", { length: 20 }).default("offline"),
  metadata: jsonb("metadata").default({}),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

export const geofences = trackingSchema.table("geofences", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: varchar("name", { length: 100 }).notNull(),
  geometry: jsonb("geometry").notNull().default({}),
  alertOnEnter: boolean("alert_on_enter").default(true),
  alertOnExit: boolean("alert_on_exit").default(true),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

export const alerts = trackingSchema.table("alerts", {
  id: uuid("id").defaultRandom().primaryKey(),
  assetId: uuid("asset_id"),
  geofenceId: uuid("geofence_id"),
  type: varchar("type", { length: 50 }).notNull(),
  message: text("message").notNull(),
  acknowledged: boolean("acknowledged").default(false),
  metadata: jsonb("metadata").default({}),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

// ============================================
// POI / Places (geo schema)
// Note: geom geometry(POINT,4326) column added via migration:
// ALTER TABLE geo.poi ADD COLUMN geom GEOMETRY(POINT,4326);
// CREATE INDEX ON geo.poi USING GIST(geom);
// ============================================

export const poi = geoSchema.table("poi", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: text("name").notNull(),
  category: varchar("category", { length: 50 }),
  subcategory: varchar("subcategory", { length: 50 }),
  address: text("address"),
  phone: varchar("phone", { length: 30 }),
  website: text("website"),
  hours: jsonb("hours").default({}),
  tags: jsonb("tags").default({}),
  osmId: integer("osm_id"),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

// ============================================
// Water Scarcity Tables (public schema)
// ============================================

export const waterGauges = pgTable("water_gauges", {
  id: uuid("id").defaultRandom().primaryKey(),
  siteNo: varchar("site_no", { length: 20 }).notNull().unique(),
  siteName: text("site_name"),
  lat: doublePrecision("lat").notNull(),
  lon: doublePrecision("lon").notNull(),
  flowCfs: doublePrecision("flow_cfs"),
  percentile: integer("percentile"),
  trend: varchar("trend", { length: 20 }),
  condition: varchar("condition", { length: 30 }),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow(),
});

export const droughtData = pgTable("drought_data", {
  id: uuid("id").defaultRandom().primaryKey(),
  weekDate: varchar("week_date", { length: 20 }).notNull().unique(),
  geojson: jsonb("geojson").notNull(),
  fetchedAt: timestamp("fetched_at", { withTimezone: true }).defaultNow(),
});

// ============================================
// Community Strategy Requests (public schema)
// ============================================

export const strategyRequests = pgTable("strategy_requests", {
  id: uuid("id").defaultRandom().primaryKey(),
  userId: uuid("user_id").references(() => users.id),
  teamId: uuid("team_id").references(() => teams.id),
  strategyType: varchar("strategy_type", { length: 50 }).notNull(), // 'keyline'|'silvopasture'|'reforestation'|'biochar'|'water_harvesting'|'cover_cropping'
  title: text("title").notNull(),
  description: text("description"),
  lat: doublePrecision("lat").notNull(),
  lon: doublePrecision("lon").notNull(),
  status: varchar("status", { length: 20 }).default("open"), // 'open'|'in_progress'|'completed'
  voteCount: integer("vote_count").default(0),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

export const requestVotes = pgTable("request_votes", {
  requestId: uuid("request_id").notNull().references(() => strategyRequests.id, { onDelete: "cascade" }),
  userId: uuid("user_id").notNull().references(() => users.id, { onDelete: "cascade" }),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
}, (rv) => ({
  pk: primaryKey({ columns: [rv.requestId, rv.userId] }),
}));

export const priorityZones = pgTable("priority_zones", {
  id: uuid("id").defaultRandom().primaryKey(),
  strategyType: varchar("strategy_type", { length: 50 }).notNull(),
  requestCount: integer("request_count").notNull(),
  totalVotes: integer("total_votes").notNull(),
  centroidLat: doublePrecision("centroid_lat"),
  centroidLon: doublePrecision("centroid_lon"),
  geojson: jsonb("geojson"), // ConvexHull polygon from DBSCAN cluster
  computedAt: timestamp("computed_at", { withTimezone: true }).defaultNow(),
});

// ============================================
// Soil Health Tables (public schema)
// ============================================

export const soilGridCache = pgTable("soil_grid_cache", {
  id: uuid("id").defaultRandom().primaryKey(),
  lat: doublePrecision("lat").notNull(),
  lon: doublePrecision("lon").notNull(),
  ph: doublePrecision("ph"),
  organicCarbon: doublePrecision("organic_carbon"),
  nitrogen: doublePrecision("nitrogen"),
  bulkDensity: doublePrecision("bulk_density"),
  cec: doublePrecision("cec"),
  cachedAt: timestamp("cached_at", { withTimezone: true }).defaultNow(),
});

// ============================================
// Environmental Alert System (public schema)
// ============================================

export const watchedLocations = pgTable("watched_locations", {
  id: uuid("id").defaultRandom().primaryKey(),
  userId: uuid("user_id").notNull().references(() => users.id, { onDelete: "cascade" }),
  name: text("name").notNull(),
  lat: doublePrecision("lat").notNull(),
  lon: doublePrecision("lon").notNull(),
  radiusKm: integer("radius_km").default(50),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

export const alertSubscriptions = pgTable("alert_subscriptions", {
  id: uuid("id").defaultRandom().primaryKey(),
  userId: uuid("user_id").notNull().references(() => users.id, { onDelete: "cascade" }),
  watchedLocationId: uuid("watched_location_id").references(() => watchedLocations.id, { onDelete: "cascade" }),
  alertType: varchar("alert_type", { length: 50 }).notNull(), // 'fire_proximity'|'drought_escalation'|'streamflow_critical'|'priority_zone_created'
  threshold: jsonb("threshold"), // type-specific threshold config
  emailEnabled: boolean("email_enabled").default(true),
  inAppEnabled: boolean("in_app_enabled").default(true),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

export const environmentalAlerts = pgTable("environmental_alerts", {
  id: uuid("id").defaultRandom().primaryKey(),
  userId: uuid("user_id").notNull().references(() => users.id, { onDelete: "cascade" }),
  alertType: varchar("alert_type", { length: 50 }).notNull(),
  severity: varchar("severity", { length: 20 }).notNull(), // 'info'|'warning'|'critical'
  title: text("title").notNull(),
  body: text("body"),
  metadata: jsonb("metadata"), // extra data like location, fire name, watchedLocationId, etc.
  isRead: boolean("is_read").default(false),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});
