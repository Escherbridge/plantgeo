---
type: source-admission-owner-decision
status: decided
decided_on: 2026-09-13
---

# Owner risk decision: unblock UBC and WTU pre-acquisition gates

The operator (project owner) reviewed [coordinate-policy-search-exhausted-20260913.md](coordinate-policy-search-exhausted-20260913.md)
and [custody-and-archive-control-preflight-proposal-20260913.md](custody-and-archive-control-preflight-proposal-20260913.md)
and made the following decisions directly, rather than waiting on an
institutional reply. This is an explicit owner call, recorded here so it is
auditable, not something a research or implementation pass elected on its
own.

## Coordinate-withholding policy

**Decision:** Proceed without an institutional reply. Both WTU and UBC
already publish these specimen records openly on GBIF under CC-BY/CC0.
Absence of a stated withholding policy is treated as "this institution is
not withholding coordinates beyond what GBIF's own per-record mechanism
already flags." The pipeline already captures `informationWithheld` and
`dataGeneralizations` per record ([normalize.py](../../../../services/agri-data-service/src/agri_data_service/pipeline/direct/botanical_occurrences/normalize.py):128-129)
and carries them through as record-level annotations. GBIF convention is
that a source which generalizes a coordinate does so *before* export and
signals it via `dataGeneralizations`; the exported `decimalLatitude`/
`decimalLongitude` are already the institution's approved public values.
No further code change is needed for this decision — it accepts the
existing field-capture as sufficient evidence.

**Not sending** the corrected WTU or UBC outreach drafts is a consequence
of this decision, not an oversight. Either can still be sent later if a
correction or dispute arises.

## Custody and archive-control preflight

**Decision:** Adopt the proposal in [custody-and-archive-control-preflight-proposal-20260913.md](custody-and-archive-control-preflight-proposal-20260913.md)
as written, with the five open points resolved as follows:

1. **Custody owner:** project owner (Jade Zaher / this repository's
   maintainer).
2. **Quarantine location:** `s3://plantgeo-parquet-9ymvp7gv/quarantine/botanical_occurrences/`
   — the existing private object store already used for pipeline staging
   (see [plantgeo-parquet-bucket-wiring memory]); outside Git, outside any
   public-read prefix.
3. **Retention duration/trigger:** keep the raw archive indefinitely
   alongside its normalized Parquet output. This is already-public,
   CC-BY/CC0-licensed data; there is no confidentiality reason to delete it,
   and keeping it supports future re-verification and release comparison.
4. **Withdrawal record:** if WTU or UBC ever asks for removal, log the
   request and the deletion action as a new evidence file under this
   track's `evidence/` directory before deleting.
5. **Permission manifest authority:** the CC-BY 4.0 (WTU) / CC0 1.0 (UBC)
   terms stated in each institution's own release-bound EML are the
   permission. No separate signed agreement is required for CC-BY/CC0
   public data reuse of this kind (source archiving, normalization, public
   maps, downloads, documented-taxon summaries). The transfer manifest
   records the EML citation as its authority (see
   [ubc-permission-manifest.json](ubc-permission-manifest.json)).

## Scope of this decision

This unblocks the two **pre-acquisition** gates only. The **post-capture**
gates in [admission-decisions.json](admission-decisions.json) — archive/
member safety and hashes, field-map reconciliation, and two-release native-
ID comparison — are unchanged and still required before any occurrence
release is admitted for serving. `admitted_releases` stays empty until
those pass.

Per the existing two-archive, one-transfer-at-a-time budget, **UBC (CC0,
simpler terms) is acquired first**; WTU is deferred to a subsequent pass
rather than acquiring both at once.
