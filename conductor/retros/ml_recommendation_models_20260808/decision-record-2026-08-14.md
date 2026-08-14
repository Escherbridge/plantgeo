---
type: decision-record
---

# Decision record: expert label plane, `agri_covariates_v2`, Models A and B

Dated 2026-08-14. Every number below was measured on a disposable database
(`agri_reco` on the local warehouse, loopback port 5442) seeded with a bounded,
provenance-preserving slice of the production Boise streams read **read-only**.
Nothing in this record was measured on, or written to, the production warehouse.

## What shipped

**Migration `20260814_0022` — the expert label plane.** Five additive tables in
`agri`, no foreign key into the `20260725_0013` causal plane:

- `expert_label_source` — work/edition/DOI identity with a licence posture and a
  content checksum.
- `expert_label_release` — one harvest run, its document digest, its counts by
  review state, and the review tier the whole release travels under.
- `expert_label` — kind, subject, CHECK-validated condition envelope, outcome,
  ordinal confidence plus its declared numeric weight, full citation lineage, the
  adversarial citation verdict, and the review state.
- `expert_label_training_instance` — the envelope evaluated against the governed
  streams for one (label, cell, day), carrying per-term verdicts **and the
  envelope terms our streams cannot express**.
- `recommendation_training_receipt` — the training receipt, pinned to a label
  release, an artifact digest and a job-ledger output.

Three functions: `expert_label_envelope_valid` (the CHECK-validated key
vocabulary), `expert_label_release_summary` (the one canonical accounting), and
`guard_expert_label_review_change` (the review state machine and the
immutable-once-reviewed rule).

**Migration `20260814_0023` — `agri_covariates_v2`.** Three body-only forward
loads: `covariate_feature_schema` (v1's 40 positions verbatim, then 7 more),
`covariate_daily_features` (v2-only branches), `covariate_declared_gap` (v2
declares what it did not build).

**Python**, split across two layers because the layer import contract forbids
SQLAlchemy in `method/`: `method/ml/expert_label_plane.py` (harvest contract,
checksums, envelope verdict rules), `method/ml/covariates_v2.py` (vector types and
the site-climate math), `method/ml/recommendation_models.py` (design matrix, fit,
evaluation, artifact, ranking) hold the pure half;
`execution/recommendation_lane.py` holds every session-bound read and write, and
`execution/recommendation_commands.py` the five CLI verbs.
`routes/recommendations.py` is the artifact-pinned serving surface.

## Measured results

### Label release

28 agent-reviewed labels (10 species_fit, 18 strategy_outcome) from 29 DOIs, plus
2 refuted-and-rejected and 2 unloadable (no condition envelope). **0 approved** —
that state requires the owner's signature and nothing here can mint it. See
`owner-signature-request-2026-08-14.md`.

### Envelope → governed stream mapping (Boise cell `na-sample:1deg:p043.00:m116.00`)

77 evaluation days (the 1st and 15th of each month, 2023-06-01 → 2026-08-01),
`as_of = 2026-08-10T00:00:00Z`, schema `agri_covariates_v1`.

| Label kind | Labels | Instances | matched | excluded | unexpressible |
| --- | ---: | ---: | ---: | ---: | ---: |
| species_fit | 10 | 770 | 401 | 369 | 0 |
| strategy_outcome | 18 | 1386 | 898 | 411 | 77 |

**Envelope-term coverage, which is the data-completion finding of this track:**

| Term | Support | Note |
| --- | --- | --- |
| `mean_annual_precipitation_mm` | direct | trailing-365-day sum of the governed precipitation stream |
| `mean_annual_temperature_c` | direct | trailing-365-day mean |
| `growing_season_frost_free_days` | direct | trailing-365-day count of `air_temperature_min > 0` |
| `aridity` | **derived proxy** | UNEP index over Hargreaves-Samani reference ET from governed tmin/tmax + cell latitude; recorded as `derived_proxy`, never as an observation |
| `elevation_m` | **unexpressible** | `agri.topography_profiles` holds 0 rows; `spatial_cell` carries geometry without height. Hit 847 times across both kinds. |
| `soil_texture` | **unexpressible** | `agri.soil_profiles` holds 0 rows; the published SoilGrids rasters are not ingested as cell observations. Hit 539 times. |
| `usda_hardiness_zone` | **unexpressible** | a derived classification the warehouse does not compute |

Every unexpressible term is stored on the instance row rather than dropped, so
"which literature conditions our warehouse still cannot check" is a query, not a
memory.

### `agri_covariates_v2`

- v1 is unchanged, and this was **measured, not asserted**: the pre-change
  function bodies were loaded from git into a second disposable database with the
  same seeded data, and v1's output over 2023-01-01 → 2024-12-31 (**29,240 rows**,
  2 years × 40 features) is **byte-identical** to the post-change function's. The
  v1 feature registry is byte-identical too, and is additionally pinned as an
  explicit expectation in `tests/test_covariates_v2_schema.py`.
