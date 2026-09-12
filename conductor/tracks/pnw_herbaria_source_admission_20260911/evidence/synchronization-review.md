---
type: independent-review
status: accepted
reviewed_on: 2026-09-11
review_scope: botanical-planning-baseline-synchronization
---

# Independent synchronization review

This review evaluates the source-track resolution between source packet
`f19678c9609c49ad517b5e03554d1cd17bf4ed4e` and planning baseline
`b9b7bf4fcc0c58556d10fc58522edced8962b869`. The reviewer operates in a separate
agent context from the author and owns only this review artifact.

The governance resolution is accepted as consistent with the owner instruction:
the source track is active while both collection admissions remain blocked. The
two editorial findings below were corrected and independently reread. No source
admission or archive-transfer permission is granted by this review.

## Findings

| Finding | Disposition |
| --- | --- |
| The A0, A1 and A2 plan headings acquired corrupted em-dash encoding during resolution. | Author restored the original em dashes; reviewer confirmed all three headings. |
| The synchronization record's initial wording could imply that blocked evidence statuses, rather than only historical track-status instructions, were superseded. | Author explicitly superseded only the historical track-status instruction and retained blocked evidence/admission statuses and collection verdicts; reviewer confirmed the revised wording. |

## Governance and preservation assessment

The resolved metadata, plan and specification retain active track status and
active partition confidence, with `admission_status: blocked` and an empty
`admitted_releases` list. Detailed WTU release/EML and UBC public-coordinate gates
remain open; both archives still lack measured schema, safety and native-identity
receipts. The checked metadata milestones do not complete acquisition or admission.

The [original independent review](independent-review.md) and
[collection decisions](admission-decisions.json) retain their blocked verdicts.
The [synchronization record](baseline-synchronization.md) explicitly separates
historical evidence from the current planning state. Original evidence and
verification receipts are intended to remain unchanged; their exact byte
preservation is delegated to the author's single final integrity sweep. The old
verification receipt must not be represented as validation of this successor.

Read-only comparison found no resolution changes against `b9b7bf4` in the
occurrence-plane, occurrence-experience, species-profile or recommendation tracks,
or the track registry. They are inherited planning work, not new runtime work.

## Relationships and acquisition boundary

The source-owned relationships correctly identify
[occurrence ingestion](../../botanical_occurrence_parquet_lane_20260911/spec.md),
[species profiles](../../botanical_species_profile_lookup_20260911/spec.md),
[occurrence experience](../../botanical_occurrence_experience_20260911/spec.md)
and [recommendation validation](../../botanical_species_recommendation_validation_20260911/spec.md).
Spatial specimen facts, support, aggregates and environmental joins stay in
Parquet. A missing trait profile neither drops specimens nor blocks otherwise
admissible occurrence publication; traits do not become occurrence attributes.

The nonspatial profile remains parent-owned, sharing the canonical taxon
authority/version/concept key. Reviewed database curation transitions once to an
approved immutable profile Parquet release; serving pins that release without
live database fallback. Per-value provenance, licence, dates, review and lineage
remain required. Deferred second-source enrichment does not block a licensed
first pilot. Recommendation composition preserves separate occurrence,
establishment and objective-effect evidence.

Future data-only quarantine capture remains conditional on rights, withholding,
custody and archive-control preflight, followed by actual archive measurements
and independent identity review. The permission gate must be reported before
transfer. A two-release UBC comparison consumes both archive slots and defers
WTU under the unchanged envelope. No acquisition, provider contact, external
messaging, ingestion, image access, runtime changes, publication, scheduling or
production mutation belongs to this synchronization.

## Verification limit

The reviewer read the resolved documents and compared the two input commits;
no automated integrity sweep, network request or application test was run in
this lane. The author must complete the one bounded documentation, JSON, link,
whitespace and immutable-evidence sweep after corrections and record its result
in [the successor verification receipt](synchronization-verification.json).
