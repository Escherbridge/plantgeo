/**
 * `land_context.public_contact_routes` -- office/program-scoped public
 * contact routes, per spec §"Reference records and identity" ("Public
 * contact routes") and §"Contact meanings and contact discovery".
 *
 * HARD CONSTRAINT (spec, repeated verbatim in two sections): never a
 * private-owner-name or personal-contact field. This table has no such
 * column, structurally -- `publishedProfessionalName` is scoped to an
 * optional PUBLISHED professional role name (e.g. "District Ranger"), not a
 * private individual's contact identity.
 *
 * `forwardingDocumented` is the explicit flag the spec requires for the
 * fourth route meaning (introduction/forwarding): it defaults to `false`
 * ("not documented") and must never be set `true` by inference -- only by a
 * proven, cited source statement that the office actually forwards inquiries.
 */
import {
  uuid,
  varchar,
  text,
  boolean,
  timestamp,
  check,
  index,
} from "drizzle-orm/pg-core";
import { sql } from "drizzle-orm";
import { landContextSchema, CONTACT_ROUTE_TYPES } from "./shared";
import { organizations } from "./organizations";
import { sourceReleases } from "./source-releases";

export const publicContactRoutes = landContextSchema.table(
  "public_contact_routes",
  {
    id: uuid("id").defaultRandom().primaryKey(),

    organizationId: uuid("organization_id")
      .notNull()
      .references(() => organizations.id, { onDelete: "cascade" }),

    /** One of the four typed route meanings from spec §"Contact meanings and contact discovery". */
    routeType: varchar("route_type", { length: 40 })
      .notNull()
      .$type<(typeof CONTACT_ROUTE_TYPES)[number]>(),

    documentedTopic: text("documented_topic").notNull(),
    documentedHelp: text("documented_help"),

    officialInquiryUrl: text("official_inquiry_url"),
    publicBusinessPhone: varchar("public_business_phone", { length: 30 }),
    publicBusinessEmail: varchar("public_business_email", { length: 255 }),
    /** Optional PUBLISHED professional role name only -- never a private/personal name. See module doc. */
    publishedProfessionalName: text("published_professional_name"),

    status: varchar("status", { length: 20 }).notNull().default("active"),

    /**
     * Explicit tri-state-by-default boolean: `false` means "not documented",
     * never "confirmed absent" -- the spec's "unknown forwarding capability
     * remains unknown." A later proven negative gets its own evidence row,
     * not a flip of this flag to a false-negative claim.
     */
    forwardingDocumented: boolean("forwarding_documented").notNull().default(false),

    verificationEvidenceUrl: text("verification_evidence_url"),
    /** When the route was last confirmed reachable/current -- NOT proof a person still holds a role. */
    verifiedAt: timestamp("verified_at", { withTimezone: true }),

    sourceReleaseId: uuid("source_release_id")
      .notNull()
      .references(() => sourceReleases.id, { onDelete: "restrict" }),

    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (table) => [
    index("ix_contact_routes_organization").on(table.organizationId),
    index("ix_contact_routes_route_type").on(table.routeType),
    index("ix_contact_routes_source_release").on(table.sourceReleaseId),
    check(
      "ck_contact_routes_route_type",
      sql`${table.routeType} IN (
        'direct_responsible_agency', 'records_property_assistance',
        'subject_matter_adviser', 'introduction_forwarding'
      )`
    ),
    check(
      "ck_contact_routes_status",
      sql`${table.status} IN ('active', 'inactive', 'unverified')`
    ),
  ]
);
