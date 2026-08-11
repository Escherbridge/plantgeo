import {
  pgTable,
  pgSchema,
  uuid,
  varchar,
  text,
  boolean,
  integer,
  bigint,
  jsonb,
  timestamp,
  doublePrecision,
  real,
  primaryKey,
  customType,
  unique,
  uniqueIndex,
  index,
} from "drizzle-orm/pg-core";
import { sql } from "drizzle-orm";

export const geoSchema = pgSchema("geo");
export const trackingSchema = pgSchema("tracking");

// Database triggers maintain spatial columns from validated application data.
const spatialGeometry = customType<{ data: string; driverData: string }>({
  dataType: () => "geometry(GEOMETRY,4326)",
});

const spatialPoint = customType<{ data: string; driverData: string }>({
  dataType: () => "geometry(POINT,4326)",
});

const spatialMultiPolygon = customType<{ data: string; driverData: string }>({
  dataType: () => "geometry(MULTIPOLYGON,4326)",
});

const spatialPolygon = customType<{ data: string; driverData: string }>({
  dataType: () => "geometry(POLYGON,4326)",
});

const spatialGeographyPoint = customType<{ data: string; driverData: string }>({
  dataType: () => "geography(POINT,4326)",
});

// ============================================
// Auth Tables (public schema)
// ============================================

export const users = pgTable("users", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: text("name"),
  email: text("email").unique().notNull(),
  emailVerified: timestamp("email_verified", { withTimezone: true }),
  image: text("image"),
  passwordHash: text("password_hash"),
  platformRole: varchar("platform_role", { length: 20 }).default("contributor"),
  verified: boolean("verified").default(false),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  // No .references() here: teams is declared after users in this file, and a
  // forward reference would create a circular initializer between the two tables.
  activeTeamId: uuid("active_team_id"),
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
  // Plain unique constraint; app code always lowercases the slug before
  // insert/update, so this stays case-insensitive-safe without a functional index.
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

export const apiKeys = pgTable(
  "api_keys",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    keyHash: text("key_hash").notNull(),
    userId: uuid("user_id").references(() => users.id),
    teamId: uuid("team_id").references(() => teams.id),
    name: text("name"),
    permissions: jsonb("permissions").default([]),
    rateLimit: integer("rate_limit").default(1000),
    lastUsed: timestamp("last_used", { withTimezone: true }),
  },
  (table) => [uniqueIndex("api_keys_key_hash_unique").on(table.keyHash)]
);

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
  teamId: uuid("team_id").references(() => teams.id),
  sortOrder: integer("sort_order").default(0),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow(),
});

/** Type-2 conformed geometry dimension: one row per version of a place. See `src/lib/server/db/AGENTS.md` §geometry-dimension. */
export const geometry = geoSchema.table(
  "geometry",
  {
    geometryId: uuid("geometry_id").defaultRandom().primaryKey(),
    naturalKey: varchar("natural_key", { length: 255 }).notNull(),
    versionValidFrom: timestamp("version_valid_from", {
      withTimezone: true,
    }).notNull(),
    versionValidTo: timestamp("version_valid_to", { withTimezone: true }),
    geomKind: varchar("geom_kind", { length: 16 }).notNull(),
    geom: spatialGeometry("geom").notNull(),
    centroid: spatialPoint("centroid").notNull(),
    gridName: varchar("grid_name", { length: 100 }),
    cellKey: varchar("cell_key", { length: 180 }),
    resolutionMeters: integer("resolution_m"),
    producer: varchar("producer", { length: 100 }).notNull(),
    // No .references() here: a self-reference would create a circular initializer,
    // the same reason users.activeTeamId is a bare uuid. The FK is in the migration,
    // and it is DEFERRABLE; closing a version has exactly one legal statement order.
    supersededBy: uuid("superseded_by"),
    lastConfirmedAt: timestamp("last_confirmed_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    unique("uq_geometry_version").on(table.naturalKey, table.versionValidFrom),
    uniqueIndex("uq_geometry_current")
      .on(table.naturalKey)
      .where(sql`${table.versionValidTo} IS NULL`),
    uniqueIndex("uq_geometry_grid_cell")
      .on(table.gridName, table.cellKey)
      .where(sql`${table.versionValidTo} IS NULL`),
    index("ix_geometry_kind").on(table.geomKind, table.producer),
    index("ix_geometry_asof").on(
      table.naturalKey,
      sql`${table.versionValidFrom} DESC`
    ),
  ]
);

export const features = geoSchema.table(
  "features",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    layerId: uuid("layer_id")
      .notNull()
      .references(() => layers.id, { onDelete: "cascade" }),
    geom: spatialGeometry("geom"),
    properties: jsonb("properties").notNull().default({}),
    status: varchar("status", { length: 20 }).default("published"),
    reviewNote: text("review_note"),
    // Current version of this place, repointed whenever a version closes.
    geometryId: uuid("geometry_id").references(() => geometry.geometryId, {
      onDelete: "restrict",
    }),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow(),
  },
  (table) => [
    uniqueIndex("features_layer_external_id_unique")
      .on(table.layerId, sql`(${table.properties} ->> 'id')`)
      .where(sql`${table.properties} ? 'id'`),
    index("ix_features_geometry_id").on(table.geometryId),
    // "What landed in this layer, and when" for /ops/backfill; see drizzle/0022 for why the two
    // write-time columns get an index each rather than one composite.
    index("idx_features_layer_created_at").on(table.layerId, table.createdAt),
    index("idx_features_layer_updated_at").on(table.layerId, table.updatedAt),
  ]
);

