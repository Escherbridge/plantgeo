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
| `schemas.py` | Arrow schemas for the five inputs (species envelope, guide rows, site conditions, exclusions, NWPL list) and the one-row-per-cell output; `conform` casts and refuses missing columns or nulls in required ones |
| `config.py` | `RuleConfig` and the two presets `V0_FROZEN` and `PRODUCTION` |
| `names.py` | name keys (binomial, autonym, rank) shared by pools, exclusions and wetland |
| `axes.py` | SSURGO site classes and the 12 envelope axes as tri-state expressions; margin; `evaluate_pairs` |
| `applicability.py` | the 13th axis, `source_applicability`: each guide row's precipitation band and habitat qualifier |
| `pools.py` | guide rows -> per (region, guild) pools: vocabulary checks, licence allow-list, region explode + overrides, woody filter, rank-change guard, stratification inheritance, noxious exclusion, infraspecific merge |
| `wetland.py` | NWPL 2022 rating resolution (with the name table) and the wetland-genus assertion |
| `origin.py` | origin decided once per (region, taxon) |
| `labels.py` | per-pick label text, `FIRE_CLAIM_PATTERN`, `assert_no_fire_claims` and `assert_no_fire_field_on_woody_buffer` |
| `ranking.py` | ranking keys and per-cell pick order |
| `engine.py` | `evaluate_cells` (batch) and `candidates_for_cell` (per point) over one shared path; `layer_metadata` |

## Schemas

The five input tables and their required (non-null) columns are the Arrow schemas in `schemas.py`; `conform`
selects exactly those columns, casts them and raises on a missing column or a null in a required one. Columns
nothing reads are not in the schemas: in particular the guide rows carry no quote or source title, so no
verbatim text from a licence-unrecorded source is committed with the fixtures.

- **Species envelope.** `nwpl_route` is required: an envelope that never went through
  `resolve_wetland_ratings` is refused at load instead of passing every wetland check (the prototype's
  red-osier dogwood upland bug).
- **Guide rows.** `guild`, `origin_scope`, `plant_role` and `match_route` are required and checked against
  fixed vocabularies at load (`pools.GUIDE_ROW_VOCABULARIES`): a misspelt `origin_scope` would silently disable
  the range-wide rule, and a misspelt `guild` would report `no_regional_guide` everywhere.
  `habitat_qualifier` is checked against its site mapping when the pools are evaluated (`applicability.py`).
- **Site conditions.** The USDA hardiness zone is not an input: the cold axis reads the record low.

## Engine

`evaluate_cells(site, species, guide_rows, exclusions, config)` returns one row per input cell, in input
order. `candidates_for_cell(cell_site, ...)` takes a one-row site frame and returns every pool taxon per
guild: picks first in rank order with labels, then non-picks with their failed axes. Both call
`region_candidates` -> `evaluate_guild` -> `ranked_picks` -> `with_pick_labels`, so the per-point tool's
top three is the batch top three by construction (the flow test pins it).

Every per-point row carries the guild's `status` and the cell's `scoring_null_reason`, exactly as the batch
reports them. A guild with no candidates at the cell is one status row with every taxon column null: status
`no_regional_guide` when no guide names taxa for it in the region, or status null with the reason when the
cell is withheld. The per-point tool does not check the region or state against a manifest (deferred).

Region is the pool key (v0: the pilot). A region spanning two states raises: presence and noxious lists
are per state. Withheld cells (`scoring_null_reason` set) get every guild column null. A guild with no
guide rows in the region gets `status = no_regional_guide`, `pool_size = 0` and null counts, never 0.

