# fire-risk (phase 2B, slice `p2b-fire-risk`)

Rationale for `fire_risk_features.py`, `fire_risk_daily.py`, `fire_risk_backtest.py` and their L1
estimator `method/ml/fire_risk_model.py`. Written as a separate file so the coordinator can merge it
into `AGENTS.md` without two sessions editing one paragraph.

The lane predicts **where fire occurs**, one scalar per `(cell_longitude, cell_latitude, valid_day)`
for horizons 1 to 14, at the fire-detections 0.005 degree grain. It says nothing about how much
energy a fire releases: `analysis/AGENTS.md` records that the apparent intensity signal collapsed to
a correlation of +0.131 once FRP was normalised per detection, so intensity is not modelled and no
column here carries it.

## The frontier is the producer's clock, never today

Every lane window ends at `lane_contract(layer).settled_through(issued_on)`, which is
`issued_on - publication_lag_days`. Signal is 9 days behind, vegetation 7, burn-severity 7, drought 4
and fire-detections 2, so a plane issued on the 19th reads NDVI through the 12th and VPD through the
10th. Those lags are measured medians, so a healthy lane normally sits a full lag behind and a
staleness check measured against TODAY would call all five broken. `observed_reader.read_lane_window`
refuses a day past the frontier rather than narrowing the window, which is why nothing here clamps or
retries: a refusal is the answer.

The one thing a reviewer should check first on any change here is that no window is derived from
`date.today()`, from `valid_day`, or from a row's own timestamp. A feature built from a day the model
could not have seen produces an excellent backtest and a useless product.

## The lattice: integer micro-degrees, shifted, never divided

Three grids meet in this module: the 0.005 degree fire-detections grid (the output grain), the 0.25
degree vegetation and signal lattice (centres at odd multiples of 0.125 degrees), and the published
rung ladder (0.01, 0.2 and 5.0 degrees, matching agri-data-service's
`warehouse/parquet/tiers.TIER_RESOLUTION_DEGREES`).

Every crossing is integer arithmetic on micro-degrees, and two measured facts are why:

- **Polars division is frame-length dependent.** `column / 0.2` on a one-row frame and on a bulk
  frame disagree in the last bit, which flips cells at exactly the lattice edges where the answer
  matters. So a coordinate becomes micro-degrees by MULTIPLICATION (`value * 1e6`, rounded to
  `Int64`), every binning is integer floor division, and the only float step on the way back out is
  one multiplication by `1e-6`.
- **Integer division of a negative longitude is engine-dependent.** Truncation towards zero and
  flooring differ, and every longitude in this region is negative. Both axes are therefore shifted
  into a non-negative space first (`+180` and `+90` degrees). Both shifts are exact multiples of
  every pitch used here, so the shift cannot move a cell boundary.

The vegetation and signal join is a NEAREST-CENTRE join by lattice arithmetic, not a spatial join:
the 0.25 degree cell boundaries are multiples of the pitch in shifted space, so
`shifted // 250000` names the cell whose centre is nearest, and the join runs on the integer index
pair. A fire cell that falls in a 0.25 cell with no vegetation row gets no stratum, which is a
refusal downstream and not a zero.

## The stratum is a declared threshold, not the analysis's quartile

`analysis/fire_risk_index.py` stratifies by greenness QUARTILE and scores quartiles 1 and 2 only,
because VPD discrimination runs 0.693 / 0.746 / 0.667 / 0.586 across them: strong in steppe and
transition, weak in closed forest. That finding is carried over; the mechanism is not. A quartile is
computed on the scoring day's own population, so it is a different definition every day and no
artifact could be reproduced against it. This module splits on a DECLARED NDVI floor
(`CLOSED_FOREST_NDVI_FLOOR`), and moving that number is a model decision that bumps
`FEATURE_SET_VERSION`.

Three strata exist: `closed_forest`, `open_canopy` and `unknown`. `unknown` is what a cell with no
fuel state gets, and no artifact may declare it scoreable. The schema keeps `stratum` non-null on
every row precisely so a refused cell is as attributable as a scored one.

