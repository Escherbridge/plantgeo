# Label-Harvest Strategy for the Recommendation Plane

**Date:** 2026-08-14
**Scope:** `agri.expert_label` plane (alembic `20260814_0022`), release `literature-labels:2026-08-14:96cfec27be6fe1c7` (28 `agent_reviewed`, 0 `approved` — counts as reported in the track brief; not re-queried from prod for this document).
**Problem:** Model A scored 0.025 accuracy against a 0.541 majority baseline under grouped leave-one-source-out CV because the current harvest carries effectively one label per species per source. This document grounds the schema, inventories the joinable signal streams, designs a multi-source label set, reconciles it against what the deployed system observes, and stages an executable plan.

Every repo claim below carries a `file:line` citation. External-source claims carry URLs. Anything unverified is labeled **assumption**.

---

## 1. Ground truth: the schema the harvest must fit

All five tables are created by `services/agri-data-service/alembic/versions/20260814_0022_expert_label_plane.py`; the same DDL is mirrored in `services/agri-data-service/db/agri/tables/expert_label*.sql` (generated from the migration head — see header convention, `db/agri/functions/expert_label_envelope_valid.sql:1-5`).

### 1.1 `agri.expert_label_source` — the cited work

Columns (`20260814_0022_expert_label_plane.py:63-78`): `source_key` (unique), `doi`, `source_url`, `title`, `publication_year` (CHECK 1800–2100), `journal_or_publisher`, `edition_or_version`, `license_posture`, `source_checksum` (sha256 hex CHECK).

**DOI gating is soft, not absolute:** `ck_expert_label_source_locator CHECK (doi IS NOT NULL OR source_url IS NOT NULL)` (line 76). A source with no DOI but a stable institutional URL is admissible. What *is* hard is the citation check on the label itself (§1.3).

`source_key` is `doi:<lowercased-doi>` when a DOI exists, else `work:<16-hex title+year digest>` (`src/agri_data_service/method/ml/expert_label_plane.py:192-197`). This matters for CV design: **the LOSO group key is `doi or source_key`** (`src/agri_data_service/method/ml/recommendation_models.py:398`), so one cited work = one CV fold.

The default `license_posture` is `"structured_facts_and_short_attributed_quote"` — source prose is not reproduced, only structured facts plus a ≤1000-char attributed locator (`expert_label_plane.py:31-32`; quote bound: migration line 201-204).

### 1.2 `agri.expert_label_release` — one harvest run

Columns (migration lines 90-133): `release_key` (unique), `harvest_document_uri` + `harvest_document_checksum`, `harvested_at`, per-state counts with a CHECK that they sum to `label_count` (lines 113-120), `slice_summary` jsonb, `review_tier` ∈ {`draft`, `agent_reviewed_pending_owner_signature`, `owner_signed`} defaulting to the middle tier (line 102, 126-132), `owner_signature_reference`/`owner_signed_at` (required by CHECK before `owner_signed`, lines 121-124), `release_checksum`, `loader_code_checksum`.

Release identity: `literature-labels:{harvested_at}:{first 16 hex of harvest_document_checksum}` (`expert_label_plane.py:258-262`) — which is exactly how the current release key was minted. `loader_code_checksum` is a sha256 of the loader module itself (`expert_label_plane.py:265-267`). Capacity: hard cap of 5,000 labels per release, 400 training instances per label (`expert_label_plane.py:79-80`).

### 1.3 `agri.expert_label` — the label

Columns (migration lines 142-227): `label_key` (unique), `release_id`, `source_id`, `label_kind`, `subject` + `subject_normalized`, `outcome`, `condition_envelope` jsonb (CHECK-validated, below), `envelope_checksum`, `rationale` (NOT NULL), `supporting_quote` (1–1000 chars), `confidence` ∈ {high, medium, low} with `confidence_weight` ∈ [0,1] (lines 178-183; the declared mapping is 0.9/0.6/0.3, `expert_label_plane.py:34-36`), `harvest_slice`, three `citation_check_*` fields, review fields, `label_checksum`.

**Label kinds and outcomes are a closed vocabulary** (migration lines 185-200):

| `label_kind` | allowed `outcome` |
|---|---|
| `species_fit` | `fit`, `marginal`, `unfit` |
| `strategy_outcome` | `effective`, `mixed`, `ineffective` |

Any other label kind (e.g. a numeric yield target) is a **migration, not data**.

