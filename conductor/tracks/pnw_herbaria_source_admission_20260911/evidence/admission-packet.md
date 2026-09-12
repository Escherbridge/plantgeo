---
type: source-admission-packet
status: blocked
reviewed_on: 2026-09-11
---

# WTU and UBC vascular specimen admission packet

**Author recommendation: admit no occurrence release yet.** This is a completed
metadata investigation and a blocked admission packet, not a complete A1/A2
archive pilot. The independent verdict belongs to [the reviewer](independent-review.md).
No occurrence corpus, archive member or image was acquired. No terms were accepted,
institution contacted, service changed, data ingested or production state mutated.

The owner subsequently authorized a bounded data-only pilot after collection
redistribution rights, release identity, attribution, coordinate withholding and
archive controls pass. The open pre-acquisition gates below prevent exercising
that authorization in this pass. Archive validation after a permitted quarantine
capture remains necessary; a metadata check cannot certify archive bytes.

The [machine-readable decisions](admission-decisions.json) keep unmeasured
archive hashes, lengths, members and profiles null rather than zero.

## Intended use and collection boundary

PlantGeo proposes to preserve public specimen facts in restricted raw custody,
normalize them to immutable Parquet, and eventually redistribute approved facts
and sparse documented-taxon summaries through maps, downloads and agent responses,
including potential commercial use. Publication is separately gated. Specimens
are evidence of collection events, not abundance, surveyed absence, current
occupancy, habitat requirements or intervention effects.

Only WTU vascular and UBC vascular are candidates. Institution names are not
collection-wide licenses. Excluded: every other collection, nonvascular plants,
fungi, algae, lichens, authenticated/restricted exports, private localities,
images/thumbnails, OCR, media descriptions and image-derived coordinates, website
prose/code, and any incompatible or ambiguous record-level rights.
Media extension presence never authorizes dereferencing media URLs.

## Evidence authority

Started from committed main `a7e6b223cc91663ac6c9462321426a53667cbfce`.
The immutable viability reference is commit
`35624ddbaf93670b839333ae51d7ef15e1f5a85e`,
`conductor/research/pnw-herbaria-source-viability-20260910.md`; it was read with
`git show`, not edited. This investigation refines its previously unknown
institutional UBC version route; it does not rewrite that historical note.

Governing precedents read: [layer outcomes](../../../../docs/layer-lane-standard.md),
[current Parquet lattice](../../../code_styleguides/layer-lanes.md),
[release governance](../../../release-governance.md), and
[soil admission preparation](../../environmental_postgres_retirement_20260904/evidence/soil-static-admission-preparation-20260910.md).
Their relevant rules are complete versus partial, measured bytes versus advertised
metadata, source watermark versus polling time, source custody and rollback,
independent certification, and no runtime database observation fallback.

The [September 12 metadata refresh](metadata-refresh-20260912.md) remeasured the
allowlisted metadata surface without requesting an archive. It did not close a
pre-acquisition gate: WTU still has no standalone release-bound EML or field map,
and UBC still has no published coordinate-withholding policy applicable to the
institutional IPT distribution. Acquisition therefore stopped before transfer.

Sources were captured under `.omc/research/` before bounded scrt filtering.
See [source register](source-register.md) and [HTTP custody receipts](../../../../.omc/research/pnw-admission-http-receipts-20260911.json).
Web-tool extracts may be cached and omit table rows. Direct HTML/XML receipts
prove retrieved bytes at the recorded time; they do not prove occurrence bytes.

## WTU vascular decision

