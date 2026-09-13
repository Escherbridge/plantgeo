-- PlantGeo land_context reference plane.
--
-- Registers the five relations from
-- conductor/tracks/pnw_land_context_reference_plane_20260911/spec.md
-- ("Reference records and identity"): boundary_versions, organizations,
-- public_contact_routes, place_office_topic_relationships, source_releases.
--
-- This is a PROPOSED forward migration hand-written to match the Drizzle
-- table definitions in src/lib/server/db/schema/land-context/*.ts. It is
-- NOT yet registered in drizzle/meta/_journal.json or pinned in
-- src/lib/server/db/migration-contract.ts -- see
-- src/lib/server/db/schema/land-context/shared.ts module doc. An integrator
-- must add both, then run this migration, in the same review that wires
-- these tables into schema.ts.
--
-- Static-lookup nature per conductor/code_styleguides/layer-lanes.md §1a:
-- these tables carry a version watermark, never a daily/forecast column.

CREATE SCHEMA IF NOT EXISTS land_context;
--> statement-breakpoint

CREATE TABLE land_context.source_releases (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"publisher" text NOT NULL,
	"canonical_endpoint" text NOT NULL,
	"rights_summary" text NOT NULL,
	"attribution" text NOT NULL,
	"permitted_fields" jsonb NOT NULL,
	"source_version" varchar(120),
	"source_watermark_at" timestamp with time zone,
	"captured_at" timestamp with time zone NOT NULL,
	"artifact_object_key" text NOT NULL,
	"artifact_checksum_sha256" varchar(64) NOT NULL,
	"schema_version" varchar(20) NOT NULL,
	"normalization_version" varchar(20) NOT NULL,
	"coverage_description" text,
	"temporal_nature" varchar(20) DEFAULT 'static_lookup' NOT NULL,
	"admission_verdict" varchar(30) DEFAULT 'not_checked' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "ck_source_releases_temporal_nature" CHECK ("temporal_nature" IN ('static_lookup', 'release_series')),
	CONSTRAINT "ck_source_releases_admission_verdict" CHECK ("admission_verdict" IN (
		'not_checked', 'current', 'stale', 'partial', 'source_unavailable',
		'withheld_by_terms', 'unsupported_history', 'out_of_coverage'
	))
);
--> statement-breakpoint

CREATE UNIQUE INDEX "uq_source_releases_artifact_key" ON land_context.source_releases USING btree ("artifact_object_key");
--> statement-breakpoint
CREATE INDEX "ix_source_releases_publisher" ON land_context.source_releases USING btree ("publisher");
--> statement-breakpoint

CREATE TABLE land_context.boundary_versions (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"source_namespace" varchar(100) NOT NULL,
	"native_feature_key" varchar(255) NOT NULL,
	"native_feature_version" varchar(100),
	"family" varchar(60) NOT NULL,
	"interest_type" varchar(80) NOT NULL,
	"state" varchar(2) NOT NULL,
	"county" varchar(100),
	"nonpersonal_attributes" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"geom" geometry(GEOMETRY,4326) NOT NULL,
	"bbox_min_lon" double precision NOT NULL,
	"bbox_min_lat" double precision NOT NULL,
	"bbox_max_lon" double precision NOT NULL,
	"bbox_max_lat" double precision NOT NULL,
	"crs" varchar(32) DEFAULT 'EPSG:4326' NOT NULL,
	"record_url" text,
	"source_accuracy" text,
	"derivation_method" varchar(30) DEFAULT 'source_native' NOT NULL,
	"clipped_to_aoi" boolean DEFAULT false NOT NULL,
	"full_feature_area_sqm" double precision,
	"within_aoi_area_sqm" double precision,
	"lineage_predecessor_id" uuid,
	"lineage_continuity" varchar(20) DEFAULT 'unknown' NOT NULL,
	"source_release_id" uuid NOT NULL,
	"version_valid_from" timestamp with time zone,
	"version_valid_to" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "ck_boundary_versions_lineage_continuity" CHECK ("lineage_continuity" IN ('continuous', 'split', 'merged', 'unknown')),
	CONSTRAINT "ck_boundary_versions_derivation_method" CHECK ("derivation_method" IN ('source_native', 'aoi_clipped', 'reprojected', 'generalized'))
);
--> statement-breakpoint

ALTER TABLE land_context.boundary_versions
	ADD CONSTRAINT "boundary_versions_source_release_id_fk"
	FOREIGN KEY ("source_release_id") REFERENCES land_context.source_releases("id") ON DELETE RESTRICT;
--> statement-breakpoint

-- Self-referencing lineage pointer; deferred so a split/merge insert-then-repoint
-- can happen in one transaction without a temporary null, mirroring
-- geo.geometry.superseded_by in drizzle/0000_baseline.sql.
ALTER TABLE land_context.boundary_versions
	ADD CONSTRAINT "boundary_versions_lineage_predecessor_id_fk"
	FOREIGN KEY ("lineage_predecessor_id") REFERENCES land_context.boundary_versions("id")
	DEFERRABLE INITIALLY DEFERRED;
--> statement-breakpoint

CREATE UNIQUE INDEX "uq_boundary_versions_native_key" ON land_context.boundary_versions
	USING btree ("source_namespace", "native_feature_key", "native_feature_version");
--> statement-breakpoint
CREATE INDEX "ix_boundary_versions_family_state" ON land_context.boundary_versions
	USING btree ("family", "state", "county");
--> statement-breakpoint
CREATE INDEX "ix_boundary_versions_source_release" ON land_context.boundary_versions
	USING btree ("source_release_id");
--> statement-breakpoint
CREATE INDEX "ix_boundary_versions_geom" ON land_context.boundary_versions USING gist ("geom");
--> statement-breakpoint

CREATE TABLE land_context.organizations (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"identity_kind" varchar(20) NOT NULL,
	"official_public_name" text NOT NULL,
	"org_type" varchar(80) NOT NULL,
	"parent_organization_id" uuid,
	"office_point" geometry(POINT,4326),
	"jurisdiction_state" varchar(2),
	"jurisdiction_county" varchar(100),
	"jurisdiction_description" text,
	"source_release_id" uuid NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint

ALTER TABLE land_context.organizations
	ADD CONSTRAINT "organizations_parent_organization_id_fk"
	FOREIGN KEY ("parent_organization_id") REFERENCES land_context.organizations("id");
--> statement-breakpoint
ALTER TABLE land_context.organizations
	ADD CONSTRAINT "organizations_source_release_id_fk"
	FOREIGN KEY ("source_release_id") REFERENCES land_context.source_releases("id") ON DELETE RESTRICT;
--> statement-breakpoint

CREATE INDEX "ix_organizations_parent" ON land_context.organizations USING btree ("parent_organization_id");
--> statement-breakpoint
CREATE INDEX "ix_organizations_jurisdiction" ON land_context.organizations
	USING btree ("jurisdiction_state", "jurisdiction_county");
--> statement-breakpoint
CREATE INDEX "ix_organizations_source_release" ON land_context.organizations USING btree ("source_release_id");
--> statement-breakpoint

CREATE TABLE land_context.public_contact_routes (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"organization_id" uuid NOT NULL,
	"route_type" varchar(40) NOT NULL,
	"documented_topic" text NOT NULL,
	"documented_help" text,
	"official_inquiry_url" text,
	"public_business_phone" varchar(30),
	"public_business_email" varchar(255),
	"published_professional_name" text,
	"status" varchar(20) DEFAULT 'active' NOT NULL,
	"forwarding_documented" boolean DEFAULT false NOT NULL,
	"verification_evidence_url" text,
	"verified_at" timestamp with time zone,
	"source_release_id" uuid NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "ck_contact_routes_route_type" CHECK ("route_type" IN (
		'direct_responsible_agency', 'records_property_assistance',
		'subject_matter_adviser', 'introduction_forwarding'
	)),
	CONSTRAINT "ck_contact_routes_status" CHECK ("status" IN ('active', 'inactive', 'unverified'))
);
--> statement-breakpoint

ALTER TABLE land_context.public_contact_routes
	ADD CONSTRAINT "contact_routes_organization_id_fk"
	FOREIGN KEY ("organization_id") REFERENCES land_context.organizations("id") ON DELETE CASCADE;
--> statement-breakpoint
ALTER TABLE land_context.public_contact_routes
	ADD CONSTRAINT "contact_routes_source_release_id_fk"
	FOREIGN KEY ("source_release_id") REFERENCES land_context.source_releases("id") ON DELETE RESTRICT;
--> statement-breakpoint

CREATE INDEX "ix_contact_routes_organization" ON land_context.public_contact_routes USING btree ("organization_id");
--> statement-breakpoint
CREATE INDEX "ix_contact_routes_route_type" ON land_context.public_contact_routes USING btree ("route_type");
--> statement-breakpoint
CREATE INDEX "ix_contact_routes_source_release" ON land_context.public_contact_routes USING btree ("source_release_id");
--> statement-breakpoint

CREATE TABLE land_context.place_office_topic_relationships (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"subject_type" varchar(30) NOT NULL,
	"subject_id" uuid NOT NULL,
	"object_type" varchar(30) NOT NULL,
	"object_id" uuid NOT NULL,
	"relationship_kind" varchar(50) NOT NULL,
	"applicable_geography" text,
	"documented_topic" text,
	"source_evidence_url" text,
	"crosswalk_evidence" jsonb,
	"effective_from" timestamp with time zone,
	"effective_to" timestamp with time zone,
	"assignment_method" varchar(30) NOT NULL,
	"review_status" varchar(20) DEFAULT 'unreviewed' NOT NULL,
	"source_release_id" uuid NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "ck_relationships_subject_type" CHECK ("subject_type" IN ('boundary_version', 'geometry')),
	CONSTRAINT "ck_relationships_object_type" CHECK ("object_type" IN ('organization', 'public_contact_route')),
	CONSTRAINT "ck_relationships_assignment_method" CHECK ("assignment_method" IN (
		'source_documented', 'name_crosswalk', 'nearest_point_heuristic', 'manual_review'
	)),
	CONSTRAINT "ck_relationships_review_status" CHECK ("review_status" IN ('unreviewed', 'reviewed', 'rejected'))
);
--> statement-breakpoint

ALTER TABLE land_context.place_office_topic_relationships
	ADD CONSTRAINT "relationships_source_release_id_fk"
	FOREIGN KEY ("source_release_id") REFERENCES land_context.source_releases("id") ON DELETE RESTRICT;
--> statement-breakpoint

CREATE INDEX "ix_relationships_subject" ON land_context.place_office_topic_relationships
	USING btree ("subject_type", "subject_id");
--> statement-breakpoint
CREATE INDEX "ix_relationships_object" ON land_context.place_office_topic_relationships
	USING btree ("object_type", "object_id");
--> statement-breakpoint
CREATE INDEX "ix_relationships_kind" ON land_context.place_office_topic_relationships USING btree ("relationship_kind");
--> statement-breakpoint
CREATE INDEX "ix_relationships_source_release" ON land_context.place_office_topic_relationships USING btree ("source_release_id");
