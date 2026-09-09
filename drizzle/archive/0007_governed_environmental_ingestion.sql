-- Governed drought + soil ingestion targets.
--
-- geo.drought_areas holds one US Drought Monitor D0-D4 polygon per weekly
-- release, as real PostGIS geometry rather than a JSON blob, so the map layer
-- can be served bbox-clipped and simplified in the database instead of shipping
-- the full ~19 MB national collection to every client. valid_date is the USDM
-- Tuesday the release is valid for -- it is the request parameter the upstream
-- file was fetched by, never inferred from the payload, which carries no date.
CREATE TABLE IF NOT EXISTS "geo"."drought_areas" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"valid_date" varchar(10) NOT NULL,
	"dm_category" integer NOT NULL,
	"geom" geometry(MULTIPOLYGON,4326) NOT NULL,
	"source_url" text NOT NULL,
	"ingested_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "drought_areas_dm_category_range" CHECK ("dm_category" BETWEEN 0 AND 4),
	CONSTRAINT "drought_areas_valid_date_iso" CHECK ("valid_date" ~ '^\d{4}-\d{2}-\d{2}$')
);
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "drought_areas_valid_date_category_unique" ON "geo"."drought_areas" ("valid_date","dm_category");
--> statement-breakpoint
CREATE INDEX IF NOT EXISTS "drought_areas_valid_date_idx" ON "geo"."drought_areas" ("valid_date");
--> statement-breakpoint
-- Bbox reads prefilter on the bounding box before ST_Intersection clips.
CREATE INDEX IF NOT EXISTS "drought_areas_geom_gist" ON "geo"."drought_areas" USING GIST ("geom");
--> statement-breakpoint
-- SoilGrids point cache: provenance plus a negative-cache marker, so a verified
-- upstream no-data cell (SoilGrids returns null means outside its coverage) is
-- recorded once instead of re-queried on every request. "complete" false is an
-- observation about the upstream, never a substitute value.
ALTER TABLE "soil_grid_cache" ADD COLUMN IF NOT EXISTS "ocd" double precision;
--> statement-breakpoint
ALTER TABLE "soil_grid_cache" ADD COLUMN IF NOT EXISTS "complete" boolean DEFAULT false NOT NULL;
--> statement-breakpoint
ALTER TABLE "soil_grid_cache" ADD COLUMN IF NOT EXISTS "source_url" text;
--> statement-breakpoint
-- Cache lookups round to a fixed grid, so the cell is the identity. Duplicate
-- rows from any earlier uncoordinated writer are collapsed to the newest before
-- the constraint is added; the table is empty on every current deployment.
DELETE FROM "soil_grid_cache" a
USING "soil_grid_cache" b
WHERE a."lat" = b."lat"
  AND a."lon" = b."lon"
  AND a.ctid < b.ctid;
--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "soil_grid_cache_cell_unique" ON "soil_grid_cache" ("lat","lon");
