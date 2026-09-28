---
type: Track Spec
title: Plant-suitability serving plane — the frozen v0 prototype taken to a production GIS plane
description: Take the frozen round-4 plant-suitability prototype (three pilots, 7,500 cells) to a production serving plane for WA, OR and ID. It has one Parquet row per 0.01° cell and three guilds of candidates (greenstrip, post-fire restoration, woody buffer / hedgerow), none of them framed as preventing fire. It consumes the committed soil-properties lane rather than re-acquiring soil, adds climate-normal lanes at a usable grain (USDA PHZM 2023, a regional frost-free bias surface, and a precipitation normal only after its terms clear), and adds a governed, re-verifiable curation dataset at region scale. Species tables become one static reference release set. The full ranked list is a live DuckDB join exposed as an agent tool behind verified access. Validation is independent recompute, transcription review, a rule-derived watch list, LANDFIRE EVT, then community observations.
tags: [feature, plant_suitability_plane_20260927, pending]
timestamp: 2026-09-27
resource: ./metadata.json
---

# Plant-suitability serving plane

Chartered 2026-09-27 from the frozen v0 prototype and the verified research brief. Plan:
[`plan.md`](./plan.md). Context: [`../../product.md`](../../product.md),
[`../../tech-stack.md`](../../tech-stack.md). This track is bound by:

- [`docs/layer-lane-standard.md`](../../../docs/layer-lane-standard.md). Its §0, §2, §5–§7, §9–§11
  and §13 are current. §8's cron triad was superseded on 2026-09-02 by executor-registered duties.
- [`layer-lanes.md`](../../code_styleguides/layer-lanes.md) §1a: every lane declares a nature.
  Every lane here is `static_lookup` with a change-event watermark.
- [`federation.md`](../../code_styleguides/federation.md) and [`python.md`](../../code_styleguides/python.md).

Python citations use `path::symbol`, relative to
`services/agri-data-service/src/agri_data_service/` unless a path says otherwise. Line numbers go
stale, so they are left out.

**Nothing in this spec exists as code at HEAD `56467bd4` unless §2 says it does.** Every lane,
module and tool named below as "new" is future work.

---

## 1. Overview

The v0 prototype answers one question per 0.01° cell: *which plants named by a regional seed or
planting guide pass this cell's site envelope, per guild?* It answered it for three
0.5° × 0.5° pilots, 7,500 cells in all. This track answers the same question for every cell of
Washington, Oregon and Idaho, and serves the answer three ways:

1. **Map plane.** One Parquet row per 0.01° cell, carrying per-guild status, pool size, counts,
   the top-3 scientific names, common names and per-pick labels, plus the site values that decided
   them. Reads are public, like every other layer.
2. **Click panel.** The cell's served row: the top 3 per guild with labels.
3. **Agent tool.** The *full* ranked list for a cell is a live DuckDB join of the cell's site
   vector against the region's species tables. It is available only behind **verified access**.

Every served guild shows **candidates**. No served string may claim that a plant prevents, slows
or reduces wildfire (research F0, F4). Restoration is framed as the seeding half of
"pre-emergent herbicide + perennial seeding" (F1, medium confidence).

The plane is built from these parts:

| part | nature | what it is |
|---|---|---|
| `soil-properties` (**existing lane, consumed**) | `static_lookup` | SoilGrids v2.0 on a 0.005° lattice. This plane reads its z9 (0.01°) rung. |
| `climate-normals-hardiness` (new) | `static_lookup` | USDA PHZM 2023, a 30″ grid of the 1991–2020 mean annual extreme minimum |
| `climate-normals-frost-free` (new) | `static_lookup` | reanalysis frost-free days plus a **regional** station-bias surface |
| `climate-normals-precipitation` (new, gated) | `static_lookup` | mean and 20th-percentile annual precipitation. It is built only after decision D-PRISM. |
| `plant-curation-regions` (new) | `static_lookup` | each cell's state, MLRA, NWPL region and curation region |
| `plant-reference` release set (new) | `static_lookup` | six streams in one release: guide rows, guide applicability, species envelope, wetland ratings, exclusions, guild pools |
| `plant-suitability` (new, served) | `static_lookup`, derived | one row per 0.01° cell of WA, OR and ID |

## 2. Background: what exists at HEAD `56467bd4`

### 2.1 The frozen prototype (gitignored, off-production)

`.omc/research/plant-suitability-v0-20260926/FROZEN.md` froze round 4 as v0 on 2026-09-27.

- **Artefacts:**
  - served cells: `join/{boise,corvallis,bend}_cells.parquet`;
  - rules: `join/GUILD_RULES.md`, whose ten modules are listed there;
  - evidence: `join/SUMMARY.md`;
  - curated lists: `regional_lists/steppe.parquet` (747 rows, 689 live) and
    `regional_lists/westside_hedgerow.parquet` (711), documented in `SOURCES_steppe.md` and
    `SOURCES_westside.md`;
  - site inputs: `site_conditions/{soil,climate}/`.
- **Review at freeze:**
  - The data review was APPROVE-WITH-FIXES. Its independent recompute found **0 mismatches over
    7,500 cells**, with 53,051 labels checked.
  - The evidence review was CHANGES-REQUIRED: every curated row verified, but two major
    plausibility defects in the join.
- **Seven open issues** are carried into this track (FROZEN.md §"Open issues"). §5 FR-1 to FR-8
  and FR-10 close them:
  1. **Unstratified in-region list.** 7,009 of 7,011 Boise restoration top-3 slots rest only on the
     BLM Boise District NFESRP Appendix A, which has no bands.
  2. **Range-wide "native".** TN 2A, TN PM-77 and TN PM-50 origins are range-wide. FROZEN estimates
     about 1,000 top-3 slots without a state-presence flag, for example *Prunus pumila* on 210 slots
     at Boise. SUMMARY's `not_recorded_unflagged` rule counts 305 across all pilots after round 4.
     The two numbers are reconciled in FR-5.
  3. **Bend region reading not yet applied.** "Eastern Oregon" means east of the Cascade crest.
  4. **Label format.** In-region ranks ahead of frost-free certainty, but the labels do not document
     it.
  5. **Minor items:** a dead `RANGE_WIDE_NATIVE_PATTERN`; per-pilot TN PM-50 confidence; TN 2A's
     self-contradictory *Cotoneaster integerrimus* origin; the `pick_definition` absence sentence.
  6. **Climate grain.** ERA5-Land frost-free days run **15–44.5 d long**. ERA5 precipitation runs
     about ×1.73 PRISM at Bend (`site_conditions/climate/QUALITY.md`). PRISM terms are not cleared.
  7. **Coverage.** There is no west-side greenstrip guide. Unscorable names number 56 in FROZEN and
     70 in SUMMARY finding 1.
- **What v0 proved:**
  - The three enforced watch rules (`outside_source_band`, `habitat_qualifier_mismatch`,
    `no_source_row_applies`) went from 4,356, 554 and 4,705 slots to **0**.
  - OBL/FACW taxa hold 625 top-3 slots and **0** of them are on not-wet cells. This result rests on
    SSURGO drainage and hydric inputs.
  - The frost-free station biases are Boise 15 d, Corvallis 35 d and Bend 44.5 d. They come from
    three NCEI stations.

### 2.2 The research (`docs/research/pnw-plant-suitability-2026-09-26/report.md`)

