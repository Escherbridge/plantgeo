---
type: track-spec
slug: pnw_herbaria_source_admission_20260911
status: active
---

# PNW Herbaria source admission and release governance

## Outcome

Establish whether a small, named set of Consortium of Pacific Northwest Herbaria
collections can be captured, normalized and redistributed as governed specimen
facts. This track ends with an admission or refusal packet for each collection.
It publishes no PlantGeo layer.

## Active track and blocked collection admissions

The track remains active under planning baseline `b9b7bf4`; WTU and UBC
occurrence-release admissions remain blocked with no admitted releases. Active
means the source-governance work is open, not that acquisition gates have passed.
The [synchronization record](evidence/baseline-synchronization.md) preserves the
historical evidence and distinguishes these two states.

The [occurrence plane](../botanical_occurrence_parquet_lane_20260911/spec.md)
owns spatial specimen facts. The parent-owned
[species-profile lookup](../botanical_species_profile_lookup_20260911/spec.md)
owns nonspatial growth requirements and per-value evidence. They share a pinned
taxon authority/version/concept key; traits are not occurrence attributes, and a
missing profile must neither drop specimens nor block an otherwise admitted
occurrence release. The
[recommendation-validation track](../botanical_species_recommendation_validation_20260911/spec.md)
composes documented occurrence, establishment compatibility and separately
reviewed objective-effect evidence without conflating them.

## September 11 execution boundary

The [admission packet](evidence/admission-packet.md) completes the bounded
metadata investigation. Both occurrence-release admissions remain blocked;
standalone UBC EML is captured, but no specimen archive is acquired or admitted.
See the [source register](evidence/source-register.md),
[independent verdict](evidence/independent-review.md) and
[requests and handoff](evidence/requests-and-handoff.md).

The owner subsequently made occurrence ingestion an intended outcome and
authorized a bounded WTU/UBC data-only pilot after collection redistribution
rights, stable release identity, attribution, coordinate withholding and archive
controls pass. Report the permission gate before downloading. Exact archive
validation follows only a permitted quarantine capture; metadata alone cannot
certify archive bytes. Images, publication, scheduling, API/UI exposure and
production changes remain excluded. Runtime/service/frontend work is not part
of this source-admission evidence pass.

The parent owns a separate nonspatial species-profile track. Its approved
immutable botanical-species-profile Parquet release is the only serving input;
agri.species or compatible reviewed authoring may curate draft values, with no
live database fallback. This track owns source facts and occurrence handoff only.

The source investigation is retained at commit `35624dd`, path
`conductor/research/pnw-herbaria-source-viability-20260910.md`. That note found a
documented bulk-download path and a conditionally viable data-only pilot. It did
not download an archive, prove a stable incremental feed, or reconcile the
portal's live headline count with its dated download inventory.

## Initial scope

Start with **WTU vascular** and **UBC vascular** only. Both provider pages were
recorded as candidates for a specimen-data pilot, subject to the exact terms in
the downloaded EML and archive. Do not admit an institution as one unit when its
collections carry different terms. Do not acquire specimen images; media rights
are a separate surface from specimen facts.

For every candidate collection, preserve:

- canonical access and metadata URLs, publisher, collection identifiers,
  citation, rights holder, terms, advertised record count/size and update value;
- response validators, retrieval time, content SHA-256, archive/member hashes,
  EML, field mappings, parser version and complete/partial/quarantined outcome;
- public/sensitive/locality-generalized scope and every rights or policy
  exclusion; and
- a refusal reason when terms, identity, schema or bounded-processing rules do
  not support admission.

## Evidence questions

The pilot must measure rather than assume:

1. Which Darwin Core core and extension fields are present and populated?
2. Do native occurrence and extension identifiers remain stable across at least
   two equivalent complete releases?
3. How many records are georeferenced, within PlantGeo's declared envelope,
   nonspatial, withheld, generalized, taxonomically unresolved or quarantined?
4. Can source deletions and corrections be distinguished from partial exports
   or rights exclusions?
5. What source watermark represents a collection release? HTTP validators are
   transport facts, not automatically biological publication versions.
6. What are the measured compressed/decompressed bytes, rows, peak memory,
   parsing time and retry behavior?

## Bounds for the pilot

The planning ceilings inherited from the research are two allowlisted archives,
one download at a time, 64 MiB compressed per archive, 128 MiB compressed total,
2 GiB decompressed total, 32 archive members, 600,000 core rows, two million
extension rows, eight HTTP attempts and a ten-minute wall deadline. Exceeding a
bound records a deferred or failed result; it does not justify silently widening
the job.

Reject archive traversal, duplicate member names, unexpected schemas, missing
or incompatible EML and any attempt to recover withheld coordinates. Keep bytes
quarantined until the collection's terms and schema are admitted.

## Acceptance

This active track completes when every pilot collection has a dated admission/refusal
packet, immutable source-release identity, schema/profile receipt, identity
stability verdict, rights decision, processing budget and rollback/withdrawal
procedure. Admission authorizes the downstream design tracks to use those exact
releases; it does not authorize publication, scheduling or production ingestion.
