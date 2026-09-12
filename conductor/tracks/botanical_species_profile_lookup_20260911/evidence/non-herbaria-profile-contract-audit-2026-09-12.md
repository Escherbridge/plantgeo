---
type: evidence-receipt
track: botanical_species_profile_lookup_20260911
date: 2026-09-12
status: audited-release-blocked
source_commit: 6c08907ba392d8dcdff3e96c41fdb27c6a74fe9d
source_tree: 92f3baef253389b737f3e36fd47a96b52c43c312
continuation_base_commit: 64f4f892bd2b744cc097c7f76a1f239997b80f52
continuation_base_tree: f8697da694a3f9fbbf28e70ff7581bd59546bfd6
---

# Non-Herbaria botanical profile contract audit

## Verdict and boundary

**P0/P1 verdict: blocked for immutable publication.** The repository has a bounded,
fail-closed transitional lookup over the existing `agri.species` authoring shape, but it
does not contain an admitted non-Herbaria source, a versioned taxon-concept mapping, or
per-assertion provenance sufficient to publish growth requirements, water/oil/tissue
composition, or agricultural-role values as a `botanical-species-profile` release.

This is a repository-only contract audit at base commit
`6c08907ba392d8dcdff3e96c41fdb27c6a74fe9d` and base tree
`92f3baef253389b737f3e36fd47a96b52c43c312`. It did not query local PostgreSQL,
`pgt`, Railway, production, or an object store. It did not acquire an external dataset,
evaluate source contents, ingest, publish, run a writer or scheduler, deploy, rank a
species, or make a planting, fuel, fire, food, timber, pollination, nitrogen-fixation,
or suitability claim. Therefore it makes no claim about production rows or about the
fitness of any candidate source.

The continuation audit revalidated the same bounded surfaces at base commit
`64f4f892bd2b744cc097c7f76a1f239997b80f52` and base tree
`f8697da694a3f9fbbf28e70ff7581bd59546bfd6`. The authoring table/model,
transitional plane and HTTP adapter, application registration, agent tool and prompt, MCP
instructions, and recommendation-model trait vocabulary are byte-identical between the
original and continuation bases. The intervening repository changes do not change this
receipt's admission verdict. The continuation likewise performed no database or external
read and makes no population claim.

## Repository inventory

### Existing authoring shape

`services/agri-data-service/db/agri/tables/species.sql` and
`services/agri-data-service/src/agri_data_service/models/species.py` expose one wide row
per internal Species UUID:

| Current field | Stored shape | What is known | Release-blocking ambiguity |
| --- | --- | --- | --- |
| `id` | UUID | Exact internal row identifier | Not an authority/version/taxon-concept identifier; it cannot support an occurrence-to-profile join by itself. |
| `scientific_name` | required unique text | One name string | Name uniqueness is not concept identity and cannot encode synonyms, taxonomic splits/merges, authority, or release version. |
| `common_name`, `usda_symbol`, `family` | text | Identity labels | No source-record identifier, vocabulary version, licence, or reviewed taxon mapping. |
| `growth_habit` | nullable text | Unverified authoring label | No controlled term identifier, source field, evidence class, method, geography, life stage, or conflict record. |
| `native_status` | nullable text | Unverified authoring label | Native/introduced status is jurisdiction- and time-dependent; neither scope is stored. |
| `usda_zones` | nullable integer range | A PostgreSQL range | Authority/release and inclusive/exclusive semantics are not published as assertion metadata. A zone is not a measured temperature requirement. |
| `min_precip_mm`, `max_precip_mm` | nullable floats | Values named in millimetres | Period, statistic, establishment versus persistence meaning, geography, method and source unit/conversion are absent. |
| `min_ph`, `max_ph` | nullable floats | Unitless pH bounds | Measurement medium/extractant, soil horizon, method and evidentiary meaning are absent. |
| `light_requirement` | nullable text | Unverified authoring label | No irradiance/photoperiod basis, controlled vocabulary, life stage, method or context. |
| `drought_tolerance`, `salt_tolerance` | nullable text | Unverified authoring labels | No tested stressor, exposure duration, salinity measure, response endpoint, scale, life stage or method. A tolerance label is not an outcome effect. |
| `nitrogen_fixer` | required Boolean, application default `false` | Legacy role flag | `false` collapses evidence-backed absence, unassessed, and defaulted value. No symbiosis/organism, assay, plant part, conditions or review provenance. |
| `pollinator_value` | nullable enum `none/low/medium/high` | A four-label scale | Scale authority, pollinator group, floral resource, season, geography, method and comparator are absent. `none` must not mean unassessed. |
| `edible`, `timber_value` | required Booleans, application default `false` | Legacy use flags | Negative evidence and missingness are indistinguishable. Plant part, use, processing, safety, jurisdiction and evidence are absent. |
| `guild_roles` | nullable text array | Free-form role labels | No term identifiers, assertion identifiers, applicability, source field, review state or per-value provenance. |

