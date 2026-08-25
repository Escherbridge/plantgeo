# Local PlantGeo Data Load and Future Promotion

## Dedicated source of record

Only the dedicated PlantGeo warehouse is eligible to become a future promotion
source. Do not load or export `pgt`, `postgres`, `ardanova_*`, or another
application's database. The native `plantgeo` database created on the local
PostgreSQL 16 server is correctly isolated by name, but the server currently
exposes only `pgcrypto`; it cannot host the required PlantGeo warehouse because
PostGIS and pgvector are unavailable. (TimescaleDB was removed on 2026-08-25.)

`infra/local-warehouse/compose.yaml` creates the intended local source:

- a small PostgreSQL 16 image (formerly Timescale HA with TimescaleDB until
  2026-08-25, when the extension was removed), matching the repository's
  supported source major; it retains PostGIS, pgvector, and pgcrypto packages
  while removing upstream automatic extension/tuning init scripts; semantic
  promotion preflight requires the target PostgreSQL major to be equal to or
  newer than the source;
- loopback-only `127.0.0.1:5442` listener and the `plantgeo_warehouse_pgdata`
  volume;
- a distinct `plantgeo` database and `plantgeo_owner` bootstrap identity;
- no automatic extensions, migrations, data loads, public port, or Railway
  dependency.

The container must boot and the operator must explicitly verify/enable
PostGIS, pgvector, and pgcrypto before approved migrations and local source
ingestion begin. (Note: TimescaleDB was removed on 2026-08-25.)

## Local load sequence

1. Start the dedicated warehouse and confirm its private health.
2. Read the extension catalog. If all required packages are available, the
   operator explicitly enables the extensions; never put `CREATE EXTENSION` in
   an image startup script or a long-running application service.
3. Run reviewed Drizzle and Alembic migrations using a short-lived migration
   identity. Drizzle owns `public`, `geo`, and `tracking`; Alembic owns `agri`.
4. Point `DATABASE_URL` (or the `LOCAL_SOURCE_LOADER_DATABASE_URL` override) at
   `127.0.0.1:5442/plantgeo`. There is no loader-role bootstrap step any more:
   the 2026-08-08 role teardown (`20260808_0019`) deleted `create-loader-role.sql`
   and every DSN assertion with it, so confirm the target yourself.
5. Run `agri-cli source-ingest` for reviewed bounded captures. It produces
   governed `agri.data_source`, `source_release`, `artifact`, `release_set`,
   and `release_set_item` records. It does not create models, forecasts, or
   waypoints.

## Future Railway handoff

The promotion unit is the governed `agri` lineage bundle, not a whole
workstation database and not the generic `public`, `geo`, or `tracking`
schemas. The future target bootstrap sequence is:

1. Start `plantgeo-spatiotemporal-db` privately, prove image capability with
   `pg_available_extensions`, then prove installation with `pg_extension`.
2. Run reviewed migration and role gates on an empty target.
3. Create a closed semantic lineage bundle: canonical JSON data files plus a
   hash-only manifest for only the selected, validated `agri` source-lineage
   records. Record the content and per-file SHA-256 values, source/target
   service IDs, PostgreSQL and extension versions, migration revision, row
   counts, and immutable release checksums. The manifest never contains a DSN
   or raw source payload; bounded inline artifact bytes live only in the
   separately checksummed artifact data file.
4. Restore first into a disposable private target with the semantic adapter.
   The `release_set` membership trigger means it must insert each release set
   as `draft`, insert exact membership, then transition it to `validated`; do
   not disable triggers, use `session_replication_role`, or use a blind
   `pg_restore`. A conventional table-scoped `pg_dump` may later be retained
   as an audit/recovery sidecar, but it is never the promotion restore input.
5. After independent validation, run the same controlled handoff inside the
   Railway private network. The database remains private; a workstation must
   not receive a public database endpoint just to perform a promotion.

Incremental local results continue through the bounded publication contract.
This initial lineage promotion remains `artifact_only` and is not a production
forecast/model/waypoint release.

The current implementation provides offline semantic archive write/load,
integrity validation, and trigger-safe restore planning. It deliberately has
no database exporter, restore CLI, or Railway execution job yet. Those require
a separately reviewed private-control-plane adapter and a disposable
round-trip drill before any production handoff.

## Railway database image

The current Railway PostgreSQL 18 candidate fails because its mounted volume is
root-owned while the image runs PostgreSQL as UID 1000. The smallest remediation
is `RAILWAY_RUN_UID=0`, allowing the upstream entrypoint to prepare `PGDATA`
and then drop privileges. The upstream image packages PostGIS and pgvector
(TimescaleDB was removed on 2026-08-25). Verify `pgcrypto` after boot; only make
a pinned derived image with the corresponding PostgreSQL contrib package if
catalog evidence shows it is absent.
