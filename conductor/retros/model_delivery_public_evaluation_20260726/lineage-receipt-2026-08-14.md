---
type: evidence-receipt
---

# GHISACONUS lineage receipt — 2026-08-14 (resolved)

The GHISACONUS source/release/artifact/release-set lineage write, previously
blocked (see `lineage-blocked-2026-08-14.md`), has been executed successfully
against prod via a client-side loader:
`services/agri-data-service/src/agri_data_service/execution/public_evaluation_lineage.py`,
invoked as:

```
uv run python -m agri_data_service.execution.public_evaluation_lineage
```

## Storage mode chosen, and why

**Chosen: `storage_class = 'local_raw_cache'` with `content_bytes = NULL` for
all three artifacts** (the CSV, the distribution archive, and the source
metadata JSON) — the same reference-plus-checksum mode
`historical_writer/era5.py`'s `_ensure_era5_artifact` uses for its ZIPs.

This is not the only legal option, and the choice was deliberate:

- `agri.artifact`'s `ck_artifact_inline_artifact_has_content` CHECK constraint
  only requires `content_bytes IS NOT NULL` when `storage_class =
  'database_inline'`. For any other `storage_class`, `content_bytes` may be
  `NULL` — confirmed against the live schema, not assumed. Since the schema
  does not *require* bytes, the client-side-parameter-binding option (reading
  all ~16.7 MB locally and inserting it as bound `bytea` parameters) was not
  needed at all, and was not used.
- The 8,877-byte metadata JSON is small enough that it *could* have been
  inlined the way the Open-Meteo lane inlines its ~360 KB response chunks.
  It was kept under the same `local_raw_cache` mode as the two large files
  instead, so this one release's three artifacts share one storage
  discipline rather than a mixed-mode set, and so the receipt below reads the
  same way for all three. No information is lost: the metadata bytes remain
  fully recoverable from the local path recorded in the artifact's own
  `metadata_json.local_path`, backed by its pinned SHA-256.
- Unlike the ERA5 lane (which tracks its raw-cache pointer through a separate
  checkpoint/receipt file keyed by checksum), this one-off loader has no such
  side system, so each artifact's `metadata_json` carries `local_path`
  directly — the pointer needed to find the bytes again lives on the row
  itself.

No binary payload crossed the wire in this run. `pg_read_binary_file`'s
original failure mode (a server-side function trying to read a path that
only exists on this Windows machine) is structurally avoided, not patched
around: the loader never asks Postgres to read a file at all.

## Digests re-verified before writing (client-side, from disk)

| Input | Local path | Bytes | SHA-256 | Result |
| --- | --- | --- | --- | --- |
| GHISACONUS CSV | `C:\tmp\plantgeo-kaggle-ghisaconus-v1\GHISACONUS_2008_001_speclib.csv` | 11,540,638 | `e2f5a21b24fac00e930520ba959ab54cc8a3f8c56368f8e0a1868bbf3e3377d5` | match |
| Distribution archive | `C:\tmp\plantgeo-kaggle-ghisaconus-v1.zip` | 5,225,446 | `3bb701ab61eb2069c09ae7cc9cb66fbd0995165f127478af2b6afb9d406abac0` | match |
| Source metadata | `C:\tmp\plantgeo-kaggle-ghisaconus-metadata.json` | 8,877 | `42480207bdeed7b40753637f4b24b07d44232ba4ed6f8455fabdecd4c1b6b220` | match |

The loader refuses (`GhisaconusRehashResults.require_all_match`) before
building any row if any of the three mismatches; none did.

## Transaction and pre-commit verification

Executed inside one explicit transaction
(`local_source_loader_session(database_url) as session, session.begin():`).
Every `ensure_*` write uses the same idempotent governed-provenance contract
`historical_writer/era5.py` uses (`execution/provenance.py`), so a repeat run
would return the existing rows rather than duplicate them. All five
`artifact_idempotent`/`*_idempotent` flags below read `false`, confirming
this was a fresh insert, not a replay.

Before the transaction was allowed to return (and therefore before commit),
`_verify_ghisaconus_lineage` re-read every row back inside the still-open
transaction and `persist_ghisaconus_lineage` asserted
`GhisaconusLineageVerificationCounts.is_complete` — exactly one data source,
one source release, one release set (state `validated`), one release-set
item, and three artifacts — before returning. Only then did the `async with
session.begin():` block exit cleanly and commit.

## Rows inserted, and their keys