- **Verified findings used.** These are the zero-based ids in `GUILD_RULES.md` §1; the report
  quotes them in §9:

  | id | finding | vote |
  |---|---|---|
  | F0 | Sagebrush fuel breaks have been tested only as a class, never by species (Weise et al. 2023) | 3-0, high |
  | F1 | Pre-emergent herbicide plus perennial seeding gave the largest perennial gain (Bennion et al. 2025). The support is thin: 3 studies and at most about 45 months of follow-up. | 3-0, medium |
  | F2 | NRCS CPS 394 accepts only "fire-resistant, non-invasive vegetation" and advises natives | 3-0, high |
  | F3 | USDA PHZM 2023, a 30″ grid, can be the per-cell hardiness key. Its terms are attribution plus a disclaimer, on a single verifier. | 3-0 |
  | F4 | No verified primary evidence shows that any selectable plant reduces spread | synthesis |

- **Refuted, never cite:**
  - fuel breaks performed worst on low-resilience ground (1-2);
  - seeding alone does not raise perennial cover (0-3);
  - CPS 394 names no species or thresholds (0-3);
  - the PLANTS per-state counts 878/942/804 (0-3);
  - the GBIF-vs-PLANTS magnitudes (0-3);
  - the other report §7 rows.
- **Limits:**
  - USDA PLANTS characteristics cover about **2,186 taxa**. They give one national value, expert
    range limits, and are conservative (3-0). PLANTS' own licence terms were **not checked**.
  - PRISM terms were **not checked**, and neither were the WA and ID noxious-list editions.
- **Data model.** Report §2 proposes small tables plus a query-time join. The 6.86 M-row arithmetic
  for seven guilds is superseded by the owner's one-row-per-cell decision (§3.1).

### 2.3 The soil plane at HEAD

- **`soil-properties`** is committed (`56467bd4`):
  - schema: `warehouse/schemas/soil_properties.py::SOIL_PROPERTIES_SCHEMA`;
  - lane package: `pipeline/direct/soil_properties/`;
  - registration: `pipeline/parquet/lane_registry.py::_REFERENCE_DATA_REGISTRATIONS`;
  - agent reader: `agent/soil_properties.py` and `agent/tools.py::soil_properties_at_point`;
  - TS reader: `src/lib/server/services/soilgrids.ts`.
- **What the lane is** (CONTRACT C1): ten SoilGrids properties at three depths on a **0.005°
  origin-keyed lattice** (3.92 M cells), with z9, z5 and z0 derived as **means**.
- **It has never been written.** Its `AGENTS.md` says "Built (WS-A A1), not yet run". No publish is
  recorded in `conductor/RUNBOOK.md`.
- **Join rule** (CONTRACT C11): to an origin-keyed lattice of size `s`,
  `key = floor(centre / s) * s`. The z9 rung is already 0.01° origins.
- **SSURGO survey properties are deferred** (`.omc/soil-data-plane-20260927/DESIGN.md` D3 and §4;
  follow-up F1). The `soil-survey` lane:
  - is an offline capture CLI plus a live pull that raises `SsurgoPullRetiredError`
    (`pipeline/direct/soil_survey/AGENTS.md`);
  - has a Parquet schema (`warehouse/schemas/soil_survey.py::SOIL_SURVEY_SCHEMA`) that carries
    only `drainage_class`, `hydric_rating` and `land_capability_class` per **delineation polygon**,
    with **no** horizon properties (EC, CaCO₃, restriction depth, horizon pH or texture);
  - is not published.

### 2.4 Climate at HEAD

- **No lane provides normals.** A grep for `phzm|hardiness|prism|frost-free|normals` under
  `services/agri-data-service/src` and `src/` finds only trait fields in
  `warehouse/botanical_species_profiles/contract.py` and `models/profiles.py`.
- **The legacy `climate-field-*` streams** are a 1° NASA POWER lattice of 397 cells.
- **The config-driven track** (`config_driven_ingestion_20260926`) will move meteorology to
  Open-Meteo ERA5 on the **0.25°** `analysis-0p25` lattice. That is coarser than v0's 0.1°
  ERA5-Land, which already ran frost-free 15–44.5 d long. Its runner, `lanes/*.toml` and
  `pipeline/lanes/` **do not exist at HEAD**.

### 2.5 Serving, rungs and access at HEAD

- **Rungs.** `warehouse/parquet/tiers.py::TIER_RESOLUTION_DEGREES` is `{9: 0.01, 5: 0.2, 0: 5.0}`.
  The z13 base is lane-declared. **0.05° is not a rung.**
- **Derivation guard.** `tiers.py::MAX_DERIVATION_ROWS = 5_000_000` is a per-call guard. Latitude
  banding exists:
  - `pipeline/parquet/derivation.py::register_latitude_banding` and `LatitudeBanding.of_height`;
  - band edges are multiples of 0.2°;
  - only `BAND_SAFE_AGGREGATES = {sum, min, max, first, all, any, null}` are admitted.
- **Serving files exist:**
  - `src/lib/server/services/parquet-trpc-readers.ts`, `parquet-slider-capabilities.ts`,
    `parquet-plane-client.ts`;
  - `src/types/time-slider.ts` (`SNAPSHOT_SURFACE_LAYER_NAMES`);
  - `src/lib/map/layer-registry.ts`, `layer-legends.ts`;
  - `src/components/map/LayerManager.tsx`;
  - `src/lib/server/trpc/routers/environmental.ts`.
  - `layer-lane-standard.md` §9.1 cites `environmental-read-model.test.ts` and
    `pre-aggregation-catalogue.test.ts`. **Neither exists**; only
    `src/__tests__/services/parquet-slider-capabilities.test.ts` does.
- **Census pin.** `tests/parquet_ops/test_coverage_census.py::EXPECTED_REGISTERED_CENSUS_LANES = 35`
  pins the census count. Every new registered lane changes it.
- **AI access.**
  - `src/app/api/ai/regional-intelligence/route.ts::POST` requires a session and reserves quota
    through `src/lib/server/security/regional-intelligence-access.ts::reserveRegionalIntelligenceUsage`.
  - `::resolveEntitlementPolicy` resolves **every** signed-in user to `signed_in` (20 requests per
    hour).
  - `users.emailVerified` exists (`src/lib/server/db/schema.ts`), and
    `src/lib/server/trpc/routers/teams/shared.ts::requireVerifiedIdentity` requires it for
    invitations.
  - **There is no verified-access gate, no aevani.com affiliate rule, and no CC BY-NC filter on
    agent output.**
- **Where agri tools reach the model.** They go through the served catalogue
  (`routes/agent_tools.py::call_agent_tool`) and are declared to TS by
  `src/lib/server/services/regional-evidence-tools.ts`.

### 2.6 Related tracks

The herbaria and occurrence surfaces this plane supersedes, as a "what grows here" answer, exist at
HEAD:

- the specimen release set `warehouse/schemas/botanical_occurrences.py`, which includes the stream
  `botanical-cell-taxon-summary`;
- `planes/botanical_occurrences.py`;
- `src/components/map/layers/BotanicalRichnessLayer.tsx`, `BotanicalOccurrencesLayer.tsx` and
  `BotanicalCollectionEffortLayer.tsx`.

§12 gives each related track's relation.

## 3. Decisions