## Why drought is a regional scalar and burn history is not

Both lanes carry WKB and this service has no spatial join on the polars path, so both needed a
decision rather than a library.

`burn-severity` rows are individual fire perimeters, which are compact. Their WKB bounding box is
scanned in pure Python (`wkb_envelope`) and every base cell inside the box is marked. A bounding box
OVERSTATES a perimeter, so the feature is named `prior_burn_envelope_overlap` and is a conservative
upper bound on "a fire has been here recently", not a containment test. The scan is bounded twice:
one geometry may not exceed `MAX_GEOMETRY_BYTES`, and the envelopes together may not cover more than
`MAX_PRIOR_BURN_CELLS` base cells. Past either, it refuses instead of running slower.

USDM drought releases are CONUS-wide multipolygons of up to ~140,000 vertices. The same bounding-box
trick would assign every cell in the region the same class, which is a cell-resolved feature in name
only. Drought is therefore carried honestly as ONE regional scalar, the worst class in the newest
settled release, under the name `regional_drought_category`. Making it cell-resolved needs either a
DuckDB spatial clip (the `analysis/AGENTS.md` recipe: clip to the region envelope first, one polygon
per query) or a pre-reduced drought-by-cell lane. That is a known gap, recorded in the slice's
evidence file, not a silent approximation.

## Seasonality and photoperiod are computed, never read

The owner requirement is that seasonality influences the output. `day_of_year_sine` and
`day_of_year_cosine` close the cycle against the valid day's OWN year length (366 in a leap year), so
31 December and 1 January are adjacent. `photoperiod_seconds` is FAO-56 (Allen et al. 1998) equations
24 and 25: solar declination, then the sunset hour angle, with the cosine argument clipped so a polar
day answers a full day instead of raising. Nothing reads a solar fact table, so the feature exists for
every cell and every horizon without a lane dependency.

Only these three features vary with the horizon, which is why the plane is built once per cell and
then crossed with the horizons rather than rebuilt per horizon.

## The feature checksum

Each row carries `feature_set_version` and a `feature_checksum`: the SHA-256 of the canonical JSON of
its grain, its stratum and its feature values. It is what lets a later reader bind a published score
to the exact vector it was produced from, and it is computed in Python, which is why the plane is
bounded at `MAX_FEATURE_ROWS` for one issue day.

## The model refuses rather than scoring zero

`method/ml/fire_risk_model.py` is numpy plus `foundation` only, so it holds no reader and no bucket
client and can be ported to a kernel later. It is an L2-penalised logistic fit by iteratively
reweighted least squares (the penalty is what keeps the Hessian invertible and it never touches the
intercept), standardised against moments stored in the artifact, then calibrated by equal-width
histogram bins pooled with pool-adjacent-violators so the map from raw score to probability is
monotone.

`predict` answers one of four named refusals and never a number it cannot defend:
`artifact_missing` (an untrained lane), `out_of_stratum` (a stratum the artifact does not claim skill
in), `feature_missing` (a non-finite feature), `feature_set_mismatch` (an artifact fitted on another
feature set) and `backtest_lift_not_cleared` (the FR-5 gate below). A refused row carries a null
probability and a null risk score, because a fabricated zero reads as "no risk here" on a map.

The artifact is canonical JSON, never a pickle: coefficients, intercept, moments, feature names,
stratum table, calibration bins, the trained-on window and the backtest reference, plus its own
digest computed over the document WITHOUT the digest field. A stored document whose digest disagrees
with its body is refused.

## The backtest is the publication gate

`fire_risk_backtest.py` walks the feature plane forward one year at a time, per stratum, and scores
the model against two baselines on the same held-out rows:

- **VPD-only logistic**, because the composite beat vapour pressure deficit alone by about 0.03 in
  the only measurement this lane has, and that measurement was in sample.
- **Climatology**, the cell's own seasonal ignition rate learned from the training years within a
  15-day half-window of the same day of the year. A cell the training years never held falls back to
  the training base rate, which is the honest answer for a cell with no history.