```json
{
  "data_source_id": "4c5efda7-37fc-436c-bd5f-ca5b446f27dd",
  "data_source_idempotent": false,
  "source_release_id": "d79a3fc8-5a62-4144-847e-f1f8a06234c5",
  "source_release_idempotent": false,
  "release_set_id": "36e522e7-6564-42df-ae55-2b623cc1dd03",
  "release_set_state": "validated",
  "release_set_idempotent": false,
  "artifact_ids": {
    "source_csv": "a0823c62-cda5-4f92-a8b6-e7a6009b3f7f",
    "source_archive": "7a8810db-86c6-4b76-810b-fa385a522813",
    "source_metadata": "46e4cea2-81c4-4bef-82ee-269ee7677484"
  },
  "artifact_idempotent": {
    "source_csv": false,
    "source_archive": false,
    "source_metadata": false
  },
  "verification": {
    "data_source_count": 1,
    "source_release_count": 1,
    "release_set_count": 1,
    "release_set_item_count": 1,
    "artifact_count": 3,
    "release_set_state": "validated"
  }
}
```

## Post-commit confirmation (independent read-only probe, after the run exited)

A separate, read-only `asyncpg` probe against the same prod database
confirmed every row is actually durable (not just visible inside the
writing transaction):

```
data_source:      key='kaggle-ghisaconus-mirror', review_state='approved', is_active=true
source_release:   payload_checksum='e2f5a21b24fac00e930520ba959ab54cc8a3f8c56368f8e0a1868bbf3e3377d5', payload_bytes=11540638, validation_state='valid'
release_set:      logical_key='ghisaconus-v1-public-benchmark-20260726', state='validated',
                   manifest_checksum='535fa6a2f412b627e735689d756b2ce29f9e288f9e790611719b0868d9920f0b'
release_set_item: 1 row, source_role='benchmark_input'
artifacts:        3 rows, each storage_class='local_raw_cache' and content_bytes IS NULL,
                   checksum_sha256/size_bytes matching the table above exactly
```

## What this does and does not change

This is a **lineage retention write only**. It records that the GHISACONUS
source/release/artifact/release-set is governed, checksummed, and traceable
in the warehouse. It does not run the crop-spectrum classifier, does not
touch the crop lane's ABSTAIN decision (`decision-record-2026-08-14.md`), and
does not create a typed benchmark fact plane — those remain gated on Phase 2
approval, which was not granted (rice: 2 independent images against a
3-image requirement). See `decision-record-2026-08-14.md`'s "Evidence
matrix" row for "Lineage recorded in prod", updated to point here.

## Addendum — 2026-08-14: `upstream_source_url` added to all three artifacts

Independent review (finding M14) noted each artifact's `uri` above is a
`warehouse://public-benchmarks/...` locator that resolves nowhere, and the
only *actually fetchable* pointer any of the three rows carried was the
Kaggle `base_url` recorded on `data_source` (`https://www.kaggle.com/datasets/
billbasener/hyperspectral-library-of-agricultural-crops-usgs`, `data_source`
row `4c5efda7-37fc-436c-bd5f-ca5b446f27dd`) — never copied onto the artifacts
themselves. Fixed two ways:

1. **Forward fix**: `build_ghisaconus_lineage_fields` in
   `services/agri-data-service/src/agri_data_service/execution/public_evaluation_lineage.py`
   now writes `metadata_json.upstream_source_url = data_source["base_url"]`
   on every artifact it builds, so a future re-run of this loader (or any
   loader following the same pattern) carries the real pointer from the
   start.
2. **Prod backfill**: a small transactional `UPDATE` was run directly
   against prod for the three already-persisted artifact rows, merging
   `upstream_source_url` into each row's existing `metadata_json` (every
   other key — `local_path`, `retention_role`, `source_release_payload` —
   left untouched). Verified inside the same transaction before commit, then
   re-verified with an independent read-only probe after commit:

   | artifact_id | kind | upstream_source_url |
   | --- | --- | --- |
   | `a0823c62-cda5-4f92-a8b6-e7a6009b3f7f` | source_csv | `https://www.kaggle.com/datasets/billbasener/hyperspectral-library-of-agricultural-crops-usgs` |
   | `7a8810db-86c6-4b76-810b-fa385a522813` | source_archive | `https://www.kaggle.com/datasets/billbasener/hyperspectral-library-of-agricultural-crops-usgs` |
   | `46e4cea2-81c4-4bef-82ee-269ee7677484` | source_metadata | `https://www.kaggle.com/datasets/billbasener/hyperspectral-library-of-agricultural-crops-usgs` |

   3 rows touched, 3 rows verified. `uri` and every other column are
   unchanged; this is a `metadata_json` merge only, not a re-ingest.