### 3.1 Owner decisions (SETTLED 2026-09-27; encode, do not re-open)

| # | decision |
|---|---|
| OD-1 | **A GIS serving plane with ONE row per cell.** Each guild carries a count, pool size, status, top-3 scientific names, common names and per-pick labels. |
| OD-2 | **The full ranked list is a live DuckDB join** of the cell's site vector against the species tables, exposed as an **agent tool behind VERIFIED access**. The platform is open and non-commercial and AI access is gated. **aevani.com affiliates get access by default. CC BY-NC records never appear in affiliate-facing output.** |
| OD-3 | **Guilds v1:** `greenstrip`, `post_fire_restoration`, and `hedgerow_buffer` (displayed as "woody buffer / hedgerow candidates": windbreak and pollinator value, **no fire claim**). Defensible-space landscaping is **deferred**. |
| OD-4 | **Pools are species named in regional seed or planting guides only.** PLANTS never adds a species. |
| OD-5 | **Non-natives are allowed** unless a state lists them noxious. They are flagged "introduced — native alternative preferred (CPS 394)". |
| OD-6 | **USDA PLANTS fire resistance is a LABEL only**, never a filter or a rank key. |
| OD-7 | **The NWPL 2022 wetland-indicator axis** is in. |
| OD-8 | **A frost-free shortfall within the measured station bias is "uncertain", not a fail.** |
| OD-9 | **Neighbour-region guide rows stay**, flagged, and rank below in-region rows. |
| OD-10 | **"Eastern Oregon" in a guide means east of the Cascade crest**, so it is in-region for Bend. |
| OD-11 | **In-region ranks ahead of frost-free certainty.** |
| OD-12 | **Framing:** every guild is "candidates", never a claim that a plant prevents or reduces wildfire (F0, F4). Restoration is framed as herbicide plus perennial seeding (F1, medium). |
| OD-13 | **The suitability plane supersedes the herbaria data** as the platform's "what grows here" answer. |

**Platform rules** (project memory, not re-derived):

- Postgres keeps only community features. Every other plane is Parquet read by DuckDB.
- `MAX_DERIVATION_ROWS` is a per-call guard. Derive large lane-days in latitude bands.
- Never run PlantGeo locally.
- Production mutations wait for an explicit owner go.
- Authors never verify their own work.
- One test, build and ruff sweep at the end.
- Push small and often: one step per push, and a closure review under 500 lines.

### 3.2 Decided by this spec (each with its reversal cost)

| # | decision | why | reversal cost |
|---|---|---|---|
| S1 | **Base grain z13 = 0.01° cell origins.** Coarse rungs are z9 (0.01), z5 (0.2) and z0 (5.0). | v0 validated this grain. PHZM's 30″ (~0.0083°) resolves inside a cell. `soil-properties` z9 is exactly this lattice. **0.05° is not a platform rung**, so the report's "start the pyramid at 0.05°" cannot be a base. | Moving to 0.005° is a rebuild at 4× the compute. Rows stay under the guard (≤ 3.92 M wide), and the aggregates are already band-safe. |
| S2 | **Wide rows, WA/OR/ID only.** About 0.76 M rows is planning arithmetic: 656,076 km² of total state area ÷ ~0.868 km² per cell at 45.5 °N. The build reports the real count. The envelope ceiling is 980,000 (1,400 × 700). | OD-1. It is under `MAX_DERIVATION_ROWS` in one call, so **no latitude banding is needed for the rungs**. | Adding states is a curation cost, not a schema change. |
| S3 | **Coarse-rung aggregates are band-safe only.** Counts use `max`. `<guild>_scored_any` uses `any`. `<guild>_no_guide_all` uses `all`. Lists and labels use `null`. Constants use `first` under a constancy contract. | One `how` per column across rungs (EVT tripwire). Banding can be switched on by one `register_latitude_banding` line if S1 moves to 0.005°. | Low. |
| S4 | **The build runs in 0.2° latitude bands.** That is 35 bands of ≤ 28,000 cells. | Per-query RAM is about 1 GB (memory `plantgeo-ram-is-per-query-not-cache`). About 11 M (cell, taxon) evaluations per band is an estimate at ≤ 400 pool taxa; v0's pools were 0–112 per guild. | Band height is one constant. |
| S5 | **One rule engine, pure, in `warehouse/plant_suitability/`** (new). Both the batch build and the agent tool import it. | `warehouse` may import only `foundation` (`tests/test_layer_import_contract.py::LAYER_FORBIDDEN_IMPORTS`). The precedent is `warehouse/botanical_species_profiles/`. The `method/` layer is gone. | Moving the package is a refactor of the import sites only. |
| S6 | **New lanes are `pipeline/direct/<lane>/` packages that mirror `soil_properties`.** They are one-off operator publications with capture, prepare, publish, verify, maintain and retract verbs, and `maintain` reports "republish". | The config-driven runner, TOML and `pipeline/lanes/` do not exist at HEAD. `soil_properties` is the newest reviewed static precedent. | Each lane joins the config-driven Phase 4 cut-over as a `static_lookup` (its CQ-8 `--republish-current`). The rule engine sits in `warehouse/`, so no strategy ever imports `pipeline/direct/**`. |
| S7 | **The species tables are ONE release set, `plant-reference`, with six streams:** `plant-guide-rows`, `plant-guide-applicability`, `plant-species-envelope`, `plant-wetland-ratings`, `plant-exclusions`, `plant-guild-pools`. One release id binds all six. | This is the `warehouse/schemas/botanical_occurrences.py` precedent: seven grains, one release set, sparse, "an absent row is the absence of evidence". The pools are derived from the other five and must agree with them byte for byte. | Splitting later needs a release-id migration. |
| S8 | **Soil comes from `soil-properties` z9** (0.01° mean of ≤ 4 valid 0.005° cells). pH uses the thickness-weighted 0–30 cm formula (CONTRACT C5.2). Texture uses normalised SoilGrids fractions and the USDA triangle (C5.2). | "Consume the lane, do not re-acquire soil." This track becomes the **first consumer of a soil-properties coarse rung**, which CONTRACT C1 says nobody reads yet, so it needs a C1 amendment by the soil orchestrator (§11). | If the soil track objects, read z13 and aggregate the four cells in the build. |
| S9 | **The access gate lives at the TS entitlement seam.** `resolveEntitlementPolicy` gains a verified-access result, and the tool is withheld from the model's catalogue unless the requester is verified. | The seam's own comment names it as the place the auth/org workstream replaces. | One predicate. |
| S10 | **Each normals lane is its own lane per source** (hardiness, frost-free, precipitation). | Different providers, terms and watermarks (`federation.md`: one source Protocol per layer). | Merging them later is a release-id change. |
| S11 | **The normals lanes are inputs, not map layers, in v1.** The served row carries the site values that decided each cell. | "Persist everything we serve" (memory, 2026-08-03). Serving is optional. Each lane's `docs/lanes/*.md` records `serving: internal` with the reason, as a declared deviation from standard §13. | Low: a reader added later. |
| S12 | **The plane is not published until a cleared precipitation normal exists.** | Two axes and every banded guide row (`source_applicability`) read precipitation. Serving them `unknown` would bring back the round-3 `outside_source_band` defect class (4,356 slots). | One gate. |

### 3.3 Open questions for ONE grill round (defaults stand unless the owner overrides)

