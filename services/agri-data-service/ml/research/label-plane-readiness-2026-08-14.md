# Label Plane: Owner Decisions, Prod Reality, and Readiness Gaps

**Date:** 2026-08-14, extended 2026-08-15 with the second decision round (§2.1) and §4.7.
**Status:** supersedes §5 (density target), §6.1 (ECOCROP-first), §6.4 (GBIF fold splitting) and §8 (staged plan) of
[`label-harvest-strategy-2026-08-14.md`](label-harvest-strategy-2026-08-14.md). §1–§4 and §7 of that document remain
the grounded schema inventory and are still correct except where §1 below corrects them.
**Audience:** whoever runs the next harvest and the next training pass.

This document records (a) the owner's decisions on the label-selection design, (b) what production actually
contains as of a read-only census run 2026-08-14, (c) an honest assessment of which decisions fix the failure and
which do not, and (d) the data, processes, and artifacts that must be added before a training run means anything.

---

## 1. Production census, 2026-08-14 (read-only, `DATABASE_URL_SYNC`)

Seven findings, three of which change the plan.

### 1.1 The model has never been trained against production

`agri.expert_label_training_instance` holds **0 rows**. Training reads exclusively from persisted instances
(`select_matched_training_instances.sql`), so no fit has ever consumed production data.

**Consequence:** the headline diagnostic — "Model A scored 0.025 accuracy against a 0.541 majority baseline" —
did not come from production. It came from a local or ephemeral run whose inputs are not reproducible from the
prod plane. It should be treated as an indicative local result, not a production baseline, until §5 Phase B
reproduces it. Every downstream inference in the strategy doc rests on that number.

### 1.2 The trait and companion channels are schema-only

| table | rows | bearing |
|---|---|---|
| `agri.species` | **0** | carries `growth_habit`, `drought_tolerance`, `salt_tolerance`, `nitrogen_fixer`, `usda_zones`, `min/max_precip_mm`, `min/max_ph`, `light_requirement`, `pollinator_value`, `edible`, `timber_value`, `guild_roles` |
| `agri.companion_relationships` | **0** | `species_a_id`/`species_b_id`, `relationship_type` ∈ companion/antagonist/neutral, `guild_function`, evidence + review-state columns |

This is the single most useful census result. **The trait feature channel the owner asked for already exists as a
governed table with an evidence-and-review posture** — it does not need TRY to exist first, and it does not need a
migration. TRY becomes a *filler* for missing trait values, cited per value, not a prerequisite. Likewise
`companion_relationships` already models the companion-planting feature as a reviewed, cited relation.

### 1.3 The empty planes the strategy doc predicted are confirmed empty

`soil_profiles` 0, `topography_profiles` 0, `climate_profiles` 0, `drought_polygon_snapshot` 0. §1.3 and §7.2 of
the strategy doc are correct.

### 1.4 The meteorology backbone is healthy and wide enough for a coverage-first plan

`agri.signal_observation`: ~46.1 M rows / 26 GB. Per `agri.signal_coverage_audit`, the seven POWER met signals
(`air_temperature_max/mean/min`, `dew_point_temperature`, `precipitation`, `relative_humidity`, `wind_speed`)
cover **397 cells from 2022-04-30 to 2026-08-06** — about 4.3 years. `load_site_climate_terms` needs a trailing
365-day window with ≥330 observed days, so **every day from roughly 2023-04-30 onward is derivable on all 397
cells.** The coverage-first decision (§2, answers 12–13) is fully supported by data already in production. Nothing
needs ingesting to run it.

### 1.5 There are soil-moisture signals in the warehouse that the model does not read

Also present on the POWER grid (397 cells, same window): `soil_wetness_surface`, `soil_wetness_root_zone`,
`soil_wetness_profile`. On the NDVI grid (1568 cells): `soil_water_content_layer_1..3`, `soil_temperature_level_1..4`,
`vapor_pressure_deficit`.

None of these reach `covariate_feature_schema`, which carries only the 7 POWER met signals × 5 shapes, USDM, and
calendar. **Soil moisture is one of the owner's four stated objectives and there is an on-grid, in-window governed
signal for it that the recommendation model cannot see.** See §4.6.

### 1.6 The cell grid

`agri.spatial_cell` = 1965 rows, split `nasa-power-0.5-degree` 397 cells @ 55 660 m and `sentinel2-ndvi-0p25deg`
1568 cells @ 27 830 m. The strategy doc's 397 is correct for the label-join grid.

### 1.7 Two labels were silently lost at load, and the accounting now reconciles

The harvest document holds 32 entries (30 `kept`, 2 `rejected`). Production holds **30 labels: 28 `agent_reviewed`
+ 2 `rejected`**.

The two missing ones are `strategy_outcome` / `agroforestry` / `mixed` and `strategy_outcome` / `silvopasture` /
`mixed` — both carry an **empty `condition_envelope`** and were reported unloadable by design
(`expert_label_plane.py:128-131`). The 2 `rejected` rows are the verifier's catches (*Panicum miliaceum*,
*Sorghum bicolor*), both loaded correctly in the `rejected` state.

So a 6 % authoring loss is already happening for the exact reason §4.2 exists: nothing checks envelope
expressibility before a harvest is written.

---

## 2. Owner decisions, settled

Recorded so they are not re-litigated. Numbering follows the grilling questions.

