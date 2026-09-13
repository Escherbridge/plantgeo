-- Retire the private strategy-request path. Track `public_strategy_requests_20260913`, Phase 3,
-- implementing OQ-B (retire the private/team-scoped path entirely) and OQ-C (votes and likes stay
-- separate concepts, but the vote's foreign key moves to `geo.features`).
--
-- WHAT GOES, AND WHY
--
-- `strategy_requests` was a non-geospatial, owner/team-private table with bare `lat`/`lon`
-- `double precision` columns -- no PostGIS geometry, no `geo.features` row, nothing the Martin
-- tile pipeline or the client draft overlay could ever draw. `interventions.submitRequest`
-- (Phase 2) replaced it: a request is now a published `geo.features` row carrying
-- `properties.kind = 'request'`, which means it is on the map the moment it is written and
-- readable by every visitor, signed in or not.
--
-- `priority_zones` was a DBSCAN-cluster rollup whose only data source was `strategy_requests`.
-- Its only reader, `community.getPriorityZones`, goes in the same change; the one other reference
-- (`checkPriorityZoneAlerts` in src/lib/server/services/alert-engine.ts) sits behind a constant
-- pinned to "inactive" and has never run. Per the spec's Out of Scope, the cluster-summary feature
-- is retired with the table rather than rebuilt against `geo.features`.
--
-- BEFORE THIS MIGRATION RUNS: `scripts/backfill-strategy-requests.mjs --apply` must have copied
-- the production `strategy_requests` rows into `geo.features` (3 rows as verified at Phase 1, all
-- from one account). The drop below is irreversible and the backfill cannot run after it. The
-- script is idempotent and re-running it is safe; running the migration without it silently
-- discards a contributor's past asks, which is the outcome Phase 1 explicitly chose against.
--
-- WHAT STAYS: `request_votes`
--
-- Phase 1 (OQ-C) kept votes and likes as DISTINCT product concepts -- a vote is the
-- no-toggle-off, denormalized-count support signal, `geo.feature_likes` is the per-user toggle --
-- so the table survives its parent. Its foreign key moves from `strategy_requests.id` to
-- `geo.features.id` and the column is renamed `request_id` -> `feature_id` to say what it now
-- points at. The table is empty in production (0 rows verified at Phase 1), so the FK move needs
-- no data repair and can never orphan a row.
--
-- This leaves `request_votes` as DORMANT INFRASTRUCTURE in this pass: `community.voteOnRequest`,
-- its only writer, is deleted with the rest of the retired router, and no replacement procedure is
-- wired in Phase 3. That is the stated Phase 1 outcome -- the table survives, the feature using it
-- does not yet -- not an oversight. A future track wiring a public "support this request" action
-- inserts `(feature_id, user_id)` here and derives the count with `count(*)`; the denormalized
-- `strategy_requests.vote_count` it used to increment no longer exists to drift.

ALTER TABLE "request_votes" DROP CONSTRAINT IF EXISTS "request_votes_request_id_strategy_requests_id_fk";--> statement-breakpoint
ALTER TABLE "request_votes" DROP CONSTRAINT IF EXISTS "request_votes_request_id_user_id_pk";--> statement-breakpoint
ALTER TABLE "request_votes" RENAME COLUMN "request_id" TO "feature_id";--> statement-breakpoint
DELETE FROM "request_votes" WHERE "feature_id" NOT IN (SELECT "id" FROM "geo"."features");--> statement-breakpoint
ALTER TABLE "request_votes" ADD CONSTRAINT "request_votes_feature_id_user_id_pk" PRIMARY KEY("feature_id","user_id");--> statement-breakpoint
ALTER TABLE "request_votes" ADD CONSTRAINT "request_votes_feature_id_features_id_fk" FOREIGN KEY ("feature_id") REFERENCES "geo"."features"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
DROP TABLE IF EXISTS "priority_zones";--> statement-breakpoint
DROP TABLE IF EXISTS "strategy_requests";
