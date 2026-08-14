---
type: decision-record
---

# GHISACONUS lineage script: schema fits, execution mechanism does not — RESOLVED 2026-08-14

**Status update 2026-08-14, later the same day:** this blocker is resolved.
The owner sanctioned a client-side loader
(`services/agri-data-service/src/agri_data_service/execution/public_evaluation_lineage.py`)
that performs the same logical inserts this document's blocked SQL script
attempted, without ever asking Postgres to read a local file. It ran
successfully against prod; rows are committed. See
`lineage-receipt-2026-08-14.md` for the storage-mode decision, the digests
re-verified from disk, the transaction/verification detail, and the row
counts and keys.

**Everything below this line is kept as history: the original blocked
analysis, unedited, showing why the raw SQL script alone could not run.**
It is retained rather than deleted because the reasoning (schema vetting
clean, `pg_read_binary_file` reads the wrong filesystem) is exactly what
motivated the client-side loader's design, and because deleting it would
erase the record that this was blocked before it was resolved.

---

`C:\tmp\plantgeo-retain-ghisaconus.sql` was vetted against the current prod
schema and **not run**. This is a blocked note for the owner, per the track
brief's own escape hatch: fix only column renames/obvious mappings; anything
larger stays blocked and gets recorded, not improvised.

## Schema vetting: clean, nothing to fix

Read-only probe against prod (`DATABASE_URL_SYNC`, `switchback.proxy.rlwy.net`,
database `plantgeo`), 2026-08-14:

- `alembic_version` = `20260808_0019`, matching the head the brief names.
- `information_schema.columns` for the five tables the script writes
  (`agri.data_source`, `agri.source_release`, `agri.release_set`,
  `agri.release_set_item`, `agri.artifact`) returns **64** columns, exactly the
  sum of columns in the canonical `db/agri/tables/*.sql` declarative tree
  (20 + 19 + 9 + 4 + 12) — the local declarative tree is not stale relative to
  prod.
- Every column name the script's `INSERT ... (col, col, ...)` lists names an
  existing column; no renamed or dropped column was found.
- Every `CHECK` constraint the literal values must satisfy does: `review_state
  = 'approved'` carries a non-null `reviewed_at`; `validation_state = 'valid'`
  carries a non-null `validated_at`; `manifest_checksum` matches
  `^[0-9a-f]{64}$`; `observed_to >= observed_from`; the `release_set` row is
  inserted `draft` (validated_at NULL, satisfying the draft branch) and only
  promoted to `validated` by the later `UPDATE`, **after** `release_set_item`
  is inserted — which is the correct order under the "membership freezes after
  validation" trigger documented in `execution/AGENTS.md`.
- No pre-existing collision: `agri.data_source` has no row with
  `key = 'kaggle-ghisaconus-mirror'`, and `agri.release_set` has no row with
  `logical_key = 'ghisaconus-v1-public-benchmark-20260726'`. This is a
  genuinely new lineage write, not a re-run of an already-retained one — the
  `warehouse_retention` block inside
  `conductor/retros/restoration_ag_demo_20260726/receipts/kaggle-ghisaconus-v1.json`
  (source_release_id `e586c7b8-...`, release_set_id `b7cca256-...`) describes
  an *intended* retention target, not a row that already exists in prod.

**Conclusion: the schema did not move.** There is no column rename, type
change, or dropped constraint to patch. `plantgeo-retain-ghisaconus-vetted-2026-08-14.sql`
in this directory is therefore byte-identical to the original (SHA-256
`077a4b8acba29b9e824bc14ebac17e354937e0048c9e45827d144696b330e97d`, matching
`C:\tmp\plantgeo-retain-ghisaconus.sql`).

## What actually blocks it: `pg_read_binary_file` reads the wrong filesystem

