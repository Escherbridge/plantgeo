---
type: specification
---

# Seasonal forecast and residual-feedback evaluation

## Goal

Improve the *evaluation* of local metric forecasts by comparing seasonal and
lag-aware candidates with the existing SQL-linear and daily-increment-bootstrap
baselines. Persist every candidate forecast, scored outcome, and derived signal
as immutable, availability-aware evidence. No output may become operational,
published, causal, or advisory solely because it improves a retrospective score.

In this track, **feedback** means a residual-derived signal that becomes
available only after its source actual has been recorded. It is not neural-network
backpropagation: no neural model, gradient-updated production parameter, or
silent rewrite of a prior prediction is in scope.

## Evidence boundary

- The current Boise WS2M series has complete input coverage but only one scored
  30-day retrospective origin; it is insufficient for model selection.
- The older Denver candidate remains rejected and is a comparator, not training
  evidence for a new claim.
- Forecast residuals are not intervention treatment/control labels and must not
  train or justify strategy-selection efficacy.
- All work begins on a disposable database or database-free frozen export. It
  must not mutate a retained source release, current iteration, receipt,
  publication pointer, forecast serving view, or Railway service.

## Target architecture

```text
validated source release
  -> canonical date-spined series
  -> immutable base forecast candidates
  -> later-recorded actuals and residuals
  -> derived seasonal / residual-feedback signals
  -> immutable derived-signal forecast candidates
  -> checksummed feature snapshot for ML evaluation only
```

Every arrow records input release identities, source/feature checksums, code or
recipe checksum, issue/cutoff time, valid time, warehouse-recorded/as-of
visibility, and data-availability time. A derived signal is a directed acyclic
dependency with a bounded maximum depth. Each feedback parent must have both a
forecast cutoff and valid time strictly earlier than the child cutoff, and its
actual-recorded availability must be at or before the child origin. Its
availability is no earlier than the latest upstream actual-recorded time. A
future model cannot read a residual, score, or feature produced after its
simulated origin.

## Persistence contract

The design phase must specify additive, dedicated forecast relations rather than
overloading operational publication tables:

1. a reviewed signal definition/version with unit, support, cadence, recipe,
   parent schema, and max dependency depth;
2. immutable signal values with source iteration/value, availability, checksum,
   and provenance; and
3. evaluation candidate/run/value/metric/receipt rows that bind the candidate
   algorithm, hyperparameters, fold/origin assignment, interval outputs, actual
   linkage, and final checksum.

Existing `forecast_iteration*`, hindcast, feature-snapshot, model, training-run,
and receipt planes remain authoritative where their contracts already fit. The
schema design must explicitly document which new relation is needed and why;
no duplicate source of truth is allowed.

## Candidate ladder and gates

At minimum compare the existing methods with persistence, seasonal-naive, and a
regularized lag/seasonal-feature candidate. Lag construction, imputation,
scaling, season definitions, and hyperparameter selection are fit only inside
each expanding training fold. Reserve later non-overlapping target windows as a
final untouched test set; if target windows must overlap, use a predeclared
dependence-aware paired/block uncertainty method instead of treating origins as
independent. Report MAE, RMSE, bias, MAPE where valid, skill versus persistence,
pass fraction, and interval coverage by horizon and season.

No candidate advances unless it clears the existing forecast quality policy on
the frozen final holdout and has no leakage, support, lineage, or calibration
failure. A better average score with poor seasonal slices, weak coverage, or
insufficient origins remains evaluation-only and rejected.

## ML consumer boundary

Only a validated, checksummed feature snapshot may expose derived forecast
signals to an ML experiment. Feature availability must be at or before each
training/prediction origin; feature consumers must preserve native spatial and
temporal support. ML evaluation artifacts are persisted separately from serving
views, and no strategy label, effect claim, or forecast publication follows from
their existence.

## Acceptance criteria

- A read-only data-quality report records source freshness, cadence, missingness,
  duplicates, outliers, release lineage, actual maturity, and leakage checks.
- The evaluation corpus contains enough independent rolling origins and seasonal
  strata for a pre-registered final holdout; otherwise it records abstention.
- A migration/design review proves the dependency DAG is acyclic, bounded, and
  availability-aware before any persistent derived value is accepted.
- Every candidate and feedback signal is reproducible from immutable inputs and
  cannot alter a past forecast or enter a publication path.
- A separate reviewer validates statistical splits, database grants, checksum
  binding, and ML-feature availability before an implementation phase starts.