Ranked by blast radius, then reversal cost:

| # | question | default | alternative | reversal cost |
|---|---|---|---|---|
| Q1 | **SSURGO survey properties.** The soil lane cannot supply EC, CaCO₃, restriction depth, drainage or hydric per cell, or survey texture and pH. Six v0 axes (salinity, calcareous, root_depth, anaerobic, drought, wetland_indicator) and three habitat qualifiers read them. | **Hard dependency.** `plant-suitability` is published only after the soil track's SSURGO follow-up (DESIGN §4, F1) publishes per-cell survey properties. Everything else ships dark. | Serve an interim v1 with those axes `unknown` and two fail-closed rules: OBL/FACW taxa fail on unknown wetness, and wet or saline habitat qualifiers fail on unknown. | Interim: one rebuild and a label change when the survey lands. Hard dependency: calendar delay only. |
| Q2 | **Cold-axis statistic.** PHZM gives the mean annual extreme minimum. v0's primary was the record minimum, from ERA5-Land. | **PHZM 2023 mean annual extreme minimum** against PLANTS `Temperature, Minimum`. This is v0's `cold_uses_mean_annual_extreme_minimum` sensitivity run, which is strictly more lenient. The label says "hardiness basis: USDA PHZM 2023". | Keep the record minimum from the frost-free base field (0.1° reanalysis, which smooths extremes). | One axis definition, then a rebuild. |
| Q3 | **Frost-free base field and spend.** | **Open-Meteo ERA5-Land 0.1° daily minimum, 1991–2020.** This is v0's source, so its station comparison and oracle hold. The cost is ≈ **5.9 M weighted calls** (≈ 7,570 boxes × max(1, 10,958 / 14) = 783), which is more than one month's paid 5,000,000 cap (config-driven WQ-4). It is metered, and split across ≥ 2 months. | (b) CDS ERA5-Land: no Open-Meteo spend, but volume and terms were **not checked**. (c) A 0.25° transform over `meteorology-*` after config-driven G7: no new spend, but coarser, and it waits for G7. | Rebuild the frost-free lane. |
| Q4 | **Verified access for non-affiliates.** | **Verified** = `emailVerified` AND (email domain `aevani.com` OR role `expert`/`admin` from `src/lib/server/trpc/init.ts`). There is no self-serve request flow in v1. | A request-and-grant row reviewed by an admin. | One predicate. |

## 4. Architecture

### 4.1 Region model

A **curation region** is a (state, MLRA) pair. A guide document declares its geography once. For
each region, the curation assigns `in_region` or `neighbour`, with a basis quote and a confidence.
"Eastern Oregon" resolves to the Oregon MLRAs east of the Cascade crest (OD-10). The MLRA list is
the output of FR-9, not a list asserted here. v0 found MLRAs 6 and 10 hold 2,353 of Bend's 2,401
cells.

- **Neighbour relations are curated per (document, region), never inferred from distance.** The
  west-side regions are **not** neighbours of the Intermountain greenstrip guide (TN PM-16), so the
  west-side greenstrip stays `no_regional_guide` (FR-10).
- **The NWPL region per cell** comes from `plant-curation-regions`. Until it is checked against the
  USACE boundary map, its basis label reads "MLRA proxy (not checked against the USACE boundary
  map)". This is v0's caveat, `GUILD_RULES.md` §5.1.

### 4.2 Lanes and streams

| slug | grain | key | watermark (change event) | notes |
|---|---|---|---|---|
| `climate-normals-hardiness` | 0.01° z13, centre-sampled from the 30″ grid (DI) | cell origin | PHZM file pins (Last-Modified or ETag) | `mean_annual_extreme_min_c`, `hardiness_zone`; attribution and disclaimer text in the metadata |
| `climate-normals-frost-free` | 0.01° z13 | cell origin | base-field release id + bias-station inventory digest | `frost_free_days_base`, `frost_free_bias_days`, `frost_free_bias_basis` (region id, station count n, spread) |
| `climate-normals-precipitation` | 0.01° z13 | cell origin | the chosen source's pins | `precip_mean_mm`, `precip_p20_mm`; **built only after D-PRISM** |
| `plant-curation-regions` | 0.01° z13 | cell origin | boundary-source pins | `state`, `mlra`, `nwpl_region` (+ basis), `curation_region_id` |
| `plant-reference` / `plant-guide-rows` | one row per (document, printed line, taxon) | `row_id` | curation release id | quote, page, `quote_check`, `origin_scope`, bands, qualifier, `superseded_by`, `licence`, `scorable` (+ reason) |
| `plant-reference` / `plant-guide-applicability` | one row per (guide row, region) | (`row_id`, `curation_region_id`) | same | `in_region`, basis quote, confidence; split from guide rows because one row applies to many regions |
| `plant-reference` / `plant-species-envelope` | one row per PLANTS taxon | `taxon_key` = `usda-plants:<symbol>` | PLANTS download date | trait tiers (`GUILD_RULES.md` §3), infraspecific merge record (C1), raw Fire Resistant values |
| `plant-reference` / `plant-wetland-ratings` | one row per (taxon, NWPL region) | (`taxon_key`, region) | NWPL 2022 edition | the rating, the route (incl. C3 resolutions), checked-absent |
| `plant-reference` / `plant-exclusions` | one row per (jurisdiction, list, edition, entry) | entry id | list edition | matched `taxon_key` or null + `review_hint` |
| `plant-reference` / `plant-guild-pools` | one row per (curation region, guild, taxon) | (region, guild, `taxon_key`) | derived | origin decision (C4 + FR-2), carrying rows, fire-resistance label (greenstrip and restoration only), noxious removal |
| `plant-suitability` | 0.01° z13, one row per cell | cell origin | the max over the input release ids + `rules_version` | §4.3 |

### 4.3 The served row (`plant-suitability`)

- **Keys and region:** `cell_longitude`, `cell_latitude` (SW origin, multiples of 0.01),
  `state`, `curation_region_id`, `mlra`, `nwpl_region`, `nwpl_region_basis`.
- **Scoring status:**
  - `scoring_null_reason`: null when scored; `no_soil_estimate`; `survey_not_scorable` (once survey
    properties land: a miscellaneous area or NOTCOM, v0 D1);
  - `site_data_quality`, `site_missing_inputs` (list).
- **Site vector**, which is what the tool joins:
  - climate: `mean_annual_extreme_min_c`, `hardiness_zone`, `frost_free_days_base`,
    `frost_free_bias_days`, `precip_mean_mm`, `precip_p20_mm`;
  - soil: `ph_0_30cm`, `texture_group`, each with a `*_basis`;
  - survey (after Q1): `drainage_class`, `hydric`, `restriction_depth_cm`, `ec_ds_per_m`,
    `caco3_pct`, `wetness_class`, `drainage_regime`.
- **Per guild `g`:**
  - `g_status` ∈ {`scored`, `no_regional_guide`, `site_not_scored`}, `g_pool_size`;
  - counts: `g_count`, `g_count_fully_known`, `g_count_in_region`,
    `g_count_uncertain_frost_free`, `g_count_introduced_flagged`;
  - top 3: `g_top3`, `g_top3_common_names`, `g_top3_labels` (all `list<string>`, because a label
    may contain `;`, v0 C9), `g_unknown_axes_top3`;
  - ties: `g_rank1_tie_count`, `g_rank1_tied_names`;
  - coarse helpers: `g_scored_any`, `g_no_guide_all`.
