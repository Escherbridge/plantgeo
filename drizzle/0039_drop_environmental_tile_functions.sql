-- DORMANT. Not registered in drizzle/meta/_journal.json; do not register it as part of this task.
-- Written by wave C (lane C1) of conductor/tracks/environmental_postgres_retirement_20260904 so
-- that the drop is READY, reviewed and reversible. Firing it is wave D, and is an owner-confirmed
-- action gated on the three-part packet spec.md D1 requires for every dropped object.
--
-- WHAT THE READER WAVES ACTUALLY DID, AND WHY THAT IS NOT THIS FILE. Lane C1 removed four
-- READERS and lane FP3 removed the fifth. All five layers below now draw from the private Parquet
-- plane -- `environmental.getSensorStations`, `getEvacuationZones`, `getBurnSeverity`,
-- `getWatershedBoundaries`, `getFirePerimeters`, all in
-- src/lib/server/trpc/routers/environmental.ts -- and each function was unpublished from
-- infra/martin/martin.yaml in the same commit as its reader, so Martin no longer opens a
-- PostgreSQL connection for any of them. Removing the reader is what makes the drop possible; the
-- drop itself is a separate, individually reversible step, because a dropped function cannot be
-- un-dropped by reverting a deploy.
--
-- THE THREE-PART PACKET EACH OBJECT BELOW STILL OWES (spec.md D1), none of which exists yet:
--   1. Parity receipt -- a counted comparison per layer showing the Parquet twin covers at least
--      every day and row the PostgreSQL relation holds. Under-coverage is a blocker, not a note.
--      THREE are already known to be short of proof and MUST be measured before this runs:
--        * `sensors` -- 25 of 26 published base days lacked a complete coarse ladder at the last
--          measurement (2026-08-25). At any zoom below 13 those days answer `day_not_written`, so
--          the map draws nothing where sensor_tiles drew dots.
--        * `evacuation-zones` -- confirmed hit by the 2026-09-02 DuckDB `LOAD spatial` failure that
--          broke z9 derivation, dead-lettered at the 2026-09-04 handoff, and unverified since the
--          fix landed in 152feca.
--        * `fire-perimeters` -- the newest and the emptiest: its 45 pre-existing partition days
--          stopped being readable when the lane was re-keyed on `snapshot_day`, so it has no
--          published snapshot at all until an ordinary tick writes one. Nothing to count yet.
--      `burn-severity` and `watersheds` were measured complete on 2026-08-25 but use the identical
--      DuckDB derivation path and have not been re-measured either.
--   2. Zero readers -- a repository-wide proof, in the c2 removal-packet form. As of 2026-09-04 the
--      only remaining references to these five names are: this file, the migrations that created
--      them (0010, 0009, 0012, 0017, 0023, 0033, 0038), their `drizzle/tests/*.test.sql` companions,
--      the tile-generalization sweep in src/__tests__/components/native-polygon-regression.test.tsx
--      (which reads the newest CREATE for each of them on purpose, because a function live in the
--      database is one a rollback can put back in front of a reader), and prose in docs/ and
--      conductor/. No TypeScript reader, no martin.yaml entry, no Python.
--   3. Archived snapshot -- `pg_dump` of each function definition (and of `geo.watershed_rollup`,
--      see below) written to R2 under the retirement prefix, key and sha256 recorded in
--      `evidence/drop-packets/<object>.md`.
--
-- REHEARSE ON `agri_sweep` FIRST. spec.md D1: every drop is rehearsed on the disposable database
-- before production. RESTART MARTIN AFTER APPLYING. Nothing in the deploy pipeline restarts it, and
-- while these five ids are no longer published in martin.yaml, any environment whose Martin config
-- has not caught up would abort on `on_invalid: abort` with a function it cannot resolve.
--
-- geo.fire_risk_tiles IS NOW INCLUDED, AND THIS HEADER USED TO FORBID EXACTLY THAT. Until
-- 2026-09-04 it said the fifth environmental tile function must not be added here, because it
-- still had a live reader: the `fire-perimeters` Parquet lane was registered `daily_series` on a
-- per-incident `observed_day` while `geo.features` holds WFIGS's current-incident set refreshed IN
-- PLACE, so the 177 published perimeters sat across 45 partition days and no single-day or release
-- read reproduced the UNION the map draws. That premise is gone: the lane was re-registered
-- `static_lookup` on ("snapshot_day", "unique_fire_identifier") -- one published snapshot IS the
-- standing set, the shape `evacuation-zones` already used for an identical current-state feed --
-- and `environmental.getFirePerimeters` reads it, resolving the newest snapshot at or before the
-- requested day and filtering it in frame on `observed_day IS NULL OR observed_day <= as_of`.
-- `fire_risk_tiles` has left DYNAMIC_TILE_SOURCE_IDS (src/lib/map/sources.ts) and martin.yaml, so
-- it is now a zero-reader function on exactly the terms the other four are, and it owes the same
-- three-part packet before this file may be fired.
--
-- ONE EXTRA MEASUREMENT THIS LANE OWES ITS PARITY RECEIPT. The lane's 45 pre-existing partition
-- days became structurally unreadable at re-registration BY DESIGN -- the new `snapshot_day` key
-- makes `conform_to_stream_schema` raise rather than silently mis-serve an old partition -- so
-- until one ordinary tick writes a fresh snapshot the layer answers "not yet observed" and the
-- receipt has nothing to count. A parity receipt taken before that first snapshot would compare
-- 177 PostgreSQL rows against an empty lane and must not be read as under-coverage of the twin.
--
-- RELATIONS THIS DROP UNBLOCKS BUT DOES NOT TOUCH, each owing its own packet:
--   * `geo.watershed_rollup` (drizzle/0023_watershed_zoom_generalization.sql) -- a cartographic
--     aggregate whose ONLY reader is `geo.watershed_tiles()`. Dropping that function makes the
--     rollup a zero-reader relation, and its hourly refresh in
--     `services/agri-data-service/.../jobs/matview_refresh.py` a zero-value job.
--   * `geo.features` / `geo.layers` rows for these five layers, which several other objects still
--     read; they are the A3 inventory's problem, not this file's.
--
-- IDEMPOTENT BY CONSTRUCTION. `DROP FUNCTION IF EXISTS` with an explicit argument list, so this is
-- a no-op wherever the function is already gone and cannot accidentally match a differently-typed
-- overload. No CASCADE anywhere: a dependency that would be swept away is a reader nobody proved
-- absent, and this file must fail loudly on one rather than remove it.

DROP FUNCTION IF EXISTS geo.sensor_tiles(integer, integer, integer);
--> statement-breakpoint

DROP FUNCTION IF EXISTS geo.evacuation_zone_tiles(integer, integer, integer);
--> statement-breakpoint

DROP FUNCTION IF EXISTS geo.burn_severity_tiles(integer, integer, integer);
--> statement-breakpoint

DROP FUNCTION IF EXISTS geo.watershed_tiles(integer, integer, integer);
--> statement-breakpoint

DROP FUNCTION IF EXISTS geo.fire_risk_tiles(integer, integer, integer);
