/**
 * `land_context.boundary_versions` -- one row per admitted version of a
 * source feature across the four product families (parcel, electric
 * service territory, BLM surface management/administrative, state-managed
 * land). Per spec §"Reference records and identity".
 *
 * Identity discipline: `nativeFeatureKey` is always a STRING (never an int
 * or ArcGIS OBJECTID) so a county parcel ID with leading zeros round-trips
 * exactly, per spec "A parcel key includes county/source namespace and the
 * original string ID. Preserve leading zeros." `lineageContinuity` defaults
 * to `unknown` rather than fabricating identity from coordinates/timestamps
 * across a split or merge.
 */
import {
  uuid,
  varchar,
  text,
  boolean,
  jsonb,
  doublePrecision,
  timestamp,
  check,
  uniqueIndex,
  index,
} from "drizzle-orm/pg-core";
import { sql } from "drizzle-orm";
import { landContextSchema, spatialGeometry } from "./shared";
import { sourceReleases } from "./source-releases";

export const boundaryVersions = landContextSchema.table(
  "boundary_versions",
  {
    id: uuid("id").defaultRandom().primaryKey(),

    /** e.g. `wa_dor_parcels`, `blm_sma`, `id_electric_territories`. Not a display label. */
    sourceNamespace: varchar("source_namespace", { length: 100 }).notNull(),
    /** The original string ID, county/namespace-qualified upstream; leading zeros preserved. */
    nativeFeatureKey: varchar("native_feature_key", { length: 255 }).notNull(),
    /** The source's own revision/version token for this feature, when it publishes one. */
    nativeFeatureVersion: varchar("native_feature_version", { length: 100 }),

    /** One of the four product families from spec's family table. */
    family: varchar("family", { length: 60 }).notNull(),
    /** e.g. `fee_parcel`, `retail_service_territory`, `surface_management`, `administrative_unit`, `state_trust_land`. */
    interestType: varchar("interest_type", { length: 80 }).notNull(),

    state: varchar("state", { length: 2 }).notNull(),
    county: varchar("county", { length: 100 }),

    /**
     * Nonpersonal attributes only (assessor use, crop cover, Census urban
     * class, local zoning, utility identity, etc). Structurally: this table
     * has no owner-name or personal-contact column at all, so there is
     * nothing here to exclude at read time -- the absence is the contract.
     */
    nonpersonalAttributes: jsonb("nonpersonal_attributes").notNull().default({}),

    geom: spatialGeometry("geom").notNull(),
    bboxMinLon: doublePrecision("bbox_min_lon").notNull(),
    bboxMinLat: doublePrecision("bbox_min_lat").notNull(),
    bboxMaxLon: doublePrecision("bbox_max_lon").notNull(),
    bboxMaxLat: doublePrecision("bbox_max_lat").notNull(),
    crs: varchar("crs", { length: 32 }).notNull().default("EPSG:4326"),

    recordUrl: text("record_url"),
    /** Free-text source accuracy/precision statement; not a computed confidence score. */
    sourceAccuracy: text("source_accuracy"),

    /** `source_native` | `aoi_clipped` | `reprojected` | `generalized`. */
    derivationMethod: varchar("derivation_method", { length: 30 })
      .notNull()
      .default("source_native"),
    clippedToAoi: boolean("clipped_to_aoi").notNull().default(false),
    fullFeatureAreaSqm: doublePrecision("full_feature_area_sqm"),
    withinAoiAreaSqm: doublePrecision("within_aoi_area_sqm"),

    /** Self-referencing lineage pointer for a proven split/merge predecessor; never inferred. */
    lineagePredecessorId: uuid("lineage_predecessor_id"),
    /** `continuous` | `split` | `merged` | `unknown` -- defaults to `unknown`, never fabricated. */
    lineageContinuity: varchar("lineage_continuity", { length: 20 })
      .notNull()
      .default("unknown"),

    sourceReleaseId: uuid("source_release_id")
      .notNull()
      .references(() => sourceReleases.id, { onDelete: "restrict" }),

    /**
     * static_lookup version window: null `versionValidFrom` means "current
     * reference, vintage disclosed via `sourceReleaseId`"; both nullable so
     * an unknown watermark is never silently substituted with a poll time.
     */
    versionValidFrom: timestamp("version_valid_from", { withTimezone: true }),
    versionValidTo: timestamp("version_valid_to", { withTimezone: true }),

    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (table) => [
    uniqueIndex("uq_boundary_versions_native_key").on(
      table.sourceNamespace,
      table.nativeFeatureKey,
      table.nativeFeatureVersion
    ),
    index("ix_boundary_versions_family_state").on(table.family, table.state, table.county),
    index("ix_boundary_versions_source_release").on(table.sourceReleaseId),
    check(
      "ck_boundary_versions_lineage_continuity",
      sql`${table.lineageContinuity} IN ('continuous', 'split', 'merged', 'unknown')`
    ),
    check(
      "ck_boundary_versions_derivation_method",
      sql`${table.derivationMethod} IN ('source_native', 'aoi_clipped', 'reprojected', 'generalized')`
    ),
  ]
);