The table has timestamps but no taxonomic authority/version, source dataset/release,
source record/field, original value/unit, normalization rule, evidence class, method,
licence, coverage, assertion review, conflict, or field-level missingness columns. Column
presence and row presence are therefore not evidence sufficiency.

`agri.companion_relationships` is better bounded: approved rows require a review timestamp
and citation and can retain a source URL, evidence grade, applicability and jurisdiction.
It still is a separate relationship product, not provenance for the wide Species fields,
and must not be used to infer a species-level agricultural role or deployment outcome.

### Current readers and wiring

- `planes/botanical_species_information.py` accepts one canonical-form internal Species
  UUID, caps companion rows and response bytes, renders legacy values as
  `unverified_authoring`, emits explicit `unknown/not_reported`, leaves the unmodeled
  fuel/tissue section unknown, and refuses ranking, planting, occurrence, suitability,
  objective-effect and fuel/fire conclusions. Database faults return a typed refusal.
- `interface/http/botanical_species_information.py` exposes the transitional route with
  `Cache-Control: no-store`; `app.py` mounts it only for `combined_local` and
  `published_reader`, not `receiver_writer`.
- `agent/tools.py` registers `species_information`, binds it to the caller-supplied exact
  UUID in agent runs, and includes it in the common tool tuple. `agent/mcp_server.py`
  derives MCP descriptors and calls from that same registry. `agent/prompts.py` instructs
  the model to preserve unpublished/unverified/missing states and the claim refusals.
- The transitional route is not the final release-pinned P3 implementation. Its internal
  Species UUID must not be relabelled as a canonical external taxon concept, and its
  database read must not become fallback serving for an absent Parquet release.
- `method/ml/recommendation_models.py` defines feature terms that accept an already-fetched
  `agri.species` mapping, including the three legacy Booleans. That code is inventory
  evidence of a future cutover requirement, not authorization to train from the wide row.
  The current executable recommendation trainer does not pass such a mapping, so this audit
  records a latent interface risk rather than claiming present runtime consumption.
  Before any profile-backed training or ranking is permitted, the caller must be proven to
  supply a pinned, reviewed immutable profile and to preserve unknown/conflict states.

### Planned non-Herbaria sources are not admitted sources

`docs/strategy-deployment-readiness.md` prioritizes USDA PLANTS and NRCS Plant
Materials for identity, regional status, reported requirements and attributed
establishment evidence, and mentions TRY and FEIS only as possible enrichment.
`docs/ecological-knowledge-source-register.md` records useful source-family guardrails,
including source-date/null preservation for USDA PLANTS and exact document, region,
material/cultivar and release-year scope for Plant Materials guidance. Those planning
references contain no captured artifact, field inventory, exact admitted release/hash,
field-level rights verdict, normalization fixtures, coverage audit, or independent
admission decision. They do not admit any value. Public accessibility or a named source
family is not bulk-access, redistribution, API-display or training authorization.

## Separate lightweight lookup decision

**Decision: retain a separate lightweight lookup, with two explicitly non-interchangeable
products.** The existing `botanical-species-information` endpoint remains a transitional,
exact-internal-UUID view of editable authoring rows. The future
`botanical-species-profile` product is a nonspatial, immutable, release-pinned Parquet
lookup containing canonical taxon, assertion, reconciliation-decision, profile and manifest
artifacts. Neither product is a botanical-occurrence layer, an environmental signal lane,
or a recommendation endpoint.

This separation is load-bearing:

- relational `agri.species` remains useful for lightweight curation and the bounded legacy
  bridge, but its wide columns do not acquire source, method, unit, rights or review evidence
  merely because the HTTP, agent and MCP paths can read them;
- the final lookup is keyed by authority/version/taxon-concept plus profile release, not the
  transitional internal Species UUID and never normalized name text;
- profile publication freezes reviewed values and their assertion/decision lineage without
  copying editable defaults into evidence-backed negatives;
- an absent profile release, artifact, taxon or field produces declared missingness or a typed
  refusal, never a read-through to `agri.species`;
