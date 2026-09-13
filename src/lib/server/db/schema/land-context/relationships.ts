/**
 * `land_context.place_office_topic_relationships` -- many-to-many evidence
 * linking a place/boundary subject to an organization or contact-route
 * object, per spec §"Reference records and identity"
 * ("Place-office-topic relationships").
 *
 * Subject/object are polymorphic (`subjectType`/`objectType` + id) rather
 * than FK columns per concrete table: a subject may be a `boundary_version`
 * or a bare `geometry` natural key (the existing `geo.geometry` Type-2
 * dimension this lane does not own), and an object may be an `organization`
 * or a `public_contact_route`. `assignmentMethod` records HOW the match was
 * made -- "names and nearest points alone do not prove an assignment" (spec)
 * -- and `reviewStatus` keeps an unreviewed crosswalk match distinguishable
 * from a reviewed one.
 */
import {
  uuid,
  varchar,
  text,
  jsonb,
  timestamp,
  check,
  index,
} from "drizzle-orm/pg-core";
import { sql } from "drizzle-orm";
import { landContextSchema } from "./shared";
import { sourceReleases } from "./source-releases";

export const placeOfficeTopicRelationships = landContextSchema.table(
  "place_office_topic_relationships",
  {
    id: uuid("id").defaultRandom().primaryKey(),

    /** `boundary_version` | `geometry` (the place/boundary side). */
    subjectType: varchar("subject_type", { length: 30 }).notNull(),
    subjectId: uuid("subject_id").notNull(),

    /** `organization` | `public_contact_route` (the office/route side). */
    objectType: varchar("object_type", { length: 30 }).notNull(),
    objectId: uuid("object_id").notNull(),

    /** e.g. `responsible_agency`, `records_assistance`, `adviser`, `introduction_forwarding`, `crosswalk_match`. */
    relationshipKind: varchar("relationship_kind", { length: 50 }).notNull(),

    /** Free-text or reference description of the geography this relationship applies to. */
    applicableGeography: text("applicable_geography"),
    documentedTopic: text("documented_topic"),

    sourceEvidenceUrl: text("source_evidence_url"),
    /** Structured crosswalk match evidence (matched name/code, score, method detail) when applicable. */
    crosswalkEvidence: jsonb("crosswalk_evidence"),

    /** Nullable effective interval -- unknown effective dates stay null, never defaulted. */
    effectiveFrom: timestamp("effective_from", { withTimezone: true }),
    effectiveTo: timestamp("effective_to", { withTimezone: true }),

    /** `source_documented` | `name_crosswalk` | `nearest_point_heuristic` | `manual_review`. */
    assignmentMethod: varchar("assignment_method", { length: 30 }).notNull(),
    /** `unreviewed` | `reviewed` | `rejected`. */
    reviewStatus: varchar("review_status", { length: 20 }).notNull().default("unreviewed"),

    sourceReleaseId: uuid("source_release_id")
      .notNull()
      .references(() => sourceReleases.id, { onDelete: "restrict" }),

    createdAt: timestamp("created_at", { withTimezone: true }).defaultNow().notNull(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).defaultNow().notNull(),
  },
  (table) => [
    index("ix_relationships_subject").on(table.subjectType, table.subjectId),
    index("ix_relationships_object").on(table.objectType, table.objectId),
    index("ix_relationships_kind").on(table.relationshipKind),
    index("ix_relationships_source_release").on(table.sourceReleaseId),
    check(
      "ck_relationships_subject_type",
      sql`${table.subjectType} IN ('boundary_version', 'geometry')`
    ),
    check(
      "ck_relationships_object_type",
      sql`${table.objectType} IN ('organization', 'public_contact_route')`
    ),
    check(
      "ck_relationships_assignment_method",
      sql`${table.assignmentMethod} IN (
        'source_documented', 'name_crosswalk', 'nearest_point_heuristic', 'manual_review'
      )`
    ),
    check(
      "ck_relationships_review_status",
      sql`${table.reviewStatus} IN ('unreviewed', 'reviewed', 'rejected')`
    ),
  ]
);