Both seams end by guarding what they return (`engine.guard_served_table`): `assert_no_fire_claims` over every
string and list-of-string column and over `layer_metadata`, then `assert_no_fire_field_on_woody_buffer` over
the labels (ported from the prototype's `build_cell_recommendations.py`). A guide row whose source name makes a
fire claim therefore stops the build instead of being served in a label.

`layer_metadata(config, excluded_sources)` is what every served table carries: the rule set, the pick
definition, the no-fire-claim note, and `plantgeo:excluded_sources`, a JSON object of every source_id the
licence gate dropped with its licence text. `licence_excluded_sources(guide_rows, config)` computes that
mapping for a caller that writes the batch table (`with_layer_metadata`).

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
| `permitted_licences` | none (no gate; v0 had none) | public domain, US Government work, CC0, CC BY | "exclude non-commercial sources from v1" |

Restoration uses the plain flag in `PRODUCTION`: the owner named greenstrip (with CPS 394) and hedgerow
(without) and gave the reason ("it is the Firebreak standard"); restoration is not a firebreak either.

Of the four `in_region_overrides` source ids, only TN 2A changes the curated rows today (TN PM-50 and the OR/WA
guide are already in-region for Bend there); all four stay because they record the owner's decision and survive
re-curation.

`null_restriction_depth_is_unknown` exists because production ships SSURGO restriction depth null. Once
restriction depth is served it must flip to False, and the producer must then emit an unknown restriction status
(not `none_recorded`) for cells it does not serve, so a missing depth stays unknown rather than a pass.

Shared by both presets (owner decisions already in v0): the cold axis compares the site RECORD LOW with
PLANTS minimum temperature; a frost-free shortfall within the region's station-bias magnitude is unknown
("uncertain frost-free fit"), never a fail; in-region ranks ahead of frost-free certainty; neighbour guides
stay, labelled "in-region: no — neighbouring guide".

## Axes

1 pass, 0 fail, null unknown; a missing value is never a silent pass and unknown never fails, it is
counted (`unknown_axis_count`, `count_fully_known`). Thresholds are prototype `GUILD_RULES.md` §5.

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
because a region's guides are the evidence for that region's sites.

## Pools

Only taxa a regional guide names; PLANTS supplies traits and labels, never extra species. Non-natives are
allowed unless the state noxious list or the row's own noxious annotation names them.

**Licence gate.** `permitted_licences` is an allow-list that fails closed: a guide row is kept only when its
`license`, trimmed and case-folded, equals a permitted licence exactly. Nothing is substring-matched ("CC BY"
does not admit "CC BY-NC"), and a null, "unrecorded", "All rights reserved" or any non-commercial text is
dropped at load, before pools, origin votes and stratification see it. Every dropped source_id is listed with
its licence text in `plantgeo:excluded_sources`. `V0_FROZEN` has no gate.

**Fixture licences are an assumption.** v0 captured no licences. `tests/plant_suitability/build_fixtures.py`
marks a source "US Government work" only where the prototype's `regional_lists/SOURCES_*.md` names one US federal
agency (NRCS, BLM, USFS, USFWS, USDA ARS) as the sole publisher; every other source, including co-published,
contractor-named (BLM Boise District ESR plan) and SWCD-distributed ones, stays "unrecorded". Real per-source
licence curation is owed before any production run; until then PRODUCTION serves only the federal guides.

The woody guild (`hedgerow_buffer`) counts only rows whose own `plant_role` is shrub or tree. A
species-rank listing that reaches an infraspecific PLANTS taxon keeps it only when PLANTS records it in the
state. The infraspecific merge keeps one row per binomial; the kept row is named-by-source, then the
species row or autonym, then recorded in the state, and a tie on all three raises (never alphabetical).
Members only fill the kept row's null traits; a fill that inverts a range is rejected. A guide row with no page
is tagged "page n/a" (prototype `regional_pools.page_label`).

## Wetland

NWPL 2022 rating per taxon by route: accepted binomial, PLANTS synonyms, the name table
(*Cornus sericea* -> *Cornus alba*, *Ledum glandulosum* -> *Rhododendron columbianum*), then the names the
guides printed; several hits keep the wettest. Per cell, the rating in the cell's NWPL region is used,
falling back to the other western region. A wetland-genus pool taxon left `not_listed` raises unless it is
documented checked-absent (*Salix pentandra*).

## Origin

Decided once per (region, taxon) across every guide row of the region, all guilds, against the region's state:
one verdict per document (a document that says native anywhere is native; a curator-marked conflicting
"introduced" falls to PLANTS state presence), in-region documents outvote neighbours, a tie falls to PLANTS,
silence falls to PLANTS L48 status. A taxon PLANTS does not record in the state, with no in-region
state-scoped native statement, is flagged "not recorded in <state> (PLANTS)" and ranks like an introduced
taxon. The guide row column `origin_scope` (`regional` / `range_wide`) replaces v0's dead
`RANGE_WIDE_NATIVE_PATTERN`, which matched only rows whose origin was "unstated". The flag wording is rendered
per guild from the preset. Licence-dropped documents do not vote.

## Labels

` | `-joined fields: greenstrip and restoration start with the verbatim
"PLANTS fire-resistant label (not a fire claim; judged against California fires): <Yes|No|conflicting|unknown>";
hedgerow carries no fire field. Then origin, in-region, frost-free fit, "listed as", sources (with
`[neighbour]` / `[medium]`), and in production the optional stratification field.

`FIRE_CLAIM_PATTERN` is the one claim guard: a claim verb (prevent, reduce, slow, stop, suppress, ...)
within six words of a fire noun (fire, wildfire, flame, flammability, ignition), "keeps fire from / away /
out", or a fire-proofing compound (fire-resistant, flame-retardant, fire-slowing, fire-stopping, firewise,
FireSmart). The allow-list holds only the verbatim label prefix and the no-fire-claim disclaimer, both removed
before the search. `assert_no_fire_field_on_woody_buffer` adds the prototype's structural check: no woody-buffer
label mentions "fire" at all, and every greenstrip/restoration label opens with the verbatim prefix.

## Ranking

Leading keys for every guild: in-region, confirmed frost-free before uncertain, fewest unknown axes,
unflagged before flagged. Then per guild (prototype `guild_rules.RANKING_KEYS`), robustness margin, and the
name last. `rank1_tie_count` shows how often the name alone decides rank 1.

## Tests and fixtures

`tests/plant_suitability/` drives both public seams. `tests/fixtures/plant_suitability/` holds 20 cells per
pilot (the named role cells first, then one cell per square of a 5 x 5 longitude/latitude grid, visited
diagonal by diagonal so every row and column of the pilot is sampled), every guide row, the matched species
rows, ID/OR exclusions, the NWPL rows those taxa need, the frozen v0 output rows for the chosen cells, and
`fixture_cells.json` naming each special cell and taxon. They are generated, never hand-edited, by
`tests/plant_suitability/build_fixtures.py` from the frozen prototype.

Production tests use real `PRODUCTION` unless their subject comes from a licence-unrecorded source; those use
`dataclasses.replace(PRODUCTION, permitted_licences=None)` and say why in one line.