- v2 adds indices 41–45 (`mc_forecast_*`) and 46–47 (semiannual day-of-year
  harmonics). The lookback window is unchanged at 28 days.
- The MC block works end to end on real governed data: for the NDVI cell
  `sentinel2-ndvi-0p25deg:43.6250:-116.1250`, days 2026-08-06 onward carry
  p10/p50/p90 and a lead-day count from the one finalized iteration (cutoff
  2026-08-05, `recorded_at` 2026-08-05T20:11:01Z), admitted only for days at or
  after that recording instant.
- The MC block is **empty for the meteorology pilot cell**, correctly: no
  forecast series is bound to that cell, so there is no iteration to read. Partial
  stays partial.
- AnEn and ML-ridge forecast statistics are **declared gaps**
  (`agri.covariate_declared_gap('agri_covariates_v2')`), not empty columns.

### The per-issue-date gate, and what measuring it revealed

The plan asks v2 to replace the single global knowledge cutoff with per-issue-date
gating. Implemented as a strict filter first, it produced **zero** admissible
meteorology for every day in the pilot window. The cause, measured: the Boise
history was **bulk-backfilled**. Its three source releases carry exactly three
`data_available_at` instants (2026-08-05, 2026-08-06, 2026-08-09) covering
observations from 2022-04-30 onward, so the publication latency of a given
observation ranges from 3 days to **1,559 days**. No revision of any pre-August
day was available on that day, so a strict as-of reconstruction admits nothing.

**Decision.** v2 gates by *preference*, not by exclusion: for each (issue day,
observation day) it takes the revision that was current at the issue day, and when
the stream records none, it takes the **earliest-ever-published** revision rather
than v1's *latest*. v2 therefore moves strictly away from revision leakage in both
regimes — exact when the stream supports as-of reconstruction, minimal-revision
when it does not — while v1 always takes the most-revised value. Which regime
applied to a row is visible in the returned `data_available_at`. Measured cost:
1085/1085 meteorology values present under both v1 and v2 on a 31-day window, with
v2 taking 2.6 s against v1's 0.06 s (the per-issue re-pick is O(days × lookback ×
signals)); the mapper therefore runs on a bounded day grid.

**Named limitation:** drought is not per-issue re-picked. Its revision choice
happens inside `agri.drought_class_daily_series`, which exposes no alternatives.

**What would close this properly:** an ingest that records a real per-observation
publication time instead of one instant per backfill release. Until then, no
as-of reconstruction of this warehouse's history can be exact, whatever the
covariate layer does.

### Model A — species fit

`species_fit_compatibility_logistic_v1`, sklearn `LogisticRegression` (lbfgs,
C=1.0, seed 20260814), sample weights = label confidence weight, standardization
moments exported into the artifact.

| Metric | Value |
| --- | ---: |
| Labels fitted (effective sample size) | **8** |
| Design rows | 401 |
| Distinct cited sources | 8 |
| Cross-validation | grouped leave-one-source-out, 8 folds |
| Accuracy | **0.025** |
| Macro-F1 | 0.022 |
| Majority-class baseline accuracy | **0.541** |
| Mean absolute utility error | 0.620 |
| RMSE (utility scale) | 0.665 |
| Artifact sha256 | `ed6216cc6513c2495ad0b3d5a25684e8285216674824724e91b87aa3987b4442` |
| Receipt id | `728e1de5-6d1e-4421-9ecb-46d3ed5fc0d2` |

**Read this honestly: Model A does not generalize out-of-source. It is twenty
times worse than predicting the majority class.** The reason is structural and was
visible before the fit: the harvest holds exactly one label per species and one
label per source, so leave-one-source-out removes the only evidence about the
held-out species *and* about its outcome class simultaneously. The model is
literally never shown a comparable case. Two of the ten species labels produced no
matched day at all (their envelopes exclude Boise), leaving 8.

What this run does prove: the plane, the mapper, the fit, the grouped evaluation,
the canonical artifact and the receipt chain all work end to end on governed data.
What it does not prove: that a species-fit recommender is possible at this label
count. It is not, and no amount of design-row inflation would change it.

### Model B — strategy selection

`strategy_selection_compatibility_logistic_v1`, same estimator and discipline,
plus a strategy-identity one-hot (Model A deliberately has none — see above).

| Metric | Value |
| --- | ---: |
| Labels fitted (effective sample size) | **14** |
| Design rows | 898 |
| Distinct cited sources | 14 |
| Strategies covered | 6 |
| Cross-validation | grouped leave-one-source-out, 14 folds |
| Accuracy | **0.546** |
| Macro-F1 | 0.247 |
| Majority-class baseline accuracy | **0.592** |
| Mean absolute utility error | 0.264 |
| RMSE (utility scale) | 0.412 |
| Artifact sha256 | `9ac45612dce68a724ac75871b36b8444a671979eda159eafd33cfe12d0a9c854` |
| Receipt id | `a679e1b6-f2ff-4602-bd72-1a7c6570f42f` |