- occurrence, establishment comparison and objective-effect evidence remain independent
  downstream inputs, so the lookup cannot rank, recommend planting or manufacture a fuel/fire
  conclusion; and
- serving rights and training rights are separate field-level gates. A serving-eligible value
  is excluded from training unless the exact source field and transformation have an affirmative
  recorded training-use scope.

The transitional endpoint may be retired after the immutable lookup is accepted. It must not
be evolved into the final product by adding a release label around the same wide database row;
that would preserve neither the assertion grain nor immutability.

## Explicit blocker ledger

| Blocker | Repository evidence | Required clearing evidence |
| --- | --- | --- |
| `B1_source_admission` | Planning names USDA PLANTS, NRCS Plant Materials, TRY and FEIS, but the repository has no captured exact non-Herbaria source release, hash, field inventory or independent field verdict. | One bounded packet for an exact release with bulk-access proof, field dictionary, coverage, update/withdrawal behavior and admit/withhold decisions. |
| `B2_rights_and_training_scope` | No candidate field has reviewed redistribution, transformation, API/agent-display and model-training terms recorded separately. | Field-level rights review with licence version, attribution, consumer-specific permission and explicit training-use status/scope. Unknown is withholding, not permission. |
| `B3_taxon_concept_identity` | `agri.species.id` is internal and `scientific_name` is only a unique string; no authority/version/concept mapping exists. | Versioned canonical taxon artifact and exact source/occurrence crosswalk with matched, ambiguous and unmatched states; no name-only contribution. |
| `B4_assertion_and_decision_grains` | The wide species row has no per-value source record/field, original value/unit, method, context, review, conflict or withdrawal record. | Implemented and independently reviewed assertion and reconciliation-decision schemas satisfying the frozen envelope below. |
| `B5_composition_and_fuel_context` | The transitional plane declares the fuel/tissue section `not_modeled_in_authoring_schema`; no admissible water, oil, tissue or fuel measurements exist. | Context-complete assertions retaining tissue/component, live/dead state, basis, unit, method, preparation, statistic, season, life stage, geography and environmental context. |
| `B6_agricultural_role_evidence` | Legacy role Booleans/defaults, the pollinator label and free-form guild array cannot distinguish negative evidence from missingness or retain per-value provenance. Companion rows are a separate bounded relationship product. | Controlled role assertions with polarity, beneficiary/object, plant part where relevant, applicability, jurisdiction, method, source, rights and immutable review fields. |
| `B7_immutable_publication` | The planned canonical-taxonomy, assertion, decision, profile, manifest, publication and final profile-reader paths do not exist; no integration receipt exists. | Hash-bound Parquet artifacts, reconciled counts, conditional pointer advance, idempotent replay, interruption recovery and rollback evidence. |
| `B8_consumer_cutover` | HTTP, agent and MCP expose only the unpublished exact-UUID authoring lookup; the recommendation feature vocabulary can accept legacy `agri.species` mappings. | Final canonical-taxon plus pinned-release HTTP/tool/MCP proof, no database fallback, and proof that training/recommendation consumers read only eligible immutable profile values while retaining unknown/conflict states. |
| `B9_independent_acceptance` | No dependency-last integration receipt or independent botanical/data-governance/agent-honesty verdict exists. | P4 exact-tree integration receipt followed by a non-author P5 verdict covering source, taxonomy, units/methods, rights, missingness, refusals and consumer boundaries. |

All nine blockers are release-blocking. Clearing one does not weaken any other, and the
transitional lookup clears none of them.

## Frozen assertion envelope

No source field may enter reviewed authoring or Parquet unless its assertion retains the
following columns. Identifiers are strings unless stated otherwise; all enumerations must
be versioned controlled vocabularies rather than free-text conventions.

### Identity and provenance required on every assertion