**Condition envelope vocabulary** — enforced by `agri.expert_label_envelope_valid` (`db/agri/functions/expert_label_envelope_valid.sql:14-24`): numeric terms `mean_annual_precipitation_mm`, `mean_annual_temperature_c`, `growing_season_frost_free_days`, `elevation_m` (point value, `[low, high]` array with open ends, or `{"min":..,"max":..}`); categorical terms `soil_texture`, `aridity`, `usda_hardiness_zone` (string or non-empty string list). The function's own comment: a term outside this list "is a schema change, not data: it must be added here and mapped onto a governed stream in the same review" (lines 29-33).

How the streams can answer each term (`expert_label_plane.py:41-49`):

| term | support | note |
|---|---|---|
| `mean_annual_precipitation_mm` | direct | trailing-365-day precip sum (§3.4) |
| `mean_annual_temperature_c` | direct | trailing-365-day mean |
| `growing_season_frost_free_days` | direct | trailing-365-day count of Tmin > 0 |
| `aridity` | derived_proxy | P/ET0 (Hargreaves), classified |
| `elevation_m` | **unexpressible** | `agri.topography_profiles` holds zero rows; `spatial_cell` has no height attribute (`expert_label_plane.py:50-54`) |
| `soil_texture` | **unexpressible** | `agri.soil_profiles` zero rows; SoilGrids rasters published but not ingested as cell observations (`expert_label_plane.py:55-58`) |
| `usda_hardiness_zone` | **unexpressible** | deriving it "would be a new governed signal, not a read" (`expert_label_plane.py:59-62`) |

Numeric tolerances (half-widths applied at match time): MAP ±60 mm, MAT ±2 °C, frost-free ±30 days, elevation ±300 m (`expert_label_plane.py:65-73`).

**Citation gating (the hard gate):** every harvested label carries a `citation_check` verdict from the adversarial verifier (`refuted`, `doi_resolves`, `reason` — `expert_label_plane.py:111-118`). `ck_expert_label_agent_review_not_refuted` forbids `agent_reviewed`/`approved` when `citation_check_refuted` is true (migration lines 169-171). `doi_resolves` is *recorded*, not a CHECK — a URL-only source can still advance.

**Review-tier state machine:** `draft → agent_reviewed → approved`, plus `rejected` (migration lines 26, 208-215). The `expert_label_review_guard` trigger fires BEFORE INSERT/UPDATE/DELETE; a direct INSERT may only be `draft`/`rejected`, so `agent_reviewed`/`approved` are reachable only through the guarded UPDATE transition (migration lines 37-48, 374-378). `approved` additionally demands a non-empty `owner_signature_reference` (lines 172-174) which "nothing in this service can mint" (line 29). Reviewed labels need `reviewed_by`/`reviewed_at` (lines 205-207). The loader's flow: insert as draft, then advance non-refuted labels via the guarded transition (`recommendation_commands.py:64`; SQL verbs `insert_expert_label.sql` and `advance_expert_label_review.sql` under `src/agri_data_service/sql/execution/`), with reviewer identity `literature-label-harvest/adversarial-citation-verifier` (`expert_label_plane.py:29`).

**Checksums:** `label_checksum` = sha256 over a canonical payload binding content + lineage + citation verdict (`expert_label_plane.py:216-241`); `envelope_checksum` over the envelope alone (lines 244-248); `label_key` = `{kind}:{subject-slug}:{16-hex digest}` so re-loading the same harvest resolves instead of duplicating (lines 251-255).

### 1.4 `agri.expert_label_training_instance` — the label mapped onto governed streams

One row per (label, spatial cell, day): `label_id`, `release_id`, `spatial_cell_id` (FK → `agri.spatial_cell`, migration lines 353-355), `observed_date`, `as_of_time`, `feature_schema_version`, `feature_values` jsonb array, `feature_checksum`, `envelope_match` jsonb, `unexpressible_terms` jsonb array, `match_state` ∈ {`matched`, `excluded`, `unexpressible`} (migration lines 230-259). Unique on `(label_id, spatial_cell_id, observed_date, feature_schema_version)` (lines 265-267). Envelope terms the streams cannot express are carried as an explicit gap, never silently dropped (migration docstring lines 20-22).

### 1.5 `agri.recommendation_training_receipt`

