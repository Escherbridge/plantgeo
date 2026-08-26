# Vegetation production proof scripts

`vegetation_ingest_status.py` reads the raw `geo.features` vegetation population in a read-only
transaction. Its changed-cell-day counts collapse raw scene rows to the same `(cellKey,
publisher-named day)` grain the promotion transform uses, then test presence against the governed
NDVI source filters. Empty populations keep nullable bounds rather than turning an operational
absence into an exception.

`vegetation_source_inventory.py` brackets the object-store scan with independent read-only governed
source censuses. A changing source makes the report non-clean. Schema classification is deliberately
closed: only the exact current schema and the pinned coordinate-less predecessor have names;
anything else is `unknown`, and unknown/read-error state exits nonzero so its day cannot become a
destructive rewrite manifest by implication.

## Canonical precipitation breakdown

`build_precipitation_from_canonical_snapshot.py` is the sole writer for the historical
`climate-field-precipitation` breakdown. It has no database surface: its only input is canonical
snapshot `prod-20260826-full-signal-v1`, and it refuses unless the source manifest bytes hash to
`465abc4e813bf28c78acd7f97a4da9d19ad959e525de3eb1f422ca2f6e73e94f`. The legacy `layer=signal`
prefix is never an input or output.

The script follows the source manifest's month/cell-batch ledgers and holds one month at a time.
Every declared source part is checked by byte count, SHA-256, row count, and canonical row digest.
Rows are then classified into the legacy governed precipitation population or one mutually
exclusive exclusion reason. Within the governed grain, the winner is the newest source-release
retrieval timestamp and then highest physical observation ID. No physical row is discarded:
precedence-ordered lineage arrays retain every ID, canonical row hash, release ID, source-part
checksum, and row ordinal, while the selected fields repeat lineage element zero for direct audit.

Destination writes are conditional create-only operations. Existing bytes are accepted only when
identical; no key is overwritten, retracted, or pruned. A month checkpoint is written only after
all four day tiers and their completion markers read back correctly. The final manifest binds all
checkpoint hashes. A rerun verifies completed months and continues at the first absent checkpoint.

The canonical source manifest records each month ledger's identity, totals, and source-row digest,
but not the ledger object's byte checksum. The source-chain audit therefore proves every constructed
ledger has the exact manifest summary, enforces every selected part's exact snapshot-root path,
verifies its bytes and row digest, and records the ledger byte SHA-256 as consumed. Its immutable
`source-chain-audit.json` binds the output manifest SHA, and `_AUDIT_COMPLETE` is written last. This
audit runs on resume too, so a completed destination never skips revalidation of its source receipts.

Coarse z09/z05/z00 rows derive directly from each z13 day. Row-level winner provenance becomes null
after aggregation because a coarse cell has no single physical source row; each day checkpoint
instead binds every derived object to the exact z13 part key and SHA-256 from which it was derived.