| Group | Required fields | Gate |
| --- | --- | --- |
| Assertion identity | `assertion_id`, `assertion_schema_version`, `assertion_content_hash` | Stable identifiers and a canonical serialization recipe are present and hash-verified. |
| Canonical taxon | `taxon_authority_id`, `taxon_authority_version`, `taxon_concept_id`, `accepted_name`, `source_name`, `taxon_match_state`, `taxon_match_method`, `taxon_match_record_id` | Exact concept join; ambiguous, unmatched, name-only or authority-version-mismatched rows cannot contribute a profile value. |
| Trait identity | `trait_vocabulary_id`, `trait_vocabulary_version`, `trait_id`, `source_field_id` | One source field maps to one declared concept; overloaded fields are split or withheld. |
| Source identity | `source_dataset_id`, `source_release_id`, `source_release_version`, `source_record_id`, `source_record_url` when stable, `source_artifact_hash`, `retrieved_at`, `valid_from`, `valid_to` | Exact bulk artifact/release and record can be reproduced. Mutable pages without a captured, permitted release are withheld. |
| Original assertion | `original_value`, `original_unit`, `original_qualifier`, `original_missing_code` | Raw meaning is retained verbatim enough to re-normalize; null, zero, false and absent are never conflated. |
| Normalized assertion | `normalized_value`, `normalized_unit`, `normalization_recipe_id`, `normalization_recipe_version`, `normalization_status` | Deterministic conversion is replayed and range/precision checks pass; otherwise no normalized value is published. |
| Evidence class | `evidence_class`, `evidence_type`, `citation_id` | Measured trait, curated requirement, categorical expert summary, occurrence-derived association and objective-effect evidence remain distinct. |
| Method | `method_id`, `method_name`, `method_version`, `method_detail`, `sampling_basis` | Quantitative values and ordered categories identify how they were produced. “Database value” is not a method. |
| Rights | `licence_id`, `licence_version`, `licence_url`, `redistribution_scope`, `api_display_scope`, `training_use_status`, `training_use_scope`, `attribution_text`, `rights_review_id` | Terms are reviewed separately for transformed Parquet redistribution, API/agent display and model training at field level. Unknown, conflicting or restricted rights withhold the assertion from the affected surface; a serving-eligible assertion is not thereby training-eligible. |
| Context | `geographic_scope_id`, `environmental_context`, `observation_start`, `observation_end`, `season`, `life_stage` | Required axes are populated where the trait can vary by them; unscoped context cannot be generalized. |
| Review | `review_state`, `reviewed_by`, `reviewed_at`, `review_basis`, `withdrawn_at`, `withdrawal_reason` | Only approved, non-withdrawn assertions may be candidates; reviewer identity and basis are immutable release inputs. |

### Growth-requirement field gates

| Trait family | Required field-level meaning, unit and method | Missing/conflict rule |
| --- | --- | --- |
| Growth habit | Controlled habit term identifier plus source term, life stage and curation method | Multi-habit assertions remain plural; no arbitrary single-label selection. |
| Native/introduced status | Controlled status plus jurisdiction/region concept, temporal scope and authority method | Unscoped status is withheld; differing regions are not conflicts because they are different assertion contexts. |
| Hardiness | Zone system authority/version, zone lower/upper values, bound inclusivity and mapping method; temperature equivalents only if explicitly supplied or deterministically derived under a versioned recipe | A bare integer range remains unverified; conflicting systems stay separate. |
| Temperature envelope | Numeric lower/upper, temperature unit, statistic, duration/exposure, plant response, life stage and experimental/curation method | Do not convert hardiness labels into physiology without an approved rule. |
| Precipitation/water requirement | Numeric bound/value, unit including time denominator (for example `mm/year` only when source meaning is annual), statistic/period, establishment versus mature persistence, irrigation/rainfall basis and method | Existing `*_precip_mm` cannot publish until period and meaning are resolved. Water need/tolerance is not drought mitigation. |
| Light | Controlled category or numeric irradiance/photoperiod with unit, measurement/curation method, canopy context and life stage | Category and measured light are separate evidence classes; no hidden ordinal conversion. |
| Soil moisture/drainage/texture | Separate trait identifiers; numeric water state includes basis/unit/depth, drainage and texture include versioned vocabularies and method | Never merge drainage class, texture and moisture into one “soil preference.” |
| Soil pH | Numeric range, dimensionless unit declaration, extractant/medium, soil horizon/depth, method and context | Existing pH bounds are withheld until method/medium is known. |
| Salinity | Salinity variable, unit, medium, exposure, response endpoint, method and life stage | A text tolerance label cannot be converted to a quantitative threshold. |
| Elevation | Lower/upper elevation, vertical datum, unit, geographic scope and whether curated or occurrence-derived | Occurrence elevation is an association, not automatically an establishment requirement. |
| Drought tolerance | Stress definition, exposure duration, response endpoint, comparator/scale, method, life stage and context | Must not imply planting benefit, water savings or drought mitigation. |
| Phenology | Event term, date/day-of-year representation, year range, geography, observation method and life stage | Regional or annual observations cannot be generalized without an explicit reconciliation scope. |

### Water, oil and tissue/fuel composition gates

