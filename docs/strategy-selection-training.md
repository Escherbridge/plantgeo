# Strategy-selection training contract

> **STATUS — 2026-08-22, body below untouched.** ML is frozen
> (`conductor/RUNBOOK.md` §0.24.5 — moving to
> `services/agri-data-service/src/agri_data_service/ml/` and eventually a
> separate Mojo service), and the Postgres substrate this training contract
> assumes is being retired by the same pivot (§0.23/§0.24). The
> intervention-effect framing and label-boundary discipline below are unaffected
> by the storage change, but nothing here has been re-verified against the
> Parquet/lane architecture. Read RUNBOOK §0.23/§0.24 first.

PlantGeo treats strategy selection as an intervention-effect problem, not as
multiclass classification. A model estimates the outcome under each eligible
strategy and under an explicit untreated control; a reviewed policy may then
rank conservative effect bounds or abstain.

The contract is evaluation-only until the database gates support an
`effect_candidate`. It must never be described as an operational forecast,
validated causal recommendation, or life-safety output.

## Label boundary

One training episode represents one independent subject, strategy/outcome
definition, and index time. A governed label release must bind:

- the analysis subject and predeclared spatial block;
- the governed strategy version, or an eligible untreated control;
- assignment/exposure time, intervention window, and intensity;
- nonoverlapping baseline and fully matured outcome windows;
- baseline and outcome evidence with source-release lineage;
- the time every covariate and outcome first became available;
- assignment mechanism, eligibility/risk-set identity, and episode checksum;
- the finalized database label-release checksum carried into the exported
  bundle, model artifact, training receipt, and selection receipt.

Repeated observations do not increase the independent intervention-unit count.
Controls are eligible untreated units from the same risk set, never a
synthetic “control strategy.”

The repository and currently reachable local warehouses do not yet contain
rows satisfying this complete label contract. The existing Boise wind actuals
are forecast-error labels, not intervention-effect labels. They remain useful
signal features, but cannot authorize an efficacy model.

Before importing any newly identified external source, describe its direct
field mapping in `strategy_label_source_mapping_v1` and run:

```powershell
uv run agri-service ml strategy-label-map-preflight `
  --mapping-manifest examples/strategy-label-source-mapping.incomplete.json
```

The checked-in example is intentionally all `null` with an empty covariate
list. It exits nonzero, lists the missing paths, and emits no mapping checksum;
it is a worksheet, not evidence. A ready mapping must name an immutable
`intervention_outcome_evidence` release and directly cover the reviewed outcome
definition, treatment strategy and eligible-control risk set, subject/cohort
assignment, spatial block, all intervention/baseline/outcome windows, raw
baseline and outcome evidence lineage, and every covariate's value,
availability, evidence, and release fields. The preflight never opens a
database, executes expressions, or creates label episodes.

Mapping readiness proves only that the manifest declares a complete canonical
field contract for a named and checksummed source release. It does not prove
that source rows exist, satisfy the declared semantics, pass support gates, or
are eligible for normalization; those checks remain mandatory before any
label-release write.

## Estimator ladder

Every candidate uses the same expanding-time, held-out-spatial-block folds:

1. matched pre/post difference-in-differences as the transparent baseline;
2. cross-fitted augmented inverse-propensity weighting with regularized
   propensity and outcome nuisance models;
3. a regularized doubly robust conditional-effect learner for ranking;
4. arm-specific ridge outcome models as a sensitivity check.

Artifacts are canonical JSON containing coefficients, feature order, fold
assignments, the label-release and canonical-bundle checksums, and diagnostics.
The training validator requires the artifact output metadata and training row
to repeat the finalized label-release checksum. Effects are normalized to the
outcome's benefit direction, so a positive value consistently means expected
improvement while the versioned outcome definition preserves the original
unit and direction. Python pickle is not a durable model format.

## Current benchmark gates

The evaluation-only trainer currently requires, per strategy/outcome/horizon:

- 100 distinct treated units and 200 eligible controls;
- eight spatial blocks, four start cohorts, and 20 nonempty block/cohort
  clusters;
- all predictive inputs available no later than assignment time;
- all outcomes available by the label release cutoff and by the origin of any
  fold in which they are used for training;
- at least 90% of each arm within propensity `[0.10, 0.90]`;
- stabilized weights no greater than 10;
- effective sample size of at least 50 and 25% of each raw arm;
- post-weighting maximum absolute standardized mean difference no greater
  than 0.10;
- difference-in-differences and AIPW agreement in direction and magnitude.

Promotion to an effect claim additionally requires repeated pre-intervention
support, completeness and variance-ratio gates, cluster-bootstrap uncertainty,
placebo and negative-control tests, and spillover-buffer separation. Revision
`0013` cannot verify those contracts, so it rejects every `effect_candidate`
finalization. Retrospectively reconstructed covariates can support only
research/evaluation use.

## Selection and abstention

Feasibility and effect are separate outputs. Feasibility can exclude a
strategy, but cannot establish benefit.

The benchmark orders candidates by held-out AIPW benefit. A strategy remains
eligible only when its cluster-robust AIPW lower bound exceeds the smallest
meaningful effect, the estimators agree within policy limits, and the paired
held-out interval for `best - second_best` is strictly positive. The resulting
`selected_strategy_id` is a research ranking, not an effect claim or
recommendation.

Missing constraints, immature outcomes, stale inputs, weak overlap, failed
balance, unsupported geography, model disagreement, or an effect interval
crossing the policy threshold produce a durable machine-readable abstention.
Without the additional promotion evidence above, the maximum database output
tier is `feasibility_candidate`.

## Promotion path

1. Normalize the asserted external intervention/control labels into a
   checksum-bound label release.
2. Validate leakage, lineage, support, overlap, and balance before fitting.
3. Train every registered estimator on identical time/spatial folds.
4. Store the canonical artifact through the existing content-addressed
   artifact and training-run plane.
5. Persist an evaluation-only selection receipt and candidates.
6. Permit `effect_candidate` only after independent causal-policy review,
   approved strategy evidence, validated labels/training, and a published
   forecast receipt.
