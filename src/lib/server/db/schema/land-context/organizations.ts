/**
 * `land_context.organizations` -- publisher or reviewed-internal-registry
 * identity for agencies/offices/utilities, per spec §"Reference records and
 * identity" ("Organizations/offices"). An `officePoint` aids navigation
 * only; it is never evidence of jurisdiction or authority by itself (spec
 * §"Bounded readers and agent contract").
 */
import { uuid, varchar, text, timestamp, index } from "drizzle-orm/pg-core";
import { landContextSchema, spatialPoint } from "./shared";
import { sourceReleases } from "./source-releases";

export const organizations = landContextSchema.table(
  "organizations",
  {
    id: uuid("id").defaultRandom().primaryKey(),

    /** `publisher` (the source itself is the identity) or `internal_registry` (reviewed crosswalk entry). */
    identityKind: varchar("identity_kind", { length: 20 }).notNull(),

    officialPublicName: text("official_public_name").notNull(),
    /** e.g. `federal_agency`, `state_agency`, `county_office`, `utility`, `tribal_authority`. */
    orgType: varchar("org_type", { length: 80 }).notNull(),

    parentOrganizationId: uuid("parent_organization_id"),

    /** Navigation aid only -- see module doc; never a jurisdiction claim by itself. */
    officePoint: spatialPoint("office_point"),

    jurisdictionState: varchar("jurisdiction_state", { length: 2 }),
    jurisdictionCounty: varchar("jurisdiction_county", { length: 100 }),
    /** Free-text jurisdiction reference for cases a state/county pair can't express (e.g. a district). */
    jurisdictionDescription: text("jurisdiction_description"),

    sourceReleaseId: uuid("source_release_id")
      .notNull()
      .references(() => sourceReleases.id, { onDelete: "restrict" }),

    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (table) => [
    index("ix_organizations_parent").on(table.parentOrganizationId),
    index("ix_organizations_jurisdiction").on(table.jurisdictionState, table.jurisdictionCounty),
    index("ix_organizations_source_release").on(table.sourceReleaseId),
  ]
);
