# Ecological Knowledge and Seed Source Register

**Status:** researched seed plan. This register authorizes source ingestion and draft lookup creation; it does not authorize field rates, planting prescriptions, or claims of measured benefit.

## Evidence policy

PlantGeo stores claims, not just documents. Every strategy, amendment, plant trait, planting instruction, or companion relationship must resolve to a versioned claim with a stable source, jurisdiction, applicability context, and review state. A source can support a definition without supporting a numeric threshold or an outcome claim.

Use these evidence tiers:

| Tier | Evidence | Allowed use |
| --- | --- | --- |
| `regulatory` | Current statute, regulation, or regulator-maintained limit | Eligibility/prohibition within its jurisdiction and effective dates. |
| `standard_local` | Current state/local NRCS Field Office Technical Guide, land-grant recommendation, or equivalent adopted standard | Site/crop-specific rule only when method, units, jurisdiction, and version match. |
| `standard_national` | National NRCS Conservation Practice Standard or federal technical guide | Practice definition, purposes, minimum planning concepts, and a pointer to required local review; never direct design/installation. |
| `peer_reviewed` | Primary study or systematic review with identifiable treatment, comparator, context, and outcome | Context-bounded effect/support claim; record design and uncertainty. |
| `curated_technical` | Government or land-grant technical guide with references | Trait, establishment, or feasibility guidance with visible limitations. |
| `traditional_or_anecdotal` | Cultural knowledge, practitioner report, or unsourced list | Search/discovery context only unless governance and community attribution approve another use; no efficacy claim. |

Conflicting claims remain separate. Publication policy selects or summarizes them; ingestion never overwrites disagreement. `review_status` progresses through `draft`, `source_verified`, `domain_reviewed`, `approved`, `withdrawn`, and `superseded`.

## Lookup-table contract

| Table | Minimum fields |
| --- | --- |
| `evidence_source` | Publisher, title, canonical URL/document ID, source type, version/issue/effective/retrieval dates, content checksum, jurisdiction, license/redistribution/training permissions, evidence tier, review state. |
| `evidence_claim` | Source/page/section locator, claim type, subject/object IDs, mechanism/outcome, qualitative or quantitative value, units and method, geography/system/crop/soil constraints, uncertainty, reviewer, status. |
| `strategy_guide` | Practice code/version, purpose, system, resource concern, applicability/exclusions, required companion practices, local-standard requirement, claim IDs. |
| `amendment_catalog` | Material identity/class, composition/analysis fields, contaminants/salts/pathogen considerations, regulated statuses, supplier-lot test requirement; no default application rate. |
| `amendment_rule` | Plant/crop/system, soil/tissue test analyte, lab method, original and normalized units, threshold/range, purpose, contraindications, jurisdiction, effective date, evidence claim, human-review requirement. |
| `plant_taxon` | Stable taxon/source ID, accepted scientific name, synonyms/common names, rank, native/distribution status by jurisdiction, source release. |
| `plant_trait_observation` | Taxon, trait, value/range/unit/method, life stage/cultivar, geography, evidence claim. Conflicting observations are retained. |
| `planting_instruction` | Taxon/seed lot or plant material, purpose, region/seed zone, site preparation, timing, depth/rate/spacing with units, dormancy/pretreatment, establishment/maintenance, source and review status. |
| `companion_relationship` | Taxon A/B, direction, mechanism (`trap_crop`, `beneficial_insect_habitat`, `shade`, `living_mulch`, `physical_support`, `nutrient_sequence`, `competition`, etc.), outcome and measured endpoint, crop stage, spacing, geography, evidence claim, support/conflict state. |
| `regulatory_plant_status` | Taxon, authority, jurisdiction, status, prohibited action, permit reference, effective/review dates. |
| `seed_zone_crosswalk` | Zone system/version, geometry, applicable taxa/material type, transfer rule, climate variables, uncertainty, source. |

The existing two-value `companion/antagonist` representation is too coarse for production. A plant may suppress one pest, also suppress a beneficial insect, and compete with the crop for water. Store those as separate directional claims rather than collapsing them into one verdict.