Pinned to a `label_release_id`, artifact/job lineage, four checksums, and two structural safeties: `CHECK (evaluation_only)` and `CHECK (NOT publication_authorized)` (migration lines 274-324) — the row shape cannot express a publishable model. `label_review_tier` travels on every receipt (lines 316-322).

### 1.6 The harvest document contract (what a new harvest must produce)

`load_harvest_document` reads a JSON file shaped as `HarvestDocument`: `{harvested_at, workflow, kept: [HarvestLabel], rejected: [HarvestLabel]}` with `extra="forbid"` throughout (`expert_label_plane.py:98-155`). Each `HarvestLabel`: `label_kind`, `subject`, `condition_envelope` (may be empty — reported unloadable rather than crashing the rest, lines 128-131), `outcome`, `rationale`, `source {doi?, url?, title, journal?, year, supporting_quote_or_finding?}`, `confidence`, `harvest_slice`, `citation_check {refuted, doi_resolves, reason}`.

**Everything in §6 is expressed in exactly this shape. No new fields are invented.**

---

## 2. Why 28 labels cannot train (the diagnosis, grounded)

- CV is grouped leave-one-source-out; the group key is the cited work (`recommendation_models.py:398`, scheme name `grouped_leave_one_source_out` at line 618, majority baseline at line 628).
- The code itself documents the degeneracy: "the harvest holds one label per species, so a species one-hot would be a perfectly separating column that leave-one-source-out cross-validation could never score" — Model A therefore carries no subject identity and generalizes only through envelope descriptors (`recommendation_models.py:350-354`).
- With ~1 label per (species, source) and few sources, each held-out fold contains envelope regions and outcomes no other source corroborates. The model has nothing to generalize *from*; 0.025 vs 0.541 is an honest negative, not a bug.
- Fit floors that any new release must clear: ≥6 labels (`_MIN_LABELS_FOR_FIT`, `recommendation_models.py:106`), ≥2 classes in every training fold (`_MIN_CLASSES_FOR_FIT`, line 115), ≤200,000 design rows (line 105).

**Therefore density means: multiple independent sources asserting labels over the *same* species and overlapping envelope regions, with all outcome classes represented within most folds.** More species with one source each fixes nothing.

---

## 3. The signal streams a label can actually join against

### 3.1 The two ingest planes

**Forward serving plane** — `run_all_ingestion_jobs` runs eight sources per tick plus geometry repair (`src/agri_data_service/ingest/runner.py:43-53`): FIRMS fire detections, USGS NWIS streamflow, Open-Meteo weather, WFIGS fire perimeters, USDM drought, NDVI vegetation, NWS sensors, evacuation zones. These feed the map/serving side (`geo.features`), not the covariate plane directly.

**Archive/backfill lanes** (`src/agri_data_service/ingest/lanes.py:193-230`): `firms-archive` (floor = MODIS_SP archive start, 1-day chunks — peak PNW fire season runs ~21k detections/day, lines 202-211) and `streamflow-archive` (~807 gauge-days/day over the PNW box, 10-day chunks, lines 214-226). Additional history verbs exist for USDM (`USDM_HISTORY_SOURCE`, `src/agri_data_service/ingest/commands.py:470`), MTBS, watersheds (commands.py:277, 309).

**Governed warehouse plane** — what the covariate reader consumes — is `agri.signal_observation`, keyed by `(cell_id, signal_name, observed_at, support_key, quality_flag, source_release_id, data_available_at)` (read predicates: `db/agri/functions/covariate_daily_features.sql:61-76`). Its meteorology backbone is **NASA POWER daily point data** (`https://power.larc.nasa.gov/api/temporal/daily/point`, `src/agri_data_service/execution/historical_backfill.py:24-25`, schema `nasa-power-daily-v1`), promoted through the historical-promotion machinery alongside ERA5-Land and USDM source keys (`src/agri_data_service/execution/historical_export.py:79`).

### 3.2 The spatial join key

`agri.spatial_cell`: the `nasa-power-0.5-degree` grid held **397 cells at roughly 55 km spacing** as of 2026-08-11 (`src/agri_data_service/agent/tools.py:76`). Every training instance FKs to it (migration lines 353-355). **The label join key is therefore `(subject_normalized, spatial_cell_id, observed_date)` — but labels themselves carry no location or time.** A label is a condition→outcome claim; the mapper intersects its envelope with each cell-day's derived site climate (`recommendation_lane.py:647-717`), which is what lets one literature label fan out to many (cell, day) instances (capped at 400, `expert_label_plane.py:80`). Harvested sources therefore do **not** need coordinates; they need envelopes — or site descriptions translatable into the envelope vocabulary.

