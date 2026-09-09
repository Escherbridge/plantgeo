-- The conformed Type-2 geometry dimension.
--
-- geo.geometry is where feature identity is defined once. natural_key names the
-- PLACE across its whole life; geometry_id names ONE VERSION of that place, and a
-- fact points at a geometry_id so it stays pinned to the shape that was true when
-- it was observed. Rationale that does not fit on one line lives in
-- src/lib/server/db/AGENTS.md, section geometry-dimension.
--
-- The key format is lifted verbatim from the identity contract in
-- services/agri-data-service/src/agri_data_service/ingest/identity.py, which is the
-- only place allowed to mint it (FeatureIdentity.natural_key):
--
--     natural_key = f"{producer}:{producer_local_id}"
--
-- One ASCII colon after the producer token and nothing else -- no padding, no
-- quoting, no escaping. The producer-local id itself contains further colons for
-- three of the four live layers, and two of those embed a full ISO-8601 timestamp,
-- so the namespace separator is the FIRST colon and never the last. Read the
-- producer column instead of splitting the key at all.
--
-- Producer token per live layer, from identity.PRODUCER_BY_LAYER_NAME:
--
--     fire-detections      -> firms
--     water-gauges         -> usgs-nwis
--     weather-observations -> open-meteo
--     fire-perimeters      -> wfigs
--
-- PostGIS types are written as public.geometry deliberately. This table is named
-- "geometry", so every table carries a composite type of the same name, and an
-- unqualified type reference resolves to that composite for any session whose
-- search_path puts geo ahead of public.
CREATE TABLE IF NOT EXISTS "geo"."geometry" (
	"geometry_id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"natural_key" varchar(255) NOT NULL,
	"version_valid_from" timestamp with time zone NOT NULL,
	"version_valid_to" timestamp with time zone,
	"geom_kind" varchar(16) NOT NULL,
	"geom" public.geometry(GEOMETRY,4326) NOT NULL,
	-- Plain column written at insert, never GENERATED ... STORED: a stored generated
	-- column calling a PostGIS function is what made the warehouse unrestorable from
	-- its own pg_dump. Two of those are being deleted this quarter; this is not a third.
	"centroid" public.geometry(POINT,4326) NOT NULL,
	"grid_name" varchar(100),
	"cell_key" varchar(180),
	"resolution_m" integer,
	"producer" varchar(100) NOT NULL,
	-- DEFERRABLE is what makes closing a version possible at all. ck_geometry_supersede is
	-- immediate (every CHECK is), so closing v1 must write version_valid_to and superseded_by
	-- in one statement -- but the successor cannot exist yet, because uq_geometry_current is a
	-- partial INDEX and rejects a second open row. The only legal order is close, then insert,
	-- then let COMMIT validate this FK. See AGENTS.md, section geometry-dimension.
	"superseded_by" uuid REFERENCES "geo"."geometry"("geometry_id") ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED,
	-- Staleness only: the last run that saw this version unchanged upstream. Never a
	-- validity bound, or it drifts into a second, disagreeing version_valid_to.
	"last_confirmed_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "uq_geometry_version" UNIQUE ("natural_key","version_valid_from"),
	CONSTRAINT "ck_geometry_version_order" CHECK (
		"version_valid_to" IS NULL OR "version_valid_to" > "version_valid_from"),
	-- A half-closed version -- closed, with no successor -- is unrepresentable.
	CONSTRAINT "ck_geometry_supersede" CHECK (
		("version_valid_to" IS NULL AND "superseded_by" IS NULL)
		OR ("version_valid_to" IS NOT NULL AND "superseded_by" IS NOT NULL)),
	CONSTRAINT "ck_geometry_kind" CHECK (
		"geom_kind" IN ('point','polygon','line','grid_cell')),
	CONSTRAINT "ck_geometry_cell_fields" CHECK (
		("geom_kind" <> 'grid_cell' AND "grid_name" IS NULL AND "cell_key" IS NULL
			AND "resolution_m" IS NULL)
		OR ("geom_kind" = 'grid_cell' AND "grid_name" IS NOT NULL AND "cell_key" IS NOT NULL
			AND "resolution_m" IS NOT NULL AND "resolution_m" > 0)),
	-- Cross-producer interleaving into one version chain is structurally unrepresentable.
	CONSTRAINT "ck_geometry_natural_key_namespaced" CHECK (
		"natural_key" LIKE "producer" || ':%')
);
--> statement-breakpoint
-- Exactly one current version per place.
CREATE UNIQUE INDEX IF NOT EXISTS "uq_geometry_current" ON "geo"."geometry" ("natural_key") WHERE "version_valid_to" IS NULL;
--> statement-breakpoint
-- A grid cell's key is unique among current versions only.
CREATE UNIQUE INDEX IF NOT EXISTS "uq_geometry_grid_cell" ON "geo"."geometry" ("grid_name","cell_key") WHERE "version_valid_to" IS NULL;
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "ix_geometry_geom" ON "geo"."geometry" USING GIST ("geom");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "ix_geometry_centroid" ON "geo"."geometry" USING GIST ("centroid");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "ix_geometry_kind" ON "geo"."geometry" ("geom_kind","producer");
--> statement-breakpoint
-- As-of reads, and the cheap answer to "how many versions opened in this window",
-- which is what the ingest circuit breaker measures.
CREATE INDEX IF NOT EXISTS "ix_geometry_asof" ON "geo"."geometry" ("natural_key","version_valid_from" DESC);
--> statement-breakpoint
CREATE OR REPLACE VIEW "geo"."geometry_current" AS
	SELECT * FROM "geo"."geometry" WHERE "version_valid_to" IS NULL;
