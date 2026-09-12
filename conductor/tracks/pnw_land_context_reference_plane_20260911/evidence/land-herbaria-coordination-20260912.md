---
type: coordination-gates
track: pnw_land_context_reference_plane_20260911
status: planned
reviewed_on: 2026-09-12
source_revision: fe9098a
---

# Land context and Herbaria coordination gates

## Checkpoint and authority

This local documentation reconciliation resumes the registered land packets at
`fe9098a`. Both land tracks remain **planned**, not active or admitted.
[The registry](../../../tracks.md) and each track's metadata now agree on
registration; the original unregistered planning review remains dated history.

Only local Conductor records were inspected. No new web/source verification,
production, Railway, database, object-store, ingestion or contact operation was
performed or authorized. This note does not widen Herbaria's conditional capture
scope, close a rights gate or change another track's ownership. A narrow
documentation commit is within this resumption's scope; implementation is not.

## Current evidence, without borrowing admission

| Owning record | Current recorded state | Consequence for land/contact planning |
| --- | --- | --- |
| [Herbaria metadata](../../pnw_herbaria_source_admission_20260911/metadata.json), [decisions](../../pnw_herbaria_source_admission_20260911/evidence/admission-decisions.json), [September 12 refresh](../../pnw_herbaria_source_admission_20260911/evidence/metadata-refresh-20260912.md) | Governance track active; WTU and UBC admissions blocked, no admitted releases. Refreshed metadata did not measure an archive or admit an occurrence. | Neither collection is an input to a parcel association, contact recommendation or production land tooltip. Land-family admission is independently scoped. |
| [Herbaria governance audit](../../pnw_herbaria_source_admission_20260911/evidence/source-governance-audit-20260912.md) | Offline audit retains WTU exact-release/EML and UBC institutional coordinate-policy gates; audit frontmatter still says pending independent review. | Use it to locate outstanding evidence, not as a new permission or independent approval. Do not reopen acquisition under this land pass. |
| [Occurrence plane](../../botanical_occurrence_parquet_lane_20260911/spec.md) | Planned admitted-release contract separates collection time, publication time, coordinate support/uncertainty and withholding; forbids reconstructing withheld locations. | A specimen is collection-event evidence, not current occupancy, parcel ownership, an operator identity or land-access authority. |
| [Species-profile metadata](../../botanical_species_profile_lookup_20260911/metadata.json), [specification](../../botanical_species_profile_lookup_20260911/spec.md) and [September 12 audit](../../botanical_species_profile_lookup_20260911/evidence/non-herbaria-profile-contract-audit-2026-09-12.md) | Active profile work has a transitional reviewed-authoring lookup marked not published. The audit records no admitted non-Herbaria source or immutable profile release; metadata separately retains an operator-authorized authoring-census gate. | Independent field-level source admission and the separately authorized census are distinct outstanding gates; neither supersedes the other. No database read or published-profile assumption here. Traits or suitability do not follow from a specimen/parcel overlay. |
| [Land source inventory](source-inventory.md) | Four separately governed WA/OR/ID families; source candidates and public records/help routes, no admitted releases. | Preserve private-name exclusion and the existing Idaho/Oregon/parcel rights gates. Herbaria CC0 labels cannot clear land-source or contact-directory conditions. |

## Binding boundary for any later botanical association

The land tracks do **not** depend wholesale on Herbaria admission. Independently
admitted parcel, utility, BLM and state sources and their public office routes
may progress under their own later scope. Herbaria is a conditional dependency
only if an occurrence-derived overlay, parcel association or contact suggestion
is proposed. Such an association is not added to the four-family scope by this note.

Before such use, require exact occurrence release admission, an admitted land
release, intended-use rights for both, and an independently reviewed precision/
relationship policy. Store collection/distributor/release/occurrence identity
separately from county/source/parcel ID and boundary version. A taxon identifier,
collector name, catalog number or institutional contact is not a parcel key,
owner/operator identity, regional SME assignment or introduction service.
An institutional data-clarification contact is not automatically authorized to
grant reuse rights; the owning admission task must establish that authority.

