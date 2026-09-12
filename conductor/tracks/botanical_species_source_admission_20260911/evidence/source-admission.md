---
type: source-admission-evidence
slug: botanical_species_source_admission_20260911
status: source-admission-pending
---

# Non-Herbaria source-admission candidate

The continuation after host shutdown is local only. This preserved source
inventory and four-taxon fixture remain **unaccepted**. Production authoring
census and independent source admission are pending; the local code/API/agent
review does not authorize source ingestion, release publication or deployment.

The WCVP archive and its field definitions are verified for a bounded local
candidate. Independent botanical/data-governance admission and downstream
publication approval remain pending. This receipt records a source download and
inventory, not a database ingestion or production release.

## Exact WCVP source

The original 18,112-byte `README_WCVP.xlsx` member is preserved byte-for-byte as
[`README_WCVP-v16.xlsx`](README_WCVP-v16.xlsx). Its SHA-256 is
`b4741be05ea3937b3604589602e1e24bcd3a0f558dcf8e06797eaa28b5207eaa`.
This small committed artifact retains the archive-specific release, field and
reuse evidence even if the provider's mutable download URL changes later.

| Property | Verified value |
| --- | --- |
| Provider | Royal Botanic Gardens, Kew |
| Product | World Checklist of Vascular Plants (WCVP) |
| Archive URL | https://sftp.kew.org/pub/data-repositories/WCVP/wcvp.zip |
| Official directory | https://sftp.kew.org/pub/data-repositories/WCVP/ |
| Source version | 16, from `README!A9` |
| Source extraction date | 2026-06-04, from `README!A10` |
| Retrieved | 2026-09-11; HTTP response Date 23:17:41 GMT |
| Last-Modified | Thu, 04 Jun 2026 14:30:32 GMT |
| ETag | `"54183c1-6536e62f7dc69"` |
| Bytes | 88,179,649 |
| Archive SHA-256 | `d32ea2b3a85e489b14e83bcc9eae7274532e1d113753f7be290d4b2dfde573fa` |
| Candidate source release ID | `kew-wcvp-16-2026-06-04` |
| Licence | CC-BY-3.0, declared in `README!A5:A6` |
| Citation DOI as written in README | `https://doi.org/10.34885/egs6-cp24` |

The URL is mutable. The version, extraction date and SHA-256 together identify
the captured release; an HTTP timestamp or ETag alone is not the content pin.
The DOI string above is preserved verbatim from `README!A4` and has not been used
as a substitute download identity or independently checked for resolution.

The source citation is: Govaerts R (ed.). 2026. *WCVP: World Checklist of Vascular
Plants*. Facilitated by the Royal Botanic Gardens, Kew. Version 16, extracted
2026-06-04. Retain that attribution, the archive URL, the licence link, the exact
source version/hash and an explanation of the bounded extraction/normalization
in derivative profiles. [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/)
permits redistribution and adaptation, including commercial use, with attribution
and the applicable licence conditions. Do not imply Kew endorsement.

