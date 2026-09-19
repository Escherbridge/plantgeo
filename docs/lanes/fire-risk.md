---
type: lane-contract
slug: fire-risk
horizon: 14d
---

# fire-risk lane

Source-of-truth spec for the `fire-risk` layer lane, chartered 2026-09-18 by track
[`plantgeo_ml_service_20260918`](../../conductor/tracks/plantgeo_ml_service_20260918/spec.md)
FR-5 and FR-5a.

**This lane has no writer in agri-data-service.** It is written by `services/plantgeo-ml-service`
(owner decision D2, 2026-09-18: every `kind=forecast` partition moved with the forecasters).
agri-data-service reads it for serving and registers it so its Parquet readers, its lane registry
and its slider catalogue can resolve the slug. The registered adapter refuses an export and names
the writing service.

## 1. Source system

`services/plantgeo-ml-service`, specifically:

| module | what it does |
| --- | --- |
| `plantgeo_ml_service/pipeline/fire_risk_daily.py` | the daily run that scores cells and writes partitions; this is the stem `LaneRegistration.forecast_module` names |
| `plantgeo_ml_service/pipeline/fire_risk_features.py` | assembles the feature plane, respecting each producer's publication lag |
| `plantgeo_ml_service/pipeline/fire_risk_plane.py` | the cell population that is eligible to be scored |
| `plantgeo_ml_service/pipeline/fire_risk_gate.py` | the FR-5 publication gate: what a stratum must clear before it may publish a score |
| `plantgeo_ml_service/pipeline/fire_risk_backtest.py` | the walk-forward backtest whose receipt the gate reads |

Upstream of those, the features are read from Parquet lanes this service DOES write
(`fire-detections`, `signal`, `vegetation`), never from PostgreSQL: the ML service refuses any
database variable (spec D5).

## 2. Cadence

**Daily.** Nature `daily_series`, `cadence_days=1`, `publication_lag_days=0`. Every day is a real
candidate, which is what daily scoring means.

Lag 0 is not an oversight. The scorer's own feature reads already respect each contributing lane's
publication lag, so the day it publishes is the day it ran; subtracting a second lag here would
double-count the same delay and hold back a partition that is already settled.

Horizons 1-14 are written for each issue day, carried in `horizon_days`.

**14, not the platform default of 30, and it is a DECLARED deviation rather than a shortfall.**
[`layer-lanes.md`](../../conductor/code_styleguides/layer-lanes.md) section 2 sets the default
horizon at 30 days forward and lets a lane declare a shorter one "when its evidence supports no
more"; it was amended on 2026-09-19 (this track, FR-5) to name `fire-risk`'s 14 days explicitly, so
the contract and this file say the same thing in both directions. The declaration -- `horizon: 14d`
in this file's front matter -- and not the 30-day default is what readers and the census use. The
evidence bound is FR-5's publication gate: a horizon publishes only where the walk-forward backtest
clears the declared lift, and beyond day 14 it does not.

## 3. Historical horizon

**Floor `2026-09-19`, basis: first possible ML publication.** Not a measurement of published
history -- there was none when the registration landed. The lane was chartered on 2026-09-18, so the
earliest day any writer could honestly carry is the day after.

An earlier floor would invent gap-days for a lane that did not exist, which is the exact failure
`LaneRegistration.floor_basis` exists to prevent. The floor moves only when a backfilled publication
is measured; never on a guess about how far back the features reach.

## 4. Grain

**`(cell_longitude, cell_latitude, valid_day)` at 0.005 degrees**, plus the six forecast provenance
columns (`issued_on`, `horizon_days`, `quantile` finish the sort key).

The cell size is `fire-detections`' own
(`warehouse/schemas/fire_detections.py`, `FIRE_DETECTIONS_CELL_SIZE_DEGREES`), so a risk row and
the detections it was fit on join without a dimension table, and both lanes re-floor onto the
coarser rungs through the same arithmetic.

Columns beyond the grain: `probability`, `risk_score` (both nullable), `stratum`, `refused_reason`,
`model_artifact_sha256`. The schema is pinned twice, identically:
`agri_data_service/warehouse/schemas/fire_risk.py` and
`plantgeo_ml_service/warehouse/streams.py::FIRE_RISK_SCHEMA`.

## 5. Known gaps and traps

- **The lane is expected to be EMPTY until the gate passes.** FR-5 forbids a partition at a real
  prefix until a walk-forward backtest receipt reports per-stratum PR-AUC and Brier on held-out
  years against the VPD-only and climatology baselines, with a declared minimum lift. An empty
  census here is correct, not a gap to fill.
- **A refused cell is not a zero-risk cell.** Out-of-stratum cells are refused, never scored:
  `probability` and `risk_score` are NULL and `refused_reason` carries the why. A reader that
  coalesces those nulls to zero publishes "no risk here", which is the single claim the gate exists
  to prevent.
- **The only skill ever measured is in-sample.** `analysis/fire_risk_index.py` reported AUC 0.725
  in-sample, VPD alone 0.697, and closed forest only 0.586. Do not quote those as lane accuracy.
- **This lane has no observed side.** `kind=forecast` is the only kind it writes, which makes it the
  one registered stream whose schema already carries the six provenance columns; see
  `FORECAST_ORIGINATED_STREAMS` in `warehouse/parquet/schema.py`.
- **Coarse rungs keep `stratum` in the grain.** Averaging a closed-forest probability with a
  rangeland one would produce a number no model fit, so a coarse cell reports one row per stratum
  present in it rather than one row.

## 6. Validation approach

- Schema parity between the two services, held by `tests/test_streams_parity.py` on the ML side and
  by this lane's own module here.
- `tests/parquet/test_tier_derivation.py` derives a synthetic day at every rung and casts it back to
  the storage contract, so a derivation that no longer satisfies the schema fails here rather than
  at upload.
- `tests/parquet/test_lane_contract.py` binds `forecast_module="fire_risk_daily"` to the file
  `plantgeo_ml_service/pipeline/fire_risk_daily.py`, in both directions.
- Publication acceptance is the ML service's: the gate receipt in that track's `evidence/`, read
  back through `parquet_ops/availability_coverage.py` rather than a raw listing.

## 7. Forecast recommendation

**The lane IS the forecast.** It claims horizon 14d -- the declared deviation from section 2's
30-day default, cited in section 2 above and amended into
[`layer-lanes.md`](../../conductor/code_styleguides/layer-lanes.md) section 2 itself on 2026-09-19
-- and names its module, which is what that section requires of any lane claiming a horizon. There is no second forecaster to
build on top of it, and `kind=observed` under this slug means nothing: no one observes a risk.

What remains open is the gate, not the method.
