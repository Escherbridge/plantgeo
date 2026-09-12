---
type: track-spec
slug: pnw_land_context_reference_plane_20260911
status: planned
---

# PNW land context and public contact reference plane

## Outcome and authority

Define a governed reference plane that answers which land or service boundaries
intersect a place, which public office or adviser is relevant to an idea, and
which documented public route can help with a contact-process inquiry.

This is a **registered planned packet**. [The registry](../../tracks.md) remains
the sole current work registry and indexes it as `planned`. Registration does
not activate the track or authorize execution.
Research and planning do not authorize implementation, acquisition/ingestion,
contact with anyone, publication, deployment or scheduler changes. No release
is admitted. Future execution requires explicit scope and the applicable
[release policy](../../release-governance.md).

The companion [experience track](../pnw_land_contact_experience_20260911/spec.md)
owns product presentation. This track owns proposed source admission, reference
identity, relationship evidence, serving contracts and agent data parity.
The [source inventory](evidence/source-inventory.md) is the retained evidence
baseline; its source metadata findings are not live coverage or ingestion receipts.

## Geographic and product boundary

Use Washington, Oregon and Idaho only. The query envelope is
`[-125, 42, -111, 49]` (WGS84 west/south/east/north), following the
[PNW pilot](../../../docs/runbooks/pmtiles-pnw.md). Apply a WA/OR/ID product mask
because the rectangle also intersects other jurisdictions. Retain the native
identity of an intersecting source feature; mark display clipping and compute
full-feature versus within-AOI areas explicitly.

Four separately governed product families:

| Family | Reference facts | Explicit limits |
| --- | --- | --- |
| Parcels and nonpersonal land use | County-namespaced parcel IDs, outlines, source/derived acreage with method, official record links, published ownership category, assessor use, dated crop summaries and urban membership. | Private owner names and personal owner contact data are excluded by default. Their absence is not missingness. Assessor use, crop cover, Census urban classification and local zoning stay distinct. No operator, tenant, title or development entitlement inferred. |
| Electric service territories | Publisher's retail/distribution territory and utility identity with reviewed entity crosswalk. | Approximate or overlapping polygons remain qualified. No capacity, physical asset, service guarantee or interconnection approval inferred. Gas/water/wastewater are outside this initial family and need separately admitted scope. |
| BLM surface management and office jurisdiction | Surface-manager records identifying BLM plus separate administrative polygons, office IDs and parent hierarchy. | Administrative extent is not BLM-owned land. Broader federal SMA sources must be filtered by verified manager classification. Surface, mineral and other rights stay separate. |
| State-managed land | Agency-specific land interests, reported public owner/manager/steward, regional responsibility and program references. | DNR/DSL/IDL are not interchangeable with all parks, wildlife, forestry or aquatic datasets. Report agency/interest coverage; no blanket all-state-land completeness claim. |

These UI families may contain multiple sources and temporal facets. They are
not automatically four Parquet lanes or four common-day temporal surfaces.
BLM/state absence must not imply private, unrestricted or unregulated land;
USFS, NPS, tribal and other authorities require their own coverage declaration.

## Reference records and identity

Use the existing [Parquet lane contract](../../code_styleguides/layer-lanes.md)
and enforced dependency lattice; do not invent a parallel lane package or an
environmental PostgreSQL fallback. The current geometry precedent is WKB in
Parquet. Any GeoParquet interoperability claim must be separately validated.

Proposed logical relations; physical names and grain must be frozen before coding:

- **Boundary versions:** source namespace, native feature key/version, family
  and interest type, state/county, nonpersonal attributes, WKB, bbox/CRS,
  record URL, source accuracy, derivation/clipping and lineage.
- **Organizations/offices:** publisher or reviewed internal registry identity,
  official public name/type, parent, office point and jurisdiction references.
  An office point helps navigation, not authority inference.
- **Public contact routes:** office/program role, documented topic and help,
  official inquiry/form URL, public business phone/email, optional published
  professional name, route type, status and verification evidence. Never
  import private owner names, private mailing lists or personal contact fields.
- **Place-office-topic relationships:** many-to-many subject and object IDs,
  relationship kind, applicable geography, documented topic, source/crosswalk
  evidence, effective interval if known, assignment method and review status.
- **Source/releases:** publisher, canonical endpoint, rights/attribution and
  permitted field set, source version/watermark, capture facts, immutable
  artifact identity, schema/normalization version, coverage and admission verdict.

A parcel key includes county/source namespace and the original string ID.
Preserve leading zeros. ArcGIS OBJECTID is not a durable identity without
evidence across releases. Preserve splits/merges as explicit lineage or unknown
continuity; never fabricate stable identity from coordinates or timestamps.
Office and utility name matching needs a reviewed crosswalk; names and nearest
points alone do not prove an assignment.

Keep private-owner exclusions effective in requested fields where possible,
normalization, Parquet, caches, tools and UI. If a bulk source cannot provide a
nonpersonal projection, its acquisition/minimization handling is an unresolved
admission decision before capture; this packet authorizes no such download.
A named public managing agency and public professional role remain in scope.

## Contact meanings and contact discovery

Typed route meanings must distinguish:

1. Direct responsible agency/operator, backed by an authoritative assignment.
2. Public property/record assistance and an inquiry about the contact process.
3. Subject-matter adviser with documented topic and service geography.
4. Introduction or message-forwarding service, only when expressly documented.