// ============================================
// Tracking Schema
// ============================================

export const assets = trackingSchema.table("assets", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: varchar("name", { length: 100 }).notNull(),
  type: varchar("type", { length: 50 }).default("vehicle"),
  status: varchar("status", { length: 20 }).default("offline"),
  metadata: jsonb("metadata").default({}),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

export const positions = trackingSchema.table(
  "positions",
  {
    time: timestamp("time", { withTimezone: true }).notNull(),
    assetId: uuid("asset_id")
      .notNull()
      .references(() => assets.id, { onDelete: "cascade" }),
    geom: spatialGeographyPoint("geom"),
    heading: doublePrecision("heading"),
    speed: doublePrecision("speed"),
    altitude: doublePrecision("altitude"),
    metadata: jsonb("metadata").default({}),
  },
  (table) => [
    uniqueIndex("positions_asset_time_unique").on(table.assetId, table.time),
  ]
);

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
  geom: spatialPoint("geom"),
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

/** One USDM D0-D4 classification polygon per weekly release; see `src/lib/server/AGENTS.md` §drought-ingestion. */
export const droughtAreas = geoSchema.table(
  "drought_areas",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    validDate: varchar("valid_date", { length: 10 }).notNull(),
    dmCategory: integer("dm_category").notNull(),
    geom: spatialMultiPolygon("geom").notNull(),
    sourceUrl: text("source_url").notNull(),
    ingestedAt: timestamp("ingested_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => [
    uniqueIndex("drought_areas_valid_date_category_unique").on(
      table.validDate,
      table.dmCategory
    ),
    index("drought_areas_valid_date_idx").on(table.validDate),
  ]
);

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
// Raster Publication Catalog (geo schema)
// ============================================

/**
 * One published first-party raster archive. Append-only: superseding a release stamps
 * `supersededAt` and inserts a new row. See `src/lib/server/db/AGENTS.md` §raster-release.
 */
export const rasterRelease = geoSchema.table(
  "raster_release",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    collection: text("collection").notNull(),
    property: text("property").notNull(),
    depth: text("depth").notNull(),
    statistic: text("statistic").notNull(),
    sourceName: text("source_name").notNull(),
    sourceRelease: text("source_release").notNull(),
    sourceUrl: text("source_url").notNull(),
    licenseName: text("license_name").notNull(),
    attribution: text("attribution").notNull(),
    unit: text("unit").notNull(),
    /** Divide a stored pixel by this to get `unit`; the raster holds pH*10, not pH. */
    scaleDivisor: integer("scale_divisor").notNull(),
    nodataValue: doublePrecision("nodata_value"),
    valueMin: doublePrecision("value_min"),
    valueMax: doublePrecision("value_max"),
    /** The exact ramp the tiles were painted with: `[{ value, color }]` in `unit`. */
    colorRamp: jsonb("color_ramp").notNull(),
    objectKey: text("object_key").notNull(),
    archiveFormat: text("archive_format").notNull(),
    checksumSha256: text("checksum_sha256").notNull(),
    sizeBytes: bigint("size_bytes", { mode: "number" }).notNull(),
    minZoom: integer("min_zoom").notNull(),
    maxZoom: integer("max_zoom").notNull(),
    bounds: spatialPolygon("bounds").notNull(),
    publishedAt: timestamp("published_at", { withTimezone: true }).notNull().defaultNow(),
    supersededAt: timestamp("superseded_at", { withTimezone: true }),
  },
  (table) => [
    uniqueIndex("ux_raster_release_live")
      .on(
        table.collection,
        table.property,
        table.depth,
        table.statistic,
        table.archiveFormat
      )
      .where(sql`${table.supersededAt} IS NULL`),
    index("ix_raster_release_live_collection")
      .on(table.collection, table.property)
      .where(sql`${table.supersededAt} IS NULL`),
  ]
);

// ============================================
// Soil Health Tables (public schema)
// ============================================

