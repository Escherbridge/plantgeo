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

## Soil-wetness canonical breakdown

`soil_wetness_snapshot_breakdown.py` consumes one manifest-pinned raw canonical signal snapshot and
never opens PostgreSQL. Its three exact product filters are GWETTOP, GWETROOT and GWETPROF; their
outputs live under distinct `derived-canonical/` lane prefixes, outside the mutable `layer=signal`
namespace. Each bounded UTC-month step verifies the referenced raw part receipts, classifies every
physical row, writes a full provenance sidecar and writes deduplicated z13 day parts. Release
precedence is the governed signal rule: newest frozen source-release retrieval, then greatest
physical observation id.

All writes are conditional and byte-checked. Month checkpoints land after their parts, z9/z5/z0
derive only from the completed z13 bytes, every day-tier marker lands after its part, and the z13
marker lands after the coarse ladder. A lane manifest is published only after exact input,
provenance, winner and tier reconciliation; `_COMPLETE` is last and pins the manifest SHA-256.
Receipt verification uses bounded concurrent reads within one month and publishes a deterministic
month marker only after every object passes its byte, SHA, schema and row checks. Those markers bind
the canonical checkpoint bytes and are themselves digest-bound by the lane manifest, so an
interrupted finalizer resumes without weakening or repeating completed verification.

`soil_wetness_snapshot_audit.py` is the separate read-only publication auditor. Its store wrapper has
no write method. It ignores verification-marker shortcuts, re-reads every receipt, recomputes each
month from the pinned raw parts, reproduces provenance and z13/z9/z5/z0 bytes, and requires exact lane
and bundle inventories plus the manifest/completion SHA chain.