### 3.3 The covariate feature vector (what a matched instance carries)

`agri.covariate_feature_schema` (`db/agri/functions/covariate_feature_schema.sql`):

| indices | block | stream_key | content |
|---|---|---|---|
| 1–35 | meteorology | `nasa_power` | 7 signals (Tmax/Tmean/Tmin, dew point, precip, RH, wind) × 5 shapes (lag 1/2/3, 7-day and 28-day rolling means) (lines 19-48) |
| 36–38 | drought | `usdm` | severity class lag-1/lag-7 + imputed flag (lines 60-62) |
| 39–40, 46–47 | calendar | `calendar` | day-of-year sin/cos (+ semiannual pair in v2) (lines 63-64, 91-92) |
| 41–45 | mc_forecast (v2 only) | `forecast_iteration` | Monte-Carlo forecast low/median/high/band/lead, strictly lagged (lines 86-90) |

Declared gaps (never emitted as empty columns): `era5_land` credential-gated in both versions; `analog_ensemble` and `ml_ridge_forecast` not-yet-authored in v2 (`db/agri/functions/covariate_declared_gap.sql:24-51`).

Temporal semantics: daily UTC-day resolution; v2 gates each day-D row on inputs whose `data_available_at` preceded day D (per-issue gating, `covariate_daily_features.sql:13-19, 78-130`); drought is a named v2 limitation — not per-issue re-picked (lines 268-272).

### 3.4 Site-climate terms (what the envelope is matched against)

`load_site_climate_terms` derives, per issue day, MAP (sum of trailing-365-day precip), MAT, frost-free-day count, and an aridity index P/ET0 via Hargreaves — each requiring ≥330 of the trailing 365 days observed (`recommendation_lane.py:281-389`; `CLIMATE_WINDOW_DAYS=365`, `CLIMATE_MIN_COMPLETE_DAYS=330`, `src/agri_data_service/method/ml/covariates_v2.py:31-34`). All availability-gated on `data_available_at <= as_of_time` (lane lines 290-292).

### 3.5 Streams the label plane does NOT currently join

NDVI vegetation, streamflow, FIRMS/WFIGS fire, NWS sensors, and SoilGrids exist in the service but do not reach `covariate_feature_schema` or the envelope vocabulary. They are *reconciliation* streams (§7) and *future envelope terms* (§8 Phase 3), not present join targets. Claiming otherwise would be inventing schema.

---

## 4. Label taxonomy: what a label IS here, and what the model predicts

Grounded in the code, not aspiration:

- **A label is a cited (condition envelope → outcome) claim about a subject** — "a trusted source recommends/reports this under these conditions" — explicitly *not* a treatment/control intervention outcome (migration docstring lines 6-11; the 0013 causal plane stays untouched).
- **Model A (`species_fit`)** predicts P(fit / marginal / unfit) for a subject at a (cell, day) from envelope-deviation features + site climate + the 28-day site-regime covariates + calendar (`SITE_COVARIATE_FEATURES`, `recommendation_models.py:73-81`; deviation terms lines 116-120). Class probabilities collapse to a ranking score via ordinal utilities fit=1.0 / marginal=0.5 / unfit=0.0 (`OUTCOME_UTILITY`, lines 50-57; `rank_subjects` line 795). Claim tier: `literature_grounded_recommendation_with_citations`, evaluation-only disclaimer hard-coded (lines 42-47).
- **Model B (`strategy_selection`)** does the same over `strategy_outcome` labels, with subject one-hots (line 347-349), and structurally cannot become a selection (receipt CHECKs, §1.5). Existing evidence slices: `strategy-reforestation-fire`, `strategy-water-harvesting` (lines 66-67).

**Target to keep:** the three-class ordinal fit outcome. Do not introduce a numeric yield target — the outcome vocabulary is a CHECK (migration lines 191-200) and the ranking layer consumes classes. Numeric evidence (trial yields, NASS statistics) is *banded into* fit/marginal/unfit at harvest time with the banding rule recorded in `rationale` (§6).

---

## 5. Density target for meaningful LOSO

Reasoning from the CV mechanics (§2): folds = distinct cited works; a fold is scoreable only if the training remainder covers the held-out fold's (species × envelope-region × outcome) support.

**Concrete targets (species_fit):**

