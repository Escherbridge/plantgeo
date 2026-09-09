-- The publication catalog for first-party raster releases, starting with SoilGrids.
--
-- `src/lib/vegetation.ts` has gated `getEnvironmentalTileTemplate` on an empty string since
-- the file was written, with the comment "Activation requires a database-backed, immutable
-- publication catalog." This is that catalog. Until it holds a row, no soil raster is served
-- and `SoilLayer` adds no source -- which is exactly the behaviour today, so this migration
-- changes nothing that is drawn until `register-soil-release.mjs` inserts a release.
--
-- Created in `geo`, the SERVING plane this application owns. It reads from nothing in `agri`
-- and takes no lock on a table the ingestion crawl writes, so it is safe to apply while the
-- hourly cron is running.
--
-- MARTIN IS NOT AFFECTED. `infra/martin/martin.yaml` sets `auto_publish: false` and names its
-- function sources explicitly, so a new `geo` table cannot join a composite source id and no
-- tile server needs restarting. Nothing here creates a tile function -- these archives are
-- served as PMTiles from R2 by range request, not by Martin.
--
-- IMMUTABILITY is by convention plus one index, not by trigger: a row is never updated in
-- place, and replacing a release means stamping `superseded_at` on the old row and inserting
-- a new one. The partial unique index below is what enforces "at most one live release per
-- property", so a half-finished re-publish cannot leave two archives claiming the same layer.

CREATE TABLE IF NOT EXISTS "geo"."raster_release" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	-- Identity of what is published, in the upstream's own vocabulary.
	"collection" text NOT NULL,
	"property" text NOT NULL,
	"depth" text NOT NULL,
	"statistic" text NOT NULL,
	-- Provenance the licence requires us to carry to the browser.
	"source_name" text NOT NULL,
	"source_release" text NOT NULL,
	"source_url" text NOT NULL,
	"license_name" text NOT NULL,
	"attribution" text NOT NULL,
	-- What a stored pixel means. `scale_divisor` is the upstream integer scaling: the raster
	-- holds pH*10, not pH, and a reader that ignores this renders a plausible-looking lie.
	"unit" text NOT NULL,
	"scale_divisor" integer NOT NULL,
	"nodata_value" double precision,
	-- The measured extremes of the published extent, so a legend ramp is fitted to the data
	-- that exists rather than to a textbook range the PNW does not span.
	"value_min" double precision,
	"value_max" double precision,
	-- The exact ramp the tiles were painted with, as [{value, color}] in `unit`. Carried here
	-- rather than restated in TypeScript because a legend that drifts from the pixels is worse
	-- than no legend: the archive is immutable, so its ramp is a property of the release.
	"color_ramp" jsonb NOT NULL,
	-- Where the archive actually is, and proof of which bytes were published.
	"object_key" text NOT NULL,
	"archive_format" text NOT NULL,
	"checksum_sha256" text NOT NULL,
	"size_bytes" bigint NOT NULL,
	"min_zoom" integer NOT NULL,
	"max_zoom" integer NOT NULL,
	"bounds" geometry(Polygon, 4326) NOT NULL,
	"published_at" timestamp with time zone DEFAULT now() NOT NULL,
	"superseded_at" timestamp with time zone,
	CONSTRAINT "raster_release_zoom_range" CHECK ("min_zoom" >= 0 AND "max_zoom" >= "min_zoom"),
	CONSTRAINT "raster_release_scale_positive" CHECK ("scale_divisor" > 0),
	CONSTRAINT "raster_release_checksum_shape" CHECK ("checksum_sha256" ~ '^[0-9a-f]{64}$')
);
--> statement-breakpoint

COMMENT ON TABLE "geo"."raster_release" IS
	'Append-only catalog of first-party raster tile archives on R2. A row is the assertion '
	'that a specific set of bytes, with a known checksum and licence, is being served as a '
	'map layer. Superseding a release stamps superseded_at and inserts a new row.';
--> statement-breakpoint

-- At most one live release per published property PER FORMAT. Format is part of the key because
-- one property is published twice over: the COG is the archival artifact anyone can download and
-- compute against, the tile archive is what the map draws. They supersede independently, and
-- collapsing them would make re-cutting tiles look like withdrawing the data. Partial on
-- superseded_at so the whole publication history stays queryable underneath it.
CREATE UNIQUE INDEX IF NOT EXISTS "ux_raster_release_live"
	ON "geo"."raster_release" ("collection", "property", "depth", "statistic", "archive_format")
	WHERE "superseded_at" IS NULL;
--> statement-breakpoint

-- The read path is always "what is live for this collection", so it gets the covering index.
CREATE INDEX IF NOT EXISTS "ix_raster_release_live_collection"
	ON "geo"."raster_release" ("collection", "property")
	WHERE "superseded_at" IS NULL;
--> statement-breakpoint

-- The serving view: live releases only, with the bbox flattened into the four numbers a
-- MapLibre source needs. Readers go through this so no call site can forget the
-- `superseded_at IS NULL` predicate and serve a withdrawn archive.
CREATE OR REPLACE VIEW "geo"."published_raster" AS
SELECT
	release.id,
	release.collection,
	release.property,
	release.depth,
	release.statistic,
	release.source_name,
	release.source_release,
	release.source_url,
	release.license_name,
	release.attribution,
	release.unit,
	release.scale_divisor,
	release.nodata_value,
	release.value_min,
	release.value_max,
	release.color_ramp,
	release.object_key,
	release.archive_format,
	release.checksum_sha256,
	release.size_bytes,
	release.min_zoom,
	release.max_zoom,
	ST_XMin(release.bounds) AS bbox_west,
	ST_YMin(release.bounds) AS bbox_south,
	ST_XMax(release.bounds) AS bbox_east,
	ST_YMax(release.bounds) AS bbox_north,
	release.published_at
FROM "geo"."raster_release" AS release
WHERE release.superseded_at IS NULL;
--> statement-breakpoint

COMMENT ON VIEW "geo"."published_raster" IS
	'Live raster releases with the bbox flattened to four ordinates. Read by '
	'getPublishedRasters; the only supported way to ask what raster layers exist.';
