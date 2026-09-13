/**
 * `land_context.source_releases` -- one row per admitted publisher
 * distribution/version, per spec §"Reference records and identity"
 * ("Source/releases") and §"Admission, refresh, gaps and absences".
 *
 * Every other table in this lane FKs to a row here so a record's rights,
 * field permission, capture facts and admission verdict are always
 * traceable to one immutable release, never asserted loose on the record.
 */
import {
  uuid,
  varchar,
  text,
  jsonb,
  timestamp,
  check,
  uniqueIndex,
  index,
} from "drizzle-orm/pg-core";
import { sql } from "drizzle-orm";
import { landContextSchema, ADMISSION_VERDICTS, TEMPORAL_NATURES } from "./shared";

export const sourceReleases = landContextSchema.table(
  "source_releases",
  {
    id: uuid("id").defaultRandom().primaryKey(),

    publisher: text("publisher").notNull(),
    canonicalEndpoint: text("canonical_endpoint").notNull(),

    /** Free-text rights/attribution summary; the admission gate, not a license parse. */
    rightsSummary: text("rights_summary").notNull(),
    attribution: text("attribution").notNull(),
    /**
     * Explicit allow-list of field names this release may expose downstream.
     * A bulk source lacking a nonpersonal projection stays unresolved rather
     * than admitted with private fields silently dropped at read time --
     * see spec §"Reference records and identity" closing paragraph.
     */
    permittedFields: jsonb("permitted_fields").notNull().$type<string[]>(),

    /**
     * The source's own version/watermark string, when the source publishes
     * one. Distinct from `sourceWatermarkAt`, which is the change-based
     * timestamp per layer-lanes.md §1a -- never a poll clock.
     */
    sourceVersion: varchar("source_version", { length: 120 }),
    sourceWatermarkAt: timestamp("source_watermark_at", { withTimezone: true }),

    /** When this artifact was captured -- the download clock, not a release date. */
    capturedAt: timestamp("captured_at", { withTimezone: true }).notNull(),

    /** Immutable artifact identity (object-store key), never mutated in place. */
    artifactObjectKey: text("artifact_object_key").notNull(),
    artifactChecksumSha256: varchar("artifact_checksum_sha256", { length: 64 }).notNull(),

    schemaVersion: varchar("schema_version", { length: 20 }).notNull(),
    normalizationVersion: varchar("normalization_version", { length: 20 }).notNull(),

    coverageDescription: text("coverage_description"),

    /** static_lookup for boundary/office/territory; release_series for dated crop facets. */
    temporalNature: varchar("temporal_nature", { length: 20 })
      .notNull()
      .default("static_lookup")
      .$type<(typeof TEMPORAL_NATURES)[number]>(),

    admissionVerdict: varchar("admission_verdict", { length: 30 })
      .notNull()
      .default("not_checked")
      .$type<(typeof ADMISSION_VERDICTS)[number]>(),

    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (table) => [
    uniqueIndex("uq_source_releases_artifact_key").on(table.artifactObjectKey),
    index("ix_source_releases_publisher").on(table.publisher),
    check(
      "ck_source_releases_temporal_nature",
      sql`${table.temporalNature} IN ('static_lookup', 'release_series')`
    ),
    check(
      "ck_source_releases_admission_verdict",
      sql`${table.admissionVerdict} IN (
        'not_checked', 'current', 'stale', 'partial', 'source_unavailable',
        'withheld_by_terms', 'unsupported_history', 'out_of_coverage'
      )`
    ),
  ]
);