| Item | Observed evidence / decision |
| --- | --- |
| Collection identity | Provider code WTU; dataset Vascular Plants; proposed PlantGeo key `pnw:WTU:vascular` is local, not a publisher GUID. Archive `collectionID` and native occurrence identifiers unmeasured. |
| Publisher/custody | University of Washington Burke Museum Herbarium; portal provider lists its institutional site and portal export. The institution routes database access to CPNWH. |
| Candidate route | [WTU portal DwCA](https://www.pnwherbaria.org/data/getdataset.php?File=WTU_Vascular_DwCA.zip). Link discovered, not fetched or HEAD-probed. |
| Portal inventory | 267,636 public specimens; 52.9 MB DwCA; updated 2026-06-04. Provider page separately lists collection size 268,851, indexed 268,342, data update 2026-06-04 11:08:16 and metadata update 2026-06-04. Timezone unspecified. |
| Rights | Provider vascular section states specimen facts public domain/CC0, with separate NC-SA image terms. Compatible candidate label, not exact-release EML admission. |
| Attribution | Preserve provider's collection citation, UW/Burke Museum and WTU identity, portal attribution, source URL, access date, exact future release/hash and transformation notice. Acknowledgment is encouraged by portal terms and required by PlantGeo provenance policy. |
| Exact release | Mutable filename; no immutable version, archived release retention or native-ID stability guarantee established. HTML last-modified headers cannot fill this gap. |
| Decision | BLOCKED: exact EML/field mapping unavailable outside the unrequested archive, release binding unresolved, cross-release identity and archive safety unmeasured. |

The 706 difference between indexed and public inventory counts is arithmetic,
not a proven count of withheld records. The 1,215 difference from stated collection
size is likewise unreconciled. These are different published populations;
do not explain every difference as sensitivity or deletion.

## UBC vascular decision

| Item | Observed evidence / decision |
| --- | --- |
| Collection identity | Provider UBC, Vascular Plants; IPT resource `ubc-vascular-specimens`; dataset UUID `07fd0d79-4883-435f-bba1-58fef110cd13`; EML collection and parent identifiers UBC. Proposed local key `pnw:UBC:vascular`. |
| Portal route | [UBC portal DwCA](https://www.pnwherbaria.org/data/getdataset.php?File=UBC_Vascular_DwCA.zip): advertised 191,189 public specimens, 27 MB, 2026-06-04. Provider collection/index counts are both 191,189; update 2026-06-04 09:49:01, timezone unspecified. |
| Institutional route | Provider points to Canadensys; prefer [version 16.43 resource](https://data.canadensys.net/ipt/resource?r=ubc-vascular-specimens&v=16.43) for a future pilot, conditional on resolving this route's public-coordinate contract. Do not combine it with the older portal copy. |
| Version evidence | Fresh IPT HTML advertises 16.43, 2026-09-01 12:00:36, 192,948 rows / 31 MB; lists 16.42, 2026-07-29 20:08:30, 192,783 rows. Table timezones unspecified. Version rows show a publisher sequence, not proof of complete or immutable bytes. |
| EML identity | Standalone versioned EML retrieved at 2026-09-11T22:59:23.024297+00:00; 14,208 bytes; packageId `07fd0d79-4883-435f-bba1-58fef110cd13/v16.43`; pubDate 2026-09-01. |
| EML SHA-256 | `e73735e4eafcb1a233514948f72631ceced3dd22e30445d45f34e593f7293654` |
| EML rights | CC0 1.0 / Creative Commons Zero v1.0 Universal; expressly includes commercial copying, modification and redistribution. UBC is named publisher/rightsholder on IPT. This verifies metadata rights for that version, pending archive agreement and record-level review. |
| Attribution | Institutional dataset citation is University of British Columbia Herbarium (UBC), DOI `10.5886/rtt57cc9`, consulted date; add version, hash and PlantGeo transformations. Portal's UBC citation field was blank; do not invent a publisher citation there. |
| Missing policy | No withholding/generalization/coordinate policy was found in the captured EML. Portal public-export suppression cannot automatically be transferred to the institution's broader distribution. |
| Decision | BLOCKED before acquisition: confirm institutional public-coordinate/sensitivity scope and policy applicability. Then quarantine/archive/schema and two-release native-ID proof remain required. |

The versioned [archive link](https://data.canadensys.net/ipt/archive.do?r=ubc-vascular-specimens&v=16.43)
was extracted from publisher HTML without requesting it.
The [EML bytes](../../../../.omc/research/pnw-ubc-eml-16.43-20260911.xml)
and [EML HTTP receipt](../../../../.omc/research/pnw-admission-eml-http-receipt-20260911.json)
are retained. Last-Modified is 2026-09-01 12:00:01 GMT; ETag was absent.
Neither header replaces package identity or archive SHA.
The EML's maintenance frequency is literally `unkown`; no polling cadence is
claimed. Geographic metadata describes worldwide holdings; it does not measure
a PlantGeo envelope. No envelope has been selected or profiled here.

## Rights and access verdict

The portal usage policy allows basic specimen facts, asks users to respect
sensitivity restrictions and acknowledge providers/NSF, and disclaims accuracy.
Its sharing policy governs providers; it is not an agreement PlantGeo has signed.
The download page says use requires agreement to the usage policy. No clickthrough,
login or acceptance occurred.

No demonstrated requirement for a signed agreement for these public CC0 facts
was established by this investigation. Physical specimen loans and separate
image-use policies cannot be silently applied to these data. Conversely CC0
cannot establish whether all content in a future archive is public, whether
additional terms conflict, or whether restricted localities are safe to serve.
[Draft requests and decision checklist](requests-and-handoff.md) are ready for
operator review if clarification is required. They have not been sent.

## Actual schema evidence versus required profile

Portal documentation describes `occurrence.txt`, `identifications.txt`,
`multimedia.txt`, `meta.xml` and `eml.xml`; archive-local `id` joins
extension `coreid`. It documents UTF-8, tab fields and CRLF records. Those are
portal format claims, not inspected members, and do not certify the IPT schema.
UBC standalone EML supplies title, creators/contact, pubDate, language, abstract,
rights/license, distribution, geographic/taxonomic coverage, maintenance,
citation and collection identifiers. It supplies no verified occurrence field
positions, population profile or archive `meta.xml`.

Required inspection matrix, proposed rather than observed:

| Group | Exact fields to bind from meta.xml / EML | Gate |
| --- | --- | --- |
| Resource custody | EML packageId, title, creator/publisher, contact, pubDate, intellectualRights/licensed, distribution URLs, citation, coverage, maintenance; exact XML namespaces | Missing/incompatible EML quarantines the release; disable DTD/entity/network resolution. |
| Core identity | meta.xml core id index; occurrenceID, institutionID/Code, collectionID/Code, datasetID, catalogNumber, basisOfRecord | Profile missing/duplicate IDs; reject invented keys from row position, names, coordinates or hashes. |
| Extensions | rowType, files/location, coreid index; identificationID, identifiedBy, dateIdentified, identificationQualifier, typeStatus where supplied | All joins reconciled; native extension identity distinguished from archive-local row identity. |
| Taxonomy | scientificName, scientificNameAuthorship, taxonID, acceptedNameUsageID, taxonRank, family, genus | Preserve unresolved and infraspecific taxa; authority mappings require pinned version. |
| Event evidence | eventDate, verbatimEventDate, year/month/day, recordedBy, recordNumber, modified | Keep intervals/unknowns; collection date is not publication time. |
| Location | decimalLatitude/Longitude, geodeticDatum, coordinateUncertaintyInMeters, coordinatePrecision, country/stateProvince/county/locality, verbatim coordinates | Profile missing datum/uncertainty; never use administrative centroids as specimen points. |
| Rights/sensitivity | license, rightsHolder, accessRights, informationWithheld, dataGeneralizations, occurrenceRemarks and free-text locality risk | Preserve declared generalization; no reverse geocoding or cross-source recovery of private locations. |
| Media | multimedia row count/schema and license fields only | No image bytes, URLs in serving output, OCR or media enrichment admitted. |

Required population receipt must partition core rows into accepted spatial,
accepted nonspatial, out-of-envelope, rights-excluded, duplicate-native and
quarantined outcomes with explicit precedence so counts add to total.
Withheld/generalized/taxon-unresolved flags are overlapping secondary counts,
not additional disjoint rows. Reconcile extensions separately. All counts,
field population rates, geography fractions, member names and archive hashes
are currently **unmeasured**, never zero.

## Release, correction and withdrawal contract

A future release receipt binds distributor, collection, publisher version and
publication evidence, complete-public-population scope, archive SHA/bytes,
ordered member SHA/bytes/CRC, EML/meta hashes, parser recipe/version, policy
snapshots, HTTP status/redirects/validators/retrieval time and reviewer decision.
Content SHA is byte identity; a source occurrence key is biological-record
identity; neither substitutes for the other. Current archive_sha256 is null
for both candidates.

Compare two equivalent complete releases from the same distribution, rights and
population contract. Verify native-ID continuity, reuse, missing/duplicate
keys, annotation membership and changed records. UBC v16.42 is a candidate
comparison, not an acquired release. WTU's prior complete release remains unknown.
No partial export, timeout, schema failure or change in rights filters produces
source-deletion tombstones. Removed rows are candidate withdrawals only after
complete-equivalent reconciliation or explicit publisher correction.

Corrections create a new immutable revision. A rights or sensitivity complaint
immediately blocks the affected future serving generation and its derived
summaries; preserve only permitted restricted audit evidence, according to a
reviewed retention decision. Notify the operator for source clarification rather
than contacting the provider automatically. Rebuild dependent taxon/spatial
artifacts and caches, verify suppression, then promote a reviewed generation.
Rollback may restore only a still-authorized generation; never resurrect a
withdrawn locality through old pointers, caches or downloads.

## Archive custody and quarantine protocol

No corpus bytes exist in this packet. A future acquisition must pass a preflight
rights/sensitivity review before opening an allowlisted URL. HTTP redirects are
revalidated against the collection/distributor allowlist; no login or alternate
unrestricted endpoint fallback. Stage in access-restricted quarantine, outside
Git and public buckets. Never execute members or follow embedded URLs.

Inherited per-pilot limits: at most two allowlisted archives, one transfer at a
time, 64 MiB compressed each, 128 MiB total, 2 GiB decompressed total, 32 members,
600,000 core rows, two million extension rows, eight HTTP attempts including
redirects/retries, 30 seconds per request and ten minutes wall time.
Apply byte/time limits while streaming and decompressing, not after extraction.
A two-release UBC comparison uses both archive slots and defers WTU acquisition;
do not quietly add a third archive. Publisher MB labels are not measured MiB.

Reject absolute paths, drive/UNC paths, traversal including backslash variants,
symlinks, duplicate normalized/case-colliding member names, encrypted or nested
archives, size/ratio anomalies, CRC/hash mismatch and unexpected executable or
schema content. Hash safely streamed members; inspect XML without external
entities. Unsupported schema or missing rights keeps the whole release quarantined.
A bounded sample of at most 25,000 records/collection may aid diagnosis, but cannot
certify completeness, sensitivity or stable IDs. Measure peak memory, elapsed
time, retries, compressed/decompressed bytes and all rows before admission.
Overruns stop as deferred/failed; there is no automatic ceiling increase.

Quarantine objects require a named custodian, immutable manifest, permitted
retention/access policy and deletion/withdrawal record before collection.
The repository contains only metadata captures and evidence receipts.