Do not locate, narrow or identify a withheld/generalized specimen site through
parcel boundaries, assessor links, contacts, media, free-text locality, external
sources or repeated point/bbox queries. A public parcel ID can reveal a location
without an owner's name. Apply the admitted precision policy to geometry,
derived parcel IDs, contact-route explanations, agent answers and copied drafts.
Source-public precise coordinates do not automatically authorize every derived
association; check release/record-level restrictions and uncertainty first.

Where support spans multiple parcels, an allowed association must preserve
possible/ambiguous membership and its permitted resolution. Do not label the
centroid-containing or nearest parcel as the collection site. A coarse permitted
regional records/adviser route may be useful if it does not narrow withheld
support; otherwise return a specific unavailable-association state. Ordinary
user-selected parcel lookup remains independently governed and must not become
a backdoor for restricted occurrence reconstruction.

Pin the occurrence release, collection/event interval, land boundary vintage,
relationship policy and contact verification time independently. A present-day
parcel intersecting a historical collection support does not establish the
historical owner, collecting permission or a currently growing population.
Any planting/suitability/objective-effect claim belongs to the separately gated
[recommendation-validation track](../../botanical_species_recommendation_validation_20260911/spec.md).

## Concrete next gates and owners

| Gate | Owner / coordination | Evidence required before moving beyond it |
| --- | --- | --- |
| L1 — land source admission | Land reference-plane owner | Resolve Idaho utility polygons, Idaho production state-land feed and Oregon current utility machine feed; county parcel nonpersonal fields/geographic coverage; exact distribution reuse/attribution, source watermarks and native IDs. Missing private owner names remain intentional, not a blocker. |
| L2 — public contact-role proof | Land reference-plane + contact-experience owners | Reviewed place/office/topic crosswalk, official help page, public business route, service geography and verification status. Distinguish assessor/recorder help, agency responsibility, advice and expressly documented forwarding. Curator/licensor/collector identity alone proves none of these. |
| H1 — source preflight, only in its owning scope | Herbaria source-admission owner | WTU release-bound EML/terms and public-coordinate binding; UBC policy applicable to the exact institutional IPT distribution; approved custody, retention/withdrawal and archive controls. Portal suppression claims cannot be transferred to institutional exports. Requests remain unsent absent separate outreach authority. |
| H2 — archive/identity admission, after H1 | Herbaria owner + independent admission reviewer | Permitted quarantine capture would measure actual field maps, archive/member hashes, populations and native-ID stability; metadata counts do not substitute. The existing two-archive ceiling remains: a two-release UBC comparison defers WTU. No capture in this land task. |
| C1 — optional botanical/parcel association decision | Occurrence + land + experience owners, independent governance reviewer | Decide whether to authorize this additional use at all; if so, prove exact-release rights, permitted precision, ambiguity/refusal cases and non-reconstruction before exposing any joined parcel ID or contact route. Do not block unrelated land families on this gate. |
| C2 — selected-area/tool integration | Land reference-plane + experience owners and active shared-file owners | Freeze version/selection and optional-facet contracts, bounded point/AOI queries, contact explanation and refusal semantics. Arrange explicit ownership transfer before changing catalogues/readers/agent/shared selection code. No execution allocation is made here. |

The next safe local work is to prepare source-specific admission decision
templates and offline contract cases against these gates. Any additional
metadata retrieval, provider clarification or capture must be scoped in the
owning task; this local-only resumption grants none.

## Proposed acceptance cases

All remain future checks, not tests executed or admissions made:

- Blocked WTU/UBC releases yield no occurrence-to-parcel/contact association,
  while an independently admitted ordinary land lookup remains usable.
- Generalized support spanning parcels cannot produce one exact collection
  parcel, a hidden-location clue in a draft, or a nearest-owner contact.
- An expressly permitted precise association preserves occurrence identity,
  boundary vintage and relationship evidence without claiming title or access.
- A user-selected parcel records-help card may work with private names absent;
  a botanical curator is not silently substituted as its owner contact or SME.
- Historical collection/current parcel/current office clocks are separately
  disclosed; missing history or an unpublished profile cannot become suitability.
- Optional withheld/unadmitted botanical enrichment does not hide admitted land
  geometry, invent surveyed absence, or create a private-name repair task.

See the [independent reconciliation review](land-herbaria-review-20260912.md).
The original [planning review](independent-review.md) and source inventory are
preserved unchanged; they do not certify this successor or current source access.
