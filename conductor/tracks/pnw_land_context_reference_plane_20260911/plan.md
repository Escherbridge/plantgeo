---
type: track-plan
track: pnw_land_context_reference_plane_20260911
status: in_progress
---

# Plan

The current registry records this track as `in_progress`. Implementation was authorized by
the September 12 owner override; the September 20 authorization added bounded BLM and USDA
crop delivery and ingestion. The current owner request authorizes PR review, track updates,
merging and deployment monitoring. BLM publication and branch-code readback are recorded in
the [initial delivery evidence](../pnw_land_data_delivery_20260920/evidence/production-delivery-20260921.md).

The [delivery plan](../pnw_land_data_delivery_20260920/plan.md) owns final PR review, deployment,
live acceptance and first successful scheduled turns. PR #10 passed review and quality gates
and is ready to merge. The checklist
below retains the broader family requirements; initial BLM delivery does not close parcel,
utility, state-land or verified-contact work. Those source admissions continue in the
[deferred source track](../pnw_land_sources_deferred_20260920/plan.md).

## R0 — admission and identity decisions

- [ ] Apply the [September 12 coordination gates](evidence/land-herbaria-coordination-20260912.md): retain separate land admission and condition any later botanical-to-parcel/contact association on exact-release rights, permitted precision and independent review.
- [ ] Resolve Idaho utility polygons, Idaho production state-land feed and Oregon
  current machine utility feed using authoritative distribution/version evidence.
- [ ] Establish county parcel geography/field coverage and distribution-specific
  rights for retained/derived data, map display, API/agent use and any export.
- [ ] Confirm all other source reuse/attribution conditions, native keys,
  watermark semantics, CRS, accuracy, history and expected jurisdictions.
- [ ] Freeze the four product families and required/optional facets; exclude
  private owner names/personal contact fields without creating false gaps.
- [ ] Freeze bounded request/capture parameters and numeric budgets after an
  authorized source-sizing step; no unbounded acquisition by assumption.

## R1 — reference schema and relationship contract

- [ ] Specify immutable Parquet boundary/source/contact releases and compatible
  manifest identity using the existing dependency lattice and static contract.
- [ ] Separate effective/published/captured and contact-verified clocks; declare
  unsupported history and unresolved watermark policy explicitly.
- [ ] Define county/native parcel identity, splits/merges, public agency interests,
  utility entity crosswalks and reviewed office/topic/service-region relations.
- [ ] Define direct responsibility, records help, adviser and documented
  forwarding/introduction route types with evidence and nonpersonal field rules.
- [ ] Resolve independent contact/crop facets without naively intersecting every
  publication day or hiding valid geometry behind optional enrichment gaps.

## R2 — bounded acquisition and maintenance by admitted source

- [ ] Admit exact sources/releases before any capture; retain immutable source
  and rights evidence and bounded validation/normalization receipts.
- [ ] Measure source completeness, native key stability, geometry validity,
  overlap, exclusions and source/derived area semantics on PNW samples.
- [ ] Specify source-watermark refresh, reconciliation that authors repair work,
  contact re-verification, retry/dead-letter and governed absence duties.
- [ ] Register any later approved duties in the existing continuous executor;
  no new cron or parallel writer. Distinguish unchanged from not checked.
- [ ] Prove contact removal/source corrections cannot be mistaken for partial
  acquisition; preserve explicit lineage and manifest rollback references.

## R3 — readers and agent data parity

- [ ] Provide bounded point/bbox/AOI and ID/topic relationship readers against
  admitted Parquet, with explicit limits, cursors and terminal status.
- [ ] Preserve source-resolution candidate intersections, overlapping interests,
  selected-area extent and evidence-supported office assignment.
- [ ] Add read-only agent tools using the same versions, coverage and source
  evidence as the map; include contact-process inquiry and supported neighbours.
- [ ] Validate current-reference versus historical requests, independent contact
  freshness, optional facets and stable manifest reads; no database fallback.
- [ ] Transfer the frozen response contract to the companion experience track
  before shared catalogue/API/agent registration work.

## R4 — future acceptance and release decision

- [ ] Validate urban, farm and public-land samples across WA/OR/ID, including
  boundaries/overlaps, missing feeds, stale contacts and unsupported history.
- [ ] Record source denominators, rights verdicts, schema/identity/geometry
  checks, bounded-reader budgets, schedule evidence and UI/agent parity.
- [ ] Apply the whole implementation batch before the appropriate final
  type/lint/boundary and scoped test sweep; retain release gates separately.
- [ ] Obtain independent data-governance, relationship and temporal review.
- [ ] Record publication/deployment acceptance under the release policy for each admitted
  slice; the current merge request does not resolve unadmitted source or broader product gates.
