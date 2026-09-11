---
type: research-note
status: proposed
source: pnw-herbaria
researched_on: 2026-09-10
---

# PNW Herbaria: source viability and botanical occurrence lane

This is a plan and primary-source evidence record. It does not implement or activate a lane.
No specimen archive or image was downloaded; no database, service, bucket, scheduler, or production
state was changed. Portal pages and standards documentation were inspected anonymously.
The proposal is conditionally viable for botanical specimen occurrences and documented-taxon
summaries. It cannot establish vegetation cover, vegetation community type, abundance, absence,
current occupancy, or planting suitability from specimen records alone.

## Source evidence

The portal is hosted at the Burke Museum and managed by University of Washington Herbarium/Burke
Museum staff. It aggregates vascular plants, nonvascular plants, fungi, lichens, and algae across
regional collections. The portal's regional navigation includes Alaska, British Columbia, Idaho,
Montana, Oregon, Washington, and Yukon. These labels identify its focus, not a verified geographic
extent for every record. [Portal ownership and scope](https://www.pnwherbaria.org/about.php),
[portal home](https://www.pnwherbaria.org/).

The [published download inventory](https://www.pnwherbaria.org/data/datasets.php) provides whole-portal
and per-collection ZIPs in Native and Darwin Core Archive formats. DwCA contains occurrence,
identification and media-metadata files, plus field mapping and EML metadata including licenses.
Its archive-local `id` corresponds to Native `OccurrenceID`; extensions join through `coreid`.
Public exports omit sensitive localities and some withheld records. The listed whole export is
3,266,234 records, 516.4 MB DwCA, dated 2026-06-04. WTU vascular is listed at 267,636 records/52.9 MB;
UBC vascular at 191,189/27 MB. The home page instead reports 3,328,306 records and 1,800,701 images
from 55 herbaria. These are different published populations, not a reconciliation result or a count
of records inside PlantGeo's target envelope. The discrepancy is unresolved.

The [usage policy](https://www.pnwherbaria.org/data/datausagepolicy.php) permits use of raw specimen
facts, disclaims guaranteed completeness/quality, assigns accuracy responsibility to providers,
asks users to respect sensitive-data restrictions, and encourages provider/NSF acknowledgment.
The [sharing policy](https://www.pnwherbaria.org/data/datasharingpolicy.php) says providers retain
rights. Collection-specific terms therefore require explicit admission review.

| Reviewed provider metadata | Specimen terms stated by provider | Image terms stated by provider | Admission implication |
| --- | --- | --- | --- |
| [WTU](https://www.pnwherbaria.org/data/providermetadata.php?code=WTU) | Public domain / CC0 for the displayed collections | CC BY-NC-SA 3.0 US | Candidate for a data-only pilot, subject to matching archive EML and record terms. |
| [UBC](https://www.pnwherbaria.org/data/providermetadata.php?code=UBC) | Public domain / CC0 for the displayed collections | CC BY-NC-SA 3.0 US | Second candidate; retain collection attribution and verify export metadata independently. |
| [OSC](https://www.pnwherbaria.org/data/providermetadata.php?code=OSC) | Vascular, bryophyte, algae, lichen and USFS lichen entries state CC BY-NC 4.0; the fungi entry states public domain / CC0 | Corresponding media have NC-SA terms | Do not admit the institution as one undifferentiated dataset or infer commercial redistribution permission from the portal policy. |

These are observations of published metadata, not a legal determination of competing terms.
Unresolved or incompatible rights keep the affected collection out of the distributable release.
The website's code, prose, thumbnails, and photographs have separate rights from specimen facts.
Do not acquire image bytes for this lane.

The WTU provider explicitly links
[its vascular DwCA access point](https://www.pnwherbaria.org/data/getdataset.php?File=WTU_Vascular_DwCA.zip).
UBC and OSC metadata also identify provider-hosted archive routes; those routes were not fetched.
Choose one distributor per collection release to avoid double ingestion of the same specimens.

No supported public REST API, incremental-change feed, archive retention policy, deletion feed,
rate-limit specification, or cross-release identifier stability guarantee was established by this
bounded review. The [documentation page](https://www.pnwherbaria.org/documentation.php) and
[search interface](https://www.pnwherbaria.org/data/search.php) do not establish those contracts.
The search form exposes taxonomy/synonym expansion and collecting-date filters; that is insufficient
evidence to build an undocumented query API. Prefer documented collection exports over scraping
species search pages.

## Model proposal

Use the provisional lane identity `botanical-occurrences`, separate from the existing
Sentinel-2 `vegetation` lane. Species are dimension values, not separate runtime lanes, services,
tables, or object-prefix families. Allow taxonomic groups and unresolved ranks without labeling
all records as vascular plants or all determinations as species.

The [Darwin Core terms](https://dwc.tdwg.org/terms/) distinguish occurrence identity from the digital
record, allow event dates to describe intervals, define coordinate uncertainty as a horizontal
distance, and distinguish withheld information from data generalization. Zero uncertainty is
invalid; missing uncertainty is unknown. Preserve datum, original coordinates and precision.
These standard fields are proposed mappings, not a claim that every portal export populates them.

| Logical artifact and immutable grain | Proposed fields and interpretation |
| --- | --- |
| Source collection, one `(publisher_namespace, collection_key)` | Provider, institution/collection identifiers, original herbarium code, canonical metadata/access URLs, citation, rights holder, taxonomic scope, geographic scope evidence, admitted rights class and policy receipt. A provider may own multiple collections with different rights. |
| Source release, one `(collection_key, source_version, content_sha256)` | Publisher change watermark and its meaning, advertised export day/count/size, archive/member checksums and lengths, exact source URL, ETag/Last-Modified when supplied, retrieval time, source publication time when established, schema mapping hash, policy/EML snapshots, parser version, complete/partial/quarantined state. An HTTP header is a transport validator, not automatically a biological or publication date. |
| Raw occurrence revision, one `(collection_key, source_record_key, release_key)` | Unaltered decoded values plus member/row locator, portal ID, provider occurrenceID/GUID, catalog/accession/barcode, basis of record, collector/field number, original taxon determination, eventDate/verbatim date and parsed interval/precision, country/state/county/locality, raw coordinates/datum/uncertainty, habitat/remarks, source modified value, withholding/generalization flags, row hash. |
| Identification revision, one source identification key within occurrence/release | Every determination and annotation, determiner/date, name/rank, qualifiers and type status where supplied. Preserve the source's current determination independently from an external taxonomic resolver. |
| Taxon concept and resolution edge, one source concept within taxonomy release | Verbatim scientific name, source taxonID/nameID when present, accepted-name relation, rank/family/genus, synonym edges, authority and authority version, resolver method/status. Retain ambiguous/unmatched concepts; never collapse homonyms or infraspecific ranks by a name-only join. |
| Normalized occurrence, one raw revision plus normalization recipe | Validated public coordinate support, exact/interval/unknown event date, taxon resolution, cultivation/establishment status when explicitly supplied, access decision and QC reasons, parent source release, geometry/normalization version. Preserve raw records that cannot support mapping in a non-map partition. |
| Spatial association, one occurrence revision × support version × admitted resolution | Canonical display cell, support identity and uncertainty class, source point vs possible-location region, membership method and distance semantics. Non-georeferenced records can support disclosed administrative summaries without creating a point at the county centroid. |
| Cell/taxon summary, one release-set × support/cell × taxon concept × declared event window × QC policy | Specimen-record count, explicitly defined distinct collecting-event estimate, first/last event interval, collection count, uncertainty/missingness counts, supported identification count, queryable provenance. This counts documented evidence, not organisms or vegetation density. |

The portal's documented internal uniqueness is useful, but it does not prove durable identity across
exports. Compare at least two releases before accepting it as a change-tracking key. Require an
explicit source-provided native identity; never mint occurrence identity from coordinates, current
names, row position, or a content hash. Hashes identify a revision's bytes. If extension rows lack
stable IDs, retain their ordered source membership within a release without pretending it is
cross-release biological identity.

Separate duplicate downloads of one native record from multiple specimens of the same collecting
event. Native identity removes the former; the latter remain records linked by a separately
versioned, confidence-bearing event-cluster rule. A name/location/date coincidence cannot safely
delete a specimen. Count unique taxa from base membership at each support, or from mergeable exact
sets; never sum child-cell richness or merge synonymous names without a pinned concept map.

Use one source-release set across raw, normalized, taxonomic, spatial, aggregate, and publication
artifacts. Changing a determination, coordinate, admitted rights policy, taxonomic mapping or
deduplication recipe produces a new derived generation with lineage. Record upstream removal as
a tombstone only after comparing complete equivalent source populations; partial downloads and
rights exclusions are not deletions. Keep public pointers from serving a withdrawn record and its
dependent aggregates while retaining restricted audit evidence under the applicable retention rule.

## Temporal meaning and source coverage

Start as a `static_lookup` reference snapshot with `horizon: none`, contingent on a verified
source-change watermark. Per-collection metadata distinguishes data updates from metadata updates;
neither automatically proves historical publication availability. A source collection version and
a bundle's vector of collection versions remain explicit. Do not label the request/poll clock,
specimen collection date, or a maximum date observed in records as a source release date.

No daily ecological coverage obligation is justified. Do not fabricate a historical publication
axis from old collecting dates. Upstream archive history was not established. Declare that refusal
until dated immutable releases or equivalent publication evidence exists. Freeze an event-history
scope only after inventory evidence establishes its limits; an earliest record statistic must
not continually redefine the completeness contract.

The initial map describes specimens documented in the chosen reference snapshot. A user-selected
event day/window can filter collecting evidence while showing the snapshot version separately.
If the product asks what was known on a historical day, serve only a provably eligible source
release and compatible taxonomic knowledge version, or refuse. Present-day curation applied to
an 1890 specimen does not prove it was published or identified that way in 1890.
Interval dates return an interval relationship, never a fabricated exact date.

Measure coverage on several separate denominators:

- Download coverage: expected public collection exports versus admitted complete exports.
- Ingest coverage: source-advertised/core-file records reconciled to accepted, out-of-scope,
  excluded-by-rights, nonspatial, duplicate-native-record, and quarantined records with reasons.
- Spatial usability: valid public coordinates, known datum, uncertainty distribution, administrative
  consistency, explicit target-envelope membership, and records withheld from fine-scale rendering.
- Taxonomic usability: identified to species/infraspecific rank, higher-rank-only, unresolved,
  conflicting determinations, and names changed by each resolver version.
- Event coverage: exact dates, partial dates, unknown dates, represented collecting periods and
  collection effort. A period/cell with no records means no documented records in the admitted
  material, not surveyed absence.

Primary release expectations are the admitted collection set and its versioned members; source
growth and out-of-envelope records must not masquerade as PlantGeo gaps. Store excluded collection
counts visibly. Neither portal membership nor the full portal count proves a complete PNW flora,
an exhaustive regional survey, or maximum-available historical knowledge.

## Spatial products and many-species cost

Reuse soil's fetch-once/derive-many discipline, not its dense observation semantics. The existing
ERA5 soil contract requests all eight variables together and reuses the response per chunk-day
(`pipeline/direct/AGENTS.md`, “One archive request per support chunk-day”). SSURGO represents
map-unit delineations, and SoilGrids represents predicted soil properties; neither makes a sparse
specimen location into a surveyed area.

Acquire each allowed collection release once, normalize once, then compute deterministic spatial
partitions and sparse taxon aggregates offline. A spatial job is one bounded source partition or
support block containing many taxa. It must not issue one portal request for every species or every
species-cell pair.

Choose a versioned support only after coordinate uncertainty is profiled. Provisional display rules:
exact public points at permitted detail; coarse cells with record/taxon summaries at lower zoom;
no small-area claims from a broad uncertain locality. Keep a deterministic cell for indexing without
claiming it is the true collection cell. If an uncertainty region crosses cells, distinguish
possible membership from confirmed support; cap such fan-out and put very broad/unknown locations
in an explicit coarse/nonspatial bucket. Do not smear one record into many apparent observations,
turn coordinate uncertainty into a probability distribution, or recover withheld locations using
other sources.

A vegetation-type or trait-group view would require a separate versioned source mapping taxa to a
defined classification, plus validation of the classification's intended meaning. A functional-group
filter could be displayed as recorded taxa in that group; it still would not measure the dominant
community or percent cover. Distribution modeling would be a separately validated modeled product
with sampling-bias treatment and holdout evaluation, not a byproduct of ingestion.

Budget with measured row groups and occupied combinations:

- Let `N` be eligible occurrence revisions and `K` admitted spatial rungs. Canonical point
  assignment requires at most `N × K` memberships per selected event window. Sparse aggregate rows
  are bounded by those memberships and usually merge many records. Uncertainty expansion requires
  its own explicit multiplier cap.
- Dense `cells × species × days` is forbidden. For illustration only, 1,568 cells × 10,000 taxa
  is 15.68 million slots before adding any time axis; zero-filling those slots invents absences.
  Neither 10,000 taxa nor this grid is an observed botanical inventory or a proposed native support.
- A 100–250 byte compressed normalized-row assumption would place 3.27 million rows near
  0.33–0.82 GB per complete generation, excluding raw archives, annotations, taxon maps, indices,
  aggregates and replicas. This is a sizing hypothesis, not a measured cost.
- Measure compressed bytes, row-group selectivity, peak RSS, spill bytes, transform CPU,
  per-rung row counts and archive decompression ratio on the pilot before extrapolating.
  Changed collections invalidate affected spatial/taxon partitions; unchanged raw blobs remain
  content-addressed and reusable. Compare delta complexity against a full bounded rebuild first.
- Monetary costs require actual storage/request/compute tariffs and measured workloads; this review
  provides no dollar estimate.

## Bounded next stage, proposed and not executed

A future technical pilot should use WTU vascular plus UBC vascular only after the relevant EML and
provider terms agree with the intended use. It should capture the exact byte-level schema and
produce a reviewable inventory; it should not publish the whole consortium.

Proposed engineering ceilings, not publisher quotas:

| Stage | Ceiling and stop condition |
| --- | --- |
| Inventory/metadata refresh | One inventory fetch and one metadata fetch per admitted collection; conditional GET only where supported. Weekly polling is an initial operator policy, not a claimed source schedule. No-change content does not create a release. |
| First archive pilot | Two allowlisted archive URLs; one download at a time; 64 MiB compressed per archive and 128 MiB total; eight HTTP attempts including retries, 30-second per-request deadline and ten-minute wall deadline. Any exceeded bound ends in deferred/failed state with resumable evidence. |
| Archive processing | 2 GiB decompressed total; at most 32 archive members, 600,000 core rows and 2 million extension rows; reject traversal, duplicate member names, unexpected schemas and excess sizes. Missing or incompatible EML keeps bytes in quarantine. |
| Quality sample | Up to 25,000 deterministic records per collection for detailed profiling, stratified by date/rank/coordinate quality where feasible; complete core/member counts still required. A sample cannot certify complete geography, no rare-data leaks or cross-release identity. |
| Serving prototype | One immutable manifest/release set per request; capped taxon filter, requested extent/time window and explicit output limit. Provisional maximum 5,000 detail records or 2,000 aggregate cells, 8 MiB serialized response and a two-second server budget; overflow yields continuation or a coarse result with disclosure. Benchmark before declaring an SLO. |

For remote Parquet, additionally cap object/row-group reads and scanned bytes from the pilot
measurement. Detail responses should paginate; broad viewports use aggregate rungs. Taxon search
uses the compact concept dictionary. Cache keys bind release-set SHA, taxon-map/QC recipe, spatial
support/rung, taxon filter and event window. Rendering and agent reads must share the same admitted
generation and query semantics. Normal request paths never scrape the portal, list historical
prefixes, scan all archives, or query PostgreSQL observation tables.

## Delivery gates

Apply the current [layer-lane contract](../code_styleguides/layer-lanes.md) within the enforced
foundation → method → warehouse → pipeline → planes → interface lattice, plus the process outcomes
in [the layer standard](../../docs/layer-lane-standard.md). Preserve the runbook's Parquet decision
and the current single continuous executor; do not recreate historical cron services.

1. **Admit sources:** complete collection/rights inventory; exact mapped schema; verified native-ID
   behavior and source watermarks; explicit public-only scope; reconciled pilot counts; a declared
   event-history scope and historical-publication refusal where evidence is missing.
2. **Build and replay:** immutable source capture/checkpoints, deterministic normalization and
   taxonomic mapping, byte/row/time budgets, quarantine, idempotent changed-release publication,
   complete release-set receipts, source deletion/correction handling and rollback.
3. **Publish spatial products:** explicit supported rungs and QC policy; no duplicate record
   inflation; all required rung receipts durable before one conditional pointer advancement.
   Register honest static/reference availability. A future release series must additionally publish
   the generational availability index; never bootstrap it in a user request.
4. **Register three executor duties:** source refresh, repair/reconciliation that authors bounded
   work for stale/missing collection versions or derived partitions, and coverage status.
   Lease/checkpoint/retry/dead-letter behavior comes from the sole executor. No biological
   absence is inferred by the work ledger.
5. **Serve and expose:** capability catalogue, map legends and snapshot/event semantics, bounded
   Python Parquet reader, provenance/quality counts, and agent tools returning the selected
   reference release plus event intervals and temporal/spatial neighbor distances. Uncertain
   locations return distance to the represented support and uncertainty, not a falsely exact
   distance to the organism.
6. **Independent verification:** fixtures for rights mixtures, changing taxonomy, same-event
   duplicate specimens, reused/missing IDs, partial dates, missing/invalid datum and uncertainty,
   withheld coordinates, corrupt/incomplete archives, source shrinkage, interrupted publication,
   query caps and empty/nonspatial results. Run one integrated affected verification pass after
   all implementation changes. No tests were needed or run for this research-only file.

## Evidence files and limits

Raw browser-tool responses were saved before bounded filtering under `.omc/research/`:
`pnwherbaria-home-2026-09-10.md`, `pnwherbaria-access-2026-09-10.md`,
`pnwherbaria-policy-metadata-2026-09-10.md`, `pnwherbaria-contracts-2026-09-10.md`,
`pnwherbaria-contract-detail-2026-09-10.md`, `pnwherbaria-sharing-2026-09-10.md`,
`pnwherbaria-osc-2026-09-10.md`, and `pnwherbaria-ubc-2026-09-10.md`.
They include fetch intent/date and source URLs. Some results are indexed/cached page extracts;
all reported counts/dates above are website assertions, not fresh archive measurements.
They are evidence for this proposal, not an ingestion receipt.

Still unknown: exact archive field population, usable georeferenced fraction, in-envelope counts,
species count, record and taxonomy stability across exports, earliest defensible collection scope,
historical release availability, systematic source cadence, transport quotas and performance.
Those are the pilot's acceptance questions; none has been silently converted into a production fact.