- **Provenance:** `input_release_ids` (list), `rules_version`, `release_day` (the version stamp).
- **File metadata** (`plantgeo:*` keys, as v0 R10): `layer`, `pick_definition` (with the FR-4
  sentence), `no_fire_claim`, `guilds` (JSON), `research_ids`, `precipitation_status`,
  `labels_format` (with the FR-4 sentence), `rules`, `attributions` (PHZM disclaimer, SoilGrids
  CC-BY, NWPL, PLANTS, NCEI).
  - Whether the Parquet writer carries custom key-value metadata is **not verified**. If it does
    not, FR-17 publishes a one-row `plant-suitability-metadata` companion stream.
- **Geometry is implicit.** Cells are lattice origins, and renderers draw squares from origin plus
  pitch. There is **no WKB column** (memory `plantgeo-geometry-lanes-break-the-size-estimate`).

### 4.4 Rule engine

`warehouse/plant_suitability/` (new) ports `GUILD_RULES.md` as the **rules of record v1**, which
Phase 1 freezes in `docs/lanes/plant-suitability.md`:

- the 13 axes, pass / fail / unknown, where a missing value is never a silent pass;
- `source_applicability` (C7);
- the frost-free fail-soft band, taken per cell from `frost_free_bias_days` (OD-8);
- the leading rank keys `in_region_rank`, `frost_free_uncertain_rank`, `unknown_axis_count`,
  `introduced_flag_rank` (OD-9, OD-11);
- per-guild keys and `robustness_margin`;
- the label grammar (C9), with **no fire field on `hedgerow_buffer`**.

It is pure: frames in, frames out, no I/O. The build and the tool call the same function.

### 4.5 Refresh and horizon (layer-lane-standard, per lane)

