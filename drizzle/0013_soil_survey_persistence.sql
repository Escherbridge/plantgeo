-- Persist USDA SSURGO map units in the warehouse instead of proxying Soil Data
-- Access on every viewport request.
--
-- Before this migration `geo.layers` held 9 rows and none was soil (verified
-- against production 2026-08-05), so the one soil layer that renders anything --
-- "soil-survey" -- was the only map layer with no warehouse presence at all: it
-- asked SDA live per viewport behind a Redis day-cache, and a zoomed-out
-- aggregate cost a measured 30-40 s because it tiled the viewport into 9 separate
-- upstream calls. Column set and shape match the original seed in
-- 0001_handy_riptide.sql:307-317 exactly, the same way 0011 added burn-severity.
--
-- No tile function is created here on purpose. soil-survey is drawn by
-- SoilSurveyLayer.tsx from a GeoJSON source fed by the `environmental.getSoilSurvey`
-- tRPC procedure, not by Martin -- so this migration needs no Martin restart, unlike
-- 0009 and 0012 which did add function sources.
INSERT INTO geo.layers (name, type, description, is_public)
VALUES
  ('soil-survey', 'vector', 'USDA SSURGO soil map-unit delineations, persisted per survey-area vintage', true)
ON CONFLICT (name) DO NOTHING;
--> statement-breakpoint

-- The coverage ledger, and the reason persistence needs one at all.
--
-- Proxying had exactly one honest-empty failure mode: SDA answering with no map
-- units, which really does mean "nothing surveyed here". Reading from a warehouse
-- adds a second, indistinguishable one -- ground nobody has fetched yet -- and
-- painting that as "USDA reports no surveyed SSURGO map units in this view" would
-- be the exact lie `unreadableGeometries` exists to prevent. This table is what
-- keeps the two apart: a cell with a row here was fetched and its answer is
-- authoritative (`polygon_count = 0` genuinely means unsurveyed); a cell with no
-- row here has never been asked, and the reader reports it as missing coverage.
--
-- One row per fixed-grid cell, never per request: `cell_key` is
-- '<col>:<row>' at SOIL_SURVEY_CELL_DEGREES (1/8 degree, exactly representable in
-- binary floating point so the indices never drift), minted only by
-- `usda-soil.ts#soilSurveyCellKey`. Bounds are stored denormalised alongside so a
-- ledger row is auditable without re-deriving the grid.
--
-- Deliberately not a PostGIS table: the reader enumerates the cell keys a bbox
-- overlaps arithmetically and looks them up by equality, which is exact and needs
-- no spatial index. A geometry column here would invite an `&&` lookup that is
-- merely approximate about the thing this table exists to be exact about.
CREATE TABLE IF NOT EXISTS "geo"."soil_survey_coverage" (
	"cell_key" varchar(40) PRIMARY KEY NOT NULL,
	"west" double precision NOT NULL,
	"south" double precision NOT NULL,
	"east" double precision NOT NULL,
	"north" double precision NOT NULL,
	-- Map-unit delineations SDA served for this cell and this reader persisted.
	"polygon_count" integer NOT NULL,
	-- Rows SDA served that were dropped: geometry this reader could not parse, or a
	-- survey area with no publisher vintage to date the version by. Counted rather
	-- than absorbed, for the same reason the request path carries the count.
	"unreadable_count" integer NOT NULL DEFAULT 0,
	-- SDA held more delineations for this cell than the row ceiling served, so the
	-- cell is covered only in part and must not be read as complete.
	"truncated" boolean NOT NULL DEFAULT false,
	-- When we asked, never when SSURGO published. The publisher vintage lives per
	-- feature in properties.surveyAreaVintage, taken from sacatalog.saverest.
	"fetched_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "ck_soil_survey_coverage_bounds" CHECK ("west" < "east" AND "south" < "north"),
	CONSTRAINT "ck_soil_survey_coverage_counts" CHECK (
		"polygon_count" >= 0 AND "unreadable_count" >= 0)
);
--> statement-breakpoint
-- Backfill resumption scans oldest-first over the whole ledger.
CREATE INDEX IF NOT EXISTS "ix_soil_survey_coverage_fetched_at" ON "geo"."soil_survey_coverage" ("fetched_at");
