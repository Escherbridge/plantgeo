# Plant-suitability rule engine

Warehouse (L1) library: pure Polars/Arrow, no I/O, no database, no HTTP. It may not import `pipeline`,
`planes` or `interface`. It is a port of the frozen v0 prototype
(`.omc/research/plant-suitability-v0-20260926/join/`, gitignored, main checkout only); the prototype's
`GUILD_RULES.md` is the long-form rule record, and every rule below names its prototype source.

A **pick** is an envelope-passing candidate: a taxon a regional seed or planting guide names for the
region and guild, that fails none of the axes below and is not state noxious-listed. It is not a
field-tested recommendation, and absence from a cell is not evidence of unsuitability. Nothing here
claims a plant prevents, slows or reduces fire.

## Module map

| module | owns |
|---|---|
| `schemas.py` | Arrow schemas for the five inputs and the one-row-per-cell output; USPS state codes; the site-input groups, structural site columns and their partition check; the NWPL resolution marker column; `conform` and `assert_vocabularies` |
| `licences.py` | the canonical licence ids, the source-identity check (one licence per source id and per short name, one short name per source id) and `licence_gate`; package-free so `build_fixtures.py` can load it by path |
| `config.py` | `RuleConfig` (mapping and set fields frozen on construction), the presets `V0_FROZEN` and `PRODUCTION`, `canonical_json`, `rule_config_fingerprint` and `assert_rule_config` |
| `site.py` | site conditions at load: vocabularies, `SiteInputProvenance`, the required-group gate, licence withholding, SSURGO classes |
| `names.py` | name keys (binomial, autonym, rank) shared by pools, exclusions and wetland |
| `axes.py` | SSURGO site classes, the site vocabularies they read, and the 12 envelope axes as tri-state expressions; margin; `evaluate_pairs` |
| `applicability.py` | the 13th axis, `source_applicability`: each guide row's precipitation band and habitat qualifier |
| `pools.py` | guide rows at load (vocabularies incl. `noxious_states`, licence ids, source identity, ingress fire claims), species text and composed source tags checked for claims, exclusions at load (USPS state, keyable name), region explode + overrides, woody filter, rank-change guard, stratification inheritance, noxious exclusion, infraspecific merge |
| `wetland.py` | NWPL 2022 rating resolution (with the name table and the resolving rule set's fingerprint) and the wetland-genus assertion |
| `origin.py` | origin decided once per (region, taxon) |
| `labels.py` | per-pick label text, text normalisation, the ingress phrase guard (`FIRE_CLAIM_PATTERN`, `find_fire_claims`), the egress stem and script guard (`FIRE_STEM_PATTERN`, `PLANT_NAME_EXEMPTIONS`, `FireTextAllowList`, `assert_no_fire_claims`), `fire_wording` (both guards over the rule set's own texts) and the woody-buffer fire-field check |
| `ranking.py` | ranking keys and per-cell pick order |
| `engine.py` | `prepare` -> `PreparedEngine` (both seams, metadata and its key constants, cached pools, region pinning, statuses); thin `evaluate_cells` / `candidates_for_cell` wrappers |

The package exports the input schemas (`SITE_CONDITIONS_SCHEMA`, `SPECIES_ENVELOPE_SCHEMA`, `GUIDE_ROW_SCHEMA`,
`EXCLUSION_SCHEMA`, `WETLAND_LIST_SCHEMA`), the output schemas (`CELL_RECOMMENDATIONS_SCHEMA`,
`CANDIDATE_OUTPUT_SCHEMA`), every `METADATA_*` key and `ENGINE_VERSION`.

## Schemas

The five input tables and their required (non-null) columns are the Arrow schemas in `schemas.py`; `conform`
selects exactly those columns, casts them and raises on a missing column or a null in a required one. Columns
nothing reads are not in the schemas: in particular the guide rows carry no quote or source title, so no
verbatim text from a licence-unrecorded source is committed with the fixtures.

- **Species envelope.** `nwpl_route` is required: an envelope that never went through
  `resolve_wetland_ratings` is refused at load instead of passing every wetland check (the prototype's
  red-osier dogwood upland bug). `nwpl_resolved_under` carries the fingerprint of the rule set that resolved
  the ratings (see Wetland). A range whose minimum exceeds its maximum (precipitation in either unit, pH) is
  refused at load, naming the taxa, instead of surfacing later as an inconsistent merged pool.
- **Guide rows.** `guild`, `origin_scope`, `plant_role`, `match_route` and `license` are required and checked
  against fixed vocabularies at load (`pools.GUIDE_ROW_VOCABULARIES`), and so is `habitat_qualifier` when set
  (`applicability.HABITAT_QUALIFIERS`): a misspelt `origin_scope` would silently disable the range-wide rule, a
  misspelt `guild` would report `no_regional_guide` everywhere, and an unmapped qualifier would never apply. A
  guide row naming a `plant_id` absent from the envelope raises, whether or not the licence gate admits it.
- **Site conditions.** `cell_id`, `region`, `state` and `nwpl_region` are required. `state` must be a two-letter
  USPS code and `nwpl_region` one of `AW` / `WMVC` (the prototype asserted it never null, `join/site_table.py`;
  a null or `aw` would otherwise read the other region's rating). `hydric_rating` (Yes / No / Unranked),
  `drainage_class` (the seven SSURGO classes), `restriction_status` (recorded / none_recorded / unknown) and
  `site_texture_group` (coarse / medium / fine) are checked the same way; null stays allowed there because each
  axis reads null as unknown. The error names the column and the value. The USDA hardiness zone is not an
  input: the cold axis reads the record low.
- **Site columns are partitioned.** Every site column is either structural (`STRUCTURAL_SITE_COLUMNS`: `cell_id`,
  `region`, `state`, `nwpl_region`, keys and location classes) or in exactly one site-input group. Importing
  `schemas.py` raises if a column is in no group, in two, or a group names a column the schema lacks, so a new
  measurement column cannot arrive without a provenance group.
- **Exclusions.** `state` must be a USPS code: a list keyed `Idaho` would match no `ID` cell and silently serve
  a noxious taxon.

## Engine

`prepare(species, guide_rows, exclusions, config, site_provenance=None) -> PreparedEngine` validates the rule
set, the three reference tables and the site provenance once. The engine then serves every seam from that one
state:

- `evaluate_cells(site)`: one row per input cell, in input order.
- `layer_table(site)`: the same cells as Arrow, cast to `CELL_RECOMMENDATIONS_SCHEMA` (plain `string`,
  non-null ids), carrying `metadata()`. Metadata can only come from the engine that built the cells.
- `candidates_for_cell(cell_site)`: a one-row site frame; every pool taxon per guild, picks first in rank order
  with labels, then non-picks with their failed axes. `candidates_with_metadata(cell_site)` returns the same
  frame with `metadata()`, for a per-point response that must carry its provenance.
- `warm(region_states)`: builds each named region's pools (region -> state) before serving, and pins each
  pairing (see Regions and states). Call it at startup so the first request does not pay for a pool build.
- `metadata()`: the keys below, each a `METADATA_*` constant in `engine.py`, JSON values in canonical form
  (keys sorted, compact).

| key | value |
|---|---|
| `plantgeo:rule_config` | `"<name> sha256:<hex>"`, see Presets |
| `plantgeo:engine_version` | `ENGINE_VERSION`, a date; bump it on any change to what a served table says for the same inputs and rule set |
| `plantgeo:inputs_sha256` | sha256 over the conformed species envelope, every loaded guide row (admitted or not: a row the licence gate drops still turns a guild's status from `no_regional_guide` to `licence_excluded`, review R2 N5), the exclusions (each as its rows' JSON lines, sorted, so row order never changes it) and the site-input declaration. List cells the engine reads as sets (`engine.SET_LIKE_LIST_COLUMNS`) are sorted first, and `in_region` is reordered with `applies_to_regions` (`PAIRED_LIST_COLUMNS`), so order inside a list cell never changes it either (review R3; served output is unchanged under that reordering) |
| `plantgeo:pick_definition` | the preset's pick definition |
| `plantgeo:no_fire_claim` | the disclaimer |
| `plantgeo:admitted_sources` | each admitted source_id -> `{short_name, licence}` |
| `plantgeo:excluded_sources` | each source_id the licence gate dropped -> its licence id |
| `plantgeo:site_inputs` | the full site-input declaration |
| `plantgeo:withheld_site_inputs` | the groups nulled under the gate, with their licence ids |

**Immutability.** `PreparedEngine` is a frozen dataclass (`eq=False`), so its rule set, inputs and provenance
cannot be reassigned after preparation (review p09: reassigning `config` once served v0 metadata over
production pools). The mappings a caller hands in are copied into read-only views on construction:
`SiteInputProvenance.groups` and `RuleConfig.introduced_flag_text` (review R2 N4: mutating the caller's dict
after `prepare` withheld precipitation on the next call, under unchanged metadata). The four set fields of
`RuleConfig` (`in_region_overrides`, `permitted_licences`, `required_site_input_groups`,
`declared_empty_exclusion_states`) are copied into frozensets the same way (review R3: clearing the caller's
licence set after `prepare` nulled every site group, and the engine's config no longer fingerprinted as its metadata
said). The metadata is built once at construction, guarded once (see Labels) and held read-only.
Its only mutable state is `RegionCache`: the built pools, each region's first-seen state, and one
`threading.Lock` guarding both. A cold pool build happens under the lock with a second check inside it, so
concurrent cold calls build a region once (p09 measured eight builds from sixteen cold calls on eight threads).
One lock, not one per region: builds are rare and short, and cached reads never take it.

Both seams call `region_candidates` -> `evaluate_guild` -> `ranked_picks` -> `with_pick_labels`, so the
per-point tool's top three is the batch top three by construction (the flow test pins it). The module-level
`evaluate_cells(...)` and `candidates_for_cell(...)` are one-off wrappers over `prepare(...)`.

**Cell budget.** `evaluate_cells` and `layer_table` refuse more than `MAX_CELLS_PER_CALL` (10,000) cells. The
review measured about 127 MiB per 1,000 Boise cells and a 1.40 GiB peak at 10,000 cells, whose largest pool
is 63 taxa. Peak memory follows (cell x taxon) pairs, not cells: a 109-taxon pool (v0 Bend hedgerow) would
peak near 2.4 GiB at the same cell count. So each guild is evaluated in cell chunks of at most
`MAX_CELL_TAXON_PAIRS` (640,000) pairs, the measured 1.40 GiB peak, whatever the pool size. Every step is
per cell (axes, applicability, ranking over `cell_id`, labels), so chunking never changes the output. The
figure is the reviewer's measurement before chunking; it was not re-measured.

**Regions and states.** Region is the pool key. A region no guide row names (counted before the licence gate)
raises "unknown regions", which catches a typo such as `Boise`. A region spanning two states in one call
raises. The engine also pins each region to the state it was first served (or warmed) with, and refuses a
later call pairing it with another. Presence and noxious lists are per state, so one engine silently serving
`boise` under both `ID` and `OR` was a split layer (p09 §1). A refused call pins nothing. When a region has a
scored cell, its state must have at least one noxious-list row, or appear in
`RuleConfig.declared_empty_exclusion_states`; otherwise the build raises, because an absent list would silently
exclude nothing.

**Guild status.** Every per-point row carries the guild's `status` and the cell's `scoring_null_reason`, exactly
as the batch reports them. Withheld cells (`scoring_null_reason` set) get every guild column null, and the
per-point tool answers them with status null and the reason. Otherwise a guild's status is:

| status | when | counts |
|---|---|---|
| `scored` | the pool has taxa | numbers (0 when nothing passes) |
| `no_regional_guide` | no guide row names the guild in the region, counted before the licence gate | null, pool size 0 |
| `licence_excluded` | guide rows exist, but the licence gate removed them all, or removed every scorable row (one naming a PLANTS taxon) | null, pool size 0 |
| `no_eligible_taxa` | admitted scorable rows exist, but noxious lists, the woody filter or the rank-change guard emptied the pool; or no row was ever scorable | null, pool size 0 |

The scorable count matters when an admitted row names no taxon (an unmatchable listing): before it, the gate's
removal of every row that could have scored was reported as `no_eligible_taxa` (p09 §4). `V0_FROZEN` has no
gate, so it never emits `licence_excluded`; its one empty pool (Corvallis greenstrip) has no guide rows at all
and stays `no_regional_guide`.

`resolve_wetland_ratings` is a build-time step that fills the envelope's NWPL columns; the engine only
reads them. Regional-name -> PLANTS matching is also upstream: guide rows arrive with
`matched_plant_ids` and `match_route` (the prototype's `regional_pools.match_regional_names`).

## Presets

Every behavioural difference is a field; no code branches on a preset's name.

| field | `V0_FROZEN` | `PRODUCTION` | owner decision |
|---|---|---|---|
| `in_region_overrides` | none | TN 2A, TN PM-50, OR/WA guides in-region for `bend` | "eastern Oregon" / Intermountain = east of the Cascade crest |
| `range_wide_origin_is_not_state_claim` | False | True | open issue 2: TN 2A, TN PM-77, TN PM-50 origin statements are range-wide |
| `inherit_stratification` | False | True | open issue 1: unstratified in-region rows inherit the taxon's bands/qualifiers |
| `null_restriction_depth_is_unknown` | False | True | SSURGO split: null restriction depth is unknown, never a pass |
| `introduced_flag_text` | "(CPS 394)" on every guild | CPS 394 on greenstrip only | CPS 394 is the Firebreak standard |
| `pick_definition` | v0 text | adds "absence is not evidence of unsuitability" | open issue 5 |
| `permitted_licences` | none (no gate; v0 had none) | `public-domain`, `us-government-work`, `CC0-1.0`, `CC-BY-4.0` | "exclude non-commercial sources from v1" |
| `required_site_input_groups` | none | every group in `SITE_INPUT_GROUPS` | review B1: never serve a pick blind to an input the envelope reads |
| `label_unchecked_axes` | False | True | review B1: a pick names every axis it could not check |
| `declared_empty_exclusion_states` | empty | empty | a state served without a noxious list must be declared, never inferred |

Restoration uses the plain flag in `PRODUCTION`: the owner named greenstrip (with CPS 394) and hedgerow
(without) and gave the reason ("it is the Firebreak standard"); restoration is not a firebreak either.

Of the four `in_region_overrides` source ids, only TN 2A changes the curated rows today (TN PM-50 and the OR/WA
guide are already in-region for Bend there); all four stay because they record the owner's decision and survive
re-curation.

`null_restriction_depth_is_unknown` exists because production ships SSURGO restriction depth null. Once
restriction depth is served it must flip to False, and the producer must then emit an unknown restriction status
(not `none_recorded`) for cells it does not serve, so a missing depth stays unknown rather than a pass.

**Fingerprint.** `plantgeo:rule_config` is `"<name> sha256:<hex>"`: the digest covers every `RuleConfig` field
as canonical JSON (keys sorted, set members sorted). A modified preset such as
`replace(PRODUCTION, permitted_licences=None)` keeps the name but not the digest, so it cannot pose as
`production`; a flow test changes each field in turn and fails when a new field has no entry. `prepare` also
refuses a rule set that names an unknown licence id, state code or site-input group, or whose flag texts or pick
definition carry fire wording (`config.assert_rule_config` via `labels.fire_wording`: the ingress claim check plus
the egress stem and script check with only the fixed allowed texts; review R3). Flag texts are served in every
introduced label and the pick definition in the metadata, and neither is on the egress allow-list.

Shared by both presets (owner decisions already in v0): the cold axis compares the site RECORD LOW with
PLANTS minimum temperature; a frost-free shortfall within the region's station-bias magnitude is unknown
("uncertain frost-free fit"), never a fail; in-region ranks ahead of frost-free certainty; neighbour guides
stay, labelled "in-region: no — neighbouring guide".

## Licences

Guide rows carry a canonical licence id, never free text:

| group | ids |
|---|---|
| permitted under `PRODUCTION` (`COMMERCIAL_USE_LICENCES`) | `public-domain`, `us-government-work`, `CC0-1.0`, `CC-BY-4.0` |
| known, not permitted (`RESTRICTED_LICENCES`) | `CC-BY-NC-4.0`, `CC-BY-NC-SA-4.0`, `CC-BY-ND-4.0`, `all-rights-reserved`, `unrecorded` |

Any other text raises at load, so a new licence forces curation instead of being silently dropped (the v0
port matched trimmed, case-folded free text). Source identity is checked at load
(`licences.SOURCE_IDENTITY_PAIRS`):

- one licence per `source_id`, so a source is admitted or dropped whole (before this rule, relicensing one row
  listed the source as excluded while 24 served labels still cited it);
- one licence per `source_short_name`, because origin votes and labels cite the short name. Two curated
  documents span two ids each: "ODFW 2017" (`odfw_2017`, `odfw_rehab_2017`, both `unrecorded`) and "NRCS
  OR/WA guide 2000" (`orwa_2000`, `orwa_guide_2000`, both `us-government-work`). Relicensing one id alone now
  raises, so the gate cannot admit a document under one id while dropping it under the other (p11);
- one short name per `source_id`, so `plantgeo:admitted_sources` names each source once.

`permitted_licences` is an allow-list; `None` means no gate. The gate runs at load, before pools, origin votes,
stratification and wetland name resolution see the rows. Every dropped source_id is listed with its licence id
in `plantgeo:excluded_sources`, every admitted one in `plantgeo:admitted_sources`.

**Fixture licences are an assumption.** v0 captured no licences. `tests/plant_suitability/build_fixtures.py`
marks a source `us-government-work` only where the prototype's `regional_lists/SOURCES_*.md` names one US
federal agency (NRCS, BLM, USFS, USFWS, USDA ARS) as the sole publisher; every other source, including
co-published, contractor-named (BLM Boise District ESR plan) and SWCD-distributed ones, stays `unrecorded`.
Real per-source licence curation is owed before any production run; until then PRODUCTION serves only the
federal guides.

## Site inputs

No site column carries a source or licence, so the caller declares them: `SiteInputProvenance` maps each group
in `SITE_INPUT_GROUPS` to a `SiteInputSource(source, licence, release)`. It replaces the prototype's
`prism_status` note (`join/build_cell_recommendations.py`), which the port dropped. An unknown group or
licence id raises on construction.

| group | columns | fixture source (prototype `site_conditions/`) |
|---|---|---|
| `soil_survey` | drainage, hydric, restriction status and depth, EC, CaCO3, MLRA, `scoring_null_reason` | SSURGO / SDA, `us-government-work` |
| `soil_ph_texture` | `site_ph`, `site_texture_group` | SSURGO where present, else SoilGrids v2.0, `CC-BY-4.0` |
| `cold` | `record_min_c` | ERA5-Land via Open-Meteo, `CC-BY-4.0` |
| `frost_free` | `median_frost_free_days` | ERA5-Land via Open-Meteo, `CC-BY-4.0` |
| `frost_free_station_bias` | `frost_free_days_station_bias` | NCEI 1991-2020 normals, `us-government-work` |
| `precipitation` | `mean_annual_precip_mm`, `dry_year_precip_mm` | v0: PRISM 1991-2020 normals, `unrecorded`; production: ERA5 via Open-Meteo, `CC-BY-4.0` |

pH and texture are their own group because the prototype falls back to SoilGrids (CC BY 4.0) where SSURGO has
no value, so the declaration names the more restrictive licence. The station bias is its own group because it
is measured against an NCEI station normal.

**Required groups (review B1).** With PRISM withheld, both precipitation axes and every precipitation band
read unknown, and unknown never fails, so 141,976 (cell, taxon) pairs turned into picks and 87% of top-3
slots changed; *Picea engelmannii* (at least 533 mm) ranked first at Bend on 180 mm. The labels said nothing.
`RuleConfig.required_site_input_groups` closes that: `prepare` (so every seam) raises, naming the groups, when
a required group is undeclared or its licence gate withholds it. PRODUCTION requires every group. The check is
on the declaration, never the data, so it cannot pass one chunk and fail the next. Sparse per-cell nulls inside
a permitted group stay "unknown never fails" (the owner rule for per-cell gaps), and PRODUCTION names those
unknown axes in the pick label (see Labels). A permitted group that happens to be null for every cell of one
call is treated the same way: per-cell gaps, each named.

**Withholding.** For a group the rule set does not require, under a licence gate (`permitted_licences` not
`None`): a group with any non-null value and no declaration raises, and a declared group whose licence is not
permitted is nulled, so its axes read unknown, never a fail, and it is listed in
`plantgeo:withheld_site_inputs`. `scoring_null_reason` is declared with the soil survey but is never nulled:
nulling it would score cells the survey withholds (a flow test withholds the soil survey under a variant that
does not require it). Without a gate (`V0_FROZEN`) nothing is withheld, and the declaration is only recorded in
the metadata.

**The fixture's production precipitation.** The prototype's climate intermediates carry ERA5 precipitation
next to PRISM (`site_conditions/climate/all.parquet`: `mean_annual_precip_mm` and `annual_precip_p20_mm` are
ERA5; `prism_annual_precip_mm` and `annual_precip_p20_prism_scaled_mm` are PRISM, the latter PRISM's mean
times ERA5's relative spread). The Open-Meteo model is `era5_seamless`: ERA5-Land temperature with ERA5
precipitation at 0.25 degree, because `era5_land` returns no precipitation. So the declaration says ERA5, not
ERA5-Land. The prototype's own QUALITY.md warns ERA5 is far too wet in the Bend rain shadow (fixture median
610 mm against PRISM's 337 mm); see Deferred.

## Axes

1 pass, 0 fail, null unknown; a missing value is never a silent pass and unknown never fails, it is
counted (`unknown_axis_count`, `count_fully_known`) and, under PRODUCTION, named in the pick label.
Thresholds are prototype `GUILD_RULES.md` §5.

- **SSURGO split.** Drainage class and hydric rating alone derive `site_wetness_class` and
  `site_drainage_regime`, which drive the anaerobic, drought and wetland axes. EC (salinity), CaCO3
  (lime) and restriction depth are separate inputs and are unknown when null. Production ships them null
  until a SSURGO follow-up, so every production candidate carries those unknowns.
- **Frost-free.** Site days minus the region's station bias; a shortfall no larger than `|bias|` is
  unknown. Because the bias is positive in all pilots, the axis fails exactly when raw days fall short.
- **Wetland.** A null `nwpl_route` (never resolved) is unknown, never a pass. A resolved route with no
  western rating passes: NWPL lists the taxon only outside the Arid West and Western Mountains regions, which
  NWPL reads as upland there, as it does for `not_listed` (in the fixtures: *Thuja occidentalis*, *Liatris
  pycnostachya*), and v0 scored it so.
- **Robustness margin.** Smallest normalised headroom across known numeric axes, rounded to 0.001 so equal
  limits tie exactly; it breaks ties and measures no fire effect.

## Applicability

A taxon is a pick at a cell only when at least one guide row naming it applies there: its precipitation
band (inclusive, open sides never fail) against the cell's mean annual precipitation, and its habitat
qualifier against site data (`saline_alkali_or_poor_drainage`: EC > 2 dS/m or poorly drained;
`sandy_or_loam`: coarse or medium texture; `forest_woodland`: MLRA 3, 6 or 43B; `juniper_sites`: always
unknown; `wet_soils`; `moist_to_wet_soils`). In-region support at the cell comes from the rows that carry
the pick (rows that apply, or when none applies, rows whose status is unknown).

**Stratification inheritance** (production): an in-region row with neither band nor qualifier takes every
distinct band/qualifier condition carried by any stratified row naming the same binomial in the same
region (any guild), one copy per condition, and applies when any copy applies. The label gains
"site stratification from <source tags>". Example: *Leymus triticoides* at Boise, carried in-region only
by the unstratified BLM Boise District list, inherits ODFW's saline/alkali qualifier. Same region only,
because a region's guides are the evidence for that region's sites. Only admitted rows lend conditions: a
licence-excluded stratified row never reaches the pools.

## Pools

Only taxa a regional guide names; PLANTS supplies traits and labels, never extra species. Non-natives are
allowed unless the state noxious list or the row's own noxious annotation names them. Both are keyed by USPS
code and checked at load: a guide row's `noxious_states` value outside `USPS_STATE_CODES` (e.g. "Idaho") raises,
because it would match no cell and fail open (review R2 N1). A noxious-list name no exclusion rule can key (a
bare genus without "spp.") raises too, instead of being skipped (R2 N6). A genus listing whose
listed name, common name or ambiguity flag says "non-native" removes only the genus's introduced members; a
PLANTS L48 native member stays.

The woody guild (`hedgerow_buffer`) counts only rows whose own `plant_role` is shrub or tree. A
species-rank listing that reaches an infraspecific PLANTS taxon keeps it only when PLANTS records it in the
state. The infraspecific merge keeps one row per binomial; the kept row is named-by-source, then the
species row or autonym, then recorded in the state, and a tie on all three raises (never alphabetical).
Members only fill the kept row's null traits, taking the first donor in kept-row order and then `plant_id`
order, so the result never depends on input row order; a fill that inverts a range is rejected. A guide row
with no page is tagged "page n/a" (prototype `regional_pools.page_label`).

## Wetland

NWPL 2022 rating per taxon by route: accepted binomial, PLANTS synonyms, the name table
(*Cornus sericea* -> *Cornus alba*, *Ledum glandulosum* -> *Rhododendron columbianum*), then the names the
guides printed; several hits keep the wettest. `resolve_wetland_ratings(..., config)` reads printed names only
from rows the config's licence gate admits. Under PRODUCTION, *Cleome lutea* and *C. serrulata* therefore
resolve `not_listed`: NWPL files them under *Peritoma*, which only the unrecorded Xerces guides print. Per
cell, the rating in the cell's NWPL region is used, falling back to the other western region. A wetland-genus
pool taxon left `not_listed` raises unless it is documented checked-absent (*Salix pentandra*).

**Resolved under which rule set (review p10).** `resolve_wetland_ratings` stamps `nwpl_resolved_under` with
the resolving rule set's fingerprint, and `prepare` refuses an envelope stamped with anything else, or not
stamped at all. Before this, an envelope resolved under V0 (every printed name) served PRODUCTION with ratings
read through licence-excluded Xerces names. The full fingerprint is compared, not only the licence gate: it
is cheap (about 50 ms per resolution) and needs no second notion of "compatible rule sets".

## Origin

Decided once per (region, taxon) across every guide row of the region, all guilds, against the region's state:
one verdict per document (a document that says native anywhere is native; a curator-marked conflicting
"introduced" falls to PLANTS state presence), in-region documents outvote neighbours, a tie falls to PLANTS,
silence falls to PLANTS L48 status. A taxon PLANTS does not record in the state, with no in-region
state-scoped native statement, is flagged "not recorded in <state> (PLANTS)" and ranks like an introduced
taxon. The guide row column `origin_scope` (`regional` / `range_wide`) replaces v0's dead
`RANGE_WIDE_NATIVE_PATTERN`, which matched only rows whose origin was "unstated". The flag wording is rendered
per guild from the preset.

Licence-dropped documents do not vote. A document is keyed by its short name, and a short name carries one
licence id, so a document's rows are either all admitted or all dropped: a dropped document has no admitted
region row through which to vote. Every vote is named in the label, so the flow test asserts that no served
origin label cites an excluded source.

## Labels

` | `-joined fields: greenstrip and restoration start with the verbatim
"PLANTS fire-resistant label (not a fire claim; judged against California fires): <Yes|No|conflicting|unknown>";
hedgerow carries no fire field. Then origin, in-region, frost-free fit, "listed as", sources (with
`[neighbour]` / `[medium]`), in production the optional stratification field, and in production
(`label_unchecked_axes`) a last field naming every unknown axis in full words, e.g.
"unchecked: annual precipitation, soil pH, guide band or habitat" (`labels.AXIS_LABELS`). A pick with every
axis known has no such field.

The frost-free field is "uncertain frost-free fit" (a shortfall within the station bias), "frost-free fit:
confirmed", "frost-free fit: unknown (site frost-free days missing)" when the site's frost-free days or station
bias is null, else "frost-free fit: unknown (no PLANTS minimum)". The site-missing wording is not a preset
field: the old text blamed PLANTS for a missing site value (p15), and no frozen v0 cell has a null frost-free
input, so v0's output is unchanged (rechecked on all 7,500 cells).

**Normalisation.** Every text is normalised before any match (`labels.normalised_text`):

1. NFKD, which splits ligatures, full-width letters, NBSP and accented letters into base letters plus marks.
2. Every character in Unicode categories Mn, Me, Cf and Cc is removed (marks, bidi and other format
   controls, variation selectors, tag characters, zero-width characters, U+00AD), plus the Hangul fillers
   (letters drawn blank). Whitespace controls (newline, tab, CR) become a space instead: removing them would
   glue "reduce\nwildfire" into one word.
3. NFC.
4. Dashes (U+2010-U+2015, U+2212, U+FF0D, U+2027) become "-", and Cyrillic, Greek, Armenian, Latin (alpha,
   dotless i, iota) and Coptic Latin-lookalikes are folded (a subset of the Unicode UTS #39 confusables
   skeleton). The Cyrillic palochka is drawn like both I and l, so it folds to "i" and every guard also reads
   the text with it as "l".
5. Whitespace runs become one space.

Words are separated by `[\W_]`, so an underscore separates too. Matches never cross a label segment: text is
split on " | " (fields) and " + " (citations) first, so a verb in one citation and a fire word in the next no
longer compose a claim.

**Two guards (review M1).** Round 2 found 39 of 50 new phrase-guard bypasses, so the served output is no longer
guarded by guessing claim phrasings:

- **Ingress, phrase level** (`FIRE_CLAIM_PATTERN`, `find_fire_claims`): at load, over every text column of every
  guide row (naming the offending source_ids), every text column of the species envelope (naming the taxa;
  common names are served in the top three, review R2 N2), and the source tags and listed names
  `pools.region_rows` composes (R2 N3: a short name "... Plants That Reduce" and a page "Wildfire appendix" are
  each clean, but the tag "... Plants That Reduce Wildfire appendix" is a claim). Source titles may legitimately
  contain "Fire" (BLM Range Fire 2026), so ingress refuses claims, not words: a claim verb (prevent, reduce,
  slow, stop, suppress, inhibit, contain, fight, hinder, impede, deter, lessen, minimise, ...) within six words
  of a fire noun (fire, wildfire, flame, flammability, ignition, fuel, spark, ember, burn forms, combustion);
  "keeps fire from / away / at bay"; a fire-proofing compound (fire-resistant, flame-retardant, fire-resilient,
  fire-hardy, firewise, FireSmart); "resistant to fire" and "resists the spread of fire"; low / less / non
  flammability; slow-burning, hard to ignite, non-combustible; less fire-prone; burn severity after a claim
  verb; and the barriers ("firebreak", "fuel break", "fire barrier", "firewall", "barrier / buffer / shield
  against fire", "defensible space"). Only the verbatim label prefix and the disclaimer are removed first.
  Each segment is matched as written, spelled (step 3 below: "f1r3-resistant") and joined (punctuation between
  two letters removed, ";" included: "fi.re-resistant", "fi;re-resistant"; review R3). The claim pattern keeps
  word boundaries, so joining "grand fir;European alder" creates no claim. Ingress matters most for short names:
  egress allows a registered short name verbatim, so a claim inside one must stop here.
- **Egress, fire-family stems off a closed allow-list** (`assert_no_fire_claims`, `fire_tokens`,
  `FireTextAllowList`): over every string and list-of-string value of every served table, per call.
  1. Any letter, digit, other symbol or currency sign (Unicode L*, N*, So, Sc) beyond Latin-1 left after
     normalisation refuses the table (`out_of_script_characters`), checked over the whole text. Lookalikes are
     folded to Latin first, so what remains (IPA small capitals, f with hook, Greek epsilon, Cyrillic ghe,
     regional indicator symbols, the euro sign) can only be a disguise; no served fixture or prototype text holds
     one (typographic quotes are punctuation and pass).
  2. The allowed texts are removed, whole-word: the label prefix, the disclaimer, the guild names, every admitted
     source short name and every loaded source_id (`PreparedEngine.allow_list`; excluded ids appear in
     `plantgeo:excluded_sources`). The preset's flag texts are not allowed (review R3: a flag text "introduced —
     this hedge slows wildfire spread" allowed itself); `prepare` refuses fire wording in them instead (Presets).
  3. Each remaining segment is read spelled: single letters spelled out are joined ("f i r e", "f.i.r.e") and
     digits or signs inside a word are read as letters ("f1re", "f|re"; `LETTER_SUBSTITUTES` also reads the
     inverted "!" and broken bar as i, the euro and pound signs as e, review R3).
  4. It is read joined too (`EGRESS_INTRA_WORD_PUNCTUATION`): punctuation between two letters removed ("fi.re",
     "fi-re", "fi" middle-dot "re", "fir'e"; U+2024 is "." after NFKD). Exemptions are removed before joining as
     well as after, so "Viburnum/Cornus" never reads as one unexempted word. Egress never joins across ";": the
     engine joins top-3 names with it, and the prototype holds both "grand fir" and "European alder", so
     "grand fir;European alder" would read "firEuropean" (R3 scan: the reviewer's all-punctuation class refused 26
     real served top-3 strings before exemptions moved ahead of joining).
  5. A word holding a mask sign (`MASK_SIGNS`: "*", "?") is matched against every stem with each sign standing
     for any letter, when at least two matched letters are real ("f*re", "b?rn"); a footnote mark ("Cutleaf
     daisy*") or PLANTS' "I?" matches nothing.
  6. The closed plant-name exemptions are removed whole-word (`PLANT_NAME_EXEMPTIONS`): fireweed, firewheel,
     firethorn, firecracker (penstemon), swampfire, burnet, burningbush / burning bush, burnweed (American and
     coast burnweed, *Erechtites*), blazingstar / blazing star, flameflower (*Phemeranthus*), flammula,
     viburnum, viburnifolium, agropyron, diospyros, pyrola, pyrolaceae, pyroliflora, pyrolifolia (*Salix
     pyrolifolia*). That is every word holding a stem across the 2,186 prototype taxa, both regional lists and
     every served fixture text (R2 scan, 2026-10-03), plus the real taxa the reviewers gave.
  7. Any word still holding a fire-family stem refuses the table (`FIRE_STEMS`, `FIRE_STEM_PATTERN`): fire, flam,
     burn, ignit, combust, fuel, spark, blaz, conflagr, smoulder / smolder, scorch, extinguish, pyro anywhere
     inside the word, and ember at its start only (member, September). Stems, not word forms: R2 found 26 served
     bypasses of the old exact-word list (brushfire, unburnable, flammables, ignited, blaze, conflagration,
     fireline, ...). One such word refuses the whole call, so a new plant name holding a stem refuses the layer
     until it is added to the exemptions, which fails closed. The refusal message says so: fix a false refusal
     with an exemption for a real plant name, never by loosening the stems.

  Only allowed texts that themselves hold a stem are removed (removal is whole-word, so no other can uncover
  one), which keeps the scan cheap.
- **Metadata** is guarded once, at construction, by the egress rule over its decoded values: each JSON value is
  parsed and every key and string walked, never scanned as JSON text, so a newline, tab or CR in a caller's
  site-input source is a real separator, not a "\n" escape that hides the claim (p13). The same decoded strings
  also go through the ingress claim check (review R3: "ERA5-Land; these hedges create defensible space" has no
  stem, so egress alone served it). Metadata is immutable afterwards, so it is not re-scanned per call.

**Woody buffer.** `assert_no_fire_field_on_woody_buffer` is structural: the woody buffer carries no fire field,
meaning neither the PLANTS label prefix nor any "Fire Resistant" field text (`FIRE_FIELD_PATTERN`, after
normalisation), in any woody pick or origin label. The per-point tool checks every woody row's origin label,
pick or not. A source title that contains "Fire" is a citation, not a field, and is served. Every
greenstrip/restoration label must open with the verbatim prefix.

## Ranking

Leading keys for every guild: in-region, confirmed frost-free before uncertain, fewest unknown axes,
unflagged before flagged. Then per guild (prototype `guild_rules.RANKING_KEYS`), robustness margin, and the
name last. `rank1_tie_count` shows how often the name alone decides rank 1.

## Tests and fixtures

`tests/plant_suitability/` drives the public seams. `tests/fixtures/plant_suitability/` holds 20 cells per
pilot (the named role cells first, then one cell per square of a 5 x 5 longitude/latitude grid, visited
diagonal by diagonal so every row and column of the pilot is sampled), every guide row with its licence id,
the matched species rows (NWPL ratings as the prototype resolved them, `nwpl_resolved_under` null), ID/OR
exclusions, the NWPL rows those taxa need, the frozen v0 output rows for the chosen cells, `site_inputs.json`
(the v0 declaration, PRISM precipitation), `era5_precipitation.parquet` and `era5_precipitation_source.json`
(the fixture cells' ERA5 precipitation and its declaration) and `fixture_cells.json` naming each special cell
and taxon. They are generated, never hand-edited, by `tests/plant_suitability/build_fixtures.py` from the
frozen prototype.

`support.SuitabilityFixture` serves each rule set the inputs production would:

- the envelope is resolved with `resolve_wetland_ratings` under the rule set being tested (the rejection flows
  pass a fixed one instead);
- a preset whose licence gate would withhold the PRISM precipitation it requires (PRODUCTION) reads the ERA5
  precipitation and declaration, joined by cell id; every other preset, V0 included, reads PRISM. This is the
  least churn: `site_conditions.parquet` stays the v0 input untouched, the golden keeps PRISM, and the ERA5
  table is 60 rows;
- `relicensed(...)` relicenses whole documents (every source id sharing a short name), as the load check requires.

Production tests use real `PRODUCTION` unless their subject comes from a licence-unrecorded source or needs
PRISM. Those use `dataclasses.replace(PRODUCTION, permitted_licences=None)` (which reads PRISM) or relicense the
minimum set of documents, and say why in one line. The TN 2A in-region flow is one: TN 2A's 7-12 in bands never
apply at Bend on ERA5.

**Mutation survivors left alone.** Of the round-2 survivors, these are equivalent and have no test:

- `licence_dropped_docs_vote_in_origin`: documents built from every row still vote only through an admitted
  region row, and with one licence per short name a dropped document has none.
- `normalise_no_dash_fold`: every dash is a `[\W_]` separator already, and allowed texts are normalised the
  same way as served text, so folding dashes changes no word and no token.
- `normalise_no_whitespace_collapse`: separators are `[\W_]+`, whitespace controls already become spaces, and
  allowed texts are normalised identically; the segment split needs the engine's own " | ", which it emits
  with single spaces.

Every other round-2 survivor now has a flow or table test: the fingerprint over every field; the drainage,
restriction and texture vocabularies, the rule-set and provenance validations (rejection table); the
never-withheld survey reason; the woody origin-label, prefix and field-wording checks; the list-column guard;
the layer's own-rule-set metadata; donor order (a synthetic merge over every row order). R2's one
non-equivalent survivor, `x_duplicate_cell_check_disabled`, now has a rejection row (`repeated_cell_id`).

Round 3 adds one mutant per fix (scratchpad `ps-r3fix/mutate_r3fix.py`). `r3_flag_texts_allow_listed` (flag texts
back on the egress allow-list) is equivalent: `prepare` refuses any flag text holding a stem, so allowing one
uncovers nothing.

## Deferred

- **Regional literals (review m-e).** `config.EAST_OF_CASCADE_CREST_REGION` ("bend") with its source ids, and
  `applicability.FOREST_WOODLAND_MLRAS`, are pilot facts in code. They move to region/release data in the
  wiring push.
- **Geometry.** Cells carry no geometry here; the wiring adds it.
- **Copernicus DEM in the cold group.** The record low is lapse-adjusted to Copernicus DEM GLO-90. The
  fixture declaration names it in the source text, but a group carries one licence id (ERA5 via Open-Meteo,
  CC BY 4.0). Revisit if the DEM's terms ever differ from the permitted set.
- **Per-point latency.** A prepared per-point call no longer rebuilds pools; it measured 86-457 ms for three
  guilds on the dev machine, almost all in Polars collects inside `evaluate_guild` / `with_pick_labels`.
  Revisit if the point tool needs to be interactive.
- **ERA5 precipitation at Bend.** ERA5's 0.25-degree cells blend Cascade-crest precipitation into the rain
  shadow (fixture median 610 mm against PRISM's 337 mm), so on ERA5 the TN 2A and other dryland bands rarely
  apply at Bend. Production needs a precipitation source with verified terms and finer resolution (or PRISM
  cleared) before Bend picks are credible; the engine now refuses to hide the gap, but cannot fix the data.
- **Egress limits.** Egress refuses words, not meanings: a claim with no fire-family stem ("defensible space
  shrub", "a greenbelt that protects homes") passes it, and only ingress stops it in guide rows, species text,
  the rule set's texts and the metadata. A synonym family not in `FIRE_STEMS` (e.g. "heat", "smoke") passes both;
  extend the stems and the exemptions together, against a fresh scan of the served texts.
- **Disguises still read as other words (R3 residue).** Misspellings and phonetic spellings ("fiire", "phyre"),
  a word split into multi-letter fragments by spaces ("fi re"), separators longer than three characters between
  spelled-out letters ("f -- i -- r -- e"), and ";" inside a word at egress ("fi;re-following": ingress joins
  ";" and refuses it only inside a claim). Each needs a reading that would also join real neighbouring words;
  revisit with a scan of the served texts if a source ever carries one.
- **Memory after chunking.** `MAX_CELL_TAXON_PAIRS` is derived from the pre-chunking measurement; re-measure a
  10,000-cell Bend call (pool 92-109) on an idle machine.
- **Concurrency test.** The build-once lock is verified by the reviewer's probe (p09 §3), not a suite test: a
  test would have to count builds, an interaction assertion.
