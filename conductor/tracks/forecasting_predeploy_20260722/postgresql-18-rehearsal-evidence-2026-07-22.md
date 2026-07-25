---
type: evidence
---

# PostgreSQL 18 restore/parity rehearsal evidence — 2026-07-22

## Status

Blocked before target creation. No PostgreSQL 18 server or client is installed on
this workstation, the local Podman connection is unavailable to this task, and the
Railway CLI is not installed. No Railway resource, variable, extension, database,
deployment, or schedule was inspected or changed. PostgreSQL 18 extension and restore
parity therefore remains a production blocker rather than an inferred success.

## Source archive re-verification

The only authorized archive for the future rehearsal is:

- Path: `C:\tmp\plantgeo-warehouse-backups\plantgeo-pre-0006-20260722-complete.dump`
- Bytes: `1,528,682,576`
- SHA-256: `86515C6F633B9E86E3241822FB233C43FB68FB277691B18B60F1BD7D669CAB6A`
- Format/compression: PostgreSQL custom format, zstd
- Archive TOC entries: `487` (`pg_restore --list` emitted 497 physical lines including headers)
- Dump source: PostgreSQL `16.14` on Ubuntu
- Dump tool: `pg_dump 16.9`
- Extension TOC entries: `postgis`, `timescaledb`, `vector`, and `pgcrypto`, each with its extension comment

The similarly named archive without `-complete` remains forbidden. The verified
archive has no adjacent checksum manifest, so `infra/local-warehouse/restore.ps1`
cannot consume it as written. That script also targets the literal `plantgeo`
database with `--clean`; it must not be repurposed for a disposable PostgreSQL 18
drill.

## Required bounded rehearsal

The operator must supply an exact PostgreSQL 18 target/image digest and a separately
named database matching `plantgeo_forecast_test_pg18_<change-id>`. This prefix is
also accepted by the PostgreSQL integration test's disposable-target guard. Before any create or
drop, record the resolved host, port, maintenance database, server major, database
name, owner, and active sessions. Stop unless the server major is exactly 18 and the
database name matches the disposable prefix.

1. Record available versions of PostGIS, TimescaleDB, pgvector, and pgcrypto. Stop if
   any package is missing or unapproved.
2. Create only the verified disposable database and install the four extensions as
   its owner. Record installed versions before restore.
3. Restore the verified custom archive with PostgreSQL 18 client tools, explicit
   database targeting, `--exit-on-error`, no owner/grant replay, and a reviewed TOC
   that excludes duplicate extension creation. Capture stdout, stderr, elapsed time,
   and the exact filtered TOC checksum.
4. Compare only evidence that exists in this pre-0006 archive: source/restored
   Alembic revision, schema inventory, validated release-set manifests, source
   variants, artifact digests, and historical row counts. This archive predates the
   14 v1 hindcast receipts and cannot prove their preservation.
5. Migrate the restored disposable database through `20260722_0008` and run the
   PostgreSQL forecasting integration test against that same guarded disposable
   database. Do not run `create-forecast-roles.sql` on a shared cluster: it currently
   hard-codes database `plantgeo` and creates cluster-global roles. Role parity needs
   either an isolated single-use PostgreSQL 18 cluster or a separately reviewed,
   literal-target-validated script before this gate can close.
6. Record the PostgreSQL 18 extension versions after migration and verify the same
   immutable source evidence. Any mismatch blocks cutover.
7. Drop only the database whose literal name, major version, owner, Alembic revision,
   and sessions were re-verified immediately before disposal. Preserve all logs and
   the source archive.

V1 receipt preservation is a separate required gate. Supply a verified post-receipt
backup/clone containing the 14 canonical receipts; record their checksums, upgrade to
`20260722_0008`, and prove the backfilled `hindcast_v1` versions, checksums,
value-expanded manifest, and idempotent recomputation are unchanged. CI also exercises
a deliberately structural v1 row through the 0007-to-0008 backfill, with user triggers
suppressed only for that disposable seed; that proves schema/checksum compatibility,
not governed finalizer behavior or preservation of the 14 live receipts. Never claim
that the pre-0006 archive contains those later receipts.

## Exit criteria

This blocker can be closed only by attaching the target/image identity, extension
catalog output, restore log and duration, filtered TOC checksum, before/after evidence
comparison, migration/test output, and safe-drop verification. A checklist, PostgreSQL
16 success, or Railway product availability is not PostgreSQL 18 parity evidence.
The exit evidence must also include the separate v1 preservation proof and a safe
role-rehearsal method that cannot grant against another database on the cluster.