Each measurement additionally requires `tissue_component_id`, `tissue_component_label`,
`live_dead_state`, `water_basis`, `sample_preparation`, `replicate_count` when reported,
`statistic`, `uncertainty_value`, `uncertainty_unit`, and the assertion context above.
The source's omission of a context field is recorded as missing; it is not filled from a
different source or inferred from the taxon.

| Trait | Normalized representation allowed only when | Mandatory distinctions |
| --- | --- | --- |
| Tissue water content | Value has an explicit mass/volume basis, numerator/denominator and unit, with drying or reference-mass method | Fresh/wet basis, dry basis, volumetric water, relative water content and moisture percentage are different traits. |
| Live/dead fuel moisture | Value has a percent or ratio unit, explicit wet/dry basis, fuel component, live/dead state, sampling season and drying method | Live and dead fuels never share an assertion; operational fuel-moisture classes are not tissue measurements. |
| Leaf dry-matter content | Dry mass/fresh mass or dry mass/fresh volume is explicit, with unit, tissue and preparation method | Do not merge with water content or leaf mass per area. |
| Volatile oils | Analyte/compound class, tissue, extraction/analytical method, yield/concentration unit and mass basis are explicit | “Oil,” volatile oil, essential oil, total extractives and individual compounds remain distinct. |
| Extractives/resins | Solvent or operational definition, extraction method, tissue, basis and unit are explicit | Resin presence is not volatile concentration or flammability. |
| Heat content | Gross/higher or net/lower heating value, energy-per-mass unit, moisture/ash basis and calorimetry method are explicit | Wet, dry, dry-ash-free, HHV and LHV values remain distinct. |
| Ash/mineral content | Fraction/percent basis, tissue/fuel component, preparation temperature/method and unit are explicit | Total ash and named minerals are separate traits. |
| Surface-area-to-volume ratio | Geometry definition, sample component, unit and measurement/model method are explicit | Leaf morphology cannot silently stand in for a fuel-bed ratio. |
| Bulk density | Material/fuel-bed component, packing state, volume definition, dry/wet basis, unit and method are explicit | Tissue density, wood density and fuel-bed bulk density are separate concepts. |
| Curing/litter persistence/architecture/rooting/biomass | Versioned state or numeric term, observation window, method, season/life stage and geography are explicit | Descriptive structure does not establish fire behavior, establishment success or objective effect. |

The profile may report these measurements with their contexts. It must refuse a fuel/fire
conclusion unless a separate reviewed effect assertion supports that exact conclusion under
comparable conditions. `fire_tolerance`, flammability, fire behavior, post-fire survival and
post-fire regeneration are different trait/effect identifiers.

### Agricultural-role assertion gates

Every role is modeled as an assertion, never as an unqualified species Boolean. It requires
`role_assertion_id`, versioned `role_id`, `role_object_or_beneficiary_id`, `plant_part_id`
where relevant, `applicability_context`, `jurisdiction`, method/evidence class, source and
licence fields, review fields, and explicit positive/negative/unknown polarity.

| Current concept | Additional release gate |
| --- | --- |
| Nitrogen fixation | Identify direct versus symbiotic fixation, symbiont when known, evidence method, plant part/endpoint, life stage and conditions. A default `false` is missing, not evidence of inability. |
| Pollinator value | Identify pollinator group, floral resource or interaction, season, geography, scoring scale/version, comparator and method. `none` requires affirmative evidence under a declared scope. |
| Edible use | Identify plant part, use/preparation, evidence/citation, safety limitation and jurisdiction. It is descriptive ethnobotanical/use evidence, never dietary or safety advice. |
| Timber/material use | Identify material/plant part, use class, maturity/context and evidence. Presence is not a yield, economic or planting claim. |
| Guild role | Map each source term separately to a controlled role and retain the original term; never publish a free-form array as one reviewed assertion. |
| Companion relationship | Preserve the existing relationship ID, counterpart taxon concepts, direction, relationship type, evidence/review fields and applicability; do not collapse it into either species profile or infer performance. |

## Coverage, missingness and conflict contract

### Source-admission coverage receipt

Before the first field from a source is authored, its admission packet must freeze:

1. exact dataset/release identifier, artifact hash, publisher, retrieval date, update and
   withdrawal behavior, bulk-access mechanism and machine-readable field dictionary;
2. licence version, redistribution/transformation/API-display rights, machine-readable
   model-training permission/status and scope, attribution and any field/table exceptions;