1. **Anchor species set:** ~25 species — the current release's subjects plus PNW-relevant crops/agroforestry species. **≥4 independent sources per anchor species**, so any single fold removal leaves ≥3 corroborating sources.
2. **Broad set:** 150–300 additional species at ≥2 sources each (compendium + one corroborator).
3. **Outcome coverage per species:** ≥1 label in each of ≥2 outcome classes wherever the literature honestly supports it. Range-structured sources make this natural: one work stating optimal and absolute ranges yields up to three labels per species — `fit` inside the optimal envelope, `marginal` between optimal and absolute, `unfit` outside absolute — each a separate label row with its own envelope and checksum, all citing the same source.
4. **Source count and balance:** ≥10 distinct cited works (folds) for Model A; **no single source above ~40% of labels**, or its fold dominates the score. Distinct GBIF download DOIs and distinct trial-report citations each mint distinct `source_key`s (§1.1), which is how balance is achieved mechanically.
5. **Volume:** ~600–1,200 species_fit labels total (25×4×~2.5 outcome-bands ≈ 250 from anchors; 150–300×2×~1.5 ≈ 450–900 broad). Well inside the 5,000/release cap (`expert_label_plane.py:79`).
6. **strategy_outcome:** ≥10 sources, ≥60 labels across the two existing slices before Model B's LOSO is worth reading.

**Per-(species, source) expectation stays ~1–3 labels** — that is inherent to what a citation is. Density comes from source multiplicity over shared species, not from inflating labels per source.

---

## 6. The source catalogue

All entries map into the §1.6 harvest-document shape with no schema change. License/access verified by web search 2026-08-14 unless marked assumption.

### 6.1 FAO ECOCROP — the workhorse (Phase 0)