The script's three `agri.artifact` rows source their `content_bytes` from
`pg_read_binary_file('/tmp/GHISACONUS_2008_001_speclib.csv')` and two sibling
calls. That function reads a file **on the PostgreSQL server's own
filesystem**, not the client's. Prod is a managed Railway Postgres reached
over `switchback.proxy.rlwy.net:37967` — its filesystem is a remote container,
not this Windows machine, and the referenced `/tmp/...` paths were never
staged there.

Confirmed empirically with a read-only probe (no transaction opened, nothing
written):

```
SELECT pg_read_binary_file('/tmp/GHISACONUS_2008_001_speclib.csv');
-- UndefinedFileError: could not open file "/tmp/GHISACONUS_2008_001_speclib.csv"
--   for reading: No such file or directory
SELECT current_user, usesuper FROM pg_user WHERE usename = current_user;
-- postgres, true
```

`current_user` is superuser, so this is not a privilege refusal — it is a
file that genuinely does not exist on that host. All three referenced paths
(`/tmp/GHISACONUS_2008_001_speclib.csv`, `/tmp/plantgeo-ghisaconus-v1.zip`,
`/tmp/plantgeo-ghisaconus-metadata.json`) would fail the same way; the first
one to execute aborts the whole `DO $$ ... $$` block, and the surrounding
`BEGIN`/`COMMIT` means nothing would be written, but the run itself would
error out rather than complete.

## Why this is not a "column rename or obvious mapping" fix

Making this script runnable requires choosing **how binary payloads reach a
remote database**, which is a design decision, not a schema patch:

1. Read the three local files from the client (Python + a driver such as
   `asyncpg`/`psycopg2`) and bind their bytes as parameters instead of calling
   a server-side file-read function — a different execution tool entirely
   (a script, not a portable `.sql` file), and a decision about batching,
   timeout, and memory for ~16.7 MB (11.5 MB CSV + 5.2 MB zip + 8.9 KB
   metadata) over a proxied connection in one transaction.
2. Rewrite the `.sql` file itself to carry the content as inline hex `bytea`
   literals (`E'\\x...'`) — technically possible, but turns a 4.9 KB script
   into a ~23 MB text file and is not how any other lane in this codebase
   inlines large payloads.
3. Reconsider `storage_class = 'database_inline'` for the 11.5 MB CSV and
   5.2 MB zip artifacts at all. `execution/AGENTS.md` records that the ERA5
   lane deliberately keeps large ZIPs out of `content_bytes` and stores only a
   checksum-bound local-cache pointer, precisely to avoid inlining artifacts
   at this scale. Whether GHISACONUS's one-time evaluation lineage should
   follow that precedent, or is a legitimate exception (a one-time public
   benchmark retention rather than a recurring ingest lane), is a call for
   whoever owns that precedent, not an implementation detail.

Any of these is legitimate, but each is a judgment call outside "rename a
column, remap a type." Per the track brief, the correct action is to record
this and leave it blocked rather than pick one unreviewed.

## What is NOT blocked

This blocker is scoped to the **lineage-retention** write only. It does not
affect, and is not affected by, the crop-lane or forecast-lane ABSTAIN
decisions in `decision-record-2026-08-14.md` — those rest on class support and
availability-clock evidence in `blockers.md`, not on whether this lineage
insert runs. The GHISACONUS CSV bytes (already rehashed against spec.md's
pinned digest, see `rehash-receipt-2026-08-14.md`) remain available locally
regardless of whether this warehouse lineage record exists.

## Next step for the owner — DONE 2026-08-14

~~Decide the artifact-transport approach (client-side parameter binding is
the lowest-risk option...) and re-author this as a reviewed Python execution
module, not a standalone `.sql` file, if the lineage record is still
wanted.~~

Resolved: the owner sanctioned option 3's *narrower* form (reference +
checksum, not client-side byte binding at all — `agri.artifact`'s CHECK
constraint doesn't require bytes outside `storage_class = 'database_inline'`,
so this needed no payload transport, contra the original assumption in
option 1 above). Implemented in
`public_evaluation_lineage.py`, run against prod successfully. See
`lineage-receipt-2026-08-14.md`.