`MINIMUM_PR_AUC_LIFT` is 0.02 over BOTH baselines and `MAXIMUM_BRIER_EXCESS` is 0.0, which means a
model may not be worse calibrated than the baseline it out-ranks. Those numbers are deliberately
close to the in-sample margin: a composite that clears them by a hair is not worth publishing, and
the gate is supposed to be hard to pass. The receipt states the minima it judged against, so a later
reader can see whether the bar moved.

## The daily run

`run_fire_risk_daily` builds features, scores them, writes every rung of every valid day, and only
then publishes availability.

- **The gate.** Outside a scratch prefix, an artifact with no backtest reference refuses the whole
  run, and any stratum the receipt did not clear is withheld: its rows are written with
  `refused_reason = backtest_lift_not_cleared`, never with a score. A scratch run scores everything
  the artifact claims, which is what makes a dry run useful for evaluation.
- **A scratch prefix must actually be scratch.** `dry_run_prefix` must live under `ml/scratch/`. A
  dry run that writes anywhere else is a production write wearing a flag.
- **The partition day is the VALID day, not the issue day**, so a slider asking for a future day
  reads one prefix and gets the forecast for it. The issue day and the horizon live in the
  provenance columns, which is also what keeps the sort key total.
- **`quantile` is the number 0.5, not the string "p50".** The pinned schema carries it as a float;
  a reader that wants the label renders it.
- **`random_seed` is recorded even though nothing draws.** This model is a closed-form evaluation, so
  the seed is 0 and the ensemble size is 1. A lane whose provenance columns appeared only sometimes
  would be a lane every reader has to branch on, and the day this model gains an ensemble the column
  has to already be there.
- **A coarse rung is a rung-SELECT, never an average.** Coordinates are floored to the tier cell
  ORIGIN (the sibling's `GridAggregation` convention, so a coarse cell contains exactly the base
  cells that floor into it), then the worst scored row in each coarse cell is carried up whole. Every
  published coarse row is therefore a row the model actually produced: averaging probabilities across
  a 5 degree cell would be a modelling claim nobody fitted, and the honest summary of a risk surface
  at a coarse zoom is its worst cell.
- **The prediction receipt carries no wall clock.** It is written with `put_immutable`, so a replay
  of one issue day adopts the identical object instead of overwriting it, and a receipt whose bytes
  DIFFER at the same run identity is refused, which is exactly the signal that the run stopped being
  deterministic. The completion markers do carry `completed_at`, because they are mutable claims
  about a day rather than content-addressed evidence.

## The overlap with `forecast_lane_bootstrap.py`

That module (another slice, landed in parallel) writes any forecast lane's rungs, projects its
terminal availability rows and bootstraps its lane root. `fire_risk_daily.py` does its own because the
two were authored at once, and merging them is a real decision rather than a tidy-up: its coarsening
dispatches on `FORECAST_TIER_DERIVATIONS`, which has no `fire-risk` entry, and its per-COLUMN
aggregation vocabulary cannot express a whole-row select. `max` on `risk_score` alone would pair one
row's score with another row's `stratum` and `refused_reason`. The evidence file for slice
`p2b-fire-risk` records the recommendation: give that module a row-select strategy, then delegate.

## What this slice deliberately does not do

- It does not TRAIN a published model. `train_fire_risk_model` exists because the backtest needs it;
  no artifact is fitted against production data here.
- It does not register the lane. `fire-risk` has no `LaneContract` in `warehouse/lanes.py` and no
  agri-side `LaneRegistration`; that is FR-5a and slice `p2d-fire-risk-registration`. Nothing in this
  module calls `lane_contract("fire-risk")`, only the five lanes it reads.
- It does not bootstrap the lane's availability history. `run_fire_risk_daily` publishes only when it
  is handed an `AvailabilityConfig` and a pointer store, and the generation-zero bootstrap marker is
  still owed by the lanes slice (see `AGENTS.md`, "What p2a does NOT write").
- It has no cell dimension. The scored universe is the cells the fire-detections lane holds rows for
  in the trailing window, which is biased towards cells that have recently burned. The honest
  universe is the region's cell dimension, and that is a recorded gap, not an accident.
