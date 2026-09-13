---
type: evidence
track: pnw_land_context_reference_plane_20260911
review_scope: source-rights-gates
observed_at: 2026-09-12
verdict: deferred — external rights unresolved
---

# Source rights gate verdicts

This receipt documents the status of four externally-dependent rights gates that remain unresolved as of 2026-09-12. These gates block data acquisition and ingestion from their respective sources, but do **not** block schema design, reader contracts, UI implementation, or agent integration — those proceeded in parallel under owner authorization.

## Gate verdicts

### Idaho utility polygon source
**Verdict:** Deferred — rights unresolved, no acquisition authorized against this source.

Utility polygon coverage in Idaho remains ungated. No vendor, license, watermark, native-key mapping, or reuse policy has been established. Ingestion and publication against this source remain blocked pending a dedicated rights review and acquisition decision.

### Idaho production state-land source
**Verdict:** Deferred — rights unresolved, no acquisition authorized against this source.

Idaho state-managed land authoritative feed, watermarks, and licensing remain unresolved. The source's current operational feed and reuse permissions are unknown. Ingestion and publication remain blocked pending source vetting, rights confirmation, and acquisition decision.

### Oregon current utility machine feed
**Verdict:** Deferred — rights unresolved, no acquisition authorized against this source.

Oregon's live utility polygon service (feed mechanism, update cadence, and reuse terms) remains ungated. No native-key alignment, refresh watermark, or licensing decision has been established. Ingestion and publication remain blocked pending feed characterization and rights acquisition.

### County parcel field and rights coverage
**Verdict:** Deferred — rights unresolved, no acquisition authorized against this source.

County-by-county parcel coverage, per-county field definitions, per-county licensing and reuse rights, and native-key mapping remain heterogeneous and unresolved. The scope of coverage within WA/OR/ID, field harmonization requirements, and acquisition terms are all open. Ingestion and publication remain blocked pending county-by-county rights review and selective acquisition decisions.

## Implementation note

Schema design, reader contracts, UI components, and agent tool parity for these surfaces proceeded in parallel to this gate review under 2026-09-12 owner authorization. Implementation of these contracts is authorized and proceeding independently because it does not require acquiring, importing, or publishing data from any of these four gated sources.

Real ingestion against any of these sources remains blocked until the corresponding rights gate receives explicit clearance via a future gate review. This deferral is deliberate and documented; silence on a source should never be mistaken for approval.