Model B is close to, but still below, the majority-class baseline. With 14 labels
across 6 strategies and 3 outcome classes, that is the expected result: it has
learned roughly "predict `mixed`", which is what the label distribution supports.
It is honest evidence that the lane runs, and it is not an operational
recommender.

### Serving

`GET /api/v1/recommendations/species` and `.../strategies`, both measured against
the disposable database:

- 200 with a ranked list, per-item citations (DOI, title, year, journal), class
  probabilities, objective-adjusted score, `claim_tier`,
  `label_review_tier = agent_reviewed_pending_owner_signature`,
  `evaluation_only = true`, `publication_authorized = false`, and a `pin` block
  carrying the artifact/evaluation/parameter/training-code checksums, the label
  release key and checksum, and the harvest document checksum.
- 404 `insufficient_labels` when a requested `artifact_checksum` matches no
  receipt.
- 404 when `cell_id` names no governed `agri.spatial_cell`.
- 409 `insufficient_labels` when the governed streams carry no complete feature
  row for the requested cell and day — measured for 2026-08-10, where the 28-day
  rolling window is incomplete because observations end 2026-08-06. **No candidate
  is ever scored from a default.**
- 400 on a missing/naive `as_of`, a malformed checksum, or an out-of-range weight.
  `as_of` is required: the route never substitutes the wall clock, and a test
  asserts the module contains no `date.today()`/`datetime.now()`/`utcnow`.

## Known gaps

1. **The owner signature is outstanding.** 0 labels are `approved`; the state
   exists and is unreachable without one. Everything downstream is tiered
   `agent_reviewed_pending_owner_signature`.
2. **Label count is the binding constraint**, not the algorithm. 8 and 14
   effective samples cannot support the models this track charters.
3. **No spatial blocking.** The pilot is one spatial cell, so there is exactly one
   spatial block and a spatial hold-out would be empty. Grouped
   leave-one-source-out is used instead; the deviation and the per-source
   breakdown are recorded inside every receipt's `evaluation_metrics`.
4. **Three envelope terms are unexpressible** (elevation, soil texture, hardiness
   zone), so no label's envelope is ever fully checked against the streams.
5. **Aridity is a derived proxy**, not an observation, and is labelled as such
   wherever it appears.
6. **Numeric envelope tolerances are a declared modelling choice**
   (±60 mm, ±2 °C, ±30 days, ±300 m), recorded per instance and in every
   artifact's parameter checksum. A point-valued envelope would otherwise match
   nothing.
7. **Confidence weights (0.9/0.6/0.3) are a declared mapping** from the harvest's
   ordinal confidence, not a source value.
8. **v2's per-issue gate cannot be exact on backfilled history** (above).
9. **AnEn and ML-forecast covariates are declared gaps**, authored by another
   lane.
10. **The declarative tree is fully regenerated.** *(Updated later on
    2026-08-14: after `20260814_0020` and `20260814_0021` both landed, a full
    regeneration ran; `db/agri/**` + `db/manifest.sql` are byte-identical to a
    pg_dump of the head-migrated database (234 files) and
    `test_manifest_matches_tree` passes. The earlier text of this gap described
    the mid-session state and is superseded.)*

## The `20260725_0013` causal plane remains blocked and untouched

`strategy_label_episode` and `strategy_label_release` hold **0 rows** in
production (verified 2026-08-14) and 0 rows on the disposable database after this
lane ran. No table in this plane carries a foreign key into any `strategy_*`
table — asserted by a test that reads `pg_constraint` directly. Model B writes to
`agri.recommendation_training_receipt` and the job ledger only: never to
`strategy_selection_receipt`, `strategy_selection_candidate`,
`forecast_publication`, `forecast_publication_item`, or any publication pointer.
The receipt table's own CHECK constraints pin `evaluation_only` true and
`publication_authorized` false, so the row shape cannot express a publishable
recommendation model.

## What unblocks `approved`

1. The owner countersigns `literature-labels:2026-08-14:96cfec27be6fe1c7` with a
   signature reference (see the signature request). The guard trigger accepts
   `agent_reviewed -> approved` only with one present.
2. To make the models worth approving rather than merely the labels: several
   labels per subject from independent sources (so leave-one-source-out has
   something to generalize from), and more than one spatial cell (so spatial
   blocking becomes possible).
3. To close the envelope gaps: ingest elevation and soil texture as governed
   cell-level observations, and record real per-observation publication times so
   v2's as-of gate can be exact.