1. **Two models, both with subject identity.** Model A = species selection over common deployable plants, trees,
   shrubs, grasses, and fungi, chosen for wildfire / drought / water / vegetation / soil-moisture management.
   Model B = strategy selection, with room to get more specific about execution variants (biochar has many forms;
   likewise silvopasture and agroforestry). Both return **ranked** results, multiple recommendations allowed,
   ranked by estimated efficacy conditioned on region.
2. **Source multiplicity is for evidentiary backing**, not primarily a CV device — the point is that a model
   choice can be traced to more than one real record.
3. **Species traits become model features**, not just rationale prose.
5, 14, 15. **Far fewer labels than the 600–1200 proposed.** The service cannot support that volume of honest
   adversarial verification, and it is not the constraint that matters.
6. **Phase 0 as written was a misstep.**
7. **Elevation should just be loaded** — static, one-time, does not change.
8. **The ECOCROP-first framing was a misstep.**
9. **GBIF download splitting: dropped.**
10. Source-independence detection: owner indifferent; treated below as a cheap recorded field, not a mechanism.
11. Ranking is the owner's answer to label-weighting imbalance. *(Partially a misunderstanding — see §3.3.)*
12, 13. **Fewer labels, more spatial coverage, more time.** Single-cell fits are not the goal.
16. **A third model for effect (Model C).** Growing well in a place is not producing a desired outcome; the
    system is trying to control the flow of outcomes. Model A is not rebuilt to carry this. Species count may be
    reduced further to pay for it, and extra research is expected on which species serve which objective —
    wildfire prevention, drought mitigation, water conservation, soil amendment, carbon capture. Design §4.5.

### 2.1 Second decision round, 2026-08-15

Twelve remaining questions were put to the owner and settled. These close §6 as it originally stood.

| # | decision | bearing |
|---|---|---|
| 17 | **Map once, train both ways.** Map the 28 labels across 397 cells × ~3 years, then train twice — once on the current spec, once with subject identity on. | Instances are spec-agnostic (the one-hot is applied at train time), so the before/after costs one extra fit, not one extra mapping. |
| 18 | **Model C Stage 1 objectives: soil amendment + carbon capture + wildfire.** Wildfire restricted to fuel-property studies (foliar moisture, volatile oils, fuel-bed structure); list-style "firewise plant" guidance is not admissible evidence. | §4.5. Drought mitigation and water conservation stay out of Stage 1. |
| 19 | **Anchor set: 12–15 vascular species** (trees, shrubs, grasses), carried deeply on both axes. Fungi and mycelium deferred until `soil_texture` is expressible at minimum. | §3.4, §4.5. |
| 20 | **Model C Stage 2 outcomes are directional:** `beneficial` / `neutral` / `detrimental`. | §4.5. The ranker must be able to penalise a species that worsens an objective, not merely fail to reward it. |
| 21 | **Two separate ranked lists** — "best suited here" and "most effective for this objective" — not a gate and not a blended score. | §4.5 composition. |
| 22 | **The effect list is unfiltered, and every row is annotated with its suitability verdict and probability.** | Follows from 21: nothing is hidden, and a species that cannot establish at the site is visibly marked rather than silently ranked. |
| 23 | **Objective conflicts are surfaced, not priced.** Rank on the selected objective; display any `detrimental` verdict on another objective as an explicit flag with its citation. | No net-scoring across objectives — the evidence does not support pricing a water harm against a carbon gain. |
| 24 | **Cell aggregation: store ranges, match on overlap.** min/median/max for elevation, dominant class + fraction for texture; the envelope matches when the cell's range overlaps it. | §4.4. Requires a new match predicate alongside the existing containment one. |
| 25 | **Soil-moisture signals promoted after the Phase B baseline**, not before. | §4.6. Do not move the feature schema underneath the first honest production number. |
| 26 | **Training weight becomes `confidence_weight / instance_count_for_that_label`.** | P4, §3.3. One label = one unit of evidence regardless of how many cell-days its envelope sweeps. No sampling; all matched rows are kept. |
| 27 | **Strategy subjects stay coarse; splits are evidence-led.** Keep `biochar` / `silvopasture` / `agroforestry` as subjects and record the execution variant in `rationale`. Split a subject only once ≥2 sources exist for a specific variant. | §3.6. No one-hot column is ever born degenerate. |
| 28 | **Owner countersignature deferred.** Everything continues to run at `agent_reviewed`; no promotion ceremony is scheduled. | §7.3 of the strategy doc. Every receipt and served citation keeps carrying `agent_reviewed_pending_owner_signature`; nothing is owner-approved. |

### 2.2 Third decision round, 2026-08-15 (post prod-walk)

| # | decision | bearing |
|---|---|---|
| 29 | **Rewrite the three `geo.mv_strategy_recommendations_*` definitions now**, while their source tables are empty: join `agri.spatial_cell.geometry` on the real `cell_id`, drop the `random()` lat/lon, rename `causal_benefit_tau` to an effect-utility term. | §4.8. Must land before any model writes `agri.strategy_selection_candidate`. |
| 30 | **Practitioner works are admissible at `medium` confidence**, cited by ISBN + publisher URL, verified by **page-level quote** rather than DOI resolution. Peer-reviewed trials keep `high`. | §4.9. Mark Shepard's *Restoration Agriculture* and similar practice-rooted works are in scope. |
| 31 | **Decision 28 is reversed.** The owner countersigns after Phase E, and the sign-off **reasoning and source list are stored**, not just referenced by a bare string. | §4.9. |
| 32 | **Unlock ERA5-Land for a 9 km grid** rather than locking in at 0.5°. | §4.8. Requires confirming CDS credentials and changing `ERA5_LAND_REQUESTED_GRID_DEGREES` from 1.0 to 0.1. Owner chose resolution over sequencing. |