- **What it yields:** environmental requirements for **2,000+ plant species**: min/max temperature, annual precipitation, pH, altitude, Köppen zone, photoperiod ([FAO catalog](https://data.apps.fao.org/catalog/dataset/ecocrop), [GAEZ v4 portal](https://gaez.fao.org/pages/ecocrop), [tool](https://ecocrop.apps.fao.org/ecocrop/srv/en/home)). Verified open access; now maintained under GAEZ.
- **Schema mapping:** optimal/absolute temperature → `mean_annual_temperature_c` `{min,max}`; optimal/absolute rainfall → `mean_annual_precipitation_mm`; altitude → `elevation_m` (recorded as an unexpressible-term gap today, §1.3); the fit/marginal/unfit banding of §5.3. `outcome` per band; `confidence` medium (compendium, not trial); `harvest_slice` e.g. `species-fit-ecocrop-pnw`.
- **Citation:** no per-record DOI; `source_url` to the FAO/GAEZ dataset page + `journal_or_publisher = "FAO"`, `edition_or_version` = dataset snapshot date. Passes `ck_expert_label_source_locator`. **Assumption:** FAO's usual CC BY-NC-SA 3.0 IGO posture applies to the dataset export; the plane stores structured facts under `license_posture` — record the exact license string at harvest time and have the citation-check verifier confirm it.
- **LOSO caveat:** ECOCROP is ONE cited work → one fold. Holding it out and predicting its labels from trials+literature is a rigorous generalization test, but respect the 40% balance cap (§5.4) — harvest only the PNW-relevant slice (~150–300 species), not all 2,000.

### 6.2 Land-grant extension variety-trial reports (Phase 1)

- **What:** OSU runs statewide wheat/barley trials at **11–12 PNW locations for three consecutive years per variety** ([OSU variety trials](https://cropandsoil.oregonstate.edu/wheat/osu-wheat-variety-trials)); WSU publishes annual small-grain and oilseed trial results ([WSU small grains](https://smallgrains.wsu.edu/timely-topics/), [WSU oilseeds](https://oilseeds.css.wsu.edu/variety-testing/)); University of Idaho maintains ~120 varieties through its Foundation Seed Program ([Idaho wheat](https://www.idahowheat.org/focus-on-research)). Volume: tens of (variety × site × year) results per crop per year.
- **Schema mapping:** trial site's climate normals expressed in the envelope vocabulary (MAP/MAT/frost-free — exactly the terms `load_site_climate_terms` derives, §3.4); performance banded to fit/marginal/unfit by yield percentile within trial (banding rule in `rationale`); `confidence` high (measured performance). The tolerance half-widths (`expert_label_plane.py:68-73`) are what make a site-point envelope matchable.
- **Citation:** mostly stable institutional URLs (extension report series), some DOIs where trials are published in *Agronomy Journal* / *Journal of Plant Registrations* — **assumption:** DOI coverage of specific PNW trial reports is unverified per-document; the harvest workflow's citation verifier settles each one, and URL-only reports remain admissible (§1.1). License: public university extension publications, generally free to cite as facts — record per document.
- **Value:** many independent works over the *same* species set = the fold multiplicity LOSO needs.

### 6.3 Peer-reviewed species-suitability and variety-trial literature (Phase 1, continuing)

The lane the current 28 labels came from — DOI'd, `citation_check.doi_resolves = true`, `confidence` high. Continue it as the corroboration lane for anchor species rather than the sole lane. Prioritize papers stating explicit environmental ranges or site descriptions (they translate to envelopes without invention).

### 6.4 GBIF occurrence-derived envelopes (Phase 2 — methodology-gated)

- **What:** species occurrence records; **each download mints its own DOI** and every dataset carries CC0 / CC BY / CC BY-NC ([citation guidelines](https://www.gbif.org/citation-guidelines), [terms](https://www.gbif.org/terms), [license processing](https://data-blog.gbif.org/post/gbif-occurrence-license-processing/)). Verified.
- **Schema mapping:** presence-only data ⇒ can only support `fit` (or, at envelope margins, `marginal`) claims: compute a climatic envelope (e.g. 5th–95th percentile MAP/MAT across PNW occurrences) → one `fit` label per species with the method named in `rationale` and `workflow`. **`unfit` cannot be derived without pseudo-absence assumptions — do not fabricate negatives from GBIF.** `confidence` low (0.3 weight, `expert_label_plane.py:36`).
- **License discipline:** restrict downloads to CC0/CC BY datasets (**assumption:** the exact download-API license filter parameter is unverified; verify at harvest time), record posture per download.
- **CV note:** one download DOI = one fold. Issue one download per species-batch, not one giant download, to avoid a mega-fold.

### 6.5 USDA sources

- **2023 USDA Plant Hardiness Zone Map** (USDA-ARS × OSU PRISM): rasters freely redistributable with attribution conditions; also on Ag Data Commons ([planthardiness.ars.usda.gov](https://planthardiness.ars.usda.gov/), [PRISM PHZM](https://prism.oregonstate.edu/phzm/), [Ag Data Commons record](https://agdatacommons.nal.usda.gov/articles/dataset/2023_USDA_Plant_Hardiness_Zone_Map_Mean_Annual_Extreme_Low_Temperature_Rasters/25343293) — the Ag Data Commons record functions as a citable dataset landing page). **Not a label source** — it is the *stream* that would make the already-legal `usda_hardiness_zone` envelope term expressible (Phase 3). Today that term correctly lands in `unexpressible_terms` (`expert_label_plane.py:59-62`).
- **USDA PLANTS / NRCS Ecological Site Descriptions:** public-domain species characteristics and site-based plant community suitability (ESDs via the EDIT platform). **Assumption:** exact EDIT export mechanics unverified; treat as a Phase 2 candidate with institutional citations (`journal_or_publisher = "USDA-NRCS"`).
- **USDA NASS Quick Stats (county yields):** public domain, institutional citation. **Deferred:** county geometry does not join the 0.5° cell grid without a crosswalk (an `agri.cell_source_crosswalk` table exists — `db/agri/tables/cell_source_crosswalk.sql` — but wiring NASS through it is new work), and yield banding to fit/marginal/unfit within county×crop is a methodology memo of its own. Value-per-effort is below the lanes above.

### 6.6 TRY plant trait database — supporting evidence, not labels

CC BY by default since 2019; citation Kattge et al. 2020, *Global Change Biology* 26:119–188, DOI 10.1111/gcb.14904 ([paper](https://research.wur.nl/en/publications/try-plant-trait-database-enhanced-coverage-and-open-access), [IP guidelines](https://www.try-db.org/TryWeb/TRY_Intellectual_Property_Guidelines.pdf)). Verified. Traits (frost tolerance, drought strategy) are not (condition → outcome) claims, so they do not become labels; use them inside `rationale` to justify confidence tiers and envelope bounds. Request-based access — plan lead time.

### 6.7 Explicitly out of scope for labels

FIRMS/WFIGS/MTBS fire, streamflow, NDVI, NWS sensors: these are outcome-observation streams for the reconciliation loop (§7), not literature label sources. SoilGrids: a future envelope stream (Phase 3), already published as COGs but not cell-ingested (`expert_label_plane.py:55-58`).

---

## 7. Reconciliation against the deployed system (owner requirement)

### 7.1 What gates a label as usable

A label becomes trainable only by surviving three deployed gates, all already in code:

1. **Citation gate:** non-refuted verdict, else it cannot leave `draft` (§1.3).
2. **Envelope-expressibility gate:** at mapping time, `evaluate_envelope` intersects each envelope term with the cell-day's derived site climate; verdicts land in `envelope_match`, inexpressible terms in `unexpressible_terms`, and the row gets `match_state` matched/excluded/unexpressible (`recommendation_lane.py:669-717`; `expert_label_plane.py:393-` ; migration lines 241-259). Only `matched` rows reach the design matrix (`select_matched_training_instances.sql`).
3. **Stream-completeness gate:** site-climate terms are NULL unless ≥330/365 trailing days are observed (§3.4), and design rows missing required inputs are skipped and counted (`skipped_incomplete_rows`, `recommendation_models.py:393-394, 452`) — a receipt with a high skip count is self-indicting.

### 7.2 The completeness mask and the known USDM blocker

`load_covariate_vectors` reports `complete_day_count` = days where **every** feature in the pinned vector is non-null (`recommendation_lane.py:267-271`). The drought block (indices 36–38) is fed by `agri.drought_class_daily_series`, which reads `agri.drought_polygon_snapshot` (`db/agri/functions/drought_class_daily_series.sql:23`).

**Blocker, accounted for:** `agri.drought_polygon_snapshot` has 0 rows in production (per the track brief and the prod-state memory; not re-queried here). Consequence chain: drought features return `input_count = 0` for every day → `complete_day_count = 0` for every cell → any consumer gating on the full completeness mask (the covariate wind lane and AnEn backtests) is blocked.

**Why label harvesting is NOT blocked by it:** Model A's design rows read `SITE_COVARIATE_FEATURES` — the 28-day meteorology rolling means and the calendar pair only; no drought index is in that subset (`recommendation_models.py:73-81`). Harvest, mapping, and training proceed on the NASA POWER + calendar blocks. But the coverage *reporting* (`recommendation-covariate-coverage`, `recommendation_commands.py:136-161`) will truthfully show zero complete days until USDM history is backfilled into prod — so the plan schedules the USDM prod backfill (the `USDM_HISTORY_SOURCE` walk machinery exists, `ingest/commands.py:470`, `execution/historical_usdm.py`) as a parallel, not prerequisite, workstream, and every training receipt in the interim should be read with `evaluation_metrics` + skip counts, never with the completeness mask.

### 7.3 The approval tier is unreachable — designed around, not against

Owner countersignature is pending on the current release; the schema makes `approved` unmintable by this service (§1.3). The plan therefore:

- Runs **everything at `agent_reviewed`**; every receipt and served citation carries `label_review_tier = 'agent_reviewed_pending_owner_signature'` (migration lines 29-30, 102).
- Keeps promotion a **pure state transition**: when the owner countersigns, the guarded UPDATE sets `review_state = 'approved'` with the signature reference per label, and the release flips to `owner_signed` (CHECKs at migration lines 121-124, 172-174). No label content, checksum, or instance changes — so nothing trained needs re-mapping, only re-receipting at the higher tier.
- Never edits a reviewed label in place — the trigger exists precisely because "a reviewed label is the evidence a trained artifact cites by checksum" (migration lines 37-43). Corrections are new labels in a new release.

### 7.4 The ongoing reconciliation loop

Per release cycle (all existing verbs, `recommendation_commands.py:55-161`):

1. `recommendation-labels-summary --release-key …` — per-slice/state/outcome accounting vs the harvest document's own counts (release CHECK keeps them honest, migration lines 113-120).
2. `recommendation-covariate-coverage` over the anchor cells — which feature blocks answered, which declared gaps applied.
3. `recommendation-labels-map` per (release, kind, cell, window) — track the matched/excluded/unexpressible ratio release-over-release; a rising unexpressible share is the signal to prioritize a Phase 3 stream, with `unexpressible_terms` naming exactly which term and why.
4. `recommendation-train` without `--persist` first; compare LOSO accuracy/macro-F1 against the recorded majority baseline; persist a receipt only when the fold report is worth citing.
5. **Forward validation (future, evaluation-only):** where a `fit`-labeled species is actually present in a matched cell, the NDVI lane's observed seasonal response is the deployed observation stream to compare against — a design note for after density exists, not a current join (§3.5).

---

## 8. Staged plan (value-per-effort order)

**Phase 0 — ECOCROP PNW slice (one session, no code changes).**
Produce one harvest JSON (per §1.6, workflow field naming the ECOCROP banding method) for ~40–60 anchor+PNW species × up to 3 outcome bands ≈ 100–180 labels, slice `species-fit-ecocrop-pnw`. Run: `recommendation-labels-load --harvest-document … --persist` → `recommendation-labels-summary` → `recommendation-labels-map --label-kind species_fit --cell-id <Boise/PNW cells> …` → `recommendation-train --model species_fit` (dry, then `--persist`). Files touched: the harvest JSON only. Success test: LOSO runs with ≥2 folds and the fold report is interpretable (it will still be weak — one big fold; that is expected and honest).

**Phase 1 — trial + literature corroboration lanes (2–3 sessions).**
Extension variety-trial reports (§6.2) and DOI'd papers (§6.3) for the 25 anchor species: target ≥4 sources/species, ≥10 total folds, ECOCROP ≤40% of labels. New harvest documents per lane; same verbs; new release per harvest (release keys are content-addressed, §1.2). This is the phase that makes LOSO meaningful; re-baseline Model A here.

**Phase 2 — breadth lanes (parallelizable).**
GBIF per-species-batch downloads with the presence-only methodology memo (§6.4, `fit`-only, low confidence); NRCS ESDs (§6.5). Broad set to 150–300 species × ≥2 sources.

**Phase 3 — make more of the envelope expressible (migrations begin here).**
Ordered by unexpressible-term counts observed in Phase 1–2 mapping reports: (a) SoilGrids → cell-level soil observations to flip `soil_texture` to direct (`soil_profiles` currently zero rows, `expert_label_plane.py:55-58`); (b) static per-cell elevation to flip `elevation_m` (`topography_profiles` zero rows, lines 50-54); (c) PHZM-derived `usda_hardiness_zone` as a new governed signal (lines 59-62). Each is: new ingest/promotion path + edit to `ENVELOPE_TERM_SUPPORT`/`UNEXPRESSIBLE_TERM_REASON` (`expert_label_plane.py:41-63`) + re-map (new instances at the same `feature_schema_version` semantics or a bumped one). Any *new* envelope key additionally requires editing `db/agri/functions/expert_label_envelope_valid.sql` **and** a migration loading it, per the function's own contract (lines 29-33) and the forward-load workflow (file header lines 1-5).

**Phase 4 — reconciliation hardening (parallel to 1–3).**
USDM prod history backfill to un-zero the completeness mask (§7.2); owner countersignature ceremony → guarded promotion to `approved`/`owner_signed` (§7.3); then re-issue receipts at the signed tier. Optional afterward: NASS county-yield lane via the cell crosswalk (§6.5) once its banding memo is written.

---

## 9. Verification ledger

- **Verified in-repo:** every table/column/CHECK/trigger cited from `20260814_0022_expert_label_plane.py`; envelope vocabulary and term-support maps; loader/keys/checksum schemes; CLI verbs (`recommendation_commands.py:55-161`); CV grouping and fold mechanics; covariate schema/gaps/completeness; site-climate derivation; ingest source lists and lane declarations; existence of `species`, `climate_profiles`, `soil_profiles`, `topography_profiles`, `cell_source_crosswalk`, `signal_observation`, `spatial_cell` table definitions (`db/agri/tables/`).
- **Taken from the track brief / prod-state memory, not re-verified:** 28 `agent_reviewed` / 0 `approved` counts; Model A 0.025 vs 0.541; `drought_polygon_snapshot` = 0 rows in prod; pending countersignature.
- **Web-verified 2026-08-14:** ECOCROP open access + content scope; GBIF download DOIs + CC license triad; TRY CC BY + Kattge 2020 citation; PHZM 2023 access/redistribution terms; OSU/WSU/UI trial program existence and shape.
- **Assumptions (flagged inline):** FAO dataset license string; DOI coverage of individual extension trial reports; GBIF download-API license-filter parameter; EDIT/ESD export mechanics; `soil_profiles`/`topography_profiles`/`species` row counts in prod (schema existence verified, population not).
