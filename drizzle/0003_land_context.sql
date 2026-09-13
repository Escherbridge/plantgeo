CREATE SCHEMA "geo";
--> statement-breakpoint
CREATE SCHEMA "tracking";
--> statement-breakpoint
CREATE SCHEMA "land_context";
--> statement-breakpoint
CREATE TABLE "accounts" (
	"user_id" uuid NOT NULL,
	"type" text NOT NULL,
	"provider" text NOT NULL,
	"provider_account_id" text NOT NULL,
	"refresh_token" text,
	"access_token" text,
	"expires_at" integer,
	"token_type" text,
	"scope" text,
	"id_token" text,
	"session_state" text,
	CONSTRAINT "accounts_provider_provider_account_id_pk" PRIMARY KEY("provider","provider_account_id")
);
--> statement-breakpoint
CREATE TABLE "agricultural_solutions" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" varchar(100) NOT NULL,
	"description" text,
	"suitability_rules" jsonb DEFAULT '{}'::jsonb,
	"created_at" timestamp with time zone DEFAULT now(),
	CONSTRAINT "agricultural_solutions_name_unique" UNIQUE("name")
);
--> statement-breakpoint
CREATE TABLE "ai_conversations" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"user_id" uuid NOT NULL,
	"geohash" varchar(24) NOT NULL,
	"lat" double precision NOT NULL,
	"lon" double precision NOT NULL,
	"title" varchar(255) DEFAULT 'New Analysis' NOT NULL,
	"message_count" integer DEFAULT 0 NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "ai_message_feedback" (
	"message_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	"rating" varchar(16) NOT NULL,
	"reason" varchar(1000),
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "ai_message_feedback_message_id_user_id_pk" PRIMARY KEY("message_id","user_id"),
	CONSTRAINT "ai_message_feedback_rating_check" CHECK ("ai_message_feedback"."rating" IN ('helpful', 'not_helpful'))
);
--> statement-breakpoint
CREATE TABLE "ai_messages" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"conversation_id" uuid NOT NULL,
	"role" varchar(10) NOT NULL,
	"content" text NOT NULL,
	"structured_response" jsonb,
	"token_count" integer,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE "alert_subscriptions" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"user_id" uuid NOT NULL,
	"watched_location_id" uuid,
	"alert_type" varchar(50) NOT NULL,
	"threshold" jsonb,
	"email_enabled" boolean DEFAULT true,
	"in_app_enabled" boolean DEFAULT true,
	"created_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "tracking"."alerts" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"asset_id" uuid,
	"geofence_id" uuid,
	"type" varchar(50) NOT NULL,
	"message" text NOT NULL,
	"acknowledged" boolean DEFAULT false,
	"metadata" jsonb DEFAULT '{}'::jsonb,
	"created_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "api_keys" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"key_hash" text NOT NULL,
	"user_id" uuid,
	"team_id" uuid,
	"name" text,
	"permissions" jsonb DEFAULT '[]'::jsonb,
	"rate_limit" integer DEFAULT 1000,
	"last_used" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "tracking"."assets" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" varchar(100) NOT NULL,
	"type" varchar(50) DEFAULT 'vehicle',
	"status" varchar(20) DEFAULT 'offline',
	"metadata" jsonb DEFAULT '{}'::jsonb,
	"created_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "email_verification_tokens" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"user_id" uuid NOT NULL,
	"token_hash" text NOT NULL,
	"expires_at" timestamp with time zone NOT NULL,
	"used_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "environmental_alerts" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"user_id" uuid NOT NULL,
	"dedupe_key" varchar(160),
	"alert_type" varchar(50) NOT NULL,
	"severity" varchar(20) NOT NULL,
	"title" text NOT NULL,
	"body" text,
	"metadata" jsonb,
	"is_read" boolean DEFAULT false,
	"created_at" timestamp with time zone DEFAULT now(),
	CONSTRAINT "environmental_alerts_dedupe_key_unique" UNIQUE("dedupe_key")
);
--> statement-breakpoint
CREATE TABLE "geo"."features" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"layer_id" uuid NOT NULL,
	"geom" geometry(GEOMETRY,4326),
	"properties" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"status" varchar(20) DEFAULT 'published',
	"review_note" text,
	"geometry_id" uuid,
	"created_at" timestamp with time zone DEFAULT now(),
	"updated_at" timestamp with time zone DEFAULT now(),
	"data_available_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "tracking"."geofences" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" varchar(100) NOT NULL,
	"geometry" jsonb DEFAULT '{}'::jsonb NOT NULL,
	"alert_on_enter" boolean DEFAULT true,
	"alert_on_exit" boolean DEFAULT true,
	"created_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "geo"."geometry" (
	"geometry_id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"natural_key" varchar(255) NOT NULL,
	"version_valid_from" timestamp with time zone NOT NULL,
	"version_valid_to" timestamp with time zone,
	"geom_kind" varchar(16) NOT NULL,
	"geom" geometry(GEOMETRY,4326) NOT NULL,
	"centroid" geometry(POINT,4326) NOT NULL,
	"grid_name" varchar(100),
	"cell_key" varchar(180),
	"resolution_m" integer,
	"producer" varchar(100) NOT NULL,
	"superseded_by" uuid,
	"last_confirmed_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "uq_geometry_version" UNIQUE("natural_key","version_valid_from")
);
--> statement-breakpoint
CREATE TABLE "geo"."layers" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" varchar(100) NOT NULL,
	"type" varchar(50) DEFAULT 'vector' NOT NULL,
	"description" text,
	"style" jsonb DEFAULT '{}'::jsonb,
	"is_public" boolean DEFAULT false,
	"min_zoom" integer DEFAULT 0,
	"max_zoom" integer DEFAULT 22,
	"team_id" uuid,
	"sort_order" integer DEFAULT 0,
	"created_at" timestamp with time zone DEFAULT now(),
	"updated_at" timestamp with time zone DEFAULT now(),
	CONSTRAINT "layers_name_unique" UNIQUE("name")
);
--> statement-breakpoint
CREATE TABLE "open_plant_data" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"scientific_name" varchar(200) NOT NULL,
	"common_name" varchar(200),
	"solution_id" uuid,
	"climate_requirements" jsonb DEFAULT '{}'::jsonb,
	"water_requirements" jsonb DEFAULT '{}'::jsonb,
	"soil_requirements" jsonb DEFAULT '{}'::jsonb,
	"metadata" jsonb DEFAULT '{}'::jsonb,
	"created_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "open_tooling_data" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" varchar(200) NOT NULL,
	"solution_id" uuid,
	"category" varchar(100),
	"specifications" jsonb DEFAULT '{}'::jsonb,
	"metadata" jsonb DEFAULT '{}'::jsonb,
	"created_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "password_reset_tokens" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"user_id" uuid NOT NULL,
	"token_hash" text NOT NULL,
	"expires_at" timestamp with time zone NOT NULL,
	"used_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "tracking"."positions" (
	"time" timestamp with time zone NOT NULL,
	"asset_id" uuid NOT NULL,
	"geom" "geography(POINT,4326)",
	"heading" double precision,
	"speed" double precision,
	"altitude" double precision,
	"metadata" jsonb DEFAULT '{}'::jsonb
);
--> statement-breakpoint
CREATE TABLE "priority_zones" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"strategy_type" varchar(50) NOT NULL,
	"request_count" integer NOT NULL,
	"total_votes" integer NOT NULL,
	"centroid_lat" double precision,
	"centroid_lon" double precision,
	"geojson" jsonb,
	"computed_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "geo"."raster_release" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"collection" text NOT NULL,
	"property" text NOT NULL,
	"depth" text NOT NULL,
	"statistic" text NOT NULL,
	"source_name" text NOT NULL,
	"source_release" text NOT NULL,
	"source_url" text NOT NULL,
	"license_name" text NOT NULL,
	"attribution" text NOT NULL,
	"unit" text NOT NULL,
	"scale_divisor" integer NOT NULL,
	"nodata_value" double precision,
	"value_min" double precision,
	"value_max" double precision,
	"color_ramp" jsonb NOT NULL,
	"object_key" text NOT NULL,
	"archive_format" text NOT NULL,
	"checksum_sha256" text NOT NULL,
	"size_bytes" bigint NOT NULL,
	"min_zoom" integer NOT NULL,
	"max_zoom" integer NOT NULL,
	"bounds" geometry(POLYGON,4326) NOT NULL,
	"published_at" timestamp with time zone DEFAULT now() NOT NULL,
	"superseded_at" timestamp with time zone
);
--> statement-breakpoint
CREATE TABLE "request_votes" (
	"request_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	"created_at" timestamp with time zone DEFAULT now(),
	CONSTRAINT "request_votes_request_id_user_id_pk" PRIMARY KEY("request_id","user_id")
);
--> statement-breakpoint
CREATE TABLE "sessions" (
	"session_token" text PRIMARY KEY NOT NULL,
	"user_id" uuid NOT NULL,
	"expires" timestamp with time zone NOT NULL
);
--> statement-breakpoint
CREATE TABLE "strategy_requests" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"user_id" uuid,
	"team_id" uuid,
	"strategy_type" varchar(50) NOT NULL,
	"title" text NOT NULL,
	"description" text,
	"lat" double precision NOT NULL,
	"lon" double precision NOT NULL,
	"status" varchar(20) DEFAULT 'open',
	"vote_count" integer DEFAULT 0,
	"created_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "team_invitations" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"team_id" uuid NOT NULL,
	"email" text NOT NULL,
	"team_role" varchar(20) DEFAULT 'member' NOT NULL,
	"token_hash" text NOT NULL,
	"invited_by" uuid,
	"expires_at" timestamp with time zone NOT NULL,
	"accepted_at" timestamp with time zone,
	"accepted_by" uuid,
	"revoked_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "team_join_links" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"team_id" uuid NOT NULL,
	"code_hash" text NOT NULL,
	"team_role" varchar(20) DEFAULT 'viewer' NOT NULL,
	"allowed_email_domain" text,
	"max_uses" integer,
	"use_count" integer DEFAULT 0 NOT NULL,
	"expires_at" timestamp with time zone,
	"revoked_at" timestamp with time zone,
	"created_by" uuid,
	"created_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "team_members" (
	"team_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	"team_role" varchar(20) DEFAULT 'member',
	"joined_at" timestamp with time zone DEFAULT now(),
	CONSTRAINT "team_members_team_id_user_id_pk" PRIMARY KEY("team_id","user_id")
);
--> statement-breakpoint
CREATE TABLE "teams" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" text NOT NULL,
	"slug" varchar(100),
	"description" text,
	"org_type" varchar(50),
	"specialties" jsonb,
	"website" text,
	"service_area" jsonb,
	"is_verified" boolean DEFAULT false,
	"verified_at" timestamp with time zone,
	"created_by" uuid,
	"created_at" timestamp with time zone DEFAULT now(),
	CONSTRAINT "teams_slug_unique" UNIQUE("slug")
);
--> statement-breakpoint
CREATE TABLE "users" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"name" text,
	"email" text NOT NULL,
	"email_verified" timestamp with time zone,
	"image" text,
	"password_hash" text,
	"platform_role" varchar(20) DEFAULT 'contributor',
	"verified" boolean DEFAULT false,
	"created_at" timestamp with time zone DEFAULT now(),
	"active_team_id" uuid,
	CONSTRAINT "users_email_unique" UNIQUE("email")
);
--> statement-breakpoint
CREATE TABLE "verification_tokens" (
	"identifier" text NOT NULL,
	"token" text NOT NULL,
	"expires" timestamp with time zone NOT NULL,
	CONSTRAINT "verification_tokens_identifier_token_pk" PRIMARY KEY("identifier","token")
);
--> statement-breakpoint
CREATE TABLE "watched_locations" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"user_id" uuid NOT NULL,
	"name" text NOT NULL,
	"lat" double precision NOT NULL,
	"lon" double precision NOT NULL,
	"radius_km" integer DEFAULT 50,
	"created_at" timestamp with time zone DEFAULT now()
);
--> statement-breakpoint
CREATE TABLE "land_context"."source_releases" (
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
	CONSTRAINT "ck_source_releases_temporal_nature" CHECK ("land_context"."source_releases"."temporal_nature" IN ('static_lookup', 'release_series')),
	CONSTRAINT "ck_source_releases_admission_verdict" CHECK ("land_context"."source_releases"."admission_verdict" IN (
        'not_checked', 'current', 'stale', 'partial', 'source_unavailable',
        'withheld_by_terms', 'unsupported_history', 'out_of_coverage'
      ))
);
--> statement-breakpoint
CREATE TABLE "land_context"."boundary_versions" (
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
	CONSTRAINT "ck_boundary_versions_lineage_continuity" CHECK ("land_context"."boundary_versions"."lineage_continuity" IN ('continuous', 'split', 'merged', 'unknown')),
	CONSTRAINT "ck_boundary_versions_derivation_method" CHECK ("land_context"."boundary_versions"."derivation_method" IN ('source_native', 'aoi_clipped', 'reprojected', 'generalized'))
);
--> statement-breakpoint
CREATE TABLE "land_context"."organizations" (
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
CREATE TABLE "land_context"."public_contact_routes" (
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
	CONSTRAINT "ck_contact_routes_route_type" CHECK ("land_context"."public_contact_routes"."route_type" IN (
        'direct_responsible_agency', 'records_property_assistance',
        'subject_matter_adviser', 'introduction_forwarding'
      )),
	CONSTRAINT "ck_contact_routes_status" CHECK ("land_context"."public_contact_routes"."status" IN ('active', 'inactive', 'unverified'))
);
--> statement-breakpoint
CREATE TABLE "land_context"."place_office_topic_relationships" (
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
	CONSTRAINT "ck_relationships_subject_type" CHECK ("land_context"."place_office_topic_relationships"."subject_type" IN ('boundary_version', 'geometry')),
	CONSTRAINT "ck_relationships_object_type" CHECK ("land_context"."place_office_topic_relationships"."object_type" IN ('organization', 'public_contact_route')),
	CONSTRAINT "ck_relationships_assignment_method" CHECK ("land_context"."place_office_topic_relationships"."assignment_method" IN (
        'source_documented', 'name_crosswalk', 'nearest_point_heuristic', 'manual_review'
      )),
	CONSTRAINT "ck_relationships_review_status" CHECK ("land_context"."place_office_topic_relationships"."review_status" IN ('unreviewed', 'reviewed', 'rejected'))
);
--> statement-breakpoint
ALTER TABLE "accounts" ADD CONSTRAINT "accounts_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "ai_conversations" ADD CONSTRAINT "ai_conversations_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "ai_message_feedback" ADD CONSTRAINT "ai_message_feedback_message_id_ai_messages_id_fk" FOREIGN KEY ("message_id") REFERENCES "public"."ai_messages"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "ai_message_feedback" ADD CONSTRAINT "ai_message_feedback_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "ai_messages" ADD CONSTRAINT "ai_messages_conversation_id_ai_conversations_id_fk" FOREIGN KEY ("conversation_id") REFERENCES "public"."ai_conversations"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "alert_subscriptions" ADD CONSTRAINT "alert_subscriptions_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "alert_subscriptions" ADD CONSTRAINT "alert_subscriptions_watched_location_id_watched_locations_id_fk" FOREIGN KEY ("watched_location_id") REFERENCES "public"."watched_locations"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "api_keys" ADD CONSTRAINT "api_keys_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "api_keys" ADD CONSTRAINT "api_keys_team_id_teams_id_fk" FOREIGN KEY ("team_id") REFERENCES "public"."teams"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "email_verification_tokens" ADD CONSTRAINT "email_verification_tokens_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "environmental_alerts" ADD CONSTRAINT "environmental_alerts_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "geo"."features" ADD CONSTRAINT "features_layer_id_layers_id_fk" FOREIGN KEY ("layer_id") REFERENCES "geo"."layers"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "geo"."features" ADD CONSTRAINT "features_geometry_id_geometry_geometry_id_fk" FOREIGN KEY ("geometry_id") REFERENCES "geo"."geometry"("geometry_id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "geo"."layers" ADD CONSTRAINT "layers_team_id_teams_id_fk" FOREIGN KEY ("team_id") REFERENCES "public"."teams"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "open_plant_data" ADD CONSTRAINT "open_plant_data_solution_id_agricultural_solutions_id_fk" FOREIGN KEY ("solution_id") REFERENCES "public"."agricultural_solutions"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "open_tooling_data" ADD CONSTRAINT "open_tooling_data_solution_id_agricultural_solutions_id_fk" FOREIGN KEY ("solution_id") REFERENCES "public"."agricultural_solutions"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "password_reset_tokens" ADD CONSTRAINT "password_reset_tokens_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "tracking"."positions" ADD CONSTRAINT "positions_asset_id_assets_id_fk" FOREIGN KEY ("asset_id") REFERENCES "tracking"."assets"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "request_votes" ADD CONSTRAINT "request_votes_request_id_strategy_requests_id_fk" FOREIGN KEY ("request_id") REFERENCES "public"."strategy_requests"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "request_votes" ADD CONSTRAINT "request_votes_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "sessions" ADD CONSTRAINT "sessions_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "strategy_requests" ADD CONSTRAINT "strategy_requests_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "strategy_requests" ADD CONSTRAINT "strategy_requests_team_id_teams_id_fk" FOREIGN KEY ("team_id") REFERENCES "public"."teams"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "team_invitations" ADD CONSTRAINT "team_invitations_team_id_teams_id_fk" FOREIGN KEY ("team_id") REFERENCES "public"."teams"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "team_invitations" ADD CONSTRAINT "team_invitations_invited_by_users_id_fk" FOREIGN KEY ("invited_by") REFERENCES "public"."users"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "team_invitations" ADD CONSTRAINT "team_invitations_accepted_by_users_id_fk" FOREIGN KEY ("accepted_by") REFERENCES "public"."users"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "team_join_links" ADD CONSTRAINT "team_join_links_team_id_teams_id_fk" FOREIGN KEY ("team_id") REFERENCES "public"."teams"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "team_join_links" ADD CONSTRAINT "team_join_links_created_by_users_id_fk" FOREIGN KEY ("created_by") REFERENCES "public"."users"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "team_members" ADD CONSTRAINT "team_members_team_id_teams_id_fk" FOREIGN KEY ("team_id") REFERENCES "public"."teams"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "team_members" ADD CONSTRAINT "team_members_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "teams" ADD CONSTRAINT "teams_created_by_users_id_fk" FOREIGN KEY ("created_by") REFERENCES "public"."users"("id") ON DELETE no action ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "watched_locations" ADD CONSTRAINT "watched_locations_user_id_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."users"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "land_context"."boundary_versions" ADD CONSTRAINT "boundary_versions_source_release_id_source_releases_id_fk" FOREIGN KEY ("source_release_id") REFERENCES "land_context"."source_releases"("id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "land_context"."organizations" ADD CONSTRAINT "organizations_source_release_id_source_releases_id_fk" FOREIGN KEY ("source_release_id") REFERENCES "land_context"."source_releases"("id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "land_context"."public_contact_routes" ADD CONSTRAINT "public_contact_routes_organization_id_organizations_id_fk" FOREIGN KEY ("organization_id") REFERENCES "land_context"."organizations"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "land_context"."public_contact_routes" ADD CONSTRAINT "public_contact_routes_source_release_id_source_releases_id_fk" FOREIGN KEY ("source_release_id") REFERENCES "land_context"."source_releases"("id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "land_context"."place_office_topic_relationships" ADD CONSTRAINT "place_office_topic_relationships_source_release_id_source_releases_id_fk" FOREIGN KEY ("source_release_id") REFERENCES "land_context"."source_releases"("id") ON DELETE restrict ON UPDATE no action;--> statement-breakpoint
CREATE INDEX "ai_message_feedback_user_idx" ON "ai_message_feedback" USING btree ("user_id");--> statement-breakpoint
CREATE UNIQUE INDEX "api_keys_key_hash_unique" ON "api_keys" USING btree ("key_hash");--> statement-breakpoint
CREATE UNIQUE INDEX "email_verification_tokens_token_hash_unique" ON "email_verification_tokens" USING btree ("token_hash");--> statement-breakpoint
CREATE INDEX "email_verification_tokens_user_idx" ON "email_verification_tokens" USING btree ("user_id");--> statement-breakpoint
CREATE UNIQUE INDEX "features_layer_external_id_unique" ON "geo"."features" USING btree ("layer_id",("properties" ->> 'id')) WHERE "geo"."features"."properties" ? 'id';--> statement-breakpoint
CREATE INDEX "ix_features_geometry_id" ON "geo"."features" USING btree ("geometry_id");--> statement-breakpoint
CREATE INDEX "idx_features_layer_created_at" ON "geo"."features" USING btree ("layer_id","created_at");--> statement-breakpoint
CREATE INDEX "idx_features_layer_updated_at" ON "geo"."features" USING btree ("layer_id","updated_at");--> statement-breakpoint
CREATE UNIQUE INDEX "uq_geometry_current" ON "geo"."geometry" USING btree ("natural_key") WHERE "geo"."geometry"."version_valid_to" IS NULL;--> statement-breakpoint
CREATE UNIQUE INDEX "uq_geometry_grid_cell" ON "geo"."geometry" USING btree ("grid_name","cell_key") WHERE "geo"."geometry"."version_valid_to" IS NULL;--> statement-breakpoint
CREATE INDEX "ix_geometry_kind" ON "geo"."geometry" USING btree ("geom_kind","producer");--> statement-breakpoint
CREATE INDEX "ix_geometry_asof" ON "geo"."geometry" USING btree ("natural_key","version_valid_from" DESC);--> statement-breakpoint
CREATE UNIQUE INDEX "password_reset_tokens_token_hash_unique" ON "password_reset_tokens" USING btree ("token_hash");--> statement-breakpoint
CREATE INDEX "password_reset_tokens_user_idx" ON "password_reset_tokens" USING btree ("user_id");--> statement-breakpoint
CREATE UNIQUE INDEX "positions_asset_time_unique" ON "tracking"."positions" USING btree ("asset_id","time");--> statement-breakpoint
CREATE UNIQUE INDEX "ux_raster_release_live" ON "geo"."raster_release" USING btree ("collection","property","depth","statistic","archive_format") WHERE "geo"."raster_release"."superseded_at" IS NULL;--> statement-breakpoint
CREATE INDEX "ix_raster_release_live_collection" ON "geo"."raster_release" USING btree ("collection","property") WHERE "geo"."raster_release"."superseded_at" IS NULL;--> statement-breakpoint
CREATE UNIQUE INDEX "team_invitations_token_hash_unique" ON "team_invitations" USING btree ("token_hash");--> statement-breakpoint
CREATE INDEX "team_invitations_team_email_idx" ON "team_invitations" USING btree ("team_id","email");--> statement-breakpoint
CREATE INDEX "team_invitations_email_idx" ON "team_invitations" USING btree ("email");--> statement-breakpoint
CREATE UNIQUE INDEX "team_join_links_code_hash_unique" ON "team_join_links" USING btree ("code_hash");--> statement-breakpoint
CREATE INDEX "team_join_links_team_idx" ON "team_join_links" USING btree ("team_id");--> statement-breakpoint
CREATE UNIQUE INDEX "uq_source_releases_artifact_key" ON "land_context"."source_releases" USING btree ("artifact_object_key");--> statement-breakpoint
CREATE INDEX "ix_source_releases_publisher" ON "land_context"."source_releases" USING btree ("publisher");--> statement-breakpoint
CREATE UNIQUE INDEX "uq_boundary_versions_native_key" ON "land_context"."boundary_versions" USING btree ("source_namespace","native_feature_key","native_feature_version");--> statement-breakpoint
CREATE INDEX "ix_boundary_versions_family_state" ON "land_context"."boundary_versions" USING btree ("family","state","county");--> statement-breakpoint
CREATE INDEX "ix_boundary_versions_source_release" ON "land_context"."boundary_versions" USING btree ("source_release_id");--> statement-breakpoint
CREATE INDEX "ix_organizations_parent" ON "land_context"."organizations" USING btree ("parent_organization_id");--> statement-breakpoint
CREATE INDEX "ix_organizations_jurisdiction" ON "land_context"."organizations" USING btree ("jurisdiction_state","jurisdiction_county");--> statement-breakpoint
CREATE INDEX "ix_organizations_source_release" ON "land_context"."organizations" USING btree ("source_release_id");--> statement-breakpoint
CREATE INDEX "ix_contact_routes_organization" ON "land_context"."public_contact_routes" USING btree ("organization_id");--> statement-breakpoint
CREATE INDEX "ix_contact_routes_route_type" ON "land_context"."public_contact_routes" USING btree ("route_type");--> statement-breakpoint
CREATE INDEX "ix_contact_routes_source_release" ON "land_context"."public_contact_routes" USING btree ("source_release_id");--> statement-breakpoint
CREATE INDEX "ix_relationships_subject" ON "land_context"."place_office_topic_relationships" USING btree ("subject_type","subject_id");--> statement-breakpoint
CREATE INDEX "ix_relationships_object" ON "land_context"."place_office_topic_relationships" USING btree ("object_type","object_id");--> statement-breakpoint
CREATE INDEX "ix_relationships_kind" ON "land_context"."place_office_topic_relationships" USING btree ("relationship_kind");--> statement-breakpoint
CREATE INDEX "ix_relationships_source_release" ON "land_context"."place_office_topic_relationships" USING btree ("source_release_id");