Unknown forwarding capability remains unknown. A records office's public
contact is not an identified private owner or representative. Advisers cannot
be promoted to approvers. Store the parcel/tract/location details to provide,
documented help, official contact URL, and a suggested inquiry separately from
a proven capability. A draft question may ask what process exists without
claiming an introduction, disclosure or forwarding will occur.

## Time and publication identity

Parcel/management/territory reference sources normally use `static_lookup`
with a genuine source change watermark and no forecast. Annual crop products
remain separately dated release facets. Follow the existing nature contract;
do not force different sources into daily observations.

Keep source-effective time/interval, source-published time, capture time and
contact-verification time separate, nullable when unknown. A successful page
request does not verify that a person holds a role. Transport validators, web
crawl time and a download clock are not automatically source release dates.
A source lacking a usable watermark requires an explicit contract decision;
do not silently substitute the poll time.

Boundary, crosswalk and contact releases can refresh independently. A serving
manifest must pin compatible versions, content identities and required/optional
facets for reproducible answers. The existing generic multi-lane coverage
contract intersects publication days; independently dated contacts and crop
releases cannot be naively bundled under that contract. An optional contact or
land-use gap must not hide available parcel geometry.

Selected-day readers use only evidence-supported applicable versions. If only
current reference is available, return that mode and its vintage explicitly;
historical ownership or jurisdiction is unavailable. Current office contacts
may accompany a historical boundary only with both clocks disclosed. Never
fabricate earlier snapshots from EIA county-history dates, newer map retrieval,
or differences between differently compiled releases.

## Admission, refresh, gaps and absences

Every source owes an admission verdict for the exact distribution, field set,
coverage and version. Public endpoint access alone is not reuse clearance.
The inventory carries mandatory unresolved gates: Idaho utility polygons,
Idaho production state-land feed, Oregon current utility machine feed, county
parcel field/rights coverage, and all remaining source-specific reuse conditions.
Verify map display, retained/derived Parquet, API/agent exposure and any export
within the intended rights; do not generalize one dataset's public-domain status.

Future scheduler duties belong to the existing continuous job executor and
durable ledger, per the [layer standard](../../../docs/layer-lane-standard.md).
No new Railway cron, separate polling daemon or operational mutation is planned
by this packet. A later implementation must register bounded duties for:

- Source checks on a measured cadence; an unchanged source owes no duplicate
  daily snapshot. A newer source watermark owes one current snapshot.
- Coverage reconciliation against expected jurisdictions, source releases,
  required fields and relationship coverage; author idempotent repair work
  for recoverable missing/stale releases or broken required joins.
- Status, retry/dead-letter and governed absence publication with evidence.

Static coverage is not a missing-day calendar. Distinguish not checked, current,
stale, partial, source unavailable, withheld by terms, unsupported history and
documented out-of-coverage. Private names excluded by policy are not repair
work. An outage is not proof of source absence; an empty or capped response is
not success. Contact link failures and content verification age are separate
states. Retractions, deleted features and revoked contacts require explicit
version/tombstone evidence, not disappearance inferred from a partial response.

## Bounded readers and agent contract

Serve through governed Parquet readers, not per-hover upstream requests.
Support point containment, bounded bbox/selected-AOI intersection and bounded
relationship/contact lookup by validated identifiers and topic. Freeze numeric
limits for AOI area, geometry vertices, features/relationships, response bytes,
time, pagination and rate/concurrency before implementation. A request outside
the pilot or over budget gets a typed response; no silent truncation.

Bbox/row-group pruning precedes exact source-resolution predicates. Intersection
finds candidate reported features, not legal proof. Coarse display geometry
must not select the responsible office at an edge. Return multiple intersecting
offices/territories and the overlap basis for a selected area; do not replace the
area with its centroid or the nearest office. Uncertain edges remain qualified.

Tool schemas should expose bounded equivalents of boundary resolution,
responsible/public-inquiry contact lookup, topic/geography adviser lookup,
coverage and supported temporal/spatial neighbours. Reuse the same readers and
selected point/AOI, versions and status used by the companion UI. Neighbour
distance is supplemental and must not be represented as jurisdiction.

Every answer carries source feature/release, matched region or overlap,
organization/office, role/route type, assignment evidence, public contact URL,
verification time, documented help and unresolved gaps. Distinguish no matching
feature in proven coverage from unknown coverage or unavailable history.
Tools are read-only. They may produce an inquiry draft for review, never send it.
Proposed names and registration points are not claims that tools already exist.

## Future acceptance

All future stages remain unchecked in [the plan](plan.md). Acceptance must
demonstrate source-rights admission, stable identity, excluded-field handling,
snapshot freshness, reproducible manifests, bounded readers, exact/ambiguous
boundary behavior, optional-facet independence, temporal truth and agent/UI
parity across a small representative WA/OR/ID sample. Include urban, farm,
public-land, overlapping-territory and missing-source cases.

Preserve measured coverage denominators and rejected sources; a representative
sample is not complete PNW coverage. Later runtime changes require their scoped
checks after the full batch and independent review. This documentation packet
requires only metadata/frontmatter/link/whitespace/scope validation and a
separate [planning review](evidence/independent-review.md). Planning acceptance
does not complete the track or authorize publication.