## Approved source families for the first seed release

| Source family | Seedable findings | Required guardrail |
| --- | --- | --- |
| [NRCS Conservation Practice Standards](https://www.nrcs.usda.gov/resources/guides-and-instructions/conservation-practice-standards) | Practice code/name, national definition, purpose, resource concerns, minimum criteria categories, supporting-document links. | National standards are baselines and explicitly are not sufficient to plan, design, or install; resolve the current state FOTG before an implementation rule is approved. |
| [NRCS Soil Carbon Amendment 336](https://www.nrcs.usda.gov/resources/guides-and-instructions/soil-carbon-amendment-ac-336-conservation-practice-standard) | Soil-carbon amendment practice identity, purposes, national planning preconditions, supporting artifacts. | Do not translate a national overview into a universal biochar/compost rate. |
| [NRCS Nutrient Management 590](https://www.nrcs.usda.gov/resources/guides-and-instructions/nutrient-management-ac-590-conservation-practice-standard) | Nutrient-management identity, state-standard links, source/rate/method/timing concepts, required site-specific planning. | A rate requires current soil/plant/material tests, crop need/yield goal, approved risk assessment, local land-grant guidance, method/units, and jurisdiction. |
| [NRCS Silvopasture 381](https://www.nrcs.usda.gov/resources/guides-and-instructions/silvopasture-ac-381-conservation-practice-standard) | Definition, intended purposes, practice code/version, supporting physical-effects documents. | Represent feasibility and management prerequisites; do not seed universal climate/slope/impact scores. |
| [NRCS Alley Cropping](https://www.nrcs.usda.gov/conservation-basics/land/forests/agroforestry-systems/alley-cropping) and practice 311 | System definition, intended purposes, design-element categories, related practices, general tree/shrub selection considerations. | Site layout and species choice remain local design work. |
| [NRCS Tree/Shrub Establishment 612](https://www.nrcs.usda.gov/sites/default/files/2022-10/Tree-Shrub-Establishment-612-CPS-May-2016.pdf) and [Critical Area Planting 342](https://www.nrcs.usda.gov/sites/default/files/2022-09/Critical_Area_Planting_342_CPS.pdf) | Establishment workflow fields, adaptation checks, timing/specification requirements, stabilization and maintenance concepts. | Planting rates, dates, spacing, and amendments come from current local guides and reviewed plans. |
| [USDA PLANTS help and data semantics](https://plants.usda.gov/assets/docs/PLANTS_Help_Document_2022.pdf) | Taxonomy/source IDs, names, distribution/native-status semantics, available trait definitions, links to plant guides. | Nationwide characteristic values are screens, not site guarantees; preserve source date and nulls. |
| [NRCS Plant Materials technical documents](https://www.nrcs.usda.gov/plant-materials/publications/search) | Species- and region-specific plant guides, propagation protocols, seed quality/PLS methods, planting trials and dates. | Each instruction is tied to the exact document, region, material/cultivar and release year; do not merge all plant guides into a national rate. |
| [USFS Seed Zone WebMap](https://research.fs.usda.gov/pnw/products/dataandtools/seed-zone-webmap) | Provisional, climate-matched, and empirical seed-zone geometries and transfer categories. | WebMap grass/forb/shrub layers do not apply to trees; prefer species-specific empirical zones when available. |
| [2023 USDA Plant Hardiness Zone Map](https://planthardiness.ars.usda.gov/) | Versioned extreme-minimum-temperature zone and source geometry/license metadata. | Hardiness is only a 30-year average annual extreme minimum temperature screen; it does not encode heat, water, soil, microclimate, or future survival. Follow USDA/OSU map attribution and alteration conditions. |
| [APHIS Federal Noxious Weeds](https://www.aphis.usda.gov/organism-soil-imports/federal-noxious-weeds) and [NISIC species lists](https://www.invasivespeciesinfo.gov/subject/lists) | Federal regulatory status, authority, permit/restriction references, links to state authorities. | State rules change independently. A national “not listed” result is not proof a plant is legal or non-invasive locally. |
| [EPA compost guidance](https://www.epa.gov/sustainable-management-food/composting), [biosolids land application](https://www.epa.gov/biosolids/land-application-biosolids), and Part 503 materials | Amendment classes, definitions, pathogen/pollutant regulatory preconditions and regulated-use context. | Keep fertilizer, compost, manure and biosolids distinct. Resolve state/local rules; biosolids are prohibited in USDA organic production. Do not infer suitability for food gardens from generic remediation guidance. |
| [WSU evidence-based companion planting](https://pubs.extension.wsu.edu/product/gardening-with-companion-plants-home-garden-series/) | Mechanism taxonomy, cited examples, cautions, and contradictory/negative effects. | Do not seed popular pair charts. The guide itself notes insufficient evidence for several familiar productivity/soil claims. |
| [University of Minnesota companion planting review](https://extension.umn.edu/gardening-minnesota/companion-planting-home-gardens) | Research-linked home-garden examples, trap-crop/diversity mechanisms, citations to primary studies. | Seed individual evidence claims with their cited study/context, not the page as a blanket endorsement. |

## Findings that change the model design

1. **Locality is part of the key.** NRCS says state FOTG standards adapt national criteria to local soil, climate, topography, and regulation. `jurisdiction_id` and `standard_version` therefore belong in amendment and strategy rule uniqueness constraints.
2. **A soil map is not a prescription.** Nutrient rates require current test results, crop need, source analysis, risk assessment, and local guidance. SoilGrids/SSURGO can screen where sampling is important; they cannot create an application rate.
3. **Companion planting is mechanism-specific and sometimes adverse.** Evidence supports selected trap-crop, habitat, shade, diversity, or living-mulch mechanisms, while many popular pair charts lack support. The recommender must expose endpoint, context, benefit and possible cost separately.
4. **Hardiness is one climate variable.** The USDA hardiness zone cannot substitute for heat, moisture, frost timing, salinity, soil, or microclimate limitations. It is one feature with a version and spatial resolution.
5. **Seed movement has genetic geography.** Restoration seed recommendations require a seed-zone/transfer policy and provenance. Provisional seed zones are a fallback, and their taxonomic scope matters.
6. **“Exotic” requires a regulatory workflow.** Check federal import/movement rules and current state/local invasive or noxious lists. A non-native plant never receives a general green light from a single national list.

## First seed release

Keep the first release small enough for line-by-line review:

- practice definitions for NRCS 336, 381, 311, 612, 342, 590, 340 (Cover Crop), 391 (Riparian Forest Buffer), 383 (Fuel Break), and 394 (Firebreak);
- source and jurisdiction records for the federal standards plus one pilot state's current FOTG/LGU recommendations;
- 20–30 common garden crop taxa, 20 locally native restoration taxa, and 10 reviewed forage/tree/shrub taxa for silvopasture/agroforestry;
- plant traits and planting instructions only when a source provides the exact field, method, geography and material;
- a handful of mechanism-specific companion claims with primary citations, including at least one null/contradictory example to prove the UI handles uncertainty;
- amendment catalog identities and safety preconditions for lime, gypsum, mature compost, biochar, manure and biosolids, but no default rate.

The former `STRATEGY_SEEDS` climate, slope, labor, impact, and time-to-yield values had no attached citations or jurisdiction and have been removed. The replacement seed release contains four definition-only USDA NRCS practice identities as drafts; every unsupported suitability, effort, timing, and impact field is `NULL` and cannot enter training, ranking, or public recommendations.

## Ingestion and review workflow

1. Capture the source document and checksum; record version, jurisdiction, rights and retrieval time.
2. Extract claims into staging with source locators and original units. Automated extraction cannot approve a claim.
3. Normalize taxonomy, units and geography without discarding the original value or wording.
4. Run schema, range, duplicate, contradiction, current-version, and regulatory-status checks.
5. Require agronomy/ecology review for quantitative or action-bearing claims and legal/governance review for regulated materials or restricted plants.
6. Publish an immutable lookup release. Models and recommendations pin its release ID.
7. On source change, create a new claim/release, compare it with the prior version, and explicitly supersede or withdraw affected rules and outputs.