---

## 3. Assessment: what these decisions fix, and what they do not

The owner asked directly whether the answers cover the problem. Three do, two do not, and one is a
misunderstanding worth correcting.

### 3.1 Decisions 1 + 3 fix the actual failure — and decision 1 is nearly free

The failure demonstrated in the grilling was that *Pisum sativum* (`fit`) and *Cercocarpus ledifolius*
(`marginal`) produce **bit-identical design rows** with contradictory targets, because Model A carries no subject
identity and both envelopes are `{"aridity": "semi_arid"}`.

Model B **already** builds a subject one-hot; only Model A suppresses it, at
[`recommendation_models.py:347-355`](../../src/agri_data_service/method/ml/recommendation_models.py#L347-L355),
where `subject_vocabulary` is populated for `strategy_selection` and set to `()` otherwise. `build_design_row`,
`design_feature_names`, the artifact serialisation, and `rank_subjects` all already thread a vocabulary through
(`subject_vocabulary` appears at lines 177, 192, 202, 272, 326, 356, 391, 451, 662, 686, 726, 812, 920).

**Turning on subject identity for Model A is a one-condition change plus the comment that justifies it.** It is
not a migration and not a schema change. It should be made *before* any further harvesting, because it changes
what a good label is.

Species traits from `agri.species` (§1.2) are the second half: they let a held-out species be predicted from what
kind of plant it is rather than from an ID the fold removed. Together these are the fix.

### 3.2 Decisions 12 + 13 are correct and are what makes region-conditional ranking possible

The owner wants "silvopasture is better for drought-driven regions, biochar for moist fire-prone regions." That is
a model conditioning on site climate. In a single-cell fit the site features are near-constant across rows, so no
such conditioning can be learned. Multi-cell, multi-year coverage is not a nice-to-have — **it is the mechanism by
which the requested behaviour becomes learnable**, and §1.4 shows the data is already there.

Budget: `_MAX_TRAINING_ROWS` = 200 000, `bounded_issue_days` samples the 1st and 15th (24 days/yr), and mapping is
per cell. So the design-row budget is `labels × matched_cells × 24 × years ≤ 200 000`. With 397 cells and 3 years
(72 issue days), a label matching every cell yields ~28 600 rows — about 7 such labels fill the budget. Real
envelopes match a subset, but **the budget must be planned, not discovered.** Concretely: pick coverage first
(397 cells × 3 years), then labels follow.

### 3.3 Decision 11 is a misunderstanding — ranking does not undo training imbalance

Ranking happens at *inference*: `rank_subjects` scores candidates and sorts them. The imbalance is at *training*:
a one-term `{"aridity": "semi_arid"}` label matches nearly every semi-arid cell-day and contributes thousands of
design rows, while a five-term trial-site label contributes a handful. Weights carry `confidence_weight` only
([`recommendation_models.py:414`](../../src/agri_data_service/method/ml/recommendation_models.py#L414)) — nothing
normalises by instances-per-label.

The vague label therefore *moves the fitted coefficients* far more than the precise one. Ranking the outputs
afterwards cannot undo a function that was fitted wrong. The fix is a training-time weight of roughly
`confidence_weight / n_instances_for_that_label`, so that one label = one unit of evidence regardless of how many
cell-days its envelope happens to sweep. This is the same principle the code already states in its own caveat —
"effective sample size is N labels, not N design rows" — but the caveat is only printed, never applied to the fit.

### 3.4 "Fewer labels" has a hard floor, and turning on subject identity sets it

These two decisions pull against each other, and the arithmetic should be explicit.

With a subject one-hot, a species whose only source is the held-out fold has an all-zero column in training and
predicts from the intercept — the degenerate case the original code comment feared. So:

- **≥2 sources per species is the functional minimum** (hold one out, one remains).
- **≥3 is safe** — with exactly 2, a single disagreement between them makes the fold uninformative.
- Per species, outcome bands from range-structured sources give ~1.5–2.5 labels per (species, source).

For ~25 anchor species: 25 × 3 sources × ~1.5 bands ≈ **110 `species_fit` labels** across ~40–75 distinct works.
That is the floor for a model with subject identity. It is an order of magnitude below the 600–1200 the strategy
doc proposed and an order of magnitude above the 10 that exist. The owner's instinct ("not that many") is right;
"as few as possible" is not — below ~2 sources per species the one-hot must stay off.

**If a smaller total is required, cut the species count, never the sources-per-species.** 12 species × 3 sources
beats 40 species × 1 source at every point.

### 3.5 The largest gap: the objective is not in the label vocabulary

The owner's goal is choosing plants "for the job of improving wildfire, drought and water, vegetation, and soil
moisture management." The schema does not express that claim.

`species_fit` outcomes are `fit` / `marginal` / `unfit` — a closed CHECK vocabulary meaning *this species is
climatically suited to these conditions*. There is no outcome meaning *this species improves soil moisture*.
Today objectives enter only as **provenance weights over harvest slices** — `WILDFIRE_EVIDENCE_SLICES`,
`WATER_EVIDENCE_SLICES`, whose own comment says they are "scoring weights over LABEL PROVENANCE … never a claimed
wildfire or hydrological effect."

So a Model A ranking today means *climatically suitable, and its evidence came from the fire literature* — not
*predicted to reduce fire risk*. That distinction must survive into any UI copy.

**Owner decision, 2026-08-14: build a third model for effect.** "Just because something will grow in an area does
not mean that it will produce a desired outcome." Suitability filters the candidate set; the product question is
which candidates *move* an objective — wildfire prevention, drought mitigation, water conservation, soil
amendment, carbon capture. Design in §4.5.

**Model A is kept and not rebuilt to carry effect.** The two claims stay separate because:

- They have different evidence bases. Suitability literature is broad and range-structured; effect literature is
  narrower, measures a specific outcome variable, and is more often contested.
- They fail independently. A species can be perfectly suited and useless for the objective, or actively harmful —
  a fire-prone species thriving in fire country is exactly the recommendation the system must never make.
- **The recommendation is a conjunction (suitable AND effective).** Merging them into one target trains a model on
  `P(good recommendation)` and destroys the ability to say which constraint failed — which is the basis of the
  plane's entire citation posture.
- Almost no single source labels both axes for the same species, so a merged label kind would rarely be
  satisfiable from one cited work.

### 3.6 Smaller gaps worth knowing

- **Matched-only training is epistemically correct but has a consequence.** Only rows where the site satisfies the
  envelope train. A source says nothing about conditions it did not describe, so excluding the rest is right. But
  it means the model sees negatives *only* where some source affirmatively labelled something `unfit`. Therefore
  **sources that state ranges (optimal vs absolute) are strictly more valuable than sources that state a single
  site**, because banding a range yields fit/marginal/unfit for one species from one work. This should drive
  source selection more than any other criterion. It is also the one respect in which ECOCROP's *structure* was
  attractive even though its content is too coarse (§1 of the strategy doc, §6.1).
- **Fungi and mycelium do not fit the current envelope vocabulary.** The four expressible terms are climate-only;
  fungal suitability turns on substrate, host association, and soil organic matter, none of which have a term.
  Defer until `soil_texture` is expressible at minimum, and expect to need more.
- **Companion planting is a schema-ready but model-absent feature.** `companion_relationships` exists and is
  empty. It is a relation between two subjects; the label plane is single-subject, and a one-hot over pair-atoms
  learns nothing compositional. Treat as post-baseline work with its own design pass.
- **Strategy specificity has the same floor as §3.4.** Splitting `biochar` into execution variants multiplies
  subjects, and each variant then needs ≥2 sources. Split only where the sources already exist; otherwise keep the
  coarse subject and record the variant in `rationale`.

---

## 4. What must be added

### 4.1 Data — production loads

| # | item | why | cost | blocking? |
|---|---|---|---|---|
| D1 | Populate `agri.species` for the anchor set (~25–40 species) with trait columns | the Model A feature channel (§3.1); table exists, 0 rows | small; USDA PLANTS public domain + TRY (CC BY) per-value citation | **yes — blocks the fix** |
| D2 | Per-cell elevation → `agri.topography_profiles` | flips `elevation_m` from unexpressible; it is in 11 of 30 existing envelopes | small load, **non-trivial semantics** (§4.4) | no, but high value |
| D3 | Per-cell soil texture from the published SoilGrids COGs → `agri.soil_profiles` | flips `soil_texture`; in 7 of 30 envelopes | medium; rasters already on R2 | no |
| D4 | USDM history backfill into `drought_polygon_snapshot` | un-zeroes the completeness mask; machinery exists (`USDM_HISTORY_SOURCE`) | medium | no — not in `SITE_COVARIATE_FEATURES` |
| D5 | Populate `agri.companion_relationships` | the companion-planting feature | deferred | no |

### 4.2 Processes — authoring-time gates that do not exist

Each of these prevents a failure that has already happened at least once in the 32-label harvest.

- **P1 — Envelope expressibility guard.** Reject at authoring time any label with an empty envelope or with zero
  *expressible* terms. Two labels were already lost silently (§1.7); a label carrying only `elevation_m` and
  `soil_texture` would load, map to `match_state = 'unexpressible'`, and never train, while counting toward the
  release totals as though it were evidence.
- **P2 — Envelope selectivity pre-flight.** For each candidate label, before harvest: how many of the 397 cells
  would match, over how many issue days, and how many design rows that implies. Reject envelopes that match
  ~everything (no discriminative content) or ~nothing (no training signal). This is the single most valuable
  missing artifact — without it, envelope quality is unknowable until after loading.
- **P3 — Collision register.** Compute each candidate's design-row fingerprint (envelope descriptors only, since
  that is what the model sees) against every existing label. Identical fingerprint + different outcome = a
  contradiction that must be resolved by adding a distinguishing expressible term or by rejecting the weaker
  label. This would have caught both *Pisum*/*Cercocarpus* and *Vicia villosa*/*xTriticosecale* before load.
- **P4 — Per-label instance normalisation in training weights** (§3.3).
- **P5 — Source-independence field.** A recorded, non-blocking declaration on each source: which trial network,
  programme, or dataset it derives from, so that folds sharing an origin are visible in the fold report rather
  than invisible. Cheap; owner is indifferent to enforcement, so record and display, do not gate.
- **P6 — Range-structured source preference**, written into the harvest brief (§3.6).
- **P7 — Subject naming discipline.** The existing vocabulary mixes taxa (*Pinus ponderosa*) with functional
  categories (`cover_crops`). Harmless while the one-hot is off; poisonous the moment §3.1 lands. Fix before, not
  after.

### 4.3 Artifacts — the reference the pre-flight needs

- **A1 — Cell-climate reference.** Derived MAP / MAT / frost-free-days / aridity class for all 397 POWER cells,
  computed by `load_site_climate_terms` over the available window, materialised and refreshed. Every question of
  the form "will this envelope match anything?" is unanswerable without it, and P2 is impossible without it. Data
  to build it exists today (§1.4).
- **A2 — Envelope descriptor fingerprint function**, shared by P3 and the training path so the register cannot
  drift from what the model actually sees.

### 4.4 The elevation and soil-texture semantics decision (do not skip)

A `nasa-power-0.5-degree` cell is ~55.66 km across. PNW relief inside one such cell can exceed 2000 m, and a
single cell contains many soil textures. So D2 and D3 are **not** mechanical loads:

- A single mean elevation per cell is close to meaningless at this resolution.
- The envelope match currently asks "does the site value fall inside the envelope range (± tolerance)?" For a
  cell-aggregated quantity the honest question is instead "**does the cell's range overlap the envelope's
  range?**" — a different predicate.
- Loading a mean and matching it with the existing containment predicate would produce confident, wrong
  exclusions: a montane species would be excluded from a cell whose mean is lowland but which contains ample
  montane terrain.

Decide the aggregation (min/median/max, or dominant class + fraction for texture) and the match predicate
*together*, in the same review that flips `ENVELOPE_TERM_SUPPORT`. Note also that flipping either term **narrows**
every envelope already carrying it, so previously-matched instances become excluded and every prior receipt goes
stale — a re-map, not an increment.

### 4.5 Model C — the effect plane

Model C answers **"does this species move this objective, at this site?"** Its label is a
`(species, objective, condition envelope) → effect` claim. Site-conditioning matters as much as it does for
Model B: a deep-rooted phreatophyte conserves water in one setting and lowers the water table in another, so the
same envelope machinery applies unchanged.

**Ship it in two stages. The first needs no migration.**

**Stage 1 — prototype inside the existing schema.** Harvest species-effect labels as `label_kind =
'strategy_outcome'` with `subject` = the species and `harvest_slice` = the objective, and load them **as their own
release**. Two verified facts make this safe:

- Training is pinned to a `release_key` (`_train(model_kind, release_key, schema_version, persist)`), and
  `select_matched_training_instances.sql` filters by kind and release — **not** by slice. A separate release is
  therefore the isolation boundary; without it these labels would silently contaminate Model B's training set.
- `harvest_slice` is inside `label_payload`, so it participates in `label_checksum` and `label_key`
  (`expert_label_plane.py:216-241, 251-255`). Two labels for the same species and envelope but different
  objectives get distinct keys rather than colliding into one row.

Stage 1 trains through the existing `strategy_selection` path, which already carries a subject one-hot. Cost: a
harvest and a release. Zero schema work. **It tests the question that actually gates Model C — does species-level
effect evidence exist in harvestable form? — before any migration is paid for.**

**Stage 2 — promote to a first-class kind, once Stage 1 shows which objectives yield labels.** A migration adding
`label_kind = 'species_objective_outcome'`, a CHECK'd `objective` column, and a **directional** outcome vocabulary
`beneficial` / `neutral` / `detrimental` (ordinal utility 1.0 / 0.5 / 0.0, matching `OUTCOME_UTILITY`'s existing
shape). Three reasons not to simply reuse `effective/mixed/ineffective` permanently:

- **`ineffective` ≠ harmful.** The current vocabulary cannot say a species *worsens* an objective, so the ranker
  can only fail to reward it — never penalise it. For wildfire and water that is a safety-relevant gap.
- `mixed` conflates *evidence strength* with *effect size*. Direction belongs in `outcome`, evidence strength
  already has a home in `confidence`. Keeping them separate is what makes the banding rule auditable.
- The artifact should say what it is; a Model C receipt reading `model_kind = strategy_selection` is a lie the
  lineage has to carry forever.

**Migration detail that must not be missed:** if `objective` becomes a column it **must** enter `label_payload`,
or two labels differing only by objective produce the same `label_checksum` and `label_key` and silently resolve
to one row. In Stage 1 `harvest_slice` already covers this, which is precisely why the prototype is sound.

**Composition rule (decisions 21–23) — two annotated lists, no blended score.** The surface shows *two* ranked
lists: "best suited here" from Model A, and "most effective for this objective" from Model C. The effect list is
**not filtered** by suitability; instead **every row carries its suitability verdict and probability**, so a
species that cannot establish at the site is visibly marked rather than silently ranked or silently dropped.

Objective conflicts are **surfaced, not priced**: rank on the selected objective, and display any `detrimental`
verdict on a *different* objective as an explicit flag with its citation. There is no net score across
objectives — pricing a water harm against a carbon gain is a value judgement the evidence does not support.

The consequence to hold onto: the system never collapses "grows here" and "does the job" into one number. That is
the direct expression of "just because something will grow does not mean it produces a desired outcome," and it
keeps both evidence chains separately citable.

**Not all five objectives are equally harvestable at the species level.** This is the main risk to Model C, and it
should shape the first harvest:

| objective | species-level evidence | verdict |
|---|---|---|
| soil amendment | strong — N-fixation, SOC accrual, root architecture, mycorrhizal association | **start here** |
| carbon capture | good for woody species — biomass accrual rates, SOC | **start here** |
| wildfire prevention | mixed — rigorous fuel-property literature (moisture content, volatile oils, fuel-bed structure) exists, but many "firewise plant lists" are not evidence-graded | harvest with discipline; prefer fuel-property studies over list-style guidance |
| water conservation | mostly a *practice*-level question; species-level evidence often concerns water *use*, and the well-documented cases are frequently **detrimental** (phreatophytes, juniper encroachment) | Model B territory first; strong argument for the `detrimental` outcome value |
| drought mitigation | **naming trap** — drought *tolerance* is a suitability trait and is already a column (`agri.species.drought_tolerance`); drought *mitigation* as an effect on a system is thin at species level | do not harvest until the claim is defined precisely |

**Traits matter more for Model C than for Model A.** Effect mechanisms are trait-driven — N-fixer → soil
amendment; biomass accrual → carbon; foliar moisture and volatiles → fire behaviour. That means Model C can
generalise to unlabelled species through traits rather than through the subject one-hot alone, which raises the
value of D1 (populate `agri.species`) from "helpful" to "load-bearing".

**Label budget consequence.** Each anchor species now needs suitability sources *and* effect sources per
objective. At ≥2 sources per (species, objective), two objectives adds roughly another ~100 labels on top of
§3.4's ~110. This is why cutting the species count is not merely acceptable but necessary: **a realistic anchor
set is 12–15 species**, carried deeply on both axes, rather than 25 carried thinly.

### 4.6 Soil moisture into the covariate schema (settled: after the Phase B baseline)

`soil_wetness_surface` / `_root_zone` / `_profile` are governed, on the POWER grid, and span the same window as
the met signals (§1.5). Adding them to `covariate_feature_schema` v3 gives both models a direct read on one of the
stated objectives. **Decision 25: promote after the Phase B baseline, not before** — a schema-version bump with
its own declared-gap accounting should not move underneath the first honest production number.

### 4.7 A dedicated ML training-and-serving service — not yet, and the first step is smaller

Raised by the owner 2026-08-15. Assessed here rather than actioned, because two things already exist that change
what the question is.

**Training orchestration is already solved.** Training is a registered durable job definition
(`agri.recommendation.train`, queue `forecast`, 1800 s lease, 1500 s budget, 3 attempts) invoked through
`agri-cli jobs-run`, on the same `agri.job_*` ledger every other lane uses. Queue, lease, retry, and receipt
semantics are not the missing piece. What is missing is a *scheduled host* — and the existing Railway cron pattern
already solves that at a fraction of the cost of a service.

**Serving already has a settled answer.** Two standing architecture decisions apply: *agri is a local CLI*, and
*we persist everything we serve*. A recommendation is a ranked list per (cell, objective) — 397 cells × a handful
of objectives × a small candidate set. **That precomputes to a table the Next.js app reads over tRPC like every
other layer.** Standing up a live inference service would reopen a settled decision without a driver behind it.

**Why not now:**

- **There is nothing to train.** Prod holds 0 training instances and 10 `species_fit` labels. Building an
  execution host for a workload whose runtime and memory profile has never once been measured is precisely the
  mistake the rest of this plan is structured to avoid — Phase B exists to produce that measurement.
- **The models are small.** Multinomial logistic regressions over ≤200 000 design rows: seconds of CPU, no GPU, no
  distribution. The existing 1500 s budget is already generous by orders of magnitude.
- **A fourth Railway service repeats a known-expensive setup.** The cron-service build configuration is a
  documented trap (two dashboard settings that must change together; `RAILWAY_DOCKERFILE_PATH` that can never
  work).
- **Splitting costs a shared package.** Training needs the same DB access, SQL verbs, checksum functions, and
  receipt writers as the rest of the service. A separate service means extracting a shared library or duplicating
  the lineage code — and duplicated checksum logic is the one thing this plane cannot tolerate.

**Trigger conditions — revisit when any one of these is true:**

1. A real training run's wall-clock or peak memory exceeds what the shared service absorbs comfortably (measure at
   Phase B).
2. Inference is needed for **arbitrary coordinates off the 397-cell grid**, or for user-supplied hypothetical
   conditions ("what if MAP were 400 mm?"). Precompute cannot answer either.
3. Companion-planting combinatorics grow past what precompute can enumerate (pairs are fine; guilds are not).
4. Training cadence becomes continuous rather than per-release, making an isolated failure domain worth its cost.

**Recommended increment if a host is wanted before those triggers:** a Railway cron that drains the `forecast`
queue, reusing the existing job runtime — not a new service, not a new deployment topology.

### 4.8 The serving path already exists — and it is booby-trapped

Prod walk, 2026-08-15. Three findings, one of them urgent.

**The zoom-tiered serving pattern is already built end to end.** `geo.strategy_recommendations_tiles(z, x, y)`
returns MVT and switches tier by zoom: **z ≤ 6 → `mv_strategy_recommendations_coarse`, z ≤ 11 →
`_regional`, else `_detail`**. All three matviews exist and are populated; the app reads
`geo.mv_strategy_recommendations_regional` as `STRATEGY_CONTEXT_MATVIEW`
(`src/lib/server/services/regional-context.ts:466`), tagged `evaluation_only_model`. There is also a refresh job.
**Nothing needs designing here — the surface the owner asked for exists.**

**URGENT — the matview definitions fabricate coordinates.** `mv_strategy_recommendations_detail` is defined as:

```sql
(37.5 + random() * 5.0)  AS lat,
(-120.5 + random() * 5.0) AS lon
```

Every recommendation is scattered at a **random** point in a 5° × 5° box and served as a map tile. It is dry today
only because its upstream is empty (`agri.strategy_selection_candidate` = 0 rows, `agri.strategies` = 0 rows), so
all three matviews hold 0 rows.

**The trap fires precisely on success.** The moment Model B or Model C writes selection candidates, the next
refresh scatters them at random coordinates and serves them. **These definitions must be rewritten before any
model output reaches `strategy_selection_candidate`, not after.** The rewrite is to join
`agri.spatial_cell.geometry` on the real `cell_id` — the geometry the training instances already FK to — instead
of synthesising lat/lon.

**Second, quieter trap: the served column vocabulary claims causality the models may not assert.** The matviews
expose `causal_benefit_tau` (mapped from `expected_effect`). Every recommendation receipt is structurally
`evaluation_only` with `CHECK (NOT publication_authorized)`, and the 0013 causal plane is untouched. A column
named `causal_benefit_tau` on a literature-grounded, evaluation-only ranking is a claim the evidence chain cannot
support. Rename to an effect-utility term in the same rewrite.

**Coverage gaps that do NOT block training.** `signal_coverage_audit` reports `complete` for 31 564 windows and
`no_data` for 1 965. The `no_data` set is `surface_shortwave_radiation` (all 397 POWER cells) and the ERA5-derived
soil/VPD signals (98 NDVI cells each). **None of the seven signals Model A and Model C actually read is affected**
— `SITE_COVARIATE_FEATURES` uses temperature, precipitation, humidity, wind and dew point, and the aridity proxy
is Hargreaves-Samani, which is temperature-based by construction and needs no radiation input. Site climate is
derivable on all 397 cells for the whole target window.

**Grid resolution — 397 cells is a source constraint, not a choice.** The 0.5° / 55.66 km grid is NASA POWER's
native resolution; the label plane joins it because that is where the meteorology is. Options:

| path | resolution | honest? |
|---|---|---|
| stay on `nasa-power-0.5-degree` | 55.66 km / 397 cells | yes — matches the source |
| **ERA5-Land** (`era5-land-0.1-degree`, `ERA5_LAND_NATIVE_GRID_RESOLUTION_M = 9_000`) | **9 km** | yes — a real finer source, already coded, already has a coverage-contract support key |
| interpolate POWER onto the 0.25° NDVI grid | 27.8 km | **no** — invents structure the source does not have |

ERA5-Land is the only non-fabricating way to go finer, and it is a **declared credential gap**, not missing code:
"ERA5-Land is in the governed plan but was never requested: no CDS credentials are provisioned"
(`covariate_declared_gap.sql`). Two caveats before treating it as easy: it needs CDS credentials confirmed
present, and `ERA5_LAND_REQUESTED_GRID_DEGREES` is currently **1.0**, so the request as configured would return
data *coarser* than POWER — that constant must change too.

### 4.9 Certification: sign-off reasoning and source traceability at the schema level

Decision 31 reverses the deferral. What that needs, kept minimal.

**Training traceability already exists — nothing to add.** The chain is complete and checksum-bound:
`recommendation_training_receipt.label_release_id` → `expert_label_release` → `expert_label.source_id` →
`expert_label_source` (`doi`, `source_url`, `title`, `journal_or_publisher`, `publication_year`,
`source_checksum`). Every receipt also carries `label_review_tier`. A trained artifact can already name every work
it learned from.

**Serving traceability is the actual gap.** The matviews expose scores with **no lineage columns at all** — a
served tile cannot say what it was learned from. Fold into the decision-29 rewrite: carry `label_release_key`,
`source_count`, and `label_review_tier` through to the matviews, so any recommendation on the map can be traced to
its release and its review tier without a second query.

**Sign-off reasoning needs a home, and the pattern already exists.** `expert_label_release` stores the harvest
document as `harvest_document_uri` + `harvest_document_checksum`. Mirror it exactly: a signature document (the
owner's reasoning, the acceptance criteria applied, and the source list as signed) referenced by
`owner_signature_reference` plus a new `owner_signature_checksum`. **Fold that single column into the Model C
Stage 2 migration** rather than raising a migration of its own — no new plane, no new table, one added column
reusing a pattern already in the schema.

**Admissibility criteria the signature attests to** (decision 30). Record these in the signature document so the
sign-off is reproducible rather than a bare assertion:

- Peer-reviewed trial or study with a resolving DOI → `confidence = high`.
- Practitioner or applied work with a stable ISBN/publisher URL, verified by a page-level attributed quote →
  `confidence = medium`. Passes `ck_expert_label_source_locator` on `source_url`; `doi_resolves` is recorded
  `false` without blocking, since only `citation_check_refuted` gates promotion.
- Compendium or occurrence-derived envelope → `confidence = low`.
- The verifier's job for a non-DOI work is **claim-to-page confirmation**, not locator resolution. That
  distinction belongs in the harvest brief, because it is a different verification action.

---

## 5. Revised plan

Ordered so that nothing expensive is spent before the cheap things that change its value.

**Phase A — fix the model specification (no new labels, no migration).**
Turn on the Model A subject one-hot (§3.1); add species-trait features sourced from `agri.species`; apply P4
weighting as `confidence_weight / instance_count` (decision 26); apply P7 naming discipline. Nothing here needs a
harvest.

**Phase B — get a real production baseline, which has never existed (§1.1), mapping once and training twice.**
Map the existing 28 agent-reviewed labels across all 397 POWER cells over the available window (2023-05 → 2026-08,
inside the design-row budget of §3.2) and persist the instances **once**. Then train twice on those same
instances — once on the pre-Phase-A spec, once with subject identity on (decision 17; the one-hot is applied at
train time, so this costs one extra fit, not one extra mapping). Report LOSO against the majority baseline for
both, plus matched/excluded/unexpressible ratios.

**This is the first number that will mean anything**, and it costs no new labels. Expect both runs to be weak —
10 `species_fit` labels over 10 sources is below any useful threshold, and with one source per species the
one-hot is the degenerate case the code comment warns about. The point is the honest floor, the before/after
delta, an end-to-end validation of the pipeline against prod, and a **measured runtime and memory profile**, which
§4.7 needs before any service question can be answered.

**Phase C — build the pre-flight before harvesting (§4.3, §4.2).**
A1 cell-climate reference, then P1/P2/P3 gates. Cheap, and everything after this is wasted without it.

**Phase D — populate `agri.species` for the anchor set (D1).**

**Phase E — targeted suitability harvest, sized by §3.4 and decision 19.**
**12–15 anchor vascular species** (trees, shrubs, grasses; fungi deferred) × ≥3 sources, range-structured sources
preferred (§3.6), every candidate passed through P1–P3 before it is written. Verification cost scales with labels,
so this is also the adversarial-verifier budget: plan it explicitly rather than discovering it.

**Phase E′ — Model C Stage 1, in parallel with E and sharing its species set.**
Species-effect labels for **soil amendment, carbon capture, and wildfire** (decision 18), harvested as
`strategy_outcome` with species subjects and objective slices, loaded as their own release. No migration.
Wildfire is admitted on a restriction: **fuel-property studies only** — foliar moisture, volatile oils, fuel-bed
structure — and list-style "firewise plant" guidance is not admissible evidence. Write that restriction into the
harvest brief, because it is the difference between a real signal and a laundered one.

The deliverable is a per-objective answer to "does harvestable species-level effect evidence exist?" — which is
what decides whether Stage 2's migration is worth paying for, and which objectives it should cover.

**Phase F — Model C Stage 2 migration**, scoped by what E′ found: `species_objective_outcome` kind, CHECK'd
`objective` column (in `label_payload`), directional `beneficial`/`neutral`/`detrimental` outcomes, and the
composition rule (suitability gates, effect ranks).

**Phase G — envelope expansion (D2/D3), storing ranges and matching on overlap (decision 24).**
Ordered by the unexpressible-term counts Phases B, E, and E′ actually report, not by guess. Requires a new overlap
predicate alongside the existing containment one, and a re-map, since flipping either term narrows every envelope
already carrying it.

**Phase H — soil moisture into covariate schema v3** (decision 25), after Phase B has produced its baseline.

**Parallel, non-blocking:** D4 USDM backfill.
**Explicitly not scheduled:** the owner countersignature ceremony (decision 28). Everything runs at
`agent_reviewed`; every receipt and served citation keeps carrying `agent_reviewed_pending_owner_signature`, and
nothing in the system is owner-approved. This is a standing, deliberate posture, not an oversight — but it does
mean the `approved` transition and the receipt re-issue at the signed tier remain **unexercised code paths**.

---

## 6. Open decisions for the owner

**All decisions posed as of 2026-08-15 are closed** — see §2 and §2.1. What remains is research and measurement,
not owner judgement:

1. **The anchor set itself.** Which 12–15 vascular species? This is a research task, not a decision: candidates
   must be selectable on *both* axes (≥3 suitability sources, and effect evidence for at least one of soil
   amendment / carbon capture / wildfire), and they must be common deployable PNW material. Existing subjects are
   a starting point, not a constraint.
2. **Drought mitigation and water conservation as Model C objectives.** Deferred, not rejected. Drought
   mitigation needs its claim defined distinctly from the existing `drought_tolerance` trait — which is
   suitability, not effect — before it can be harvested at all.
3. **Provenance of the 0.025 / 0.541 figures** (§1.1). Not reproducible from prod and not located. If a local
   database still holds those instances it is worth recovering; otherwise Phase B supersedes them.
4. **§4.7 service question — measurement, not decision.** Revisit only against the four trigger conditions, the
   first of which Phase B measures directly.

---

## 7. Verification ledger

- **Prod-verified 2026-08-14, read-only** (`DATABASE_URL_SYNC`, script retained in session scratchpad): all row
  counts in §1.1–§1.3; `spatial_cell` grid split; `signal_observation` size estimate via `pg_class.reltuples`;
  signal names, windows, and per-signal cell counts via `agri.signal_coverage_audit`; label counts by
  kind/state/outcome.
- **Verified in-repo:** `subject_vocabulary` threading and the Model A suppression site; `build_design_row`
  feature assembly and the identical-vector demonstration; `weights.append(confidence_weight)` with no instance
  normalisation; `evaluate_envelope` treatment of unexpressible terms; matched-only selection predicate;
  `_MAX_TRAINING_ROWS`, `_MAX_INSTANCES_PER_LABEL`, `bounded_issue_days`; `agri.species` and
  `agri.companion_relationships` column lists; the harvest document's 30 kept / 2 rejected split and the two
  empty-envelope entries; `label_payload` contents (confirming `harvest_slice` participates in `label_checksum`
  and `label_key`); training pinned to `release_key` with kind-and-release filtering and **no** slice filter,
  which is what makes the §4.5 Stage 1 release boundary load-bearing.
- **Not verified — external:** the per-objective evidence assessment in §4.5 is a domain judgement from general
  literature knowledge, not a citation survey of this repo or a web-verified source review. Treat the table as a
  harvest-ordering hypothesis to be tested by Phase E′, not as an established finding.
- **Not verified:** the provenance of the 0.025 / 0.541 figures (§1.1) — they are not reproducible from prod and
  their origin was not located. Whoever ran them should record where.
- **Superseded, do not act on:** strategy doc §5 volume targets, §6.1 ECOCROP-first sequencing, §6.4 GBIF download
  splitting, §8 Phases 0–4.