The licence belongs to this archive. It must not be replaced with a licence
reported for a GBIF derivative or a different WCVP release. The public
[POWO citation page](https://powo.science.kew.org/cite-us) still identifies version
13 as its example; the downloaded README is the release-specific authority here.

| Member | Bytes | SHA-256 |
| --- | ---: | --- |
| `README_WCVP.xlsx` | 18,112 | `b4741be05ea3937b3604589602e1e24bcd3a0f558dcf8e06797eaa28b5207eaa` |
| `wcvp_names.csv` | 300,826,661 | `b77396d5b71b777ef188095f48c96702cb1aaf77bd70b9fceefa0a46a71275e6` |
| `wcvp_distribution.csv` | 141,657,561 | `b217edce02999f4cfd4a2921d63fb90c0045d5862d210a86b93af567c223e94a` |

The CSVs use UTF-8, `|` delimiters and no quote interpretation, as specified by
`README!D12:D13`. The names CSV has **1,448,984 data rows and 31 columns**; the
distribution CSV has **1,995,338 data rows and 11 columns**. Both row counts were
checked against the bytes and match the README. Distribution records were
inventoried but are not part of the initial profile candidate.

## Field admission and identity

The complete machine-readable headers and raw selected source values are in
`source-admission.json`. The canonical identity triple is authority `WCVP`,
authority version `16`, and taxon ID equal to the string `plant_name_id`. The
source release label `kew-wcvp-16-2026-06-04` records the provider and extraction
date separately. IPNI and POWO IDs remain external identifiers; neither replaces
the versioned WCVP concept.

| Source fields | Candidate use and limit | README evidence |
| --- | --- | --- |
| `plant_name_id`, `accepted_plant_name_id` | Canonical concept and source-declared synonym links. Accepted rows point to themselves. Empty accepted IDs remain unresolved. | `A16:D16`, `A39:D39` |
| `taxon_status`, `taxon_rank`, authorship, hybrid/infraspecific fields, `parent_plant_name_id`, `basionym_plant_name_id` | Preserve source nomenclature and concept boundaries. Never collapse an accepted subspecies or cultivar into the parent by name. | `A18:D34`, `A37:D44` |
| `lifeform_description` | Categorical lifeform summary under the modified Raunkiaer vocabulary. Preserve the complete raw phrase. Missing is unknown. It does not measure height, biomass, fuel architecture or rooting. | `A35:E35` |
| `climate_description` | Categorical habitat summary derived from published habitat information. Preserve the raw category. It does not define temperature, precipitation, hardiness or establishment tolerances. | `A36:D36` |
| `reviewed` | Family-level peer-review flag only. Keep source review separate from local per-value approval and release review. | `A46:D46` |
| `geographic_area`, distribution member | Preserve if needed as authority context, but no occurrence, local native-status, suitability or effect inference is admitted. | `A34:D34`, distribution definitions |

The source status vocabulary includes Accepted, Synonym, Invalid, Illegitimate,
Unplaced, Artificial Hybrid, Misapplied, Orthographic and Local Biotype. README
also describes Provisionally Accepted for the Darwin Core archive variant only;
that status is absent from this names CSV. Unplaced, illegitimate and certain
nonvascular accepted concepts can lack an accepted ID. Such rows cannot become a
silent canonical match.

The source's family-review totals are 641,096 `Y`, 807,778 `N` and 110 blank
name records. These are source taxonomy metadata, not counts of reviewed trait
measurements. Cultivars are not a dedicated field in this archive; no cultivar
identity or cultivar-specific growth/fuel claim is synthesized.

## Bounded demonstrator

The requested names were used only to discover records inside this one frozen
authority. The returned concept IDs below are the input for deterministic
extraction. There is no cross-source join by normalized name text.

| Accepted name | WCVP ID | IPNI / POWO ID | Family reviewed | Lifeform | Climate | Candidate decision |
| --- | --- | --- | --- | --- | --- | --- |
| Pseudotsuga menziesii (Mirb.) Franco | `379633` | `677141-1` | Y | tree | temperate | Include |
| Pinus ponderosa Douglas ex C.Lawson | `380358` | `307165-2` | Y | tree | temperate | Include |
| Alnus rubra Bong. | `6584` | `294986-1` | Y | tree | temperate | Include |
| Trifolium repens L. | `2439997` | `523626-1` | Y | perennial | temperate | Include |
| Achillea millefolium L. | `2908230` | `2294-2` | N | perennial | temperate | Defer: family unreviewed |

The four-taxon candidate has **four accepted rows, six directly linked synonym
rows, four nonmissing lifeform summaries and four nonmissing habitat summaries**.
The six synonym concept IDs are `2634091` (to `2439997`), `386877`, `6550`, `6586`,
`6587` (to `6584`), and `381882` (to `379633`). No directly linked synonym row was
found for the selected Pinus ponderosa species concept. This does not assert
that the species has no historical names: names linked to accepted subordinate
concepts remain with those concepts. The deferred Achillea concept has six
linked nonaccepted names, including three with status Illegitimate; they remain
excluded from this candidate.

No admitted source value supplies quantitative growth requirements,
agricultural roles, companion effects, tissue water, live/dead fuel moisture,
leaf dry matter, oils, resins, extractives, heat content, ash/minerals, curing,
litter, fuel-bed behavior, fire tolerance, fire response or post-fire recovery.
These are explicit evidence gaps. Family membership does not establish nitrogen
fixation, and the categorical word `tree` does not establish fuel architecture.

## Deferred sources

**USDA PLANTS:** The official
[downloads page](https://plants.sc.egov.usda.gov/downloads) describes a complete
checklist containing symbols, names and family; that checklist is not a growth
requirements release. The official
[help document](https://plants.sc.egov.usda.gov/DocumentLibrary/Pdf/PLANTS_Help_Document.pdf)
states that plant lists, text and distribution information may be reused without
copyright restriction and asks for PLANTS attribution. Images have separate
conditions and are excluded. Public app source exposes dynamic characteristics
handlers, but no exact, release-identified bulk growth payload, source checksum,
field-unit contract or authoritative WCVP crosswalk was admitted. No USDA plant
data were downloaded or ingested. A future admission must verify those missing
items before using growth requirements.

**TRY:** The official
[request instructions](https://www.try-db.org/TryWeb/Prop0.php) distinguish public
CC BY records from temporarily restricted records requiring owner permission.
The request process notifies contributors. No request, registration or agreement
was submitted. Public per-record enrichment remains optional and unadmitted
until its exact record rights, source version, download identity and measurement
context are captured. Restricted or embargoed records remain blocked.

**FEIS:** The official
[about page](https://research.fs.usda.gov/feis/about) describes literature
syntheses and site-specific fire studies. It does not establish a reusable,
release-pinned trait table for this candidate. No fuel values were extracted.
Any later assertion must cite its individual review and underlying per-value
evidence, and preserve tissue/component, live/dead state, moisture basis, units,
method, season, life stage, geography and environmental context. Fire response
or post-fire recovery cannot substitute for fuel chemistry or flammability.

## Handoff and remaining gates

The immutable local source bytes are under
`local-artifacts/botanical-species-profile/source-admission/`; raw web/doc captures
are under `.omc/research/`. Both locations are ignored by Git. The committed JSON
receipt pins exact hashes, fields, IDs, source semantics and capture hashes so an
integrator can verify a later re-download without depending on a local path.
`wcvp-16-source-release.json` is the profile contract's `SourceRelease` descriptor;
its reviewer is the source-admission author, and its review basis explicitly
defers independent acceptance. Its terms hash identifies the exact Excel README.

No database write was performed. No unavailable census was represented as empty.
The profile owner must retain release and per-value attribution, raw values,
categorical evidence classes, explicit unknown/refusal states, synonym concept
identity and the separation between source family review and local approval.
The independent reviewer must examine this candidate after integration; this
receipt is not an independent verdict. USDA, TRY, FEIS, quantitative growth and
fuel enrichment remain unadmitted. Production publication/deployment remain
separate gates.
