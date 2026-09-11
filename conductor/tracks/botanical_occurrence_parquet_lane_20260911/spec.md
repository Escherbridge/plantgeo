---
type: track-spec
slug: botanical_occurrence_parquet_lane_20260911
status: planned
---

# Governed botanical occurrence and taxonomy Parquet plane

## Outcome

Build the governed data plane behind botanical specimen experiences. The plane
is separate from Sentinel-2 `vegetation`: a preserved specimen is evidence that
a documented collection event exists, not a measurement of vegetation cover,
abundance, current occupancy, suitability or surveyed absence.

This track remains design-only until
`pnw_herbaria_source_admission_20260911` admits an exact collection release.

## Logical artifacts

The design must version and link these grains:

| Artifact | Grain and purpose |
| --- | --- |
| Source collection | One publisher namespace and collection key, including admitted rights and scope. |
| Source release | One collection/version/content hash, with complete population and schema receipts. |
| Raw occurrence revision | One native occurrence in one release; preserve verbatim values and source row locator. |
| Identification revision | Every determination/annotation supplied for an occurrence. |
| Taxon concept and resolution | Source concept plus a pinned external authority/version and ambiguous/unmatched states. |
| Normalized occurrence | One raw revision plus a versioned normalization/QC recipe. |
| Spatial association | Occurrence × support version × admitted resolution, with uncertainty semantics. |
| Support evaluation | Release set × support cell × event window × QC policy, recording evaluated/admitted evidence coverage, confirmed/possible/withheld/generalized/nonspatial membership and exclusion counts. |
| Sparse cell/taxon summary | Release set × support × taxon concept × declared event window × QC policy. |

Native identity removes duplicate downloads of the same record. It must not
collapse multiple specimens from the same collecting event. Event clustering is
a separate confidence-bearing relationship; name/location/date coincidence is
not a deletion rule.

## Temporal contract

Start as `static_lookup`, keyed to an immutable collection-release set. Requests
carry two different times:

- the source snapshot/version describing which records and determinations are
  available; and
- the specimen collecting event date or interval used as an evidence filter.

Do not create daily ecological availability or claim what was historically known
from the collection date. When no historically eligible source release exists,
historical-publication queries refuse. Partial and interval dates remain partial
or interval-valued; no exact day is invented.

## Spatial and taxonomic contract

Exact public coordinates may support detail points. Generalized or uncertain
locations use a declared support and retain coordinate uncertainty. Broad or
unknown localities remain coarse or nonspatial. County centroids never become
specimen points, and withheld locations cannot be reconstructed.

Assign one deterministic index cell per admitted support without claiming it is
the true collection location. If uncertainty crosses cells, distinguish possible
from confirmed membership and cap fan-out. Sparse summaries count documented
records, event estimates, concepts and contributing collections. They never
zero-fill `cells × taxa × days` or infer absence.

Taxon resolution binds an authority and version, preserves source names and
determinations, and retains ambiguous/unmatched concepts. Homonyms and
infraspecific ranks cannot be collapsed by a name-only join.

## Publication and serving

Raw capture is immutable and restricted according to its admission. Derived
Parquet generations bind the source-release set, taxonomy/QC recipe, support and
aggregation version. All required detail and aggregate artifacts become durable
before one conditional pointer advances.

Register three executor duties only after implementation review: source refresh,
repair/reconciliation that authors bounded work, and coverage/release status.
Normal serving never scrapes the portal, lists historical prefixes or queries a
PostgreSQL observation table. Readers require a pinned release set, bounded
extent, taxon filter, event window and output limit, with explicit continuation,
coarsening, refusal and nonspatial results.

Agent neighbour results return the queried interval, the neighbour's own event
date or interval, signed and absolute temporal distance in days where computable,
the represented spatial support and real spatial distance. Interval overlap is
reported as overlap, not an invented exact-day match. Exact-result absence and a
substituted temporal or spatial neighbour are distinct response states; the tool
must never silently replace the requested result with a neighbour.

## Acceptance

Acceptance requires exact input/output reconciliation, source correction and
withdrawal behavior, idempotent replay, interrupted-publication recovery,
rights/taxonomy/QC version changes, all required spatial rungs, bounded reader and
agent tools, three executor schedules, rollback and independent review. This
track publishes data infrastructure; map layers remain owned by the experience
track.

The final data-contract, recovery and scientific-honesty verdict belongs to a
separate reviewer after implementation and evidence are complete. An author or
integration slice cannot approve its own work.