3. taxonomic, geographic, temporal and life-stage scope, record/taxon counts and known
   sampling or curation limits;
4. for every candidate source field: source field identifier, source term/value domain,
   populated, blank, invalid and out-of-domain counts, distinct unit/method/context values,
   taxon-match counts by state, and the proposed trait/role mapping;
5. deterministic normalization fixtures including bounds, precision, qualifiers and all
   source missing codes; and
6. independent source, botanical and rights reviewers with an admit/withhold decision per
   field. Dataset-level admission never upgrades an unreviewed field.

Coverage is not “number of non-null cells” alone. A release manifest must report eligible
taxa/assertions and published, missing, conflicted, rejected, withdrawn and rights-withheld
counts by field, source release and taxonomic/geographic scope. Counts must reconcile to the
assertion artifact and to every published profile row.

### Missingness vocabulary

The assertion and profile schemas must distinguish at least:

- `not_reported` — eligible source record has no assertion for the field;
- `not_applicable` — the field does not apply under the recorded context;
- `source_missing_code` — the source supplied an explicit missing/unknown code;
- `taxon_unmatched` and `taxon_ambiguous` — no exact concept join is available;
- `unit_missing_or_unsupported` and `method_missing_or_unsupported`;
- `context_insufficient` — required basis, tissue, life-stage, geography or period is absent;
- `licence_withheld`, `review_rejected`, `withdrawn`, and `source_record_invalid`;
- `conflicting_assertions` — admissible assertions disagree under equivalent scope; and
- `not_evaluated` — the pipeline or reviewer has not evaluated the field.

Only evidence-backed negative assertions may publish a negative value. `false`, zero, empty
string, empty array and null are never interchangeable. A profile-level missing reason links
to contributing or rejected assertion IDs where they exist.

### Reconciliation and conflict gate

One deterministic decision record per taxon × trait × comparable context must contain
`decision_id`, `policy_id`, `policy_version`, candidate assertion IDs, selected assertion
IDs, disposition (`selected`, `combined`, `conflict`, `withheld`, `missing`), rationale,
reviewer and timestamp. Alternatives are never deleted.

- Assertions are comparable only after taxon concept, trait, unit/basis, method class and
  relevant context axes agree or have an explicitly reviewed conversion.
- Exact duplicates may be deduplicated by content hash while retaining all source links.
- Compatible numeric combination requires a versioned aggregation rule and preserved
  source values, uncertainty and sample weights. Otherwise one reviewed assertion is
  selected or the field remains conflicted.
- An unresolved material disagreement publishes no single normalized value. The profile
  returns `state=conflict`, the assertion/decision IDs and the differing values.
- Source replacement, taxonomy change, correction, withdrawal or policy change creates a
  new release and a field-level change explanation; it never mutates an old release.

## Immutable publication gate

Publication remains prohibited until all of the following are true:

- canonical taxon, assertion, decision, profile and release-manifest schemas implement the
  grains above, and exact occurrence-to-profile concept joins are proven without name text;
- at least one non-Herbaria source has an independent, field-level admission receipt, with
  unadmitted fields withheld;
- every published value traces through decision and assertion IDs to exact source record,
  field, release/artifact hash, original value/unit, method, context, licence and review;
- the manifest records field-level eligibility for each intended consumer; assertions with
  unknown, withheld or prohibited training rights cannot enter training artifacts or model
  consumers even when their terms permit a separately scoped serving release;
- field-level coverage/missing/conflict counts reconcile exactly, and legacy Boolean
  defaults cannot become negative evidence;
- deterministic normalization, source-version change, conflict, withdrawal, idempotent
  replay, interrupted publication, conditional pointer advance and rollback are proven;
- all required Parquet artifacts are immutable and hash-bound before one release pointer
  advances; an absent artifact, field or release returns a typed refusal with no database
  fallback;
- the final HTTP, model-tool and MCP paths accept canonical taxon identity plus a pinned
  release and preserve provenance, conflicts, missingness and response bounds; and
- training and recommendation consumers are proven not to read the editable wide Species
  row as published evidence. Ranking, planting, suitability and objective-effect behavior
  remain in the separate recommendation-validation track.

## Resulting disposition

This audit freezes the minimum P0/P1 contract for future source admission. It does not admit
a source, complete P1, authorize schema or runtime changes, or satisfy P2–P5. The safe next
step is an independent field-level admission review of one exact non-Herbaria source release
against this contract. Until that exists, the transitional lookup remains the only permitted
profile surface and all profile publication, training, ranking and deployment claims remain
withheld.