/** SoilGrids point cache keyed on a rounded grid cell; see `src/lib/server/AGENTS.md` §soil-evidence. */
export const soilGridCache = pgTable(
  "soil_grid_cache",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    lat: doublePrecision("lat").notNull(),
    lon: doublePrecision("lon").notNull(),
    ph: doublePrecision("ph"),
    organicCarbon: doublePrecision("organic_carbon"),
    nitrogen: doublePrecision("nitrogen"),
    bulkDensity: doublePrecision("bulk_density"),
    cec: doublePrecision("cec"),
    ocd: doublePrecision("ocd"),
    /** False records a verified upstream no-data cell so it is not re-queried. */
    complete: boolean("complete").notNull().default(false),
    sourceUrl: text("source_url"),
    cachedAt: timestamp("cached_at", { withTimezone: true }).defaultNow(),
  },
  (table) => [uniqueIndex("soil_grid_cache_cell_unique").on(table.lat, table.lon)]
);

/**
 * Which SSURGO grid cells have been fetched from USDA Soil Data Access, so a cell
 * nobody asked for stays distinguishable from a cell the survey found nothing in.
 * See `src/lib/server/AGENTS.md` §soil-survey-persistence.
 */
export const soilSurveyCoverage = geoSchema.table(
  "soil_survey_coverage",
  {
    /** '<col>:<row>' on the 1/8-degree grid; minted only by `soilSurveyCellKey`. */
    cellKey: varchar("cell_key", { length: 40 }).primaryKey(),
    west: doublePrecision("west").notNull(),
    south: doublePrecision("south").notNull(),
    east: doublePrecision("east").notNull(),
    north: doublePrecision("north").notNull(),
    polygonCount: integer("polygon_count").notNull(),
    /** Rows SDA served that had no readable geometry or no publisher vintage. */
    unreadableCount: integer("unreadable_count").notNull().default(0),
    /** SDA held more delineations than the row ceiling served: covered in part only. */
    truncated: boolean("truncated").notNull().default(false),
    /** When we asked. Never when SSURGO published — that is per feature. */
    fetchedAt: timestamp("fetched_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [index("ix_soil_survey_coverage_fetched_at").on(table.fetchedAt)]
);

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
  dedupeKey: varchar("dedupe_key", { length: 160 }).unique(),
  alertType: varchar("alert_type", { length: 50 }).notNull(),
  severity: varchar("severity", { length: 20 }).notNull(), // 'info'|'warning'|'critical'
  title: text("title").notNull(),
  body: text("body"),
  metadata: jsonb("metadata"), // extra data like location, fire name, watchedLocationId, etc.
  isRead: boolean("is_read").default(false),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

// ============================================
// AI Regional Intelligence (public schema)
// ============================================

export const aiConversations = pgTable("ai_conversations", {
  id: uuid("id").defaultRandom().primaryKey(),
  userId: uuid("user_id")
    .notNull()
    .references(() => users.id, { onDelete: "cascade" }),
  geohash: varchar("geohash", { length: 12 }).notNull(),
  lat: doublePrecision("lat").notNull(),
  lon: doublePrecision("lon").notNull(),
  title: varchar("title", { length: 255 }).notNull().default("New Analysis"),
  messageCount: integer("message_count").default(0).notNull(),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
  updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
});

export const aiMessages = pgTable("ai_messages", {
  id: uuid("id").defaultRandom().primaryKey(),
  conversationId: uuid("conversation_id")
    .notNull()
    .references(() => aiConversations.id, { onDelete: "cascade" }),
  role: varchar("role", { length: 10 }).notNull(),
  content: text("content").notNull(),
  structuredResponse: jsonb("structured_response"),
  tokenCount: integer("token_count"),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
});

// ============================================
// Historical Data Service (geo schema)
// Note: geom geometry(POINT,4326) column to be added via migration
// ============================================

export const historicalFireData = geoSchema.table("historical_fire_data", {
  id: uuid("id").defaultRandom().primaryKey(),
  date_bucket: timestamp("date_bucket", { withTimezone: true }).notNull(),
  lat: doublePrecision("lat").notNull(),
  lon: doublePrecision("lon").notNull(),
  geom: spatialPoint("geom"),
  fire_risk_score: doublePrecision("fire_risk_score"),
  detected_anomalies: integer("detected_anomalies").default(0),
  metadata: jsonb("metadata").default({}),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

export const historicalWaterDrought = geoSchema.table("historical_water_drought", {
  id: uuid("id").defaultRandom().primaryKey(),
  date_bucket: timestamp("date_bucket", { withTimezone: true }).notNull(),
  lat: doublePrecision("lat").notNull(),
  lon: doublePrecision("lon").notNull(),
  geom: spatialPoint("geom"),
  water_scarcity_index: doublePrecision("water_scarcity_index"),
  streamflow_cfs: doublePrecision("streamflow_cfs"),
  metadata: jsonb("metadata").default({}),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

export const historicalVegetation = geoSchema.table("historical_vegetation", {
  id: uuid("id").defaultRandom().primaryKey(),
  date_bucket: timestamp("date_bucket", { withTimezone: true }).notNull(),
  lat: doublePrecision("lat").notNull(),
  lon: doublePrecision("lon").notNull(),
  geom: spatialPoint("geom"),
  ndvi_value: doublePrecision("ndvi_value"),
  ecological_health_index: doublePrecision("ecological_health_index"),
  metadata: jsonb("metadata").default({}),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

// ============================================
// Agent Knowledge Bases (public schema)
// ============================================

export const agriculturalSolutions = pgTable("agricultural_solutions", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: varchar("name", { length: 100 }).notNull().unique(), // e.g., 'Hydroponics', 'Silvopasture'
  description: text("description"),
  suitability_rules: jsonb("suitability_rules").default({}), // Rules matching environment to this solution
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

export const openPlantData = pgTable("open_plant_data", {
  id: uuid("id").defaultRandom().primaryKey(),
  scientific_name: varchar("scientific_name", { length: 200 }).notNull(),
  common_name: varchar("common_name", { length: 200 }),
  solution_id: uuid("solution_id").references(() => agriculturalSolutions.id),
  climate_requirements: jsonb("climate_requirements").default({}),
  water_requirements: jsonb("water_requirements").default({}),
  soil_requirements: jsonb("soil_requirements").default({}),
  metadata: jsonb("metadata").default({}), // Sourced from USDA etc.
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

export const openToolingData = pgTable("open_tooling_data", {
  id: uuid("id").defaultRandom().primaryKey(),
  name: varchar("name", { length: 200 }).notNull(),
  solution_id: uuid("solution_id").references(() => agriculturalSolutions.id),
  category: varchar("category", { length: 100 }), // e.g., 'Irrigation', 'Structures'
  specifications: jsonb("specifications").default({}),
  metadata: jsonb("metadata").default({}),
  createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
});

// ============================================
// Organization Credential Lifecycle & Invitations (public schema)
// "Organizations" are the existing teams/team_members tables above; these
// tables add credential-lifecycle (verification/reset tokens) and
// team invitation/join-link flows on top of them.
// ============================================

export const emailVerificationTokens = pgTable(
  "email_verification_tokens",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    tokenHash: text("token_hash").notNull(),
    expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
    usedAt: timestamp("used_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  },
  (t) => [
    uniqueIndex("email_verification_tokens_token_hash_unique").on(t.tokenHash),
    index("email_verification_tokens_user_idx").on(t.userId),
  ]
);

export const passwordResetTokens = pgTable(
  "password_reset_tokens",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    tokenHash: text("token_hash").notNull(),
    expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
    usedAt: timestamp("used_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  },
  (t) => [
    uniqueIndex("password_reset_tokens_token_hash_unique").on(t.tokenHash),
    index("password_reset_tokens_user_idx").on(t.userId),
  ]
);

export const teamInvitations = pgTable(
  "team_invitations",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    teamId: uuid("team_id")
      .notNull()
      .references(() => teams.id, { onDelete: "cascade" }),
    email: text("email").notNull(), // always stored lowercase by app code
    teamRole: varchar("team_role", { length: 20 }).notNull().default("member"),
    tokenHash: text("token_hash").notNull(),
    invitedBy: uuid("invited_by").references(() => users.id),
    expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
    acceptedAt: timestamp("accepted_at", { withTimezone: true }),
    acceptedBy: uuid("accepted_by").references(() => users.id),
    revokedAt: timestamp("revoked_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  },
  (t) => [
    uniqueIndex("team_invitations_token_hash_unique").on(t.tokenHash),
    index("team_invitations_team_email_idx").on(t.teamId, t.email),
    index("team_invitations_email_idx").on(t.email),
  ]
);

export const teamJoinLinks = pgTable(
  "team_join_links",
  {
    id: uuid("id").defaultRandom().primaryKey(),
    teamId: uuid("team_id")
      .notNull()
      .references(() => teams.id, { onDelete: "cascade" }),
    codeHash: text("code_hash").notNull(),
    teamRole: varchar("team_role", { length: 20 }).notNull().default("viewer"),
    allowedEmailDomain: text("allowed_email_domain"),
    maxUses: integer("max_uses"),
    useCount: integer("use_count").notNull().default(0),
    expiresAt: timestamp("expires_at", { withTimezone: true }),
    revokedAt: timestamp("revoked_at", { withTimezone: true }),
    createdBy: uuid("created_by").references(() => users.id),
    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow(),
  },
  (t) => [
    uniqueIndex("team_join_links_code_hash_unique").on(t.codeHash),
    index("team_join_links_team_idx").on(t.teamId),
  ]
);
