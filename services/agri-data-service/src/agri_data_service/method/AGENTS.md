# Layer L1: Pure Method

## Responsibility
Pure domain computation algorithms (Monte Carlo simulation, machine learning recommendation models, conformal calibration, preflight mapping validators).

## Dependency Rules
- **May import**: `foundation` (L0).
- **May NOT import**: `warehouse`, `pipeline`, `planes`, `interface`, `sqlalchemy`, `httpx`, `asyncpg`, `click`.

## Sub-packages
- `method/monte_carlo/`: Seasonal-anomaly daily NDVI bootstrap simulations.
- `method/ml/`: Strategy selection, species fit ranker, expert label plane, Analog Ensemble k-NN forecaster, split-conformal calibration.
- `method/monte_carlo` and `method/ml` are siblings at L1; neither imports the other.

## `method/ml/expert_label_plane.py`, `covariates_v2.py`, `recommendation_models.py`

These three are the **pure** half of the recommendation lane. Every read and write
they need lives in `execution/recommendation_lane.py`, because this layer may not
import SQLAlchemy — `tests/test_layer_import_contract.py` enforces that, and the
split is why those modules hold dataclasses and functions over already-fetched rows
rather than sessions.

### `expert_label_plane.py`

Literature labels are **expert recommendation labels** — "what a trusted source
recommends under these conditions" — not intervention outcome labels. They get their
own plane (`20260814_0022`) and carry no foreign key into the `20260725_0013` causal
plane, whose custody preflight rightly demands treatment/control evidence these are
not.

- **Review states** are `draft -> agent_reviewed -> approved`, plus `rejected`. A
  harvested label enters `draft`; the adversarial citation verifier's verdict decides
  whether it advances or is rejected; `approved` requires an owner signature
  reference that nothing in this service can mint.
- **`ENVELOPE_TERM_SUPPORT` is the load-bearing table.** `direct` terms are derived
  from governed observations, `derived_proxy` from governed observations through a
  named formula, and `unexpressible` has no stream at all. An unexpressible term is
  never counted as satisfied *or* violated; it is carried out on the instance row so
  the data-completion gap is a query rather than a memory. Extending the vocabulary
  means editing this table **and** `agri.expert_label_envelope_valid` in the same
  review, or the CHECK constraint and the mapper disagree.
- **`NUMERIC_TERM_TOLERANCE` and `CONFIDENCE_WEIGHTS` are declared modelling choices,
  not source values.** A point-valued envelope ("MAP 283 mm") would match no real site
  exactly, and a fit needs a numeric sample weight where the harvest states an
  ordinal. Both travel inside the artifact's parameter checksum, so a reader can see
  what was assumed.
- **`bounded_issue_days`** samples a fixed grid (the 1st and 15th) rather than every
  day: an envelope is a site description, so neighbouring days match or fail
  together, and a bounded deterministic grid keeps the instance count finite without
  a random draw a checksum could not reproduce.

### `covariates_v2.py`

Holds the pinned version identities, the vector/coverage/climate types, and the
site-climate math. Two things worth knowing:

- **`AS_OF_MODE_BY_SCHEMA_VERSION` records what each version's gate actually is.**
  v1 is `global` (one knowledge cutoff for the whole history). v2 is
  `per_issue_date_preferred_earliest_fallback`: it prefers the revision current at the
  feature row's own issue date and falls back to the *earliest*-published one when
  the stream records none — never a later one. Measured on this warehouse, the
  fallback is the normal path, because the history was bulk-backfilled and carries
  one availability instant per release. See the track decision record.
- **Hargreaves-Samani reference ET is a declared proxy**, used only to derive an
  aridity class from governed temperature and latitude. It is not a governed stream,
  and every consumer records the term as `derived_proxy`.

### `recommendation_models.py`

Both models are **compatibility** models: they learn how the relationship between a
site's governed conditions and a candidate's stated envelope maps to the source's
stated outcome, and they rank candidates by expected ordinal utility.

- **Model A carries no subject-identity column and Model B does.** The harvest holds
  one label per species, so a species one-hot would perfectly separate the training
  set and leave-one-source-out could never score it. Strategies have 2-5 labels each,
  so their one-hot survives the split. That asymmetry is deliberate and is the single
  most important thing to preserve if either model is retrained.
- **Effective sample size is the label count, never the design-row count.** The rows
  of one label are the same literature claim evaluated on different days of one cell.
  Every metric payload says so in its own `caveats`.
- **Evaluation is grouped leave-one-source-out, and the deviation is recorded.**
  Spatial blocking is unavailable while the pilot is one cell; that is written into
  `EvaluationMetrics.deviations` rather than left for a reader to notice.
- **The wildfire and water objectives are weights over label provenance** — the share
  of a candidate's supporting labels drawn from the fire or water literature slices —
  never a claimed wildfire or hydrological effect.
- **Artifacts are canonical JSON, never pickle.** Coefficients, intercepts and the
  standardization moments are exported explicitly, and `class_probabilities`
  recomputes the softmax from the artifact alone, which is what lets the serving
  route score without an estimator object.
