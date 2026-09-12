---
type: evidence-receipt
track: botanical_species_profile_lookup_20260911
date: 2026-09-11
status: blocked
---

# Railway production botanical census and source-admission readiness

## Scope and safety boundary

This receipt records a strictly read-only readiness gate. No Railway service,
database, source, WCVP bundle, object store, release pointer, migration or
deployment was accessed or changed. Secrets, connection strings and private
hostnames are intentionally not recorded.

## Exact repository evidence

- Checkout: detached `HEAD` in the PlantGeo worktree.
- Exact tree/commit: `abdf99b993117f77a1912c05ccabd3df082a8903` (`abdf99b`).
- Working tree: clean at inspection time.
- The profile track remains `active`, but its implementation paths in
  `conductor/tracks/botanical_species_profile_lookup_20260911/metadata.json`
  are marked `future`; the integration receipt and independent review are also
  marked `future`.
- The occurrence track remains `planned` and explicitly depends on an exact
  admitted collection release.
- The independent PNW Herbaria source-admission packet records both collection
  admissions as `blocked`; no exact collection release is admitted.
- The existing profile contract permits `agri.species` as a reviewed authoring
  surface only. It does not authorize treating a database row as a published
  profile or as a serving fallback.

## Railway/config availability

The local process environment contained no Railway or PostgreSQL target/config
variables, including no `RAILWAY_*`, `DATABASE_URL`, `LOCAL_SOURCE_LOADER_DATABASE_URL`
or `PG*` variables. The Railway CLI was not installed (`railway_cli=absent`),
and the PostgreSQL client was not installed (`psql=absent`). Consequently there
was no authenticated Railway target to inspect and no read-only DSN to test.

These are configuration/tooling-presence facts only; they are not evidence that
the remote production database is empty or that any botanical table is absent.

## Database census

Not run. Because no authorized read-only DSN or query client was available, this
gate produced no schema, table-name, row-count, identity-column, review-state,
provenance-column or UUID findings. No connection attempt was made with a
guessed, documented, local, or default credential.

## WCVP and source-admission verdict

WCVP remains explicitly **unaccepted**. This receipt is not an independent
source-admission verdict and does not authorize download, ingestion, taxonomy
mapping, publication or recommendation use. The existing independent admission
verdict remains blocked pending exact-release, rights and identity evidence.

## Remaining gates

1. Provide an operator-authorized Railway target and a read-only DSN through the
   approved secret/configuration channel, without recording resolved values.
2. Run the read-only census against the authorized target: botanical table
   names, counts, identity/review/provenance columns, and existing UUIDs.
3. Reconcile any `agri.species` evidence against the profile contract; do not
   infer sufficiency from column presence or row presence.
4. Complete independent source admission for each candidate source, with WCVP
   still withheld unless separately admitted.
5. Implement authoring-to-immutable-Parquet reconciliation, publication,
   bounded API/agent reads, integration receipt and dependency-last independent
   review before acceptance.