--> statement-breakpoint
-- geo.features holds CURRENT state, refreshed in place, so this points at the current
-- version and must be repointed whenever a version closes. RESTRICT, never CASCADE:
-- facts pin a version permanently, so dimension rows are never deleted.
ALTER TABLE "geo"."features" ADD COLUMN IF NOT EXISTS "geometry_id" uuid REFERENCES "geo"."geometry"("geometry_id") ON DELETE RESTRICT;
--> statement-breakpoint
-- RESTRICT is enforced by scanning the referencing side; without this it is a seq scan.
CREATE INDEX IF NOT EXISTS "ix_features_geometry_id" ON "geo"."features" ("geometry_id");
--> statement-breakpoint
-- Pin the search_path of every pre-existing geo routine. Naming this table "geometry"
-- inside schema "geo" creates a composite type geo.geometry that shadows PostGIS's
-- public.geometry on any search_path with geo ahead of public; all six routines below
-- declare locals as a bare "geometry" and would resolve them to the row type and fail at
-- runtime. Bodies are untouched and reference every table schema-qualified, so pinning
-- to public,pg_catalog is behaviour-preserving. See src/lib/server/db/AGENTS.md
-- §geometry-dimension.
ALTER FUNCTION "geo"."fire_risk_tiles"(integer,integer,integer) SET search_path = public, pg_catalog;--> statement-breakpoint
ALTER FUNCTION "geo"."sensor_tiles"(integer,integer,integer) SET search_path = public, pg_catalog;--> statement-breakpoint
ALTER FUNCTION "geo"."intervention_tiles"(integer,integer,integer) SET search_path = public, pg_catalog;--> statement-breakpoint
ALTER FUNCTION "geo"."building_tiles"(integer,integer,integer) SET search_path = public, pg_catalog;--> statement-breakpoint
ALTER FUNCTION "geo"."sync_feature_geom_from_properties"() SET search_path = public, pg_catalog;--> statement-breakpoint
ALTER FUNCTION "geo"."sync_historical_point_geom"() SET search_path = public, pg_catalog;
