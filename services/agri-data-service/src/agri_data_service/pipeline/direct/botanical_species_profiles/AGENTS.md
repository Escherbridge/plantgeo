# Immutable botanical reference releases

This package publishes a bounded nonspatial lookup, not an environmental day,
zoom ladder or automatic forward-refresh lane. It never reads or writes a
database. Source-specific admission and ingestion remain separate from the
generic domain contract and publisher. No provider agreement is accepted here.

`publication.py` builds taxa, assertions, decisions, profiles and manifest
Parquet artifacts. The release ID hashes canonical reviewed inputs, including
all source identities and licences, reviewer/decision identity, authoring-census
state, normalization/reconciliation versions and every retained source record.
Source documents' raw JSON is retained only as bounded opaque taxonomy evidence;
assertions, source metadata, profile values and decisions use native Arrow structs.

The manifest names the four data hashes, counts and exact release-prefixed keys.
Serving verifies schema, metadata, byte and row ceilings, all content receipts,
the reviewed-input release digest and deterministic reconciliation. Nothing can
substitute an editable database row for missing or corrupt publication bytes.
Explicit release IDs are required; the mutable current pointer is an operator
publication control and never supplies a silent serving default.

Publication writes all artifacts immutably, reads them back, and then uses the
shared `AvailabilityStorage` conditional pointer protocol. A crash before the
pointer leaves a safely replayable partial release. Retry adopts exact bytes;
concurrent competing pointer updates fail. Rollback conditionally points to a
previous fully verified release and leaves both histories intact.

`LocalAvailabilityStorage` implements the same contract with bounded reads,
safe root-contained object keys, file locks and atomic fsynced replacement. It
creates no server and performs no production operation. The existing
`BotoAvailabilityStorage` is the object-store implementation; callers supply it
with their ordinary validated configuration when a future publication is approved.

Ceilings are 100 taxa, 10,000 assertions, one decision per declared field/taxon,
32 MiB per encoded or decoded artifact and 64 MiB of encoded or decoded artifacts per
release. API and agent layers impose their own smaller response and time limits.

`source_ingest.py` is the offline WCVP 16 adapter. It requires a separately
reviewed source descriptor, exact archive SHA-256, an explicit bounded taxon-ID
selection and source-admitted fields. It reads the pipe-delimited names member
with quoting disabled, retains raw accepted and directly linked synonym records,
and maps only admitted lifeform and habitat categories. Family peer review is
preserved as source context; it is not a per-value scientific endorsement.
Quantitative requirements, fuel traits and effects remain absent unless separately
admitted. Accepted subspecies links do not become species-level synonyms.

The module CLI exposes `build-wcvp` and `inspect` against a local object-store
directory. It verifies the source descriptor hash before ingestion and records
the authoring census as unavailable unless the operator explicitly supplies
`--authoring-census-state` and `--authoring-census-note`. The default note states
that no reviewed census is attached and the snapshot cannot supersede existing
relational rows. Before selecting `reviewed_snapshot`, the integration owner
must check the census and preservation decisions, then attach the exact receipt
path, SHA-256 and summary in the note. Both fields become reviewed release inputs;
these flags do not perform or verify a database census. Source assertion provenance
identifies the WCVP source-field adapter independently of census status.
The CLI never discovers taxa through a live
provider, modifies existing relational rows or publishes to remote storage.
Source updates require a fresh descriptor and review decision; no mutable URL
or name-only match can silently change a release.