| duty | how it is met |
|---|---|
| Horizon (§2) | `static_lookup`: one snapshot at the watermark, or nothing. `HistoryCapability` is declared per lane with its floor basis. |
| Forward refresh (§8) | Each lane's `maintain` verb re-probes its pins or input release ids and reports `pinned` or `republish` (the soil-properties pattern). A republish is never an owed day. |
| Gap detection and governed absences (§5–§7) | For a static lane the only gap is "never written", which the availability index shows. Cells without an input value get **no row** in the input lanes (soil-properties' row rule) and a `scoring_null_reason` in `plant-suitability`. |
| Executor duties | None. The lanes are one-off operator publications (soil-properties AGENTS.md: "No cron, no lane spec and no TOML"). `execution/gap_repair_contract.py` gets a `static_lookup` excuse per lane, as for `soil-properties`. This deviation from §8 is recorded per lane. |
| Triggers for `plant-suitability` | Any change in `soil-properties`, a normals lane, `plant-curation-regions`, `plant-reference`, or `rules_version`. `maintain` compares the recorded `input_release_ids` and reports `republish`. |

### 4.6 Serving and access

- **Map plane** (public). The TS reader plus tRPC use the `soil-properties` reader pattern
  (`src/lib/server/services/soilgrids.ts`).
  - The slider capability is a **declared snapshot** (`SNAPSHOT_SURFACE_LAYER_NAMES`, standard
    §9.1 way 2) with a vintage badge. It is not forecastable.
  - The legend has a per-guild count ramp, a hatched `no_regional_guide` class and grey
    `site_not_scored`.
  - At z9 to z12 the rungs carry `max` counts. The labels come from z13 on click.
- **Click panel** (public): the cell's served top 3 per guild with labels, and the guild framing
  text.
- **Agent tool `plant_candidates_at_point`** (new, agri, `agent/plant_candidates.py`, registered in
  `agent/tools.py`, declared in `regional-evidence-tools.ts`):
  1. Take a point and a radius.
  2. Select the nearest served cell (the C2 selection, 0.01 lattice).
  3. Build its site vector.
  4. Run a live DuckDB join against that region's `plant-guild-pools`, `plant-species-envelope`
     and `plant-wetland-ratings`.
  5. Return the full ranked list per guild, with axis verdicts, labels, `distance_m` and the
     cell's own `release_day`.
  - Refusals use the C5.6 vocabulary.
  - **Withheld unless verified (Q4).** For an aevani.com affiliate, every row whose document
    `licence` is non-commercial is excluded.
- **Kill switches**, both default off and parsed by the CONTRACT C2 rule: only the trimmed exact
  value `true` is on.
  - `PLANT_SUITABILITY_READS_ENABLED` covers the web and agri reads.
  - `PLANT_CANDIDATES_TOOL_ENABLED` covers the tool declaration.
  - With both off, behaviour is byte for byte unchanged.

### 4.7 Validation

| layer | reviewer | pass rule |
|---|---|---|
| Independent recompute | a separate agent that never saw the build code | recompute sampled cells, stratified by band, region and guild, from the published inputs; **0 mismatches** |
| Evidence and transcription | a separate agent per curation batch | every row's quote re-found on its cited page; applicability bases re-read; superseded rows present |
| Rule watch list (v0 C11) | computed by the build and read by the reviewer | the three enforced rules read **0**; the others are reported with their top taxa |
| LANDFIRE EVT | a validation reviewer after `vegetation_type_landfire_evt_20260918` lands (its package is **absent** at HEAD) | agreement between guild lifeform and EVT physiognomy per region, as a report, never a filter |
| Community observations | report §5.4, after the community layer exists | presence and absence **test** the plane; they never train it. An excluded taxon observed where the plane shows candidates is an early-warning signal. |

## 5. Functional requirements

Priority: **P0** blocks publish; **P1** blocks serving; **P2** follows.

| id | requirement | acceptance | pri |
|---|---|---|---|
| FR-1 | **Band inheritance** (issue 1). A taxon on an unbanded, unqualified in-region row inherits the band union and qualifier set of its other rows in the same guild, labelled "band inherited from <short name>". With no other rows it applies everywhere, labelled "no band stated". (DI.) | The Phase-1 oracle recompute on the three pilots reports the Boise restoration slots resting only on unbanded rows (was 7,009 of 7,011). *Leymus triticoides* picks only where `saline_alkali_or_poor_drainage` applies. The three enforced watch rules read 0. | P0 |
| FR-2 | **State presence over range-wide origin** (issues 2 and 5). The curation column `origin_scope` ∈ {`state`, `region`, `range_wide`, `unstated`} replaces `RANGE_WIDE_NATIVE_PATTERN`. A `range_wide` native statement never suppresses "not recorded in <state> (PLANTS)". | `not_recorded_unflagged` reads 0 on the pilots. *Prunus pumila* at Boise is flagged. No code or data references the pattern. | P0 |
| FR-3 | **Bend region reading** (issue 3, OD-10). TN 2A, TN PM-50 and the OR/WA guide are in-region for the east-of-crest Oregon regions. TN PM-50 confidence is recorded per region (`medium` only where the reading is inferred). | The oracle shows Bend picks from those documents labelled `in-region: yes`. The per-region confidence is in `plant-guide-applicability`. | P0 |
| FR-4 | **Labels and pick definition** (issues 4 and 5). `labels_format` contains "uncertain picks rank below confirmed picks with the same in-region status". `pick_definition` contains "absence from a cell is not evidence of unsuitability". TN 2A's *Cotoneaster integerrimus* conflict is marked `nativity_conflict` and settled by the C4 rule. | Build assertions on the metadata strings; the curation row carries the conflict. | P0 |
| FR-5 | **Coverage reconciliation** (issue 7, part). Reconcile the 56 (FROZEN) and 70 (SUMMARY) unscorable-name counts. Every unscorable name is a `plant-guide-rows` row with `scorable = false` and a reason. Reconcile the ~1,000 (FROZEN) and 305 (SUMMARY) unflagged-slot counts the same way. | A reconciliation note in `evidence/phase1.md`; unscorable rows are queryable. | P1 |
| FR-6 | **Hardiness normals** (issue 6, part). Publish `climate-normals-hardiness` from USDA PHZM 2023 30″, with the attribution and disclaimer text captured verbatim at P0 and carried in the served metadata. | The ZIP cross-check matches the published zone at the three v0 ZIPs (83702 7a, 97330 8b, 97701 6b). The attribution string is present in the served file metadata. | P0 |
| FR-7 | **Frost-free normals with a regional bias surface** (issue 6, part; OD-8). The base field comes from Q3. Bias per station = base cell − NCEI 1991–2020 `ANN-TMIN-PRBGSL-T32FP50`, over **every** qualifying station in WA/OR/ID. The surface is the per-MLRA median when n ≥ 3, else the state median. `frost_free_bias_basis` carries the region, n and IQR. The fail-soft band per cell is \|bias\|. | The three v0 stations reproduce 15 / 35 / 44.5 d within the base-field release. The station count and the per-region table are in `evidence/phase2.md`. No cell uses a three-station constant. | P0 |
| FR-8 | **Decision D-PRISM, then precipitation normals.** Record PRISM's terms verbatim and a serve / do-not-serve decision. If not cleared, evaluate alternatives (NOAA nClimGrid monthly; Daymet v4; WorldClim 2.1, which is 1970–2000 and so the wrong period). Each alternative's terms are **not checked** today, and each is compared with NCEI station normals as v0 did. | The decision record with the owner's go. Station comparison table. `plant-suitability` stays unpublished until this closes (S12). | P0 |
| FR-9 | **Curation regions lane.** Every 0.01° cell of WA/OR/ID gets `state`, `mlra`, `nwpl_region` (+ basis) and `curation_region_id`. The MLRA list is enumerated from the source and the count reported. | Row count equals the WA/OR/ID cell count the build reports. v0's pilot MLRA shares are reproduced (Corvallis 2 89%; Bend 6 60%, 10 37%; Boise 11 47%, 10 33%, 43B 20%). | P0 |
| FR-10 | **Region coverage matrix and honest gaps** (issue 7). Every (curation region × guild) lists its in-region documents, neighbour documents, or none. WA regions are assessed first from each transcribed document's stated geography (the OR/WA guide covers WA), then by a **bounded** WA source search. A gap is served as `g_status = no_regional_guide`, with the searched-source list in the metadata, **never filled**. The west-side greenstrip gap is explicit. | The matrix covers every region the FR-9 census lists. No west-side cell has a greenstrip pool. The search log is in evidence. | P1 |
| FR-11 | **Governed curation dataset.** Curated rows are reviewed text (one JSONL per document). Raw documents are stored content-addressed (sha256) in the object store. The build re-runs the v0 quote check (exact, or ordered tokens within 2 × tokens + 10) and **refuses** on any failure. Superseded rows stay with `superseded_by`. Every document carries `licence` and `redistribution`. `verify` re-checks every quote against the stored bytes. | `verify` reports 0 failures on the ported 1,458 v0 rows (747 steppe + 711 westside). A deliberately broken quote fixture fails the build. | P0 |
| FR-12 | **Species envelope** from PLANTS characteristics. Trait tiers follow `GUILD_RULES.md` §3; the infraspecific merge follows C1 (no alphabetical kept row; a tie fails the build). `taxon_key` = `usda-plants:<symbol>`, and `authority_version` = the download date. Genus-only names are never expanded (OD-4). | Parity with v0's `species_traits.py` output for every pilot pool taxon. The inverted-range assertion holds. | P0 |
| FR-13 | **Wetland ratings** (OD-7): NWPL 2022, with the C3 name resolutions, the checked-absent list and the wetland-genus assertion. | *Cornus sericea* resolves to *C. alba* (FACW). The build fails on an unrated wetland-genus pool taxon. | P0 |
| FR-14 | **Exclusions** (OD-5): state noxious lists by edition: OR 2025 (a PDF only, report §6), plus WA and ID editions to be sourced (**not checked**). The v0 `exclusion_match.py` rules apply. | Every list row is matched or carries a `review_hint`. The edition and source URL are per row. | P0 |
| FR-15 | **Guild pools** per (region, guild): the C4 origin decision with FR-2, in-region flags (OD-9), carrying rows, the fire-resistance label on greenstrip and restoration only (OD-6), and noxious removal. | Parity with v0's `guild_candidates.parquet` for the three pilots under the v1 rules. | P0 |
| FR-16 | **Rule-engine parity.** The production engine reproduces the Phase-1 oracle on the 7,500 pilot cells. | **0 mismatches** on every per-guild column, by a separate recompute agent. | P0 |
| FR-17 | **`plant-suitability` lane**: the §4.3 row, the S1–S4 grain and rungs, and the §4.5 refresh. The metadata travels in the file or a companion stream. | Row count reported. Rungs z13, z9, z5 and z0 are published. A test proves `derive_tier` behaviour at a 0.01° base equal to the z9 pitch (identity, or refusal with the 0.005° fallback). `maintain` reports `pinned`. | P0 |
| FR-18 | **Map serving**: reader, tRPC procedure, snapshot capability, registry entry, legend, click panel and kill switch. The conformance test uses hand-spelled lists (§9). | The capability test pins the stream by a hand-spelled name. Browser evidence comes from a standalone Playwright config against the deployed build, never the repo config. | P1 |
| FR-19 | **Agent tool** as in §4.6: verified access, the affiliate NC exclusion, distances, and the `release_day` of the answering cell (standard §11). | Tests: an unverified requester never sees the tool; an affiliate never receives an NC row; the result carries `distance_m`. | P1 |
| FR-20 | **Framing guard** (OD-12). A build, reader and tool assertion: no served string, label, prompt fragment or fixture text matches the fire-effect pattern (v0 `FIRE_CLAIM`). Restoration framing cites F1's scope, including "F1 does not cover forested burns or the west side". | Pattern tests over every served string, and over the tool's system-prompt fragment. | P0 |
| FR-21 | **Validation** as in §4.7. | Each row of §4.7 recorded with its verdict. | P1 (EVT and community: P2) |
| FR-22 | **Supersession record** (OD-13). This track records the herbaria serving path as superseded. Retirement of any served herbaria or occurrence surface is a separate owner go under standard §13.1, not done here. | `metadata.json` `supersedes` and the §12 table. | P1 |

## 6. Non-functional requirements

| id | class | requirement |
|---|---|---|
| NFR-1 | Performance | Map reads are point or bbox reads on one static partition. The tool's live join covers one cell × ≤ ~400 pool taxa and runs under the site brief's 3,000 ms deadline (CONTRACT C2). The p95 is **measured at Phase 5, not assumed**. |
| NFR-2 | Resource | The build runs in 0.2° bands (S4). Each band's peak RSS is recorded. No derive call exceeds `MAX_DERIVATION_ROWS`. |
| NFR-3 | Security | Verified access for the tool (Q4). Coordinates are validated before any read (C5.1's coverage rule). The flags are parsed by one rule. No PII enters the plane. Secrets (`OBJECT_STORE_*`, DSNs, keys) are never printed. |
| NFR-4 | Licensing | The served plane contains **zero** non-commercial records, enforced by a build assertion over the `licence` column. Affiliate tool output excludes NC rows. PHZM attribution and disclaimer, SoilGrids CC-BY and NCEI/NWPL/PLANTS credits are in the served metadata. |
| NFR-5 | Determinism | The same input release ids and `rules_version` give the same bytes (sha256 recorded). |
| NFR-6 | Honesty | Every value carries its basis: model estimate (SoilGrids), normal (PHZM, NCEI-corrected reanalysis), or curated source. **Never "measurement"** for a model estimate (CONTRACT C9). |
| NFR-7 | Process | Authors run no tests. One monitor sweep per push (Python: `scripts/check.py`, ruff, mypy, pytest; web when `src/**` changes: `npm test`, typecheck, build, `check:data-boundary`). Each phase boundary gets an adversarial review with a recorded verdict. |

## 7. User stories

1. **Restoration planner.** *As* a planner after a rangeland fire, *I want* the candidate seeding
   species a regional guide names for this cell, *so that* I can start a seed-mix conversation.
   - *Given* a burned sagebrush cell near Boise, *when* I click it, *then* I see up to three
     post-fire restoration candidates with origin, in-region and frost-free labels.
   - The guild text says the candidates are for the seeding half of herbicide plus perennial
     seeding (F1, medium). Nothing says the plants reduce fire.
2. **West-side landowner.** *As* a Corvallis landowner, *I want* greenstrip candidates, *so that* I
   can plan a fuel break.
   - *Given* any west-of-the-Cascades cell, *when* I open greenstrip, *then* I see "no regional
     guide for this guild here" and the sources that were searched, never an east-side list.
3. **Verified affiliate.** *As* an aevani.com affiliate, *I want* the full ranked list and why each
   plant passes, *so that* I can brief a client.
   - *Given* I am verified, *when* I ask the agent about a point, *then* the tool returns every
     candidate with axis verdicts and the distance to the answering cell, and no row sourced under
     a non-commercial licence.
4. **Unverified user.** *Given* I am signed in but not verified, *when* I ask the agent the same
   question, *then* it answers from the public served row (the top 3) and says the full list needs
   verified access.
5. **Reviewer.** *As* the owner's reviewer, *I want* every served pick traceable to a guide page
   and a quote, *so that* a wrong pick can be refuted.
   - *Given* a pick label, *when* I follow `sources:`, *then* `verify` re-finds the quote on that
     page of the stored document.

## 8. Technical considerations

### 8.1 Layer-lane-standard §13 checklist, per lane

| item | how every lane here meets it |
|---|---|
| declared once, sources verified | `LaneRegistration` in `_REFERENCE_DATA_REGISTRATIONS`, with `floor_basis` citing the source and pin |
| `HistoryCapability` | `static_lookup`: one snapshot at the watermark |
| forward refresh | the `maintain` verb (§4.5). The deviation from executor registration is recorded. |
| gap detection | availability index plus `maintain`. There are no daily gaps by nature. |
| governed absences | the input lanes write no row; `plant-suitability` gives a `scoring_null_reason` |
| serving and catalogue | `plant-suitability` only (S11); snapshot capability |
| slider | a snapshot declaration with a vintage badge |
| agent tools | `plant_candidates_at_point`, with distance and `release_day` |
| tests and sweep | per push |

### 8.2 Tripwires (repeated in `metadata.json`)

- **Re-grep every partition `owns` at launch.** The partitions were computed at `56467bd4`.
- **`soil-properties` must be published** (the soil track's P1, P3 and P4) before any
  `plant-suitability` build.
- **Reading the soil z9 rung makes this track its first coarse consumer.** Request the CONTRACT C1
  amendment through the soil orchestrator. This track never edits `.omc/soil-data-plane-20260927/`.
- **0.05° is not a rung.** Never declare it.
- **Only band-safe aggregates** (S3).
- **Never run PlantGeo locally.** Builds are operator one-offs under an owner go. Prototype re-runs
  use the gitignored `.omc` venv, as v0 did.
- **Python runs with `PROJ_LIB`, `PROJ_DATA` and `GDAL_DATA` unset** (memory
  `agri-crop-cover-tests-fail-on-machine-proj-lib`).
- **A bare URL in a `src/**` comment fails `check:data-boundary`** and the web image build. Source
  URLs live in the lane `AGENTS.md` and `docs/lanes/`.
- **Shared checkout:** commit with an explicit pathspec. `QUALITY_RECEIPT.json` digests the whole
  service tree.
- **`EXPECTED_REGISTERED_CENSUS_LANES` changes with every registered lane.** Update it in the
  registration slice.
- **No reference table goes to Postgres**, whatever `environmental_parquet_serving_20260912`'s
  non-goal wording says (§11).

## 9. Assumptions (each with its reversal cost)

| # | assumption | reversal cost |
|---|---|---|
| A1 | The Parquet writer can carry `plantgeo:*` key-value metadata. The fallback is a one-row companion stream. | Low: one extra stream. |
| A2 | `derive_tier` accepts a 0.01° base whose pitch equals z9's. FR-17's test proves or refutes it, and the fallback is a 0.005° base. | Medium: 4× the compute. |
| A3 | The nonspatial streams of `plant-reference` fit the `pipeline/direct/botanical_species_profiles/` publication pattern. That pattern exists but was not read in depth here. | Medium: a publication shim. |
| A4 | PHZM terms are attribution plus a disclaimer (report §9, single verifier). They are re-read verbatim at P0. | High if refused: the cold axis loses its cleared source. |
| A5 | NCEI 1991–2020 station normals are public domain. **Not checked.** | Low: cite and attribute. |
| A6 | USDA PLANTS characteristics may be redistributed as a derived table. PLANTS bulk terms are **not checked** (report §6). | High: the envelope stream would stay internal-only. |
| A7 | Centre-sampling PHZM's 30″ grid into 0.01° cells is acceptable. The conservative alternative, the minimum over touching pixels, is a DI flip. | Low: a rebuild. |
| A8 | Curated rows live as JSONL inside the lane package and ship with the image. The raw documents live in the object store. | Low: move the data directory. |
| A9 | The bias-surface rule (MLRA median when n ≥ 3, else the state median) is a design inference, not evidence. | Low: a rebuild. |
| A10 | FR-1's band inheritance from other documents is a design inference. The alternative inherits from in-region rows only. | Low: a rebuild. |
| A11 | Writing raw guide documents to the object store is a production mutation and waits for an owner go. | None. |

## 10. Risks

| risk | effect | mitigation |
|---|---|---|
| **PRISM terms not cleared** (FROZEN issue 6; report §9 "Unverified") | Precipitation has no cleared source, so the plane cannot publish (S12) | FR-8 evaluates alternatives against stations. If none clears, the plane stays dark and no guild is published without precipitation. |
| **Curation cost** | v0 took four rounds for three pilots and 1,458 rows. Region scale means many more documents, WA from scratch, and NRCS PDFs reachable only through Internet Archive captures | Bounded per-region batches, one push each. The evidence reviewer covers every batch. Gaps are shown, never filled (FR-10). |
| **ERA5 grain** | Frost-free runs 15–44.5 d long at 0.1°, and precipitation ×1.73 at Bend | The regional bias surface (FR-7) and fail-soft (OD-8). Precipitation never comes from ERA5 (FR-8). |
| **PLANTS coverage ~2,186 taxa, one national value, conservative** | Guide names without characteristics are unscorable (56 or 70 in v0). Envelopes understate cold and dry tolerance. | Unscorable names are published, not hidden (FR-5). `pick_definition` says absence is not evidence (FR-4). v1 distribution evidence (report §3) is out of scope. |
| **SSURGO properties deferred** (Q1) | Six axes and the wetland defences lose their inputs | Q1's default makes it a hard dependency. |
| **SoilGrids texture smoothing** | v0 counted PLANTS-coarse cells SoilGrids vs SSURGO: Boise 0 vs 174, Bend 6 vs 868. The texture axis is wrong on Bend pumice. | Survey texture precedence is part of Q1 (soil DESIGN §4.2, grouped precedence). Until then the texture basis says "SoilGrids model estimate". |
| **Open-Meteo spend** (Q3) | ≈ 5.9 M weighted calls exceeds one month's cap and competes with config-driven G7 (~1.75 M) | Split across months, metered. Owner go at PG-2. |
| **No verified-access gate exists** | The tool has nowhere to be gated | S9 and Q4. `/security-review` at Phase 5. |
| **Line-number and count citations rot** | Wrong citations look authoritative | `path::symbol` only. Counts are recomputed at each phase start. |

## 11. Conflicts found (with existing tracks and the soil lane)

1. **The soil lane cannot supply six of v0's thirteen axes' inputs.** `soil-properties` is
   SoilGrids-only (CONTRACT C1). SSURGO survey properties are deferred (soil DESIGN D3). The
   `soil-survey` Parquet schema has drainage and hydric per polygon only and is unpublished.
   "Consume, do not re-acquire" therefore leaves salinity, calcareous, root depth, anaerobic,
   drought regime and wetland wetness `unknown` (Q1).
2. **`soil-properties` is registered but never written**: "Built, not yet run". This is a
   precondition, owned by the soil track.
3. **CONTRACT C1 says no consumer reads soil coarse rungs.** S8 makes this track the first. That
   needs a C1 amendment by the soil orchestrator.
4. **The report's "start the pyramid at 0.05°" does not fit the platform's rung ladder**
   (`{9: 0.01, 5: 0.2, 0: 5.0}`). Superseded by S1.
5. **The report's seven-guild `species_guild` vocabulary and its 6.86 M-row arithmetic** are
   superseded by OD-3 and OD-1.
6. **`config_driven_ingestion_20260926` has not landed.** New lanes land as `pipeline/direct/`
   packages (S6) that its Phase 4 must migrate, and its Phase 7 deletes `pipeline/direct/**`. The
   rule engine in `warehouse/` keeps the logic out of the deleted tree. Its meteorology at 0.25°
   cannot supply normals.
7. **`botanical_species_profile_lookup_20260911` overlaps** on hardiness, precipitation, pH,
   salinity and drought traits.
   - It keeps `agri.species` in Postgres as its authoring surface, against the owner rule that
     Postgres keeps community features only.
   - It forbids name-only joins. This plane keys on the PLANTS symbol (a real identifier), and
     guide names resolve to symbols only through curated routes.
   - Proposal, not decided here: `plant-species-envelope` becomes a PLANTS trait-assertion source
     that the profile lookup may admit. Neither product silently overrides the other.
8. **`environmental_parquet_serving_20260912`'s non-goals exempt "approved species reference
   profiles" from Parquet.** This track follows the owner rule instead: Parquet.
9. **`docs/layer-lane-standard.md` §9.1 cites two conformance tests that do not exist at HEAD.**
   This track extends `parquet-slider-capabilities.test.ts`.
10. **FROZEN and SUMMARY disagree** on unscorable names (56 vs 70) and unflagged slots (~1,000 vs
    305). Reconciled in FR-5.

## 12. Supersession and links

| track | relation | what happens here |
|---|---|---|
| `pnw_herbaria_source_admission_20260911` | **superseded** as a data path for "what grows here" (OD-13) | Recorded. Its admission packets remain history. No specimen pilot is needed for this plane. Flipping its status is the owner's or coordinator's call, not made in this track. |
| `botanical_occurrence_parquet_lane_20260911` | **superseded in part**: the specimen serving plane (`botanical-cell-taxon-summary`, the richness and occurrence layers) stops being the "what grows here" answer | Retiring any served surface is a separate owner go under standard §13.1. |
| `botanical_species_recommendation_validation_20260911` | **superseded in part.** Its recommendation join and API (r1, r3) are replaced by this plane's candidate framing. Its abstaining validation protocol is **reused** by Phase 6. | Linked, not edited. |
| `botanical_occurrence_experience_20260911` | related: shares the map dock and legend space | Coordinate the legend order at Phase 5. |
| `botanical_species_source_admission_20260911` | related | none |
| `botanical_species_profile_lookup_20260911` | related, overlapping (§11.7) | The owner decides the relation. |
| `gbif_source_admission_20260914` | related: future v1 distribution evidence and community validation carry per-record licences, the likely first NC source | Phase 6 reads it. |
| `environmental_parquet_serving_20260912` | parent contract (governed Parquet serving) | This track follows it, except its species-profile non-goal (§11.8). |
| `multiscale_polygon_surface_20260901` | related: continuous-surface rendering for lattice layers | Phase 5 reuses its lattice renderer conventions. |
| `config_driven_ingestion_20260926` | dependency for later migration (S6); its meteorology is not an input | Linked. |
| `vegetation_type_landfire_evt_20260918` | validation input (Phase 6) | Blocked until it lands. |

## 13. Out of scope

- Defensible-space landscaping (the owner's deferral) and PNW 590 as a source.
- Any claim, rank key or filter based on fire effect. PLANTS "Fire Tolerance" stays an
  establishment trait.
- v1 distribution evidence (GBIF / community-widened envelopes, report §3). Community data is used
  **for validation only**.
- Acquiring SSURGO or SoilGrids. This belongs to the soil track.
- Serving the normals lanes as their own map layers (S11).
- Per-cell fire-history columns. The site brief's fire section already answers them.
- States other than WA, OR and ID, including the Montana cells inside the envelope.
- Retiring herbaria or occurrence code or data (§12).
- Irrigation and maintenance recommendations (report §4.4) and cover-crop guilds.

## 14. Open questions beyond the grill

1. Which NWPL region boundary source is authoritative? v0's LRR correspondence is unchecked.
2. Which WA-specific seed and planting guides exist, for example from the NRCS Pullman PMC or WSU?
   FR-10's bounded search answers this; nothing is assumed.
3. The WA and ID noxious-list editions and whether machine-readable copies exist (report §8.4).
4. The PHZM 2023 distribution format and pins: the file name, the grid-versus-zone-polygon choice,
   and Last-Modified / ETag.
